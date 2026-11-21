# -*- coding: utf-8 -*-
"""Tests: Syscall-Policy (reiche Rueckgaben inkl. zero_arg + default_ret) und der MCP-Policy-Store."""
from services.native_emu_service import NativeEmuService
from services.native_ffi_service import NativeFfiService


def _eng():
    return NativeEmuService(publish=False).new_engine()


def test_rich_policy_zero_arg_and_default():
    eng = _eng()
    ffi = NativeFfiService(publish=False)
    ffi.install_syscall_hook(eng, returns={79: {"ret": 0, "zero_arg": 2, "zero_len": 16}},
                             default_ret=7)
    cpu = eng.cpu; cpu.pc = 0x2c000
    cpu.m.wb(0x70000, b"\xff" * 16); cpu.x[2] = 0x70000
    cpu.x[8] = 79                                  # newfstatat -> ret 0 + statbuf genullt
    cpu._svc_hook(cpu)
    assert cpu.x[0] == 0
    assert cpu.m.rb(0x70000, 16) == b"\x00" * 16
    cpu.x[8] = 222                                 # nicht gelistet -> default_ret 7
    cpu._svc_hook(cpu)
    assert cpu.x[0] == 7


def test_int_spec_and_str_keys():
    eng = _eng()
    ffi = NativeFfiService(publish=False)
    ffi.install_syscall_hook(eng, returns={"174": 10234}, default_ret=0)   # str-Key
    cpu = eng.cpu; cpu.pc = 0x2c000
    cpu.x[8] = 174
    cpu._svc_hook(cpu)
    assert cpu.x[0] == 10234


def test_mcp_policy_store_roundtrip():
    from mcp_server import tools
    try:
        r = tools.native_syscall_policy_set(None, preset="genuine")
        assert r["ok"] and "79" in r["policy"]["by_nr"]
        g = tools.native_syscall_policy_get(None)
        assert g["policy"]["by_nr"]["79"]["ret"] == 0
        r2 = tools.native_syscall_policy_set(None, policy={"default_ret": 5, "by_nr": {"117": 0}})
        assert r2["policy"]["default_ret"] == 5
        assert tools.native_syscall_policy_get(None)["policy"]["by_nr"]["117"] == 0
    finally:
        tools.native_syscall_policy_set(None, preset="clear")
