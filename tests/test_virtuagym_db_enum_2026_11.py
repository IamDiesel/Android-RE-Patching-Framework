# -*- coding: utf-8 -*-
"""Tests fuer SQLite-Fehler-Tracking, OpenHelper-Version-Guard, OkHttp-250k-Budget, Socket-Host/Port-Reads & Rx1-Stubs."""
from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _make_vm_and_stubs():
    from core.java.dalvik import DalvikVM
    from core.java.dex_loader import AndroidStubs
    from core.java.lifecycle import _StubRuntime

    vm = DalvikVM()
    vm.lenient = True
    stubs = AndroidStubs(vm=vm)
    stubs.seed_framework_statics(vm)
    vm.runtime = _StubRuntime(stubs)
    return vm, stubs


def test_sqlite_errors_recorded_in_deviations_instead_of_silent_pass():
    from core.java.objects import JObject, JString
    from core.java.sqlite_prefs_bridge import install_sqlite_and_prefs_grundset

    vm, stubs = _make_vm_and_stubs()
    install_sqlite_and_prefs_grundset(stubs)
    R = stubs.resolve

    _, db = R(
        "Landroid/database/sqlite/SQLiteDatabase;",
        "openOrCreateDatabase",
        "(Ljava/lang/String;Landroid/database/sqlite/SQLiteDatabase$CursorFactory;)Landroid/database/sqlite/SQLiteDatabase;",
        [JString(":memory:"), None],
        True,
    )

    # 1) execSQL syntax error
    ok, res_exec = R("Landroid/database/sqlite/SQLiteDatabase;", "execSQL", "(Ljava/lang/String;)V", [db, JString("SYNTAX BROKEN !!!")], False)
    assert ok and res_exec is None

    # 2) insert into nonexistent table -> -1
    cv = JObject("Landroid/content/ContentValues;")
    R("Landroid/content/ContentValues;", "<init>", "()V", [cv], False)
    R("Landroid/content/ContentValues;", "put", "(Ljava/lang/String;Ljava/lang/String;)V", [cv, JString("k"), JString("v")], False)
    ok, row_id = R(
        "Landroid/database/sqlite/SQLiteDatabase;",
        "insert",
        "(Ljava/lang/String;Ljava/lang/String;Landroid/content/ContentValues;)J",
        [db, JString("missing_tbl"), None, cv],
        False,
    )
    assert ok and row_id == -1

    # 3) update nonexistent table -> 0
    ok, upd_cnt = R(
        "Landroid/database/sqlite/SQLiteDatabase;",
        "update",
        "(Ljava/lang/String;Landroid/content/ContentValues;Ljava/lang/String;[Ljava/lang/String;)I",
        [db, JString("missing_tbl"), cv, None, None],
        False,
    )
    assert ok and upd_cnt == 0

    # 4) rawQuery on nonexistent table -> empty Cursor
    ok, cur = R(
        "Landroid/database/sqlite/SQLiteDatabase;",
        "rawQuery",
        "(Ljava/lang/String;[Ljava/lang/String;)Landroid/database/Cursor;",
        [db, JString("SELECT * FROM missing_tbl"), None],
        False,
    )
    assert ok and R("Landroid/database/Cursor;", "getCount", "()I", [cur], False)[1] == 0

    # 5) structured query, delete & compileStatement error -> lenient returns + deviations
    ok, cur_q = R(
        "Landroid/database/sqlite/SQLiteDatabase;",
        "query",
        "(Ljava/lang/String;[Ljava/lang/String;Ljava/lang/String;[Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)Landroid/database/Cursor;",
        [db, JString("missing_tbl"), None, None, None, None, None, None],
        False,
    )
    assert ok and R("Landroid/database/Cursor;", "getCount", "()I", [cur_q], False)[1] == 0
    ok, del_cnt = R("Landroid/database/sqlite/SQLiteDatabase;", "delete", "(Ljava/lang/String;Ljava/lang/String;[Ljava/lang/String;)I", [db, JString("missing_tbl"), None, None], False)
    assert ok and del_cnt == 0
    _, stmt = R("Landroid/database/sqlite/SQLiteDatabase;", "compileStatement", "(Ljava/lang/String;)Landroid/database/sqlite/SQLiteStatement;", [db, JString("INSERT INTO missing_tbl VALUES (?)")], False)
    R("Landroid/database/sqlite/SQLiteStatement;", "execute", "()V", [stmt], False)

    assert stubs.__dict__["_sqlite_err_count"][":memory:"] == 7
    sites = {(d["kind"], d["site"]) for d in vm.deviations.to_list()}
    for op in ("execSQL", "insert", "update", "rawQuery", "query", "delete", "stmt_exec"):
        assert ("sqlite_error", f":memory::{op}") in sites


def test_sqlite_open_helper_oncreate_and_onupgrade_version_guard_and_debugstop():
    from core.java.deviation_journal import DebugStop
    from core.java.objects import JObject, JString
    from core.java.sqlite_prefs_bridge import install_sqlite_and_prefs_grundset

    vm, stubs = _make_vm_and_stubs()
    install_sqlite_and_prefs_grundset(stubs)
    R = stubs.resolve

    mode = {"onCreate": "sql_err", "onUpgrade": "sql_err"}

    def _helper_hook(rcls, cls, name, sig, args, static, _vm):
        if cls != "Lcom/demo/TestHelper;":
            return False, None
        if name == "onCreate":
            db_arg = args[1]
            cur_mode = mode["onCreate"]
            if cur_mode == "sql_err":
                R("Landroid/database/sqlite/SQLiteDatabase;", "execSQL", "(Ljava/lang/String;)V", [db_arg, JString("CREATE TABLE broken (")], False)
                return True, None
            if cur_mode == "raise":
                raise RuntimeError("boom in onCreate")
            if cur_mode == "debug_stop":
                raise DebugStop("stop in onCreate")
            if cur_mode == "ok":
                R("Landroid/database/sqlite/SQLiteDatabase;", "execSQL", "(Ljava/lang/String;)V", [db_arg, JString("CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT)")], False)
                return True, None
        if name == "onUpgrade":
            db_arg = args[1]
            cur_mode = mode["onUpgrade"]
            if cur_mode == "sql_err":
                R("Landroid/database/sqlite/SQLiteDatabase;", "execSQL", "(Ljava/lang/String;)V", [db_arg, JString("ALTER TABLE nope ADD COLUMN x INT")], False)
                return True, None
            if cur_mode == "raise":
                raise RuntimeError("boom in onUpgrade")
            if cur_mode == "ok":
                R("Landroid/database/sqlite/SQLiteDatabase;", "execSQL", "(Ljava/lang/String;)V", [db_arg, JString("CREATE TABLE items_v4(id INTEGER PRIMARY KEY)")], False)
                return True, None
        return False, None

    vm._method_hooks.append(_helper_hook)

    # A) onCreate SQL syntax error -> user_version stays 0, _db_init_failed=True
    h1 = JObject("Lcom/demo/TestHelper;")
    R(
        "Landroid/database/sqlite/SQLiteOpenHelper;",
        "<init>",
        "(Landroid/content/Context;Ljava/lang/String;Landroid/database/sqlite/SQLiteDatabase$CursorFactory;I)V",
        [h1, None, JString("db_fail_sql.db"), None, 2],
        False,
    )
    wdb1 = vm.invoke_virtual(h1, "getWritableDatabase", "()Landroid/database/sqlite/SQLiteDatabase;", (h1,))
    assert R("Landroid/database/sqlite/SQLiteDatabase;", "getVersion", "()I", [wdb1], False)[1] == 0
    assert h1.fields.get("_db_init_failed") is True
    assert h1.fields.get("_db_init_done") is False

    # B) onCreate throws -> user_version stays 0, _db_init_failed=True, sqlite_lifecycle_error recorded
    mode["onCreate"] = "raise"
    vm.invoke_virtual(h1, "getWritableDatabase", "()Landroid/database/sqlite/SQLiteDatabase;", (h1,))
    assert R("Landroid/database/sqlite/SQLiteDatabase;", "getVersion", "()I", [wdb1], False)[1] == 0
    assert h1.fields.get("_db_init_failed") is True
    assert h1.fields.get("_db_init_done") is False
    assert any(d["kind"] == "sqlite_lifecycle_error" and d["site"] == "Lcom/demo/TestHelper;->onCreate" for d in vm.deviations.to_list())

    # C) onCreate raises DebugStop -> must re-raise immediately
    mode["onCreate"] = "debug_stop"
    with pytest.raises(DebugStop):
        vm.invoke_virtual(h1, "getWritableDatabase", "()Landroid/database/sqlite/SQLiteDatabase;", (h1,))

    # D) onCreate succeeds cleanly -> user_version becomes target_v (2), _db_init_done=True
    mode["onCreate"] = "ok"
    h1.fields["_db_init_done"] = False
    wdb1_ok = vm.invoke_virtual(h1, "getWritableDatabase", "()Landroid/database/sqlite/SQLiteDatabase;", (h1,))
    assert R("Landroid/database/sqlite/SQLiteDatabase;", "getVersion", "()I", [wdb1_ok], False)[1] == 2
    assert h1.fields.get("_db_init_done") is True
    assert h1.fields.get("_db_init_failed") is False

    # E) onUpgrade from version 1 -> target 4: failure keeps version 1, success bumps to 4
    h2 = JObject("Lcom/demo/TestHelper;")
    R(
        "Landroid/database/sqlite/SQLiteOpenHelper;",
        "<init>",
        "(Landroid/content/Context;Ljava/lang/String;Landroid/database/sqlite/SQLiteDatabase$CursorFactory;I)V",
        [h2, None, JString("db_upgrade.db"), None, 4],
        False,
    )
    _, pre_db = R(
        "Landroid/database/sqlite/SQLiteDatabase;",
        "openOrCreateDatabase",
        "(Ljava/lang/String;Landroid/database/sqlite/SQLiteDatabase$CursorFactory;)Landroid/database/sqlite/SQLiteDatabase;",
        [JString("db_upgrade.db"), None],
        True,
    )
    R("Landroid/database/sqlite/SQLiteDatabase;", "setVersion", "(I)V", [pre_db, 1], False)

    mode["onUpgrade"] = "sql_err"
    wdb2 = vm.invoke_virtual(h2, "getWritableDatabase", "()Landroid/database/sqlite/SQLiteDatabase;", (h2,))
    assert R("Landroid/database/sqlite/SQLiteDatabase;", "getVersion", "()I", [wdb2], False)[1] == 1
    assert h2.fields.get("_db_init_failed") is True
    assert h2.fields.get("_db_init_done") is False

    mode["onUpgrade"] = "raise"
    vm.invoke_virtual(h2, "getWritableDatabase", "()Landroid/database/sqlite/SQLiteDatabase;", (h2,))
    assert R("Landroid/database/sqlite/SQLiteDatabase;", "getVersion", "()I", [wdb2], False)[1] == 1
    assert h2.fields.get("_db_init_failed") is True
    assert any(d["kind"] == "sqlite_lifecycle_error" and d["site"] == "Lcom/demo/TestHelper;->onUpgrade" for d in vm.deviations.to_list())

    mode["onUpgrade"] = "ok"
    wdb2_ok = vm.invoke_virtual(h2, "getWritableDatabase", "()Landroid/database/sqlite/SQLiteDatabase;", (h2,))
    assert R("Landroid/database/sqlite/SQLiteDatabase;", "getVersion", "()I", [wdb2_ok], False)[1] == 4
    assert h2.fields.get("_db_init_done") is True
    assert h2.fields.get("_db_init_failed") is False


def test_okhttp_proceed_chain_250k_budget_error_deviation_and_debugstop():
    from core.java.deviation_journal import DebugStop
    from core.java.net_recorder import NetLog, NetResponse, install_net_stubs
    from core.java.objects import JObject, JString

    vm, stubs = _make_vm_and_stubs()
    nlog = NetLog()
    install_net_stubs(stubs, responder=lambda r: NetResponse(200, {"Content-Type": "application/json"}, b'{"ok":true}', "OK"), net_log=nlog)

    # Heavy interceptor (~40,000 steps > old 25,000 cap, < new 250,000 cap) that attaches Authorization: Bearer tok123
    ic_cls = "Lapp/AccessTokenV3Interceptor;"
    ic_sig = "(Lokhttp3/Interceptor$Chain;)Lokhttp3/Response;"
    vm.register_method(
        ic_cls,
        "intercept",
        ic_sig,
        [
            ("const", 0, 0),
            ("const", 1, 20000),
            ("add-int/lit", 0, 0, 1),          # pc=2
            ("if-lt", 0, 1, 2),                # pc=3 -> ~40,000 loop steps
            ("invoke-interface", (5,), "Lokhttp3/Interceptor$Chain;", "request", "()Lokhttp3/Request;"),
            ("move-result-object", 0),
            ("invoke-virtual", (0,), "Lokhttp3/Request;", "newBuilder", "()Lokhttp3/Request$Builder;"),
            ("move-result-object", 0),
            ("const-string", 1, "Authorization"),
            ("const-string", 2, "Bearer tok123"),
            ("invoke-virtual", (0, 1, 2), "Lokhttp3/Request$Builder;", "header", "(Ljava/lang/String;Ljava/lang/String;)Lokhttp3/Request$Builder;"),
            ("move-result-object", 0),
            ("invoke-virtual", (0,), "Lokhttp3/Request$Builder;", "build", "()Lokhttp3/Request;"),
            ("move-result-object", 0),
            ("invoke-interface", (5, 0), "Lokhttp3/Interceptor$Chain;", "proceed", "(Lokhttp3/Request;)Lokhttp3/Response;"),
            ("move-result-object", 0),
            ("return-object", 0),
        ],
        nregs=6,
        nargs=2,
        static=False,
    )

    # Crash interceptor (real Dalvik throw) + DebugStop interceptor (real _dbg.on_step DebugStop)
    crash_cls = "Lapp/CrashInterceptor;"
    stop_cls = "Lapp/StopInterceptor;"
    vm.register_method(crash_cls, "intercept", ic_sig, [("const", 0, 0), ("throw", 0)], nregs=2, nargs=2, static=False)
    vm.register_method(stop_cls, "intercept", ic_sig, [("nop",), ("return-object", 0)], nregs=2, nargs=2, static=False)

    def _run_call(ic_obj):
        b = JObject("Lokhttp3/OkHttpClient$Builder;", {})
        vm.invoke_virtual(b, "<init>", "()V", (b,))
        vm.invoke_virtual(b, "addInterceptor", "(Lokhttp3/Interceptor;)Lokhttp3/OkHttpClient$Builder;", (b, ic_obj))
        cli = vm.invoke_virtual(b, "build", "()Lokhttp3/OkHttpClient;", (b,))
        rb = JObject("Lokhttp3/Request$Builder;", {})
        vm.invoke_virtual(rb, "<init>", "()V", (rb,))
        vm.invoke_virtual(rb, "url", "(Ljava/lang/String;)Lokhttp3/Request$Builder;", (rb, JString("https://api.virtuagym.test/v3/me")))
        req = vm.invoke_virtual(rb, "build", "()Lokhttp3/Request;", (rb,))
        call = vm.invoke_virtual(cli, "newCall", "(Lokhttp3/Request;)Lokhttp3/Call;", (cli, req))
        return vm.invoke_virtual(call, "execute", "()Lokhttp3/Response;", (call,))

    steps_before = vm.steps
    resp = _run_call(JObject(ic_cls, {}))
    assert vm.steps - steps_before >= 40_000
    assert isinstance(resp, JObject) and resp.fields.get("$code") == 200
    assert len(nlog.entries) == 1
    assert nlog.entries[0]["req"].headers.get("Authorization") == "Bearer tok123"

    # Error inside interceptor records okhttp_interceptor_error into vm.deviations
    _run_call(JObject(crash_cls, {}))
    assert any(
        d["kind"] == "okhttp_interceptor_error" and d["site"] == f"{crash_cls}->intercept{ic_sig}"
        for d in vm.deviations.to_list()
    )

    # DebugStop inside interceptor must re-raise and restore step_budget
    class _StopDbg:
        def on_step(self, _vm, fr, *_args):
            if fr.md is not None and fr.md.cls == stop_cls:
                raise DebugStop("debugger stopped")

    vm.step_budget = 999_999
    vm._dbg = _StopDbg()
    try:
        with pytest.raises(DebugStop):
            _run_call(JObject(stop_cls, {}))
    finally:
        vm._dbg = None
    assert vm.step_budget == 999_999


def test_inet_address_and_socket_address_unified_host_port_reads():
    from core.java.net_recorder import NetLog, install_net_stubs
    from core.java.objects import JObject, JString

    _vm, stubs = _make_vm_and_stubs()
    nlog = NetLog()
    install_net_stubs(stubs, net_log=nlog)
    R = stubs.resolve

    # InetAddress created with "host": JString(...) (e.g. hardware_media_g9 style)
    ia_plain = JObject("Ljava/net/InetAddress;", {"host": JString("192.168.1.42")})
    assert R("Ljava/net/InetAddress;", "getHostAddress", "()Ljava/lang/String;", [ia_plain], False)[1].text == "192.168.1.42"
    assert R("Ljava/net/InetAddress;", "getHostName", "()Ljava/lang/String;", [ia_plain], False)[1].text == "192.168.1.42"

    isa_from_ia = JObject("Ljava/net/InetSocketAddress;", {})
    R("Ljava/net/InetSocketAddress;", "<init>", "(Ljava/net/InetAddress;I)V", [isa_from_ia, ia_plain, 8181], False)
    assert R("Ljava/net/InetSocketAddress;", "getHostString", "()Ljava/lang/String;", [isa_from_ia], False)[1].text == "192.168.1.42"
    assert R("Ljava/net/InetSocketAddress;", "getPort", "()I", [isa_from_ia], False)[1] == 8181

    # Socket.connect + ServerSocket.bind with "host": JString(...) and "port": int
    isa_alt = JObject("Ljava/net/InetSocketAddress;", {"host": JString("172.16.0.2"), "port": 9090})
    sock = JObject("Ljava/net/Socket;", {})
    R("Ljava/net/Socket;", "connect", "(Ljava/net/SocketAddress;)V", [sock, isa_alt], False)
    srv = JObject("Ljava/net/ServerSocket;", {})
    R("Ljava/net/ServerSocket;", "bind", "(Ljava/net/SocketAddress;)V", [srv, isa_alt], False)

    urls = [(e["req"].method, e["req"].url) for e in nlog.entries]
    assert ("CONNECT", "tcp://172.16.0.2:9090") in urls
    assert ("LISTEN", "tcp://172.16.0.2:9090") in urls


def test_rx1_schedulers_single_observable_completable_fallback_stubs():
    from core.java.nav_rx_paging_g8 import install_g8_stubs
    from core.java.objects import JObject, JString

    vm, stubs = _make_vm_and_stubs()
    install_g8_stubs(stubs)
    R = stubs.resolve

    # 1) Schedulers + AndroidSchedulers return Lrx/Scheduler;
    for sm in ("io", "computation", "immediate", "newThread", "trampoline"):
        ok, sch = R("Lrx/schedulers/Schedulers;", sm, "()Lrx/Scheduler;", [], True)
        assert ok and isinstance(sch, JObject) and sch.cls == "Lrx/Scheduler;"
    ok_mt, sch_mt = R("Lrx/android/schedulers/AndroidSchedulers;", "mainThread", "()Lrx/Scheduler;", [], True)
    assert ok_mt and isinstance(sch_mt, JObject) and sch_mt.cls == "Lrx/Scheduler;"

    # 2) Single.just -> subscribeOn -> observeOn -> subscribe(Action1) invoking real Dalvik MethodDef
    seen = []
    vm.register_method(
        "Lcom/demo/Rx1Action;",
        "call",
        "(Ljava/lang/Object;)V",
        [("return-void",)],
        nregs=2,
        nargs=2,
        static=False,
    )
    orig_iv = vm.invoke_virtual

    def _track_iv(obj, mname, msig, args=()):
        if isinstance(obj, JObject) and obj.cls in ("Lcom/demo/Rx1Action;", "Lcom/demo/Rx1Err;", "Lcom/demo/Rx1Done;"):
            seen.append((obj.cls, mname, msig, args[1].text if len(args) > 1 and isinstance(args[1], JString) else None))
        return orig_iv(obj, mname, msig, args)

    vm.invoke_virtual = _track_iv

    _, s1 = R("Lrx/Single;", "just", "(Ljava/lang/Object;)Lrx/Single;", [JString("hello_rx1")], True)
    _, s2 = R("Lrx/Single;", "subscribeOn", "(Lrx/Scheduler;)Lrx/Single;", [s1, R("Lrx/schedulers/Schedulers;", "io", "()Lrx/Scheduler;", [], True)[1]], False)
    _, s3 = R("Lrx/Single;", "observeOn", "(Lrx/Scheduler;)Lrx/Single;", [s2, sch_mt], False)
    _, sub = R("Lrx/Single;", "subscribe", "(Lrx/functions/Action1;)Lrx/Subscription;", [s3, JObject("Lcom/demo/Rx1Action;", {})], False)
    assert isinstance(sub, JObject) and sub.cls == "Lrx/Subscription;"
    assert ("Lcom/demo/Rx1Action;", "call", "(Ljava/lang/Object;)V", "hello_rx1") in seen

    # 3) Single.toBlocking().value(), Observable.toSingle(), Single.error(), Completable.complete().subscribe(Action0)
    _, blk = R("Lrx/Single;", "toBlocking", "()Lrx/singles/BlockingSingle;", [s3], False)
    assert R("Lrx/singles/BlockingSingle;", "value", "()Ljava/lang/Object;", [blk], False)[1].text == "hello_rx1"

    _, obs = R("Lrx/Observable;", "just", "(Ljava/lang/Object;)Lrx/Observable;", [JString("obs_val")], True)
    _, s_conv = R("Lrx/Observable;", "toSingle", "()Lrx/Single;", [obs], False)
    _, blk2 = R("Lrx/Single;", "toBlocking", "()Lrx/singles/BlockingSingle;", [s_conv], False)
    assert R("Lrx/singles/BlockingSingle;", "value", "()Ljava/lang/Object;", [blk2], False)[1].text == "obs_val"

    err_obj = JObject("Ljava/lang/RuntimeException;", {"detailMessage": JString("rx1_boom")})
    _, s_err = R("Lrx/Single;", "error", "(Ljava/lang/Throwable;)Lrx/Single;", [err_obj], True)
    R("Lrx/Single;", "subscribe", "(Lrx/functions/Action1;Lrx/functions/Action1;)Lrx/Subscription;",
      [s_err, JObject("Lcom/demo/Rx1Action;", {}), JObject("Lcom/demo/Rx1Err;", {})], False)
    assert any(ev[0] == "Lcom/demo/Rx1Err;" for ev in seen)

    _, comp = R("Lrx/Completable;", "complete", "()Lrx/Completable;", [], True)
    R("Lrx/Completable;", "subscribe", "(Lrx/functions/Action0;)Lrx/Subscription;", [comp, JObject("Lcom/demo/Rx1Done;", {})], False)
    assert any(ev[0] == "Lcom/demo/Rx1Done;" for ev in seen)

    # 4) CompositeSubscription + Subscription.unsubscribe()
    cs = JObject("Lrx/subscriptions/CompositeSubscription;", {})
    R("Lrx/subscriptions/CompositeSubscription;", "<init>", "()V", [cs], False)
    R("Lrx/subscriptions/CompositeSubscription;", "add", "(Lrx/Subscription;)V", [cs, sub], False)
    R("Lrx/Subscription;", "unsubscribe", "()V", [sub], False)
    assert R("Lrx/Subscription;", "isUnsubscribed", "()Z", [sub], False)[1] == 1
    R("Lrx/subscriptions/CompositeSubscription;", "unsubscribe", "()V", [cs], False)
    assert R("Lrx/subscriptions/CompositeSubscription;", "isUnsubscribed", "()Z", [cs], False)[1] == 1


def test_enum_tostring_smali_minimal_repro():
    from core.java.class_provider import TextClassProvider
    from core.java.dalvik import DalvikVM, JObject, JString
    from core.java.dex_loader import AndroidStubs
    from core.java.lifecycle import _StubRuntime

    smali_src = """
.class public Lt/Typ;
.super Ljava/lang/Enum;
.field public static final INTEGER:Lt/Typ;

.method static constructor <clinit>()V
    .registers 3
    new-instance v0, Lt/Typ;
    const-string v1, "INTEGER"
    const/4 v2, 0x0
    invoke-direct {v0, v1, v2}, Ljava/lang/Enum;-><init>(Ljava/lang/String;I)V
    sput-object v0, Lt/Typ;->INTEGER:Lt/Typ;
    return-void
.end method

.method public static checkAll()Ljava/lang/String;
    .registers 5
    sget-object v0, Lt/Typ;->INTEGER:Lt/Typ;
    invoke-virtual {v0}, Lt/Typ;->toString()Ljava/lang/String;
    move-result-object v1
    new-instance v2, Ljava/lang/StringBuilder;
    invoke-direct {v2}, Ljava/lang/StringBuilder;-><init>()V
    const-string v3, "id "
    invoke-virtual {v2, v3}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    invoke-virtual {v2, v0}, Ljava/lang/StringBuilder;->append(Ljava/lang/Object;)Ljava/lang/StringBuilder;
    move-result-object v2
    invoke-virtual {v2}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;
    move-result-object v2
    invoke-static {v0}, Ljava/lang/String;->valueOf(Ljava/lang/Object;)Ljava/lang/String;
    move-result-object v3
    const/4 v4, 0x0
    invoke-static {v4}, Ljava/lang/String;->valueOf(Ljava/lang/Object;)Ljava/lang/String;
    move-result-object v4
    new-instance v0, Ljava/lang/StringBuilder;
    invoke-direct {v0}, Ljava/lang/StringBuilder;-><init>()V
    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    const-string v1, "|"
    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    invoke-virtual {v0, v2}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    invoke-virtual {v0, v3}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    invoke-virtual {v0, v4}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    invoke-virtual {v0}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;
    move-result-object v0
    return-object v0
.end method
"""
    smali_over = """
.class public Lt/LowerTyp;
.super Ljava/lang/Enum;
.field public static final REAL:Lt/LowerTyp;

.method static constructor <clinit>()V
    .registers 3
    new-instance v0, Lt/LowerTyp;
    const-string v1, "REAL"
    const/4 v2, 0x1
    invoke-direct {v0, v1, v2}, Ljava/lang/Enum;-><init>(Ljava/lang/String;I)V
    sput-object v0, Lt/LowerTyp;->REAL:Lt/LowerTyp;
    return-void
.end method

.method public toString()Ljava/lang/String;
    .registers 3
    invoke-super {p0}, Ljava/lang/Enum;->toString()Ljava/lang/String;
    move-result-object v0
    sget-object v1, Ljava/util/Locale;->ENGLISH:Ljava/util/Locale;
    invoke-virtual {v0, v1}, Ljava/lang/String;->toLowerCase(Ljava/util/Locale;)Ljava/lang/String;
    move-result-object v0
    return-object v0
.end method
"""
    vm = DalvikVM()
    vm.class_provider = TextClassProvider(smali_src)
    stubs = AndroidStubs(vm=vm)
    stubs.seed_framework_statics(vm)
    vm.runtime = _StubRuntime(stubs)

    res = vm.invoke("Lt/Typ;", "checkAll", "()Ljava/lang/String;", ())
    assert res.text == "INTEGER|id INTEGER|INTEGER|null"

    vm2 = DalvikVM()
    vm2.class_provider = TextClassProvider(smali_over)
    stubs2 = AndroidStubs(vm=vm2)
    stubs2.seed_framework_statics(vm2)
    vm2.runtime = _StubRuntime(stubs2)
    vm2._ensure_class_init("Lt/LowerTyp;")
    enum_val = vm2.statics["Lt/LowerTyp;"]["REAL"]
    assert vm2.invoke_virtual(enum_val, "toString", "()Ljava/lang/String;", (enum_val,)).text == "real"


def test_sqlite_query_structured_ignores_dalvik_null_ints():
    from core.java.objects import JArray, JString
    from core.java.sqlite_prefs_bridge import install_sqlite_and_prefs_grundset

    vm, stubs = _make_vm_and_stubs()
    install_sqlite_and_prefs_grundset(stubs)
    R = stubs.resolve

    _, db = R(
        "Landroid/database/sqlite/SQLiteDatabase;",
        "openOrCreateDatabase",
        "(Ljava/lang/String;Landroid/database/sqlite/SQLiteDatabase$CursorFactory;)Landroid/database/sqlite/SQLiteDatabase;",
        [JString(":memory:"), None],
        True,
    )
    R("Landroid/database/sqlite/SQLiteDatabase;", "execSQL", "(Ljava/lang/String;)V", [db, JString("CREATE TABLE demo_t(id INTEGER)")], False)

    # Dalvik passes null String arguments (groupBy, having, orderBy, limit) as int 0
    ok, cur = R(
        "Landroid/database/sqlite/SQLiteDatabase;",
        "query",
        "(Ljava/lang/String;[Ljava/lang/String;Ljava/lang/String;[Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)Landroid/database/Cursor;",
        [db, JString("sqlite_master"), JArray("[Ljava/lang/String;", [JString("name")]), JString("type=?"), JArray("[Ljava/lang/String;", [JString("table")]), 0, 0, 0],
        False,
    )
    assert ok
    assert R("Landroid/database/Cursor;", "getCount", "()I", [cur], False)[1] == 1
    assert not [d for d in vm.deviations.to_list() if d.get("kind") == "sqlite_error"]


_VG_UNPACKED = os.path.join(_ROOT, "source", "digifit.virtuagym.client.android", "base_unpacked_apktool")


@pytest.mark.skipif(not os.path.isdir(_VG_UNPACKED), reason="Entpackte Virtuagym-App nicht vorhanden")
def test_virtuagym_fitness_database_oncreate_builds_all_tables():
    from core.java.glass_runner import GlassAppRunner
    from core.java.objects import JObject

    gr = GlassAppRunner(_VG_UNPACKED, zone="original")
    ctx = gr.build(native_fusion=False)
    vm, stubs = ctx["vm"], ctx["stubs"]
    vm.max_steps = 500_000
    vm.step_budget = 500_000

    fd_cls = "Ldigifit/android/virtuagym/domain/db/FitnessDatabase;"
    db_helper = JObject(fd_cls, {})
    app_ctx = stubs.make_context()
    vm.invoke(fd_cls, "<init>", "(Landroid/content/Context;)V", (db_helper, app_ctx))
    wdb = vm.invoke_virtual(db_helper, "getWritableDatabase", "()Landroid/database/sqlite/SQLiteDatabase;", (db_helper,))

    conn = stubs._sqlite_conns[wdb.fields["_db_key"]]
    tables = {
        r[0]
        for r in conn.execute("SELECT name, type FROM sqlite_master").fetchall()
        if r[1] == "table" and not r[0].startswith("sqlite_")
    }
    expected = {
        "user_info",
        "club",
        "club_feature",
        "club_member",
        "navigation_item",
        "custom_home_screen_settings",
        "fitgroup",
        "socialupdate",
        "plan",
        "activitydef",
    }
    assert len(tables) >= 30
    assert expected.issubset(tables)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 174
    assert [d for d in vm.deviations.to_list() if "sqlite" in d.get("kind", "")] == []
    assert stubs.__dict__.get("_sqlite_err_count", {}) == {}


def test_json_to_jobj_generic_signature_lists_and_maps():
    from core.java.net_recorder import _json_to_jobj
    from core.java.objects import JObject, JString

    parent_smali = """
.class public final Ldto/Parent;
.super Ljava/lang/Object;

.field private children:Ljava/util/List;
    .annotation system Ldalvik/annotation/Signature;
        value = {
            "Ljava/util/List<",
            "Ldto/Child;",
            ">;"
        }
    .end annotation
.end field

.field private ids:Ljava/util/List;
    .annotation system Ldalvik/annotation/Signature;
        value = {
            "Ljava/util/List<",
            "Ljava/lang/Long;",
            ">;"
        }
    .end annotation
.end field

.field private tags:Ljava/util/Set;
    .annotation system Ldalvik/annotation/Signature;
        value = {
            "Ljava/util/Set<",
            "Ljava/lang/String;",
            ">;"
        }
    .end annotation
.end field

.field private childMap:Ljava/util/Map;
    .annotation system Ldalvik/annotation/Signature;
        value = {
            "Ljava/util/Map<",
            "Ljava/lang/String;",
            "Ldto/Child;",
            ">;"
        }
    .end annotation
.end field
"""
    child_smali = """
.class public final Ldto/Child;
.super Ljava/lang/Object;

.field private memberId:J
.field private title:Ljava/lang/String;
"""

    class _SrcProv:
        def get_source(self, desc):
            return {"Ldto/Parent;": parent_smali, "Ldto/Child;": child_smali}.get(desc, "")

    class _VmStub:
        class_provider = _SrcProv()

    payload = {
        "children": [{"member_id": 42, "title": "Alpha"}],
        "ids": [100, 200],
        "tags": ["a", "b"],
        "child_map": {"first": {"member_id": 7, "title": "Beta"}},
    }
    obj = _json_to_jobj(payload, cls_hint="Ldto/Parent;", vm=_VmStub())
    assert isinstance(obj, JObject) and obj.cls == "Ldto/Parent;"

    ch_list = obj.fields["children"]
    assert ch_list.cls == "Ljava/util/ArrayList;"
    ch0 = ch_list.fields["__items"][0]
    assert isinstance(ch0, JObject) and ch0.cls == "Ldto/Child;"
    assert ch0.fields["memberId"] == 42
    assert isinstance(ch0.fields["title"], JString) and ch0.fields["title"].text == "Alpha"

    ids_list = obj.fields["ids"].fields["__items"]
    assert [x.cls for x in ids_list] == ["Ljava/lang/Long;", "Ljava/lang/Long;"]
    assert [x.fields["value"] for x in ids_list] == [100, 200]

    tags_set = obj.fields["tags"]
    assert tags_set.cls == "Ljava/util/LinkedHashSet;"
    assert [t.text for t in tags_set.fields["__items"]] == ["a", "b"]

    cmap = obj.fields["childMap"]
    assert cmap.cls == "Ljava/util/HashMap;"
    cm_v = cmap.fields["map"]["first"]
    assert isinstance(cm_v, JObject) and cm_v.cls == "Ldto/Child;"
    assert cm_v.fields["memberId"] == 7
    assert cm_v.fields["title"].text == "Beta"


@pytest.mark.skipif(not os.path.isdir(_VG_UNPACKED), reason="Entpackte Virtuagym-App nicht vorhanden")
def test_virtuagym_cold_start_no_userid0_and_login_with_member_ids(tmp_path):
    import json
    from core.java.glass_runner import GlassAppRunner, InstanceState
    from core.java.net_recorder import NetResponse
    from core.java.objects import JString
    from core.java.seam_config import SeamConfig
    from core.java.surface import SceneGraphSurface, tap

    dummy_user_json = json.dumps({
        "statuscode": 200,
        "statusmessage": "OK",
        "result_count": 1,
        "timestamp": 1700000000,
        "result": {
            "id": 987654,
            "email": "demo@example.com",
            "username": "demouser",
            "username_url": "demouser",
            "username_clean": "demouser",
            "length_unit": "cm",
            "weight_unit": "kg",
            "user_avatar": "",
            "pro": 0,
            "language": "de",
            "activated": 1,
            "timestamp_edit": 1700000000,
            "cover_photo": "",
            "total_kcal": 0,
            "total_min": 0,
            "total_km": 0,
            "fitness_points": 0,
            "nr_likes": 0,
            "has_coach": False,
            "nr_followers": 0,
            "nr_following": 0,
            "originates_from_virtuagym": True,
            "member_ids": [{"club_id": 1234, "member_id": 55555, "member_pro": 0}],
            "content_language": "de",
            "birthday": "1990-01-01",
            "length": 180,
            "weight": 75,
            "country": "DE",
            "city": "Berlin",
            "timezone": "Europe/Berlin",
            "firstname": "Demo",
            "lastname": "User",
            "name": "Demo User",
            "gender": "m",
            "club_ids": [1234],
            "selected_bodymetrics": ["weight"],
        },
    }).encode("utf-8")

    reqs = []

    def _responder(req):
        u = str(getattr(req, "url", ""))
        reqs.append((getattr(req, "method", ""), u.split("?")[0]))
        if "/api/v0/user/current" in u:
            return NetResponse(200, {"Content-Type": "application/json"}, dummy_user_json, "OK")
        return NetResponse(200, {"Content-Type": "application/json"}, b'{"statuscode":200,"statusmessage":"OK","result_count":0,"timestamp":0,"result":[]}', "OK")

    inst = InstanceState(
        instance_id="test_vg_cold_login",
        package="digifit.virtuagym.client.android",
        app_root=_VG_UNPACKED,
        zone="original",
        apply_patches=False,
        seam_profile="self_cache",
        net_mode="mock",
        appdata_root=str(tmp_path / "appdata"),
        selfcache_root=os.path.join(_ROOT, "data", "vfs_selfcache"),
    )
    seam = SeamConfig.from_profile("self_cache")
    seam.persist_appdata = False
    gr = GlassAppRunner(_VG_UNPACKED, zone="original", instance_state=inst, seam_config=seam, base_dir=_ROOT)
    gr.build(net_responder=_responder, native_fusion=False)
    gr.run(max_steps=1_500_000, native_fusion=False)

    assert reqs == []
    assert "AccessActivity" in getattr(getattr(gr.stubs, "_current_activity", None), "cls", "")

    surf = SceneGraphSurface(gr.tree, vm=gr.vm, activity=getattr(gr.stubs, "_current_activity", None))
    ltb = gr.tree.root.find("log_in_text_button")
    if ltb and ltb.visible:
        tap(surf, ltb.bounds[0] + 5, ltb.bounds[1] + 5)

    edits = [n for n in gr.tree.root.walk() if n.visible and n.vid == "edit_text_input"]
    assert len(edits) >= 2
    for idx, val in enumerate(("demo@example.com", "secret123")):
        edits[idx].text = val
        if getattr(edits[idx], "_jobj", None) is not None:
            edits[idx]._jobj.fields["text"] = JString(val)
            edits[idx]._jobj.fields["$text"] = val

    lbtn = gr.tree.root.find("login_button")
    assert lbtn is not None
    gr.vm.steps = 0
    gr.vm.max_steps = 3_000_000
    gr.vm.step_budget = 3_000_000
    tap(surf, lbtn.bounds[0] + 10, lbtn.bounds[1] + 10)
    gr.runner.navigate_to_pending()

    assert [n for n in gr.tree.root.walk() if n.vid in ("dlg_title", "dlg_msg")] == []
    assert "FitnessOnboardingActivity" in getattr(getattr(gr.stubs, "_current_activity", None), "cls", "")
    vp = gr.stubs._prefs_store.get("VirtuaGym.prefs", {})
    assert vp.get("profile.userid") == 987654
    assert vp.get("profile.primary_club") == 1234
    assert "profile.clubs" in vp
