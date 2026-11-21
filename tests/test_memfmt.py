# -*- coding: utf-8 -*-
"""AP-3: Memory-Formatter (Hexdump + typisierte Dekodierung). Headless."""
import struct
from core.util.memfmt import hexdump, decode_typed

def test_hexdump_layout():
    data = bytes(range(16))
    lines = hexdump(data, base_addr=0x1000, width=16)
    assert len(lines) == 1
    assert lines[0].startswith("00001000  ")
    assert "00 01 02 03" in lines[0]
    assert lines[0].endswith("........")   # 0..15 nicht druckbar -> Punkte

def test_hexdump_ascii_and_wrap():
    data = b"ABCD" + bytes([0x00, 0xff]) + b"ef"
    lines = hexdump(data, base_addr=0, width=4)
    assert len(lines) == 2
    assert lines[0].endswith("ABCD")
    assert lines[1].endswith("..ef")

def test_decode_typed_ints():
    data = struct.pack("<iqBH", -5, 123456789, 200, 65535)
    assert decode_typed(data, 0, "i32") == -5
    assert decode_typed(data, 4, "i64") == 123456789
    assert decode_typed(data, 12, "u8") == 200
    assert decode_typed(data, 13, "u16") == 65535

def test_decode_typed_float_and_utf8():
    data = struct.pack("<f", 1.5) + b"hallo\x00rest"
    assert abs(decode_typed(data, 0, "f32") - 1.5) < 1e-6
    assert decode_typed(data, 4, "utf8") == "hallo"

def test_decode_out_of_range():
    assert decode_typed(b"\x01\x02", 0, "i32") is None
