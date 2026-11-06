# -*- coding: utf-8 -*-
"""Tests fuer native_rebase_service (headless, Symbolkarten als dicts)."""
from services.native_rebase_service import NativeRebaseService

OLD = {"foo": 0x1000, "bar": 0x2000, "baz": 0x3000}
NEW = {"foo": 0x1100, "bar": 0x2000, "qux": 0x4000}   # foo +0x100, bar gleich, baz weg, qux neu


def test_map_symbol_shifted():
    r = NativeRebaseService().map_symbol(OLD, NEW, "foo")
    assert r["ok"] and r["new_addr"] == 0x1100 and r["delta"] == 0x100, r


def test_map_symbol_removed():
    r = NativeRebaseService().map_symbol(OLD, NEW, "baz")
    assert r["ok"] and r["new_addr"] is None, r


def test_map_address_within_func():
    r = NativeRebaseService().map_address(OLD, NEW, 0x1008)   # +8 in foo
    assert r["name"] == "foo" and r["offset"] == 8, r
    assert r["new_addr"] == 0x1108 and r["delta"] == 0x100, r


def test_diff_counts():
    d = NativeRebaseService().diff(OLD, NEW)
    assert d["added"] == 1 and d["removed"] == 1, d
    assert d["shifted"] == 1 and d["common"] == 2, d
