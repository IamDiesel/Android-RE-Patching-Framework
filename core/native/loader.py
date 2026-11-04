"""loader — minimaler, echter ELF64/AArch64-Parser (FR-1, geräte-/Frida-frei).

Liest Header, Program-Header (PT_LOAD-Segmente) und — sofern Section-Header
vorhanden — `.dynsym`/`.dynstr` (exportierte Symbole). Reine Python-struct-
Arbeit, keine externen Abhängigkeiten. Dient dem Native Studio als
Modul/Outline-Quelle (Segmente + Symbole + Memory-Map).

Bewusst NICHT hier: Ausführung/Relocation-Anwendung (Emulator), Entpacken
gepackter `.so` (M3/LibForge-Unpacker).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional

EM_AARCH64 = 0xB7
PT_LOAD = 1

# ELF64 p_flags -> unsere Perm-Flags
PF_X, PF_W, PF_R = 0x1, 0x2, 0x4


@dataclass
class Segment:
    vaddr: int
    offset: int
    filesz: int
    memsz: int
    flags: int          # ELF p_flags (R/W/X)
    align: int

    def perm_str(self) -> str:
        return ("r" if self.flags & PF_R else "-") + \
               ("w" if self.flags & PF_W else "-") + \
               ("x" if self.flags & PF_X else "-")


@dataclass
class Symbol:
    name: str
    value: int
    size: int
    info: int           # st_info (Bind<<4 | Type)
    shndx: int


@dataclass
class ElfImage:
    data: bytes = b""
    is_aarch64: bool = False
    entry: int = 0
    segments: List[Segment] = field(default_factory=list)
    sections: Dict[str, dict] = field(default_factory=dict)   # name -> {offset,size,addr,type}
    dynsyms: Dict[str, Symbol] = field(default_factory=dict)  # name -> Symbol
    relocs: List = field(default_factory=list)                # (site_vaddr, target_vaddr, kind)

    # ---- Parsing ----------------------------------------------------------
    @classmethod
    def parse(cls, data: bytes) -> "ElfImage":
        if len(data) < 64 or data[:4] != b"\x7fELF":
            raise ValueError("kein ELF (magic fehlt)")
        ei_class, ei_data = data[4], data[5]
        if ei_class != 2:
            raise ValueError("nur ELF64 unterstützt")
        if ei_data != 1:
            raise ValueError("nur Little-Endian unterstützt")

        (e_type, e_machine, e_version, e_entry, e_phoff, e_shoff, e_flags,
         e_ehsize, e_phentsize, e_phnum, e_shentsize, e_shnum, e_shstrndx) = \
            struct.unpack_from("<HHIQQQIHHHHHH", data, 16)

        img = cls(data=data, is_aarch64=(e_machine == EM_AARCH64), entry=e_entry)

        # Program-Header (Segmente)
        for i in range(e_phnum):
            off = e_phoff + i * e_phentsize
            if off + 56 > len(data):
                break
            (p_type, p_flags, p_offset, p_vaddr, p_paddr,
             p_filesz, p_memsz, p_align) = struct.unpack_from("<IIQQQQQQ", data, off)
            if p_type == PT_LOAD:
                img.segments.append(Segment(
                    vaddr=p_vaddr, offset=p_offset, filesz=p_filesz,
                    memsz=p_memsz, flags=p_flags, align=p_align))

        # Section-Header (optional) -> Namen via shstrtab
        if e_shoff and e_shnum and e_shstrndx < e_shnum:
            secs = []
            for i in range(e_shnum):
                off = e_shoff + i * e_shentsize
                if off + 64 > len(data):
                    break
                (sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size,
                 sh_link, sh_info, sh_addralign, sh_entsize) = \
                    struct.unpack_from("<IIQQQQIIQQ", data, off)
                secs.append(dict(name_off=sh_name, type=sh_type, addr=sh_addr,
                                 offset=sh_offset, size=sh_size, link=sh_link,
                                 entsize=sh_entsize))
            shstr = secs[e_shstrndx]
            strtab = data[shstr["offset"]:shstr["offset"] + shstr["size"]]

            def _name(n_off: int, tab: bytes) -> str:
                end = tab.find(b"\x00", n_off)
                return tab[n_off:end].decode("utf-8", "replace") if end >= 0 else ""

            by_name = {}
            for s in secs:
                nm = _name(s["name_off"], strtab)
                s["name"] = nm
                by_name[nm] = s
                img.sections[nm] = {k: s[k] for k in ("offset", "size", "addr", "type")}

            # .dynsym + .dynstr (exportierte Symbole)
            dsym = by_name.get(".dynsym")
            dstr = by_name.get(".dynstr")
            if dsym and dstr and dsym["entsize"] >= 24:
                dstr_tab = data[dstr["offset"]:dstr["offset"] + dstr["size"]]
                n = dsym["size"] // dsym["entsize"]
                for i in range(n):
                    off = dsym["offset"] + i * dsym["entsize"]
                    if off + 24 > len(data):
                        break
                    st_name, st_info, st_other, st_shndx, st_value, st_size = \
                        struct.unpack_from("<IBBHQQ", data, off)
                    nm = _name(st_name, dstr_tab)
                    if nm:
                        img.dynsyms[nm] = Symbol(name=nm, value=st_value,
                                                 size=st_size, info=st_info,
                                                 shndx=st_shndx)
        try:
            _parse_dyn_relocs(img, data)
        except Exception:
            pass
        return img

    @classmethod
    def from_file(cls, path: str) -> "ElfImage":
        with open(path, "rb") as f:
            return cls.parse(f.read())

    # ---- Convenience ------------------------------------------------------
    def memory_map(self) -> List[dict]:
        """Memory-Map fürs Native-Studio-Outline (Segmente + Permissions)."""
        return [dict(vaddr=s.vaddr, memsz=s.memsz, perms=s.perm_str()) for s in self.segments]

    def symbol(self, name: str) -> Optional[Symbol]:
        return self.dynsyms.get(name)


# ---- Dynamische Relokationen (PT_DYNAMIC-basiert, auch bei gestrippten Libs) ----
# Erfasst Daten-/Zeiger-Referenzen, die NICHT im Code als adrp stehen: vtable-Eintraege,
# typeinfo-Zeiger, GOT/PLT. So wird „wer referenziert dieses typeinfo/diese vtable?" sichtbar.
def _v2o(img, v):
    for sg in img.segments:
        if sg.vaddr <= v < sg.vaddr + sg.filesz:
            return sg.offset + (v - sg.vaddr)
    return None


def _parse_dyn_relocs(img, data):
    e_phoff, = struct.unpack_from("<Q", data, 32)
    e_phentsize, e_phnum = struct.unpack_from("<HH", data, 54)
    dyn_off = dyn_sz = None
    for i in range(e_phnum):
        o = e_phoff + i * e_phentsize
        if o + 56 > len(data):
            break
        p_type, = struct.unpack_from("<I", data, o)
        if p_type == 2:  # PT_DYNAMIC
            p_offset, = struct.unpack_from("<Q", data, o + 8)
            p_filesz, = struct.unpack_from("<Q", data, o + 32)
            dyn_off, dyn_sz = p_offset, p_filesz
            break
    if dyn_off is None:
        return
    tags = {}
    for k in range(dyn_sz // 16):
        tag, val = struct.unpack_from("<qQ", data, dyn_off + k * 16)
        if tag == 0:
            break
        tags[tag] = val
    rela, relasz = tags.get(7), tags.get(8, 0)
    jmprel, pltrelsz = tags.get(23), tags.get(2, 0)
    relr, relrsz = tags.get(0x23), tags.get(0x24, 0)
    symtab, syment = tags.get(6), tags.get(11, 24)
    ranges = [(sg.vaddr, sg.vaddr + max(sg.memsz, sg.filesz)) for sg in img.segments]

    def in_load(a):
        return any(l <= a < h for l, h in ranges)

    def symval(idx):
        if not symtab:
            return 0
        so = _v2o(img, symtab + idx * syment)
        if so is None or so + 16 > len(data):
            return 0
        return struct.unpack_from("<Q", data, so + 8)[0]  # Elf64_Sym.st_value @+8

    def add_rela(region_vaddr, region_sz):
        ro = _v2o(img, region_vaddr)
        if ro is None:
            return
        for k in range(region_sz // 24):
            base = ro + k * 24
            if base + 24 > len(data):
                break
            r_offset, r_info, r_addend = struct.unpack_from("<QQq", data, base)
            rtype = r_info & 0xffffffff
            rsym = r_info >> 32
            tgt = None
            if rtype == 1027:                       # R_AARCH64_RELATIVE
                tgt = r_addend
            elif rtype in (257, 1025, 1026):        # ABS64 / GLOB_DAT / JUMP_SLOT
                sv = symval(rsym)
                if sv:
                    tgt = sv + (r_addend if rtype == 257 else 0)
            if tgt is not None and in_load(tgt):
                img.relocs.append((r_offset, tgt, "reloc"))

    if rela:
        add_rela(rela, relasz)
    if jmprel:
        add_rela(jmprel, pltrelsz)
    # RELR (gepackte relative Relokationen) — vorsorglich, falls ein Build es nutzt
    if relr and relrsz:
        ro = _v2o(img, relr)
        if ro is not None:
            addr = 0
            for i in range(relrsz // 8):
                ent, = struct.unpack_from("<Q", data, ro + i * 8)
                if ent & 1 == 0:
                    addr = ent
                    off = _v2o(img, addr)
                    if off is not None and off + 8 <= len(data):
                        tgt, = struct.unpack_from("<Q", data, off)
                        if in_load(tgt):
                            img.relocs.append((addr, tgt, "reloc"))
                    addr += 8
                else:
                    a = addr
                    bits = ent >> 1
                    for b in range(63):
                        if bits & (1 << b):
                            off = _v2o(img, a + b * 8)
                            if off is not None and off + 8 <= len(data):
                                tgt, = struct.unpack_from("<Q", data, off)
                                if in_load(tgt):
                                    img.relocs.append((a + b * 8, tgt, "reloc"))
                    addr += 63 * 8
