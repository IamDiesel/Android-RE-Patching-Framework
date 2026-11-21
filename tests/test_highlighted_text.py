# -*- coding: utf-8 -*-
"""AP1-Tests: reine Tokenizer + Ergebnis-Renderer (ohne tkinter-Display)."""
from ui.widgets.highlighted_text import tokenize, LEXERS, PALETTE
from ui.panels.workflow_result import build_result_sections


def _tags(text, lexer):
    return {t for _, _, t in tokenize(text, lexer)}


def test_python_lexer():
    t = _tags("def f(x):\n    return 0x10  # kommentar", "python")
    assert "keyword" in t and "addr" in t and "comment" in t


def test_disasm_lexer():
    t = _tags("0x390b0: ldr x19, [sp, #0x10]  ; load", "disasm")
    assert "mnemonic" in t and "register" in t and "addr" in t and "comment" in t


def test_pseudocode_lexer():
    t = _tags("goto 0x37e24  # TRAP exit", "pseudocode")
    assert "keyword" in t and "addr" in t and "comment" in t


def test_text_lexer_empty():
    assert tokenize("egal 0x10", "text") == []


def test_all_tags_have_palette():
    for lx in LEXERS:
        for _, _, tag in tokenize("def x: 0x1 ldr x0 ; c", lx):
            assert tag in PALETTE


def test_renderer_devirt():
    r = {"ok": True, "entry": "JNI_OnLoad", "fn": "0x36924", "stop": "0xdead0000",
         "n_dispatches": 756, "n_unique": 75, "n_branch_points": 1,
         "branch_points": [{"handler": "0x390b0", "successors": [
             {"target": "0x390b0", "count": 350, "runs": 1},
             {"target": "0x103320", "count": 1, "runs": 1}],
             "index_table": [{"index": "[-3][55]", "target": "0x390b0", "count": 350, "runs": 1}]}],
         "lift_mode": "skipped_off", "sequence": ["0x1", "0x2"]}
    secs = build_result_sections(r)
    titles = [s["title"] for s in secs]
    assert "Zusammenfassung" in titles and "Branch-CFG" in titles
    assert any(s["lexer"] == "pseudocode" for s in secs)


def test_renderer_devirt_runnable_python():
    r = {"ok": True, "entry": "onSearchResult", "n_dispatches": 10,
         "lift_mode": "runnable", "python": "def lifted():\n    return 0"}
    secs = build_result_sections(r)
    assert any(s["lexer"] == "python" and "def lifted" in s["text"] for s in secs)


def test_renderer_error():
    secs = build_result_sections({"ok": False, "hinweis": "kaputt"})
    assert secs[0]["title"] == "Fehler" and "kaputt" in secs[0]["text"]
