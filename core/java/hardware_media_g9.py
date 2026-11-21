# -*- coding: utf-8 -*-
"""core.java.hardware_media_g9 — G9: BLE/GATT, GPS/Location, Biometrie, NSD/mDNS & ExoPlayer/Media Seams."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.java.objects import JArray, JObject, JString


def _txt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, JString):
        return v.text or ""
    if isinstance(v, JObject) and "text" in v.fields:
        return str(v.fields["text"] or "")
    return str(v)


def _bytes_from(v: Any) -> bytes:
    if v is None:
        return b""
    data = getattr(v, "data", None)
    if data is not None:
        return bytes(int(x) & 0xFF for x in data)
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    return b""


def _hw(stubs: Any) -> Dict[str, Any]:
    return stubs.__dict__.setdefault("_hw_profile", {
        "gps": {"lat": 48.137154, "lon": 11.576124, "alt": 519.0, "acc": 5.0},
        "biometric_result": "success",  # "success" | "failed" | "error"
        "ble_devices": [
            {"name": "Glass-Tracker-01", "address": "AA:BB:CC:DD:EE:01", "rssi": -54, "services": {
                "0000180f-0000-1000-8000-00805f9b34fb": {"00002a19-0000-1000-8000-00805f9b34fb": b"\x58"},
            }},
        ],
        "nsd_services": [
            {"name": "BabyCam-LivingRoom", "type": "_http._tcp.", "host": "192.168.1.42", "port": 8080},
        ],
        "sensors": {"accelerometer": [0.0, 9.81, 0.0]},
    })


def push_location_update(stubs: Any, lat: float, lon: float, alt: float = 34.0) -> int:
    """Aktualisiert die virtuelle GPS-Position und benachrichtigt registrierte LocationListener."""
    if stubs is None:
        return 0
    hp = _hw(stubs)
    hp["gps"] = {"lat": float(lat), "lon": float(lon), "alt": float(alt), "acc": 5.0}
    loc = _make_location(stubs)
    vm = getattr(stubs, "vm", None)
    notified = 0
    for lst in list(getattr(stubs, "_location_listeners", ()) or ()):
        if isinstance(lst, JObject) and vm is not None and hasattr(vm, "invoke_virtual"):
            for mnm, msig in (
                ("onLocationChanged", "(Landroid/location/Location;)V"),
                ("onLocationResult", "(Lcom/google/android/gms/location/LocationResult;)V"),
            ):
                arg = loc if "Location)" in msig else JObject("Lcom/google/android/gms/location/LocationResult;", {"_locs": [loc]})
                try:
                    vm.invoke_virtual(lst, mnm, msig, (lst, arg))
                    notified += 1
                    break
                except Exception:
                    continue
    stubs.__dict__.setdefault("_hw_log", []).append({"kind": "gps", "lat": float(lat), "lon": float(lon), "notified": notified})
    return notified


def _make_location(stubs: Any) -> JObject:
    gps = _hw(stubs).get("gps") or {}
    return JObject("Landroid/location/Location;", {
        "_lat": float(gps.get("lat", 48.137154)),
        "_lon": float(gps.get("lon", 11.576124)),
        "_alt": float(gps.get("alt", 519.0)),
        "_acc": float(gps.get("acc", 5.0)),
        "_time": int(getattr(stubs, "_clock_ms", 1700000000000)),
    })


def install_g9_stubs(stubs: Any) -> None:
    """Registriert BLE/GATT, Location, BiometricPrompt, NSD & ExoPlayer Stubs additiv."""
    if stubs is None or getattr(stubs, "_g9_installed", False):
        return
    stubs._g9_installed = True
    _hw(stubs)
    stubs.__dict__.setdefault("_location_listeners", [])
    stubs.__dict__.setdefault("_ble_log", [])
    stubs.__dict__.setdefault("_media_events", [])
    stubs.__dict__.setdefault("_hw_log", [])

    def _add(cls: str, name: str, sig: str, fn) -> None:
        if (cls, name, sig) not in stubs._stubs:
            stubs.register(cls, name, sig, fn)

    # ---- 1. Bluetooth / BLE / GATT -----------------------------------------
    BM = "Landroid/bluetooth/BluetoothManager;"
    BA = "Landroid/bluetooth/BluetoothAdapter;"
    BLS = "Landroid/bluetooth/le/BluetoothLeScanner;"
    BD = "Landroid/bluetooth/BluetoothDevice;"
    BG = "Landroid/bluetooth/BluetoothGatt;"
    BGS = "Landroid/bluetooth/BluetoothGattService;"
    BGC = "Landroid/bluetooth/BluetoothGattCharacteristic;"
    SR = "Landroid/bluetooth/le/ScanResult;"

    adapter_obj = JObject(BA, {})
    _add(BM, "getAdapter", f"(){BA}", lambda a: adapter_obj)
    _add(BA, "getDefaultAdapter", f"(){BA}", lambda a: adapter_obj)
    _add(BA, "isEnabled", "()Z", lambda a: 1)
    _add(BA, "getBluetoothLeScanner", f"(){BLS}", lambda a: JObject(BLS, {}))

    def _make_ble_dev(dinfo: dict) -> JObject:
        return JObject(BD, {"_name": dinfo.get("name", "BLE-Device"), "_addr": dinfo.get("address", "AA:BB:CC:DD:EE:01"), "_services": dinfo.get("services") or {}})

    _add(BA, "getRemoteDevice", f"(Ljava/lang/String;){BD}",
         lambda a: _make_ble_dev(next((d for d in _hw(stubs)["ble_devices"] if d.get("address") == _txt(a[1])), {"name": "BLE-Device", "address": _txt(a[1]) if len(a) > 1 else "AA:BB:CC:DD:EE:01"})))
    _add(BD, "getName", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("_name", "BLE-Device")) if a and isinstance(a[0], JObject) else JString(""))
    _add(BD, "getAddress", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("_addr", "")) if a and isinstance(a[0], JObject) else JString(""))
    _add(SR, "getDevice", f"(){BD}", lambda a: a[0].fields.get("_dev") if a and isinstance(a[0], JObject) else None)
    _add(SR, "getRssi", "()I", lambda a: int(a[0].fields.get("_rssi", -54)) if a and isinstance(a[0], JObject) else -54)

    def _ble_scan(a):
        cb = a[-1] if a else None
        vm = getattr(stubs, "vm", None)
        if isinstance(cb, JObject) and vm is not None and hasattr(vm, "invoke_virtual"):
            for dinfo in _hw(stubs).get("ble_devices", ()):
                sr = JObject(SR, {"_dev": _make_ble_dev(dinfo), "_rssi": int(dinfo.get("rssi", -54))})
                try:
                    vm.invoke_virtual(cb, "onScanResult", f"(I{SR})V", (cb, 1, sr))
                except Exception:
                    pass
        stubs._ble_log.append({"op": "startScan", "devices": len(_hw(stubs).get("ble_devices", ()))})
        return None

    _add(BLS, "startScan", "(Landroid/bluetooth/le/ScanCallback;)V", _ble_scan)
    _add(BLS, "startScan", "(Ljava/util/List;Landroid/bluetooth/le/ScanSettings;Landroid/bluetooth/le/ScanCallback;)V", _ble_scan)
    _add(BLS, "stopScan", "(Landroid/bluetooth/le/ScanCallback;)V", lambda a: None)

    def _connect_gatt(a):
        dev = a[0] if a and isinstance(a[0], JObject) else _make_ble_dev(_hw(stubs)["ble_devices"][0])
        cb = next((x for x in a[1:] if isinstance(x, JObject) and x.cls not in ("Landroid/content/Context;", "Landroid/os/Handler;")), None)
        svcs_raw = dev.fields.get("_services") or (_hw(stubs)["ble_devices"][0].get("services") if _hw(stubs)["ble_devices"] else {})
        svcs = {}
        for suuid, chars in svcs_raw.items():
            clist = [JObject(BGC, {"_uuid": cu, "_val": bytes(cv), "_svc_uuid": su}) for cu, cv in chars.items()]
            svcs[suuid] = JObject(BGS, {"_uuid": suuid, "_chars": {c.fields["_uuid"]: c for c in clist}})
        gatt = JObject(BG, {"_dev": dev, "_cb": cb, "_svcs": svcs})
        stubs._ble_log.append({"op": "connectGatt", "addr": dev.fields.get("_addr", "")})
        vm = getattr(stubs, "vm", None)
        if isinstance(cb, JObject) and vm is not None and hasattr(vm, "invoke_virtual"):
            try:
                vm.invoke_virtual(cb, "onConnectionStateChange", f"({BG}II)V", (cb, gatt, 0, 2))
            except Exception:
                pass
        return gatt

    _add(BD, "connectGatt", f"(Landroid/content/Context;ZLandroid/bluetooth/BluetoothGattCallback;){BG}", _connect_gatt)
    _add(BD, "connectGatt", f"(Landroid/content/Context;ZLandroid/bluetooth/BluetoothGattCallback;I){BG}", _connect_gatt)

    def _gatt_discover(a):
        if not a or not isinstance(a[0], JObject):
            return 1
        cb = a[0].fields.get("_cb")
        vm = getattr(stubs, "vm", None)
        if isinstance(cb, JObject) and vm is not None and hasattr(vm, "invoke_virtual"):
            try:
                vm.invoke_virtual(cb, "onServicesDiscovered", f"({BG}I)V", (cb, a[0], 0))
            except Exception:
                pass
        return 1

    _add(BG, "discoverServices", "()Z", _gatt_discover)
    _add(BG, "getServices", "()Ljava/util/List;", lambda a: JObject("Ljava/util/ArrayList;", {"__items": list((a[0].fields.get("_svcs") or {}).values())}) if a and isinstance(a[0], JObject) else JObject("Ljava/util/ArrayList;", {"__items": []}))
    _add(BG, "getService", f"(Ljava/util/UUID;){BGS}", lambda a: (a[0].fields.get("_svcs") or {}).get(_txt(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(BGS, "getUuid", "()Ljava/util/UUID;", lambda a: JObject("Ljava/util/UUID;", {"text": a[0].fields.get("_uuid", "")}) if a and isinstance(a[0], JObject) else None)
    _add(BGS, "getCharacteristic", f"(Ljava/util/UUID;){BGC}", lambda a: (a[0].fields.get("_chars") or {}).get(_txt(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(BGC, "getValue", "()[B", lambda a: JArray("[B", list(a[0].fields.get("_val", b""))) if a and isinstance(a[0], JObject) else JArray("[B", []))
    _add(BGC, "setValue", "([B)Z", lambda a: (a[0].fields.__setitem__("_val", _bytes_from(a[1])), 1)[1] if len(a) > 1 and isinstance(a[0], JObject) else 1)

    def _gatt_rw(a, is_write=False):
        if len(a) < 2 or not isinstance(a[0], JObject) or not isinstance(a[1], JObject):
            return 1
        cb = a[0].fields.get("_cb")
        vm = getattr(stubs, "vm", None)
        stubs._ble_log.append({"op": "writeChar" if is_write else "readChar", "uuid": a[1].fields.get("_uuid", ""), "val": bytes(a[1].fields.get("_val", b""))})
        if isinstance(cb, JObject) and vm is not None and hasattr(vm, "invoke_virtual"):
            m = "onCharacteristicWrite" if is_write else "onCharacteristicRead"
            try:
                vm.invoke_virtual(cb, m, f"({BG}{BGC}I)V", (cb, a[0], a[1], 0))
            except Exception:
                pass
        return 1

    _add(BG, "readCharacteristic", f"({BGC})Z", lambda a: _gatt_rw(a, False))
    _add(BG, "writeCharacteristic", f"({BGC})Z", lambda a: _gatt_rw(a, True))
    _add(BG, "setCharacteristicNotification", f"({BGC}Z)Z", lambda a: 1)

    # ---- 2. GPS / Location & FusedLocationProviderClient -------------------
    LOC = "Landroid/location/Location;"
    _add(LOC, "getLatitude", "()D", lambda a: float(a[0].fields.get("_lat", 48.137154)) if a and isinstance(a[0], JObject) else 48.137154)
    _add(LOC, "getLongitude", "()D", lambda a: float(a[0].fields.get("_lon", 11.576124)) if a and isinstance(a[0], JObject) else 11.576124)
    _add(LOC, "getAltitude", "()D", lambda a: float(a[0].fields.get("_alt", 519.0)) if a and isinstance(a[0], JObject) else 519.0)
    _add(LOC, "getAccuracy", "()F", lambda a: float(a[0].fields.get("_acc", 5.0)) if a and isinstance(a[0], JObject) else 5.0)
    _add(LOC, "getTime", "()J", lambda a: int(a[0].fields.get("_time", 1700000000000)) if a and isinstance(a[0], JObject) else 1700000000000)

    LM = "Landroid/location/LocationManager;"
    _add(LM, "isProviderEnabled", "(Ljava/lang/String;)Z", lambda a: 1)
    _add(LM, "getLastKnownLocation", f"(Ljava/lang/String;){LOC}", lambda a: _make_location(stubs))
    _add(LM, "requestLocationUpdates", "(Ljava/lang/String;JFLandroid/location/LocationListener;)V",
         lambda a: (stubs._location_listeners.append(a[4]), push_location_update(stubs, _hw(stubs)["gps"]["lat"], _hw(stubs)["gps"]["lon"])) if len(a) > 4 and isinstance(a[4], JObject) else None)

    FLC = "Lcom/google/android/gms/location/FusedLocationProviderClient;"
    _add("Lcom/google/android/gms/location/LocationServices;", "getFusedLocationProviderClient", f"(Landroid/content/Context;){FLC}", lambda a: JObject(FLC, {}))
    _add("Lcom/google/android/gms/location/LocationServices;", "getFusedLocationProviderClient", f"(Landroid/app/Activity;){FLC}", lambda a: JObject(FLC, {}))
    _add(FLC, "getLastLocation", "()Lcom/google/android/gms/tasks/Task;",
         lambda a: JObject("Lcom/google/android/gms/tasks/Task;", {"$done": True, "$res": _make_location(stubs)}))
    _add(FLC, "requestLocationUpdates", "(Lcom/google/android/gms/location/LocationRequest;Lcom/google/android/gms/location/LocationCallback;Landroid/os/Looper;)Lcom/google/android/gms/tasks/Task;",
         lambda a: (stubs._location_listeners.append(a[2]) if len(a) > 2 and isinstance(a[2], JObject) else None, JObject("Lcom/google/android/gms/tasks/Task;", {"$done": True, "$res": None}))[1])
    _add("Lcom/google/android/gms/location/LocationResult;", "getLastLocation", f"(){LOC}",
         lambda a: (a[0].fields.get("_locs") or [None])[0] if a and isinstance(a[0], JObject) else _make_location(stubs))

    # ---- 3. BiometricPrompt & BiometricManager ------------------------------
    BMC = "Landroidx/biometric/BiometricManager;"
    BP = "Landroidx/biometric/BiometricPrompt;"
    _add(BMC, "from", f"(Landroid/content/Context;){BMC}", lambda a: JObject(BMC, {}))
    _add(BMC, "canAuthenticate", "()I", lambda a: 0)
    _add(BMC, "canAuthenticate", "(I)I", lambda a: 0)

    def _bp_init(a):
        cb = a[-1] if a else None
        if a and isinstance(a[0], JObject) and isinstance(cb, JObject):
            a[0].fields["_cb"] = cb
        return a[0] if a else None

    for _bp_sig in (
        "(Landroidx/fragment/app/FragmentActivity;Ljava/util/concurrent/Executor;Landroidx/biometric/BiometricPrompt$AuthenticationCallback;)V",
        "(Landroidx/fragment/app/Fragment;Ljava/util/concurrent/Executor;Landroidx/biometric/BiometricPrompt$AuthenticationCallback;)V",
        "(Landroidx/fragment/app/FragmentActivity;Landroidx/biometric/BiometricPrompt$AuthenticationCallback;)V",
    ):
        _add(BP, "<init>", _bp_sig, _bp_init)

    def _bp_auth(a):
        cb = a[0].fields.get("_cb") if a and isinstance(a[0], JObject) else None
        vm = getattr(stubs, "vm", None)
        mode_override = getattr(stubs, "_biometric_mode", None)
        if mode_override:
            mo = str(mode_override).lower()
            mode = "success" if "success" in mo else ("failed" if "fail" in mo else "error")
        else:
            mode = str(_hw(stubs).get("biometric_result", "success")).lower()
        stubs._hw_log.append({"kind": "biometric", "result": mode})
        if isinstance(cb, JObject) and vm is not None and hasattr(vm, "invoke_virtual"):
            try:
                if mode == "success":
                    ar = JObject("Landroidx/biometric/BiometricPrompt$AuthenticationResult;", {"_crypto": a[2] if len(a) > 2 else None})
                    vm.invoke_virtual(cb, "onAuthenticationSucceeded", "(Landroidx/biometric/BiometricPrompt$AuthenticationResult;)V", (cb, ar))
                elif mode == "failed":
                    vm.invoke_virtual(cb, "onAuthenticationFailed", "()V", (cb,))
                else:
                    vm.invoke_virtual(cb, "onAuthenticationError", "(ILjava/lang/CharSequence;)V", (cb, 10, JString("Canceled")))
            except Exception:
                pass
        return None

    _add(BP, "authenticate", "(Landroidx/biometric/BiometricPrompt$PromptInfo;)V", _bp_auth)
    _add(BP, "authenticate", "(Landroidx/biometric/BiometricPrompt$PromptInfo;Landroidx/biometric/BiometricPrompt$CryptoObject;)V", _bp_auth)

    # ---- 4. NsdManager & Media3/ExoPlayer -----------------------------------
    NSD = "Landroid/net/nsd/NsdManager;"
    NSI = "Landroid/net/nsd/NsdServiceInfo;"
    _add(NSI, "getServiceName", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("_name", "")) if a and isinstance(a[0], JObject) else JString(""))
    _add(NSI, "getServiceType", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("_type", "_http._tcp.")) if a and isinstance(a[0], JObject) else JString("_http._tcp."))
    _add(NSI, "getPort", "()I", lambda a: int(a[0].fields.get("_port", 8080)) if a and isinstance(a[0], JObject) else 8080)
    _add(NSI, "getHost", "()Ljava/net/InetAddress;",
         lambda a: JObject("Ljava/net/InetAddress;", {"host": JString(a[0].fields.get("_host", "192.168.1.42"))}) if a and isinstance(a[0], JObject) else None)

    def _nsd_disc(a):
        stype = _txt(a[1]) if len(a) > 1 else "_http._tcp."
        lst = a[-1] if a else None
        vm = getattr(stubs, "vm", None)
        if isinstance(lst, JObject) and vm is not None and hasattr(vm, "invoke_virtual"):
            for svc in _hw(stubs).get("nsd_services", ()):
                info = JObject(NSI, {"_name": svc.get("name", "Device"), "_type": svc.get("type", stype), "_host": svc.get("host", "192.168.1.42"), "_port": int(svc.get("port", 8080))})
                try:
                    vm.invoke_virtual(lst, "onServiceFound", f"({NSI})V", (lst, info))
                except Exception:
                    pass
        return None

    def _nsd_resolve(a):
        info = a[1] if len(a) > 1 and isinstance(a[1], JObject) else None
        lst = a[-1] if a else None
        if info is not None and "_host" not in info.fields:
            svc0 = (_hw(stubs).get("nsd_services") or [{}])[0]
            info.fields.setdefault("_host", svc0.get("host", "192.168.1.42"))
            info.fields.setdefault("_port", int(svc0.get("port", 8080)))
        vm = getattr(stubs, "vm", None)
        if info is not None and isinstance(lst, JObject) and vm is not None and hasattr(vm, "invoke_virtual"):
            try:
                vm.invoke_virtual(lst, "onServiceResolved", f"({NSI})V", (lst, info))
            except Exception:
                pass
        return None

    _add(NSD, "discoverServices", "(Ljava/lang/String;ILandroid/net/nsd/NsdManager$DiscoveryListener;)V", _nsd_disc)
    _add(NSD, "resolveService", f"({NSI}Landroid/net/nsd/NsdManager$ResolveListener;)V", _nsd_resolve)
    _add(NSD, "stopServiceDiscovery", "(Landroid/net/nsd/NsdManager$DiscoveryListener;)V", lambda a: None)

    for _exo_pkg in ("Landroidx/media3/common", "Lcom/google/android/exoplayer2"):
        _exo = "Landroidx/media3/exoplayer/ExoPlayer;" if "media3" in _exo_pkg else f"{_exo_pkg}/ExoPlayer;"
        _exob = f"{_exo[:-1]}$Builder;"
        _mi = f"{_exo_pkg}/MediaItem;"
        _add(_mi, "fromUri", f"(Ljava/lang/String;){_mi}", lambda a, c=_mi: JObject(c, {"_uri": _txt(a[0]) if a else ""}))
        _add(_mi, "fromUri", f"(Landroid/net/Uri;){_mi}", lambda a, c=_mi: JObject(c, {"_uri": a[0].fields.get("$uri", "") if a and isinstance(a[0], JObject) else ""}))
        _add(_exob, "<init>", "(Landroid/content/Context;)V", lambda a: a[0])
        _add(_exob, "build", f"(){_exo}", lambda a, c=_exo: JObject(c, {"_playing": False, "_state": 1}))
        _add(_exo, "setMediaItem", f"({_mi})V",
             lambda a: stubs._media_events.append({"kind": "exo_setMediaItem", "uri": a[1].fields.get("_uri", "")}) if len(a) > 1 and isinstance(a[1], JObject) else None)
        _add(_exo, "prepare", "()V", lambda a: a[0].fields.__setitem__("_state", 3) if a and isinstance(a[0], JObject) else None)
        _add(_exo, "play", "()V", lambda a: (a[0].fields.update({"_playing": True, "_state": 3}), None)[1] if a and isinstance(a[0], JObject) else None)
        _add(_exo, "pause", "()V", lambda a: a[0].fields.__setitem__("_playing", False) if a and isinstance(a[0], JObject) else None)
        _add(_exo, "setPlayWhenReady", "(Z)V", lambda a: a[0].fields.__setitem__("_playing", bool(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
        _add(_exo, "getPlayWhenReady", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_playing")) else 0)
        _add(_exo, "isPlaying", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_playing")) else 0)
        _add(_exo, "getPlaybackState", "()I", lambda a: int(a[0].fields.get("_state", 1)) if a and isinstance(a[0], JObject) else 1)
        _add(_exo, "release", "()V", lambda a: None)
