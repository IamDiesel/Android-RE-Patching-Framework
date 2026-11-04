"""B2-Tests: reale AArch64-Ausführung über den arm64mini-Adapter (core.native.engine_arm64).
Benötigt capstone + vendored core/native/arm64mini.py. Laeuft mit pytest ODER direkt.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.native import iface
from core.native.engine_arm64 import Arm64Engine, Arm64Decoder, Arm64Memory
from core.native.iface import TargetPlugin
from core.native import loader

ADD_X0_X0_X1 = 0x8b010000
RET = 0xd65f03c0
MOVZ_X0_7 = 0xd28000e0


def test_memory_backend_roundtrip_and_perms():
    m = Arm64Memory()
    m.write(0x100, b"\xaa\xbb\xcc\xdd")
    assert m.read(0x100, 4) == b"\xaa\xbb\xcc\xdd"
    m.map(0x100, 0x10, iface.PERM_RX)
    assert m.perms(0x100) & iface.PERM_X
    assert not (m.perms(0x100) & iface.PERM_W)
    # ungemappte Seite -> Default RWX (arm64mini erzwingt nichts)
    assert m.perms(0x9000) == iface.PERM_RWX


def test_decoder_decodes_real_aarch64():
    d = Arm64Decoder()
    insn = d.decode(ADD_X0_X0_X1, 0x1000)
    assert insn.mnemonic == "add"
    assert insn.size == 4 and insn.pc == 0x1000
    insn2 = d.decode(RET, 0x1004)
    assert insn2.mnemonic == "ret"


def test_run_add_ret():
    eng = Arm64Engine()
    eng.load_blob(0x1000, struct.pack("<II", ADD_X0_X0_X1, RET))
    assert eng.run_function(0x1000, args=(40, 2)) == 42
    assert eng.run_function(0x1000, args=(100, 23)) == 123


def test_run_movz_ret():
    eng = Arm64Engine()
    eng.load_blob(0x2000, struct.pack("<II", MOVZ_X0_7, RET))
    assert eng.run_function(0x2000, args=()) == 7


def test_plugin_hook_via_stub():
    """TargetPlugin-Adress-Hook wird in arm64minis stub-Mechanismus ausgeführt."""
    eng = Arm64Engine()

    class DemoPlugin(TargetPlugin):
        name = "demo"
        def address_hooks(self):
            def _hook(engine):
                engine.cpu.x[0] = 99   # Funktion durch Python ersetzt
            return {0x4000: _hook}

    eng.attach_plugin(DemoPlugin())
    assert 0x4000 in eng.cpu.stub
    # run auf die gestubbte Adresse: stub setzt x0, pc:=lr(SENT) -> return x0
    assert eng.run_function(0x4000, args=(1, 2, 3)) == 99


def test_load_image_maps_segment_on_real_mem():
    # synthetisches ELF aus dem Skelett-Test wiederverwenden (nop-Segment @0x1000)
    e_ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\x00" * 8
    header_rest = struct.pack("<HHIQQQIHHHHHH", 2, loader.EM_AARCH64, 1, 0x1000,
                              64, 0, 0, 64, 56, 1, 0, 0, 0)
    code = b"\x1f\x20\x03\xd5"
    phdr = struct.pack("<IIQQQQQQ", loader.PT_LOAD, loader.PF_R | loader.PF_X,
                       120, 0x1000, 0x1000, len(code), 0x1000, 0x1000)
    img = loader.ElfImage.parse(e_ident + header_rest + phdr + code)
    eng = Arm64Engine()
    eng.load_image(img)
    assert eng.mem.read(0x1000, 4) == code
    assert eng.mem.perms(0x1000) & iface.PERM_X


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")


if __name__ == "__main__":
    _run_all()
