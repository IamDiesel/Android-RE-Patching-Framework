# -*- coding: utf-8 -*-
"""Runtime-Breite: Context/Bundle/Resources als echte Objekte, vererbungs-tolerant (generisch)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.java.dalvik import DalvikVM
from core.java import smali as Sm
from core.java.dex_loader import AndroidStubs
from core.java.lifecycle import _StubRuntime
from core.java.objects import JObject, JString

BUNDLE = r'''
.class public Lgen/B;
.super Ljava/lang/Object;
.method public static bundleRoundTrip()Ljava/lang/String;
    .locals 4
    new-instance v0, Landroid/os/Bundle;
    invoke-direct {v0}, Landroid/os/Bundle;-><init>()V
    const-string v1, "key"
    const-string v2, "hello"
    invoke-virtual {v0, v1, v2}, Landroid/os/Bundle;->putString(Ljava/lang/String;Ljava/lang/String;)V
    invoke-virtual {v0, v1}, Landroid/os/Bundle;->getString(Ljava/lang/String;)Ljava/lang/String;
    move-result-object v3
    return-object v3
.end method
'''


def _vm(smali):
    vm = DalvikVM(max_depth=64)
    _c, mds = Sm.parse_class(smali)
    for md in mds:
        vm.registry[(md.cls, md.name, md.sig)] = md
    vm.runtime = _StubRuntime(AndroidStubs(vm=vm))
    return vm


def test_bundle_roundtrip():
    vm = _vm(BUNDLE)
    r = vm.invoke("Lgen/B;", "bundleRoundTrip", "()Ljava/lang/String;", ())
    assert isinstance(r, JString) and r.text == "hello"


def test_inherited_context_methods_resolve_to_objects():
    vm = _vm(BUNDLE)
    st = vm.runtime.stubs
    ctx = st.make_context()
    # getResources auf einer APP-Subklasse (geerbt) -> echtes Resources-Objekt, kein missing/0
    ok, res = st.resolve("Lcom/app/MainApplication;", "getResources", "()Landroid/content/res/Resources;", [ctx], False)
    assert ok and isinstance(res, JObject) and res.cls == "Landroid/content/res/Resources;"
    ok, svc = st.resolve("Lcom/app/MainActivity;", "getSystemService", "(Ljava/lang/String;)Ljava/lang/Object;",
                         [ctx, JString("window")], False)
    assert ok and isinstance(svc, JObject)
    ok, sp = st.resolve("Lcom/app/X;", "getSharedPreferences", "(Ljava/lang/String;I)Landroid/content/SharedPreferences;",
                        [ctx, JString("p"), 0], False)
    assert ok and isinstance(sp, JObject) and "map" in sp.fields


def test_getpackagename_from_profile_inherited():
    vm = _vm(BUNDLE)
    st = vm.runtime.stubs
    from core.java.device_profile import DeviceProfile
    st.set_profile(DeviceProfile(package="com.acme.x"), vm=vm)
    ok, nm = st.resolve("Lcom/acme/App;", "getPackageName", "()Ljava/lang/String;", [st.make_context()], False)
    assert ok and nm.text == "com.acme.x"
