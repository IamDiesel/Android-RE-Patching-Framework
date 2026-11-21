# -*- coding: utf-8 -*-
"""core.java.webview_bridge — G4: Interaktives Inline-WebView DOM, CookieManager & @JavascriptInterface."""
from __future__ import annotations

from html.parser import HTMLParser
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urljoin, urlparse

from core.java.objects import JObject, JString

_JS_CALL_RE = re.compile(r"^\s*(?:return\s+)?([A-Za-z_]\w*)\.([A-Za-z_]\w*)\((.*)\)\s*;?\s*$", re.DOTALL)
_DOM_GET_VAL_RE = re.compile(
    r"""^\s*(?:return\s+)?document\.(?:getElementById|querySelector)\(\s*['"]([#.\[\]=A-Za-z0-9_-]+)['"]\s*\)\.value\s*;?\s*$"""
)
_DOM_SET_VAL_RE = re.compile(
    r"""^\s*document\.(?:getElementById|querySelector)\(\s*['"]([#.\[\]=A-Za-z0-9_-]+)['"]\s*\)\.value\s*=\s*(['"])(.*?)\2\s*;?\s*$""",
    re.DOTALL,
)


def _s(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, JString):
        return v.text or ""
    if isinstance(v, JObject) and "text" in v.fields:
        return str(v.fields["text"] or "")
    return str(v)


def _domain_key(url: str) -> str:
    u = str(url or "").strip()
    if "://" in u:
        return (urlparse(u).netloc or "").split(":", 1)[0].lower() or "default"
    return u.split("/", 1)[0].split(":", 1)[0].lower() or "default"


def _cookie_store(stubs: Any) -> Dict[str, Dict[str, str]]:
    return stubs.__dict__.setdefault("_webview_cookies", {})


def set_cookie(stubs: Any, url: str, value: str) -> None:
    dom = _domain_key(url)
    raw = str(value or "").split(";", 1)[0].strip()
    if "=" in raw:
        k, v = raw.split("=", 1)
        _cookie_store(stubs).setdefault(dom, {})[k.strip()] = v.strip()


def get_cookie_header(stubs: Any, url: str) -> str:
    dom = _domain_key(url)
    store = _cookie_store(stubs)
    merged: Dict[str, str] = {}
    for sdom, kv in store.items():
        if dom == sdom or dom.endswith("." + sdom) or sdom.endswith("." + dom):
            merged.update(kv)
    return "; ".join(f"{k}={v}" for k, v in merged.items())


def install_webview_grundset(stubs: Any, tree: Any = None) -> None:
    """Registriert CookieManager- und WebViewDatabase-Stubs fuer G4."""
    if stubs is None or getattr(stubs, "_g4_installed", False):
        return
    stubs._g4_installed = True
    CM = "Landroid/webkit/CookieManager;"
    cm_inst = JObject(CM, {})
    stubs.register(CM, "getInstance", "()Landroid/webkit/CookieManager;", lambda a: cm_inst)
    stubs.register(CM, "setCookie", "(Ljava/lang/String;Ljava/lang/String;)V",
                   lambda a: set_cookie(stubs, _s(a[1]), _s(a[2])) if len(a) > 2 else None)
    stubs.register(CM, "setCookie", "(Ljava/lang/String;Ljava/lang/String;Landroid/webkit/ValueCallback;)V",
                   lambda a: set_cookie(stubs, _s(a[1]), _s(a[2])) if len(a) > 2 else None)
    stubs.register(CM, "getCookie", "(Ljava/lang/String;)Ljava/lang/String;",
                   lambda a: (lambda c: JString(c) if c else None)(get_cookie_header(stubs, _s(a[1]) if len(a) > 1 else "")))
    stubs.register(CM, "setAcceptCookie", "(Z)V", lambda a: None)
    stubs.register(CM, "setAcceptThirdPartyCookies", "(Landroid/webkit/WebView;Z)V", lambda a: None)
    stubs.register(CM, "removeAllCookies", "(Landroid/webkit/ValueCallback;)V",
                   lambda a: (_cookie_store(stubs).clear(), None)[1])
    stubs.register(CM, "removeAllCookie", "()V",
                   lambda a: (_cookie_store(stubs).clear(), None)[1])
    stubs.register(CM, "hasCookies", "()Z",
                   lambda a: 1 if any(_cookie_store(stubs).values()) else 0)
    stubs.register(CM, "flush", "()V", lambda a: None)

    WS = "Landroid/webkit/WebSettings;"
    stubs.register(WS, "setJavaScriptEnabled", "(Z)V",
                   lambda a: a[0].fields.__setitem__("$js", bool(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    stubs.register(WS, "getJavaScriptEnabled", "()Z",
                   lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("$js", True)) else 0)
    stubs.register(WS, "setDomStorageEnabled", "(Z)V",
                   lambda a: a[0].fields.__setitem__("$dom", bool(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    stubs.register(WS, "getDomStorageEnabled", "()Z",
                   lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("$dom", True)) else 0)
    stubs.register(WS, "setUserAgentString", "(Ljava/lang/String;)V",
                   lambda a: a[0].fields.__setitem__("$ua", _s(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    stubs.register(WS, "getUserAgentString", "()Ljava/lang/String;",
                   lambda a: JString(a[0].fields.get("$ua", "Mozilla/5.0 (Linux; Android 14) Chrome/124.0 Mobile") if a and isinstance(a[0], JObject) else "Mozilla/5.0"))
    stubs.register(WS, "setSupportZoom", "(Z)V", lambda a: None)
    stubs.register(WS, "setLoadWithOverviewMode", "(Z)V", lambda a: None)
    stubs.register(WS, "setUseWideViewPort", "(Z)V", lambda a: None)


class _InteractiveDomParser(HTMLParser):
    """Extrahiert Formulare, Inputs, Buttons und Links fuer das Inline-WebView-DOM."""

    def __init__(self, base_url: str = ""):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.forms: List[Dict[str, Any]] = []
        self.elements: List[Dict[str, Any]] = []
        self._cur_form: Optional[Dict[str, Any]] = None
        self._cur_btn: Optional[Dict[str, Any]] = None
        self._cur_link: Optional[Dict[str, Any]] = None
        self._cur_ta: Optional[Dict[str, Any]] = None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        t = tag.lower()
        amap = {str(k).lower(): (v if v is not None else "") for k, v in attrs}
        if t == "form":
            self._cur_form = {
                "action": urljoin(self.base_url, amap.get("action", "") or "") if self.base_url else amap.get("action", ""),
                "method": (amap.get("method") or "GET").upper(),
                "fields": [],
            }
            self.forms.append(self._cur_form)
        elif t == "input":
            itype = (amap.get("type") or "text").lower()
            name = amap.get("name") or amap.get("id") or ""
            vid = amap.get("id") or name or f"inp_{len(self.elements)}"
            if itype in ("submit", "button"):
                el = {
                    "kind": "button",
                    "id": vid,
                    "label": amap.get("value") or "Submit",
                    "onclick": amap.get("onclick") or "",
                    "form": self._cur_form,
                }
                self.elements.append(el)
            elif itype != "hidden":
                el = {
                    "kind": "input",
                    "id": vid,
                    "name": name or vid,
                    "type": itype,
                    "value": amap.get("value") or "",
                    "placeholder": amap.get("placeholder") or name or vid,
                }
                self.elements.append(el)
                if self._cur_form is not None and name:
                    self._cur_form["fields"].append(name)
            elif self._cur_form is not None and name:
                self._cur_form.setdefault("hidden", {})[name] = amap.get("value") or ""
        elif t == "textarea":
            vid = amap.get("id") or amap.get("name") or f"ta_{len(self.elements)}"
            self._cur_ta = {
                "kind": "input",
                "id": vid,
                "name": amap.get("name") or vid,
                "type": "text",
                "value": "",
                "placeholder": amap.get("placeholder") or vid,
            }
        elif t == "button":
            self._cur_btn = {
                "kind": "button",
                "id": amap.get("id") or amap.get("name") or f"btn_{len(self.elements)}",
                "label": "",
                "onclick": amap.get("onclick") or "",
                "type": (amap.get("type") or "submit").lower(),
                "form": self._cur_form,
            }
        elif t == "a":
            href = amap.get("href") or ""
            full_href = urljoin(self.base_url, href) if (self.base_url and href and not href.startswith("javascript:")) else href
            self._cur_link = {
                "kind": "link",
                "href": full_href,
                "onclick": amap.get("onclick") or "",
                "label": "",
            }

    def handle_data(self, data: str) -> None:
        if self._cur_btn is not None:
            self._cur_btn["label"] += data
        if self._cur_link is not None:
            self._cur_link["label"] += data
        if self._cur_ta is not None:
            self._cur_ta["value"] += data

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "button" and self._cur_btn is not None:
            self._cur_btn["label"] = self._cur_btn["label"].strip() or "Button"
            self.elements.append(self._cur_btn)
            self._cur_btn = None
        elif t == "a" and self._cur_link is not None:
            self._cur_link["label"] = self._cur_link["label"].strip() or self._cur_link["href"]
            if self._cur_link["label"]:
                self.elements.append(self._cur_link)
            self._cur_link = None
        elif t == "textarea" and self._cur_ta is not None:
            self._cur_ta["value"] = self._cur_ta["value"].strip()
            self.elements.append(self._cur_ta)
            self._cur_ta = None
        elif t == "form":
            self._cur_form = None


def _split_js_args(raw: str) -> List[Any]:
    txt = (raw or "").strip()
    if not txt:
        return []
    parts: List[str] = []
    cur: List[str] = []
    q: Optional[str] = None
    esc = False
    depth = 0
    for ch in txt:
        if esc:
            cur.append(ch)
            esc = False
            continue
        if ch == "\\" and q is not None:
            cur.append(ch)
            esc = True
            continue
        if ch in ("'", '"'):
            if q is None:
                q = ch
            elif q == ch:
                q = None
            cur.append(ch)
            continue
        if q is None:
            if ch in ("(", "[", "{"):
                depth += 1
            elif ch in (")", "]", "}"):
                depth = max(0, depth - 1)
            elif ch == "," and depth == 0:
                parts.append("".join(cur).strip())
                cur = []
                continue
        cur.append(ch)
    if cur:
        parts.append("".join(cur).strip())

    out: List[Any] = []
    for p in parts:
        if len(p) >= 2 and ((p[0] == "'" and p[-1] == "'") or (p[0] == '"' and p[-1] == '"')):
            out.append(p[1:-1])
        elif p == "true":
            out.append(True)
        elif p == "false":
            out.append(False)
        elif p in ("null", "undefined"):
            out.append(None)
        else:
            try:
                out.append(float(p) if "." in p else int(p))
            except Exception:
                out.append(p)
    return out


def _count_sig_params(sig: str) -> int:
    inner = sig[sig.index("(") + 1:sig.index(")")] if "(" in sig else ""
    p_cnt = 0
    i = 0
    while i < len(inner):
        c = inner[i]
        if c == "L":
            i = inner.index(";", i) + 1
            p_cnt += 1
        elif c == "[":
            i += 1
        else:
            i += 1
            p_cnt += 1
    return p_cnt


def _find_bridge_method(vm: Any, cls_desc: str, mname: str, argc: int) -> Optional[str]:
    if vm is None:
        return None
    cp = getattr(vm, "class_provider", None)
    reg = getattr(vm, "registry", None) or {}
    cur = cls_desc
    seen = set()
    while cur and cur not in seen:
        seen.add(cur)
        cf = getattr(cp, "get", lambda _c: None)(cur) if cp is not None else None
        if cf is not None:
            for (nm, sig), _ in (getattr(cf, "methods", {}) or {}).items():
                if nm == mname and _count_sig_params(sig) == argc:
                    return sig
        for (rc, rnm, rsig) in reg.keys():
            if rc == cur and rnm == mname and _count_sig_params(rsig) == argc:
                return rsig
        cur = vm.super_of(cur) if hasattr(vm, "super_of") else None
    return None


def _to_dalvik_arg(val: Any, ptype: str) -> Any:
    if val is None:
        return None
    if ptype == "Ljava/lang/String;":
        return JString(str(val))
    if ptype == "Z":
        return 1 if val else 0
    if ptype in ("I", "J", "S", "B"):
        try:
            return int(val)
        except Exception:
            return 0
    if ptype in ("F", "D"):
        try:
            return float(val)
        except Exception:
            return 0.0
    if isinstance(val, str):
        return JString(val)
    return val


def _py_from_dalvik(v: Any) -> Any:
    if isinstance(v, JString):
        return v.text
    if isinstance(v, JObject) and v.cls == "Ljava/lang/String;":
        return str(v.fields.get("value", ""))
    return v


def _sig_types_list(sig: str) -> List[str]:
    inner = sig[sig.index("(") + 1:sig.index(")")] if "(" in sig else ""
    out: List[str] = []
    i = 0
    while i < len(inner):
        st = i
        while i < len(inner) and inner[i] == "[":
            i += 1
        if i < len(inner) and inner[i] == "L":
            i = inner.index(";", i) + 1
        elif i < len(inner):
            i += 1
        out.append(inner[st:i])
    return out


def eval_webview_js(
    stubs: Any,
    vm: Any,
    tree: Any,
    wv_node: Any,
    recv: Any,
    js_code: str,
    value_cb: Any = None,
) -> Any:
    """Wertet haeufige DOM- und @JavascriptInterface-Ausdruecke deterministisch aus."""
    code = str(js_code or "").strip()
    if code.startswith("javascript:"):
        code = code[len("javascript:"):].strip()
    dom_inputs = wv_node.props.setdefault("wv_dom_inputs", {}) if wv_node is not None else {}
    lstore = stubs.__dict__.setdefault("_web_local_storage", {}) if stubs is not None else {}
    res: Any = None

    m_ls_set = re.match(r"""^\s*localStorage\.setItem\(\s*(['"])(.*?)\1\s*,\s*(['"])(.*?)\3\s*\)\s*;?\s*$""", code, re.DOTALL)
    m_ls_get = re.match(r"""^\s*(?:return\s+)?localStorage\.getItem\(\s*(['"])(.*?)\1\s*\)\s*;?\s*$""", code)
    m_ls_rm = re.match(r"""^\s*localStorage\.removeItem\(\s*(['"])(.*?)\1\s*\)\s*;?\s*$""", code)

    if m_ls_set:
        lstore[m_ls_set.group(2)] = m_ls_set.group(4)
        res = m_ls_set.group(4)
    elif m_ls_get:
        res = lstore.get(m_ls_get.group(2))
    elif m_ls_rm:
        lstore.pop(m_ls_rm.group(2), None)
        res = None
    elif (m_set := _DOM_SET_VAL_RE.match(code)):
        key = m_set.group(1).lstrip("#.")
        val = m_set.group(3)
        dom_inputs[key] = val
        if wv_node is not None:
            for ch in wv_node.walk():
                if ch.cls == "EditText" and (ch.vid == f"wv_inp_{key}" or ch.props.get("$dom_name") == key):
                    ch.text = val
            if tree is not None:
                tree.touch()
        res = val
    elif (m_get := _DOM_GET_VAL_RE.match(code)):
        key = m_get.group(1).lstrip("#.")
        res = dom_inputs.get(key, "")
    elif code in ("document.title", "return document.title"):
        if wv_node is not None:
            t_nd = wv_node.find("wv_title")
            res = t_nd.text if t_nd is not None else ""
    elif code in ("window.location.href", "location.href", "return window.location.href", "return location.href"):
        res = (wv_node.props.get("web_url") if wv_node is not None else "") or ""
    else:
        m_call = _JS_CALL_RE.match(code)
        if m_call:
            if_name, m_name, raw_args = m_call.groups()
            ifaces = {}
            if isinstance(recv, JObject):
                ifaces.update(recv.fields.get("$js_interfaces") or {})
            if not ifaces and wv_node is not None:
                ifaces.update(wv_node.props.get("$js_interfaces") or {})
            ifaces.update(getattr(stubs, "_wv_js_interfaces", {}) or {})
            bridge_obj = ifaces.get(if_name)
            if isinstance(bridge_obj, JObject) and vm is not None:
                py_args = _split_js_args(raw_args)
                msig = _find_bridge_method(vm, bridge_obj.cls, m_name, len(py_args))
                if msig:
                    ptypes = _sig_types_list(msig)
                    dargs = [_to_dalvik_arg(a, t) for a, t in zip(py_args, ptypes)]
                    try:
                        raw_ret = vm.invoke_virtual(bridge_obj, m_name, msig, tuple([bridge_obj] + dargs))
                        res = _py_from_dalvik(raw_ret)
                    except Exception:
                        res = None

    if isinstance(value_cb, JObject) and vm is not None and hasattr(vm, "invoke_virtual"):
        try:
            j_s = json.dumps(res) if res is not None else "null"
            vm.invoke_virtual(value_cb, "onReceiveValue", "(Ljava/lang/Object;)V", (value_cb, JString(j_s)))
        except Exception:
            pass
    return res


def render_html_into_webview(
    stubs: Any,
    vm: Any,
    tree: Any,
    wv_node: Any,
    recv: Any,
    url: str,
    html_text: str,
    ViewNode: Any,
) -> None:
    """Rendert HTML als interaktive ViewNodes (Titel, Texte, Inputs, Buttons, Links) in `wv_node`."""
    if wv_node is None or not html_text:
        return
    try:
        from services.webview_inspector_service import _DomInspectorParser
        prs = _DomInspectorParser(base_url=url)
        prs.feed(html_text)
        prs.close()
    except Exception:
        prs = None

    dprs = _InteractiveDomParser(base_url=url)
    try:
        dprs.feed(html_text)
        dprs.close()
    except Exception:
        pass

    wv_node.children.clear()
    dom_inputs = wv_node.props.setdefault("wv_dom_inputs", {})
    if prs is not None and prs.title:
        wv_node.add(ViewNode("TextView", vid="wv_title", text=prs.title))
    if prs is not None:
        for bi, (_tg, btxt) in enumerate(prs.text_blocks[:6]):
            wv_node.add(ViewNode("TextView", vid=f"wv_txt_{bi}", text=btxt[:200]))

    def _navigate_url(next_u: str, method: str = "GET", form_body: bytes = b"") -> None:
        if not next_u:
            return
        if next_u.startswith("javascript:"):
            eval_webview_js(stubs, vm, tree, wv_node, recv, next_u[11:])
            return
        kind = "webview_postUrl" if method == "POST" else "webview_loadUrl"
        rec_fn = getattr(stubs, "_record_web_event", None)
        if callable(rec_fn):
            rec_fn(kind, next_u, node=wv_node)
        else:
            load_into_webview(stubs, vm, tree, wv_node, recv, next_u, ViewNode, method=method, body=form_body)

    for el in dprs.elements[:16]:
        ek = el.get("kind")
        if ek == "input":
            nm = str(el.get("name") or el.get("id") or "field")
            init_v = str(el.get("value") or "")
            if init_v:
                dom_inputs[nm] = init_v
            ed = ViewNode(
                "EditText",
                vid=f"wv_inp_{nm}",
                text=dom_inputs.get(nm, init_v),
                clickable=True,
                focusable=True,
                props={
                    "hint": el.get("placeholder") or nm,
                    "$dom_name": nm,
                    "h": 36,
                    "$on_change_fn": lambda val, _k=nm, _d=dom_inputs: _d.__setitem__(_k, str(val)),
                },
            )
            wv_node.add(ed)
        elif ek == "button":
            lbl = str(el.get("label") or "Submit")
            onclick = str(el.get("onclick") or "")
            form = el.get("form")
            def _on_btn(_v=None, _oc=onclick, _fm=form):
                if _oc:
                    eval_webview_js(stubs, vm, tree, wv_node, recv, _oc)
                    return
                if _fm is not None:
                    payload = dict(_fm.get("hidden") or {})
                    for fnm in (_fm.get("fields") or ()):
                        payload[fnm] = dom_inputs.get(fnm, "")
                    action = _fm.get("action") or url
                    meth = (_fm.get("method") or "GET").upper()
                    if meth == "POST":
                        _navigate_url(action, method="POST", form_body=urlencode(payload).encode("utf-8"))
                    else:
                        sep = "&" if "?" in action else "?"
                        _navigate_url(f"{action}{sep}{urlencode(payload)}" if payload else action, method="GET")
            btn_nd = ViewNode("Button", vid=f"wv_btn_{el.get('id')}", text=lbl, clickable=True, props={"h": 36})
            btn_nd.on_click = _on_btn
            wv_node.add(btn_nd)
        elif ek == "link":
            href = str(el.get("href") or "")
            lbl = str(el.get("label") or href)
            oc = str(el.get("onclick") or "")
            def _on_link(_v=None, _h=href, _oc=oc):
                if _oc:
                    eval_webview_js(stubs, vm, tree, wv_node, recv, _oc)
                elif _h:
                    _navigate_url(_h, method="GET")
            lnk_nd = ViewNode(
                "TextView",
                vid=f"wv_lnk_{len(wv_node.children)}",
                text=lbl[:120],
                clickable=bool(href or oc),
                props={"color": "#2563eb", "h": 28},
            )
            if href or oc:
                lnk_nd.on_click = _on_link
            wv_node.add(lnk_nd)
    if tree is not None:
        tree.touch()


def load_into_webview(
    stubs: Any,
    vm: Any,
    tree: Any,
    wv_node: Any,
    recv: Any,
    url: str,
    ViewNode: Any,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    body: bytes = b"",
    app_root: Optional[str] = None,
) -> Optional[str]:
    """Laedt `http(s)` ueber `stubs.net_fire` (falls aktiv) oder Assets und rendert HTML in `wv_node`."""
    u = str(url or "")
    if u.startswith("javascript:"):
        eval_webview_js(stubs, vm, tree, wv_node, recv, u[11:])
        return None
    html_out = ""
    if u.startswith(("http://", "https://")) and callable(getattr(stubs, "net_fire", None)):
        hdrs = dict(headers or {})
        ck = get_cookie_header(stubs, u)
        if ck and "Cookie" not in hdrs and "cookie" not in hdrs:
            hdrs["Cookie"] = ck
        try:
            resp = stubs.net_fire(method, u, hdrs, body or b"")
            rhdrs = getattr(resp, "headers", None) or {}
            for hk, hv in rhdrs.items():
                if str(hk).lower() == "set-cookie" and hv:
                    set_cookie(stubs, u, str(hv))
            rbody = getattr(resp, "body", b"") or b""
            if isinstance(rbody, (bytes, bytearray)) and rbody.strip():
                txt = bytes(rbody).decode("utf-8", errors="replace")
                if "<" in txt and ">" in txt:
                    html_out = txt
        except Exception:
            html_out = ""
    elif u.startswith("file:///android_asset/") and app_root:
        rel = u[len("file:///android_asset/"):].lstrip("/")
        cand = os.path.join(app_root, "assets", rel.replace("/", os.sep))
        if os.path.isfile(cand):
            try:
                with open(cand, "r", encoding="utf-8", errors="replace") as fh:
                    html_out = fh.read()
            except Exception:
                html_out = ""
    if html_out and wv_node is not None:
        render_html_into_webview(stubs, vm, tree, wv_node, recv, u, html_out, ViewNode)
    return html_out


def handle_webview_vhook(
    stubs: Any,
    vm: Any,
    tree: Any,
    recv: Any,
    cls: str,
    name: str,
    sig: str,
    logical: tuple,
    text_fn: Any,
    record_web_event: Any,
    ViewNode: Any,
    app_root: Optional[str] = None,
) -> Tuple[bool, Any]:
    """Behandelt WebView-Methoden im DalvikVM `_vhook` inkl. JS-Bridge, Cookies & live Net."""
    if not (cls == "Landroid/webkit/WebView;" or recv.cls == "Landroid/webkit/WebView;"
            or getattr(recv.fields.get("$view"), "cls", "") == "WebView"):
        return False, None
    wv_node = recv.fields.get("$view")
    hist = recv.fields.setdefault("$wv_history", [])
    if name == "loadUrl" and len(logical) > 1:
        u = text_fn(logical[1])
        if u and not u.startswith("javascript:"):
            if not hist or hist[-1] != u:
                hist.append(u)
        record_web_event("webview_loadUrl", u, node=wv_node)
        hdrs: Dict[str, str] = {}
        if len(logical) > 2 and isinstance(logical[2], JObject):
            raw_m = logical[2].fields.get("map") or {}
            for k, v in raw_m.items():
                hdrs[str(k)] = text_fn(v)
        load_into_webview(stubs, vm, tree, wv_node, recv, u, ViewNode, method="GET", headers=hdrs, app_root=app_root)
        return True, None
    if name in ("loadData", "loadDataWithBaseURL") and len(logical) > 1:
        u = text_fn(logical[1]) if name == "loadDataWithBaseURL" else "data:text/html"
        html_s = text_fn(logical[2] if name == "loadDataWithBaseURL" and len(logical) > 2 else logical[1])
        if u and (not hist or hist[-1] != u):
            hist.append(u)
        record_web_event("webview_loadData", u or "data:text/html", extra={"html": html_s}, node=wv_node)
        if html_s and wv_node is not None:
            render_html_into_webview(stubs, vm, tree, wv_node, recv, u or "data:text/html", html_s, ViewNode)
        return True, None
    if name == "postUrl" and len(logical) > 1:
        u = text_fn(logical[1])
        post_b = bytes(int(x) & 0xFF for x in (getattr(logical[2], "data", None) or ())) if len(logical) > 2 else b""
        if u and (not hist or hist[-1] != u):
            hist.append(u)
        record_web_event("webview_postUrl", u, node=wv_node)
        load_into_webview(stubs, vm, tree, wv_node, recv, u, ViewNode, method="POST", body=post_b, app_root=app_root)
        return True, None
    if name == "canGoBack":
        return True, 1 if len(hist) > 1 else 0
    if name == "goBack":
        if len(hist) > 1:
            hist.pop()
            prev_u = hist[-1]
            record_web_event("webview_goBack", prev_u, node=wv_node)
            load_into_webview(stubs, vm, tree, wv_node, recv, prev_u, ViewNode, method="GET", app_root=app_root)
        return True, None
    if name == "reload":
        if hist:
            cur_u = hist[-1]
            record_web_event("webview_reload", cur_u, node=wv_node)
            load_into_webview(stubs, vm, tree, wv_node, recv, cur_u, ViewNode, method="GET", app_root=app_root)
        return True, None
    if name == "evaluateJavascript" and len(logical) > 1:
        js_s = text_fn(logical[1])
        cb = logical[2] if len(logical) > 2 else None
        record_web_event("webview_evalJs", "", extra={"js": js_s})
        eval_webview_js(stubs, vm, tree, wv_node, recv, js_s, value_cb=cb)
        return True, None
    if name == "addJavascriptInterface" and len(logical) > 2:
        iface_obj = logical[1]
        iface_nm = text_fn(logical[2])
        if iface_nm and isinstance(iface_obj, JObject):
            recv.fields.setdefault("$js_interfaces", {})[iface_nm] = iface_obj
            if wv_node is not None:
                wv_node.props.setdefault("$js_interfaces", {})[iface_nm] = iface_obj
            stubs.__dict__.setdefault("_wv_js_interfaces", {})[iface_nm] = iface_obj
        return True, None
    if name == "getSettings":
        ws_obj = recv.fields.get("$wv_settings")
        if not isinstance(ws_obj, JObject):
            ws_obj = JObject("Landroid/webkit/WebSettings;", {"$ua": "Mozilla/5.0 (Linux; Android 14) Chrome/124.0 Mobile", "$js": True, "$dom": True})
            recv.fields["$wv_settings"] = ws_obj
        return True, ws_obj
    if name in ("setWebViewClient", "setWebChromeClient") and len(logical) > 1:
        recv.fields["$wv_client" if "View" in name else "$wc_client"] = logical[1]
        return True, None
    if name == "getUrl":
        u = (wv_node.props.get("web_url") if wv_node is not None else "") or ""
        return True, JString(u) if u else None
    if name == "getTitle":
        t_nd = wv_node.find("wv_title") if wv_node is not None else None
        return True, JString(t_nd.text) if (t_nd is not None and t_nd.text) else None
    return False, None
