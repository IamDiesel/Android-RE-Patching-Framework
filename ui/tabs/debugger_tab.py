# -*- coding: utf-8 -*-
"""DebuggerTab — eigener Hauptreiter „🐞 Debugger" (X1/MS-6).

VS-Code-artige, dockbare Perspektive ueber dem Dock-Framework (`ui.dock`). Registriert die
Kern-Views (Transport, Smali-Editor, Register, Call-Stack, Breakpoints, Console, Timeline, missing,
Quelle/Zone, Status) + Platzhalter fuer den weiteren Ausbau (Disassembly/Memory/Objekt/Outline/
Logcat). Logik im `DebuggerController`. Siehe `spec-debugger-hauptreiter-2026-11-11`.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ui.dock.dock_manager import DockManager
from ui.controllers.debugger_controller import DebuggerController


class DebuggerTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.ctrl = DebuggerController(app)
        self.ctrl.view = self
        # Untere Zone (panel) ist eine ECHTE interne Dock-Zone: Main Console + Live-Log + Timeline
        # + missing sind dort vollwertige Dock-Views (ziehen/teilen/abdocken wie alle anderen).
        self.dm = DockManager(self, app=app, with_panel=True)
        self.dm.outer.pack(fill="both", expand=True)
        self._register_views()
        self._register_os_inspector()
        self.dm.build()

    def _register_views(self):
        dm = self.dm
        dm.register_view("transport", "Transport", self._v_transport, "top", "S")
        dm.register_view("quelle", "Quelle/Zone", self._v_quelle, "left", "D+")
        dm.register_view("callstack", "Call-Stack", self._v_callstack, "left", "S")
        dm.register_view("breakpoints", "Breakpoints", self._v_breakpoints, "left", "D+")
        dm.register_view("outline", "Outline", self._v_placeholder("Outline — P2"), "left", "D-")
        dm.register_view("editor", "Smali", self._v_editor, "editor", "S")
        dm.register_view("disasm", "Disassembly", self._v_disasm, "editor", "D+")
        dm.register_view("screen", "\U0001f4f1 Screen", self._v_screen, "right", "S")
        dm.register_view("registers", "Register", self._v_registers, "right", "S")
        dm.register_view("watch", "Watch", self._v_watch, "left", "D-")
        dm.register_view("memory", "Memory", self._v_memory, "right", "D+")
        dm.register_view("objinspect", "Objekte", self._v_objinspect, "right", "D-")
        dm.register_view("appstate", "App-State", self._v_app_state, "right", "S")
        dm.register_view("actionpoints", "Aktionspunkte", self._v_action_points, "right", "D-")
        dm.register_view("console_main", "Main Console", self._v_console_main, "panel", "S")
        dm.register_view("console_live", "Start & Live-Log", self._v_console_live, "panel", "D+")
        dm.register_view("timeline", "Timeline", self._v_timeline, "panel", "D+")
        dm.register_view("netz", "Netz", self._v_net, "panel", "D+")
        dm.register_view("jnigrenze", "JNI-Grenze", self._v_jni_boundary, "panel", "D-")
        dm.register_view("missing", "missing", self._v_missing, "panel", "D+")
        dm.register_view("seams_ipc", "Seams & IPC", self._v_seams_ipc, "panel", "D+")
        dm.register_view("ui_tree", "UI-Inspector", self._v_ui_tree, "right", "D+")
        dm.register_view("status", "Status", self._v_status, "status", "S")

    def _v_screen(self, parent):
        """Eingebetteter 📱 Screen im Debugger (kein Tab-Hopping noetig)."""
        try:
            from ui.tabs.display_tab import DisplayTab
            self.screen_view = DisplayTab(parent, self.app, embedded=True)
            return self.screen_view
        except Exception as e:
            return ttk.Label(parent, text=f"Screen Fehler: {e}")

    def feed_os_snapshot(self, snap):
        """Live-Einspeisung eines OS-Zustands (NativeOsService.dbg_snapshot) ins OS-Inspektor-Modell.
        Von der os_attach-/Debug-Kopplung aufgerufen (nach Lauf bzw. an jedem Syscall-Breakpoint)."""
        m = getattr(self, "os_model", None)
        if m is not None:
            m.ingest(snap or {})

    def os_config_delta(self):
        """Aktuelles Config-Delta (bewusste Edits) fuer den naechsten Lauf / ⬆ OS→KI."""
        m = getattr(self, "os_model", None)
        return m.build_config() if m is not None else {}

    def _register_os_inspector(self):
        """OS-Inspektor (OS-1..OS-9) als Dock-Gruppe — additiv, fehlertolerant."""
        try:
            from core.application.os_inspector_model import OsInspectorModel
            from ui.tabs.os_inspector_views import OsInspectorViews
            self.os_model = OsInspectorModel()
            self.os_views = OsInspectorViews(self.dm, self.os_model, controller=self.ctrl, reader=self.ctrl)
            self.os_views.register()
        except Exception as e:
            try:
                self.app.log("OS-Inspektor nicht geladen: %r" % (e,))
            except Exception:
                pass

    # ---- View-Factories ---------------------------------------------------
    def _v_console_main(self, parent):
        """Main Console als bus-gespeiste Textsenke (ueberall neu aufbaubar, verlustfrei)."""
        f = ttk.Frame(parent)
        t = tk.Text(f, bg="black", fg="lightgreen")
        t.pack(fill="both", expand=True)
        try:
            self.app.register_log_sink(t)
        except Exception:
            pass

        def _off(e=None, w=t):
            if e is not None and getattr(e, "widget", None) is not w:
                return
            try:
                self.app.unregister_log_sink(w)
            except Exception:
                pass
        t.bind("<Destroy>", _off, add="+")
        return f

    def _v_console_live(self, parent):
        """Live-Log als Modell-gebundene View (laufende Captures/Logs/Auswahl ueberleben)."""
        ws = getattr(self.app, "workspace_tab", None)
        if ws is None:
            return ttk.Label(parent, text="Live-Log — Workspace noch nicht bereit")
        try:
            from ui.tabs.launcher_logger_tab import LauncherLoggerTab
            return LauncherLoggerTab(parent, ws)
        except Exception as e:
            return ttk.Label(parent, text=f"Live-Log Fehler: {e}")

    def _v_placeholder(self, text):
        return lambda parent: ttk.Label(parent, text=text, anchor="center")

    def _v_net(self, parent):
        """Netz-Säule: aufgezeichnete HTTP-Requests + Request/Response Detail-Inspektor + höhenverstellbarer Splitter."""
        c = self.ctrl
        f = ttk.Frame(parent)
        entries_data = []

        net_vsplit = tk.PanedWindow(f, orient="vertical", sashwidth=6, sashrelief="raised", bd=0)
        net_vsplit.pack(side="top", fill="both", expand=True)

        top_pane = ttk.Frame(net_vsplit)
        bot_pane = ttk.Frame(net_vsplit)
        net_vsplit.add(top_pane, minsize=80, stretch="always")
        net_vsplit.add(bot_pane, minsize=95, stretch="never")
        f._net_vsplit = net_vsplit

        pw = ttk.PanedWindow(top_pane, orient="horizontal")
        pw.pack(side="top", fill="both", expand=True)

        left_f = ttk.Frame(pw)
        right_f = ttk.Frame(pw)
        pw.add(left_f, weight=2)
        pw.add(right_f, weight=3)

        lb_frame = ttk.Frame(left_f)
        lb_frame.pack(side="top", fill="both", expand=True)
        lb = tk.Listbox(lb_frame, exportselection=False)
        lb_sb = ttk.Scrollbar(lb_frame, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=lb_sb.set)
        lb_sb.pack(side="right", fill="y")
        lb.pack(side="left", fill="both", expand=True)

        det_hdr = ttk.Frame(right_f)
        det_hdr.pack(side="top", fill="x")
        ttk.Label(det_hdr, text="Request / Response Details:").pack(side="left", anchor="w")
        to_mock_btn = ttk.Button(det_hdr, text="Auswahl → Mock-Felder")
        to_mock_btn.pack(side="right", padx=2)

        det_frame = ttk.Frame(right_f)
        det_frame.pack(side="top", fill="both", expand=True)
        detail_txt = tk.Text(det_frame, wrap="word", font=("Consolas", 9), height=10)
        det_sb = ttk.Scrollbar(det_frame, orient="vertical", command=detail_txt.yview)
        detail_txt.configure(yscrollcommand=det_sb.set, state="disabled")
        det_sb.pack(side="right", fill="y")
        detail_txt.pack(side="left", fill="both", expand=True)
        detail_txt.bind("<Button-1>", lambda _e: detail_txt.focus_set())

        def _set_detail_text(txt):
            try:
                detail_txt.configure(state="normal")
                detail_txt.delete("1.0", "end")
                detail_txt.insert("1.0", txt)
                detail_txt.configure(state="disabled")
            except Exception:
                pass

        def _show_detail(idx):
            if idx < 0 or idx >= len(entries_data):
                return
            entry = entries_data[idx]
            try:
                from core.java.net_unified import format_net_entry_details
                txt = format_net_entry_details(entry, net_mode=getattr(c, "net_mode", "mock"))
            except Exception as _ef:
                txt = repr(entry) + "\n(%r)" % _ef
            _set_detail_text(txt)

        def _select_and_show(idx):
            if idx < 0 or idx >= len(entries_data):
                return
            try:
                lb.selection_clear(0, "end")
                lb.selection_set(idx)
                lb.see(idx)
            except Exception:
                pass
            _show_detail(idx)

        def _on_select(_evt=None):
            sel = lb.curselection()
            if sel:
                _show_detail(int(sel[0]))

        lb.bind("<<ListboxSelect>>", _on_select)

        def _line(method, url, code, blen):
            return "%s  %s  ->  %s (%sB)" % (method, url, code, blen)

        def _fill():
            lb.delete(0, "end")
            entries_data.clear()
            try:
                from core.java.net_unified import unified
            except Exception:
                unified = None
            os_net = None
            if hasattr(c, "fusion_os_net"):
                try:
                    os_net = c.fusion_os_net()
                except Exception:
                    os_net = None
            if not os_net:
                om = getattr(self, "os_model", None)
                if om is not None:
                    try:
                        os_net = {"log": [("connect", str(p)) for p in om.net_peers()]}
                    except Exception:
                        os_net = None
            if unified is not None:
                for row in unified(getattr(c, "net_log", None), os_net):
                    tag = "[java]" if row["source"] == "java" else "[nativ]"
                    entries_data.append(row)
                    lb.insert("end", "[#%d] %s %s" % (len(entries_data), tag, row["label"]))
            else:
                log = getattr(c, "net_log", None)
                if log is not None:
                    for e in log.entries:
                        r = e["req"]; rp = e["resp"]
                        row = {
                            "source": "java", "kind": "http",
                            "req": r.to_dict() if hasattr(r, "to_dict") else {},
                            "resp": rp.to_dict() if hasattr(rp, "to_dict") else {},
                        }
                        entries_data.append(row)
                        lb.insert("end", "[#%d] [java] %s" % (len(entries_data), _line(r.method, r.url, rp.code, len(rp.body))))
            if entries_data:
                _select_and_show(len(entries_data) - 1)
            else:
                _set_detail_text(
                    "Noch keine Netz-Requests aufgezeichnet.\n\n"
                    "Tipp: Unten 'Modus: Live-HTTP' wählen für echte Server-Anfragen (z. B. Login) "
                    "oder 'Mock/Replay' mit einer Mock-Regel."
                )

        def _on_net(data):
            d = data or {}; req = d.get("req", {}); rp = d.get("resp", {})
            entry = {
                "source": "java", "kind": "http",
                "method": req.get("method", "?"), "url": req.get("url", "?"),
                "code": rp.get("code", "?"), "resp_bytes": rp.get("body_len", 0),
                "req": dict(req), "resp": dict(rp),
            }

            def _add():
                try:
                    entries_data.append(entry)
                    line = "[#%d] [java] %s" % (
                        len(entries_data),
                        _line(entry["method"], entry["url"], entry["code"], entry["resp_bytes"]),
                    )
                    lb.insert("end", line)
                    _select_and_show(len(entries_data) - 1)
                except Exception:
                    pass
            try:
                self.app.after(0, _add)
            except Exception:
                _add()
        try:
            from core.application.event_bus import EventBus
            EventBus.subscribe("NET_REQUEST", _on_net)
        except Exception:
            pass

        # --- Mock/Replay-Editor im unteren, größenverstellbaren Bereich ---
        ttk.Label(bot_pane, text="Mock / Replay & Netz-Modus (greift sofort live im laufenden App-Lauf):").pack(side="top", anchor="w", pady=(2, 1))
        m1 = ttk.Frame(bot_pane); m1.pack(side="top", fill="x")
        ttk.Label(m1, text="Methode:").pack(side="left")
        mm = tk.StringVar(value="*")
        ttk.Combobox(m1, textvariable=mm, values=["*", "GET", "POST", "PUT", "DELETE"],
                     width=6, state="readonly").pack(side="left", padx=(1, 6))
        ttk.Label(m1, text="URL/Teil:").pack(side="left")
        murl = tk.StringVar(); ttk.Entry(m1, textvariable=murl, width=22).pack(side="left", padx=(1, 6))
        ttk.Label(m1, text="Code:").pack(side="left")
        mcode = tk.StringVar(value="200"); ttk.Entry(m1, textvariable=mcode, width=5).pack(side="left")
        m2 = ttk.Frame(bot_pane); m2.pack(side="top", fill="x")
        ttk.Label(m2, text="Body:").pack(side="left")
        mbody = tk.StringVar(); ttk.Entry(m2, textvariable=mbody, width=40).pack(side="left", fill="x", expand=True)
        mlb_wrap = ttk.Frame(bot_pane)
        mlb_wrap.pack(side="top", fill="both", expand=True, pady=2)
        mlb = tk.Listbox(mlb_wrap, height=3)
        mlb_sb = ttk.Scrollbar(mlb_wrap, orient="vertical", command=mlb.yview)
        mlb.configure(yscrollcommand=mlb_sb.set)
        mlb_sb.pack(side="right", fill="y")
        mlb.pack(side="left", fill="both", expand=True)

        def _use_selected_for_mock():
            sel = lb.curselection()
            idx = int(sel[0]) if sel else (len(entries_data) - 1)
            if idx < 0 or idx >= len(entries_data):
                return
            ent = entries_data[idx]
            if ent.get("source") != "java":
                return
            req_d = ent.get("req") or {}
            res_d = ent.get("resp") or {}
            meth = str(req_d.get("method") or "*").upper()
            mm.set(meth if meth in ("GET", "POST", "PUT", "DELETE") else "*")
            murl.set(str(req_d.get("url") or "").split("?", 1)[0])
            mcode.set(str(res_d.get("code") or 200))
            mbody.set(str(res_d.get("body") or res_d.get("body_preview") or ""))
            c._log("> Netz-Mock-Felder aus #%d uebernommen" % (idx + 1))

        to_mock_btn.configure(command=_use_selected_for_mock)

        def _refresh_mocks():
            mlb.delete(0, "end")
            for k, resp in c.net_mocks().items():
                blen = len(getattr(resp, "body", b"") or b"")
                mlb.insert("end", "%s  ->  %s (%dB)" % (k, getattr(resp, "code", "?"), blen))

        def _add_mock():
            u = murl.get().strip()
            if not u:
                c._log("> Netz-Mock: URL/Teil fehlt"); return
            meth = None if mm.get() == "*" else mm.get()
            c.add_net_mock(u, mcode.get().strip() or 200, mbody.get(), method=meth)
            _refresh_mocks()

        def _clear_mocks():
            c.clear_net_mocks(); _refresh_mocks()

        mb = ttk.Frame(bot_pane); mb.pack(side="top", fill="x")
        ttk.Button(mb, text="Mock +", command=_add_mock).pack(side="left", padx=2)
        ttk.Button(mb, text="Mocks leeren", command=_clear_mocks).pack(side="left", padx=2)
        # AP1.5/6C: Netz-Modus + Konditionen (Online | Gedrosselt | Offline | Timeout)
        ttk.Label(mb, text="Modus:").pack(side="left", padx=(8, 1))
        _cur_nm = getattr(c, "net_mode", "mock")
        _nm_lbl = "Live-HTTP" if _cur_nm == "live" else ("DB-Replay" if _cur_nm == "db_replay" else "Mock/Replay")
        nmv = tk.StringVar(value=_nm_lbl)
        cb = ttk.Combobox(mb, textvariable=nmv, width=11, state="readonly",
                          values=("Mock/Replay", "DB-Replay", "Live-HTTP"))
        cb.pack(side="left")
        nmv.trace_add("write", lambda *_: c.set_net_mode(nmv.get()))

        nc_bar = ttk.Frame(bot_pane); nc_bar.pack(side="top", fill="x", pady=(2, 2))
        ttk.Label(nc_bar, text="Leitung:").pack(side="left")
        _nc0 = getattr(c, "net_condition", None)
        ncv = tk.StringVar(value=getattr(_nc0, "mode", "online") if _nc0 else "online")
        ttk.Combobox(nc_bar, textvariable=ncv, width=10, state="readonly",
                     values=("online", "throttled", "offline", "timeout")).pack(side="left", padx=(2, 6))
        ttk.Label(nc_bar, text="Latenz(ms):").pack(side="left")
        latv = tk.StringVar(value=str(getattr(_nc0, "latency_ms", 0) if _nc0 else 0))
        ttk.Entry(nc_bar, textvariable=latv, width=5).pack(side="left", padx=(1, 6))
        ttk.Label(nc_bar, text="kbit/s:").pack(side="left")
        kbv = tk.StringVar(value=str(getattr(_nc0, "kbps", 0) if _nc0 else 0))
        ttk.Entry(nc_bar, textvariable=kbv, width=6).pack(side="left", padx=(1, 6))
        ttk.Button(nc_bar, text="Setzen",
                   command=lambda: c.set_net_condition(ncv.get(), latv.get(), kbv.get())).pack(side="left", padx=2)

        def _clear_net_trace():
            lb.delete(0, "end")
            entries_data.clear()
            log_obj = getattr(c, "net_log", None)
            if log_obj is not None and isinstance(getattr(log_obj, "entries", None), list):
                log_obj.entries.clear()
            _set_detail_text("")

        def _ui_safe(fn):
            def _run():
                try:
                    if lb.winfo_exists():
                        fn()
                except Exception:
                    pass
            try:
                self.app.after(0, _run)
            except Exception:
                _run()

        try:
            from core.application.event_bus import EventBus
            EventBus.subscribe("APP_IMPORTED", lambda _d=None: _ui_safe(_clear_net_trace))
            EventBus.subscribe("GLASS_STARTED", lambda _d=None: _ui_safe(_fill))
        except Exception:
            pass

        def _save_net_log():
            try:
                from tkinter import filedialog
                p = filedialog.asksaveasfilename(
                    title="Debugger Netz-Log speichern",
                    defaultextension=".json",
                    filetypes=[("API-Log JSON", "*.json"), ("Alle Dateien", "*.*")],
                )
                if p:
                    c.save_net_log_json(p)
            except Exception as ex:
                c._log("> Netz-Log speichern Fehler: %r" % ex)

        def _load_net_log():
            try:
                from tkinter import filedialog
                p = filedialog.askopenfilename(
                    title="API-Log / Replay laden",
                    filetypes=[("API-Log / SQLite", "*.json *.db *.sqlite *.sqlite3"), ("Alle Dateien", "*.*")],
                )
                if p and c.load_net_log_file(p, as_replay=True) >= 0:
                    nmv.set("Mock/Replay")
                    _fill()
                    _refresh_mocks()
            except Exception as ex:
                c._log("> Netz-Log laden Fehler: %r" % ex)

        def _import_api_inspector():
            try:
                if c.import_net_from_api_db() >= 0:
                    nmv.set("Mock/Replay")
                    _fill()
                    _refresh_mocks()
            except Exception as ex:
                c._log("> API-Inspector Import Fehler: %r" % ex)

        def _open_flutter_extract():
            fn = getattr(self, "open_flutter_api_extractor", None)
            if callable(fn):
                fn()

        def _open_browser_inspect():
            fn = getattr(self, "open_browser_inspector", None)
            if callable(fn):
                fn()

        br = ttk.Frame(f); br.pack(side="top", fill="x")
        ttk.Button(br, text="Aktualisieren", command=lambda: (_fill(), _refresh_mocks())).pack(side="left", padx=2)
        ttk.Button(br, text="Leeren", command=_clear_net_trace).pack(side="left", padx=2)
        ttk.Button(br, text="\U0001f4be Log speichern", command=_save_net_log).pack(side="left", padx=2)
        ttk.Button(br, text="\U0001f4c2 Log/Replay laden", command=_load_net_log).pack(side="left", padx=2)
        ttk.Button(br, text="\u21c4 Aus API-Inspector", command=_import_api_inspector).pack(side="left", padx=2)
        ttk.Button(br, text="\U0001f50d Engine-APIs…", command=_open_flutter_extract).pack(side="left", padx=2)
        ttk.Button(br, text="\U0001f310 Browser & Inspect", command=_open_browser_inspect).pack(side="left", padx=2)
        _fill(); _refresh_mocks()
        return f

    def open_browser_inspector(self, url: str | None = None):
        """Oeffnet den Browser & Web-Inspektor (im Haupt-Anzeige-Tab oder als Fenster)."""
        disp = getattr(self, "display", None) or getattr(self.app, "display_tab", None)
        if disp is not None and hasattr(disp, "toggle_browser_view"):
            disp.toggle_browser_view(url=url)
            return
        from ui.widgets.browser_inspector_panel import BrowserInspectorPanel
        win = tk.Toplevel(self.winfo_toplevel())
        win.title("🌐 Browser & Web-Inspektor — Android RE Patching Framework")
        win.geometry("1220x760")
        pnl = BrowserInspectorPanel(win, self.app)
        pnl.pack(fill="both", expand=True)
        if url:
            pnl.load_current_url(url)

    def open_flutter_api_extractor(self):
        """Oeffnet den optionalen statischen Flutter/Dart-API-Extraktor-Vorschaudialog."""
        root = self.ctrl._resolve_root()
        if not root:
            self.ctrl._log("> Statische Engine-APIs: Kein App-Root gefunden (zuerst App importieren/entpacken).")
            return
        db_path = ""
        try:
            db_path = self.app.cfg.paths.get("API_DB", "")
        except Exception:
            db_path = ""
        if not db_path:
            import os
            db_path = os.path.join(self.ctrl._base_dir(), "api_traffic.db")
        from ui.dialogs.flutter_api_extract_dialog import FlutterApiExtractDialog
        FlutterApiExtractDialog(self.winfo_toplevel(), self.app, root, db_path)

    def _v_jni_boundary(self, parent):
        """Java<->nativ-Grenze: JNI-Call-Log (live) + Grenz-Aktionspunkt-Editor (beobachten +
        manipulieren von Eingangs-Args und Rueckgabe, wie die Java-Aktionspunkte)."""
        c = self.ctrl
        f = ttk.Frame(parent)
        ttk.Label(f, text="Grenz-Aktionspunkt (fn/Klasse, Bedingung, Arg/Ret):").pack(side="top", anchor="w")
        r1 = ttk.Frame(f); r1.pack(side="top", fill="x")
        ttk.Label(r1, text="fn(hex):").pack(side="left")
        fnv = tk.StringVar(); ttk.Entry(r1, textvariable=fnv, width=10).pack(side="left")
        ttk.Label(r1, text="Klasse:").pack(side="left", padx=(6, 1))
        clv = tk.StringVar(); ttk.Entry(r1, textvariable=clv, width=14).pack(side="left")
        ttk.Label(r1, text="wenn:").pack(side="left", padx=(6, 1))
        cnv = tk.StringVar(); ttk.Entry(r1, textvariable=cnv, width=12).pack(side="left")
        r2 = ttk.Frame(f); r2.pack(side="top", fill="x")
        ttk.Label(r2, text="Arg#:").pack(side="left")
        aiv = tk.StringVar(); ttk.Entry(r2, textvariable=aiv, width=3).pack(side="left")
        ttk.Label(r2, text=":=").pack(side="left")
        aev = tk.StringVar(); ttk.Entry(r2, textvariable=aev, width=12).pack(side="left")
        acv = tk.BooleanVar(value=False)
        ttk.Checkbutton(r2, text="const", variable=acv).pack(side="left", padx=(4, 0))
        ttk.Label(r2, text="ret:=").pack(side="left", padx=(6, 1))
        rev = tk.StringVar(); ttk.Entry(r2, textvariable=rev, width=12).pack(side="left")
        aplb = tk.Listbox(f, height=4); aplb.pack(side="top", fill="x", pady=2)

        def _refresh_aps():
            aplb.delete(0, "end")
            for sp in getattr(c, "jni_aps", []):
                a0 = sp["arg_assigns"][0] if sp.get("arg_assigns") else None
                aplb.insert("end", "fn=%s cls=%s%s%s%s" % (
                    (hex(sp["fnptr"]) if sp.get("fnptr") is not None else "*"),
                    sp.get("this_cls") or "*",
                    ("  wenn %s" % sp["cond"]) if sp.get("cond") else "",
                    ("  a%s:=%s" % (a0["index"], a0.get("value", a0.get("expr"))) if a0 else ""),
                    ("  ret:=%s" % sp["ret_expr"]) if sp.get("ret_expr") else ""))

        def _add_ap():
            arg_assigns = []
            if aiv.get().strip() and aev.get().strip():
                raw = aev.get().strip()
                if acv.get():
                    try: raw = int(raw, 0)
                    except Exception: pass
                key = "value" if acv.get() else "expr"
                arg_assigns = [{"index": int(aiv.get().strip()), key: raw, "const": bool(acv.get())}]
            c.add_boundary_ap(fnptr=(fnv.get().strip() or None), this_cls=(clv.get().strip() or None),
                              cond=(cnv.get().strip() or None), arg_assigns=arg_assigns,
                              ret_expr=(rev.get().strip() or None))
            _refresh_aps()

        br = ttk.Frame(f); br.pack(side="top", fill="x")
        ttk.Button(br, text="Grenz-AP +", command=_add_ap).pack(side="left", padx=2)
        ttk.Button(br, text="Leeren", command=lambda: (c.clear_boundary_aps(), _refresh_aps())).pack(side="left", padx=2)

        ttk.Separator(f, orient="horizontal").pack(side="top", fill="x", pady=3)
        ttk.Label(f, text="JNI-Call-Log (Java -> nativ, live):").pack(side="top", anchor="w")
        lb = tk.Listbox(f); lb.pack(side="top", fill="both", expand=True)

        def _fill_log():
            lb.delete(0, "end")
            for e in c.jni_entries():
                lb.insert("end", "fn@0x%x %s a=%s -> %s" % (
                    e.get("fnptr", 0), e.get("cls", ""), e.get("args_eff"), e.get("ret_eff")))

        def _on_jni(data):
            d = data or {}
            line = "fn@0x%x %s a=%s -> %s" % (d.get("fnptr", 0), d.get("cls", ""),
                                              d.get("args_eff"), d.get("ret_eff"))
            def _add():
                try: lb.insert("end", line); lb.see("end")
                except Exception: pass
            try: self.app.after(0, _add)
            except Exception: _add()
        try:
            from core.application.event_bus import EventBus
            EventBus.subscribe("JNI_CALL", _on_jni)
        except Exception:
            pass
        br2 = ttk.Frame(f); br2.pack(side="top", fill="x")
        ttk.Button(br2, text="Aktualisieren", command=lambda: (_fill_log(), _refresh_aps())).pack(side="left", padx=2)
        ttk.Button(br2, text="Leeren", command=lambda: lb.delete(0, "end")).pack(side="left", padx=2)
        _refresh_aps(); _fill_log()
        return f

    def _v_action_points(self, parent):
        """Kompakter Aktionspunkt-Editor: bedingte/funktionale/getriggerte Manipulation an der
        aktuell geladenen Methode. Ort = current_target() + pc; Ziel := Wert (const|Ausdruck)."""
        c = self.ctrl
        f = ttk.Frame(parent)
        r1 = ttk.Frame(f); r1.pack(side="top", fill="x")
        ttk.Label(r1, text="pc:").pack(side="left")
        pcv = tk.StringVar(); ttk.Entry(r1, textvariable=pcv, width=4).pack(side="left")
        ttk.Label(r1, text="Ziel:").pack(side="left", padx=(6, 1))
        tgt = tk.StringVar(value="v0"); ttk.Entry(r1, textvariable=tgt, width=8).pack(side="left")
        ttk.Label(r1, text="Wert:").pack(side="left", padx=(6, 1))
        val = tk.StringVar(); ttk.Entry(r1, textvariable=val, width=12).pack(side="left")
        cst = tk.BooleanVar(value=True)
        ttk.Checkbutton(r1, text="const", variable=cst).pack(side="left", padx=(4, 0))
        r2 = ttk.Frame(f); r2.pack(side="top", fill="x")
        ttk.Label(r2, text="wenn:").pack(side="left")
        cond = tk.StringVar(); ttk.Entry(r2, textvariable=cond, width=16).pack(side="left")
        cont = tk.BooleanVar(value=False)
        ttk.Checkbutton(r2, text="continue (ohne Halt)", variable=cont).pack(side="left", padx=(6, 0))
        lb = tk.Listbox(f, height=6); lb.pack(side="top", fill="both", expand=True, pady=2)

        def _refresh_list():
            lb.delete(0, "end")
            for sp in c.aps:
                a0 = sp["assigns"][0] if sp["assigns"] else None
                lb.insert("end", "%s pc=%s  %s := %s%s%s" % (
                    sp.get("name"), sp.get("pc"),
                    (a0["target"] if a0 else "-"),
                    (a0["value"] if a0 else "-"),
                    ("  wenn %s" % sp["cond"]) if sp.get("cond") else "",
                    "  [cont]" if sp.get("when_continue") else ""))

        def _add():
            cls, name, sig = c.current_target()
            if not name:
                c._log("> Aktionspunkt: keine Methode geladen (erst Methode/Index laden)"); return
            raw = val.get().strip()
            value = raw
            if cst.get() and raw:
                try:
                    value = int(raw, 0)
                except Exception:
                    try:
                        value = float(raw)
                    except Exception:
                        value = raw
            assigns = []
            if tgt.get().strip() and raw:
                assigns = [{"target": tgt.get().strip(), "value": value, "const": bool(cst.get())}]
            c.add_action_point(cls, name, sig, (pcv.get().strip() or None), assigns,
                               cond=(cond.get().strip() or None), when_continue=bool(cont.get()))
            _refresh_list()

        def _clear():
            c.clear_action_points(); _refresh_list()

        br = ttk.Frame(f); br.pack(side="top", fill="x")
        ttk.Button(br, text="Aktionspunkt +", command=_add).pack(side="left", padx=2)
        ttk.Button(br, text="Leeren", command=_clear).pack(side="left", padx=2)
        _refresh_list()
        return f

    def _add_tooltip(self, widget, text):
        """Hover-Tooltip fuer selbsterklaerende Debugger-Elemente."""
        state = {"win": None, "job": None}

        def _hide(_e=None):
            if state["job"] is not None:
                try:
                    widget.after_cancel(state["job"])
                except Exception:
                    pass
                state["job"] = None
            w = state["win"]
            state["win"] = None
            if w is not None:
                try:
                    w.destroy()
                except Exception:
                    pass

        def _show():
            state["job"] = None
            try:
                if not widget.winfo_exists():
                    return
                tw = tk.Toplevel(widget)
                tw.wm_overrideredirect(True)
                tw.attributes("-topmost", True)
                x = widget.winfo_rootx() + 8
                y = widget.winfo_rooty() + widget.winfo_height() + 4
                tw.wm_geometry("+%d+%d" % (x, y))
                lbl = tk.Label(
                    tw, text=text, justify="left", wraplength=380,
                    background="#1e293b", foreground="#f8fafc",
                    relief="solid", borderwidth=1, padx=8, pady=5,
                    font=("Segoe UI", 8),
                )
                lbl.pack()
                state["win"] = tw
            except Exception:
                pass

        def _enter(_e=None):
            _hide()
            try:
                state["job"] = widget.after(350, _show)
            except Exception:
                pass

        widget.bind("<Enter>", _enter, add="+")
        widget.bind("<Leave>", _hide, add="+")
        widget.bind("<ButtonPress>", _hide, add="+")
        widget.bind("<Destroy>", _hide, add="+")

    def _show_debugger_help_dialog(self):
        """Erklaert Modus, Seams, Schritt-Budget und Optionen uebersichtlich auf Deutsch."""
        win = tk.Toplevel(self.winfo_toplevel())
        win.title("Debugger-Hilfe: Modus, Seams, Budget & Optionen")
        win.geometry("680x540")
        txt = tk.Text(win, wrap="word", font=("Segoe UI", 9), padx=12, pady=10,
                      bg="#f8fafc", fg="#0f172a", relief="flat")
        vs = ttk.Scrollbar(win, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.tag_configure("h1", font=("Segoe UI", 11, "bold"), foreground="#1d4ed8", spacing1=8, spacing3=3)
        txt.tag_configure("b", font=("Segoe UI", 9, "bold"), foreground="#0f172a")
        sections = [
            ("1. Modus (Ausfuehrungsart)", [
                ("Gläsern+ARM64", "Führt die gesamte App (Application -> MainActivity) in der Dalvik-VM aus UND lädt echte native .so-Libraries über das gemeinsame VFS 2.0 in den ARM64-Emulator (JNI_OnLoad + JNI-Callbacks + Netzwerk)."),
                ("Gläsern App", "Führt den gesamten App-Lifecycle rein in der Dalvik-VM mit Java-/JNI-Shims aus (schneller, ohne ARM64-Emulator)."),
                ("Einzelmethode", "Führt gezielt nur die im Editor gewählte Smali-Methode mit den eingetragenen args isoliert aus."),
            ]),
            ("2. Quelle/Zone & Live-Overlays (Zero-Build)", [
                ("original", "Führt 100 % unveränderten App-Code aus source/ aus (alle Patches deaktiviert)."),
                ("live", "Zero-Build Live-Patches: Nutzt sauberes source/ als Basis und überlagert aktive Änderungen aus Smali Studio, LibForge, LibCarve, Hex Patcher und Lib Replacer sofort im RAM/VFS 2.0 (kein APK-Build nötig)."),
                ("destination", "Führt exakt das physisch gebaute Verzeichnis aus destination/ aus (ohne doppelte RAM-Overlays)."),
                ("Wurzel (Override)", "Optionaler manueller Ordner-Pfad — überschreibt die automatische Zonen-Auflösung."),
            ]),
            ("3. Seams (Nahtstellen-Profil zur nativen Welt)", [
                ("self_cache (Auto-Cache)", "Empfohlen: App entpackt beim ersten Lauf eventuell geschützte .so-Libraries selbst; entpackte Bibliotheken werden für schnelle Folgeläufe gecacht."),
                ("faithful (100% Echt)", "Kein Cache — jeder Lauf führt den kompletten nativen/Java-Entpacker und alle System-Prüfungen unverändert von Grund auf aus."),
                ("noop_native (Nur Java)", "Überspringt das Laden nativer .so-Bibliotheken (SoLoader/System.loadLibrary als No-Op). Ideal, um UI und Java-/Kotlin-Logik schnell zu debuggen."),
            ]),
            ("4. Schritt-Budget & App-Lifecycle", [
                ("Budget (Schritte/Klick)", "Legt fest, wie viele Dalvik-Instruktionen pro Klick auf ▶ Start/Weiter maximal ausgeführt werden (z.B. 50k, 200k, 2M oder ∞). Wird das Budget erreicht, pausiert der Debugger exakt an der aktuellen Smali-Zeile (Call-Stack & Register bleiben live erhalten); mit ▶ Weiter rechnet er ab dort weiter."),
                ("Warum stoppt eine App vor dem Budget?", "Wenn der App-Start (onCreate/onResume) vollständig durchgelaufen ist (z.B. nach 7.336 Schritten bei Compose-/UI-Apps), meldet der Debugger mode=done. Interagiere dann direkt mit der Handy-Anzeige (Klicks/Text), um neue Events auszulösen."),
            ]),
            ("5. Optionen ▾", [
                ("Halt bei Schritt 0 (Entry-Stop)", "Pausiert direkt an der allerersten Instruktion nach dem Start, noch vor Ausführung von Logik."),
                ("Fehler tolerieren (Lenient-Modus)", "Setzt Ausführung fort, wenn ein Android-Framework-Stub oder eine Methode noch nicht implementiert ist (Empfohlen: AN)."),
                ("Live-Overlay Schalter (Smali / LibCarve / LibForge)", "Erlauben das gezielte Zu-/Abschalten einzelner Patch-Quellen bei Quelle 'live', um Änderungen isoliert zu testen."),
                ("Statische Flutter/Engine-APIs extrahieren…", "Optionaler Nur-Lese-Scanner für flutter_assets (.env/JSON) & libapp.so: überführt erkannte Cognito-, AppSync-GraphQL- und REST-Vorlagen nach Auswahl direkt in den API Inspector — ganz ohne 27-MB-Engine-Boot (auch im Netz-Tab über '🔍 Engine-APIs…' erreichbar)."),
                ("Time-Travel Timeline aufzeichnen", "Speichert jeden ausgeführten Schritt für Scrubbing, Lesezeichen und 'Rev' (Step-Back). Etwas langsamer bei sehr großen Läufen."),
                ("Register-Kopie je Schritt", "Merkt zusätzlich alle Registerinhalte in der Timeline."),
                ("Bei Exception sofort pausieren", "Hält sofort an, sobald eine Dalvik-Exception geworfen wird (optional nur bei unbehandelten Exceptions)."),
                ("Flight-Recorder (JSONL)", "Schreibt jeden 100. Schritt, Calls und Pausen nach data/glass_flight.jsonl mit."),
            ]),
            ("6. Studio-Brücken (Smali Studio & LibCarve)", [
                ("Smali-Ansicht", "'🔧 Im Smali Studio öffnen' und '💾 Als Smali-Patch speichern' übernehmen die aktuelle Methode direkt in den Workspace."),
                ("Disassembly-Ansicht", "'🔪 In LibCarve öffnen' und '💾 Als LibCarve-Patch vorbereiten' übergeben Bibliothek und virtuelle Zieladresse direkt an das LibCarve Patch Studio."),
            ]),
        ]
        for heading, items in sections:
            txt.insert("end", heading + "\n", "h1")
            for k, desc in items:
                txt.insert("end", "• " + k + ": ", "b")
                txt.insert("end", desc + "\n\n")
        txt.configure(state="disabled")

    def _v_transport(self, parent):
        c = self.ctrl
        f = ttk.Frame(parent)
        r1 = ttk.Frame(f); r1.pack(side="top", fill="x", pady=(1, 1))
        r2 = ttk.Frame(f); r2.pack(side="top", fill="x", pady=(1, 1))

        # Zeile 1: Modus, Seams, Budget, Transport-Buttons, Optionen, Hilfe, Befund
        lbl_mode = ttk.Label(r1, text="Modus:")
        lbl_mode.pack(side="left", padx=(2, 1))
        mode_init = ("Gläsern+ARM64" if getattr(c, "native_fusion", False) else "Gläsern App")
        mode_v = tk.StringVar(value=mode_init)
        self._mode_var = mode_v
        cb_mode = ttk.Combobox(r1, textvariable=mode_v, width=14, state="readonly",
                               values=("Einzelmethode", "Gläsern App", "Gläsern+ARM64"))
        cb_mode.pack(side="left", padx=2)
        def _on_mode(*_):
            m = mode_v.get()
            c.native_fusion = (m == "Gläsern+ARM64")
        mode_v.trace_add("write", _on_mode)
        self._add_tooltip(
            cb_mode,
            "Ausführungsmodus:\n"
            "• Gläsern App: Ganzer App-Lifecycle in Dalvik mit UI- & Netz-Aufzeichnung.\n"
            "• Gläsern+ARM64: Zusätzlich echte native .so-Libraries im ARM64-Emulator.\n"
            "• Einzelmethode: Nur die im Editor gewählte Smali-Methode isoliert starten.",
        )

        ttk.Label(r1, text="Seams:").pack(side="left", padx=(6, 1))
        _sc_map = {
            "self_cache": "self_cache (Auto-Cache)",
            "faithful": "faithful (100% Echt)",
            "noop_native": "noop_native (Nur Java)",
        }
        sc_init = getattr(getattr(c, "seam_config", None), "unpack_mode", "self_cache")
        seam_v = tk.StringVar(value=_sc_map.get(sc_init, sc_init))
        seam_cb = ttk.Combobox(
            r1, textvariable=seam_v, width=21, state="readonly",
            values=("self_cache (Auto-Cache)", "faithful (100% Echt)", "noop_native (Nur Java)"),
        )
        seam_cb.pack(side="left", padx=2)
        seam_v.trace_add("write", lambda *_: c.set_seam_profile((seam_v.get() or "self_cache").split()[0]))
        self._add_tooltip(
            seam_cb,
            "Nahtstellen-Profil (Java ↔ Nativ):\n"
            "• self_cache: Entpackte .so-Libs beim 1. Lauf cachen.\n"
            "• faithful: 100% echter Lauf ohne Cache.\n"
            "• noop_native: Native Libs überspringen (reines Java/Smali-Debugging).",
        )

        ttk.Label(r1, text="Budget:").pack(side="left", padx=(6, 1))
        self.v_step_budget = tk.StringVar(value="2M")
        cb_budget = ttk.Combobox(
            r1, textvariable=self.v_step_budget, width=6, state="readonly",
            values=("50k", "200k", "500k", "2M", "10M", "\u221e"),
        )
        cb_budget.pack(side="left", padx=2)
        self._add_tooltip(
            cb_budget,
            "Schritt-Budget pro Klick auf ▶ Start/Weiter.\n"
            "Wird das Budget erreicht, pausiert der Debugger exakt an der aktuellen Smali-Zeile "
            "(Call-Stack & Register bleiben erhalten). ▶ Weiter setzt für das nächste Budget fort.",
        )

        def _smart_start():
            jdb = getattr(c, "_jdb", None)
            if jdb is not None and getattr(jdb, "mode", None) == "paused":
                c.cont()
            elif (jdb is not None and getattr(jdb, "mode", None) == "done"
                  and getattr(getattr(c, "glass_tree", None), "root", None) is not None
                  and mode_v.get() != "Einzelmethode"):
                c.refresh_interactive_ui()
            elif mode_v.get() == "Einzelmethode":
                c.start()
            else:
                c.start_app()

        btn_start = ttk.Button(r1, text="\u25b6 Start/Weiter", command=_smart_start)
        btn_start.pack(side="left", padx=2)
        btn_pause = ttk.Button(r1, text="\u23f8 Pause", command=c.pause)
        btn_pause.pack(side="left", padx=1)
        btn_stop = ttk.Button(r1, text="\u23f9 Stop", command=c.stop)
        btn_stop.pack(side="left", padx=1)
        btn_restart = ttk.Button(
            r1, text="\U0001f504 Neu",
            command=lambda: c.start() if mode_v.get() == "Einzelmethode" else c.start_app())
        btn_restart.pack(side="left", padx=1)
        self._add_tooltip(btn_start, "Startet den App-Lauf, setzt ab der Pause fort oder aktualisiert im Event-Loop die UI.")
        self._add_tooltip(btn_pause, "Hält an der nächsten Instruktion an (Call-Stack & Register bleiben sichtbar).")
        self._add_tooltip(btn_stop, "Bricht den aktuellen Lauf ab.")
        self._add_tooltip(btn_restart, "Startet die App bzw. Einzelmethode komplett neu ab Schritt 0.")

        for label, cb, tip in (
            ("In", c.step, "Step Into: 1 Dalvik-Instruktion (auch in Submethoden)."),
            ("Over", c.step_over, "Step Over: Nächste Instruktion auf gleicher Ebene."),
            ("Out", c.step_out, "Step Out: Aktuelle Methode bis zum Rücksprung ausführen."),
            ("Rev", c.reverse_step, "Reverse Step: 1 Schritt zurück (benötigt Time-Travel Option)."),
        ):
            b = ttk.Button(r1, text=label, command=cb)
            b.pack(side="left", padx=1)
            self._add_tooltip(b, tip)

        opt_mb = ttk.Menubutton(r1, text="\u2699 Optionen \u25be")
        opt_menu = tk.Menu(opt_mb, tearoff=0)
        opt_mb.configure(menu=opt_menu)
        sv = tk.BooleanVar(value=c.entry_stop)
        tv = tk.BooleanVar(value=getattr(c, "tolerant", True))
        ov_sm = tk.BooleanVar(value=getattr(c, "overlay_smali", True))
        ov_lc = tk.BooleanVar(value=getattr(c, "overlay_libcarve", True))
        ov_lf = tk.BooleanVar(value=getattr(c, "overlay_libforge", True))
        rtv = tk.BooleanVar(value=getattr(c, "record_timeline", False))
        rrv = tk.BooleanVar(value=getattr(c, "record_regs", False))
        exv = tk.BooleanVar(value=getattr(c, "stop_on_exception", False))
        exu = tk.BooleanVar(value=getattr(c, "exception_unhandled_only", True))
        flv = tk.BooleanVar(value=getattr(c, "flight_recorder_enabled", False))
        opt_menu.add_checkbutton(label="Bei Schritt 0 pausieren (Entry-Stop)", variable=sv,
                                 command=lambda: setattr(c, "entry_stop", bool(sv.get())))
        opt_menu.add_checkbutton(label="Fehlende Stubs tolerieren (Lenient)", variable=tv,
                                 command=lambda: setattr(c, "tolerant", bool(tv.get())))
        opt_menu.add_separator()
        opt_menu.add_checkbutton(label="Live: Smali-Studio-Patches überlagern", variable=ov_sm,
                                 command=lambda: setattr(c, "overlay_smali", bool(ov_sm.get())))
        opt_menu.add_checkbutton(label="Live: LibCarve- & Hex-Patches im VFS überlagern", variable=ov_lc,
                                 command=lambda: setattr(c, "overlay_libcarve", bool(ov_lc.get())))
        opt_menu.add_checkbutton(label="Live: Aktive LibForge-Libs im VFS mounten", variable=ov_lf,
                                 command=lambda: setattr(c, "overlay_libforge", bool(ov_lf.get())))
        opt_menu.add_separator()
        opt_menu.add_checkbutton(label="Time-Travel Timeline aufzeichnen", variable=rtv,
                                 command=lambda: setattr(c, "record_timeline", bool(rtv.get())))
        opt_menu.add_checkbutton(label="Register-Snapshot je Schritt", variable=rrv,
                                 command=lambda: setattr(c, "record_regs", bool(rrv.get())))
        opt_menu.add_checkbutton(label="Bei Exception sofort pausieren", variable=exv,
                                 command=lambda: c.set_stop_on_exception(bool(exv.get()), bool(exu.get())))
        opt_menu.add_checkbutton(label="Nur unbehandelte Exceptions", variable=exu,
                                 command=lambda: c.set_stop_on_exception(bool(exv.get()), bool(exu.get())))
        opt_menu.add_checkbutton(label="Flight-Recorder (JSONL-Mitschnitt)", variable=flv,
                                 command=lambda: c.set_flight_recorder(bool(flv.get())))
        opt_mb.pack(side="left", padx=3)
        self._add_tooltip(opt_mb, "Ausführungs-, Trace- & Patch-Optionen (siehe ❓ Hilfe für Details).")
        opt_menu.add_separator()
        opt_menu.add_command(
            label="🔍 Statische Flutter/Engine-APIs extrahieren…",
            command=self.open_flutter_api_extractor,
        )

        btn_help = ttk.Button(r1, text="\u2753 Hilfe", command=self._show_debugger_help_dialog)
        btn_help.pack(side="left", padx=1)
        self._add_tooltip(btn_help, "Zeigt ausführliche Erklärungen zu Modus, Seams, Budget & Optionen.")

        btn_befund = ttk.Button(r1, text="\U0001f4f8 Befund", command=c.generate_befund_report)
        btn_befund.pack(side="left", padx=2)
        self._add_tooltip(btn_befund, "Erstellt einen Markdown-Befundbericht inkl. Stack, Netz & OS-State.")

        # Zeile 2: Schrittzähler-Badge + Einzelmethode / args / Time-Travel Goto
        self.lbl_budget_info = ttk.Label(r2, text="Schritte: 0 / 200k", foreground="#334155")
        self.lbl_budget_info.pack(side="left", padx=(2, 10))
        ttk.Label(r2, text="Methode:").pack(side="left", padx=(2, 1))
        mv = tk.StringVar(value=c.name)
        ttk.Entry(r2, textvariable=mv, width=18).pack(side="left")
        mv.trace_add("write", lambda *_: setattr(c, "name", mv.get()))
        self._methode_var = mv
        ttk.Label(r2, text="args:").pack(side="left", padx=(8, 1))
        av = tk.StringVar(value=c.args)
        ttk.Entry(r2, textvariable=av, width=14).pack(side="left")
        av.trace_add("write", lambda *_: setattr(c, "args", av.get()))
        ttk.Label(r2, text="Timeline-Goto:").pack(side="left", padx=(8, 1))
        gv = tk.StringVar(value="0")
        ttk.Entry(r2, textvariable=gv, width=6).pack(side="left")
        ttk.Button(r2, text="Go", command=lambda: c.goto(gv.get())).pack(side="left", padx=2)
        return f

    def _v_quelle(self, parent):
        c = self.ctrl
        f = ttk.Frame(parent, padding=2)
        r1 = ttk.Frame(f); r1.pack(side="top", fill="x", pady=1)
        ttk.Label(r1, text="Wurzel:").pack(side="left")
        rv = tk.StringVar(value=c.zone_root)
        ttk.Entry(r1, textvariable=rv, width=14).pack(side="left", fill="x", expand=True, padx=2)
        rv.trace_add("write", lambda *_: setattr(c, "zone_root", rv.get()))
        self._zone_root_var = rv
        ttk.Label(r1, text="Zone:").pack(side="left", padx=(4, 1))
        zv = tk.StringVar(value=c.zone)
        cb_zone = ttk.Combobox(r1, textvariable=zv, width=11, values=("original", "live", "destination"),
                               state="readonly")
        cb_zone.pack(side="left")
        zv.trace_add("write", lambda *_: c.set_source_zone(zv.get(), log=False))
        cb_zone.bind("<<ComboboxSelected>>", lambda _e=None: c.set_source_zone(zv.get(), log=True))
        self._zone_var = zv
        self._add_tooltip(
            cb_zone,
            "Code-/Lib-Quelle (synchron mit 'Stand:' oben):\n"
            "• original: Unverändertes source/\n"
            "• live: Sauberes source/ + aktive Patches (Smali, LibForge, LibCarve, Hex) live im RAM/VFS (ohne Build)\n"
            "• destination: Physisch gebauter Stand in destination/",
        )

        r2 = ttk.Frame(f); r2.pack(side="top", fill="x", pady=1)
        ttk.Label(r2, text="Klasse/Pfad:").pack(side="left")
        pv = tk.StringVar(value=c.relpath)
        ttk.Entry(r2, textvariable=pv, width=18).pack(side="left", fill="x", expand=True, padx=2)
        pv.trace_add("write", lambda *_: setattr(c, "relpath", pv.get()))
        self._relpath_var = pv

        r3 = ttk.Frame(f); r3.pack(side="top", fill="x", pady=2)
        ttk.Button(r3, text="\U0001f4e5 Aus Studio laden", command=c.studio_load).pack(side="left", padx=(0, 4))
        ttk.Button(r3, text="\U0001f50d Index-Suche", command=self._open_index_search).pack(side="left", padx=2)

        r4 = ttk.Frame(f); r4.pack(side="top", fill="x", pady=1)
        ttk.Label(r4, text="App-Entry:").pack(side="left")
        ev = tk.StringVar(value=c.app_entry)
        ttk.Entry(r4, textvariable=ev, width=16).pack(side="left", fill="x", expand=True, padx=2)
        ev.trace_add("write", lambda *_: setattr(c, "app_entry", ev.get()))
        self._app_entry_var = ev
        ttk.Button(r4, text="App-Lifecycle", command=c.lifecycle).pack(side="left", padx=2)
        return f

    def _v_editor(self, parent):
        c = self.ctrl
        f = ttk.Frame(parent)
        eb = ttk.Frame(f); eb.pack(side="top", fill="x")
        ttk.Button(eb, text="\U0001f527 Im Smali Studio öffnen", command=c.open_in_smali_studio).pack(side="left", padx=2, pady=1)
        ttk.Button(eb, text="\U0001f4be Als Smali-Patch speichern", command=c.save_as_smali_patch).pack(side="left", padx=2, pady=1)
        body = ttk.Frame(f); body.pack(side="top", fill="both", expand=True)
        gutter = tk.Canvas(body, width=48, bg="#f0f0f0", highlightthickness=0, takefocus=0)
        gutter.pack(side="left", fill="y")
        vs = ttk.Scrollbar(body, orient="vertical")
        vs.pack(side="right", fill="y")
        txt = tk.Text(body, wrap="none", font=("Consolas", 9), undo=True)
        txt.pack(side="left", fill="both", expand=True)
        txt.insert("1.0", c.source)
        txt.tag_configure("pcline", background="#ffe9a8")
        txt.tag_configure("sy_com", foreground="#6a9955")
        txt.tag_configure("sy_dir", foreground="#2f6df0")
        txt.tag_configure("sy_lbl", foreground="#9b2fae")
        txt.tag_configure("sy_reg", foreground="#0a9396")
        txt.tag_configure("sy_str", foreground="#b5651d")
        txt.tag_configure("sy_num", foreground="#b5651d")
        txt.tag_configure("sy_op", foreground="#1b4fb8")

        def _yset(a, b):
            try:
                vs.set(a, b)
            except Exception:
                pass
            self.redraw_editor_gutter()
        txt.configure(yscrollcommand=_yset)
        vs.configure(command=lambda *a: (txt.yview(*a), self.redraw_editor_gutter()))

        def _on_edit(_e=None):
            setattr(c, "source", txt.get("1.0", "end-1c"))
            if getattr(self, "_edit_job", None):
                try:
                    self.after_cancel(self._edit_job)
                except Exception:
                    pass
            self._edit_job = self.after(300, self.rebuild_code_model)
        txt.bind("<KeyRelease>", _on_edit)
        gutter.bind("<Button-1>", self._editor_on_gutter_click)
        c.bind("editor", txt)
        self._editor_txt = txt
        self._editor_gutter = gutter
        self.after(60, self.rebuild_code_model)
        return f

    def _v_disasm(self, parent):
        c = self.ctrl
        f = ttk.Frame(parent)
        db = ttk.Frame(f); db.pack(side="top", fill="x")
        ttk.Button(db, text="\U0001f52a In LibCarve öffnen", command=c.open_in_libcarve).pack(side="left", padx=2, pady=1)
        ttk.Button(db, text="\U0001f4be Als LibCarve-Patch vorbereiten", command=c.prepare_libcarve_patch).pack(side="left", padx=2, pady=1)
        tr = ttk.Treeview(f, columns=("op", "txt"), show="tree headings", height=12)
        tr.heading("#0", text="pc"); tr.column("#0", width=44, stretch=False)
        tr.heading("op", text="op"); tr.column("op", width=96, stretch=False)
        tr.heading("txt", text="Instruktion"); tr.column("txt", width=260)
        tr.pack(fill="both", expand=True)
        tr.tag_configure("pc", background="#ffe9a8")
        tr.tag_configure("bp", background="#ffd0d0")
        tr.tag_configure("op_branch", foreground="#9b2fae")
        tr.tag_configure("op_invoke", foreground="#2f6df0")
        tr.tag_configure("op_const", foreground="#b5651d")
        tr.tag_configure("op_field", foreground="#0a9396")
        tr.tag_configure("op_ret", foreground="#c0392b")
        tr.bind("<Double-Button-1>", self._disasm_dblclick)
        tr.bind("<<TreeviewSelect>>", self._disasm_select)
        self._disasm_tree = tr
        c.bind("disasm", tr)
        self.after(80, self.rebuild_code_model)
        return f

    def rebuild_code_model(self):
        """Quelltext-Modell neu berechnen; Disasm-Tabelle + Gutter auffrischen."""
        self._edit_job = None
        c = self.ctrl
        try:
            c.refresh_source_map()
        except Exception:
            pass
        tr = getattr(self, "_disasm_tree", None)
        self._disasm_cat = {}
        if tr is not None:
            try:
                if tr.winfo_exists():
                    for i in tr.get_children():
                        tr.delete(i)
                    for e in c._srcmap:
                        cat = self._op_cat(e["op"])
                        self._disasm_cat[e["pc"]] = cat
                        tr.insert("", "end", iid="pc%d" % e["pc"], text=str(e["pc"]),
                                  values=(e["op"], e["text"]), tags=((cat,) if cat else ()))
            except Exception:
                pass
        self._hl_smali(getattr(self, "_editor_txt", None))
        self.redraw_editor_gutter()
        self._sync_disasm_highlight()

    def redraw_editor_gutter(self):
        g = getattr(self, "_editor_gutter", None)
        txt = getattr(self, "_editor_txt", None)
        if g is None or txt is None:
            return
        try:
            if not (g.winfo_exists() and txt.winfo_exists()):
                return
        except Exception:
            return
        try:
            c = self.ctrl
            g.delete("all")
            bp_lines = set()
            for pc in c.bps:
                ln = c.pc_to_line(pc)
                if ln:
                    bp_lines.add(ln)
            pcln = c.current_pc_line()
            idx = txt.index("@0,0")
            guard = 0
            while guard < 100000:
                guard += 1
                dl = txt.dlineinfo(idx)
                if dl is None:
                    break
                y = dl[1]
                lineno = int(str(idx).split(".")[0])
                if lineno in bp_lines:
                    g.create_oval(6, y + 3, 14, y + 11, fill="#e53935", outline="")
                if pcln == lineno:
                    g.create_polygon(17, y + 3, 17, y + 12, 24, y + 7, fill="#f9a825", outline="")
                g.create_text(46, y, anchor="ne", text=str(lineno),
                              font=("Consolas", 8), fill="#777")
                nxt = txt.index("%s+1line" % idx)
                if txt.compare(nxt, ">=", "end") or nxt == idx:
                    break
                idx = nxt
        except Exception:
            pass

    def _editor_on_gutter_click(self, e):
        txt = getattr(self, "_editor_txt", None)
        if txt is None:
            return
        try:
            idx = txt.index("@0,%d" % e.y)
            lineno = int(str(idx).split(".")[0])
        except Exception:
            return
        self.ctrl.toggle_bp_at_line(lineno)

    def sync_code_views(self, st):
        """Vom Controller nach Step/Refresh (UI-Thread): PC-Marker in Editor + Disasm."""
        c = self.ctrl
        pc = st.get("pc") if isinstance(st, dict) else None
        ln = c.pc_to_line(pc) if pc is not None else None
        txt = getattr(self, "_editor_txt", None)
        if txt is not None:
            try:
                if txt.winfo_exists():
                    txt.tag_remove("pcline", "1.0", "end")
                    if ln:
                        txt.update_idletasks()
                        txt.tag_add("pcline", "%d.0" % ln, "%d.end" % ln)
                        txt.see("%d.0" % ln)
            except Exception:
                pass
        self.redraw_editor_gutter()
        try:
            self.after(30, self.redraw_editor_gutter)
        except Exception:
            pass
        lbl_b = getattr(self, "lbl_budget_info", None)
        if lbl_b is not None and isinstance(st, dict):
            try:
                if lbl_b.winfo_exists():
                    step_n = st.get("step") or 0
                    btxt = self.v_step_budget.get() if hasattr(self, "v_step_budget") else "200k"
                    lim = getattr(getattr(c, "_jdb", None), "_budget_limit", None)
                    lim_s = ("\u221e" if btxt == "\u221e" else (f"{lim:,}" if isinstance(lim, int) and lim < 500_000_000 else btxt))
                    _glass_active = getattr(getattr(c, "glass_tree", None), "root", None) is not None
                    if st.get("mode") == "done" and _glass_active:
                        lbl_b.config(text="Bereit \u00b7 UI aktiv (Steps: %s / %s)" % (
                            f"{step_n:,}" if isinstance(step_n, int) else step_n, lim_s))
                    else:
                        rsn = " [Budget-Pause]" if st.get("reason") == "budget" and st.get("mode") == "paused" else ""
                        lbl_b.config(text="Schritte: %s / %s%s" % (
                            f"{step_n:,}" if isinstance(step_n, int) else step_n, lim_s, rsn))
            except Exception:
                pass
        self._sync_disasm_highlight()
        self._fill_object_tree()
        self._fill_watch()
        mr = getattr(self, "_mem_read", None)
        if callable(mr):
            try:
                mr()
            except Exception:
                pass
        sc = getattr(self, "_timeline_scale", None)
        if sc is not None:
            try:
                n = self.ctrl.timeline_len()
                step = st.get("step") if isinstance(st, dict) else None
                if sc.winfo_exists() and n > 1 and isinstance(step, int) and step >= 0:
                    sc.set(min(1.0, step / (n - 1)))
            except Exception:
                pass

    def _sync_disasm_highlight(self):
        tr = getattr(self, "_disasm_tree", None)
        if tr is None:
            return
        try:
            if not tr.winfo_exists():
                return
            c = self.ctrl
            curpc = c._last_pc
            for e in c._srcmap:
                iid = "pc%d" % e["pc"]
                if not tr.exists(iid):
                    continue
                tags = []
                cat = self._disasm_cat.get(e["pc"]) if hasattr(self, "_disasm_cat") else None
                if cat:
                    tags.append(cat)
                if e["pc"] in c.bps:
                    tags.append("bp")
                if curpc is not None and e["pc"] == curpc:
                    tags.append("pc")
                tr.item(iid, tags=tuple(tags))
            if curpc is not None and tr.exists("pc%d" % curpc):
                tr.see("pc%d" % curpc)
        except Exception:
            pass

    def _v_memory(self, parent):
        from core.util.memfmt import hexdump, decode_typed
        c = self.ctrl
        f = ttk.Frame(parent)
        row = ttk.Frame(f); row.pack(side="top", fill="x")
        ttk.Label(row, text="Quelle vN:").pack(side="left")
        sv = tk.StringVar(value="v0"); ttk.Entry(row, textvariable=sv, width=5).pack(side="left")
        ttk.Label(row, text="Off:").pack(side="left", padx=(4, 1))
        ov = tk.StringVar(value="0"); ttk.Entry(row, textvariable=ov, width=5).pack(side="left")
        rv = tk.StringVar(value="i32")
        ttk.Combobox(row, textvariable=rv, width=6, state="readonly",
                     values=("i8", "u8", "i16", "u16", "i32", "u32", "i64", "u64", "f32", "f64", "utf8")
                     ).pack(side="left", padx=(4, 0))
        out = tk.Text(f, wrap="none", font=("Consolas", 9), height=12)
        out.pack(side="top", fill="both", expand=True)
        dec = ttk.Label(f, text="", anchor="w", font=("Consolas", 9))
        dec.pack(side="top", fill="x")

        def _read():
            base, data = c.read_memory(sv.get())
            try:
                off = int(ov.get(), 0)
            except Exception:
                off = 0
            try:
                out.delete("1.0", "end")
                out.insert("1.0", "\n".join(hexdump(data, base)))
                try:
                    dv = decode_typed(data, off, rv.get())
                except Exception as e:
                    dv = "<%s>" % e
                dec.config(text="@+%d %s = %r  (len=%d)" % (off, rv.get(), dv, len(data)))
            except Exception:
                pass
        ttk.Button(row, text="Lesen", command=_read).pack(side="left", padx=4)
        self._mem_read = _read
        f.after(120, _read)
        return f

    def _v_app_state(self, parent):
        """Live-App-State: aktuelle Methode, geladene Klassen (vm._clinit_done) und statische Felder
        ($10/$11/$$e ...). Beobachten UND mitten im Lauf aendern (set_static)."""
        c = self.ctrl
        f = ttk.Frame(parent)
        head = ttk.Label(f, text="Methode: —   |   geladene Klassen: 0", anchor="w")
        head.pack(fill="x", padx=4, pady=(3, 1))
        cols = ("fqn", "val")
        tv = ttk.Treeview(f, columns=cols, show="headings", height=9)
        tv.heading("fqn", text="Statisches Feld"); tv.heading("val", text="Wert")
        tv.column("fqn", width=250); tv.column("val", width=90)
        tv.pack(fill="both", expand=True, padx=4)
        bar = ttk.Frame(f); bar.pack(fill="x", padx=4, pady=2)
        ttk.Label(bar, text="Filter:").pack(side="left")
        flt = tk.StringVar(value="")
        ttk.Entry(bar, textvariable=flt, width=18).pack(side="left", padx=2)

        def _refresh(*_):
            try:
                if not tv.winfo_exists():
                    return
                cm = c.current_method()
                head.config(text="Methode: %s   |   geladene Klassen: %d"
                            % (cm or "—", len(c.loaded_classes())))
                tv.delete(*tv.get_children())
                for fqn, val in sorted(c.app_statics(flt.get().strip() or None).items()):
                    tv.insert("", "end", values=(fqn, repr(val)[:48]))
            except Exception:
                pass

        ttk.Button(bar, text="Aktualisieren", command=_refresh).pack(side="left", padx=4)
        # Edit-Zeile: Klasse / Feld / Wert -> set_static (live)
        eb = ttk.Frame(f); eb.pack(fill="x", padx=4, pady=(0, 3))
        cls_v = tk.StringVar(value="")
        fld_v = tk.StringVar(value="")
        val_v = tk.StringVar(value="1")
        ttk.Entry(eb, textvariable=cls_v, width=22).pack(side="left", padx=1)
        ttk.Entry(eb, textvariable=fld_v, width=8).pack(side="left", padx=1)
        ttk.Entry(eb, textvariable=val_v, width=7).pack(side="left", padx=1)
        ttk.Button(eb, text="Setzen (live)",
                   command=lambda: (c.set_static(cls_v.get().strip(), fld_v.get().strip(),
                                                  val_v.get()), _refresh())).pack(side="left", padx=2)

        def _pick_static(_e=None):
            try:
                sel = tv.selection()
                if not sel:
                    return
                vals = tv.item(sel[0], "values")
                fqn = vals[0] if vals else ""
                if "->" in fqn:
                    cpart, fpart = fqn.split("->", 1)
                    cls_v.set(cpart)
                    fld_v.set(fpart)
                if len(vals) > 1:
                    val_v.set(str(vals[1]).strip("'"))
            except Exception:
                pass
        tv.bind("<<TreeviewSelect>>", _pick_static)

        def _on_evt(_data=None):
            try:
                self.app.after(0, _refresh)
            except Exception:
                pass
        try:
            from core.application.event_bus import EventBus
            EventBus.subscribe("DEBUG_PAUSED", _on_evt)
            EventBus.subscribe("GLASS_STATE", _on_evt)
            EventBus.subscribe("GLASS_STARTED", _on_evt)
            EventBus.subscribe("MANIPULATION_APPLIED", _on_evt)
        except Exception:
            pass
        _refresh()
        return f

    def _v_objinspect(self, parent):
        tr = ttk.Treeview(parent, columns=("type", "preview"), show="tree headings", height=12)
        tr.heading("#0", text="Feld/Reg"); tr.column("#0", width=120)
        tr.heading("type", text="Typ"); tr.column("type", width=120, stretch=False)
        tr.heading("preview", text="Wert"); tr.column("preview", width=180)
        self._obj_tree = tr
        self.after(80, self._fill_object_tree)
        return tr

    def _fill_object_tree(self):
        tr = getattr(self, "_obj_tree", None)
        if tr is None:
            return
        try:
            if not tr.winfo_exists():
                return
            for i in tr.get_children():
                tr.delete(i)
            self._obj_iid = 0
            for node in self.ctrl.object_roots():
                self._insert_obj_node("", node)
        except Exception:
            pass

    def _insert_obj_node(self, parent_iid, node):
        tr = getattr(self, "_obj_tree", None)
        if tr is None:
            return
        self._obj_iid = getattr(self, "_obj_iid", 0) + 1
        iid = "o%d" % self._obj_iid
        try:
            tr.insert(parent_iid, "end", iid=iid, text=node.get("label", ""),
                      values=(node.get("type", ""), node.get("preview", "")))
            for ch in node.get("children", []):
                self._insert_obj_node(iid, ch)
            if node.get("truncated"):
                self._obj_iid += 1
                tr.insert(iid, "end", iid="o%d" % self._obj_iid, text="\u2026",
                          values=("", "(tiefer abgeschnitten)"))
        except Exception:
            pass

    def _disasm_dblclick(self, e):
        tr = getattr(self, "_disasm_tree", None)
        if tr is None:
            return
        try:
            iid = tr.identify_row(e.y) or (tr.selection()[0] if tr.selection() else "")
        except Exception:
            iid = ""
        if iid.startswith("pc"):
            self.ctrl.toggle_bp(int(iid[2:]))

    def _disasm_select(self, _e=None):
        tr = getattr(self, "_disasm_tree", None)
        txt = getattr(self, "_editor_txt", None)
        if tr is None or txt is None:
            return
        try:
            sel = tr.selection()
            if not sel or not sel[0].startswith("pc"):
                return
            ln = self.ctrl.pc_to_line(int(sel[0][2:]))
            if ln and txt.winfo_exists():
                txt.see("%d.0" % ln)
        except Exception:
            pass

    def _v_registers(self, parent):
        c = self.ctrl
        f = ttk.Frame(parent)
        mode_bar = ttk.Frame(f); mode_bar.pack(side="top", fill="x")
        ttk.Label(mode_bar, text="Ansicht:").pack(side="left")
        ttk.Button(mode_bar, text="Dalvik", width=7, command=lambda: c.set_inspect_mode("dalvik")).pack(side="left", padx=2)
        ttk.Button(mode_bar, text="ARM64", width=7, command=lambda: c.set_inspect_mode("arm64")).pack(side="left", padx=2)
        tr = ttk.Treeview(f, columns=("name", "v"), show="tree headings", height=8)
        tr.heading("#0", text="Reg"); tr.column("#0", width=46, stretch=False)
        tr.heading("name", text="Name"); tr.column("name", width=90, stretch=False)
        tr.heading("v", text="Wert"); tr.column("v", width=150)
        tr.pack(side="top", fill="both", expand=True)
        c.bind("registers", tr)
        row = ttk.Frame(f); row.pack(side="top", fill="x")
        ttk.Label(row, text="idx:").pack(side="left")
        iv = tk.StringVar(); ttk.Entry(row, textvariable=iv, width=4).pack(side="left")
        ttk.Label(row, text="Wert:").pack(side="left", padx=(4, 1))
        vv = tk.StringVar(); ttk.Entry(row, textvariable=vv, width=8).pack(side="left")
        ttk.Button(row, text="Set Reg", command=lambda: c.set_reg(iv.get(), vv.get())).pack(side="left", padx=2)
        return f

    def _v_watch(self, parent):
        c = self.ctrl
        f = ttk.Frame(parent)
        tr = ttk.Treeview(f, columns=("v",), show="tree headings", height=6)
        tr.heading("#0", text="Ausdruck"); tr.column("#0", width=120)
        tr.heading("v", text="Wert"); tr.column("v", width=140)
        tr.pack(side="top", fill="both", expand=True)
        self._watch_tree = tr
        row = ttk.Frame(f); row.pack(side="top", fill="x")
        ev = tk.StringVar()
        ttk.Entry(row, textvariable=ev, width=16).pack(side="left")
        ttk.Button(row, text="+", width=2,
                   command=lambda: (c.add_watch(ev.get()), ev.set(""))).pack(side="left", padx=2)
        ttk.Button(row, text="leeren", command=c.clear_watches).pack(side="left")
        self.after(80, self._fill_watch)
        return f

    def _fill_watch(self):
        tr = getattr(self, "_watch_tree", None)
        if tr is None:
            return
        try:
            if not tr.winfo_exists():
                return
            for i in tr.get_children():
                tr.delete(i)
            for expr, val in self.ctrl.watch_values():
                tr.insert("", "end", text=expr, values=(repr(val),))
        except Exception:
            pass

    def _open_index_search(self):
        """Oeffnet die globale Smali-Methoden-Suche (geteilter RAM-Index des Smali Studios)."""
        studio = getattr(getattr(self.app, "workspace_tab", None), "smali_studio", None)
        if studio is None:
            self.ctrl._log("> Smali Studio nicht bereit (erst App laden/entpacken).")
            return
        eng = getattr(studio, "search_engine", None)
        if eng is None:
            self.ctrl._log("> keine Such-Engine verfuegbar.")
            return
        if not getattr(eng, "is_indexed", False):
            try:
                studio._ensure_index_loaded()
            except Exception as e:
                self.ctrl._log("> Index-Aufbau fehlgeschlagen: %r" % e)
                return
            if not getattr(eng, "is_indexed", False):
                self.ctrl._log("> RAM-Index wird aufgebaut - gleich erneut 'Index-Funktion' klicken.")
                return
        try:
            from ui.dialogs.global_search_dialog import SmaliGlobalSearchWindow
            SmaliGlobalSearchWindow(self.winfo_toplevel(), eng.ram_cache, self._on_index_pick)
        except Exception as e:
            self.ctrl._log("> Suchfenster-Fehler: %r" % e)

    def _on_index_pick(self, rel_path, target_line=0):
        """Callback des Suchfensters: Methodenblock aus dem Index in den Debugger laden."""
        studio = getattr(getattr(self.app, "workspace_tab", None), "smali_studio", None)
        if studio is None:
            return
        try:
            content = dict(studio.search_engine.ram_cache).get(rel_path)
        except Exception:
            content = None
        if content is None:
            self.ctrl._log("> Index: Datei nicht gefunden (%s)" % rel_path)
            return
        smali_dir = ""
        try:
            smali_dir = studio.get_smali_dir()
        except Exception:
            pass
        ok = self.ctrl.load_method_block(content, rel_path, target_line, smali_dir)
        if ok:
            try:
                if getattr(self, "_methode_var", None) is not None:
                    self._methode_var.set(self.ctrl.name)
                if getattr(self, "_relpath_var", None) is not None:
                    self._relpath_var.set(self.ctrl.relpath)
            except Exception:
                pass

    @staticmethod
    def _op_cat(op):
        if op.startswith("return"):
            return "op_ret"
        if op.startswith(("if-", "goto")) or "switch" in op:
            return "op_branch"
        if op.startswith("invoke"):
            return "op_invoke"
        if op.startswith(("const", "new-")):
            return "op_const"
        if op.startswith(("iget", "iput", "sget", "sput", "aget", "aput")):
            return "op_field"
        return ""

    def _hl_smali(self, txt):
        """Einfaches Smali-Syntax-Highlighting (Direktiven/Labels/Opcodes/Register/Strings/Hex/Kommentare)."""
        import re
        if txt is None:
            return
        try:
            if not txt.winfo_exists():
                return
            for tg in ("sy_com", "sy_dir", "sy_lbl", "sy_reg", "sy_str", "sy_num", "sy_op"):
                txt.tag_remove(tg, "1.0", "end")
            content = txt.get("1.0", "end-1c")
            for i, line in enumerate(content.split("\n"), start=1):
                ci = line.find("#")
                code = line if ci < 0 else line[:ci]
                if ci >= 0:
                    txt.tag_add("sy_com", "%d.%d" % (i, ci), "%d.end" % i)
                st = line.lstrip()
                ind = len(line) - len(st)
                if st.startswith("."):
                    parts = st.split()
                    tok = parts[0] if parts else st
                    txt.tag_add("sy_dir", "%d.%d" % (i, ind), "%d.%d" % (i, ind + len(tok)))
                elif st.startswith(":"):
                    txt.tag_add("sy_lbl", "%d.%d" % (i, ind), "%d.end" % i)
                else:
                    m = re.match(r"\s*([a-zA-Z][\w/-]*)", line)
                    if m and m.group(1):
                        txt.tag_add("sy_op", "%d.%d" % (i, m.start(1)), "%d.%d" % (i, m.end(1)))
                for m in re.finditer(r'"(?:[^"\\]|\\.)*"', code):
                    txt.tag_add("sy_str", "%d.%d" % (i, m.start()), "%d.%d" % (i, m.end()))
                for m in re.finditer(r"\b[vp]\d+\b", code):
                    txt.tag_add("sy_reg", "%d.%d" % (i, m.start()), "%d.%d" % (i, m.end()))
                for m in re.finditer(r"\b0x[0-9a-fA-F]+\b", code):
                    txt.tag_add("sy_num", "%d.%d" % (i, m.start()), "%d.%d" % (i, m.end()))
        except Exception:
            pass

    def _v_callstack(self, parent):
        c = self.ctrl
        tr = ttk.Treeview(parent, columns=("a", "b"), show="tree headings", height=6)
        tr.heading("#0", text="#"); tr.column("#0", width=30, stretch=False)
        tr.heading("a", text="Methode"); tr.heading("b", text="Pos")
        def _pick(_e=None):
            sel = tr.selection()
            if not sel:
                return
            txt = tr.item(sel[0], "text")
            if str(txt).isdigit():
                c.select_stack_frame(int(txt))
        tr.bind("<Double-Button-1>", _pick)
        c.bind("callstack", tr)
        return tr

    def _v_breakpoints(self, parent):
        c = self.ctrl
        f = ttk.Frame(parent)
        tr = ttk.Treeview(f, columns=("t",), show="tree headings", height=6)
        tr.heading("#0", text="pc"); tr.column("#0", width=50, stretch=False)
        tr.heading("t", text="Typ")
        tr.pack(side="top", fill="both", expand=True)
        c.bind("bplist", tr)
        row = ttk.Frame(f); row.pack(side="top", fill="x")
        ttk.Label(row, text="pc:").pack(side="left")
        pv = tk.StringVar(); ttk.Entry(row, textvariable=pv, width=4).pack(side="left")
        ttk.Label(row, text="hit:").pack(side="left", padx=(4, 1))
        hv = tk.StringVar(); ttk.Entry(row, textvariable=hv, width=3).pack(side="left")
        osv = tk.BooleanVar()
        ttk.Checkbutton(row, text="1x", variable=osv).pack(side="left", padx=(4, 0))
        row2 = ttk.Frame(f); row2.pack(side="top", fill="x")
        ttk.Label(row2, text="if:").pack(side="left")
        cv = tk.StringVar(); ttk.Entry(row2, textvariable=cv, width=16).pack(side="left")
        ttk.Button(row2, text="+ BP",
                   command=lambda: c.add_bp_ex(pv.get(), cv.get(), hv.get(), bool(osv.get()))).pack(side="left", padx=2)
        ttk.Button(row2, text="leeren", command=c.clear_bp).pack(side="left")
        return f

    def _v_console(self, parent):
        t = tk.Text(parent, wrap="none", height=6, font=("Consolas", 9), state="disabled")
        self.ctrl.bind("console", t)
        return t

    def _v_timeline(self, parent):
        c = self.ctrl
        f = ttk.Frame(parent)
        tr = ttk.Treeview(f, columns=("pc", "op"), show="tree headings", height=6)
        tr.heading("#0", text="Step"); tr.column("#0", width=50, stretch=False)
        tr.heading("pc", text="pc"); tr.heading("op", text="op")
        tr.pack(side="top", fill="both", expand=True)
        tr.tag_configure("bm", background="#fff2b0")
        tr.bind("<Double-Button-1>", self._timeline_goto)
        c.bind("timeline", tr)
        self._timeline_tree = tr
        row = ttk.Frame(f); row.pack(side="top", fill="x")
        sc = ttk.Scale(row, from_=0.0, to=1.0, orient="horizontal")
        sc.pack(side="left", fill="x", expand=True)
        sc.bind("<ButtonRelease-1>", lambda e: c.scrub_to(sc.get()))
        self._timeline_scale = sc
        ttk.Button(row, text="\u2605", width=3, command=c.bookmark_current).pack(side="left", padx=2)

        # --- Marker: Netz- und Manipulations-Ereignisse auf der Zeitachse ---
        ttk.Label(f, text="Marker (Netz / Manipulation):").pack(side="top", anchor="w")
        mk = tk.Listbox(f, height=4); mk.pack(side="top", fill="x", pady=2)
        mk.tag = None
        tr.tag_configure("mk", background="#cfe8ff")
        self._marker_list = mk

        def _refresh_markers():
            mk.delete(0, "end")
            self._marker_rows = []
            try:
                items = c.markers()
            except Exception:
                items = []
            steps = {}
            for m in items:
                step = m.get("step"); kind = m.get("kind", "?"); lbl = m.get("label", "")
                sym = "NET" if kind == "net" else ("MANIP" if kind == "manip" else kind.upper())
                mk.insert("end", "@%s  [%s]  %s" % (step, sym, lbl))
                self._marker_rows.append(step)
                steps[step] = kind
            # Step-Zeilen der Zeitachse markieren (Bookmarks bleiben unberuehrt)
            try:
                for iid in tr.get_children(""):
                    try:
                        sidx = int(tr.item(iid, "text"))
                    except Exception:
                        continue
                    if sidx in steps and "bm" not in (tr.item(iid, "tags") or ()):
                        tr.item(iid, tags=("mk",))
            except Exception:
                pass

        def _marker_goto(e):
            try:
                i = mk.nearest(e.y)
                if 0 <= i < len(getattr(self, "_marker_rows", [])):
                    self.ctrl.goto(self._marker_rows[i])
            except Exception:
                pass
        mk.bind("<Double-Button-1>", _marker_goto)
        ttk.Button(f, text="Marker aktualisieren", command=_refresh_markers).pack(side="top", anchor="w", pady=1)
        self._refresh_markers = _refresh_markers
        return f

    def _timeline_goto(self, e):
        tr = getattr(self, "_timeline_tree", None)
        if tr is None:
            return
        try:
            iid = tr.identify_row(e.y)
            if not iid:
                return
            step = int(tr.item(iid, "text"))
        except Exception:
            return
        self.ctrl.goto(step)

    def _v_seams_ipc(self, parent):
        try:
            from ui.widgets.seams_inspector_panel import SeamsInspectorPanel
            self.seams_panel = SeamsInspectorPanel(parent, self.ctrl)
            return self.seams_panel
        except Exception as e:
            return ttk.Label(parent, text=f"Seams & IPC Fehler: {e}")

    def _v_ui_tree(self, parent):
        try:
            from ui.widgets.seams_inspector_panel import UiTreeInspectorPanel
            self.ui_tree_panel = UiTreeInspectorPanel(parent, self.ctrl)
            return self.ui_tree_panel
        except Exception as e:
            return ttk.Label(parent, text=f"UI-Inspector Fehler: {e}")

    def _v_missing(self, parent):
        wrap = ttk.Frame(parent)
        bar = ttk.Frame(wrap)
        bar.pack(fill="x", padx=2, pady=2)
        flt_var = tk.StringVar(value="")
        eng_var = tk.StringVar(value="Alle")
        ttk.Label(bar, text="Filter:").pack(side="left", padx=(2, 2))
        flt_ent = ttk.Entry(bar, textvariable=flt_var, width=18)
        flt_ent.pack(side="left", padx=2)
        ttk.Label(bar, text="Engine:").pack(side="left", padx=(6, 2))
        eng_cb = ttk.Combobox(
            bar, textvariable=eng_var, width=14, state="readonly",
            values=("Alle", "Compose", "Net/gRPC", "Crypto/Billing", "Hardware", "IPC",
                    "Nav/Rx/Paging", "Flutter/RN/Web", "Java/Android"),
        )
        eng_cb.pack(side="left", padx=2)

        tr = ttk.Treeview(wrap, columns=("sig", "engine"), show="tree headings", height=6)
        tr.heading("#0", text="Klasse")
        tr.heading("sig", text="Signatur")
        tr.heading("engine", text="Engine")
        tr.column("engine", width=110)

        def _copy_stub():
            sel = tr.selection()
            if not sel:
                return
            item = tr.item(sel[0])
            cls_t = item.get("text", "")
            vals = item.get("values") or ("",)
            sig_t = vals[0] if vals else ""
            snip = f'stubs.register("{cls_t}", "{sig_t.split("(", 1)[0]}", "({sig_t.split("(", 1)[1] if "(" in sig_t else ")V"}", lambda a: None)'
            try:
                wrap.clipboard_clear()
                wrap.clipboard_append(snip)
            except Exception:
                pass

        ttk.Button(bar, text="📋 Stub", command=_copy_stub).pack(side="right", padx=2)
        tr.pack(fill="both", expand=True)

        def _apply_filter(rows=None):
            from ui.widgets.seams_inspector_panel import classify_missing_engine
            src = list(rows) if rows is not None else list(getattr(self.ctrl, "_last_missing", None) or [])
            f_txt = flt_var.get().strip().lower()
            e_txt = eng_var.get().strip()
            for iid in tr.get_children():
                tr.delete(iid)
            shown = 0
            for m in src:
                cls_s = str(m.get("cls", "") or "")
                nm_s = str(m.get("name", "") or "")
                sg_s = str(m.get("sig", "") or "")
                eng_s = classify_missing_engine(cls_s, sg_s)
                if e_txt and e_txt != "Alle" and eng_s != e_txt:
                    continue
                if f_txt and f_txt not in cls_s.lower() and f_txt not in nm_s.lower() and f_txt not in sg_s.lower():
                    continue
                tr.insert("", "end", text=cls_s, values=(f"{nm_s}{sg_s}", eng_s))
                shown += 1
                if shown >= 250:
                    break

        tr._apply_missing_data = _apply_filter
        flt_ent.bind("<KeyRelease>", lambda _e=None: _apply_filter())
        eng_cb.bind("<<ComboboxSelected>>", lambda _e=None: _apply_filter())
        self.ctrl.bind("missing", tr)
        return wrap

    def _v_status(self, parent):
        lbl = ttk.Label(parent, text="idle", anchor="w", font=("Consolas", 9))
        self.ctrl.bind("status", lbl)
        return lbl
