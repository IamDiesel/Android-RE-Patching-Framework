# -*- coding: utf-8 -*-
"""Tests fuer das MCP-Tool native.os_attach (A1): N2 Env/Syscall-Substrat im Studio.

Headless; ruft tools.native_os_attach(None, ...) direkt (ctx wird nicht gebraucht, wie bei
native_syscall_policy_*). Ueberspringt, wenn libdb0c fehlt.
"""
import pytest

from mcp_server import tools, registry


def _skip_if_no_lib(r):
    if not r.get("ok") and "Lib fehlt" in (r.get("hinweis") or ""):
        pytest.skip("libdb0c.so nicht gefunden")


def test_registry_has_os_attach():
    spec = registry.get("native.os_attach")
    assert spec is not None
    assert spec.group == "M" and spec.tier == "O"        # lokal/lesend -> frei
    assert registry.tier_default(spec.tier) is True


def test_os_attach_runs_jni_onload():
    # kleines Budget: hier zaehlt die Wohlgeformtheit des Ergebnisses, nicht das Erreichen von done.
    r = tools.native_os_attach(None, entry="JNI_OnLoad", budget=100_000)
    _skip_if_no_lib(r)
    assert r["ok"], r
    assert r["stop"] in ("done", "budget", "survive_cap"), r
    # konvergierter Syscall-Pfad: Summary mit den drei Stufen
    s = r["syscalls"]
    assert {"by_handler", "by_policy", "by_default", "calls", "mmaps"} <= set(s), s
    assert r["env"]["maps_bytes"] > 0
    # JNI-Fassade ist installiert (natives waechst bis 59, sobald die Registrierung durch ist)
    assert r["jni"] is not None and isinstance(r["jni"].get("natives"), int), r["jni"]


def test_os_attach_with_env_inputs():
    r = tools.native_os_attach(
        None, entry="JNI_OnLoad",
        maps="7000000000-7000135000 r-xp 0 fd:00 1 /x/libdb0c.so\n",
        vfs='{"/sys/fs/selinux/enforce": "0"}',
        config='{"uid": 10234, "pid": 4321, "tid": 4399}',
        budget=100_000)
    _skip_if_no_lib(r)
    assert r["ok"], r
    # Env-Eingaben werden unabhaengig vom Laufausgang uebernommen
    assert "/sys/fs/selinux/enforce" in r["env"]["vfs"]
    assert r["env"]["config"].get("uid") == 10234
    assert r["env"]["config"].get("pid") == 4321


def test_os_attach_bad_entry():
    r = tools.native_os_attach(None, entry="does_not_exist", budget=1000)
    _skip_if_no_lib(r)
    assert not r["ok"] and "Entry" in (r.get("hinweis") or ""), r
