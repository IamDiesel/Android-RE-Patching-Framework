# -*- coding: utf-8 -*-
"""native_ffi_service — generische FFI-/Stub-Schicht fuer die arm64mini-Engine (A3.1).

Hintergrund: arm64mini besitzt bereits einen Trap-Mechanismus
``cpu.stub[pc] -> fn(cpu) -> next_pc|None`` (None => Rueckkehr nach ``lr``);
``run()``/``run_from()``/``step_block()`` honorieren ihn. Diese Schicht nutzt ihn, um
**Fremdaufrufe** (libc-Imports; spaeter JNIEnv/JavaVM in A3.2) abzufangen, statt echten
Code auszufuehren: jeder Import bekommt eine synthetische **Stub-Adresse**, deren Zeiger in
den zugehoerigen **GOT-Slot** geschrieben wird. Springt die Lib dorthin (``blr x8``/``br x8``
nach ``ldr x8,[got]``), feuert ein Python-Handler.

Das ist der generische Kern von **A3 (FakeEnvironment)** und fuellt zugleich einen Teil der
§6-Luecke (Syscalls/Imports) fuer beliebige ``.so``. Zielspezifisches Verhalten (sauberes
``/proc``, Thread-/Zeit-Modell, Reaktions-Abfang) kommt als Handler-Bibliothek in A3.2 obendrauf.

Wichtig fuer den Import-Teil: der Loader fuehrt in ``img.relocs`` bewusst nur **intern
aufloesbare** Relocs (Ziel in der Lib). Import-Slots (undefinierte Symbole, ``st_value=0``)
fehlen dort — darum parst dieser Service sie eigenstaendig aus ``PT_DYNAMIC``.

SOA: stateless Service; der Zustand (Stub-Map, Aufruf-Log, Handler) liegt am eng-Objekt
(``eng._ffi``), damit GUI/MCP/Tests dieselbe Sicht haben. Engine-Kontrakt bleibt bit-exakt:
``run()`` unveraendert, hier nur additive Nutzung von ``cpu.stub`` + Writes in die GOT.
"""
from __future__ import annotations

import struct
from typing import Callable, Dict, List, Optional, Tuple

_M64 = (1 << 64) - 1

# Synthetischer Stub-Adressraum: weit ausserhalb jeder realen .so-Last
# (libdb0c endet ~0x135000). Keine Code-Mappings noetig — die Dispatch-Pruefung
# in arm64mini greift VOR fetch/compile, Stub-Adressen werden nie dekodiert.
STUB_BASE = 0x7A000000
STUB_STRIDE = 0x10

# AArch64-Reloc-Typen
_R_GLOB_DAT = 1025
_R_JUMP_SLOT = 1026


class _FfiState:
    """Am eng gehaltener FFI-Zustand (eine Quelle der Wahrheit)."""

    __slots__ = ("by_name", "by_addr", "handlers", "log", "next_stub",
                 "default_ret", "max_log")

    def __init__(self):
        self.by_name: Dict[str, int] = {}      # Symbol -> Stub-Adresse
        self.by_addr: Dict[int, str] = {}      # Stub-Adresse -> Symbol
        self.handlers: Dict[str, Callable] = {}  # Symbol -> fn(cpu, ffi) -> ret|None
        self.log: List[dict] = []              # geordnete Aufrufe
        self.next_stub: int = STUB_BASE
        self.default_ret: int = 0
        self.max_log: int = 200000


class NativeFfiService:
    """Generische FFI-Stub-Schicht. ``publish`` ist fuer optionale Events reserviert."""

    def __init__(self, publish: bool = True):
        self.publish = publish

    # ---- Zustand am eng -------------------------------------------------
    def _st(self, eng) -> _FfiState:
        st = getattr(eng, "_ffi", None)
        if st is None:
            st = _FfiState()
            eng._ffi = st
        return st

    # ---- Import-Relocs aus dem ELF (site -> Symbolname) -----------------
    @staticmethod
    def import_relocs(image) -> List[Tuple[int, str]]:
        """Liste ``(GOT-Site, Symbolname)`` fuer **undefinierte (importierte)** Symbole.

        Eigenstaendiger PT_DYNAMIC-Walk (JUMP_SLOT/GLOB_DAT mit ``st_shndx==0`` und
        ``st_value==0``). IP-neutral, read-only; dedupliziert je Site."""
        data = getattr(image, "data", None)
        segs = getattr(image, "segments", None)
        if not data or not segs:
            return []
        out: List[Tuple[int, str]] = []
        try:
            e_phoff = struct.unpack_from("<Q", data, 32)[0]
            e_phentsize, e_phnum = struct.unpack_from("<HH", data, 54)
            dyn_off = None
            dyn_sz = 0
            for i in range(e_phnum):
                o = e_phoff + i * e_phentsize
                if o + 56 > len(data):
                    break
                if struct.unpack_from("<I", data, o)[0] == 2:  # PT_DYNAMIC
                    dyn_off = struct.unpack_from("<Q", data, o + 8)[0]
                    dyn_sz = struct.unpack_from("<Q", data, o + 32)[0]
                    break
            if dyn_off is None:
                return []
            tags: Dict[int, int] = {}
            for k in range(dyn_sz // 16):
                tag, val = struct.unpack_from("<qQ", data, dyn_off + k * 16)
                if tag == 0:
                    break
                tags[tag] = val
            symtab = tags.get(6)       # DT_SYMTAB
            strtab = tags.get(5)       # DT_STRTAB
            syment = tags.get(11, 24)  # DT_SYMENT
            rela, relasz = tags.get(7), tags.get(8, 0)        # DT_RELA/RELASZ
            jmprel, pltrelsz = tags.get(23), tags.get(2, 0)   # DT_JMPREL/PLTRELSZ
            if not symtab or not strtab:
                return []

            seg_rows = [(s.vaddr, s.offset, max(int(getattr(s, "filesz", 0)), 0)) for s in segs]

            def v2o(v):
                for va, off, fsz in seg_rows:
                    if va <= v < va + fsz:
                        return off + (v - va)
                return None

            str_off = v2o(strtab)

            def symname(idx):
                so = v2o(symtab + idx * syment)
                if so is None or so + 24 > len(data):
                    return None
                st_name = struct.unpack_from("<I", data, so)[0]
                st_shndx = struct.unpack_from("<H", data, so + 6)[0]
                st_value = struct.unpack_from("<Q", data, so + 8)[0]
                if st_shndx != 0 or st_value != 0:
                    return None  # nur undefinierte (importierte) Symbole
                if str_off is None:
                    return None
                p = str_off + st_name
                end = data.find(b"\x00", p)
                if end < 0:
                    return None
                nm = data[p:end].decode("utf-8", "replace")
                return nm or None

            def scan(region, size):
                ro = v2o(region)
                if ro is None:
                    return
                for k in range(size // 24):
                    base = ro + k * 24
                    if base + 24 > len(data):
                        break
                    r_offset, r_info, _add = struct.unpack_from("<QQq", data, base)
                    rtype = r_info & 0xffffffff
                    rsym = r_info >> 32
                    if rtype in (_R_GLOB_DAT, _R_JUMP_SLOT):
                        nm = symname(rsym)
                        if nm:
                            out.append((int(r_offset), nm))

            if jmprel:
                scan(jmprel, pltrelsz)
            if rela:
                scan(rela, relasz)
        except Exception:
            return out
        seen = set()
        uniq: List[Tuple[int, str]] = []
        for site, nm in out:
            if site in seen:
                continue
            seen.add(site)
            uniq.append((site, nm))
        return uniq

    # ---- Stub-Verwaltung ------------------------------------------------
    def _alloc_stub(self, st: _FfiState) -> int:
        a = st.next_stub
        st.next_stub += STUB_STRIDE
        return a

    def _make_dispatch(self, eng, name: str) -> Callable:
        """Baut den Dispatcher fuer ein Symbol: Args/Ret protokollieren, Handler feuern,
        None zurueckgeben (=> Rueckkehr nach lr)."""
        st = self._st(eng)

        def _disp(cpu):
            x = cpu.x
            args = [int(x[i]) & _M64 for i in range(8)]
            err = None
            ret = None
            handler = st.handlers.get(name)
            if handler is not None:
                try:
                    ret = handler(cpu, self)
                except Exception as e:  # Handler darf den Lauf nie sprengen
                    err = repr(e)
                    ret = None
            if ret is None:
                ret = st.default_ret
            cpu.x[0] = int(ret) & _M64
            if len(st.log) < st.max_log:
                st.log.append({"name": name, "args": args,
                               "ret": int(cpu.x[0]) & _M64,
                               "lr": int(cpu.lr) & _M64, "err": err})
            return None  # => pc = lr

        return _disp

    def install_named_stub(self, eng, name: str) -> int:
        """Legt (idempotent) eine Stub-Adresse fuer ``name`` an und haengt den Dispatcher
        in ``cpu.stub`` ein. Rueckgabe: die Stub-Adresse."""
        st = self._st(eng)
        addr = st.by_name.get(name)
        if addr is None:
            addr = self._alloc_stub(st)
            st.by_name[name] = addr
            st.by_addr[addr] = name
            eng.cpu.stub[addr] = self._make_dispatch(eng, name)
        return addr

    # ---- Imports verdrahten ---------------------------------------------
    def wire_imports(self, eng, image) -> dict:
        """Jeden Import auf eine Stub-Adresse biegen: GOT-Slot := Stub, Dispatcher setzen.

        Reihenfolge: zuerst ``apply_relocations`` (fuellt die internen Dispatch-Zeiger),
        dann ``wire_imports`` (fuellt die Import-Slots, die der Loader NULL laesst)."""
        st = self._st(eng)
        rels = self.import_relocs(image)
        wired: List[dict] = []
        for site, name in rels:
            addr = self.install_named_stub(eng, name)
            try:
                eng.mem.write(int(site), (addr & _M64).to_bytes(8, "little"))
            except Exception:
                continue
            wired.append({"site": int(site), "name": name, "stub": addr})
        return {"ok": True, "count": len(wired), "unique": len(st.by_name),
                "names": sorted(st.by_name.keys()), "wired": wired}

    # ---- Handler / Konfiguration ----------------------------------------
    def set_handler(self, eng, name: str, fn: Callable) -> None:
        """Handler ``fn(cpu, ffi) -> ret|None`` fuer ein Symbol registrieren (A3.2)."""
        self._st(eng).handlers[name] = fn

    def set_default_return(self, eng, val: int) -> None:
        self._st(eng).default_ret = int(val) & _M64

    def stub_of(self, eng, name: str) -> Optional[int]:
        return self._st(eng).by_name.get(name)

    def name_of(self, eng, addr: int) -> Optional[str]:
        return self._st(eng).by_addr.get(int(addr) & _M64)

    # ---- Beobachtung ----------------------------------------------------
    def get_log(self, eng) -> list:
        return list(self._st(eng).log)

    def clear_log(self, eng) -> int:
        st = self._st(eng)
        n = len(st.log)
        st.log = []
        return n

    def summary(self, eng) -> dict:
        from collections import Counter
        st = self._st(eng)
        c = Counter(e["name"] for e in st.log)
        return {"imports": len(st.by_name), "calls": len(st.log),
                "by_name": dict(c)}
