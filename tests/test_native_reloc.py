# -*- coding: utf-8 -*-
"""Tests fuer native_reloc_service (headless, Fake-Image mit .relocs)."""
from services.native_reloc_service import NativeRelocService


class _Img:
    def __init__(self, relocs):
        self.relocs = relocs


def test_resolve_hit():
    img = _Img([(0x1000, 0x2000, "reloc"), (0x1008, 0x3000, "reloc")])
    r = NativeRelocService().resolve(img, 0x1000)
    assert r["ok"] and r["target"] == 0x2000, r


def test_resolve_hex_string():
    img = _Img([(0x1008, 0x3000, "reloc")])
    r = NativeRelocService().resolve(img, "0x1008")
    assert r["ok"] and r["target"] == 0x3000, r


def test_resolve_miss():
    img = _Img([(0x1000, 0x2000, "reloc")])
    assert NativeRelocService().resolve(img, 0x9999)["ok"] is False


def test_around():
    img = _Img([(0x1000, 0x2000, "reloc"), (0x1008, 0x3000, "reloc"), (0x2000, 0x4000, "reloc")])
    a = NativeRelocService().around(img, 0x1004, radius=0x10)
    assert a["count"] == 2, a
    assert a["relocs"][0]["site"] == 0x1000 and a["relocs"][1]["site"] == 0x1008, a
