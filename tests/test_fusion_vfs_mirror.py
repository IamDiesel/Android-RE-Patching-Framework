# -*- coding: utf-8 -*-
"""#355: Assets/Lib der entpackten App ins VFS spiegeln (App liest selbst, OS entpackt nichts)."""
import os, tempfile
from services.native_emu_service import NativeEmuService
from services.native_os_service import NativeOsService
from core.java.fusion_autoload import mirror_tree_to_vfs


def test_mirror_assets_into_vfs():
    svc = NativeEmuService(publish=False); eng = svc.new_engine()
    osv = NativeOsService(publish=False); osv.attach_env(eng); osv.install(eng)
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, "assets"))
        with open(os.path.join(root, "assets", "libX.so.enc"), "wb") as f:
            f.write(b"PACKED-BYTES")
        n = mirror_tree_to_vfs(osv, eng, root, "assets", "/data/app/assets")
        assert n == 1
        st = eng._os
        assert st.vfs.get("/data/app/assets/libX.so.enc") == b"PACKED-BYTES"


def test_mirror_missing_dir_zero():
    svc = NativeEmuService(publish=False); eng = svc.new_engine()
    osv = NativeOsService(publish=False); osv.attach_env(eng); osv.install(eng)
    assert mirror_tree_to_vfs(osv, eng, "/nope", "assets", "/x") == 0
