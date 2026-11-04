"""Headless-Tests fuer core.native (M1-Skelett). Laeuft mit pytest ODER direkt:
    python tests/test_native_skeleton.py
Keine GUI-/Framework-Abhaengigkeiten.
"""
import struct
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.native import iface, loader
from core.native.emu import Emulator
from core.native.iface import (CoverageReport, FakeEnvironment, TargetPlugin,
                               UnknownInstructionError, EV_UNKNOWN_INSTR, EV_STEP)
from core.native.snapshot import Snapshot


# ---- Test-Double: einfacher byte-adressierter Speicher ---------------------
class DictMemory(iface.MemoryBackend):
    def __init__(self):
        self._m = {}
        self._ranges = []  # (addr, size, perms)

    def map(self, addr, size, perms=iface.PERM_RW):
        self._ranges.append((addr, size, perms))

    def protect(self, addr, size, perms):
        self._ranges.append((addr, size, perms))

    def perms(self, addr):
        p = 0
        for a, s, pr in self._ranges:
            if a <= addr < a + s:
                p = pr
        return p

    def read(self, addr, size):
        return bytes(self._m.get(addr + i, 0) for i in range(size))

    def write(self, addr, data):
        for i, b in enumerate(data):
            self._m[addr + i] = b


def _synthetic_elf():
    e_ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\x00" * 8  # ELF64 LE
    phoff, phentsize, phnum = 64, 56, 1
    header_rest = struct.pack(
        "<HHIQQQIHHHHHH",
        2, loader.EM_AARCH64, 1, 0x1000,   # e_type, e_machine(AArch64), e_version, e_entry
        phoff, 0, 0,                        # e_phoff, e_shoff(keine), e_flags
        64, phentsize, phnum, 0, 0, 0)      # e_ehsize, e_phentsize, e_phnum, shentsize, shnum, shstrndx
    code = b"\x1f\x20\x03\xd5"              # nop -> 0xd503201f
    code_off = phoff + phentsize           # 120
    phdr = struct.pack("<IIQQQQQQ",
                       loader.PT_LOAD, loader.PF_R | loader.PF_X,
                       code_off, 0x1000, 0x1000, len(code), 0x1000, 0x1000)
    return e_ident + header_rest + phdr + code


def test_loader_parses_synthetic_elf():
    img = loader.ElfImage.parse(_synthetic_elf())
    assert img.is_aarch64 is True
    assert img.entry == 0x1000
    assert len(img.segments) == 1
    seg = img.segments[0]
    assert seg.vaddr == 0x1000 and seg.memsz == 0x1000
    assert seg.perm_str() == "r-x"
    assert img.memory_map()[0]["perms"] == "r-x"
    assert img.dynsyms == {}  # keine Sections -> keine Symbole


def test_loader_rejects_non_elf():
    for bad in (b"", b"not-an-elf-file-with-enough-length-padding........................"):
        try:
            loader.ElfImage.parse(bad)
            assert False, "sollte ValueError werfen"
        except ValueError:
            pass


def test_unknown_instruction_error_fields():
    err = UnknownInstructionError(0x1000, 0xd503201f, encoding="no decoder")
    assert err.pc == 0x1000 and err.raw == 0xd503201f
    d = err.as_dict()
    assert d["pc"] == 0x1000 and d["raw"] == 0xd503201f
    assert "0x1000" in str(err) and "0xd503201f" in str(err)


def test_coverage_report():
    c = CoverageReport()
    c.record("mov"); c.record("mov"); c.record("add")
    c.record_unknown(0x20, 0xdeadbeef)
    assert c.seen["mov"] == 2 and c.steps == 3
    other = CoverageReport(); other.record("mov"); other.record_unknown(0x24, 0xdeadbeef)
    c.merge(other)
    assert c.seen["mov"] == 3 and c.steps == 4
    s = c.summary()
    assert "distinct_opcodes=2" in s and "unknown_sites=2" in s and "unknown_raw=1" in s


def test_fake_environment_deterministic():
    env = FakeEnvironment(clock_start_ns=100, clock_step_ns=10)
    env.set_maps("0000-1000 r-xp lib.so\n")
    env.set_threads([{"tid": 1, "name": "main"}])
    assert "lib.so" in env.proc_self_maps()
    assert env.thread_list()[0]["name"] == "main"
    assert env.clock_ns() == 100 and env.clock_ns() == 110  # monoton, fixierbar


def test_emulator_load_and_structured_unknown():
    events = []
    emu = Emulator(DictMemory(), listener=lambda e, p: events.append((e, p)))
    emu.load_image(loader.ElfImage.parse(_synthetic_elf()))
    assert emu.cpu.pc == 0x1000
    assert emu.mem.perms(0x1000) & iface.PERM_X
    try:
        emu.step()  # kein Dekoder -> strukturierter Fehler, kein stiller Abbruch
        assert False, "sollte UnknownInstructionError werfen"
    except UnknownInstructionError as e:
        assert e.pc == 0x1000 and e.raw == 0xd503201f
    assert any(ev == EV_UNKNOWN_INSTR for ev, _ in events)
    assert emu.coverage.unknown and emu.coverage.unknown[0][1] == 0xd503201f


def test_plugin_hook_registration_and_listener():
    events = []
    emu = Emulator(DictMemory(), listener=lambda e, p: events.append((e, p)))
    emu.load_image(loader.ElfImage.parse(_synthetic_elf()))
    state = {"ran": False}

    class DemoPlugin(TargetPlugin):
        name = "demo"
        def address_hooks(self):
            def _hook(em):
                state["ran"] = True
                em.cpu.pc += 4   # Hook schreitet selbst voran
            return {0x1000: _hook}

    emu.attach_plugin(DemoPlugin())
    insn = emu.step()           # pc==0x1000 -> Hook statt Dekoder
    assert state["ran"] is True
    assert insn.mnemonic == "<hook>"
    assert emu.coverage.seen.get("<hook>") == 1
    assert any(ev == EV_STEP and p.get("hook") for ev, p in events)


def test_snapshot_roundtrip():
    emu = Emulator(DictMemory())
    emu.cpu.x[0] = 0xcafe
    emu.cpu.pc = 0x1234
    snap = Snapshot.capture(emu, meta={"note": "m1"})
    emu.cpu.x[0] = 0
    emu.cpu.pc = 0
    snap.restore_into(emu)
    assert emu.cpu.x[0] == 0xcafe and emu.cpu.pc == 0x1234
    assert snap.meta["note"] == "m1"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")


if __name__ == "__main__":
    _run_all()
