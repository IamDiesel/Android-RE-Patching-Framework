# -*- coding: utf-8 -*-
"""ui.widgets.browser_inspector_panel — Integrierte Browser-Anzeige inkl. DOM/Netz/CDP-Inspect."""
from __future__ import annotations

import json
import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from services.webview_inspector_service import (
    DEFAULT_MOBILE_UA,
    WebViewInspectorService,
    discover_app_urls,
)


DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class BrowserInspectorPanel(ttk.Frame):
    """Eigen-Browser + Live-Inspect (DOM, Header, Links, Formulare, Quelltext, Chromium CDP)."""

    def __init__(self, parent, app=None, on_switch_to_screen=None):
        super().__init__(parent)
        self.app = app
        self.on_switch_to_screen = on_switch_to_screen
        base_dir = "."
        try:
            base_dir = self.app.cfg.config.get("BASE_DIR") or "."
        except Exception:
            pass
        self.service = WebViewInspectorService(base_dir=base_dir)
        self._last_result = None
        self._dom_map = {}
        self._screenshot_photo = None
        self._web_events = []
        self._build_ui()
        self.after(150, self.refresh_discovered_urls)

    def _log(self, msg: str):
        try:
            if self.app and callable(getattr(self.app, "log", None)):
                self.app.log(f"[Browser/Inspect] {msg}")
        except Exception:
            pass

    def _app_root(self) -> str:
        try:
            ctrl = getattr(getattr(self.app, "debugger_tab", None), "ctrl", None)
            if ctrl is not None:
                r = ctrl._resolve_root() if hasattr(ctrl, "_resolve_root") else getattr(ctrl, "zone_root", "")
                if r:
                    return r
            cfg = getattr(self.app, "cfg", None)
            if cfg is not None:
                src = cfg.paths.get("SOURCE_APK", "")
                if src:
                    return os.path.join(src, cfg.config.get("UNPACK_DIR", "base_unpacked_apktool"))
        except Exception:
            pass
        return ""

    def _build_ui(self):
        # --- Kopfzeile: App-URLs + Adressleiste + Chromium ---
        top1 = ttk.Frame(self)
        top1.pack(fill="x", padx=6, pady=(5, 2))
        if callable(self.on_switch_to_screen):
            ttk.Button(top1, text="📱 Handy-Screen", command=self.on_switch_to_screen).pack(side="left", padx=(0, 6))

        ttk.Label(top1, text="App-URLs:").pack(side="left")
        self.cmb_app_urls = ttk.Combobox(top1, width=42, state="readonly")
        self.cmb_app_urls.pack(side="left", padx=4, fill="x", expand=True)
        self.cmb_app_urls.bind("<<ComboboxSelected>>", self._on_pick_app_url)
        ttk.Button(top1, text="🔄 URLs scannen", command=self.refresh_discovered_urls).pack(side="left", padx=2)

        self.var_ua = tk.StringVar(value="Mobile Android")
        ttk.Combobox(top1, textvariable=self.var_ua, values=("Mobile Android", "Desktop Chrome"),
                     width=14, state="readonly").pack(side="left", padx=4)

        top2 = ttk.Frame(self)
        top2.pack(fill="x", padx=6, pady=2)
        ttk.Label(top2, text="URL:").pack(side="left")
        self.ent_url = ttk.Entry(top2)
        self.ent_url.pack(side="left", fill="x", expand=True, padx=4)
        self.ent_url.bind("<Return>", lambda _e: self.load_current_url())
        ttk.Button(top2, text="▶ Laden & Inspect", command=self.load_current_url).pack(side="left", padx=2)
        ttk.Button(top2, text="🌐 Chromium + Inspect (F12)", command=self.open_in_chromium).pack(side="left", padx=2)
        self.var_proxy = tk.BooleanVar(value=False)
        ttk.Checkbutton(top2, text="MITM-Proxy", variable=self.var_proxy).pack(side="left", padx=2)
        ttk.Button(top2, text="📤 -> API Inspector", command=self.send_to_api_inspector).pack(side="left", padx=2)

        self.lbl_status = ttk.Label(self, text="Bereit — App-URL wählen oder URL eingeben.", anchor="w")
        self.lbl_status.pack(fill="x", padx=8, pady=(0, 3))

        # --- Hauptbereich: Links Browser-Vorschau, Rechts Inspect ---
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        # Links: Browser-Vorschau (Eigenlösung / Live-CDP-Screenshot)
        left = ttk.LabelFrame(paned, text="Web-Vorschau (Eigenlösung / Chromium-Screen)")
        paned.add(left, weight=2)

        prev_nb = ttk.Notebook(left)
        prev_nb.pack(fill="both", expand=True, padx=3, pady=3)
        self.prev_nb = prev_nb

        f_render = ttk.Frame(prev_nb)
        prev_nb.add(f_render, text="📄 Render-Ansicht")
        self.txt_preview = tk.Text(f_render, wrap="word", bg="#fafafa", fg="#1f2937",
                                   font=("Segoe UI", 10), padx=10, pady=8)
        sy_p = ttk.Scrollbar(f_render, orient="vertical", command=self.txt_preview.yview)
        self.txt_preview.configure(yscrollcommand=sy_p.set)
        sy_p.pack(side="right", fill="y")
        self.txt_preview.pack(side="left", fill="both", expand=True)
        self.txt_preview.tag_configure("h1", font=("Segoe UI", 13, "bold"), foreground="#111827", spacing3=6)
        self.txt_preview.tag_configure("h2", font=("Segoe UI", 11, "bold"), foreground="#1e3a8a", spacing1=6, spacing3=3)
        self.txt_preview.tag_configure("link", foreground="#2563eb", underline=True)
        self.txt_preview.tag_configure("meta", foreground="#6b7280", font=("Segoe UI", 8))

        f_shot = ttk.Frame(prev_nb)
        prev_nb.add(f_shot, text="🖥 Chromium-Live-Bild")
        self.lbl_shot = ttk.Label(f_shot, text="Klicke oben 'Chromium + Inspect (F12)' und rechts im Tab\n'Chromium CDP -> Live-Screenshot holen'.",
                                  anchor="center", justify="center")
        self.lbl_shot.pack(fill="both", expand=True, padx=8, pady=8)

        # Rechts: 5 Inspect-Tabs
        right = ttk.LabelFrame(paned, text="Inspektor (DevTools)")
        paned.add(right, weight=3)

        ins_nb = ttk.Notebook(right)
        ins_nb.pack(fill="both", expand=True, padx=3, pady=3)
        self.ins_nb = ins_nb

        # 1. DOM & Elemente
        f_dom = ttk.Frame(ins_nb)
        ins_nb.add(f_dom, text="🧩 DOM & Elemente")
        dom_split = ttk.PanedWindow(f_dom, orient=tk.VERTICAL)
        dom_split.pack(fill="both", expand=True)
        dom_top = ttk.Frame(dom_split)
        dom_split.add(dom_top, weight=3)
        self.tree_dom = ttk.Treeview(dom_top, columns=("Attrs", "Text"), show="tree headings")
        self.tree_dom.heading("#0", text="Tag")
        self.tree_dom.heading("Attrs", text="Attribute")
        self.tree_dom.heading("Text", text="Text")
        self.tree_dom.column("#0", width=130)
        self.tree_dom.column("Attrs", width=210)
        sy_d = ttk.Scrollbar(dom_top, orient="vertical", command=self.tree_dom.yview)
        self.tree_dom.configure(yscrollcommand=sy_d.set)
        sy_d.pack(side="right", fill="y")
        self.tree_dom.pack(side="left", fill="both", expand=True)
        self.tree_dom.bind("<<TreeviewSelect>>", self._on_dom_select)
        self.txt_dom_detail = tk.Text(dom_split, height=6, bg="#1e1e1e", fg="#d4d4d4", font=("Consolas", 9))
        dom_split.add(self.txt_dom_detail, weight=1)

        # 2. Netzwerk & Header
        f_net = ttk.Frame(ins_nb)
        ins_nb.add(f_net, text="📡 Netzwerk & Header")
        self.txt_net = tk.Text(f_net, wrap="none", bg="#1e1e1e", fg="#d4d4d4", font=("Consolas", 9))
        self.txt_net.pack(fill="both", expand=True)

        # 3. Links, Formulare & APIs
        f_api = ttk.Frame(ins_nb)
        ins_nb.add(f_api, text="🔗 Links, Formulare & APIs")
        self.tree_apis = ttk.Treeview(f_api, columns=("Kind", "Target", "Info"), show="headings")
        self.tree_apis.heading("Kind", text="Typ")
        self.tree_apis.heading("Target", text="URL / Action")
        self.tree_apis.heading("Info", text="Detail")
        self.tree_apis.column("Kind", width=90, stretch=False)
        self.tree_apis.column("Target", width=310)
        self.tree_apis.pack(fill="both", expand=True)
        self.tree_apis.bind("<Double-1>", self._on_api_dblclick)

        # 4. Quelltext
        f_src = ttk.Frame(ins_nb)
        ins_nb.add(f_src, text="📜 Quelltext")
        src_bar = ttk.Frame(f_src)
        src_bar.pack(fill="x", padx=2, pady=2)
        ttk.Label(src_bar, text="Suche:").pack(side="left")
        self.ent_src_find = ttk.Entry(src_bar, width=24)
        self.ent_src_find.pack(side="left", padx=4)
        ttk.Button(src_bar, text="Suchen", command=self._find_in_source).pack(side="left")
        self.txt_src = tk.Text(f_src, wrap="none", bg="#1e1e1e", fg="#d4d4d4", font=("Consolas", 9))
        self.txt_src.pack(fill="both", expand=True)

        # 5. Chromium CDP & Konsole
        f_cdp = ttk.Frame(ins_nb)
        ins_nb.add(f_cdp, text="🛠 Chromium CDP & Konsole")
        cdp_bar = ttk.Frame(f_cdp)
        cdp_bar.pack(fill="x", padx=4, pady=4)
        ttk.Button(cdp_bar, text="📸 Live-Screenshot", command=self._cdp_screenshot).pack(side="left", padx=2)
        ttk.Button(cdp_bar, text="📄 Live-DOM ziehen", command=self._cdp_pull_dom).pack(side="left", padx=2)
        ttk.Button(cdp_bar, text="🍪 Cookies/Tokens", command=lambda: self._cdp_eval("JSON.stringify({cookie: document.cookie, localStorage: Object.entries(localStorage || {})})")).pack(side="left", padx=2)
        js_row = ttk.Frame(f_cdp)
        js_row.pack(fill="x", padx=4, pady=2)
        ttk.Label(js_row, text="JS>").pack(side="left")
        self.ent_js = ttk.Entry(js_row)
        self.ent_js.insert(0, "document.title + ' | ' + location.href")
        self.ent_js.pack(side="left", fill="x", expand=True, padx=4)
        self.ent_js.bind("<Return>", lambda _e: self._cdp_eval(self.ent_js.get()))
        ttk.Button(js_row, text="Ausführen", command=lambda: self._cdp_eval(self.ent_js.get())).pack(side="left")
        self.txt_cdp = tk.Text(f_cdp, bg="#111827", fg="#a7f3d0", font=("Consolas", 9))
        self.txt_cdp.pack(fill="both", expand=True, padx=4, pady=4)

    def add_web_event(self, ev: dict):
        if not isinstance(ev, dict) or not ev.get("url"):
            return
        self._web_events.append(ev)
        self.refresh_discovered_urls()
        if not self.ent_url.get().strip():
            self.ent_url.insert(0, ev["url"])

    def refresh_discovered_urls(self):
        app_root = self._app_root()
        ctrl = getattr(getattr(self.app, "debugger_tab", None), "ctrl", None)
        events = list(self._web_events)
        try:
            tree = getattr(ctrl, "glass_tree", None)
            if tree is not None:
                events.extend(getattr(tree, "web_events", ()) or ())
        except Exception:
            pass
        found = discover_app_urls(app_root, web_events=events)
        labels = [f"[{it['kind']}] {it['url']}  — {it['source']}" for it in found]
        self._discovered = found
        self.cmb_app_urls["values"] = labels
        if found and not self.ent_url.get().strip():
            self.cmb_app_urls.current(0)
            self.ent_url.delete(0, tk.END)
            self.ent_url.insert(0, found[0]["url"])

    def _on_pick_app_url(self, _e=None):
        idx = self.cmb_app_urls.current()
        if 0 <= idx < len(getattr(self, "_discovered", ())):
            u = self._discovered[idx]["url"]
            self.ent_url.delete(0, tk.END)
            self.ent_url.insert(0, u)

    def load_current_url(self, url_override: str | None = None):
        target = (url_override if url_override is not None else self.ent_url.get()).strip()
        if not target:
            messagebox.showinfo("Browser & Inspect", "Bitte zuerst eine URL oder HTML-Asset-Datei wählen.", parent=self)
            return
        self.ent_url.delete(0, tk.END)
        self.ent_url.insert(0, target)
        ua = DESKTOP_UA if "Desktop" in self.var_ua.get() else DEFAULT_MOBILE_UA
        if self.var_proxy.get():
            port = 8080
            try:
                port = int(self.app.cfg.config.get("PROXY_PORT", 8080))
            except Exception:
                pass
            self.service.proxy_url = f"http://127.0.0.1:{port}"
        else:
            self.service.proxy_url = None
        self.lbl_status.config(text=f"Lade {target} ...")

        def _worker():
            res = self.service.fetch_and_inspect(target, user_agent=ua)
            if self.winfo_exists():
                self.after(0, lambda: self._apply_inspection_result(res))

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_inspection_result(self, res: dict):
        self._last_result = res
        if not res.get("ok") and not res.get("status"):
            self.lbl_status.config(text=f"Fehler: {res.get('error', '?')}")
            self.txt_preview.delete("1.0", tk.END)
            self.txt_preview.insert("1.0", f"Fehler beim Laden von {res.get('url', '')}:\n{res.get('error', '')}")
            return

        self.lbl_status.config(
            text=f"HTTP {res.get('status')} {res.get('reason', '')} · {res.get('elapsed_ms', 0)} ms · {res.get('size', 0)} Bytes · {res.get('final_url', '')}"
        )
        # Render-Vorschau
        tp = self.txt_preview
        tp.delete("1.0", tk.END)
        tp.insert(tk.END, f"{res.get('title', '')}\n", "h1")
        tp.insert(tk.END, f"URL: {res.get('final_url', '')}\nStatus: HTTP {res.get('status')} · Links: {len(res.get('links', []))} · Formulare: {len(res.get('forms', []))}\n\n", "meta")
        for tag, txt in (res.get("text_blocks") or [])[:250]:
            style = "h2" if tag in ("h1", "h2", "h3") else None
            prefix = "🔗 " if tag == "a" else ("• " if tag == "li" else "")
            tp.insert(tk.END, f"{prefix}{txt}\n", style or ())
        if not res.get("text_blocks") and res.get("text"):
            tp.insert(tk.END, res["text"][:15000])

        # DOM Tree
        self.tree_dom.delete(*self.tree_dom.get_children())
        self._dom_map.clear()

        def _add_dom(parent_iid, dnode, depth=0):
            if depth > 14 or len(self._dom_map) > 800:
                return
            attrs = dnode.get("attrs") or {}
            astr = " ".join(f"{k}={v}" for k, v in list(attrs.items())[:5])
            txt = (dnode.get("text") or "")[:90]
            iid = self.tree_dom.insert(parent_iid, "end", text=f"<{dnode.get('tag', '?')}>", values=(astr, txt))
            self._dom_map[iid] = dnode
            for ch in dnode.get("children") or ():
                _add_dom(iid, ch, depth + 1)

        dom_root = res.get("dom") or {}
        _add_dom("", dom_root)

        # Netzwerk & Header
        lines = [
            f"=== REQUEST ===",
            f"{res.get('method', 'GET')} {res.get('url', '')}",
            f"Final-URL: {res.get('final_url', '')}",
        ]
        for k, v in (res.get("req_headers") or {}).items():
            lines.append(f"  {k}: {v}")
        lines.append("\n=== RESPONSE ===")
        lines.append(f"Status: {res.get('status')} {res.get('reason', '')} ({res.get('elapsed_ms', 0)} ms, {res.get('size', 0)} B)")
        for k, v in (res.get("resp_headers") or {}).items():
            lines.append(f"  {k}: {v}")
        if res.get("cookies"):
            lines.append("\n=== COOKIES ===")
            for ck in res["cookies"]:
                lines.append(f"  {ck['name']}={ck['value']} (domain={ck['domain']}, path={ck['path']})")
        self.txt_net.delete("1.0", tk.END)
        self.txt_net.insert("1.0", "\n".join(lines))

        # Links / Formulare / APIs
        self.tree_apis.delete(*self.tree_apis.get_children())
        for lk in res.get("links") or ():
            self.tree_apis.insert("", "end", values=("Link", lk["href"], lk.get("text", "")[:70]))
        for fm in res.get("forms") or ():
            flds = ", ".join(f"{i['name'] or i['type']}={i['value']}" for i in fm.get("inputs", []))
            self.tree_apis.insert("", "end", values=(f"Form ({fm['method']})", fm["action"], flds[:90]))
        for sc in res.get("scripts") or ():
            if sc.get("src"):
                self.tree_apis.insert("", "end", values=("Script", sc["src"], ""))
        for au in res.get("api_urls") or ():
            self.tree_apis.insert("", "end", values=("API/URL", au, "aus HTML/JS"))

        # Quelltext
        self.txt_src.delete("1.0", tk.END)
        self.txt_src.insert("1.0", res.get("text", "")[:120000])

    def _on_dom_select(self, _e=None):
        sel = self.tree_dom.selection()
        if not sel:
            return
        dn = self._dom_map.get(sel[0])
        if dn is None:
            return
        self.txt_dom_detail.delete("1.0", tk.END)
        self.txt_dom_detail.insert("1.0", json.dumps({
            "tag": dn.get("tag"),
            "attrs": dn.get("attrs"),
            "text": dn.get("text"),
            "children_count": len(dn.get("children") or ()),
        }, ensure_ascii=False, indent=2))

    def _on_api_dblclick(self, _e=None):
        sel = self.tree_apis.selection()
        if not sel:
            return
        vals = self.tree_apis.item(sel[0], "values")
        if vals and len(vals) >= 2 and vals[1]:
            self.load_current_url(vals[1])

    def _find_in_source(self):
        q = self.ent_src_find.get().strip()
        if not q:
            return
        self.txt_src.tag_remove("search_hit", "1.0", tk.END)
        self.txt_src.tag_configure("search_hit", background="#854d0e", foreground="#ffffff")
        pos = self.txt_src.search(q, "1.0", nocase=True, stopindex=tk.END)
        if pos:
            end = f"{pos}+{len(q)}c"
            self.txt_src.tag_add("search_hit", pos, end)
            self.txt_src.see(pos)

    def open_in_chromium(self):
        url = self.ent_url.get().strip() or "about:blank"
        port = 8080
        try:
            port = int(self.app.cfg.config.get("PROXY_PORT", 8080))
        except Exception:
            pass
        mobile = "Desktop" not in self.var_ua.get()
        res = self.service.launch_chromium_with_inspect(
            url, use_proxy=bool(self.var_proxy.get()), proxy_addr=f"127.0.0.1:{port}", mobile=mobile,
        )
        if not res.get("ok"):
            messagebox.showwarning("Chromium", res.get("error", "Start fehlgeschlagen."), parent=self)
            return
        self.lbl_status.config(text=f"Chromium + DevTools gestartet (PID {res.get('pid')}, CDP-Port {res.get('cdp_port')})")
        self._log(f"Chromium gestartet ({os.path.basename(res.get('exe', ''))}) auf CDP-Port {res.get('cdp_port')}")
        self.after(1400, self._cdp_screenshot)

    def _cdp_screenshot(self):
        def _w():
            png = self.service.capture_cdp_screenshot()
            if not png or not self.winfo_exists():
                return
            def _ui():
                try:
                    import base64
                    self._screenshot_photo = tk.PhotoImage(data=base64.b64encode(png).decode("ascii"))
                    try:
                        from PIL import Image, ImageTk
                        import io
                        im = Image.open(io.BytesIO(png))
                        im.thumbnail((420, 760))
                        self._screenshot_photo = ImageTk.PhotoImage(im)
                    except Exception:
                        pass
                    self.lbl_shot.config(image=self._screenshot_photo, text="")
                    self.prev_nb.select(1)
                except Exception:
                    pass
            self.after(0, _ui)
        threading.Thread(target=_w, daemon=True).start()

    def _cdp_pull_dom(self):
        def _w():
            html = self.service.get_cdp_dom_html()
            if not self.winfo_exists():
                return
            def _ui():
                if html and not html.startswith("(Keine offene"):
                    res = self.service._build_inspection_result(
                        url=self.ent_url.get().strip() or "cdp://page",
                        final_url=self.ent_url.get().strip() or "cdp://page",
                        method="CDP", status=200, reason="OK",
                        req_headers={}, resp_headers={"Content-Type": "text/html"},
                        raw_bytes=html.encode("utf-8", "replace"), text=html, elapsed_ms=0,
                    )
                    self._apply_inspection_result(res)
                else:
                    self.txt_cdp.insert(tk.END, f"\n{html}\n")
            self.after(0, _ui)
        threading.Thread(target=_w, daemon=True).start()

    def _cdp_eval(self, expr: str):
        def _w():
            out = self.service.eval_cdp_js(expr)
            if self.winfo_exists():
                self.after(0, lambda: self.txt_cdp.insert(tk.END, f"\n> {expr}\n{out}\n"))
        threading.Thread(target=_w, daemon=True).start()

    def send_to_api_inspector(self):
        res = self._last_result
        if not res or not res.get("url"):
            messagebox.showinfo("API Inspector", "Bitte zuerst eine URL laden.", parent=self)
            return
        try:
            ctrl = getattr(getattr(self.app, "debugger_tab", None), "ctrl", None)
            if ctrl is not None and getattr(ctrl, "net_log", None) is not None:
                from core.java.net_recorder import NetRequest, NetResponse
                rq = NetRequest(res.get("method", "GET"), res["final_url"], res.get("req_headers", {}), b"")
                rp = NetResponse(int(res.get("status", 200)), res.get("resp_headers", {}), res.get("raw_bytes", b""), res.get("reason", "OK"))
                ctrl.net_log.record(rq, rp)
            api_db = getattr(ctrl, "_api_db", None) if ctrl is not None else None
            if api_db is not None and hasattr(api_db, "log_request"):
                api_db.log_request(
                    res.get("method", "GET"), res["final_url"], int(res.get("status", 200)),
                    json.dumps(res.get("req_headers", {})), "",
                    json.dumps(res.get("resp_headers", {})), (res.get("text") or "")[:32000],
                )
            elif hasattr(self.app, "api_tab") and hasattr(self.app.api_tab, "controller"):
                ac = self.app.api_tab.controller
                if hasattr(ac, "db") and ac.db is not None:
                    ac.db.log_request(
                        res.get("method", "GET"), res["final_url"], int(res.get("status", 200)),
                        json.dumps(res.get("req_headers", {})), "",
                        json.dumps(res.get("resp_headers", {})), (res.get("text") or "")[:32000],
                    )
            self._log(f"In API Inspector / Debugger-Netz übergeben: {res['final_url']}")
            messagebox.showinfo("Übergeben", f"Request wurde an Netz-Log & API Inspector übergeben:\n{res['final_url']}", parent=self)
        except Exception as e:
            messagebox.showwarning("API Inspector", f"Übergabe fehlgeschlagen: {e}", parent=self)
