# -*- coding: utf-8 -*-
"""Tests fuer Jetpack-Compose-Dashboard/Schedule-Infrastruktur:
- java.time (Instant/ZoneId/ZonedDateTime/DateTimeFormatter/LocalDate) + SimpleDateFormat + Date + Locale
- TreeMap(Map) / MapsKt.toSortedMap + Map$Entry.getKey/getValue mit LocalDate-Keys
- BuildersKt.async + AwaitKt.awaitAll + Deferred.await
- Call-site-keyed Composer.rememberedValue / updateRememberedValue & key-tracking changed()
- LazyListScope (stickyHeader, items), inlined Row/Column/Box, Modifier.clickable auf Card,
  Scaffold (topBar -> content -> bottomBar), NavigationBarItem & Dark-Theme paint_commands.
"""
import datetime as dt

from core.java.dalvik import DalvikVM, Frame, MethodDef
from core.java.dex_loader import AndroidStubs
from core.java.objects import JObject, JString
from core.java.view_tree import ViewTree, install_view_stubs, paint_commands


def _mk_vm():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    vm.runtime = type("_R", (), {"stubs": stubs})()

    def _r(cls, name, sig, args=(), static=False):
        ok, res = stubs.resolve(cls, name, sig, list(args), static)
        assert ok, f"Stub not resolved: {cls}->{name}{sig}"
        return res

    tree = install_view_stubs(stubs)
    return vm, stubs, tree, _r


def test_java_time_instant_formatter_and_simpledateformat():
    vm, stubs, _tree, R = _mk_vm()
    stubs.seed_framework_statics(vm)

    # Tuesday 2026-11-17 16:45 local time
    ts = int(dt.datetime(2026, 11, 17, 16, 45, 0).timestamp())
    inst = R("Ljava/time/Instant;", "ofEpochSecond", "(J)Ljava/time/Instant;", (ts,), True)
    zid = R("Ljava/time/ZoneId;", "systemDefault", "()Ljava/time/ZoneId;", (), True)
    zdt = R("Ljava/time/Instant;", "atZone", "(Ljava/time/ZoneId;)Ljava/time/ZonedDateTime;", (inst, zid), False)
    ld = R("Ljava/time/ZonedDateTime;", "toLocalDate", "()Ljava/time/LocalDate;", (zdt,), False)
    assert isinstance(ld, JObject) and ld.cls == "Ljava/time/LocalDate;"

    fmt_short = R("Ljava/time/format/DateTimeFormatter;", "ofPattern",
                  "(Ljava/lang/String;)Ljava/time/format/DateTimeFormatter;", (JString("dd.MM."),), True)
    s_short = R("Ljava/time/LocalDate;", "format", "(Ljava/time/format/DateTimeFormatter;)Ljava/lang/String;",
                (ld, fmt_short), False)
    assert s_short.text == "17.11."

    de_loc = vm.statics["Ljava/util/Locale;"]["GERMAN"]
    fmt_de = R("Ljava/time/format/DateTimeFormatter;", "ofPattern",
               "(Ljava/lang/String;Ljava/util/Locale;)Ljava/time/format/DateTimeFormatter;",
               (JString("EEEE, dd.MM.yyyy"), de_loc), True)
    s_de = R("Ljava/time/LocalDate;", "format", "(Ljava/time/format/DateTimeFormatter;)Ljava/lang/String;",
             (ld, fmt_de), False)
    assert s_de.text == "Dienstag, 17.11.2026"

    # SimpleDateFormat("HH:mm", Locale.getDefault())
    def_loc = R("Ljava/util/Locale;", "getDefault", "()Ljava/util/Locale;", (), True)
    sdf = JObject("Ljava/text/SimpleDateFormat;")
    R("Ljava/text/SimpleDateFormat;", "<init>", "(Ljava/lang/String;Ljava/util/Locale;)V",
      (sdf, JString("HH:mm"), def_loc), False)
    jdate = JObject("Ljava/util/Date;")
    R("Ljava/util/Date;", "<init>", "(J)V", (jdate, ts * 1000), False)
    formatted_time = R("Ljava/text/SimpleDateFormat;", "format", "(Ljava/util/Date;)Ljava/lang/String;",
                       (sdf, jdate), False)
    assert formatted_time.text == "16:45"

    # Resources.getDrawable returns BitmapDrawable instead of 0
    res = JObject("Landroid/content/res/Resources;")
    dr = R("Landroid/content/res/Resources;", "getDrawable",
           "(ILandroid/content/res/Resources$Theme;)Landroid/graphics/drawable/Drawable;",
           (res, 12345, None), False)
    assert isinstance(dr, JObject) and "Drawable" in dr.cls


def test_treemap_to_sorted_map_and_map_entry_preserve_localdate_keys():
    vm, _stubs, _tree, R = _mk_vm()
    d_wed = R("Ljava/time/LocalDate;", "of", "(III)Ljava/time/LocalDate;", (2026, 11, 18), True)
    d_tue = R("Ljava/time/LocalDate;", "of", "(III)Ljava/time/LocalDate;", (2026, 11, 17), True)
    # Two distinct calls for same date yield canonical key match
    d_tue_dup = R("Ljava/time/LocalDate;", "plusDays", "(J)Ljava/time/LocalDate;",
                  (R("Ljava/time/LocalDate;", "of", "(III)Ljava/time/LocalDate;", (2026, 11, 16), True), 1), False)

    lhm = JObject("Ljava/util/LinkedHashMap;")
    R("Ljava/util/LinkedHashMap;", "<init>", "()V", (lhm,), False)
    R("Ljava/util/Map;", "put", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", (lhm, d_wed, JString("W")), False)
    R("Ljava/util/Map;", "put", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", (lhm, d_tue, JString("T1")), False)
    got = R("Ljava/util/Map;", "get", "(Ljava/lang/Object;)Ljava/lang/Object;", (lhm, d_tue_dup), False)
    assert isinstance(got, JString) and got.text == "T1"

    sorted_map = R("Lkotlin/collections/MapsKt;", "toSortedMap", "(Ljava/util/Map;)Ljava/util/SortedMap;", (lhm,), True)
    eset = R("Ljava/util/SortedMap;", "entrySet", "()Ljava/util/Set;", (sorted_map,), False)
    entries = eset.fields["__items"]
    keys = [R("Ljava/util/Map$Entry;", "getKey", "()Ljava/lang/Object;", (ent,), False) for ent in entries]
    vals = [R("Ljava/util/Map$Entry;", "getValue", "()Ljava/lang/Object;", (ent,), False) for ent in entries]

    assert [k.fields["day"] for k in keys] == [17, 18]
    assert [v.text for v in vals] == ["T1", "W"]


def test_builders_async_and_await_all_and_callsite_composer_slots():
    vm, _stubs, tree, _r = _mk_vm()
    vm.register_method(
        "Lcom/example/Worker;", "invoke",
        "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;",
        [("const", 2, 7), ("new-instance", 3, "Lcom/example/Detail;"), ("return-object", 3)],
        nregs=4, nargs=3, static=False,
    )
    vm.register_method(
        "Lcom/example/ScreenA;", "render",
        "(Landroidx/compose/runtime/Composer;)Ljava/lang/Object;",
        [("return-void",)], nregs=2, nargs=2, static=False,
    )

    def _hook(cls, name, sig, args, static=False, rcls=None):
        for hk in vm._method_hooks:
            ok, val = hk(rcls or cls, cls, name, sig, list(args), static, vm)
            if ok:
                return val
        raise AssertionError(f"not hooked: {cls}->{name}")

    scope = JObject("Lkotlinx/coroutines/CoroutineScope;")
    w1 = JObject("Lcom/example/Worker;")
    d1 = _hook("Lkotlinx/coroutines/BuildersKt;", "async$default",
               "(Lkotlinx/coroutines/CoroutineScope;Lkotlin/coroutines/CoroutineContext;Lkotlinx/coroutines/CoroutineStart;Lkotlin/jvm/functions/Function2;ILjava/lang/Object;)Lkotlinx/coroutines/Deferred;",
               (scope, None, None, w1, 3, None), static=True)
    assert isinstance(d1.fields.get("$res"), JObject) and d1.fields["$res"].cls == "Lcom/example/Detail;"

    col = JObject("Ljava/util/ArrayList;", {"__items": [d1]})
    awaited = _hook("Lkotlinx/coroutines/AwaitKt;", "awaitAll",
                    "(Ljava/util/Collection;Lkotlin/coroutines/Continuation;)Ljava/lang/Object;",
                    (col, None), static=True)
    assert len(awaited.fields["__items"]) == 1
    assert awaited.fields["__items"][0].cls == "Lcom/example/Detail;"

    single_awaited = _hook("Lkotlinx/coroutines/Deferred;", "await",
                           "(Lkotlin/coroutines/Continuation;)Ljava/lang/Object;",
                           (d1, None), static=False, rcls=d1.cls)
    assert single_awaited is awaited.fields["__items"][0]

    # Call-site-keyed rememberedValue + changed tracking across screens
    comp = JObject("Landroidx/compose/runtime/ComposerImpl;")
    empty = _hook("Landroidx/compose/runtime/Composer$Companion;", "getEmpty", "()Ljava/lang/Object;", (), static=True)

    fr_a = Frame(2)
    fr_a.md = MethodDef("Lcom/example/LoginScreen;", "Render", "()V", [], 1, 0, True)
    fr_a.cur = 10
    fr_b = Frame(2)
    fr_b.md = MethodDef("Lcom/example/DashboardScreen;", "Render", "()V", [], 1, 0, True)
    fr_b.cur = 10

    vm._frames = [fr_a]
    assert _hook("Landroidx/compose/runtime/Composer;", "rememberedValue", "()Ljava/lang/Object;", (comp,)) is empty
    fr_a.cur = 15
    _hook("Landroidx/compose/runtime/Composer;", "updateRememberedValue", "(Ljava/lang/Object;)V", (comp, JString("login_email")))

    # Switch to DashboardScreen -> must NOT collide with LoginScreen's slot
    vm._frames = [fr_b]
    assert _hook("Landroidx/compose/runtime/Composer;", "rememberedValue", "()Ljava/lang/Object;", (comp,)) is empty
    fr_b.cur = 18
    _hook("Landroidx/compose/runtime/Composer;", "updateRememberedValue", "(Ljava/lang/Object;)V", (comp, JString("tab_0")))

    # Register root_fn2 so _compose_recompose resets per-pass occurrence counters
    def _root_fn(rcls, cls, name, s, args, static, _vm):
        if cls == "Lcom/example/RootPass;" and name == "invoke":
            return True, None
        return False, None
    vm._method_hooks.append(_root_fn)
    _hook("Landroidx/activity/compose/ComponentActivityKt;", "setContent$default",
          "(Landroidx/activity/ComponentActivity;Landroidx/compose/runtime/CompositionContext;Lkotlin/jvm/functions/Function2;ILjava/lang/Object;)V",
          (None, None, JObject("Lcom/example/RootPass;"), 1, None), static=True)

    # changed() detects unchanged vs changed LocalDate across recomposition passes
    d_nov16 = JObject("Ljava/time/LocalDate;", {"year": 2026, "month": 11, "day": 16})
    d_nov23 = JObject("Ljava/time/LocalDate;", {"year": 2026, "month": 11, "day": 23})
    fr_b.cur = 25
    vm._frames = [fr_b]
    assert _hook("Landroidx/compose/runtime/Composer;", "changed", "(Ljava/lang/Object;)Z", (comp, d_nov16)) == 1
    tree._compose_recompose()
    vm._frames = [fr_b]
    assert _hook("Landroidx/compose/runtime/Composer;", "changed", "(Ljava/lang/Object;)Z", (comp, d_nov16)) == 0
    tree._compose_recompose()
    vm._frames = [fr_b]
    assert _hook("Landroidx/compose/runtime/Composer;", "changed", "(Ljava/lang/Object;)Z", (comp, d_nov23)) == 1
    vm._frames = []


def test_scaffold_lazy_list_inlined_layouts_card_click_and_dark_paint():
    vm, _stubs, tree, _r = _mk_vm()
    clicked = []
    py_fns = {}

    def _dispatch_py(rcls, cls, name, s, args, static, _vm):
        if cls in py_fns and name == "invoke":
            py_fns[cls](args)
            return True, None
        return False, None

    vm._method_hooks.append(_dispatch_py)

    # Delegate invoke calls through vm._method_hooks (replacing _invoke helpers cleanly via registry stub)
    orig_invoke = vm.invoke

    def _inv_with_hooks(cls, name, sig, args=()):
        if cls in py_fns and name == "invoke":
            py_fns[cls](args)
            return None
        return orig_invoke(cls, name, sig, args)

    vm.invoke = _inv_with_hooks

    def _hk(cls, name, sig, args, static=False, rcls=None):
        for hk in vm._method_hooks:
            ok, val = hk(rcls or cls, cls, name, sig, list(args), static, vm)
            if ok:
                return val
        raise AssertionError(f"not hooked: {cls}->{name}")

    def _reg_fn(cls_name, sig, py_fn):
        py_fns[cls_name] = py_fn
        return JObject(cls_name)

    # darkColorScheme sets dark_mode
    _hk("Landroidx/compose/material3/ColorSchemeKt;", "darkColorScheme$default",
        "(I)Landroidx/compose/material3/ColorScheme;", (0,), static=True)
    assert tree.dark_mode is True

    comp = JObject("Landroidx/compose/runtime/ComposerImpl;")
    nav_click = _reg_fn("Lcom/example/NavClick;", "", lambda _a: clicked.append("nav"))
    top_title = _reg_fn("Lcom/example/TopTitle;", "", lambda a: _hk(
        "Landroidx/compose/material3/TextKt;", "Text-Nvy7gAk", "(Ljava/lang/String;)V", (JString("Wochenplan"),), static=True))
    top_act = _reg_fn("Lcom/example/TopActions;", "", lambda a: _hk(
        "Landroidx/compose/material3/ButtonKt;", "TextButton", "(Lkotlin/jvm/functions/Function0;Lkotlin/jvm/functions/Function3;)V",
        (nav_click, _reg_fn("Lcom/example/LogoutTxt;", "", lambda _a: _hk(
            "Landroidx/compose/material3/TextKt;", "Text-Nvy7gAk", "(Ljava/lang/String;)V", (JString("Logout"),), static=True))), static=True))
    topbar_fn = _reg_fn("Lcom/example/TopBarSlot;", "", lambda a: _hk(
        "Landroidx/compose/material3/AppBarKt;", "TopAppBar-GHTll3U",
        "(Lkotlin/jvm/functions/Function2;Lkotlin/jvm/functions/Function3;)V",
        (top_title, top_act), static=True))

    nav_lbl = _reg_fn("Lcom/example/NavLbl;", "", lambda a: _hk(
        "Landroidx/compose/material3/TextKt;", "Text-Nvy7gAk", "(Ljava/lang/String;)V", (JString("Wochenplan"),), static=True))
    bottombar_fn = _reg_fn("Lcom/example/BottomSlot;", "", lambda a: (
        _hk("Landroidx/compose/material3/NavigationBarKt;", "NavigationBar-HsRjFd4",
            "(Lkotlin/jvm/functions/Function3;)V",
            (_reg_fn("Lcom/example/NavBarBody;", "", lambda a2: _hk(
                "Landroidx/compose/material3/NavigationBarKt;", "NavigationBarItem",
                "(Landroidx/compose/foundation/layout/RowScope;ZLkotlin/jvm/functions/Function0;Lkotlin/jvm/functions/Function2;)V",
                (None, 1, nav_click, nav_lbl), static=True)),),
            static=True)
    ))

    sticky_body = _reg_fn("Lcom/example/StickyBody;", "", lambda a: _hk(
        "Landroidx/compose/material3/TextKt;", "Text-Nvy7gAk", "(Ljava/lang/String;)V", (JString("Dienstag, 17.11.2026"),), static=True))
    card_click = _reg_fn("Lcom/example/OnCard;", "", lambda _a: clicked.append("card"))
    card_content = _reg_fn("Lcom/example/CardContent;", "", lambda _a: _hk(
        "Landroidx/compose/material3/TextKt;", "Text-Nvy7gAk", "(Ljava/lang/String;)V", (JString("Kickboxen"),), static=True))
    item_body = _reg_fn("Lcom/example/ItemBody;", "", lambda a: (
        _hk("Landroidx/compose/material3/CardKt;", "Card",
            "(Landroidx/compose/ui/Modifier;Lkotlin/jvm/functions/Function3;)V",
            (_hk("Landroidx/compose/foundation/ClickableKt;", "clickable-oSLSa3U$default",
                 "(Landroidx/compose/ui/Modifier;Lkotlin/jvm/functions/Function0;)Landroidx/compose/ui/Modifier;",
                 (JObject("Landroidx/compose/ui/Modifier;"), card_click),
                 static=True),
             card_content),
            static=True)
    ))

    def _lazy_builder(args):
        lscope = args[1]
        _hk("Landroidx/compose/foundation/lazy/LazyListScope;", "stickyHeader$default",
            "(Landroidx/compose/foundation/lazy/LazyListScope;Ljava/lang/Object;Ljava/lang/Object;Lkotlin/jvm/functions/Function4;ILjava/lang/Object;)V",
            (lscope, None, None, sticky_body, 3, None), static=True)
        _hk("Landroidx/compose/foundation/lazy/LazyListScope;", "items",
            "(ILkotlin/jvm/functions/Function1;Lkotlin/jvm/functions/Function1;Lkotlin/jvm/functions/Function4;)V",
            (lscope, 2, None, None, item_body), static=False, rcls="Landroidx/compose/foundation/lazy/LazyListScope;")

    lazy_fn = _reg_fn("Lcom/example/LazyBuilder;", "", _lazy_builder)

    def _content_body(args):
        # Inline Row (<  16.11. - 22.11.  >)
        rmp = _hk("Landroidx/compose/foundation/layout/RowKt;", "rowMeasurePolicy", "()Landroidx/compose/ui/layout/MeasurePolicy;", (), static=True)
        _hk("Landroidx/compose/runtime/Composer;", "startReusableNode", "()V", (comp,))
        _hk("Landroidx/compose/runtime/Updater;", "set-impl", "(Landroidx/compose/runtime/Composer;Ljava/lang/Object;Lkotlin/jvm/functions/Function2;)V", (comp, rmp, None), static=True)
        _hk("Landroidx/compose/material3/ButtonKt;", "Button", "(Lkotlin/jvm/functions/Function0;Lkotlin/jvm/functions/Function3;)V",
            (nav_click, _reg_fn("Lcom/example/PrevTxt;", "", lambda _a: _hk(
                "Landroidx/compose/material3/TextKt;", "Text-Nvy7gAk", "(Ljava/lang/String;)V", (JString("<"),), static=True))), static=True)
        _hk("Landroidx/compose/material3/TextKt;", "Text-Nvy7gAk", "(Ljava/lang/String;)V", (JString("16.11. - 22.11."),), static=True)
        _hk("Landroidx/compose/material3/ButtonKt;", "Button", "(Lkotlin/jvm/functions/Function0;Lkotlin/jvm/functions/Function3;)V",
            (nav_click, _reg_fn("Lcom/example/NextTxt;", "", lambda _a: _hk(
                "Landroidx/compose/material3/TextKt;", "Text-Nvy7gAk", "(Ljava/lang/String;)V", (JString(">"),), static=True))), static=True)
        _hk("Landroidx/compose/runtime/Composer;", "endNode", "()V", (comp,))
        _hk("Landroidx/compose/foundation/lazy/LazyDslKt;", "LazyColumn", "(Lkotlin/jvm/functions/Function1;)V", (lazy_fn,), static=True)

    content_fn = _reg_fn("Lcom/example/ScaffoldContent;", "", _content_body)
    _hk("Landroidx/compose/material3/ScaffoldKt;", "Scaffold-TvnljyQ",
        "(Landroidx/compose/ui/Modifier;Lkotlin/jvm/functions/Function2;Lkotlin/jvm/functions/Function2;Lkotlin/jvm/functions/Function3;)V",
        (None, topbar_fn, bottombar_fn, content_fn), static=True)

    # Verify ordering: TopBar y < Content Row y < Sticky Header y < Cards y < Bottom NavigationBar y
    by_role = {n.props.get("role"): n for n in tree.nodes() if n.props.get("role")}
    assert by_role["topbar"].bounds[1] < by_role["sticky_header"].bounds[1] < by_role["card"].bounds[1] < by_role["navbar"].bounds[1]

    cards = [n for n in tree.nodes() if n.props.get("role") == "card"]
    assert len(cards) == 2
    assert cards[0].clickable
    cards[0].on_click(cards[0])
    assert clicked == ["card"]

    cmds = paint_commands(tree)
    assert any(c.get("text") == "Dienstag, 17.11.2026" for c in cmds)
    assert any(c.get("text") == "Kickboxen" for c in cmds)
    assert any(c.get("fill") == "#a8c7fa" for c in cmds)  # dark-mode '<' / '>' pill
