# -*- coding: utf-8 -*-
"""core.java.dex — bounded DEX-Parser (J3 / MS-4).

Liest die Struktur eines ``classes.dex`` (Header, String-/Type-/Proto-/Field-/Method-IDs,
class_defs + class_data + code_items) so weit, dass Klassen/Methoden aufzaehlbar und ihr
Bytecode (code_item.insns) zugaenglich sind. Der Opcode-Decoder nach Tupel-IR sitzt in
`core.java.dex_code`. Bewusst bounded: keine Annotationen, kein Debug-Info, keine tries/handler
(werden uebersprungen). Zielagnostisch, rein lesend.
"""
from __future__ import annotations

import struct


def _uleb128(data, off):
    result = 0
    shift = 0
    while True:
        b = data[off]
        off += 1
        result |= (b & 0x7f) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, off


def _sleb128(data, off):
    result = 0
    shift = 0
    while True:
        b = data[off]
        off += 1
        result |= (b & 0x7f) << shift
        shift += 7
        if not (b & 0x80):
            if b & 0x40:
                result |= -(1 << shift)
            break
    return result, off


def _mutf8(data, off):
    """MUTF-8 bis zum Nullbyte dekodieren (Java modified UTF-8, bounded)."""
    out = []
    while True:
        b = data[off]
        off += 1
        if b == 0:
            break
        if b < 0x80:
            out.append(b)
        elif (b & 0xe0) == 0xc0:
            b2 = data[off]; off += 1
            out.append(((b & 0x1f) << 6) | (b2 & 0x3f))
        elif (b & 0xf0) == 0xe0:
            b2 = data[off]; off += 1
            b3 = data[off]; off += 1
            out.append(((b & 0x0f) << 12) | ((b2 & 0x3f) << 6) | (b3 & 0x3f))
        else:
            out.append(b)
    try:
        return "".join(chr(c) for c in out)
    except Exception:
        return bytes(out).decode("utf-8", "replace")


class Method:
    __slots__ = ("cls", "name", "sig", "access", "code_off")

    def __init__(self, cls, name, sig, access, code_off):
        self.cls = cls
        self.name = name
        self.sig = sig
        self.access = access
        self.code_off = code_off

    @property
    def is_static(self):
        return bool(self.access & 0x8)

    @property
    def is_native(self):
        return bool(self.access & 0x100)

    @property
    def is_abstract(self):
        return bool(self.access & 0x400)


class DexFile:
    """Geparstes DEX. Index-Tabellen eager, Klassen-/Code-Details lazy."""

    def __init__(self, data: bytes):
        self.data = data
        if data[:4] != b"dex\n":
            raise ValueError("kein DEX (Magic falsch)")
        self.version = data[4:7].decode("ascii", "replace")
        h = struct.unpack_from("<8xI20xIIIII", data, 0)  # checksum, file_size, header_size, endian, link_size, link_off
        (self.string_ids_size, self.string_ids_off,
         self.type_ids_size, self.type_ids_off,
         self.proto_ids_size, self.proto_ids_off,
         self.field_ids_size, self.field_ids_off,
         self.method_ids_size, self.method_ids_off,
         self.class_defs_size, self.class_defs_off,
         self.data_size, self.data_off) = struct.unpack_from("<14I", data, 56)
        self._str_cache = {}

    # ---- Index-Zugriffe ----------------------------------------------------
    def string(self, idx):
        if idx in self._str_cache:
            return self._str_cache[idx]
        off = struct.unpack_from("<I", self.data, self.string_ids_off + idx * 4)[0]
        _, p = _uleb128(self.data, off)   # utf16_size (ignoriert)
        s = _mutf8(self.data, p)
        self._str_cache[idx] = s
        return s

    def type(self, idx):
        descr_idx = struct.unpack_from("<I", self.data, self.type_ids_off + idx * 4)[0]
        return self.string(descr_idx)

    def _type_list(self, off):
        if not off:
            return []
        size = struct.unpack_from("<I", self.data, off)[0]
        out = []
        for i in range(size):
            tidx = struct.unpack_from("<H", self.data, off + 4 + i * 2)[0]
            out.append(self.type(tidx))
        return out

    def proto(self, idx):
        base = self.proto_ids_off + idx * 12
        shorty_idx, ret_idx, params_off = struct.unpack_from("<III", self.data, base)
        params = self._type_list(params_off)
        ret = self.type(ret_idx)
        return params, ret

    def sig_of_proto(self, idx):
        params, ret = self.proto(idx)
        return "(" + "".join(params) + ")" + ret

    def method(self, idx):
        base = self.method_ids_off + idx * 8
        cls_idx, proto_idx, name_idx = struct.unpack_from("<HHI", self.data, base)
        return self.type(cls_idx), self.string(name_idx), self.sig_of_proto(proto_idx)

    def field(self, idx):
        base = self.field_ids_off + idx * 8
        cls_idx, type_idx, name_idx = struct.unpack_from("<HHI", self.data, base)
        return self.type(cls_idx), self.string(name_idx), self.type(type_idx)

    # ---- Klassen / Methoden / Static-Defaults -------------------------------
    def class_defs(self):
        for i in range(self.class_defs_size):
            base = self.class_defs_off + i * 32
            class_idx, access, superclass_idx, interfaces_off, source_idx, ann_off, cd_off, sv_off = \
                struct.unpack_from("<8I", self.data, base)
            ifaces = []
            if interfaces_off:
                sz = struct.unpack_from("<I", self.data, interfaces_off)[0]
                ifaces = [self.type(struct.unpack_from("<H", self.data, interfaces_off + 4 + j * 2)[0])
                          for j in range(sz)]
            yield {"name": self.type(class_idx), "access": access,
                   "super": self.type(superclass_idx) if superclass_idx != 0xffffffff else None,
                   "interfaces": ifaces,
                   "class_data_off": cd_off,
                   "static_values_off": sv_off}

    def find_class(self, name):
        for cd in self.class_defs():
            if cd["name"] == name:
                return cd
        return None

    def _decode_encoded_value(self, off):
        """Dekodiert EIN DEX `encoded_value` ab `off` -> (python_wert_oder_tag, neues_off)."""
        d = self.data
        hdr = d[off]
        off += 1
        vtype = hdr & 0x1F
        varg = (hdr >> 5) & 0x7
        if vtype == 0x1E:    # VALUE_NULL
            return None, off
        if vtype == 0x1F:    # VALUE_BOOLEAN
            return (1 if varg else 0), off
        size = varg + 1
        raw = d[off:off + size]
        off += size
        if vtype == 0x00:    # VALUE_BYTE (signed 1 byte)
            v = raw[0] if raw else 0
            return (v - 0x100 if v & 0x80 else v), off
        if vtype in (0x02, 0x04, 0x06):  # SHORT / INT / LONG (signed little-endian)
            val = int.from_bytes(raw, "little", signed=True)
            return val, off
        if vtype == 0x03:    # VALUE_CHAR (unsigned little-endian)
            return int.from_bytes(raw, "little", signed=False), off
        if vtype == 0x10:    # VALUE_FLOAT (right zero-padded auf 4 Bytes)
            buf = (b"\x00" * max(0, 4 - len(raw))) + raw[:4]
            return struct.unpack("<f", buf)[0], off
        if vtype == 0x11:    # VALUE_DOUBLE (right zero-padded auf 8 Bytes)
            buf = (b"\x00" * max(0, 8 - len(raw))) + raw[:8]
            return struct.unpack("<d", buf)[0], off
        if vtype == 0x17:    # VALUE_STRING
            sidx = int.from_bytes(raw, "little", signed=False)
            return ("string", self.string(sidx)), off
        if vtype == 0x18:    # VALUE_TYPE
            tidx = int.from_bytes(raw, "little", signed=False)
            return ("type", self.type(tidx)), off
        if vtype == 0x1C:    # VALUE_ARRAY (rekursiv)
            off -= size
            arr, off = self._decode_encoded_array(off)
            return arr, off
        if vtype == 0x1D:    # VALUE_ANNOTATION (skip)
            off -= size
            _, off = _uleb128(d, off)
            cnt, off = _uleb128(d, off)
            for _ in range(cnt):
                _, off = _uleb128(d, off)
                _, off = self._decode_encoded_value(off)
            return None, off
        return 0, off

    def _decode_encoded_array(self, off):
        """Dekodiert ein DEX `encoded_array` an `off` -> (Liste, neues_off)."""
        if not off:
            return [], off
        cnt, off = _uleb128(self.data, off)
        out = []
        for _ in range(cnt):
            val, off = self._decode_encoded_value(off)
            out.append(val)
        return out, off

    def static_field_values(self, class_def):
        """Liefert `{field_name: initialwert}` aus `class_data.static_fields` + `static_values_off`."""
        cd_off = class_def.get("class_data_off", 0)
        sv_off = class_def.get("static_values_off", 0)
        if not cd_off:
            return {}
        d = self.data
        sfs, p = _uleb128(d, cd_off)
        _, p = _uleb128(d, p)
        _, p = _uleb128(d, p)
        _, p = _uleb128(d, p)
        fields = []
        fidx = 0
        for _ in range(sfs):
            diff, p = _uleb128(d, p)
            _, p = _uleb128(d, p)
            fidx += diff
            _cls, fname, ftype = self.field(fidx)
            fields.append((fname, ftype))
        if not fields:
            return {}
        vals = []
        if sv_off:
            vals, _ = self._decode_encoded_array(sv_off)
        out = {}
        for i, (fname, ftype) in enumerate(fields):
            if i < len(vals):
                out[fname] = vals[i]
            else:
                out[fname] = 0 if (ftype and ftype[0] in "ZBCSIJFD") else None
        return out

    def class_methods(self, class_def):
        """Direkte + virtuelle Methoden einer Klasse als `Method`-Liste."""
        off = class_def.get("class_data_off", 0)
        if not off:
            return []
        d = self.data
        sfs, off = _uleb128(d, off)
        ifs, off = _uleb128(d, off)
        dms, off = _uleb128(d, off)
        vms, off = _uleb128(d, off)
        # encoded_field-Listen ueberspringen
        for _ in range(sfs):
            _, off = _uleb128(d, off); _, off = _uleb128(d, off)
        for _ in range(ifs):
            _, off = _uleb128(d, off); _, off = _uleb128(d, off)
        methods = []
        for group, n in (("direct", dms), ("virtual", vms)):
            midx = 0
            for _ in range(n):
                diff, off = _uleb128(d, off)
                access, off = _uleb128(d, off)
                code_off, off = _uleb128(d, off)
                midx += diff
                cls, name, sig = self.method(midx)
                methods.append(Method(cls, name, sig, access, code_off))
        return methods

    def code_item(self, off):
        """code_item an Offset -> dict mit registers_size/ins_size/outs_size/insns (u16-Liste)."""
        if not off:
            return None
        d = self.data
        registers_size, ins_size, outs_size, tries_size, debug_off, insns_size = \
            struct.unpack_from("<HHHHII", d, off)
        insns = list(struct.unpack_from("<%dH" % insns_size, d, off + 16))
        return {"registers_size": registers_size, "ins_size": ins_size, "outs_size": outs_size,
                "tries_size": tries_size, "insns_size": insns_size, "insns": insns}
