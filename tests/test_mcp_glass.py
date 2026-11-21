# -*- coding: utf-8 -*-
"""MCP-Parität: native.glass_run (Lifecycle gläsern + View + Netz + Aktionspunkte) + native.net_log. Headless."""
import json
from mcp_server import tools

ACT = """.class public Lt/Act;
.super Landroid/app/Activity;
.method public onCreate(Landroid/os/Bundle;)V
    .registers 6
    new-instance v0, Landroid/widget/TextView;
    invoke-direct {v0}, Landroid/widget/TextView;-><init>()V
    const-string v1, "Glukose"
    invoke-virtual {v0, v1}, Landroid/widget/TextView;->setText(Ljava/lang/String;)V
    invoke-virtual {p0, v0}, Lt/Act;->setContentView(Landroid/view/View;)V
    new-instance v2, Ljava/net/URL;
    const-string v3, "https://api/glucose"
    invoke-direct {v2, v3}, Ljava/net/URL;-><init>(Ljava/lang/String;)V
    invoke-virtual {v2}, Ljava/net/URL;->openConnection()Ljava/net/URLConnection;
    move-result-object v4
    invoke-virtual {v4}, Ljava/net/HttpURLConnection;->getResponseCode()I
    return-void
.end method
"""


def _mkapp(tmp_path):
    d = tmp_path / "app"
    (d / "smali" / "t").mkdir(parents=True)
    (d / "smali" / "t" / "Act.smali").write_text(ACT, encoding="utf-8")
    return str(d)


def test_glass_run_view_net_actionpoint(tmp_path):
    root = _mkapp(tmp_path)
    res = tools.native_glass_run(
        None, studio_root=root, zone="original", entry="Lt/Act;",
        net_replay=json.dumps({"/glucose": {"code": 200, "body": '{"value":112}'}}),
        action_points=json.dumps([{"cls": "Lt/Act;", "name": "onCreate",
                                   "sig": "(Landroid/os/Bundle;)V", "pc": 0,
                                   "assigns": [{"target": "Lt/Act;.flag", "value": 1, "const": True}],
                                   "when_continue": True}]))
    assert res["ok"] is True, res.get("hinweis")
    assert res["view"]["views"] >= 1
    assert "Glukose" in res["view"]["texts"]
    assert res["net_count"] >= 1
    assert res["net_log"][0]["req"]["url"] == "https://api/glucose"
    assert res["net_log"][0]["resp"]["code"] == 200
    assert res["action_points"] == 1 and not res["ap_errors"]


def test_net_log_reads_last(tmp_path):
    root = _mkapp(tmp_path)
    tools.native_glass_run(None, studio_root=root, entry="Lt/Act;",
                           net_replay=json.dumps({"/glucose": "ok"}))
    out = tools.native_net_log(None)
    assert out["ok"] is True
    assert any("glucose" in e["req"]["url"] for e in out["net_log"])


def test_ui_snapshot_reads_last(tmp_path):
    """MCP-Surface-Paritaet: native.ui_snapshot liefert die UI-Momentaufnahme des letzten Laufs."""
    root = _mkapp(tmp_path)
    tools.native_glass_run(None, studio_root=root, entry="Lt/Act;")
    out = tools.native_ui_snapshot(None)
    assert out["ok"] is True
    assert "Glukose" in out["text"]
    assert any(n["cls"] == "TextView" for n in out["nodes"])
    assert out["width"] and out["height"]
