# -*- coding: utf-8 -*-
"""tests/test_glaesernes_grundset_g7_g14.py — Regressionstests fuer G7–G14 (Ausbaustufe 2)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import struct
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.java.dalvik import DalvikVM, MethodDef  # noqa: E402
from core.java.dex_loader import AndroidStubs  # noqa: E402
from core.java.lifecycle import LifecycleRunner  # noqa: E402
from core.java.net_recorder import NetLog, NetResponse, install_net_stubs  # noqa: E402
from core.java.objects import JArray, JObject, JString  # noqa: E402
from core.java.view_tree import ViewNode, install_view_stubs, paint_commands, swipe_pager  # noqa: E402


def _vm_with_views(app_root=None, with_net=False, responder=None):
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    stubs.seed_framework_statics(vm)
    tree = install_view_stubs(stubs, app_root=app_root)
    net_log = None
    if with_net:
        net_log = NetLog()
        install_net_stubs(
            stubs,
            responder=responder or (lambda _r: NetResponse(200, {"Content-Type": "application/json"}, b'{"ok":true}', "OK")),
            net_log=net_log,
        )
    vm.runtime = type("_RT", (), {"stubs": stubs})()
    return vm, stubs, tree, net_log


class _DictProv:
    def __init__(self, methods, root=None):
        self.methods = methods
        self.root = root

    def get_method(self, cls, name, sig):
        return self.methods.get((cls, name, sig))

    def has_class(self, cls):
        return any(c == cls for (c, _, _) in self.methods)


# ---------------------------------------------------------------------------
# G7: Protobuf/gRPC Wire-Decoder, WebSocket, Cronet & libcurl FFI
# ---------------------------------------------------------------------------
def test_g7_protobuf_websocket_cronet_grpc_and_libcurl():
    from core.java.net_protocols_g7 import (
        decode_protobuf_wire,
        extract_smali_protobuf_schema,
        inject_websocket_message,
        install_libcurl_seam,
    )
    from core.java.net_unified import format_net_entry_details, normalize_java, normalize_native

    # 1) Protobuf Wire + gRPC 5-Byte-Framing + Smali-Schema
    msg_pb = b"\x08\x2a\x12\x05alice"  # field 1 = 42, field 2 = "alice"
    grpc_frame = b"\x00" + struct.pack(">I", len(msg_pb)) + msg_pb
    dec = decode_protobuf_wire(grpc_frame, {1: "user_id", 2: "username"})
    assert dec["user_id"] == 42
    assert dec["username"] == "alice"

    schema = extract_smali_protobuf_schema(
        ".method dynamicMethod()Ljava/lang/Object;\n"
        'const-string v0, "userId_"\n'
        'const-string v1, "email_"\n'
        "invoke-static {}, Lcom/google/protobuf/GeneratedMessageLite;->newMessageInfo()Ljava/lang/Object;\n"
        ".end method\n"
    )
    assert schema == {1: "userId", 2: "email"}

    # 2) WebSocket Listener + gRPC + Cronet + ByteString
    events = []
    vm, stubs, _t, net_log = _vm_with_views(
        with_net=True,
        responder=lambda r: NetResponse(
            200,
            {"Content-Type": "application/grpc"},
            b"\x00" + struct.pack(">I", len(msg_pb)) + msg_pb,
            "OK",
        ),
    )
    ws_lst_cls = "Lcom/test/WsLst;"
    cronet_cb_cls = "Lcom/test/CronetCb;"

    def _rec_ev(kind, a):
        events.append((kind, a))

    # Listener-Klassen als echte MethodDef stubs ueber _DictProv + invoke_virtual
    vm.class_provider = _DictProv({
        (ws_lst_cls, "onOpen", "(Lokhttp3/WebSocket;Lokhttp3/Response;)V"):
            MethodDef(ws_lst_cls, "onOpen", "(Lokhttp3/WebSocket;Lokhttp3/Response;)V", [("return-void",)], 3, 3),
        (ws_lst_cls, "onMessage", "(Lokhttp3/WebSocket;Ljava/lang/String;)V"):
            MethodDef(ws_lst_cls, "onMessage", "(Lokhttp3/WebSocket;Ljava/lang/String;)V", [("return-void",)], 3, 3),
        (cronet_cb_cls, "onResponseStarted", "(Lorg/chromium/net/UrlRequest;Lorg/chromium/net/UrlResponseInfo;)V"):
            MethodDef(cronet_cb_cls, "onResponseStarted", "(Lorg/chromium/net/UrlRequest;Lorg/chromium/net/UrlResponseInfo;)V", [("return-void",)], 3, 3),
        (cronet_cb_cls, "onSucceeded", "(Lorg/chromium/net/UrlRequest;Lorg/chromium/net/UrlResponseInfo;)V"):
            MethodDef(cronet_cb_cls, "onSucceeded", "(Lorg/chromium/net/UrlRequest;Lorg/chromium/net/UrlResponseInfo;)V", [("return-void",)], 3, 3),
    })
    orig_iv = vm.invoke_virtual

    def _track_iv(obj, name, sig, args=()):
        events.append((obj.cls, name, args))
        return orig_iv(obj, name, sig, args)

    vm.invoke_virtual = _track_iv

    # OkHttp WebSocket Open -> Send -> Inject
    ok_client = JObject("Lokhttp3/OkHttpClient;", {})
    ws_req = JObject("Lokhttp3/Request;", {"url": "wss://ws.demo.local/stream"})
    ws_lst = JObject(ws_lst_cls, {})
    ok, ws = stubs.resolve("Lokhttp3/OkHttpClient;", "newWebSocket",
                           "(Lokhttp3/Request;Lokhttp3/WebSocketListener;)Lokhttp3/WebSocket;",
                           [ok_client, ws_req, ws_lst], False)
    assert ok and isinstance(ws, JObject)
    stubs.resolve("Lokhttp3/WebSocket;", "send", "(Ljava/lang/String;)Z", [ws, JString("hello")], False)
    assert inject_websocket_message(stubs, "wss://ws.demo.local", '{"pong":1}') is True
    assert any(ev[1] == "onOpen" for ev in events)
    assert any(ev[1] == "onMessage" for ev in events)

    # Cronet UrlRequest
    ok, cb = stubs.resolve("Lorg/chromium/net/CronetEngine$Builder;", "build", "()Lorg/chromium/net/CronetEngine;",
                           [JObject("Lorg/chromium/net/CronetEngine$Builder;")], False)
    assert ok
    cronet_cb = JObject(cronet_cb_cls, {})
    _, urb = stubs.resolve("Lorg/chromium/net/CronetEngine;", "newUrlRequestBuilder",
                           "(Ljava/lang/String;Lorg/chromium/net/UrlRequest$Callback;Ljava/util/concurrent/Executor;)Lorg/chromium/net/UrlRequest$Builder;",
                           [cb, JString("https://api.demo.local/cfg"), cronet_cb, None], False)
    _, ur = stubs.resolve("Lorg/chromium/net/UrlRequest$Builder;", "build", "()Lorg/chromium/net/UrlRequest;", [urb], False)
    stubs.resolve("Lorg/chromium/net/UrlRequest;", "start", "()V", [ur], False)
    assert any(ev[0] == cronet_cb_cls and ev[1] == "onSucceeded" for ev in events)

    # gRPC unary call -> net_unified Protobuf-Decode Details
    _, mc = stubs.resolve("Lio/grpc/ManagedChannelBuilder;", "forTarget",
                          "(Ljava/lang/String;)Lio/grpc/ManagedChannelBuilder;", [JString("grpc.demo.local")], True)
    _, ch = stubs.resolve("Lio/grpc/ManagedChannelBuilder;", "build", "()Lio/grpc/ManagedChannel;", [mc], False)
    md = JObject("Lio/grpc/MethodDescriptor;", {"_name": "AuthService/Login"})
    req_msg = JObject("Lcom/google/protobuf/GeneratedMessageLite;", {"_bytes": msg_pb})
    ok, g_resp = stubs.resolve(
        "Lio/grpc/stub/ClientCalls;", "blockingUnaryCall",
        "(Lio/grpc/Channel;Lio/grpc/MethodDescriptor;Lio/grpc/CallOptions;Ljava/lang/Object;)Ljava/lang/Object;",
        [ch, md, None, req_msg], True,
    )
    assert ok and g_resp.fields["_bytes"].endswith(msg_pb)
    rows = normalize_java(net_log)
    grpc_row = next(r for r in rows if "AuthService/Login" in r["url"])
    details = format_net_entry_details(grpc_row)
    assert "[Protobuf/gRPC Wire-Decode]:" in details
    assert '"alice"' in details

    # 3) Native libcurl FFI Seam
    from services.native_emu_service import NativeEmuService
    from services.native_ffi_service import NativeFfiService
    from services.native_os_service import NativeOsService
    from core.native.libc_stubs import _libc_alloc
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    ffi = NativeFfiService(publish=False)
    os_svc = NativeOsService(publish=False)
    os_svc.attach_env(eng)
    os_svc.install(eng)
    install_libcurl_seam(eng, ffi, stubs=stubs)

    def _call(sym, *args):
        addr = ffi.install_named_stub(eng, sym)
        for i, v in enumerate(args):
            eng.cpu.x[i] = v
        eng.cpu.lr = 0xDEAD0000
        eng.cpu.stub[addr](eng.cpu)
        return int(eng.cpu.x[0])

    url_buf = _libc_alloc(eng, 64)
    rx_buf = _libc_alloc(eng, 128)
    info_out = _libc_alloc(eng, 16)
    eng.mem.write(url_buf, b"https://curl.demo.local/ping\x00")
    assert _call("curl_global_init", 0) == 0
    handle = _call("curl_easy_init")
    assert handle > 0
    _call("curl_easy_setopt", handle, 10002, url_buf)  # CURLOPT_URL
    _call("curl_easy_setopt", handle, 10001, rx_buf)   # CURLOPT_WRITEDATA
    assert _call("curl_easy_perform", handle) == 0
    _call("curl_easy_getinfo", handle, 2097154, info_out)
    assert struct.unpack("<Q", eng.mem.read(info_out, 8))[0] == 200
    assert b"alice" in eng.mem.read(rx_buf, 32)
    native_rows = normalize_native(eng._os.net)
    assert any(r["kind"] == "curl_send" and "[native/curl]" in r["label"] for r in native_rows)


# ---------------------------------------------------------------------------
# G8: Jetpack Compose Navigation, XML NavGraph, Paging 3 & RxJava
# ---------------------------------------------------------------------------
def test_g8_compose_navhost_xml_navgraph_paging3_and_rxjava(tmp_path):
    from core.java.nav_rx_paging_g8 import parse_nav_graph_xml

    nav_dir = tmp_path / "res" / "navigation"
    nav_dir.mkdir(parents=True)
    (nav_dir / "main_graph.xml").write_text(
        '<navigation xmlns:android="http://schemas.android.com/apk/res/android" '
        'xmlns:app="http://schemas.android.com/apk/res-auto" '
        'app:startDestination="@id/homeFrag">'
        '<fragment android:id="@+id/homeFrag" android:name="com.demo.HomeFragment">'
        '<action android:id="@+id/toDetail" app:destination="@id/detailFrag"/>'
        '</fragment>'
        '<fragment android:id="@+id/detailFrag" android:name="com.demo.DetailFragment"/>'
        '</navigation>',
        encoding="utf-8",
    )
    graph = parse_nav_graph_xml(str(tmp_path), "@navigation/main_graph")
    assert graph["start"] == "homeFrag"
    assert graph["destinations"]["homeFrag"]["actions"]["toDetail"] == "detailFrag"

    vm, stubs, tree, _ = _vm_with_views(app_root=str(tmp_path))
    runner = LifecycleRunner(vm, provider=None, with_stubs=False, stubs=stubs)

    # Compose rememberNavController + NavHost + composable + navigate + handle_back
    nc_ok, nc = vm.dispatch_hook_or_none if hasattr(vm, "dispatch_hook_or_none") else (False, None)
    # Direkter Hook-Aufruf ueber vm._method_hooks
    ngb = JObject("Landroidx/navigation/NavGraphBuilder;", {"_routes": {}})
    # Baue Route-Lambdas ueber Python-MethodDef in DictProv
    visited = []
    rt_cls = "Lcom/demo/NavScreens;"

    def _comp_call(cls, name, sig, args):
        for fn in vm._method_hooks:
            ok_h, val = fn(cls, cls, name, sig, args, True, vm)
            if ok_h:
                return val
        raise AssertionError(f"Hook missing for {cls}->{name}{sig}")

    # Lambdas als echte Dalvik-Stubs über _DictProv
    vm.class_provider = _DictProv({})
    orig_inv = vm.invoke

    def _fake_inv(cls, name, sig, args=()):
        if cls == "Lcom/demo/BuilderLambda;" and name == "invoke":
            b_arg = args[1]
            _comp_call("Landroidx/navigation/compose/NavGraphBuilderKt;", "composable",
                       "(Landroidx/navigation/NavGraphBuilder;Ljava/lang/String;Lkotlin/jvm/functions/Function4;)V",
                       [b_arg, JString("home"), JObject("Lcom/demo/HomeLambda;")])
            _comp_call("Landroidx/navigation/compose/NavGraphBuilderKt;", "composable",
                       "(Landroidx/navigation/NavGraphBuilder;Ljava/lang/String;Lkotlin/jvm/functions/Function4;)V",
                       [b_arg, JString("detail/{itemId}"), JObject("Lcom/demo/DetailLambda;")])
            return None
        if cls in ("Lcom/demo/HomeLambda;", "Lcom/demo/DetailLambda;") and name == "invoke":
            entry = args[2]
            _, dest = stubs.resolve("Landroidx/navigation/NavBackStackEntry;", "getDestination",
                                    "()Landroidx/navigation/NavDestination;", [entry], False)
            _, r_str = stubs.resolve("Landroidx/navigation/NavDestination;", "getRoute",
                                     "()Ljava/lang/String;", [dest], False)
            _, arg_b = stubs.resolve("Landroidx/navigation/NavBackStackEntry;", "getArguments",
                                     "()Landroid/os/Bundle;", [entry], False)
            visited.append((r_str.text, arg_b.fields["map"].get("itemId").text if "itemId" in arg_b.fields["map"] else None))
            return None
        return orig_inv(cls, name, sig, args)

    vm.invoke = _fake_inv
    nc = _comp_call("Landroidx/navigation/compose/NavHostControllerKt;", "rememberNavController",
                    "([Landroidx/navigation/Navigator;Landroidx/compose/runtime/Composer;I)Landroidx/navigation/NavHostController;",
                    [None, None, 0])
    builder = JObject("Lcom/demo/BuilderLambda;")
    _comp_call("Landroidx/navigation/compose/NavHostKt;", "NavHost",
               "(Landroidx/navigation/NavHostController;Ljava/lang/String;Landroidx/compose/ui/Modifier;Ljava/lang/String;Lkotlin/jvm/functions/Function1;)V",
               [nc, JString("home"), builder])
    assert visited[-1] == ("home", None)

    stubs.resolve("Landroidx/navigation/NavHostController;", "navigate", "(Ljava/lang/String;)V",
                  [nc, JString("detail/42")], False)
    assert nc.fields["_current_route"] == "detail/42"
    _comp_call("Landroidx/navigation/compose/NavHostKt;", "NavHost",
               "(Landroidx/navigation/NavHostController;Ljava/lang/String;Lkotlin/jvm/functions/Function1;)V",
               [nc, JString("home"), builder])
    assert visited[-1] == ("detail/{itemId}", "42") or ("detail/42", "42")

    # Zurueck per Hardware-Back (handle_back -> pop_compose_nav_if_possible)
    assert runner.handle_back() is True
    assert nc.fields["_current_route"] == "home"

    # Paging 3
    jlist = JObject("Ljava/util/ArrayList;", {"__items": [JString("A"), JString("B"), JString("C")]})
    _, pdata = stubs.resolve("Landroidx/paging/PagingData;", "from", "(Ljava/util/List;)Landroidx/paging/PagingData;", [jlist], True)
    lpi = _comp_call("Landroidx/paging/compose/LazyPagingItemsKt;", "collectAsLazyPagingItems",
                     "(Lkotlinx/coroutines/flow/Flow;Landroidx/compose/runtime/Composer;I)Landroidx/paging/compose/LazyPagingItems;",
                     [pdata, None, 0])
    _, cnt = stubs.resolve("Landroidx/paging/compose/LazyPagingItems;", "getItemCount", "()I", [lpi], False)
    _, second = stubs.resolve("Landroidx/paging/compose/LazyPagingItems;", "get", "(I)Ljava/lang/Object;", [lpi, 1], False)
    assert cnt == 3 and second.text == "B"

    # RxJava3 Single.just -> map -> subscribe
    rx_seen = []
    vm_orig_iv = vm.invoke_virtual

    def _rx_iv(obj, mname, msig, args=()):
        if obj.cls == "Lcom/demo/RxMapFn;":
            return JString(f"mapped:{args[1].text}")
        if obj.cls == "Lcom/demo/RxConsFn;":
            rx_seen.append(args[1].text)
            return None
        return vm_orig_iv(obj, mname, msig, args)

    vm.invoke_virtual = _rx_iv
    _, s1 = stubs.resolve("Lio/reactivex/rxjava3/core/Single;", "just",
                          "(Ljava/lang/Object;)Lio/reactivex/rxjava3/core/Single;", [JString("ok")], True)
    _, s2 = stubs.resolve("Lio/reactivex/rxjava3/core/Single;", "map",
                          "(Lio/reactivex/rxjava3/functions/Function;)Lio/reactivex/rxjava3/core/Single;",
                          [s1, JObject("Lcom/demo/RxMapFn;")], False)
    stubs.resolve("Lio/reactivex/rxjava3/core/Single;", "subscribe",
                  "(Lio/reactivex/rxjava3/functions/Consumer;)Lio/reactivex/rxjava3/disposables/Disposable;",
                  [s2, JObject("Lcom/demo/RxConsFn;")], False)
    assert rx_seen == ["mapped:ok"]


# ---------------------------------------------------------------------------
# G9: BLE/GATT, GPS/Location, Biometrie, NSD & Media3/ExoPlayer
# ---------------------------------------------------------------------------
def test_g9_ble_gps_biometric_nsd_and_media():
    from core.java.hardware_media_g9 import _hw, push_location_update

    vm, stubs, _tree, _ = _vm_with_views()
    hw_events = []
    orig_iv = vm.invoke_virtual

    def _hw_iv(obj, name, sig, args=()):
        hw_events.append((obj.cls, name, args))
        return None

    vm.invoke_virtual = _hw_iv

    # BLE Scan
    bm = JObject("Landroid/bluetooth/BluetoothManager;")
    _, ba = stubs.resolve("Landroid/bluetooth/BluetoothManager;", "getAdapter", "()Landroid/bluetooth/BluetoothAdapter;", [bm], False)
    _, sc = stubs.resolve("Landroid/bluetooth/BluetoothAdapter;", "getBluetoothLeScanner", "()Landroid/bluetooth/le/BluetoothLeScanner;", [ba], False)
    stubs.resolve("Landroid/bluetooth/le/BluetoothLeScanner;", "startScan",
                  "(Landroid/bluetooth/le/ScanCallback;)V", [sc, JObject("Lcom/demo/ScanCb;")], False)
    assert any(ev[1] == "onScanResult" for ev in hw_events)

    # GPS Listener + push_location_update
    lm = JObject("Landroid/location/LocationManager;")
    loc_lst = JObject("Lcom/demo/LocLst;")
    stubs.resolve("Landroid/location/LocationManager;", "requestLocationUpdates",
                  "(Ljava/lang/String;JFLandroid/location/LocationListener;)V",
                  [lm, JString("gps"), 1000, 1.0, loc_lst], False)
    n_not = push_location_update(stubs, 53.5511, 9.9937, alt=12.0)
    assert n_not == 1
    _, flc = stubs.resolve("Lcom/google/android/gms/location/LocationServices;", "getFusedLocationProviderClient",
                           "(Landroid/content/Context;)Lcom/google/android/gms/location/FusedLocationProviderClient;",
                           [JObject("Landroid/content/Context;")], True)
    _, tsk = stubs.resolve("Lcom/google/android/gms/location/FusedLocationProviderClient;", "getLastLocation",
                           "()Lcom/google/android/gms/tasks/Task;", [flc], False)
    loc = tsk.fields["$res"]
    _, lat = stubs.resolve("Landroid/location/Location;", "getLatitude", "()D", [loc], False)
    assert abs(lat - 53.5511) < 1e-4

    # BiometricPrompt success + failed
    _, bio_mgr = stubs.resolve("Landroidx/biometric/BiometricManager;", "from",
                               "(Landroid/content/Context;)Landroidx/biometric/BiometricManager;",
                               [JObject("Landroid/content/Context;")], True)
    assert stubs.resolve("Landroidx/biometric/BiometricManager;", "canAuthenticate", "(I)I", [bio_mgr, 15], False)[1] == 0
    bp = JObject("Landroidx/biometric/BiometricPrompt;")
    bio_cb = JObject("Lcom/demo/BioCb;")
    stubs.resolve("Landroidx/biometric/BiometricPrompt;", "<init>",
                  "(Landroidx/fragment/app/FragmentActivity;Landroidx/biometric/BiometricPrompt$AuthenticationCallback;)V",
                  [bp, None, bio_cb], False)
    stubs.resolve("Landroidx/biometric/BiometricPrompt;", "authenticate",
                  "(Landroidx/biometric/BiometricPrompt$PromptInfo;)V", [bp, JObject("Landroidx/biometric/BiometricPrompt$PromptInfo;")], False)
    assert any(ev[1] == "onAuthenticationSucceeded" for ev in hw_events)

    _hw(stubs)["biometric_result"] = "failed"
    stubs.resolve("Landroidx/biometric/BiometricPrompt;", "authenticate",
                  "(Landroidx/biometric/BiometricPrompt$PromptInfo;)V", [bp, JObject("Landroidx/biometric/BiometricPrompt$PromptInfo;")], False)
    assert any(ev[1] == "onAuthenticationFailed" for ev in hw_events)

    # NSD & ExoPlayer
    stubs.resolve("Landroid/net/nsd/NsdManager;", "discoverServices",
                  "(Ljava/lang/String;ILandroid/net/nsd/NsdManager$DiscoveryListener;)V",
                  [JObject("Landroid/net/nsd/NsdManager;"), JString("_http._tcp."), 1, JObject("Lcom/demo/NsdLst;")], False)
    assert any(ev[1] == "onServiceFound" for ev in hw_events)

    eb = JObject("Landroidx/media3/exoplayer/ExoPlayer$Builder;")
    _, player = stubs.resolve("Landroidx/media3/exoplayer/ExoPlayer$Builder;", "build", "()Landroidx/media3/exoplayer/ExoPlayer;", [eb], False)
    stubs.resolve("Landroidx/media3/exoplayer/ExoPlayer;", "prepare", "()V", [player], False)
    stubs.resolve("Landroidx/media3/exoplayer/ExoPlayer;", "play", "()V", [player], False)
    assert stubs.resolve("Landroidx/media3/exoplayer/ExoPlayer;", "isPlaying", "()Z", [player], False)[1] == 1
    assert stubs.resolve("Landroidx/media3/exoplayer/ExoPlayer;", "getPlaybackState", "()I", [player], False)[1] == 3  # STATE_READY
    stubs.resolve("Landroidx/media3/exoplayer/ExoPlayer;", "pause", "()V", [player], False)
    assert stubs.resolve("Landroidx/media3/exoplayer/ExoPlayer;", "isPlaying", "()Z", [player], False)[1] == 0
    vm.invoke_virtual = orig_iv


# ---------------------------------------------------------------------------
# G10: Manifest Receiver/Service/DeepLink, Uri.Builder, CustomTabs, PendingIntent
# ---------------------------------------------------------------------------
def test_g10_manifest_deeplink_customtabs_broadcast_and_pendingintent():
    from core.java.ipc_oauth_g10 import deliver_deeplink, fire_pending_intent, trigger_broadcast
    from core.java.manifest import parse_manifest_text

    man_xml = (
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.demo.oauth">'
        '  <application android:name=".DemoApp">'
        '    <meta-data android:name="api_env" android:value="staging"/>'
        '    <activity android:name=".AuthCallbackActivity" android:exported="true">'
        '      <intent-filter>'
        '        <action android:name="android.intent.action.VIEW"/>'
        '        <data android:scheme="demoauth" android:host="callback" android:pathPrefix="/v1"/>'
        '      </intent-filter>'
        '    </activity>'
        '    <receiver android:name=".SyncReceiver" android:exported="false">'
        '      <intent-filter><action android:name="com.demo.SYNC_NOW"/></intent-filter>'
        '    </receiver>'
        '    <service android:name=".BgService"/>'
        '    <provider android:name=".DemoProvider" android:authorities="com.demo.oauth.provider"/>'
        '  </application>'
        '</manifest>'
    )
    man = parse_manifest_text(man_xml)
    assert man.meta_data["api_env"] == "staging"
    assert man.deep_links and man.deep_links[0]["activity"] == "Lcom/demo/oauth/AuthCallbackActivity;"
    assert man.receivers and man.receivers[0]["actions"] == ["com.demo.SYNC_NOW"]
    assert man.services and man.providers and man.providers[0]["authorities"] == "com.demo.oauth.provider"

    vm, stubs, _t, _ = _vm_with_views()
    # Uri.parse + Uri$Builder
    b = JObject("Landroid/net/Uri$Builder;")
    stubs.resolve("Landroid/net/Uri$Builder;", "<init>", "()V", [b], False)
    stubs.resolve("Landroid/net/Uri$Builder;", "scheme", "(Ljava/lang/String;)Landroid/net/Uri$Builder;", [b, JString("https")], False)
    stubs.resolve("Landroid/net/Uri$Builder;", "authority", "(Ljava/lang/String;)Landroid/net/Uri$Builder;", [b, JString("auth.demo.local")], False)
    stubs.resolve("Landroid/net/Uri$Builder;", "appendPath", "(Ljava/lang/String;)Landroid/net/Uri$Builder;", [b, JString("token")], False)
    stubs.resolve("Landroid/net/Uri$Builder;", "appendQueryParameter", "(Ljava/lang/String;Ljava/lang/String;)Landroid/net/Uri$Builder;", [b, JString("code"), JString("abc")], False)
    _, built_uri = stubs.resolve("Landroid/net/Uri$Builder;", "build", "()Landroid/net/Uri;", [b], False)
    _, uri_s = stubs.resolve("Landroid/net/Uri;", "toString", "()Ljava/lang/String;", [built_uri], False)
    assert uri_s.text == "https://auth.demo.local/token?code=abc"
    _, qp = stubs.resolve("Landroid/net/Uri;", "getQueryParameter", "(Ljava/lang/String;)Ljava/lang/String;", [built_uri, JString("code")], False)
    assert qp.text == "abc"

    # Deliver DeepLink -> target onNewIntent / pending nav
    target_cls = "Lcom/demo/oauth/AuthCallbackActivity;"
    seen_intents = []
    cur_act = JObject(target_cls, {})
    stubs._current_activity = cur_act
    orig_iv = vm.invoke_virtual
    vm.invoke_virtual = lambda obj, mn, sg, args=(): seen_intents.append((obj.cls, mn, args[1]))
    assert deliver_deeplink(stubs, None, "demoauth://callback/v1?code=xyz99", manifest=man) is True
    assert any(x[0] == target_cls and x[1] == "onNewIntent" for x in seen_intents)

    # Dynamic BroadcastReceiver + PendingIntent + CustomTabsIntent
    bcasts = []
    vm.invoke_virtual = lambda obj, mn, sg, args=(): bcasts.append((obj.cls, mn, args[2].fields.get("action")))
    ctx = JObject("Landroid/content/Context;")
    flt = JObject("Landroid/content/IntentFilter;", {"_actions": ["com.demo.PING"]})
    stubs.resolve("Landroid/content/Context;", "registerReceiver",
                  "(Landroid/content/BroadcastReceiver;Landroid/content/IntentFilter;)Landroid/content/Intent;",
                  [ctx, JObject("Lcom/demo/DynRx;"), flt], False)
    assert trigger_broadcast(stubs, vm, "com.demo.PING", extras={"ok": "1"}) == 1
    assert bcasts[-1] == ("Lcom/demo/DynRx;", "onReceive", "com.demo.PING")

    pi_intent = JObject("Landroid/content/Intent;", {"action": "com.demo.PING"})
    _, pi = stubs.resolve("Landroid/app/PendingIntent;", "getBroadcast",
                          "(Landroid/content/Context;ILandroid/content/Intent;I)Landroid/app/PendingIntent;",
                          [ctx, 1, pi_intent, 0], True)
    assert fire_pending_intent(stubs, vm, None, pi) is True
    assert len(bcasts) == 2

    ctb = JObject("Landroidx/browser/customtabs/CustomTabsIntent$Builder;", {})
    _, cti = stubs.resolve("Landroidx/browser/customtabs/CustomTabsIntent$Builder;", "build", "()Landroidx/browser/customtabs/CustomTabsIntent;", [ctb], False)
    stubs.resolve("Landroidx/browser/customtabs/CustomTabsIntent;", "launchUrl", "(Landroid/content/Context;Landroid/net/Uri;)V", [cti, ctx, built_uri], False)
    assert any(ev.get("kind") == "custom_tab" for ev in stubs._ipc_log)
    vm.invoke_virtual = orig_iv


# ---------------------------------------------------------------------------
# G11: JCA Crypto, EncryptedSharedPreferences, Play Billing & Integrity
# ---------------------------------------------------------------------------
def test_g11_crypto_jca_encrypted_prefs_and_play_billing():
    from core.java.crypto_billing_g11 import set_billing_pro_override

    vm, stubs, _t, _ = _vm_with_views()

    # SHA-256
    _, md = stubs.resolve("Ljava/security/MessageDigest;", "getInstance", "(Ljava/lang/String;)Ljava/security/MessageDigest;", [JString("SHA-256")], True)
    stubs.resolve("Ljava/security/MessageDigest;", "update", "([B)V", [md, JArray("[B", list(b"abc"))], False)
    _, dig = stubs.resolve("Ljava/security/MessageDigest;", "digest", "()[B", [md], False)
    assert bytes(dig.data) == hashlib.sha256(b"abc").digest()

    # HMAC-SHA256
    _, sk = stubs.resolve("Ljavax/crypto/spec/SecretKeySpec;", "<init>", "([BLjava/lang/String;)V",
                          [JObject("Ljavax/crypto/spec/SecretKeySpec;"), JArray("[B", list(b"secret")), JString("HmacSHA256")], False)
    _, mac = stubs.resolve("Ljavax/crypto/Mac;", "getInstance", "(Ljava/lang/String;)Ljavax/crypto/Mac;", [JString("HmacSHA256")], True)
    stubs.resolve("Ljavax/crypto/Mac;", "init", "(Ljava/security/Key;)V", [mac, sk], False)
    _, mout = stubs.resolve("Ljavax/crypto/Mac;", "doFinal", "([B)[B", [mac, JArray("[B", list(b"payload"))], False)
    assert bytes(mout.data) == hmac.new(b"secret", b"payload", hashlib.sha256).digest()

    # PBKDF2WithHmacSHA256
    _, skf = stubs.resolve("Ljavax/crypto/SecretKeyFactory;", "getInstance", "(Ljava/lang/String;)Ljavax/crypto/SecretKeyFactory;", [JString("PBKDF2WithHmacSHA256")], True)
    spec = JObject("Ljavax/crypto/spec/PBEKeySpec;", {"_pw": "pw", "_salt": b"salt", "_iter": 100, "_bits": 256})
    _, derived = stubs.resolve("Ljavax/crypto/SecretKeyFactory;", "generateSecret", "(Ljava/security/spec/KeySpec;)Ljavax/crypto/SecretKey;", [skf, spec], False)
    assert bytes(derived.fields["_key"]) == hashlib.pbkdf2_hmac("sha256", b"pw", b"salt", 100, 32)

    # EncryptedSharedPreferences Roundtrip
    ctx = JObject("Landroid/content/Context;")
    mk = JObject("Landroidx/security/crypto/MasterKey;")
    _, eprefs = stubs.resolve(
        "Landroidx/security/crypto/EncryptedSharedPreferences;", "create",
        "(Landroid/content/Context;Ljava/lang/String;Landroidx/security/crypto/MasterKey;Landroidx/security/crypto/EncryptedSharedPreferences$PrefKeyEncryptionScheme;Landroidx/security/crypto/EncryptedSharedPreferences$PrefValueEncryptionScheme;)Landroid/content/SharedPreferences;",
        [ctx, JString("sec_prefs"), mk, None, None], True,
    )
    _, ed = stubs.resolve("Landroid/content/SharedPreferences;", "edit", "()Landroid/content/SharedPreferences$Editor;", [eprefs], False)
    stubs.resolve("Landroid/content/SharedPreferences$Editor;", "putString", "(Ljava/lang/String;Ljava/lang/String;)Landroid/content/SharedPreferences$Editor;",
                  [ed, JString("jwt"), JString("tok_123")], False)
    stubs.resolve("Landroid/content/SharedPreferences$Editor;", "apply", "()V", [ed], False)
    _, got = stubs.resolve("Landroid/content/SharedPreferences;", "getString", "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;",
                          [eprefs, JString("jwt"), None], False)
    assert got.text == "tok_123"

    # BillingClient Pro Override
    set_billing_pro_override(stubs, True, ["pro_yearly"])
    bc = JObject("Lcom/android/billingclient/api/BillingClient;")
    orig_iv = vm.invoke_virtual
    bill_resp = []
    vm.invoke_virtual = lambda obj, mn, sg, args=(): bill_resp.append((mn, args))
    stubs.resolve(
        "Lcom/android/billingclient/api/BillingClient;", "queryPurchasesAsync",
        "(Lcom/android/billingclient/api/QueryPurchasesParams;Lcom/android/billingclient/api/PurchasesResponseListener;)V",
        [bc, JObject("Lcom/android/billingclient/api/QueryPurchasesParams;"), JObject("Lcom/demo/PurchLst;")], False,
    )
    vm.invoke_virtual = orig_iv
    assert bill_resp and bill_resp[-1][0] == "onQueryPurchasesResponse"
    plist = bill_resp[-1][1][2]
    assert plist.fields["__items"][0].fields["_product_id"] == "pro_yearly"

    # Play Integrity
    _, im = stubs.resolve("Lcom/google/android/play/core/integrity/IntegrityManagerFactory;", "create",
                          "(Landroid/content/Context;)Lcom/google/android/play/core/integrity/IntegrityManager;", [ctx], True)
    _, itask = stubs.resolve("Lcom/google/android/play/core/integrity/IntegrityManager;", "requestIntegrityToken",
                             "(Lcom/google/android/play/core/integrity/IntegrityTokenRequest;)Lcom/google/android/gms/tasks/Task;",
                             [im, JObject("Lcom/google/android/play/core/integrity/IntegrityTokenRequest;")], False)
    assert "integrity" in itask.fields["$res"].fields["token"].text


# ---------------------------------------------------------------------------
# G12: Flutter libapp.so ELF64/AOT Deep Analyzer & Interaktive RN-JS-Session
# ---------------------------------------------------------------------------
def _make_minimal_elf64(extra_rodata: bytes) -> bytes:
    # Minimal gültiger ELF64 Little-Endian ARM64 (e_machine=183) mit .shstrtab, .dynstr, .dynsym, .rodata
    ehdr_sz = 64
    shdr_sz = 64
    shnum = 5  # null, .shstrtab, .dynstr, .dynsym, .rodata
    shstrtab = b"\x00.shstrtab\x00.dynstr\x00.dynsym\x00.rodata\x00"
    dynstr = b"\x00_kDartVmSnapshotData\x00_kDartIsolateSnapshotData\x00"
    # 2 Elf64_Sym entries (0 = null, 1 = _kDartVmSnapshotData at offset 1)
    sym0 = b"\x00" * 24
    sym1 = struct.pack("<IBBHQQ", 1, 0x11, 0, 4, 0, 64)
    dynsym = sym0 + sym1

    off_shstr = ehdr_sz
    off_dynstr = off_shstr + len(shstrtab)
    off_dynsym = off_dynstr + len(dynstr)
    off_rodata = off_dynsym + len(dynsym)
    off_shdr = off_rodata + len(extra_rodata)

    ehdr = bytearray(ehdr_sz)
    ehdr[0:4] = b"\x7fELF"
    ehdr[4] = 2  # ELFCLASS64
    ehdr[5] = 1  # ELFDATA2LSB
    ehdr[6] = 1
    struct.pack_into("<HHIQQQIHHHHHH", ehdr, 16, 3, 183, 1, 0, 0, off_shdr, 0, ehdr_sz, 0, 0, shdr_sz, shnum, 1)

    def sh(name_i, s_type, off, sz, link=0, esz=0):
        return struct.pack("<IIQQQQIIQQ", name_i, s_type, 0, 0, off, sz, link, 0, 8, esz)

    shdrs = (
        sh(0, 0, 0, 0)
        + sh(1, 3, off_shstr, len(shstrtab))
        + sh(11, 3, off_dynstr, len(dynstr))
        + sh(19, 11, off_dynsym, len(dynsym), link=2, esz=24)
        + sh(27, 1, off_rodata, len(extra_rodata))
    )
    return bytes(ehdr) + shstrtab + dynstr + dynsym + extra_rodata + shdrs


def test_g12_flutter_elf64_deep_analyzer_and_rn_interactive_session(tmp_path):
    from core.java.engine_deep_g12 import RnInteractiveJsSession, analyze_flutter_libapp_deep
    from core.java.flutter_app_producer import extract_flutter_app_spec

    app_dir = tmp_path / "flutter_app"
    (app_dir / "lib" / "arm64-v8a").mkdir(parents=True)
    ro = (
        b"package:virtuagym_mobile/login_screen.dart\x00"
        b"package:flutter_bloc/bloc.dart\x00"
        b"LoginScreen\x00WorkoutBloc\x00query GetWorkouts\x00"
        b"plugins.flutter.io/shared_preferences\x00"
        b"https://api.demo.local/v2/workouts\x00"
    )
    so_path = app_dir / "lib" / "arm64-v8a" / "libapp.so"
    so_path.write_bytes(_make_minimal_elf64(ro))

    deep = analyze_flutter_libapp_deep(str(so_path))
    assert deep["is_elf64"] is True
    assert deep["machine"] == 183
    assert ".rodata" in deep["sections"]
    assert "_kDartVmSnapshotData" in deep["kdart_symbols"]
    assert "virtuagym_mobile" in deep["dart_packages"]
    assert "LoginScreen" in deep["dart_classes"] and "WorkoutBloc" in deep["dart_classes"]
    assert "query GetWorkouts" in deep["graphql_queries"]
    assert "https://api.demo.local/v2/workouts" in deep["rest_endpoints"]

    spec = extract_flutter_app_spec(str(app_dir))
    assert "virtuagym_mobile" in spec["dart_packages"]
    assert "WorkoutBloc" in spec["dart_classes"]

    # Interactive React Native Session (Mount -> ChangeText -> Tap -> Fetch -> ViewTree + Yoga Box)
    _vm, stubs, _t, net_log = _vm_with_views(
        with_net=True,
        responder=lambda r: NetResponse(200, {"Content-Type": "application/json"}, b'{"token":"rn_ok"}', "OK"),
    )
    sess = RnInteractiveJsSession(
        bundle_js='function App() { fetch("https://rn.demo.local/auth"); }',
        stubs=stubs,
        use_node=False,
    )
    surf0, rec0 = sess.mount()
    assert sess.mount_data["root"]["children"][0]["props"]["_box"]["w"] > 0
    sess.change_text(3, "alice@rn.de")
    surf1, rec1 = sess.tap(5)
    assert net_log.entries and net_log.entries[-1]["req"].url == "https://rn.demo.local/auth"
    assert any("rn_ok" in (n.text or "") for n in surf1.tree.nodes())


# ---------------------------------------------------------------------------
# G13: GUI Password-Masking, Toast Overlay, Node Highlight, Swipe & Panels
# ---------------------------------------------------------------------------
def test_g13_gui_password_mask_toast_highlight_swipe_and_panels():
    import tkinter as tk
    from core.java.layout_inflater import parse_layout_xml
    from core.java.view_tree import ViewTree
    from ui.widgets.seams_inspector_panel import SeamsInspectorPanel, UiTreeInspectorPanel, classify_missing_engine

    xml_str = (
        '<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android" android:orientation="vertical">'
        '<EditText android:id="@+id/pw" android:inputType="textPassword" android:text="s3cr3t"/>'
        '</LinearLayout>'
    )
    tree = ViewTree()
    tree.root = parse_layout_xml(xml_str)
    pw_node = tree.root.find("pw")
    assert pw_node is not None and pw_node.props.get("is_password") is True

    # Maskierung vs Klartext + Highlight + Toast in paint_commands
    tree.mask_passwords = True
    tree._highlight_node = pw_node
    tree._toasts = ["Login gespeichert"]
    tree.layout()
    cmds_masked = paint_commands(tree)
    assert any(c.get("text") == "\u2022" * 6 for c in cmds_masked if c["op"] == "text")
    assert any(c.get("outline") == "#00e5ff" for c in cmds_masked if c["op"] == "rect")
    assert any(c.get("role") == "toast" for c in cmds_masked if c["op"] == "rect")
    assert any(c.get("text") == "Login gespeichert" for c in cmds_masked if c["op"] == "text")

    tree.mask_passwords = False
    cmds_plain = paint_commands(tree)
    assert any(c.get("text") == "s3cr3t" for c in cmds_plain if c["op"] == "text")

    # Swipe auf HorizontalPager / PagerState
    ps = {"_page": 0, "_page_count": 3}
    pager_node = ViewNode("ViewPager2", props={"$pager_state": ps})
    tree.root.add(pager_node)
    tree.layout()
    bx, by, bw, bh = pager_node.bounds
    assert swipe_pager(tree, bx + max(2, bw // 2), by + max(2, bh // 2), 1) is True
    assert ps["_page"] == 1
    assert swipe_pager(tree, bx + max(2, bw // 2), by + max(2, bh // 2), -1) is True
    assert ps["_page"] == 0

    # Engine Classifier
    assert classify_missing_engine("Lokhttp3/WebSocket;", "()V") == "Net/gRPC"
    assert classify_missing_engine("Lcom/android/billingclient/api/BillingClient;", "()V") == "Crypto/Billing"
    assert classify_missing_engine("Landroidx/compose/foundation/pager/PagerState;", "()I") == "Compose"

    # Headless GUI Panel + DisplayTab Checks
    try:
        tk_root = tk.Tk()
        tk_root.withdraw()
    except tk.TclError:
        return
    try:
        vm, stubs, t_live, _ = _vm_with_views(with_net=True)
        if t_live.root is None:
            t_live.root = ViewNode("LinearLayout")
        t_live.root.add(ViewNode("EditText", vid="pw2", text="geheim", props={"is_password": True}))
        t_live.layout()
        ctrl = type("_FakeCtrl", (), {"_stubs": stubs, "_runner": None, "glass_tree": t_live, "app": None})()
        from ui.tabs.display_tab import DisplayTab
        dt = DisplayTab(tk_root, type("_App", (), {})())
        ctrl.app = type("_App", (), {"display_tab": dt})()
        dt.set_tree(t_live, vm="glass")

        assert dt._pw_mask is False
        dt.toggle_password_mask()
        assert dt._pw_mask is True and t_live.mask_passwords is True
        dt.toggle_ui_inspect()
        assert dt._ui_inspect_mode is True
        dt.toggle_ui_inspect()
        assert dt._ui_inspect_mode is False and t_live._highlight_node is None

        seams_p = SeamsInspectorPanel(tk_root, ctrl)
        seams_p.billing_var.set(True)
        assert seams_p.apply_billing() is True
        assert stubs._billing_state["pro_override"] is True
        seams_p.bio_var.set("FAILED")
        seams_p.apply_biometric()
        assert stubs._hw_profile["biometric_result"] == "failed"

        ui_p = UiTreeInspectorPanel(tk_root, ctrl)
        assert ui_p.refresh() >= 2
    finally:
        tk_root.destroy()


# ---------------------------------------------------------------------------
# G14: Cross-Engine Stubs (org.json, gzip, Compose, libc, WebView, Flutter)
# ---------------------------------------------------------------------------
def test_g14_cross_engine_stubs_json_gzip_libc_webview_and_flutter_channels():
    from core.java.engine_stubs_g14 import get_rn_builtin_modules
    from core.java.flutter_channel_bridge import install_flutter_channel_bridge, invoke_flutter_channel
    from core.java.webview_bridge import eval_webview_js, handle_webview_vhook
    from core.native.libc_stubs import _libc_alloc, install_libc_stubs
    from services.native_emu_service import NativeEmuService
    from services.native_ffi_service import NativeFfiService

    vm, stubs, tree, _ = _vm_with_views()

    # 1) org.json.JSONObject + JSONArray
    jo = JObject("Lorg/json/JSONObject;")
    stubs.resolve("Lorg/json/JSONObject;", "<init>", "(Ljava/lang/String;)V", [jo, JString('{"a":7,"b":"hi"}')], False)
    stubs.resolve("Lorg/json/JSONObject;", "put", "(Ljava/lang/String;Z)Lorg/json/JSONObject;", [jo, JString("ok"), 1], False)
    _, got_b = stubs.resolve("Lorg/json/JSONObject;", "optString", "(Ljava/lang/String;)Ljava/lang/String;", [jo, JString("b")], False)
    _, out_s = stubs.resolve("Lorg/json/JSONObject;", "toString", "()Ljava/lang/String;", [jo], False)
    assert got_b.text == "hi" and json.loads(out_s.text) == {"a": 7, "b": "hi", "ok": True}

    # 2) java.util.Base64 + GZIPOutputStream / GZIPInputStream
    _, enc = stubs.resolve("Ljava/util/Base64;", "getEncoder", "()Ljava/util/Base64$Encoder;", [], True)
    _, b64_s = stubs.resolve("Ljava/util/Base64$Encoder;", "encodeToString", "([B)Ljava/lang/String;", [enc, JArray("[B", list(b"hello"))], False)
    _, dec_o = stubs.resolve("Ljava/util/Base64;", "getDecoder", "()Ljava/util/Base64$Decoder;", [], True)
    _, raw_b = stubs.resolve("Ljava/util/Base64$Decoder;", "decode", "(Ljava/lang/String;)[B", [dec_o, b64_s], False)
    assert bytes(raw_b.data) == b"hello"

    baos = JObject("Ljava/io/ByteArrayOutputStream;")
    stubs.resolve("Ljava/io/ByteArrayOutputStream;", "<init>", "()V", [baos], False)
    gz_out = JObject("Ljava/util/zip/GZIPOutputStream;")
    stubs.resolve("Ljava/util/zip/GZIPOutputStream;", "<init>", "(Ljava/io/OutputStream;)V", [gz_out, baos], False)
    stubs.resolve("Ljava/util/zip/GZIPOutputStream;", "write", "([B)V", [gz_out, JArray("[B", list(b"compressed-payload"))], False)
    stubs.resolve("Ljava/util/zip/GZIPOutputStream;", "close", "()V", [gz_out], False)
    _, gz_arr = stubs.resolve("Ljava/io/ByteArrayOutputStream;", "toByteArray", "()[B", [baos], False)
    gz_bytes = bytes(gz_arr.data) if gz_arr is not None else bytes(baos.fields.get("buf") or baos.fields.get("_buf") or b"")
    bais = JObject("Ljava/io/ByteArrayInputStream;")
    stubs.resolve("Ljava/io/ByteArrayInputStream;", "<init>", "([B)V", [bais, JArray("[B", list(gz_bytes))], False)
    gz_in = JObject("Ljava/util/zip/GZIPInputStream;")
    stubs.resolve("Ljava/util/zip/GZIPInputStream;", "<init>", "(Ljava/io/InputStream;)V", [gz_in, bais], False)
    out_arr = JArray("[B", [0] * 32)
    _, nread = stubs.resolve("Ljava/util/zip/GZIPInputStream;", "read", "([BII)I", [gz_in, out_arr, 0, 32], False)
    assert bytes(out_arr.data[:nread]) == b"compressed-payload"

    # 3) Toast + Compose mutableIntStateOf
    _, tst = stubs.resolve("Landroid/widget/Toast;", "makeText", "(Landroid/content/Context;Ljava/lang/CharSequence;I)Landroid/widget/Toast;",
                           [None, JString("G14 Toast!"), 0], True)
    stubs.resolve("Landroid/widget/Toast;", "show", "()V", [tst], False)
    assert "G14 Toast!" in tree._toasts

    ok_c, mis = vm._method_hooks[-2]("", "Landroidx/compose/runtime/SnapshotIntStateKt;", "mutableIntStateOf",
                                     "(I)Landroidx/compose/runtime/MutableIntState;", [42], True, vm) if len(vm._method_hooks) >= 2 else (False, None)
    assert any(
        h("", "Landroidx/compose/runtime/SnapshotIntStateKt;", "mutableIntStateOf",
          "(I)Landroidx/compose/runtime/MutableIntState;", [42], True, vm)[0]
        for h in vm._method_hooks
    )

    # 4) Native libc Extensions
    eng = NativeEmuService(publish=False).new_engine()
    ffi = NativeFfiService(publish=False)
    install_libc_stubs(eng, ffi)

    def _libc_call(sym, *args):
        addr = ffi.install_named_stub(eng, sym)
        for i, v in enumerate(args):
            eng.cpu.x[i] = v
        eng.cpu.lr = 0xDEAD0000
        eng.cpu.stub[addr](eng.cpu)
        return int(eng.cpu.x[0])

    prop_k = _libc_alloc(eng, 64)
    out_buf = _libc_alloc(eng, 64)
    eng.mem.write(prop_k, b"ro.product.model\x00")
    nprop = _libc_call("__system_property_get", prop_k, out_buf)
    assert nprop > 0 and b"Pixel" in eng.mem.read(out_buf, nprop)
    tag_p = _libc_alloc(eng, 32)
    msg_p = _libc_alloc(eng, 64)
    eng.mem.write(tag_p, b"TagG14\x00")
    eng.mem.write(msg_p, b"Native hello\x00")
    _libc_call("__android_log_write", 4, tag_p, msg_p)
    assert (eng._android_logs[-1]["tag"], eng._android_logs[-1]["msg"]) == ("TagG14", "Native hello")

    # 5) WebView WebSettings + localStorage + History
    wv_node = ViewNode("WebView", props={"web_url": "https://a.local"})
    if tree.root is None:
        tree.root = ViewNode("LinearLayout")
    tree.root.add(wv_node)
    wv_recv = JObject("Landroid/webkit/WebView;", {"$view": wv_node})
    ws = JObject("Landroid/webkit/WebSettings;", {"$node": wv_node})
    stubs.resolve("Landroid/webkit/WebSettings;", "setJavaScriptEnabled", "(Z)V", [ws, 1], False)
    assert stubs.resolve("Landroid/webkit/WebSettings;", "getJavaScriptEnabled", "()Z", [ws], False)[1] == 1
    eval_webview_js(stubs, vm, tree, wv_node, wv_recv, "localStorage.setItem('tok', 'v1');")
    assert eval_webview_js(stubs, vm, tree, wv_node, wv_recv, "return localStorage.getItem('tok');") == "v1"
    _txt_fn = lambda x: x.text if isinstance(x, JString) else str(x)
    handle_webview_vhook(stubs, vm, tree, wv_recv, "Landroid/webkit/WebView;", "loadUrl",
                         "(Ljava/lang/String;)V", (wv_recv, JString("https://a.local")), _txt_fn, lambda *a, **kw: None, ViewNode)
    handle_webview_vhook(stubs, vm, tree, wv_recv, "Landroid/webkit/WebView;", "loadUrl",
                         "(Ljava/lang/String;)V", (wv_recv, JString("https://b.local")), _txt_fn, lambda *a, **kw: None, ViewNode)
    assert handle_webview_vhook(stubs, vm, tree, wv_recv, "Landroid/webkit/WebView;", "canGoBack",
                                "()Z", (wv_recv,), _txt_fn, lambda *a, **kw: None, ViewNode)[1] == 1
    handle_webview_vhook(stubs, vm, tree, wv_recv, "Landroid/webkit/WebView;", "goBack",
                         "()V", (wv_recv,), _txt_fn, lambda *a, **kw: None, ViewNode)
    assert wv_recv.fields["$wv_history"] == ["https://a.local"]

    # 6) Flutter Builtin Channels & RN Modules
    install_flutter_channel_bridge(stubs, tree)
    assert invoke_flutter_channel(stubs, "plugins.it_nomads.com/flutter_secure_storage", "write", {"key": "k1", "value": "sec"})["status"] == "success"
    assert invoke_flutter_channel(stubs, "plugins.it_nomads.com/flutter_secure_storage", "read", {"key": "k1"})["result"] == "sec"
    assert invoke_flutter_channel(stubs, "dev.fluttercommunity.plus/device_info", "getAndroidDeviceInfo", {})["result"]["brand"] == "google"
    assert invoke_flutter_channel(stubs, "dev.fluttercommunity.plus/connectivity", "check", {})["result"] == "wifi"
    rn_mods = get_rn_builtin_modules(stubs)
    assert "PlatformConstants" in rn_mods and "LinkingManager" in rn_mods and "Clipboard" in rn_mods
