# -*- coding: utf-8 -*-
"""
Hybrid-Web App Producer (DCloud HTML5+ / uni-app, Cordova, Capacitor, WebView)
=============================================================================

Analysiert Hybrid-Web-APKs (HTML/JS/CSS-Bundles unter ``assets/apps/.../www``,
``assets/www``, ``assets/public``, ``res/xml/config.xml``, ``dcloud_control.xml``)
generisch und montiert eine interaktive App-Oberfläche samt eingebettetem
``InPhoneBrowserController`` direkt in einen ``ViewTree``.
"""

from __future__ import annotations

import html
import json
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List, Optional

from core.java.surface import SceneGraphSurface
from core.java.view_tree import ViewNode, ViewTree


_URL_RE = re.compile(r"https?://[^\s\"'<>`\\)]+", re.IGNORECASE)
_PROTO_REL_URL_RE = re.compile(r"""["']//([a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(?::\d+)?(?:/[^\s"'<>`\\)]*)?)["']""")
_API_ROUTE_RE = re.compile(
    r"""["'](/(?:user|device|app|update|group|auth|api|order|shop|mall|config|control|v\d+)[a-zA-Z0-9_/-]{2,60})["']"""
)
_DOC_NOISE_DOMAINS = (
    "w3.org", "schemas.android.com", "schema.org", "localhost", "127.0.0.1",
    "jquery.com", "jquery.org", "github.com", "mozilla.org", "opensource.org",
    "iso.org", "loc.gov", "cubiq.org", "unixpapa.com", "keith-wood.name",
    "d-project.com", "denso-wave.com", "code.google.com", "naver.com",
    "chartjs.org", "dcloud.net.cn/mui", "wikipedia.org", "stackoverflow.com",
    "momentjs.com", "dropboxusercontent.com", "24ways.org", "scaledinnovation.com",
    "paulirish.com", "html5canvastutorials.com", "robertpenner.com", "davidwalsh.name",
    "anujgakhar.com", "nathanaeljones.com", "chartjs.gitbooks.io",
)
_TAG_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_HEADING_RE = re.compile(r"<h[1-3][^>]*>(.*?)</h[1-3]>", re.IGNORECASE | re.DOTALL)
_HEADER_TITLE_RE = re.compile(
    r'<[^>]*class="[^"]*mui-title[^"]*"[^>]*>(.*?)</[^>]+>',
    re.IGNORECASE | re.DOTALL,
)
_BUTTON_RE = re.compile(r"<button[^>]*>(.*?)</button>", re.IGNORECASE | re.DOTALL)
_INPUT_RE = re.compile(r"<input\b([^>]+)>", re.IGNORECASE | re.DOTALL)
_ATTR_PLACEHOLDER_RE = re.compile(r'placeholder\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
_ATTR_LOCALE_RE = re.compile(r'data-locale\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")
_HTML_STRIP_RE = re.compile(r"<[^>]+>")
_TPL_CALL_RE = re.compile(r"\{\{\s*[^('\"{]*['\"]([^'\"]+)['\"][^}]*\}\}")
_TPL_ANY_RE = re.compile(r"\{\{[^}]*\}\}")


def _short_label(text: str, limit: int = 28) -> str:
    s = " ".join(str(text or "").split())
    if len(s) <= limit:
        return s
    return s[: max(1, limit - 1)].rstrip() + "…"


def _clean_html_text(raw: str) -> str:
    no_tags = _HTML_STRIP_RE.sub(" ", raw or "")
    unescaped = html.unescape(no_tags)
    extracted = _TPL_CALL_RE.sub(r" \1 ", unescaped)
    cleaned = _TPL_ANY_RE.sub(" ", extracted)
    return " ".join(cleaned.split()).strip()


def _img_node(vid: str, path: str, h: int, w: Optional[int] = None) -> Optional[ViewNode]:
    if not path or not os.path.isfile(path):
        return None
    props: Dict[str, Any] = {
        "drawable": {"kind": "raster", "path": path, "name": os.path.basename(path)},
        "h": int(h),
    }
    if w is not None:
        props["w"] = int(w)
    return ViewNode("ImageView", vid=vid, props=props)


def _find_dcloud_www(app_root: str) -> Optional[Dict[str, str]]:
    assets_dir = os.path.join(app_root, "assets")
    apps_dir = os.path.join(assets_dir, "apps")
    if not os.path.isdir(apps_dir):
        return None

    appid = ""
    ctrl_xml = os.path.join(assets_dir, "data", "dcloud_control.xml")
    if os.path.isfile(ctrl_xml):
        try:
            tree = ET.parse(ctrl_xml)
            for elem in tree.iter():
                if elem.tag.lower().endswith("app") and elem.attrib.get("appid"):
                    appid = str(elem.attrib["appid"]).strip()
                    break
        except Exception:
            pass

    candidates: List[str] = []
    if appid:
        candidates.append(appid)
    try:
        for entry in sorted(os.listdir(apps_dir)):
            if entry not in candidates and os.path.isdir(os.path.join(apps_dir, entry)):
                candidates.append(entry)
    except OSError:
        pass

    for cid in candidates:
        www_dir = os.path.join(apps_dir, cid, "www")
        if os.path.isdir(www_dir):
            return {"appid": cid, "www": www_dir}
    return None


def _parse_properties_file(path: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                s = line.strip()
                if not s or s.startswith("#") or s.startswith("!") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                k = k.strip()
                v = v.strip()
                if k:
                    out[k] = v
    except OSError:
        pass
    return out


def _collect_i18n(web_root: str) -> Dict[str, str]:
    merged: Dict[str, str] = {}
    if not web_root or not os.path.isdir(web_root):
        return merged
    prop_files: List[str] = []
    json_files: List[str] = []
    for root, _dirs, files in os.walk(web_root):
        for fn in sorted(files):
            low = fn.lower()
            full = os.path.join(root, fn)
            if low.endswith(".properties"):
                prop_files.append(full)
            elif low in ("en.json", "de.json", "strings_en.json"):
                json_files.append(full)

    # Englisch / Deutsch bevorzugen fuer lesbare UI-Labels
    def _rank(p: str) -> int:
        b = os.path.basename(p).lower()
        if "en" in b:
            return 0
        if "de" in b:
            return 1
        return 2

    for pf in sorted(prop_files, key=_rank, reverse=True):
        merged.update(_parse_properties_file(pf))
    for jf in sorted(json_files, key=_rank, reverse=True):
        try:
            with open(jf, "r", encoding="utf-8", errors="replace") as fh:
                obj = json.load(fh)
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(v, (str, int, float)):
                        merged[str(k)] = str(v)
        except Exception:
            pass
    return merged


def _resolve_locale_text(text: str, locale_key: str, i18n: Dict[str, str]) -> str:
    raw = text.strip()
    m = _TPL_CALL_RE.search(raw)
    if m:
        inner = m.group(1).strip()
        if inner in i18n and i18n[inner]:
            return i18n[inner]
        t = _clean_html_text(raw)
    else:
        t = _clean_html_text(raw)
    if locale_key and locale_key in i18n and i18n[locale_key]:
        return i18n[locale_key]
    if t and t in i18n and i18n[t]:
        return i18n[t]
    return t or locale_key


def _classify_page(rel_path: str, title: str, headings: List[str]) -> str:
    hay = f"{rel_path} {title} {' '.join(headings)}".lower()
    if any(k in hay for k in ("control", "param", "temp", "fridge", "steer")):
        return "control"
    if any(k in hay for k in ("device", "scan", "pair", "ble", "list")):
        return "device"
    if any(k in hay for k in ("shop", "store", "mall")):
        return "shop"
    if any(k in hay for k in ("about", "help", "info", "version", "guide")):
        return "about"
    if any(k in hay for k in ("login", "auth", "user", "personal", "account")):
        return "account"
    return "page"


def _parse_html_page(abs_path: str, web_root: str, i18n: Dict[str, str]) -> Dict[str, Any]:
    rel = os.path.relpath(abs_path, web_root).replace("\\", "/")
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read(250_000)
    except OSError:
        raw = ""

    title = ""
    for pat in (_HEADER_TITLE_RE, _TAG_TITLE_RE):
        m = pat.search(raw)
        if m:
            loc_m = _ATTR_LOCALE_RE.search(m.group(0))
            title = _resolve_locale_text(
                _clean_html_text(m.group(1)),
                loc_m.group(1) if loc_m else "",
                i18n,
            )
            if title:
                break
    if not title:
        stem = os.path.splitext(os.path.basename(rel))[0]
        title = i18n.get(stem, stem.replace("_", " ").replace("-", " ").title())

    headings: List[str] = []
    for m in _HEADING_RE.finditer(raw):
        txt = _clean_html_text(m.group(1))
        if txt and txt not in headings:
            headings.append(txt)
        if len(headings) >= 8:
            break

    buttons: List[str] = []
    for m in _BUTTON_RE.finditer(raw):
        loc_m = _ATTR_LOCALE_RE.search(m.group(0))
        txt = _resolve_locale_text(
            _clean_html_text(m.group(1)),
            loc_m.group(1) if loc_m else "",
            i18n,
        )
        if txt and txt not in buttons:
            buttons.append(txt)
        if len(buttons) >= 10:
            break

    inputs: List[str] = []
    for m in _INPUT_RE.finditer(raw):
        pm = _ATTR_PLACEHOLDER_RE.search(m.group(1))
        if pm:
            ph = i18n.get(pm.group(1), pm.group(1)).strip()
            if ph and ph not in inputs:
                inputs.append(ph)
        if len(inputs) >= 6:
            break

    # Generische data-locale Labels auf Span/Div/P aufsammeln
    labels: List[str] = []
    for lm in _ATTR_LOCALE_RE.finditer(raw):
        val = i18n.get(lm.group(1), lm.group(1)).strip()
        if val and val not in labels:
            labels.append(val)
        if len(labels) >= 16:
            break

    cat = _classify_page(rel, title, headings)
    return {
        "rel_path": rel,
        "abs_path": abs_path,
        "title": _short_label(title, 32),
        "category": cat,
        "headings": headings,
        "buttons": buttons,
        "inputs": inputs,
        "labels": labels,
    }


def _collect_images(app_root: str, web_root: str) -> Dict[str, Any]:
    logo = ""
    hero = ""
    gallery: List[str] = []

    res_dir = os.path.join(app_root, "res")
    icon_candidates: List[str] = []
    if os.path.isdir(res_dir):
        for root, _dirs, files in os.walk(res_dir):
            for fn in files:
                low = fn.lower()
                if not low.endswith((".png", ".jpg", ".jpeg", ".webp")):
                    continue
                if "icon" in low or "ic_launcher" in low or "logo" in low:
                    full = os.path.join(root, fn)
                    if not low.endswith(".9.png"):
                        icon_candidates.append(full)
    if icon_candidates:
        icon_candidates.sort(key=lambda p: os.path.getsize(p) if os.path.isfile(p) else 0, reverse=True)
        logo = icon_candidates[0]

    web_imgs: List[str] = []
    if web_root and os.path.isdir(web_root):
        for root, _dirs, files in os.walk(web_root):
            for fn in sorted(files):
                low = fn.lower()
                if not low.endswith((".png", ".jpg", ".jpeg", ".webp")):
                    continue
                if low.endswith(".9.png"):
                    continue
                web_imgs.append(os.path.join(root, fn))

    for p in web_imgs:
        low = os.path.basename(p).lower()
        if not logo and any(k in low for k in ("logo", "icon", "app")):
            logo = p
        elif not hero and any(k in low for k in ("fridge", "device", "hero", "banner", "car", "main", "splash", "product")):
            hero = p
    if not hero and web_imgs:
        # Groesstes Bild im Web-Asset als Hero waehlen (ueber 2 KB, unter 2 MB)
        sized = [p for p in web_imgs if 2000 <= (os.path.getsize(p) if os.path.isfile(p) else 0) <= 2_000_000]
        if sized:
            sized.sort(key=lambda p: os.path.getsize(p), reverse=True)
            hero = sized[0]
    for p in web_imgs[:12]:
        if p not in gallery:
            gallery.append(p)
    return {"logo": logo, "hero": hero, "gallery": gallery}


def _is_noise_url(url: str) -> bool:
    low_u = (url or "").lower()
    return any(bad in low_u for bad in _DOC_NOISE_DOMAINS)


def _extract_urls(web_root: str) -> List[str]:
    backend: List[str] = []
    other: List[str] = []
    seen = set()
    if not web_root or not os.path.isdir(web_root):
        return []
    for root, _dirs, files in os.walk(web_root):
        for fn in sorted(files):
            low = fn.lower()
            if not low.endswith((".js", ".html", ".htm", ".json", ".xml")):
                continue
            full = os.path.join(root, fn)
            try:
                if os.path.getsize(full) > 768_000:
                    continue
                with open(full, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            candidates = list(_URL_RE.findall(text))
            for m in _PROTO_REL_URL_RE.findall(text):
                candidates.append("https://" + m)
            for u in candidates:
                u = u.rstrip(".,;:\"')")
                if _is_noise_url(u) or u in seen:
                    continue
                seen.add(u)
                low_u = u.lower()
                if any(k in low_u for k in ("api.", "/v1", "/v2", "/api", "push.", "ws", "scinan")):
                    backend.append(u)
                else:
                    other.append(u)
    return (backend + other)[:24]


def _extract_api_endpoints(web_root: str, urls: List[str]) -> List[Dict[str, str]]:
    """Extrahiert REST-/API-Endpunkte generisch aus Web-Bundles (.js/.html)."""
    if not web_root or not os.path.isdir(web_root):
        return []

    def _base_score(u: str) -> tuple[int, str]:
        low = u.lower()
        # Live-/Produktions-API vor Test-Endpunkten bevorzugen, HTTP/HTTPS ohne Websocket
        is_ws = "websocket" in low or low.startswith(("ws://", "wss://"))
        is_test = "testapi" in low or "test." in low
        is_api = ("api." in low or "/v1" in low or "/v2" in low or "/api" in low)
        return (1 if is_ws else 0, 0 if (is_api and not is_test) else (1 if is_api else 2), u)

    http_urls = [u for u in urls if u.lower().startswith(("http://", "https://")) and not _is_noise_url(u)]
    primary_base = sorted(http_urls, key=_base_score)[0].rstrip("/") if http_urls else ""

    routes: List[str] = []
    seen_routes = set()
    for root, _dirs, files in os.walk(web_root):
        for fn in sorted(files):
            if not fn.lower().endswith((".js", ".html", ".htm")):
                continue
            full = os.path.join(root, fn)
            try:
                if os.path.getsize(full) > 768_000:
                    continue
                with open(full, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            for rpath in _API_ROUTE_RE.findall(text):
                low_r = rpath.lower()
                if low_r.endswith((".html", ".htm", ".js", ".css", ".png", ".jpg", ".jpeg", ".svg")):
                    continue
                if rpath not in seen_routes:
                    seen_routes.add(rpath)
                    routes.append(rpath)

    endpoints: List[Dict[str, str]] = []
    if primary_base and routes:
        def _route_prio(p: str) -> int:
            lp = p.lower()
            if "update/app" in lp or "device/list" in lp or "device/status" in lp:
                return 0
            if "control" in lp or "login" in lp or "info" in lp:
                return 1
            return 2

        for rpath in sorted(routes, key=_route_prio):
            lp = rpath.lower()
            method = "GET" if any(k in lp for k in ("list", "info", "status", "avatar", "version", "get")) else "POST"
            cat = (
                "control" if any(k in lp for k in ("control", "status", "device"))
                else ("auth" if any(k in lp for k in ("login", "register", "token", "pwd", "user")) else "api")
            )
            endpoints.append({
                "method": method,
                "path": rpath,
                "url": primary_base + rpath,
                "category": cat,
            })
            if len(endpoints) >= 16:
                break
    elif http_urls:
        for u in http_urls[:8]:
            endpoints.append({"method": "GET", "path": urllib.parse.urlsplit(u).path or "/", "url": u, "category": "api"})
    return endpoints


def analyze_hybrid_web_app(app_root: str) -> Dict[str, Any]:
    """Analysiert DCloud HTML5+, Cordova, Capacitor oder WebView-Assets generisch."""
    root = str(app_root or "").strip()
    framework = "WebView"
    app_id = ""
    app_name = ""
    version = ""
    launch_path = ""
    web_root = ""
    accent_color = "#00A8E7"
    permissions: List[str] = []

    dcloud = _find_dcloud_www(root) if root and os.path.isdir(root) else None
    if dcloud is not None:
        framework = "DCloud HTML5+"
        app_id = dcloud["appid"]
        web_root = dcloud["www"]
        manifest_path = os.path.join(web_root, "manifest.json")
        if os.path.isfile(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8", errors="replace") as fh:
                    mf = json.load(fh)
                if isinstance(mf, dict):
                    app_id = str(mf.get("id") or app_id).strip()
                    app_name = str(mf.get("name") or app_name).strip()
                    ver = mf.get("version")
                    if isinstance(ver, dict):
                        version = str(ver.get("name") or ver.get("code") or "").strip()
                    launch_path = str(mf.get("launch_path") or launch_path).strip()
                    plus = mf.get("plus") if isinstance(mf.get("plus"), dict) else {}
                    sb = plus.get("statusbar") if isinstance(plus.get("statusbar"), dict) else {}
                    bg = str(sb.get("background") or "").strip()
                    if _HEX_COLOR_RE.match(bg):
                        accent_color = bg[:7]
                    perms = mf.get("permissions")
                    if isinstance(perms, dict):
                        permissions = sorted(str(k) for k in perms.keys())
            except Exception:
                pass
    elif root and os.path.isdir(root):
        cap_json = os.path.join(root, "assets", "capacitor.config.json")
        cordova_xml = os.path.join(root, "res", "xml", "config.xml")
        www_dir = os.path.join(root, "assets", "www")
        pub_dir = os.path.join(root, "assets", "public")

        if os.path.isfile(cap_json):
            framework = "Capacitor"
            try:
                with open(cap_json, "r", encoding="utf-8", errors="replace") as fh:
                    cfg = json.load(fh)
                if isinstance(cfg, dict):
                    app_id = str(cfg.get("appId") or "").strip()
                    app_name = str(cfg.get("appName") or "").strip()
                    web_dir = str(cfg.get("webDir") or "").strip()
                    if web_dir and os.path.isdir(os.path.join(root, "assets", web_dir)):
                        web_root = os.path.join(root, "assets", web_dir)
            except Exception:
                pass

        if os.path.isfile(cordova_xml):
            if framework == "WebView":
                framework = "Cordova"
            try:
                xt = ET.parse(cordova_xml)
                xroot = xt.getroot()
                if not app_id:
                    app_id = str(xroot.attrib.get("id") or "").strip()
                if not version:
                    version = str(xroot.attrib.get("version") or "").strip()
                for elem in xroot:
                    tag = elem.tag.split("}")[-1].lower()
                    if tag == "name" and elem.text and not app_name:
                        app_name = elem.text.strip()
                    elif tag == "content" and elem.attrib.get("src") and not launch_path:
                        launch_path = elem.attrib["src"].strip()
                    elif tag == "feature" and elem.attrib.get("name"):
                        permissions.append(str(elem.attrib["name"]).strip())
            except Exception:
                pass

        if not web_root:
            if os.path.isdir(www_dir):
                web_root = www_dir
            elif os.path.isdir(pub_dir):
                web_root = pub_dir
            else:
                web_root = os.path.join(root, "assets")

        if not launch_path and web_root and os.path.isdir(web_root):
            for cand in ("index.html", "main.html", "home.html"):
                if os.path.isfile(os.path.join(web_root, cand)):
                    launch_path = cand
                    break

    i18n = _collect_i18n(web_root)
    pages: List[Dict[str, Any]] = []
    if web_root and os.path.isdir(web_root):
        for rdir, _dirs, files in os.walk(web_root):
            for fn in sorted(files):
                if fn.lower().endswith((".html", ".htm")):
                    pages.append(_parse_html_page(os.path.join(rdir, fn), web_root, i18n))
        pages.sort(key=lambda p: (0 if p["rel_path"] == launch_path else 1, p["rel_path"]))

    launch_file = ""
    if web_root and launch_path:
        cand = os.path.normpath(os.path.join(web_root, launch_path.lstrip("/\\")))
        if os.path.isfile(cand):
            launch_file = cand
    if not launch_file and pages:
        launch_file = pages[0]["abs_path"]
        launch_path = pages[0]["rel_path"]

    if not app_name:
        if pages and pages[0]["title"]:
            app_name = pages[0]["title"]
        elif root:
            app_name = os.path.basename(os.path.dirname(root) if os.path.basename(root).startswith("base") else root) or "Hybrid App"
        else:
            app_name = "Hybrid App"

    images = _collect_images(root, web_root)
    urls = _extract_urls(web_root)
    api_endpoints = _extract_api_endpoints(web_root, urls)
    perm_join = " ".join(permissions).lower()
    page_cats = {p["category"] for p in pages}
    has_ble = any(k in perm_join for k in ("bluetooth", "ble", "scinan")) or "device" in page_cats or "control" in page_cats
    has_sqlite = "sqlite" in perm_join or "storage" in perm_join
    archetype = "device_control" if has_ble else "portal"

    return {
        "framework": framework,
        "app_id": app_id,
        "app_name": app_name,
        "version": version,
        "launch_path": launch_path,
        "launch_file": launch_file,
        "web_root": web_root,
        "accent_color": accent_color,
        "permissions": permissions,
        "i18n": i18n,
        "pages": pages,
        "images": images,
        "urls": urls,
        "api_endpoints": api_endpoints,
        "has_ble": has_ble,
        "has_sqlite": has_sqlite,
        "archetype": archetype,
    }


def _file_url(path: str) -> str:
    if not path:
        return ""
    norm = os.path.abspath(path).replace("\\", "/")
    if not norm.startswith("/"):
        norm = "/" + norm
    return "file://" + urllib.parse.quote(norm)


class HybridWebInPhoneController:
    """Interaktiver In-Phone-Controller für DCloud/Cordova/Capacitor/WebView-Apps."""

    def __init__(
        self,
        tree: ViewTree,
        app_root: str,
        *,
        spec: Optional[Dict[str, Any]] = None,
        stubs: Any = None,
        on_async_update: Optional[Callable[[], None]] = None,
    ):
        self.tree = tree
        self.app_root = app_root or ""
        self.spec = spec if spec is not None else analyze_hybrid_web_app(self.app_root)
        self.stubs = stubs
        self.on_async_update = on_async_update

        self.screen = "home"
        self._nav_stack: List[str] = []
        self.selected_page: Optional[Dict[str, Any]] = None
        self.power_on = True
        self.target_temp = 4
        self.unit = "°C"
        self.mode = "ECO"
        self.status_msg = ""
        self._initial_api_fired = False

        from core.java.flutter_app_producer import InPhoneBrowserController

        quick_urls: List[Dict[str, str]] = []
        if self.spec.get("launch_file"):
            quick_urls.append({
                "label": _short_label(os.path.basename(self.spec["launch_file"]), 16),
                "url": _file_url(self.spec["launch_file"]),
            })
        for p in self.spec.get("pages", ())[:3]:
            u = _file_url(p["abs_path"])
            if not any(q["url"] == u for q in quick_urls):
                quick_urls.append({"label": _short_label(p["title"], 16), "url": u})
        for u in self.spec.get("urls", ())[:3]:
            pr = urllib.parse.urlsplit(u)
            quick_urls.append({"label": _short_label(pr.netloc or u, 16), "url": u})

        self._browser = InPhoneBrowserController(
            tree=self.tree,
            app_root=self.app_root,
            stubs=self.stubs,
            quick_urls=quick_urls,
            on_exit=self.close_browser,
            on_async_update=on_async_update,
        )
        _orig_browser_mount = self._browser.mount

        def _delegated_browser_mount(container_node: Optional[ViewNode] = None) -> ViewNode:
            if container_node is None and self.screen == "browser":
                return self.mount()
            return _orig_browser_mount(container_node=container_node)

        self._browser.mount = _delegated_browser_mount  # type: ignore[method-assign]

    def set_async_updater(self, cb: Optional[Callable[[], None]]) -> None:
        self.on_async_update = cb
        self._browser.on_async_update = cb

    def _fire_web_api(self, method: str, url: str, body: Any = None) -> Dict[str, Any]:
        """Leitet Hybrid-Web REST/AJAX-Requests durch AndroidStubs.net_fire in den Netz-Inspektor."""
        if not url:
            return {"code": 0, "body": ""}
        nf = getattr(self.stubs, "net_fire", None) if self.stubs is not None else None
        if not callable(nf):
            self.status_msg = f"{method} {_short_label(url, 32)} (lokale Simulation ohne Netz-Stub)"
            return {"code": 0, "body": ""}
        if isinstance(body, (bytes, bytearray)):
            raw_body = bytes(body)
        elif isinstance(body, str):
            raw_body = body.encode("utf-8")
        elif isinstance(body, (dict, list)):
            raw_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        else:
            raw_body = b""
        hdrs = {
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-Hybrid-Framework": str(self.spec.get("framework") or "WebView"),
        }
        try:
            resp = nf(method.upper(), url, headers=hdrs, body=raw_body)
            code = int(getattr(resp, "code", None) or getattr(resp, "status", 0) or 0)
            rb = getattr(resp, "body", b"")
            btxt = bytes(rb).decode("utf-8", errors="replace") if isinstance(rb, (bytes, bytearray)) else str(rb or "")
        except Exception as exc:
            code = 0
            btxt = repr(exc)
        if self.tree is not None and hasattr(self.tree, "web_events"):
            self.tree.web_events.append({"kind": "hybrid_ajax", "method": method.upper(), "url": url, "code": code})
        self.status_msg = f"{method.upper()} {urllib.parse.urlsplit(url).path or '/'} → HTTP {code}"
        if callable(self.on_async_update):
            try:
                self.on_async_update()
            except Exception:
                pass
        return {"code": code, "body": btxt}

    def _trigger_initial_api_sync(self) -> None:
        if self._initial_api_fired:
            return
        nf = getattr(self.stubs, "net_fire", None) if self.stubs is not None else None
        eps = list(self.spec.get("api_endpoints") or ())
        if not callable(nf) or not eps:
            return
        self._initial_api_fired = True
        ep = eps[0]
        self._fire_web_api(ep["method"], ep["url"], {"app_id": self.spec.get("app_id") or ""})

    def _send_device_control_api(self) -> None:
        eps = list(self.spec.get("api_endpoints") or ())
        if not eps:
            return
        ctrl_ep = next((e for e in eps if "control" in e.get("path", "").lower()), None)
        if ctrl_ep is None:
            ctrl_ep = next((e for e in eps if e.get("category") == "control"), eps[0])
        self._fire_web_api(
            ctrl_ep["method"],
            ctrl_ep["url"],
            {"power": self.power_on, "target_temp": self.target_temp, "unit": self.unit, "mode": self.mode},
        )

    def _go(self, screen: str) -> None:
        if self.screen != screen:
            self._nav_stack.append(self.screen)
            self.screen = screen
        self.mount()

    def open_browser(self, url: str = "") -> None:
        if self.screen != "browser":
            self._nav_stack.append(self.screen)
            self.screen = "browser"
        target = url or (_file_url(self.spec["launch_file"]) if self.spec.get("launch_file") else "")
        if target and self._browser.current_url != target:
            self._browser.load_url(target)
        else:
            self.mount()

    def open_page_detail(self, page: Dict[str, Any]) -> None:
        self.selected_page = page
        self._go("page_detail")

    def close_browser(self) -> None:
        if self.screen == "browser":
            self.screen = self._nav_stack.pop() if self._nav_stack else "home"
        self.mount()

    def toggle_browser(self) -> None:
        if self.screen == "browser":
            self.close_browser()
        else:
            self.open_browser()

    def handle_back(self) -> bool:
        if self.screen == "browser":
            if len(self._browser.history) > 1:
                self._browser.go_back()
                return True
            self.close_browser()
            return True
        if self._nav_stack:
            self.screen = self._nav_stack.pop()
            self.mount()
            return True
        return False

    def handle_home(self) -> bool:
        if self.screen != "home":
            self._nav_stack.clear()
            self.screen = "home"
            self.mount()
            return True
        return False

    def _build_topbar(self) -> ViewNode:
        tb = ViewNode(
            "LinearLayout",
            vid="hw_topbar",
            props={"orientation": "horizontal", "role": "topbar", "bg": self.spec.get("accent_color") or "#00A8E7"},
        )
        logo = _img_node("hw_logo_iv", self.spec.get("images", {}).get("logo", ""), h=28, w=28)
        if logo is not None:
            tb.add(logo)
        tb.add(ViewNode(
            "TextView",
            vid="hw_title_tv",
            text=_short_label(self.spec.get("app_name") or "Hybrid App", 24),
            props={"layout_weight": 1.0, "gravity": 16, "bold": True},
        ))
        wbtn = ViewNode(
            "Button",
            vid="hw_open_web_btn",
            text="Web",
            clickable=True,
            props={"w": 52, "h": 28, "bg": "#1e3a5f", "color": "#ffffff"},
        )
        wbtn.on_click = lambda _v=None: self.open_browser()
        tb.add(wbtn)
        return tb

    def _build_navbar(self) -> ViewNode:
        nb = ViewNode("LinearLayout", vid="hw_navbar", props={"orientation": "horizontal", "role": "navbar"})
        tabs = [
            ("home", "Start"),
            ("pages", "Seiten"),
            ("apis", "APIs"),
            ("browser", "WebView"),
            ("about", "Info"),
        ]
        for key, lbl in tabs:
            btn = ViewNode(
                "Button",
                vid=f"hw_nav_{key}",
                text=lbl,
                clickable=True,
                props={"layout_weight": 1.0, "role": "navitem", "selected": (self.screen == key)},
            )
            if key == "browser":
                btn.on_click = lambda _v=None: self.open_browser()
            else:
                btn.on_click = lambda _v=None, _k=key: self._go(_k)
            nb.add(btn)
        return nb

    def _build_home(self, content: ViewNode) -> None:
        fw = self.spec.get("framework") or "WebView"
        lp = self.spec.get("launch_path") or "index.html"
        content.add(ViewNode(
            "TextView",
            vid="hw_runtime_badge",
            text=f"{fw}  •  {len(self.spec.get('pages', ()))} HTML-Templates  •  {lp}",
            props={"role": "subtitle", "textSize": 11},
        ))

        hero_card = ViewNode("LinearLayout", vid="hw_hero_card", props={"orientation": "vertical", "role": "card"})
        hero = _img_node("hw_hero_iv", self.spec.get("images", {}).get("hero", ""), h=86)
        if hero is not None:
            hero_card.add(hero)

        if self.spec.get("archetype") == "device_control":
            state_lbl = "Verbunden (Simuliert)" if self.power_on else "Standby (Aus)"
            hero_card.add(ViewNode(
                "TextView",
                vid="hw_device_status",
                text=f"Gerätestatus: {state_lbl}  |  Sollwert: {self.target_temp}{self.unit}  ({self.mode})",
                props={"bold": True, "textSize": 13},
            ))
            row = ViewNode("LinearLayout", vid="hw_ctrl_row", props={"orientation": "horizontal"})
            b_pwr = ViewNode(
                "Button",
                vid="hw_pwr_btn",
                text=("Ausschalten" if self.power_on else "Einschalten"),
                clickable=True,
                props={
                    "layout_weight": 1.0,
                    "bg": "#2f6df0" if self.power_on else "#d8e3ff",
                    "color": "#ffffff" if self.power_on else "#1b2230",
                },
            )
            b_pwr.on_click = lambda _v=None: (
                setattr(self, "power_on", not self.power_on),
                self._send_device_control_api(),
                self.mount(),
            )

            b_dec = ViewNode("Button", vid="hw_temp_dec", text="−", clickable=True, props={"w": 46})
            b_dec.on_click = lambda _v=None: (
                setattr(self, "target_temp", self.target_temp - 1),
                self._send_device_control_api(),
                self.mount(),
            )

            b_inc = ViewNode("Button", vid="hw_temp_inc", text="+", clickable=True, props={"w": 46})
            b_inc.on_click = lambda _v=None: (
                setattr(self, "target_temp", self.target_temp + 1),
                self._send_device_control_api(),
                self.mount(),
            )

            b_mode = ViewNode("Button", vid="hw_mode_btn", text=self.mode, clickable=True, props={"w": 66})
            b_mode.on_click = lambda _v=None: (
                setattr(self, "mode", "MAX" if self.mode == "ECO" else "ECO"),
                self._send_device_control_api(),
                self.mount(),
            )
            row.add(b_pwr)
            row.add(b_dec)
            row.add(b_inc)
            row.add(b_mode)
            hero_card.add(row)
        else:
            hero_card.add(ViewNode(
                "TextView",
                vid="hw_portal_summary",
                text=f"App: {self.spec.get('app_name')} ({self.spec.get('app_id') or 'lokal'})",
                props={"bold": True, "textSize": 13},
            ))

        launch_btn = ViewNode(
            "Button",
            vid="hw_launch_webview",
            text=f"Startseite im WebView öffnen ({os.path.basename(lp) or 'index.html'})",
            clickable=True,
            props={"bg": "#2f6df0", "color": "#ffffff"},
        )
        launch_btn.on_click = lambda _v=None: self.open_browser()
        hero_card.add(launch_btn)
        content.add(hero_card)

        # Wichtigste HTML-Ansichten als direkt klickbare Eintraege (max. 4 eindeutige, damit Navbar ins Viewport passt)
        pages = list(self.spec.get("pages") or ())
        uniq_pages: List[Dict[str, Any]] = []
        seen_keys = set()
        for pg in pages:
            k = (pg.get("title", "").lower(), pg.get("category", ""))
            if k in seen_keys:
                continue
            seen_keys.add(k)
            uniq_pages.append(pg)
            if len(uniq_pages) >= 4:
                break
        if uniq_pages:
            sec = ViewNode("LinearLayout", vid="hw_pages_quick", props={"orientation": "vertical", "role": "card"})
            sec.add(ViewNode("TextView", vid="hw_pages_hdr", text="Entdeckte Hybrid-Seiten", props={"bold": True}))
            for idx, pg in enumerate(uniq_pages):
                btn = ViewNode(
                    "Button",
                    vid=f"hw_qpage_{idx}",
                    text=f"{pg['title']}  [{pg['category']}]",
                    clickable=True,
                    props={"bg": "#e8eef9"},
                )
                btn.on_click = lambda _v=None, _pg=pg: self.open_page_detail(_pg)
                sec.add(btn)
            content.add(sec)

        if self.status_msg:
            content.add(ViewNode("TextView", vid="hw_status_msg", text=self.status_msg, props={"role": "subtitle"}))

    def _build_pages_list(self, content: ViewNode) -> None:
        pages = list(self.spec.get("pages") or ())
        content.add(ViewNode(
            "TextView",
            vid="hw_all_pages_hdr",
            text=f"Alle Web-Templates ({len(pages)})",
            props={"bold": True, "textSize": 14},
        ))
        for idx, pg in enumerate(pages[:14]):
            card = ViewNode("LinearLayout", vid=f"hw_pg_card_{idx}", props={"orientation": "vertical", "role": "card"})
            card.add(ViewNode("TextView", vid=f"hw_pg_t_{idx}", text=pg["title"], props={"bold": True}))
            card.add(ViewNode("TextView", vid=f"hw_pg_p_{idx}", text=pg["rel_path"], props={"role": "subtitle", "textSize": 10}))
            row = ViewNode("LinearLayout", vid=f"hw_pg_r_{idx}", props={"orientation": "horizontal"})
            b1 = ViewNode("Button", vid=f"hw_pg_det_{idx}", text="Details", clickable=True, props={"layout_weight": 1.0, "bg": "#e8eef9"})
            b1.on_click = lambda _v=None, _pg=pg: self.open_page_detail(_pg)
            b2 = ViewNode("Button", vid=f"hw_pg_web_{idx}", text="Im WebView öffnen", clickable=True, props={"layout_weight": 1.0, "bg": "#2f6df0", "color": "#ffffff"})
            b2.on_click = lambda _v=None, _u=_file_url(pg["abs_path"]): self.open_browser(_u)
            row.add(b1)
            row.add(b2)
            card.add(row)
            content.add(card)

    def _build_page_detail(self, content: ViewNode) -> None:
        pg = self.selected_page or (self.spec.get("pages") or [{}])[0]
        title = pg.get("title") or "Web-Seite"
        rel = pg.get("rel_path") or ""
        card = ViewNode("LinearLayout", vid="hw_detail_card", props={"orientation": "vertical", "role": "card"})
        card.add(ViewNode("TextView", vid="hw_det_title", text=title, props={"bold": True, "textSize": 15}))
        if rel:
            card.add(ViewNode("TextView", vid="hw_det_path", text=rel, props={"role": "subtitle"}))
        for key, label in (("headings", "Überschriften"), ("inputs", "Eingabefelder"), ("buttons", "Buttons"), ("labels", "Texte / i18n")):
            vals = list(pg.get(key) or ())
            if vals:
                card.add(ViewNode(
                    "TextView",
                    vid=f"hw_det_{key}",
                    text=f"{label}: {', '.join(vals[:6])}",
                    props={"textSize": 11},
                ))
        if pg.get("abs_path"):
            ob = ViewNode("Button", vid="hw_det_open", text="Diese HTML-Seite im WebView rendern", clickable=True, props={"bg": "#2f6df0", "color": "#ffffff"})
            ob.on_click = lambda _v=None: self.open_browser(_file_url(pg["abs_path"]))
            card.add(ob)
        bb = ViewNode("Button", vid="hw_det_back", text="Zurück", clickable=True, props={"bg": "#e8eef9"})
        bb.on_click = lambda _v=None: self.handle_back()
        card.add(bb)
        content.add(card)

    def _build_about(self, content: ViewNode) -> None:
        card = ViewNode("LinearLayout", vid="hw_about_card", props={"orientation": "vertical", "role": "card"})
        card.add(ViewNode("TextView", vid="hw_ab_hdr", text="Hybrid-Runtime & Plugins", props={"bold": True, "textSize": 14}))
        meta = [
            f"Framework: {self.spec.get('framework')}",
            f"App-ID: {self.spec.get('app_id') or '-'}",
            f"Version: {self.spec.get('version') or '-'}",
            f"Launch: {self.spec.get('launch_path') or '-'}",
            f"i18n-Keys: {len(self.spec.get('i18n') or {})}",
        ]
        for idx, line in enumerate(meta):
            card.add(ViewNode("TextView", vid=f"hw_ab_m_{idx}", text=line, props={"textSize": 11}))
        perms = list(self.spec.get("permissions") or ())
        if perms:
            card.add(ViewNode(
                "TextView",
                vid="hw_ab_perms",
                text="Plugins / Features: " + ", ".join(perms[:16]),
                props={"role": "subtitle", "textSize": 10},
            ))
        content.add(card)

        urls = list(self.spec.get("urls") or ())
        if urls:
            uc = ViewNode("LinearLayout", vid="hw_urls_card", props={"orientation": "vertical", "role": "card"})
            uc.add(ViewNode("TextView", vid="hw_urls_hdr", text=f"Entdeckte URLs ({len(urls)})", props={"bold": True}))
            for idx, u in enumerate(urls[:6]):
                ub = ViewNode("Button", vid=f"hw_url_{idx}", text=_short_label(u, 38), clickable=True, props={"bg": "#e8eef9"})
                ub.on_click = lambda _v=None, _u=u: self.open_browser(_u)
                uc.add(ub)
            content.add(uc)

    def _build_apis_screen(self, content: ViewNode) -> None:
        eps = list(self.spec.get("api_endpoints") or ())
        hdr = ViewNode("LinearLayout", vid="hw_apis_hdr_card", props={"orientation": "vertical", "role": "card"})
        hdr.add(ViewNode(
            "TextView",
            vid="hw_apis_title",
            text=f"Entdeckte Hybrid-Web APIs ({len(eps)})",
            props={"bold": True, "textSize": 14},
        ))
        hdr.add(ViewNode(
            "TextView",
            vid="hw_apis_hint",
            text="Klick sendet den Request ueber net_fire (Mock/Replay oder Live-HTTP) direkt in den Netz-Reiter.",
            props={"role": "subtitle", "textSize": 10},
        ))
        if self.status_msg:
            hdr.add(ViewNode("TextView", vid="hw_apis_status", text=self.status_msg, props={"bold": True, "textSize": 11}))
        content.add(hdr)

        if not eps:
            content.add(ViewNode("TextView", vid="hw_apis_empty", text="Keine REST-Routen im Web-Bundle gefunden.", props={"role": "subtitle"}))
            return

        for idx, ep in enumerate(eps[:12]):
            card = ViewNode("LinearLayout", vid=f"hw_ep_card_{idx}", props={"orientation": "vertical", "role": "card"})
            card.add(ViewNode(
                "TextView",
                vid=f"hw_ep_lbl_{idx}",
                text=f"{ep['method']}  {ep['path']}  [{ep['category']}]",
                props={"bold": True, "textSize": 11},
            ))
            card.add(ViewNode("TextView", vid=f"hw_ep_url_{idx}", text=_short_label(ep["url"], 44), props={"role": "subtitle", "textSize": 10}))
            btn = ViewNode(
                "Button",
                vid=f"hw_ep_send_{idx}",
                text="▶ Request senden",
                clickable=True,
                props={"bg": "#2f6df0", "color": "#ffffff"},
            )
            btn.on_click = lambda _v=None, _ep=ep: (
                self._fire_web_api(_ep["method"], _ep["url"], {"app_id": self.spec.get("app_id") or ""}),
                self.mount(),
            )
            card.add(btn)
            content.add(card)

    def mount(self) -> ViewNode:
        self.tree.dark_mode = False
        self.tree.window_bg = "#f4f6fb"
        self.tree.window_bg_dark = False
        self.tree.root = None
        root = ViewNode("LinearLayout", vid="hw_root", props={"orientation": "vertical", "bg": "#f4f6fb"})
        root.add(self._build_topbar())

        if self.screen == "browser":
            body = self._browser.build_browser_nodes()
            body.props["layout_weight"] = 1.0
            root.add(body)
        else:
            scroll = ViewNode("ScrollView", vid="hw_scroll", props={"layout_weight": 1.0})
            content = ViewNode("LinearLayout", vid="hw_content", props={"orientation": "vertical"})
            scroll.add(content)
            if self.screen == "pages":
                self._build_pages_list(content)
            elif self.screen == "page_detail":
                self._build_page_detail(content)
            elif self.screen == "apis":
                self._build_apis_screen(content)
            elif self.screen == "about":
                self._build_about(content)
            else:
                self._build_home(content)
            root.add(scroll)

        root.add(self._build_navbar())
        self.tree.set_root(root)
        return root


def mount_hybrid_web_app_into_tree(
    tree: ViewTree,
    app_root: str,
    *,
    stubs: Any = None,
    on_async_update: Optional[Callable[[], None]] = None,
) -> HybridWebInPhoneController:
    """Analysiert eine Hybrid-Web-App und montiert den Controller direkt in ``tree``."""
    existing = getattr(tree, "_hybrid_ctrl", None)
    if isinstance(existing, HybridWebInPhoneController) and existing.app_root == (app_root or ""):
        if stubs is not None:
            existing.stubs = stubs
            existing._browser.stubs = stubs
        if on_async_update is not None:
            existing.set_async_updater(on_async_update)
        existing._trigger_initial_api_sync()
        existing.mount()
        return existing

    ctrl = HybridWebInPhoneController(
        tree=tree,
        app_root=app_root,
        stubs=stubs,
        on_async_update=on_async_update,
    )
    tree._hybrid_ctrl = ctrl
    ctrl._trigger_initial_api_sync()
    ctrl.mount()
    return ctrl


def hybrid_web_producer(ctx: Optional[Dict[str, Any]] = None) -> SceneGraphSurface:
    """ProducerRegistry-Fabrik für Hybrid-Web-Apps (DCloud, Cordova, Capacitor, WebView)."""
    ctx = ctx if isinstance(ctx, dict) else {}
    app_root = str(ctx.get("app_root") or "").strip()
    tree = ctx.get("tree")
    if not isinstance(tree, ViewTree):
        tree = ViewTree(width=380, height=776)
    stubs = ctx.get("stubs")
    mount_hybrid_web_app_into_tree(tree, app_root, stubs=stubs)
    return SceneGraphSurface(tree)
