import os, sys, tempfile, shutil
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from services.active_session_service import ActiveSessionService


def _p(f):
    return {"type": "smali", "scope": "method", "file": f, "orig": "orig_" + f, "edit": "e"}


def test_delete_propagates_via_save():
    """T1-Praemisse: eine Loeschung (Key in baseline, weg in mine) propagiert."""
    base = tempfile.mkdtemp()
    try:
        svc = ActiveSessionService(base)
        svc.save([_p("A.smali"), _p("B.smali")])
        svc.save([_p("A.smali")])  # B entfernt (wie GUI-Loeschung + Write-Back)
        assert sorted(p["file"] for p in svc.patches()) == ["A.smali"]
        # Neustart (frische Instanz) sieht denselben Stand
        assert sorted(p["file"] for p in ActiveSessionService(base).patches()) == ["A.smali"]
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_clear_with_current_baseline():
    base = tempfile.mkdtemp()
    try:
        svc = ActiveSessionService(base)
        svc.save([_p("A.smali")])
        svc.save([])  # leeren bei aktueller baseline funktioniert
        assert svc.patches() == []
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_cross_process_item_survives_empty_local_save():
    """Dokumentiert die Merge-Semantik: ein vom ANDEREN Prozess (MCP) geschriebener
    Eintrag, den die eigene baseline nicht kennt, ueberlebt ein leeres Save ->
    deshalb braucht 'Aktive Session leeren' (T2) einen merge-umgehenden Clear."""
    base = tempfile.mkdtemp()
    try:
        svc = ActiveSessionService(base)           # baseline leer
        ActiveSessionService(base).save([_p("X.smali")])  # 'MCP' schreibt X
        merged = svc.save([])                      # eigenes leeres Save
        assert sorted(p["file"] for p in merged) == ["X.smali"]
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_clear_bypasses_merge():
    """T2: clear() entfernt auch Fremd-Eintraege (die ein leeres save() behaelt)."""
    base = tempfile.mkdtemp()
    try:
        svc = ActiveSessionService(base)
        ActiveSessionService(base).save([_p("X.smali"), _p("Y.smali")])  # 'MCP'-Eintraege
        svc.clear()
        assert svc.patches() == []
        assert ActiveSessionService(base).patches() == []  # Datei wirklich leer
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    ok = 0
    for fn in fns:
        try:
            fn(); print("PASS", fn.__name__); ok += 1
        except Exception:
            print("FAIL", fn.__name__); traceback.print_exc()
    print("%d/%d passed" % (ok, len(fns)))
    sys.exit(0 if ok == len(fns) else 1)
