# -*- coding: utf-8 -*-
"""core.java.flutter_app_producer — Interaktive In-Phone-UI für Flutter-Apps & In-Phone-Browser.

Erzeugt für Flutter-APKs (z. B. Kippy) direkt innerhalb des 380x800 Handy-Simulators einen
interaktiven ViewTree auf Basis der echten App-Assets (Übersetzungen, Marken-PNGs, .env/JSON-Configs,
Cognito-/GraphQL-/REST-Vorlagen) sowie einen integrierten In-Phone-Browser für WebViews & URLs.
Kein Auslagern in externe Fenster; alle Netzwerkaktionen laufen über `stubs.net_fire` / `NetLog`.
"""
from __future__ import annotations

import glob
import json
import os
import re
import threading
import urllib.parse
from typing import Any, Callable, Dict, List, Optional

from core.java.view_tree import ViewNode, ViewTree
from services.flutter_api_extractor_service import FlutterApiExtractorService
from services.webview_inspector_service import (
    DEFAULT_MOBILE_UA,
    WebViewInspectorService,
    _DomInspectorParser,
    discover_app_urls,
)


def _read_json_safe(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _parse_env_files(fa_dir: str) -> Dict[str, str]:
    kv: Dict[str, str] = {}
    if not fa_dir or not os.path.isdir(fa_dir):
        return kv
    for dp, _dn, fns in os.walk(fa_dir):
        for fn in sorted(fns):
            low = fn.lower()
            if low.startswith(".env") or low.endswith(".env"):
                fp = os.path.join(dp, fn)
                try:
                    with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                        for ln in fh:
                            st = ln.strip()
                            if not st or st.startswith("#") or "=" not in st:
                                continue
                            k, v = st.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip('"').strip("'")
                            if k:
                                kv[k] = v
                except Exception:
                    pass
    return kv


def _pick_translation_file(fa_dir: str, brand_hint: str = "") -> Optional[str]:
    if not fa_dir or not os.path.isdir(fa_dir):
        return None
    cands: List[str] = []
    for dp, _dn, fns in os.walk(fa_dir):
        if "translation" in dp.lower() or "i18n" in dp.lower() or "locale" in dp.lower():
            for fn in sorted(fns):
                if fn.lower().endswith(".json"):
                    cands.append(os.path.join(dp, fn))
    if not cands:
        return None
    b = brand_hint.strip().lower()

    def _score(p: str) -> tuple:
        bn = os.path.basename(p).lower()
        is_de = bn.startswith("de") or "-de" in bn or "_de" in bn
        is_en = bn.startswith("en") or "-en" in bn or "_en" in bn
        has_brand = bool(b and b in bn)
        return (
            0 if is_de else (1 if is_en else 2),
            0 if has_brand else 1,
            bn,
        )

    cands.sort(key=_score)
    return cands[0]


def _pick_brand_images(fa_dir: str, brand_hint: str = "") -> Dict[str, str]:
    if not fa_dir or not os.path.isdir(fa_dir):
        return {}
    all_pngs: List[str] = []
    for dp, _dn, fns in os.walk(fa_dir):
        for fn in sorted(fns):
            if fn.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                all_pngs.append(os.path.join(dp, fn))
    if not all_pngs:
        return {}
    b = brand_hint.strip().lower()

    def _pref(paths: List[str]) -> str:
        if not paths:
            return ""
        paths_sorted = sorted(
            paths,
            key=lambda p: (0 if (b and f"/{b}/" in p.replace("\\", "/").lower()) else 1, len(p)),
        )
        return paths_sorted[0]

    def _match(patterns: tuple[str, ...]) -> str:
        for pat in patterns:
            hits = [p for p in all_pngs if pat in os.path.basename(p).lower()]
            if hits:
                return _pref(hits)
        return ""

    return {
        "logo": _match(("logo_1153", "logo_1152", "logo", "app_icon")),
        "hero": _match(("auth_screen", "splash", "cat_intro", "dog_intro")),
        "pet": _match(("dog_intro", "cat_intro", "device.png", "dog_banner", "cat.png")),
        "position": _match(("blur_position_page", "position", "map")),
        "activity": _match(("blur_activity_page", "activity")),
        "health": _match(("blur_gauge", "battery_full", "battery")),
    }


def extract_flutter_app_spec(app_root: str) -> Dict[str, Any]:
    """Liest UI-Texte, Marken-PNGs, Web-URLs und API-Endpunkte rein statisch aus einer Flutter-APK."""
    pkg = ""
    app_name = ""
    try:
        from core.java.manifest import parse_manifest
        man = parse_manifest(app_root)
        pkg = getattr(man, "package", "") or ""
    except Exception:
        pass

    fa_dir = os.path.join(app_root or "", "assets", "flutter_assets")
    env_kv = _parse_env_files(fa_dir)

    cfg_brand = ""
    cfg_pool = ""
    cfg_client = ""
    if os.path.isdir(fa_dir):
        cfg_files = sorted(glob.glob(os.path.join(fa_dir, "**", "*.json"), recursive=True))
        for cf in cfg_files:
            rel = os.path.relpath(cf, fa_dir).replace("\\", "/").lower()
            if "translation" in rel:
                continue
            data = _read_json_safe(cf)
            if not data:
                continue
            if "config" in rel or "android" in rel:
                if data.get("appBrand") and not cfg_brand:
                    cfg_brand = str(data["appBrand"])
                if data.get("appName") and not app_name:
                    app_name = str(data["appName"])
                if data.get("UserPoolID") and not cfg_pool:
                    cfg_pool = str(data["UserPoolID"])
                if data.get("ClientID") and not cfg_client:
                    cfg_client = str(data["ClientID"])

    brand_hint = cfg_brand or app_name or (pkg.split(".")[-1] if pkg else "Flutter")
    if not app_name:
        app_name = cfg_brand.title() if cfg_brand else (pkg.split(".")[-1].title() if pkg else "Flutter App")

    tr_path = _pick_translation_file(fa_dir, brand_hint=brand_hint)
    tr = _read_json_safe(tr_path) if tr_path else {}
    tr = tr or {}

    login_tr = tr.get("login") if isinstance(tr.get("login"), dict) else {}
    demo_tr = tr.get("demo") if isinstance(tr.get("demo"), dict) else {}
    bar_tr = tr.get("bottom_bar") if isinstance(tr.get("bottom_bar"), dict) else {}
    home_tr = tr.get("home") if isinstance(tr.get("home"), dict) else {}
    menu_tr = tr.get("menu_page") if isinstance(tr.get("menu_page"), dict) else {}
    urls_tr = tr.get("urls") if isinstance(tr.get("urls"), dict) else {}

    webapp_base = ""
    for uv in urls_tr.values():
        s_uv = str(uv or "")
        if s_uv.startswith(("http://", "https://")) and "webapp." in s_uv.lower():
            pr = urllib.parse.urlsplit(s_uv)
            if pr.scheme and pr.netloc:
                webapp_base = f"{pr.scheme}://{pr.netloc}"
                break

    resolved_urls: List[Dict[str, str]] = []
    seen_u = set()
    pref_url_keys = (
        ("shop", "Shop"),
        ("support", "Support WebApp"),
        ("kippy_support", "Hilfe-Center"),
        ("tutorial_support", "Tutorials"),
        ("live_tracking", "Live-Tracking Guide"),
        ("geo", "Geofence Guide"),
        ("activity_history", "Aktivitäts-Guide"),
    )
    for key, nice_lbl in pref_url_keys:
        raw_u = str(urls_tr.get(key) or "").strip()
        if not raw_u:
            continue
        if "{web_app_path}" in raw_u and webapp_base:
            raw_u = raw_u.replace("{web_app_path}", webapp_base)
        if raw_u.startswith(("http://", "https://")) and raw_u not in seen_u:
            seen_u.add(raw_u)
            resolved_urls.append({"key": key, "label": nice_lbl, "url": raw_u})

    discovered = discover_app_urls(app_root)
    for it in discovered:
        u = str(it.get("url") or "").strip()
        if u and u not in seen_u and not u.lower().endswith(".pdf"):
            if u.startswith(("http://", "https://")) or os.path.isfile(u):
                if "appsync" in u.lower() or "cognito" in u.lower():
                    continue
                seen_u.add(u)
                resolved_urls.append({
                    "key": it.get("kind", "url"),
                    "label": it.get("label") or urllib.parse.urlsplit(u).netloc or "Web",
                    "url": u,
                })

    api_scan = FlutterApiExtractorService.extract_from_app_root(app_root)
    templates = list(api_scan.get("templates") or ())
    cog_tpl = next((t for t in templates if t.get("category") == "Cognito" and "InitiateAuth" in str(t.get("description", ""))), None)
    gql_tpl = next((t for t in templates if t.get("category") == "GraphQL"), None)
    rest_tpl = next((t for t in templates if t.get("category") == "REST"), None)

    cognito_cfg = None
    if cog_tpl or cfg_pool:
        pool_id = cfg_pool or ""
        region = "eu-west-1"
        m_reg = re.search(r"\b((?:eu|us|ap|sa|ca|me|af|il)-[a-z]+-\d+)\b", pool_id or (cog_tpl or {}).get("url", ""))
        if m_reg:
            region = m_reg.group(1)
        cid = cfg_client
        if not cid and cog_tpl:
            try:
                cid = json.loads(cog_tpl.get("body") or "{}").get("ClientId", "")
            except Exception:
                cid = ""
        cognito_cfg = {
            "url": (cog_tpl or {}).get("url") or f"https://cognito-idp.{region}.amazonaws.com/",
            "pool_id": pool_id,
            "client_id": cid or "<COGNITO_CLIENT_ID>",
            "region": region,
        }

    graphql_url = env_kv.get("BASE_URL") or (gql_tpl or {}).get("url") or ""
    if not graphql_url and env_kv.get("HOST"):
        h = env_kv["HOST"].strip()
        graphql_url = f"https://{h}/graphql" if not h.startswith("http") else h
    api_key = env_kv.get("API_KEY") or ""
    if not api_key and gql_tpl:
        api_key = (gql_tpl.get("headers") or {}).get("x-api-key", "")

    demo_email = ""
    for t in templates:
        if "petlink.demo@" in str(t):
            demo_email = "petlink.demo@datamars.com"
            break
    aot_meta = _scan_libapp_aot_metadata(app_root)
    if not demo_email and aot_meta.get("demo_email"):
        demo_email = aot_meta["demo_email"]

    images = _pick_brand_images(fa_dir, brand_hint=brand_hint)
    return {
        "package": pkg,
        "app_name": app_name,
        "brand": brand_hint,
        "env": env_kv,
        "translations": {
            "login": login_tr,
            "demo": demo_tr,
            "bottom_bar": bar_tr,
            "home": home_tr,
            "menu": menu_tr,
        },
        "images": images,
        "quick_urls": resolved_urls,
        "all_discovered_urls": discovered,
        "cognito": cognito_cfg,
        "graphql_url": graphql_url,
        "graphql_template": gql_tpl,
        "rest_template": rest_tpl,
        "api_key": api_key,
        "templates": templates,
        "demo_email": demo_email,
        "aot_routes": aot_meta.get("routes", []),
        "aot_channels": aot_meta.get("channels", []),
        "aot_endpoints": aot_meta.get("endpoints", []),
        "dart_packages": aot_meta.get("dart_packages", []),
        "dart_classes": aot_meta.get("dart_classes", []),
        "graphql_queries": aot_meta.get("graphql_queries", []),
        "kdart_symbols": aot_meta.get("kdart_symbols", []),
    }


_AOT_SKIP_PREFIXES = (
    "/data/", "/system/", "/proc/", "/dev/", "/apex/", "/vendor/",
    "/lib/", "/usr/", "/etc/", "/storage/", "/mnt/", "/sdcard/", "/sys/",
)


def _scan_libapp_aot_metadata(app_root: Optional[str], max_bytes: int = 12 * 1024 * 1024) -> Dict[str, Any]:
    """Begrenzter AOT-Scanner fuer `libapp.so`: extrahiert Routen, Platform-Channels und HTTPS-Endpunkte."""
    routes: List[str] = []
    channels: List[str] = []
    endpoints: List[str] = []
    dart_packages: List[str] = []
    dart_classes: List[str] = []
    graphql_queries: List[str] = []
    kdart_symbols: List[str] = []
    sections: List[str] = []
    demo_email = ""
    if not app_root or not os.path.isdir(os.path.join(app_root, "lib")):
        return {
            "routes": routes, "channels": channels, "endpoints": endpoints, "demo_email": demo_email,
            "dart_packages": dart_packages, "dart_classes": dart_classes,
            "graphql_queries": graphql_queries, "kdart_symbols": kdart_symbols, "sections": sections,
        }
    seen_r, seen_c, seen_e = set(), set(), set()
    for lp in sorted(glob.glob(os.path.join(app_root, "lib", "*", "libapp.so")))[:2]:
        try:
            with open(lp, "rb") as fh:
                blob = fh.read(max_bytes)
        except Exception:
            continue
        try:
            from core.java.engine_deep_g12 import analyze_flutter_libapp_deep
            deep = analyze_flutter_libapp_deep(blob, max_bytes=max_bytes)
            for k_list, tgt in (
                ("dart_packages", dart_packages),
                ("dart_classes", dart_classes),
                ("graphql_queries", graphql_queries),
                ("kdart_symbols", kdart_symbols),
                ("sections", sections),
            ):
                for item in deep.get(k_list) or ():
                    if item not in tgt:
                        tgt.append(item)
        except Exception:
            pass
        m_em = re.search(rb"[A-Za-z0-9._%+-]*demo[A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", blob, re.I)
        if m_em and not demo_email:
            demo_email = m_em.group(0).decode("ascii", "replace")
        for m in re.finditer(rb"https://[a-zA-Z0-9.-]+(?:/[a-zA-Z0-9_./?=&%-]*)?", blob):
            u = m.group(0).decode("ascii", "ignore")
            if len(u) >= 12 and u not in seen_e:
                seen_e.add(u)
                endpoints.append(u)
                if len(endpoints) >= 32:
                    break
        for m in re.finditer(rb"(?:plugins\.flutter\.io|dev\.fluttercommunity\.plus|flutter|com\.[a-zA-Z0-9_.]+|io\.[a-zA-Z0-9_.]+)/[a-zA-Z0-9_.-]{2,48}", blob):
            ch = m.group(0).decode("ascii", "ignore")
            if ch not in seen_c:
                seen_c.add(ch)
                channels.append(ch)
                if len(channels) >= 24:
                    break
        for m in re.finditer(rb"(?<![A-Za-z0-9_.:])(/[a-zA-Z][a-zA-Z0-9_/-]{1,40})(?![A-Za-z0-9_.:])", blob):
            rt = m.group(1).decode("ascii", "ignore")
            low = rt.lower()
            if low.startswith(_AOT_SKIP_PREFIXES) or any(low.endswith(ext) for ext in (".so", ".png", ".jpg", ".json", ".xml", ".ttf")):
                continue
            if "//" in rt or rt.count("/") > 4:
                continue
            if rt not in seen_r:
                seen_r.add(rt)
                routes.append(rt)
                if len(routes) >= 32:
                    break
    return {
        "routes": routes, "channels": channels, "endpoints": endpoints, "demo_email": demo_email,
        "dart_packages": dart_packages, "dart_classes": dart_classes,
        "graphql_queries": graphql_queries, "kdart_symbols": kdart_symbols, "sections": sections,
    }


def _short_label(text: str, limit: int = 34) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s if len(s) <= limit else (s[: limit - 1] + "…")


def _image_node(vid: str, path: str, h: int, scale: str = "fit") -> Optional[ViewNode]:
    if not path or not os.path.isfile(path):
        return None
    props: Dict[str, Any] = {
        "drawable": {"kind": "raster", "path": path, "name": os.path.basename(path)},
        "h": int(h),
    }
    if scale == "crop":
        props["scale_type"] = "CENTER_CROP"
    return ViewNode("ImageView", vid=vid, props=props)


class InPhoneBrowserController:
    """Mobiler In-Phone-Browser, der direkt in einem ViewTree (380x800 Handy-Display) rendert."""

    def __init__(
        self,
        tree: Optional[ViewTree] = None,
        *,
        app_root: str = "",
        stubs: Any = None,
        net_fire_fn: Optional[Callable[..., Any]] = None,
        quick_urls: Optional[List[Dict[str, str]]] = None,
        on_exit: Optional[Callable[[], None]] = None,
        on_async_update: Optional[Callable[[], None]] = None,
    ):
        self.tree = tree if tree is not None else ViewTree(width=380, height=776)
        self.app_root = app_root or ""
        self.stubs = stubs
        self._net_fire_override = net_fire_fn
        self.on_exit = on_exit
        self.on_async_update = on_async_update
        self.inspector = WebViewInspectorService(base_dir=".")
        self.quick_urls: List[Dict[str, str]] = list(quick_urls or ())
        self.history: List[str] = []
        self.current_url: str = ""
        self.url_draft: str = ""
        self.render_mode: str = "auto"   # "auto" (Pixel PNG + DOM) | "dom" (reiner DOM)
        self.last_result: Optional[Dict[str, Any]] = None
        self.last_png: Optional[bytes] = None
        self.loading: bool = False
        self.status_msg: str = ""
        self._form_values: Dict[int, Dict[str, str]] = {}
        self._lock = threading.Lock()
        self._seq = 0
        self.tree._in_phone_browser = self

    def _get_net_fire(self) -> Optional[Callable[..., Any]]:
        if callable(self._net_fire_override):
            return self._net_fire_override
        st = self.stubs
        if st is not None and callable(getattr(st, "net_fire", None)):
            return st.net_fire
        return None

    def _record_web_event(self, kind: str, url: str) -> None:
        st = self.stubs
        rec_fn = getattr(st, "_record_web_event", None) if st is not None else None
        if callable(rec_fn):
            try:
                rec_fn(kind, url)
                return
            except Exception:
                pass
        if self.tree is not None and hasattr(self.tree, "web_events"):
            self.tree.web_events.append({"kind": kind, "url": str(url or "")})

    def _ensure_quick_urls(self) -> List[Dict[str, str]]:
        if self.quick_urls:
            return self.quick_urls
        found = discover_app_urls(self.app_root, web_events=getattr(self.tree, "web_events", None))
        out: List[Dict[str, str]] = []
        for it in found:
            u = str(it.get("url") or "").strip()
            if not u or u.lower().endswith(".pdf"):
                continue
            if "appsync" in u.lower() or "cognito" in u.lower():
                continue
            lbl = it.get("label") or ""
            if not lbl or lbl == it.get("source"):
                pr = urllib.parse.urlsplit(u)
                lbl = os.path.basename(u) if os.path.isfile(u) else (pr.netloc or it.get("source") or "Web")
            out.append({"label": _short_label(lbl, 16), "url": u})
            if len(out) >= 6:
                break
        self.quick_urls = out
        return self.quick_urls

    def _inspect_from_bytes(self, url: str, method: str, status: int, reason: str,
                            req_hdrs: dict, resp_hdrs: dict, raw_bytes: bytes) -> Dict[str, Any]:
        text = (raw_bytes or b"").decode("utf-8", "replace")
        return self.inspector._build_inspection_result(
            url=url,
            final_url=url,
            method=method.upper(),
            status=int(status),
            reason=str(reason or "OK"),
            req_headers=dict(req_hdrs or {}),
            resp_headers=dict(resp_hdrs or {}),
            raw_bytes=bytes(raw_bytes or b""),
            text=text,
            elapsed_ms=1,
        )

    def _record_real_fetch_into_net_log(self, url: str, method: str, req_hdrs: dict, body_bytes: bytes,
                                        res: Dict[str, Any], replace_mock_default: bool = False) -> None:
        net_log = getattr(self.stubs, "net_log", None) if self.stubs is not None else None
        if net_log is None:
            return
        try:
            from core.java.net_recorder import NetRequest, NetResponse
            req = NetRequest(method.upper(), url, req_hdrs or {}, body_bytes or b"")
            resp = NetResponse(
                int(res.get("status", 200) or 200),
                dict(res.get("resp_headers") or {}),
                bytes(res.get("raw_bytes") or (res.get("text") or "").encode("utf-8")),
                str(res.get("reason") or "OK"),
            )
            entries = getattr(net_log, "entries", None)
            if replace_mock_default and isinstance(entries, list) and entries:
                last = entries[-1]
                last_src = str(getattr(last.get("resp"), "headers", {}).get("X-Glass-Source", ""))
                if "mock-default" in last_src and getattr(last.get("req"), "url", "") == url:
                    last["resp"] = resp
                    return
            net_log.record(req, resp)
            try:
                from core.application.event_bus import EventBus
                EventBus.publish("NET_REQUEST", {"req": req.to_dict(), "resp": resp.to_dict()})
            except Exception:
                pass
        except Exception:
            pass

    def load_url_sync(
        self,
        url: str,
        *,
        method: str = "GET",
        form_data: Optional[Dict[str, str]] = None,
        allow_pixel_preview: bool = False,
        push_history: bool = True,
        container_node: Optional[ViewNode] = None,
    ) -> Dict[str, Any]:
        """Lädt eine lokale HTML-Asset-Datei oder HTTP(S)-URL synchron in den In-Phone-ViewTree."""
        target = str(url or "").strip()
        if not target:
            self.status_msg = "Bitte URL eingeben oder App-Link wählen."
            self.mount(container_node=container_node)
            return {"ok": False, "error": self.status_msg}

        if not target.startswith(("http://", "https://", "file:///")) and not os.path.isfile(target):
            target = "https://" + target

        req_method = method.upper()
        req_hdrs = {
            "User-Agent": DEFAULT_MOBILE_UA,
            "Accept": "text/html,application/xhtml+xml,application/json,*/*",
        }
        body_bytes = b""
        if form_data:
            encoded = urllib.parse.urlencode(form_data)
            if req_method == "GET" and target.startswith(("http://", "https://")):
                sep = "&" if "?" in target else "?"
                target = f"{target}{sep}{encoded}"
            else:
                body_bytes = encoded.encode("utf-8")
                req_hdrs["Content-Type"] = "application/x-www-form-urlencoded"

        if push_history and (not self.history or self.history[-1] != target):
            self.history.append(target)
        self.current_url = target
        self.url_draft = target
        self.last_png = None
        self._form_values.clear()
        self._record_web_event("in_phone_browser", target)

        res: Dict[str, Any]
        net_fire = self._get_net_fire()
        if target.startswith(("http://", "https://")) and callable(net_fire):
            resp = net_fire(req_method, target, req_hdrs, body_bytes)
            src = str(getattr(resp, "headers", {}).get("X-Glass-Source", "") or "")
            rbody = bytes(getattr(resp, "body", b"") or b"")
            if rbody and ("mock-rule" in src or "live-http" in src or "db-replay" in src):
                res = self._inspect_from_bytes(
                    target, req_method, getattr(resp, "code", 200),
                    getattr(resp, "message", "OK"), req_hdrs, getattr(resp, "headers", {}), rbody,
                )
            elif "live-http-error" in src:
                res = {
                    "ok": False,
                    "url": target,
                    "final_url": target,
                    "method": req_method,
                    "status": getattr(resp, "code", 502),
                    "error": rbody.decode("utf-8", "replace") or getattr(resp, "message", "Netzwerkfehler"),
                    "text_blocks": [],
                    "links": [],
                    "forms": [],
                }
            else:
                # Im Offline-Mock-Modus ohne spezifische Mock-Regel direkt abrufen & im Netz-Log nachziehen
                res = self.inspector.fetch_and_inspect(
                    target, method=req_method, headers=req_hdrs, body=(body_bytes or None),
                )
                if res.get("ok"):
                    self._record_real_fetch_into_net_log(
                        target, req_method, req_hdrs, body_bytes, res,
                        replace_mock_default=("mock-default" in src),
                    )
        else:
            res = self.inspector.fetch_and_inspect(
                target, method=req_method, headers=req_hdrs, body=(body_bytes or None),
            )
            if target.startswith(("http://", "https://")) and res.get("ok"):
                self._record_real_fetch_into_net_log(target, req_method, req_hdrs, body_bytes, res)

        self.last_result = res
        self.loading = False
        if res.get("ok"):
            self.status_msg = f"HTTP {res.get('status', 200)} · {res.get('size', 0)} B"
        else:
            self.status_msg = f"Fehler: {res.get('error') or res.get('reason') or 'nicht erreichbar'}"

        if allow_pixel_preview and self.render_mode != "dom" and target.startswith(("http://", "https://")) and res.get("ok"):
            png = self.inspector.render_headless_mobile_png(target, width=364, height=500, timeout=8.0)
            if png:
                self.last_png = png

        self.mount(container_node=container_node)
        return res

    def load_url(
        self,
        url: str,
        *,
        method: str = "GET",
        form_data: Optional[Dict[str, str]] = None,
        push_history: bool = True,
        container_node: Optional[ViewNode] = None,
    ) -> None:
        """Lädt lokal synchron oder bei Live-URLs asynchron im Hintergrund mit Live-UI-Update."""
        target = str(url or "").strip()
        is_remote = bool(target and not os.path.isfile(target) and not target.startswith("file:///"))
        if is_remote and callable(self.on_async_update):
            with self._lock:
                self._seq += 1
                ticket = self._seq
            self.current_url = target if "://" in target else ("https://" + target)
            self.url_draft = self.current_url
            self.loading = True
            self.status_msg = f"Lade {self.current_url} ..."
            self.mount(container_node=container_node)

            def _worker():
                self.load_url_sync(
                    self.current_url,
                    method=method,
                    form_data=form_data,
                    allow_pixel_preview=True,
                    push_history=push_history,
                    container_node=container_node,
                )
                with self._lock:
                    if ticket != self._seq:
                        return
                try:
                    self.on_async_update()
                except Exception:
                    pass

            threading.Thread(target=_worker, daemon=True).start()
            return
        self.load_url_sync(
            target,
            method=method,
            form_data=form_data,
            allow_pixel_preview=False,
            push_history=push_history,
            container_node=container_node,
        )

    def go_back(self) -> bool:
        if len(self.history) > 1:
            self.history.pop()
            prev = self.history[-1]
            self.load_url(prev, push_history=False)
            return True
        if callable(self.on_exit):
            self.on_exit()
            return True
        return False

    def build_browser_nodes(self) -> ViewNode:
        """Erzeugt den kompletten mobilen In-Phone-Browser als ViewNode-Subtree."""
        self._ensure_quick_urls()
        root = ViewNode("LinearLayout", vid="in_phone_browser_root", props={"role": "scaffold"})

        # 1) Topbar: Zurück | Titel | Reload
        topbar = ViewNode("LinearLayout", vid="browser_topbar", props={"orientation": "horizontal", "role": "topbar"})
        back_btn = ViewNode("Button", vid="browser_back_btn", text="←", clickable=True, props={"w": 42})
        back_btn.on_click = lambda _v=None: self.go_back()
        host_lbl = urllib.parse.urlsplit(self.current_url).netloc if self.current_url.startswith(("http://", "https://")) else ""
        top_title = ViewNode(
            "TextView",
            vid="browser_title",
            text=f"🌐 {host_lbl or 'In-Phone Browser'}",
            props={"layout_weight": 1.0, "gravity": 16},
        )
        rel_btn = ViewNode("Button", vid="browser_reload_btn", text="↻", clickable=True, props={"w": 42})

        def _draft_url() -> str:
            if getattr(self.tree, "root", None) is not None:
                nd = self.tree.root.find("browser_url_input")
                if nd is not None and isinstance(getattr(nd, "text", None), str):
                    self.url_draft = nd.text
            return self.url_draft or self.current_url

        rel_btn.on_click = lambda _v=None: self.load_url(_draft_url(), push_history=False)
        topbar.add(back_btn)
        topbar.add(top_title)
        topbar.add(rel_btn)
        root.add(topbar)

        # 2) Adresszeile: EditText + Los + Darstellungs-Umschalter
        addr_row = ViewNode("LinearLayout", vid="browser_addr_row", props={"orientation": "horizontal"})
        url_inp = ViewNode(
            "EditText",
            vid="browser_url_input",
            text=self.url_draft or self.current_url,
            clickable=True,
            focusable=True,
            props={
                "hint": "URL eingeben (z. B. https://...)",
                "layout_weight": 1.0,
                "$on_change_fn": lambda txt: setattr(self, "url_draft", txt),
                "$on_submit_fn": lambda _t=None: self.load_url(_draft_url()),
            },
        )
        go_btn = ViewNode("Button", vid="browser_go_btn", text="Los", clickable=True, props={"w": 46})
        go_btn.on_click = lambda _v=None: self.load_url(_draft_url())
        mode_lbl = "📄" if (self.last_png and self.render_mode != "dom") else "📸"
        mode_btn = ViewNode("Button", vid="browser_mode_btn", text=mode_lbl, clickable=True, props={"w": 40})

        def _toggle_mode(_v=None):
            self.render_mode = "dom" if self.render_mode != "dom" else "auto"
            self.mount()

        mode_btn.on_click = _toggle_mode
        addr_row.add(url_inp)
        addr_row.add(go_btn)
        addr_row.add(mode_btn)
        root.add(addr_row)

        # 3) Schnellwahl-Leiste für App-URLs
        if self.quick_urls:
            q_card = ViewNode("LinearLayout", vid="browser_quick_card", props={"role": "card"})
            q_hdr = ViewNode("TextView", vid="browser_quick_hdr", text="Entdeckte App-URLs (im Handy öffnen):")
            q_hdr.props["h"] = 20
            q_card.add(q_hdr)
            row = None
            for idx, entry in enumerate(self.quick_urls[:4]):
                if idx % 2 == 0:
                    row = ViewNode("LinearLayout", props={"orientation": "horizontal"})
                    q_card.add(row)
                u_tgt = entry["url"]
                btn = ViewNode(
                    "Button",
                    vid=f"browser_quick_{idx}",
                    text=_short_label(entry.get("label") or "Link", 18),
                    clickable=True,
                    props={
                        "bg_drawable": {"kind": "shape", "fill": "#eef3ff", "outline": "#9bb5f5", "radius": 6},
                        "h": 34,
                    },
                )
                btn.on_click = lambda _v=None, _u=u_tgt: self.load_url(_u)
                row.add(btn)
            root.add(q_card)

        # 4) Ladehinweis / Fehler / gerenderte Seite
        if self.loading:
            lcard = ViewNode("LinearLayout", vid="browser_loading_card", props={"role": "card"})
            lcard.add(ViewNode("TextView", vid="browser_loading_txt", text=f"⏳ {self.status_msg}"))
            root.add(lcard)
            return root

        if self.last_png and self.render_mode != "dom":
            vp_card = ViewNode("LinearLayout", vid="browser_viewport_card", props={"role": "card"})
            vp_hdr = ViewNode("TextView", vid="browser_vp_hdr", text=f"Mobile Browser-Vorschau · {self.status_msg}")
            vp_hdr.props["h"] = 22
            vp_card.add(vp_hdr)
            vp_card.add(ViewNode(
                "ImageView",
                vid="browser_pixel_view",
                props={"img_bytes": self.last_png, "h": 430, "img": "mobile_viewport"},
            ))
            root.add(vp_card)

        res = self.last_result
        if res is None:
            empty_card = ViewNode("LinearLayout", vid="browser_empty_card", props={"role": "card"})
            empty_card.add(ViewNode("TextView", vid="browser_empty_t", text="In-Phone Browser bereit"))
            empty_card.add(ViewNode(
                "TextView",
                vid="browser_empty_d",
                text="Wähle oben eine App-URL oder gib eine Adresse ein — alles wird direkt hier im Handy gerendert (Chromium Analyse optional über 🔬 Inspect).",
            ))
            root.add(empty_card)
            return root

        if not res.get("ok"):
            err_card = ViewNode("LinearLayout", vid="browser_error_card", props={"role": "card"})
            err_card.add(ViewNode("TextView", vid="browser_err_title", text="Seite nicht geladen"))
            err_card.add(ViewNode("TextView", vid="browser_err_body", text=str(res.get("error") or self.status_msg)))
            root.add(err_card)
            return root

        # Titel & Status-Card
        page_card = ViewNode("LinearLayout", vid="browser_page_card", props={"role": "card"})
        page_card.add(ViewNode("TextView", vid="browser_page_title", text=_short_label(res.get("title") or self.current_url, 60)))
        page_card.add(ViewNode("TextView", vid="browser_page_meta", text=f"{self.status_msg} · {self.current_url[:58]}"))
        seen_txt = set()
        added_blocks = 0
        for tag, txt in (res.get("text_blocks") or ()):
            clean = re.sub(r"\s+", " ", str(txt or "")).strip()
            if not clean or clean in seen_txt or clean == res.get("title"):
                continue
            seen_txt.add(clean)
            if len(clean) > 220:
                clean = clean[:217] + "..."
            page_card.add(ViewNode("TextView", vid=f"browser_txt_{added_blocks}", text=clean))
            added_blocks += 1
            if added_blocks >= 8:
                break
        if added_blocks == 0 and res.get("text"):
            preview = res["text"].strip()[:240]
            page_card.add(ViewNode("TextView", vid="browser_raw_preview", text=preview))
        root.add(page_card)

        # Interaktive HTML-Formulare
        forms = list(res.get("forms") or ())
        for fi, form in enumerate(forms[:3]):
            fcard = ViewNode("LinearLayout", vid=f"browser_form_card_{fi}", props={"role": "card"})
            act_u = form.get("action") or self.current_url
            m_str = (form.get("method") or "GET").upper()
            fcard.add(ViewNode("TextView", vid=f"browser_form_hdr_{fi}", text=f"Formular ({m_str} -> {_short_label(act_u, 36)})"))
            fvals = self._form_values.setdefault(fi, {})
            for ii, inp in enumerate((form.get("inputs") or ())[:6]):
                iname = inp.get("name") or f"feld_{ii}"
                itype = (inp.get("type") or "text").lower()
                if itype in ("hidden", "submit", "button"):
                    if itype == "hidden" and iname:
                        fvals.setdefault(iname, str(inp.get("value") or ""))
                    continue
                if iname not in fvals:
                    fvals[iname] = str(inp.get("value") or "")
                ed = ViewNode(
                    "EditText",
                    vid=f"browser_form_{fi}_{iname}",
                    text=fvals[iname],
                    clickable=True,
                    focusable=True,
                    props={
                        "hint": inp.get("placeholder") or iname,
                        "$on_change_fn": lambda val, _fv=fvals, _k=iname: _fv.__setitem__(_k, val),
                    },
                )
                fcard.add(ed)
            sub_btn = ViewNode(
                "Button",
                vid=f"browser_form_submit_{fi}",
                text=f"Absenden ({m_str})",
                clickable=True,
            )
            def _submit_form(_v=None, _au=act_u, _m=m_str, _fv=fvals, _fi=fi):
                if getattr(self.tree, "root", None) is not None:
                    for fk in list(_fv.keys()):
                        ed_n = self.tree.root.find(f"browser_form_{_fi}_{fk}")
                        if ed_n is not None and isinstance(getattr(ed_n, "text", None), str):
                            _fv[fk] = ed_n.text
                self.load_url(_au, method=_m, form_data=dict(_fv))

            sub_btn.on_click = _submit_form
            fcard.add(sub_btn)
            root.add(fcard)

        # Klickbare Links (Navigation direkt im Handy)
        links = list(res.get("links") or ())
        if links:
            lcard = ViewNode("LinearLayout", vid="browser_links_card", props={"role": "card"})
            lcard.add(ViewNode("TextView", vid="browser_links_hdr", text=f"Links auf der Seite ({len(links)}):"))
            seen_href = set()
            cnt = 0
            for lk in links:
                href = str(lk.get("href") or "").strip()
                if not href or href.startswith(("#", "javascript:")) or href in seen_href:
                    continue
                seen_href.add(href)
                lbl = lk.get("text") or urllib.parse.urlsplit(href).path or href
                lbtn = ViewNode(
                    "Button",
                    vid=f"browser_link_{cnt}",
                    text=f"🔗 {_short_label(lbl, 34)}",
                    clickable=True,
                    props={
                        "bg_drawable": {"kind": "shape", "fill": "#f3f6ff", "outline": "#b8c7ea", "radius": 6},
                        "h": 34,
                    },
                )
                lbtn.on_click = lambda _v=None, _h=href: self.load_url(_h)
                lcard.add(lbtn)
                cnt += 1
                if cnt >= 10:
                    break
            root.add(lcard)

        return root

    def mount(self, container_node: Optional[ViewNode] = None) -> ViewNode:
        if container_node is None:
            fctrl = getattr(self.tree, "_flutter_ctrl", None)
            if fctrl is not None and getattr(fctrl, "screen", "") == "browser":
                return fctrl.mount()
        node = self.build_browser_nodes()
        if container_node is not None:
            container_node.children = [node]
            node._parent = container_node
        else:
            self.tree.dark_mode = False
            self.tree.window_bg = "#f3f4f7"
            self.tree.window_bg_dark = False
            self.tree.root = node
        self.tree.touch()
        return node


class FlutterInPhoneController:
    """Interaktiver In-Phone-Screen-Controller für Flutter-Apps direkt im ViewTree."""

    def __init__(
        self,
        tree: ViewTree,
        app_root: str,
        *,
        stubs: Any = None,
        on_async_update: Optional[Callable[[], None]] = None,
    ):
        self.tree = tree
        self.app_root = app_root
        self.stubs = stubs
        self.on_async_update = on_async_update
        self.spec = extract_flutter_app_spec(app_root)
        self.screen = "welcome"
        self._screen_before_browser = "welcome"
        self.home_tab = 0
        self.email = ""
        self.password = ""
        self.auth_status = ""
        self.auth_token = ""
        self.demo_mode = False
        self.api_log_summary = ""
        self.browser = InPhoneBrowserController(
            tree=tree,
            app_root=app_root,
            stubs=stubs,
            quick_urls=self.spec.get("quick_urls"),
            on_exit=self.close_browser,
            on_async_update=on_async_update,
        )

    def set_async_updater(self, cb: Optional[Callable[[], None]]) -> None:
        self.on_async_update = cb
        self.browser.on_async_update = cb

    def _tr(self, section: str, key: str, default: str) -> str:
        sec = (self.spec.get("translations") or {}).get(section)
        if isinstance(sec, dict):
            val = sec.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return default

    def _net_fire(self, method: str, url: str, headers: dict, body: bytes):
        fn = getattr(self.stubs, "net_fire", None) if self.stubs is not None else None
        if callable(fn):
            return fn(method, url, headers, body)
        from core.java.net_recorder import NetResponse
        return NetResponse(200, {"Content-Type": "application/json"}, b"{}", "OK")

    def invoke_channel(self, channel: str, method: str, arguments: Any = None) -> Dict[str, Any]:
        """Loest einen Platform-Channel-Aufruf ueber die aktive G6-MethodChannel-Bridge aus."""
        try:
            from core.java.flutter_channel_bridge import invoke_flutter_channel
            return invoke_flutter_channel(self.stubs, channel, method, arguments)
        except Exception as exc:
            return {"channel": channel, "method": method, "status": "error", "error": repr(exc), "result": None}

    def open_browser(self, url: Optional[str] = None) -> None:
        if self.screen != "browser":
            self._screen_before_browser = self.screen
        self.screen = "browser"
        target = url
        if not target:
            q = self.spec.get("quick_urls") or []
            target = self.browser.current_url or (q[0]["url"] if q else "")
        if target and (target != self.browser.current_url or self.browser.last_result is None):
            self.browser.load_url(target)
        else:
            self.mount()

    def close_browser(self) -> None:
        if self.screen == "browser":
            self.screen = self._screen_before_browser or "welcome"
            if self.screen == "browser":
                self.screen = "welcome"
            self.mount()

    def toggle_browser(self, url: Optional[str] = None) -> bool:
        if self.screen == "browser" and url is None:
            self.close_browser()
            return False
        self.open_browser(url=url)
        return True

    def handle_back(self) -> bool:
        if self.screen == "browser":
            return self.browser.go_back()
        if self.screen == "login":
            self.auth_status = ""
            self.screen = "welcome"
            self.mount()
            return True
        if self.screen == "home":
            if self.home_tab != 0:
                self.home_tab = 0
                self.mount()
                return True
            self.screen = "welcome"
            self.mount()
            return True
        return False

    def handle_home(self) -> bool:
        if self.screen == "browser":
            self.screen = "home" if (self.auth_token or self.demo_mode) else "welcome"
            self.mount()
            return True
        if self.auth_token or self.demo_mode:
            self.screen = "home"
            self.home_tab = 0
            self.mount()
            return True
        self.screen = "welcome"
        self.mount()
        return True

    def _fire_backend_sync(self) -> tuple[int, str]:
        gql_url = self.spec.get("graphql_url") or ""
        api_key = self.spec.get("api_key") or ""
        gql_tpl = self.spec.get("graphql_template")
        rest_tpl = self.spec.get("rest_template")
        if gql_url:
            hdrs = {
                "Content-Type": "application/json",
                "User-Agent": f"{self.spec.get('brand', 'Flutter')}-App/Android",
            }
            if api_key:
                hdrs["x-api-key"] = api_key
            if self.auth_token and self.auth_token != "demo-session":
                hdrs["Authorization"] = self.auth_token
            body_s = (gql_tpl or {}).get("body") or json.dumps({"query": "query GetDashboard { __typename }"})
            resp = self._net_fire("POST", gql_url, hdrs, body_s.encode("utf-8"))
            return int(getattr(resp, "code", 200)), gql_url
        if rest_tpl and rest_tpl.get("url"):
            u = rest_tpl["url"]
            m = rest_tpl.get("method") or "GET"
            hdrs = dict(rest_tpl.get("headers") or {})
            body_b = (rest_tpl.get("body") or "").encode("utf-8")
            resp = self._net_fire(m, u, hdrs, body_b)
            return int(getattr(resp, "code", 200)), u
        return 200, "offline-local"

    def submit_login(self, _node=None) -> None:
        if getattr(self.tree, "root", None) is not None:
            em_nd = self.tree.root.find("login_email")
            if em_nd is not None and isinstance(getattr(em_nd, "text", None), str) and em_nd.text:
                self.email = em_nd.text
            pw_nd = self.tree.root.find("login_password")
            if pw_nd is not None and isinstance(getattr(pw_nd, "text", None), str) and pw_nd.text:
                self.password = pw_nd.text
        user = (self.email or "").strip() or (self.spec.get("demo_email") or "test@example.com")
        pwd = self.password or ""
        if not pwd:
            self.auth_status = "Bitte Passwort eingeben (oder 'Demo testen' wählen)."
            self.mount()
            return
        self.email = user
        cog = self.spec.get("cognito")
        if cog:
            hdrs = {
                "Content-Type": "application/x-amz-json-1.1",
                "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
            }
            payload = {
                "AuthFlow": "USER_PASSWORD_AUTH",
                "ClientId": cog["client_id"],
                "AuthParameters": {"USERNAME": user, "PASSWORD": pwd},
            }
            url = cog["url"]
            resp = self._net_fire("POST", url, hdrs, json.dumps(payload).encode("utf-8"))
        elif self.spec.get("graphql_url"):
            url = self.spec["graphql_url"]
            hdrs = {"Content-Type": "application/json"}
            if self.spec.get("api_key"):
                hdrs["x-api-key"] = self.spec["api_key"]
            payload = {"query": "mutation SignIn { login }", "variables": {"username": user}}
            resp = self._net_fire("POST", url, hdrs, json.dumps(payload).encode("utf-8"))
        else:
            self.demo_mode = True
            self.screen = "home"
            self.auth_status = ""
            self.mount()
            return

        code = int(getattr(resp, "code", 200))
        rbody = bytes(getattr(resp, "body", b"") or b"").decode("utf-8", "replace")
        parsed = None
        try:
            parsed = json.loads(rbody) if rbody.strip().startswith("{") else None
        except Exception:
            parsed = None

        err_type = parsed.get("__type") if isinstance(parsed, dict) else None
        if 200 <= code < 300 and not err_type:
            tok = ""
            if isinstance(parsed, dict):
                ar = parsed.get("AuthenticationResult")
                if isinstance(ar, dict):
                    tok = str(ar.get("AccessToken") or ar.get("IdToken") or "")
            self.auth_token = tok or "session-ok"
            self.demo_mode = False
            self.auth_status = ""
            sc, ep = self._fire_backend_sync()
            self.api_log_summary = f"Auth {code} · Sync {sc} ({_short_label(ep, 32)})"
            self.screen = "home"
            self.home_tab = 0
            self.mount()
            return

        msg = ""
        if isinstance(parsed, dict):
            msg = str(parsed.get("message") or parsed.get("Message") or parsed.get("error") or "")
        if not msg:
            msg = getattr(resp, "message", "") or f"HTTP {code}"
        self.auth_status = f"Anmeldung fehlgeschlagen ({code}): {msg}"
        self.mount()

    def enter_demo(self, _node=None) -> None:
        self.demo_mode = True
        self.auth_token = "demo-session"
        self.auth_status = ""
        sc, ep = self._fire_backend_sync()
        self.api_log_summary = f"Demo aktiv · Sync {sc} ({_short_label(ep, 32)})"
        self.screen = "home"
        self.home_tab = 0
        self.mount()

    def _build_welcome_root(self) -> ViewNode:
        imgs = self.spec.get("images") or {}
        app_name = self.spec.get("app_name") or "App"
        box = ViewNode("LinearLayout", vid="flutter_welcome_root", props={"role": "scaffold"})

        topbar = ViewNode("LinearLayout", vid="welcome_topbar", props={"orientation": "horizontal", "role": "topbar"})
        topbar.add(ViewNode("TextView", vid="welcome_brand_hdr", text=app_name, props={"layout_weight": 1.0}))
        web_btn = ViewNode("Button", vid="welcome_web_btn", text="🌐 Web", clickable=True, props={"w": 76})
        web_btn.on_click = lambda _v=None: self.open_browser()
        topbar.add(web_btn)
        box.add(topbar)

        hero_card = ViewNode("LinearLayout", vid="welcome_hero_card", props={"role": "card"})
        logo_v = _image_node("welcome_logo", imgs.get("logo") or imgs.get("hero") or "", h=78)
        if logo_v is not None:
            hero_card.add(logo_v)
        hero_v = _image_node("welcome_hero", imgs.get("hero") or imgs.get("pet") or "", h=132, scale="crop")
        if hero_v is not None and (imgs.get("hero") != imgs.get("logo")):
            hero_card.add(hero_v)

        w_title = self._tr("login", "welcome", f"Willkommen bei {app_name}")
        hero_card.add(ViewNode("TextView", vid="welcome_title", text=w_title))
        sub_txt = self._tr("login", "credentials", "Flutter UI + Backend-Endpunkte interaktiv im Handy-Simulator")
        hero_card.add(ViewNode("TextView", vid="welcome_sub", text=sub_txt))

        btn_login = ViewNode("Button", vid="btn_open_login", text=self._tr("login", "login", "Anmelden"), clickable=True)
        btn_login.on_click = lambda _v=None: (setattr(self, "screen", "login"), setattr(self, "auth_status", ""), self.mount())
        hero_card.add(btn_login)

        btn_demo = ViewNode(
            "Button",
            vid="btn_try_demo",
            text=self._tr("demo", "try_demo", "Demo testen"),
            clickable=True,
            props={"bg_drawable": {"kind": "shape", "fill": "#eef3ff", "outline": "#9bb5f5", "radius": 6}},
        )
        btn_demo.on_click = self.enter_demo
        hero_card.add(btn_demo)

        btn_reg = ViewNode(
            "Button",
            vid="btn_register",
            text=self._tr("login", "register", "Registrieren"),
            clickable=True,
            props={"bg_drawable": {"kind": "shape", "fill": "#ffffff", "outline": "#c3c9d6", "radius": 6}},
        )
        btn_reg.on_click = lambda _v=None: self.open_browser()
        hero_card.add(btn_reg)
        box.add(hero_card)

        q_urls = self.spec.get("quick_urls") or []
        if q_urls:
            wcard = ViewNode("LinearLayout", vid="welcome_links_card", props={"role": "card"})
            wcard.add(ViewNode("TextView", vid="welcome_links_hdr", text="In-App Webseiten direkt öffnen:"))
            row = ViewNode("LinearLayout", props={"orientation": "horizontal"})
            for idx, entry in enumerate(q_urls[:2]):
                b = ViewNode(
                    "Button",
                    vid=f"welcome_url_{idx}",
                    text=entry["label"],
                    clickable=True,
                    props={"bg_drawable": {"kind": "shape", "fill": "#eef3ff", "outline": "#9bb5f5", "radius": 6}, "h": 34},
                )
                b.on_click = lambda _v=None, _u=entry["url"]: self.open_browser(_u)
                row.add(b)
            wcard.add(row)
            box.add(wcard)

        return box

    def _build_login_root(self) -> ViewNode:
        imgs = self.spec.get("images") or {}
        box = ViewNode("LinearLayout", vid="flutter_login_root", props={"role": "scaffold"})

        topbar = ViewNode("LinearLayout", vid="login_topbar", props={"orientation": "horizontal", "role": "topbar"})
        back_btn = ViewNode("Button", vid="login_back_btn", text="←", clickable=True, props={"w": 42})
        back_btn.on_click = lambda _v=None: (setattr(self, "screen", "welcome"), self.mount())
        topbar.add(back_btn)
        topbar.add(ViewNode("TextView", vid="login_hdr", text=self._tr("login", "login", "Anmelden"), props={"layout_weight": 1.0}))
        box.add(topbar)

        card = ViewNode("LinearLayout", vid="login_card", props={"role": "card"})
        logo_v = _image_node("login_logo", imgs.get("logo") or imgs.get("pet") or "", h=64)
        if logo_v is not None:
            card.add(logo_v)
        card.add(ViewNode("TextView", vid="login_sub", text=self._tr("login", "welcome", f"Willkommen bei {self.spec.get('app_name', 'App')}")))

        ed_email = ViewNode(
            "EditText",
            vid="login_email",
            text=self.email,
            clickable=True,
            focusable=True,
            props={
                "hint": self._tr("login", "email", "E-Mail oder Telefonnummer"),
                "$on_change_fn": lambda t: setattr(self, "email", t),
            },
        )
        ed_pwd = ViewNode(
            "EditText",
            vid="login_password",
            text=self.password,
            clickable=True,
            focusable=True,
            props={
                "hint": self._tr("login", "password", "Passwort"),
                "$on_change_fn": lambda t: setattr(self, "password", t),
                "$on_submit_fn": lambda _t=None: self.submit_login(),
            },
        )
        card.add(ed_email)
        card.add(ed_pwd)

        if self.auth_status:
            card.add(ViewNode("TextView", vid="login_error", text=self.auth_status))

        btn_submit = ViewNode("Button", vid="btn_login_submit", text=self._tr("login", "login", "Anmelden"), clickable=True)
        btn_submit.on_click = self.submit_login
        card.add(btn_submit)

        if self.spec.get("demo_email"):
            btn_fill = ViewNode(
                "Button",
                vid="btn_fill_demo",
                text=f"Demo-E-Mail übernehmen ({_short_label(self.spec['demo_email'], 22)})",
                clickable=True,
                props={"bg_drawable": {"kind": "shape", "fill": "#eef3ff", "outline": "#9bb5f5", "radius": 6}, "h": 34},
            )
            btn_fill.on_click = lambda _v=None: (setattr(self, "email", self.spec["demo_email"]), self.mount())
            card.add(btn_fill)

        btn_demo = ViewNode(
            "Button",
            vid="btn_login_demo",
            text=self._tr("demo", "start", "Demo starten"),
            clickable=True,
            props={"bg_drawable": {"kind": "shape", "fill": "#ffffff", "outline": "#c3c9d6", "radius": 6}},
        )
        btn_demo.on_click = self.enter_demo
        card.add(btn_demo)
        box.add(card)

        cog = self.spec.get("cognito")
        if cog or self.spec.get("graphql_url"):
            info_c = ViewNode("LinearLayout", vid="login_net_info", props={"role": "card"})
            ep_txt = (cog or {}).get("url") or self.spec.get("graphql_url")
            info_c.add(ViewNode("TextView", vid="login_ep_lbl", text=f"Auth-Endpunkt: {_short_label(ep_txt, 46)}"))
            box.add(info_c)

        return box

    def _build_home_root(self) -> ViewNode:
        imgs = self.spec.get("images") or {}
        app_name = self.spec.get("app_name") or "App"
        tabs = [
            ("home", self._tr("bottom_bar", "home", "HEIM")),
            ("position", self._tr("bottom_bar", "position", "POSITION")),
            ("activity", self._tr("bottom_bar", "activity", "AKTIVITÄT")),
            ("menu", self._tr("bottom_bar", "menu", "MENÜ")),
        ]
        tab_key, tab_title = tabs[self.home_tab % len(tabs)]

        box = ViewNode("LinearLayout", vid="flutter_home_root", props={"role": "scaffold"})

        topbar = ViewNode("LinearLayout", vid="home_topbar", props={"orientation": "horizontal", "role": "topbar"})
        badge = "Demo" if self.demo_mode else "Aktiv"
        topbar.add(ViewNode("TextView", vid="home_title", text=f"{app_name} · {tab_title} ({badge})", props={"layout_weight": 1.0}))
        web_btn = ViewNode("Button", vid="home_web_btn", text="🌐 Web", clickable=True, props={"w": 72})
        web_btn.on_click = lambda _v=None: self.open_browser()
        topbar.add(web_btn)
        box.add(topbar)

        card = ViewNode("LinearLayout", vid="home_main_card", props={"role": "card"})
        if tab_key == "home":
            pv = _image_node("home_pet_img", imgs.get("pet") or imgs.get("logo") or "", h=130)
            if pv is not None:
                card.add(pv)
            card.add(ViewNode("TextView", vid="home_card_t", text=self._tr("home", "add_your_pet", f"Willkommen in {app_name}")))
            card.add(ViewNode("TextView", vid="home_card_geo", text=f"Status: {self._tr('home', 'geo', 'Sicher im Geofence')} · {self._tr('home', 'bluetooth', 'In Ihrer Nähe.')}"))
        elif tab_key == "position":
            pv = _image_node("home_pos_img", imgs.get("position") or imgs.get("pet") or "", h=170, scale="crop")
            if pv is not None:
                card.add(pv)
            card.add(ViewNode("TextView", vid="pos_card_t", text="Live-Position & Geofence"))
            card.add(ViewNode("TextView", vid="pos_card_d", text="Interaktiver GPS-/AppSync-Tracker über Debugger-Netzwerk-Pipeline."))
        elif tab_key == "activity":
            av = _image_node("home_act_img", imgs.get("activity") or imgs.get("health") or "", h=170, scale="crop")
            if av is not None:
                card.add(av)
            card.add(ViewNode("TextView", vid="act_card_t", text="Aktivität & Gesundheit"))
            card.add(ViewNode("TextView", vid="act_card_d", text="Tagesziele, Bewegung & Akkulaufzeit aus AppSync / GraphQL."))
        else:
            bv = _image_node("home_bat_img", imgs.get("health") or imgs.get("logo") or "", h=72)
            if bv is not None:
                card.add(bv)
            card.add(ViewNode("TextView", vid="menu_card_t", text=self._tr("menu", "app_settings", "App-Einstellungen")))
            card.add(ViewNode("TextView", vid="menu_card_d", text=self._tr("menu", "edit_profile", "Profil & Geräte-Einstellungen")))

        if self.api_log_summary:
            card.add(ViewNode("TextView", vid="home_api_summary", text=f"✅ {self.api_log_summary}"))

        sync_btn = ViewNode("Button", vid="btn_home_sync", text="🔄 API / GraphQL Sync ausführen", clickable=True)

        def _do_sync(_v=None):
            sc, ep = self._fire_backend_sync()
            self.api_log_summary = f"Sync {sc} ({_short_label(ep, 32)})"
            self.mount()

        sync_btn.on_click = _do_sync
        card.add(sync_btn)
        box.add(card)

        q_urls = self.spec.get("quick_urls") or []
        if q_urls:
            ucard = ViewNode("LinearLayout", vid="home_web_card", props={"role": "card"})
            ucard.add(ViewNode("TextView", vid="home_web_hdr", text="In-App WebViews (öffnen im Handy):"))
            row = None
            for idx, entry in enumerate(q_urls[:4]):
                if idx % 2 == 0:
                    row = ViewNode("LinearLayout", props={"orientation": "horizontal"})
                    ucard.add(row)
                wb = ViewNode(
                    "Button",
                    vid=f"home_url_btn_{idx}",
                    text=entry["label"],
                    clickable=True,
                    props={"bg_drawable": {"kind": "shape", "fill": "#eef3ff", "outline": "#9bb5f5", "radius": 6}, "h": 34},
                )
                wb.on_click = lambda _v=None, _u=entry["url"]: self.open_browser(_u)
                row.add(wb)
            box.add(ucard)

        navbar = ViewNode("LinearLayout", vid="home_navbar", props={"orientation": "horizontal", "role": "navbar"})
        for idx, (_tk, tlbl) in enumerate(tabs):
            nb = ViewNode(
                "Button",
                vid=f"nav_tab_{idx}",
                text=_short_label(tlbl, 12),
                clickable=True,
                props={"role": "navitem", "selected": (idx == self.home_tab)},
            )
            nb.on_click = lambda _v=None, _i=idx: (setattr(self, "home_tab", _i), self.mount())
            navbar.add(nb)
        box.add(navbar)
        return box

    def mount(self, touch: bool = True) -> ViewNode:
        self.tree.dark_mode = False
        self.tree.window_bg = "#f3f4f7"
        self.tree.window_bg_dark = False
        if self.screen == "browser":
            content = self.browser.build_browser_nodes()
        elif self.screen == "login":
            content = self._build_login_root()
        elif self.screen == "home":
            content = self._build_home_root()
        else:
            content = self._build_welcome_root()

        cur_root = getattr(self.tree, "root", None)
        if cur_root is not None and getattr(cur_root, "cls", "") == "FlutterView":
            fl_root = cur_root
            fl_root.props["role"] = "scaffold"
        else:
            fl_root = ViewNode("FlutterView", vid="flutter_root", props={"role": "scaffold"})
            self.tree.root = fl_root
        fl_root.children = [content]
        content._parent = fl_root
        if touch:
            self.tree.touch()
        return fl_root


def mount_flutter_app_into_tree(
    tree: ViewTree,
    app_root: str,
    *,
    stubs: Any = None,
    on_async_update: Optional[Callable[[], None]] = None,
) -> FlutterInPhoneController:
    """Hängt eine interaktive Flutter-In-Phone-UI samt Browser in `tree` ein und verankert sie an `tree._flutter_ctrl`."""
    existing = getattr(tree, "_flutter_ctrl", None)
    if isinstance(existing, FlutterInPhoneController) and existing.app_root == app_root:
        if stubs is not None:
            existing.stubs = stubs
            existing.browser.stubs = stubs
        if on_async_update is not None:
            existing.set_async_updater(on_async_update)
        existing.mount()
        return existing

    ctrl = FlutterInPhoneController(tree, app_root, stubs=stubs, on_async_update=on_async_update)
    tree._flutter_ctrl = ctrl
    tree._in_phone_browser = ctrl.browser
    ctrl.mount()
    return ctrl
