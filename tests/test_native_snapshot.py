# -*- coding: utf-8 -*-
"""Test fuer den Snapshot-Pfad (save in Engine A -> load in frische Engine B), headless."""
import os
import tempfile

from services.native_emu_service import NativeEmuService


def test_snapshot_mem_roundtrip_cross_engine():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    eng.mem.write(0x9000, b"\xAB\xCD\xEF\x01")
    fd, p = tempfile.mkstemp(suffix=".pkl.xz"); os.close(fd)
    try:
        svc.save_snapshot(eng, p)
        eng2 = svc.new_engine()                      # frische Engine
        assert svc.read_mem(eng2, 0x9000, 4) == b"\x00\x00\x00\x00"
        svc.load_snapshot(eng2, p)
        assert svc.read_mem(eng2, 0x9000, 4) == b"\xAB\xCD\xEF\x01"
    finally:
        if os.path.exists(p):
            os.remove(p)
