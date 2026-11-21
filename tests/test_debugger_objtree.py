# -*- coding: utf-8 -*-
"""AP-2: Objekt-Inspektor-Baum (bounded, zyklensicher). Headless."""
from core.java.objects import JString, JObject, JArray
from core.java.debug import build_object_node

def test_string_node():
    n = build_object_node("v0", JString("hallo"))
    assert n["type"] == "String" and n["preview"] == "hallo" and n["children"] == []

def test_array_node():
    n = build_object_node("v1", JArray("I", [1, 2, 3]))
    assert n["type"] == "I[3]"
    assert [c["label"] for c in n["children"]] == ["[0]", "[1]", "[2]"]
    assert n["children"][1]["preview"] == "2"

def test_object_fields():
    o = JObject("Lcom/x;", {"a": JString("s"), "b": 7})
    n = build_object_node("v2", o)
    assert n["type"] == "Lcom/x;"
    labels = {c["label"]: c for c in n["children"]}
    assert labels["a"]["type"] == "String"
    assert labels["b"]["preview"] == "7"

def test_depth_cap():
    inner = JObject("Lc;", {"x": 1})
    mid = JObject("Lb;", {"inner": inner})
    top = JObject("La;", {"mid": mid})
    n = build_object_node("root", top, max_depth=1)
    # Tiefe 0 = top, Tiefe 1 = mid (truncated, keine Kinder expandiert)
    mid_node = n["children"][0]
    assert mid_node["label"] == "mid"
    assert mid_node.get("truncated") is True
    assert mid_node["children"] == []

def test_cycle_guard():
    o = JObject("Lcyc;", {})
    o.fields["self"] = o
    n = build_object_node("v0", o, max_depth=6)
    child = n["children"][0]
    assert child["label"] == "self"
    assert child["preview"] == "<cycle>"
    assert child["children"] == []

def test_array_cap():
    big = JArray("I", list(range(100)))
    n = build_object_node("v0", big)
    # 64 Elemente + 1 Sammelknoten
    assert len(n["children"]) == 65
    assert n["children"][-1]["preview"] == "+36 weitere"
