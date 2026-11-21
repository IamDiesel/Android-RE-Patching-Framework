# -*- coding: utf-8 -*-
"""core.java.rn_hermes_bridge — S3-Hermes: die langlebige Fusion-Bruecke zwischen dem Framework und
einem persistenten C++-Hermes+Yoga-Host (eigener Prozess), der LLUs ECHTES (entschluesseltes) Bundle
auf einer echten Hermes-Runtime laufen laesst.

Protokoll: zeilenweises JSON ueber stdin/stdout (ein JSON-Objekt je Zeile), Feld "m" = Nachrichtentyp.

  Host -> Framework
    {"m":"call","id":k,"module":"PlatformConstants","method":"getConstants","args":[...]}
        TurboModule/NativeModules-Aufruf; erwartet {"m":"result","id":k,"value":...}
    {"m":"commit","surfaceId":N,"root":{tag,component,props,children:[...]}}
        Fabric-Commit (completeRoot) -> RnMountRecorder -> SceneGraphSurface
    {"m":"log","level":...,"text":...}        Diagnose (ExceptionsManager etc.)
    {"m":"ready"}                              erster Render fertig, Host ist interaktiv
    {"m":"ack","type":...,"tag":...}           Event verarbeitet (Re-Render folgt ggf. als commit)
    {"m":"bye"}                                Host beendet sich

  Framework -> Host
    {"m":"result","id":k,"value":...}          Antwort auf "call"
    {"m":"load","hbc":"<pfad>"}                Klartext-HBC laden + ausfuehren
    {"m":"event","type":"press","tag":N}       Klick/Taste live zurueck (-> JS onPress)
    {"m":"stop"}                               Host beenden

Glaesern + manipulierbar: jeder TurboModule-Call laeuft durch `TurboModuleRouter` (Log + optional
`jni_boundary`-Beobachtungspunkt + Override). Der Mount-Pfad teilt sich die Logik mit rn_js_bridge.

C++-Host (noch zu bauen): makeHermesRuntime() + evaluateJavaScript(HBC) + Yoga + JSI-Host, der
`global.nativeFabricUIManager` setzt und das obige Protokoll spricht. Bis dahin steht ein Stub-Host
(tools/rn_hermes_host/stub_host.py), der denselben Vertrag spricht - so ist die Bruecke jetzt testbar.

WICHTIG (Befund 2026-11-12): assets/index.android.bundle ist VOLLSTAENDIG VERSCHLUESSELT (Entropie
~7,95/8,0, kein HBC-Magic, keine JS-Strings). Vor Hermes muss das Bundle entschluesselt werden
(Vorstufe; der bestehende .so-Decrypter deckt NUR Libs ab). Siehe AP-Doc S3-Hermes."""
from __future__ import annotations

import json
import subprocess
import threading
import queue

from core.java.rn_js_bridge import tree_from_mount_json


class TurboModuleRouter:
    """Routet TurboModule-/NativeModules-Aufrufe. Minimalmenge als Konstanten; alles andere optional
    ueber die DalvikVM (jni_boundary-faehig). Glaesern (Log) + manipulierbar (Override)."""

    def __init__(self, vm=None, boundary=None, device=None):
        self.vm = vm
        self.boundary = boundary          # optional: JniBoundaryHook (Beobachtung/Manipulation)
        self.log = []                     # [{module,method,args,value,source}]
        self.overrides = {}               # (module,method) -> value
        self.module_map = {}              # WP4: (module,method) -> (cls,name,sig) registrierte Java-Methode
        self._vm_errors = []              # WP4: abgefangene vm.invoke-Fehler (glaesern, kein Fake-Ergebnis)
        dev = device or {}
        w = dev.get("width", 1080); h = dev.get("height", 2160)
        self._builtin = {
            ("PlatformConstants", "getConstants"): {
                "isTesting": True, "reactNativeVersion": {"major": 0, "minor": 83, "patch": 0},
                "Version": 34, "Release": "14", "Model": "glass", "Brand": "generic",
                "Manufacturer": "glass", "ServerHost": "", "uiMode": "normal"},
            ("SourceCode", "getConstants"): {"scriptURL": "glass://index.android.bundle"},
            ("DeviceInfo", "getConstants"): {"Dimensions": {
                "window": {"width": w, "height": h, "scale": 3, "fontScale": 1},
                "screen": {"width": w, "height": h, "scale": 3, "fontScale": 1}}},
            ("I18nManager", "getConstants"): {"isRTL": False, "doLeftAndRightSwapInRTL": True,
                                              "localeIdentifier": "de_DE"},
            ("Appearance", "getColorScheme"): "light",
            ("UIManager", "getConstants"): {},
            ("UIManager", "getViewManagerConfig"): None,
            ("Timing", "createTimer"): None, ("Timing", "deleteTimer"): None,
            ("AppState", "getConstants"): {"initialAppState": "active"},
            ("ExceptionsManager", "reportException"): None,
        }

    def override(self, module, method, value):
        self.overrides[(module, method)] = value

    def call(self, module, method, args):
        key = (module, method)
        # 1) Manipulation (Override) hat Vorrang
        if key in self.overrides:
            val = self.overrides[key]; src = "override"
        # 2) Networking -> durch den gemeinsamen net_recorder leiten (inkl. NetCondition & API-DB)
        elif key == ("Networking", "sendRequest"):
            nf = getattr(getattr(getattr(self.vm, "runtime", None), "stubs", None), "net_fire", None)
            if callable(nf):
                val = self._dispatch_networking(args, nf); src = "net_recorder"
            elif self.vm is not None:
                val = self._dispatch_vm(module, method, args); src = "dalvik"
            else:
                val = None; src = "unresolved"
        # 3) eingebaute Minimalmenge
        elif key in self._builtin:
            val = self._builtin[key]; src = "builtin"
        # 4) Durchreichen an die DalvikVM (optional, jni_boundary-beobachtet)
        elif self.vm is not None:
            val = self._dispatch_vm(module, method, args); src = "dalvik"
        else:
            val = None; src = "unresolved"
        self.log.append({"module": module, "method": method, "args": args,
                         "value": val, "source": src})
        return val

    @staticmethod
    def _dispatch_networking(args, net_fire):
        method = "GET"; url = ""; headers = {}; body = b""
        if len(args) >= 1 and isinstance(args[0], dict):
            spec = args[0]
            method = spec.get("method") or "GET"
            url = spec.get("url") or ""
            headers = spec.get("headers") or {}
            raw = spec.get("data") or spec.get("body") or b""
            if isinstance(raw, dict):
                raw = raw.get("string") or ""
            body = raw.encode("utf-8") if isinstance(raw, str) else (raw or b"")
        else:
            if len(args) >= 1 and args[0]:
                method = str(args[0])
            if len(args) >= 2 and args[1]:
                url = str(args[1])
            if len(args) >= 3 and isinstance(args[2], (str, bytes)):
                body = args[2].encode("utf-8") if isinstance(args[2], str) else args[2]
            if len(args) >= 4 and isinstance(args[3], dict):
                headers = args[3]
        try:
            resp = net_fire(method, url, headers=headers, body=body)
            return {
                "status": getattr(resp, "code", 200),
                "headers": dict(getattr(resp, "headers", {}) or {}),
                "body": (getattr(resp, "body", b"") or b"").decode("utf-8", "replace"),
            }
        except Exception as e:
            return {"status": 0, "error": str(e)}

    def map_module(self, module, method, cls, name, sig):
        """WP4: Faithful-Mapping NativeModule.method -> registrierte Java-Methode (cls,name,sig).
        Waechst coverage-getrieben beim echten Bundle-Lauf. Rueckgabe: self (fluent)."""
        self.module_map[(module, method)] = (cls, name, sig)
        return self

    def _fnptr_for(self, module, method):
        import zlib
        return 0xA0000000 | (zlib.crc32(("%s.%s" % (module, method)).encode("utf-8")) & 0x00FFFFFF)

    def _dispatch_vm(self, module, method, args):
        """WP4: Echter Rueckkanal in die DalvikVM - glaesern + manipulierbar ueber jni_boundary.
        Ist (module,method) auf eine registrierte Java-Methode gemappt (map_module), wird sie ECHT
        in der VM ausgefuehrt (vm.invoke); Ein-/Ausgang laufen durch den JniBoundaryHook
        (before/after: Override via safe_eval, Log, EventBus JNI_CALL, Debugger-Marker). Ohne Mapping
        bleibt der Call glaesern beobachtbar und liefert None (KEIN gefaketes Ergebnis) - die konkrete
        Abbildung waechst coverage-getrieben beim echten Bundle-Lauf."""
        fnptr = self._fnptr_for(module, method)
        this_cls = self.module_map.get((module, method), (("rn:%s" % module),))[0]
        hook = self.boundary
        args_eff = list(args)
        if hook is not None and hasattr(hook, "before"):
            try:
                args_eff = hook.before(fnptr, this_cls, list(args))
            except Exception:
                args_eff = list(args)
        ret = None
        target = self.module_map.get((module, method))
        if target is not None and self.vm is not None:
            cls, name, sig = target
            try:
                ret = self.vm.invoke(cls, name, sig, tuple(args_eff))
            except Exception as e:
                ret = None
                self._vm_errors.append({"module": module, "method": method, "error": repr(e)})
        if hook is not None and hasattr(hook, "after"):
            try:
                ret = hook.after(fnptr, this_cls, args_eff, ret)
            except Exception:
                pass
        elif hook is not None and hasattr(hook, "log"):
            try:
                hook.log.record(fnptr, this_cls, list(args), list(args_eff), None, ret)
            except Exception:
                pass
        return ret


class HermesHostClient:
    """Verwaltet den persistenten Host-Prozess und das JSON-RPC. `host_cmd` = Startkommando (Liste).
    Synchron (pump) fuer deterministische Tests; der echte Betrieb kann auf einen Thread heben."""

    def __init__(self, host_cmd=None, vm=None, boundary=None, width=1080, height=2160, device=None):
        # V4: Startkommando konfigurierbar. Vorrang: Argument > ENV HERMES_HOST_CMD > Fehler.
        # So laeuft derselbe Host im Container direkt und unter Windows via `wsl.exe ... ` ueber stdio.
        if host_cmd is None:
            import os, shlex
            env = os.environ.get("HERMES_HOST_CMD")
            if not env:
                raise ValueError("HermesHostClient: host_cmd fehlt und ENV HERMES_HOST_CMD ist leer")
            host_cmd = shlex.split(env)
        self.host_cmd = list(host_cmd)
        self.router = TurboModuleRouter(vm=vm, boundary=boundary,
                                        device=device or {"width": width, "height": height})
        self.width = width; self.height = height
        self.proc = None
        self.surface = None
        self.recorder = None
        self.events_sent = []
        self.acks = []
        self.logs = []
        self.ready = False
        self.done = False
        self.timed_out = False
        self._paused = False          # WP2: Debug-Halt aktiv -> Idle-Timeout darf UI-Host nicht killen
        self._q = queue.Queue()
        self._stderr = []
        self._eval_seq = 0                # WP4: JS-Eval-Request-Zaehler
        self._eval_results = {}           # WP4: id -> {'value'|'error'}
        self._readers = []
        import os as _os
        try:
            self.idle_timeout = float(_os.environ.get("HERMES_HOST_IDLE_TIMEOUT", "15"))
        except Exception:
            self.idle_timeout = 15.0

    def start(self):
        import sys
        _kw = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                   text=True, encoding="utf-8", bufsize=1)   # utf-8 statt Windows-cp1252 (Umlaute im JSON)
        if sys.platform == "win32":
            _kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # kein CMD-Fenster ueber Tkinter
        self.proc = subprocess.Popen(self.host_cmd, **_kw)
        # Reader-Threads: stdout -> Queue (entkoppelt das blockierende readline, Idle-Timeout moeglich);
        # stderr wird LAUFEND geleert, sonst blockiert der Host am vollen 64K-Pipe-Puffer (Deadlock).
        t_out = threading.Thread(target=self._read_stdout, daemon=True)
        t_err = threading.Thread(target=self._read_stderr, daemon=True)
        t_out.start(); t_err.start()
        self._readers = [t_out, t_err]
        return self

    def _read_stdout(self):
        try:
            for line in iter(self.proc.stdout.readline, ""):
                self._q.put(line)
        except Exception:
            pass
        self._q.put(None)   # EOF-Sentinel

    def _read_stderr(self):
        try:
            for line in iter(self.proc.stderr.readline, ""):
                if len(self._stderr) < 1000:
                    self._stderr.append(line)
        except Exception:
            pass

    def _send(self, obj):
        self.proc.stdin.write(json.dumps(obj) + "\n"); self.proc.stdin.flush()

    def load_bundle(self, hbc_path):
        self._send({"m": "load", "hbc": hbc_path})

    def send_event(self, type, tag=None, text=None):
        ev = {"m": "event", "type": type}
        if tag is not None: ev["tag"] = int(tag)
        if text is not None: ev["text"] = text
        self.events_sent.append(ev); self._send(ev)

    def _terminate(self):
        """Subprozess aktiv abraeumen: terminate -> kurzes wait -> kill. Verhindert Zombie/Runaway
        (eine in while(true){} haengende JS-Engine wuerde sonst im Hintergrund CPU verbrennen)."""
        p = self.proc
        if p is None:
            return
        try:
            if p.poll() is None:
                p.terminate()
                try: p.wait(timeout=1.0)
                except Exception:
                    try: p.kill()
                    except Exception: pass
                    try: p.wait(timeout=1.0)
                    except Exception: pass
        except Exception:
            pass

    def stop(self):
        try:
            if self.proc and self.proc.poll() is None:
                self._send({"m": "stop"}); self.proc.stdin.close()
        except Exception:
            pass
        self._terminate()

    def close(self):
        self.stop()

    def __del__(self):
        try:
            self._terminate()
        except Exception:
            pass

    def set_paused(self, paused):
        """WP2: Debug-Halt-Zustand. Waehrend eines aktiven Breakpoint-Halts wartet pump_until
        geduldig weiter, statt den UI-Host per Idle-Timeout zu killen. Vom Debugger-Controller
        ueber surface._hermes_client.set_paused(True/False) geschaltet."""
        self._paused = bool(paused)
        return self

    def is_paused(self):
        return self._paused

    def _handle(self, msg):
        m = msg.get("m")
        if m == "call":
            val = self.router.call(msg.get("module"), msg.get("method"), msg.get("args") or [])
            self._send({"m": "result", "id": msg.get("id"), "value": val})
        elif m == "commit":
            self.surface, self.recorder = tree_from_mount_json(
                {"surfaceId": msg.get("surfaceId"), "root": msg.get("root")},
                width=self.width, height=self.height)
            try:
                from core.application.event_bus import EventBus
                EventBus.publish("GLASS_SURFACE", {
                    "surface": self.surface,
                    "recorder": self.recorder,
                    "source": "rn_hermes",
                    "client": self,
                })
            except Exception:
                pass
        elif m == "log":
            self.logs.append(msg)
        elif m == "ready":
            self.ready = True
        elif m == "ack":
            self.acks.append(msg)
        elif m == "eval_result":
            self._eval_results[msg.get("id")] = {"value": msg.get("value"), "error": msg.get("error")}
        elif m == "bye":
            self.done = True

    def pump_until(self, pred, max_lines=10000):
        """Liest Host-Zeilen und verarbeitet sie, bis pred(self) wahr ist, der Host endet (bye) oder
        EOF. Rueckgabe: True wenn pred erfuellt."""
        n = 0
        while n < max_lines:
            if pred(self):
                return True
            try:
                line = self._q.get(timeout=self.idle_timeout) if self.idle_timeout > 0 else self._q.get()
            except queue.Empty:
                if self._paused:
                    continue                               # WP2: Breakpoint-Halt aktiv -> Host am Leben lassen, weiter warten
                self.timed_out = True; self.done = True   # haengender Host: sauber beenden statt blockieren
                self._terminate()                          # Runaway aktiv abraeumen (kein CPU-Leak/Zombie)
                return pred(self)
            if line is None:
                self.done = True
                return pred(self)
            n += 1
            try:
                msg = json.loads(line)
            except Exception:
                continue
            self._handle(msg)
        return pred(self)

    def tap(self, x, y, type=None):
        """Koordinaten-Tap ueber das ECHTE Responder-System: topTouchStart+topTouchEnd auf den
        klickbaren Knoten (echtes Pressable feuert onPress erst nach der Start/End-Sequenz).
        Mit type (z.B. "topTouchEnd") wird stattdessen genau EIN Event geschickt (Rueckwaertskompat
        zu AP3-onTouchEnd-Knoten). Rueckgabe: {handled, tag}."""
        surf = self.surface
        node = surf.hit(x, y) if surf is not None else None
        tag = getattr(node, "_rn_tag", None) if node is not None else None
        if tag is None:
            return {"handled": False}
        before = len(self.acks)
        seq = [type] if type is not None else ["topTouchStart", "topTouchEnd"]
        for t in seq:
            self.send_event(t, tag=int(tag))
        self.pump_until(lambda c: len(c.acks) >= before + len(seq) or c.done)   # Re-Commit(s) + acks
        return {"handled": True, "tag": int(tag)}

    def key(self, x, y, text, type="topChange"):
        """Texteingabe in ein AndroidTextInput an (x,y): sendet topChange mit Text ueber den
        registrierten Event-Handler (-> onChangeText -> State -> Re-Commit). Rueckgabe: {handled, tag, text}."""
        surf = self.surface
        node = surf.hit(x, y) if surf is not None else None
        tag = getattr(node, "_rn_tag", None) if node is not None else None
        if tag is None:
            return {"handled": False}
        before = len(self.acks)
        self.send_event(type, tag=int(tag), text=str(text))
        self.pump_until(lambda c: len(c.acks) > before or c.done)
        return {"handled": True, "tag": int(tag), "text": str(text)}

    def advance_time(self, ms):
        """Virtuelle Zeit vorrücken: feuert fällige verzögerte Timer (setTimeout delay>0, setInterval,
        requestAnimationFrame) im Host. Verzögerte Timer laufen NIE automatisch (Endlosschleifen-Schutz) -
        nur hierüber. Pumpt bis zum ack (inkl. evtl. Re-Commits). Rueckgabe: {ms}."""
        before = len(self.acks)
        self._send({"m": "advance_time", "ms": float(ms)})
        self.pump_until(lambda c: len(c.acks) > before or c.done)
        return {"ms": float(ms)}

    def js_eval(self, code, max_lines=10000):
        """WP4: Live-JS-Eval im laufenden Hermes-Host - App-State inspizieren/patchen ohne Neustart.
        Schickt js_eval, pumpt bis eval_result (oder bye). Rueckgabe: {'value':str} oder {'error':str}.
        Setzt einen gebauten hermes_host mit js_eval-Handler voraus; der Node-Mock liefert no_result."""
        self._eval_seq += 1
        rid = self._eval_seq
        self._send({"m": "js_eval", "id": rid, "code": str(code)})
        self.pump_until(lambda c: rid in c._eval_results or c.done, max_lines)
        return self._eval_results.pop(rid, {"error": "no_result"})

    def set_node_prop(self, tag, key, value):
        """WP4: Live-Override eines Szenegraph-Knoten-Props (glaesern + manipulierbar, analog
        Router/jni_boundary-Override). Nutzt den Recorder (rec.update_props) und markiert die Surface
        dirty, sodass der naechste Frame die Aenderung zeigt. Die REALE JS-State-Aenderung laeuft ueber
        js_eval; dies ist die Beobachter-/Override-Ebene auf dem committeten Baum. Rueckgabe: {handled,tag}."""
        rec = self.recorder
        if rec is None or not hasattr(rec, "update_props"):
            return {"handled": False, "tag": tag}
        if int(tag) not in getattr(rec, "nodes", {}):
            return {"handled": False, "tag": tag}
        try:
            rec.update_props(int(tag), {key: value})
            tree = getattr(self.surface, "tree", None)
            if tree is not None and hasattr(tree, "version"):
                tree.version += 1            # dirty -> Re-Render zeigt den Override
        except Exception as e:
            return {"handled": False, "tag": tag, "error": repr(e)}
        return {"handled": True, "tag": int(tag), "key": key}

    def run_until_ready(self, max_lines=10000):
        self.pump_until(lambda c: c.ready or c.done, max_lines)
        return self.surface


def hermes_host_available(hbc=None):
    """True, wenn ein ECHTER C++-Hermes-Host + HBC verfuegbar ist, sonst Node-Mock-Fallback.
    WICHTIG (Windows/PyCharm, zero-config): auf Windows zaehlt NUR eine echte hermes_host.exe -
    das Linux-ELF/run_host.sh im selben Projektordner darf NIE anspringen (sonst Idle-Timeout statt
    sofortigem Node-Fallback). Auf Linux gilt run_host.sh (Headless-Tests). Pfad via __file__
    (unabhaengig vom PyCharm-Arbeitsverzeichnis)."""
    import os, sys, pathlib
    if os.environ.get("HERMES_HOST_CMD"):
        return True
    dist = pathlib.Path(__file__).resolve().parents[2] / "tools" / "rn_hermes_host" / "dist"
    bundle = pathlib.Path(hbc) if hbc else (dist / "rnreal.hbc")
    if sys.platform == "win32":
        return (dist / "hermes_host.exe").exists() and bundle.exists()
    return (dist / "run_host.sh").exists() and bundle.exists()


def rn_hermes_producer(ctx):
    """ProducerRegistry-Fabrik fuer den ECHTEN C++-Hermes-Pfad (S3-Hermes). ctx (dict) kann tragen:
    'host_cmd' (Liste), 'hbc' (Bundle-Pfad), 'width'/'height'. Faellt sauber auf den Node-Mock
    (rn_js_bridge) zurueck, wenn weder HERMES_HOST_CMD noch dist/-Host+Bundle verfuegbar sind.
    Die lebende HermesHostClient-Instanz wird am Surface abgelegt (surface._hermes_client), damit
    native.ui_tap/ui_key spaeter echte Events -> Re-Commit ueber den Host schicken koennen."""
    import os, pathlib
    ctx = ctx if isinstance(ctx, dict) else {}
    width = int(ctx.get("width", 380)); height = int(ctx.get("height", 800))
    host_cmd = ctx.get("host_cmd")
    hbc = ctx.get("hbc")
    if host_cmd is None and not os.environ.get("HERMES_HOST_CMD"):
        if hermes_host_available(hbc):
            import sys
            dist = pathlib.Path(__file__).resolve().parents[2] / "tools" / "rn_hermes_host" / "dist"
            bundle = hbc or str(dist / "rnreal.hbc")
            if sys.platform == "win32":
                host_cmd = [str(dist / "hermes_host.exe"), str(bundle), str(width), str(height)]
            else:
                host_cmd = ["bash", str(dist / "run_host.sh"), str(bundle), str(width), str(height)]
        else:
            # Fallback: Node-Mock (gleiche Surface-Semantik, ohne echte Hermes-Runtime)
            from core.java.rn_js_bridge import rn_js_producer
            return rn_js_producer(ctx)
    c = HermesHostClient(host_cmd, width=width, height=height).start()
    surf = c.run_until_ready()
    if surf is None or getattr(c, "timed_out", False):
        c.stop()                          # haengenden/fehlgeschlagenen Host nicht leaken
        return None
    try:
        surf._hermes_client = c           # lebender Client fuer tap/key-Re-Commit
    except Exception:
        pass
    return surf


# Registrierung jetzt zentral in core.java.producer_wiring.ensure_default_producers (lazy, idempotent).
