# -*- coding: utf-8 -*-
"""Tests fuer N3 / WS-E2: opt-in W^X-/Perms-Enforcement (native_mmu_service)."""
import pytest

from services.native_emu_service import NativeEmuService
from services.native_mmu_service import NativeMmuService
from core.native import iface


def _eng():
    return NativeEmuService(publish=False).new_engine()


def test_rw_ok_rx_faults_and_disable_restores():
    eng = _eng()
    eng.mem.map(0x40000, 0x1000, iface.PERM_RW)
    eng.mem.map(0x41000, 0x1000, iface.PERM_RX)
    mmu = NativeMmuService(publish=False)
    mmu.enable(eng)
    eng.cpu.m.w32(0x40000, 0x11223344)                 # RW -> ok
    assert eng.cpu.m.r32(0x40000) == 0x11223344
    with pytest.raises(iface.MemoryFault):
        eng.cpu.m.w32(0x41000, 0xdeadbeef)             # RX -> Fault
    assert mmu.summary(eng)["faults"] == 1
    mmu.disable(eng)
    eng.cpu.m.w32(0x41000, 0xcafe)                     # Enforcement aus -> ok
    assert eng.cpu.m.r32(0x41000) == 0xcafe


def test_wb_bytes_write_guarded():
    eng = _eng()
    eng.mem.map(0x44000, 0x1000, iface.PERM_RX)
    NativeMmuService(publish=False).enable(eng)
    with pytest.raises(iface.MemoryFault):
        eng.cpu.m.wb(0x44000, b"\x01\x02\x03")


def test_on_fault_allow_suppresses_raise():
    eng = _eng()
    eng.mem.map(0x41000, 0x1000, iface.PERM_RX)
    seen = []

    def allow(e, addr, kind):
        seen.append((addr, kind))
        return True                                    # erlauben -> kein Raise

    mmu = NativeMmuService(publish=False)
    mmu.enable(eng, on_fault=allow)
    eng.cpu.m.w32(0x41000, 0x99)                       # geloggt, aber zugelassen
    assert eng.cpu.m.r32(0x41000) == 0x99
    assert seen and seen[0][1] == "write"
    assert mmu.summary(eng)["faults"] == 1


def test_mprotect_changes_enforcement():
    eng = _eng()
    eng.mem.map(0x42000, 0x1000, iface.PERM_RX)
    mmu = NativeMmuService(publish=False)
    mmu.enable(eng)
    with pytest.raises(iface.MemoryFault):
        eng.cpu.m.w8(0x42000, 1)
    mmu.mprotect(eng, 0x42000, 0x1000, iface.PERM_RW)  # jetzt beschreibbar
    eng.cpu.m.w8(0x42000, 7)
    assert eng.cpu.m.r8(0x42000) == 7


def test_unmapped_is_permissive():
    eng = _eng()
    NativeMmuService(publish=False).enable(eng)
    eng.cpu.m.w64(0x90000, 0x1234)                     # unmapped -> RWX-Default -> ok
    assert eng.cpu.m.r64(0x90000) == 0x1234


def test_default_off_allows_rx_write():
    eng = _eng()
    eng.mem.map(0x43000, 0x1000, iface.PERM_RX)
    eng.cpu.m.w32(0x43000, 0xabc)                      # ohne enable() -> erlaubt
    assert eng.cpu.m.r32(0x43000) == 0xabc
    assert NativeMmuService(publish=False).summary(eng)["enabled"] is False
