# -*- coding: utf-8 -*-
"""memfmt — reine Speicher-Darstellung fuer die Memory-Ansicht des Debuggers.
Hexdump (Hex+ASCII) und typisierte Dekodierung (i8..u64/f32/f64/utf8). Zielagnostisch, headless testbar.
"""
from __future__ import annotations

import struct

_FMT = {"i8": "<b", "u8": "<B", "i16": "<h", "u16": "<H", "i32": "<i", "u32": "<I",
        "i64": "<q", "u64": "<Q", "f32": "<f", "f64": "<d"}


def hexdump(data, base_addr=0, width=16):
    """Liste von Zeilen 'ADDR  HEXBYTES  ASCII' (width Bytes je Zeile)."""
    data = bytes(data)
    out = []
    for off in range(0, len(data), width):
        chunk = data[off:off + width]
        hexpart = " ".join("%02x" % b for b in chunk).ljust(width * 3 - 1)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        out.append("%08x  %s  %s" % (base_addr + off, hexpart, asc))
    return out


def decode_typed(data, offset=0, kind="i32"):
    """Einen Wert am Offset als kind dekodieren. utf8 = NUL-terminierter String ab Offset."""
    data = bytes(data)
    if kind == "utf8":
        return data[offset:].split(b"\x00", 1)[0].decode("utf-8", "replace")
    f = _FMT.get(kind)
    if f is None:
        raise ValueError("unbekannter Typ: %s" % kind)
    size = struct.calcsize(f)
    if offset < 0 or offset + size > len(data):
        return None
    return struct.unpack_from(f, data, offset)[0]
