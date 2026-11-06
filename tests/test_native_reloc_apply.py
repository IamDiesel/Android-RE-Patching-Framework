# -*- coding: utf-8 -*-
"""Test fuer engine.apply_relocations + service load_image(apply_relocs=) (headless)."""
from services.native_emu_service import NativeEmuService


class _Img:
    relocs = [(0x5000, 0x1234, "reloc"), (0x5008, 0xABCDEF, "reloc")]


def test_apply_relocations_writes_pointers():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    n = eng.apply_relocations(_Img())
    assert n == 2
    assert svc.read_mem(eng, 0x5000, 8) == (0x1234).to_bytes(8, "little")
    assert svc.read_mem(eng, 0x5008, 8) == (0xABCDEF).to_bytes(8, "little")


def test_load_image_flag_off_does_not_apply():
    # apply_relocs=False -> Relocs werden NICHT geschrieben (Default/Kontrakt-sicher).
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()

    class _ImgSeg:
        is_aarch64 = True
        segments = []
        relocs = [(0x6000, 0x4321, "reloc")]

    svc.load_image(eng, _ImgSeg(), apply_relocs=False)
    assert svc.read_mem(eng, 0x6000, 8) == b"\x00" * 8
    n = svc.load_image(eng, _ImgSeg(), apply_relocs=True)
    assert n == 1
    assert svc.read_mem(eng, 0x6000, 8) == (0x4321).to_bytes(8, "little")
