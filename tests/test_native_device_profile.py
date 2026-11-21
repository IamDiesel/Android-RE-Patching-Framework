# -*- coding: utf-8 -*-
"""Generisches, konfigurierbares Device-Profil: faithful android.*-Konstanten (zielagnostisch)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.java.dalvik import DalvikVM
from core.java.dex_loader import AndroidStubs
from core.java.device_profile import DeviceProfile
from core.java.objects import JString


def _stubs(**over):
    vm = DalvikVM()
    st = AndroidStubs(vm=vm, profile=DeviceProfile(**over))
    st.seed_framework_statics(vm)
    return vm, st


def test_build_statics_seeded_from_profile():
    vm, st = _stubs(sdk_int=33, model="TestPhone")
    assert vm.statics["Landroid/os/Build$VERSION;"]["SDK_INT"] == 33
    assert vm.statics["Landroid/os/Build;"]["MODEL"].text == "TestPhone"


def test_package_from_profile_not_hardcoded():
    vm, st = _stubs(package="com.acme.app")
    ok, ret = st.resolve("Landroid/content/Context;", "getPackageName", "()Ljava/lang/String;", [None], False)
    assert ok and isinstance(ret, JString) and ret.text == "com.acme.app"


def test_packed_position_group_faithful():
    vm, st = _stubs()
    # getPackedPositionGroup(0L) == 0 (faithful Bit-Extraktion)
    ok, ret = st.resolve("Landroid/widget/ExpandableListView;", "getPackedPositionGroup", "(J)I", [0], True)
    assert ok and ret == 0
    # Gruppe 5 kodiert in den oberen 31 Bit
    packed = (5 << 32)
    ok, ret = st.resolve("Landroid/widget/ExpandableListView;", "getPackedPositionGroup", "(J)I", [packed], True)
    assert ret == 5


def test_thread_priority_from_profile():
    vm, st = _stubs(thread_priority=10)
    ok, ret = st.resolve("Landroid/os/Process;", "getThreadPriority", "(I)I", [0], True)
    assert ok and ret == 10


def test_set_profile_reseeds():
    vm, st = _stubs(sdk_int=30)
    st.set_profile(DeviceProfile(sdk_int=35), vm=vm)
    assert vm.statics["Landroid/os/Build$VERSION;"]["SDK_INT"] == 35
