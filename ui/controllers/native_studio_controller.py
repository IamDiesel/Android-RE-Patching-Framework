"""NativeStudioController — Logik des Native-Studio-Analyse-Tabs (B4).

Dünne Controller-Schicht (MVC): empfängt UI-Ereignisse, delegiert an den
GUI-freien Analysekern `core.native` und spiegelt Ergebnisse in den Tab + die
Haupt-Konsole. Lange Läufe im Daemon-Thread; UI-Updates via `app.after`.

Zonen-Policy (A1, native-studio-impl.md): Analyse liest das ORIGINAL bzw. den
Analyse-Arbeitsbereich — nie die gepatchte Destination. Dieser Controller lädt
nur explizit gewählte Dateien; die durchsetzende Service-Schicht (native_emu_service)
folgt in einem späteren Schritt.
"""
import os
import struct
import threading

import tkinter as tk
from tkinter import filedialog, messagebox

# GUI-freier Analysekern
from core.native import iface
from core.native.loader import ElfImage
from core.native.engine_arm64 import Arm64Engine, Arm64Decoder
from core.native.nindex import NativeIndex
from core.native import unpack as _unpack


class NativeStudioController:
    def __init__(self, tab, app):
        self.tab = tab
        self.app = app
        self.image = None          # zuletzt geladenes ElfImage
        self.image_path = None
        self._decoder = None
        self.index = None          # NativeIndex des zuletzt geladenen Images
        self._session_id = str(os.getpid())
        # Navigations-Historie (baum-basiert, nach Smali-Studio-Vorbild)
        self.history_nodes = {}
        self.history_root_id = None
        self.current_history_id = None
        self.history_counter = 0
        self._search_win = None
        self.current_addr = None
        self._decompiling = False
        self._indexing = False
        self._pending_after_index = []
        # Decrypter-/Unpack-Plugins aus dem git-ignorierten native_plugins/ laden (Ordner-Scan)
        self.registry = None
        try:
            from core.native.unpack import UnpackRegistry
            base = self.app.cfg.config.get("BASE_DIR", os.getcwd())
            self.registry = UnpackRegistry()
            n = self.registry.discover(os.path.join(base, "native_plugins"))
            if n:
                self._log(f"{n} Decrypter-Plugin(e) geladen: {', '.join(self.registry.names())}")
        except Exception as e:
            self._log(f"Plugin-Discovery übersprungen: {e!r}")

    # ---- Hilfen -----------------------------------------------------------
    def _log(self, msg):
        """In die GEMEINSAME Main Console (via tab.append_status -> app.log, thread-sicher)."""
        try:
            self.app.after(0, lambda: self.tab.append_status(msg))
        except Exception:
            try:
                self.app.log(f"[NativeStudio] {msg}")
            except Exception:
                pass

    def _dec(self):
        if self._decoder is None:
            self._decoder = Arm64Decoder()
        return self._decoder

    def _require_capstone(self):
        """capstone wird zum Dekodieren gebraucht. Fehlt es, klaren Hinweis geben."""
        try:
            import capstone  # noqa: F401
            return True
        except ImportError:
            self._log("capstone fehlt im GUI-Python. Fix: GUI neu starten (installiert sich via "
                      "requirements.txt beim Bootstrap) ODER einmalig 'pip install capstone' "
                      "im selben Python ausfuehren.")
            return False

    # ---- Aktionen ---------------------------------------------------------
    def engine_selftest(self):
        """Fährt echten AArch64-Code durch die Engine — sofort sichtbarer Beleg."""
        def _work():
            if not self._require_capstone():
                return
            try:
                eng = Arm64Engine(listener=lambda ev, p: None)
                eng.load_blob(0x1000, struct.pack("<II", 0x8b010000, 0xd65f03c0))  # add x0,x0,x1 ; ret
                r_add = eng.run_function(0x1000, args=(40, 2))
                eng.load_blob(0x2000, struct.pack("<II", 0xd28000e0, 0xd65f03c0))  # movz x0,#7 ; ret
                r_mov = eng.run_function(0x2000, args=())
                dec = self._dec().decode(0x8b010000, 0x1000)
                ok = (r_add == 42 and r_mov == 7 and dec.mnemonic == "add")
                self._log(f"Engine-Selbsttest: add(40,2)={r_add} (erwartet 42), "
                          f"movz#7={r_mov} (erwartet 7), decode(0x8b010000)='{dec.mnemonic}' "
                          f"-> {'OK ✓' if ok else 'FEHLER ✗'}")
            except Exception as e:
                self._log(f"Engine-Selbsttest FEHLER: {e!r}")
        threading.Thread(target=_work, daemon=True).start()

    def _fav_service(self):
        from services.native_favorites_service import NativeFavoritesService
        p = os.path.join(_unpack.plugin_root(self.base_dir(), self._session_plugin()), "run_favorites.json")
        return NativeFavoritesService(p)

    def _cfg_jsonsafe(self, cfg):
        c = dict(cfg)
        c["mem_overrides"] = [
            {"addr": ov.get("addr"),
             "bytes": (ov["bytes"].hex() if isinstance(ov.get("bytes"), (bytes, bytearray)) else ov.get("bytes", ""))}
            for ov in cfg.get("mem_overrides", []) or []
        ]
        return c

    def fav_refresh(self):
        try:
            names = self._fav_service().list()
            self.app.after(0, lambda: self.tab.set_fav_list(names))
        except Exception as e:
            self._log(f"Favoriten-Liste FEHLER: {e!r}")

    def fav_save(self):
        try:
            from tkinter import simpledialog
            name = simpledialog.askstring("Arbeitsansicht speichern", "Name der Arbeitsansicht:")
            if not name:
                return
            st = self._fav_service().add(name, self._cfg_jsonsafe(self.tab.get_run_config()))
            self._log(f"Arbeitsansicht '{name}' {st}.")
            self.fav_refresh()
        except Exception as e:
            self._log(f"Favorit speichern FEHLER: {e!r}")

    def fav_update(self):
        name = (self.tab.fav_combo.get() or "").strip()
        if not name:
            self._log("Keine Arbeitsansicht ausgewaehlt (erst im Dropdown waehlen)."); return
        try:
            self._fav_service().add(name, self._cfg_jsonsafe(self.tab.get_run_config()))
            self._log(f"Arbeitsansicht '{name}' aktualisiert.")
        except Exception as e:
            self._log(f"Favorit aktualisieren FEHLER: {e!r}")

    def fav_load(self, name):
        if not name:
            return
        try:
            cfg = self._fav_service().get(name)
            if cfg is None:
                self._log(f"Arbeitsansicht '{name}' nicht gefunden."); return
            self.app.after(0, lambda: self.tab.set_run_config(cfg))
            self._log(f"Arbeitsansicht '{name}' geladen.")
        except Exception as e:
            self._log(f"Favorit laden FEHLER: {e!r}")

    def _relocs_on(self):
        """Zustand der GUI-Checkbox 'Relocs anwenden' (robust)."""
        try:
            return bool(self.tab.var_relocs.get())
        except Exception:
            return False

    def resolve_reloc(self, addr):
        """Loest den Reloc-Zeiger an `addr` statisch auf und springt zum Ziel (#178)."""
        if self.image is None:
            self._log("Keine Lib geladen."); return
        try:
            a = int(str(addr), 0)
        except Exception:
            self._log(f"Ungueltige Adresse: {addr!r}"); return
        try:
            from services.native_reloc_service import NativeRelocService
            r = NativeRelocService().resolve(self.image, a)
        except Exception as e:
            self._log(f"Reloc-Aufloesung FEHLER: {e!r}"); return
        t = r.get("target")
        if t is not None:
            self._log(f"Reloc 0x{a:x} -> 0x{t:x} (springe hin).")
            self.navigate(t, set_entry=False)
        else:
            self._log(f"Keine Reloc an 0x{a:x} (kein per Reloc gefuellter Zeiger).")

    def run_emulation(self):
        """A1a: fuehrt die Funktion aus dem Run-Panel aus (run-to-completion) und zeigt
        Register/Memory/Coverage. Nutzt services.native_emu_service (headless getestet)."""
        if self.image is None:
            self._log("Erst eine .so laden (kein Image)."); return
        if not self._require_capstone():
            return
        cfg = self.tab.get_run_config()
        if not cfg.get("entry"):
            self._log("Kein Entry angegeben (Symbol oder 0xAddr)."); return
        _ent = str(cfg["entry"]).strip()
        try:
            if _ent.lower().startswith("0x"):
                _ea = int(_ent, 16)
            elif self.image is not None and _ent in self.image.dynsyms:
                _ea = self.image.dynsyms[_ent].value
            else:
                _ea = None
        except Exception:
            _ea = None
        if _ea is not None and not self._is_code_addr(_ea):
            self._log(f"Entry 0x{_ea:x} liegt nicht in ausfuehrbarem Code (Daten/Import) — Run abgebrochen.")
            return

        def _work():
            try:
                from services.native_emu_service import NativeEmuService
            except Exception as e:
                self._log(f"native_emu_service nicht ladbar: {e!r}"); return
            svc = NativeEmuService(publish=False)
            eng = svc.new_engine()
            try:
                svc.load_image(eng, self.image, apply_relocs=self._relocs_on())
            except Exception as e:
                self._log(f"Laden in Engine FEHLER: {e!r}"); return
            snap_path = getattr(self, "_snapshot_path", None)
            if snap_path:
                try:
                    svc.load_snapshot(eng, snap_path)
                    self._log(f"Snapshot geladen: {snap_path}")
                except Exception as e:
                    self._log(f"Snapshot laden FEHLER: {e!r}"); return
            if cfg.get("stubs"):
                try:
                    done = svc.install_stubs(eng, cfg["stubs"], image=self.image)
                    self._log(f"Stubs gesetzt: {len(done)} ({', '.join('0x%x' % a for a in done) or '—'})")
                except Exception as e:
                    self._log(f"Stub-Setup FEHLER: {e!r}")
            args = list(cfg.get("args") or [])
            reg_args, stack_args = args[:8], args[8:]
            sp = cfg.get("sp")
            if stack_args:
                if sp is None:
                    self._log("Stack-Argumente gesetzt, aber kein SP angegeben."); return
                try:
                    for k, v in enumerate(stack_args):
                        if not isinstance(v, int):
                            self._log("Pointer/Buffer-Args nur in x0–x7 (Stack: nur Ganzzahlen)."); continue
                        eng.mem.write(sp + k * 8, int(v).to_bytes(8, "little"))
                except Exception as e:
                    self._log(f"Stack-Setup FEHLER: {e!r}"); return
            self.app.after(0, self.tab.start_progress)
            self._log(f"▶ Run {cfg['entry']}  x0..={', '.join((hex(a) if isinstance(a, int) else 'ptr/buf') for a in reg_args) or '—'}"
                      + (f"  (+{len(stack_args)} Stack-Args)" if stack_args else ""))
            try:
                res = svc.run(eng, cfg["entry"], reg_args, cfg.get("mem_overrides"),
                              sp, cfg.get("maxins", 2000000), image=self.image)
            except Exception as e:
                res = {"ok": False, "why": "exception", "error": repr(e)}
            finally:
                self.app.after(0, self.tab.stop_progress)
            if res.get("why") == "maxins":
                self._log(f"\u23f1 Instruktionsbudget erreicht @{res.get('at') or '?'} \u2014 kein Fehler: "
                          f"die Emulation lief bis zum maxins-Limit. Erhoehe maxins oder setze "
                          f"einen Breakpoint, um gezielt anzuhalten.")
                self._last_engine = eng
                return
            if not res.get("ok"):
                self._log(f"Run FEHLER [{res.get('why')}]: "
                          f"{res.get('error') or res.get('hint') or res.get('pc')}")
                return
            self._last_engine = eng
            self.app.after(0, lambda: self.tab.set_registers(res))
            cov = (f"Lauf OK · ret=0x{res['ret']:x} · entry={cfg['entry']} · "
                   f"x0..={', '.join((hex(a) if isinstance(a, int) else 'ptr/buf') for a in reg_args) or '—'}")
            self.app.after(0, lambda: self.tab.set_coverage(cov))
            self._log(f"Run fertig: ret=0x{res['ret']:x}")
            if res.get("out"):
                self._log("Out-Puffer: " + ", ".join(f"{k}={v}" for k, v in res["out"].items()))
        threading.Thread(target=_work, daemon=True).start()

    # ---- Debugger: Trace / Step / Lift (#GUI1) ----------------------------
    _DBG_BUDGET = 4000  # Fallback-Budget (Bloecke), wenn kein gueltiges maxins-Feld
    _DBG_SESSION = "secrets/probe/out/dbg"  # geteilter Sitzungsordner (Mensch + KI)

    def _dbg_budget(self):
        """Budget (Bloecke) fuer Resume/Auto-Lift aus dem Run-Panel-Feld 'maxins';
        Fallback _DBG_BUDGET, wenn leer/ungueltig. Im Main-Thread aufrufen (liest tk)."""
        try:
            m = int((self.tab.get_run_config() or {}).get("maxins"))
            if m > 0:
                return m
        except Exception:
            pass
        return self._DBG_BUDGET

    def _dbg_setup(self):
        """Frische Debug-Engine wie run_emulation aufbauen + Trace-Lauf initialisieren."""
        if self.image is None:
            self._log("Erst eine .so laden (kein Image)."); return None
        if not self._require_capstone():
            return None
        cfg = self.tab.get_run_config()
        if not cfg.get("entry"):
            self._log("Kein Entry angegeben (Symbol oder 0xAddr)."); return None
        try:
            from services.native_emu_service import NativeEmuService
            from services.native_trace_service import NativeTraceService
        except Exception as e:
            self._log(f"Debug-Services nicht ladbar: {e!r}"); return None
        svc = NativeEmuService(publish=False)
        eng = svc.new_engine()
        try:
            svc.load_image(eng, self.image, apply_relocs=self._relocs_on())
        except Exception as e:
            self._log(f"Laden in Engine FEHLER: {e!r}"); return None
        if cfg.get("stubs"):
            try:
                svc.install_stubs(eng, cfg["stubs"], image=self.image)
            except Exception as e:
                self._log(f"Stub-Setup FEHLER: {e!r}")
        try:
            ea = svc.resolve_entry(cfg["entry"], self.image)
            int_args, _out = svc.materialize_args(eng, cfg.get("args") or [])
            svc.apply_mem(eng, cfg.get("mem_overrides"))
        except Exception as e:
            self._log(f"Arg-/Mem-Setup FEHLER: {e!r}"); return None
        tr = NativeTraceService(publish=False)
        tr.begin(eng, ea, int_args[:8], sp=cfg.get("sp"), faithful=True)
        self._dbg_svc = svc
        self._dbg_eng = eng
        self._dbg_tr = tr
        self._dbg_bps = getattr(self, "_dbg_bps", set())  # persistente Breakpoints (GUI-Gutter)
        self._log(f"⟲ Debug bereit @0x{ea:x} (Relocs {'an' if self._relocs_on() else 'aus'}).")
        self._dbg_update()
        return eng

    def _dbg_require(self):
        if getattr(self, "_dbg_eng", None) is None:
            return self._dbg_setup()
        return self._dbg_eng

    def _dbg_update(self):
        eng = getattr(self, "_dbg_eng", None)
        if eng is None:
            return
        try:
            d = self._dbg_svc.snapshot_regs(eng); d["ret"] = d["x"][0]
            self.app.after(0, lambda: self.tab.set_registers(d))
        except Exception:
            pass
        try:
            trace = self._dbg_tr.get_trace(eng)
            self.app.after(0, lambda: self.tab.set_trace(trace))
        except Exception:
            pass
        try:
            pc = int(eng.cpu.pc)
            self.app.after(0, lambda: self.navigate(pc, set_entry=False))
        except Exception:
            pass

    def plugin_names(self):
        """Namen der entdeckten native_plugins/ (fuer die dynamische GUI)."""
        try:
            return self.registry.names() if self.registry else []
        except Exception:
            return []

    def plugin_contributions(self):
        """Stufe 2: deklarative GUI-Beitraege der Plugins (leer -> kein dynamischer Bereich)."""
        try:
            return self.registry.contributions() if self.registry else []
        except Exception:
            return []

    def plugin_action(self, action, plugin=None):
        """Dispatcher fuer Plugin-Beitrags-Aktionen. Bekannte Namen werden gemappt;
        unbekannte werden geloggt (erweiterbar fuer kuenftige Plugins)."""
        try:
            if action == "unpack":
                return self.unpack_dialog()
            self._log(f"Plugin-Aktion '{action}' (Plugin {plugin or '?'}) nicht implementiert.")
        except Exception as e:
            self._log(f"Plugin-Aktion FEHLER: {e!r}")

    def jni_run(self):
        """⚡ JNI: JNI_OnLoad mit der Fake-JNIEnv-Fassade (Reloc+Imports) laufen lassen,
        JNI-Call-Liste fuellen. Haelt an den 4 RASP-Routinen; Budget = maxins-Feld."""
        if self.image is None:
            self._log("Erst eine .so laden (kein Image)."); return
        if not self._require_capstone():
            return
        _budget = self._dbg_budget()
        self._log("⚡ JNI-Lauf gestartet (Budget %d) — laeuft im Hintergrund, kann einige Sekunden dauern …" % _budget)

        def _w():
            try:
                from services.native_emu_service import NativeEmuService
                from services.native_ffi_service import NativeFfiService
                from services.native_trace_service import NativeTraceService
                from services import native_jni_env_service as Jm
                from services.native_jni_env_service import NativeJniEnvService
                svc = NativeEmuService(publish=False)
                eng = svc.new_engine()
                svc.load_image(eng, self.image, apply_relocs=True)
                ffi = NativeFfiService(publish=False); ffi.wire_imports(eng, self.image)
                jni = NativeJniEnvService(publish=False); jni.install(eng, ffi, set_entry_reg=None)
                cfg = self.tab.get_run_config()
                entry = svc.resolve_entry(cfg.get("entry") or "JNI_OnLoad", self.image)
                tr = NativeTraceService(publish=False)
                tr.begin(eng, entry, args=[Jm.VM_OBJ], faithful=True)
                r = tr.run(eng, breakpoints=[], maxins=_budget, mode="block")
                self._jni = jni
                calls = [c for c in jni.get_calls()
                         if c.get("fn") in ("FindClass", "RegisterNatives") or "method" in c]
                self.app.after(0, lambda: self.tab.set_jni_calls(calls))
                s = jni.summary()
                self._log(f"⚡ JNI-Lauf: stop @{r.get('pc_hex')} ({r.get('steps')} Schritte) · "
                          f"Calls {s['calls']} · RegisterNatives {s['natives']} · Kategorien {s['by_category']}")
            except Exception as e:
                self._log(f"JNI-Lauf FEHLER: {e!r}")
        import threading
        threading.Thread(target=_w, daemon=True).start()

    def jni_show_popup(self, row):
        """Popup: Smali-Body + Klassifizierung + Vorschlagsrueckgabe + Buttons (Durchreichen/
        Manipulieren/Zum Smali springen)."""
        import tkinter as tk
        from tkinter import ttk, messagebox, simpledialog
        cls = row.get("class"); name = row.get("method"); sig = row.get("sig")
        jni = getattr(self, "_jni", None)
        if jni and cls and name and sig:
            info = jni.smali_of(cls, name, sig)
            title = f"JNI: {cls}->{name}{sig}"
        else:
            info = {"ok": False, "category": row.get("category"), "proposed": row.get("proposed", "-"),
                    "propsrc": "-", "retchar": "-", "file": None,
                    "body": f"{row.get('fn')}  —  Klasse: {cls or '(leer)'}\n\n"
                            f"(Kein Methoden-Smali fuer diese Zeile. Das reiche Popup mit Body + "
                            f"Klassifizierung erscheint bei GetMethodID/Call*-Zeilen.)"}
            title = f"JNI: {row.get('fn')} {cls or ''}"
        top = tk.Toplevel(self.app); top.title(title); top.geometry("840x560")
        head = ttk.Frame(top); head.pack(fill="x", padx=8, pady=6)
        ttk.Label(head, text=(f"{cls}->{name}{sig}" if name else f"{row.get('fn')}  {cls or '(leer)'}"),
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(head, text=(f"Kategorie: {info.get('category') or '—'}    ·    "
                              f"Vorschlag: {info.get('proposed')} ({info.get('propsrc')})    ·    "
                              f"Rueckgabetyp: {info.get('retchar')}    ·    aufgeloest: {info.get('ok')}")).pack(anchor="w")
        if info.get("file"):
            ttk.Label(head, text=str(info["file"]), foreground="gray").pack(anchor="w")
        tf = ttk.Frame(top); tf.pack(fill="both", expand=True, padx=8)
        sb = ttk.Scrollbar(tf, orient="vertical"); sb.pack(side="right", fill="y")
        txt = tk.Text(tf, wrap="none", font=("Consolas", 9), yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True); sb.config(command=txt.yview)
        txt.insert("1.0", info.get("body") or "(kein Smali-Body gefunden)")
        txt.config(state="disabled")
        bf = ttk.Frame(top); bf.pack(fill="x", padx=8, pady=6)
        ttk.Button(bf, text="Durchreichen", command=top.destroy).pack(side="left", padx=3)

        def _manip():
            v = simpledialog.askstring("Manipulieren",
                                       "Erzwungener Rueckgabewert (Dez oder 0x..):",
                                       initialvalue=str(info.get("proposed", 0)), parent=top)
            if v is not None:
                try:
                    jni.set_override(cls, name, sig, int(v, 0))
                    self._log(f"JNI-Override: {cls}->{name}{sig} = {v}")
                except Exception as e:
                    self._log(f"Override FEHLER: {e!r}")
        ttk.Button(bf, text="Manipulieren", command=_manip).pack(side="left", padx=3)
        ttk.Button(bf, text="Zum Smali springen",
                   command=lambda: self.jni_goto_smali(cls, name, sig)).pack(side="left", padx=3)

    def jni_goto_smali(self, cls, name, sig):
        """Best-effort: Smali-Datei/Methode oeffnen bzw. Pfad in die Konsole (im Smali Studio oeffnen)."""
        try:
            jni = getattr(self, "_jni", None)
            cf = jni._class_file(cls) if jni else None
            if not cf:
                self._log(f"Smali-Datei fuer {cls} nicht gefunden."); return
            opened = False
            for attr in ("open_smali", "goto_smali", "smali_open"):
                fn = getattr(self.app, attr, None)
                if callable(fn):
                    try:
                        fn(cf, (name or "") + (sig or "")); opened = True; break
                    except Exception:
                        pass
            if not opened:
                self._log(f"Smali: {cf}   (Methode {name}{sig}) — im Smali Studio oeffnen.")
        except Exception as e:
            self._log(f"Smali-Sprung FEHLER: {e!r}")

    def dbg_start(self):
        """(Neu) Debug-Sitzung am aktuellen Entry/Arg-Stand starten (pc am Entry)."""
        self._dbg_eng = None
        self._dbg_setup()

    def dbg_step_block(self):
        eng = self._dbg_require()
        if eng is None:
            return
        r = self._dbg_tr.step_block(eng)
        self._log(f"⏭ Block → pc={r.get('pc_hex')}" + ("  (fertig)" if r.get("done") else ""))
        self._dbg_update()

    def dbg_step_insn(self):
        eng = self._dbg_require()
        if eng is None:
            return
        r = self._dbg_tr.step_insn(eng)
        ins = r.get("insn") or {}
        self._log(f"⏱ {ins.get('mnem','?')} {ins.get('ops','')} → pc={r.get('pc_hex')}"
                  + ("  (fertig)" if r.get("done") else ""))
        self._dbg_update()

    def dbg_resume(self):
        eng = self._dbg_require()
        if eng is None:
            return
        _budget = self._dbg_budget()  # tk-Feld im Main-Thread lesen

        def _w():
            _bps = list(getattr(self, "_dbg_bps", set()))
            r = self._dbg_tr.resume(eng, breakpoints=_bps, maxins=_budget,
                                    mode=("insn" if _bps else "block"))
            st = ("Breakpoint" if r.get("paused") else "fertig" if r.get("done")
                  else "Budget" if r.get("budget") else "?")
            self._log(f"⏩ Resume (Budget {_budget}) → {st} @ {r.get('pc_hex')} ({r.get('steps')} Schritte)")
            self._dbg_update()

        import threading
        threading.Thread(target=_w, daemon=True).start()

    def dbg_lift_python(self):
        """🐍 Trace→Python: aktuelle Spur liften — oder, ohne Sitzung, einmal komplett tracen + liften."""
        if getattr(self, "_dbg_eng", None) is None:
            if self._dbg_setup() is None:
                return
            _budget = self._dbg_budget()

            def _w0():
                self._dbg_tr.run(self._dbg_eng, breakpoints=[], maxins=_budget, mode="block")
                self._dbg_update()
                self._dbg_do_lift()

            import threading
            threading.Thread(target=_w0, daemon=True).start()
            return
        self._dbg_do_lift()

    def _dbg_do_lift(self):
        eng = getattr(self, "_dbg_eng", None)
        if eng is None:
            return
        try:
            from services.native_trace_decompile_service import NativeTraceDecompileService
            lr = NativeTraceDecompileService().lift_to_function(eng)
        except Exception as e:
            self._log(f"Lift FEHLER: {e!r}"); return
        if lr.get("ok"):
            src = lr["source"]
            self._log(f"🐍 Trace→Python: {lr['visits']} Besuche · {lr['unique']} Bloecke → Funktion geliftet.")
        else:
            src = f"# Lift unvollstaendig: why={lr.get('why')}\n# {lr.get('residual') or lr.get('addr') or ''}"
            self._log(f"🐍 Trace→Python: unvollstaendig ({lr.get('why')}).")
        self.app.after(0, lambda: self.tab.set_python_lift(src))

    def set_breakpoints(self, addrs):
        """Von der GUI gesetzte Breakpoints uebernehmen (int-Adressen)."""
        out = set()
        for a in (addrs or []):
            try:
                out.add(int(a) if isinstance(a, int) else int(str(a), 0))
            except Exception:
                pass
        self._dbg_bps = out

    def dbg_status(self):
        """⇅ Abgleich: vergleicht deinen Stand mit der geteilten Sitzung (wer ist neuer?)."""
        def _w():
            try:
                from services.native_debug_session_service import NativeDebugSessionService
                pk = NativeDebugSessionService().peek(self._DBG_SESSION)
            except Exception as e:
                self._log(f"Abgleich FEHLER: {e!r}"); return
            fmeta = pk.get("meta")
            if not fmeta:
                self._log("⇅ Abgleich: keine geteilte Sitzung vorhanden (noch kein ⬆ An KI)."); return
            fseq = int(fmeta.get("seq", 0) or 0)
            base = int(getattr(self, "_dbg_base_seq", 0) or 0)
            eng = getattr(self, "_dbg_eng", None)
            local_pc = (f"0x{int(eng.cpu.pc):x}" if eng is not None else "—")
            if fseq > base:
                rel = f"geteilt ist NEUER (seq {fseq} > dein {base}) → ⬇ Von KI laden"
            elif eng is not None:
                rel = f"du bist auf/ueber dem geteilten Stand (seq {base}) → ⬆ An KI zum Teilen"
            else:
                rel = "kein lokaler Debug-Stand (erst ⟲ Dbg)"
            tr = pk.get("trace") or []
            self._log(f"⇅ Abgleich: geteilt seq {fseq} · writer {fmeta.get('writer')} · "
                      f"pc 0x{int(fmeta.get('pc', 0)):x} · {len(tr)} Bloecke · {fmeta.get('ts')}  |  "
                      f"dein pc {local_pc}, letzter Sync-seq {base}  →  {rel}")
        import threading
        threading.Thread(target=_w, daemon=True).start()

    def dbg_sync(self):
        """⬆ An KI: aktuellen Debug-Stand in die geteilte Sitzung schreiben (push), mit Clobber-Schutz."""
        eng = getattr(self, "_dbg_eng", None)
        if eng is None:
            self._log("Keine Debug-Sitzung — erst ⟲ Dbg / Step."); return
        from services.native_debug_session_service import NativeDebugSessionService
        sess = NativeDebugSessionService()
        try:
            fmeta = sess.peek(self._DBG_SESSION, with_trace=False).get("meta") or {}
        except Exception:
            fmeta = {}
        base = int(getattr(self, "_dbg_base_seq", 0) or 0)
        fseq = int(fmeta.get("seq", 0) or 0)
        force = False
        if fmeta and fseq > base:   # jemand (KI) hat seit deinem letzten Abgleich geschrieben
            try:
                from tkinter import messagebox
                force = messagebox.askyesno(
                    "Geteilte Sitzung ueberschreiben?",
                    f"Die geteilte Sitzung wurde seit deinem letzten Abgleich von "
                    f"'{fmeta.get('writer')}' auf seq {fseq} fortgeschrieben.\n\n"
                    f"Mit DEINEM Stand ueberschreiben?\n(Nein = erst ⬇ Von KI laden)")
            except Exception:
                force = False
            if not force:
                self._log(f"⬆ An KI abgebrochen — geteilt ist neuer (seq {fseq}, {fmeta.get('writer')}). Erst ⬇ Von KI."); return

        def _w():
            try:
                cfg = self.tab.get_run_config()
                meta = {"entry": cfg.get("entry"), "sp": cfg.get("sp"),
                        "relocs": self._relocs_on(),
                        "lib": getattr(self, "image_path", None),
                        "bps": sorted(getattr(self, "_dbg_bps", set())), "mode": "block"}
                r = sess.save(eng, self._DBG_SESSION, meta=meta, writer="gui",
                              base_seq=base, force=force)
                if not r.get("ok") and r.get("why") == "conflict":
                    self._log(f"⬆ An KI: Konflikt (Datei seq {r['file_seq']} > dein {r['base_seq']}). Erst ⬇ Von KI."); return
                self._dbg_base_seq = int(r.get("seq", base + 1))
                self._log(f"⬆ An KI → geschrieben (seq {r['seq']}, pc {r['pc']}, {r['trace']} Bloecke).")
            except Exception as e:
                self._log(f"An-KI FEHLER: {e!r}")

        import threading
        threading.Thread(target=_w, daemon=True).start()

    def _resolve_lib(self, libpath):
        """Lib-Pfad aus der Sitzung aufloesen (as-is / absolut / tools/lib/<basename>)."""
        if not libpath:
            return None
        cands = [libpath, os.path.abspath(libpath),
                 os.path.join("tools", "lib", os.path.basename(libpath))]
        for c in cands:
            try:
                if c and os.path.isfile(c):
                    return c
            except Exception:
                pass
        return None

    def dbg_load_session(self):
        """⬇ Von KI: geteilten Stand laden. Fehlt die Lib, wird sie aus meta['lib'] nachgeladen."""
        from services.native_debug_session_service import NativeDebugSessionService
        sess = NativeDebugSessionService()
        try:
            m0 = sess.peek(self._DBG_SESSION, with_trace=False).get("meta") or {}
        except Exception:
            m0 = {}
        libpath = self._resolve_lib(m0.get("lib"))

        def _restore():
            def _w():
                try:
                    from services.native_emu_service import NativeEmuService
                    from services.native_trace_service import NativeTraceService
                    svc = NativeEmuService(publish=False)
                    eng = svc.new_engine()
                    ld = sess.load(eng, self._DBG_SESSION)
                    if not ld.get("ok"):
                        self._log("⬇ Von KI: keine Sitzung gefunden."); return
                    self._dbg_svc = svc
                    self._dbg_eng = eng
                    self._dbg_tr = NativeTraceService(publish=False)
                    m = ld.get("meta") or {}
                    self._dbg_bps = set(m.get("bps") or [])
                    self._dbg_base_seq = int(m.get("seq", 0) or 0)
                    self.app.after(0, lambda: self.tab.set_breakpoint_marks(self._dbg_bps))
                    self._log(f"⬇ Von KI: Sitzung geladen (seq {ld.get('seq')}, pc {ld['pc']}, "
                              f"{ld['trace_len']} Bloecke, writer {m.get('writer')}).")
                    self._dbg_update()
                except Exception as e:
                    self._log(f"Von-KI FEHLER: {e!r}")
            import threading
            threading.Thread(target=_w, daemon=True).start()

        if self.image is None:
            if libpath:
                self._log(f"⬇ Von KI: lade zugehoerige Lib {os.path.basename(libpath)} …")
                self._load_so(libpath, then=_restore)
            else:
                self._log("⬇ Von KI: keine (auffindbare) Lib in der Sitzung — lade die .so manuell, "
                          "dann erneut ⬇ Von KI (Register/Trace lade ich jetzt trotzdem).")
                _restore()
        else:
            if libpath and os.path.basename(libpath) != os.path.basename(getattr(self, "image_path", "") or ""):
                self._log(f"⚠ Geladene Lib ({os.path.basename(getattr(self, 'image_path', '?') or '?')}) "
                          f"≠ Sitzungs-Lib ({os.path.basename(libpath)}) — Adressen koennten abweichen.")
            _restore()

    def snapshot_save(self):
        """Speichert den Zustand des letzten Laufs (Mem+Register) als .pkl.xz."""
        eng = getattr(self, "_last_engine", None)
        if eng is None:
            self._log("Noch kein Lauf — erst ▶ Run (Snapshot speichert den Zustand nach dem Lauf)."); return
        try:
            from tkinter import filedialog
            p = filedialog.asksaveasfilename(defaultextension=".pkl.xz",
                                             filetypes=[("Snapshot", "*.pkl.xz"), ("Alle", "*.*")])
            if not p:
                return
            from services.native_emu_service import NativeEmuService
            NativeEmuService(publish=False).save_snapshot(eng, p)
            self._log(f"Snapshot gespeichert: {p}")
        except Exception as e:
            self._log(f"Snapshot speichern FEHLER: {e!r}")

    def snapshot_set_load(self):
        """Waehlt einen Snapshot, der beim naechsten ▶ Run vor der Ausfuehrung geladen wird."""
        try:
            from tkinter import filedialog
            p = filedialog.askopenfilename(filetypes=[("Snapshot", "*.pkl.xz"), ("Alle", "*.*")])
            if not p:
                return
            self._snapshot_path = p
            self._log(f"Snapshot fuer naechsten Run gesetzt: {p} (▶ Run laedt ihn vor der Ausfuehrung). "
                      "Zum Deaktivieren Native Studio '🧹 Leeren'.")
        except Exception as e:
            self._log(f"Snapshot-Auswahl FEHLER: {e!r}")

    def show_memory(self):
        """Liest 64 Bytes an der im Memory-Reiter angegebenen Adresse aus dem letzten Lauf."""
        eng = getattr(self, "_last_engine", None)
        if eng is None:
            self._log("Noch kein Lauf - erst '▶ Run' ausfuehren."); return
        tok = self.tab.mem_addr.get().strip()
        try:
            a = int(tok, 16) if tok.lower().startswith("0x") else int(tok, 0)
        except Exception:
            self._log(f"Ungueltige Adresse: {tok!r}"); return
        try:
            data = bytes(eng.mem.read(a, 64))
        except Exception as e:
            self._log(f"Memory-Read FEHLER: {e!r}"); return
        self.app.after(0, lambda: self.tab.set_memory(a, data))


    def load_so_dialog(self):
        """Datei wählen (Main-Thread), dann im Hintergrund parsen."""
        path = filedialog.askopenfilename(
            title="Native Bibliothek / ELF laden (Original bzw. Analyse-Arbeitsbereich)",
            filetypes=[("ELF/.so", "*.so *.bin *.elf"), ("Alle Dateien", "*.*")])
        if not path:
            return
        self._load_so(path)

    def _load_so(self, path, then=None):
        def _work():
            try:
                img = ElfImage.parse(open(path, "rb").read())
                self.image = img
                self.image_path = path
                self.index = None
                self._short2addr = None
                self.app.after(0, self._reset_history)
                rows = [("meta", "Datei", os.path.basename(path)),
                        ("meta", "AArch64", str(img.is_aarch64)),
                        ("meta", "Entry", f"0x{img.entry:x}")]
                for i, s in enumerate(img.segments):
                    rows.append(("seg", f"PT_LOAD[{i}] {s.perm_str()}", f"0x{s.vaddr:x}"))
                _syms = list(img.dynsyms.items())
                _defined = sorted(((n, sy) for n, sy in _syms if sy.value), key=lambda t: t[1].value)
                _imports = sorted(n for n, sy in _syms if not sy.value)
                for _n, _sy in _defined:
                    _t = _sy.info & 0xf  # STT_FUNC=2, STT_GNU_IFUNC=10 -> Code; sonst Daten (RTTI/vtable/...)
                    rows.append(("fn" if _t in (2, 10) else "data", _n, f"0x{_sy.value:x}"))
                for _n in _imports:
                    rows.append(("import", _n, "0x0"))
                self._outline_base_rows = rows
                self.app.after(0, lambda: self.tab.set_outline(self._apply_anno(rows)))
                self._log(f"geladen: {os.path.basename(path)} — "
                          f"{len(img.segments)} Segmente, {len(img.dynsyms)} Symbole, "
                          f"AArch64={img.is_aarch64}")
                self.app.after(0, lambda: self.tab.set_loaded(f"📦 {os.path.basename(path)} · {len(img.dynsyms)} Symbole"))
                if not img.is_aarch64:
                    self._log("Hinweis: kein AArch64-ELF (oder gepackt) — Entpacken folgt mit M3.")
                else:
                    self._log("Lade fertig. Baue XREF-Index (Fast) im Hintergrund — Suche/XREF danach sofort.")
                    self._build_index(img)
                if then:
                    self.app.after(0, then)
            except Exception as e:
                self._log(f"Laden FEHLER: {e!r}")
        threading.Thread(target=_work, daemon=True).start()

    def _choose_from_list(self, title, items):
        """Kleiner modaler Auswahldialog (Mehrfachauswahl). -> Liste gewaehlter Items oder None."""
        top = tk.Toplevel(self.tab.winfo_toplevel())
        top.title(title)
        top.geometry("560x340")
        top.attributes("-topmost", True)
        tk.Label(top, text="Gepackte Libs im APK — auswaehlen (Strg/Shift fuer mehrere):").pack(anchor="w", padx=8, pady=(8, 2))
        lb = tk.Listbox(top, selectmode="extended")
        for it in items:
            lb.insert("end", it)
        lb.select_set(0, "end")   # Default: alle
        lb.pack(fill="both", expand=True, padx=8, pady=4)
        res = {"sel": None}

        def _ok():
            idx = lb.curselection()
            res["sel"] = [items[i] for i in idx] if idx else list(items)
            top.destroy()

        def _cancel():
            res["sel"] = None
            top.destroy()

        from tkinter import ttk as _ttk
        bar = _ttk.Frame(top); bar.pack(fill="x", padx=8, pady=6)
        _ttk.Button(bar, text="Entpacken", command=_ok).pack(side="right", padx=4)
        _ttk.Button(bar, text="Abbrechen", command=_cancel).pack(side="right", padx=4)
        top.transient(self.tab.winfo_toplevel()); top.grab_set()
        self.tab.winfo_toplevel().wait_window(top)
        return res["sel"]

    def unpack_dialog(self):
        """APK oder gepackte .so über ein Decrypter-Plugin (native_plugins/) entschlüsseln."""
        if not self.registry or not self.registry.plugins():
            messagebox.showinfo("Native Studio",
                                "Kein Decrypter-Plugin in native_plugins/ gefunden.")
            return
        packed = filedialog.askopenfilename(
            title="APK oder gepackte .so wählen",
            filetypes=[("APK oder .so", "*.apk *.so"), ("Alle Dateien", "*.*")])
        if not packed:
            return
        plugins = self.registry.find_for(packed) or self.registry.plugins()
        plugin = plugins[0]

        # APK: gepackte Libs ermitteln + ggf. auswaehlen (Main-Thread, da modal)
        from core.native.unpack import is_zip as _is_zip
        entries = [None]
        if _is_zip(packed):
            try:
                targets = plugin.list_targets(packed) if hasattr(plugin, "list_targets") else []
            except Exception:
                targets = []
            if not targets:
                messagebox.showinfo("Native Studio", "Keine gepackte Lib in lib/arm64-v8a/ im APK gefunden.")
                return
            if len(targets) == 1:
                entries = [targets[0]]
            else:
                byname = {t.split("/")[-1]: t for t in targets}
                sel = self._choose_from_list("Gepackte Libs im APK", list(byname.keys()))
                if not sel:
                    return
                entries = [byname[s] for s in sel]

        def _work():
            self._log(f"Entpacken mit Plugin '{plugin.name}' …")
            ok_any = False
            for entry in entries:
                label = (entry.split("/")[-1] if entry else os.path.basename(packed))
                try:
                    res = plugin.unpack(packed, apk_entry=entry) if entry else plugin.unpack(packed)
                except Exception as e:
                    self._log(f"Entpacken FEHLER ({label}): {e!r}")
                    continue
                if res.get("ok"):
                    ok_any = True
                    self._log(f"OK ({label}): {res.get('info', res.get('out_path', ''))}")
                    self._load_so(res["out_path"])
                else:
                    self._log(f"Entpacken nicht möglich [{res.get('why')}] ({label}): {res.get('hint', '')}")
            if not ok_any:
                self._log("Keine Lib erfolgreich entpackt.")
        threading.Thread(target=_work, daemon=True).start()

    def disasm_entry(self, count=48):
        """Disassembliert ab Entry (bzw. erstem Symbol, falls Entry==0)."""
        if self.image is None:
            messagebox.showinfo("Native Studio", "Erst eine .so/ELF laden.")
            return
        addr = self.image.entry
        if not addr and self.index is not None and self.index.syms:
            addr = self.index.syms[0][0]
        if not addr:
            xsegs = [s for s in self.image.segments if getattr(s, "flags", 0) & 0x1]
            if xsegs:
                addr = xsegs[0].vaddr
                self._log(f"Entry ist 0x0 (Library) — disassembliere ab erstem ausfuehrbaren Segment 0x{addr:x}.")
        if not addr:
            self._log("Kein sinnvoller Startpunkt (Entry 0x0, kein Symbol/Segment). Gib rechts im XREF-Feld eine Adresse ein.")
            return
        self.disasm_at(addr, count)

    def disasm_at(self, addr, count=48):
        """Disassembliert `count` Instruktionen ab `addr` in die Disassembly-Ansicht."""
        if self.image is None:
            return

        def _work():
            if not self._require_capstone():
                return
            try:
                rows = self._disasm_rows(addr, count, mark_addr=addr)
                header = f"ab 0x{addr:x} · {count} Instruktionen (linear)"
                self.app.after(0, lambda: self.tab.set_disasm_rows(header, rows))
                self._log(f"Disassembly ab 0x{addr:x} ({count} Instruktionen).")
            except Exception as e:
                self._log(f"Disasm FEHLER: {e!r}")
        threading.Thread(target=_work, daemon=True).start()

    # ---- XREF / CallGraph (NativeIndex) -----------------------------------
    def base_dir(self):
        try:
            return self.app.cfg.config.get("BASE_DIR", os.getcwd())
        except Exception:
            return os.getcwd()

    def _session_plugin(self):
        """Plugin-Bucket im Analyse-Arbeitsbereich (erstes Plugin, sonst 'native')."""
        try:
            names = self.registry.names() if self.registry else []
        except Exception:
            names = []
        return names[0] if names else "native"

    def _build_index(self, img, fast=True, then=None):
        """Baut den XREF/CallGraph-Index (NativeIndex) THREADED. Cache stabil +
        sitzungsuebergreifend unter data/native/<plugin>/index/ (SHA-gegatet) ->
        nur EINMAL pro Lib teuer, danach aus Cache. `then` laeuft nach Erfolg im
        Main-Thread (lazy Trigger holt die ausloesende Aktion nach)."""
        if self.image is None or not self._require_capstone():
            return
        if getattr(self, "_indexing", False):
            self._log("Index wird bereits gebaut — bitte warten.")
            return

        def _work():
            try:
                cache = os.path.join(_unpack.plugin_root(self.base_dir(), self._session_plugin()), "index")
            except Exception:
                cache = None
            self._indexing = True
            self.app.after(0, self.tab.start_progress)
            self._log("Baue XREF/CallGraph-Index … (einmalig pro Lib, danach aus Cache)")
            try:
                idx = NativeIndex().build(img, cache_dir=cache, fast=fast, listener=lambda ev, p: None)
                self.index = idx
                nsym = len(idx.syms)
                msg = (f"Index bereit: {idx.count} Instruktionen, {len(idx.edges)} Kanten, "
                       f"{nsym} Symbole. Suche/XREF jetzt verfuegbar (Doppelklick auf eine Adresse -> XREF).")
                if nsym < 8:
                    msg += (" Hinweis: wenige/keine Symbole (obfuskierte/stripped Lib) — der "
                            "Funktions-CallGraph (symbolbasiert) deckt v. a. Go-Libs ab und bleibt "
                            "hier duenn; Instruktions-XREF funktioniert dennoch.")
                self._log(msg)
                try:
                    self._emit_enriched_outline()
                except Exception as _e:
                    self._log(f"Outline-Anreicherung uebersprungen: {_e!r}")
                cbs = ([then] if then else []) + self._pending_after_index
                self._pending_after_index = []
                for _cb in cbs:
                    self.app.after(0, _cb)
            except Exception as e:
                self._log(f"Index FEHLER: {e!r}")
            finally:
                self._indexing = False
                self.app.after(0, self.tab.stop_progress)
        threading.Thread(target=_work, daemon=True).start()

    def _ensure_index(self, then=None):
        """Lazy: baut den Index bei Bedarf und holt danach `then` nach."""
        if self.index is not None:
            if then:
                then()
            return True
        if self.image is None or not getattr(self.image, "is_aarch64", False):
            self._log(self._index_state_msg())
            return False
        if getattr(self, "_indexing", False):
            if then:
                self._pending_after_index.append(then)
                self._log("Index wird gebaut, danach fuehre ich die Aktion automatisch aus …")
            return False
        self._build_index(self.image, then=then)
        return False

    def _index_state_msg(self):
        """Praezise Statusmeldung statt des irrefuehrenden 'Kein Index'."""
        if self.image is None:
            return "Erst eine .so/ELF laden (📥)."
        if not getattr(self.image, "is_aarch64", False):
            return "Lib ist gepackt/kein AArch64 — erst '📦 entpacken'."
        if getattr(self, "_indexing", False):
            return "Index wird noch gebaut — bitte warten (Fortschrittsbalken oben laeuft)."
        return "Kein Index vorhanden — bitte die .so neu laden (📥)."

    def xref_at(self, addr):
        """„Wer referenziert 0xADDR?" — direkte (Branch-/Adress-)Referenzen + Caller-Funktionen."""
        if self.index is None:
            if self.image is not None and getattr(self.image, "is_aarch64", False) and not getattr(self, "_indexing", False):
                self._log("Index wird gebaut, dann zeige ich die XREF automatisch …")
                self._ensure_index(then=lambda: self.xref_at(addr))
            else:
                self._log(self._index_state_msg())
            return
        if addr == 0:
            self._log("Adresse 0x0 = undefiniertes/importiertes Symbol — kein Ort in diesem Modul.")
            self.app.after(0, lambda: self.tab.set_xref("0x0 (Import/undefiniert)", []))
            return

        def _work():
            try:
                idx = self.index
                xr = idx.xrefs_to(addr)
                tfunc = idx.func_at(addr)
                name2addr = {n: a for a, n in idx.syms}
                rows = []
                for src, mnem in xr:
                    rows.append((mnem, f"0x{src:x}", idx.func_at(src) or "—"))
                callers = sorted(idx.callers_of(tfunc)) if tfunc else []
                for cf in callers:
                    ca = name2addr.get(cf)
                    rows.append(("ruft ->", f"0x{ca:x}" if ca is not None else "", cf))
                title = f"0x{addr:x}" + (f"  ({tfunc})" if tfunc else "")
                self.app.after(0, lambda: self.tab.set_xref(title, rows))
                note = f"XREF 0x{addr:x}: {len(xr)} direkte Referenz(en)"
                if tfunc:
                    note += f", {len(callers)} Caller-Funktion(en) auf '{tfunc}'"
                if not xr and not callers:
                    note += " — nichts gefunden (ggf. nur indirekt/virtuell erreicht)."
                self._log(note)
            except Exception as e:
                self._log(f"XREF FEHLER: {e!r}")
        threading.Thread(target=_work, daemon=True).start()

    def goto(self, addr):
        """Zur Adresse springen (Alias auf navigate)."""
        self.navigate(addr)

    def goto_text(self, text):
        """Adresse ODER Symbol aus dem Gehe-zu-Feld aufloesen und anspringen (Fenster um die Adresse)."""
        s = (text or "").strip()
        if not s:
            return
        try:
            from services.native_emu_service import NativeEmuService
            a = NativeEmuService(publish=False).resolve_entry(s, self.image)
        except Exception:
            self._log(f"Gehe zu: nicht aufloesbar: {s!r}"); return
        self.navigate(int(a), set_entry=False)

    # --- Navigation + baum-basierte Historie (nach Smali-Studio-Vorbild) ---
    def navigate(self, addr, add_to_history=True, set_entry=False):
        """Zentrale Navigation: Disassemblieren + XREF + Historie, mit UI-Spiegelung
        (XREF-Feld + Outline-Markierung; Run-Entry nur bei expliziter Funktionswahl)."""
        if self.image is None:
            return
        try:
            self.app.after(0, lambda: self._mirror_nav(addr, set_entry))
        except Exception:
            pass
        if addr == 0:
            self._log("Adresse 0x0 = importiertes/undefiniertes Symbol - nicht in dieser Lib "
                      "definiert (kein Ort zum Anspringen).")
            self.app.after(0, lambda: self.tab.set_xref("0x0 (Import/undefiniert)", []))
            return
        name = None
        if self.index is not None:
            try:
                name = self.index.func_at(addr)
            except Exception:
                name = None
        if add_to_history:
            self._push_history(addr, name)
        self.disasm_func(addr)
        self.xref_at(addr)

    def _mirror_nav(self, addr, set_entry=False):
        """UI-Spiegelung einer Navigation (Main-Thread): XREF-Feld, Run-Entry, Outline-Markierung."""
        try:
            self.tab.xref_addr.delete(0, "end"); self.tab.xref_addr.insert(0, f"0x{addr:x}")
        except Exception:
            pass
        if set_entry:
            try:
                self.tab.run_entry.delete(0, "end"); self.tab.run_entry.insert(0, f"0x{addr:x}")
            except Exception:
                pass
        try:
            self.tab.select_outline_addr(addr)
        except Exception:
            pass

    def _reset_history(self):
        self.history_nodes = {}
        self.history_root_id = None
        self.current_history_id = None
        self.history_counter = 0
        self._refresh_history_tree()

    def _push_history(self, addr, name):
        new_id = f"nhist_{self.history_counter}"; self.history_counter += 1
        node = {"id": new_id, "addr": addr, "name": name, "parent": self.current_history_id, "children": []}
        is_dup = False
        if self.current_history_id and self.current_history_id in self.history_nodes:
            parent = self.history_nodes[self.current_history_id]
            if parent["children"]:
                last = self.history_nodes[parent["children"][-1]]
                if last["addr"] == addr:
                    self.current_history_id = last["id"]; self.history_counter -= 1; is_dup = True
        if not is_dup:
            self.history_nodes[new_id] = node
            if self.current_history_id and self.current_history_id in self.history_nodes:
                self.history_nodes[self.current_history_id]["children"].append(new_id)
            elif not self.history_root_id:
                self.history_root_id = new_id
            self.current_history_id = new_id
        self._refresh_history_tree()

    def load_history_node(self, node_id):
        node = self.history_nodes.get(node_id)
        if not node:
            return
        self.current_history_id = node_id
        self.navigate(node["addr"], add_to_history=False)
        self._refresh_history_tree()

    def navigate_back(self):
        if not self.current_history_id:
            return
        node = self.history_nodes.get(self.current_history_id)
        if node and node["parent"]:
            self.load_history_node(node["parent"])

    def navigate_forward(self):
        if not self.current_history_id:
            return
        node = self.history_nodes.get(self.current_history_id)
        if node and node["children"]:
            self.load_history_node(node["children"][-1])

    def _build_history_tree(self, parent_iid, node_id):
        node = self.history_nodes[node_id]
        if node["name"]:
            label = f"{node['name'].split('(')[0][:40]} @0x{node['addr']:x}"
        else:
            label = f"0x{node['addr']:x}"
        tags = ("current",) if node_id == self.current_history_id else ()
        self.tab.tree_history.insert(parent_iid, "end", iid=node_id, text=label, tags=tags, open=True)
        for ch in node["children"]:
            self._build_history_tree(node_id, ch)

    def _refresh_history_tree(self):
        tree = getattr(self.tab, "tree_history", None)
        if tree is None:
            return
        for i in tree.get_children():
            tree.delete(i)
        # Flache, chronologische Liste statt kaskadierender Einrueckung (#164).
        # Branching bleibt im Datenmodell (parent/children) fuer back/forward erhalten.
        for nid, node in self.history_nodes.items():
            nm = self._short_name(node["name"]) if node.get("name") else ""
            label = (f"{nm.split('(')[0][:40]} @0x{node['addr']:x}" if nm else f"0x{node['addr']:x}")
            tags = ("current",) if nid == self.current_history_id else ()
            tree.insert("", "end", iid=nid, text=label, tags=tags)
        if self.current_history_id:
            try:
                tree.selection_set(self.current_history_id); tree.see(self.current_history_id)
            except Exception:
                pass

    def open_search(self):
        if self.index is None:
            if self.image is not None and getattr(self.image, "is_aarch64", False) and not getattr(self, "_indexing", False):
                self._log("Index wird gebaut, dann oeffne ich die Suche automatisch …")
                self._ensure_index(then=self.open_search)
            else:
                self._log(self._index_state_msg())
            return
        try:
            from ui.dialogs.native_search_dialog import NativeGlobalSearchWindow
        except Exception as e:
            self._log(f"Such-Dialog nicht ladbar: {e!r}")
            return
        if getattr(self, "_search_win", None) is not None:
            try:
                if self._search_win.winfo_exists():
                    self._search_win.lift(); self._search_win.focus_force(); return
            except Exception:
                pass
        top = self.tab.winfo_toplevel()
        self._search_win = NativeGlobalSearchWindow(top, self.index, self.image, lambda a: self.navigate(a, set_entry=True))

    def decompile_button(self):
        """C-Button: >1 Funktion in der Outline markiert -> Batch (je eigener Tab);
        sonst die aktuelle Funktion (mit Cache-Rueckfrage)."""
        sel = []
        try:
            sel = list(self.tab.outline.selection())
        except Exception:
            sel = []
        if len(sel) > 1:
            addrs = []
            for iid in sel:
                vals = self.tab.outline.item(iid, "values")
                kind = vals[0] if vals else ""
                a = vals[2] if len(vals) > 2 else ""
                if kind in ("fn", "sub") and a and a != "0x0":
                    try:
                        addrs.append(int(a, 16))
                    except Exception:
                        pass
            if len(addrs) > 1:
                self.decompile_many(addrs)
                return
        self.decompile_current()

    def decompile_many(self, addrs):
        """Dekompiliert mehrere Funktionen nacheinander (ein Ghidra-Lauf gleichzeitig),
        je Funktion ein C-Tab. Cache wird automatisch genutzt (ohne Rueckfrage)."""
        if self.image is None or not self.image_path:
            self._log("Erst eine .so laden (Datei auf Platte noetig fuer Ghidra).")
            return
        if getattr(self, "_decompiling", False):
            self._log("Ein Ghidra-Lauf laeuft bereits — bitte warten.")
            return
        norm, seen = [], set()
        for a in addrs:
            start = a
            if self.index is not None:
                try:
                    start, _e = self.index.func_bounds(a)
                except Exception:
                    pass
            if start not in seen and self._is_code_addr(start):
                seen.add(start); norm.append(start)
        if not norm:
            self._log("Keine dekompilierbaren Funktionen in der Auswahl.")
            return
        self._decompiling = True

        def _work():
            try:
                try:
                    from core.infrastructure.tool_manager import ToolManager
                    headless = ToolManager.resolve_ghidra(self.app.cfg.config, allow_download=True)
                except Exception:
                    headless = None
                try:
                    from services.ghidra_service import GhidraService
                    svc = GhidraService(self.app.cfg, self.app.log)
                    self._ghidra_svc = svc
                except Exception as e:
                    self._log(f"Ghidra-Dienst nicht ladbar: {e!r}")
                    return
                if not (headless or svc.available()):
                    self._log("Ghidra nicht verfuegbar.")
                    return
                ws = _unpack.plugin_root(self.base_dir(), self._session_plugin())
                self.app.after(0, self.tab.start_progress)
                self.app.after(0, lambda: self.tab.set_decompile_running(True))
                self._log(f"Batch-Decompile: {len(norm)} Funktion(en) …")
                for i, start in enumerate(norm, 1):
                    if getattr(svc, "_cancelled", False):
                        self._log("Batch abgebrochen.")
                        break
                    name = None
                    if self.index is not None:
                        try:
                            name = self.index.func_at(start)
                        except Exception:
                            name = None
                    title = name or f"sub_{start:x}"
                    self._log(f"[{i}/{len(norm)}] {self._short_name(title)} @0x{start:x} …")
                    try:
                        res = svc.decompile(self.image_path, start, ws)
                    except Exception as e:
                        res = {"ok": False, "why": "exception", "error": repr(e)}
                    if res.get("ok"):
                        c = res["c"]
                        self.app.after(0, lambda t=title, cc=c, aa=start: self.tab.show_decompile(t, cc, aa))
                    else:
                        why = res.get("why")
                        self._log(f"[{i}/{len(norm)}] {self._short_name(title)}: kein Ergebnis [{why}].")
                        if why == "cancelled":
                            break
                self._log("Batch-Decompile fertig.")
            finally:
                self._decompiling = False
                self.app.after(0, self.tab.stop_progress)
                self.app.after(0, lambda: self.tab.set_decompile_running(False))
        threading.Thread(target=_work, daemon=True).start()

    def decompile_current(self):
        """Dekompiliert die aktuelle Funktion via Ghidra-headless (nur ein Lauf gleichzeitig)."""
        if self.image is None or not self.image_path:
            self._log("Erst eine .so laden (Datei auf Platte noetig fuer Ghidra).")
            return
        if getattr(self, "_decompiling", False):
            self._log("Ein Ghidra-Lauf laeuft bereits — bitte warten (nur einer gleichzeitig).")
            return
        addr = self.current_addr if self.current_addr is not None else self.image.entry
        name = None
        if self.index is not None:
            try:
                start, _end = self.index.func_bounds(addr)
                addr = start
                name = self.index.func_at(addr)
            except Exception:
                pass
        if not addr:
            self._log("Dekompilieren nicht moeglich: Adresse 0x0 / importiertes Symbol — "
                      "erst eine definierte Funktion waehlen (Outline/Disasm).")
            return
        if not self._is_code_addr(addr):
            self._log("Decompile uebersprungen: Adresse liegt nicht in ausfuehrbarem Code "
                      "(Daten-Symbol wie RTTI/vtable oder Import). Ghidra wuerde sonst die ganze "
                      "Lib analysieren — erst eine echte Funktion waehlen.")
            return
        title = name or f"0x{addr:x}"
        # Bereits gecacht? -> fragen: gecachtes C zeigen ODER neu dekompilieren
        try:
            _ws0 = _unpack.plugin_root(self.base_dir(), self._session_plugin())
            _cfile = os.path.join(_ws0, "ghidra", _unpack.sha8(self.image_path), "func_%x.c" % addr)
        except Exception:
            _cfile = None
        if _cfile and os.path.isfile(_cfile):
            _use_cache = messagebox.askyesno(
                "Ghidra Decompile",
                f"Fuer {title} liegt bereits ein gecachtes C-Ergebnis vor.\n\n"
                "Ja  = gecachtes C sofort anzeigen\n"
                "Nein = neu dekompilieren (laeuft erneut durch Ghidra, dauert)")
            if _use_cache:
                try:
                    with open(_cfile, "r", encoding="utf-8", errors="replace") as _f:
                        _c = _f.read()
                    self._log(f"Ghidra: C fuer {title} aus Cache.")
                    self.app.after(0, lambda: self.tab.show_decompile(title, _c, addr))
                except Exception as _e:
                    self._log(f"Cache-Lesefehler: {_e!r}")
                return
        self._decompiling = True

        def _work():
            try:
                try:
                    from core.infrastructure.tool_manager import ToolManager
                    self._log("Ghidra wird vorbereitet (einmaliger Download moeglich, kann dauern) …")
                    headless = ToolManager.resolve_ghidra(self.app.cfg.config, allow_download=True)
                except Exception as e:
                    self._log(f"Ghidra-Bereitstellung fehlgeschlagen: {e!r}")
                    headless = None
                try:
                    from services.ghidra_service import GhidraService
                    svc = GhidraService(self.app.cfg, self.app.log)
                    self._ghidra_svc = svc
                except Exception as e:
                    self._log(f"Ghidra-Dienst nicht ladbar: {e!r}")
                    return
                if not (headless or svc.available()):
                    self._log("Ghidra nicht verfuegbar (Auto-Install fehlgeschlagen — offline?). Optional Pfad zu "
                              "analyzeHeadless(.bat) als 'GHIDRA_HEADLESS' in config.json setzen.")
                    return
                ws = _unpack.plugin_root(self.base_dir(), self._session_plugin())
                self._log(f"Ghidra dekompiliert {title} … (erster Lauf je Lib analysiert die ganze "
                          f"Bibliothek — das dauert; Fortschritt in dieser Konsole)")
                self.app.after(0, self.tab.start_progress)
                self.app.after(0, lambda: self.tab.set_decompile_running(True))
                try:
                    res = svc.decompile(self.image_path, addr, ws)
                finally:
                    self.app.after(0, self.tab.stop_progress)
                    self.app.after(0, lambda: self.tab.set_decompile_running(False))
                if res.get("ok"):
                    c_src = res["c"]
                    self._log(f"Ghidra: C fuer {title} bereit ({'aus Cache' if res.get('cached') else 'frisch erzeugt, wird gecacht -> naechstes Mal sofort'}).")
                    self.app.after(0, lambda: self.tab.show_decompile(title, c_src, addr))
                else:
                    why = res.get("why")
                    if why == "cancelled":
                        self._log("Ghidra-Lauf abgebrochen.")
                    elif why == "timeout":
                        self._log("Ghidra-Timeout — Lib evtl. zu gross fuer die Erstanalyse "
                                  "(Timeout bereits erhoeht; ggf. erneut starten lassen).")
                    elif why == "no_output":
                        errs = res.get("errors") or []
                        if errs:
                            self._log("Ghidra: kein Ergebnis. Fehler aus dem Analyse-Lauf:")
                            for e in errs:
                                self._log("   " + e)
                        else:
                            self._log("Ghidra: kein Ergebnis.")
                    else:
                        self._log(f"Ghidra fehlgeschlagen [{why}]: {res.get('hint') or res.get('log','')}")
            finally:
                self._decompiling = False
        threading.Thread(target=_work, daemon=True).start()

    def cancel_decompile(self):
        """Bricht einen laufenden Ghidra-Decompile ab (Button ⏹)."""
        svc = getattr(self, "_ghidra_svc", None)
        if svc is None or not getattr(self, "_decompiling", False):
            self._log("Kein laufender Ghidra-Lauf zum Abbrechen.")
            return
        try:
            svc.cancel()
        except Exception as e:
            self._log(f"Abbruch-Fehler: {e!r}")

    def _short_name(self, name):
        """Lesbarer Kurzname aus gemangletem Itanium-Namen: letzte laengen-praefixierte
        <len><exakt n Zeichen>-Komponente (…E19get_untyped_deleterEv -> get_untyped_deleter)."""
        if not name or not name.startswith("_Z"):
            return name
        i, L, last = 0, len(name), None
        while i < L:
            c = name[i]
            if c in "CD" and i + 1 < L and name[i + 1] in "0123" and (i + 2 >= L or not name[i + 2].isdigit()):
                i += 2  # Ctor/Dtor-Marker -> Klassenname (last) bleibt stehen
                continue
            if c.isdigit():
                j = i
                while j < L and name[j].isdigit():
                    j += 1
                n = int(name[i:j])
                ident = name[j:j + n]
                if n > 0 and len(ident) == n and (ident[:1].isalpha() or ident[:1] == "_"):
                    last = ident
                i = j + n if n > 0 else j
            else:
                i += 1
        return last or name

    def _ensure_short_map(self):
        """Baut (lazy) {eindeutiger Kurzname -> addr} ueber definierte Code-Symbole."""
        if getattr(self, "_short2addr", None) is not None:
            return
        mp, dup = {}, set()
        if self.image is not None:
            for nm, sy in self.image.dynsyms.items():
                if not sy.value or (sy.info & 0xf) not in (2, 10):
                    continue
                s = self._short_name(nm)
                if s in mp and mp[s] != sy.value:
                    dup.add(s)
                else:
                    mp[s] = sy.value
            for d in dup:
                mp.pop(d, None)
        self._short2addr = mp

    def decompile_token(self, token, base):
        """Aus dem C-Dekompilat: Token -> Adresse -> springen + neuer C-Tab.
        Reihenfolge: FUN_<ram> · exakter Dynsym-Name · eindeutiger Kurzname (demangled)."""
        if self.image is None:
            return
        import re as _re
        addr = None
        m = _re.fullmatch(r"(FUN|LAB|DAT)_0*([0-9a-fA-F]+)", token)
        if m:
            if base is None:
                self._log(f"'{token}': ImageBase unbekannt (kein C-Header) — nicht aufloesbar.")
                return
            k3 = m.group(1)
            a = int(m.group(2), 16) - base
            if a <= 0:
                self._log(f"'{token}': Adresse ausserhalb der Lib.")
                return
            self.navigate(a, set_entry=False)
            if k3 == "FUN" and self._is_code_addr(a):
                self.decompile_addr(a)                     # Funktion -> eigener C-Tab
            elif k3 == "LAB":
                self._log(f"Sprungmarke {token} -> 0x{a:x} (in aktueller Funktion).")
            else:
                self._log(f"Daten {token} -> 0x{a:x} — Hexdump/XREF zeigt den Bezug.")
            return
        if token in self.image.dynsyms and self.image.dynsyms[token].value:
            addr = self.image.dynsyms[token].value
        if addr is None and self.index is not None:
            for a, n in self.index.syms:
                if n == token:
                    addr = a
                    break
        if addr is None:
            self._ensure_short_map()
            addr = self._short2addr.get(token)
        if not addr or addr <= 0:
            if token in self.image.dynsyms:
                self._log(f"'{token}': Import/extern — Rumpf liegt in anderer Lib, kein Sprung.")
            else:
                self._log(f"'{token}': kein eindeutiges Code-Ziel im C (unbenannt/mehrdeutig/Datentyp). "
                          "Anspringbar sind FUN_…-Calls und eindeutige Namen.")
            return
        if not self._is_code_addr(addr):
            self._log(f"'{token}' -> 0x{addr:x}: kein Code — nur Navigation.")
            self.navigate(addr, set_entry=False)
            return
        self.navigate(addr, set_entry=False)
        self.decompile_addr(addr)

    def decompile_addr(self, addr):
        """Dekompiliert eine bestimmte Adresse (oeffnet einen neuen C-Tab)."""
        self.current_addr = addr
        self.decompile_current()

    def load_addr_into_run(self, addr):
        """Traegt addr als Entry ins Run-Panel ein (Ausfuehrung dann im Hauptfenster via ▶ Run)."""
        def _set():
            try:
                self.tab.run_entry.delete(0, "end")
                self.tab.run_entry.insert(0, f"0x{addr:x}")
            except Exception:
                pass
        self.app.after(0, _set)
        self._log(f"0x{addr:x} ins Run-Panel geladen — im Hauptfenster ▶ Run (Args/SP nach Bedarf).")

    def _read_vaddr(self, addr, n):
        """Liest n Bytes ab virtueller Adresse aus dem geladenen Image (0 fuer .bss/ausserhalb Datei)."""
        img = self.image
        if img is None:
            return b""
        for s in img.segments:
            lo = s.vaddr
            hi = lo + max(getattr(s, "memsz", 0), getattr(s, "filesz", 0))
            if lo <= addr < hi:
                off = s.offset + (addr - s.vaddr)
                fend = s.offset + s.filesz
                data = img.data[off:min(off + n, fend)] if off < fend else b""
                if len(data) < n:
                    data = data + b"\x00" * (n - len(data))
                return data
        return b""

    def _is_code_addr(self, addr):
        """True, wenn addr in einem ausfuehrbaren Segment (PF_X) liegt."""
        img = self.image
        if img is None or not addr:
            return False
        for s in img.segments:
            lo = s.vaddr
            hi = lo + max(getattr(s, "memsz", 0), getattr(s, "filesz", 0))
            if (getattr(s, "flags", 0) & 0x1) and lo <= addr < hi:
                return True
        return False

    def _disasm_rows(self, start, count, mark_addr=None):
        """Dekodiert count Instruktionen ab start zu Tabellenzeilen:
        (mark, addr, bytes, mnem, ops, tags, target). target = aufgeloestes Sprungziel."""
        import re as _re
        _BR_T = {"b", "bl", "cbz", "cbnz", "tbz", "tbnz"}
        _BR_N = {"br", "blr", "ret"}
        eng = Arm64Engine(listener=lambda ev, p: None)
        eng.load_image(self.image)
        dec = self._dec()
        rows = []
        pc = start
        for _ in range(count):
            raw = int.from_bytes(eng.mem.read(pc, 4), "little")
            tags = []
            target = None
            mark = "▶" if (mark_addr is not None and pc == mark_addr) else ""
            if mark:
                tags.append("cur")
            try:
                insn = dec.decode(raw, pc)
                mnem = insn.mnemonic
                ops = insn.op_str
                low = mnem.lower()
                base = low.split(".", 1)[0]
                if base in _BR_T or base in _BR_N or low.startswith("b."):
                    tags.append("branch")
                if base in _BR_T or low.startswith("b."):
                    mm = list(_re.finditer(r"0x[0-9a-fA-F]+", ops))
                    if mm:
                        try:
                            target = int(mm[-1].group(0), 16)
                        except Exception:
                            target = None
            except iface.UnknownInstructionError:
                mnem = ".word"
                ops = f"0x{raw:08x}"
                tags.append("data")
            rows.append((mark, f"0x{pc:08x}", f"{raw:08x}", mnem, ops, tuple(tags), target))
            pc += 4
        return rows

    def _anno_service(self):
        from services.native_annotations_service import NativeAnnotationsService
        p = os.path.join(_unpack.plugin_root(self.base_dir(), self._session_plugin()), "annotations.json")
        return NativeAnnotationsService(p)

    def _anno_sha(self):
        try:
            return _unpack.sha8(self.image_path)
        except Exception:
            return "?"

    def _apply_anno(self, rows):
        """Overlay: ersetzt Namen definierter Zeilen durch gespeicherte Annotationen (✎)."""
        try:
            anno = self._anno_service().all(self._anno_sha())
        except Exception:
            anno = {}
        if not anno:
            return rows
        out = []
        for r in rows:
            if len(r) >= 3 and r[0] in ("fn", "sub", "data"):
                info = anno.get(r[2])
                if info and info.get("name"):
                    out.append((r[0], "\u270e " + info["name"], r[2]))
                    continue
            out.append(r)
        return out

    def annotate(self, addr, name, note=""):
        """Speichert einen eigenen Namen/Notiz fuer eine Adresse und frischt die Outline auf."""
        try:
            self._anno_service().set(self._anno_sha(), f"0x{addr:x}", name, note or "")
            self._log(f"Annotiert 0x{addr:x}: {name}" + (f"  // {note}" if note else ""))
        except Exception as e:
            self._log(f"Annotieren FEHLER: {e!r}"); return
        try:
            if self.index is not None:
                self._emit_enriched_outline()
            elif getattr(self, "_outline_base_rows", None):
                self.app.after(0, lambda: self.tab.set_outline(self._apply_anno(self._outline_base_rows)))
        except Exception:
            pass

    def _emit_enriched_outline(self):
        """Ergaenzt die Outline um vom Index entdeckte Funktionsanfaenge (bl-Ziele)
        als sub_<addr>, ohne Dubletten zu benannten Symbolen. Idempotent."""
        base = getattr(self, "_outline_base_rows", None)
        if not base or self.image is None:
            return
        idx = self.index
        named = set()
        for row in base:
            if row and row[0] in ("fn", "data") and len(row) > 2:
                try:
                    named.add(int(row[2], 16))
                except Exception:
                    pass
        starts = set()
        if idx is not None:
            for src, mnem, tgt in idx.edges:
                if mnem.split(".", 1)[0] == "bl":
                    starts.add(tgt)
        subs = sorted(a for a in starts if a and a not in named)
        if not subs:
            return
        meta = [r for r in base if r[0] == "meta"]
        seg = [r for r in base if r[0] == "seg"]
        defs = [r for r in base if r[0] in ("fn", "data")]
        imps = [r for r in base if r[0] == "import"]
        _nfn = sum(1 for r in defs if r[0] == "fn")
        merged = list(defs) + [("sub", f"sub_{a:x}", f"0x{a:x}") for a in subs]
        try:
            merged.sort(key=lambda r: int(r[2], 16))
        except Exception:
            pass
        rows = meta + seg + merged + imps
        self.app.after(0, lambda: self.tab.set_outline(self._apply_anno(rows)))
        self._log(f"Outline angereichert: {_nfn} benannte Funktionen + {len(subs)} per Index entdeckte "
                  f"(sub_…) + Daten-Symbole (grau). Doppelklick auf sub_ oeffnet die Funktion.")

    def disasm_func(self, addr):
        """Disassembliert die Funktion, die `addr` enthaelt (Symbolgrenzen)."""
        if self.image is None:
            return
        self.current_addr = addr
        if not self._is_code_addr(addr):
            self._log("Daten-Symbol (RTTI/vtable/Konstante) — keine Code-Disassembly; zeige Hexdump. "
                      "XREF zeigt, wer es referenziert (vtables oft nur indirekt/virtuell).")
            _data = self._read_vaddr(addr, 128)
            _rows = []
            for _i in range(0, len(_data), 16):
                _ch = _data[_i:_i + 16]
                _hx = " ".join("%02x" % _b for _b in _ch)
                _asc = "".join(chr(_b) if 32 <= _b < 127 else "." for _b in _ch)
                _rows.append(("", "0x%08x" % (addr + _i), "", ".byte", "%-47s  %s" % (_hx, _asc),
                              ("data",), None))
            if not _rows:
                _rows = [("", "0x%08x" % addr, "", ".byte", "(keine Dateibytes — evtl. .bss/ausserhalb)",
                          ("data",), None)]
            self.app.after(0, lambda: self.tab.set_disasm_rows("(Daten @0x%x) — Hexdump" % addr, _rows))
            return
        start, end, name = addr, addr + 48 * 4, None
        if self.index is not None:
            try:
                start, end = self.index.func_bounds(addr)
                name = self.index.func_at(addr)
            except Exception:
                pass
        if addr == start:                              # Funktionskopf -> ab Funktionsanfang (gekappt)
            _disp_start = start
            count = max(1, min((end - start) // 4, 2000))
        else:                                          # Sprung zu pc/BP -> festes Fenster UM addr
            _disp_start = max(0, addr - 64 * 4)
            count = 256

        def _work():
            if not self._require_capstone():
                return
            try:
                rows = self._disasm_rows(_disp_start, count, mark_addr=addr)
                hdr_name = self._short_name(name) if name else f"sub_{start:x}"
                _we = _disp_start + count * 4
                _anchor = "" if addr == start else f"  ·  @0x{addr:x}"
                header = f"{hdr_name}{_anchor}  ·  0x{_disp_start:x} .. 0x{_we:x}  ({count} Instr.)"
                self.app.after(0, lambda: self.tab.set_disasm_rows(header, rows))
                self._log(f"Funktion @0x{start:x}" + (f" ({name})" if name else "") + f": {count} Instruktionen.")
            except Exception as e:
                self._log(f"Disasm-Funktion FEHLER: {e!r}")
        threading.Thread(target=_work, daemon=True).start()
