# -*- coding: utf-8 -*-
"""Regression: STOP bricht einen laufenden 'cont' kooperativ ab (RN-App haengt sonst). Headless."""
import threading, time
from core.java.dalvik import DalvikVM
from core.java import smali as Sm
from core.java.debug import JavaDebugger

# Methode mit langer Schleife (1 Mio Iterationen) -> laeuft lange genug zum Abbrechen.
LOOP = """.class public Lt/Loop;
.super Ljava/lang/Object;
.method public static run()I
    .registers 3
    const v0, 0x0
    const v1, 0xf4240
    :loop
    if-ge v0, v1, :done
    add-int/lit8 v0, v0, 0x1
    goto :loop
    :done
    return v0
.end method
"""


def _mk():
    vm = DalvikVM()
    for md in Sm.parse_class(LOOP)[1]:
        vm.register(md)
    return JavaDebugger(vm)


def test_stop_interrupts_running_cont():
    jdb = _mk()
    jdb.start("Lt/Loop;", "run", "()I", (), stop_at_entry=True)
    assert jdb.mode == "paused"
    # cont in einem Thread starten (laeuft die Schleife)
    t = threading.Thread(target=jdb.cont, daemon=True)
    t.start()
    time.sleep(0.2)                      # laeuft gerade
    assert jdb.mode in ("run", "paused", "stopped")
    jdb.stop()                           # Abbruch
    t.join(timeout=5)
    assert not t.is_alive(), "cont-Thread haengt trotz stop"
    assert jdb.mode == "stopped"         # sauber abgebrochen
    # nicht bis zum Schleifenende (1e6) gelaufen
    assert jdb.step_count and jdb.step_count < 1_000_000   # AP1.1: Zaehler statt Timeline


def test_stop_idempotent_when_idle():
    jdb = _mk()
    st = jdb.stop()                      # ohne Lauf: kein Crash
    assert st["mode"] in ("idle", "stopped")
