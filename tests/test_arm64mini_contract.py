"""Kontrakt-Test fuer core/native/arm64mini.py.

Sichert die Oberflaeche, auf die die (eingefrorene) lipy-ale-Schicht baut, sowie
goldene Ausfuehrungs-Vektoren. Schlaegt an, wenn die Weiterentwicklung im Studio
den Rueckwaerts-Kontrakt zu lipy bricht (nur ADDITIV erweitern!).

Laedt arm64mini wie ale_pure: per exec() der Datei in einen Namensraum.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AM = os.path.join(ROOT, "core", "native", "arm64mini.py")

_ns = {}
exec(compile(open(AM, encoding="utf-8").read(), AM, "exec"), _ns)


def _load():
    return _ns["Mem"], _ns["CPU"], _ns["M64"]


def _run(code, args=(), base=0x100000, setup=None, maxins=100000):
    # maxins HART beschraenkt: ein fehlerhafter Vektor darf nie haengen.
    Mem, CPU, _ = _load()
    m = Mem(); m.wb(base, bytes(code))
    c = CPU(m, {})
    if setup:
        setup(c, base)
    return c.run(base, tuple(args), maxins), c, m


# ---- Oberflaeche (Modul-Namen) ----
def test_module_exports_names():
    assert "Mem" in _ns and "CPU" in _ns and "M64" in _ns
    assert _ns["M64"] == (1 << 64) - 1


def test_mem_surface():
    Mem, _, _ = _load()
    m = Mem()
    for meth in ("wb", "rb", "w"):
        assert callable(getattr(m, meth)), f"Mem.{meth} fehlt"
    assert hasattr(m, "pages"), "Mem.pages fehlt (Snapshot-Kontrakt)"
    m.wb(0x2000, b"\xde\xad\xbe\xef")
    assert bytes(m.rb(0x2000, 4)) == b"\xde\xad\xbe\xef"


def test_cpu_surface():
    Mem, CPU, _ = _load()
    c = CPU(Mem(), {})
    # Konstruktor-Attribute (am frischen Objekt vorhanden):
    for attr in ("x", "sp", "lr", "m", "bb", "stub", "probes", "cache"):
        assert hasattr(c, attr), f"CPU.{attr} fehlt"
    # pc wird erst in run() gesetzt (ale_pure setzt es in der eigenen Schleife selbst)
    # -> Kontrakt ist: pc ist setz-/lesbar.
    c.pc = 0x1000
    assert c.pc == 0x1000
    assert isinstance(c.x, list) and len(c.x) >= 31
    assert isinstance(c.bb, dict) and isinstance(c.stub, dict) and isinstance(c.probes, dict)
    assert callable(c.run) and callable(c.wr)
    c.wr("x0", 0x1234)
    assert c.x[0] == 0x1234


# ---- Goldene Ausfuehrungs-Vektoren ----
def test_golden_const():
    # movz x0,#0x2a ; ret
    r, _, _ = _run(bytes.fromhex("400580d2") + bytes.fromhex("c0035fd6"))
    assert r == 0x2a


def test_golden_add_args():
    # add x0,x0,x1 ; ret   mit (7,5)
    r, _, _ = _run(bytes.fromhex("0000018b") + bytes.fromhex("c0035fd6"), args=(7, 5))
    assert r == 12


def test_golden_mul():
    # movz x2,#3 ; movz x1,#4 ; mul x0,x2,x1 ; ret
    code = bytes.fromhex("620080d2") + bytes.fromhex("810080d2") + bytes.fromhex("407c019b") + bytes.fromhex("c0035fd6")
    r, _, _ = _run(code)
    assert r == 12


# ---- Protokoll: Probe feuert am Block-Eintritt ----
def test_probe_fires():
    fired = []
    # movz x0,#5 ; ret
    code = bytes.fromhex("a00080d2") + bytes.fromhex("c0035fd6")

    def setup(c, base):
        c.probes[base] = lambda cpu: fired.append(cpu.pc)

    r, _, _ = _run(code, setup=setup)
    assert r == 5 and fired, "Probe am Entry wurde nicht ausgeloest"


# ---- Protokoll: Stub wird konsultiert; Rueckgabe None => lr (hier SENT => Stop) ----
def test_stub_protocol():
    # run() setzt lr=SENT(0xdead0000) und stoppt bei pc==SENT mit Rueckgabe x0.
    # Stub am Entry: setzt x0 und gibt None zurueck => pc=lr=SENT => sofortiger, sauberer Stop.
    hit = []

    def setup(c, b):
        def st(cpu):
            hit.append(1); cpu.wr("x0", 0x42); return None
        c.stub[b] = st   # Stub am Einstiegs-pc

    r, _, _ = _run(bytes.fromhex("c0035fd6"), setup=setup)   # Filler-ret, wird nie erreicht
    assert hit, "Stub am Entry wurde nicht konsultiert"
    assert r == 0x42, f"Stub-Rueckgabe None=>lr/Stop falsch: 0x{r:x}"
