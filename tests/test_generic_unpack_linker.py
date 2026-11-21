# -*- coding: utf-8 -*-
"""Tests fuer Multi-SO-LinkerSpace, Standard-libc-FFI, Futex-/Init-Stall-Schutz,
Mem.wb/rb-Performance, VFS-Prioritaet, NativeFusionLoader & APK-Signatur-Fidelity.
100 % generisch, synthetische AArch64-ELFs (IP-neutral)."""
from __future__ import annotations

import io
import os
import struct
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.native.arm64mini import Mem
from core.native.loader import ElfImage
from core.native.libc_stubs import install_libc_stubs, ensure_tls_page, TLS_CANARY_OFF
from core.java.dalvik import DalvikVM
from core.java.dex_loader import AndroidStubs
from core.java.fs_bridge import install_fs_stubs, get_shared_fs
from core.java.fusion_autoload import autoload_library, run_init_funcs
from core.java.fusion_loader import NativeFusionLoader
from core.java.objects import JObject, JArray, JString
from services.native_emu_service import NativeEmuService
from services.native_ffi_service import NativeFfiService
from services.native_jni_env_service import NativeJniEnvService
from services.native_os_service import NativeOsService
from core.java.native_link import NativeMethodTable


def _movz_x0_ret(val: int) -> bytes:
    return struct.pack("<II", 0xD2800000 | ((val & 0xFFFF) << 5), 0xD65F03C0)


def _build_aarch64_elf(exports: dict[str, bytes], *, init_array_blobs: list[bytes] = ()) -> bytes:
    """Baut ein gueltiges minimales ELF64/AArch64 ET_DYN Shared-Object ab vaddr=0x0."""
    ehdr_sz = 64
    phdr_sz = 56 * 2   # PT_LOAD + PT_DYNAMIC
    code_off = 0x200
    cur = code_off

    sym_addrs: dict[str, int] = {}
    chunks = []
    for nm, blob in exports.items():
        sym_addrs[nm] = cur
        chunks.append((cur, blob))
        cur = (cur + len(blob) + 15) & ~15

    ctor_addrs = []
    for blob in init_array_blobs:
        ctor_addrs.append(cur)
        chunks.append((cur, blob))
        cur = (cur + len(blob) + 15) & ~15

    init_arr_off = cur
    init_arr_bytes = b"".join(struct.pack("<Q", a) for a in ctor_addrs)
    if init_arr_bytes:
        chunks.append((init_arr_off, init_arr_bytes))
        cur = (cur + len(init_arr_bytes) + 15) & ~15

    # .rela.dyn fuer init_array-Eintraege (R_AARCH64_RELATIVE = 1027)
    rela_off = cur
    rela_bytes = bytearray()
    for idx, a in enumerate(ctor_addrs):
        rela_bytes += struct.pack("<QQq", init_arr_off + idx * 8, 1027, a)
    if rela_bytes:
        chunks.append((rela_off, bytes(rela_bytes)))
        cur = (cur + len(rela_bytes) + 15) & ~15

    # .dynstr
    dynstr_off = cur
    dynstr = bytearray(b"\x00")
    sym_name_offs: dict[str, int] = {}
    for nm in exports:
        sym_name_offs[nm] = len(dynstr)
        dynstr += nm.encode() + b"\x00"
    chunks.append((dynstr_off, bytes(dynstr)))
    cur = (cur + len(dynstr) + 15) & ~15

    # .dynsym
    dynsym_off = cur
    dynsym = bytearray(b"\x00" * 24)  # STN_UNDEF
    for nm, off_val in sym_name_offs.items():
        # st_name(I), st_info(B)=GLOBAL|FUNC(0x12), st_other(B), st_shndx(H)=1, st_value(Q), st_size(Q)
        dynsym += struct.pack("<IBBHQQ", off_val, 0x12, 0, 1, sym_addrs[nm], len(exports[nm]))
    chunks.append((dynsym_off, bytes(dynsym)))
    cur = (cur + len(dynsym) + 15) & ~15

    # .dynamic
    dyn_off = cur
    dyn_ents = [
        (5, dynstr_off),      # DT_STRTAB
        (6, dynsym_off),      # DT_SYMTAB
        (10, len(dynstr)),    # DT_STRSZ
        (11, 24),             # DT_SYMENT
    ]
    if ctor_addrs:
        dyn_ents += [
            (25, init_arr_off),       # DT_INIT_ARRAY
            (27, len(init_arr_bytes)),# DT_INIT_ARRAYSZ
            (7, rela_off),            # DT_RELA
            (8, len(rela_bytes)),     # DT_RELASZ
            (9, 24),                  # DT_RELAENT
        ]
    dyn_ents.append((0, 0))
    dyn_bytes = b"".join(struct.pack("<qQ", t, v) for t, v in dyn_ents)
    chunks.append((dyn_off, dyn_bytes))
    cur = (cur + len(dyn_bytes) + 15) & ~15

    # .shstrtab
    shstr_off = cur
    shstr = b"\x00.text\x00.dynstr\x00.dynsym\x00.shstrtab\x00"
    chunks.append((shstr_off, shstr))
    cur = (cur + len(shstr) + 15) & ~15

    shoff = cur
    shdrs = [
        struct.pack("<IIQQQQIIQQ", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        struct.pack("<IIQQQQIIQQ", 1, 1, 6, code_off, code_off, max(1, cur - code_off), 0, 0, 16, 0),
        struct.pack("<IIQQQQIIQQ", 7, 3, 2, dynstr_off, dynstr_off, len(dynstr), 0, 0, 1, 0),
        struct.pack("<IIQQQQIIQQ", 15, 11, 2, dynsym_off, dynsym_off, len(dynsym), 2, 1, 8, 24),
        struct.pack("<IIQQQQIIQQ", 23, 3, 0, shstr_off, shstr_off, len(shstr), 0, 0, 1, 0),
    ]
    total = shoff + len(shdrs) * 64
    buf = bytearray(total)
    # e_ident
    buf[0:16] = b"\x7fELF\x02\x01\x01" + b"\x00" * 9
    struct.pack_into("<HHIQQQIHHHHHH", buf, 16, 3, 0xB7, 1, 0, ehdr_sz, shoff, 0, 64, 56, 2, 64, len(shdrs), 4)
    # PT_LOAD (vaddr=0)
    struct.pack_into("<IIQQQQQQ", buf, 64, 1, 7, 0, 0, 0, total, total, 0x1000)
    # PT_DYNAMIC
    struct.pack_into("<IIQQQQQQ", buf, 64 + 56, 2, 6, dyn_off, dyn_off, dyn_off, len(dyn_bytes), len(dyn_bytes), 8)
    for pos, ch in chunks:
        buf[pos:pos + len(ch)] = ch
    for idx, sh in enumerate(shdrs):
        buf[shoff + idx * 64:shoff + (idx + 1) * 64] = sh
    return bytes(buf)


def test_mem_multipage_wb_rb_correct_and_fast():
    m = Mem()
    payload = bytes((i * 37) & 0xFF for i in range(4 * 1024 * 1024))
    t0 = time.monotonic()
    m.wb(0x1003, payload)
    got = m.rb(0x1003, len(payload))
    dt = time.monotonic() - t0
    assert got == payload
    assert dt < 0.8, f"Mem.wb/rb zu langsam: {dt:.2f}s"


def test_linker_multi_so_isolated_bias_and_maps():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    ffi = NativeFfiService(publish=False)
    jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)
    os_svc = NativeOsService(publish=False)
    os_svc.attach_env(eng)
    os_svc.install(eng)
    tbl = NativeMethodTable()

    elf_a = _build_aarch64_elf({"Java_com_app_A_val": _movz_x0_ret(0x1111)})
    elf_b = _build_aarch64_elf({"Java_com_app_B_val": _movz_x0_ret(0x2222)})
    img_a = ElfImage.parse(elf_a)
    img_b = ElfImage.parse(elf_b)

    info_a = autoload_library(svc, eng, jni, img_a, tbl, run_onload=False, ffi=ffi, name="liba.so")
    info_b = autoload_library(svc, eng, jni, img_b, tbl, run_onload=False, ffi=ffi, name="libb.so")

    assert info_a["load_bias"] != info_b["load_bias"]
    assert info_a["load_bias"] >= 0x70_0000_0000
    assert abs(info_b["load_bias"] - info_a["load_bias"]) >= 0x10_0000

    res_a = svc.run(eng, tbl.lookup("Lcom/app/A;", "val", None)["fnptr"], ())
    res_b = svc.run(eng, tbl.lookup("Lcom/app/B;", "val", None)["fnptr"], ())
    assert res_a["ok"] and res_a["ret"] == 0x1111
    assert res_b["ok"] and res_b["ret"] == 0x2222

    maps_txt = eng._os.env.proc_self_maps()
    assert "liba.so" in maps_txt and "libb.so" in maps_txt


def test_libc_stubs_malloc_memcpy_pthread_cxa():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    ffi = NativeFfiService(publish=False)
    install_libc_stubs(eng, ffi)
    cpu = eng.cpu
    st = ffi._st(eng)

    assert cpu.tpidr_el0 != 0
    canary = int.from_bytes(cpu.m.rb(cpu.tpidr_el0 + TLS_CANARY_OFF, 8), "little")
    assert canary != 0

    cpu.x[0] = 32
    p1 = st.handlers["malloc"](cpu, ffi)
    cpu.m.wb(p1, b"hello-world\x00")
    cpu.x[0] = p1
    assert st.handlers["strlen"](cpu, ffi) == 11

    cpu.x[0] = p1; cpu.x[1] = 128
    p2 = st.handlers["realloc"](cpu, ffi)
    assert p2 != 0 and p2 != p1
    assert cpu.m.rb(p2, 12) == b"hello-world\x00"

    # __cxa_guard_acquire -> 1 beim 1. Mal, 0 nach release
    gaddr = 0x62_0000_0000
    cpu.m.wb(gaddr, b"\x00" * 8)
    cpu.x[0] = gaddr
    assert st.handlers["__cxa_guard_acquire"](cpu, ffi) == 1
    cpu.x[0] = gaddr
    st.handlers["__cxa_guard_release"](cpu, ffi)
    cpu.x[0] = gaddr
    assert st.handlers["__cxa_guard_acquire"](cpu, ffi) == 0

    # pthread TLS key roundtrip
    kout = 0x62_0000_0100
    cpu.x[0] = kout
    assert st.handlers["pthread_key_create"](cpu, ffi) == 0
    kid = int.from_bytes(cpu.m.rb(kout, 4), "little")
    cpu.x[0] = kid; cpu.x[1] = 0xCAFEBABE
    assert st.handlers["pthread_setspecific"](cpu, ffi) == 0
    cpu.x[0] = kid
    assert st.handlers["pthread_getspecific"](cpu, ffi) == 0xCAFEBABE


def test_futex_wait_stall_detector_breaks_cleanly():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    os_svc = NativeOsService(publish=False)
    os_svc.attach_env(eng)
    os_svc.install(eng)
    ensure_tls_page(eng)

    uaddr = 0x50_0000
    eng.mem.write(uaddr, struct.pack("<I", 0))
    # Schleife: mov x8, #98 (NR_FUTEX); mov x0, x19; mov x1, #0; mov x2, #0; svc #0; b #-20
    # 98 = 0x62 -> movz x8, #98 = 0xD2800C48
    code = struct.pack(
        "<IIIIII",
        0xD2800C48,  # movz x8, #98
        0xAA1303E0,  # mov x0, x19
        0xD2800001,  # mov x1, #0
        0xD2800002,  # mov x2, #0
        0xD4000001,  # svc #0
        0x17FFFFFB,  # b #-20
    )
    svc.load_blob(eng, 0x20000, code)
    eng.cpu.x[19] = uaddr
    t0 = time.monotonic()
    eng.run_function(0x20000, (), maxins=500_000, max_wall_s=1.0)
    dt = time.monotonic() - t0
    assert dt < 0.25
    assert str(eng.cpu.stop_reason or "").startswith("futex_stall@")


def test_run_init_funcs_isolates_stalled_ctor():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    ffi = NativeFfiService(publish=False)
    jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)
    tbl = NativeMethodTable()

    ctor0_loop = struct.pack("<I", 0x14000000)  # b #0 (Endlosschleife)
    ctor1_mark = struct.pack("<IIII", 0xD2A00201, 0xD2995FC0, 0xF9000020, 0xD65F03C0)  # write 0xCAFE -> 0x100000
    elf = _build_aarch64_elf({"dummy": _movz_x0_ret(1)}, init_array_blobs=[ctor0_loop, ctor1_mark])
    img = ElfImage.parse(elf)

    info = autoload_library(svc, eng, jni, img, tbl, run_onload=False, ffi=ffi,
                            name="libstalled.so", init_wall_s=0.8)
    assert info["init_stalled"] >= 1
    assert info["init_called"] == 1
    assert int.from_bytes(eng.cpu.m.rb(0x100000, 8), "little") == 0xCAFE


def test_mprotect_exec_invalidates_jit_cache():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    os_svc = NativeOsService(publish=False)
    os_svc.attach_env(eng)
    os_svc.install(eng)
    ensure_tls_page(eng)

    addr = 0x30000
    eng.mem.write(addr, _movz_x0_ret(1))
    assert eng.run_function(addr, (), maxins=1000) == 1

    # Selbstmodifizierender Code: Bytes aendern + mprotect(..., PROT_EXEC=4)
    eng.mem.write(addr, _movz_x0_ret(42))
    eng.cpu.x[0] = addr
    eng.cpu.x[1] = 0x1000
    eng.cpu.x[2] = 0x5  # PROT_READ | PROT_EXEC
    eng.cpu.x[8] = 226  # NR_MPROTECT
    eng.cpu._svc_hook(eng.cpu)

    assert eng.run_function(addr, (), maxins=1000) == 42


def test_vfs_priority_unlink_fd_and_magic_warning():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    os_svc = NativeOsService(publish=False)
    os_svc.attach_env(eng)
    os_svc.install(eng)
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)

    # Legacy-Mount + ueberschreibender VFS 2.0-Mount + unlink
    os_svc.vfs_mount(eng, {"/data/app/a.txt": b"legacy", "/data/app/b.txt": b"legacy_b"})
    fs = install_fs_stubs(stubs, eng, pkg="com.test.p")
    fs.add_file("/data/app/a.txt", b"vfs2_wins")
    fs.unlink("/data/app/b.txt")
    assert fs.read(fs.lookup("/data/app/a.txt")) == b"vfs2_wins"
    assert fs.lookup("/data/app/b.txt") is None

    # /proc/self/fd/N & _retained_files via NativeFusionLoader.vfs_read
    fs._fd_blobs = {77: b"FD_BLOB"}
    fs._retained_files = {"/data/data/com.test.p/files/libret.so": b"RETAINED"}
    assert NativeFusionLoader.vfs_read(eng, "/proc/self/task/1234/fd/77") == b"FD_BLOB"
    assert NativeFusionLoader.vfs_read(eng, "/data/data/com.test.p/files/libret.so") == b"RETAINED"

    # Magic-Mismatch-Diagnose inkl. recent_missing
    stubs.missing.append({"cls": "Landroid/os/Debug;", "name": "fakeCheck", "sig": "()I"})
    fs.add_file("/data/data/com.test.p/files/libbad.so", b"NOT_AN_ELF_" + b"\x00" * 100)
    logs = []
    loader = NativeFusionLoader(
        svc=svc, eng=eng, jni=None, table=NativeMethodTable(),
        stubs=stubs, pkg="com.test.p", log_fn=logs.append,
    )
    loader.load_path("/data/data/com.test.p/files/libbad.so")
    warns = getattr(stubs, "_unpack_warnings", [])
    assert warns and warns[-1]["kind"] == "invalid_elf_magic"
    assert any(m["name"] == "fakeCheck" for m in warns[-1]["recent_missing"])
    assert any("Packer-Diagnose" in ln for ln in logs)


def test_package_manager_real_apk_cert():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("META-INF/CERT.RSA", b"REAL_PKCS7_CERT_BYTES_123456")
        zf.writestr("AndroidManifest.xml", b"manifest")
    apk_bytes = buf.getvalue()

    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    os_svc = NativeOsService(publish=False)
    os_svc.attach_env(eng)
    os_svc.install(eng)
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    install_fs_stubs(stubs, eng, pkg="com.cert.app", apk_bytes=apk_bytes)

    pm = JObject("Landroid/content/pm/PackageManager;", {})
    ok, pinfo = stubs.resolve(
        "Landroid/content/pm/PackageManager;",
        "getPackageInfo",
        "(Ljava/lang/String;I)Landroid/content/pm/PackageInfo;",
        [pm, JString("com.cert.app"), 64],
        static=False,
    )
    assert ok and isinstance(pinfo, JObject)
    sigs = pinfo.fields["signatures"]
    assert isinstance(sigs, JArray) and len(sigs.data) == 1
    ok2, barr = stubs.resolve(
        "Landroid/content/pm/Signature;",
        "toByteArray",
        "()[B",
        [sigs.data[0]],
        static=False,
    )
    assert ok2 and bytes(barr.data) == b"REAL_PKCS7_CERT_BYTES_123456"


def test_buffered_zip_input_stream_does_not_bypass_to_exhausted_inner_accel():
    payload = b"dex\n035\x00HELLO_DEX"
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("classes.dex", payload)
    zip_bytes = zbuf.getvalue()

    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    os_svc = NativeOsService(publish=False)
    os_svc.attach_env(eng)
    os_svc.install(eng)
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    install_fs_stubs(stubs, eng, pkg="com.zip.app")

    cipher = JObject("Lo/FakeCipher;", {"__accel_stream": {"data": zip_bytes, "pos": 0}})
    zis = JObject("Ljava/util/zip/ZipInputStream;", {})
    stubs.resolve("Ljava/util/zip/ZipInputStream;", "<init>", "(Ljava/io/InputStream;)V", [zis, cipher], False)

    bis = JObject("Ljava/io/BufferedInputStream;", {})
    stubs.resolve("Ljava/io/BufferedInputStream;", "<init>", "(Ljava/io/InputStream;)V", [bis, zis], False)

    ok_e, ent = stubs.resolve("Ljava/util/zip/ZipInputStream;", "getNextEntry", "()Ljava/util/zip/ZipEntry;", [zis], False)
    assert ok_e and isinstance(ent, JObject)
    assert cipher.fields["__accel_stream"]["pos"] == len(zip_bytes)

    out_buf = JArray("[B", [0] * len(payload))
    ok_r, n = stubs.resolve("Ljava/io/BufferedInputStream;", "read", "([B)I", [bis, out_buf], False)
    assert ok_r and n == len(payload)
    assert bytes(int(b) & 0xFF for b in out_buf.data[:n]) == payload


def _build_aarch64_elf_with_needed(exports: dict[str, bytes], needed: list[str] = (), soname: str = "") -> bytes:
    """Erweitert `_build_aarch64_elf` minimal um DT_NEEDED und DT_SONAME im PT_DYNAMIC."""
    base = bytearray(_build_aarch64_elf(exports))
    # Einfaches Neubauen mit extra dynstr + dynamic am Ende der Datei innerhalb PT_LOAD
    ehdr_sz = 64
    e_phoff = struct.unpack_from("<Q", base, 32)[0]
    p_dyn_off = e_phoff + 56
    dyn_off, dyn_sz = struct.unpack_from("<QQ", base, p_dyn_off + 8)[0], struct.unpack_from("<QQ", base, p_dyn_off + 32)[0]
    # dyn_strtab lesen
    str_va = str_sz = 0
    for k in range(dyn_sz // 16):
        t, v = struct.unpack_from("<qQ", base, dyn_off + k * 16)
        if t == 5:
            str_va = v
        elif t == 10:
            str_sz = v
    old_str = bytes(base[str_va:str_va + str_sz])
    new_str = bytearray(old_str)
    extra_tags = []
    for dep in needed:
        off = len(new_str)
        new_str += dep.encode() + b"\x00"
        extra_tags.append((1, off))  # DT_NEEDED
    if soname:
        off = len(new_str)
        new_str += soname.encode() + b"\x00"
        extra_tags.append((14, off))  # DT_SONAME

    new_str_off = len(base)
    base += new_str
    # neues .dynamic am Ende anhaengen
    old_ents = []
    for k in range(dyn_sz // 16):
        t, v = struct.unpack_from("<qQ", base, dyn_off + k * 16)
        if t == 0:
            break
        if t == 5:
            v = new_str_off
        elif t == 10:
            v = len(new_str)
        old_ents.append((t, v))
    all_ents = extra_tags + old_ents + [(0, 0)]
    new_dyn_bytes = b"".join(struct.pack("<qQ", t, v) for t, v in all_ents)
    new_dyn_off = len(base)
    base += new_dyn_bytes
    total = len(base)
    # PT_LOAD filesz/memsz und PT_DYNAMIC offset/vaddr/filesz aktualisieren
    struct.pack_into("<QQ", base, e_phoff + 32, total, total)
    struct.pack_into("<QQQQQ", base, p_dyn_off + 8, new_dyn_off, new_dyn_off, new_dyn_off, len(new_dyn_bytes), len(new_dyn_bytes))
    return bytes(base)


def test_dt_needed_autoload_sys_dlopen_selfcache_and_smc_guard(tmp_path):
    from core.java.fusion_dlopen import install_native_dlopen
    from core.java.seam_config import SeamConfig
    from core.native.linker import get_linker

    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    ffi = NativeFfiService(publish=False)
    jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)
    os_svc = NativeOsService(publish=False)
    os_svc.attach_env(eng)
    os_svc.install(eng)
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    fs = install_fs_stubs(stubs, eng, pkg="com.multi.app")
    tbl = NativeMethodTable()

    dep_bytes = _build_aarch64_elf_with_needed({"helper_fn": _movz_x0_ret(55)}, needed=["libc.so"], soname="libhelper.so")
    main_bytes = _build_aarch64_elf_with_needed({"Java_com_multi_app_M_go": _movz_x0_ret(88)}, needed=["libhelper.so", "libc.so"])
    img_main = ElfImage.parse(main_bytes)
    assert img_main.needed == ["libhelper.so", "libc.so"]

    fs.add_file("/data/data/com.multi.app/files/libhelper.so", dep_bytes)
    fs.add_file("/data/data/com.multi.app/files/libmain.so", main_bytes)

    ldr = NativeFusionLoader(
        svc=svc, eng=eng, jni=jni, table=tbl, ffi=ffi, stubs=stubs,
        pkg="com.multi.app", base_dir=str(tmp_path),
        seam_config=SeamConfig.from_profile("self_cache"),
    )
    install_native_dlopen(eng, ffi, svc, jni, tbl,
                          vfs_read=lambda p: ldr.vfs_read(eng, p),
                          fusion_loader=ldr)

    # System-dlopen("libc.so") + dlsym(h, "malloc") / dlsym(RTLD_DEFAULT, "helper_fn")
    svc.load_blob(eng, 0x9000, b"libc.so\x00")
    svc.load_blob(eng, 0x9020, b"malloc\x00")
    svc.load_blob(eng, 0x9040, b"/data/data/com.multi.app/files/libmain.so\x00")
    svc.load_blob(eng, 0x9090, b"helper_fn\x00")
    dlopen_cb = eng.cpu.stub[ffi.stub_of(eng, "dlopen")]
    dlsym_cb = eng.cpu.stub[ffi.stub_of(eng, "dlsym")]

    eng.cpu.x[0] = 0x9000; dlopen_cb(eng.cpu); h_libc = int(eng.cpu.x[0])
    assert h_libc != 0
    eng.cpu.x[0] = h_libc; eng.cpu.x[1] = 0x9020; dlsym_cb(eng.cpu)
    assert int(eng.cpu.x[0]) != 0

    # dlopen(libmain.so) -> laedt libhelper.so per DT_NEEDED automatisch & cached beide in self_cache!
    eng.cpu.x[0] = 0x9040; dlopen_cb(eng.cpu); h_main = int(eng.cpu.x[0])
    assert h_main != 0
    lnk = get_linker(eng)
    assert lnk.find_by_name("libhelper.so") is not None
    assert lnk.find_by_name("libmain.so") is not None
    sc_dir = tmp_path / "data" / "vfs_selfcache" / "com.multi.app"
    assert (sc_dir / "libmain.so").is_file()

    # dlsym(RTLD_DEFAULT=0, "helper_fn") findet Symbol aus dem global_syms Pool!
    eng.cpu.x[0] = 0; eng.cpu.x[1] = 0x9090; dlsym_cb(eng.cpu)
    assert int(eng.cpu.x[0]) == lnk.resolve_symbol("helper_fn")

    # Schnelle libs (<200ms) ohne Stall werden NICHT in den _onload_cache geschrieben (sicher fuer .bss/SMC)
    onload_cdir = sc_dir / "_onload_cache"
    assert not onload_cdir.exists() or list(onload_cdir.glob("*.json")) == []
