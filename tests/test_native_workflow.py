# -*- coding: utf-8 -*-
"""AP2-Tests: Workflow-Registry + Dispatch (ohne echte Engine, nur Meta/Fehlerpfade)."""
from mcp_server import tools


def test_workflow_meta_order_and_fields():
    m = tools.native_workflow_meta(None)
    assert m["ok"] and "devirt" in m["workflows"]
    assert m["order"] and all(w in m["workflows"] for w in m["order"])
    dv = m["workflows"]["devirt"]
    keys = [f["key"] for f in dv["fields"]]
    assert "entry" in keys and "runs" in keys and "lift_max" in keys
    assert dv["hints"] == ["run_fn", "gate2"]


def test_workflow_unknown():
    r = tools.native_workflow(None, "gibtsnicht")
    assert r["ok"] is False and "Unbekannt" in r.get("hinweis", "")


def test_workflow_params_json_string():
    # params als JSON-String darf nicht crashen (Dispatch faengt Engine-Fehler ab)
    r = tools.native_workflow(None, "devirt", '{"entry":"X","runs":1,"budget":1,"lift_max":0}')
    assert isinstance(r, dict) and "ok" in r
