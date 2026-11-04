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
                svc.load_image(eng, self.image)
            except Exception as e:
                self._log(f"Laden in Engine FEHLER: {e!r}"); return
            args = list(cfg.get("args") or [])
            reg_args, stack_args = args[:8], args[8:]
            sp = cfg.get("sp")
            if stack_args:
                if sp is None:
                    self._log("Stack-Argumente gesetzt, aber kein SP angegeben."); return
                try:
                    for k, v in enumerate(stack_args):
                        eng.mem.write(sp + k * 8, int(v).to_bytes(8, "little"))
                except Exception as e:
                    self._log(f"Stack-Setup FEHLER: {e!r}"); return
            self.app.after(0, self.tab.start_progress)
            self._log(f"▶ Run {cfg['entry']}  x0..={', '.join(hex(a) for a in reg_args) or '—'}"
                      + (f"  (+{len(stack_args)} Stack-Args)" if stack_args else ""))
            try:
                res = svc.run(eng, cfg["entry"], reg_args, cfg.get("mem_overrides"),
                              sp, cfg.get("maxins", 2000000), image=self.image)
            except Exception as e:
                res = {"ok": False, "why": "exception", "error": repr(e)}
            finally:
                self.app.after(0, self.tab.stop_progress)
            if not res.get("ok"):
                self._log(f"Run FEHLER [{res.get('why')}]: "
                          f"{res.get('error') or res.get('hint') or res.get('pc')}")
                return
            self._last_engine = eng
            self.app.after(0, lambda: self.tab.set_registers(res))
            cov = (f"Lauf OK · ret=0x{res['ret']:x} · entry={cfg['entry']} · "
                   f"x0..={', '.join(hex(a) for a in reg_args) or '—'}")
            self.app.after(0, lambda: self.tab.set_coverage(cov))
            self._log(f"Run fertig: ret=0x{res['ret']:x}")
        threading.Thread(target=_work, daemon=True).start()

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

    def _load_so(self, path):
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
                self.app.after(0, lambda: self.tab.set_outline(rows))
                self._log(f"geladen: {os.path.basename(path)} — "
                          f"{len(img.segments)} Segmente, {len(img.dynsyms)} Symbole, "
                          f"AArch64={img.is_aarch64}")
                self.app.after(0, lambda: self.tab.set_loaded(f"📦 {os.path.basename(path)} · {len(img.dynsyms)} Symbole"))
                if not img.is_aarch64:
                    self._log("Hinweis: kein AArch64-ELF (oder gepackt) — Entpacken folgt mit M3.")
                else:
                    self._log("Lade fertig. Baue XREF-Index (Fast) im Hintergrund — Suche/XREF danach sofort.")
                    self._build_index(img)
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
        if self.history_root_id:
            self._build_history_tree("", self.history_root_id)
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
        self.app.after(0, lambda: self.tab.set_outline(rows))
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
        count = max(1, min((end - start) // 4, 2000))

        def _work():
            if not self._require_capstone():
                return
            try:
                rows = self._disasm_rows(start, count, mark_addr=addr)
                hdr_name = self._short_name(name) if name else f"sub_{start:x}"
                header = f"{hdr_name}  ·  0x{start:x} .. 0x{end:x}  ({count} Instr.)"
                self.app.after(0, lambda: self.tab.set_disasm_rows(header, rows))
                self._log(f"Funktion @0x{start:x}" + (f" ({name})" if name else "") + f": {count} Instruktionen.")
            except Exception as e:
                self._log(f"Disasm-Funktion FEHLER: {e!r}")
        threading.Thread(target=_work, daemon=True).start()
