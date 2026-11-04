# -*- coding: utf-8 -*-
"""Offline-Tests fuer Fix 1.9 (Inject-Added-Libs / LibForge-Aktivierung).

Deckt die Kernkriterien ab, OHNE ein echtes apktool/Device:
  T1 set_active(): Exklusivitaet pro so_output_name (gleichnamige werden deaktiviert),
     genau EIN finaler Zustand -> schuetzt gegen 'alte Variante gewinnt'.
  T2 InjectAddedLibsStep: die aktive .so landet im Workspace-lib/ mit FRISCHER mtime
     (copyfile+utime statt copy2) -> schlaegt die apktool-build/apk/lib-Cache-mtime.
"""
import os, sys, time, tempfile, json, shutil

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if R not in sys.path:
    sys.path.insert(0, R)

from services.native_lib_service import NativeLibManager
from core.pipeline.steps.lib_steps import InjectAddedLibsStep


class _Cfg:
    def __init__(self, base, dest):
        self.config = {"BASE_DIR": base}
        self.paths = {"DEST_DIR": dest, "APP_SOURCE_DIR": os.path.join(base, "source")}


class _Ctx:
    def __init__(self, base, dest, folder):
        self.cfg = _Cfg(base, dest)
        self._folder = folder
        self.logs = []
    def get_unpacked_dir_name(self):
        return self._folder
    def log(self, m):
        self.logs.append(str(m))


def _mk_lib(mgr, name, data, old_mtime=None):
    lib = mgr.create(name=name)
    d = os.path.join(mgr.libs_dir, lib.id)
    os.makedirs(d, exist_ok=True)
    sof = os.path.join(d, "lib%s.so" % name)
    with open(sof, "wb") as f:
        f.write(data)
    if old_mtime:
        os.utime(sof, (old_mtime, old_mtime))
    lib.so_relpath = os.path.relpath(sof, mgr.base_dir).replace("\\", "/")
    lib.abi = "arm64-v8a"
    mgr.update(lib)
    return lib.id


def test_set_active_exclusive():
    base = tempfile.mkdtemp(prefix="t1_")
    try:
        mgr = NativeLibManager(base)
        a = _mk_lib(mgr, "trace", b"A-v5.8")
        b = _mk_lib(mgr, "trace", b"B-v7.0")
        auto1 = mgr.set_active(a, True)
        assert auto1 == [], "erstes Aktivieren darf nichts deaktivieren"
        auto2 = mgr.set_active(b, True)
        assert auto2 == [a], "Aktivieren von b muss a (gleichnamig) deaktivieren, auto=%r" % auto2
        m2 = NativeLibManager(base)  # fresh von Platte
        assert m2.get(a).active is False, "a muss nach Exklusivitaet inaktiv sein"
        assert m2.get(b).active is True, "b muss aktiv sein"
        act = [l for l in m2.get_active()]
        assert len(act) == 1 and act[0].id == b, "genau EINE aktive trace, und zwar b"
        print("T1 OK: set_active exklusiv -> genau 1 aktive Variante (b), a auto-deaktiviert")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_inject_added_fresh_mtime():
    base = tempfile.mkdtemp(prefix="t2b_")
    dest = tempfile.mkdtemp(prefix="t2d_")
    try:
        folder = "base_unpacked_apktool"
        mgr = NativeLibManager(base)
        old = time.time() - 5_000_000  # ~58 Tage alt
        lid = _mk_lib(mgr, "trace", b"ACTIVE-v5.8-PAYLOAD", old_mtime=old)
        mgr.set_active(lid, True)
        # Ziel-APK-Baum mit vorhandenem lib/arch (sonst skippt der Step)
        arch_dir = os.path.join(dest, folder, "lib", "arm64-v8a")
        os.makedirs(arch_dir, exist_ok=True)
        # (optional) eine veraltete Datei simulieren, die ueberschrieben werden soll
        with open(os.path.join(arch_dir, "libtrace.so"), "wb") as f:
            f.write(b"STALE-v7.0")
        os.utime(os.path.join(arch_dir, "libtrace.so"), (old, old))

        ctx = _Ctx(base, dest, folder)
        t0 = time.time()
        ok = InjectAddedLibsStep().execute({}, ctx)
        assert ok is True, "execute muss True liefern"
        dst = os.path.join(arch_dir, "libtrace.so")
        assert os.path.exists(dst), "libtrace.so muss im Workspace liegen"
        with open(dst, "rb") as f:
            got = f.read()
        assert got == b"ACTIVE-v5.8-PAYLOAD", "falscher Inhalt: %r" % got
        mt = os.path.getmtime(dst)
        assert mt >= t0 - 2, "mtime nicht frisch (mt=%.0f t0=%.0f) -> Cache-Trap!" % (mt, t0)
        assert mt > old + 1000, "mtime darf nicht die alte Quell-mtime sein"
        print("T2 OK: aktive v5.8 injiziert, Inhalt korrekt, mtime frisch (mt-t0=%.2fs)" % (mt - t0))
    finally:
        shutil.rmtree(base, ignore_errors=True)
        shutil.rmtree(dest, ignore_errors=True)



def test_overwrite_guard_protects_app_lib():
    """F2: Wenn eine gleichnamige ECHTE App-Lib im Source existiert, darf LibForge
    sie NICHT ueberschreiben (statt der toten 'not in managed_names'-Tautologie)."""
    base = tempfile.mkdtemp(prefix="t3b_"); dest = tempfile.mkdtemp(prefix="t3d_")
    try:
        folder = "base_unpacked_apktool"
        mgr = NativeLibManager(base)
        lid = _mk_lib(mgr, "trace", b"LIBFORGE-PAYLOAD")
        mgr.set_active(lid, True)
        arch_dir = os.path.join(dest, folder, "lib", "arm64-v8a"); os.makedirs(arch_dir, exist_ok=True)
        # Workspace-Zustand nach MirrorWorkspace: Original-App-Lib liegt schon da
        with open(os.path.join(arch_dir, "libtrace.so"), "wb") as fo: fo.write(b"APP-ORIGINAL")
        # ... und existiert auch im Source-Original -> damit gilt sie als echte App-Lib
        src_arch = os.path.join(base, "source", "com.app", folder, "lib", "arm64-v8a")
        os.makedirs(src_arch, exist_ok=True)
        with open(os.path.join(src_arch, "libtrace.so"), "wb") as fo: fo.write(b"APP-ORIGINAL")

        ctx = _Ctx(base, dest, folder)
        ctx.cfg.paths["APP_SOURCE_DIR"] = os.path.join(base, "source", "com.app")
        ok = InjectAddedLibsStep().execute({}, ctx)
        assert ok is True
        with open(os.path.join(arch_dir, "libtrace.so"), "rb") as fo: got = fo.read()
        assert got == b"APP-ORIGINAL", "echte App-Lib wurde ueberschrieben! got=%r" % got
        assert any("echte App-Lib" in m for m in ctx.logs), "kein Skip-Log fuer App-Lib"
        print("T3 OK: echte App-Lib (im Source) wird NICHT ueberschrieben (lib_replace-Hinweis geloggt)")
    finally:
        shutil.rmtree(base, ignore_errors=True); shutil.rmtree(dest, ignore_errors=True)


def test_find_lib_prefers_active():
    """E: _find_lib('trace') liefert bei Mehrdeutigkeit die AKTIVE Variante (nicht libs[0])."""
    try:
        from mcp_server.tools import _find_lib
    except Exception as e:
        print("T4 SKIP: _find_lib nicht importierbar (%s)" % e); return
    base = tempfile.mkdtemp(prefix="t4_")
    try:
        mgr = NativeLibManager(base)
        a = _mk_lib(mgr, "trace", b"A"); b = _mk_lib(mgr, "trace", b"B")
        mgr.set_active(b, True)  # b aktiv, a inaktiv
        class _C:
            def __init__(self, m): self._m = m
            def libs(self): return self._m
        m2 = NativeLibManager(base)
        _, lib = _find_lib(_C(m2), "trace")
        assert lib is not None and lib.id == b, "erwartet aktive (b), bekam %s" % (lib.id if lib else None)
        _, lib2 = _find_lib(_C(m2), a)  # id-Match schlaegt Name/aktiv
        assert lib2 is not None and lib2.id == a, "id-Match muss exakt a liefern"
        print("T4 OK: _find_lib bevorzugt aktive Variante bei Namens-Mehrdeutigkeit; id-Match exakt")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_lib_list_metadata():
    """P2-3: lib_list liefert so_exists/size/sha256/so_mtime der aktiven .so."""
    try:
        from mcp_server.tools import lib_list
    except Exception as e:
        print("T5 SKIP: lib_list nicht importierbar (%s)" % e); return
    import hashlib
    base = tempfile.mkdtemp(prefix="t5_")
    try:
        mgr = NativeLibManager(base)
        lid = _mk_lib(mgr, "trace", b"HELLO-v5.8-" + b"X"*5000)
        g = mgr.get(lid); g.version = "5.8"; mgr.update(g); mgr.set_active(lid, True)
        class _C:
            def __init__(self, m): self._m = m
            def libs(self): return self._m
        rows = lib_list(_C(NativeLibManager(base)))
        r = next(x for x in rows if x["id"] == lid)
        exp = hashlib.sha256(b"HELLO-v5.8-" + b"X"*5000).hexdigest()[:12]
        assert r["so_exists"] and r["size"] == 5011 and r["sha256"] == exp and r["so_mtime"] > 0, r
        assert r["version"] == "5.8" and r["active"] is True, r
        print("T5 OK: lib_list-Metadaten korrekt (sha=%s, size=%d)" % (r["sha256"], r["size"]))
    finally:
        shutil.rmtree(base, ignore_errors=True)

if __name__ == "__main__":
    fails = 0
    for fn in (test_set_active_exclusive, test_inject_added_fresh_mtime, test_overwrite_guard_protects_app_lib, test_find_lib_prefers_active, test_lib_list_metadata):
        try:
            fn()
        except AssertionError as e:
            fails += 1
            print("FAIL %s: %s" % (fn.__name__, e))
        except Exception as e:
            fails += 1
            import traceback; traceback.print_exc()
    print("=== %s ===" % ("ALLE OK" if fails == 0 else "%d FEHLER" % fails))
    sys.exit(1 if fails else 0)
