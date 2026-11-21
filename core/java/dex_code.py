# -*- coding: utf-8 -*-
"""core.java.dex_code — DEX-Bytecode-Decoder nach Tupel-IR (J3 / MS-4).

Decodiert ein ``code_item.insns`` (16-Bit-Code-Units) in das Tupel-IR, das `core.java.dalvik.DalvikVM`
ausfuehrt, und loest DEX-Branch-Offsets (in Code-Units) auf Instruktions-Indizes auf. Teilt die
Opcode-Semantik mit dem Smali-Frontend. Bounded: try/catch, switch und fill-array-data werden als
``dex-unsupported:<op>`` markiert (Coverage-Loop; die DalvikVM wirft darauf `DalvikUnknownOpcode`).
Zielagnostisch.
"""
from __future__ import annotations

from core.java.dalvik import MethodDef

# --- Opcode -> Format ---------------------------------------------------------
_FMT = {}
def _set(a, b, fmt):
    for o in range(a, b + 1):
        _FMT[o] = fmt

_FMT[0x00] = "10x"
_FMT[0x01] = "12x"; _FMT[0x02] = "22x"; _FMT[0x03] = "32x"
_FMT[0x04] = "12x"; _FMT[0x05] = "22x"; _FMT[0x06] = "32x"
_FMT[0x07] = "12x"; _FMT[0x08] = "22x"; _FMT[0x09] = "32x"
_set(0x0a, 0x0d, "11x")                          # move-result*/move-exception
_FMT[0x0e] = "10x"                               # return-void
_set(0x0f, 0x11, "11x")                          # return/return-wide/return-object
_FMT[0x12] = "11n"; _FMT[0x13] = "21s"; _FMT[0x14] = "31i"; _FMT[0x15] = "21h"
_FMT[0x16] = "21s"; _FMT[0x17] = "31i"; _FMT[0x18] = "51l"; _FMT[0x19] = "21h"
_FMT[0x1a] = "21c"; _FMT[0x1b] = "31c"; _FMT[0x1c] = "21c"
_FMT[0x1d] = "11x"; _FMT[0x1e] = "11x"           # monitor-enter/exit
_FMT[0x1f] = "21c"                               # check-cast
_FMT[0x20] = "22c"                               # instance-of
_FMT[0x21] = "12x"                               # array-length
_FMT[0x22] = "21c"                               # new-instance
_FMT[0x23] = "22c"                               # new-array
_FMT[0x24] = "35c"; _FMT[0x25] = "3rc"           # filled-new-array
_FMT[0x26] = "31t"                               # fill-array-data
_FMT[0x27] = "11x"                               # throw
_FMT[0x28] = "10t"; _FMT[0x29] = "20t"; _FMT[0x2a] = "30t"
_FMT[0x2b] = "31t"; _FMT[0x2c] = "31t"           # packed/sparse-switch
_set(0x2d, 0x31, "23x")                          # cmp*
_set(0x32, 0x37, "22t")                          # if-eq..if-le
_set(0x38, 0x3d, "21t")                          # if-eqz..if-lez
_set(0x44, 0x51, "23x")                          # aget..aput-short
_set(0x52, 0x5f, "22c")                          # iget..iput-short
_set(0x60, 0x6d, "21c")                          # sget..sput-short
_set(0x6e, 0x72, "35c")                          # invoke-*
_set(0x74, 0x78, "3rc")                          # invoke-*/range
_set(0x7b, 0x8f, "12x")                          # unary ops
_set(0x90, 0xaf, "23x")                          # binop
_set(0xb0, 0xcf, "12x")                          # binop/2addr
_set(0xd0, 0xd7, "22s")                          # binop/lit16
_set(0xd8, 0xe2, "22b")                          # binop/lit8

_LEN = {"10x": 1, "12x": 1, "11n": 1, "11x": 1, "10t": 1,
        "20t": 2, "22x": 2, "21t": 2, "21s": 2, "21h": 2, "21c": 2, "23x": 2,
        "22b": 2, "22t": 2, "22s": 2, "22c": 2,
        "30t": 3, "31i": 3, "31t": 3, "31c": 3, "32x": 3, "35c": 3, "3rc": 3, "51l": 5}

# Opcode -> IR-Name (Arithmetik/Vergleich/Array/Feld/Invoke)
_BINOP = ["add", "sub", "mul", "div", "rem", "and", "or", "xor", "shl", "shr", "ushr"]
_NAMES = {}
_NAMES.update({0x01: "move", 0x02: "move", 0x03: "move", 0x04: "move", 0x05: "move",
               0x06: "move", 0x07: "move", 0x08: "move", 0x09: "move",
               0x0a: "move-result", 0x0b: "move-result", 0x0c: "move-result", 0x0d: "move-exception",
               0x0e: "return-void", 0x0f: "return", 0x10: "return", 0x11: "return-object",
               0x1d: "nop", 0x1e: "nop", 0x21: "array-length"})
_IFV = {0x32: "if-eq", 0x33: "if-ne", 0x34: "if-lt", 0x35: "if-ge", 0x36: "if-gt", 0x37: "if-le"}
_IFZ = {0x38: "if-eqz", 0x39: "if-nez", 0x3a: "if-ltz", 0x3b: "if-gez", 0x3c: "if-gtz", 0x3d: "if-lez"}
_AGET = {0x44: "aget", 0x45: "aget-wide", 0x46: "aget-object", 0x47: "aget-boolean",
         0x48: "aget-byte", 0x49: "aget-char", 0x4a: "aget-short",
         0x4b: "aput", 0x4c: "aput-wide", 0x4d: "aput-object", 0x4e: "aput-boolean",
         0x4f: "aput-byte", 0x50: "aput-char", 0x51: "aput-short"}
_IGET = {0x52: "iget", 0x53: "iget-wide", 0x54: "iget-object", 0x55: "iget-boolean",
         0x56: "iget-byte", 0x57: "iget-char", 0x58: "iget-short",
         0x59: "iput", 0x5a: "iput-wide", 0x5b: "iput-object", 0x5c: "iput-boolean",
         0x5d: "iput-byte", 0x5e: "iput-char", 0x5f: "iput-short"}
_SGET = {0x60: "sget", 0x61: "sget-wide", 0x62: "sget-object", 0x63: "sget-boolean",
         0x64: "sget-byte", 0x65: "sget-char", 0x66: "sget-short",
         0x67: "sput", 0x68: "sput-wide", 0x69: "sput-object", 0x6a: "sput-boolean",
         0x6b: "sput-byte", 0x6c: "sput-char", 0x6d: "sput-short"}
_UNARY = {
    0x7b: "neg-int", 0x7c: "not-int", 0x7d: "neg-long", 0x7e: "not-long", 0x7f: "neg-float",
    0x80: "neg-double", 0x81: "int-to-long", 0x82: "int-to-float", 0x83: "int-to-double",
    0x84: "long-to-int", 0x85: "long-to-float", 0x86: "long-to-double", 0x87: "float-to-int",
    0x88: "float-to-long", 0x89: "float-to-double", 0x8a: "double-to-int", 0x8b: "double-to-long",
    0x8c: "double-to-float", 0x8d: "int-to-byte", 0x8e: "int-to-char", 0x8f: "int-to-short"}
_CMP = {0x2d: "cmpl-float", 0x2e: "cmpg-float", 0x2f: "cmpl-double", 0x30: "cmpg-double", 0x31: "cmp-long"}


def _binop_name(rel):
    if rel < 11:
        return _BINOP[rel] + "-int"
    if rel < 22:
        return _BINOP[rel - 11] + "-long"
    if rel < 27:
        return _BINOP[rel - 22] + "-float"
    return _BINOP[rel - 27] + "-double"
_INVOKE = {0x6e: "invoke-virtual", 0x6f: "invoke-super", 0x70: "invoke-direct",
           0x71: "invoke-static", 0x72: "invoke-interface",
           0x74: "invoke-virtual", 0x75: "invoke-super", 0x76: "invoke-direct",
           0x77: "invoke-static", 0x78: "invoke-interface"}


class DexUnsupported(Exception):
    def __init__(self, op, uoff):
        self.op = op
        self.uoff = uoff
        super().__init__(f"DEX-Opcode 0x{op:02x} nicht unterstuetzt @unit {uoff}")


def _s4(x): return x - 16 if x & 0x8 else x
def _s8(x): return x - 0x100 if x & 0x80 else x
def _s16(x): return x - 0x10000 if x & 0x8000 else x
def _s32(x): return x - (1 << 32) if x & 0x80000000 else x


def _payload_units(insns, idx, ident):
    """Laenge (in Code-Units) eines eingebetteten Payload-Blocks (switch / array-data)."""
    if ident == 0x0100:       # packed-switch-payload
        size = insns[idx + 1]
        return 4 + size * 2
    if ident == 0x0200:       # sparse-switch-payload
        size = insns[idx + 1]
        return 2 + size * 4
    # 0x0300 fill-array-data-payload
    width = insns[idx + 1]
    size = insns[idx + 2] | (insns[idx + 3] << 16)
    return 4 + (size * width + 1) // 2


def decode_code(dex, ci, cls, name, sig, access):
    """code_item + DexFile -> MethodDef (Tupel-IR, Branches aufgeloest)."""
    insns = ci["insns"]
    n = len(insns)
    ir = []
    uoff_to_idx = {}
    branch_fix = []   # (ir_index, target_uoff)
    switch_fix = []   # ir_index von packed/sparse-switch (Ziel-Liste aufloesen)
    idx = 0
    while idx < n:
        u0 = insns[idx]
        if u0 in (0x0100, 0x0200, 0x0300):     # eingebetteter Payload-Block -> ueberspringen
            idx += max(_payload_units(insns, idx, u0), 1)
            continue
        uoff_to_idx[idx] = len(ir)
        op = u0 & 0xff
        fmt = _FMT.get(op)
        if fmt is None:
            ir.append(("dex-unsupported:0x%02x" % op,))
            idx += 1
            continue
        length = _LEN[fmt]
        tup, btgt = _decode_one(dex, op, fmt, insns, idx)
        if btgt is not None:
            branch_fix.append((len(ir), btgt))
        if tup[0] in ("packed-switch", "sparse-switch"):
            switch_fix.append(len(ir))
        ir.append(tup)
        idx += length
    for ir_i, tgt_uoff in branch_fix:
        tgt = uoff_to_idx.get(tgt_uoff)
        if tgt is None:
            tgt = len(ir)   # out-of-range -> faellt aus der Methode (defensive)
        ir[ir_i] = ir[ir_i][:-1] + (tgt,)
    # Switch-Multi-Target (Ziel-Liste am letzten Tupel-Element)
    for ir_i in switch_fix:
        t = ir[ir_i]
        tgts = tuple(uoff_to_idx.get(u, len(ir)) for u in t[-1])
        ir[ir_i] = t[:-1] + (tgts,)
    nregs = ci["registers_size"]
    nargs = ci["ins_size"]
    return MethodDef(cls, name, sig, ir, nregs, nargs, bool(access & 0x8))


def _decode_one(dex, op, fmt, insns, idx):
    """Rueckgabe: (tuple_ir, branch_target_uoff_or_None). Bei Branch ist das letzte Tupel-Element
    voruebergehend das Ziel-Unit-Offset (wird spaeter zum IR-Index)."""
    u0 = insns[idx]
    AA = (u0 >> 8) & 0xff
    A = (u0 >> 8) & 0xf
    B = (u0 >> 12) & 0xf

    nm = _NAMES.get(op)
    if op == 0x00:
        return ("nop",), None
    if nm == "move":
        if fmt == "12x":
            return ("move", A, B), None
        if fmt == "22x":
            return ("move", AA, insns[idx + 1]), None
        if fmt == "32x":
            return ("move", insns[idx + 1], insns[idx + 2]), None
    if nm in ("move-result", "move-exception"):
        return (nm if nm == "move-exception" else "move-result", AA), None
    if op == 0x0e:
        return ("return-void",), None
    if op in (0x0f, 0x10):
        return ("return", AA), None
    if op == 0x11:
        return ("return-object", AA), None
    if op == 0x27:   # throw
        return ("throw", AA), None
    if op in _CMP:   # 23x cmp*
        u1 = insns[idx + 1]
        return (_CMP[op], AA, u1 & 0xff, (u1 >> 8) & 0xff), None
    # Konstanten
    if op == 0x12:   # const/4
        return ("const", A, _s4(B) & 0xffffffff), None
    if op == 0x13:   # const/16
        return ("const", AA, _s16(insns[idx + 1]) & 0xffffffff), None
    if op == 0x14:   # const
        return ("const", AA, (insns[idx + 1] | (insns[idx + 2] << 16)) & 0xffffffff), None
    if op == 0x15:   # const/high16
        return ("const", AA, (insns[idx + 1] << 16) & 0xffffffff), None
    if op == 0x16:   # const-wide/16
        return ("const", AA, _s16(insns[idx + 1]) & ((1 << 64) - 1)), None
    if op == 0x17:   # const-wide/32
        return ("const", AA, _s32(insns[idx + 1] | (insns[idx + 2] << 16)) & ((1 << 64) - 1)), None
    if op == 0x18:   # const-wide (51l)
        v = (insns[idx + 1] | (insns[idx + 2] << 16) | (insns[idx + 3] << 32) | (insns[idx + 4] << 48))
        return ("const", AA, v), None
    if op == 0x19:   # const-wide/high16
        return ("const", AA, (insns[idx + 1] << 48)), None
    if op == 0x1a:   # const-string
        return ("const-string", AA, dex.string(insns[idx + 1])), None
    if op == 0x1b:   # const-string/jumbo
        return ("const-string", AA, dex.string(insns[idx + 1] | (insns[idx + 2] << 16))), None
    if op == 0x1c:   # const-class
        return ("const-class", AA, dex.type(insns[idx + 1])), None
    if op in (0x1d, 0x1e):
        return ("nop",), None
    if op == 0x1f:   # check-cast
        return ("check-cast", AA, dex.type(insns[idx + 1])), None
    if op == 0x20:   # instance-of
        return ("instance-of", A, B, dex.type(insns[idx + 1])), None
    if op == 0x21:   # array-length
        return ("array-length", A, B), None
    if op == 0x22:   # new-instance
        return ("new-instance", AA, dex.type(insns[idx + 1])), None
    if op == 0x23:   # new-array
        return ("new-array", A, B, dex.type(insns[idx + 1])), None
    if op == 0x24:   # filled-new-array (35c)
        cnt = (u0 >> 12) & 0xf
        G = (u0 >> 8) & 0xf
        u2 = insns[idx + 2]
        regs = [u2 & 0xf, (u2 >> 4) & 0xf, (u2 >> 8) & 0xf, (u2 >> 12) & 0xf, G][:cnt]
        return ("filled-new-array", tuple(regs), dex.type(insns[idx + 1])), None
    if op == 0x25:   # filled-new-array/range
        cnt = AA
        first = insns[idx + 2]
        return ("filled-new-array", tuple(range(first, first + cnt)), dex.type(insns[idx + 1])), None
    # Branches
    if op == 0x28:   # goto 10t
        return ("goto", idx + _s8(AA)), (idx + _s8(AA))
    if op == 0x29:   # goto/16 20t
        return ("goto", idx + _s16(insns[idx + 1])), (idx + _s16(insns[idx + 1]))
    if op == 0x2a:   # goto/32 30t
        rel = _s32(insns[idx + 1] | (insns[idx + 2] << 16))
        return ("goto", idx + rel), (idx + rel)
    if op in _IFV:
        rel = _s16(insns[idx + 1])
        return (_IFV[op], A, B, idx + rel), (idx + rel)
    if op in _IFZ:
        rel = _s16(insns[idx + 1])
        return (_IFZ[op], AA, idx + rel), (idx + rel)
    # Arrays (23x)
    if op in _AGET:
        u1 = insns[idx + 1]
        return (_AGET[op], AA, u1 & 0xff, (u1 >> 8) & 0xff), None
    # Instanz-Felder (22c)
    if op in _IGET:
        c, f, _t = dex.field(insns[idx + 1])
        return (_IGET[op], A, B, c, f), None
    # Statische Felder (21c)
    if op in _SGET:
        c, f, _t = dex.field(insns[idx + 1])
        return (_SGET[op], AA, c, f), None
    # Invokes
    if op in _INVOKE:
        cls, name, sig = dex.method(insns[idx + 1])
        if fmt == "35c":
            cnt = (u0 >> 12) & 0xf
            G = (u0 >> 8) & 0xf
            u2 = insns[idx + 2]
            regs = [u2 & 0xf, (u2 >> 4) & 0xf, (u2 >> 8) & 0xf, (u2 >> 12) & 0xf, G][:cnt]
        else:  # 3rc
            first = insns[idx + 2]
            regs = list(range(first, first + AA))
        return (_INVOKE[op], tuple(regs), cls, name, sig), None
    # Unary (12x)
    if op in _UNARY:
        return (_UNARY[op], A, B), None
    # Binop (23x)
    if 0x90 <= op <= 0xaf:
        u1 = insns[idx + 1]
        return (_binop_name(op - 0x90), AA, u1 & 0xff, (u1 >> 8) & 0xff), None
    # Binop/2addr (12x)
    if 0xb0 <= op <= 0xcf:
        return (_binop_name(op - 0xb0) + "/2addr", A, B), None
    # Binop/lit16 (22s)
    if 0xd0 <= op <= 0xd7:
        base = ["add", "rsub", "mul", "div", "rem", "and", "or", "xor"][op - 0xd0]
        return (base + "-int/lit16", A, B, _s16(insns[idx + 1])), None
    # Binop/lit8 (22b)
    if 0xd8 <= op <= 0xe2:
        base = ["add", "rsub", "mul", "div", "rem", "and", "or", "xor", "shl", "shr", "ushr"][op - 0xd8]
        u1 = insns[idx + 1]
        return (base + "-int/lit8", AA, u1 & 0xff, _s8((u1 >> 8) & 0xff)), None
    # fill-array-data (31t) -> Payload lesen
    if op == 0x26:
        rel = _s32(insns[idx + 1] | (insns[idx + 2] << 16))
        p = idx + rel
        width = insns[p + 1]
        size = insns[p + 2] | (insns[p + 3] << 16)
        nunits = (size * width + 1) // 2
        raw = bytearray()
        for i in range(nunits):
            u = insns[p + 4 + i]
            raw.append(u & 0xff); raw.append((u >> 8) & 0xff)
        vals = tuple(int.from_bytes(raw[i * width:(i + 1) * width], "little") for i in range(size))
        return ("fill-array-data", AA, vals), None
    # packed-switch (31t) -> (reg, first_key, [ziel-uoffs])
    if op == 0x2b:
        rel = _s32(insns[idx + 1] | (insns[idx + 2] << 16))
        p = idx + rel
        size = insns[p + 1]
        first_key = _s32(insns[p + 2] | (insns[p + 3] << 16))
        tgts = [idx + _s32(insns[p + 4 + i * 2] | (insns[p + 5 + i * 2] << 16)) for i in range(size)]
        return ("packed-switch", AA, first_key, tgts), None
    # sparse-switch (31t) -> (reg, [keys], [ziel-uoffs])
    if op == 0x2c:
        rel = _s32(insns[idx + 1] | (insns[idx + 2] << 16))
        p = idx + rel
        size = insns[p + 1]
        keys = [_s32(insns[p + 2 + i * 2] | (insns[p + 3 + i * 2] << 16)) for i in range(size)]
        tb = p + 2 + size * 2
        tgts = [idx + _s32(insns[tb + i * 2] | (insns[tb + i * 2 + 1] << 16)) for i in range(size)]
        return ("sparse-switch", AA, keys, tgts), None
    # Rest: markiert (Coverage-Loop)
    return ("dex-unsupported:0x%02x" % op,), None
