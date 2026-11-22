# -*- coding: utf-8 -*-
"""Tests fuer die glaeserne Uhrzeit- & Datumsvorgabe (Virtual Clock / Time-Travel)."""
import datetime as dt
import struct
import tkinter as tk
from unittest.mock import MagicMock

import pytest

from core.java.dalvik import DalvikVM
from core.java.device_profile import DeviceProfile
from core.java.dex_loader import AndroidStubs
from core.java.objects import JObject, JString
from core.java.view_tree import install_view_stubs
from core.java.virtual_clock import (
    attach_virtual_clock_to_native_env,
    reset_virtual_clock_to_host,
    set_virtual_clock,
    shift_virtual_clock,
)
from services.native_emu_service import NativeEmuService
from services.native_os_service import NativeOsService

_M64 = (1 << 64) - 1
_SCRATCH = 0x70000


def _mk_vm(profile=None):
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm, profile=profile)
    vm.runtime = type("_R", (), {"stubs": stubs})()
    stubs.seed_framework_statics(vm)
    tree = install_view_stubs(stubs)

    def _r(cls, name, sig, args=(), static=False):
        ok, res = stubs.resolve(cls, name, sig, list(args), static)
        assert ok, f"Stub nicht aufgeloest: {cls}->{name}{sig}"
        return res

    return vm, stubs, tree, _r


def _svc(cpu, nr, *args):
    for i, a in enumerate(args):
        cpu.x[i] = a & _M64
    cpu.x[8] = nr & _M64
    cpu._svc_hook(cpu)
    return cpu.x[0]


def test_virtual_clock_syncs_all_java_and_android_time_apis():
    vm, stubs, _tree, R = _mk_vm()
    st = set_virtual_clock(stubs, iso_str="2027-06-15 08:30:45", tz_name="UTC", frozen=True)
    expected_ms = 1813048245000  # 2027-06-15T08:30:45Z
    assert st["epoch_ms"] == expected_ms

    # System.currentTimeMillis() eingefroren vs. voranschreitend
    assert R("Ljava/lang/System;", "currentTimeMillis", "()J", (), True) == expected_ms
    assert R("Ljava/lang/System;", "currentTimeMillis", "()J", (), True) == expected_ms
    set_virtual_clock(stubs, frozen=False)
    assert R("Ljava/lang/System;", "currentTimeMillis", "()J", (), True) == expected_ms + 100
    assert R("Ljava/lang/System;", "currentTimeMillis", "()J", (), True) == expected_ms + 200
    set_virtual_clock(stubs, epoch_ms=expected_ms, frozen=True)

    # java.util.Date + SimpleDateFormat
    d_now = JObject("Ljava/util/Date;")
    R("Ljava/util/Date;", "<init>", "()V", (d_now,), False)
    assert R("Ljava/util/Date;", "getTime", "()J", (d_now,), False) == expected_ms

    d_past = JObject("Ljava/util/Date;")
    R("Ljava/util/Date;", "<init>", "(J)V", (d_past, expected_ms - 5000), False)
    assert R("Ljava/util/Date;", "after", "(Ljava/util/Date;)Z", (d_now, d_past), False) == 1
    assert R("Ljava/util/Date;", "before", "(Ljava/util/Date;)Z", (d_past, d_now), False) == 1

    sdf = JObject("Ljava/text/SimpleDateFormat;")
    R("Ljava/text/SimpleDateFormat;", "<init>", "(Ljava/lang/String;)V", (sdf, JString("yyyy-MM-dd HH:mm:ss")), False)
    assert R("Ljava/text/SimpleDateFormat;", "format", "(Ljava/util/Date;)Ljava/lang/String;", (sdf, d_now), False).text == "2027-06-15 08:30:45"

    # java.util.Calendar (0-basierter Monat, Sunday=1..Tuesday=3)
    cal_cls = vm.statics["Ljava/util/Calendar;"]
    cal = R("Ljava/util/Calendar;", "getInstance", "()Ljava/util/Calendar;", (), True)
    assert R("Ljava/util/Calendar;", "get", "(I)I", (cal, cal_cls["YEAR"]), False) == 2027
    assert R("Ljava/util/Calendar;", "get", "(I)I", (cal, cal_cls["MONTH"]), False) == 5      # Juni = 5
    assert R("Ljava/util/Calendar;", "get", "(I)I", (cal, cal_cls["DAY_OF_MONTH"]), False) == 15
    assert R("Ljava/util/Calendar;", "get", "(I)I", (cal, cal_cls["DAY_OF_WEEK"]), False) == cal_cls["TUESDAY"]
    assert R("Ljava/util/Calendar;", "get", "(I)I", (cal, cal_cls["HOUR_OF_DAY"]), False) == 8
    assert R("Ljava/util/Calendar;", "get", "(I)I", (cal, cal_cls["MINUTE"]), False) == 30
    assert R("Ljava/util/Calendar;", "get", "(I)I", (cal, cal_cls["SECOND"]), False) == 45

    R("Ljava/util/Calendar;", "add", "(II)V", (cal, cal_cls["DAY_OF_MONTH"], 20), False)
    assert R("Ljava/util/Calendar;", "get", "(I)I", (cal, cal_cls["MONTH"]), False) == 6      # Juli = 6
    assert R("Ljava/util/Calendar;", "get", "(I)I", (cal, cal_cls["DAY_OF_MONTH"]), False) == 5

    # java.util.TimeZone
    tz = R("Ljava/util/TimeZone;", "getDefault", "()Ljava/util/TimeZone;", (), True)
    assert R("Ljava/util/TimeZone;", "getID", "()Ljava/lang/String;", (tz,), False).text == "UTC"

    # java.time.* + android.text.format.DateFormat
    inst = R("Ljava/time/Instant;", "now", "()Ljava/time/Instant;", (), True)
    assert R("Ljava/time/Instant;", "toEpochMilli", "()J", (inst,), False) == expected_ms

    ld = R("Ljava/time/LocalDate;", "now", "()Ljava/time/LocalDate;", (), True)
    assert R("Ljava/time/LocalDate;", "toString", "()Ljava/lang/String;", (ld,), False).text == "2027-06-15"

    lt = R("Ljava/time/LocalTime;", "now", "()Ljava/time/LocalTime;", (), True)
    assert R("Ljava/time/LocalTime;", "toString", "()Ljava/lang/String;", (lt,), False).text.startswith("08:30:45")

    ldt = R("Ljava/time/LocalDateTime;", "now", "()Ljava/time/LocalDateTime;", (), True)
    assert R("Ljava/time/LocalDateTime;", "getYear", "()I", (ldt,), False) == 2027
    assert R("Ljava/time/LocalDateTime;", "getHour", "()I", (ldt,), False) == 8

    zdt = R("Ljava/time/ZonedDateTime;", "now", "()Ljava/time/ZonedDateTime;", (), True)
    assert R("Ljava/time/ZonedDateTime;", "getDayOfMonth", "()I", (zdt,), False) == 15

    fmt_android = R(
        "Landroid/text/format/DateFormat;",
        "format",
        "(Ljava/lang/CharSequence;J)Ljava/lang/CharSequence;",
        (JString("dd.MM.yyyy HH:mm"), expected_ms),
        True,
    )
    assert fmt_android.text == "15.06.2027 08:30"


def test_virtual_clock_time_travel_status_bar_and_device_profile():
    prof = DeviceProfile(clock_iso="2028-02-29 23:55:00", clock_tz="Europe/Berlin", clock_frozen=True)
    _vm, stubs, tree, R = _mk_vm(profile=prof)

    assert tree._stubs_ref is stubs
    assert stubs.format_status_clock() == "29.02.2028 23:55 ❄️"

    shift_virtual_clock(stubs, minutes=10)
    assert stubs.format_status_clock() == "01.03.2028 00:05 ❄️"
    ld = R("Ljava/time/LocalDate;", "now", "()Ljava/time/LocalDate;", (), True)
    assert R("Ljava/time/LocalDate;", "toString", "()Ljava/lang/String;", (ld,), False).text == "2028-03-01"

    reset_virtual_clock_to_host(stubs)
    assert stubs._clock_custom is False
    status_txt = stubs.format_status_clock()
    assert len(status_txt) == 5 and status_txt[2] == ":"


def test_virtual_clock_syncs_native_arm64_realtime_without_breaking_monotonic():
    _vm, stubs, _tree, _R = _mk_vm()
    set_virtual_clock(stubs, iso_str="2029-01-01 12:00:00", tz_name="UTC", frozen=True)
    expected_sec = 1861963200  # 2029-01-01T12:00:00Z

    eng = NativeEmuService(publish=False).new_engine()
    os_svc = NativeOsService(publish=False)
    os_svc.attach_env(eng, clock_start_ns=1000, clock_step_ns=5)
    os_svc.install(eng)
    attach_virtual_clock_to_native_env(stubs, eng)
    cpu = eng.cpu

    ts = _SCRATCH
    # CLOCK_REALTIME (0) nutzt virtuelle Wanduhrzeit
    assert _svc(cpu, 113, 0, ts) == 0
    sec_rt, nsec_rt = struct.unpack_from("<QQ", bytes(cpu.m.rb(ts, 16)))
    assert sec_rt == expected_sec
    assert nsec_rt == 0

    # gettimeofday (169) nutzt ebenfalls virtuelle Wanduhrzeit
    assert _svc(cpu, 169, ts, 0) == 0
    sec_tod, usec_tod = struct.unpack_from("<QQ", bytes(cpu.m.rb(ts, 16)))
    assert (sec_tod, usec_tod) == (expected_sec, 0)

    # CLOCK_MONOTONIC (1) bleibt deterministisch auf env.clock_ns()
    assert _svc(cpu, 113, 1, ts) == 0
    assert struct.unpack_from("<QQ", bytes(cpu.m.rb(ts, 16))) == (0, 1000)
    assert _svc(cpu, 113, 1, ts) == 0
    assert struct.unpack_from("<QQ", bytes(cpu.m.rb(ts, 16))) == (0, 1005)


def test_seams_panel_and_controller_preset():
    from ui.controllers.debugger_controller import DebuggerController
    from ui.widgets.seams_inspector_panel import SeamsInspectorPanel

    class _Var:
        def __init__(self, val):
            self._v = val

        def get(self):
            return self._v

        def set(self, v):
            self._v = v

    # 1) Headless Preset + Live-Clock-Logik von SeamsInspectorPanel + DebuggerController
    _vm, stubs, _tree, _R = _mk_vm()
    ctrl = type("_Ctrl", (), {"_stubs": None, "_runner": None, "app": None})()
    fake_panel = type("_P", (), {
        "ctrl": ctrl,
        "clock_iso_var": _Var("2030-05-10 14:00:00"),
        "clock_tz_var": _Var("UTC"),
        "clock_freeze_var": _Var(True),
        "status_lbl": MagicMock(),
        "_get_stubs_runner": lambda self: (self.ctrl._stubs, self.ctrl._runner),
        "_refresh_screens": lambda self: None,
    })()

    # Vor App-Start: Preset wird auf ctrl vorgemerkt
    st_pre = SeamsInspectorPanel.apply_clock(fake_panel)
    assert st_pre["iso_str"] == "2030-05-10 14:00:00"
    assert ctrl._clock_preset == {"iso_str": "2030-05-10 14:00:00", "tz_name": "UTC", "frozen": True}

    # Bei App-Start wendet DebuggerController das Preset exakt an
    stubs.set_profile(DeviceProfile(package="com.example.trial"), vm=_vm)
    set_virtual_clock(stubs, **ctrl._clock_preset)
    assert stubs.format_status_clock() == "10.05.2030 14:00 ❄️"

    # Live im Lauf vorspulen (+1 Tag, +1 Std) & Reset auf Jetzt
    ctrl._stubs = stubs
    st_day = SeamsInspectorPanel.shift_clock(fake_panel, days=1)
    assert st_day["iso"] == "2030-05-11 14:00:00"
    st_hr = SeamsInspectorPanel.shift_clock(fake_panel, hours=1)
    assert st_hr["iso"] == "2030-05-11 15:00:00"
    assert stubs.format_status_clock() == "11.05.2030 15:00 ❄️"

    st_now = SeamsInspectorPanel.reset_clock_now(fake_panel)
    assert ctrl._clock_preset is None
    assert fake_panel.clock_freeze_var.get() is False
    assert st_now["custom"] is False
    assert hasattr(DebuggerController, "_build_vm")

    # 2) Echter Tk-Widget-Test (falls Tcl/Tk auf Host vorhanden)
    try:
        tk_root = tk.Tk()
        tk_root.withdraw()
    except tk.TclError:
        return
    try:
        ctrl2 = type("_Ctrl2", (), {"_stubs": stubs, "_runner": None, "app": None})()
        panel = SeamsInspectorPanel(tk_root, ctrl2)
        panel.clock_iso_var.set("2031-07-20 09:15:00")
        panel.clock_tz_var.set("Europe/Berlin")
        panel.clock_freeze_var.set(True)
        res = panel.apply_clock()
        assert res["frozen"] is True
        assert stubs.format_status_clock() == "20.07.2031 09:15 ❄️"
    finally:
        tk_root.destroy()
