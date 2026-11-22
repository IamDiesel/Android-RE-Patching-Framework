# -*- coding: utf-8 -*-
"""Regressionstests fuer com.elink.smartlock-Startup, NTFS-Case-Kollisionen & Phased-Stub-Resolution."""
from __future__ import annotations

import io
import os
import struct
import zipfile

import pytest

from core.java.class_provider import StudioClassProvider
from core.java.dalvik import DalvikVM
from core.java.dex_loader import AndroidStubs
from core.java.glass_runner import GlassAppRunner, InstanceState
from core.java.lifecycle import LifecycleRunner
from core.java.native_link import NativeMethodTable, install_native_link
from core.java.objects import JObject, JString
from core.java.seam_config import SeamConfig
from core.java.smali import MethodDef
from core.java.surface import SceneGraphSurface
from core.java.view_tree import install_view_stubs
from services.native_emu_service import NativeEmuService
from services.native_jni_env_service import NativeJniEnvService


_SMARTLOCK_ROOT = os.path.join("source", "com.elink.smartlock", "base_unpacked_apktool")


def _encode_uleb128(val: int) -> bytes:
    out = bytearray()
    while True:
        b = val & 0x7F
        val >>= 7
        if val:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _build_minimal_dex_bytes() -> bytes:
    """Erzeugt eine gueltige 1-Klassen-DEX fuer `Lt5/b;` mit `public static val()I -> 7`."""
    strings = ["I", "Ljava/lang/Object;", "Lt5/b;", "V", "val"]
    type_str_idxs = [0, 1, 2]  # I=0, Object=1, Lt5/b;=2
    # Proto 0: ()I -> shorty='I'(0), ret=I(0), params_off=0
    protos = [(0, 0, 0)]
    # Method 0: Lt5/b;->val()I -> cls=2, proto=0, name=4
    methods = [(2, 0, 4)]

    hdr_size = 112
    str_ids_off = hdr_size
    type_ids_off = str_ids_off + len(strings) * 4
    proto_ids_off = type_ids_off + len(type_str_idxs) * 4
    method_ids_off = proto_ids_off + len(protos) * 12
    class_defs_off = method_ids_off + len(methods) * 8
    data_off = class_defs_off + 32

    buf = bytearray(data_off)

    # String-Daten (mutf8 mit Laengen-ULEB)
    str_offs = []
    for s in strings:
        str_offs.append(len(buf))
        raw = s.encode("utf-8")
        buf.extend(_encode_uleb128(len(raw)))
        buf.extend(raw)
        buf.append(0)

    # code_item (aligned 4): const/4 v0, #7 (0x7012); return v0 (0x000f)
    while len(buf) % 4:
        buf.append(0)
    code_off = len(buf)
    buf.extend(struct.pack("<HHHHII", 1, 0, 0, 0, 0, 2))
    buf.extend(struct.pack("<HH", 0x7012, 0x000F))

    # class_data_item: static_fields=0, instance_fields=0, direct_methods=1, virtual_methods=0
    class_data_off = len(buf)
    buf.extend(_encode_uleb128(0))
    buf.extend(_encode_uleb128(0))
    buf.extend(_encode_uleb128(1))
    buf.extend(_encode_uleb128(0))
    # direct_method[0]: method_idx_diff=0, access=ACC_PUBLIC|ACC_STATIC (0x9), code_off
    buf.extend(_encode_uleb128(0))
    buf.extend(_encode_uleb128(0x9))
    buf.extend(_encode_uleb128(code_off))

    for i, off in enumerate(str_offs):
        struct.pack_into("<I", buf, str_ids_off + i * 4, off)
    for i, si in enumerate(type_str_idxs):
        struct.pack_into("<I", buf, type_ids_off + i * 4, si)
    for i, (sh, rt, po) in enumerate(protos):
        struct.pack_into("<III", buf, proto_ids_off + i * 12, sh, rt, po)
    for i, (ci, pi, ni) in enumerate(methods):
        struct.pack_into("<HHI", buf, method_ids_off + i * 8, ci, pi, ni)
    # class_def[0]: Lt5/b; super Ljava/lang/Object;
    struct.pack_into(
        "<8I", buf, class_defs_off,
        2, 1, 1, 0, 0xFFFFFFFF, 0, class_data_off, 0,
    )

    # Header (magic dex\n035\0, checksum dummy, file_size@32, hdr_size@36, endian@40, link/map=12B, counts@56)
    buf[0:8] = b"dex\n035\x00"
    struct.pack_into(
        "<III12x14I", buf, 32,
        len(buf), hdr_size, 0x12345678,
        len(strings), str_ids_off,
        len(type_str_idxs), type_ids_off,
        len(protos), proto_ids_off,
        0, 0,
        len(methods), method_ids_off,
        1, class_defs_off,
        len(buf) - data_off, data_off,
    )
    return bytes(buf)


def test_native_link_preserves_phased_stub_resolution():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    vm.runtime = type("_RT", (), {"stubs": stubs, "resolve_invoke": lambda self, *a: stubs.resolve(*a)})()

    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    jni = NativeJniEnvService(publish=False)
    tbl = NativeMethodTable()
    install_native_link(vm, eng, jni, table=tbl, on_load_library=lambda _n: None)

    assert vm._find_runtime_stubs() is stubs

    fl = JObject("Landroid/widget/FrameLayout;", {})
    ss = JObject("Landroid/os/SystemService;", {"name": "accessibility"})
    vm.register_method(
        "Lt/Probe;",
        "run",
        "(Landroid/widget/FrameLayout;Landroid/view/accessibility/AccessibilityManager;)Z",
        [
            ("const", 2, 1),
            ("invoke-virtual", (0, 2), "Landroid/widget/FrameLayout;", "setWillNotDraw", "(Z)V"),
            ("invoke-virtual", (1,), "Landroid/view/accessibility/AccessibilityManager;", "isTouchExplorationEnabled", "()Z"),
            ("move-result", 3),
            ("return", 3),
        ],
        nregs=4,
        nargs=2,
        static=True,
    )
    ret = vm.invoke(
        "Lt/Probe;",
        "run",
        "(Landroid/widget/FrameLayout;Landroid/view/accessibility/AccessibilityManager;)Z",
        (fl, ss),
    )
    assert ret == 0
    assert vm.missing == []
    assert stubs.missing == []


def test_activity_manager_memory_class_and_settings_global_get_float():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    install_view_stubs(stubs)
    vm.runtime = type("_RT", (), {"stubs": stubs, "resolve_invoke": lambda self, *a: stubs.resolve(*a)})()

    am = JObject("Landroid/os/SystemService;", {"name": "activity"})
    h1, mc = stubs.resolve_phased(
        ["Landroid/os/SystemService;", "Landroid/app/ActivityManager;", "Ljava/lang/Object;"],
        "Landroid/os/SystemService;",
        "Landroid/app/ActivityManager;",
        "getMemoryClass",
        "()I",
        [am],
        False,
    )
    h2, lmc = stubs.resolve_phased(
        ["Landroid/os/SystemService;", "Landroid/app/ActivityManager;", "Ljava/lang/Object;"],
        "Landroid/os/SystemService;",
        "Landroid/app/ActivityManager;",
        "getLargeMemoryClass",
        "()I",
        [am],
        False,
    )
    assert h1 and mc == 256
    assert h2 and lmc == 512

    h3, anim = stubs.resolve(
        "Landroid/provider/Settings$Global;",
        "getFloat",
        "(Landroid/content/ContentResolver;Ljava/lang/String;F)F",
        [None, JString("animator_duration_scale"), 0x3F800000],
        True,
    )
    assert h3 and pytest.approx(anim) == 1.0

    h4, quoted = stubs.resolve(
        "Lorg/json/JSONObject;",
        "quote",
        "(Ljava/lang/String;)Ljava/lang/String;",
        [JString('hi"there')],
        True,
    )
    assert h4 and isinstance(quoted, JString) and quoted.text == '"hi\\"there"'

    for cls, name, sig, args in (
        ("Landroid/view/inputmethod/BaseInputConnection;", "<init>", "(Landroid/view/View;Z)V", [JObject("Landroid/view/inputmethod/BaseInputConnection;", {}), None, 1]),
        ("Landroid/view/accessibility/AccessibilityNodeProvider;", "<init>", "()V", [JObject("Landroid/view/accessibility/AccessibilityNodeProvider;", {})]),
        ("Landroid/view/View;", "setAutoHandwritingEnabled", "(Z)V", [JObject("Landroid/view/View;", {}), 0]),
    ):
        ok, _ = stubs.resolve(cls, name, sig, args, False, allow_default=False)
        assert ok, f"Stub fehlt: {cls}->{name}{sig}"
    assert stubs.missing == []


def test_reference_queue_remove_exits_interrupt_catching_daemon_loop():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    vm.runtime = type("_RT", (), {"stubs": stubs, "resolve_invoke": lambda self, *a: stubs.resolve(*a)})()

    rq = JObject("Ljava/lang/ref/ReferenceQueue;", {})
    # Simuliert `while (true) { try { rq.remove(); } catch (InterruptedException e) {} }`
    md = MethodDef(
        "Lt/Cleaner;",
        "cleanLoop",
        "(Ljava/lang/ref/ReferenceQueue;)V",
        [
            ("invoke-virtual", (0,), "Ljava/lang/ref/ReferenceQueue;", "remove", "()Ljava/lang/ref/Reference;"),  # 0
            ("move-result-object", 1),                                                                        # 1
            ("goto", 0),                                                                                      # 2
            ("return-void",),                                                                                 # 3
        ],
        nregs=2,
        nargs=1,
        static=True,
        handlers=[(0, 2, "Ljava/lang/InterruptedException;", 0)],
    )
    vm.register(md)
    lr = LifecycleRunner(vm, with_stubs=False, stubs=stubs)
    vm.max_steps = 500
    lr._invoke_on("Lt/Cleaner;", "cleanLoop", "(Ljava/lang/ref/ReferenceQueue;)V", (rq,))
    assert vm.steps < 50


def test_studio_class_provider_case_collision_and_dex_fallback(tmp_path):
    smali_dir = tmp_path / "base_unpacked_apktool"
    upper_file = smali_dir / "smali" / "T5" / "b.smali"
    upper_file.parent.mkdir(parents=True)
    upper_file.write_text(
        ".class public LT5/b;\n.super Ljava/lang/Object;\n"
        ".method public static x()I\n.locals 1\nconst/4 v0, 0x3\nreturn v0\n.end method\n",
        encoding="utf-8",
    )
    apk_path = tmp_path / "base.apk"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("classes.dex", _build_minimal_dex_bytes())
    apk_path.write_bytes(buf.getvalue())

    prov = StudioClassProvider(str(smali_dir))
    assert prov._read_source("LT5/b;")[0] is not None
    # Unter Windows NTFS wuerde T5/b.smali auch auf Lt5/b.smali matchen; muss abgewiesen werden!
    assert prov._read_source("Lt5/b;") == (None, None)

    assert "Lt5/b;" in prov.all_class_descriptors()
    assert prov.get_super("Lt5/b;") == "Ljava/lang/Object;"
    vm = DalvikVM()
    vm.class_provider = prov
    assert vm.invoke("Lt5/b;", "val", "()I", ()) == 7
    assert vm.invoke("LT5/b;", "x", "()I", ()) == 3


def test_handler_send_message_delayed_and_countdown_latch_await():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    vm.runtime = type("_RT", (), {"stubs": stubs, "resolve_invoke": lambda self, *a: stubs.resolve(*a)})()

    ran = []
    # 1) Message.obtain(handler, runnable) + sendMessageDelayed
    r1 = JObject("Lt/R1;", {})
    vm.register_method("Lt/R1;", "run", "()V", [("return-void",)], nregs=1, nargs=1, static=False)
    hnd = JObject("Landroid/os/Handler;", {})
    ok_m, msg = stubs.resolve(
        "Landroid/os/Message;",
        "obtain",
        "(Landroid/os/Handler;Ljava/lang/Runnable;)Landroid/os/Message;",
        [hnd, r1],
        True,
    )
    assert ok_m and isinstance(msg, JObject)
    ok_s, sent = stubs.resolve(
        "Landroid/os/Handler;",
        "sendMessageDelayed",
        "(Landroid/os/Message;J)Z",
        [hnd, msg, 500],
        False,
    )
    assert ok_s and sent == 1 and len(stubs.pending_runnables) == 1

    # 2) Executor + CountDownLatch.await() fuehrt ausstehenden Runnable aus
    latch = JObject("Ljava/util/concurrent/CountDownLatch;", {})
    stubs.resolve("Ljava/util/concurrent/CountDownLatch;", "<init>", "(I)V", [latch, 1], False)
    r2 = JObject("Lt/Worker;", {})

    def _run_worker(a):
        ran.append("worker")
        stubs.resolve("Ljava/util/concurrent/CountDownLatch;", "countDown", "()V", [latch], False)
        return None

    stubs.register("Lt/Worker;", "run", "()V", _run_worker)
    stubs.pending_runnables.clear()
    stubs._queue_runnable(r2, 0)
    stubs.resolve("Ljava/util/concurrent/CountDownLatch;", "await", "()V", [latch], False)
    assert ran == ["worker"]
    assert latch.fields.get("_count") == 0


@pytest.mark.skipif(not os.path.isdir(_SMARTLOCK_ROOT), reason="com.elink.smartlock nicht entpackt")
def test_smartlock_cold_start_privacy_dialog_and_login_navigation(tmp_path):
    sc = SeamConfig.from_profile("self_cache")
    sc.persist_appdata = False
    inst = InstanceState(
        instance_id="test_smartlock_startup",
        package="com.elink.smartlock",
        app_root=_SMARTLOCK_ROOT,
        zone="original",
        appdata_root=str(tmp_path / "appdata"),
        selfcache_root=str(tmp_path / "selfcache"),
    )
    runner = GlassAppRunner(inst.app_root, zone="original", seam_config=sc, instance_state=inst)
    res = runner.run(max_steps=400_000, max_wall_s=30.0, native_fusion=True)

    assert res.get("ok") is True
    assert res.get("activity") == "Lcom/elink/lib/common/base/AppStart;"
    assert res.get("steps", 1_000_000) < 100_000

    # Keiner der gemeldeten Screenshot-Stubs darf noch als missing auftauchen
    bad_missing = {
        ("Landroid/view/inputmethod/BaseInputConnection;", "<init>"),
        ("Landroid/view/View;", "setAutoHandwritingEnabled"),
        ("Landroid/view/accessibility/AccessibilityNodeProvider;", "<init>"),
        ("Landroid/provider/Settings$Global;", "getFloat"),
        ("Landroid/widget/FrameLayout;", "setWillNotDraw"),
        ("Lorg/json/JSONObject;", "quote"),
        ("Landroid/os/SystemService;", "isTouchExplorationEnabled"),
        ("Landroid/os/SystemService;", "restartInput"),
        ("Landroid/os/SystemService;", "isSpellCheckerEnabled"),
        ("Landroid/os/SystemService;", "getEnabledSpellCheckerInfos"),
    }
    observed_missing = {
        (m.get("cls"), m.get("name"))
        for m in (runner.stubs.missing + runner.vm.missing)
        if isinstance(m, dict)
    }
    assert not (bad_missing & observed_missing), f"Fehlende Screenshot-Stubs noch vorhanden: {bad_missing & observed_missing}"

    surf = SceneGraphSurface(runner.tree, vm=runner.vm)
    surf.paint()
    vis = runner.tree.visible_nodes()
    by_vid = {n.vid: n for n in vis if n.vid}
    assert by_vid["md_title"].text == "Warning"
    assert by_vid["md_buttonDefaultNegative"].text == "Disagree"
    assert by_vid["md_buttonDefaultPositive"].text == "agree"
    assert by_vid["md_buttonDefaultPositive"].clickable is True

    # Tippen auf "agree" -> LooperScheduler + ARouter "/login/Login" -> LoginActivity
    bx, by, bw, bh = by_vid["md_buttonDefaultPositive"].bounds
    runner.vm.steps = 0
    runner.vm.max_steps = 400_000
    runner.vm.step_budget = 400_000
    surf.dispatch({"type": "tap", "x": bx + bw // 2, "y": by + bh // 2})
    runner.runner._drain_runnables()
    assert runner.runner.navigate_to_pending() is True
    surf.paint()

    assert getattr(runner.stubs, "_current_activity", None) is not None
    assert runner.stubs._current_activity.cls == "Lcom/elink/module/login/LoginActivity;"
    vis_login = {n.vid: n for n in runner.tree.visible_nodes() if n.vid}
    assert vis_login["account_edt"].focusable is True
    assert vis_login["pwd_edt"].focusable is True
    assert vis_login["login_btn"].clickable is True
    assert vis_login["login_btn"].text == "Login"
    assert vis_login["register_btn"].clickable is True
