# -*- coding: utf-8 -*-
"""Regressions- & End-to-End-Tests fuer Virtuagym Dashboard-Navigation (Community, Mein Profil,
Back-Button & alle weiteren Dashboard-Kacheln / Bottom-Tabs) sowie Compose/Dalvik-Fixes (2026-11)."""
from __future__ import annotations

import json
import os
import struct
import sys
import zlib
from types import SimpleNamespace

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.java import smali as Sm  # noqa: E402
from core.java.app_runtime import is_unrendered_surface_shell  # noqa: E402
from core.java.dalvik import DalvikException, DalvikVM  # noqa: E402
from core.java.dex_loader import AndroidStubs  # noqa: E402
from core.java.glass_runner import GlassAppRunner, InstanceState  # noqa: E402
from core.java.lifecycle import LifecycleRunner, _StubRuntime  # noqa: E402
from core.java.nav_rx_paging_g8 import attach_g8_vm_hook, install_g8_stubs  # noqa: E402
from core.java.net_recorder import NetResponse  # noqa: E402
from core.java.objects import JObject, JString  # noqa: E402
from core.java.seam_config import SeamConfig  # noqa: E402
from core.java.surface import SceneGraphSurface  # noqa: E402
from core.java.view_tree import ViewNode, ViewTree, install_view_stubs  # noqa: E402
from ui.tabs.display_tab import DisplayTab  # noqa: E402


_VG_UNPACKED = os.path.join(_ROOT, "source", "digifit.virtuagym.client.android", "base_unpacked_apktool")


def _invoke_hook_or_stub(vm, cls: str, name: str, sig: str, args, static: bool):
    for hk in list(vm._method_hooks):
        handled, ret = hk(cls, cls, name, sig, list(args), static, vm)
        if handled:
            return ret
    ok, ret = vm.runtime.resolve_invoke(cls, name, sig, list(args), static)
    assert ok, f"Missing stub/hook: {cls}->{name}{sig}"
    return ret


def _make_png(w: int = 16, h: int = 16, rgba=(30, 40, 55, 255)) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgba) * w for _ in range(h))

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )


def _isolated_runner(app_root: str, pkg: str, tmp_path) -> GlassAppRunner:
    sc = SeamConfig.from_profile("self_cache")
    sc.persist_appdata = False
    inst = InstanceState(
        instance_id=f"test_{pkg}",
        package=pkg,
        app_root=str(app_root),
        zone="original",
        appdata_root=str(tmp_path / "appdata"),
        selfcache_root=str(tmp_path / "selfcache"),
    )
    return GlassAppRunner(str(app_root), zone="original", seam_config=sc, instance_state=inst)


# ---------------------------------------------------------------------------
# 1. Unit-Tests (ohne entpacktes APK lauffaehig)
# ---------------------------------------------------------------------------
def test_integer_and_long_parse_throw_catchable_number_format_exception():
    src = (
        ".class public Lt/NumParse;\n"
        ".super Ljava/lang/Object;\n"
        ".method public static parseIntCaught(Ljava/lang/String;)I\n"
        "    .locals 1\n"
        "    :ts\n"
        "    invoke-static {p0}, Ljava/lang/Integer;->parseInt(Ljava/lang/String;)I\n"
        "    move-result v0\n"
        "    :te\n"
        "    return v0\n"
        "    :ch\n"
        "    move-exception v0\n"
        "    const v0, 99001\n"
        "    return v0\n"
        "    .catch Ljava/lang/NumberFormatException; {:ts .. :te} :ch\n"
        ".end method\n"
        ".method public static parseLongCaught(Ljava/lang/String;)J\n"
        "    .locals 2\n"
        "    :ts\n"
        "    invoke-static {p0}, Ljava/lang/Long;->parseLong(Ljava/lang/String;)J\n"
        "    move-result-wide v0\n"
        "    :te\n"
        "    return-wide v0\n"
        "    :ch\n"
        "    move-exception v0\n"
        "    const-wide/32 v0, 77001\n"
        "    return-wide v0\n"
        "    .catch Ljava/lang/NumberFormatException; {:ts .. :te} :ch\n"
        ".end method\n"
        ".method public static parseIntUncaught(Ljava/lang/String;)I\n"
        "    .locals 1\n"
        "    invoke-static {p0}, Ljava/lang/Integer;->parseInt(Ljava/lang/String;)I\n"
        "    move-result v0\n"
        "    return v0\n"
        ".end method\n"
    )
    vm = DalvikVM()
    vm.lenient = True
    stubs = AndroidStubs(vm=vm)
    vm.runtime = _StubRuntime(stubs)
    for md in Sm.parse_class(src)[1]:
        vm.register(md)

    # Auch im lenient=True-Modus muss ein aktiver .catch-Handler greifen (z. B. DeeplinkBus /user)
    assert vm.invoke("Lt/NumParse;", "parseIntCaught", "(Ljava/lang/String;)I", (JString("user"),)) == 99001
    assert vm.invoke("Lt/NumParse;", "parseIntCaught", "(Ljava/lang/String;)I", (JString("29319"),)) == 29319
    assert vm.invoke("Lt/NumParse;", "parseIntCaught", "(Ljava/lang/String;)I", (JString("2147483648"),)) == 99001
    assert vm.invoke("Lt/NumParse;", "parseLongCaught", "(Ljava/lang/String;)J", (JString("workout"),)) == 77001
    assert vm.invoke("Lt/NumParse;", "parseLongCaught", "(Ljava/lang/String;)J", (JString("2147483648"),)) == 2147483648

    # Ohne aktiven Handler liefert lenient=True 0; lenient=False wirft DalvikException
    assert vm.invoke("Lt/NumParse;", "parseIntUncaught", "(Ljava/lang/String;)I", (JString("user"),)) == 0
    vm.lenient = False
    with pytest.raises(DalvikException, match="NumberFormatException"):
        vm.invoke("Lt/NumParse;", "parseIntUncaught", "(Ljava/lang/String;)I", (JString("user"),))

    # Wrapper-Unboxing in Bundle & Intent Extras
    vm.lenient = True
    b = JObject("Landroid/os/Bundle;", {
        "map": {
            "uid": JObject("Ljava/lang/Integer;", {"value": 99001}),
            "gid": JObject("Ljava/lang/Long;", {"value": 29319}),
            "ratio": JObject("Ljava/lang/Float;", {"value": 1.5}),
            "ok": JObject("Ljava/lang/Boolean;", {"value": 1}),
        }
    })
    assert _invoke_hook_or_stub(vm, "Landroid/os/Bundle;", "getInt", "(Ljava/lang/String;)I", [b, JString("uid")], False) == 99001
    assert _invoke_hook_or_stub(vm, "Landroid/os/Bundle;", "getLong", "(Ljava/lang/String;)J", [b, JString("gid")], False) == 29319
    assert _invoke_hook_or_stub(vm, "Landroid/os/Bundle;", "getFloat", "(Ljava/lang/String;)F", [b, JString("ratio")], False) == pytest.approx(1.5)
    assert _invoke_hook_or_stub(vm, "Landroid/os/Bundle;", "getBoolean", "(Ljava/lang/String;)Z", [b, JString("ok")], False) == 1

    it = JObject("Landroid/content/Intent;", {
        "_extras": {
            "gid": JObject("Ljava/lang/Long;", {"value": 29319}),
            "flag": JObject("Ljava/lang/Boolean;", {"value": 1}),
            "label": "Club",
        }
    })
    assert _invoke_hook_or_stub(vm, "Landroid/content/Intent;", "getLongExtra", "(Ljava/lang/String;J)J", [it, JString("gid"), 0], False) == 29319
    assert _invoke_hook_or_stub(vm, "Landroid/content/Intent;", "getBooleanExtra", "(Ljava/lang/String;Z)Z", [it, JString("flag"), 0], False) == 1
    assert _invoke_hook_or_stub(vm, "Landroid/content/Intent;", "getStringExtra", "(Ljava/lang/String;)Ljava/lang/String;", [it, JString("label")], False).text == "Club"


def test_on_back_pressed_dispatcher_direct_nav_input_and_display_tab_glass_vm():
    src = (
        ".class public Lt/MyBackCb;\n"
        ".super Landroidx/activity/OnBackPressedCallback;\n"
        ".field public called:I\n"
        ".method public constructor <init>()V\n"
        "    .locals 0\n"
        "    invoke-direct {p0}, Ljava/lang/Object;-><init>()V\n"
        "    const/4 v0, 0x0\n"
        "    iput v0, p0, Lt/MyBackCb;->called:I\n"
        "    return-void\n"
        ".end method\n"
        ".method public handleOnBackPressed()V\n"
        "    .locals 1\n"
        "    const/4 v0, 0x1\n"
        "    iput v0, p0, Lt/MyBackCb;->called:I\n"
        "    return-void\n"
        ".end method\n"
        ".class public Lt/BackActs;\n"
        ".super Landroidx/activity/ComponentActivity;\n"
        ".method public constructor <init>()V\n"
        "    .locals 0\n"
        "    invoke-direct {p0}, Ljava/lang/Object;-><init>()V\n"
        "    return-void\n"
        ".end method\n"
        ".method public static onBackClick()V\n"
        "    .locals 1\n"
        "    new-instance v0, Landroidx/navigationevent/DirectNavigationEventInput;\n"
        "    invoke-virtual {v0}, Landroidx/navigationevent/DirectNavigationEventInput;->backCompleted()V\n"
        "    return-void\n"
        ".end method\n"
    )
    vm = DalvikVM()
    vm.lenient = True
    stubs = AndroidStubs(vm=vm)
    vm.runtime = _StubRuntime(stubs)
    tree = install_view_stubs(stubs)
    for cls_txt in src.split(".class "):
        if cls_txt.strip():
            for md in Sm.parse_class(".class " + cls_txt)[1]:
                vm.register(md)

    runner = LifecycleRunner(vm, stubs=stubs)
    act_home = JObject("Lt/HomeAct;", {})
    stubs._current_activity = act_home
    home_root = ViewNode("LinearLayout", vid="home_root")
    home_root.add(ViewNode("TextView", text="Landing Page"))
    tree.set_root(home_root)
    runner.push_current_activity()

    act2 = JObject("Lt/BackActs;", {})
    stubs._current_activity = act2
    stubs._activity_finished = False
    tree.root = ViewNode("LinearLayout", vid="detail_root")

    disp = _invoke_hook_or_stub(vm, "Landroidx/activity/ComponentActivity;", "getOnBackPressedDispatcher", "()Landroidx/activity/OnBackPressedDispatcher;", [act2], False)
    cb = JObject("Lt/MyBackCb;", {"$enabled": True, "called": 0})
    _invoke_hook_or_stub(vm, "Landroidx/activity/OnBackPressedDispatcher;", "addCallback", "(Landroidx/activity/OnBackPressedCallback;)V", [disp, cb], False)

    # 1) DirectNavigationEventInput.backCompleted() triggert OnBackPressedCallback.handleOnBackPressed()
    vm.invoke("Lt/BackActs;", "onBackClick", "()V", ())
    assert cb.fields["called"] == 1
    assert stubs._back_callback_consumed is True
    assert stubs._activity_finished is False

    # 2) Deaktivierter Callback -> Activity wird geschlossen (pop zurück zur HomeAct)
    _invoke_hook_or_stub(vm, "Landroidx/activity/OnBackPressedCallback;", "setEnabled", "(Z)V", [cb, 0], False)
    stubs._back_callback_consumed = False
    vm.invoke("Lt/BackActs;", "onBackClick", "()V", ())
    assert stubs._activity_finished is True
    assert runner.navigate_to_pending() is True
    assert stubs._current_activity is act_home
    assert tree.root is home_root

    # 3) Compose BackHandler(enabled=True) konsumiert runner.handle_back()
    bh_src = (
        ".class public Lt/BhFn;\n"
        ".super Ljava/lang/Object;\n"
        ".field public fired:I\n"
        ".method public invoke()Ljava/lang/Object;\n"
        "    .locals 1\n"
        "    const/4 v0, 0x1\n"
        "    iput v0, p0, Lt/BhFn;->fired:I\n"
        "    const/4 v0, 0x0\n"
        "    return-object v0\n"
        ".end method\n"
    )
    for md in Sm.parse_class(bh_src)[1]:
        vm.register(md)
    act_bh = JObject("Landroidx/activity/ComponentActivity;", {})
    stubs._current_activity = act_bh
    runner.activity_stack.append({"activity": act_home, "cls": "Lt/HomeAct;", "root": home_root, "intent": None})
    bh_fn = JObject("Lt/BhFn;", {"fired": 0})
    _invoke_hook_or_stub(vm, "Landroidx/activity/compose/BackHandlerKt;", "BackHandler", "(ZLkotlin/jvm/functions/Function0;Landroidx/compose/runtime/Composer;II)V", [1, bh_fn, None, 0, 0], True)
    assert runner.handle_back() is True
    assert bh_fn.fields["fired"] == 1
    assert stubs._current_activity is act_bh

    # 4) Ohne aktiven BackHandler stellt handle_back() act_home + Compose-State vom Stack wieder her
    act_bh.fields["_compose_back_handlers"] = []
    assert runner.handle_back() is True
    assert stubs._current_activity is act_home
    assert tree.root is home_root

    # 5) DisplayTab._render_status_overlay bewahrt vm="glass" bei RUNTIME-GRENZE -> Back bleibt aktiv
    dispatched = []
    mock_ctrl = SimpleNamespace(
        current_method=lambda: "UserProfileActivity->onResume",
        loaded_classes=lambda: ["A", "B"],
        net_log=SimpleNamespace(entries=[]),
        _clinit_errors=[],
        _resolve_root=lambda: "",
        dispatch_ui_event=lambda ev: dispatched.append(ev),
    )
    fake_tab = SimpleNamespace(
        vm="glass",
        tree=None,
        _glass_mode="paused",
        app=SimpleNamespace(debugger_tab=SimpleNamespace(ctrl=mock_ctrl)),
        _alive=lambda: True,
        _log=lambda _msg: None,
        _inphone_ctrl=lambda: None,
    )

    def _fake_set_tree(tr, vm=None, activity=None):
        fake_tab.tree = tr
        fake_tab.vm = vm
        fake_tab._activity = activity

    fake_tab.set_tree = _fake_set_tree
    DisplayTab._render_status_overlay(fake_tab, mode="paused")
    assert fake_tab.vm == "glass"
    DisplayTab._on_nav(fake_tab, "back")
    assert dispatched == ["back"]


def test_subcompose_layout_local_activity_annotated_string_and_stateflow_recompose():
    src = (
        ".class public Lt/ComposeSubFlow;\n"
        ".super Ljava/lang/Object;\n"
        ".field public step:I\n"
        ".field public flow:Ljava/lang/Object;\n"
        ".method public invoke(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;\n"
        "    .locals 5\n"
        "    check-cast p2, Ljava/lang/Integer;\n"
        "    invoke-virtual {p2}, Ljava/lang/Integer;->intValue()I\n"
        "    # Material3 Text(AnnotatedString) + CompositionLocals pruefen\n"
        "    invoke-static {}, Landroidx/activity/compose/LocalActivityKt;->getLocalActivity()Landroidx/compose/runtime/ProvidableCompositionLocal;\n"
        "    move-result-object v0\n"
        "    check-cast p1, Landroidx/compose/runtime/Composer;\n"
        "    invoke-interface {p1, v0}, Landroidx/compose/runtime/Composer;->consume(Landroidx/compose/runtime/CompositionLocal;)Ljava/lang/Object;\n"
        "    move-result-object v0\n"
        "    const-string v1, \"act-missing\"\n"
        "    if-eqz v0, :cond_no_act\n"
        "    invoke-static {}, Landroidx/compose/ui/platform/CompositionLocalsKt;->getLocalLayoutDirection()Landroidx/compose/runtime/ProvidableCompositionLocal;\n"
        "    move-result-object v0\n"
        "    invoke-interface {p1, v0}, Landroidx/compose/runtime/Composer;->consume(Landroidx/compose/runtime/CompositionLocal;)Ljava/lang/Object;\n"
        "    move-result-object v0\n"
        "    sget-object v2, Landroidx/compose/ui/unit/LayoutDirection;->Ltr:Landroidx/compose/ui/unit/LayoutDirection;\n"
        "    if-ne v0, v2, :cond_no_act\n"
        "    new-instance v0, Landroidx/compose/ui/text/AnnotatedString;\n"
        "    iget-object v1, p0, Lt/ComposeSubFlow;->flow:Ljava/lang/Object;\n"
        "    invoke-static {v1, p1}, Landroidx/compose/runtime/SnapshotStateKt;->collectAsState(Lkotlinx/coroutines/flow/StateFlow;Landroidx/compose/runtime/Composer;)Landroidx/compose/runtime/State;\n"
        "    move-result-object v1\n"
        "    invoke-interface {v1}, Landroidx/compose/runtime/State;->getValue()Ljava/lang/Object;\n"
        "    move-result-object v1\n"
        "    check-cast v1, Ljava/lang/String;\n"
        "    iput-object v1, v0, Landroidx/compose/ui/text/AnnotatedString;->text:Ljava/lang/String;\n"
        "    invoke-static {v0, p1}, Landroidx/compose/material3/TextKt;->Text(Landroidx/compose/ui/text/AnnotatedString;Landroidx/compose/runtime/Composer;)V\n"
        "    :cond_no_act\n"
        "    const/4 v0, 0x0\n"
        "    return-object v0\n"
        ".end method\n"
    )
    measure_src = (
        ".class public Lt/SubMeasureFn;\n"
        ".super Ljava/lang/Object;\n"
        ".field public maxW:I\n"
        ".field public measCount:I\n"
        ".method public invoke(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;\n"
        "    .locals 3\n"
        "    check-cast p2, Landroidx/compose/ui/unit/Constraints;\n"
        "    invoke-virtual {p2}, Landroidx/compose/ui/unit/Constraints;->unbox-impl()J\n"
        "    move-result-wide v0\n"
        "    invoke-static {v0, v1}, Landroidx/compose/ui/unit/Constraints;->getMaxWidth-impl(J)I\n"
        "    move-result v2\n"
        "    iput v2, p0, Lt/SubMeasureFn;->maxW:I\n"
        "    new-instance v2, Ljava/lang/Object;\n"
        "    check-cast p1, Landroidx/compose/ui/layout/SubcomposeMeasureScope;\n"
        "    invoke-interface {p1, v2, v2}, Landroidx/compose/ui/layout/SubcomposeMeasureScope;->subcompose(Ljava/lang/Object;Lkotlin/jvm/functions/Function2;)Ljava/util/List;\n"
        "    move-result-object v0\n"
        "    invoke-interface {v0}, Ljava/util/List;->size()I\n"
        "    move-result v0\n"
        "    iput v0, p0, Lt/SubMeasureFn;->measCount:I\n"
        "    const/4 v0, 0x0\n"
        "    return-object v0\n"
        ".end method\n"
    )
    vm = DalvikVM()
    vm.lenient = True
    vm.statics.setdefault("Landroidx/compose/ui/unit/LayoutDirection;", {})["Ltr"] = JObject(
        "Landroidx/compose/ui/unit/LayoutDirection;", {"__ename": "Ltr", "__ordinal": 0}
    )
    stubs = AndroidStubs(vm=vm)
    vm.runtime = _StubRuntime(stubs)
    tree = install_view_stubs(stubs)
    tree.width = 412
    tree.height = 820
    for s in (src, measure_src):
        for md in Sm.parse_class(s)[1]:
            vm.register(md)

    runner = LifecycleRunner(vm, stubs=stubs)
    act = JObject("Landroidx/activity/ComponentActivity;", {})
    stubs._current_activity = act
    flow = _invoke_hook_or_stub(vm, "Lkotlinx/coroutines/flow/StateFlowKt;", "MutableStateFlow", "(Ljava/lang/Object;)Lkotlinx/coroutines/flow/MutableStateFlow;", [JString("Initial")], True)
    fn = JObject("Lt/ComposeSubFlow;", {"flow": flow})

    _invoke_hook_or_stub(
        vm,
        "Landroidx/activity/compose/ComponentActivityKt;",
        "setContent",
        "(Landroidx/activity/ComponentActivity;Landroidx/compose/runtime/CompositionContext;Lkotlin/jvm/functions/Function2;)V",
        [act, None, fn],
        True,
    )
    texts0 = [n.text for n in tree.visible_nodes() if n.text]
    assert texts0 == ["Initial"]
    assert all("JObject(" not in t for t in texts0)

    # Nach-setContent Mutation auf StateFlow -> recompose_if_dirty() aktualisiert die View-Tree-Ausgabe
    _invoke_hook_or_stub(vm, "Lkotlinx/coroutines/flow/MutableStateFlow;", "setValue", "(Ljava/lang/Object;)V", [flow, JString("Heutiges Sparring")], False)
    assert tree.recompose_if_dirty() is True
    texts1 = [n.text for n in tree.visible_nodes() if n.text]
    assert texts1 == ["Heutiges Sparring"]

    # SubcomposeLayout mit Constraints + SubcomposeMeasureScope
    mfn = JObject("Lt/SubMeasureFn;", {"maxW": 0, "measCount": -1})
    _invoke_hook_or_stub(
        vm,
        "Landroidx/compose/ui/layout/SubcomposeLayoutKt;",
        "SubcomposeLayout",
        "(Landroidx/compose/ui/Modifier;Lkotlin/jvm/functions/Function2;Landroidx/compose/runtime/Composer;II)V",
        [None, mfn, None, 0, 0],
        True,
    )
    assert mfn.fields["maxW"] == 412
    assert mfn.fields["measCount"] == 0


def test_compose_navhost_obfuscated_lambda_and_class_reference_field_a():
    # R8-obfuskierte Lambda-Klassen ohne "Lambda" im Klassennamen + ClassReference.a
    src = (
        ".class public Ls9/c;\n"
        ".super Ljava/lang/Object;\n"
        ".field public rendered:I\n"
        ".method public invoke(Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;\n"
        "    .locals 1\n"
        "    const/4 v0, 0x1\n"
        "    iput v0, p0, Ls9/c;->rendered:I\n"
        "    const/4 v0, 0x0\n"
        "    return-object v0\n"
        ".end method\n"
        ".class public Ls9/b;\n"
        ".super Ljava/lang/Object;\n"
        ".field public compFn:Ls9/c;\n"
        ".method public invoke(Ljava/lang/Object;)Ljava/lang/Object;\n"
        "    .locals 2\n"
        "    const-string v0, \"settings_home\"\n"
        "    iget-object v1, p0, Ls9/b;->compFn:Ls9/c;\n"
        "    check-cast p1, Landroidx/navigation/NavGraphBuilder;\n"
        "    invoke-static {p1, v0, v1}, Landroidx/navigation/compose/NavGraphBuilderKt;->composable(Landroidx/navigation/NavGraphBuilder;Ljava/lang/String;Lkotlin/jvm/functions/Function4;)V\n"
        "    const/4 v0, 0x0\n"
        "    return-object v0\n"
        ".end method\n"
    )
    vm = DalvikVM()
    vm.lenient = True
    stubs = AndroidStubs(vm=vm)
    vm.runtime = _StubRuntime(stubs)
    tree = install_view_stubs(stubs)
    install_g8_stubs(stubs)
    attach_g8_vm_hook(stubs, vm)
    for cls_txt in src.split(".class "):
        if cls_txt.strip():
            for md in Sm.parse_class(".class " + cls_txt)[1]:
                vm.register(md)

    comp_fn = JObject("Ls9/c;", {"rendered": 0})
    builder_fn = JObject("Ls9/b;", {"compFn": comp_fn})
    nc = _invoke_hook_or_stub(vm, "Landroidx/navigation/compose/NavHostControllerKt;", "rememberNavController", "([Landroidx/navigation/Navigator;Landroidx/compose/runtime/Composer;I)Landroidx/navigation/NavHostController;", [None, None, 0], True)
    _invoke_hook_or_stub(
        vm,
        "Landroidx/navigation/compose/NavHostKt;",
        "NavHost",
        "(Landroidx/navigation/NavHostController;Ljava/lang/String;Landroidx/compose/ui/Modifier;Lkotlin/jvm/functions/Function1;Landroidx/compose/runtime/Composer;II)V",
        [nc, JString("settings_home"), None, builder_fn, None, 0, 0],
        True,
    )
    assert comp_fn.fields["rendered"] == 1

    # R8 ReflectionFactory / ClassReference.a
    jcls = JObject("Ljava/lang/Class;", {"name": "Ldigifit/android/virtuagym/MyVm;"})
    kcls = _invoke_hook_or_stub(vm, "Lkotlin/jvm/internal/Reflection;", "a", "(Ljava/lang/Class;)Lkotlin/reflect/KClass;", [jcls], True)
    assert kcls.fields.get("a") is jcls
    assert kcls.fields.get("$jclass") is jcls
    sname = _invoke_hook_or_stub(vm, "Lkotlin/jvm/internal/ClassReference;", "getSimpleName", "()Ljava/lang/String;", [kcls], False)
    assert isinstance(sname, JString) and sname.text == "MyVm"


# ---------------------------------------------------------------------------
# 2. Virtuagym Dashboard E2E Tests (auf lokalem entpackten Studio-Ordner)
# ---------------------------------------------------------------------------
def _boot_virtuagym_home(tmp_path):
    png_bg = _make_png(32, 32, (24, 28, 36, 255))
    png_icon = _make_png(24, 24, (255, 255, 255, 255))

    tiles = [
        ("Community", "virtuagym://app.virtuagym.com/fitness/group/29319"),
        ("Schreib etwas", "virtuagym://app.virtuagym.com/fitness/writepost"),
        ("Mein Profil", "virtuagym://app.virtuagym.com/fitness/user"),
        ("Terminplan", "virtuagym://app.virtuagym.com/fitness/club/schedule"),
        ("Aktivitätskalender", "virtuagym://app.virtuagym.com/fitness/calendar"),
        ("Fortschritt", "virtuagym://app.virtuagym.com/fitness/progresstracker"),
        ("Challenge", "virtuagym://app.virtuagym.com/fitness/challenge"),
        ("Workouts", "virtuagym://app.virtuagym.com/fitness/workout"),
        ("Ernährung", "virtuagym://app.virtuagym.com/fitness/app/food"),
        ("Check-In", "virtuagym://app.virtuagym.com/fitness/club/qrcheckin"),
        ("Boxring Stuttgart", "virtuagym://app.virtuagym.com/fitness/club"),
        ("Einstellungen", "virtuagym://app.virtuagym.com/fitness/settings"),
    ]

    club_payload = {
        "statuscode": 200,
        "statusmessage": "OK",
        "result_count": 1,
        "timestamp": 1760000000,
        "result": [
            {
                "id": 77001,
                "name": "Boxring Stuttgart",
                "color": "181C24",
                "accent_color": "E53935",
                "gradient_light": "222222",
                "gradient_dark": "111111",
                "logo": "https://cdn.example.test/img/logo.png",
                "print_logo": "https://cdn.example.test/img/logo.png",
                "background": "https://cdn.example.test/img/bg.png",
                "country_code": "DE",
                "city": "Stuttgart",
                "street": "Teststrasse 1",
                "zipcode": "70173",
                "website": "https://example.test",
                "features": {
                    "enable_custom_homescreen": True,
                    "enable_clubfinder": True,
                    "enable_community": True,
                    "enable_activity_calendar": True,
                    "enable_my_plan": True,
                    "enable_flexible_planning": True,
                    "enable_challenges": True,
                    "enable_workouts": True,
                    "enable_nutrition": True,
                    "enable_progress": True,
                },
            }
        ],
    }

    settings_payload = {
        "statuscode": 200,
        "statusmessage": "OK",
        "result_count": 1,
        "result": [
            {
                "club_id": 77001,
                "custom_homescreen_settings": {
                    "items_per_row": 3,
                    "header_background_type": "NONE",
                    "use_background_image": True,
                    "background_color": "181C24",
                    "background_image": "bg.png",
                    "background_overlay_color": "000000",
                    "background_overlay_opacity": 0.55,
                    "horizontal_text_alignment": "center",
                    "text_shadow_enabled": True,
                    "homescreen_items_shape": "square",
                    "homescreen_tile_border_color": "00000000",
                },
                "navigation_items": [
                    {
                        "navigation_item_id": 100 + i,
                        "home_screen_visible": True,
                        "home_screen_label": label,
                        "home_screen_order": i + 1,
                        "home_screen_picture_android": f"icon_{i + 1}.png",
                        "item_background_color": "00000000",
                        "pro_only": 0,
                        "android_link": link,
                        "deleted": 0,
                    }
                    for i, (label, link) in enumerate(tiles)
                ],
            }
        ],
    }

    seen_urls = []

    def responder(req):
        url = getattr(req, "url", "") or ""
        seen_urls.append(url)
        if url.endswith(".png") or "img/" in url or "/thumb/" in url:
            blob = png_bg if ("bg" in url or "logo" in url) else png_icon
            return NetResponse(200, {"Content-Type": "image/png"}, blob, "OK")
        if "user/current" in url:
            body = {
                "statuscode": 200,
                "statusmessage": "OK",
                "result_count": 1,
                "timestamp": 1760000000,
                "result": {
                    "id": 99001,
                    "username": "demouser",
                    "email": "demo@example.com",
                    "first_name": "Demo",
                    "last_name": "Member",
                    "displayname": "Demo Member",
                    "gender": "m",
                    "club_ids": [77001],
                    "member_ids": [
                        {"club_id": 77001, "superclub_id": 0, "member_id": 88001, "member_pro": 1}
                    ],
                    "pro": 1,
                },
            }
            return NetResponse(200, {"Content-Type": "application/json"}, json.dumps(body).encode("utf-8"), "OK")
        if "mobile_app/settings" in url:
            return NetResponse(200, {"Content-Type": "application/json"}, json.dumps(settings_payload).encode("utf-8"), "OK")
        if "v0/club" in url:
            return NetResponse(200, {"Content-Type": "application/json"}, json.dumps(club_payload).encode("utf-8"), "OK")
        if "/api/user/0" in url or "/v0/user/0/" in url:
            return NetResponse(405, {"Content-Type": "text/plain"}, b"Invalid request method: GET", "Method Not Allowed")
        if "/api/user/99001/messages" in url or "/api/v0/user/99001/messages" in url:
            body = {
                "statuscode": 200,
                "result": [
                    {
                        "message_id": 6001,
                        "message": "Heutiges Sparring um 18 Uhr!",
                        "user_id": 99001,
                        "user_avatar": "",
                        "user_displayname": "Demo Member",
                        "nr_comments": 1,
                        "nr_likes": 4,
                        "user_liked": 0,
                        "timestamp": 1760000000,
                        "video_url": "",
                        "images": [],
                        "image_width": 0,
                        "image_height": 0,
                        "posted_by_employee": True,
                        "media": [],
                    }
                ],
            }
            return NetResponse(200, {"Content-Type": "application/json"}, json.dumps(body).encode("utf-8"), "OK")
        if "/api/user/99001" in url:
            body = {
                "statuscode": 200,
                "result": {
                    "user_id": 99001,
                    "is_online": True,
                    "user_displayname": "Demo Member",
                    "user_avatar": "",
                    "cover_photo": "",
                    "pro": 1,
                    "gender": "m",
                    "nr_likes": 7,
                    "privacy_contact": False,
                    "country": "DE",
                    "city": "Stuttgart",
                    "total_kcal": 1200,
                    "total_min": 180,
                    "total_km": 15,
                    "fitness_points": 420,
                    "user_liked": False,
                    "user_following": False,
                    "nr_following": 3,
                    "nr_followers": 8,
                },
            }
            return NetResponse(200, {"Content-Type": "application/json"}, json.dumps(body).encode("utf-8"), "OK")
        if "/api/group/29319/members" in url:
            body = {
                "statuscode": 200,
                "result": [
                    {"user_id": 99001, "user_displayname": "Demo Member", "user_avatar": "", "is_admin": True}
                ],
            }
            return NetResponse(200, {"Content-Type": "application/json"}, json.dumps(body).encode("utf-8"), "OK")
        if "/api/v0/group/29319/messages" in url:
            body = {
                "statuscode": 200,
                "result": [
                    {
                        "message_id": 5001,
                        "message": "Heutiges Sparring um 18 Uhr!",
                        "user_id": 99001,
                        "user_avatar": "",
                        "user_displayname": "Demo Member",
                        "nr_comments": 2,
                        "nr_likes": 5,
                        "user_liked": 0,
                        "timestamp": 1760000000,
                        "video_url": "",
                        "images": [],
                        "image_width": 0,
                        "image_height": 0,
                        "posted_by_employee": True,
                        "media": [],
                    }
                ],
            }
            return NetResponse(200, {"Content-Type": "application/json"}, json.dumps(body).encode("utf-8"), "OK")
        if "/api/group/29319" in url:
            body = {
                "statuscode": 200,
                "result": {
                    "group_id": 29319,
                    "name": "Synthetic Boxring Community",
                    "descr": "Synthetische Testgruppe fuer Dashboard-Navigation",
                    "language": "de",
                    "joinable": True,
                    "allowed_to_comment": 1,
                    "allowed_to_post": True,
                    "nr_members": 42,
                    "pending_invitation": False,
                    "header_image": "",
                    "avatar": "",
                    "club_id": "77001",
                    "joined": True,
                    "hide_memberlist": False,
                    "pinned_message_ids": [],
                },
            }
            return NetResponse(200, {"Content-Type": "application/json"}, json.dumps(body).encode("utf-8"), "OK")
        return NetResponse(200, {"Content-Type": "application/json"}, b'{"statuscode":200,"result":[]}', "OK")

    gr = _isolated_runner(_VG_UNPACKED, "digifit.virtuagym.client.android", tmp_path)
    gr.build(net_responder=responder, native_fusion=False)
    gr.run(
        entry="Ldigifit/android/virtuagym/presentation/screen/access/virtuagym/view/AccessActivity;",
        max_steps=400_000,
        max_wall_s=60.0,
        native_fusion=False,
    )
    surf = SceneGraphSurface(gr.tree, vm=gr.vm)
    surf.paint()
    open_btn = gr.tree.root.find("log_in_text_button")
    assert open_btn is not None
    bx, by, bw, bh = open_btn.bounds
    gr.stubs._clock_ms = 1760000002000
    gr.vm.steps = 0
    gr.vm.max_steps = 400_000
    gr.vm.step_budget = 400_000
    surf.dispatch({"type": "tap", "x": bx + bw // 2, "y": by + bh // 2})
    gr.runner._drain_runnables()
    surf.paint()

    for vid, val in (("email", "demo@example.com"), ("password", "syn-pass-123")):
        nd = gr.tree.root.find(vid)
        assert nd is not None
        nd.text = val
        for sub in nd.walk():
            if sub.cls == "EditText":
                sub.text = val

    sub_btn = gr.tree.root.find("login_button")
    assert sub_btn is not None
    bx, by, bw, bh = sub_btn.bounds
    gr.stubs._clock_ms += 2000
    gr.vm.steps = 0
    gr.vm.max_steps = 2_000_000
    gr.vm.step_budget = 2_000_000
    surf.dispatch({"type": "tap", "x": bx + bw // 2, "y": by + bh // 2})
    gr.runner._drain_runnables()

    gr.run(
        entry="Ldigifit/android/virtuagym/presentation/screen/home/overview/view/HomeActivity;",
        max_steps=1_200_000,
        max_wall_s=90.0,
        native_fusion=False,
    )
    return gr, seen_urls, tiles


def _click_dashboard_tile(gr, index: int):
    surf = SceneGraphSurface(gr.tree, vm=gr.vm)
    surf.paint()
    vis = gr.tree.visible_nodes()
    touch_views = [n for n in vis if n.vid == "touch_view" and n.clickable]
    assert len(touch_views) == 12
    tv = touch_views[index]
    bx, by, bw, bh = tv.bounds
    cx = bx + max(1, bw // 2)
    cy = next(
        (cand_y for cand_y in (by + max(1, bh // 2), by + max(4, bh // 5))
         if gr.tree.clickable_at(cx, cand_y) is tv),
        by + max(4, bh // 5),
    )
    gr.vm.steps = 0
    gr.vm.max_steps = 1_500_000
    gr.vm.step_budget = 1_500_000
    assert surf.dispatch({"type": "tap", "x": cx, "y": cy}) is True
    gr.runner.navigate_to_pending()
    gr.runner._drain_runnables()
    SceneGraphSurface(gr.tree, vm=gr.vm).paint()


@pytest.mark.skipif(not os.path.isdir(_VG_UNPACKED), reason="Entpackte Virtuagym-App nicht vorhanden")
def test_virtuagym_community_tile_renders_messages_and_back_returns_to_home(tmp_path):
    gr, seen_urls, _ = _boot_virtuagym_home(tmp_path)
    _click_dashboard_tile(gr, 0)

    cur_act = getattr(gr.stubs, "_current_activity", None)
    assert cur_act is not None and "GroupDetailActivity" in cur_act.cls
    assert any("/api/group/29319?" in u or u.endswith("/api/group/29319") for u in seen_urls)
    assert any("/api/group/29319/members" in u for u in seen_urls)
    assert any("/api/v0/group/29319/messages" in u for u in seen_urls)

    texts = [n.text for n in gr.tree.visible_nodes() if (n.text or "").strip()]
    assert any("Synthetic Boxring Community" in t for t in texts)
    assert any("Heutiges Sparring um 18 Uhr!" in t for t in texts)
    assert not any("JObject(" in t for t in texts)

    gr.vm.steps = 0
    gr.vm.max_steps = 1_000_000
    gr.vm.step_budget = 1_000_000
    assert gr.runner.handle_back() is True
    home_act = getattr(gr.stubs, "_current_activity", None)
    assert home_act is not None and "HomeActivity" in home_act.cls
    assert len([n for n in gr.tree.visible_nodes() if n.vid == "tile_label"]) == 12


@pytest.mark.skipif(not os.path.isdir(_VG_UNPACKED), reason="Entpackte Virtuagym-App nicht vorhanden")
def test_virtuagym_profile_tile_uses_logged_in_user_id_renders_and_goes_back(tmp_path):
    gr, seen_urls, _ = _boot_virtuagym_home(tmp_path)
    seen_before = len(seen_urls)
    _click_dashboard_tile(gr, 2)

    cur_act = getattr(gr.stubs, "_current_activity", None)
    assert cur_act is not None and "UserProfileActivity" in cur_act.cls
    req_after = seen_urls[seen_before:]
    assert any("/api/user/99001" in u for u in req_after)
    assert any("/api/v0/user/99001/messages" in u for u in req_after)
    assert not any("/user/0" in u for u in req_after)
    assert not is_unrendered_surface_shell(gr.tree)

    texts = [n.text for n in gr.tree.visible_nodes() if (n.text or "").strip()]
    assert any("Demo Member" in t for t in texts)
    assert any("Heutiges Sparring um 18 Uhr!" in t for t in texts)
    assert not any("JObject(" in t for t in texts)

    gr.vm.steps = 0
    gr.vm.max_steps = 1_000_000
    gr.vm.step_budget = 1_000_000
    assert gr.runner.handle_back() is True
    home_act = getattr(gr.stubs, "_current_activity", None)
    assert home_act is not None and "HomeActivity" in home_act.cls


@pytest.mark.skipif(not os.path.isdir(_VG_UNPACKED), reason="Entpackte Virtuagym-App nicht vorhanden")
def test_virtuagym_all_other_dashboard_tiles_and_bottom_tabs(tmp_path):
    gr, _, _ = _boot_virtuagym_home(tmp_path)

    expected_activities = {
        1: "ComposePostActivity",
        3: "ScheduleActivity",
        5: "ProgressOverviewActivity",
        6: "ChallengeOverviewActivity",
        7: "WorkoutOverviewActivity",
        9: "QrCodeGeneratorActivity",
        10: "ClubDetailActivity",
        11: "FitnessSettingsActivity",
    }

    for idx, exp_cls in expected_activities.items():
        _click_dashboard_tile(gr, idx)
        cur_act = getattr(gr.stubs, "_current_activity", None)
        assert cur_act is not None and exp_cls in cur_act.cls, f"Tile {idx}: got {getattr(cur_act, 'cls', None)}"
        assert gr.tree.visible_nodes(), f"Tile {idx} ({exp_cls}) rendered no visible nodes"

        gr.vm.steps = 0
        gr.vm.max_steps = 1_000_000
        gr.vm.step_budget = 1_000_000
        assert gr.runner.handle_back() is True, f"Back failed from Tile {idx} ({exp_cls})"
        back_act = getattr(gr.stubs, "_current_activity", None)
        assert back_act is not None and "HomeActivity" in back_act.cls

    # Tile 8 (Ernaehrung -> FoodAppNavigator-Dialog auf HomeActivity) vor Tab-Wechsel testen
    _click_dashboard_tile(gr, 8)
    assert "HomeActivity" in getattr(gr.stubs._current_activity, "cls", "")
    assert gr.tree.visible_nodes()
    if gr.tree.root is not None:
        gr.tree.root.children = [c for c in gr.tree.root.children if c.props.get("role") != "dialog"]
        gr.tree.touch()

    # Tile 4 (Aktivitaetskalender -> Tabwechsel auf HomeActivity ohne HomeMyPlanPresenter NPE)
    _click_dashboard_tile(gr, 4)
    assert "HomeActivity" in getattr(gr.stubs._current_activity, "cls", "")
    assert gr.tree.visible_nodes()

    # Bottom-Tabs ("Social", "Me") & zurueck zu "Home" (mit allen 12 Dashboard-Kacheln)
    for tab_label in ("Social", "Me", "Home"):
        surf_tab = SceneGraphSurface(gr.tree, vm=gr.vm)
        surf_tab.paint()
        tab_node = next(
            (n for n in gr.tree.visible_nodes() if (n.text or "").strip() == tab_label),
            None,
        )
        assert tab_node is not None, f"Bottom tab {tab_label!r} not found"
        tx, ty, tw, th = tab_node.bounds
        gr.vm.steps = 0
        gr.vm.max_steps = 1_000_000
        gr.vm.step_budget = 1_000_000
        assert surf_tab.dispatch({"type": "tap", "x": tx + max(1, tw // 2), "y": ty + max(1, th // 2)}) is True
        gr.runner.navigate_to_pending()
        gr.runner._drain_runnables()
        assert gr.tree.visible_nodes()
    assert len([n for n in gr.tree.visible_nodes() if n.vid == "tile_label"]) == 12
