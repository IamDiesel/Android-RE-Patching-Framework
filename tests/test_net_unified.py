# -*- coding: utf-8 -*-
"""Netz-Vereinheitlichung: Java (net_recorder) + nativ (OS-8) in einer quellen-markierten Liste. Headless."""
from core.java.net_recorder import NetLog, NetRequest, NetResponse
from core.java.net_unified import normalize_java, normalize_native, unified


def test_normalize_java_from_objects_and_dicts():
    log = NetLog()
    log.record(NetRequest("POST", "https://a/x", {}, b"body"), NetResponse(200, {}, b'{"v":1}'))
    rows = normalize_java(log)
    assert rows[0]["source"] == "java" and rows[0]["method"] == "POST" and rows[0]["code"] == 200
    # auch aus to_list() (dict-Form)
    rows2 = normalize_java(log.to_list())
    assert rows2[0]["url"] == "https://a/x"


def test_normalize_native_socket_log():
    os_net = {"log": [("connect", "10.0.0.1", 443), ("send", 3, b"hello"), ("recv", 3, b"worldlong")]}
    rows = normalize_native(os_net)
    assert [r["kind"] for r in rows] == ["connect", "send", "recv"]
    assert rows[0]["host"] == "10.0.0.1" and rows[0]["port"] == 443
    assert rows[1]["bytes"] == 5 and rows[2]["bytes"] == 9


def test_unified_merges_both_sources():
    log = NetLog(); log.record(NetRequest("GET", "https://a/g"), NetResponse(200, {}, b"ok"))
    os_net = {"log": [("connect", "h", 80)]}
    rows = unified(log, os_net)
    assert [r["source"] for r in rows] == ["java", "native"]
    assert len(rows) == 2
