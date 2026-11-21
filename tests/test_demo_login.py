# -*- coding: utf-8 -*-
"""#357: vollstaendiger App-Lauf mit GUI + nativer Lib bis zum Login-Screen (generisch, Nordstern).
Login-UI aufgebaut, Passwort-Eingabe, Anmelden-Klick ruft die NATIVE Auth-Methode auf der Engine."""
from core.java.demo_login import build_login_app, set_password, do_login


def _labels(tree):
    out = []
    def walk(n):
        if n is None:
            return
        t = getattr(n, "text", None) or getattr(n, "hint", None)
        out.append((getattr(n, "cls", "?"), t))
        for c in getattr(n, "children", []) or []:
            walk(c)
    walk(tree.root)
    return out


def test_login_screen_built():
    ctx = build_login_app()
    labels = _labels(ctx["tree"])
    classes = [c for c, _ in labels]
    assert classes.count("EditText") == 2        # Benutzername + Passwort
    assert "Button" in classes
    texts = [t for _, t in labels]
    assert "Anmelden" in texts and "Bitte anmelden" in texts


def test_login_success_calls_native():
    ctx = build_login_app()
    set_password(ctx, "geheim12")                 # len 8 -> native check = 2 >= 0 -> OK
    status = do_login(ctx)
    assert status == "Angemeldet (nativ bestaetigt)"
    # die native Methode wurde wirklich ueber die Grenze ausgefuehrt
    assert len(ctx["hook"].log) == 1
    e = ctx["hook"].log.entries[0]
    assert e["fnptr"] == 0x1000 and e["args_in"] == [8] and e["ret_eff"] == 2


def test_login_reject_short_password():
    ctx = build_login_app()
    set_password(ctx, "abc")                      # len 3 -> native check = -3 -> abgelehnt
    status = do_login(ctx)
    assert status == "Passwort zu kurz (nativ geprueft)"
    assert ctx["hook"].log.entries[0]["ret_eff"] == -3 % (1 << 64) or ctx["hook"].log.entries[0]["ret_eff"] == (-3 & ((1<<64)-1))


def test_boundary_manipulation_forces_login():
    """Grenz-Manipulation: Rueckgabe der nativen Pruefung auf 0 zwingen -> Login trotz kurzem Passwort."""
    ctx = build_login_app()
    ctx["hook"].add_action_point(fnptr=0x1000, ret_expr="0")   # erzwinge >=0
    set_password(ctx, "x")
    status = do_login(ctx)
    assert status == "Angemeldet (nativ bestaetigt)"
