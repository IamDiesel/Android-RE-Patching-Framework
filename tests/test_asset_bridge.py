# -*- coding: utf-8 -*-
"""#356: AssetManager -> VFS. Die App liest ihre Assets selbst; Reads sind mit anderen
InputStream-Handlern (Netz) verkettet."""
from core.java.dex_loader import AndroidStubs
from core.java.objects import JObject, JString, JArray
from core.java.asset_bridge import install_asset_stubs, seed_assets_from_dir

_C = "Landroid/content/res/AssetManager;"


def _stubs():
    s = AndroidStubs()
    install_asset_stubs(s)
    return s


def test_open_and_read_asset():
    s = _stubs()
    s.vfs.mount("libX.so.enc", b"PACKED-BYTES")
    ok, is_ = s.resolve(_C, "open", "(Ljava/lang/String;)Ljava/io/InputStream;",
                        (JObject(_C), JString("libX.so.enc")), False)
    assert ok and isinstance(is_, JObject) and bytes(is_.fields["$data"]) == b"PACKED-BYTES"
    arr = JArray("B", [0] * 32)
    ok, n = s.resolve("Ljava/io/InputStream;", "read", "([B)I", (is_, arr), False)
    assert ok and n == len(b"PACKED-BYTES")
    assert bytes(bytearray(arr.data[:n])) == b"PACKED-BYTES"
    # EOF
    ok, n2 = s.resolve("Ljava/io/InputStream;", "read", "([B)I", (is_, arr), False)
    assert n2 == -1


def test_read_single_byte_and_available():
    s = _stubs()
    s.vfs.mount("a.bin", b"AB")
    _, is_ = s.resolve(_C, "open", "(Ljava/lang/String;)Ljava/io/InputStream;",
                       (JObject(_C), JString("a.bin")), False)
    _, av = s.resolve("Ljava/io/InputStream;", "available", "()I", (is_,), False)
    assert av == 2
    _, b0 = s.resolve("Ljava/io/InputStream;", "read", "()I", (is_,), False)
    _, b1 = s.resolve("Ljava/io/InputStream;", "read", "()I", (is_,), False)
    _, b2 = s.resolve("Ljava/io/InputStream;", "read", "()I", (is_,), False)
    assert (b0, b1, b2) == (65, 66, -1)


def test_list_assets():
    s = _stubs()
    s.vfs.mount("libs/a.so", b"x"); s.vfs.mount("libs/b.so", b"y"); s.vfs.mount("other", b"z")
    ok, arr = s.resolve(_C, "list", "(Ljava/lang/String;)[Ljava/lang/String;",
                        (JObject(_C), JString("libs/")), False)
    names = sorted(x.text for x in arr.data)
    assert names == ["libs/a.so", "libs/b.so"]


def test_read_chaining_delegates_non_asset():
    """Read auf einem Nicht-Asset-Objekt geht an den zuvor registrierten Handler (z. B. Netz)."""
    s = AndroidStubs()
    marker = {"called": 0}
    s.register_by_name("read", "([B)I", lambda a: (marker.__setitem__("called", marker["called"] + 1) or 7))
    install_asset_stubs(s)   # kettet sich davor
    ok, n = s.resolve("Ljava/io/InputStream;", "read", "([B)I",
                      (JObject("Ljava/net/SomeStream;"), JArray("B", [0])), False)
    assert n == 7 and marker["called"] == 1


def test_seed_assets_from_dir(tmp_path):
    import os
    s = AndroidStubs(); install_asset_stubs(s)
    os.makedirs(os.path.join(str(tmp_path), "assets"))
    with open(os.path.join(str(tmp_path), "assets", "index.bundle"), "wb") as f:
        f.write(b"JSBYTES")
    n = seed_assets_from_dir(s, str(tmp_path))
    assert n == 1 and s.vfs.read("index.bundle") == b"JSBYTES"
