# -*- coding: utf-8 -*-
import os
import time
import sys
from core.java.rn_hermes_bridge import HermesHostClient, TurboModuleRouter
from core.java.surface import snapshot

_HOST = [sys.executable, os.path.join("tools", "rn_hermes_host", "stub_host.py")]


def test_router_builtins_and_override():
    r = TurboModuleRouter()
    pc = r.call("PlatformConstants", "getConstants", [])
    assert pc and pc["reactNativeVersion"]["minor"] == 83
    assert r.call("SourceCode", "getConstants", [])["scriptURL"].startswith("glass://")
    # manipulierbar
    r.override("Appearance", "getColorScheme", "dark")
    assert r.call("Appearance", "getColorScheme", []) == "dark"
    # glaesern: jeder Call protokolliert
    assert any(e["module"] == "PlatformConstants" for e in r.log)


def test_router_unresolved_without_vm():
    r = TurboModuleRouter()
    assert r.call("SomeUnknownModule", "doThing", [1, 2]) is None
    assert r.log[-1]["source"] == "unresolved"


def test_host_commit_builds_surface_and_routes_turbomodules():
    c = HermesHostClient(_HOST, width=380, height=800).start()
    try:
        surface = c.run_until_ready()
        assert surface is not None
        texts = " ".join(snapshot(surface)["text"])
        assert "LibreLinkUp" in texts and "Anmelden" in texts
        # TurboModules wurden ueber den Router geroutet (glaesern)
        mods = {e["module"] for e in c.router.log}
        assert {"PlatformConstants", "SourceCode", "DeviceInfo"} <= mods
    finally:
        c.stop()


def test_event_round_trip():
    c = HermesHostClient(_HOST, width=380, height=800).start()
    try:
        c.run_until_ready()
        # Button (tag 6) ist klickbar -> Press live an den Host zurueck
        nodes = snapshot(c.surface)["nodes"]
        btn = [n for n in nodes if n["clickable"]][0]
        x = btn["bounds"][0] + btn["bounds"][2] / 2
        y = btn["bounds"][1] + btn["bounds"][3] / 2
        c.send_event("press", tag=6)
        c.pump_until(lambda cl: bool(cl.acks) or cl.done)
        assert c.acks and c.acks[-1]["type"] == "press"
    finally:
        c.stop()


# ---- WP2: Idle-Timeout halt-bewusst (Breakpoint-Halt darf UI-Host nicht killen) ----
def test_pump_until_unpaused_times_out_and_terminates():
    c = HermesHostClient(["x"], width=10, height=10)
    c.idle_timeout = 0.05
    c.pump_until(lambda s: False, max_lines=5)   # stiller Host (leere Queue)
    assert c.timed_out is True


def test_pump_until_paused_blocks_terminate_then_resumes():
    import threading
    c = HermesHostClient(["x"], width=10, height=10)
    c.idle_timeout = 0.05
    c.set_paused(True)
    assert c.is_paused() is True
    done = []
    t = threading.Thread(target=lambda: done.append(c.pump_until(lambda s: False, max_lines=100000)),
                         daemon=True)
    t.start()
    time.sleep(0.3)
    # Halt aktiv: Host lebt, kein Timeout-Kill trotz Stille
    assert c.timed_out is False and t.is_alive()
    # Resume: naechster Idle-Zyklus raeumt sauber ab
    c.set_paused(False)
    t.join(timeout=2.0)
    assert (not t.is_alive()) and c.timed_out is True


# ---- WP4: TurboModuleRouter echter Rueckkanal in die DalvikVM (glaesern + manipulierbar) ----
from core.java.dalvik import DalvikVM
from core.java.jni_boundary import JniBoundaryHook, JniBoundaryLog


def _vm_with_identity():
    vm = DalvikVM()
    # static answer(I)I -> return arg0 (echte registrierte Java-Methode)
    vm.register_method("Lrn/NM;", "answer", "(I)I", [("return", 0)], nregs=1, nargs=1, static=True)
    return vm


def test_router_routes_real_vm_through_boundary():
    vm = _vm_with_identity()
    hook = JniBoundaryHook(vm=vm, log=JniBoundaryLog())
    r = TurboModuleRouter(vm=vm, boundary=hook)
    r.map_module("MyModule", "get", "Lrn/NM;", "answer", "(I)I")
    assert r.call("MyModule", "get", [41]) == 41          # ECHT durch die VM ausgefuehrt
    assert r.log[-1]["source"] == "dalvik"
    assert len(hook.log.to_list()) >= 1                    # glaesern protokolliert


def test_router_unmapped_stays_glass_and_none():
    vm = _vm_with_identity()
    hook = JniBoundaryHook(vm=vm, log=JniBoundaryLog())
    r = TurboModuleRouter(vm=vm, boundary=hook)
    assert r.call("Unknown", "x", [1]) is None             # kein Fake-Ergebnis
    assert r.log[-1]["source"] == "dalvik"


def test_router_boundary_override_rewrites_ret():
    vm = _vm_with_identity()
    hook = JniBoundaryHook(vm=vm, log=JniBoundaryLog())
    r = TurboModuleRouter(vm=vm, boundary=hook)
    r.map_module("MyModule", "get", "Lrn/NM;", "answer", "(I)I")
    fn = r._fnptr_for("MyModule", "get")
    hook.add_action_point(fnptr=fn, ret_expr="99")         # Grenz-Override des Rueckgabewerts
    assert r.call("MyModule", "get", [41]) == 99
