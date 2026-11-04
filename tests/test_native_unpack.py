"""Tests für die generische Unpack-Naht (core.native.unpack) — IP-neutral, headless."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.native import unpack
from core.native.unpack import UnpackPlugin, UnpackRegistry


class _Dummy(UnpackPlugin):
    name = "dummy"
    def can_handle(self, path):
        return path.endswith(".packed")
    def unpack(self, packed_path, out_path=None):
        return {"ok": True, "out_path": (out_path or packed_path + ".dec"), "info": "dummy"}


def test_registry_register_and_find():
    r = UnpackRegistry()
    r.register(_Dummy())
    assert r.names() == ["dummy"]
    assert len(r.find_for("x.packed")) == 1
    assert len(r.find_for("x.so")) == 0


def test_registry_discover_folder_scan():
    with tempfile.TemporaryDirectory() as d:
        # privates Plugin-Modul im Scan-Ordner anlegen
        with open(os.path.join(d, "demo_decrypter.py"), "w", encoding="utf-8") as f:
            f.write(
                "from core.native.unpack import UnpackPlugin\n"
                "class Demo(UnpackPlugin):\n"
                "    name='demo'\n"
                "    def unpack(self, packed_path, out_path=None):\n"
                "        return {'ok': True}\n"
                "def register(reg):\n"
                "    reg.register(Demo())\n")
        # Datei, die NICHT als Plugin zählt
        with open(os.path.join(d, "helper.py"), "w", encoding="utf-8") as f:
            f.write("x=1\n")
        r = UnpackRegistry()
        n = r.discover(d)
        assert n == 1 and "demo" in r.names()


def test_discover_missing_dir_is_safe():
    r = UnpackRegistry()
    assert r.discover("/nonexistent/dir/xyz") == 0
    assert r.names() == []


def test_analysis_paths(tmp_path=None):
    import tempfile
    with tempfile.TemporaryDirectory() as base:
        root = unpack.plugin_root(base, "llu")
        assert root.endswith(os.path.join("data", "native", "llu"))
        assert os.path.isdir(root)
        lib = unpack.lib_cache_dir(base, "llu", "deadbeef")
        assert os.path.isdir(lib) and "deadbeef" in lib and os.path.sep + "lib" + os.path.sep in lib
        snap = unpack.snapshots_dir(base, "llu")
        assert os.path.isdir(snap) and snap.endswith("snapshots")
        sess = unpack.session_dir(base, "llu", "PID-20261102-120000")
        assert os.path.isdir(sess) and "sessions" in sess and "PID-20261102-120000" in sess


def test_sha8_stable():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "f.bin")
        open(p, "wb").write(b"hello world")
        s1 = unpack.sha8(p); s2 = unpack.sha8(p)
        assert s1 == s2 and len(s1) == 8


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")


if __name__ == "__main__":
    _run_all()
