# -*- coding: utf-8 -*-
"""Regressionstests für Coroutine-Job/LifecycleScope-Cancellation, KeyStore-Persistenz & Collection/Bundle-Stubs."""
from __future__ import annotations

import base64
import os
import shutil
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.java.dalvik import DalvikVM  # noqa: E402
from core.java.dex_loader import AndroidStubs  # noqa: E402
from core.java.glass_runner import GlassAppRunner, InstanceState, apply_virtual_clock_spec  # noqa: E402
from core.java.lifecycle import LifecycleRunner, _StubRuntime  # noqa: E402
from core.java.objects import JArray, JObject, JString  # noqa: E402
from core.java.seam_config import SeamConfig  # noqa: E402
from core.java.smali import parse_class  # noqa: E402
from core.java.sqlite_prefs_bridge import persist_java_appdata_to_dir, sync_java_appdata_from_dir  # noqa: E402
from core.java.view_tree import ViewNode, install_view_stubs  # noqa: E402


def _make_env():
    vm = DalvikVM()
    vm.lenient = True
    stubs = AndroidStubs(vm=vm)
    stubs.seed_framework_statics(vm)
    tree = install_view_stubs(stubs)
    vm.runtime = _StubRuntime(stubs)
    runner = LifecycleRunner(vm, with_stubs=False, stubs=stubs)
    return vm, stubs, tree, runner


def _call_static(vm, cls, name, sig, args=()):
    for hk in list(vm._method_hooks):
        handled, ret = hk(cls, cls, name, sig, list(args), True, vm)
        if handled:
            return ret
    ok, ret = vm.runtime.resolve_invoke(cls, name, sig, list(args), True)
    assert ok, f"Missing static hook/stub: {cls}->{name}{sig}"
    return ret


def test_sharedflow_job_and_lifecycle_scope_cancel_removes_zombie_collector():
    vm, stubs, tree, runner = _make_env()

    _, mds = parse_class(
        ".class public final Lcom/example/SubBlock;\n"
        ".super Ljava/lang/Object;\n"
        ".field public flow:Lkotlinx/coroutines/flow/SharedFlow;\n"
        ".field public coll:Lkotlinx/coroutines/flow/FlowCollector;\n"
        ".method public final invoke(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;\n"
        "    .locals 2\n"
        "    iget-object v0, p0, Lcom/example/SubBlock;->flow:Lkotlinx/coroutines/flow/SharedFlow;\n"
        "    iget-object v1, p0, Lcom/example/SubBlock;->coll:Lkotlinx/coroutines/flow/FlowCollector;\n"
        "    invoke-interface {v0, v1, p2}, Lkotlinx/coroutines/flow/SharedFlow;->collect(Lkotlinx/coroutines/flow/FlowCollector;Lkotlin/coroutines/Continuation;)Ljava/lang/Object;\n"
        "    move-result-object v0\n"
        "    return-object v0\n"
        ".end method\n"
    )
    for md in mds:
        vm.register(md)

    emitted = []
    flow = _call_static(
        vm,
        "Lkotlinx/coroutines/flow/SharedFlowKt;",
        "MutableSharedFlow$default",
        "(IILkotlinx/coroutines/channels/BufferOverflow;ILjava/lang/Object;)Lkotlinx/coroutines/flow/MutableSharedFlow;",
        (0, 0, None, 7, None),
    )

    def _hook(rcls, cls, name, sig, args, static, _vm):
        if cls == "Lcom/example/Collector;" and name == "emit":
            emitted.append(args[0].fields["tag"])
            return True, JObject("Lkotlin/Unit;")
        return False, None

    vm._method_hooks.append(_hook)

    # A) Explicit Job.cancel() removes collector
    sc_manual = JObject("Landroidx/lifecycle/LifecycleCoroutineScopeImpl;")
    c_manual = JObject("Lcom/example/Collector;", {"tag": "manual"})
    blk_manual = JObject("Lcom/example/SubBlock;", {"flow": flow, "coll": c_manual})
    job_manual = _call_static(
        vm,
        "Lkotlinx/coroutines/BuildersKt;",
        "launch$default",
        "(Lkotlinx/coroutines/CoroutineScope;Lkotlin/coroutines/CoroutineContext;Lkotlinx/coroutines/CoroutineStart;Lkotlin/jvm/functions/Function2;ILjava/lang/Object;)Lkotlinx/coroutines/Job;",
        (sc_manual, None, None, blk_manual, 3, None),
    )
    assert isinstance(job_manual, JObject)
    assert vm.invoke_virtual(job_manual, "isActive", "()Z", (job_manual,)) == 1

    cont = JObject("Lkotlin/coroutines/Continuation;")
    vm.invoke_virtual(flow, "tryEmit", "(Ljava/lang/Object;)Z", (flow, JObject("Lkotlin/Unit;"), cont))
    assert emitted == ["manual"]

    vm.invoke_virtual(job_manual, "cancel", "(Ljava/util/concurrent/CancellationException;)V", (job_manual, None))
    assert vm.invoke_virtual(job_manual, "isCancelled", "()Z", (job_manual,)) == 1
    assert vm.invoke_virtual(job_manual, "isActive", "()Z", (job_manual,)) == 0

    emitted.clear()
    vm.invoke_virtual(flow, "tryEmit", "(Ljava/lang/Object;)Z", (flow, JObject("Lkotlin/Unit;"), cont))
    assert emitted == []

    # B) Activity.finish() cancels LifecycleOwner & LifecycleRegistry scopes, but NEVER ProcessLifecycleOwner!
    act = JObject("Lcom/example/ActLogin;", {"lifecycleRegistry": JObject("Landroidx/lifecycle/LifecycleRegistry;")})
    stubs._current_activity = act
    stubs._current_activity_obj = act
    tree.set_root(ViewNode("LinearLayout", vid="root_login"))

    sc_act = _call_static(
        vm, "Landroidx/lifecycle/LifecycleOwnerKt;", "getLifecycleScope",
        "(Landroidx/lifecycle/LifecycleOwner;)Landroidx/lifecycle/LifecycleCoroutineScope;", (act,),
    )
    sc_reg = _call_static(
        vm, "Landroidx/lifecycle/LifecycleKt;", "getCoroutineScope",
        "(Landroidx/lifecycle/Lifecycle;)Landroidx/lifecycle/LifecycleCoroutineScope;", (act.fields["lifecycleRegistry"],),
    )
    proc = _call_static(
        vm, "Landroidx/lifecycle/ProcessLifecycleOwner;", "get", "()Landroidx/lifecycle/LifecycleOwner;", (),
    )
    sc_proc = _call_static(
        vm, "Landroidx/lifecycle/LifecycleOwnerKt;", "getLifecycleScope",
        "(Landroidx/lifecycle/LifecycleOwner;)Landroidx/lifecycle/LifecycleCoroutineScope;", (proc,),
    )

    for sc, tag in ((sc_act, "act"), (sc_reg, "reg"), (sc_proc, "proc")):
        blk = JObject("Lcom/example/SubBlock;", {"flow": flow, "coll": JObject("Lcom/example/Collector;", {"tag": tag})})
        _call_static(
            vm,
            "Lkotlinx/coroutines/BuildersKt;", "launch$default",
            "(Lkotlinx/coroutines/CoroutineScope;Lkotlin/coroutines/CoroutineContext;Lkotlinx/coroutines/CoroutineStart;Lkotlin/jvm/functions/Function2;ILjava/lang/Object;)Lkotlinx/coroutines/Job;",
            (sc, None, None, blk, 3, None),
        )

    emitted.clear()
    vm.invoke_virtual(flow, "tryEmit", "(Ljava/lang/Object;)Z", (flow, JObject("Lkotlin/Unit;"), cont))
    assert set(emitted) == {"act", "reg", "proc"}

    # Finish act and pump navigate_to_pending -> act & reg scopes cancel, proc survives
    stubs.resolve("Landroid/app/Activity;", "finish", "()V", [act], False)
    runner.navigate_to_pending()
    emitted.clear()
    vm.invoke_virtual(flow, "tryEmit", "(Ljava/lang/Object;)Z", (flow, JObject("Lkotlin/Unit;"), cont))
    assert emitted == ["proc"]


def test_keystore_alias_persists_and_recovers_from_encrypted_prefs(tmp_path):
    vm1, stubs1, _, _ = _make_env()

    def R(st, cls, name, sig, args=(), static=False):
        ok, val = st.resolve(cls, name, sig, list(args), static)
        assert ok, f"Missing stub: {cls}->{name}{sig}"
        return val

    # 1) Create vg_key and encrypt sample string into SharedPreferences
    ks1 = R(stubs1, "Ljava/security/KeyStore;", "getInstance", "(Ljava/lang/String;)Ljava/security/KeyStore;", (JString("AndroidKeyStore"),), True)
    R(stubs1, "Ljava/security/KeyStore;", "load", "(Ljava/security/KeyStore$LoadStoreParameter;)V", (ks1, None))
    b = JObject("Landroid/security/keystore/KeyGenParameterSpec$Builder;")
    R(stubs1, "Landroid/security/keystore/KeyGenParameterSpec$Builder;", "<init>", "(Ljava/lang/String;I)V", (b, JString("vg_key"), 3))
    spec = R(stubs1, "Landroid/security/keystore/KeyGenParameterSpec$Builder;", "build", "()Landroid/security/keystore/KeyGenParameterSpec;", (b,))
    kg = R(stubs1, "Ljavax/crypto/KeyGenerator;", "getInstance", "(Ljava/lang/String;Ljava/lang/String;)Ljavax/crypto/KeyGenerator;", (JString("AES"), JString("AndroidKeyStore")), True)
    R(stubs1, "Ljavax/crypto/KeyGenerator;", "init", "(Ljava/security/spec/AlgorithmParameterSpec;)V", (kg, spec))
    sk1 = R(stubs1, "Ljavax/crypto/KeyGenerator;", "generateKey", "()Ljavax/crypto/SecretKey;", (kg,))

    c_enc = R(stubs1, "Ljavax/crypto/Cipher;", "getInstance", "(Ljava/lang/String;)Ljavax/crypto/Cipher;", (JString("AES/GCM/NoPadding"),), True)
    R(stubs1, "Ljavax/crypto/Cipher;", "init", "(ILjava/security/Key;)V", (c_enc, 1, sk1))
    iv = R(stubs1, "Ljavax/crypto/Cipher;", "getIV", "()[B", (c_enc,))
    ct = R(stubs1, "Ljavax/crypto/Cipher;", "doFinal", "([B)[B", (c_enc, JArray("[B", list(b"user@example.com"))))
    b64_val = base64.b64encode(bytes(iv.data) + bytes(ct.data)).decode("ascii")

    sp1 = stubs1.resolve("Landroid/content/Context;", "getSharedPreferences", "(Ljava/lang/String;I)Landroid/content/SharedPreferences;", [None, JString("VirtuaGym.prefs"), 0], False)[1]
    ed1 = R(stubs1, "Landroid/content/SharedPreferences;", "edit", "()Landroid/content/SharedPreferences$Editor;", (sp1,))
    R(stubs1, "Landroid/content/SharedPreferences$Editor;", "putString", "(Ljava/lang/String;Ljava/lang/String;)Landroid/content/SharedPreferences$Editor;", (ed1, JString("profile.email"), JString(b64_val)))
    R(stubs1, "Landroid/content/SharedPreferences$Editor;", "apply", "()V", (ed1,))

    appdata = str(tmp_path / "appdata")
    assert persist_java_appdata_to_dir(stubs1, appdata) >= 2
    assert os.path.isfile(os.path.join(appdata, "keystore.json"))

    # 2) Round-trip via keystore.json
    _, stubs2, _, _ = _make_env()
    assert sync_java_appdata_from_dir(stubs2, appdata) >= 2
    ks2 = R(stubs2, "Ljava/security/KeyStore;", "getInstance", "(Ljava/lang/String;)Ljava/security/KeyStore;", (JString("AndroidKeyStore"),), True)
    assert R(stubs2, "Ljava/security/KeyStore;", "containsAlias", "(Ljava/lang/String;)Z", (ks2, JString("vg_key"))) == 1
    assert R(stubs2, "Ljava/security/KeyStore;", "getKey", "(Ljava/lang/String;[C)Ljava/security/Key;", (ks2, JString("vg_key"), None)) is not None

    # 3) Legacy recovery when keystore.json does not exist yet
    os.remove(os.path.join(appdata, "keystore.json"))
    _, stubs3, _, _ = _make_env()
    sync_java_appdata_from_dir(stubs3, appdata)
    ks3 = R(stubs3, "Ljava/security/KeyStore;", "getInstance", "(Ljava/lang/String;)Ljava/security/KeyStore;", (JString("AndroidKeyStore"),), True)
    assert R(stubs3, "Ljava/security/KeyStore;", "containsAlias", "(Ljava/lang/String;)Z", (ks3, JString("unrelated_key"))) == 0
    assert R(stubs3, "Ljava/security/KeyStore;", "containsAlias", "(Ljava/lang/String;)Z", (ks3, JString("vg_key"))) == 1
    sk3 = R(stubs3, "Ljava/security/KeyStore;", "getKey", "(Ljava/lang/String;[C)Ljava/security/Key;", (ks3, JString("vg_key"), None))
    assert sk3 is not None

    raw = base64.b64decode(stubs3._prefs_store["VirtuaGym.prefs"]["profile.email"].text)
    gcm = JObject("Ljavax/crypto/spec/GCMParameterSpec;")
    R(stubs3, "Ljavax/crypto/spec/GCMParameterSpec;", "<init>", "(I[B)V", (gcm, 128, JArray("[B", list(raw[:12]))))
    c_dec = R(stubs3, "Ljavax/crypto/Cipher;", "getInstance", "(Ljava/lang/String;)Ljavax/crypto/Cipher;", (JString("AES/GCM/NoPadding"),), True)
    R(stubs3, "Ljavax/crypto/Cipher;", "init", "(ILjava/security/Key;Ljava/security/spec/AlgorithmParameterSpec;)V", (c_dec, 2, sk3, gcm))
    dec = R(stubs3, "Ljavax/crypto/Cipher;", "doFinal", "([B)[B", (c_dec, JArray("[B", list(raw[12:]))))
    assert bytes(dec.data) == b"user@example.com"


def test_bundle_typed_parcelable_enummap_and_soft_reference():
    _, stubs, _, _ = _make_env()

    def R(cls, name, sig, args=(), static=False):
        ok, val = stubs.resolve(cls, name, sig, list(args), static)
        assert ok, f"Missing stub: {cls}->{name}{sig}"
        return val

    # Bundle typed getParcelable(String, Class)
    b = JObject("Landroid/os/Bundle;")
    R("Landroid/os/Bundle;", "<init>", "()V", (b,))
    payload = JObject("Lcom/example/ParcelableItem;", {"x": 7})
    R("Landroid/os/Bundle;", "putParcelable", "(Ljava/lang/String;Landroid/os/Parcelable;)V", (b, JString("item"), payload))
    got = R("Landroid/os/Bundle;", "getParcelable", "(Ljava/lang/String;Ljava/lang/Class;)Ljava/lang/Object;",
            (b, JString("item"), JObject("Ljava/lang/Class;", {"name": "com.example.ParcelableItem"})))
    assert got is payload

    # EnumMap
    em = JObject("Ljava/util/EnumMap;")
    k = JObject("Lcom/example/MyEnum;", {"__ename": "FIRST", "__ordinal": 0})
    R("Ljava/util/EnumMap;", "<init>", "(Ljava/lang/Class;)V", (em, JObject("Ljava/lang/Class;", {"name": "com.example.MyEnum"})))
    R("Ljava/util/EnumMap;", "put", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", (em, k, JString("val1")))
    assert R("Ljava/util/EnumMap;", "size", "()I", (em,)) == 1
    assert R("Ljava/util/EnumMap;", "get", "(Ljava/lang/Object;)Ljava/lang/Object;", (em, k)).text == "val1"

    # SoftReference / Reference.get
    sr = JObject("Ljava/lang/ref/SoftReference;")
    target = JObject("Ljava/lang/Object;")
    R("Ljava/lang/ref/SoftReference;", "<init>", "(Ljava/lang/Object;)V", (sr, target))
    assert R("Ljava/lang/ref/Reference;", "get", "()Ljava/lang/Object;", (sr,)) is target
    R("Ljava/lang/ref/Reference;", "clear", "()V", (sr,))
    assert R("Ljava/lang/ref/Reference;", "get", "()Ljava/lang/Object;", (sr,)) is None


_VG_APP_ROOT = os.path.join(_ROOT, "source", "digifit.virtuagym.client.android", "base_unpacked_apkeditor")
_VG_APPDATA_SRC = os.path.join(_ROOT, "data", "vfs_appdata", "digifit.virtuagym.client.android")


@pytest.mark.skipif(
    not (os.path.isdir(_VG_APP_ROOT) and os.path.isdir(_VG_APPDATA_SRC)),
    reason="Entpackte Virtuagym-Sourcen / AppData im Workspace nicht vorhanden",
)
def test_virtuagym_no_onboarding_loop_in_isolated_copy(tmp_path):
    tmp_appdata = tmp_path / "appdata"
    isolated_dir = tmp_appdata / "digifit.virtuagym.client.android"
    shutil.copytree(_VG_APPDATA_SRC, isolated_dir)

    # Falls live AppData ausgeloggt ist, synthetische vg_key-Session im isolierten Copy seeden
    _, seed_stubs, _, _ = _make_env()
    try:
        sync_java_appdata_from_dir(seed_stubs, str(isolated_dir))
        vg_prefs = seed_stubs._prefs_store.get("VirtuaGym.prefs", {})
        raw_auth = vg_prefs.get("profile.authtype")
        auth_val = raw_auth.text if isinstance(raw_auth, JString) else str(raw_auth or "")
        if not auth_val or auth_val == "authtype.none" or not (vg_prefs.get("profile.email") and vg_prefs.get("profile.password")):
            def _R(st_obj, cls, name, sig, args=(), static=False):
                ok, val = st_obj.resolve(cls, name, sig, list(args), static)
                assert ok, f"Missing stub: {cls}->{name}{sig}"
                return val

            ks = _R(seed_stubs, "Ljava/security/KeyStore;", "getInstance", "(Ljava/lang/String;)Ljava/security/KeyStore;", (JString("AndroidKeyStore"),), True)
            _R(seed_stubs, "Ljava/security/KeyStore;", "load", "(Ljava/security/KeyStore$LoadStoreParameter;)V", (ks, None))
            b = JObject("Landroid/security/keystore/KeyGenParameterSpec$Builder;")
            _R(seed_stubs, "Landroid/security/keystore/KeyGenParameterSpec$Builder;", "<init>", "(Ljava/lang/String;I)V", (b, JString("vg_key"), 3))
            spec = _R(seed_stubs, "Landroid/security/keystore/KeyGenParameterSpec$Builder;", "build", "()Landroid/security/keystore/KeyGenParameterSpec;", (b,))
            kg = _R(seed_stubs, "Ljavax/crypto/KeyGenerator;", "getInstance", "(Ljava/lang/String;Ljava/lang/String;)Ljavax/crypto/KeyGenerator;", (JString("AES"), JString("AndroidKeyStore")), True)
            _R(seed_stubs, "Ljavax/crypto/KeyGenerator;", "init", "(Ljava/security/spec/AlgorithmParameterSpec;)V", (kg, spec))
            sk = _R(seed_stubs, "Ljavax/crypto/KeyGenerator;", "generateKey", "()Ljavax/crypto/SecretKey;", (kg,))
            sp = seed_stubs.resolve("Landroid/content/Context;", "getSharedPreferences", "(Ljava/lang/String;I)Landroid/content/SharedPreferences;", [None, JString("VirtuaGym.prefs"), 0], False)[1]
            ed = _R(seed_stubs, "Landroid/content/SharedPreferences;", "edit", "()Landroid/content/SharedPreferences$Editor;", (sp,))
            for k_name, pt in (("profile.email", b"test@example.com"), ("profile.password", b"secret123")):
                c_enc = _R(seed_stubs, "Ljavax/crypto/Cipher;", "getInstance", "(Ljava/lang/String;)Ljavax/crypto/Cipher;", (JString("AES/GCM/NoPadding"),), True)
                _R(seed_stubs, "Ljavax/crypto/Cipher;", "init", "(ILjava/security/Key;)V", (c_enc, 1, sk))
                iv = _R(seed_stubs, "Ljavax/crypto/Cipher;", "getIV", "()[B", (c_enc,))
                ct = _R(seed_stubs, "Ljavax/crypto/Cipher;", "doFinal", "([B)[B", (c_enc, JArray("[B", list(pt))))
                b64_val = base64.b64encode(bytes(iv.data) + bytes(ct.data)).decode("ascii")
                _R(seed_stubs, "Landroid/content/SharedPreferences$Editor;", "putString", "(Ljava/lang/String;Ljava/lang/String;)Landroid/content/SharedPreferences$Editor;", (ed, JString(k_name), JString(b64_val)))
            _R(seed_stubs, "Landroid/content/SharedPreferences$Editor;", "putString", "(Ljava/lang/String;Ljava/lang/String;)Landroid/content/SharedPreferences$Editor;", (ed, JString("profile.authtype"), JString("authtype.basicauth")))
            _R(seed_stubs, "Landroid/content/SharedPreferences$Editor;", "putString", "(Ljava/lang/String;Ljava/lang/String;)Landroid/content/SharedPreferences$Editor;", (ed, JString("profile.firstname"), JString("Demo")))
            _R(seed_stubs, "Landroid/content/SharedPreferences$Editor;", "putInt", "(Ljava/lang/String;I)Landroid/content/SharedPreferences$Editor;", (ed, JString("profile.userid"), 987654))
            _R(seed_stubs, "Landroid/content/SharedPreferences$Editor;", "putInt", "(Ljava/lang/String;I)Landroid/content/SharedPreferences$Editor;", (ed, JString("intake_selected_level"), 1))
            _R(seed_stubs, "Landroid/content/SharedPreferences$Editor;", "apply", "()V", (ed,))
            persist_java_appdata_to_dir(seed_stubs, str(isolated_dir))
    finally:
        for c in list(getattr(seed_stubs, "_sqlite_conns", {}).values()):
            try:
                c.close()
            except Exception:
                pass

    inst = InstanceState(
        instance_id="test_vg_fork",
        package="digifit.virtuagym.client.android",
        app_root=_VG_APP_ROOT,
        zone="original",
        apply_patches=False,
        seam_profile="self_cache",
        net_mode="mock",
        appdata_root=str(tmp_appdata),
        selfcache_root=os.path.join(_ROOT, "data", "vfs_selfcache"),
    )
    runner = GlassAppRunner(
        app_root=inst.app_root,
        zone="original",
        instance_state=inst,
        seam_config=SeamConfig.from_profile("self_cache"),
        base_dir=_ROOT,
    )
    ctx = runner.build(native_fusion=True, net_mode="mock")
    vm = ctx["vm"]
    lc = runner.runner
    stubs = ctx["stubs"]
    vt = ctx["tree"]
    stubs2 = None
    try:
        # 1) Cold launch recovers vg_key & stays on HomeActivity
        lc.run_app(studio_root=inst.app_root)
        vt.layout()
        assert getattr(stubs._current_activity, "cls", None) == "Ldigifit/android/virtuagym/presentation/screen/home/overview/view/HomeActivity;"

        # 2) Full login -> onboarding -> notification -> BecomePro Back / direct club flow -> HomeActivity must NOT loop back
        runner2 = GlassAppRunner(
            app_root=inst.app_root,
            zone="original",
            instance_state=inst,
            seam_config=SeamConfig.from_profile("self_cache"),
            base_dir=_ROOT,
        )
        ctx2 = runner2.build(native_fusion=True, net_mode="mock")
        vm2, lc2, stubs2, vt2 = ctx2["vm"], runner2.runner, ctx2["stubs"], ctx2["tree"]
        for key in ("profile.email", "profile.password", "profile.authtype"):
            stubs2._prefs_store["VirtuaGym.prefs"].pop(key, None)

        lc2.run_app(studio_root=inst.app_root)
        assert getattr(stubs2._current_activity, "cls", None) == "Ldigifit/android/virtuagym/presentation/screen/access/virtuagym/view/AccessActivity;"
        acc_pres = stubs2._current_activity.fields.get("a")
        sync_java_appdata_from_dir(stubs2, str(isolated_dir))
        vm2.invoke(
            "Ldigifit/android/virtuagym/presentation/screen/access/virtuagym/presenter/AccessPresenter;",
            "i",
            "(Ldigifit/android/virtuagym/presentation/screen/access/virtuagym/presenter/AccessPresenter;)V",
            (acc_pres,),
        )
        lc2._drain_runnables()
        lc2.navigate_to_pending()
        assert getattr(stubs2._current_activity, "cls", None) == "Ldigifit/android/virtuagym/presentation/screen/onboarding/view/FitnessOnboardingActivity;"

        def _tap(label):
            apply_virtual_clock_spec(stubs2, {"shift_seconds": 3})
            vt2.layout()
            target = next(
                (nd for nd in vt2.root.walk()
                 if getattr(nd, "clickable", False) and nd.bounds[2] > 0 and label.lower() in str(getattr(nd, "text", "")).lower()),
                None,
            )
            assert target is not None, f"Button {label!r} nicht gefunden"
            cb = getattr(target, "on_click", None)
            view_obj = getattr(target, "_jobj", None) or (target.props.get("_jobj") if hasattr(target, "props") else None)
            if callable(cb):
                cb()
            elif cb is not None:
                vm2.invoke_virtual(cb, "onClick", "(Landroid/view/View;)V", (cb, view_obj))
            lc2._drain_runnables()
            lc2.navigate_to_pending()

        seen_frags = []
        onboarding_taps = 0
        while (
            "FitnessOnboardingActivity" in (getattr(stubs2._current_activity, "cls", "") or "")
            and onboarding_taps < 10
        ):
            vt2.layout()
            frag_clss = tuple(sorted({
                getattr(f, "cls", "")
                for f in getattr(stubs2, "_fragments_by_id", {}).values()
                if getattr(f, "cls", None)
            }))
            if frag_clss:
                seen_frags.append(frag_clss)
            if any("NameIntakeFragment" in fc for fc in frag_clss) and vt2.root is not None:
                for et_nd in vt2.root.walk():
                    if et_nd.cls == "EditText" and len(str(et_nd.text or "").strip()) < 2:
                        et_nd.text = "Demo"
                        if callable(getattr(et_nd, "on_change", None)):
                            et_nd.on_change("Demo")
            _tap("Next")  # bis zu 8 Intake-Schritte -> LoadingIntake -> NotificationPermission
            onboarding_taps += 1
        assert len(seen_frags) == len(set(seen_frags))
        assert onboarding_taps >= 2
        assert getattr(stubs2._current_activity, "cls", None) == "Ldigifit/android/virtuagym/presentation/notification/FitnessNotificationPermissionActivity;"
        _tap("Maybe later")  # -> BecomePro (Freemium) oder direkt HomeActivity (Club-Mitglied)
        if getattr(stubs2._current_activity, "cls", None) == "Ldigifit/android/virtuagym/presentation/screen/pro/BecomeProActivity;":
            lc2.handle_back()
            vt2.layout()
        assert getattr(stubs2._current_activity, "cls", None) == "Ldigifit/android/virtuagym/presentation/screen/home/overview/view/HomeActivity;"
    finally:
        for st_obj in (stubs, stubs2):
            if st_obj is None:
                continue
            for c in list(getattr(st_obj, "_sqlite_conns", {}).values()):
                try:
                    c.close()
                except Exception:
                    pass


def test_sharedprefs_signed_32_64_normalization_and_sqlite_bridge_healing(tmp_path):
    import json

    vm, stubs, _tree, _runner = _make_env()
    _, sp = stubs.resolve(
        "Landroid/content/Context;", "getSharedPreferences",
        "(Ljava/lang/String;I)Landroid/content/SharedPreferences;",
        (None, JString("VirtuaGym.prefs"), 0), False,
    )
    _, ed = stubs.resolve(
        "Landroid/content/SharedPreferences;", "edit",
        "()Landroid/content/SharedPreferences$Editor;", (sp,), False,
    )
    # 64-bit unsigned -1 (18446744073709551615) and 32-bit unsigned -1 (0xFFFFFFFF) normalize to -1
    stubs.resolve("Landroid/content/SharedPreferences$Editor;", "putInt", "(Ljava/lang/String;I)Landroid/content/SharedPreferences$Editor;", (ed, JString("app_theme"), (1 << 64) - 1), False)
    stubs.resolve("Landroid/content/SharedPreferences$Editor;", "putInt", "(Ljava/lang/String;I)Landroid/content/SharedPreferences$Editor;", (ed, JString("accent"), 0xFFFFFFFF), False)
    stubs.resolve("Landroid/content/SharedPreferences$Editor;", "putLong", "(Ljava/lang/String;J)Landroid/content/SharedPreferences$Editor;", (ed, JString("neg_long"), (1 << 64) - 1), False)
    stubs.resolve("Landroid/content/SharedPreferences$Editor;", "putLong", "(Ljava/lang/String;J)Landroid/content/SharedPreferences$Editor;", (ed, JString("lastsync"), 1795282269212), False)
    stubs.resolve("Landroid/content/SharedPreferences$Editor;", "apply", "()V", (ed,), False)

    assert stubs.resolve("Landroid/content/SharedPreferences;", "getInt", "(Ljava/lang/String;I)I", (sp, JString("app_theme"), 0), False)[1] == -1
    assert stubs.resolve("Landroid/content/SharedPreferences;", "getInt", "(Ljava/lang/String;I)I", (sp, JString("accent"), 0), False)[1] == -1
    assert stubs.resolve("Landroid/content/SharedPreferences;", "getInt", "(Ljava/lang/String;I)I", (sp, JString("missing"), (1 << 64) - 1), False)[1] == -1
    assert stubs.resolve("Landroid/content/SharedPreferences;", "getLong", "(Ljava/lang/String;J)J", (sp, JString("neg_long"), 0), False)[1] == -1
    assert stubs.resolve("Landroid/content/SharedPreferences;", "getLong", "(Ljava/lang/String;J)J", (sp, JString("lastsync"), 0), False)[1] == 1795282269212

    # Disk persistence heals legacy 18446744073709551615 written earlier
    sp_dir = tmp_path / "shared_prefs"
    sp_dir.mkdir(parents=True)
    (sp_dir / "Legacy.prefs.json").write_text(json.dumps({
        "__name": "Legacy.prefs",
        "items": {
            "primary_club.gradient_start": {"__t": "i", "v": 18446744073709551615},
            "lastsync": {"__t": "i", "v": 1795282269212},
        },
    }), encoding="utf-8")
    sync_java_appdata_from_dir(stubs, str(tmp_path))
    assert stubs._prefs_store["Legacy.prefs"]["primary_club.gradient_start"] == -1
    assert stubs._prefs_store["Legacy.prefs"]["lastsync"] == 1795282269212
    persist_java_appdata_to_dir(stubs, str(tmp_path))
    healed = json.loads((sp_dir / "Legacy.prefs.json").read_text(encoding="utf-8"))
    assert healed["items"]["primary_club.gradient_start"]["v"] == -1
    assert healed["items"]["lastsync"]["v"] == 1795282269212


def test_retrofit_form_url_encoded_field_and_okhttp_form_body():
    from urllib.parse import parse_qs
    from core.java.net_recorder import NetResponse, install_net_stubs

    vm, stubs, _tree, _runner = _make_env()
    seen_reqs = []

    def _responder(req):
        seen_reqs.append(req)
        return NetResponse(200, {"Content-Type": "application/json"}, b'{"access_token":"tok123"}', "OK")

    install_net_stubs(stubs, responder=_responder)

    def _call_hook(cls, mname, msig, margs):
        for h in vm._method_hooks:
            ok, val = h(cls, cls, mname, msig, margs, False, vm)
            if ok:
                return val
        raise AssertionError(f"Hook nicht getroffen: {cls}->{mname}{msig}")

    # 1) OkHttp FormBody$Builder + Request$Builder.post
    fb_cls = "Lokhttp3/FormBody$Builder;"
    b = JObject(fb_cls, {})
    _call_hook(fb_cls, "<init>", "()V", [b])
    _call_hook(fb_cls, "add", "(Ljava/lang/String;Ljava/lang/String;)Lokhttp3/FormBody$Builder;", [b, JString("grant_type"), JString("password")])
    _call_hook(fb_cls, "addEncoded", "(Ljava/lang/String;Ljava/lang/String;)Lokhttp3/FormBody$Builder;", [b, JString("username"), JString("daniel%40test.de")])
    form_body = _call_hook(fb_cls, "build", "()Lokhttp3/FormBody;", [b])
    assert isinstance(form_body, JObject) and form_body.fields.get("$content_type") == "application/x-www-form-urlencoded"
    assert b"grant_type=password" in form_body.fields.get("$body", b"")

    rb = JObject("Lokhttp3/Request$Builder;", {})
    _call_hook(rb.cls, "<init>", "()V", [rb])
    _call_hook(rb.cls, "url", "(Ljava/lang/String;)Lokhttp3/Request$Builder;", [rb, JString("https://iam.example/token")])
    _call_hook(rb.cls, "post", "(Lokhttp3/RequestBody;)Lokhttp3/Request$Builder;", [rb, form_body])
    req_obj = _call_hook(rb.cls, "build", "()Lokhttp3/Request;", [rb])
    cli = JObject("Lokhttp3/OkHttpClient;", {})
    call_obj = _call_hook(cli.cls, "newCall", "(Lokhttp3/Request;)Lokhttp3/Call;", [cli, req_obj])
    _call_hook(call_obj.cls, "execute", "()Lokhttp3/Response;", [call_obj])
    assert len(seen_reqs) == 1
    assert b"grant_type=password" in seen_reqs[0].body
    assert seen_reqs[0].headers.get("Content-Type") == "application/x-www-form-urlencoded"

    # 2) Retrofit @FormUrlEncoded @Field + @FieldMap
    svc_smali = """
.class public interface abstract Ltest/OAuthApi;
.super Ljava/lang/Object;

.method public abstract getToken(Ljava/lang/String;Ljava/lang/String;Ljava/util/Map;)Ljava/lang/Object;
    .annotation runtime Lretrofit2/http/FormUrlEncoded;
    .end annotation
    .annotation runtime Lretrofit2/http/POST;
        value = "auth/token"
    .end annotation
    .param p1    # Ljava/lang/String;
        .annotation runtime Lretrofit2/http/Field;
            value = "username"
        .end annotation
    .end param
    .param p2    # Ljava/lang/String;
        .annotation runtime Lretrofit2/http/Field;
            value = "grant_type"
        .end annotation
    .end param
    .param p3    # Ljava/util/Map;
        .annotation runtime Lretrofit2/http/FieldMap;
        .end annotation
    .end param
.end method
"""

    class _Prov:
        def get_source(self, desc):
            return svc_smali if desc == "Ltest/OAuthApi;" else ""

    vm.class_provider = _Prov()
    rfb = JObject("Lretrofit2/Retrofit$Builder;", {})
    _call_hook(rfb.cls, "<init>", "()V", [rfb])
    _call_hook(rfb.cls, "baseUrl", "(Ljava/lang/String;)Lretrofit2/Retrofit$Builder;", [rfb, JString("https://iam.example/")])
    rf = _call_hook(rfb.cls, "build", "()Lretrofit2/Retrofit;", [rfb])
    svc = _call_hook(rf.cls, "create", "(Ljava/lang/Class;)Ljava/lang/Object;", [rf, JObject("Ljava/lang/Class;", {"name": "test.OAuthApi"})])
    extra_map = JObject("Ljava/util/HashMap;", {"map": {JString("client_id"): JString("vg_android")}})
    _call_hook(svc.cls, "getToken", "(Ljava/lang/String;Ljava/lang/String;Ljava/util/Map;)Ljava/lang/Object;", [svc, JString("daniel@vg.de"), JString("password"), extra_map])
    assert len(seen_reqs) == 2
    parsed = parse_qs(seen_reqs[1].body.decode("utf-8"))
    assert parsed["username"] == ["daniel@vg.de"]
    assert parsed["grant_type"] == ["password"]
    assert parsed["client_id"] == ["vg_android"]
    assert seen_reqs[1].headers.get("Content-Type") == "application/x-www-form-urlencoded"


def test_appcompat_toolbar_rendering_and_vector_clear_color_filter(tmp_path):
    res_dir = tmp_path / "res"
    (res_dir / "drawable").mkdir(parents=True)
    (res_dir / "drawable" / "ic_cal.xml").write_text(
        '<vector xmlns:android="http://schemas.android.com/apk/res/android" '
        'android:width="24dp" android:height="24dp" android:viewportWidth="24" android:viewportHeight="24">'
        '<path android:fillColor="#212121" android:pathData="M3,3h18v18h-18z"/></vector>',
        encoding="utf-8",
    )
    from core.java.drawable_loader import DrawableResolver
    from core.java.view_tree import paint_commands

    vm = DalvikVM()
    vm.lenient = True
    stubs = AndroidStubs(vm=vm)
    stubs.seed_framework_statics(vm)
    resv = DrawableResolver(str(tmp_path))
    stubs._drawmap = {0x7F080001: "ic_cal"}
    stubs._drawable_resolver = resv
    tree = install_view_stubs(stubs)
    tree._drawable_resolver = resv
    vm.runtime = _StubRuntime(stubs)

    def _vh(rcls, cls, name, sig, logical, static=False):
        for h in vm._method_hooks:
            ok, val = h(rcls, cls, name, sig, logical, static, vm)
            if ok:
                return ok, val
        return False, None

    # 1) AppCompatTextView in BrandAwareToolbar must paint "Hi Daniel"
    tb = ViewNode(cls="BrandAwareToolbar", vid="toolbar")
    tb.add(ViewNode(cls="AppCompatImageButton"))
    tb.add(ViewNode(cls="AppCompatTextView", text="Hi Daniel"))
    root = ViewNode(cls="LinearLayout", props={"orientation": 1})
    root.add(tb)

    # 2) VectorDrawable clearColorFilter -> mutate -> setColorFilter -> setImageDrawable must NOT turn into #000000 rect
    iv_node = ViewNode(cls="ImageView", vid="bottom_navigation_item_icon", props={"xml_w": 24, "xml_h": 24})
    root.add(iv_node)
    tree.set_root(root)

    _, drv = _vh("Landroidx/core/content/ContextCompat;", "Landroidx/core/content/ContextCompat;", "getDrawable", "(Landroid/content/Context;I)Landroid/graphics/drawable/Drawable;", [None, 0x7F080001], static=True)
    assert isinstance(drv, JObject) and drv.fields.get("_drawable", {}).get("kind") == "vector"
    _vh(drv.cls, drv.cls, "clearColorFilter", "()V", [drv])
    _, drv = _vh("Landroidx/core/graphics/drawable/DrawableCompat;", "Landroidx/core/graphics/drawable/DrawableCompat;", "wrap", "(Landroid/graphics/drawable/Drawable;)Landroid/graphics/drawable/Drawable;", [drv], static=True)
    _, mut = _vh(drv.cls, drv.cls, "mutate", "()Landroid/graphics/drawable/Drawable;", [drv])
    _vh("Landroidx/core/graphics/drawable/DrawableCompat;", "Landroidx/core/graphics/drawable/DrawableCompat;", "setTint", "(Landroid/graphics/drawable/Drawable;I)V", [mut, 0xFF747474], static=True)
    iv_jobj = JObject("Landroid/widget/ImageView;", {"$view": iv_node})
    stubs.resolve("Landroid/widget/ImageView;", "setImageDrawable", "(Landroid/graphics/drawable/Drawable;)V", [iv_jobj, mut], False)

    assert iv_node.props.get("drawable", {}).get("kind") == "vector"
    assert iv_node.props.get("drawable", {}).get("tint") == "#747474"
    cmds = paint_commands(tree)
    assert any(c.get("op") == "text" and c.get("text") == "Hi Daniel" for c in cmds)
    assert not any(c.get("op") == "rect" and c.get("fill") == "#000000" for c in cmds)


def test_clear_active_appdata_controller_and_mcp_glass_control_parity(tmp_path):
    from mcp_server import registry as reg
    from mcp_server import tools as mcp_tools
    from ui.controllers.debugger_controller import DebuggerController

    app_dir = tmp_path / "data" / "vfs_appdata" / "com.demo.reset"
    (app_dir / "shared_prefs").mkdir(parents=True)
    (app_dir / "shared_prefs" / "Auth.prefs.json").write_text('{"__name":"Auth","items":{}}', encoding="utf-8")
    (app_dir / "keystore.json").write_text('{"aliases":["k1"]}', encoding="utf-8")

    # 1) Controller clears disk + in-memory prefs & keystore safely
    ctrl = DebuggerController(app=None)
    ctrl._base_dir = lambda: str(tmp_path)
    ctrl._resolve_pkg = lambda: "com.demo.reset"
    ctrl.instance_state = None
    ctrl._stubs = AndroidStubs(vm=DalvikVM())
    ctrl._stubs._prefs_store["Auth.prefs"] = {"token": "abc"}
    ctrl._stubs._keystore_aliases = {"k1": JObject("Ljavax/crypto/SecretKey;", {})}
    res_c = ctrl.clear_active_appdata(restart=False)
    assert res_c["ok"] is True and res_c["removed_files"] == 2
    assert not app_dir.exists()
    assert ctrl._stubs._prefs_store == {}
    assert ctrl._stubs._keystore_aliases == {}

    # 2) MCP native_glass_control(cmd='reset_appdata') parity + registry documentation
    (app_dir / "shared_prefs").mkdir(parents=True)
    (app_dir / "shared_prefs" / "Auth.prefs.json").write_text('{"__name":"Auth","items":{}}', encoding="utf-8")

    class _Cfg:
        config = {"BASE_DIR": str(tmp_path)}

    class _Ctx:
        cfg = _Cfg()

    out = mcp_tools.native_glass_control(
        _Ctx(),
        cmd="reset_appdata",
        params_json='{"package":"com.demo.reset","instance_id":"main"}',
        target="headless",
    )
    assert out["ok"] is True
    assert out["headless"]["ok"] is True
    assert out["headless"]["removed_files"] == 1
    assert not app_dir.exists()
    spec = reg.get("native.glass_control")
    assert spec is not None and "reset_appdata" in spec.summary


def test_explicit_persist_session_opt_in_controller_exit_and_mcp_parity(tmp_path):
    from mcp_server import registry as reg
    from mcp_server import tools as mcp_tools
    from ui.controllers.debugger_controller import DebuggerController

    saved_cfgs = []

    class _FakeCfg:
        def __init__(self):
            self.config = {"DEBUGGER_PERSIST_SESSION": False, "BASE_DIR": str(tmp_path)}
            self.paths = {"BASE_DIR": str(tmp_path)}

        def save(self):
            saved_cfgs.append(bool(self.config.get("DEBUGGER_PERSIST_SESSION")))

    class _FakeApp:
        cfg = _FakeCfg()

    app_dir = tmp_path / "data" / "vfs_appdata" / "com.demo.sess"
    ctrl = DebuggerController(app=_FakeApp())
    ctrl._base_dir = lambda: str(tmp_path)
    ctrl._resolve_pkg = lambda: "com.demo.sess"
    assert ctrl.persist_session is False
    assert ctrl.seam_config.persist_appdata is False

    # 1) When persist_session == False: persist_active_appdata writes 0 files, on_studio_exit cleans stale appdata
    ctrl._stubs = AndroidStubs(vm=DalvikVM())
    ctrl._stubs._prefs_store["Auth.prefs"] = {"session": "tmp"}
    assert ctrl.persist_active_appdata() == 0
    assert not app_dir.exists()

    (app_dir / "shared_prefs").mkdir(parents=True)
    (app_dir / "shared_prefs" / "Stale.prefs.json").write_text('{"__name":"Stale","items":{}}', encoding="utf-8")
    ctrl.on_studio_exit()
    assert not app_dir.exists()
    assert ctrl._stubs._prefs_store == {}

    # 2) Explicit opt-in saves live session immediately + persists config + keeps files across exit
    ctrl._stubs._prefs_store["Auth.prefs"] = {"session": "saved"}
    res_on = ctrl.set_persist_session(True)
    assert res_on == {"ok": True, "persist_session": True}
    assert saved_cfgs[-1] is True
    assert ctrl.seam_config.persist_appdata is True
    assert (app_dir / "shared_prefs" / "Auth.prefs.json").is_file()

    ctrl._stubs._prefs_store["Auth.prefs"]["extra"] = "kept_on_exit"
    ctrl.on_studio_exit()
    assert (app_dir / "shared_prefs" / "Auth.prefs.json").is_file()
    assert "kept_on_exit" in (app_dir / "shared_prefs" / "Auth.prefs.json").read_text(encoding="utf-8")

    # 3) MCP native_glass_run default (persist_session=False) vs explicit opt-in (persist_session=True)
    smali_app = tmp_path / "sess_app"
    (smali_app / "smali" / "com" / "demo" / "mcp_sess").mkdir(parents=True)
    (smali_app / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.demo.mcp_sess">'
        '<application><activity android:name=".MainAct"><intent-filter>'
        '<action android:name="android.intent.action.MAIN"/>'
        '<category android:name="android.intent.category.LAUNCHER"/>'
        '</intent-filter></activity></application></manifest>',
        encoding="utf-8",
    )
    (smali_app / "smali" / "com" / "demo" / "mcp_sess" / "MainAct.smali").write_text(
        '.class public Lcom/demo/mcp_sess/MainAct;\n'
        '.super Landroid/app/Activity;\n'
        '.method public onCreate(Landroid/os/Bundle;)V\n'
        '    .registers 5\n'
        '    const-string v0, "P"\n'
        '    const/4 v1, 0x0\n'
        '    invoke-virtual {p0, v0, v1}, Landroid/content/Context;->getSharedPreferences(Ljava/lang/String;I)Landroid/content/SharedPreferences;\n'
        '    move-result-object v2\n'
        '    invoke-interface {v2}, Landroid/content/SharedPreferences;->edit()Landroid/content/SharedPreferences$Editor;\n'
        '    move-result-object v2\n'
        '    const-string v3, "k"\n'
        '    const-string v4, "v1"\n'
        '    invoke-interface {v2, v3, v4}, Landroid/content/SharedPreferences$Editor;->putString(Ljava/lang/String;Ljava/lang/String;)Landroid/content/SharedPreferences$Editor;\n'
        '    move-result-object v2\n'
        '    invoke-interface {v2}, Landroid/content/SharedPreferences$Editor;->apply()V\n'
        '    return-void\n'
        '.end method\n',
        encoding="utf-8",
    )

    class _Cfg:
        config = {"BASE_DIR": str(tmp_path)}

    class _Ctx:
        cfg = _Cfg()

    mcp_dir = tmp_path / "data" / "vfs_appdata" / "com.demo.mcp_sess"
    r_def = mcp_tools.native_glass_run(_Ctx(), studio_root=str(smali_app), zone="original", entry="Lcom/demo/mcp_sess/MainAct;", max_steps=10000)
    assert r_def["ok"] is True and r_def["persist_session"] is False
    assert not mcp_dir.exists()

    r_opt = mcp_tools.native_glass_run(_Ctx(), studio_root=str(smali_app), zone="original", entry="Lcom/demo/mcp_sess/MainAct;", max_steps=10000, persist_session=True)
    assert r_opt["ok"] is True and r_opt["persist_session"] is True
    assert (mcp_dir / "shared_prefs" / "P.json").is_file()

    r_ctl = mcp_tools.native_glass_control(_Ctx(), cmd="set_persist_session", params_json='{"enabled":false}', target="headless")
    assert r_ctl["ok"] is True and r_ctl["headless"] == {"ok": True, "persist_session": False}
    assert "set_persist_session" in reg.get("native.glass_control").summary


def test_core_statics_kotlin_unit_file_collections_and_sysprop():
    vm, stubs, _tree, _runner = _make_env()

    assert vm.statics["Ljava/lang/Boolean;"]["TRUE"].fields["value"] == 1
    assert vm.statics["Ljava/lang/Boolean;"]["FALSE"].fields["value"] == 0
    assert isinstance(vm.statics["Lkotlin/Unit;"]["INSTANCE"], JObject)
    assert vm.statics["Lkotlin/Unit;"]["a"] is vm.statics["Lkotlin/Unit;"]["INSTANCE"]
    assert vm.statics["Ljava/io/File;"]["separator"].text == "/"
    assert vm.statics["Ljava/io/File;"]["pathSeparator"].text == ":"
    assert vm.statics["Ljava/io/File;"]["separatorChar"] == ord("/")
    assert vm.statics["Ljava/io/File;"]["pathSeparatorChar"] == ord(":")
    assert vm.statics["Ljava/util/Collections;"]["EMPTY_LIST"].fields["__items"] == []
    assert vm.statics["Ljava/util/Collections;"]["EMPTY_SET"].fields["__items"] == []
    assert vm.statics["Ljava/util/Collections;"]["EMPTY_MAP"].fields["map"] == {}

    ok_1, sep = vm.runtime.resolve_invoke("Ljava/lang/System;", "getProperty", "(Ljava/lang/String;)Ljava/lang/String;", [JString("file.separator")], True)
    assert ok_1 and sep.text == "/"
    ok_2, fb = vm.runtime.resolve_invoke("Ljava/lang/System;", "getProperty", "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;", [JString("custom.missing"), JString("fallback")], True)
    assert ok_2 and fb.text == "fallback"
    ok_hit, tmpd = vm.runtime.resolve_invoke("Ljava/lang/System;", "getProperty", "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;", [JString("java.io.tmpdir"), JString("ignored")], True)
    assert ok_hit and tmpd.text == "/data/local/tmp"


def test_tint_typed_array_and_set_text_zero_guard():
    vm, stubs, tree, _runner = _make_env()

    tta = JObject("Landroidx/appcompat/widget/TintTypedArray;", {"_attrs": {1: 0x01010001}})
    for hk in list(vm._method_hooks):
        handled, ret = hk("Landroidx/appcompat/widget/TintTypedArray;", "Landroidx/appcompat/widget/TintTypedArray;", "getDrawable", "(I)Landroid/graphics/drawable/Drawable;", [tta, 0], False, vm)
        if handled:
            assert ret is None
            break
    else:
        pytest.fail("TintTypedArray.getDrawable hook did not intercept")

    for hk in list(vm._method_hooks):
        handled, ret = hk("Landroidx/appcompat/widget/TintTypedArray;", "Landroidx/appcompat/widget/TintTypedArray;", "getDrawableIfKnown", "(I)Landroid/graphics/drawable/Drawable;", [tta, 99], False, vm)
        if handled:
            assert ret is None
            break

    tv_node = ViewNode("TextView", vid="title", text="initial")
    tv_obj = JObject("Landroid/widget/TextView;", {"$view": tv_node})
    tree.set_root(tv_node)
    ok_st0, _ = vm.runtime.resolve_invoke("Landroid/widget/TextView;", "setText", "(I)V", [tv_obj, 0], False)
    assert ok_st0
    assert tv_node.text == ""
    ok_st1, _ = vm.runtime.resolve_invoke("Landroid/widget/TextView;", "setText", "(I)V", [tv_obj, 0x7F130001], False)
    assert ok_st1
    assert tv_node.text == "res#2131951617"


def test_xml_bool_int_resources_and_stringset_prefs_persistence(tmp_path):
    from core.java.layout_inflater import build_bool_map, build_integer_map

    app_dir = tmp_path / "app"
    res_val = app_dir / "res" / "values"
    res_val.mkdir(parents=True)
    (res_val / "public.xml").write_text(
        '<resources>'
        '<public type="bool" name="feat_a" id="0x7f050001"/>'
        '<public type="integer" name="max_cols" id="0x7f0b0002"/>'
        '</resources>',
        encoding="utf-8",
    )
    (res_val / "bools.xml").write_text('<resources><bool name="feat_a">true</bool></resources>', encoding="utf-8")
    (res_val / "integers.xml").write_text('<resources><integer name="max_cols">3</integer></resources>', encoding="utf-8")

    bmap, bnames = build_bool_map(str(app_dir))
    imap, inames = build_integer_map(str(app_dir))
    assert bmap[0x7F050001] == 1 and bnames["feat_a"] is True
    assert imap[0x7F0B0002] == 3 and inames["max_cols"] == 3

    vm = DalvikVM()
    vm.lenient = True
    stubs = AndroidStubs(vm=vm)
    stubs.seed_framework_statics(vm)
    install_view_stubs(stubs, app_root=str(app_dir))
    vm.runtime = _StubRuntime(stubs)

    res_obj = JObject("Landroid/content/res/Resources;", {})
    def _res_hook(name, sig, args):
        for hk in list(vm._method_hooks):
            handled, ret = hk("Landroid/content/res/Resources;", "Landroid/content/res/Resources;", name, sig, [res_obj] + list(args), False, vm)
            if handled:
                return ret
        pytest.fail(f"Hook did not handle Resources->{name}{sig}")

    assert _res_hook("getBoolean", "(I)Z", [0x7F050001]) == 1
    assert _res_hook("getInteger", "(I)I", [0x7F0B0002]) == 3

    ctx = stubs.make_context()
    ok_sp, sp = vm.runtime.resolve_invoke("Landroid/content/Context;", "getSharedPreferences", "(Ljava/lang/String;I)Landroid/content/SharedPreferences;", [ctx, JString("SetPrefs"), 0], False)
    assert ok_sp
    ok_ed, ed = vm.runtime.resolve_invoke("Landroid/content/SharedPreferences;", "edit", "()Landroid/content/SharedPreferences$Editor;", [sp], False)
    assert ok_ed
    ss_in = JObject("Ljava/util/LinkedHashSet;", {"__items": [JString("alpha"), JString("beta")], "$items": [JString("alpha"), JString("beta")]})
    vm.runtime.resolve_invoke("Landroid/content/SharedPreferences$Editor;", "putStringSet", "(Ljava/lang/String;Ljava/util/Set;)Landroid/content/SharedPreferences$Editor;", [ed, JString("tags"), ss_in], False)
    vm.runtime.resolve_invoke("Landroid/content/SharedPreferences$Editor;", "apply", "()V", [ed], False)

    ok_get, ss_out = vm.runtime.resolve_invoke("Landroid/content/SharedPreferences;", "getStringSet", "(Ljava/lang/String;Ljava/util/Set;)Ljava/util/Set;", [sp, JString("tags"), None], False)
    assert ok_get and [x.text for x in ss_out.fields["__items"]] == ["alpha", "beta"]

    ok_all, all_map = vm.runtime.resolve_invoke("Landroid/content/SharedPreferences;", "getAll", "()Ljava/util/Map;", [sp], False)
    assert ok_all and "tags" in all_map.fields["map"]

    save_dir = tmp_path / "persisted"
    persist_java_appdata_to_dir(stubs, str(save_dir))
    _vm2, stubs2, _t2, _r2 = _make_env()
    sync_java_appdata_from_dir(stubs2, str(save_dir))
    restored = stubs2._prefs_store["SetPrefs"]["tags"]
    assert isinstance(restored, JObject) and [x.text for x in restored.fields["__items"]] == ["alpha", "beta"]


def test_scoped_fragment_manager_arguments_bundle_and_launch_exc_reset():
    from core.java.debug import JavaDebugger

    vm, stubs, tree, _runner = _make_env()
    act = JObject("Landroidx/appcompat/app/AppCompatActivity;", {})
    stubs._current_activity = act
    root_n = ViewNode("FrameLayout", vid="container")
    tree.set_root(root_n)

    def _call_hook(obj, name, sig, args):
        for hk in list(vm._method_hooks):
            handled, ret = hk(obj.cls, obj.cls, name, sig, [obj] + list(args), False, vm)
            if handled:
                return ret
        pytest.fail(f"Hook did not handle {obj.cls}->{name}{sig}")

    fm_act = _call_hook(act, "getSupportFragmentManager", "()Landroidx/fragment/app/FragmentManager;", [])
    frag_parent = JObject("Landroidx/fragment/app/Fragment;", {"$is_fragment": True})
    frag_child = JObject("Landroidx/fragment/app/Fragment;", {"$is_fragment": True})

    b = stubs.make_bundle()
    vm.runtime.resolve_invoke("Landroid/os/Bundle;", "putLong", "(Ljava/lang/String;J)V", [b, JString("uid"), 424242], False)
    arr = JArray("[J", [10, 20, 30])
    vm.runtime.resolve_invoke("Landroid/os/Bundle;", "putLongArray", "(Ljava/lang/String;[J)V", [b, JString("ids"), arr], False)
    _call_hook(frag_parent, "setArguments", "(Landroid/os/Bundle;)V", [b])
    got_b = _call_hook(frag_parent, "getArguments", "()Landroid/os/Bundle;", [])
    _, uid_v = vm.runtime.resolve_invoke("Landroid/os/Bundle;", "getLong", "(Ljava/lang/String;J)J", [got_b, JString("uid"), 0], False)
    _, ids_v = vm.runtime.resolve_invoke("Landroid/os/Bundle;", "getLongArray", "(Ljava/lang/String;)[J", [got_b, JString("ids")], False)
    assert uid_v == 424242
    assert list(ids_v.data) == [10, 20, 30]

    tx1 = _call_hook(fm_act, "beginTransaction", "()Landroidx/fragment/app/FragmentTransaction;", [])
    _call_hook(tx1, "replace", "(ILandroidx/fragment/app/Fragment;)Landroidx/fragment/app/FragmentTransaction;", [1, frag_parent])
    _call_hook(tx1, "commitNow", "()V", [])

    fm_child = _call_hook(frag_parent, "getChildFragmentManager", "()Landroidx/fragment/app/FragmentManager;", [])
    assert fm_child is not fm_act
    tx2 = _call_hook(fm_child, "beginTransaction", "()Landroidx/fragment/app/FragmentTransaction;", [])
    _call_hook(tx2, "add", "(Landroidx/fragment/app/Fragment;Ljava/lang/String;)Landroidx/fragment/app/FragmentTransaction;", [frag_child, JString("child")])
    _call_hook(tx2, "commitNow", "()V", [])

    act_frags = _call_hook(fm_act, "getFragments", "()Ljava/util/List;", [])
    child_frags = _call_hook(fm_child, "getFragments", "()Ljava/util/List;", [])
    assert act_frags.fields["__items"] == [frag_parent]
    assert child_frags.fields["__items"] == [frag_child]
    assert _call_hook(frag_parent, "getParentFragmentManager", "()Landroidx/fragment/app/FragmentManager;", []) is fm_act

    jdb = JavaDebugger(vm)
    jdb.last_exception = {"cls": "Ljava/lang/NullPointerException;", "handled": False}
    st = jdb.run_interaction(lambda: None)
    assert st["exception"] is None


def test_viewpager1_switch_and_fixed_bottom_bar_hit_test():
    from core.java.ui_mcp_bridge import extract_interactive_elements
    from core.java.surface import SceneGraphSurface, tap

    vm, stubs, tree, _runner = _make_env()

    _, mds_frag0 = parse_class(
        ".class public final Lcom/example/Tab0Frag;\n"
        ".super Landroidx/fragment/app/Fragment;\n"
        ".method public constructor <init>()V\n"
        "    .locals 1\n"
        "    invoke-direct {p0}, Landroidx/fragment/app/Fragment;-><init>()V\n"
        "    return-void\n"
        ".end method\n"
        ".method public onCreateView(Landroid/view/LayoutInflater;Landroid/view/ViewGroup;Landroid/os/Bundle;)Landroid/view/View;\n"
        "    .locals 2\n"
        "    new-instance v0, Landroid/widget/TextView;\n"
        "    invoke-virtual {p0}, Landroidx/fragment/app/Fragment;->getContext()Landroid/content/Context;\n"
        "    move-result-object v1\n"
        "    invoke-direct {v0, v1}, Landroid/widget/TextView;-><init>(Landroid/content/Context;)V\n"
        "    const-string v1, \"PageZero\"\n"
        "    invoke-virtual {v0, v1}, Landroid/widget/TextView;->setText(Ljava/lang/CharSequence;)V\n"
        "    return-object v0\n"
        ".end method\n"
    )
    _, mds_frag1 = parse_class(
        ".class public final Lcom/example/Tab1Frag;\n"
        ".super Landroidx/fragment/app/Fragment;\n"
        ".method public constructor <init>()V\n"
        "    .locals 1\n"
        "    invoke-direct {p0}, Landroidx/fragment/app/Fragment;-><init>()V\n"
        "    return-void\n"
        ".end method\n"
        ".method public onCreateView(Landroid/view/LayoutInflater;Landroid/view/ViewGroup;Landroid/os/Bundle;)Landroid/view/View;\n"
        "    .locals 2\n"
        "    new-instance v0, Landroid/widget/TextView;\n"
        "    invoke-virtual {p0}, Landroidx/fragment/app/Fragment;->getContext()Landroid/content/Context;\n"
        "    move-result-object v1\n"
        "    invoke-direct {v0, v1}, Landroid/widget/TextView;-><init>(Landroid/content/Context;)V\n"
        "    const-string v1, \"PageOne\"\n"
        "    invoke-virtual {v0, v1}, Landroid/widget/TextView;->setText(Ljava/lang/CharSequence;)V\n"
        "    return-object v0\n"
        ".end method\n"
    )
    _, mds_adp = parse_class(
        ".class public final Lcom/example/MyVpAdapter;\n"
        ".super Landroidx/fragment/app/FragmentStatePagerAdapter;\n"
        ".method public constructor <init>()V\n"
        "    .locals 0\n"
        "    return-void\n"
        ".end method\n"
        ".method public getCount()I\n"
        "    .locals 1\n"
        "    const/4 v0, 0x2\n"
        "    return v0\n"
        ".end method\n"
        ".method public getItem(I)Landroidx/fragment/app/Fragment;\n"
        "    .locals 1\n"
        "    if-nez p1, :p1\n"
        "    new-instance v0, Lcom/example/Tab0Frag;\n"
        "    invoke-direct {v0}, Lcom/example/Tab0Frag;-><init>()V\n"
        "    return-object v0\n"
        "    :p1\n"
        "    new-instance v0, Lcom/example/Tab1Frag;\n"
        "    invoke-direct {v0}, Lcom/example/Tab1Frag;-><init>()V\n"
        "    return-object v0\n"
        ".end method\n"
    )
    for mds in (mds_frag0, mds_frag1, mds_adp):
        for md in mds:
            vm.registry[(md.cls, md.name, md.sig)] = md

    def _vhook_call(cls, name, sig, args):
        for hk in list(vm._method_hooks):
            handled, ret = hk(cls, cls, name, sig, list(args), False, vm)
            if handled:
                return ret
        pytest.fail(f"Hook did not handle {cls}->{name}{sig}")

    coord = ViewNode("CoordinatorLayout", vid="root")
    vp_node = ViewNode("ViewPager", vid="pager")
    vp_obj = JObject("Landroidx/viewpager/widget/ViewPager;", {"$view": vp_node})
    vp_node._jobj = vp_obj
    tall_child = ViewNode("TextView", text="TallContent", props={"height_dp": 1200})
    bnav = ViewNode("BottomNavigationBar", vid="bottom_navigation_holder", props={"gravity": "bottom", "height_dp": 60})
    clicked = []
    tab1_btn = ViewNode("Button", vid="tab1_btn", text="Tab1", clickable=True, props={"height_dp": 48})
    tab1_btn.on_click = lambda _v=None: (
        clicked.append(True),
        _vhook_call("Landroidx/viewpager/widget/ViewPager;", "setCurrentItem", "(IZ)V", (vp_obj, 1, 0)),
    )
    bnav.add(tab1_btn)
    coord.add(vp_node)
    coord.add(tall_child)
    coord.add(bnav)
    tree.width = 380
    tree.height = 760
    tree.set_root(coord)

    assert bnav.props.get("$fixed_y") is True
    assert bnav.bounds[1] >= 680

    elems = extract_interactive_elements(tree, scroll_y=400, view_h=760)
    b_elem = next(e for e in elems if e["id"] == "tab1_btn")
    assert b_elem["viewport_bounds"] == b_elem["bounds"]

    hit_x, hit_y = b_elem["tap_xy"]
    assert tree.fixed_clickable_at(hit_x, hit_y) is tab1_btn

    adp = JObject("Lcom/example/MyVpAdapter;", {})
    _vhook_call("Landroidx/viewpager/widget/ViewPager;", "setAdapter", "(Landroidx/viewpager/widget/PagerAdapter;)V", (vp_obj, adp))
    assert any(n.text == "PageZero" for n in vp_node.walk())

    surf = SceneGraphSurface(tree, vm=vm)
    assert tap(surf, hit_x, hit_y) == {"handled": True}
    assert clicked == [True]
    assert any(n.text == "PageOne" for n in vp_node.walk())
    assert 0 in vp_obj.fields["__vp1_frags"] and 1 in vp_obj.fields["__vp1_frags"]


def test_net_recorder_unwraps_jobject_strings():
    from core.java.net_recorder import NetLog, install_net_stubs

    vm = DalvikVM()
    vm.lenient = True
    stubs = AndroidStubs(vm=vm)
    stubs.seed_framework_statics(vm)
    vm.runtime = _StubRuntime(stubs)
    log = NetLog()
    install_net_stubs(stubs, log)

    str_obj = JObject("Ljava/lang/String;", {"text": "https://example.test/api"})
    url_obj = JObject("Ljava/net/URL;", {})
    ok_u, _ = vm.runtime.resolve_invoke("Ljava/net/URL;", "<init>", "(Ljava/lang/String;)V", [url_obj, str_obj], False)
    assert ok_u
    assert url_obj.fields.get("$url") == "https://example.test/api"


_VG_UNPACKED = os.path.join(_ROOT, "source", "digifit.virtuagym.client.android", "base_unpacked_apktool")


@pytest.mark.skipif(not os.path.isdir(_VG_UNPACKED), reason="Entpackte Virtuagym-App nicht vorhanden")
def test_virtuagym_home_start_screen_toolbar_layout_and_all_four_tabs():
    from core.java.net_recorder import NetResponse
    from core.java.surface import SceneGraphSurface, tap

    gr = GlassAppRunner(_VG_UNPACKED, zone="original")
    gr.build(net_responder=lambda _r: NetResponse(200, {}, b"{}", "OK"), native_fusion=False)
    vp = gr.stubs._prefs_store.setdefault("VirtuaGym.prefs", {})
    vp["profile.authtype"] = JString("authtype.vg_oauth")
    vp["profile.userid"] = 12345
    gr.run(
        entry="Ldigifit/android/virtuagym/presentation/screen/home/overview/view/HomeActivity;",
        max_steps=800_000,
        native_fusion=False,
    )

    assert getattr(gr.vm, "last_exc", None) is None
    assert "HomeActivity" in getattr(getattr(gr.stubs, "_current_activity", None), "cls", "")

    tb = next((n for n in gr.tree.root.walk() if "Toolbar" in (n.cls or "")), None)
    assert tb is not None
    phantom_imgs = [c for c in tb.children if "Image" in (c.cls or "") and not c.props.get("drawable") and not c.props.get("img")]
    assert phantom_imgs == []

    bnav = gr.tree.root.find("bottom_navigation_holder")
    assert bnav is not None and bnav.props.get("$fixed_y") is True and bnav.bounds[1] >= 660

    tabs = [n for n in gr.tree.root.walk() if n.vid == "bottom_navigation_container"]
    assert len(tabs) == 4
    assert all(t.clickable and t.on_click is not None for t in tabs)

    img_menu = gr.tree.root.find("images_menu")
    ct_title = gr.tree.root.find("content_title")
    if img_menu is not None and ct_title is not None:
        assert abs(img_menu.bounds[1] - ct_title.bounds[1]) <= 24
        for ic in img_menu.children:
            assert ic.bounds[2] <= 36 and ic.bounds[3] <= 36

    surf = SceneGraphSurface(gr.tree, vm=gr.vm, activity=getattr(gr.stubs, "_current_activity", None))
    vp_main = gr.tree.root.find("view_pager")
    assert vp_main is not None

    unhandled_excs = []
    orig_on_exc = getattr(gr.vm, "on_exception", None)

    def _track_exc(exc_obj, cls="", name="", pc=0, handled=False):
        if not handled:
            unhandled_excs.append((getattr(exc_obj, "cls", None), cls, name, pc))
        if orig_on_exc:
            return orig_on_exc(exc_obj, cls, name, pc, handled)

    gr.vm.on_exception = _track_exc
    expected_frags = {
        1: "HomeExploreFragment",
        2: "HomeCommunityFragment",
        3: "HomeMeFragment",
        0: "HomeMyPlanFragment",
    }
    try:
        for idx in (1, 2, 3, 0):
            t = tabs[idx]
            cx = t.bounds[0] + t.bounds[2] // 2
            cy = t.bounds[1] + t.bounds[3] // 2
            assert tap(surf, cx, cy) == {"handled": True}
            frag = vp_main._jobj.fields.get("__vp1_frags", {}).get(idx)
            assert frag is not None and expected_frags[idx] in (frag.cls or "")
            assert not any("res#0" in (n.text or "") for n in gr.tree.root.walk() if n.text)
    finally:
        gr.vm.on_exception = orig_on_exc

    assert unhandled_excs == []
