# -*- coding: utf-8 -*-
"""G13: GUI-Panels fuer Seams/IPC-Trigger & interaktiven UI-Tree-Inspector + Missing-Classifier."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, List, Optional, Tuple


def classify_missing_engine(cls_desc: str, sig: str = "") -> str:
    """Ordnet einen `missing`-Stub-Eintrag fuer Filterung einer Engine/Kategorie zu."""
    c = str(cls_desc or "")
    s = str(sig or "")
    if c.startswith(("Landroidx/compose/", "Lcom/compose/")):
        return "Compose"
    if any(k in c for k in ("Lokhttp3/", "Lio/grpc/", "Lorg/chromium/net/", "Lokio/", "Ljava/net/", "Lretrofit2/")):
        return "Net/gRPC"
    if any(k in c for k in ("Ljavax/crypto/", "Ljava/security/", "Landroidx/security/crypto/",
                            "Lcom/android/billingclient/", "Lcom/google/android/play/core/integrity/")):
        return "Crypto/Billing"
    if any(k in c for k in ("Landroid/bluetooth/", "Landroid/location/", "Lcom/google/android/gms/location/",
                            "Landroidx/biometric/", "Landroid/net/nsd/", "Landroid/media/", "Landroidx/media3/",
                            "Lcom/google/android/exoplayer2/")):
        return "Hardware"
    if any(k in c for k in ("Landroid/net/Uri", "Landroid/content/Intent", "Landroid/app/PendingIntent",
                            "Landroid/app/AlarmManager", "Landroidx/browser/", "LocalBroadcastManager")):
        return "IPC"
    if any(k in c for k in ("Lio/flutter/", "Lcom/facebook/react/", "Landroid/webkit/")):
        return "Flutter/RN/Web"
    if "Lio/reactivex/" in c or "Lrx/" in c or "Landroidx/navigation/" in c or "Landroidx/paging/" in c:
        return "Nav/Rx/Paging"
    if s and "Compose" in s:
        return "Compose"
    return "Java/Android"


class SeamsInspectorPanel(ttk.Frame):
    """Live-Steuerung fuer Play-Billing, GPS/Biometrie, DeepLink/Broadcast/PendingIntent & WebSocket."""

    def __init__(self, parent: Any, ctrl: Any):
        super().__init__(parent)
        self.ctrl = ctrl
        self._build_ui()

    def _get_stubs_runner(self) -> Tuple[Any, Any]:
        stubs = getattr(self.ctrl, "_stubs", None)
        runner = getattr(self.ctrl, "_runner", None)
        return stubs, runner

    def _refresh_screens(self) -> None:
        try:
            app = getattr(self.ctrl, "app", None)
            for disp in (getattr(app, "display_tab", None), getattr(getattr(app, "debugger_tab", None), "screen_view", None)):
                if disp is not None and hasattr(disp, "render"):
                    disp.render()
        except Exception:
            pass

    def _build_ui(self) -> None:
        pad = {"padx": 6, "pady": 3}

        # 1) Play Billing Pro Override
        bf = ttk.LabelFrame(self, text="Play Billing / Entitlement (G11)")
        bf.pack(fill="x", **pad)
        self.billing_var = tk.BooleanVar(value=False)
        self.billing_skus_var = tk.StringVar(value="pro_monthly,pro_yearly")
        ttk.Checkbutton(bf, text="Pro aktiv", variable=self.billing_var, command=self.apply_billing).pack(side="left", padx=4)
        ttk.Label(bf, text="SKUs:").pack(side="left")
        ttk.Entry(bf, textvariable=self.billing_skus_var, width=22).pack(side="left", padx=4)
        ttk.Button(bf, text="Anwenden", command=self.apply_billing).pack(side="left", padx=4)

        # 2) Hardware / Sensorik (GPS + Biometrie)
        hf = ttk.LabelFrame(self, text="Hardware & Biometrie (G9)")
        hf.pack(fill="x", **pad)
        self.lat_var = tk.StringVar(value="52.5200")
        self.lon_var = tk.StringVar(value="13.4050")
        ttk.Label(hf, text="Lat:").pack(side="left", padx=(4, 1))
        ttk.Entry(hf, textvariable=self.lat_var, width=9).pack(side="left", padx=2)
        ttk.Label(hf, text="Lon:").pack(side="left", padx=(4, 1))
        ttk.Entry(hf, textvariable=self.lon_var, width=9).pack(side="left", padx=2)
        ttk.Button(hf, text="📍 GPS senden", command=self.send_gps).pack(side="left", padx=4)
        ttk.Label(hf, text="Biometrie:").pack(side="left", padx=(8, 2))
        self.bio_var = tk.StringVar(value="SUCCESS")
        bio_cb = ttk.Combobox(hf, textvariable=self.bio_var, width=20, state="readonly",
                              values=("SUCCESS", "ERROR_NO_BIOMETRICS", "ERROR_USER_CANCELED", "FAILED"))
        bio_cb.pack(side="left", padx=2)
        bio_cb.bind("<<ComboboxSelected>>", lambda _e=None: self.apply_biometric())

        # 3) IPC / DeepLink / Broadcast / PendingIntent
        ipf = ttk.LabelFrame(self, text="IPC / OAuth / DeepLink (G10)")
        ipf.pack(fill="x", **pad)
        self.uri_var = tk.StringVar(value="app://oauth/callback?code=glass_demo")
        ttk.Entry(ipf, textvariable=self.uri_var, width=32).pack(side="left", padx=4)
        ttk.Button(ipf, text="🔗 DeepLink", command=self.fire_deeplink).pack(side="left", padx=2)
        self.bcast_var = tk.StringVar(value="android.intent.action.ACTION_POWER_CONNECTED")
        ttk.Entry(ipf, textvariable=self.bcast_var, width=26).pack(side="left", padx=(8, 2))
        ttk.Button(ipf, text="📡 Broadcast", command=self.fire_broadcast).pack(side="left", padx=2)
        ttk.Button(ipf, text="⏰ PendingIntent[0]", command=self.fire_pending).pack(side="left", padx=2)

        # 4) WebSocket Injektor
        wsf = ttk.LabelFrame(self, text="WebSocket Injektor (G7)")
        wsf.pack(fill="x", **pad)
        self.ws_payload_var = tk.StringVar(value='{"event":"ping","ok":true}')
        ttk.Entry(wsf, textvariable=self.ws_payload_var, width=42).pack(side="left", padx=4)
        ttk.Button(wsf, text="⚡ WS-Frame senden", command=self.send_ws_frame).pack(side="left", padx=4)

        self.status_lbl = ttk.Label(self, text="Bereit", anchor="w")
        self.status_lbl.pack(fill="x", padx=6, pady=(2, 4))

    def apply_billing(self) -> bool:
        stubs, _ = self._get_stubs_runner()
        skus = [s.strip() for s in self.billing_skus_var.get().split(",") if s.strip()]
        enabled = bool(self.billing_var.get())
        if stubs is None:
            self.status_lbl.config(text="Billing-Preset (warte auf App-Lauf)")
            return False
        from core.java.crypto_billing_g11 import set_billing_pro_override
        set_billing_pro_override(stubs, enabled=enabled, products=skus or ["pro_monthly"])
        self.status_lbl.config(text=f"Billing: {'PRO' if enabled else 'FREE'} ({', '.join(skus)})")
        return True

    def apply_biometric(self) -> None:
        stubs, _ = self._get_stubs_runner()
        mode = self.bio_var.get() or "SUCCESS"
        if stubs is not None:
            stubs._biometric_mode = mode
            from core.java.hardware_media_g9 import _hw
            _hw(stubs)["biometric_result"] = "success" if mode == "SUCCESS" else ("failed" if mode == "FAILED" else "error")
        self.status_lbl.config(text=f"Biometrie-Modus: {mode}")

    def send_gps(self) -> int:
        stubs, _ = self._get_stubs_runner()
        if stubs is None:
            self.status_lbl.config(text="GPS: kein aktiver App-Lauf")
            return 0
        try:
            lat = float(self.lat_var.get())
            lon = float(self.lon_var.get())
        except ValueError:
            self.status_lbl.config(text="GPS: ungültige Koordinaten")
            return 0
        from core.java.hardware_media_g9 import push_location_update
        n = push_location_update(stubs, lat, lon)
        self._refresh_screens()
        self.status_lbl.config(text=f"GPS ({lat:.4f}, {lon:.4f}) -> {n} Listener")
        return n

    def fire_deeplink(self) -> bool:
        stubs, runner = self._get_stubs_runner()
        uri = self.uri_var.get().strip()
        if stubs is None or not uri:
            self.status_lbl.config(text="DeepLink: kein aktiver App-Lauf/URI")
            return False
        from core.java.ipc_oauth_g10 import deliver_deeplink
        ok = deliver_deeplink(stubs, runner, uri)
        self._refresh_screens()
        self.status_lbl.config(text=f"DeepLink '{uri}' -> {'OK' if ok else 'kein Match'}")
        return ok

    def fire_broadcast(self) -> int:
        stubs, _ = self._get_stubs_runner()
        act = self.bcast_var.get().strip()
        if stubs is None or not act:
            self.status_lbl.config(text="Broadcast: kein aktiver App-Lauf/Action")
            return 0
        from core.java.ipc_oauth_g10 import trigger_broadcast
        n = trigger_broadcast(stubs, getattr(stubs, "vm", None), act)
        self._refresh_screens()
        self.status_lbl.config(text=f"Broadcast '{act}' -> {n} Receiver")
        return n

    def fire_pending(self) -> bool:
        stubs, runner = self._get_stubs_runner()
        if stubs is None:
            return False
        from core.java.ipc_oauth_g10 import fire_pending_intent
        ok = fire_pending_intent(stubs, getattr(stubs, "vm", None), runner, 0)
        self._refresh_screens()
        self.status_lbl.config(text=f"PendingIntent[0] -> {'ausgeführt' if ok else 'keiner registriert'}")
        return ok

    def send_ws_frame(self) -> bool:
        stubs, _ = self._get_stubs_runner()
        payload = self.ws_payload_var.get()
        if stubs is None:
            return False
        from core.java.net_protocols_g7 import inject_websocket_message
        ok = inject_websocket_message(stubs, None, payload)
        self._refresh_screens()
        self.status_lbl.config(text=f"WS-Frame -> {'injiziert' if ok else 'kein WebSocket offen'}")
        return ok


class UiTreeInspectorPanel(ttk.Frame):
    """Hierarchischer Live-UI-Baum-Inspektor mit Suche & Canvas-Highlight."""

    def __init__(self, parent: Any, ctrl: Any):
        super().__init__(parent)
        self.ctrl = ctrl
        self._node_map: dict[str, Any] = {}
        self._build_ui()

    def _current_tree(self) -> Any:
        t = getattr(self.ctrl, "glass_tree", None)
        if t is not None:
            return t
        app = getattr(self.ctrl, "app", None)
        return getattr(getattr(app, "display_tab", None), "tree", None)

    def _build_ui(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x", padx=4, pady=3)
        self.filter_var = tk.StringVar(value="")
        ent = ttk.Entry(top, textvariable=self.filter_var, width=24)
        ent.pack(side="left", fill="x", expand=True, padx=(0, 4))
        ent.bind("<KeyRelease>", lambda _e=None: self.refresh())
        ttk.Button(top, text="🔄 Refresh", command=self.refresh).pack(side="left")

        self.tree = ttk.Treeview(self, columns=("bounds", "text", "flags"), show="tree headings", height=10)
        self.tree.heading("#0", text="View / ID")
        self.tree.heading("bounds", text="Bounds")
        self.tree.heading("text", text="Text")
        self.tree.heading("flags", text="Flags")
        self.tree.column("#0", width=170)
        self.tree.column("bounds", width=105)
        self.tree.column("text", width=140)
        self.tree.column("flags", width=75)
        self.tree.pack(fill="both", expand=True, padx=4)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        self.detail_lbl = ttk.Label(self, text="Kein Knoten ausgewählt", anchor="w")
        self.detail_lbl.pack(fill="x", padx=4, pady=3)

    def refresh(self, view_tree: Any = None) -> int:
        vt = view_tree or self._current_tree()
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._node_map.clear()
        if vt is None or getattr(vt, "root", None) is None:
            return 0
        flt = self.filter_var.get().strip().lower()
        count = 0

        def _matches(nd: Any) -> bool:
            if not flt:
                return True
            hay = f"{getattr(nd, 'cls', '')} {getattr(nd, 'vid', '') or ''} {getattr(nd, 'text', '') or ''}".lower()
            if flt in hay:
                return True
            return any(_matches(ch) for ch in (getattr(nd, "children", None) or ()))

        def _insert(parent_iid: str, nd: Any) -> None:
            nonlocal count
            if not _matches(nd):
                return
            lbl = getattr(nd, "cls", "View") or "View"
            vid = getattr(nd, "vid", None)
            if vid:
                lbl += f" #{vid}"
            bx = getattr(nd, "bounds", (0, 0, 0, 0))
            btxt = f"{bx[0]},{bx[1]} {bx[2]}x{bx[3]}"
            flags: List[str] = []
            if getattr(nd, "clickable", False):
                flags.append("clk")
            if getattr(nd, "focusable", False):
                flags.append("foc")
            if not getattr(nd, "visible", True):
                flags.append("hid")
            if (getattr(nd, "props", None) or {}).get("is_password"):
                flags.append("pw")
            txt = str(getattr(nd, "text", "") or (getattr(nd, "props", None) or {}).get("hint", "") or "")[:40]
            iid = self.tree.insert(parent_iid, "end", text=lbl, values=(btxt, txt, " ".join(flags)), open=bool(flt))
            self._node_map[iid] = nd
            count += 1
            for ch in (getattr(nd, "children", None) or ()):
                _insert(iid, ch)

        _insert("", vt.root)
        return count

    def _on_select(self, _e=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        nd = self._node_map.get(sel[0])
        if nd is None:
            return
        vt = self._current_tree()
        if vt is not None:
            vt._highlight_node = nd
        try:
            app = getattr(self.ctrl, "app", None)
            for disp in (getattr(app, "display_tab", None), getattr(getattr(app, "debugger_tab", None), "screen_view", None)):
                if disp is not None and hasattr(disp, "render"):
                    disp.render()
        except Exception:
            pass
        props = getattr(nd, "props", None) or {}
        self.detail_lbl.config(
            text=f"{nd.cls} vid={nd.vid or '-'} bounds={nd.bounds} clk={nd.clickable} pw={bool(props.get('is_password'))}"
        )
