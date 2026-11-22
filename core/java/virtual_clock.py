# -*- coding: utf-8 -*-
"""core.java.virtual_clock — Einheitliche, glaeserne Uhrzeit- & Datumsvorgabe (Virtual Clock).

Bindet Dalvik/Java-Zeit-APIs (System.currentTimeMillis, java.util.Date, Calendar/GregorianCalendar,
TimeZone, SimpleDateFormat/DateFormat, java.time.*, android.text.format.DateFormat), den nativen
ARM64-Layer (clock_gettime(CLOCK_REALTIME), gettimeofday) und die Display-Statusleiste an EINE
gemeinsame, im Debugger frei steuerbare virtuelle Uhr an.
"""
from __future__ import annotations

import calendar as _pycal
from datetime import date as _date, datetime as _datetime, time as _time_dt
from datetime import timedelta as _timedelta, timezone as _timezone, tzinfo as _tzinfo
import re as _re
import time as _time
from typing import Any, Optional

from core.java.objects import JObject, JString

_DE_WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
_DE_WEEKDAYS_SHORT = ("Mo.", "Di.", "Mi.", "Do.", "Fr.", "Sa.", "So.")
_DE_WEEKDAYS_2 = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
_EN_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_EN_WEEKDAYS_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_EN_WEEKDAYS_2 = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
_DE_MONTHS = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)
_EN_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

_TZ_OFF_RE = _re.compile(r"^(?:UTC|GMT)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?$", _re.IGNORECASE)
_TOKEN_RE = _re.compile(
    r"'([^']*)'|cccccc|ccccc|cccc|ccc|cc|c|EEEE|EEE|EE|E|LLLL|LLL|LL|L|MMMM|MMM|MM|M|yyyy|yyy|yy|y|dd|d|HH|H|hh|h|mm|m|ss|s|SSS|a"
)

_EU_DST_ZONES = frozenset({
    "Europe/Berlin", "Europe/Vienna", "Europe/Zurich", "Europe/Paris",
    "Europe/Rome", "Europe/Madrid", "Europe/Amsterdam", "Europe/Brussels",
    "CET", "MET", "Europe/Prague", "Europe/Warsaw",
})
_FIXED_ZONES = {
    "America/New_York": -5,
    "US/Eastern": -5,
    "America/Chicago": -6,
    "America/Denver": -7,
    "America/Los_Angeles": -8,
    "US/Pacific": -8,
    "Asia/Tokyo": 9,
    "Asia/Shanghai": 8,
    "Asia/Seoul": 9,
    "Europe/London": 0,
    "Europe/Moscow": 3,
}


def _last_sunday(year: int, month: int) -> _date:
    last_day = _pycal.monthrange(year, month)[1]
    d = _date(year, month, last_day)
    offset = (d.weekday() + 1) % 7   # Sunday=6 -> (6+1)%7 = 0
    return d - _timedelta(days=offset)


class _EuCentralTz(_tzinfo):
    """Pure-Python CET/CEST-Fallback falls Windows kein `tzdata`-Paket besitzt."""

    def __init__(self, zone_name: str = "Europe/Berlin"):
        self._name = zone_name

    def _bounds_utc(self, year: int):
        mar = _datetime.combine(_last_sunday(year, 3), _time_dt(1, 0))
        oct_ = _datetime.combine(_last_sunday(year, 10), _time_dt(1, 0))
        return mar, oct_

    def dst(self, dt: Optional[_datetime]) -> _timedelta:
        if dt is None:
            return _timedelta(0)
        naive = dt.replace(tzinfo=None)
        mar_utc, oct_utc = self._bounds_utc(naive.year)
        dst_start_wall = mar_utc + _timedelta(hours=1)  # 02:00 CET -> 03:00 CEST
        dst_end_wall = oct_utc + _timedelta(hours=2)    # 03:00 CEST -> 02:00 CET
        if dst_start_wall <= naive < dst_end_wall:
            return _timedelta(hours=1)
        return _timedelta(0)

    def utcoffset(self, dt: Optional[_datetime]) -> _timedelta:
        return _timedelta(hours=1) + self.dst(dt)

    def tzname(self, dt: Optional[_datetime]) -> str:
        return "CEST" if self.dst(dt) else "CET"

    def fromutc(self, dt: _datetime) -> _datetime:
        naive_utc = dt.replace(tzinfo=None)
        mar_utc, oct_utc = self._bounds_utc(naive_utc.year)
        off = _timedelta(hours=2) if (mar_utc <= naive_utc < oct_utc) else _timedelta(hours=1)
        return (dt + off).replace(tzinfo=self)


def resolve_tzinfo(tz_name: Optional[str] = None) -> _tzinfo:
    """Loest eine Java/IANA-Zeitzone robust nach `tzinfo` auf (auch auf Windows ohne `tzdata`)."""
    if not tz_name:
        return _datetime.now().astimezone().tzinfo or _timezone.utc
    name = str(tz_name).strip()
    if not name or name.lower() in ("system", "local", "default"):
        return _datetime.now().astimezone().tzinfo or _timezone.utc
    up = name.upper()
    if up in ("UTC", "GMT", "Z", "ETC/UTC", "ETC/GMT"):
        return _timezone.utc
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        pass
    m = _TZ_OFF_RE.match(name)
    if m:
        sign = 1 if m.group(1) == "+" else -1
        hrs = int(m.group(2))
        mins = int(m.group(3) or 0)
        return _timezone(sign * _timedelta(hours=hrs, minutes=mins), name=name)
    if name in _EU_DST_ZONES:
        return _EuCentralTz(name)
    if name in _FIXED_ZONES:
        return _timezone(_timedelta(hours=_FIXED_ZONES[name]), name=name)
    return _datetime.now().astimezone().tzinfo or _timezone.utc


def parse_datetime_spec(spec: Any, tz_name: Optional[str] = None) -> int:
    """Parst ISO/DE-Datumsangaben oder Epoch-Werte zu Epoch-Millisekunden."""
    if isinstance(spec, (int, float)) and not isinstance(spec, bool):
        val = float(spec)
        return int(round(val if abs(val) >= 1e11 else val * 1000.0))
    s = str(spec or "").strip()
    if not s:
        raise ValueError("Leere Zeitangabe")
    if _re.fullmatch(r"[+-]?\d+(?:\.\d+)?", s):
        val = float(s)
        return int(round(val if abs(val) >= 1e11 else val * 1000.0))
    dt_obj: Optional[_datetime] = None
    iso_cand = s[:-1] + "+00:00" if s.endswith("Z") else s
    try:
        dt_obj = _datetime.fromisoformat(iso_cand)
    except ValueError:
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y %H:%M",
            "%d.%m.%Y",
        ):
            try:
                dt_obj = _datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
    if dt_obj is None:
        raise ValueError(f"Ungueltiges Datums-/Zeitformat: {s!r}")
    if dt_obj.tzinfo is None:
        dt_obj = dt_obj.replace(tzinfo=resolve_tzinfo(tz_name))
    return int(round(dt_obj.timestamp() * 1000.0))


def _s64(v: Any) -> int:
    iv = int(v or 0) & 0xFFFFFFFFFFFFFFFF
    return iv - 0x10000000000000000 if (iv & 0x8000000000000000) else iv


def _s32(v: Any) -> int:
    iv = int(v or 0) & 0xFFFFFFFF
    return iv - 0x100000000 if (iv & 0x80000000) else iv


def format_java_pattern(dt_or_d: Any, pattern: str, locale_obj: Any = None) -> str:
    """Formatiert `date`/`datetime`/`time` nach Java SimpleDateFormat/DateTimeFormatter-Mustern."""
    pat = pattern or "yyyy-MM-dd"
    lang = ""
    if isinstance(locale_obj, JObject):
        lang = str(locale_obj.fields.get("lang") or "").lower()
    w_long = _EN_WEEKDAYS if lang == "en" else _DE_WEEKDAYS
    w_short = _EN_WEEKDAYS_SHORT if lang == "en" else _DE_WEEKDAYS_SHORT
    months = _EN_MONTHS if lang == "en" else _DE_MONTHS
    w_2 = _EN_WEEKDAYS_2 if lang == "en" else _DE_WEEKDAYS_2

    yr = getattr(dt_or_d, "year", 1970)
    mo = getattr(dt_or_d, "month", 1)
    dy = getattr(dt_or_d, "day", 1)
    hr = getattr(dt_or_d, "hour", 0)
    mn = getattr(dt_or_d, "minute", 0)
    sc = getattr(dt_or_d, "second", 0)
    ms_part = getattr(dt_or_d, "microsecond", 0) // 1000
    wd = dt_or_d.weekday() if hasattr(dt_or_d, "weekday") else 0

    def _sub(m):
        if m.group(1) is not None:
            return m.group(1)
        tok = m.group(0)
        if tok in ("EEEE", "cccc"):
            return w_long[wd]
        if tok in ("cccccc", "cc", "c"):
            return w_2[wd]
        if tok == "ccccc":
            return w_2[wd][:1]
        if tok in ("EEE", "EE", "E", "ccc"):
            return w_short[wd]
        if tok in ("MMMM", "LLLL"):
            return months[mo - 1]
        if tok in ("MMM", "LLL"):
            return months[mo - 1][:3]
        if tok in ("yyyy", "yyy", "y"):
            return f"{yr:04d}"
        if tok == "yy":
            return f"{yr % 100:02d}"
        if tok in ("MM", "LL"):
            return f"{mo:02d}"
        if tok in ("M", "L"):
            return str(mo)
        if tok == "dd":
            return f"{dy:02d}"
        if tok == "d":
            return str(dy)
        if tok == "HH":
            return f"{hr:02d}"
        if tok == "H":
            return str(hr)
        if tok == "hh":
            return f"{(hr % 12) or 12:02d}"
        if tok == "h":
            return str((hr % 12) or 12)
        if tok == "mm":
            return f"{mn:02d}"
        if tok == "m":
            return str(mn)
        if tok == "ss":
            return f"{sc:02d}"
        if tok == "s":
            return str(sc)
        if tok == "SSS":
            return f"{ms_part:03d}"
        if tok == "a":
            return "AM" if hr < 12 else "PM"
        return tok

    return _TOKEN_RE.sub(_sub, pat)


def _lookup_month_name(tok_val: str) -> Optional[int]:
    cand = (tok_val or "").strip().rstrip(".").lower()
    if not cand:
        return None
    for tbl in (_EN_MONTHS, _DE_MONTHS):
        for idx, mname in enumerate(tbl, start=1):
            low = mname.lower()
            if cand == low or (len(cand) >= 3 and low.startswith(cand[:3])):
                return idx
    return None


def _parse_java_pattern_strict(text: str, pattern: str) -> Optional[_datetime]:
    """Parst Datums-/Zeitstrings locale-unabhaengig nach Java SimpleDateFormat/DateTimeFormatter-Mustern."""
    s = str(text or "").strip()
    pat = str(pattern or "").strip()
    if not s or not pat:
        return None
    parts = ["^"]
    groups: list[tuple[str, str]] = []
    pos = 0
    for idx, m in enumerate(_TOKEN_RE.finditer(pat)):
        if m.start() > pos:
            parts.append(_re.escape(pat[pos:m.start()]))
        if m.group(1) is not None:
            lit = m.group(1)
            parts.append(_re.escape("'" if lit == "" else lit))
        else:
            tok = m.group(0)
            gname = f"g{idx}"
            if tok in ("EEEE", "EEE", "EE", "E"):
                parts.append(r"[A-Za-z\u00c4\u00d6\u00dc\u00e4\u00f6\u00fc\u00df]+\.?")
            elif tok == "yyyy":
                parts.append(f"(?P<{gname}>\\d{{4}})")
                groups.append((gname, tok))
            elif tok == "yy":
                parts.append(f"(?P<{gname}>\\d{{2}})")
                groups.append((gname, tok))
            elif tok in ("MMMM", "MMM"):
                parts.append(f"(?P<{gname}>[A-Za-z\\u00c4\\u00d6\\u00dc\\u00e4\\u00f6\\u00fc\\u00df]+\\.?)")
                groups.append((gname, tok))
            elif tok in ("MM", "M", "dd", "d", "HH", "H", "hh", "h", "mm", "m", "ss", "s"):
                parts.append(f"(?P<{gname}>\\d{{1,2}})")
                groups.append((gname, tok))
            elif tok == "SSS":
                parts.append(f"(?P<{gname}>\\d{{1,3}})")
                groups.append((gname, tok))
            elif tok == "a":
                parts.append(f"(?P<{gname}>AM|PM|am|pm)")
                groups.append((gname, tok))
            else:
                parts.append(_re.escape(tok))
        pos = m.end()
    if pos < len(pat):
        parts.append(_re.escape(pat[pos:]))
    parts.append("$")
    if not groups:
        return None
    m_rx = _re.fullmatch("".join(parts), s)
    if m_rx is None:
        return None
    yr, mo, dy, hr, mn, sc, us = 1970, 1, 1, 0, 0, 0, 0
    used_12h = False
    ampm = None
    for gname, tok in groups:
        val = m_rx.group(gname)
        if tok == "yyyy":
            yr = int(val)
        elif tok == "yy":
            v2 = int(val)
            yr = (2000 + v2) if v2 < 70 else (1900 + v2)
        elif tok in ("MMMM", "MMM"):
            m_idx = _lookup_month_name(val)
            if m_idx is None:
                return None
            mo = m_idx
        elif tok in ("MM", "M"):
            mo = int(val)
        elif tok in ("dd", "d"):
            dy = int(val)
        elif tok in ("HH", "H"):
            hr = int(val)
        elif tok in ("hh", "h"):
            used_12h = True
            hr = int(val)
        elif tok in ("mm", "m"):
            mn = int(val)
        elif tok in ("ss", "s"):
            sc = int(val)
        elif tok == "SSS":
            us = int(val.ljust(3, "0")) * 1000
        elif tok == "a":
            ampm = val.upper()
    if used_12h and ampm:
        hr = (hr % 12) + (12 if ampm == "PM" else 0)
    try:
        return _datetime(yr, mo, dy, hr, mn, sc, us)
    except ValueError:
        return None


def parse_java_pattern(text: str, pattern: str, locale_obj: Any = None) -> _datetime:
    """Parst Zeitstrings nach Java-Pattern mit ISO- und DE-Fallback (locale-unabhaengig)."""
    _ = locale_obj
    dt_strict = _parse_java_pattern_strict(text, pattern)
    if dt_strict is not None:
        return dt_strict
    s = str(text or "").strip()
    if s:
        try:
            iso = s[:-1] + "+00:00" if s.endswith("Z") else s
            dt_iso = _datetime.fromisoformat(iso)
            if dt_iso.tzinfo is not None:
                dt_iso = dt_iso.astimezone(_timezone.utc).replace(tzinfo=None)
            return dt_iso
        except Exception:
            pass
        ms = parse_datetime_spec(s, "UTC")
        return _datetime.fromtimestamp(ms / 1000.0, tz=_timezone.utc).replace(tzinfo=None)
    raise ValueError(f"Ungueltiges Datums-/Zeitformat: {text!r}")


def _touch_view_tree(stubs: Any) -> None:
    vt = getattr(stubs, "view_tree", None)
    if vt is not None and hasattr(vt, "_touch"):
        try:
            vt._touch()
        except Exception:
            pass


def get_virtual_clock_state(stubs: Any) -> dict[str, Any]:
    """Liefert den aktuellen Zustand der virtuellen Uhr auf `stubs`."""
    ms = int(stubs.current_time_ms(advance=False)) if hasattr(stubs, "current_time_ms") else int(getattr(stubs, "_clock_ms", 0))
    tz_name = getattr(stubs, "_clock_tz", None)
    dt = stubs.current_datetime() if hasattr(stubs, "current_datetime") else _datetime.fromtimestamp(ms / 1000.0)
    return {
        "epoch_ms": ms,
        "iso": dt.strftime("%Y-%m-%d %H:%M:%S"),
        "tz": tz_name or "System",
        "frozen": bool(getattr(stubs, "_clock_frozen", False)),
        "custom": bool(getattr(stubs, "_clock_custom", False)),
    }


def set_virtual_clock(
    stubs: Any,
    *,
    epoch_ms: Optional[int] = None,
    iso_str: Optional[str] = None,
    tz_name: Optional[str] = None,
    frozen: Optional[bool] = None,
    step_ms: Optional[int] = None,
) -> dict[str, Any]:
    """Setzt Datum, Uhrzeit, Zeitzone und Freeze-Modus der virtuellen Uhr."""
    if tz_name is not None:
        cleaned = str(tz_name).strip()
        stubs._clock_tz = None if (not cleaned or cleaned.lower() == "system") else cleaned
    if epoch_ms is not None:
        stubs._clock_ms = int(epoch_ms)
        stubs._clock_custom = True
    elif iso_str is not None and str(iso_str).strip():
        stubs._clock_ms = parse_datetime_spec(iso_str, getattr(stubs, "_clock_tz", None))
        stubs._clock_custom = True
    if frozen is not None:
        stubs._clock_frozen = bool(frozen)
        if stubs._clock_frozen:
            stubs._clock_custom = True
    if step_ms is not None:
        stubs._clock_step_ms = max(0, int(step_ms))
    stubs._clock_offset_ms = int(getattr(stubs, "_clock_ms", 0)) - int(_time.time() * 1000)
    _touch_view_tree(stubs)
    return get_virtual_clock_state(stubs)


def shift_virtual_clock(
    stubs: Any,
    *,
    delta_ms: int = 0,
    days: float = 0.0,
    hours: float = 0.0,
    minutes: float = 0.0,
    seconds: float = 0.0,
) -> dict[str, Any]:
    """Verschiebt die virtuelle Uhr um das angegebene Delta (Vor- oder Zurueckspulen)."""
    total_ms = int(delta_ms + round(((days * 86400.0) + (hours * 3600.0) + (minutes * 60.0) + seconds) * 1000.0))
    cur = int(stubs.current_time_ms(advance=False)) if hasattr(stubs, "current_time_ms") else int(getattr(stubs, "_clock_ms", 0))
    stubs._clock_ms = cur + total_ms
    stubs._clock_custom = True
    stubs._clock_offset_ms = int(stubs._clock_ms) - int(_time.time() * 1000)
    _touch_view_tree(stubs)
    return get_virtual_clock_state(stubs)


def reset_virtual_clock_to_host(stubs: Any, *, tz_name: Optional[str] = None) -> dict[str, Any]:
    """Setzt die virtuelle Uhr auf die aktuelle Host-Zeit zurueck."""
    stubs._clock_ms = int(_time.time() * 1000)
    stubs._clock_tz = tz_name
    stubs._clock_frozen = False
    stubs._clock_custom = False
    stubs._clock_offset_ms = 0
    _touch_view_tree(stubs)
    return get_virtual_clock_state(stubs)


def attach_virtual_clock_to_native_env(stubs: Any, eng: Any) -> None:
    """Verbindet Native ARM64 `CLOCK_REALTIME`/`gettimeofday` mit `stubs.current_time_ms`."""
    if stubs is None or eng is None or not hasattr(stubs, "current_time_ms"):
        return
    fn = lambda _st=stubs: int(_st.current_time_ms(advance=False)) * 1_000_000
    for env in (getattr(eng, "env", None), getattr(getattr(eng, "_os", None), "env", None)):
        if env is not None:
            env._realtime_ns_fn = fn


def seed_calendar_statics(vm: Any) -> None:
    """Seedet `java.util.Calendar`/`GregorianCalendar`-Konstanten und `ZoneOffset.UTC`."""
    cal_consts = {
        "ERA": 0, "YEAR": 1, "MONTH": 2, "WEEK_OF_YEAR": 3, "WEEK_OF_MONTH": 4,
        "DATE": 5, "DAY_OF_MONTH": 5, "DAY_OF_YEAR": 6, "DAY_OF_WEEK": 7,
        "DAY_OF_WEEK_IN_MONTH": 8, "AM_PM": 9, "HOUR": 10, "HOUR_OF_DAY": 11,
        "MINUTE": 12, "SECOND": 13, "MILLISECOND": 14, "ZONE_OFFSET": 15, "DST_OFFSET": 16,
        "FIELD_COUNT": 17,
        "SUNDAY": 1, "MONDAY": 2, "TUESDAY": 3, "WEDNESDAY": 4,
        "THURSDAY": 5, "FRIDAY": 6, "SATURDAY": 7,
        "JANUARY": 0, "FEBRUARY": 1, "MARCH": 2, "APRIL": 3, "MAY": 4, "JUNE": 5,
        "JULY": 6, "AUGUST": 7, "SEPTEMBER": 8, "OCTOBER": 9, "NOVEMBER": 10,
        "DECEMBER": 11, "UNDECIMBER": 12,
        "AM": 0, "PM": 1,
        "ALL_STYLES": 0, "SHORT": 1, "LONG": 2,
    }
    for cls_name in ("Ljava/util/Calendar;", "Ljava/util/GregorianCalendar;"):
        vm.statics.setdefault(cls_name, {}).update(cal_consts)
    vm.statics.setdefault("Ljava/text/DateFormat;", {}).update({
        "FULL": 0, "LONG": 1, "MEDIUM": 2, "SHORT": 3, "DEFAULT": 2,
    })
    vm.statics.setdefault("Ljava/time/ZoneOffset;", {}).setdefault(
        "UTC", JObject("Ljava/time/ZoneOffset;", {"__id": "UTC"})
    )
    loc_st = vm.statics.setdefault("Ljava/util/Locale;", {})
    for lk, lang, ctry in (
        ("US", "en", "US"), ("UK", "en", "GB"), ("ENGLISH", "en", ""),
        ("GERMAN", "de", ""), ("GERMANY", "de", "DE"), ("ROOT", "en", ""),
    ):
        loc_st.setdefault(lk, JObject("Ljava/util/Locale;", {"lang": lang, "country": ctry}))


def _add_months_clamped(dt: _datetime, delta_months: int) -> _datetime:
    total = (dt.year * 12 + (dt.month - 1)) + int(delta_months)
    ny, nm0 = divmod(total, 12)
    ny = max(1, min(9999, ny))
    nm = nm0 + 1
    nd = min(dt.day, _pycal.monthrange(ny, nm)[1])
    return dt.replace(year=ny, month=nm, day=nd)


def _obj_epoch_ms(stubs: Any, obj: Any, default_now: bool = False) -> int:
    if isinstance(obj, JObject):
        if "__ms" in obj.fields:
            return int(obj.fields.get("__ms") or 0)
        if "__epoch_ms" in obj.fields:
            return int(obj.fields.get("__epoch_ms") or 0)
        if "__epoch_sec" in obj.fields:
            return int(obj.fields.get("__epoch_sec") or 0) * 1000
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        return int(obj)
    return int(stubs.current_time_ms(advance=False)) if default_now else 0


def install_virtual_clock_stubs(stubs: Any) -> None:
    """Registriert Calendar, GregorianCalendar, TimeZone, Date/DateFormat, java.time.* & SystemClock."""
    R = stubs.register

    def _eff_tz_name(explicit_tz: Optional[str] = None) -> Optional[str]:
        if explicit_tz:
            return explicit_tz
        return getattr(stubs, "_clock_tz", None)

    def _dt_from_ms(ms: int, tz_name: Optional[str] = None) -> _datetime:
        eff = _eff_tz_name(tz_name)
        if eff:
            return _datetime.fromtimestamp(ms / 1000.0, tz=resolve_tzinfo(eff))
        return _datetime.fromtimestamp(ms / 1000.0)

    def _ms_from_dt(dt: _datetime, tz_name: Optional[str] = None, ms_rem: int = 0) -> int:
        eff = _eff_tz_name(tz_name)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=resolve_tzinfo(eff)) if eff else dt.astimezone()
        return (int(dt.timestamp()) * 1000) + (int(ms_rem) % 1000)

    def _tz_obj(tz_id: Optional[str] = None) -> JObject:
        return JObject("Ljava/util/TimeZone;", {"__id": tz_id or getattr(stubs, "_clock_tz", None) or "Europe/Berlin"})

    # --- java.util.TimeZone ---
    TZ = "Ljava/util/TimeZone;"
    R(TZ, "getDefault", "()Ljava/util/TimeZone;", lambda a: _tz_obj())
    R(TZ, "setDefault", "(Ljava/util/TimeZone;)V",
      lambda a: setattr(stubs, "_clock_tz", a[0].fields.get("__id") if a and isinstance(a[0], JObject) else None))
    R(TZ, "getTimeZone", "(Ljava/lang/String;)Ljava/util/TimeZone;",
      lambda a: _tz_obj((stubs._txt(a[0]) or "UTC") if a else "UTC"))
    R(TZ, "getID", "()Ljava/lang/String;",
      lambda a: JString(str(a[0].fields.get("__id") or "Europe/Berlin") if a and isinstance(a[0], JObject) else "Europe/Berlin"))
    R(TZ, "getDisplayName", "()Ljava/lang/String;",
      lambda a: JString(str(a[0].fields.get("__id") or "Europe/Berlin") if a and isinstance(a[0], JObject) else "Europe/Berlin"))
    R(TZ, "getDisplayName", "(ZI)Ljava/lang/String;",
      lambda a: JString(str(a[0].fields.get("__id") or "Europe/Berlin") if a and isinstance(a[0], JObject) else "Europe/Berlin"))
    R(TZ, "getDisplayName", "(Ljava/util/Locale;)Ljava/lang/String;",
      lambda a: JString(str(a[0].fields.get("__id") or "Europe/Berlin") if a and isinstance(a[0], JObject) else "Europe/Berlin"))

    def _tz_offset_ms(a, at_epoch_ms: Optional[int] = None) -> int:
        tz_id = a[0].fields.get("__id") if a and isinstance(a[0], JObject) else None
        tz = resolve_tzinfo(tz_id)
        ms = at_epoch_ms if at_epoch_ms is not None else int(stubs.current_time_ms(advance=False))
        dt = _datetime.fromtimestamp(ms / 1000.0, tz=tz)
        off = tz.utcoffset(dt) or _timedelta(0)
        return int(off.total_seconds() * 1000)

    R(TZ, "getRawOffset", "()I", lambda a: _tz_offset_ms(a))
    R(TZ, "getOffset", "(J)I", lambda a: _tz_offset_ms(a, _s64(a[1]) if len(a) > 1 else None))
    R(TZ, "inDaylightTime", "(Ljava/util/Date;)Z",
      lambda a: (lambda tz_id, ms: 1 if (resolve_tzinfo(tz_id).dst(_datetime.fromtimestamp(ms / 1000.0, tz=resolve_tzinfo(tz_id))) or _timedelta(0)) else 0)(
          a[0].fields.get("__id") if a and isinstance(a[0], JObject) else None,
          _obj_epoch_ms(stubs, a[1] if len(a) > 1 else None, default_now=True),
      ))

    # --- java.util.Calendar & java.util.GregorianCalendar ---
    CAL = "Ljava/util/Calendar;"
    GCAL = "Ljava/util/GregorianCalendar;"

    def _mk_cal(tz_id: Optional[str] = None, ms: Optional[int] = None) -> JObject:
        return JObject(GCAL, {
            "__ms": int(ms) if ms is not None else int(stubs.current_time_ms(advance=False)),
            "__tz": tz_id if tz_id is not None else getattr(stubs, "_clock_tz", None),
        })

    def _cal_init(a, kind: str = "now"):
        if not a or not isinstance(a[0], JObject):
            return None
        cal = a[0]
        if kind == "tz" and len(a) > 1 and isinstance(a[1], JObject):
            cal.fields["__tz"] = a[1].fields.get("__id")
            cal.fields["__ms"] = int(stubs.current_time_ms(advance=False))
        elif kind == "ymd" and len(a) >= 4:
            y, m0, d = int(a[1]), int(a[2]), int(a[3])
            hh = int(a[4]) if len(a) > 4 else 0
            mm = int(a[5]) if len(a) > 5 else 0
            ss = int(a[6]) if len(a) > 6 else 0
            tz_id = cal.fields.get("__tz")
            cal.fields["__ms"] = _ms_from_dt(_datetime(max(1, y), max(1, min(12, m0 + 1)), max(1, d), hh, mm, ss), tz_id)
        else:
            cal.fields.setdefault("__tz", getattr(stubs, "_clock_tz", None))
            cal.fields["__ms"] = int(stubs.current_time_ms(advance=False))
        return None

    R(CAL, "getInstance", "()Ljava/util/Calendar;", lambda a: _mk_cal())
    R(CAL, "getInstance", "(Ljava/util/TimeZone;)Ljava/util/Calendar;",
      lambda a: _mk_cal(a[0].fields.get("__id") if a and isinstance(a[0], JObject) else None))
    R(CAL, "getInstance", "(Ljava/util/Locale;)Ljava/util/Calendar;", lambda a: _mk_cal())
    R(CAL, "getInstance", "(Ljava/util/TimeZone;Ljava/util/Locale;)Ljava/util/Calendar;",
      lambda a: _mk_cal(a[0].fields.get("__id") if a and isinstance(a[0], JObject) else None))
    for _csig, _ckind in (
        ("()V", "now"),
        ("(Ljava/util/TimeZone;)V", "tz"),
        ("(Ljava/util/Locale;)V", "now"),
        ("(Ljava/util/TimeZone;Ljava/util/Locale;)V", "tz"),
        ("(III)V", "ymd"),
        ("(IIIII)V", "ymd"),
        ("(IIIIII)V", "ymd"),
    ):
        R(GCAL, "<init>", _csig, lambda a, _k=_ckind: _cal_init(a, _k))
    R(CAL, "<init>", "()V", lambda a: _cal_init(a, "now"))

    def _cal_dt(cal: Any) -> tuple[_datetime, Optional[str], int]:
        ms = _obj_epoch_ms(stubs, cal, default_now=True)
        tz_id = cal.fields.get("__tz") if isinstance(cal, JObject) else None
        return _dt_from_ms(ms, tz_id), tz_id, ms

    def _cal_get(a) -> int:
        cal = a[0] if a else None
        fld = int(a[1]) if len(a) > 1 else 1
        dt, tz_id, ms = _cal_dt(cal)
        if fld == 0:
            return 1
        if fld == 1:
            return dt.year
        if fld == 2:
            return dt.month - 1
        if fld == 3:
            return int(dt.isocalendar()[1])
        if fld in (4, 8):
            return ((dt.day - 1) // 7) + 1
        if fld == 5:
            return dt.day
        if fld == 6:
            return dt.timetuple().tm_yday
        if fld == 7:
            return (dt.isoweekday() % 7) + 1
        if fld == 9:
            return 0 if dt.hour < 12 else 1
        if fld == 10:
            return dt.hour % 12
        if fld == 11:
            return dt.hour
        if fld == 12:
            return dt.minute
        if fld == 13:
            return dt.second
        if fld == 14:
            return int(ms) % 1000
        if fld in (15, 16):
            tz = resolve_tzinfo(_eff_tz_name(tz_id))
            off = tz.utcoffset(dt) or _timedelta(0)
            dst = tz.dst(dt) or _timedelta(0)
            return int((dst if fld == 16 else (off - dst)).total_seconds() * 1000)
        return 0

    def _cal_set_field(cal: JObject, fld: int, val: int) -> None:
        dt, tz_id, ms = _cal_dt(cal)
        ms_rem = ms % 1000
        v = _s32(val)
        if fld == 1:
            ny = max(1, min(9999, v))
            dt = dt.replace(year=ny, day=min(dt.day, _pycal.monthrange(ny, dt.month)[1]))
        elif fld == 2:
            dt = _add_months_clamped(dt.replace(month=1), v)
        elif fld == 3:
            cur_woy = int(dt.isocalendar()[1])
            dt = dt + _timedelta(days=7 * (v - cur_woy))
        elif fld in (4, 8):
            cur_wom = ((dt.day - 1) // 7) + 1
            dt = dt + _timedelta(days=7 * (v - cur_wom))
        elif fld == 5:
            dt = dt + _timedelta(days=v - dt.day)
        elif fld == 6:
            cur_doy = dt.timetuple().tm_yday
            dt = dt + _timedelta(days=v - cur_doy)
        elif fld == 7:
            first_dow = int(cal.fields.get("_first_dow", 2) or 2)
            cur_java_dow = (dt.isoweekday() % 7) + 1  # Sun=1, Mon=2, ..., Sat=7
            week_start = dt - _timedelta(days=(cur_java_dow - first_dow) % 7)
            day_off = ((v - first_dow) % 7) if (1 <= v <= 7) else (v - first_dow)
            dt = week_start + _timedelta(days=day_off)
        elif fld == 11:
            dt = dt + _timedelta(hours=v - dt.hour)
        elif fld == 10:
            base = 12 if dt.hour >= 12 else 0
            dt = dt.replace(hour=(base + v) % 24)
        elif fld == 9:
            dt = dt.replace(hour=(dt.hour % 12) + (12 if v else 0))
        elif fld == 12:
            dt = dt + _timedelta(minutes=v - dt.minute)
        elif fld == 13:
            dt = dt + _timedelta(seconds=v - dt.second)
        elif fld == 14:
            cal.fields["__ms"] = (ms - ms_rem) + v
            return
        cal.fields["__ms"] = _ms_from_dt(dt, tz_id, ms_rem)

    def _cal_set_multi(a):
        cal = a[0] if a and isinstance(a[0], JObject) else None
        if cal is None or len(a) < 4:
            return None
        _, tz_id, ms = _cal_dt(cal)
        y = max(1, min(9999, _s32(a[1])))
        m0 = _s32(a[2])
        d = _s32(a[3])
        dt0, _, _ = _cal_dt(cal)
        hh = _s32(a[4]) if len(a) > 4 else dt0.hour
        mm = _s32(a[5]) if len(a) > 5 else dt0.minute
        ss = _s32(a[6]) if len(a) > 6 else dt0.second
        base = _add_months_clamped(_datetime(y, 1, 1, 0, 0, 0), m0)
        dt = base + _timedelta(days=d - 1, hours=hh, minutes=mm, seconds=ss)
        cal.fields["__ms"] = _ms_from_dt(dt, tz_id, ms % 1000)
        return None

    def _cal_add(a):
        cal = a[0] if a and isinstance(a[0], JObject) else None
        if cal is None or len(a) < 3:
            return None
        fld, amt = _s32(a[1]), _s32(a[2])
        dt, tz_id, ms = _cal_dt(cal)
        ms_rem = ms % 1000
        if fld == 14:
            cal.fields["__ms"] = ms + amt
            return None
        if fld == 1:
            dt = _add_months_clamped(dt, amt * 12)
        elif fld == 2:
            dt = _add_months_clamped(dt, amt)
        elif fld in (3, 4):
            dt = dt + _timedelta(days=amt * 7)
        elif fld in (5, 6, 7):
            dt = dt + _timedelta(days=amt)
        elif fld in (10, 11):
            dt = dt + _timedelta(hours=amt)
        elif fld == 12:
            dt = dt + _timedelta(minutes=amt)
        elif fld == 13:
            dt = dt + _timedelta(seconds=amt)
        cal.fields["__ms"] = _ms_from_dt(dt, tz_id, ms_rem)
        return None

    for _ccls in (CAL, GCAL):
        R(_ccls, "getTimeInMillis", "()J", lambda a: _obj_epoch_ms(stubs, a[0] if a else None, default_now=True))
        R(_ccls, "setTimeInMillis", "(J)V",
          lambda a: a[0].fields.__setitem__("__ms", _s64(a[1]) if len(a) > 1 else 0) if a and isinstance(a[0], JObject) else None)
        R(_ccls, "getTime", "()Ljava/util/Date;",
          lambda a: JObject("Ljava/util/Date;", {"__ms": _obj_epoch_ms(stubs, a[0] if a else None, default_now=True)}))
        R(_ccls, "setTime", "(Ljava/util/Date;)V",
          lambda a: a[0].fields.__setitem__("__ms", _obj_epoch_ms(stubs, a[1] if len(a) > 1 else None)) if a and isinstance(a[0], JObject) else None)
        R(_ccls, "getTimeZone", "()Ljava/util/TimeZone;",
          lambda a: _tz_obj(a[0].fields.get("__tz") if a and isinstance(a[0], JObject) else None))
        R(_ccls, "setTimeZone", "(Ljava/util/TimeZone;)V",
          lambda a: a[0].fields.__setitem__("__tz", a[1].fields.get("__id") if len(a) > 1 and isinstance(a[1], JObject) else None) if a and isinstance(a[0], JObject) else None)
        R(_ccls, "get", "(I)I", _cal_get)
        R(_ccls, "set", "(II)V",
          lambda a: _cal_set_field(a[0], int(a[1]), int(a[2])) if len(a) >= 3 and isinstance(a[0], JObject) else None)
        R(_ccls, "set", "(III)V", _cal_set_multi)
        R(_ccls, "set", "(IIIII)V", _cal_set_multi)
        R(_ccls, "set", "(IIIIII)V", _cal_set_multi)
        R(_ccls, "add", "(II)V", _cal_add)
        R(_ccls, "roll", "(II)V", _cal_add)
        R(_ccls, "clear", "()V", lambda a: a[0].fields.__setitem__("__ms", 0) if a and isinstance(a[0], JObject) else None)
        R(_ccls, "before", "(Ljava/lang/Object;)Z",
          lambda a: 1 if len(a) > 1 and _obj_epoch_ms(stubs, a[0]) < _obj_epoch_ms(stubs, a[1]) else 0)
        R(_ccls, "after", "(Ljava/lang/Object;)Z",
          lambda a: 1 if len(a) > 1 and _obj_epoch_ms(stubs, a[0]) > _obj_epoch_ms(stubs, a[1]) else 0)
        R(_ccls, "compareTo", "(Ljava/util/Calendar;)I",
          lambda a: (lambda x, y: -1 if x < y else (1 if x > y else 0))(_obj_epoch_ms(stubs, a[0]), _obj_epoch_ms(stubs, a[1]) if len(a) > 1 else 0))
        R(_ccls, "compareTo", "(Ljava/lang/Object;)I",
          lambda a: (lambda x, y: -1 if x < y else (1 if x > y else 0))(_obj_epoch_ms(stubs, a[0]), _obj_epoch_ms(stubs, a[1]) if len(a) > 1 else 0))
        R(_ccls, "clone", "()Ljava/lang/Object;",
          lambda a: JObject(a[0].cls, dict(a[0].fields)) if a and isinstance(a[0], JObject) else None)

    def _cal_actual_max(a) -> int:
        cal = a[0] if a else None
        fld = int(a[1]) if len(a) > 1 else 5
        dt, _, _ = _cal_dt(cal)
        if fld == 1:
            return 9999
        if fld == 2:
            return 11
        if fld == 3:
            return 53
        if fld in (4, 8):
            return 5
        if fld == 5:
            return _pycal.monthrange(dt.year, dt.month)[1]
        if fld == 6:
            return 366 if _pycal.isleap(dt.year) else 365
        if fld == 7:
            return 7
        if fld == 9:
            return 1
        if fld == 10:
            return 11
        if fld == 11:
            return 23
        if fld in (12, 13):
            return 59
        if fld == 14:
            return 999
        return 31

    def _cal_actual_min(a) -> int:
        fld = int(a[1]) if len(a) > 1 else 5
        if fld in (1, 3, 4, 5, 6, 7, 8):
            return 1
        return 0

    R(CAL, "setFirstDayOfWeek", "(I)V",
      lambda a: a[0].fields.__setitem__("_first_dow", int(a[1] if len(a) > 1 else 2)) if a and isinstance(a[0], JObject) else None)
    R(CAL, "isLenient", "()Z",
      lambda a: 1 if (not a or not isinstance(a[0], JObject) or a[0].fields.get("_lenient", True)) else 0)
    R(CAL, "getActualMaximum", "(I)I", _cal_actual_max)
    R(CAL, "getMaximum", "(I)I", _cal_actual_max)
    R(CAL, "getLeastMaximum", "(I)I", _cal_actual_max)
    R(CAL, "getActualMinimum", "(I)I", _cal_actual_min)
    R(CAL, "getMinimum", "(I)I", _cal_actual_min)
    R(CAL, "getGreatestMinimum", "(I)I", _cal_actual_min)

    # --- java.util.Date Ergaenzungen ---
    DT = "Ljava/util/Date;"
    R(DT, "before", "(Ljava/util/Date;)Z",
      lambda a: 1 if len(a) > 1 and _obj_epoch_ms(stubs, a[0]) < _obj_epoch_ms(stubs, a[1]) else 0)
    R(DT, "after", "(Ljava/util/Date;)Z",
      lambda a: 1 if len(a) > 1 and _obj_epoch_ms(stubs, a[0]) > _obj_epoch_ms(stubs, a[1]) else 0)
    R(DT, "compareTo", "(Ljava/util/Date;)I",
      lambda a: (lambda x, y: -1 if x < y else (1 if x > y else 0))(_obj_epoch_ms(stubs, a[0]), _obj_epoch_ms(stubs, a[1]) if len(a) > 1 else 0))
    R(DT, "compareTo", "(Ljava/lang/Object;)I",
      lambda a: (lambda x, y: -1 if x < y else (1 if x > y else 0))(_obj_epoch_ms(stubs, a[0]), _obj_epoch_ms(stubs, a[1]) if len(a) > 1 else 0))
    R(DT, "equals", "(Ljava/lang/Object;)Z",
      lambda a: 1 if len(a) > 1 and isinstance(a[1], JObject) and _obj_epoch_ms(stubs, a[0]) == _obj_epoch_ms(stubs, a[1]) else 0)
    R(DT, "hashCode", "()I",
      lambda a: (lambda ms: ((ms ^ (ms >> 32)) & 0x7FFFFFFF))(_obj_epoch_ms(stubs, a[0]) if a else 0))
    R(DT, "toString", "()Ljava/lang/String;",
      lambda a: JString(" ".join(_dt_from_ms(_obj_epoch_ms(stubs, a[0] if a else None)).strftime("%a %b %d %H:%M:%S %Z %Y").split())))

    # --- java.text.SimpleDateFormat / DateFormat Ergaenzungen ---
    def _sdf_parse(a):
        pat = a[0].fields.get("__pattern", "") if a and isinstance(a[0], JObject) else ""
        tz_id = a[0].fields.get("__tz") if a and isinstance(a[0], JObject) else None
        raw = (stubs._txt(a[1]) or "").strip() if len(a) > 1 else ""
        if raw:
            dt_strict = _parse_java_pattern_strict(raw, pat)
            if dt_strict is not None:
                return JObject(DT, {"__ms": _ms_from_dt(dt_strict, tz_id, dt_strict.microsecond // 1000)})
            py_pat = (
                pat.replace("'T'", "T").replace("yyyy", "%Y").replace("MM", "%m")
                   .replace("dd", "%d").replace("HH", "%H").replace("mm", "%M").replace("ss", "%S")
            )
            if py_pat and "%" in py_pat:
                try:
                    dt = _datetime.strptime(raw, py_pat)
                    return JObject(DT, {"__ms": _ms_from_dt(dt, tz_id)})
                except Exception:
                    pass
            try:
                return JObject(DT, {"__ms": parse_datetime_spec(raw, tz_id or getattr(stubs, "_clock_tz", None))})
            except Exception:
                pass
        return None

    for _df_cls in ("Ljava/text/SimpleDateFormat;", "Ljava/text/DateFormat;"):
        R(_df_cls, "<init>", "()V",
          lambda a: a[0].fields.__setitem__("__pattern", "dd.MM.yyyy HH:mm") if a and isinstance(a[0], JObject) else None)
        R(_df_cls, "setTimeZone", "(Ljava/util/TimeZone;)V",
          lambda a: a[0].fields.__setitem__("__tz", a[1].fields.get("__id") if len(a) > 1 and isinstance(a[1], JObject) else None) if a and isinstance(a[0], JObject) else None)
        R(_df_cls, "getTimeZone", "()Ljava/util/TimeZone;",
          lambda a: _tz_obj(a[0].fields.get("__tz") if a and isinstance(a[0], JObject) else None))
        R(_df_cls, "parse", "(Ljava/lang/String;)Ljava/util/Date;", _sdf_parse)

    for _gsig, _gpat in (
        ("()Ljava/text/DateFormat;", "dd.MM.yyyy"),
        ("(I)Ljava/text/DateFormat;", "dd.MM.yyyy"),
        ("(ILjava/util/Locale;)Ljava/text/DateFormat;", "dd.MM.yyyy"),
    ):
        R("Ljava/text/DateFormat;", "getDateInstance", _gsig,
          lambda a, _p=_gpat: JObject("Ljava/text/SimpleDateFormat;", {"__pattern": _p}))
    for _gsig, _gpat in (
        ("()Ljava/text/DateFormat;", "HH:mm:ss"),
        ("(I)Ljava/text/DateFormat;", "HH:mm"),
        ("(ILjava/util/Locale;)Ljava/text/DateFormat;", "HH:mm"),
    ):
        R("Ljava/text/DateFormat;", "getTimeInstance", _gsig,
          lambda a, _p=_gpat: JObject("Ljava/text/SimpleDateFormat;", {"__pattern": _p}))
    for _gsig in (
        "()Ljava/text/DateFormat;",
        "(II)Ljava/text/DateFormat;",
        "(IILjava/util/Locale;)Ljava/text/DateFormat;",
    ):
        R("Ljava/text/DateFormat;", "getDateTimeInstance", _gsig,
          lambda a: JObject("Ljava/text/SimpleDateFormat;", {"__pattern": "dd.MM.yyyy HH:mm"}))

    # --- java.time.* Komplettierung ---
    INST = "Ljava/time/Instant;"

    def _inst_obj(ms: int) -> JObject:
        return JObject(INST, {"__epoch_sec": int(ms) // 1000, "__epoch_ms": int(ms)})

    R(INST, "now", "()Ljava/time/Instant;", lambda a: _inst_obj(stubs.current_time_ms(advance=False)))
    R(INST, "parse", "(Ljava/lang/CharSequence;)Ljava/time/Instant;",
      lambda a: _inst_obj(parse_datetime_spec(stubs._txt(a[0]) if a else "", "UTC")))
    R(INST, "plusSeconds", "(J)Ljava/time/Instant;",
      lambda a: _inst_obj(_obj_epoch_ms(stubs, a[0]) + (_s64(a[1]) * 1000 if len(a) > 1 else 0)))
    R(INST, "minusSeconds", "(J)Ljava/time/Instant;",
      lambda a: _inst_obj(_obj_epoch_ms(stubs, a[0]) - (_s64(a[1]) * 1000 if len(a) > 1 else 0)))
    R(INST, "plusMillis", "(J)Ljava/time/Instant;",
      lambda a: _inst_obj(_obj_epoch_ms(stubs, a[0]) + (_s64(a[1]) if len(a) > 1 else 0)))
    R(INST, "isBefore", "(Ljava/time/Instant;)Z",
      lambda a: 1 if len(a) > 1 and _obj_epoch_ms(stubs, a[0]) < _obj_epoch_ms(stubs, a[1]) else 0)
    R(INST, "isAfter", "(Ljava/time/Instant;)Z",
      lambda a: 1 if len(a) > 1 and _obj_epoch_ms(stubs, a[0]) > _obj_epoch_ms(stubs, a[1]) else 0)
    R(INST, "toString", "()Ljava/lang/String;",
      lambda a: JString(_datetime.fromtimestamp(_obj_epoch_ms(stubs, a[0]) / 1000.0, tz=_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))

    LT = "Ljava/time/LocalTime;"

    def _lt_wrap(t: _time_dt) -> JObject:
        return JObject(LT, {"hour": t.hour, "minute": t.minute, "second": t.second, "__time": t})

    def _lt_unwrap(obj: Any) -> _time_dt:
        if isinstance(obj, JObject):
            if isinstance(obj.fields.get("__time"), _time_dt):
                return obj.fields["__time"]
            if "__dt" in obj.fields and isinstance(obj.fields["__dt"], _datetime):
                return obj.fields["__dt"].time()
            return _time_dt(int(obj.fields.get("hour", 0)) % 24, int(obj.fields.get("minute", 0)) % 60, int(obj.fields.get("second", 0)) % 60)
        return stubs.current_datetime().time()

    R(LT, "now", "()Ljava/time/LocalTime;", lambda a: _lt_wrap(stubs.current_datetime().time()))
    R(LT, "now", "(Ljava/time/ZoneId;)Ljava/time/LocalTime;",
      lambda a: _lt_wrap(stubs.current_datetime(a[0].fields.get("__id") if a and isinstance(a[0], JObject) and not a[0].fields.get("__sys_default") else None).time()))
    R(LT, "of", "(II)Ljava/time/LocalTime;", lambda a: _lt_wrap(_time_dt(int(a[0]) % 24, int(a[1]) % 60)))
    R(LT, "of", "(III)Ljava/time/LocalTime;", lambda a: _lt_wrap(_time_dt(int(a[0]) % 24, int(a[1]) % 60, int(a[2]) % 60)))
    R(LT, "parse", "(Ljava/lang/CharSequence;)Ljava/time/LocalTime;",
      lambda a: _lt_wrap(_time_dt.fromisoformat((stubs._txt(a[0]) or "00:00").strip())))
    R(LT, "getHour", "()I", lambda a: _lt_unwrap(a[0] if a else None).hour)
    R(LT, "getMinute", "()I", lambda a: _lt_unwrap(a[0] if a else None).minute)
    R(LT, "getSecond", "()I", lambda a: _lt_unwrap(a[0] if a else None).second)
    R(LT, "toString", "()Ljava/lang/String;", lambda a: JString(_lt_unwrap(a[0] if a else None).strftime("%H:%M:%S")))
    R(LT, "format", "(Ljava/time/format/DateTimeFormatter;)Ljava/lang/String;",
      lambda a: JString(format_java_pattern(
          _datetime.combine(_date(1970, 1, 1), _lt_unwrap(a[0] if a else None)),
          a[1].fields.get("__pattern", "HH:mm:ss") if len(a) > 1 and isinstance(a[1], JObject) else "HH:mm:ss",
          a[1].fields.get("__locale") if len(a) > 1 and isinstance(a[1], JObject) else None,
      )))

    LDT = "Ljava/time/LocalDateTime;"

    def _ldt_wrap(dt: _datetime) -> JObject:
        return JObject(LDT, {"__dt": dt})

    def _ldt_unwrap(obj: Any) -> _datetime:
        if isinstance(obj, JObject) and isinstance(obj.fields.get("__dt"), _datetime):
            return obj.fields["__dt"]
        return stubs.current_datetime()

    R(LDT, "now", "()Ljava/time/LocalDateTime;", lambda a: _ldt_wrap(stubs.current_datetime()))
    R(LDT, "now", "(Ljava/time/ZoneId;)Ljava/time/LocalDateTime;",
      lambda a: _ldt_wrap(stubs.current_datetime(a[0].fields.get("__id") if a and isinstance(a[0], JObject) and not a[0].fields.get("__sys_default") else None)))
    R(LDT, "of", "(IIIII)Ljava/time/LocalDateTime;",
      lambda a: _ldt_wrap(_datetime(int(a[0]), int(a[1]), int(a[2]), int(a[3]), int(a[4]))))
    R(LDT, "of", "(IIIIII)Ljava/time/LocalDateTime;",
      lambda a: _ldt_wrap(_datetime(int(a[0]), int(a[1]), int(a[2]), int(a[3]), int(a[4]), int(a[5]))))
    R(LDT, "of", "(Ljava/time/LocalDate;Ljava/time/LocalTime;)Ljava/time/LocalDateTime;",
      lambda a: _ldt_wrap(_datetime.combine(
          a[0].fields.get("__date") if a and isinstance(a[0], JObject) and isinstance(a[0].fields.get("__date"), _date) else stubs.current_datetime().date(),
          _lt_unwrap(a[1] if len(a) > 1 else None),
      )))
    R(LDT, "parse", "(Ljava/lang/CharSequence;)Ljava/time/LocalDateTime;",
      lambda a: _ldt_wrap(_datetime.fromisoformat((stubs._txt(a[0]) or "").strip().rstrip("Z"))))

    def _ldt_parse_fmt(a):
        raw = (stubs._txt(a[0]) or "").strip() if a else ""
        pat = a[1].fields.get("__pattern", "yyyy-MM-dd'T'HH:mm:ss") if len(a) > 1 and isinstance(a[1], JObject) else "yyyy-MM-dd'T'HH:mm:ss"
        loc = a[1].fields.get("__locale") if len(a) > 1 and isinstance(a[1], JObject) else None
        return _ldt_wrap(parse_java_pattern(raw, pat, loc))

    R(LDT, "parse", "(Ljava/lang/CharSequence;Ljava/time/format/DateTimeFormatter;)Ljava/time/LocalDateTime;", _ldt_parse_fmt)
    R("Ljava/time/LocalDate;", "parse", "(Ljava/lang/CharSequence;Ljava/time/format/DateTimeFormatter;)Ljava/time/LocalDate;",
      lambda a: (lambda d: JObject("Ljava/time/LocalDate;", {"year": d.year, "month": d.month, "day": d.day, "__date": d}))(
          parse_java_pattern(
              (stubs._txt(a[0]) or "").strip() if a else "",
              a[1].fields.get("__pattern", "yyyy-MM-dd") if len(a) > 1 and isinstance(a[1], JObject) else "yyyy-MM-dd",
              a[1].fields.get("__locale") if len(a) > 1 and isinstance(a[1], JObject) else None,
          ).date()
      ))
    R(LDT, "toLocalDate", "()Ljava/time/LocalDate;",
      lambda a: (lambda d: JObject("Ljava/time/LocalDate;", {"year": d.year, "month": d.month, "day": d.day, "__date": d}))(_ldt_unwrap(a[0] if a else None).date()))
    R(LDT, "toLocalTime", "()Ljava/time/LocalTime;", lambda a: _lt_wrap(_ldt_unwrap(a[0] if a else None).time()))
    R(LDT, "getYear", "()I", lambda a: _ldt_unwrap(a[0] if a else None).year)
    R(LDT, "getMonthValue", "()I", lambda a: _ldt_unwrap(a[0] if a else None).month)
    R(LDT, "getDayOfMonth", "()I", lambda a: _ldt_unwrap(a[0] if a else None).day)
    R(LDT, "getHour", "()I", lambda a: _ldt_unwrap(a[0] if a else None).hour)
    R(LDT, "getMinute", "()I", lambda a: _ldt_unwrap(a[0] if a else None).minute)
    R(LDT, "getSecond", "()I", lambda a: _ldt_unwrap(a[0] if a else None).second)
    R(LDT, "plusDays", "(J)Ljava/time/LocalDateTime;",
      lambda a: _ldt_wrap(_ldt_unwrap(a[0] if a else None) + _timedelta(days=_s64(a[1]) if len(a) > 1 else 0)))
    R(LDT, "minusDays", "(J)Ljava/time/LocalDateTime;",
      lambda a: _ldt_wrap(_ldt_unwrap(a[0] if a else None) - _timedelta(days=_s64(a[1]) if len(a) > 1 else 0)))
    R(LDT, "plusHours", "(J)Ljava/time/LocalDateTime;",
      lambda a: _ldt_wrap(_ldt_unwrap(a[0] if a else None) + _timedelta(hours=_s64(a[1]) if len(a) > 1 else 0)))
    R(LDT, "plusMinutes", "(J)Ljava/time/LocalDateTime;",
      lambda a: _ldt_wrap(_ldt_unwrap(a[0] if a else None) + _timedelta(minutes=_s64(a[1]) if len(a) > 1 else 0)))
    R(LDT, "isBefore", "(Ljava/time/chrono/ChronoLocalDateTime;)Z",
      lambda a: 1 if len(a) > 1 and _ldt_unwrap(a[0]).replace(tzinfo=None) < _ldt_unwrap(a[1]).replace(tzinfo=None) else 0)
    R(LDT, "isAfter", "(Ljava/time/chrono/ChronoLocalDateTime;)Z",
      lambda a: 1 if len(a) > 1 and _ldt_unwrap(a[0]).replace(tzinfo=None) > _ldt_unwrap(a[1]).replace(tzinfo=None) else 0)

    def _ldt_epoch_ms_offset(a) -> int:
        dt = _ldt_unwrap(a[0] if a else None).replace(tzinfo=None)
        z_obj = a[1] if len(a) > 1 and isinstance(a[1], JObject) else None
        if z_obj is not None and "__offset_sec" in z_obj.fields:
            tz = _timezone(_timedelta(seconds=int(z_obj.fields["__offset_sec"])))
        else:
            zid = (z_obj.fields.get("__id") if z_obj is not None else None) or "UTC"
            tz = resolve_tzinfo(zid)
        return int(round(dt.replace(tzinfo=tz).timestamp() * 1000.0))

    for _cdt_cls in (LDT, "Ljava/time/chrono/ChronoLocalDateTime;"):
        R(_cdt_cls, "toInstant", "(Ljava/time/ZoneOffset;)Ljava/time/Instant;",
          lambda a: _inst_obj(_ldt_epoch_ms_offset(a)))
        R(_cdt_cls, "toEpochSecond", "(Ljava/time/ZoneOffset;)J",
          lambda a: _ldt_epoch_ms_offset(a) // 1000)

    ZO = "Ljava/time/ZoneOffset;"
    R(ZO, "of", "(Ljava/lang/String;)Ljava/time/ZoneOffset;",
      lambda a: (lambda zid: JObject(ZO, {"__id": "UTC" if zid in ("", "Z", "z") else zid}))((stubs._txt(a[0]) or "UTC").strip() if a else "UTC"))
    R(ZO, "ofHours", "(I)Ljava/time/ZoneOffset;",
      lambda a: (lambda h: JObject(ZO, {"__id": f"{h:+03d}:00", "__offset_sec": h * 3600}))(int(a[0]) if a else 0))
    R(ZO, "ofTotalSeconds", "(I)Ljava/time/ZoneOffset;",
      lambda a: (lambda s: JObject(ZO, {"__id": "UTC" if s == 0 else f"{s // 3600:+03d}:{(abs(s) % 3600) // 60:02d}", "__offset_sec": s}))(int(a[0]) if a else 0))
    R(ZO, "getTotalSeconds", "()I",
      lambda a: int(a[0].fields.get("__offset_sec", 0)) if a and isinstance(a[0], JObject) else 0)
    R(ZO, "getId", "()Ljava/lang/String;",
      lambda a: JString(str(a[0].fields.get("__id", "Z") or "Z")) if a and isinstance(a[0], JObject) else JString("Z"))

    R(LDT, "atZone", "(Ljava/time/ZoneId;)Ljava/time/ZonedDateTime;",
      lambda a: (lambda dt, zid: (lambda ms: JObject("Ljava/time/ZonedDateTime;", {
          "__dt": dt, "__date": dt.date(), "__epoch_sec": ms // 1000, "__epoch_ms": ms,
      }))(_ms_from_dt(dt, zid, dt.microsecond // 1000)))(_ldt_unwrap(a[0] if a else None), a[1].fields.get("__id") if len(a) > 1 and isinstance(a[1], JObject) and not a[1].fields.get("__sys_default") else None))
    R(LDT, "toString", "()Ljava/lang/String;",
      lambda a: JString(_ldt_unwrap(a[0] if a else None).replace(tzinfo=None).isoformat(timespec="seconds")))

    ZDT = "Ljava/time/ZonedDateTime;"

    def _zdt_now(tz_obj=None) -> JObject:
        zid = tz_obj.fields.get("__id") if isinstance(tz_obj, JObject) and not tz_obj.fields.get("__sys_default") else None
        dt = stubs.current_datetime(zid)
        ms = int(stubs.current_time_ms(advance=False))
        return JObject(ZDT, {"__dt": dt, "__date": dt.date(), "__epoch_sec": ms // 1000, "__epoch_ms": ms})

    def _zdt_unwrap(obj: Any) -> _datetime:
        if isinstance(obj, JObject) and isinstance(obj.fields.get("__dt"), _datetime):
            return obj.fields["__dt"]
        return stubs.current_datetime()

    R(ZDT, "now", "()Ljava/time/ZonedDateTime;", lambda a: _zdt_now())
    R(ZDT, "now", "(Ljava/time/ZoneId;)Ljava/time/ZonedDateTime;", lambda a: _zdt_now(a[0] if a else None))
    R(ZDT, "getYear", "()I", lambda a: _zdt_unwrap(a[0] if a else None).year)
    R(ZDT, "getMonthValue", "()I", lambda a: _zdt_unwrap(a[0] if a else None).month)
    R(ZDT, "getDayOfMonth", "()I", lambda a: _zdt_unwrap(a[0] if a else None).day)
    R(ZDT, "getHour", "()I", lambda a: _zdt_unwrap(a[0] if a else None).hour)
    R(ZDT, "getMinute", "()I", lambda a: _zdt_unwrap(a[0] if a else None).minute)
    R(ZDT, "getSecond", "()I", lambda a: _zdt_unwrap(a[0] if a else None).second)
    R(ZDT, "plusDays", "(J)Ljava/time/ZonedDateTime;",
      lambda a: (lambda dt: JObject(ZDT, {"__dt": dt, "__date": dt.date(), "__epoch_sec": int(dt.timestamp()), "__epoch_ms": int(dt.timestamp() * 1000)}))(_zdt_unwrap(a[0] if a else None) + _timedelta(days=_s64(a[1]) if len(a) > 1 else 0)))
    R(ZDT, "minusDays", "(J)Ljava/time/ZonedDateTime;",
      lambda a: (lambda dt: JObject(ZDT, {"__dt": dt, "__date": dt.date(), "__epoch_sec": int(dt.timestamp()), "__epoch_ms": int(dt.timestamp() * 1000)}))(_zdt_unwrap(a[0] if a else None) - _timedelta(days=_s64(a[1]) if len(a) > 1 else 0)))
    R(ZDT, "toInstant", "()Ljava/time/Instant;", lambda a: _inst_obj(_obj_epoch_ms(stubs, a[0] if a else None, default_now=True)))
    R(ZDT, "toString", "()Ljava/lang/String;", lambda a: JString(_zdt_unwrap(a[0] if a else None).isoformat(timespec="seconds")))

    # --- android.text.format.DateFormat, DateUtils & android.os.SystemClock ---
    ADF = "Landroid/text/format/DateFormat;"
    R(ADF, "is24HourFormat", "(Landroid/content/Context;)Z", lambda a: 1)

    def _best_dt_pattern(a) -> JString:
        loc_obj = a[0] if a and isinstance(a[0], JObject) else None
        skel = (stubs._txt(a[1]) if len(a) > 1 else "") or ""
        skel_s = skel.strip()
        if not skel_s:
            return JString("dd.MM.yyyy")
        lang = str((loc_obj.fields.get("lang") if loc_obj is not None else "") or "de").lower()
        is_en = lang.startswith("en")
        _map = {
            "cccccc": "cccccc",
            "cccc": "EEEE",
            "ccc": "EEE",
            "Hm": "HH:mm",
            "HHmm": "HH:mm",
            "Hms": "HH:mm:ss",
            "hm": "h:mm a",
            "jms": "HH:mm:ss",
            "jm": "HH:mm",
            "MMMd": "MMM d" if is_en else "d. MMM",
            "dMMM": "MMM d" if is_en else "d. MMM",
            "MMMMd": "MMMM d" if is_en else "d. MMMM",
            "dMMMM": "MMMM d" if is_en else "d. MMMM",
            "EEEEMMMd": "EEEE, MMM d" if is_en else "EEEE, d. MMM",
            "MMMEEEEd": "EEEE, MMM d" if is_en else "EEEE, d. MMM",
            "MEd": "EEE, M/d" if is_en else "EEE, dd.MM.",
            "MMMEd": "EEE, MMM d" if is_en else "EEE, d. MMM",
            "yMMMd": "MMM d, yyyy" if is_en else "dd.MM.yyyy",
            "yMd": "yyyy/MM/dd" if is_en else "dd.MM.yyyy",
            "yMMMM": "MMMM yyyy",
        }
        return JString(_map.get(skel_s, skel_s))

    R(ADF, "getBestDateTimePattern", "(Ljava/util/Locale;Ljava/lang/String;)Ljava/lang/String;", _best_dt_pattern)
    R(ADF, "getTimeFormat", "(Landroid/content/Context;)Ljava/text/DateFormat;",
      lambda a: JObject("Ljava/text/SimpleDateFormat;", {"__pattern": "HH:mm"}))
    R(ADF, "getDateFormat", "(Landroid/content/Context;)Ljava/text/DateFormat;",
      lambda a: JObject("Ljava/text/SimpleDateFormat;", {"__pattern": "dd.MM.yyyy"}))
    R(ADF, "getMediumDateFormat", "(Landroid/content/Context;)Ljava/text/DateFormat;",
      lambda a: JObject("Ljava/text/SimpleDateFormat;", {"__pattern": "d. MMM yyyy"}))
    R(ADF, "getLongDateFormat", "(Landroid/content/Context;)Ljava/text/DateFormat;",
      lambda a: JObject("Ljava/text/SimpleDateFormat;", {"__pattern": "d. MMMM yyyy"}))
    R(ADF, "format", "(Ljava/lang/CharSequence;J)Ljava/lang/CharSequence;",
      lambda a: JString(format_java_pattern(_dt_from_ms(_s64(a[1]) if len(a) > 1 else 0), stubs._txt(a[0]) or "dd.MM.yyyy HH:mm")))
    R(ADF, "format", "(Ljava/lang/CharSequence;Ljava/util/Date;)Ljava/lang/CharSequence;",
      lambda a: JString(format_java_pattern(_dt_from_ms(_obj_epoch_ms(stubs, a[1] if len(a) > 1 else None)), stubs._txt(a[0]) or "dd.MM.yyyy HH:mm")))
    R(ADF, "format", "(Ljava/lang/CharSequence;Ljava/util/Calendar;)Ljava/lang/CharSequence;",
      lambda a: JString(format_java_pattern(_dt_from_ms(_obj_epoch_ms(stubs, a[1] if len(a) > 1 else None)), stubs._txt(a[0]) or "dd.MM.yyyy HH:mm")))

    ADU = "Landroid/text/format/DateUtils;"

    def _rel_span_str(target_ms: int, now_ms: int, min_res: int = 60_000) -> JString:
        dt_t = _dt_from_ms(target_ms)
        dt_n = _dt_from_ms(now_ms)
        day_diff = (dt_t.date() - dt_n.date()).days
        if min_res >= 86_400_000:
            if day_diff == 0:
                return JString("Today")
            if day_diff == -1:
                return JString("Yesterday")
            if day_diff == 1:
                return JString("Tomorrow")
            return JString(format_java_pattern(dt_t, "EEEE, d. MMM"))
        diff_ms = now_ms - target_ms
        if abs(diff_ms) < 60_000:
            return JString("Just now")
        if 0 < diff_ms < 3_600_000:
            return JString(f"{max(1, diff_ms // 60_000)} min. ago")
        if 0 < diff_ms < 86_400_000 and day_diff == 0:
            return JString(f"{max(1, diff_ms // 3_600_000)} hr. ago")
        if day_diff == -1:
            return JString("Yesterday")
        if day_diff == 1:
            return JString("Tomorrow")
        return JString(format_java_pattern(dt_t, "d. MMM"))

    R(ADU, "isToday", "(J)Z",
      lambda a: 1 if _dt_from_ms(_s64(a[0]) if a else 0).date() == stubs.current_datetime().date() else 0)
    R(ADU, "getRelativeTimeSpanString", "(J)Ljava/lang/CharSequence;",
      lambda a: _rel_span_str(_s64(a[0]) if a else 0, int(stubs.current_time_ms(advance=False)), 60_000))
    R(ADU, "getRelativeTimeSpanString", "(JJJ)Ljava/lang/CharSequence;",
      lambda a: _rel_span_str(_s64(a[0]) if a else 0, _s64(a[1]) if len(a) > 1 else int(stubs.current_time_ms(advance=False)), _s64(a[2]) if len(a) > 2 else 60_000))
    R(ADU, "getRelativeTimeSpanString", "(JJJI)Ljava/lang/CharSequence;",
      lambda a: _rel_span_str(_s64(a[0]) if a else 0, _s64(a[1]) if len(a) > 1 else int(stubs.current_time_ms(advance=False)), _s64(a[2]) if len(a) > 2 else 60_000))
    R(ADU, "getRelativeTimeSpanString", "(Landroid/content/Context;JZ)Ljava/lang/CharSequence;",
      lambda a: _rel_span_str(_s64(a[1]) if len(a) > 1 else 0, int(stubs.current_time_ms(advance=False)), 60_000))
    R(ADU, "getRelativeTimeSpanString", "(Landroid/content/Context;J)Ljava/lang/CharSequence;",
      lambda a: _rel_span_str(_s64(a[1]) if len(a) > 1 else 0, int(stubs.current_time_ms(advance=False)), 60_000))
    R(ADU, "formatDateTime", "(Landroid/content/Context;JI)Ljava/lang/String;",
      lambda a: JString(format_java_pattern(_dt_from_ms(_s64(a[1]) if len(a) > 1 else 0), "d. MMM yyyy")))
    R(ADU, "formatElapsedTime", "(J)Ljava/lang/String;",
      lambda a: (lambda sec: JString(f"{sec // 3600}:{(sec % 3600) // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"))(max(0, _s64(a[0]) if a else 0)))

    R("Landroid/os/SystemClock;", "currentThreadTimeMillis", "()J", lambda a: stubs.current_time_ms(advance=False))
