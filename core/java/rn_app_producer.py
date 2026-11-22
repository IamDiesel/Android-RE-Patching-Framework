# -*- coding: utf-8 -*-
"""core.java.rn_app_producer — Generischer React-Native- & Hermes-HBC-In-Phone-Producer.

Analysiert reale React-Native-APKs (`assets/index.android.bundle` als Hermes-Bytecode HBC,
Klartext-JS oder verschlüsseltes Bundle, `res/drawable*`, `res/values/strings.xml`, Manifest)
und hängt eine interaktive ReactRootView-UI samt TurboModuleRouter-, BLE-GATT-, AsyncStorage-
und NetRecorder-Verdrahtung direkt in die Handy-Simulation (`DisplayTab`) ein — zielagnostisch.
"""
from __future__ import annotations

from collections import Counter
import glob
import json
import math
import os
import re
import struct
from typing import Any, Callable, Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET

from core.java.view_tree import ViewNode, ViewTree

_HBC_MAGIC = b"\xc6\x1f\xbc\x03\xc1\x03\x19\x1f"

_NOISE_URL_DOMAINS = (
    "reactjs.org", "react.dev", "reactnative.dev", "reactnavigation.org",
    "facebook.com", "fb.me", "redux.js.org", "redux-toolkit.js.org",
    "github.com", "mozilla.org", "w3.org", "ietf.org", "bit.ly",
    "react.i18next.com", "localhost", "10.0.2.2",
)

_KNOWN_RN_MODULES = (
    "BleManager", "RNAsyncStorage", "AsyncStorage", "PermissionsAndroid",
    "RNBootSplash", "ActionSheetManager", "DeviceInfo", "RNSVGSvgView",
    "RNGestureHandler", "RNSScreen", "ReactNativeLocalization",
)

_SKIP_IMAGE_PREFIXES = (
    "abc_", "notification_", "common_google_", "googleg_", "notify_panel",
)

_DE_LABEL_CANDIDATES = {
    "my_devices": ("Mein Kühlschrank", "Meine Geräte"),
    "add_device": ("Gerät hinzufügen", "Gerät koppeln"),
    "scan_hint": ("Scannen Sie den Kühlschrank", "Nach Geräten in der Nähe suchen"),
    "tap_pair": ("Tippen Sie zum Koppeln auf das APP-Kühlschranksymbol", "Zum Verbinden Gerät auswählen"),
    "pairing_guide": ("Pairing-Anleitung",),
    "pairing_text": (
        "Nach einem Piepton des Kühlschranks, klicken Sie auf die 'Einstellungen'-Taste zur Steuerung. "
        "Falls keine Einstellungen-Taste vorhanden ist, klicken Sie auf die 'Temp erhöhen'-Taste.",
    ),
    "under_control": ("Der Kühlschrank befindet sich unter der App-Steuerung",),
    "cur_temp": ("Aktuelle Temperatur",),
    "settings": ("Einstellungen", "Allgemeine Einstellungen"),
    "night_mode": ("Nacht-Modus",),
    "not_found": ("Gerät kann nicht gefunden werden",),
}

_EN_LABEL_CANDIDATES = {
    "my_devices": ("My Fridge", "My Devices"),
    "add_device": ("Add Fridge", "Add Device"),
    "scan_hint": ("Search more fridges", "Scan nearby devices"),
    "tap_pair": ("Clik fridge icon for bluetooth pairing", "Select device to pair"),
    "pairing_guide": ("Pairing Guide",),
    "pairing_text": (
        "After a beep from the fridge, click the 'Settings' button to control it. "
        "If there is no settings button, click the 'Temp Up' button.",
    ),
    "under_control": ("Fridge is under APP controls",),
    "cur_temp": ("Current Temp", "Min Temperature"),
    "settings": ("Settings", "Advance setting"),
    "night_mode": ("Night Mode",),
    "not_found": ("Device not found",),
}


def _short_label(text: str, limit: int = 34) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s if len(s) <= limit else (s[: limit - 1] + "…")


def _entropy(sample: bytes) -> float:
    if not sample:
        return 0.0
    cnt = Counter(sample)
    n = float(len(sample))
    return -sum((c / n) * math.log2(c / n) for c in cnt.values())


def _extract_metadata_from_strings(strings: List[str]) -> Dict[str, Any]:
    endpoints: List[str] = []
    seen_ep = set()
    ble_uuids: List[str] = []
    seen_uuid = set()
    native_mods: List[str] = []
    seen_mod = set()
    routes: List[str] = []
    seen_rt = set()
    brand_colors: List[str] = []
    seen_col = set()

    uuid_re = re.compile(r"\b([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\b")
    url_re = re.compile(r"^(?:https?|wss?)://[A-Za-z0-9._:/?=&%-]{6,1024}$")
    col_re = re.compile(r"^#[0-9a-fA-F]{6}$")

    for s in strings:
        if not s:
            continue
        if s.startswith(("http://", "https://", "ws://", "wss://")) and url_re.match(s):
            low = s.lower()
            if not any(nd in low for nd in _NOISE_URL_DOMAINS) and s not in seen_ep:
                seen_ep.add(s)
                endpoints.append(s)
                if len(endpoints) >= 24:
                    break
    for s in strings:
        if not s:
            continue
        for um in uuid_re.finditer(s):
            u = um.group(1).lower()
            if u not in seen_uuid:
                seen_uuid.add(u)
                ble_uuids.append(u)
        for mod in _KNOWN_RN_MODULES:
            if mod in s and mod not in seen_mod:
                seen_mod.add(mod)
                native_mods.append(mod)
        if col_re.match(s) and s.upper() not in seen_col:
            uc = s.upper()
            r, g, b = int(uc[1:3], 16), int(uc[3:5], 16), int(uc[5:7], 16)
            if max(r, g, b) - min(r, g, b) >= 24 and len(brand_colors) < 16:
                seen_col.add(uc)
                brand_colors.append(uc)
        if (
            4 <= len(s) <= 30
            and s[0].isupper()
            and s.isidentifier()
            and not s.startswith(("RCT", "React", "Animated", "Virtualized", "Keyboard", "SafeArea", "InputAccessory", "Native", "Fallback"))
            and any(k in s.lower() for k in ("home", "fridge", "search", "scan", "connect", "device", "setting", "lang", "login", "auth", "about", "control", "page", "screen"))
            and s not in seen_rt
        ):
            seen_rt.add(s)
            routes.append(s)

    return {
        "endpoints": endpoints,
        "ble_uuids": ble_uuids[:24],
        "native_modules": native_mods,
        "routes": routes[:40],
        "brand_colors": brand_colors,
    }


def parse_hermes_hbc_bundle(data_or_path: Any, max_bytes: int = 16 * 1024 * 1024) -> Dict[str, Any]:
    """Parst Hermes-Bytecode-Header & exakte Small/OverflowStringTable (HBC v84–v99+, reines Python)."""
    if isinstance(data_or_path, (bytes, bytearray)):
        data = bytes(data_or_path[:max_bytes])
    elif data_or_path and os.path.isfile(os.fspath(data_or_path)):
        with open(data_or_path, "rb") as fh:
            data = fh.read(max_bytes)
    else:
        return {"is_hbc": False, "version": 0, "function_count": 0, "strings": [], "endpoints": [], "ble_uuids": [], "native_modules": [], "routes": [], "brand_colors": []}

    if len(data) < 128 or data[:8] != _HBC_MAGIC:
        return {"is_hbc": False, "version": 0, "function_count": 0, "strings": [], "endpoints": [], "ble_uuids": [], "native_modules": [], "routes": [], "brand_colors": []}

    version = struct.unpack_from("<I", data, 8)[0]
    fn_cnt, sk_cnt, id_cnt, st_cnt, ov_cnt, ss_sz = struct.unpack_from("<6I", data, 40)

    strings: List[str] = []
    best_score = -1
    for fsz, use_id in ((12, True), (16, True), (12, False), (16, False)):
        sst_off = 128 + fn_cnt * fsz + sk_cnt * 4 + (id_cnt * 4 if use_id else 0)
        ovt_off = sst_off + st_cnt * 4
        ss_off = ovt_off + ov_cnt * 8
        if ss_off < 128 or ss_off + ss_sz > len(data) or st_cnt <= 0:
            continue
        ok = True
        cand: List[str] = []
        printable_cnt = 0
        for i in range(st_cnt):
            entry = struct.unpack_from("<I", data, sst_off + i * 4)[0]
            is_u16 = entry & 1
            s_off = (entry >> 1) & 0x7FFFFF
            s_len = (entry >> 24) & 0xFF
            if s_len == 0xFF:
                if s_off >= ov_cnt:
                    ok = False
                    break
                s_off, s_len = struct.unpack_from("<II", data, ovt_off + s_off * 8)
            blen = s_len * (2 if is_u16 else 1)
            if s_off + blen > ss_sz:
                ok = False
                break
            raw = data[ss_off + s_off : ss_off + s_off + blen]
            try:
                txt = raw.decode("utf-16le", "replace") if is_u16 else raw.decode("latin1")
            except Exception:
                txt = ""
            if i < 64 and txt and all(32 <= ord(ch) < 127 or ord(ch) > 127 for ch in txt):
                printable_cnt += 1
            cand.append(txt)
        if ok:
            score = printable_cnt + (20 if "RCT" in "".join(cand[:60]) or "flex" in "".join(cand[:40]) else 0)
            if score > best_score:
                best_score = score
                strings = cand

    meta = _extract_metadata_from_strings(strings)
    return {
        "is_hbc": True,
        "version": int(version),
        "function_count": int(fn_cnt),
        "string_count": len(strings),
        "strings": strings,
        **meta,
    }


def _scan_res_drawables(app_root: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    res_dir = os.path.join(app_root or "", "res")
    by_name: Dict[str, str] = {}
    if os.path.isdir(res_dir):
        for pat in ("drawable*", "mipmap*"):
            for folder in sorted(glob.glob(os.path.join(res_dir, pat))):
                for ext in ("*.png", "*.jpg", "*.jpeg"):
                    for fp in sorted(glob.glob(os.path.join(folder, ext))):
                        bn = os.path.basename(fp).lower()
                        if bn.startswith(_SKIP_IMAGE_PREFIXES):
                            continue
                        stem = os.path.splitext(bn)[0]
                        if stem not in by_name or "xhdpi" in folder or "xxhdpi" in folder or "mdpi" in folder:
                            by_name[stem] = fp

    def _pick(keys: Tuple[str, ...]) -> str:
        for k in keys:
            if k in by_name:
                return by_name[k]
        for k in keys:
            for stem, fp in by_name.items():
                if k in stem:
                    return fp
        return ""

    images = {
        "logo": _pick(("bootsplash_logo", "ic_launcher_round", "ic_launcher", "logo")),
        "bg": _pick(("app_res_bg", "_bg", "background")),
        "device": _pick(("app_res_fridge", "fridge", "app_res_drawerimg", "device", "tracker")),
        "ble": _pick(("app_res_ble", "ble", "app_res_linking", "linking")),
        "add": _pick(("app_res_add", "_add")),
        "power_on": _pick(("app_res_power",)),
        "power_off": _pick(("app_res_power_off", "power_off")),
        "eco_on": _pick(("app_res_eco_on", "eco_on")),
        "eco_off": _pick(("app_res_eco_off", "eco_off")),
        "lock_on": _pick(("app_res_lock_on", "lock_on")),
        "lock_off": _pick(("app_res_lock_off", "lock_off")),
        "left_on": _pick(("app_res_lefton", "app_res_left_on")),
        "right_on": _pick(("app_res_righton", "app_res_right_on")),
        "battery": _pick(("app_res_b1", "battery")),
        "settings": _pick(("app_res_set",)),
        "lang": _pick(("app_res_lang",)),
        "tip1": _pick(("app_res_tip1",)),
        "tip2": _pick(("app_res_tip2",)),
    }
    return by_name, images


def _pick_i18n_map(strings: List[str], candidates: Dict[str, Tuple[str, ...]]) -> Dict[str, str]:
    str_set = set(strings)
    out: Dict[str, str] = {}
    for key, opts in candidates.items():
        found = next((o for o in opts if o in str_set), "")
        out[key] = found or opts[0]
    return out


def extract_rn_app_spec(app_root: str) -> Dict[str, Any]:
    """Extrahiert Manifest, App-Name, Drawables, Hermes/JS-Metadaten, BLE-UUIDs & UI-Archetyp."""
    pkg = ""
    application = ""
    launcher = ""
    perms: List[str] = []
    try:
        from core.java.manifest import parse_manifest
        man = parse_manifest(app_root)
        if man is not None:
            pkg = getattr(man, "package", "") or ""
            application = getattr(man, "application", "") or ""
            launcher = man.entry() or ""
            perms = list(getattr(man, "permissions", ()) or ())
    except Exception:
        pass

    man_xml = os.path.join(app_root or "", "AndroidManifest.xml")
    if os.path.isfile(man_xml) and not perms:
        try:
            root_el = ET.parse(man_xml).getroot()
            ans = "{http://schemas.android.com/apk/res/android}"
            for up in root_el.findall("uses-permission"):
                nm = up.get(ans + "name") or ""
                if nm:
                    perms.append(nm)
        except Exception:
            pass

    app_name = ""
    st_xml = os.path.join(app_root or "", "res", "values", "strings.xml")
    if os.path.isfile(st_xml):
        try:
            sroot = ET.parse(st_xml).getroot()
            for el in sroot.findall("string"):
                if el.get("name") == "app_name" and (el.text or "").strip():
                    app_name = el.text.strip()
                    break
        except Exception:
            pass
    if not app_name:
        app_name = pkg.split(".")[-1].replace("_", " ").title() if pkg else "React Native App"

    drawable_map, images = _scan_res_drawables(app_root)

    bundle_path = os.path.join(app_root or "", "assets", "index.android.bundle")
    bundle_kind = "none"
    bundle_size = 0
    bundle_entropy = 0.0
    hbc_info: Dict[str, Any] = {
        "is_hbc": False, "version": 0, "function_count": 0,
        "strings": [], "endpoints": [], "ble_uuids": [],
        "native_modules": [], "routes": [], "brand_colors": [],
    }
    if os.path.isfile(bundle_path):
        bundle_size = os.path.getsize(bundle_path)
        with open(bundle_path, "rb") as fh:
            head = fh.read(min(65536, bundle_size))
        bundle_entropy = _entropy(head)
        if head[:8] == _HBC_MAGIC:
            bundle_kind = "hbc"
            hbc_info = parse_hermes_hbc_bundle(bundle_path)
        elif bundle_entropy < 6.8:
            bundle_kind = "js"
            try:
                raw_txt = open(bundle_path, "r", encoding="utf-8", errors="replace").read(4 * 1024 * 1024)
            except Exception:
                raw_txt = ""
            toks = re.findall(r"['\"]([^'\"\n\r]{3,160})['\"]", raw_txt)
            meta = _extract_metadata_from_strings(toks)
            hbc_info = {"is_hbc": False, "version": 0, "function_count": 0, "strings": toks, **meta}
        else:
            bundle_kind = "encrypted"

    strings = hbc_info.get("strings") or []
    joined_low = "\n".join(strings[:4000]).lower()
    has_ble = bool(
        hbc_info.get("ble_uuids")
        or "BleManager" in (hbc_info.get("native_modules") or ())
        or any("BLUETOOTH" in p.upper() for p in perms)
    )
    iot_hits = sum(joined_low.count(k) for k in ("fridge", "freezer", "temperature", "kühlschrank", "eco", "bluetooth"))
    login_hits = sum(joined_low.count(k) for k in ("sign in", "password", "forgot password", "passwort", "username"))
    no_login_hint = "without registration and login" in joined_low

    if bundle_kind == "encrypted":
        archetype = "encrypted"
    elif has_ble and (no_login_hint or iot_hits >= login_hits):
        archetype = "ble_iot"
    else:
        archetype = "auth_api"

    dual_zone = (
        bool(images.get("left_on") or images.get("right_on"))
        or any("Left" in s or "Linke" in s for s in strings[:5000])
    )
    endpoints = list(hbc_info.get("endpoints") or [])
    quick_urls = []
    for u in endpoints[:4]:
        lbl = u.split("://", 1)[-1].split("/", 1)[0]
        quick_urls.append({"label": lbl[:24], "url": u})

    return {
        "package": pkg,
        "application": application,
        "launcher": launcher,
        "app_name": app_name,
        "permissions": perms,
        "bundle_kind": bundle_kind,
        "bundle_size": bundle_size,
        "bundle_entropy": round(bundle_entropy, 3),
        "hbc_version": int(hbc_info.get("version") or 0),
        "function_count": int(hbc_info.get("function_count") or 0),
        "string_count": len(strings),
        "endpoints": endpoints,
        "quick_urls": quick_urls,
        "ble_uuids": list(hbc_info.get("ble_uuids") or []),
        "native_modules": list(hbc_info.get("native_modules") or []),
        "routes": list(hbc_info.get("routes") or []),
        "brand_colors": list(hbc_info.get("brand_colors") or []),
        "accent_color": (hbc_info.get("brand_colors") or ["#0061AC"])[0],
        "drawable_map": drawable_map,
        "images": images,
        "archetype": archetype,
        "dual_zone": dual_zone,
        "i18n": {
            "de": _pick_i18n_map(strings, _DE_LABEL_CANDIDATES),
            "en": _pick_i18n_map(strings, _EN_LABEL_CANDIDATES),
        },
    }


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


class RnInPhoneController:
    """Interaktiver In-Phone-Screen-Controller fuer React-Native- & Hermes-HBC-Apps."""

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
        self.spec = extract_rn_app_spec(app_root)
        self.archetype = self.spec["archetype"]
        self.lang = "de"

        if self.archetype == "ble_iot":
            self.screen = "scan"
        else:
            self.screen = "login"
        self._screen_before_browser = self.screen
        self._nav_stack: List[str] = []

        # BLE / IoT Device State
        self.scanning = False
        self.discovered_devices: List[Dict[str, Any]] = []
        self.connected_device: Optional[Dict[str, Any]] = None
        self.power_on = True
        self.zone = "L"
        self.temp_l = -4
        self.temp_r = 4
        self.cur_temp_l = -2
        self.cur_temp_r = 5
        self.eco_mode = False
        self.locked = False
        self.unit = "C"
        self.battery_prot = "H"
        self.voltage = 12.6
        self.cloud_status = ""
        self.ble_log_summary = ""

        # Auth / API State
        self.email = ""
        self.password = ""
        self.auth_status = ""
        self.auth_token = ""
        self.home_tab = 0

        from core.java.rn_hermes_bridge import TurboModuleRouter
        vm = getattr(stubs, "vm", None) if stubs is not None else None
        self.turbo = TurboModuleRouter(vm=vm)
        if stubs is not None:
            stubs._rn_turbo_router = self.turbo
        for mod, meth in (
            ("PlatformConstants", "getConstants"),
            ("DeviceInfo", "getConstants"),
            ("I18nManager", "getConstants"),
            ("AppState", "getConstants"),
        ):
            self.turbo.call(mod, meth, [])

        from core.java.flutter_app_producer import InPhoneBrowserController
        self.browser = InPhoneBrowserController(
            tree=tree,
            app_root=app_root,
            stubs=stubs,
            quick_urls=self.spec.get("quick_urls"),
            on_exit=self.close_browser,
            on_async_update=on_async_update,
        )
        self._seed_ble_devices()

    def set_async_updater(self, cb: Optional[Callable[[], None]]) -> None:
        self.on_async_update = cb
        self.browser.on_async_update = cb

    def _t(self, key: str, default: str = "") -> str:
        return (self.spec.get("i18n") or {}).get(self.lang, {}).get(key) or default or key

    def _seed_ble_devices(self) -> None:
        uuids = self.spec.get("ble_uuids") or ["00001234-0000-1000-8000-00805f9b34fb"]
        svc_uuid = uuids[0]
        chr_w = uuids[1] if len(uuids) > 1 else "00001235-0000-1000-8000-00805f9b34fb"
        brand = (self.spec.get("app_name") or "Fridge").split()[0].title()
        default_dev = {
            "name": f"{brand}-BLE-A15",
            "address": "A4:C1:38:90:12:EF",
            "rssi": -51,
            "services": {svc_uuid: {chr_w: b"\xfe\xfe\x04\x01\x01\x01"}},
        }
        if self.stubs is not None:
            hp = self.stubs.__dict__.setdefault("_hw_profile", {})
            cur_devs = list(hp.get("ble_devices") or ())
            if not cur_devs or (len(cur_devs) == 1 and cur_devs[0].get("name") == "Glass-Tracker-01"):
                hp["ble_devices"] = [default_dev, {
                    "name": f"{brand}-DualZone-Pro",
                    "address": "A4:C1:38:90:55:02",
                    "rssi": -63,
                    "services": {svc_uuid: {chr_w: b"\xfe\xfe\x04\x01\x01\x02"}},
                }]
            self.discovered_devices = list(hp["ble_devices"])
        else:
            self.discovered_devices = [default_dev]

    def _save_rn_storage(self, key: str, val: str) -> None:
        if self.stubs is None:
            return
        store = self.stubs.__dict__.setdefault("_prefs_store", {}).setdefault("RNAsyncStorage", {})
        store[str(key)] = str(val)

    def _emit_toast(self, msg: str) -> None:
        if self.tree is not None and msg:
            self.tree.last_toast = str(msg)
            self.tree.__dict__.setdefault("_toasts", []).append(str(msg))
            self.tree.touch()

    def _navigate(self, next_screen: str) -> None:
        if self.screen != next_screen:
            self._nav_stack.append(self.screen)
            self.screen = next_screen
        self.mount()

    def _ble_write(self, cmd_name: str, payload: bytes) -> None:
        uuids = self.spec.get("ble_uuids") or [
            "00001234-0000-1000-8000-00805f9b34fb",
            "00001235-0000-1000-8000-00805f9b34fb",
        ]
        write_uuid = uuids[1] if len(uuids) > 1 else uuids[0]
        if self.stubs is not None:
            self.stubs.__dict__.setdefault("_ble_log", []).append({
                "op": "writeChar",
                "uuid": write_uuid,
                "cmd": cmd_name,
                "val": bytes(payload),
            })
        self.turbo.call("BleManager", "write", [write_uuid, payload.hex(), cmd_name])
        self._save_rn_storage("last_ble_cmd", f"{cmd_name}:{payload.hex()}")
        self.ble_log_summary = f"GATT {cmd_name} -> {write_uuid[:8]} ({payload.hex()})"

    def _format_temp(self, c_val: int) -> str:
        if self.unit == "F":
            f_val = int(round(c_val * 9 / 5 + 32))
            return f"{f_val} °F"
        return f"{c_val} °C"

    def _net_fire(self, method: str, url: str, headers: dict, body: Any) -> Dict[str, Any]:
        res = self.turbo.call(
            "Networking",
            "sendRequest",
            [{"method": method, "url": url, "headers": headers, "data": body}],
        )
        tlog = getattr(self.turbo, "log", None)
        unresolved = bool(tlog and tlog[-1].get("source") == "unresolved")
        if (not isinstance(res, dict) or (not res.get("status") and unresolved)) and self.stubs is not None:
            nf = getattr(self.stubs, "net_fire", None)
            if callable(nf):
                if isinstance(body, (bytes, bytearray)):
                    raw_body = bytes(body)
                elif isinstance(body, str):
                    raw_body = body.encode("utf-8")
                elif isinstance(body, (dict, list)):
                    raw_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
                else:
                    raw_body = b""
                resp = nf(method, url, headers=headers or {}, body=raw_body)
                rb = getattr(resp, "body", b"")
                if isinstance(rb, (bytes, bytearray)):
                    body_txt = bytes(rb).decode("utf-8", errors="replace")
                else:
                    body_txt = str(rb or "")
                res = {
                    "status": int(getattr(resp, "status", 0) or 0),
                    "body": body_txt,
                    "reason": str(getattr(resp, "reason", "") or ""),
                }
                if tlog:
                    tlog[-1]["source"] = "net_stubs"
        return res if isinstance(res, dict) else {"status": 0, "body": ""}

    def open_browser(self, url: Optional[str] = None) -> None:
        if self.screen != "browser":
            self._screen_before_browser = self.screen
        self.screen = "browser"
        if url:
            self.browser.load_url(url)
        else:
            self.mount()

    def close_browser(self) -> None:
        if self.screen == "browser":
            self.screen = self._screen_before_browser or ("control" if self.connected_device else ("scan" if self.archetype == "ble_iot" else "login"))
        self.mount()

    def toggle_browser(self, url: Optional[str] = None) -> None:
        if self.screen == "browser" and url is None:
            self.close_browser()
        else:
            self.open_browser(url)

    def handle_back(self) -> bool:
        if self.screen == "browser":
            if self.browser.go_back():
                self.mount()
                return True
            self.close_browser()
            return True
        if self._nav_stack:
            self.screen = self._nav_stack.pop()
            self.mount()
            return True
        return False

    def handle_home(self) -> bool:
        if self.screen == "browser":
            self.close_browser()
            return True
        root_scr = "scan" if self.archetype == "ble_iot" else "login"
        if self.screen != root_scr:
            self._nav_stack.clear()
            self.screen = root_scr
            self.mount()
            return True
        return False

    def _build_ble_topbar(self) -> ViewNode:
        tb = ViewNode("LinearLayout", vid="rn_topbar", props={"orientation": "horizontal", "role": "topbar"})
        logo = _img_node("rn_logo_iv", self.spec["images"].get("logo", ""), h=28, w=28)
        if logo is not None:
            tb.add(logo)
        tb.add(ViewNode(
            "TextView", vid="rn_app_title",
            text=_short_label(self.spec.get("app_name") or "RN App", 24),
            props={"layout_weight": 1.0, "gravity": 16, "bold": True},
        ))
        lbtn = ViewNode(
            "Button", vid="rn_lang_btn",
            text=("DE" if self.lang == "de" else "EN"),
            clickable=True, props={"w": 44, "h": 28},
        )
        lbtn.on_click = lambda _v=None: (
            setattr(self, "lang", "en" if self.lang == "de" else "de"),
            self.mount(),
        )
        tb.add(lbtn)
        return tb

    def _build_ble_navbar(self) -> ViewNode:
        nb = ViewNode("LinearLayout", vid="rn_navbar", props={"orientation": "horizontal", "role": "navbar"})
        tabs = [
            ("scan", "Geräte" if self.lang == "de" else "Devices"),
            ("control", "Steuerung" if self.lang == "de" else "Control"),
            ("settings", "Einstellungen" if self.lang == "de" else "Settings"),
        ]
        for s_key, lbl in tabs:
            btn = ViewNode(
                "Button", vid=f"rn_nav_{s_key}", text=lbl, clickable=True,
                props={"role": "navitem", "selected": (self.screen == s_key)},
            )
            def _go(_v=None, _k=s_key):
                if _k == "control" and self.connected_device is None and self.discovered_devices:
                    self.connected_device = self.discovered_devices[0]
                self._navigate(_k)
            btn.on_click = _go
            nb.add(btn)
        return nb

    def _build_ble_scan(self) -> ViewNode:
        box = ViewNode("LinearLayout", vid="rn_scan_screen", props={"orientation": 1})
        box.add(self._build_ble_topbar())

        hero = ViewNode("LinearLayout", vid="rn_scan_hero", props={"role": "card"})
        hero.add(ViewNode(
            "TextView", vid="rn_scan_badge",
            text=f"React Native · Hermes HBC v{self.spec.get('hbc_version', 98)} · {self.spec.get('string_count', 0)} Strings",
            props={"color": self.spec.get("accent_color", "#0061AC"), "bold": True},
        ))
        dev_iv = _img_node("rn_hero_device", self.spec["images"].get("device") or self.spec["images"].get("ble", ""), h=84)
        if dev_iv is not None:
            hero.add(dev_iv)
        hero.add(ViewNode("TextView", vid="rn_scan_title", text=self._t("my_devices", "Meine Geräte")))
        hero.add(ViewNode("TextView", vid="rn_scan_hint", text=self._t("scan_hint", "Nach BLE-Geräten suchen")))

        btn_row = ViewNode("LinearLayout", props={"orientation": "horizontal"})
        scan_btn = ViewNode(
            "Button", vid="rn_ble_scan_btn",
            text=("🔍 Scannen" if self.lang == "de" else "🔍 Scan"),
            clickable=True,
            props={"layout_weight": 1.0, "bg_tint": self.spec.get("accent_color", "#0061AC")},
        )
        def _do_scan(_v=None):
            self.scanning = True
            self.turbo.call("PermissionsAndroid", "requestMultiple", [["BLUETOOTH_SCAN", "BLUETOOTH_CONNECT", "ACCESS_FINE_LOCATION"]])
            self.turbo.call("BleManager", "start", [{}])
            self.turbo.call("BleManager", "scan", [self.spec.get("ble_uuids") or [], 5, False])
            self._seed_ble_devices()
            self._emit_toast(f"{len(self.discovered_devices)} BLE-Geräte gefunden")
            self.mount()
        scan_btn.on_click = _do_scan
        btn_row.add(scan_btn)

        guide_btn = ViewNode(
            "Button", vid="rn_pairing_guide_btn",
            text=("📖 " + _short_label(self._t("pairing_guide", "Anleitung"), 18)),
            clickable=True,
            props={"layout_weight": 1.0, "bg_drawable": {"kind": "shape", "fill": "#e8eef9", "outline": "#9bb5f5", "radius": 6}},
        )
        guide_btn.on_click = lambda _v=None: self._navigate("guide")
        btn_row.add(guide_btn)
        hero.add(btn_row)
        box.add(hero)

        lcard = ViewNode("LinearLayout", vid="rn_dev_list_card", props={"role": "card"})
        lcard.add(ViewNode("TextView", vid="rn_dev_list_hdr", text=f"Gefundene BLE-Geräte ({len(self.discovered_devices)}):"))
        for idx, dev in enumerate(self.discovered_devices[:4]):
            row = ViewNode("LinearLayout", vid=f"rn_dev_row_{idx}", props={"orientation": "horizontal"})
            row.add(ViewNode(
                "TextView", vid=f"rn_dev_lbl_{idx}",
                text=f"{dev.get('name')} ({dev.get('rssi', -55)} dBm)",
                props={"layout_weight": 1.0, "gravity": 16},
            ))
            cbtn = ViewNode(
                "Button", vid=f"rn_connect_dev_{idx}",
                text=("Verbinden" if self.lang == "de" else "Connect"),
                clickable=True, props={"w": 96, "h": 34},
            )
            def _connect(_v=None, _d=dev):
                self.connected_device = _d
                self.turbo.call("BleManager", "connect", [_d.get("address")])
                self.turbo.call("BleManager", "retrieveServices", [_d.get("address")])
                self._ble_write("SYNC_HANDSHAKE", b"\xfe\xfe\x03\x01\x02")
                self._navigate("control")
            cbtn.on_click = _connect
            row.add(cbtn)
            lcard.add(row)
        box.add(lcard)
        box.add(self._build_ble_navbar())
        return box

    def _build_ble_guide(self) -> ViewNode:
        box = ViewNode("LinearLayout", vid="rn_guide_screen", props={"orientation": 1})
        box.add(self._build_ble_topbar())
        gcard = ViewNode("LinearLayout", vid="rn_guide_card", props={"role": "card"})
        gcard.add(ViewNode("TextView", vid="rn_guide_title", text=self._t("pairing_guide", "Pairing-Anleitung")))
        tip_iv = _img_node("rn_guide_img", self.spec["images"].get("tip1") or self.spec["images"].get("tip2", ""), h=160)
        if tip_iv is not None:
            gcard.add(tip_iv)
        gcard.add(ViewNode("TextView", vid="rn_guide_body", text=self._t("pairing_text")))
        brow = ViewNode("LinearLayout", props={"orientation": "horizontal"})
        b1 = ViewNode("Button", vid="rn_guide_back", text="← Zurück", clickable=True, props={"layout_weight": 1.0})
        b1.on_click = lambda _v=None: self.handle_back()
        b2 = ViewNode("Button", vid="rn_guide_connect", text="Jetzt verbinden", clickable=True, props={"layout_weight": 1.0})
        b2.on_click = lambda _v=None: (
            setattr(self, "connected_device", self.discovered_devices[0] if self.discovered_devices else None),
            self._navigate("control"),
        )
        brow.add(b1)
        brow.add(b2)
        gcard.add(brow)
        box.add(gcard)
        box.add(self._build_ble_navbar())
        return box

    def _build_ble_control(self) -> ViewNode:
        if self.connected_device is None and self.discovered_devices:
            self.connected_device = self.discovered_devices[0]
        box = ViewNode("LinearLayout", vid="rn_control_screen", props={"orientation": 1})
        box.add(self._build_ble_topbar())

        dname = (self.connected_device or {}).get("name", self.spec.get("app_name", "Fridge"))
        dmac = (self.connected_device or {}).get("address", "A4:C1:38:90:12:EF")
        hdr = ViewNode("LinearLayout", vid="rn_ctrl_hdr", props={"role": "card"})
        hdr.add(ViewNode("TextView", vid="rn_ctrl_dev", text=f"🔵 {dname} ({dmac})"))
        hdr.add(ViewNode("TextView", vid="rn_ctrl_sub", text=self._t("under_control", "Unter App-Steuerung")))
        hdr.add(ViewNode("TextView", vid="rn_ctrl_batt", text=f"Spannung: {self.voltage:.1f}V · Batterie-Schutz: {self.battery_prot} · Status: {'AN' if self.power_on else 'STANDBY'}"))
        box.add(hdr)

        if self.spec.get("dual_zone"):
            zrow = ViewNode("LinearLayout", vid="rn_zone_row", props={"orientation": "horizontal"})
            zl = ViewNode("Button", vid="rn_zone_l", text=f"Zone Links ({self._format_temp(self.temp_l)})", clickable=True,
                          props={"layout_weight": 1.0, "selected": (self.zone == "L")})
            zl.on_click = lambda _v=None: (setattr(self, "zone", "L"), self.mount())
            zr = ViewNode("Button", vid="rn_zone_r", text=f"Zone Rechts ({self._format_temp(self.temp_r)})", clickable=True,
                          props={"layout_weight": 1.0, "selected": (self.zone == "R")})
            zr.on_click = lambda _v=None: (setattr(self, "zone", "R"), self.mount())
            zrow.add(zl); zrow.add(zr)
            box.add(zrow)

        tcard = ViewNode("LinearLayout", vid="rn_temp_card", props={"role": "card"})
        tgt = self.temp_l if self.zone == "L" else self.temp_r
        cur = self.cur_temp_l if self.zone == "L" else self.cur_temp_r
        tcard.add(ViewNode("TextView", vid="rn_cur_temp_lbl",
                           text=f"{self._t('cur_temp', 'Aktuelle Temperatur')}: {self._format_temp(cur)}  |  Ziel: {self._format_temp(tgt)}"))

        def _set_tgt(new_c: int):
            if self.locked:
                self._emit_toast("Bediensperre aktiv — erst entsperren")
                return
            clamped = max(-20, min(20, int(new_c)))
            if self.zone == "L":
                self.temp_l = clamped
            else:
                self.temp_r = clamped
            self._ble_write(f"SET_TEMP_{self.zone}", struct.pack(">BBb", 0xFE, 0x05 if self.zone == "L" else 0x06, clamped))
            self.mount()

        trow = ViewNode("LinearLayout", props={"orientation": "horizontal"})
        bmin = ViewNode("Button", vid="rn_temp_minus", text="− 1°", clickable=True, props={"layout_weight": 1.0})
        bmin.on_click = lambda _v=None: _set_tgt(tgt - 1)
        bpls = ViewNode("Button", vid="rn_temp_plus", text="+ 1°", clickable=True, props={"layout_weight": 1.0})
        bpls.on_click = lambda _v=None: _set_tgt(tgt + 1)
        trow.add(bmin); trow.add(bpls)
        tcard.add(trow)

        prog_val = int(round((tgt + 20) * 100 / 40.0))
        slider = ViewNode("SeekBar", vid="rn_temp_slider", clickable=True, props={
            "progress": max(0, min(100, prog_val)), "max": 100, "h": 28,
            "$on_progress_fn": lambda p: _set_tgt(int(round(p * 40 / 100.0)) - 20),
        })
        tcard.add(slider)
        box.add(tcard)

        acard = ViewNode("LinearLayout", vid="rn_actions_card", props={"role": "card"})
        r1 = ViewNode("LinearLayout", props={"orientation": "horizontal"})
        bpwr = ViewNode("Button", vid="rn_btn_power", text=("⏻ Power: AN" if self.power_on else "⏻ Power: AUS"), clickable=True, props={"layout_weight": 1.0})
        def _tog_pwr(_v=None):
            if self.locked:
                self._emit_toast("Gesperrt!")
                return
            self.power_on = not self.power_on
            self._ble_write("SET_POWER", bytes([0xFE, 0xFE, 0x03, 0x01, 1 if self.power_on else 0]))
            self.mount()
        bpwr.on_click = _tog_pwr
        bmod = ViewNode("Button", vid="rn_btn_mode", text=("🌿 ECO" if self.eco_mode else "❄ MAX"), clickable=True, props={"layout_weight": 1.0})
        def _tog_mode(_v=None):
            if self.locked:
                self._emit_toast("Gesperrt!")
                return
            self.eco_mode = not self.eco_mode
            self._ble_write("SET_MODE", bytes([0xFE, 0xFE, 0x03, 0x02, 1 if self.eco_mode else 0]))
            self.mount()
        bmod.on_click = _tog_mode
        r1.add(bpwr); r1.add(bmod)
        acard.add(r1)

        r2 = ViewNode("LinearLayout", props={"orientation": "horizontal"})
        blck = ViewNode("Button", vid="rn_btn_lock", text=("🔒 Gesperrt" if self.locked else "🔓 Entsperrt"), clickable=True, props={"layout_weight": 1.0})
        blck.on_click = lambda _v=None: (
            setattr(self, "locked", not self.locked),
            self._ble_write("SET_LOCK", bytes([0xFE, 0xFE, 0x03, 0x03, 1 if self.locked else 0])),
            self.mount(),
        )
        bunt = ViewNode("Button", vid="rn_btn_unit", text=f"Einheit: °{self.unit}", clickable=True, props={"layout_weight": 1.0})
        bunt.on_click = lambda _v=None: (
            setattr(self, "unit", "F" if self.unit == "C" else "C"),
            self._ble_write("SET_UNIT", bytes([0xFE, 0xFE, 0x03, 0x04, 1 if self.unit == "F" else 0])),
            self.mount(),
        )
        r2.add(blck); r2.add(bunt)
        acard.add(r2)

        if self.spec.get("endpoints"):
            cbtn = ViewNode(
                "Button", vid="rn_cloud_activate_btn",
                text="☁ Gerät aktivieren (API-Sync)", clickable=True,
                props={"bg_drawable": {"kind": "shape", "fill": "#e8eef9", "outline": "#9bb5f5", "radius": 6}},
            )
            def _do_cloud(_v=None):
                ep = self.spec["endpoints"][0]
                res = self._net_fire(
                    "POST", ep, {"Content-Type": "application/json"},
                    json.dumps({"mac": dmac, "device": dname, "pkg": self.spec.get("package")}),
                )
                self.cloud_status = f"API HTTP {res.get('status', 0)}"
                self.mount()
            cbtn.on_click = _do_cloud
            acard.add(cbtn)

        if self.ble_log_summary or self.cloud_status:
            acard.add(ViewNode("TextView", vid="rn_ctrl_log", text=" · ".join(x for x in (self.ble_log_summary, self.cloud_status) if x)))
        box.add(acard)
        box.add(self._build_ble_navbar())
        return box

    def _build_ble_settings(self) -> ViewNode:
        box = ViewNode("LinearLayout", vid="rn_settings_screen", props={"orientation": 1})
        box.add(self._build_ble_topbar())

        scard = ViewNode("LinearLayout", vid="rn_settings_card", props={"role": "card"})
        scard.add(ViewNode("TextView", vid="rn_set_hdr", text=self._t("settings", "Einstellungen")))
        brow = ViewNode("LinearLayout", props={"orientation": "horizontal"})
        for lvl in ("H", "M", "L"):
            btn = ViewNode("Button", vid=f"rn_bat_{lvl}", text=f"Batterie {lvl}", clickable=True,
                           props={"layout_weight": 1.0, "selected": (self.battery_prot == lvl)})
            btn.on_click = lambda _v=None, _l=lvl: (
                setattr(self, "battery_prot", _l),
                self._ble_write(f"SET_BATT_{_l}", b"\xfe\xfe\x03\x05" + _l.encode("ascii")),
                self.mount(),
            )
            brow.add(btn)
        scard.add(brow)
        disc = ViewNode("Button", vid="rn_disconnect_btn", text="Verbindung trennen", clickable=True)
        disc.on_click = lambda _v=None: (setattr(self, "connected_device", None), self._navigate("scan"))
        scard.add(disc)
        box.add(scard)

        tcard = ViewNode("LinearLayout", vid="rn_telem_card", props={"role": "card"})
        tcard.add(ViewNode("TextView", vid="rn_telem_hdr", text="Hermes / React Native Engine (gläsern)"))
        tcard.add(ViewNode("TextView", vid="rn_telem_hbc",
                           text=f"Bundle: {self.spec.get('bundle_kind', '').upper()} v{self.spec.get('hbc_version', 0)} · {self.spec.get('function_count', 0)} Funcs · {self.spec.get('string_count', 0)} Strings"))
        tcard.add(ViewNode("TextView", vid="rn_telem_uuid",
                           text="GATT UUIDs: " + (", ".join(u[:8] for u in (self.spec.get("ble_uuids") or [])[:4]) or "-")))
        tcard.add(ViewNode("TextView", vid="rn_telem_turbo", text=f"TurboModule Calls: {len(self.turbo.log)}"))
        box.add(tcard)
        box.add(self._build_ble_navbar())
        return box

    def _build_auth_or_encrypted_screen(self) -> ViewNode:
        box = ViewNode("LinearLayout", vid="rn_auth_root", props={"orientation": 1})
        box.add(self._build_ble_topbar())
        card = ViewNode("LinearLayout", vid="rn_auth_card", props={"role": "card"})
        bkind = self.spec.get("bundle_kind", "js")
        badge_txt = (
            f"Verschlüsseltes RN-Bundle (Entropie {self.spec.get('bundle_entropy', 0)}) — kein Klartext-JS"
            if bkind == "encrypted"
            else f"React Native ({bkind.upper()}) · {self.spec.get('string_count', 0)} Strings"
        )
        card.add(ViewNode("TextView", vid="rn_auth_badge", text=badge_txt, props={"color": self.spec.get("accent_color", "#0061AC"), "bold": True}))
        if self.screen == "home":
            card.add(ViewNode("TextView", vid="rn_home_title", text=f"{self.spec.get('app_name')} — Übersicht"))
            card.add(ViewNode("TextView", vid="rn_home_info", text=f"User: {self.email or 'demo'} · Endpoints: {len(self.spec.get('endpoints') or ())} · TurboCalls: {len(self.turbo.log)}"))
            for idx, ep in enumerate((self.spec.get("endpoints") or ())[:3]):
                eb = ViewNode("Button", vid=f"rn_ep_btn_{idx}", text=f"🌐 GET {_short_label(ep, 28)}", clickable=True)
                eb.on_click = lambda _v=None, _u=ep: self.open_browser(_u)
                card.add(eb)
            bback = ViewNode("Button", vid="rn_logout_btn", text="Abmelden", clickable=True)
            bback.on_click = lambda _v=None: (setattr(self, "screen", "login"), self.mount())
            card.add(bback)
        else:
            u_in = ViewNode("EditText", vid="rn_login_user", text=self.email, clickable=True, focusable=True,
                            props={"hint": "E-Mail / Benutzer", "$on_change_fn": lambda t: setattr(self, "email", str(t))})
            p_in = ViewNode("EditText", vid="rn_login_pass", text=self.password, clickable=True, focusable=True,
                            props={"hint": "Passwort", "is_password": True, "$on_change_fn": lambda t: setattr(self, "password", str(t))})
            card.add(u_in); card.add(p_in)
            sub = ViewNode("Button", vid="rn_login_submit", text="Anmelden", clickable=True)
            def _do_login(_v=None):
                ep = (self.spec.get("endpoints") or ["https://api.local/login"])[0]
                res = self._net_fire("POST", ep, {"Content-Type": "application/json"},
                                     json.dumps({"username": self.email, "password": self.password}))
                self.auth_status = f"HTTP {res.get('status', 200)}"
                self.screen = "home"
                self.mount()
            sub.on_click = _do_login
            card.add(sub)
            skp = ViewNode("Button", vid="rn_skip_login", text="Direkt zur Übersicht", clickable=True)
            skp.on_click = lambda _v=None: (setattr(self, "screen", "home"), self.mount())
            card.add(skp)
            if self.auth_status:
                card.add(ViewNode("TextView", vid="rn_auth_status", text=self.auth_status))
        box.add(card)
        return box

    def mount(self, touch: bool = True) -> ViewNode:
        self.tree.dark_mode = False
        self.tree.window_bg = "#f3f4f7"
        self.tree.window_bg_dark = False
        if self.screen == "browser":
            content = self.browser.build_browser_nodes()
        elif self.archetype == "ble_iot":
            if self.screen == "guide":
                content = self._build_ble_guide()
            elif self.screen == "control":
                content = self._build_ble_control()
            elif self.screen == "settings":
                content = self._build_ble_settings()
            else:
                content = self._build_ble_scan()
        else:
            content = self._build_auth_or_encrypted_screen()

        cur_root = getattr(self.tree, "root", None)
        if cur_root is not None and getattr(cur_root, "cls", "") == "ReactRootView":
            rn_root = cur_root
            rn_root.props["role"] = "scaffold"
        else:
            rn_root = ViewNode("ReactRootView", vid="rn_root", props={"role": "scaffold"})
            self.tree.root = rn_root
        rn_root.children = [content]
        content._parent = rn_root
        if touch:
            self.tree.touch()
        return rn_root


def mount_rn_app_into_tree(
    tree: ViewTree,
    app_root: str,
    *,
    stubs: Any = None,
    on_async_update: Optional[Callable[[], None]] = None,
) -> RnInPhoneController:
    """Hängt eine interaktive React-Native-In-Phone-UI samt Browser & TurboModuleRouter ein."""
    existing = getattr(tree, "_rn_ctrl", None)
    if isinstance(existing, RnInPhoneController) and existing.app_root == app_root:
        if stubs is not None:
            existing.stubs = stubs
            existing.browser.stubs = stubs
        if on_async_update is not None:
            existing.set_async_updater(on_async_update)
        existing.mount()
        return existing

    ctrl = RnInPhoneController(tree, app_root, stubs=stubs, on_async_update=on_async_update)
    tree._rn_ctrl = ctrl
    ctrl.mount()
    return ctrl
