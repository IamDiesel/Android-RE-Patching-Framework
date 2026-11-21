# -*- coding: utf-8 -*-
"""services.libcarve_compiler_service — 3-Stufen-Assembler, C-Cave-Compiler & ELF64-Rebuilder.

Kompiliert ARM64-Assembler-Edits und einzelne C-Ersatzfunktionen fuer bestehende
Android-`.so`-Bibliotheken via Android NDK LLVM (`clang`) inkl. 16-KB-Page-Alignment
und schnellem Python-Fallback fuer gaengige ARM64-Standard-Instruktionen.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from core.infrastructure.command_runner import CommandRunner
from core.infrastructure.tool_manager import ToolManager
from core.native.loader import ElfImage, PF_R, PF_W, PF_X, PT_LOAD

_ALIGN_16K = 0x4000
_ARM64_NOP = b"\x1f\x20\x03\xd5"
_BRANCH_128MB_LIMIT = 0x07FF_FFFC


def _align_up(val: int, align: int) -> int:
    return (int(val) + align - 1) & ~(align - 1)


def _extract_section_from_elf_obj(obj_bytes: bytes, section_name: str = ".text") -> bytes:
    """Extrahiert Rohbytes einer Section (z.B. .text) aus einer ELF64 Relocatable (.o)."""
    if len(obj_bytes) < 64 or obj_bytes[:4] != b"\x7fELF" or obj_bytes[4] != 2:
        raise ValueError("Kein gueltiges ELF64-Objekt")
    (_et, _em, _ev, _ee, _ep, e_shoff, _ef, _ehs, _epe, _epn, e_shentsize, e_shnum, e_shstrndx) = \
        struct.unpack_from("<HHIQQQIHHHHHH", obj_bytes, 16)
    if not e_shoff or not e_shnum or e_shstrndx >= e_shnum:
        raise ValueError("ELF-Objekt hat keine Section-Header")
    secs = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        secs.append(struct.unpack_from("<IIQQQQIIQQ", obj_bytes, off))
    shstr = secs[e_shstrndx]
    strtab = obj_bytes[shstr[4]:shstr[4] + shstr[5]]

    def _nm(no: int) -> str:
        end = strtab.find(b"\x00", no)
        return strtab[no:end].decode("utf-8", "replace") if end >= 0 else ""

    for s in secs:
        if _nm(s[0]) == section_name:
            return obj_bytes[s[4]:s[4] + s[5]]
    raise ValueError(f"Section '{section_name}' nicht im ELF-Objekt gefunden")


def _extract_loaded_exec_from_linked_elf(elf_bytes: bytes) -> Tuple[int, bytes]:
    """Liest das erste ausfuehrbare PT_LOAD-Segment einer verlinkten PIE/Cave-ELF ab Text-VAddr."""
    img = ElfImage.parse(elf_bytes)
    # Bevorzuge .text..Ende des ausfuehrbaren Segments (umfasst .text + .rodata)
    text_sec = img.sections.get(".text")
    for seg in img.segments:
        if seg.flags & PF_X:
            if text_sec and seg.vaddr <= text_sec["addr"] < seg.vaddr + seg.filesz:
                delta = text_sec["offset"] - seg.offset
                blob = img.data[text_sec["offset"]:seg.offset + seg.filesz]
                return int(text_sec["addr"]), blob
            return int(seg.vaddr), img.data[seg.offset:seg.offset + seg.filesz]
    if text_sec:
        return int(text_sec["addr"]), img.data[text_sec["offset"]:text_sec["offset"] + text_sec["size"]]
    raise ValueError("Kein ausfuehrbares Segment im kompilierten Cave-ELF")


def _reg_num(tok: str) -> Tuple[bool, int]:
    """Parst x0..x30/xzr oder w0..w30/wzr -> (is_64bit, reg_idx 0..31)."""
    t = tok.strip().lower()
    if t in ("xzr",):
        return True, 31
    if t in ("wzr",):
        return False, 31
    if t == "sp":
        return True, 31
    if len(t) >= 2 and t[0] in ("x", "w") and t[1:].isdigit():
        idx = int(t[1:])
        if 0 <= idx <= 30:
            return (t[0] == "x"), idx
    raise ValueError(f"Unbekanntes Register: {tok!r}")


def _parse_int(tok: str) -> int:
    s = tok.strip().lstrip("#").strip()
    return int(s, 0)


def _normalize_asm_lines(asm_text: str) -> List[str]:
    """Bereitet ARM64-Assemblerzeilen auf (Kommentare entfernen, ';' als Trenner)."""
    lines: List[str] = []
    for raw_line in str(asm_text or "").splitlines():
        # '//' Kommentar entfernen
        if "//" in raw_line:
            raw_line = raw_line.split("//", 1)[0]
        # Semikolon als Befehlstrenner innerhalb einer Zeile
        for part in raw_line.split(";"):
            s = part.strip()
            if s:
                lines.append(s)
    return lines


def _try_mini_asm_arm64(lines: List[str], base_vaddr: int = 0) -> Optional[bytes]:
    """Deterministischer Python-Fallback-Encoder fuer Standard-ARM64-Patches (<0.1ms).
    Gibt None zurueck, sobald eine komplexe Instruktion oder ein Label vorkommt (-> NDK clang)."""
    out = bytearray()
    pc = int(base_vaddr)
    for ln in lines:
        low = ln.lower().strip()
        if low.endswith(":") or (low.startswith(".") and not low.startswith((".byte", ".word", ".quad", ".inst"))):
            return None
        if low == "nop":
            out += _ARM64_NOP
            pc += 4
            continue
        if low == "ret":
            out += struct.pack("<I", 0xD65F03C0)
            pc += 4
            continue
        m_byte = re.match(r"^\.byte\s+(.+)$", low)
        if m_byte:
            try:
                for p in m_byte.group(1).split(","):
                    out.append(_parse_int(p) & 0xFF)
                pc = base_vaddr + len(out)
                continue
            except Exception:
                return None
        m_word = re.match(r"^\.(?:word|inst|4byte)\s+(.+)$", low)
        if m_word:
            try:
                for p in m_word.group(1).split(","):
                    out += struct.pack("<I", _parse_int(p) & 0xFFFFFFFF)
                pc += 4
                continue
            except Exception:
                return None
        m_quad = re.match(r"^\.quad\s+(.+)$", low)
        if m_quad:
            try:
                for p in m_quad.group(1).split(","):
                    out += struct.pack("<Q", _parse_int(p) & 0xFFFFFFFFFFFFFFFF)
                pc = base_vaddr + len(out)
                continue
            except Exception:
                return None

        # mov wD/xD, #imm16  / movz wD/xD, #imm16
        m_movi = re.match(r"^(mov|movz|movn)\s+([wx]\d+|[wx]zr)\s*,\s*(#?-?(?:0x[0-9a-f]+|\d+))$", low)
        if m_movi:
            op, rd_s, imm_s = m_movi.groups()
            try:
                is64, rd = _reg_num(rd_s)
                imm = _parse_int(imm_s)
            except Exception:
                return None
            sf = 1 if is64 else 0
            if op in ("mov", "movz") and 0 <= imm <= 0xFFFF:
                word = (sf << 31) | (0b10100101 << 23) | (0 << 21) | ((imm & 0xFFFF) << 5) | rd
                out += struct.pack("<I", word)
                pc += 4
                continue
            if op == "movn" and 0 <= imm <= 0xFFFF:
                word = (sf << 31) | (0b00100101 << 23) | (0 << 21) | ((imm & 0xFFFF) << 5) | rd
                out += struct.pack("<I", word)
                pc += 4
                continue
            if op == "mov" and -0x10000 <= imm < 0:
                inv = (~imm) & 0xFFFF
                word = (sf << 31) | (0b00100101 << 23) | (0 << 21) | (inv << 5) | rd
                out += struct.pack("<I", word)
                pc += 4
                continue
            return None

        # mov xD, xS  / mov wD, wS (via orr d, xzr/wzr, s)
        m_movr = re.match(r"^mov\s+([wx]\d+|[wx]zr)\s*,\s*([wx]\d+|[wx]zr)$", low)
        if m_movr:
            try:
                d64, rd = _reg_num(m_movr.group(1))
                s64, rs = _reg_num(m_movr.group(2))
            except Exception:
                return None
            if d64 != s64:
                return None
            sf = 1 if d64 else 0
            word = (sf << 31) | (0b0101010 << 24) | (0 << 21) | (rs << 16) | (0 << 10) | (31 << 5) | rd
            out += struct.pack("<I", word)
            pc += 4
            continue

        # b <addr_or_#off> / bl <addr_or_#off>
        m_br = re.match(r"^(b|bl)\s+(#?-?(?:0x[0-9a-f]+|\d+))$", low)
        if m_br:
            op, tok = m_br.groups()
            try:
                val = _parse_int(tok)
            except Exception:
                return None
            # Mit '#' als relativer Offset interpretiert; absolute Adresse (z.B. b 0x4000) als Ziel relativ zu pc
            delta = val if tok.startswith("#") else (val - pc)
            if (delta & 3) != 0 or not (-0x0800_0000 <= delta <= 0x07FF_FFFC):
                return None
            imm26 = (delta >> 2) & 0x03FF_FFFF
            top = 0x9400_0000 if op == "bl" else 0x1400_0000
            out += struct.pack("<I", top | imm26)
            pc += 4
            continue

        return None
    return bytes(out)


class LibCarveCompilerService:
    """Kompiliert ARM64-ASM und C-Code-Caves in bestehende ELF64/.so-Dateien."""

    @staticmethod
    def patches_digest(patches: List[dict]) -> str:
        norm = json.dumps(patches or [], sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def encode_arm64_branch(cls, from_vaddr: int, to_vaddr: int, link: bool = False) -> bytes:
        delta = int(to_vaddr) - int(from_vaddr)
        if (delta & 3) != 0 or abs(delta) > _BRANCH_128MB_LIMIT:
            raise ValueError(f"Sprungdistanz ausserhalb +-128MB oder nicht 4B-aligned: 0x{delta:x}")
        imm26 = (delta >> 2) & 0x03FF_FFFF
        top = 0x9400_0000 if link else 0x1400_0000
        return struct.pack("<I", top | imm26)

    @classmethod
    def build_trampoline(cls, hook_vaddr: int, cave_vaddr: int) -> bytes:
        """Erzeugt 4-Byte 'b cave' (falls +-128MB) oder 16-Byte ABS64-Trampolin."""
        delta = int(cave_vaddr) - int(hook_vaddr)
        if (delta & 3) == 0 and -0x0800_0000 <= delta <= _BRANCH_128MB_LIMIT:
            return cls.encode_arm64_branch(hook_vaddr, cave_vaddr, link=False)
        # ldr x16, #8  (0x58000050) ; br x16 (0xd61f0200) ; .quad cave_vaddr
        return struct.pack("<IIQ", 0x58000050, 0xD61F0200, int(cave_vaddr) & 0xFFFFFFFFFFFFFFFF)

    @classmethod
    def assemble_arm64(
        cls,
        asm_text: str,
        *,
        vaddr: int = 0,
        orig_size: int = 0,
        auto_nop: bool = True,
        cfg: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """Assembliert ARM64-Instruktionen zu Maschinenbytes inkl. Groessen-/NOP-Check."""
        lines = _normalize_asm_lines(asm_text)
        if not lines:
            return {"ok": False, "error": "Assembler-Eingabe ist leer.", "bytes": b"", "hex": ""}

        # 1) Schneller deterministischer Mini-Assembler
        raw = _try_mini_asm_arm64(lines, base_vaddr=int(vaddr))
        backend = "mini_arm64"

        # 2) NDK Clang Assembler (fuer alle uebrigen ARM64-Instruktionen, Labels, Makros)
        if raw is None:
            clang = ToolManager.resolve_clang(cfg or {}, allow_download=False)
            if not clang:
                return {
                    "ok": False,
                    "error": "Komplexe ARM64-Instruktion benoetigt Android NDK clang (nicht gefunden).",
                    "bytes": b"",
                    "hex": "",
                }
            backend = "ndk_clang_asm"
            with tempfile.TemporaryDirectory(prefix="libcarve_asm_") as td:
                s_path = os.path.join(td, "patch.S")
                o_path = os.path.join(td, "patch.o")
                src_lines = [".text", ".align 2"]
                for ln in lines:
                    # Absolute Zieladressen bei b/bl relativ zum vaddr des Patches umschreiben, falls als 0x... angegeben
                    m_abs = re.match(r"^(b|bl)\s+(0x[0-9a-fA-F]+)$", ln.strip(), re.I)
                    if m_abs and vaddr:
                        tgt = int(m_abs.group(2), 16)
                        delta = tgt - (int(vaddr) + (len(src_lines) - 2) * 4)
                        ln = f"{m_abs.group(1)} . + ({delta})"
                    src_lines.append(ln)
                with open(s_path, "w", encoding="utf-8") as fh:
                    fh.write("\n".join(src_lines) + "\n")
                cmd = f'"{clang}" --target=aarch64-linux-android24 -c -x assembler -o "{o_path}" "{s_path}"'
                res = CommandRunner.run_blocking(cmd, cwd=td)
                if getattr(res, "returncode", 1) != 0 or not os.path.isfile(o_path):
                    err = (getattr(res, "stderr", "") or getattr(res, "stdout", "") or "Assembler-Fehler").strip()
                    return {"ok": False, "error": err, "bytes": b"", "hex": ""}
                with open(o_path, "rb") as fh:
                    raw = _extract_section_from_elf_obj(fh.read(), ".text")

        nops_added = 0
        raw_len = len(raw)
        final_bytes = bytearray(raw)
        needs_cave = False
        if orig_size > 0:
            if raw_len < orig_size and auto_nop and (orig_size - raw_len) % 4 == 0:
                n_pad = (orig_size - raw_len) // 4
                final_bytes.extend(_ARM64_NOP * n_pad)
                nops_added = n_pad
            elif raw_len > orig_size:
                needs_cave = True

        fb = bytes(final_bytes)
        return {
            "ok": True,
            "backend": backend,
            "bytes": fb,
            "raw_len": raw_len,
            "final_len": len(fb),
            "orig_size": int(orig_size),
            "nops_added": nops_added,
            "needs_cave": needs_cave,
            "hex": fb.hex(" "),
        }

    @classmethod
    def compile_c_cave(
        cls,
        c_code: str,
        *,
        cave_vaddr: int,
        func_name: str = "carve_entry",
        helper_defs: Optional[Dict[str, Tuple[int, str]]] = None,
        cfg: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """Kompiliert eine C-Funktion positionsunabhaengig (-fPIC -nostdlib) fuer ein Code-Cave."""
        clang = ToolManager.resolve_clang(cfg or {}, allow_download=False)
        if not clang:
            return {
                "ok": False,
                "error": "C-Cave-Kompilierung benoetigt Android NDK clang (nicht gefunden).",
                "bytes": b"",
            }
        body = str(c_code or "").strip()
        if not body:
            return {"ok": False, "error": "C-Quellcode ist leer.", "bytes": b""}

        # Wenn der Nutzer keinen eigenen Entry definiert, automatisch einbetten falls carve_entry fehlt
        pre_lines = [
            "#include <stdint.h>",
            "#include <stddef.h>",
        ]
        if helper_defs:
            for hname, (haddr, hsig) in helper_defs.items():
                pre_lines.append(f"/* Helper {hname} @ 0x{int(haddr):x} */")
                if hsig:
                    pre_lines.append(f"#define {hname} (({hsig})(uintptr_t)0x{int(haddr):x}ULL)")
        full_c = "\n".join(pre_lines) + "\n\n" + body + "\n"

        with tempfile.TemporaryDirectory(prefix="libcarve_c_") as td:
            c_path = os.path.join(td, "cave.c")
            elf_path = os.path.join(td, "cave.elf")
            with open(c_path, "w", encoding="utf-8") as fh:
                fh.write(full_c)
            # Pruefe ob func_name in full_c definiert ist, sonst --entry auf erstes Symbol verweisen
            entry_flag = f"-Wl,--entry={func_name}" if re.search(rf"\b{re.escape(func_name)}\s*\(", body) else ""
            cmd = (
                f'"{clang}" --target=aarch64-linux-android24 -Os -fPIC -fno-stack-protector '
                f'-fno-asynchronous-unwind-tables -fno-exceptions -nostdlib '
                f'-Wl,-Ttext=0x{int(cave_vaddr):x} -Wl,--build-id=none {entry_flag} '
                f'-o "{elf_path}" "{c_path}"'
            )
            res = CommandRunner.run_blocking(cmd, cwd=td)
            if getattr(res, "returncode", 1) != 0 or not os.path.isfile(elf_path):
                err = (getattr(res, "stderr", "") or getattr(res, "stdout", "") or "C-Compile-Fehler").strip()
                return {"ok": False, "error": err, "bytes": b""}
            with open(elf_path, "rb") as fh:
                linked = fh.read()
            actual_vaddr, cave_bytes = _extract_loaded_exec_from_linked_elf(linked)
            return {
                "ok": True,
                "cave_vaddr": actual_vaddr,
                "bytes": cave_bytes,
                "hex": cave_bytes[:64].hex(" "),
                "size": len(cave_bytes),
            }

    @classmethod
    def _next_cave_vaddr(cls, img: ElfImage, file_len: int) -> Tuple[int, int]:
        """Berechnet 16-KB-ausgerichtete (cave_offset, cave_vaddr) jenseits des bisherigen Images."""
        cave_offset = _align_up(file_len, _ALIGN_16K)
        max_v = max((s.vaddr + max(s.memsz, s.filesz) for s in img.segments), default=0x10000)
        cave_vaddr = _align_up(max_v, _ALIGN_16K)
        # Kongruenz (p_vaddr % 0x4000 == p_offset % 0x4000) ist durch Ausrichtung beider auf 16KB erfuellt
        return cave_offset, cave_vaddr

    @classmethod
    def _inject_exec_segment(cls, buf: bytearray, img: ElfImage, cave_offset: int, cave_vaddr: int, blob: bytes) -> None:
        """Betttet ein 16-KB-ausgerichtetes PF_R|PF_X PT_LOAD-Segment in ELF64 ein."""
        if len(buf) < cave_offset:
            buf.extend(b"\x00" * (cave_offset - len(buf)))
        buf.extend(blob)
        filesz = len(blob)

        e_phoff, = struct.unpack_from("<Q", buf, 32)
        e_phentsize, e_phnum = struct.unpack_from("<HH", buf, 54)
        new_phdr = struct.pack(
            "<IIQQQQQQ",
            PT_LOAD, PF_R | PF_X, cave_offset, cave_vaddr, cave_vaddr,
            filesz, filesz, _ALIGN_16K,
        )

        # Strategie A: Unkritisches PT_NOTE (4) / PT_GNU_STACK (0x6474e551) im bestehenden Phdr-Array ersetzen
        for i in range(e_phnum):
            off = e_phoff + i * e_phentsize
            if off + 56 > len(buf):
                break
            p_type, = struct.unpack_from("<I", buf, off)
            if p_type in (4, 0x6474E551):  # PT_NOTE oder PT_GNU_STACK
                buf[off:off + 56] = new_phdr
                return

        # Strategie B: Bestehende Phdrs + 1 neues PT_LOAD sauber ans Dateiende verlagern
        ph_table = bytearray(buf[e_phoff:e_phoff + e_phnum * e_phentsize])
        ph_table.extend(new_phdr)
        while len(buf) % 8 != 0:
            buf.append(0)
        new_phoff = len(buf)
        buf.extend(ph_table)
        struct.pack_into("<Q", buf, 32, new_phoff)
        struct.pack_into("<H", buf, 56, e_phnum + 1)

    @classmethod
    def apply_patches_to_elf(
        cls,
        elf_bytes: bytes,
        patches: List[dict],
        *,
        cfg: Optional[dict] = None,
    ) -> Tuple[bytes, List[dict]]:
        """Wendet eine Liste von LibCarve-Patches (ASM In-Place, ASM Cave, C-Cave, Hex) auf ELF64 an."""
        buf = bytearray(elf_bytes)
        img = ElfImage.parse(bytes(buf))
        reports: List[dict] = []

        # Schritt 1: In-Place Patches + Sammeln der Cave-Patches
        cave_items: List[Tuple[int, dict, int]] = []
        for idx, p in enumerate(patches or ()):
            if not isinstance(p, dict):
                continue
            mode = str(p.get("mode") or "asm").strip().lower()
            vaddr_raw = p.get("vaddr", 0)
            try:
                vaddr = int(vaddr_raw, 0) if isinstance(vaddr_raw, str) else int(vaddr_raw)
            except Exception:
                reports.append({"index": idx, "ok": False, "error": f"Ungueltige VAddr: {vaddr_raw!r}"})
                continue
            orig_size = int(p.get("orig_size") or 0)
            auto_nop = bool(p.get("auto_nop", True))

            if mode == "hex":
                hex_str = str(p.get("patch") or p.get("edit") or "").replace(" ", "").strip()
                try:
                    pb = bytes.fromhex(hex_str)
                except ValueError as e:
                    reports.append({"index": idx, "ok": False, "vaddr": hex(vaddr), "error": f"Hex-Fehler: {e}"})
                    continue
                off = img.vaddr_to_offset(vaddr)
                if off is None or off + len(pb) > len(buf):
                    reports.append({"index": idx, "ok": False, "vaddr": hex(vaddr), "error": "VAddr nicht im Dateibereich"})
                    continue
                buf[off:off + len(pb)] = pb
                reports.append({"index": idx, "ok": True, "mode": "hex", "vaddr": hex(vaddr), "size": len(pb)})
                continue

            if mode == "c_cave":
                cave_items.append((idx, p, vaddr))
                continue

            # ASM Modus (In-Place falls passend, sonst Cave)
            asm_res = cls.assemble_arm64(p.get("edit", ""), vaddr=vaddr, orig_size=orig_size, auto_nop=auto_nop, cfg=cfg)
            if not asm_res.get("ok"):
                reports.append({"index": idx, "ok": False, "vaddr": hex(vaddr), "error": asm_res.get("error", "ASM Fehler")})
                continue
            if asm_res.get("needs_cave"):
                cave_items.append((idx, {**p, "_pre_asm": asm_res["bytes"]}, vaddr))
                continue

            pb = asm_res["bytes"]
            off = img.vaddr_to_offset(vaddr)
            if off is None or off + len(pb) > len(buf):
                reports.append({"index": idx, "ok": False, "vaddr": hex(vaddr), "error": "VAddr nicht im Dateibereich"})
                continue
            buf[off:off + len(pb)] = pb
            reports.append({
                "index": idx, "ok": True, "mode": "asm_inplace",
                "vaddr": hex(vaddr), "size": len(pb), "nops": asm_res.get("nops_added", 0),
            })

        # Schritt 2: Alle C-Cave / Oversized-ASM Patches in ein gemeinsames 16-KB-Segment kompilieren
        if cave_items:
            cave_off_start, cave_vaddr_base = cls._next_cave_vaddr(img, len(buf))
            combined_cave = bytearray()
            cursor_vaddr = cave_vaddr_base
            trampolines: List[Tuple[int, int, bytes, dict, int]] = []

            for idx, p, hook_vaddr in cave_items:
                mode = str(p.get("mode") or "c_cave").lower()
                if "_pre_asm" in p:
                    # Bereits assemblierter ASM-Block
                    cblob = p["_pre_asm"]
                    c_res = {"ok": True, "cave_vaddr": cursor_vaddr, "bytes": cblob}
                else:
                    fname = str(p.get("func_c_name") or p.get("func") or "carve_entry")
                    c_res = cls.compile_c_cave(
                        p.get("edit", ""),
                        cave_vaddr=cursor_vaddr,
                        func_name=re.sub(r"[^A-Za-z0-9_]", "_", fname) or "carve_entry",
                        helper_defs=p.get("helper_defs"),
                        cfg=cfg,
                    )
                if not c_res.get("ok"):
                    reports.append({"index": idx, "ok": False, "vaddr": hex(hook_vaddr), "error": c_res.get("error")})
                    continue
                target_vaddr = int(c_res["cave_vaddr"])
                cblob = bytes(c_res["bytes"])
                tramp = cls.build_trampoline(hook_vaddr, target_vaddr)
                off = img.vaddr_to_offset(hook_vaddr)
                if off is None or off + len(tramp) > len(buf):
                    reports.append({"index": idx, "ok": False, "vaddr": hex(hook_vaddr), "error": "Hook-VAddr ungueltig"})
                    continue
                orig_sz = int(p.get("orig_size") or 0)
                if orig_sz > 0 and len(tramp) > orig_sz:
                    reports.append({
                        "index": idx, "ok": False, "vaddr": hex(hook_vaddr),
                        "error": f"Trampolin ({len(tramp)}B) groesser als Originalfunktion ({orig_sz}B)",
                    })
                    continue
                combined_cave.extend(cblob)
                while len(combined_cave) % 16 != 0:
                    combined_cave.append(0)
                cursor_vaddr = cave_vaddr_base + len(combined_cave)
                trampolines.append((idx, off, tramp, p, target_vaddr))

            if combined_cave and trampolines:
                cls._inject_exec_segment(buf, img, cave_off_start, cave_vaddr_base, bytes(combined_cave))
                for idx, off, tramp, p, tvaddr in trampolines:
                    buf[off:off + len(tramp)] = tramp
                    reports.append({
                        "index": idx, "ok": True, "mode": str(p.get("mode") or "c_cave"),
                        "vaddr": hex(int(p.get("vaddr", 0), 0) if isinstance(p.get("vaddr"), str) else int(p.get("vaddr", 0))),
                        "cave_vaddr": hex(tvaddr), "trampoline_size": len(tramp),
                    })

        out_bytes = bytes(buf)
        # Abschliessende ELF64-Integritaetspruefung
        ElfImage.parse(out_bytes)
        return out_bytes, reports

    @classmethod
    def compile_to_workspace_cache(
        cls,
        base_dir: str,
        pkg: str,
        abi: str,
        lib_name: str,
        base_elf_bytes: bytes,
        patches: List[dict],
        *,
        cfg: Optional[dict] = None,
    ) -> Tuple[bool, str, List[dict]]:
        """Kompiliert gepatchte .so nach data/libcarve/<pkg>/<abi>/<lib_name> (mit SHA-Meta)."""
        out_bytes, reports = cls.apply_patches_to_elf(base_elf_bytes, patches, cfg=cfg)
        safe_pkg = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in str(pkg or "default.app")).strip("._") or "default.app"
        out_dir = os.path.join(os.path.abspath(base_dir or "."), "data", "libcarve", safe_pkg, abi or "arm64-v8a")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, os.path.basename(lib_name))
        with open(out_path, "wb") as fh:
            fh.write(out_bytes)
        meta = {
            "base_sha": hashlib.sha256(base_elf_bytes).hexdigest()[:12],
            "out_sha": hashlib.sha256(out_bytes).hexdigest()[:12],
            "patch_count": len(patches or ()),
            "patch_digest": cls.patches_digest(patches),
        }
        with open(out_path + ".meta.json", "w", encoding="utf-8") as mfh:
            json.dump(meta, mfh, indent=2)
        all_ok = all(r.get("ok") for r in reports) if reports else True
        return all_ok, out_path, reports
