# -*- coding: utf-8 -*-
"""tests/test_libcarve_and_live_overlay.py — Ende-zu-Ende-Tests fuer:
1. ElfImage & NativeIndex (_FMT=5: vaddr/offset, stripped sub_, CallGraph, DataGraph, AOB-Suche)
2. LibCarveCompilerService (ASM In-Place + NOP-Padding + 16-KB ELF64 Cave + C-Cave)
3. LibCarvePatternService (relokations-tolerante Wildcard-AOB-Signatur auf verschobener .so)
4. Zero-Build Live-Overlay (WorkspaceOverlayResolver + NativeFusionLoader Prio 1)
5. Pipeline-Build-Integration (ApplyLibCarvePatchesStep)
6. Optionaler Statischer Flutter/Dart-API-Extractor -> API Inspector SQLite DB
7. MCP-Paritaet (Gruppe N + Gruppe I + zone='live' + Favoriten) & KI-Sync Clobber-Schutz
8. Tk-GUI Smoke Test (WorkspaceTab mit Hex Patcher, LibForge, LibCarve, Smali Studio)
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.application.session_state import SessionState
from core.native.loader import ElfImage, PF_R, PF_X, PT_LOAD
from core.native.nindex import NativeIndex


# ---------------------------------------------------------------------------
# Synthetischer AArch64-ELF64-Baukasten (IP-neutral, 16-KB-aligned PT_LOAD)
# ---------------------------------------------------------------------------
def _enc_adrp(rd: int, pc: int, target: int) -> int:
    imm = (target >> 12) - (pc >> 12)
    immlo = imm & 0x3
    immhi = (imm >> 2) & 0x7FFFF
    return 0x90000000 | (immlo << 29) | (immhi << 5) | (rd & 0x1F)


def _enc_add_imm(rd: int, rn: int, imm12: int) -> int:
    return 0x91000000 | ((imm12 & 0xFFF) << 10) | ((rn & 0x1F) << 5) | (rd & 0x1F)


def _enc_bl(pc: int, target: int) -> int:
    imm26 = ((target - pc) // 4) & 0x3FFFFFF
    return 0x94000000 | imm26


def _build_test_elf64(
    *,
    code_words_at: dict[int, list[int]],
    ro_blobs_at: dict[int, bytes],
    code_vaddr: int = 0x4000,
    ro_vaddr: int = 0x8000,
    align: int = 0x4000,
) -> bytes:
    """Erzeugt ein minimales validiertes ELF64/AArch64 Shared Object ohne Sektionen (stripped)."""
    max_code_end = max((va - code_vaddr + len(ws) * 4 for va, ws in code_words_at.items()), default=0x200)
    code_sz = max(0x400, ((max_code_end + 63) // 64) * 64)
    max_ro_end = max((va - ro_vaddr + len(b) for va, b in ro_blobs_at.items()), default=0x100)
    ro_sz = max(0x100, ((max_ro_end + 63) // 64) * 64)

    code_off = align
    ro_off = align + 0x4000

    total_len = ro_off + ro_sz
    buf = bytearray(total_len)

    # Code füllen (Standard: NOPs)
    for pos in range(0, code_sz, 4):
        struct.pack_into("<I", buf, code_off + pos, 0xD503201F)
    for va, words in code_words_at.items():
        rel = va - code_vaddr
        for idx, w in enumerate(words):
            struct.pack_into("<I", buf, code_off + rel + idx * 4, w & 0xFFFFFFFF)

    # Rodata füllen
    for va, blob in ro_blobs_at.items():
        rel = va - ro_vaddr
        buf[ro_off + rel:ro_off + rel + len(blob)] = blob

    # ELF64 Header
    e_ident = b"\x7fELF\x02\x01\x01" + b"\x00" * 9
    ehdr = e_ident + struct.pack(
        "<HHIQQQIHHHHHH",
        3,              # ET_DYN
        0xB7,           # EM_AARCH64
        1,              # EV_CURRENT
        code_vaddr,     # e_entry
        64,             # e_phoff
        0,              # e_shoff
        0,              # e_flags
        64,             # e_ehsize
        56,             # e_phentsize
        2,              # e_phnum
        64,             # e_shentsize
        0,              # e_shnum
        0,              # e_shstrndx
    )
    ph0 = struct.pack("<IIQQQQQQ", PT_LOAD, PF_R | PF_X, code_off, code_vaddr, code_vaddr, code_sz, code_sz, align)
    ph1 = struct.pack("<IIQQQQQQ", PT_LOAD, PF_R, ro_off, ro_vaddr, ro_vaddr, ro_sz, ro_sz, align)
    buf[0:64] = ehdr
    buf[64:120] = ph0
    buf[120:176] = ph1
    return bytes(buf)


def _sample_elf_v1() -> bytes:
    """Stripped ARM64 ELF:
    - 0x4000: stp fp,lr; adrp x0,0x8000; add x0,x0,#0x20; bl 0x4100; mov w1,#7; ldp; ret
    - 0x4100: stp fp,lr; mov w0,#0x2a; ldp; ret
    - 0x8020: b'demo_config_endpoint\\x00'
    """
    return _build_test_elf64(
        code_vaddr=0x4000,
        ro_vaddr=0x8000,
        code_words_at={
            0x4000: [
                0xA9BF7BFD,                         # stp x29, x30, [sp, #-16]!
                _enc_adrp(0, 0x4004, 0x8000),       # adrp x0, #0x8000
                _enc_add_imm(0, 0, 0x20),           # add x0, x0, #0x20 -> 0x8020
                _enc_bl(0x400C, 0x4100),            # bl 0x4100
                0x528000E1,                         # mov w1, #7
                0xA8C17BFD,                         # ldp x29, x30, [sp], #16
                0xD65F03C0,                         # ret
            ],
            0x4100: [
                0xA9BF7BFD,                         # stp x29, x30, [sp, #-16]!
                0x52800540,                         # mov w0, #0x2a
                0xA8C17BFD,                         # ldp x29, x30, [sp], #16
                0xD65F03C0,                         # ret
            ],
        },
        ro_blobs_at={
            0x8000: b"header_magic_pad\x00",
            0x8020: b"demo_config_endpoint\x00",
        },
    )


def _sample_elf_v2_shifted() -> bytes:
    """Identische Logik, aber Funktion auf 0x4200, Callee auf 0x4340, String auf 0x8050 verschoben."""
    return _build_test_elf64(
        code_vaddr=0x4000,
        ro_vaddr=0x8000,
        code_words_at={
            0x4200: [
                0xA9BF7BFD,
                _enc_adrp(0, 0x4204, 0x8000),
                _enc_add_imm(0, 0, 0x50),           # anderes imm12 -> 0x8050
                _enc_bl(0x420C, 0x4340),            # anderes bl-Ziel -> 0x4340
                0x528000E1,
                0xA8C17BFD,
                0xD65F03C0,
            ],
            0x4340: [
                0xA9BF7BFD,
                0x52800540,
                0xA8C17BFD,
                0xD65F03C0,
            ],
        },
        ro_blobs_at={
            0x8050: b"demo_config_endpoint\x00",
        },
    )


# ---------------------------------------------------------------------------
# 1. ElfImage & NativeIndex (_FMT=5)
# ---------------------------------------------------------------------------
def test_elfimage_and_nindex_stripped_analysis(tmp_path):
    raw = _sample_elf_v1()
    img = ElfImage.parse(raw)
    assert img.is_aarch64 is True
    off = img.vaddr_to_offset(0x4000)
    assert off == 0x4000
    assert img.offset_to_vaddr(off) == 0x4000
    assert len(img.read_vaddr(0x8020, 20)) == 20
    summ = img.elf_summary()
    assert summ["is_aarch64"] is True
    assert summ["is_16kb_aligned"] is True
    assert summ["segment_count"] == 2

    idx = NativeIndex().build(img, cache_dir=str(tmp_path), fast=True)
    # Stripped sub_4000 & sub_4100 synthetisiert
    names = {nm: a for a, nm, _k in idx.func_catalog}
    assert names.get("sub_4000") == 0x4000
    assert names.get("sub_4100") == 0x4100

    # Outgoing Call Graph
    out_calls = idx.outgoing_from(0x4000, 0x4020)
    assert any(tgt == 0x4100 and mn == "bl" for _src, mn, tgt, _cn in out_calls)

    # Data Graph + String-Extraktion
    strs = idx.extract_strings(img)
    assert any(s == "demo_config_endpoint" and a == 0x8020 for a, s in strs)
    dg = idx.datagraph_for(img, 0x4000, 0x4020)
    assert any(r.get("target") == 0x8020 for r in dg)

    # AOB-Suche mit Wildcards
    hits = idx.search_aob(img, "FD 7B BF A9 ?? ?? ?? ?? ?? ?? ?? 91")
    assert 0x4000 in hits


# ---------------------------------------------------------------------------
# 2. LibCarveCompilerService (In-Place ASM + NOP-Padding + 16-KB-Cave + C-Cave)
# ---------------------------------------------------------------------------
def test_libcarve_compiler_inplace_and_cave():
    from services.libcarve_compiler_service import LibCarveCompilerService

    raw = _sample_elf_v1()

    # In-Place ASM mit automatischem NOP-Padding auf orig_size=16
    res = LibCarveCompilerService.assemble_arm64("mov w0, #1\nret", vaddr=0x4100, orig_size=16, auto_nop=True)
    assert res["ok"] is True
    assert res["needs_cave"] is False
    assert res["nops_added"] == 2
    assert len(res["bytes"]) == 16

    patched_inplace, rep1 = LibCarveCompilerService.apply_patches_to_elf(
        raw,
        [{"mode": "asm", "vaddr": "0x4100", "orig_size": 16, "edit": "mov w0, #1\nret"}],
    )
    assert rep1 and rep1[0]["ok"] is True
    assert rep1[0]["mode"] == "asm_inplace"
    img_in = ElfImage.parse(patched_inplace)
    assert img_in.read_vaddr(0x4100, 8) == res["bytes"][:8]

    # Oversized ASM -> automatisches 16-KB-ausgerichtetes Cave-Segment + Branch-Trampolin
    big_asm = "\n".join(["mov w0, #1", "mov w1, #2", "mov w2, #3", "ret"])
    patched_cave, rep2 = LibCarveCompilerService.apply_patches_to_elf(
        raw,
        [{"mode": "asm", "vaddr": "0x4100", "orig_size": 8, "edit": big_asm}],
    )
    assert rep2 and rep2[0]["ok"] is True
    assert "cave_vaddr" in rep2[0]
    cave_vaddr = int(rep2[0]["cave_vaddr"], 16)
    assert cave_vaddr % 0x4000 == 0
    img_cave = ElfImage.parse(patched_cave)
    assert any(s.vaddr == cave_vaddr and (s.flags & PF_X) for s in img_cave.segments)
    # Erstes Wort an 0x4100 muss ein unbedingtes B (0x14...... / 0x17......) ins Cave sein
    w_tramp, = struct.unpack_from("<I", img_cave.read_vaddr(0x4100, 4), 0)
    assert (w_tramp & 0xFC000000) == 0x14000000

    # Optional: Falls NDK Clang im Workspace vorhanden -> C-Cave ebenfalls end-to-end testen
    chk_c = LibCarveCompilerService.compile_c_cave("int carve_entry(void) { return 7; }", cave_vaddr=0x20000)
    if chk_c.get("ok"):
        patched_c, rep_c = LibCarveCompilerService.apply_patches_to_elf(
            raw,
            [{"mode": "c_cave", "vaddr": "0x4100", "edit": "int carve_entry(void) { return 7; }"}],
        )
        assert rep_c and rep_c[0]["ok"] is True
        assert ElfImage.parse(patched_c).elf_summary()["is_16kb_aligned"] is True


# ---------------------------------------------------------------------------
# 3. LibCarvePatternService (Wildcard-AOB ueber verschobene .so-Version)
# ---------------------------------------------------------------------------
def test_libcarve_pattern_relocation_matching(tmp_path):
    from services.libcarve_pattern_service import LibCarvePatternService

    img_v1 = ElfImage.parse(_sample_elf_v1())
    idx_v1 = NativeIndex().build(img_v1, cache_dir=str(tmp_path / "c1"), fast=True)
    sig = LibCarvePatternService.generate_signature(img_v1, idx_v1, 0x4000, byte_len=28)

    # ADRP (4004) + ADD (4008) + BL (400C) muessen maskiert sein
    tokens = sig["aob"].split()
    assert len(tokens) == 28
    assert tokens[4:8] == ["??", "??", "??", "??"]      # adrp
    assert tokens[12:16] == ["??", "??", "??", "??"]    # bl
    assert "demo_config_endpoint" in sig["anchor_strings"]

    img_v2 = ElfImage.parse(_sample_elf_v2_shifted())
    idx_v2 = NativeIndex().build(img_v2, cache_dir=str(tmp_path / "c2"), fast=True)

    patch_tpl = {
        "file": "libdemo.so",
        "vaddr": "0x4000",
        "func": "sub_4000",
        "aob": sig["aob"],
        "orig_hex": sig["orig_hex"],
        "anchor_strings": sig["anchor_strings"],
        "rel_offset": 0,
    }
    res = LibCarvePatternService.match_patch(img_v2, idx_v2, patch_tpl)
    assert res["ok"] is True
    assert res["vaddr"] == 0x4200


# ---------------------------------------------------------------------------
# 4. Zero-Build Live-Overlay (WorkspaceOverlayResolver + NativeFusionLoader Prio 1)
# ---------------------------------------------------------------------------
def test_workspace_overlay_and_fusion_priority_one(tmp_path):
    from services.workspace_overlay_service import WorkspaceOverlayResolver
    from core.java.fusion_loader import NativeFusionLoader

    pkg = "com.example.demo"
    base_dir = tmp_path
    app_root = base_dir / "source" / pkg / "base_unpacked_apkeditor"
    so_orig_path = app_root / "lib" / "arm64-v8a" / "libdemo.so"
    so_orig_path.parent.mkdir(parents=True, exist_ok=True)
    orig_elf = _sample_elf_v1()
    so_orig_path.write_bytes(orig_elf)

    # Saubere Basis im self_cache (unveraenderte Original-ELF), waehrend VFS-Overlay den Live-Patch haelt
    sc_dir = base_dir / "data" / "vfs_selfcache" / pkg
    sc_dir.mkdir(parents=True, exist_ok=True)
    stale_elf = orig_elf
    (sc_dir / "libdemo.so").write_bytes(stale_elf)

    try:
        SessionState.clear_session()
        SessionState.active_smali_patches.append({
            "type": "smali",
            "file": "com/example/Demo.smali",
            "orig": "const/4 v0, 0x0",
            "edit": "const/4 v0, 0x1",
            "scope": "method",
        })
        SessionState.active_libcarve_patches.append({
            "type": "libcarve",
            "file": "libdemo.so",
            "abi": "arm64-v8a",
            "vaddr": "0x4100",
            "orig_size": 16,
            "mode": "asm",
            "edit": "mov w0, #9\nret",
        })

        ov = WorkspaceOverlayResolver.collect(str(base_dir), pkg, str(app_root))
        assert ov["summary"]["smali"] == 1
        assert ov["summary"]["libcarve"] == 1
        assert "libdemo.so" in ov["vfs_so_overlays"]
        live_bytes = ov["vfs_so_overlays"]["libdemo.so"]
        assert live_bytes != orig_elf
        # Source-Datei bleibt unangetastet!
        assert so_orig_path.read_bytes() == orig_elf

        # Simuliere VFS 2.0 + NativeFusionLoader Prio 1
        class _DummyFS:
            def __init__(self, mapping):
                self._m = mapping

            def read_file(self, p):
                return self._m.get(p)

        class _DummyOS:
            def __init__(self, mapping):
                self.fs = _DummyFS(mapping)
                self.vfs = mapping

        class _DummyEng:
            pass

        eng = _DummyEng()
        eng._vfs_overlay_libs = set(ov["vfs_so_overlays"].keys())
        eng._os = _DummyOS({f"/data/app/{pkg}/lib/arm64/libdemo.so": live_bytes})

        loader = NativeFusionLoader(
            svc=None, eng=eng, jni=None, table=None,
            app_root=str(app_root), pkg=pkg, base_dir=str(base_dir),
        )
        found_bytes, found_vp = loader._find_vfs_overlay("demo")
        assert found_bytes == live_bytes
        assert found_vp.endswith("/libdemo.so")
        assert (sc_dir / "libdemo.so").read_bytes() == stale_elf
    finally:
        SessionState.clear_session()


# ---------------------------------------------------------------------------
# 5. Build-Pipeline (ApplyLibCarvePatchesStep)
# ---------------------------------------------------------------------------
def test_pipeline_apply_libcarve_step(tmp_path):
    from core.infrastructure.config_manager import DEFAULT_CONFIG
    from core.pipeline.steps.patch_steps import ApplyLibCarvePatchesStep

    assert any(s.get("type") == "libcarve_patch" for s in DEFAULT_CONFIG["PIPELINES"]["BUILD_NATIVE"])
    assert any(s.get("type") == "libcarve_patch" for s in DEFAULT_CONFIG["PIPELINES"]["BUILD_FLUTTER"])

    dest_root = tmp_path / "destination"
    unpacked_name = "app_unpacked"
    so_dest = dest_root / unpacked_name / "lib" / "arm64-v8a" / "libdemo.so"
    so_dest.parent.mkdir(parents=True, exist_ok=True)
    orig_elf = _sample_elf_v1()
    so_dest.write_bytes(orig_elf)

    class _FakeCfg:
        paths = {"DEST_DIR": str(dest_root), "EXTRACT_DIR": ""}
        config = {}

    class _FakeCtx:
        cfg = _FakeCfg()
        logs = []

        def get_unpacked_dir_name(self):
            return unpacked_name

        def log(self, msg):
            self.logs.append(msg)

    try:
        SessionState.clear_session()
        SessionState.active_libcarve_patches.append({
            "type": "libcarve",
            "file": "libdemo.so",
            "abi": "arm64-v8a",
            "vaddr": "0x4100",
            "orig_size": 16,
            "mode": "asm",
            "edit": "mov w0, #3\nret",
        })
        ctx = _FakeCtx()
        ok = ApplyLibCarvePatchesStep().execute({}, ctx)
        assert ok is True
        new_bytes = so_dest.read_bytes()
        assert new_bytes != orig_elf
        # Mov w0, #3 == 0x52800060
        assert struct.unpack_from("<I", ElfImage.parse(new_bytes).read_vaddr(0x4100, 4), 0)[0] == 0x52800060
    finally:
        SessionState.clear_session()


# ---------------------------------------------------------------------------
# 6. Optionaler Statischer Flutter/Dart-API-Extractor -> API Inspector SQLite DB
# ---------------------------------------------------------------------------
def test_flutter_static_api_extractor_and_db_import(tmp_path):
    from services.flutter_api_extractor_service import FlutterApiExtractorService
    from services.api_db_service import ApiDbService

    app_root = tmp_path / "flutter_app"
    assets = app_root / "assets" / "flutter_assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / ".env").write_text(
        "COGNITO_REGION=eu-west-1\n"
        "COGNITO_USER_POOL_ID=eu-west-1_AbCdEfG12\n"
        "COGNITO_CLIENT_ID=1exampleclientid23456\n"
        "APPSYNC_ENDPOINT=https://xyzdemo123.appsync-api.eu-west-1.amazonaws.com/graphql\n"
        "APPSYNC_API_KEY=da2-demoapikey1234567890abcdef\n"
        "REST_BASE_URL=https://api.example.invalid\n",
        encoding="utf-8",
    )
    lib_dir = app_root / "lib" / "arm64-v8a"
    lib_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "libapp.so").write_bytes(
        b"\x00\x01"
        b"query GetDeviceStatus($id: ID!) { getDevice(id: $id) { id battery } }\x00"
        b"https://api.example.invalid/v1/telemetry\x00"
        b"/api/v1/devices\x00"
    )

    scan = FlutterApiExtractorService.extract_from_app_root(str(app_root))
    assert scan["ok"] is True
    assert scan["is_flutter"] is True
    cats = {t["category"] for t in scan["templates"]}
    assert "Cognito" in cats
    assert "GraphQL" in cats
    assert "REST" in cats

    db_path = str(tmp_path / "api_traffic.db")
    chosen = scan["templates"][:2]
    n_ins = FlutterApiExtractorService.import_templates_to_db(db_path, chosen)
    assert n_ins == len(chosen)

    rows = ApiDbService(db_path).get_requests()
    assert len(rows) == len(chosen)
    assert "[Statische Vorlage:" in rows[0][9]


# ---------------------------------------------------------------------------
# 7. MCP-Paritaet & Mensch-KI-Sync (Clobber-Schutz)
# ---------------------------------------------------------------------------
def test_mcp_libcarve_parity_and_ki_workspace_sync(tmp_path, monkeypatch):
    from mcp_server import tools, registry
    from services.favorite_service import FavoriteService
    from services.active_session_service import ActiveSessionService
    from services.ki_workspace_sync_service import KiWorkspaceSyncService

    monkeypatch.setattr(tools, "_REPO", str(tmp_path))

    # Vorbereiten einer isolierten App-Umgebung
    pkg = "com.example.demo"
    src_dir = tmp_path / "source" / pkg
    so_path = src_dir / "base_unpacked_apkeditor" / "lib" / "arm64-v8a" / "libdemo.so"
    so_path.parent.mkdir(parents=True, exist_ok=True)
    so_path.write_bytes(_sample_elf_v1())

    # Flutter assets für api_extract_flutter_static Test
    fl_assets = src_dir / "base_unpacked_apkeditor" / "assets" / "flutter_assets"
    fl_assets.mkdir(parents=True, exist_ok=True)
    (fl_assets / ".env").write_text("BASE_URL=https://rest.example.invalid\n", encoding="utf-8")

    class _CfgObj:
        paths = {
            "APP_SOURCE_DIR": str(src_dir),
            "EXTRACT_DIR": str(src_dir / "base_unpacked_apkeditor"),
            "API_DB": str(tmp_path / "api_traffic.db"),
        }
        config = {"APP_PACKAGE": pkg, "BASE_DIR": str(tmp_path)}

    class _DummyCtx:
        cfg = _CfgObj()

        def __init__(self):
            self._fav = FavoriteService(str(tmp_path / "favorites.json"))
            self._act = ActiveSessionService(str(tmp_path / "active_session.json"))

        def favorites(self):
            return self._fav

        def active_session(self):
            return self._act

        def snapshot(self):
            return {"caps": {}, "tools": {}}

        def cfg_tool_overrides(self):
            return {}

    ctx = _DummyCtx()

    try:
        SessionState.clear_session()

        # Gruppe N Capabilities / Default-Tiers
        caps = tools.mcp_capabilities(ctx)
        assert "libcarve.libs" in caps["tools"]
        assert caps["tools"]["libcarve.libs"]["enabled"] is True
        assert caps["tools"]["libcarve.session_set"]["enabled"] is False
        assert caps["tools"]["libcarve.compile"]["enabled"] is False
        assert "api.extract_flutter_static" in caps["tools"]
        assert "api.import_templates" in caps["tools"]

        # FastMCP Server-Registrierung prüfen (falls `mcp` installiert)
        try:
            from mcp_server.server import build_server
            mcp_inst = build_server(ctx, "127.0.0.1", 8765)
            assert mcp_inst is not None
        except ImportError:
            pass

        # Read-Tools + Preview + Patch CRUD
        libs_res = tools.libcarve_libs(ctx)
        assert libs_res["ok"] is True and libs_res["count"] >= 1

        out_res = tools.libcarve_outline(ctx, "libdemo.so")
        assert out_res["ok"] is True
        assert any(f["name"] == "sub_4000" for f in out_res["functions"])

        s_res = tools.libcarve_search(ctx, "libdemo.so", "demo_config", mode="str")
        assert s_res["ok"] is True and s_res["count"] >= 1

        insp = tools.libcarve_inspect_func(ctx, "libdemo.so", "0x4000")
        assert insp["ok"] is True and insp["signature"]["aob"]

        prev = tools.libcarve_assemble_preview(ctx, "mov w0, #1\nret", mode="asm", vaddr="0x4100", orig_size=16)
        assert prev["ok"] is True and prev["nops_added"] == 2

        add_res = tools.libcarve_patch_add(ctx, "libdemo.so", "0x4100", "mov w0, #5\nret", orig_size=16)
        assert add_res["ok"] is True
        assert tools.libcarve_patch_list(ctx)["count"] == 1
        assert tools.libcarve_patch_update(ctx, 0, code="mov w0, #6\nret")["ok"] is True
        assert tools.libcarve_patch_remove(ctx, 0)["ok"] is True
        assert tools.libcarve_patch_list(ctx)["count"] == 0

        # Favorit mit type="libcarve" + Anwenden
        fav_payload = {
            "name": "Demo Native Patch",
            "version": "1.0",
            "description": "Test LibCarve Favorit",
            "patches": [{
                "type": "libcarve",
                "file": "libdemo.so",
                "abi": "arm64-v8a",
                "vaddr": "0x4100",
                "orig_size": 16,
                "mode": "asm",
                "edit": "mov w0, #4\nret",
            }],
        }
        fav_msg = tools.favorites_add(ctx, json.dumps(fav_payload))
        assert "libcarve" in fav_msg
        app_res = tools.patch_apply_favorite(ctx, "Demo Native Patch")
        assert app_res["applied_ok"] == 1
        assert len(SessionState.active_libcarve_patches) == 1

        # Statischer API-Extractor via MCP + DB-Übernahme
        ex_res = tools.api_extract_flutter_static(ctx)
        assert ex_res["ok"] is True and ex_res["template_count"] >= 1
        tids = [ex_res["templates"][0]["id"]]
        imp_res = tools.api_import_templates(ctx, json.dumps(tids))
        assert imp_res["ok"] is True and imp_res["imported"] == 1

        # KI-Sync Clobber-Schutz
        class _DummyEd:
            def __init__(self):
                self.text = "nop"
                self.is_dirty = True
                self.var_patch_mode = type("_V", (), {"_v": "asm", "set": lambda s, x: setattr(s, "_v", x), "get": lambda s: s._v})()

            def get_edit_text(self):
                return self.text

            def set_edit_text(self, val, mark_dirty=False):
                self.text = val
                self.is_dirty = bool(mark_dirty)

        dummy_ed = _DummyEd()
        dummy_app = type("_App", (), {
            "active_session_svc": ctx.active_session(),
            "workspace_tab": type("_Ws", (), {
                "patch_book": None,
                "smali_studio": None,
                "lib_carve": type("_Lc", (), {
                    "editor": dummy_ed,
                    "controller": type("_LcCtrl", (), {
                        "image": ElfImage.parse(_sample_elf_v1()),
                        "image_path": str(so_path),
                        "image_abi": "arm64-v8a",
                        "current_vaddr": 0x4100,
                        "current_func_name": "sub_4100",
                        "navigate_to": lambda *_a, **_k: None,
                    })(),
                })(),
            })(),
        })()

        sync = KiWorkspaceSyncService(str(tmp_path))
        p_res = sync.workspace_push(dummy_app)
        assert p_res["ok"] is True
        bad_seq = sync.stage_from_ki({"libcarve_draft": {"code": "ret"}}, base_seq=999)
        assert bad_seq["ok"] is False and bad_seq["error"] == "seq_conflict"
        good_seq = sync.stage_from_ki({"libcarve_draft": {"code": "mov w0, #1\nret", "mode": "asm"}})
        assert good_seq["ok"] is True

        # Clobber-Schutz lehnt ab -> Editor-Text bleibt erhalten
        sync.workspace_pull(dummy_app, ask_overwrite=lambda _msg: False)
        assert dummy_ed.text == "nop"
        # Zustimmung -> KI-Entwurf wird übernommen
        sync.workspace_pull(dummy_app, ask_overwrite=lambda _msg: True)
        assert "mov w0, #1" in dummy_ed.text
    finally:
        SessionState.clear_session()


# ---------------------------------------------------------------------------
# 8. Tk-GUI Smoke Test (WorkspaceTab + LibCarveTab + LibCarveEditorWidget)
# ---------------------------------------------------------------------------
def test_tk_workspace_and_libcarve_smoke(tmp_path):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError as e:
        pytest.skip(f"Kein Display fuer Tk: {e}")

    from ui.tabs.workspace_tab import WorkspaceTab

    class _Cfg:
        paths = {"APP_SOURCE_DIR": str(tmp_path), "DEST_DIR": str(tmp_path), "TOOLS_DIR": str(tmp_path)}
        config = {"BASE_DIR": str(tmp_path), "APP_PACKAGE": "com.example.demo", "APP_NAME": "Demo", "APP_VERSION": "1.0"}

    class _DummyCG:
        nodes = {}
        edges = {}
        reverse_edges = {}

        def get_methods_in_file(self, _f):
            return []

    class _DummyApp:
        current_id = "TEST-ID"
        cfg = _Cfg()
        smali_cache = []
        cg = _DummyCG()

        def after(self, _ms, _fn=None):
            pass

        def log(self, _msg):
            pass

        def check_lock(self):
            return False

    try:
        ws = WorkspaceTab(root, _DummyApp())
        root.update_idletasks()
        tab_titles = [ws.patch_book.tab(t, "text") for t in ws.patch_book.tabs()]
        assert "Hex Patcher (Flutter / C++)" in tab_titles
        assert "🛠 LibForge" in tab_titles
        assert "🔪 LibCarve (C / ASM)" in tab_titles
        assert "Smali Studio (Java / Kotlin)" in tab_titles

        ws.lib_carve.editor.set_edit_text("mov w0, #1\nret", mark_dirty=False)
        assert ws.lib_carve.editor.is_dirty is False
        ws.lib_carve.editor.txt_edit.event_generate("<KeyRelease>")
        # get_edit_text funktioniert ohne Fehler
        assert "mov w0, #1" in ws.lib_carve.editor.get_edit_text()
        assert callable(getattr(ws.lib_carve, "_show_libcarve_help_dialog", None))
    finally:
        root.destroy()
