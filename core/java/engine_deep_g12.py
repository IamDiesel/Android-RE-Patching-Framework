# -*- coding: utf-8 -*-
"""G12: Engine-Vertiefungen — Flutter libapp.so ELF64/AOT-Deep-Analyzer & Interaktive RN-JS-Session.

Erweitert Flutter-AOT-Analyse (ELF64-Sektionen, .dynsym/_kDart*-Symbole, Dart-Packages,
PascalCase-Widget/Bloc-Klassen, GraphQL/REST/Channel-Strings) und React-Native
um eine interaktive Multi-Turn-Session (Stateful Mount -> Tap -> ChangeText -> Fetch -> ViewTree)
mit deterministischem Python-Fallback unter Windows sowie optionalem Node-Host.
"""
from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import tempfile
from typing import Any, Callable, Dict, List, Optional, Tuple


_K_DART_NAMES = (
    "_kDartVmSnapshotData",
    "_kDartVmSnapshotInstructions",
    "_kDartIsolateSnapshotData",
    "_kDartIsolateSnapshotInstructions",
)


def _read_cstr_bytes(buf: bytes, offset: int, limit: int = 256) -> str:
    if offset < 0 or offset >= len(buf):
        return ""
    end = buf.find(b"\x00", offset, min(len(buf), offset + limit))
    if end == -1:
        end = min(len(buf), offset + limit)
    return buf[offset:end].decode("utf-8", errors="replace")


def _parse_elf64_headers(blob: bytes) -> Tuple[bool, int, List[str], List[str]]:
    """Parst ELF64-Header, Section-Namen und .dynsym-Symbole (defensiv)."""
    if len(blob) < 64 or blob[:4] != b"\x7fELF" or blob[4] != 2 or blob[5] != 1:
        return False, 0, [], []
    machine = struct.unpack_from("<H", blob, 18)[0]
    e_shoff = struct.unpack_from("<Q", blob, 40)[0]
    e_shentsize = struct.unpack_from("<H", blob, 58)[0]
    e_shnum = struct.unpack_from("<H", blob, 60)[0]
    e_shstrndx = struct.unpack_from("<H", blob, 62)[0]
    sections: List[Tuple[str, int, int, int, int, int]] = []
    if e_shoff and e_shentsize >= 64 and 0 < e_shnum <= 256 and e_shoff + e_shentsize * e_shnum <= len(blob):
        shstr_off, shstr_size = 0, 0
        if e_shstrndx < e_shnum:
            base_str = e_shoff + e_shstrndx * e_shentsize
            shstr_off = struct.unpack_from("<Q", blob, base_str + 24)[0]
            shstr_size = struct.unpack_from("<Q", blob, base_str + 32)[0]
        strtab = blob[shstr_off:shstr_off + shstr_size] if shstr_off + shstr_size <= len(blob) else b""
        for i in range(e_shnum):
            base = e_shoff + i * e_shentsize
            sh_name_idx, sh_type = struct.unpack_from("<II", blob, base)
            sh_offset, sh_size = struct.unpack_from("<QQ", blob, base + 24)
            sh_link = struct.unpack_from("<I", blob, base + 40)[0]
            sh_entsize = struct.unpack_from("<Q", blob, base + 56)[0]
            sname = _read_cstr_bytes(strtab, sh_name_idx) if strtab else ""
            sections.append((sname, sh_type, sh_offset, sh_size, sh_link, sh_entsize))

    sec_names = [s[0] for s in sections if s[0]]
    kdart_syms: List[str] = []
    seen_sym = set()

    for sname, sh_type, sh_offset, sh_size, sh_link, sh_entsize in sections:
        if sname in (".dynsym", ".symtab") or sh_type in (2, 11):  # SHT_SYMTAB / SHT_DYNSYM
            entsize = sh_entsize if sh_entsize >= 24 else 24
            dynstr = b""
            if 0 <= sh_link < len(sections):
                _, _, l_off, l_sz, _, _ = sections[sh_link]
                if l_off + l_sz <= len(blob):
                    dynstr = blob[l_off:l_off + l_sz]
            if not dynstr:
                for l_name, _, l_off, l_sz, _, _ in sections:
                    if l_name in (".dynstr", ".strtab") and l_off + l_sz <= len(blob):
                        dynstr = blob[l_off:l_off + l_sz]
                        break
            if dynstr and sh_offset + sh_size <= len(blob):
                count = min(4096, sh_size // entsize)
                for idx in range(count):
                    st_name = struct.unpack_from("<I", blob, sh_offset + idx * entsize)[0]
                    sym = _read_cstr_bytes(dynstr, st_name)
                    if sym and ("kDart" in sym or sym.startswith("Dart_")) and sym not in seen_sym:
                        seen_sym.add(sym)
                        kdart_syms.append(sym)
    return True, machine, sec_names, kdart_syms


_NOISE_PASCAL = {
    "Widget", "BuildContext", "StatelessWidget", "StatefulWidget", "State",
    "Object", "String", "Future", "Stream", "List", "Map", "Set", "Function",
    "Exception", "Error", "Null", "Boolean", "Integer", "Double", "Dynamic",
    "HttpClient", "MethodChannel", "EventChannel", "PlatformException",
    "TextEditingController", "NavigatorState", "ScaffoldState",
}


def analyze_flutter_libapp_deep(so_path_or_bytes: Any, max_bytes: int = 16 * 1024 * 1024) -> Dict[str, Any]:
    """Analysiert Flutter `libapp.so` (Pfad, Verzeichnis oder Rohbytes) tief ohne Execution."""
    blob = b""
    source_path = ""
    if isinstance(so_path_or_bytes, (bytes, bytearray)):
        blob = bytes(so_path_or_bytes[:max_bytes])
    elif so_path_or_bytes is not None:
        p = os.fspath(so_path_or_bytes)
        if os.path.isdir(p):
            for cand in (
                os.path.join(p, "lib", "arm64-v8a", "libapp.so"),
                os.path.join(p, "lib", "armeabi-v7a", "libapp.so"),
                os.path.join(p, "libapp.so"),
            ):
                if os.path.isfile(cand):
                    p = cand
                    break
        if os.path.isfile(p):
            source_path = p
            try:
                with open(p, "rb") as fh:
                    blob = fh.read(max_bytes)
            except Exception:
                blob = b""

    is_elf64, machine, sec_names, kdart_syms = _parse_elf64_headers(blob)
    seen_sym = set(kdart_syms)
    for kd in _K_DART_NAMES:
        if kd.encode("ascii") in blob and kd not in seen_sym:
            seen_sym.add(kd)
            kdart_syms.append(kd)

    dart_packages: List[str] = []
    seen_pkg = set()
    for m in re.finditer(rb"package:([a-zA-Z0-9_]+)/[a-zA-Z0-9_./-]+\.dart", blob):
        pkg = m.group(1).decode("ascii", "ignore")
        if pkg and pkg not in seen_pkg:
            seen_pkg.add(pkg)
            dart_packages.append(pkg)
            if len(dart_packages) >= 64:
                break

    dart_classes: List[str] = []
    seen_cls = set()
    cls_pat = rb"\b([A-Z][A-Za-z0-9]{2,40}(?:Screen|Page|Widget|Bloc|Cubit|Repository|Controller|Service|Provider|ViewModel|State))\b"
    for m in re.finditer(cls_pat, blob):
        cname = m.group(1).decode("ascii", "ignore")
        if cname not in _NOISE_PASCAL and cname not in seen_cls:
            seen_cls.add(cname)
            dart_classes.append(cname)
            if len(dart_classes) >= 96:
                break

    gql_queries: List[str] = []
    seen_gql = set()
    for m in re.finditer(rb"\b(query|mutation|subscription)\s+([A-Za-z_][A-Za-z0-9_]{2,47})(\s*[({])?", blob):
        kind_b, op_b, tail_b = m.group(1), m.group(2), m.group(3)
        op_name = op_b.decode("ascii", "ignore")
        if not op_name:
            continue
        is_ident = (
            op_name[0].isupper()
            or any(ch.isupper() or ch == "_" for ch in op_name[1:])
            or bool(tail_b)
        )
        if not is_ident:
            continue
        qn = f"{kind_b.decode('ascii', 'ignore')} {op_name}"
        if qn not in seen_gql:
            seen_gql.add(qn)
            gql_queries.append(qn)
            if len(gql_queries) >= 48:
                break
    if len(gql_queries) < 48:
        for m in re.finditer(rb"\b(Query|Mutation|Subscription)\$([A-Za-z_][A-Za-z0-9_]{2,48})\b", blob):
            qn = f"{m.group(1).decode('ascii', 'ignore').lower()} {m.group(2).decode('ascii', 'ignore')}"
            if qn not in seen_gql:
                seen_gql.add(qn)
                gql_queries.append(qn)
                if len(gql_queries) >= 48:
                    break

    rest_endpoints: List[str] = []
    seen_ep = set()
    for m in re.finditer(rb"https?://[a-zA-Z0-9.-]+(?:/[a-zA-Z0-9_./?=&%-]*)?", blob):
        u = m.group(0).decode("ascii", "ignore")
        if len(u) >= 10 and u not in seen_ep:
            seen_ep.add(u)
            rest_endpoints.append(u)
            if len(rest_endpoints) >= 48:
                break

    channels: List[str] = []
    seen_ch = set()
    for m in re.finditer(
        rb"(?:plugins\.flutter\.io|plugins\.it_nomads\.com|dev\.fluttercommunity\.plus|flutter|com\.[a-zA-Z0-9_.]+|io\.[a-zA-Z0-9_.]+)/[a-zA-Z0-9_.-]{2,48}",
        blob,
    ):
        ch = m.group(0).decode("ascii", "ignore")
        if ch not in seen_ch:
            seen_ch.add(ch)
            channels.append(ch)
            if len(channels) >= 32:
                break

    return {
        "source_path": source_path,
        "is_elf64": is_elf64,
        "machine": machine,
        "sections": sec_names,
        "kdart_symbols": kdart_syms,
        "dart_packages": dart_packages,
        "dart_classes": dart_classes,
        "graphql_queries": gql_queries,
        "rest_endpoints": rest_endpoints,
        "platform_channels": channels,
    }


def compute_yoga_layout(node: Dict[str, Any], x: int = 0, y: int = 0,
                        max_w: int = 380, max_h: int = 720) -> Dict[str, Any]:
    """Berechnet deterministische 2D-Flexbox/Yoga-Boxen (`props['_box']`) fuer RN-Mount-JSON."""
    if not isinstance(node, dict):
        return {}
    props = node.setdefault("props", {})
    style = props.get("style") if isinstance(props.get("style"), dict) else {}
    pad = int(style.get("padding", props.get("padding", 8)) or 0)
    gap = int(style.get("gap", props.get("gap", 8)) or 0)
    flex_dir = str(style.get("flexDirection", props.get("flexDirection", "column"))).lower()
    req_w = style.get("width", props.get("width"))
    req_h = style.get("height", props.get("height"))
    w = int(req_w) if isinstance(req_w, (int, float)) else int(max_w)

    children = node.get("children") or []
    comp = str(node.get("component") or "")
    default_h = 44 if "TextInput" in comp else (40 if props.get("onPress") else (28 if "Paragraph" in comp or "Text" in comp else 64))

    cur_x = x + pad
    cur_y = y + pad
    inner_w = max(24, w - 2 * pad)
    total_child_h = 0

    if flex_dir == "row" and children:
        per_w = max(24, (inner_w - gap * max(0, len(children) - 1)) // len(children))
        row_h = 0
        for ch in children:
            if not isinstance(ch, dict):
                continue
            ch_style = (ch.get("props") or {}).get("style") or {}
            cw = int(ch_style["width"]) if isinstance(ch_style.get("width"), (int, float)) else per_w
            box = compute_yoga_layout(ch, cur_x, cur_y, cw, max_h)
            row_h = max(row_h, box.get("h", default_h))
            cur_x += cw + gap
        total_child_h = row_h
    else:
        for ch in children:
            if not isinstance(ch, dict):
                continue
            box = compute_yoga_layout(ch, cur_x, cur_y, inner_w, max_h)
            ch_h = box.get("h", default_h)
            cur_y += ch_h + gap
            total_child_h += ch_h + gap
        if children and total_child_h > 0:
            total_child_h -= gap

    h = int(req_h) if isinstance(req_h, (int, float)) else (pad * 2 + total_child_h if children else default_h)
    box = {"x": int(x), "y": int(y), "w": int(w), "h": int(min(max_h, max(20, h)))}
    props["_box"] = box
    return box


class RnInteractiveJsSession:
    """Interaktive Multi-Turn-React-Native-Session (Mount -> Tap -> ChangeText -> Fetch -> ViewTree)."""

    def __init__(self, bundle_js: Optional[str] = None, stubs: Any = None,
                 app_json: Optional[Dict[str, Any]] = None, use_node: bool = True):
        self.bundle_js = bundle_js
        self.stubs = stubs
        self.use_node = bool(use_node)
        self.surface_id = 11
        self._state: Dict[str, Any] = {}
        self._handlers: Dict[int, Dict[str, Callable[..., Any]]] = {}
        self._fetch_log: List[Dict[str, Any]] = []
        self._route = "home"
        self.mount_data: Dict[str, Any] = app_json if isinstance(app_json, dict) else self._default_login_tree()
        if isinstance(self.mount_data.get("surfaceId"), int):
            self.surface_id = self.mount_data["surfaceId"]
        from core.java.engine_stubs_g14 import get_rn_builtin_modules
        self.builtin_modules = get_rn_builtin_modules(stubs)
        self._node_state_file: Optional[str] = None
        if bundle_js and app_json is None:
            self._init_from_bundle_text()

    def _default_login_tree(self) -> Dict[str, Any]:
        return {
            "surfaceId": 11,
            "root": {
                "tag": 1, "component": "RCTView", "props": {"style": {"padding": 16, "gap": 12}},
                "children": [
                    {"tag": 2, "component": "Paragraph", "props": {"text": "Sign In (React Native)"}, "children": []},
                    {"tag": 3, "component": "AndroidTextInput", "props": {"placeholder": "Email", "text": ""}, "children": []},
                    {"tag": 4, "component": "AndroidTextInput", "props": {"placeholder": "Password", "text": "", "secureTextEntry": True}, "children": []},
                    {"tag": 5, "component": "RCTView", "props": {"accessibilityRole": "button", "onPress": True, "testID": "btn_login"},
                     "children": [{"tag": 6, "component": "Paragraph", "props": {"text": "Submit"}, "children": []}]},
                ],
            },
        }

    def _init_from_bundle_text(self) -> None:
        js_txt = str(self.bundle_js or "")
        if os.path.isfile(js_txt):
            try:
                with open(js_txt, "r", encoding="utf-8", errors="replace") as fh:
                    js_txt = fh.read()
            except Exception:
                js_txt = ""
        m_url = re.search(r"fetch\s*\(\s*['\"](https?://[^'\"]+)['\"]", js_txt)
        if m_url:
            self._state["_bundle_fetch_url"] = m_url.group(1)

    def _node_available(self) -> bool:
        if not self.use_node:
            return False
        try:
            from core.java.rn_js_bridge import node_available
            return bool(node_available())
        except Exception:
            return False

    def _fire_fetch(self, url: str, method: str = "POST", headers: Optional[Dict[str, str]] = None,
                    body: Any = b"") -> Dict[str, Any]:
        hdrs = dict(headers or {"Content-Type": "application/json"})
        raw = json.dumps(body).encode("utf-8") if isinstance(body, (dict, list)) else (
            body if isinstance(body, (bytes, bytearray)) else str(body or "").encode("utf-8"))
        code, rbody = 200, b"{}"
        if self.stubs is not None and callable(getattr(self.stubs, "net_fire", None)):
            resp = self.stubs.net_fire(method, url, hdrs, raw)
            if resp is not None:
                code = int(getattr(resp, "code", 200) or 200)
                rb = getattr(resp, "body", b"")
                rbody = bytes(rb) if isinstance(rb, (bytes, bytearray)) else str(rb or "").encode("utf-8")
        rec = {"method": method, "url": url, "code": code, "body": rbody.decode("utf-8", "replace")}
        self._fetch_log.append(rec)
        return rec

    def _find_node(self, node: Any, tag_or_id: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(node, dict):
            return None
        props = node.get("props") or {}
        if node.get("tag") == tag_or_id or props.get("testID") == tag_or_id or props.get("id") == tag_or_id:
            return node
        for ch in node.get("children") or []:
            hit = self._find_node(ch, tag_or_id)
            if hit is not None:
                return hit
        return None

    def _collect_inputs(self, node: Any, out: Dict[str, str]) -> None:
        if not isinstance(node, dict):
            return
        comp = str(node.get("component") or "")
        props = node.get("props") or {}
        if "TextInput" in comp:
            key = str(props.get("testID") or props.get("placeholder") or node.get("tag"))
            out[key] = str(props.get("text") or "")
        for ch in node.get("children") or []:
            self._collect_inputs(ch, out)

    def register_handler(self, tag: int, event_name: str, fn: Callable[..., Any]) -> None:
        self._handlers.setdefault(int(tag), {})[str(event_name)] = fn

    def mount(self) -> Tuple[Any, Dict[str, Any]]:
        """Mountet den aktuellen React-Native-Baum, fuehrt optionalen Node-Host aus & rechnet Yoga-Layout."""
        if self._node_available() and isinstance(self.bundle_js, str) and "completeRoot" in self.bundle_js:
            try:
                out = self._run_node_turn(self.bundle_js, event=None)
                if isinstance(out, dict) and isinstance(out.get("root"), dict):
                    self.mount_data = out
            except Exception:
                pass
        root = self.mount_data.get("root")
        if isinstance(root, dict):
            compute_yoga_layout(root)
        return self.to_view_tree()

    def _run_node_turn(self, bundle_code: str, event: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        host_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "tools", "rn_js_host")
        host_js = os.path.join(host_dir, "host.js").replace("\\", "/")
        ev_json = json.dumps(event or {})
        state_json = json.dumps(self._state)
        wrapper = (
            f"const {{ makeFabric, turbo }} = require({json.dumps(host_js)});\n"
            "const __f = makeFabric();\n"
            "globalThis.nativeFabricUIManager = __f.fabric;\n"
            "globalThis.TurboModuleRegistry = { get: (n) => turbo[n] || {}, getEnforcing: (n) => turbo[n] || {} };\n"
            f"globalThis.__GLASS_STATE__ = {state_json};\n"
            f"globalThis.__GLASS_EVENT__ = {ev_json};\n"
            f"{bundle_code}\n"
            "process.stdout.write(JSON.stringify(__f.result() || {}));\n"
        )
        proc = subprocess.run(["node", "-e", wrapper], capture_output=True, text=True, timeout=6)
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout.strip().splitlines()[-1])
        return None

    def change_text(self, tag_or_id: Any, text: str) -> Tuple[Any, Dict[str, Any]]:
        """Aktualisiert einen RN-TextInput und loest ggf. registrierte onChangeText-Handler aus."""
        target = self._find_node(self.mount_data.get("root"), tag_or_id)
        if target is not None:
            target.setdefault("props", {})["text"] = str(text)
            tag = int(target.get("tag", 0))
            cb = (self._handlers.get(tag) or {}).get("onChangeText")
            if callable(cb):
                cb(self, str(text))
        return self.mount()

    def tap(self, tag_or_id: Any) -> Tuple[Any, Dict[str, Any]]:
        """Loest einen Tap (`onPress`) auf einen RN-Knoten aus inkl. Handler, Fetch & Navigation."""
        target = self._find_node(self.mount_data.get("root"), tag_or_id)
        if target is not None:
            tag = int(target.get("tag", 0))
            cb = (self._handlers.get(tag) or {}).get("onPress")
            if callable(cb):
                cb(self, target)
            elif self._state.get("_bundle_fetch_url"):
                inputs: Dict[str, str] = {}
                self._collect_inputs(self.mount_data.get("root"), inputs)
                resp = self._fire_fetch(self._state["_bundle_fetch_url"], "POST", body=inputs)
                self._route = "authenticated"
                self.mount_data = {
                    "surfaceId": self.surface_id,
                    "root": {
                        "tag": 1, "component": "RCTView", "props": {"style": {"padding": 16, "gap": 10}},
                        "children": [
                            {"tag": 20, "component": "Paragraph", "props": {"text": f"Status: {resp['code']}"}, "children": []},
                            {"tag": 21, "component": "Paragraph", "props": {"text": resp["body"][:200]}, "children": []},
                        ],
                    },
                }
        return self.mount()

    def to_view_tree(self) -> Tuple[Any, Dict[str, Any]]:
        """Konvertiert den aktuellen RN-Mount-Baum via `tree_from_mount_json` in einen `ViewTree`."""
        from core.java.rn_js_bridge import tree_from_mount_json
        surface, rec = tree_from_mount_json(self.mount_data)
        return surface, rec
