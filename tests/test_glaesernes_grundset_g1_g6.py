# -*- coding: utf-8 -*-
"""tests/test_glaesernes_grundset_g1_g6.py — Regressionstests fuer alle 6 Saeulen G1–G6."""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.java.dalvik import DalvikVM, MethodDef  # noqa: E402
from core.java.dex_loader import AndroidStubs  # noqa: E402
from core.java.lifecycle import LifecycleRunner  # noqa: E402
from core.java.net_recorder import NetLog, NetResponse, install_net_stubs  # noqa: E402
from core.java.objects import JArray, JObject, JString  # noqa: E402
from core.java.view_tree import ViewNode, install_view_stubs  # noqa: E402


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
            responder=responder or (lambda _r: NetResponse(200, {"Content-Type": "text/html"}, b"<h1>OK</h1>", "OK")),
            net_log=net_log,
        )
    vm.runtime = type("_RT", (), {"stubs": stubs})()
    return vm, stubs, tree, net_log


# ---------------------------------------------------------------------------
# G3: SQLite / Room, SharedPreferences Sync, KeyStore/Cipher & ActivityResult
# ---------------------------------------------------------------------------
def test_g3_sqlite_crud_transactions_openhelper_and_room():
    vm, stubs, _tree, _ = _vm_with_views()

    def R(cls, name, sig, args=(), static=False):
        ok, val = stubs.resolve(cls, name, sig, list(args), static)
        assert ok, f"Nicht aufgeloest: {cls}->{name}{sig}"
        return val

    db = R("Landroid/database/sqlite/SQLiteDatabase;", "openOrCreateDatabase",
           "(Ljava/lang/String;Landroid/database/sqlite/SQLiteDatabase$CursorFactory;)Landroid/database/sqlite/SQLiteDatabase;",
           (JString("g3_test.db"), None), static=True)
    R("Landroid/database/sqlite/SQLiteDatabase;", "execSQL", "(Ljava/lang/String;)V",
      (db, JString("CREATE TABLE notes(id INTEGER PRIMARY KEY AUTOINCREMENT, title 0 not null)")))

    cv = JObject("Landroid/content/ContentValues;")
    R("Landroid/content/ContentValues;", "<init>", "()V", (cv,))
    R("Landroid/content/ContentValues;", "put", "(Ljava/lang/String;Ljava/lang/String;)V", (cv, JString("title"), JString("Hello")))
    row_id = R("Landroid/database/sqlite/SQLiteDatabase;", "insert",
               "(Ljava/lang/String;Ljava/lang/String;Landroid/content/ContentValues;)J",
               (db, JString("notes"), None, cv))
    assert row_id == 1

    # Rollback-Test
    R("Landroid/database/sqlite/SQLiteDatabase;", "beginTransaction", "()V", (db,))
    cv2 = JObject("Landroid/content/ContentValues;")
    R("Landroid/content/ContentValues;", "<init>", "()V", (cv2,))
    R("Landroid/content/ContentValues;", "put", "(Ljava/lang/String;Ljava/lang/String;)V", (cv2, JString("title"), JString("RolledBack")))
    R("Landroid/database/sqlite/SQLiteDatabase;", "insert",
      "(Ljava/lang/String;Ljava/lang/String;Landroid/content/ContentValues;)J",
      (db, JString("notes"), None, cv2))
    R("Landroid/database/sqlite/SQLiteDatabase;", "endTransaction", "()V", (db,))  # kein setTransactionSuccessful

    cur = R("Landroid/database/sqlite/SQLiteDatabase;", "rawQuery",
            "(Ljava/lang/String;[Ljava/lang/String;)Landroid/database/Cursor;",
            (db, JString("SELECT id, title FROM notes"), None))
    assert R("Landroid/database/Cursor;", "getCount", "()I", (cur,)) == 1
    assert R("Landroid/database/Cursor;", "moveToFirst", "()Z", (cur,)) == 1
    cidx = R("Landroid/database/Cursor;", "getColumnIndex", "(Ljava/lang/String;)I", (cur, JString("title")))
    assert R("Landroid/database/Cursor;", "getString", "(I)Ljava/lang/String;", (cur, cidx)).text == "Hello"
    R("Landroid/database/Cursor;", "close", "()V", (cur,))

    # SQLiteOpenHelper onCreate + PRAGMA user_version
    helper_events = []

    def _oh_hook(rcls, cls, name, sig, args, static, _vm):
        if cls == "Lcom/example/MyHelper;" and name == "onCreate":
            helper_events.append("onCreate")
            R("Landroid/database/sqlite/SQLiteDatabase;", "execSQL", "(Ljava/lang/String;)V",
              (args[1], JString("CREATE TABLE items(k TEXT, v INT)")))
            return True, None
        if cls == "Lcom/example/MyHelper;" and name == "onOpen":
            helper_events.append("onOpen")
            return True, None
        return False, None

    vm._method_hooks.append(_oh_hook)
    oh = JObject("Lcom/example/MyHelper;")
    R("Landroid/database/sqlite/SQLiteOpenHelper;", "<init>",
      "(Landroid/content/Context;Ljava/lang/String;Landroid/database/sqlite/SQLiteDatabase$CursorFactory;I)V",
      (oh, None, JString("helper.db"), None, 3))
    wdb = vm.invoke_virtual(oh, "getWritableDatabase", "()Landroid/database/sqlite/SQLiteDatabase;", (oh,))
    assert helper_events == ["onCreate", "onOpen"]
    assert R("Landroid/database/sqlite/SQLiteDatabase;", "getVersion", "()I", (wdb,)) == 3

    # Room.databaseBuilder + runInTransaction
    rb = R("Landroidx/room/Room;", "databaseBuilder",
           "(Landroid/content/Context;Ljava/lang/Class;Ljava/lang/String;)Landroidx/room/RoomDatabase$Builder;",
           (None, JObject("Ljava/lang/Class;", {"name": "com.example.AppDb"}), JString("room.db")), static=True)
    rdb = R("Landroidx/room/RoomDatabase$Builder;", "build", "()Landroidx/room/RoomDatabase;", (rb,))
    sdb = R("Landroidx/sqlite/db/SupportSQLiteOpenHelper;", "getWritableDatabase", "()Landroidx/sqlite/db/SupportSQLiteDatabase;",
            (R("Landroidx/room/RoomDatabase;", "getOpenHelper", "()Landroidx/sqlite/db/SupportSQLiteOpenHelper;", (rdb,)),))
    R("Landroidx/sqlite/db/SupportSQLiteDatabase;", "execSQL", "(Ljava/lang/String;)V",
      (sdb, JString("CREATE TABLE users(name TEXT)")))
    R("Landroidx/room/RoomDatabase;", "beginTransaction", "()V", (rdb,))
    stmt = R("Landroidx/room/RoomDatabase;", "compileStatement", "(Ljava/lang/String;)Landroidx/sqlite/db/SupportSQLiteStatement;",
             (rdb, JString("INSERT INTO users(name) VALUES (?)")))
    R("Landroidx/sqlite/db/SupportSQLiteStatement;", "bindString", "(ILjava/lang/String;)V", (stmt, 1, JString("Alice")))
    assert R("Landroidx/sqlite/db/SupportSQLiteStatement;", "executeInsert", "()J", (stmt,)) == 1
    R("Landroidx/room/RoomDatabase;", "setTransactionSuccessful", "()V", (rdb,))
    R("Landroidx/room/RoomDatabase;", "endTransaction", "()V", (rdb,))
    rcur = R("Landroidx/sqlite/db/SupportSQLiteDatabase;", "query", "(Ljava/lang/String;)Landroid/database/Cursor;",
             (sdb, JString("SELECT name FROM users")))
    assert R("Landroid/database/Cursor;", "getCount", "()I", (rcur,)) == 1


def test_g3_sharedprefs_appdata_sync_keystore_and_activity_result_flags(tmp_path):
    from core.java.sqlite_prefs_bridge import persist_java_appdata_to_dir, sync_java_appdata_from_dir

    appdata = str(tmp_path / "appdata")
    vm1, stubs1, _, _ = _vm_with_views()
    ok, sp1 = stubs1.resolve("Landroid/preference/PreferenceManager;", "getDefaultSharedPreferences",
                             "(Landroid/content/Context;)Landroid/content/SharedPreferences;", [None], True)
    assert ok
    ok, ed = stubs1.resolve("Landroid/content/SharedPreferences;", "edit", "()Landroid/content/SharedPreferences$Editor;", [sp1], False)
    stubs1.resolve("Landroid/content/SharedPreferences$Editor;", "putString",
                   "(Ljava/lang/String;Ljava/lang/String;)Landroid/content/SharedPreferences$Editor;", [ed, JString("token"), JString("abc")], False)
    stubs1.resolve("Landroid/content/SharedPreferences$Editor;", "putInt",
                   "(Ljava/lang/String;I)Landroid/content/SharedPreferences$Editor;", [ed, JString("uid"), 42], False)
    ok, m_all = stubs1.resolve("Landroid/content/SharedPreferences;", "getAll", "()Ljava/util/Map;", [sp1], False)
    assert ok and m_all.fields["map"]["uid"].fields["value"] == 42
    assert persist_java_appdata_to_dir(stubs1, appdata) >= 1

    # Sync into fresh VM/Stubs instance
    vm2, stubs2, tree2, _ = _vm_with_views()
    assert sync_java_appdata_from_dir(stubs2, appdata) >= 1
    assert stubs2._prefs_store["default"]["token"].text == "abc"
    assert stubs2._prefs_store["default"]["uid"] == 42

    # KeyStore + Cipher round-trip
    ks = stubs2.resolve("Ljava/security/KeyStore;", "getInstance", "(Ljava/lang/String;)Ljava/security/KeyStore;", [JString("AndroidKeyStore")], True)[1]
    stubs2.resolve("Ljava/security/KeyStore;", "load", "(Ljava/security/KeyStore$LoadStoreParameter;)V", [ks, None], False)
    b = JObject("Landroid/security/keystore/KeyGenParameterSpec$Builder;")
    stubs2.resolve("Landroid/security/keystore/KeyGenParameterSpec$Builder;", "<init>", "(Ljava/lang/String;I)V", [b, JString("my_key"), 3], False)
    spec = stubs2.resolve("Landroid/security/keystore/KeyGenParameterSpec$Builder;", "build", "()Landroid/security/keystore/KeyGenParameterSpec;", [b], False)[1]
    kg = stubs2.resolve("Ljavax/crypto/KeyGenerator;", "getInstance", "(Ljava/lang/String;Ljava/lang/String;)Ljavax/crypto/KeyGenerator;",
                        [JString("AES"), JString("AndroidKeyStore")], True)[1]
    stubs2.resolve("Ljavax/crypto/KeyGenerator;", "init", "(Ljava/security/spec/AlgorithmParameterSpec;)V", [kg, spec], False)
    sk = stubs2.resolve("Ljavax/crypto/KeyGenerator;", "generateKey", "()Ljavax/crypto/SecretKey;", [kg], False)[1]
    assert stubs2.resolve("Ljava/security/KeyStore;", "containsAlias", "(Ljava/lang/String;)Z", [ks, JString("my_key")], False)[1] == 1

    c_enc = stubs2.resolve("Ljavax/crypto/Cipher;", "getInstance", "(Ljava/lang/String;)Ljavax/crypto/Cipher;", [JString("AES/GCM/NoPadding")], True)[1]
    stubs2.resolve("Ljavax/crypto/Cipher;", "init", "(ILjava/security/Key;)V", [c_enc, 1, sk], False)
    iv = stubs2.resolve("Ljavax/crypto/Cipher;", "getIV", "()[B", [c_enc], False)[1]
    plain = JArray("[B", list(b"secret-session-42"))
    enc = stubs2.resolve("Ljavax/crypto/Cipher;", "doFinal", "([B)[B", [c_enc, plain], False)[1]
    assert bytes(enc.data) != b"secret-session-42"

    gcm = JObject("Ljavax/crypto/spec/GCMParameterSpec;")
    stubs2.resolve("Ljavax/crypto/spec/GCMParameterSpec;", "<init>", "(I[B)V", [gcm, 128, iv], False)
    c_dec = stubs2.resolve("Ljavax/crypto/Cipher;", "getInstance", "(Ljava/lang/String;)Ljavax/crypto/Cipher;", [JString("AES/GCM/NoPadding")], True)[1]
    stubs2.resolve("Ljavax/crypto/Cipher;", "init", "(ILjava/security/Key;Ljava/security/spec/AlgorithmParameterSpec;)V", [c_dec, 2, sk, gcm], False)
    dec = stubs2.resolve("Ljavax/crypto/Cipher;", "doFinal", "([B)[B", [c_dec, enc], False)[1]
    assert bytes(dec.data) == b"secret-session-42"

    # ActivityResult + FLAG_ACTIVITY_SINGLE_TOP
    results = []
    vm2.registry[("Lcom/example/ActMain;", "<init>", "()V")] = MethodDef("Lcom/example/ActMain;", "<init>", "()V", [("return-void",)], 1, 1)
    vm2.registry[("Lcom/example/ActChild;", "<init>", "()V")] = MethodDef("Lcom/example/ActChild;", "<init>", "()V", [("return-void",)], 1, 1)

    def _act_cb_hook(rcls, cls, name, sig, args, static, _vm):
        if cls == "Lcom/example/ResultCb;" and name == "onActivityResult":
            results.append(args[1])
            return True, None
        if cls == "Lcom/example/ActMain;" and name == "onNewIntent":
            results.append("onNewIntent")
            return True, None
        return False, None

    vm2._method_hooks.append(_act_cb_hook)
    runner = LifecycleRunner(vm2, with_stubs=False, stubs=stubs2)
    act_main = JObject("Lcom/example/ActMain;")
    stubs2._current_activity = act_main
    stubs2._current_activity_obj = act_main
    tree2.set_root(ViewNode("LinearLayout", vid="main_root"))

    # SINGLE_TOP re-launch triggers onNewIntent
    st_intent = JObject("Landroid/content/Intent;", {"$target": "Lcom/example/ActMain;", "_flags": 0x20000000})
    stubs2.resolve("Landroid/app/Activity;", "startActivity", "(Landroid/content/Intent;)V", [act_main, st_intent], False)
    assert runner.navigate_to_pending() is True
    assert "onNewIntent" in results

    # registerForActivityResult(StartActivityForResult) -> setResult + finish -> callback
    contract = JObject("Landroidx/activity/result/contract/ActivityResultContracts$StartActivityForResult;")
    cb = JObject("Lcom/example/ResultCb;")
    launcher = vm2.invoke_virtual(act_main, "registerForActivityResult",
                                  "(Landroidx/activity/result/contract/ActivityResultContract;Landroidx/activity/result/ActivityResultCallback;)Landroidx/activity/result/ActivityResultLauncher;",
                                  (act_main, contract, cb))
    child_intent = JObject("Landroid/content/Intent;", {"$target": "Lcom/example/ActChild;"})
    vm2.invoke_virtual(launcher, "launch", "(Ljava/lang/Object;)V", (launcher, child_intent))
    assert runner.navigate_to_pending() is True
    act_child = stubs2._current_activity
    stubs2.resolve("Landroid/app/Activity;", "setResult", "(ILandroid/content/Intent;)V",
                   [act_child, -1, JObject("Landroid/content/Intent;", {"extras": {"ok": 1}})], False)
    stubs2.resolve("Landroid/app/Activity;", "finish", "()V", [act_child], False)
    assert runner.navigate_to_pending() is True
    assert stubs2._current_activity is act_main
    ar = next(r for r in results if isinstance(r, JObject) and r.cls == "Landroidx/activity/result/ActivityResult;")
    assert ar.fields["mResultCode"] == -1


# ---------------------------------------------------------------------------
# G1: Multi-File values/*.xml, ?attr/, Stateful Selector, ConstraintLayout
# ---------------------------------------------------------------------------
def test_g1_values_dimens_attr_cascade_and_stateful_selectors(tmp_path):
    res = tmp_path / "res"
    (res / "values").mkdir(parents=True)
    (res / "color").mkdir(parents=True)
    (res / "drawable").mkdir(parents=True)
    (res / "layout").mkdir(parents=True)

    (res / "values" / "public.xml").write_text(
        '<resources><public type="layout" name="screen" id="0x7f0c0001"/>'
        '<public type="attr" name="colorPrimary" id="0x7f040001"/></resources>',
        encoding="utf-8",
    )
    (res / "values" / "colors.xml").write_text(
        '<resources><color name="brand_blue">#2563eb</color><color name="idle_gray">#64748b</color></resources>',
        encoding="utf-8",
    )
    (res / "values" / "dimens.xml").write_text(
        '<resources><dimen name="box_pad">14dp</dimen><dimen name="btn_h">48dp</dimen></resources>',
        encoding="utf-8",
    )
    (res / "values" / "themes_extra.xml").write_text(
        '<resources><style name="Theme.Base"><item name="colorPrimary">@color/brand_blue</item></style>'
        '<style name="AppTheme" parent="Theme.Base"/></resources>',
        encoding="utf-8",
    )
    (res / "color" / "chip_text.xml").write_text(
        '<selector xmlns:android="http://schemas.android.com/apk/res/android">'
        '<item android:state_selected="true" android:color="#ffffff"/>'
        '<item android:color="@color/idle_gray"/></selector>',
        encoding="utf-8",
    )
    (res / "drawable" / "chip_bg.xml").write_text(
        '<selector xmlns:android="http://schemas.android.com/apk/res/android">'
        '<item android:state_selected="true"><shape android:shape="rectangle"><solid android:color="?attr/colorPrimary"/></shape></item>'
        '<item><shape android:shape="rectangle"><solid android:color="#f1f5f9"/></shape></item></selector>',
        encoding="utf-8",
    )
    (res / "layout" / "screen.xml").write_text(
        '<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android" '
        'android:layout_width="match_parent" android:layout_height="wrap_content" android:padding="@dimen/box_pad">'
        '<Button android:id="@+id/chip_btn" android:layout_width="wrap_content" android:layout_height="@dimen/btn_h" '
        'android:text="Option A" android:background="@drawable/chip_bg" android:textColor="@color/chip_text"/>'
        '</LinearLayout>',
        encoding="utf-8",
    )

    from core.java.drawable_loader import DrawableResolver

    resv = DrawableResolver(str(tmp_path))
    assert resv.resolve_attr("?attr/colorPrimary") == "@color/brand_blue"
    assert resv.resolve_dimen("@dimen/btn_h") == 48.0

    vm, stubs, tree, _ = _vm_with_views(app_root=str(tmp_path))
    stubs.resolve("Landroid/app/Activity;", "setContentView", "(I)V", [JObject("Landroid/app/Activity;"), 0x7F0C0001], False)
    assert tree.root.props["$pad_x"] == 14
    btn = tree.root.find("chip_btn")
    assert btn is not None and btn.props["color"] == "#64748b"
    assert btn.props["bg_drawable"]["fill"] == "#f1f5f9"

    # Toggle selected=True via View.setSelected(Z)V -> stateful ColorStateList & Selector update
    vobj = JObject("Landroid/view/View;", {"$view": btn})
    vm.invoke_virtual(vobj, "setSelected", "(Z)V", (vobj, 1))
    assert btn.props["selected"] is True
    assert btn.props["color"] == "#ffffff"
    assert btn.props["bg_drawable"]["fill"] == "#2563eb"


def test_g1_constraintlayout_ratio_guideline_chains_and_bias():
    from core.java.layout_inflater import parse_layout_xml
    from core.java.view_tree import ViewTree

    xml = """
    <androidx.constraintlayout.widget.ConstraintLayout
        xmlns:android="http://schemas.android.com/apk/res/android"
        xmlns:app="http://schemas.android.com/apk/res-auto"
        android:layout_width="match_parent" android:layout_height="wrap_content">
      <androidx.constraintlayout.widget.Guideline
          android:id="@+id/mid_guide"
          android:layout_width="wrap_content" android:layout_height="wrap_content"
          android:orientation="vertical" app:layout_constraintGuide_percent="0.4"/>
      <View android:id="@+id/banner"
          android:layout_width="match_parent" android:layout_height="0dp"
          app:layout_constraintDimensionRatio="16:9"/>
      <Button android:id="@+id/left_btn"
          android:layout_width="0dp" android:layout_height="40dp" android:text="Left"
          app:layout_constraintStart_toStartOf="parent" app:layout_constraintEnd_toStartOf="@+id/mid_guide"/>
      <Button android:id="@+id/right_btn"
          android:layout_width="0dp" android:layout_height="40dp" android:text="Right"
          app:layout_constraintStart_toEndOf="@+id/mid_guide" app:layout_constraintEnd_toEndOf="parent"/>
    </androidx.constraintlayout.widget.ConstraintLayout>
    """
    root = parse_layout_xml(xml)
    tree = ViewTree(width=400, height=800)
    tree.set_root(root)

    banner = root.find("banner")
    left_b = root.find("left_btn")
    right_b = root.find("right_btn")
    # 16:9 ratio at ~384px inner width => height ~ 216px
    assert 190 <= banner.bounds[3] <= 230
    # Left & Right sit side-by-side at the same y across 40% Guideline!
    assert left_b.bounds[1] == right_b.bounds[1]
    assert left_b.bounds[0] + left_b.bounds[2] < right_b.bounds[0]
    assert left_b.bounds[2] < right_b.bounds[2]


# ---------------------------------------------------------------------------
# G2: Jetpack Compose Color, Modifiers, Flow, LazyGrid, Pager, Chip, Slider
# ---------------------------------------------------------------------------
def test_g2_compose_color_modifiers_grid_pager_chips_slider_and_flow():
    from core.java.compose_grundset import unpack_compose_color

    assert unpack_compose_color((0xFF2563EB << 32)) == ("#2563eb", 1.0)
    assert unpack_compose_color(16) == (None, 1.0)

    vm, stubs, tree, _ = _vm_with_views()
    py_fns = {}

    def _py_hook(rcls, cls, name, sig, args, static, _vm):
        if cls in py_fns and name == "invoke":
            fn = py_fns[cls]
            res = fn(args)
            return True, res
        return False, None

    vm._method_hooks.append(_py_hook)
    orig_invoke = vm.invoke

    def _inv(cls, name, sig, args=()):
        if cls in py_fns and name == "invoke":
            return py_fns[cls](args)
        return orig_invoke(cls, name, sig, args)

    vm.invoke = _inv

    def _hk(cls, name, sig, args, static=False):
        for hk in vm._method_hooks:
            ok, val = hk(cls, cls, name, sig, list(args), static, vm)
            if ok:
                return val
        raise AssertionError(f"Nicht gehookt: {cls}->{name}{sig}")

    # PagerState + Flow + Modifiers + Widgets
    events = []
    mod0 = JObject("Landroidx/compose/ui/Modifier;")
    shape12 = _hk("Landroidx/compose/foundation/shape/RoundedCornerShapeKt;", "RoundedCornerShape-0680j_4",
                  "(F)Landroidx/compose/foundation/shape/RoundedCornerShape;", (12.0,), static=True)
    mod1 = _hk("Landroidx/compose/foundation/layout/PaddingKt;", "padding-3ABfNKs",
               "(Landroidx/compose/ui/Modifier;F)Landroidx/compose/ui/Modifier;", (mod0, 14.0), static=True)
    mod2 = _hk("Landroidx/compose/foundation/BackgroundKt;", "background-bw27NRU",
               "(Landroidx/compose/ui/Modifier;JLandroidx/compose/ui/graphics/Shape;)Landroidx/compose/ui/Modifier;",
               (mod1, (0xFFE0F2FE << 32), shape12), static=True)
    assert mod2.fields["$pad_x"] == 14 and mod2.fields["$bg_color"] == "#e0f2fe" and mod2.fields["$radius"] == 12.0

    # collectAsStateWithLifecycle
    sf = JObject("Lkotlinx/coroutines/flow/MutableStateFlow;", {"$last": JString("ready")})
    st_obj = _hk("Landroidx/lifecycle/compose/FlowExtKt;", "collectAsStateWithLifecycle",
                 "(Lkotlinx/coroutines/flow/StateFlow;Landroidx/compose/runtime/Composer;I)Landroidx/compose/runtime/State;",
                 (sf, None, 0), static=True)
    assert st_obj.fields["$state_val"].text == "ready"

    # PagerState & HorizontalPager + FilterChip + Slider + RadioButton + LazyVerticalGrid
    pcount_fn = "Lcom/example/PageCount;"
    py_fns[pcount_fn] = lambda _a: 3
    pstate = _hk("Landroidx/compose/foundation/pager/PagerStateKt;", "rememberPagerState",
                 "(IFLkotlin/jvm/functions/Function0;Landroidx/compose/runtime/Composer;II)Landroidx/compose/foundation/pager/PagerState;",
                 (1, 0.0, JObject(pcount_fn), None, 0, 0), static=True)
    assert pstate.fields["$page"] == 1 and pstate.fields["$page_count"] == 3

    pager_page_fn = "Lcom/example/PagerPage;"
    py_fns[pager_page_fn] = lambda a: _hk(
        "Landroidx/compose/material3/TextKt;", "Text--4IGK_g",
        "(Ljava/lang/String;Landroidx/compose/ui/Modifier;JLandroidx/compose/runtime/Composer;I)V",
        (JString(f"Page {a[2].fields['value']}"), mod2, (0xFF1E3A8A << 32), None, 0), static=True,
    )
    _hk("Landroidx/compose/foundation/pager/PagerKt;", "HorizontalPager-oI3XNZo",
        "(Landroidx/compose/foundation/pager/PagerState;Landroidx/compose/ui/Modifier;Lkotlin/jvm/functions/Function4;Landroidx/compose/runtime/Composer;I)V",
        (pstate, mod0, JObject(pager_page_fn), None, 0), static=True)
    assert any(n.text == "Page 1" and n.props.get("color") == "#1e3a8a" for n in tree.root.walk())

    chip_lbl_fn = "Lcom/example/ChipLbl;"
    py_fns[chip_lbl_fn] = lambda _a: _hk("Landroidx/compose/material3/TextKt;", "Text", "(Ljava/lang/String;)V", (JString("Strength"),), static=True)
    chip_clk_fn = "Lcom/example/ChipClk;"
    py_fns[chip_clk_fn] = lambda _a: events.append("chip")
    _hk("Landroidx/compose/material3/ChipKt;", "FilterChip",
        "(ZLkotlin/jvm/functions/Function0;Lkotlin/jvm/functions/Function2;Landroidx/compose/ui/Modifier;Landroidx/compose/runtime/Composer;I)V",
        (1, JObject(chip_clk_fn), JObject(chip_lbl_fn), mod0, None, 0), static=True)
    chip_btn = next((n for n in tree.root.walk() if n.cls == "Button" and n.text == "Strength"), None)
    assert chip_btn is not None and chip_btn.props["selected"] is True
    chip_btn.on_click(chip_btn)
    assert "chip" in events

    slider_fn = "Lcom/example/SliderCb;"
    py_fns[slider_fn] = lambda a: events.append(("slider", a[1].fields["value"]))
    _hk("Landroidx/compose/material3/SliderKt;", "Slider",
        "(FLkotlin/jvm/functions/Function1;Landroidx/compose/ui/Modifier;Landroidx/compose/runtime/Composer;I)V",
        (0.25, JObject(slider_fn), mod0, None, 0), static=True)
    sb = next((n for n in tree.root.walk() if n.cls == "SeekBar"), None)
    assert sb is not None
    sb.on_click(sb)
    assert any(isinstance(e, tuple) and e[0] == "slider" for e in events)

    # LazyVerticalGrid with GridCells$Fixed(2)
    gc = JObject("Landroidx/compose/foundation/lazy/grid/GridCells$Fixed;")
    _hk("Landroidx/compose/foundation/lazy/grid/GridCells$Fixed;", "<init>", "(I)V", (gc, 2))
    grid_scope_fn = "Lcom/example/GridContent;"
    grid_item_fn = "Lcom/example/GridItem;"
    py_fns[grid_item_fn] = lambda a: _hk("Landroidx/compose/material3/TextKt;", "Text", "(Ljava/lang/String;)V",
                                          (JString(f"Card {a[2].fields['value']}"),), static=True)
    py_fns[grid_scope_fn] = lambda a: _hk("Landroidx/compose/foundation/lazy/grid/LazyGridScope;", "items",
                                           "(ILkotlin/jvm/functions/Function4;)V", (a[1], 4, JObject(grid_item_fn)))
    _hk("Landroidx/compose/foundation/lazy/grid/LazyGridDslKt;", "LazyVerticalGrid",
        "(Landroidx/compose/foundation/lazy/grid/GridCells;Landroidx/compose/ui/Modifier;Lkotlin/jvm/functions/Function1;)V",
        (gc, mod0, JObject(grid_scope_fn)), static=True)
    rv = next((n for n in tree.root.walk() if n.cls == "RecyclerView"), None)
    assert rv is not None and rv.props["span_count"] == 2 and len(rv.children) == 4


# ---------------------------------------------------------------------------
# G4: WebView Inline DOM, CookieManager, evaluateJavascript & @JavascriptInterface
# ---------------------------------------------------------------------------
def test_g4_webview_inline_dom_cookies_evaljs_jsinterface_and_offline_guard():
    seen_reqs = []

    def _resp(req):
        seen_reqs.append((req.method, req.url, dict(req.headers)))
        html = (
            "<html><head><title>Portal</title></head><body>"
            "<h1>Sign In</h1>"
            "<form action='/auth' method='POST'>"
            "<input id='user' name='user' placeholder='Email'/>"
            "<button onclick=\"AppBridge.onAuth('alice')\">Login JS</button>"
            "</form>"
            "<a href='/help'>Help</a>"
            "</body></html>"
        ).encode("utf-8")
        return NetResponse(200, {"Content-Type": "text/html", "Set-Cookie": "sid=xyz789; Path=/"}, html, "OK")

    vm, stubs, tree, _ = _vm_with_views(with_net=True, responder=_resp)
    root = ViewNode("LinearLayout", props={"orientation": 1})
    wv_node = ViewNode("WebView", vid="web")
    root.add(wv_node)
    tree.set_root(root)
    wv_obj = JObject("Landroid/webkit/WebView;", {"$view": wv_node})

    # CookieManager round-trip
    cm = stubs.resolve("Landroid/webkit/CookieManager;", "getInstance", "()Landroid/webkit/CookieManager;", [], True)[1]
    stubs.resolve("Landroid/webkit/CookieManager;", "setCookie", "(Ljava/lang/String;Ljava/lang/String;)V",
                  [cm, JString("https://app.example.com"), JString("pre=1; Path=/")], False)
    assert "pre=1" in stubs.resolve("Landroid/webkit/CookieManager;", "getCookie", "(Ljava/lang/String;)Ljava/lang/String;",
                                    [cm, JString("https://app.example.com/login")], False)[1].text

    # @JavascriptInterface Dalvik bridge
    bridge_calls = []
    vm.registry[("Lcom/example/JsBridge;", "onAuth", "(Ljava/lang/String;)Ljava/lang/String;")] = MethodDef(
        "Lcom/example/JsBridge;", "onAuth", "(Ljava/lang/String;)Ljava/lang/String;", [], 2, 2
    )
    eval_results = []
    vm._method_hooks.append(
        lambda rcls, cls, nm, sig, a, st, _vm: (True, (bridge_calls.append(a[1].text), JString("token-ok"))[1])
        if (cls == "Lcom/example/JsBridge;" and nm == "onAuth")
        else ((True, (eval_results.append(a[1].text), None)[1]) if (cls == "Lcom/example/EvalCb;" and nm == "onReceiveValue") else (False, None))
    )
    vm.invoke_virtual(wv_obj, "addJavascriptInterface", "(Ljava/lang/Object;Ljava/lang/String;)V",
                      (wv_obj, JObject("Lcom/example/JsBridge;"), JString("AppBridge")))

    # loadUrl -> Cookie sent, Set-Cookie stored, inline DOM EditText/Button/Link rendered
    vm.invoke_virtual(wv_obj, "loadUrl", "(Ljava/lang/String;)V", (wv_obj, JString("https://app.example.com/login")))
    assert seen_reqs and "pre=1" in seen_reqs[0][2].get("Cookie", "")
    assert "sid=xyz789" in get_ck(stubs, "https://app.example.com")
    assert wv_node.find("wv_title").text == "Portal"
    inp = wv_node.find("wv_inp_user")
    assert inp is not None and inp.cls == "EditText"
    inp.props["$on_change_fn"]("bob@example.com")

    cb = JObject("Lcom/example/EvalCb;")
    vm.invoke_virtual(wv_obj, "evaluateJavascript", "(Ljava/lang/String;Landroid/webkit/ValueCallback;)V",
                      (wv_obj, JString("document.getElementById('user').value"), cb))
    assert eval_results[-1] == '"bob@example.com"'

    js_btn = next(n for n in wv_node.walk() if n.cls == "Button" and n.text == "Login JS")
    js_btn.on_click(js_btn)
    assert bridge_calls == ["alice"]

    # Offline safety (no net_fire installed)
    vm_off, _stubs_off, tree_off, _ = _vm_with_views(with_net=False)
    wv_off_n = ViewNode("WebView")
    tree_off.set_root(wv_off_n)
    wv_off = JObject("Landroid/webkit/WebView;", {"$view": wv_off_n})
    vm_off.invoke_virtual(wv_off, "loadUrl", "(Ljava/lang/String;)V", (wv_off, JString("https://offline.example.org/")))
    assert tree_off.web_events[-1]["url"] == "https://offline.example.org/"


def get_ck(stubs, url):
    from core.java.webview_bridge import get_cookie_header
    return get_cookie_header(stubs, url)


# ---------------------------------------------------------------------------
# G6: BoringSSL Seam, net_unified Decoding, Flutter Channels & libapp.so AOT
# ---------------------------------------------------------------------------
def test_g6_boringssl_seam_http_net_unified_channels_and_aot_metadata(tmp_path):
    from services.native_emu_service import NativeEmuService
    from services.native_ffi_service import NativeFfiService
    from services.native_os_service import NativeOsService
    from core.native.boringssl_seam import install_boringssl_seam
    from core.native.libc_stubs import _libc_alloc
    from core.java.net_unified import format_net_entry_details, unified
    from core.java.flutter_channel_bridge import invoke_flutter_channel
    from core.java.flutter_app_producer import extract_flutter_app_spec

    vm, stubs, _tree, net_log = _vm_with_views(
        with_net=True,
        responder=lambda _r: NetResponse(200, {"Content-Type": "application/json"}, b'{"pet":"Kippy"}', "OK"),
    )
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    os_svc = NativeOsService(publish=False)
    os_svc.attach_env(eng)
    os_svc.install(eng)
    ffi = NativeFfiService(publish=False)
    install_boringssl_seam(eng, ffi, stubs=stubs)

    # Wire handlers via direct FFI stub invocations
    def _call(sym, *args):
        addr = ffi.install_named_stub(eng, sym)
        for i, v in enumerate(args):
            eng.cpu.x[i] = v
        eng.cpu.lr = 0xDEAD0000
        eng.cpu.stub[addr](eng.cpu)
        return int(eng.cpu.x[0])

    ssl_ptr = _call("SSL_new", 0)
    assert ssl_ptr != 0
    sni_buf = _libc_alloc(eng, 64)
    eng.mem.write(sni_buf, b"api.kippy.eu\x00")
    assert _call("SSL_set_tlsext_host_name", ssl_ptr, sni_buf) == 1
    assert _call("SSL_connect", ssl_ptr) == 1
    assert _call("SSL_get_verify_result", ssl_ptr) == 0

    req_raw = b"GET /v1/pet HTTP/1.1\r\nHost: api.kippy.eu\r\nAuthorization: Bearer tok\r\n\r\n"
    tx_buf = _libc_alloc(eng, len(req_raw) + 16)
    eng.mem.write(tx_buf, req_raw)
    assert _call("SSL_write", ssl_ptr, tx_buf, len(req_raw)) == len(req_raw)

    rx_buf = _libc_alloc(eng, 512)
    nread = _call("SSL_read", ssl_ptr, rx_buf, 512)
    resp_raw = bytes(eng.mem.read(rx_buf, nread))
    assert b"200 OK" in resp_raw and b'{"pet":"Kippy"}' in resp_raw

    # NetLog + net_unified verification
    rows = unified(net_log, eng._os.net if hasattr(eng, "_os") else eng.os._net())
    assert any(r.get("source") == "java" and "https://api.kippy.eu/v1/pet" in r.get("url", "") for r in rows)
    assert any(r.get("source") == "native" and "[native/ssl] GET https://api.kippy.eu/v1/pet" in r.get("label", "") for r in rows)
    nat_row = next(r for r in rows if r.get("source") == "native" and "GET https://api.kippy.eu/v1/pet" in r.get("label", ""))
    assert "=== REQUEST ===" in format_net_entry_details(nat_row)

    # Flutter MethodChannel -> Java Handler & built-in SharedPreferences channel
    mc_cls = "Lio/flutter/plugin/common/MethodChannel;"
    mc = JObject(mc_cls)
    stubs.resolve(mc_cls, "<init>", "(Lio/flutter/plugin/common/BinaryMessenger;Ljava/lang/String;)V",
                  [mc, None, JString("com.kippy/ble")], False)

    def _ble_handler(rcls, cls, nm, sig, a, st, _vm):
        if cls == "Lcom/kippy/BleHandler;" and nm == "onMethodCall":
            res_obj = a[2]
            stubs.resolve("Lio/flutter/plugin/common/MethodChannel$Result;", "success",
                          "(Ljava/lang/Object;)V", [res_obj, JString("connected")], False)
            return True, None
        return False, None

    vm._method_hooks.append(_ble_handler)
    stubs.resolve(mc_cls, "setMethodCallHandler", "(Lio/flutter/plugin/common/MethodChannel$MethodCallHandler;)V",
                  [mc, JObject("Lcom/kippy/BleHandler;")], False)
    out = invoke_flutter_channel(stubs, "com.kippy/ble", "connect", {"mac": "AA:BB"})
    assert out["status"] == "success" and out["result"] == "connected"

    invoke_flutter_channel(stubs, "plugins.flutter.io/shared_preferences", "setString", {"key": "flutter.lang", "value": "de"})
    sp_all = invoke_flutter_channel(stubs, "plugins.flutter.io/shared_preferences", "getAll", {})
    assert sp_all["result"]["flutter.lang"] == "de"

    # libapp.so AOT Route/Channel/Endpoint Scanner
    lib_dir = tmp_path / "lib" / "arm64-v8a"
    lib_dir.mkdir(parents=True)
    (lib_dir / "libapp.so").write_bytes(
        b"\x7fELF" + b"\x00" * 64
        + b"\x00/home_screen\x00/pet_details\x00plugins.flutter.io/shared_preferences\x00https://api.example.io/graphql\x00"
    )
    spec = extract_flutter_app_spec(str(tmp_path))
    assert "/home_screen" in spec["aot_routes"]
    assert "plugins.flutter.io/shared_preferences" in spec["aot_channels"]
    assert "https://api.example.io/graphql" in spec["aot_endpoints"]


# ---------------------------------------------------------------------------
# G5: React Native Yoga 2D Flexbox, Visual Styles, AsyncStorage & Networking
# ---------------------------------------------------------------------------
def test_g5_rn_yoga_flexbox_styles_edittext_asyncstorage_and_networking(tmp_path):
    from core.java.rn_js_bridge import tree_from_mount_json
    from core.java.rn_yoga_seams import RnAsyncStorage, RnNetworkingBridge

    mount_data = {
        "surfaceId": 1,
        "root": {
            "tag": 1,
            "component": "RCTView",
            "props": {"style": {"padding": 16, "backgroundColor": "#ffffff", "gap": 12}},
            "children": [
                {
                    "tag": 2,
                    "component": "RCTView",
                    "props": {
                        "style": [
                            {"flexDirection": "row", "height": 50},
                            {"justifyContent": "space-between", "alignItems": "center", "backgroundColor": "#eff6ff", "borderRadius": 10},
                        ]
                    },
                    "children": [
                        {"tag": 3, "component": "Paragraph", "props": {"text": "Title", "style": {"width": 100, "height": 24, "color": "#1e3a8a"}}, "children": []},
                        {"tag": 4, "component": "Paragraph", "props": {"text": "Badge", "style": {"width": 60, "height": 24}}, "children": []},
                    ],
                },
                {
                    "tag": 5,
                    "component": "AndroidTextInput",
                    "props": {"placeholder": "Email", "style": {"height": 42, "borderWidth": 1, "borderColor": "#94a3b8"}},
                    "children": [],
                },
                {
                    "tag": 6,
                    "component": "RCTView",
                    "props": {"style": {"position": "absolute", "right": 10, "bottom": 10, "width": 44, "height": 44, "backgroundColor": "blue"}},
                    "children": [],
                },
            ],
        },
    }
    surf, rec = tree_from_mount_json(mount_data, width=380, height=700)
    row = rec.nodes[2]
    t3, t4, inp5, fab6 = rec.nodes[3], rec.nodes[4], rec.nodes[5], rec.nodes[6]

    assert row.props["bg_drawable"]["fill"] == "#eff6ff" and row.props["bg_drawable"]["radius"] == 10
    assert t3.props["color"] == "#1e3a8a"
    # Row layout: side-by-side at same centered y, space-between pushes t4 to right edge!
    assert t3.bounds[1] == t4.bounds[1]
    assert t4.bounds[0] > t3.bounds[0] + t3.bounds[2]
    assert abs((t4.bounds[0] + t4.bounds[2]) - (row.bounds[0] + row.bounds[2])) < 1.5
    # Absolute FAB pinned bottom-right
    assert fab6.bounds == (326.0, 646.0, 44.0, 44.0)
    assert fab6.props["bg_drawable"]["fill"] == "#3b82f6"

    # EditText onChangeText syncs text + emits changeText event
    inp5.props["$on_change_fn"]("user@rn.test")
    assert inp5.text == "user@rn.test"
    assert any(ev["type"] == "changeText" and ev["tag"] == 5 and ev["text"] == "user@rn.test" for ev in rec.events)

    # AsyncStorage persistence + Networking seam -> NetLog
    store_file = str(tmp_path / "rn_async.json")
    s1 = RnAsyncStorage(backing_path=store_file)
    s1.set_item("jwt", "header.payload.sig")
    s2 = RnAsyncStorage(backing_path=store_file)
    assert s2.get_item("jwt") == "header.payload.sig"

    _vm, stubs, _tree, net_log = _vm_with_views(
        with_net=True,
        responder=lambda _r: NetResponse(201, {"Content-Type": "application/json"}, b'{"created":true}', "Created"),
    )
    nb = RnNetworkingBridge(stubs=stubs)
    r = nb.send_request("POST", "https://rn.example.io/items", {"Content-Type": "application/json"}, b'{"x":1}')
    assert r["code"] == 201 and b'"created":true' in r["body"]
    assert any("https://rn.example.io/items" in (e["req"].url if isinstance(e, dict) and hasattr(e.get("req"), "url") else "") for e in net_log.entries)


def test_kueckbox_compose_login_live_typing_updates_immediately_without_focus_loss():
    import os
    import tkinter as tk
    from types import SimpleNamespace
    from core.java.glass_runner import GlassAppRunner
    from core.java.net_recorder import NetResponse
    from core.java.view_tree import paint_commands

    kb_root = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "source",
        "com.example.kueckbox",
        "base_unpacked_apktool",
    )
    if not os.path.isdir(kb_root):
        pytest.skip("KueckBox APK nicht lokal entpackt vorhanden")

    gr = GlassAppRunner(kb_root, zone="original")
    gr.build(net_responder=lambda _r: NetResponse(200, {}, b"{}", "OK"), native_fusion=False)
    gr.run(max_steps=600_000, native_fusion=False)

    # 1) Synchrones Recompose direkt beim Compose OutlinedTextField-$on_change_fn
    email_n0 = next((n for n in gr.tree.nodes() if n.cls == "EditText" and (n.props or {}).get("hint") == "E-Mail Adresse"), None)
    assert email_n0 is not None and callable(email_n0.props.get("$on_change_fn"))
    email_n0.props["$on_change_fn"]("u")
    txts0 = [c.get("text", "") for c in paint_commands(gr.tree) if c.get("op") == "text"]
    assert "u" in txts0

    # Reset Feld fuer UI-DisplayTab-Test inkl. simulierter asynchroner Rekomposition nach Klick
    email_n1 = next((n for n in gr.tree.nodes() if n.cls == "EditText" and (n.props or {}).get("hint") == "E-Mail Adresse"), None)
    assert email_n1 is not None
    email_n1.props["$on_change_fn"]("")

    try:
        tk_root = tk.Tk()
        tk_root.withdraw()
    except tk.TclError:
        return

    class _MinApp:
        def after(self, _ms, fn=None):
            if callable(fn):
                fn()

        def log(self, _m):
            pass

    dt = None
    try:
        dt = __import__("ui.tabs.display_tab", fromlist=["DisplayTab"]).DisplayTab(tk_root, _MinApp())
        dt.set_tree(gr.tree, vm="glass")

        email_node = next(n for n in gr.tree.nodes() if n.cls == "EditText" and (n.props or {}).get("hint") == "E-Mail Adresse")
        bx, by, bw, bh = email_node.bounds
        ox, oy = 16, 16 + 24
        s = getattr(dt, "_eff_s", 1.0) or 1.0
        dt._on_click(SimpleNamespace(x=int((ox + bx + bw / 2) * s), y=int((oy + by + bh / 2) * s)))

        # Rekomposition ersetzt ViewNode-Instanzen -> _focus-Anker muss live neu aufloesen
        gr.tree._compose_recompose()

        for ch, expected in (("a", "a"), ("l", "al"), ("e", "ale"), ("x", "alex")):
            dt._on_key(SimpleNamespace(keysym=ch, char=ch, state=0))
            texts = [c.get("text", "") for c in paint_commands(dt.tree) if c.get("op") == "text"]
            assert expected in texts

        dt._on_key(SimpleNamespace(keysym="BackSpace", char="", state=0))
        texts_bs = [c.get("text", "") for c in paint_commands(dt.tree) if c.get("op") == "text"]
        assert "ale" in texts_bs

        # Tab wechselt Fokus auf Passwort-Feld -> Tipp-Updates erscheinen sofort, E-Mail bleibt erhalten
        assert dt._on_key(SimpleNamespace(keysym="Tab", char="\t", state=0)) == "break"
        for ch, expected in (("1", "1"), ("2", "12"), ("3", "123")):
            dt._on_key(SimpleNamespace(keysym=ch, char=ch, state=0))
            texts_pw = [c.get("text", "") for c in paint_commands(dt.tree) if c.get("op") == "text"]
            assert expected in texts_pw
            assert "ale" in texts_pw
    finally:
        if dt is not None:
            try:
                dt.destroy()
            except Exception:
                pass
        try:
            tk_root.destroy()
        except Exception:
            pass
