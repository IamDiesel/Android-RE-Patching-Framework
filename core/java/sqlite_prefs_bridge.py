# -*- coding: utf-8 -*-
"""core.java.sqlite_prefs_bridge — G3: Echte sqlite3-SQLite/Room-Bridge, SharedPreferences-Sync & ActivityResultRegistry."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
from typing import Any, Dict, List, Optional

from core.java.objects import JArray, JObject, JString

_CURSOR_CLASSES = (
    "Landroid/database/Cursor;",
    "Landroid/database/sqlite/SQLiteCursor;",
    "Landroid/database/AbstractCursor;",
    "Landroid/database/MatrixCursor;",
)

_SP_TYPE_CLASSES = (
    "Ljava/lang/Boolean;",
    "Ljava/lang/Byte;",
    "Ljava/lang/Short;",
    "Ljava/lang/Integer;",
    "Ljava/lang/Long;",
    "Ljava/lang/Float;",
    "Ljava/lang/Double;",
)


def _s(v: Any) -> Optional[str]:
    if v is None or (isinstance(v, int) and not isinstance(v, bool) and v == 0):
        return None
    if isinstance(v, JString):
        return v.text
    if isinstance(v, JObject):
        if "text" in v.fields and isinstance(v.fields["text"], str):
            return v.fields["text"]
        if "$str" in v.fields:
            return str(v.fields["$str"])
        if "value" in v.fields and v.cls == "Ljava/lang/String;":
            return str(v.fields["value"])
        return None
    return str(v)


def _to_py(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, JString):
        return v.text
    if isinstance(v, JArray):
        if v.eltype in ("B", "[B"):
            return bytes(int(x) & 0xFF for x in (v.data or ()))
        return [_to_py(x) for x in (v.data or ())]
    if isinstance(v, JObject):
        if v.cls == "Ljava/lang/Boolean;":
            return 1 if v.fields.get("value") else 0
        if v.cls in _SP_TYPE_CLASSES:
            return v.fields.get("value")
        if v.cls == "Ljava/lang/String;":
            return v.fields.get("value", "")
        if "value" in v.fields:
            return _to_py(v.fields["value"])
        return _s(v)
    return v


def _sql_params(raw_args: Any) -> List[Any]:
    if raw_args is None:
        return []
    if isinstance(raw_args, JArray):
        return [_to_py(x) for x in (raw_args.data or ())]
    if isinstance(raw_args, (list, tuple)):
        return [_to_py(x) for x in raw_args]
    if isinstance(raw_args, JObject) and "__items" in raw_args.fields:
        return [_to_py(x) for x in (raw_args.fields.get("__items") or ())]
    return []


def _wrap_java_val(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, str):
        return JString(v)
    if isinstance(v, bytes):
        return JArray("[B", list(v))
    if isinstance(v, bool):
        return JObject("Ljava/lang/Boolean;", {"value": 1 if v else 0})
    if isinstance(v, int):
        return JObject("Ljava/lang/Integer;", {"value": v})
    if isinstance(v, float):
        return JObject("Ljava/lang/Double;", {"value": v})
    return v


def _safe_file_key(name: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in str(name or "default"))
    return clean.strip("._") or "default"


def _db_disk_path(stubs: Any, db_name: str) -> str:
    nm = str(db_name or "").strip()
    if not nm or nm == ":memory:":
        return ":memory:"
    appdata = getattr(stubs, "_appdata_dir", None)
    if not appdata:
        return ":memory:"
    bn = os.path.basename(nm.replace("\\", "/")) or "app.db"
    db_dir = os.path.join(appdata, "databases")
    os.makedirs(db_dir, exist_ok=True)
    return os.path.join(db_dir, bn)


def _get_conn(stubs: Any, db_name: str) -> sqlite3.Connection:
    conns: Dict[str, sqlite3.Connection] = stubs.__dict__.setdefault("_sqlite_conns", {})
    key = str(db_name or ":memory:").strip() or ":memory:"
    if key in conns:
        return conns[key]
    target = _db_disk_path(stubs, key)
    conn = sqlite3.connect(target, check_same_thread=False)
    conns[key] = conn
    stubs.__dict__.setdefault("_sqlite_db_paths", {})[key] = target
    stubs.__dict__.setdefault("_sqlite_tx_depth", {})[key] = []
    return conn


def open_db_obj(stubs: Any, db_name: str, cls_desc: str = "Landroid/database/sqlite/SQLiteDatabase;") -> JObject:
    key = str(db_name or ":memory:").strip() or ":memory:"
    _get_conn(stubs, key)
    return JObject(cls_desc, {"_db_key": key, "_closed": False})


def _auto_commit_if_idle(stubs: Any, key: str, conn: sqlite3.Connection) -> None:
    stack = stubs.__dict__.setdefault("_sqlite_tx_depth", {}).get(key, [])
    if not stack:
        conn.commit()


def _record_sqlite_err(stubs: Any, key: str, op: str, sql: Any, err: Any) -> None:
    counts = stubs.__dict__.setdefault("_sqlite_err_count", {})
    counts[key] = int(counts.get(key, 0) or 0) + 1
    vm = getattr(stubs, "vm", None)
    devs = getattr(vm, "deviations", None) if vm is not None else None
    if callable(getattr(devs, "record", None)):
        devs.record(
            "sqlite_error",
            f"{key}:{op}",
            f"{err!r} | {str(sql)[:140]}",
            step=getattr(vm, "steps", 0),
        )


def _sanitize_sql(sql: str) -> str:
    """Normalisiert haeufige dekompilierte DDL-Artefakte (z. B. `<col> 0 not null` -> `<col> TEXT not null`)."""
    s = str(sql or "")
    if not s:
        return s
    low = s.lstrip().lower()
    if low.startswith("create table"):
        s = re.sub(
            r"(?<=[(,])\s*([A-Za-z_]\w*)\s+0(?=\s*(?:unique|not\s+null|null|default|primary|references|,|\)))",
            r" \1 TEXT",
            s,
            flags=re.IGNORECASE,
        )
    return s


def _make_cursor(rows: List[tuple], cols: List[str]) -> JObject:
    return JObject(
        "Landroid/database/sqlite/SQLiteCursor;",
        {"_rows": list(rows), "_cols": list(cols), "_pos": -1, "_closed": False},
    )


def _cursor_row(cur_obj: Any) -> Optional[tuple]:
    if not isinstance(cur_obj, JObject):
        return None
    rows = cur_obj.fields.get("_rows") or []
    pos = int(cur_obj.fields.get("_pos", -1))
    if 0 <= pos < len(rows):
        return rows[pos]
    return None


def _bind_question_params(sql: str, params: List[Any]) -> tuple[str, List[Any]]:
    return sql, params


def _extract_support_query(stubs: Any, q_obj: Any) -> tuple[str, List[Any]]:
    if isinstance(q_obj, (str, JString)):
        return _s(q_obj) or "", []
    if not isinstance(q_obj, JObject):
        return "", []
    for k in ("_sql", "sql", "mSql", "query"):
        v = q_obj.fields.get(k)
        if v:
            sql_s = _s(v) or ""
            args_v = q_obj.fields.get("_bind_args") or q_obj.fields.get("mBindArgs") or q_obj.fields.get("bindArgs")
            return sql_s, _sql_params(args_v)
    vm = getattr(stubs, "vm", None)
    if vm is not None and hasattr(vm, "invoke_virtual"):
        try:
            sql_res = vm.invoke_virtual(q_obj, "getSql", "()Ljava/lang/String;", (q_obj,))
            return _s(sql_res) or "", []
        except Exception:
            pass
    return "", []


def _norm_pref_int(v: int) -> int:
    """Heilt vorzeichenlose 64-Bit-Zweierkomplement-Werte (z. B. -1 = 0xFFFFFFFFFFFFFFFF)."""
    x = int(v)
    if x >= (1 << 63):
        x &= (1 << 64) - 1
        return x - (1 << 64)
    return x


def _encode_pref_val(v: Any) -> Any:
    if isinstance(v, JString):
        return {"__t": "s", "v": v.text}
    if isinstance(v, JObject) and v.cls == "Ljava/lang/String;":
        return {"__t": "s", "v": str(v.fields.get("value", ""))}
    if isinstance(v, JObject) and ("__items" in (v.fields or {}) or "$items" in (v.fields or {})):
        items = v.fields.get("__items") or v.fields.get("$items") or ()
        return {"__t": "ss", "v": [_s(it) or "" for it in items]}
    if isinstance(v, bool):
        return {"__t": "b", "v": 1 if v else 0}
    if isinstance(v, int):
        return {"__t": "i", "v": _norm_pref_int(v)}
    if isinstance(v, float):
        return {"__t": "f", "v": v}
    if isinstance(v, str):
        return {"__t": "s", "v": v}
    return None


def _decode_pref_val(raw: Any) -> Any:
    if isinstance(raw, dict) and "__t" in raw:
        t = raw.get("__t")
        v = raw.get("v")
        if t == "s":
            return JString(str(v or ""))
        if t == "ss":
            items = [JString(str(x or "")) for x in (v or ())]
            return JObject("Ljava/util/LinkedHashSet;", {"__items": list(items), "$items": list(items)})
        if t == "b":
            return 1 if v else 0
        if t == "i":
            return _norm_pref_int(int(v or 0))
        if t == "f":
            return float(v or 0.0)
    if isinstance(raw, str):
        return JString(raw)
    if isinstance(raw, bool):
        return 1 if raw else 0
    if isinstance(raw, int):
        return _norm_pref_int(raw)
    return raw


def _ks_key_bytes(alias: str) -> bytes:
    return hashlib.sha256(f"glaesern-ks:{alias}".encode("utf-8")).digest()


def _make_secret_key(alias: str) -> JObject:
    return JObject("Ljavax/crypto/SecretKey;", {"_alias": alias, "_key_bytes": _ks_key_bytes(alias)})


def sync_java_appdata_from_dir(stubs: Any, host_dir: str) -> int:
    """Lädt persistierte SharedPreferences, KeyStore-Aliases und aktiviert SQLite-Disk-Pfade unter ``host_dir``."""
    if stubs is None or not host_dir:
        return 0
    stubs._appdata_dir = os.path.abspath(host_dir)
    sp_dir = os.path.join(stubs._appdata_dir, "shared_prefs")
    count = 0
    if os.path.isdir(sp_dir):
        prefs_store = getattr(stubs, "_prefs_store", None)
        if not isinstance(prefs_store, dict):
            prefs_store = {}
            stubs._prefs_store = prefs_store
        for fn in sorted(os.listdir(sp_dir)):
            if not fn.endswith(".json"):
                continue
            fp = os.path.join(sp_dir, fn)
            try:
                with open(fp, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
                if isinstance(payload, dict):
                    pname = str(payload.get("__name") or fn[:-5])
                    items = payload.get("items") if isinstance(payload.get("items"), dict) else payload
                    target = prefs_store.setdefault(pname, {})
                    for k, val in items.items():
                        if k == "__name":
                            continue
                        target[str(k)] = _decode_pref_val(val)
                    count += 1
            except Exception:
                continue
    ks_path = os.path.join(stubs._appdata_dir, "keystore.json")
    if os.path.isfile(ks_path):
        try:
            with open(ks_path, "r", encoding="utf-8") as kfh:
                kpayload = json.load(kfh)
            aliases = kpayload.get("aliases", []) if isinstance(kpayload, dict) else []
            if isinstance(aliases, list):
                ks_store = stubs.__dict__.setdefault("_keystore_aliases", {})
                for al in aliases:
                    al_s = str(al or "")
                    if al_s:
                        ks_store[al_s] = _make_secret_key(al_s)
                count += 1
        except Exception:
            pass
    return count


def clear_java_appdata_state(
    stubs: Any = None,
    app_dir: Optional[str] = None,
    fusion: Any = None,
    package: Optional[str] = None,
) -> Dict[str, Any]:
    """Schliesst SQLite-Handles, leert In-Memory-Prefs/KeyStore/VFS und loescht das AppData-Verzeichnis."""
    import shutil

    target_dir = os.path.abspath(app_dir) if app_dir else None
    if not target_dir and stubs is not None:
        raw_dir = getattr(stubs, "_appdata_dir", None)
        if raw_dir:
            target_dir = os.path.abspath(raw_dir)

    if stubs is not None:
        conns = getattr(stubs, "_sqlite_conns", None)
        if isinstance(conns, dict):
            for conn in list(conns.values()):
                try:
                    conn.close()
                except Exception:
                    pass
            conns.clear()
        for attr in ("_sqlite_db_paths", "_sqlite_tx_depth", "_prefs_store", "_keystore_aliases"):
            dct = getattr(stubs, attr, None)
            if isinstance(dct, dict):
                dct.clear()

    if fusion and isinstance(fusion, dict) and fusion.get("eng") is not None and package:
        try:
            from core.java.fs_bridge import get_shared_fs
            fs = get_shared_fs(fusion["eng"])
            prefixes = (f"/data/data/{package}", f"/data/user/0/{package}")
            for fp in [k for k in list(getattr(fs, "files", {}).keys()) if k.startswith(prefixes)]:
                fs.files.pop(fp, None)
        except Exception:
            pass

    removed_files = 0
    if target_dir and os.path.isdir(target_dir):
        for _root, _dirs, files in os.walk(target_dir):
            removed_files += len(files)
        shutil.rmtree(target_dir, ignore_errors=True)

    return {
        "ok": True,
        "appdata_dir": target_dir,
        "removed_files": removed_files,
    }


def persist_java_appdata_to_dir(stubs: Any, host_dir: str) -> int:
    """Persistiert SharedPreferences nach ``<host_dir>/shared_prefs``, KeyStore-Aliases und flusht SQLite-DBs."""
    if stubs is None or not host_dir:
        return 0
    abs_dir = os.path.abspath(host_dir)
    stubs._appdata_dir = abs_dir
    sp_dir = os.path.join(abs_dir, "shared_prefs")
    count = 0
    prefs_store = getattr(stubs, "_prefs_store", None) or {}
    if prefs_store:
        os.makedirs(sp_dir, exist_ok=True)
        for pname, store in list(prefs_store.items()):
            if not isinstance(store, dict):
                continue
            enc_items = {}
            for k, v in store.items():
                ev = _encode_pref_val(v)
                if ev is not None:
                    enc_items[str(k)] = ev
            out_path = os.path.join(sp_dir, f"{_safe_file_key(pname)}.json")
            try:
                tmp = out_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump({"__name": pname, "items": enc_items}, fh, ensure_ascii=False, indent=2)
                os.replace(tmp, out_path)
                count += 1
            except Exception:
                continue

    ks_aliases = getattr(stubs, "_keystore_aliases", None) or {}
    if isinstance(ks_aliases, dict) and ks_aliases:
        os.makedirs(abs_dir, exist_ok=True)
        ks_path = os.path.join(abs_dir, "keystore.json")
        try:
            tmp_ks = ks_path + ".tmp"
            with open(tmp_ks, "w", encoding="utf-8") as kfh:
                json.dump({"aliases": sorted(str(k) for k in ks_aliases.keys() if k)}, kfh, ensure_ascii=False, indent=2)
            os.replace(tmp_ks, ks_path)
            count += 1
        except Exception:
            pass

    conns = getattr(stubs, "_sqlite_conns", None) or {}
    db_paths = getattr(stubs, "_sqlite_db_paths", None) or {}
    for key, conn in list(conns.items()):
        try:
            conn.commit()
            if db_paths.get(key) == ":memory:" and key != ":memory:":
                disk_dst = _db_disk_path(stubs, key)
                if disk_dst and disk_dst != ":memory:":
                    dst_conn = sqlite3.connect(disk_dst)
                    try:
                        conn.backup(dst_conn)
                        dst_conn.commit()
                    finally:
                        dst_conn.close()
                    count += 1
        except Exception:
            continue
    return count


def install_sqlite_and_prefs_grundset(stubs: Any) -> None:
    """Registriert G3-Grundset-Stubs (ContentValues, SQLite, Room, Cursor, SharedPrefs, ActivityResult)."""
    if stubs is None or getattr(stubs, "_g3_installed", False):
        return
    stubs._g3_installed = True
    R = stubs.register
    RB = stubs.register_by_name

    # ---- SharedPreferences Fix (PreferenceManager + getAll) -----------------
    def _pref_mgr_default(a):
        return JObject("Landroid/content/SharedPreferences;", {"__prefs_name": "default"})

    R("Landroid/preference/PreferenceManager;", "getDefaultSharedPreferences",
      "(Landroid/content/Context;)Landroid/content/SharedPreferences;", _pref_mgr_default)
    R("Landroidx/preference/PreferenceManager;", "getDefaultSharedPreferences",
      "(Landroid/content/Context;)Landroid/content/SharedPreferences;", _pref_mgr_default)

    def _sp_get_all(a):
        nm = a[0].fields.get("__prefs_name", "default") if a and isinstance(a[0], JObject) else "default"
        raw = dict(stubs._prefs_store.get(nm, {}))
        boxed = {str(k): _wrap_java_val(v) for k, v in raw.items()}
        rkeys = {str(k): JString(str(k)) for k in boxed.keys()}
        return JObject("Ljava/util/HashMap;", {"map": boxed, "__raw_keys": rkeys})

    R("Landroid/content/SharedPreferences;", "getAll", "()Ljava/util/Map;", _sp_get_all)

    # ---- ContentValues ----------------------------------------------------
    CV = "Landroid/content/ContentValues;"

    def _cv_map(obj: Any) -> Dict[str, Any]:
        if isinstance(obj, JObject):
            m = obj.fields.get("__cv")
            if m is None:
                m = obj.fields["__cv"] = {}
            return m
        return {}

    def _cv_init(a):
        if a and isinstance(a[0], JObject):
            init_m = dict(_cv_map(a[1])) if (len(a) > 1 and isinstance(a[1], JObject)) else {}
            a[0].fields["__cv"] = init_m
        return None

    def _cv_put(a):
        if a and len(a) > 2:
            k = _s(a[1])
            if k is not None:
                _cv_map(a[0])[k] = _to_py(a[2])
        return None

    R(CV, "<init>", "()V", _cv_init)
    R(CV, "<init>", "(I)V", _cv_init)
    R(CV, "<init>", "(Landroid/content/ContentValues;)V", _cv_init)
    for _pt in (
        "Ljava/lang/String;", "Ljava/lang/Integer;", "Ljava/lang/Long;",
        "Ljava/lang/Float;", "Ljava/lang/Double;", "Ljava/lang/Boolean;",
        "Ljava/lang/Short;", "Ljava/lang/Byte;", "[B",
    ):
        R(CV, "put", f"(Ljava/lang/String;{_pt})V", _cv_put)
    R(CV, "putNull", "(Ljava/lang/String;)V",
      lambda a: _cv_map(a[0]).__setitem__(_s(a[1]), None) if a and len(a) > 1 and _s(a[1]) else None)
    R(CV, "putAll", "(Landroid/content/ContentValues;)V",
      lambda a: _cv_map(a[0]).update(_cv_map(a[1])) if a and len(a) > 1 else None)
    R(CV, "get", "(Ljava/lang/String;)Ljava/lang/Object;",
      lambda a: _wrap_java_val(_cv_map(a[0]).get(_s(a[1]))) if a and len(a) > 1 else None)
    R(CV, "getAsString", "(Ljava/lang/String;)Ljava/lang/String;",
      lambda a: JString(str(_cv_map(a[0]).get(_s(a[1])))) if (a and len(a) > 1 and _cv_map(a[0]).get(_s(a[1])) is not None) else None)
    R(CV, "getAsInteger", "(Ljava/lang/String;)Ljava/lang/Integer;",
      lambda a: JObject("Ljava/lang/Integer;", {"value": int(_cv_map(a[0]).get(_s(a[1])))}) if (a and len(a) > 1 and _cv_map(a[0]).get(_s(a[1])) is not None) else None)
    R(CV, "getAsLong", "(Ljava/lang/String;)Ljava/lang/Long;",
      lambda a: JObject("Ljava/lang/Long;", {"value": int(_cv_map(a[0]).get(_s(a[1])))}) if (a and len(a) > 1 and _cv_map(a[0]).get(_s(a[1])) is not None) else None)
    R(CV, "getAsBoolean", "(Ljava/lang/String;)Ljava/lang/Boolean;",
      lambda a: JObject("Ljava/lang/Boolean;", {"value": 1 if _cv_map(a[0]).get(_s(a[1])) else 0}) if (a and len(a) > 1 and _s(a[1]) in _cv_map(a[0])) else None)
    R(CV, "containsKey", "(Ljava/lang/String;)Z",
      lambda a: 1 if (a and len(a) > 1 and _s(a[1]) in _cv_map(a[0])) else 0)
    R(CV, "size", "()I", lambda a: len(_cv_map(a[0])) if a else 0)
    R(CV, "isEmpty", "()Z", lambda a: 1 if (not a or not _cv_map(a[0])) else 0)
    R(CV, "clear", "()V", lambda a: (_cv_map(a[0]).clear(), None)[1] if a else None)
    R(CV, "remove", "(Ljava/lang/String;)V",
      lambda a: (_cv_map(a[0]).pop(_s(a[1]), None), None)[1] if a and len(a) > 1 else None)
    R(CV, "keySet", "()Ljava/util/Set;",
      lambda a: JObject("Ljava/util/LinkedHashSet;", {"__items": [JString(k) for k in _cv_map(a[0]).keys()]}) if a else JObject("Ljava/util/LinkedHashSet;", {"__items": []}))

    # ---- Cursor -----------------------------------------------------------
    def _cur_move_to(cur_obj, new_pos):
        if not isinstance(cur_obj, JObject):
            return 0
        n = len(cur_obj.fields.get("_rows") or ())
        if new_pos < 0:
            cur_obj.fields["_pos"] = -1
            return 0
        if new_pos >= n:
            cur_obj.fields["_pos"] = n
            return 0
        cur_obj.fields["_pos"] = new_pos
        return 1

    def _cur_val(a):
        row = _cursor_row(a[0] if a else None)
        idx = int(a[1]) if len(a) > 1 else -1
        if row is None or not (0 <= idx < len(row)):
            return None
        return row[idx]

    def _cur_col_idx(a):
        if not a or not isinstance(a[0], JObject) or len(a) < 2:
            return -1
        cname = (_s(a[1]) or "").lower()
        cols = [str(c).lower() for c in (a[0].fields.get("_cols") or ())]
        try:
            return cols.index(cname)
        except ValueError:
            return -1

    def _cur_type(v):
        if v is None:
            return 0
        if isinstance(v, (int, bool)):
            return 1
        if isinstance(v, float):
            return 2
        if isinstance(v, (bytes, bytearray)):
            return 4
        return 3

    for c_cls in _CURSOR_CLASSES:
        R(c_cls, "getCount", "()I", lambda a: len(a[0].fields.get("_rows") or ()) if a and isinstance(a[0], JObject) else 0)
        R(c_cls, "getPosition", "()I", lambda a: int(a[0].fields.get("_pos", -1)) if a and isinstance(a[0], JObject) else -1)
        R(c_cls, "moveToFirst", "()Z", lambda a: _cur_move_to(a[0] if a else None, 0))
        R(c_cls, "moveToLast", "()Z", lambda a: _cur_move_to(a[0] if a else None, len(a[0].fields.get("_rows") or ()) - 1) if a and isinstance(a[0], JObject) else 0)
        R(c_cls, "moveToNext", "()Z", lambda a: _cur_move_to(a[0] if a else None, int(a[0].fields.get("_pos", -1)) + 1) if a and isinstance(a[0], JObject) else 0)
        R(c_cls, "moveToPrevious", "()Z", lambda a: _cur_move_to(a[0] if a else None, int(a[0].fields.get("_pos", -1)) - 1) if a and isinstance(a[0], JObject) else 0)
        R(c_cls, "moveToPosition", "(I)Z", lambda a: _cur_move_to(a[0] if a else None, int(a[1])) if a and len(a) > 1 else 0)
        R(c_cls, "isFirst", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_rows") and int(a[0].fields.get("_pos", -1)) == 0) else 0)
        R(c_cls, "isLast", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_rows") and int(a[0].fields.get("_pos", -1)) == len(a[0].fields.get("_rows")) - 1) else 0)
        R(c_cls, "isBeforeFirst", "()Z", lambda a: 1 if (not a or not isinstance(a[0], JObject) or int(a[0].fields.get("_pos", -1)) < 0) else 0)
        R(c_cls, "isAfterLast", "()Z", lambda a: 1 if (not a or not isinstance(a[0], JObject) or int(a[0].fields.get("_pos", -1)) >= len(a[0].fields.get("_rows") or ())) else 0)
        R(c_cls, "getColumnIndex", "(Ljava/lang/String;)I", _cur_col_idx)
        R(c_cls, "getColumnIndexOrThrow", "(Ljava/lang/String;)I", _cur_col_idx)
        R(c_cls, "getColumnName", "(I)Ljava/lang/String;",
          lambda a: JString((a[0].fields.get("_cols") or [])[int(a[1])]) if a and len(a) > 1 and isinstance(a[0], JObject) and 0 <= int(a[1]) < len(a[0].fields.get("_cols") or ()) else JString(""))
        R(c_cls, "getColumnNames", "()[Ljava/lang/String;",
          lambda a: JArray("[Ljava/lang/String;", [JString(c) for c in (a[0].fields.get("_cols") or ())]) if a and isinstance(a[0], JObject) else JArray("[Ljava/lang/String;", []))
        R(c_cls, "getColumnCount", "()I", lambda a: len(a[0].fields.get("_cols") or ()) if a and isinstance(a[0], JObject) else 0)
        R(c_cls, "getString", "(I)Ljava/lang/String;", lambda a: (lambda v: None if v is None else JString(str(v)))(_cur_val(a)))
        R(c_cls, "getInt", "(I)I", lambda a: (lambda v: 0 if v is None else int(v))(_cur_val(a)))
        R(c_cls, "getLong", "(I)J", lambda a: (lambda v: 0 if v is None else int(v))(_cur_val(a)))
        R(c_cls, "getFloat", "(I)F", lambda a: (lambda v: 0.0 if v is None else float(v))(_cur_val(a)))
        R(c_cls, "getDouble", "(I)D", lambda a: (lambda v: 0.0 if v is None else float(v))(_cur_val(a)))
        R(c_cls, "getShort", "(I)S", lambda a: (lambda v: 0 if v is None else int(v))(_cur_val(a)))
        R(c_cls, "getBlob", "(I)[B", lambda a: (lambda v: None if v is None else JArray("[B", list(bytes(v) if isinstance(v, (bytes, bytearray)) else str(v).encode("utf-8"))))(_cur_val(a)))
        R(c_cls, "getType", "(I)I", lambda a: _cur_type(_cur_val(a)))
        R(c_cls, "isNull", "(I)Z", lambda a: 1 if _cur_val(a) is None else 0)
        R(c_cls, "close", "()V", lambda a: (a[0].fields.__setitem__("_closed", True), None)[1] if a and isinstance(a[0], JObject) else None)
        R(c_cls, "isClosed", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_closed")) else 0)

    MC = "Landroid/database/MatrixCursor;"
    R(MC, "<init>", "([Ljava/lang/String;)V",
      lambda a: a[0].fields.update({"_cols": [str(x) for x in _sql_params(a[1])], "_rows": [], "_pos": -1, "_closed": False}) if a and len(a) > 1 and isinstance(a[0], JObject) else None)
    R(MC, "<init>", "([Ljava/lang/String;I)V",
      lambda a: a[0].fields.update({"_cols": [str(x) for x in _sql_params(a[1])], "_rows": [], "_pos": -1, "_closed": False}) if a and len(a) > 1 and isinstance(a[0], JObject) else None)
    R(MC, "addRow", "([Ljava/lang/Object;)V",
      lambda a: a[0].fields.setdefault("_rows", []).append(tuple(_sql_params(a[1]))) if a and len(a) > 1 and isinstance(a[0], JObject) else None)

    # ---- SQLiteDatabase & SupportSQLiteDatabase ---------------------------
    def _db_conn(db_obj: Any) -> tuple[str, sqlite3.Connection]:
        key = db_obj.fields.get("_db_key", ":memory:") if isinstance(db_obj, JObject) else ":memory:"
        return key, _get_conn(stubs, key)

    def _exec_sql(a):
        if not a or len(a) < 2:
            return None
        key, conn = _db_conn(a[0])
        sql = _sanitize_sql(_s(a[1]) or "")
        params = _sql_params(a[2]) if len(a) > 2 else []
        if sql.strip():
            try:
                conn.execute(sql, params)
                _auto_commit_if_idle(stubs, key, conn)
            except sqlite3.Error as _sqe:
                _record_sqlite_err(stubs, key, "execSQL", sql, _sqe)
        return None

    def _raw_query(a):
        if not a or len(a) < 2:
            return _make_cursor([], [])
        key, conn = _db_conn(a[0])
        if len(a) == 2 and not isinstance(a[1], (str, JString)):
            sql, params = _extract_support_query(stubs, a[1])
        else:
            sql = _s(a[1]) or ""
            params = _sql_params(a[2]) if len(a) > 2 else []
        sql = _sanitize_sql(sql)
        if not sql.strip():
            return _make_cursor([], [])
        try:
            cur = conn.execute(sql, params)
            cols = [d[0] for d in (cur.description or ())]
            rows = cur.fetchall()
            return _make_cursor(rows, cols)
        except sqlite3.Error as _sqe:
            _record_sqlite_err(stubs, key, "rawQuery", sql, _sqe)
            return _make_cursor([], [])

    def _query_structured(a):
        # Handles:
        #   query(table, cols, sel, selArgs, groupBy, having, orderBy[, limit])
        #   query(distinct, table, cols, sel, selArgs, groupBy, having, orderBy, limit[, cancelSig])
        if not a:
            return _make_cursor([], [])
        if len(a) == 2:
            return _raw_query(a)
        key, conn = _db_conn(a[0])
        idx = 1
        distinct = False
        if isinstance(a[1], int):
            distinct = bool(a[1])
            idx = 2
        table = _s(a[idx]) if idx < len(a) else ""
        cols = _sql_params(a[idx + 1]) if idx + 1 < len(a) else []
        sel = _s(a[idx + 2]) if idx + 2 < len(a) else None
        sel_args = _sql_params(a[idx + 3]) if idx + 3 < len(a) else []
        grp = _s(a[idx + 4]) if idx + 4 < len(a) else None
        hav = _s(a[idx + 5]) if idx + 5 < len(a) else None
        ord_by = _s(a[idx + 6]) if idx + 6 < len(a) else None
        lim = _s(a[idx + 7]) if idx + 7 < len(a) else None
        col_sql = ", ".join(cols) if cols else "*"
        parts = [f"SELECT {'DISTINCT ' if distinct else ''}{col_sql} FROM {table}"]
        if sel:
            parts.append(f"WHERE {sel}")
        if grp:
            parts.append(f"GROUP BY {grp}")
        if hav:
            parts.append(f"HAVING {hav}")
        if ord_by:
            parts.append(f"ORDER BY {ord_by}")
        if lim:
            parts.append(f"LIMIT {lim}")
        sql = " ".join(parts)
        try:
            cur = conn.execute(sql, sel_args)
            cnames = [d[0] for d in (cur.description or ())]
            return _make_cursor(cur.fetchall(), cnames)
        except sqlite3.Error as _sqe:
            _record_sqlite_err(stubs, key, "query", sql, _sqe)
            return _make_cursor([], [])

    def _db_insert(a, replace: bool = False):
        if not a or len(a) < 4:
            return -1
        key, conn = _db_conn(a[0])
        table = _s(a[1]) or ""
        null_hack = _s(a[2])
        cv = _cv_map(a[3]) if isinstance(a[3], JObject) else {}
        verb = "INSERT OR REPLACE" if replace else "INSERT"
        if not cv:
            if null_hack:
                sql = f"{verb} INTO {table} ({null_hack}) VALUES (NULL)"
                vals = []
            else:
                sql = f"{verb} INTO {table} DEFAULT VALUES"
                vals = []
        else:
            keys = list(cv.keys())
            cols_s = ", ".join(keys)
            ph_s = ", ".join("?" for _ in keys)
            sql = f"{verb} INTO {table} ({cols_s}) VALUES ({ph_s})"
            vals = [cv[k] for k in keys]
        try:
            cur = conn.execute(sql, vals)
            _auto_commit_if_idle(stubs, key, conn)
            return int(cur.lastrowid or 0)
        except sqlite3.Error as _sqe:
            _record_sqlite_err(stubs, key, "replace" if replace else "insert", sql, _sqe)
            return -1

    def _db_insert_conflict(a):
        algo = int(a[4]) if len(a) > 4 else 0
        # CONFLICT_REPLACE = 5, CONFLICT_IGNORE = 4
        if algo == 5:
            return _db_insert(a[:4], replace=True)
        if algo == 4:
            if not a or len(a) < 4:
                return -1
            key, conn = _db_conn(a[0])
            table = _s(a[1]) or ""
            cv = _cv_map(a[3]) if isinstance(a[3], JObject) else {}
            if cv:
                keys = list(cv.keys())
                sql = f"INSERT OR IGNORE INTO {table} ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})"
                try:
                    cur = conn.execute(sql, [cv[k] for k in keys])
                    _auto_commit_if_idle(stubs, key, conn)
                    return int(cur.lastrowid or 0)
                except sqlite3.Error as _sqe:
                    _record_sqlite_err(stubs, key, "insertWithOnConflict", sql, _sqe)
                    return -1
        return _db_insert(a[:4], replace=False)

    def _db_update(a):
        if not a or len(a) < 3:
            return 0
        key, conn = _db_conn(a[0])
        # Android SQLiteDatabase.update(table, values, whereClause, whereArgs)
        # SupportSQLiteDatabase.update(table, conflictAlgorithm, values, whereClause, whereArgs)
        if len(a) >= 5 and isinstance(a[2], int):
            table = _s(a[1]) or ""
            cv = _cv_map(a[3]) if isinstance(a[3], JObject) else {}
            where = _s(a[4])
            wargs = _sql_params(a[5]) if len(a) > 5 else []
        else:
            table = _s(a[1]) or ""
            cv = _cv_map(a[2]) if isinstance(a[2], JObject) else {}
            where = _s(a[3]) if len(a) > 3 else None
            wargs = _sql_params(a[4]) if len(a) > 4 else []
        if not cv:
            return 0
        keys = list(cv.keys())
        sets = ", ".join(f"{k}=?" for k in keys)
        sql = f"UPDATE {table} SET {sets}" + (f" WHERE {where}" if where else "")
        try:
            cur = conn.execute(sql, [cv[k] for k in keys] + wargs)
            _auto_commit_if_idle(stubs, key, conn)
            return int(cur.rowcount or 0)
        except sqlite3.Error as _sqe:
            _record_sqlite_err(stubs, key, "update", sql, _sqe)
            return 0

    def _db_delete(a):
        if not a or len(a) < 2:
            return 0
        key, conn = _db_conn(a[0])
        table = _s(a[1]) or ""
        where = _s(a[2]) if len(a) > 2 else None
        wargs = _sql_params(a[3]) if len(a) > 3 else []
        sql = f"DELETE FROM {table}" + (f" WHERE {where}" if where else "")
        try:
            cur = conn.execute(sql, wargs)
            _auto_commit_if_idle(stubs, key, conn)
            return int(cur.rowcount or 0)
        except sqlite3.Error as _sqe:
            _record_sqlite_err(stubs, key, "delete", sql, _sqe)
            return 0

    def _db_begin_tx(a):
        if not a:
            return None
        key, conn = _db_conn(a[0])
        stack = stubs.__dict__.setdefault("_sqlite_tx_depth", {}).setdefault(key, [])
        if not stack:
            conn.execute("BEGIN")
        stack.append(False)
        return None

    def _db_set_tx_ok(a):
        if not a:
            return None
        key, _ = _db_conn(a[0])
        stack = stubs.__dict__.setdefault("_sqlite_tx_depth", {}).get(key, [])
        if stack:
            stack[-1] = True
        return None

    def _db_end_tx(a):
        if not a:
            return None
        key, conn = _db_conn(a[0])
        stack = stubs.__dict__.setdefault("_sqlite_tx_depth", {}).get(key, [])
        if stack:
            ok_flag = stack.pop()
            if not stack:
                if ok_flag:
                    conn.commit()
                else:
                    conn.rollback()
            elif not ok_flag:
                stack[-1] = False
        return None

    def _db_in_tx(a):
        if not a:
            return 0
        key, _ = _db_conn(a[0])
        return 1 if stubs.__dict__.setdefault("_sqlite_tx_depth", {}).get(key) else 0

    def _db_get_version(a):
        _, conn = _db_conn(a[0])
        row = conn.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row else 0

    def _db_set_version(a):
        key, conn = _db_conn(a[0])
        v = int(a[1]) if len(a) > 1 else 0
        conn.execute(f"PRAGMA user_version = {v}")
        _auto_commit_if_idle(stubs, key, conn)
        return None

    def _db_compile_stmt(a):
        key, _ = _db_conn(a[0])
        sql = _s(a[1]) if len(a) > 1 else ""
        return JObject("Landroid/database/sqlite/SQLiteStatement;", {"_db_key": key, "_sql": sql or "", "_binds": {}})

    for db_cls in ("Landroid/database/sqlite/SQLiteDatabase;", "Landroidx/sqlite/db/SupportSQLiteDatabase;"):
        R(db_cls, "openOrCreateDatabase", "(Ljava/lang/String;Landroid/database/sqlite/SQLiteDatabase$CursorFactory;)Landroid/database/sqlite/SQLiteDatabase;",
          lambda a: open_db_obj(stubs, _s(a[0]) if a else ":memory:"))
        R(db_cls, "openOrCreateDatabase", "(Ljava/io/File;Landroid/database/sqlite/SQLiteDatabase$CursorFactory;)Landroid/database/sqlite/SQLiteDatabase;",
          lambda a: open_db_obj(stubs, (a[0].fields.get("path") if a and isinstance(a[0], JObject) else ":memory:")))
        R(db_cls, "openDatabase", "(Ljava/lang/String;Landroid/database/sqlite/SQLiteDatabase$CursorFactory;I)Landroid/database/sqlite/SQLiteDatabase;",
          lambda a: open_db_obj(stubs, _s(a[0]) if a else ":memory:"))
        R(db_cls, "create", "(Landroid/database/sqlite/SQLiteDatabase$CursorFactory;)Landroid/database/sqlite/SQLiteDatabase;",
          lambda a: open_db_obj(stubs, ":memory:"))
        R(db_cls, "execSQL", "(Ljava/lang/String;)V", _exec_sql)
        R(db_cls, "execSQL", "(Ljava/lang/String;[Ljava/lang/Object;)V", _exec_sql)
        R(db_cls, "rawQuery", "(Ljava/lang/String;[Ljava/lang/String;)Landroid/database/Cursor;", _raw_query)
        R(db_cls, "rawQuery", "(Ljava/lang/String;[Ljava/lang/String;Landroid/os/CancellationSignal;)Landroid/database/Cursor;", _raw_query)
        R(db_cls, "query", "(Ljava/lang/String;)Landroid/database/Cursor;", _raw_query)
        R(db_cls, "query", "(Ljava/lang/String;[Ljava/lang/Object;)Landroid/database/Cursor;", _raw_query)
        R(db_cls, "query", "(Landroidx/sqlite/db/SupportSQLiteQuery;)Landroid/database/Cursor;", _raw_query)
        R(db_cls, "query", "(Landroidx/sqlite/db/SupportSQLiteQuery;Landroid/os/CancellationSignal;)Landroid/database/Cursor;", _raw_query)
        R(db_cls, "query", "(Ljava/lang/String;[Ljava/lang/String;Ljava/lang/String;[Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)Landroid/database/Cursor;", _query_structured)
        R(db_cls, "query", "(Ljava/lang/String;[Ljava/lang/String;Ljava/lang/String;[Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)Landroid/database/Cursor;", _query_structured)
        R(db_cls, "query", "(ZLjava/lang/String;[Ljava/lang/String;Ljava/lang/String;[Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)Landroid/database/Cursor;", _query_structured)
        R(db_cls, "insert", "(Ljava/lang/String;Ljava/lang/String;Landroid/content/ContentValues;)J", lambda a: _db_insert(a, False))
        R(db_cls, "insertOrThrow", "(Ljava/lang/String;Ljava/lang/String;Landroid/content/ContentValues;)J", lambda a: _db_insert(a, False))
        R(db_cls, "replace", "(Ljava/lang/String;Ljava/lang/String;Landroid/content/ContentValues;)J", lambda a: _db_insert(a, True))
        R(db_cls, "replaceOrThrow", "(Ljava/lang/String;Ljava/lang/String;Landroid/content/ContentValues;)J", lambda a: _db_insert(a, True))
        R(db_cls, "insertWithOnConflict", "(Ljava/lang/String;Ljava/lang/String;Landroid/content/ContentValues;I)J", _db_insert_conflict)
        R(db_cls, "insert", "(Ljava/lang/String;ILandroid/content/ContentValues;)J",
          lambda a: _db_insert([a[0], a[1], None, a[3]], replace=(int(a[2]) == 5)))
        R(db_cls, "update", "(Ljava/lang/String;Landroid/content/ContentValues;Ljava/lang/String;[Ljava/lang/String;)I", _db_update)
        R(db_cls, "update", "(Ljava/lang/String;ILandroid/content/ContentValues;Ljava/lang/String;[Ljava/lang/Object;)I", _db_update)
        R(db_cls, "updateWithOnConflict", "(Ljava/lang/String;Landroid/content/ContentValues;Ljava/lang/String;[Ljava/lang/String;I)I", _db_update)
        R(db_cls, "delete", "(Ljava/lang/String;Ljava/lang/String;[Ljava/lang/String;)I", _db_delete)
        R(db_cls, "delete", "(Ljava/lang/String;Ljava/lang/String;[Ljava/lang/Object;)I", _db_delete)
        R(db_cls, "compileStatement", "(Ljava/lang/String;)Landroid/database/sqlite/SQLiteStatement;", _db_compile_stmt)
        R(db_cls, "compileStatement", "(Ljava/lang/String;)Landroidx/sqlite/db/SupportSQLiteStatement;", _db_compile_stmt)
        R(db_cls, "beginTransaction", "()V", _db_begin_tx)
        R(db_cls, "beginTransactionNonExclusive", "()V", _db_begin_tx)
        R(db_cls, "setTransactionSuccessful", "()V", _db_set_tx_ok)
        R(db_cls, "endTransaction", "()V", _db_end_tx)
        R(db_cls, "inTransaction", "()Z", _db_in_tx)
        R(db_cls, "getVersion", "()I", _db_get_version)
        R(db_cls, "setVersion", "(I)V", _db_set_version)
        R(db_cls, "isOpen", "()Z", lambda a: 0 if (a and isinstance(a[0], JObject) and a[0].fields.get("_closed")) else 1)
        R(db_cls, "close", "()V", lambda a: (a[0].fields.__setitem__("_closed", True), None)[1] if a and isinstance(a[0], JObject) else None)
        R(db_cls, "getPath", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("_db_key", ":memory:")) if a and isinstance(a[0], JObject) else JString(":memory:"))

    # ---- SQLiteStatement & SupportSQLiteStatement -------------------------
    def _stmt_exec(stmt_obj, mode="exec"):
        if not isinstance(stmt_obj, JObject):
            return 0 if mode in ("insert", "update", "long") else (JString("") if mode == "str" else None)
        key = stmt_obj.fields.get("_db_key", ":memory:")
        conn = _get_conn(stubs, key)
        sql = stmt_obj.fields.get("_sql") or ""
        binds = stmt_obj.fields.get("_binds") or {}
        max_idx = max(binds.keys(), default=0)
        params = [binds.get(i) for i in range(1, max_idx + 1)] if max_idx else []
        san_sql = _sanitize_sql(sql)
        try:
            cur = conn.execute(san_sql, params)
        except sqlite3.Error as _sqe:
            _record_sqlite_err(stubs, key, f"stmt_{mode}", san_sql, _sqe)
            return 0 if mode in ("insert", "update", "long") else (JString("") if mode == "str" else None)
        if mode == "insert":
            _auto_commit_if_idle(stubs, key, conn)
            return int(cur.lastrowid or 0)
        if mode == "update":
            _auto_commit_if_idle(stubs, key, conn)
            return int(cur.rowcount or 0)
        if mode == "long":
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        if mode == "str":
            row = cur.fetchone()
            return JString(str(row[0])) if row and row[0] is not None else None
        _auto_commit_if_idle(stubs, key, conn)
        return None

    for st_cls in ("Landroid/database/sqlite/SQLiteStatement;", "Landroidx/sqlite/db/SupportSQLiteStatement;", "Landroidx/sqlite/db/SupportSQLiteProgram;"):
        R(st_cls, "bindString", "(ILjava/lang/String;)V",
          lambda a: a[0].fields.setdefault("_binds", {}).__setitem__(int(a[1]), _s(a[2])) if a and len(a) > 2 and isinstance(a[0], JObject) else None)
        R(st_cls, "bindLong", "(IJ)V",
          lambda a: a[0].fields.setdefault("_binds", {}).__setitem__(int(a[1]), int(a[2])) if a and len(a) > 2 and isinstance(a[0], JObject) else None)
        R(st_cls, "bindDouble", "(ID)V",
          lambda a: a[0].fields.setdefault("_binds", {}).__setitem__(int(a[1]), float(a[2])) if a and len(a) > 2 and isinstance(a[0], JObject) else None)
        R(st_cls, "bindBlob", "(I[B)V",
          lambda a: a[0].fields.setdefault("_binds", {}).__setitem__(int(a[1]), _to_py(a[2])) if a and len(a) > 2 and isinstance(a[0], JObject) else None)
        R(st_cls, "bindNull", "(I)V",
          lambda a: a[0].fields.setdefault("_binds", {}).__setitem__(int(a[1]), None) if a and len(a) > 1 and isinstance(a[0], JObject) else None)
        R(st_cls, "clearBindings", "()V",
          lambda a: (a[0].fields.setdefault("_binds", {}).clear(), None)[1] if a and isinstance(a[0], JObject) else None)
        R(st_cls, "execute", "()V", lambda a: _stmt_exec(a[0] if a else None, "exec"))
        R(st_cls, "executeInsert", "()J", lambda a: _stmt_exec(a[0] if a else None, "insert"))
        R(st_cls, "executeUpdateDelete", "()I", lambda a: _stmt_exec(a[0] if a else None, "update"))
        R(st_cls, "simpleQueryForLong", "()J", lambda a: _stmt_exec(a[0] if a else None, "long"))
        R(st_cls, "simpleQueryForString", "()Ljava/lang/String;", lambda a: _stmt_exec(a[0] if a else None, "str"))
        R(st_cls, "close", "()V", lambda a: None)

    # ---- SQLiteOpenHelper & Room ------------------------------------------
    def _oh_init(a):
        if a and isinstance(a[0], JObject):
            nm = _s(a[2]) if len(a) > 2 else ":memory:"
            ver = int(a[4]) if (len(a) > 4 and isinstance(a[4], int)) else 1
            a[0].fields["_db_name"] = nm or ":memory:"
            a[0].fields["_target_version"] = max(1, ver)
        return None

    R("Landroid/database/sqlite/SQLiteOpenHelper;", "<init>",
      "(Landroid/content/Context;Ljava/lang/String;Landroid/database/sqlite/SQLiteDatabase$CursorFactory;I)V", _oh_init)
    R("Landroid/database/sqlite/SQLiteOpenHelper;", "<init>",
      "(Landroid/content/Context;Ljava/lang/String;Landroid/database/sqlite/SQLiteDatabase$CursorFactory;ILandroid/database/DatabaseErrorHandler;)V", _oh_init)

    def _helper_get_db(helper_obj: Any, _vm=None) -> Any:
        if not isinstance(helper_obj, JObject):
            return open_db_obj(stubs, ":memory:")
        db_name = helper_obj.fields.get("_db_name") or ":memory:"
        db_obj = helper_obj.fields.get("_db_obj")
        if not isinstance(db_obj, JObject):
            db_obj = open_db_obj(stubs, db_name)
            helper_obj.fields["_db_obj"] = db_obj
        if helper_obj.fields.get("_db_init_done"):
            return db_obj
        helper_obj.fields["_db_init_done"] = True
        db_key = db_obj.fields.get("_db_key", ":memory:")
        conn = _get_conn(stubs, db_key)
        row = conn.execute("PRAGMA user_version").fetchone()
        cur_v = int(row[0]) if row else 0
        target_v = int(helper_obj.fields.get("_target_version", 1) or 1)
        vm_cur = _vm or getattr(stubs, "vm", None)
        if vm_cur is not None and hasattr(vm_cur, "invoke_virtual"):
            def _try_cb(mname, msig, cargs):
                from core.java.deviation_journal import DebugStop
                try:
                    vm_cur.invoke_virtual(helper_obj, mname, msig, cargs)
                    return True
                except DebugStop:
                    raise
                except Exception as _e:
                    if type(_e).__name__ == "DebugStop":
                        raise
                    devs = getattr(vm_cur, "deviations", None)
                    if callable(getattr(devs, "record", None)):
                        devs.record(
                            "sqlite_lifecycle_error",
                            f"{getattr(helper_obj, 'cls', '?')}->{mname}",
                            repr(_e),
                            step=getattr(vm_cur, "steps", 0),
                        )
                    return False

            def _run_migration_cb(mname, msig, cargs):
                err_counts = stubs.__dict__.setdefault("_sqlite_err_count", {})
                errs_before = err_counts.get(db_key, 0)
                cb_ok = _try_cb(mname, msig, cargs)
                errs_after = err_counts.get(db_key, 0)
                if cb_ok and errs_after == errs_before:
                    helper_obj.fields["_db_init_failed"] = False
                    conn.execute(f"PRAGMA user_version = {target_v}")
                    conn.commit()
                    return True
                helper_obj.fields["_db_init_failed"] = True
                helper_obj.fields["_db_init_done"] = False
                if getattr(conn, "in_transaction", False):
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                return False

            _try_cb("onConfigure", "(Landroid/database/sqlite/SQLiteDatabase;)V", (helper_obj, db_obj))
            init_ok = True
            if cur_v == 0:
                init_ok = _run_migration_cb("onCreate", "(Landroid/database/sqlite/SQLiteDatabase;)V", (helper_obj, db_obj))
            elif cur_v < target_v:
                init_ok = _run_migration_cb("onUpgrade", "(Landroid/database/sqlite/SQLiteDatabase;II)V", (helper_obj, db_obj, cur_v, target_v))
            if init_ok:
                _try_cb("onOpen", "(Landroid/database/sqlite/SQLiteDatabase;)V", (helper_obj, db_obj))
        return db_obj

    stubs._g3_helper_get_db = _helper_get_db
    R("Landroid/database/sqlite/SQLiteOpenHelper;", "getWritableDatabase", "()Landroid/database/sqlite/SQLiteDatabase;", lambda a: _helper_get_db(a[0]))
    R("Landroid/database/sqlite/SQLiteOpenHelper;", "getReadableDatabase", "()Landroid/database/sqlite/SQLiteDatabase;", lambda a: _helper_get_db(a[0]))
    R("Landroid/database/sqlite/SQLiteOpenHelper;", "getDatabaseName", "()Ljava/lang/String;",
      lambda a: JString(a[0].fields.get("_db_name", "")) if a and isinstance(a[0], JObject) else JString(""))
    R("Landroid/database/sqlite/SQLiteOpenHelper;", "setWriteAheadLoggingEnabled", "(Z)V", lambda a: None)
    R("Landroid/database/sqlite/SQLiteOpenHelper;", "close", "()V", lambda a: None)

    def _room_builder(a, in_mem=False):
        raw_cls = a[1] if len(a) > 1 else None
        if isinstance(raw_cls, JObject) and "$jclass" in raw_cls.fields:
            raw_cls = raw_cls.fields["$jclass"]
        raw_nm = ":memory:" if in_mem else ((_s(a[2]) if len(a) > 2 else None) or ":memory:")
        return JObject("Landroidx/room/RoomDatabase$Builder;", {"_ctx": a[0] if a else None, "_db_cls": raw_cls, "_db_name": raw_nm})

    R("Landroidx/room/Room;", "databaseBuilder",
      "(Landroid/content/Context;Ljava/lang/Class;Ljava/lang/String;)Landroidx/room/RoomDatabase$Builder;",
      lambda a: _room_builder(a, False))
    R("Landroidx/room/Room;", "databaseBuilder",
      "(Landroid/content/Context;Lkotlin/reflect/KClass;Ljava/lang/String;)Landroidx/room/RoomDatabase$Builder;",
      lambda a: _room_builder(a, False))
    R("Landroidx/room/Room;", "databaseBuilder",
      "(Landroid/content/Context;Lkotlin/reflect/KClass;Ljava/lang/String;Lkotlin/jvm/functions/Function0;)Landroidx/room/RoomDatabase$Builder;",
      lambda a: _room_builder(a, False))
    R("Landroidx/room/Room;", "inMemoryDatabaseBuilder",
      "(Landroid/content/Context;Ljava/lang/Class;)Landroidx/room/RoomDatabase$Builder;",
      lambda a: _room_builder(a, True))
    R("Landroidx/room/Room;", "inMemoryDatabaseBuilder",
      "(Landroid/content/Context;Lkotlin/reflect/KClass;)Landroidx/room/RoomDatabase$Builder;",
      lambda a: _room_builder(a, True))

    def _room_build(a):
        b = a[0] if a and isinstance(a[0], JObject) else None
        db_name = b.fields.get("_db_name", ":memory:") if b else ":memory:"
        jcls = b.fields.get("_db_cls") if b else None
        if isinstance(jcls, JObject) and "$jclass" in jcls.fields:
            jcls = jcls.fields["$jclass"]
        raw_nm = str(jcls.fields.get("name", "")) if isinstance(jcls, JObject) else ""
        impl_desc = ("L" + raw_nm.replace(".", "/") + "_Impl;") if raw_nm else "Landroidx/room/RoomDatabase;"
        db_obj = open_db_obj(stubs, db_name, "Landroidx/sqlite/db/SupportSQLiteDatabase;")
        helper = JObject("Landroidx/sqlite/db/SupportSQLiteOpenHelper;", {"_db_name": db_name, "_db_obj": db_obj})
        sql_conn = JObject("Landroidx/sqlite/SQLiteConnection;", {"_db_key": db_obj.fields.get("_db_key", db_name), "_db_name": db_name, "_db_obj": db_obj})
        vm_cur = getattr(stubs, "vm", None)
        if impl_desc != "Landroidx/room/RoomDatabase;" and vm_cur is not None and hasattr(vm_cur, "_lazy_class"):
            try:
                vm_cur._lazy_class(impl_desc)
                if (impl_desc, "<init>", "()V") in vm_cur.registry:
                    inst = JObject(impl_desc, {"_db_obj": db_obj, "_helper": helper, "_db_name": db_name, "_sql_conn": sql_conn})
                    vm_cur.invoke(impl_desc, "<init>", "()V", (inst,))
                    # Room 2.7 KMP: Tabellen ueber createOpenDelegate()->createAllTables(SQLiteConnection) anlegen
                    for _d_sig in ("()Landroidx/room/RoomOpenDelegate;", "()Landroidx/room/RoomOpenDelegateMarker;"):
                        if (impl_desc, "createOpenDelegate", _d_sig) in vm_cur.registry:
                            try:
                                open_del = vm_cur.invoke(impl_desc, "createOpenDelegate", _d_sig, (inst,))
                                if isinstance(open_del, JObject):
                                    vm_cur.invoke_virtual(open_del, "createAllTables", "(Landroidx/sqlite/SQLiteConnection;)V", (open_del, sql_conn))
                            except Exception:
                                pass
                            break
                    return inst
            except Exception:
                pass
        return JObject("Landroidx/room/RoomDatabase;", {"_db_obj": db_obj, "_helper": helper, "_db_name": db_name, "_sql_conn": sql_conn})

    RB_CLS = "Landroidx/room/RoomDatabase$Builder;"
    R(RB_CLS, "build", "()Landroidx/room/RoomDatabase;", _room_build)
    for _bm, _bs in (
        ("allowMainThreadQueries", "()Landroidx/room/RoomDatabase$Builder;"),
        ("fallbackToDestructiveMigration", "()Landroidx/room/RoomDatabase$Builder;"),
        ("fallbackToDestructiveMigration", "(Z)Landroidx/room/RoomDatabase$Builder;"),
        ("fallbackToDestructiveMigrationOnDowngrade", "()Landroidx/room/RoomDatabase$Builder;"),
        ("fallbackToDestructiveMigrationOnDowngrade", "(Z)Landroidx/room/RoomDatabase$Builder;"),
        ("fallbackToDestructiveMigrationFrom", "([I)Landroidx/room/RoomDatabase$Builder;"),
        ("fallbackToDestructiveMigrationFrom", "(Z[I)Landroidx/room/RoomDatabase$Builder;"),
        ("enableMultiInstanceInvalidation", "()Landroidx/room/RoomDatabase$Builder;"),
        ("addMigrations", "([Landroidx/room/migration/Migration;)Landroidx/room/RoomDatabase$Builder;"),
        ("addCallback", "(Landroidx/room/RoomDatabase$Callback;)Landroidx/room/RoomDatabase$Builder;"),
        ("setJournalMode", "(Landroidx/room/RoomDatabase$JournalMode;)Landroidx/room/RoomDatabase$Builder;"),
        ("setQueryCoroutineContext", "(Lkotlin/coroutines/CoroutineContext;)Landroidx/room/RoomDatabase$Builder;"),
    ):
        R(RB_CLS, _bm, _bs, lambda a: a[0] if a else None)

    # ---- Room 2.7 KMP SQLite (androidx.sqlite.* / DBUtil / SQLiteStatementUtil) ----
    import struct as _st_room

    def _kmp_conn_pair(obj):
        if isinstance(obj, JObject):
            db_o = obj.fields.get("_db_obj")
            if isinstance(db_o, JObject):
                return _db_conn(db_o)
            key = obj.fields.get("_db_key") or obj.fields.get("_db_name") or ":memory:"
            return str(key), _get_conn(stubs, str(key))
        return ":memory:", _get_conn(stubs, ":memory:")

    def _kmp_exec_sql(a):
        if len(a) < 2:
            return None
        key, conn = _kmp_conn_pair(a[0])
        sql = _sanitize_sql(_s(a[1]) or "")
        if sql.strip():
            try:
                conn.execute(sql)
                _auto_commit_if_idle(stubs, key, conn)
            except sqlite3.Error as _sqe:
                _record_sqlite_err(stubs, key, "sqliteExecSQL", sql, _sqe)
        return None

    R("Landroidx/sqlite/SQLite;", "execSQL", "(Landroidx/sqlite/SQLiteConnection;Ljava/lang/String;)V", _kmp_exec_sql)

    def _kmp_prepare(a):
        sql_s = _s(a[1]) if len(a) > 1 else ""
        key = (a[0].fields.get("_db_key") or a[0].fields.get("_db_name") or ":memory:") if a and isinstance(a[0], JObject) else ":memory:"
        db_o = a[0].fields.get("_db_obj") if a and isinstance(a[0], JObject) else None
        return JObject("Landroidx/sqlite/SQLiteStatement;", {
            "_db_key": key,
            "_db_obj": db_o,
            "_sql": sql_s or "",
            "_binds": {},
            "_executed": False,
            "_is_query": False,
            "_cols": [],
            "_rows": [],
            "_idx": -1,
        })

    SC_CLS = "Landroidx/sqlite/SQLiteConnection;"
    R(SC_CLS, "prepare", "(Ljava/lang/String;)Landroidx/sqlite/SQLiteStatement;", _kmp_prepare)
    R(SC_CLS, "close", "()V", lambda a: None)

    def _stmt_exec_if_needed(stmt):
        if not isinstance(stmt, JObject):
            return
        if stmt.fields.get("_executed"):
            return
        stmt.fields["_executed"] = True
        sql = _sanitize_sql(stmt.fields.get("_sql", ""))
        if not sql.strip():
            stmt.fields.update({"_is_query": False, "_cols": [], "_rows": [], "_idx": -1})
            return
        binds = stmt.fields.get("_binds") or {}
        n_q = sql.count("?")
        max_k = max([n_q] + [int(k) for k in binds.keys()]) if binds else n_q
        params = [binds.get(i) for i in range(1, max_k + 1)]
        key, conn = _kmp_conn_pair(stmt)
        try:
            cur = conn.execute(sql, params)
            if cur.description is not None:
                stmt.fields["_is_query"] = True
                stmt.fields["_cols"] = [d[0] for d in cur.description]
                stmt.fields["_rows"] = cur.fetchall()
                stmt.fields["_idx"] = -1
            else:
                _auto_commit_if_idle(stubs, key, conn)
                stmt.fields["_is_query"] = False
                stmt.fields["_cols"] = []
                stmt.fields["_rows"] = []
                stmt.fields["_idx"] = -1
        except sqlite3.IntegrityError as _ie:
            _record_sqlite_err(stubs, key, "stmtStep", sql, _ie)
            from core.java.dalvik import DalvikException
            msg = f"Error code 1555 SQLITE_CONSTRAINT_PRIMARYKEY: {_ie}"
            raise DalvikException(msg, payload=JObject("Landroid/database/SQLException;", {"detailMessage": JString(msg), "message": JString(msg)}))
        except sqlite3.Error as _sqe:
            _record_sqlite_err(stubs, key, "stmtStep", sql, _sqe)
            stmt.fields.update({"_is_query": False, "_cols": [], "_rows": [], "_idx": -1})

    def _stmt_bind(stmt, idx, val):
        if isinstance(stmt, JObject):
            stmt.fields.setdefault("_binds", {})[int(idx)] = val
            stmt.fields["_executed"] = False
            stmt.fields["_idx"] = -1
        return None

    def _stmt_f32(v):
        if isinstance(v, float):
            return v
        return _st_room.unpack("<f", _st_room.pack("<I", int(v or 0) & 0xFFFFFFFF))[0]

    def _stmt_f64(v):
        if isinstance(v, float):
            return v
        return _st_room.unpack("<d", _st_room.pack("<Q", int(v or 0) & 0xFFFFFFFFFFFFFFFF))[0]

    def _stmt_cell(a):
        if not a or not isinstance(a[0], JObject):
            return None
        _stmt_exec_if_needed(a[0])
        idx = int(a[0].fields.get("_idx", -1))
        rows = a[0].fields.get("_rows") or ()
        col = int(a[1]) if len(a) > 1 else -1
        if 0 <= idx < len(rows) and 0 <= col < len(rows[idx]):
            return rows[idx][col]
        return None

    def _stmt_step(a):
        if not a or not isinstance(a[0], JObject):
            return 0
        _stmt_exec_if_needed(a[0])
        if not a[0].fields.get("_is_query"):
            return 0
        idx = int(a[0].fields.get("_idx", -1)) + 1
        a[0].fields["_idx"] = idx
        return 1 if idx < len(a[0].fields.get("_rows") or ()) else 0

    def _stmt_col_idx(a, throw_missing=False):
        if not a or not isinstance(a[0], JObject) or len(a) < 2:
            return -1
        _stmt_exec_if_needed(a[0])
        cname = (_s(a[1]) or "").strip("`").lower()
        cols = [str(c).strip("`").lower() for c in (a[0].fields.get("_cols") or ())]
        if cname in cols:
            return cols.index(cname)
        for i, col in enumerate(cols):
            if col.endswith("." + cname):
                return i
        return -1

    SS_CLS = "Landroidx/sqlite/SQLiteStatement;"
    R(SS_CLS, "bindLong", "(IJ)V", lambda a: _stmt_bind(a[0], a[1], int(a[2])) if len(a) > 2 else None)
    R(SS_CLS, "bindInt", "(II)V", lambda a: _stmt_bind(a[0], a[1], int(a[2])) if len(a) > 2 else None)
    R(SS_CLS, "bindBoolean", "(IZ)V", lambda a: _stmt_bind(a[0], a[1], 1 if a[2] else 0) if len(a) > 2 else None)
    R(SS_CLS, "bindDouble", "(ID)V", lambda a: _stmt_bind(a[0], a[1], _stmt_f64(a[2])) if len(a) > 2 else None)
    R(SS_CLS, "bindFloat", "(IF)V", lambda a: _stmt_bind(a[0], a[1], _stmt_f32(a[2])) if len(a) > 2 else None)
    R(SS_CLS, "bindText", "(ILjava/lang/String;)V", lambda a: _stmt_bind(a[0], a[1], _s(a[2])) if len(a) > 2 else None)
    R(SS_CLS, "bindBlob", "(I[B)V", lambda a: _stmt_bind(a[0], a[1], _to_py(a[2])) if len(a) > 2 else None)
    R(SS_CLS, "bindNull", "(I)V", lambda a: _stmt_bind(a[0], a[1], None) if len(a) > 1 else None)
    R(SS_CLS, "clearBindings", "()V", lambda a: a[0].fields.update({"_binds": {}, "_executed": False, "_idx": -1}) if a and isinstance(a[0], JObject) else None)
    R(SS_CLS, "reset", "()V", lambda a: a[0].fields.update({"_executed": False, "_idx": -1}) if a and isinstance(a[0], JObject) else None)
    R(SS_CLS, "close", "()V", lambda a: None)
    R(SS_CLS, "step", "()Z", _stmt_step)
    R(SS_CLS, "getLong", "(I)J", lambda a: (lambda v: 0 if v is None else int(v))(_stmt_cell(a)))
    R(SS_CLS, "getInt", "(I)I", lambda a: (lambda v: 0 if v is None else int(v))(_stmt_cell(a)))
    R(SS_CLS, "getBoolean", "(I)Z", lambda a: 1 if _stmt_cell(a) else 0)
    R(SS_CLS, "getDouble", "(I)D", lambda a: (lambda v: 0.0 if v is None else float(v))(_stmt_cell(a)))
    R(SS_CLS, "getFloat", "(I)F", lambda a: (lambda v: 0.0 if v is None else float(v))(_stmt_cell(a)))
    R(SS_CLS, "getText", "(I)Ljava/lang/String;", lambda a: (lambda v: JString("") if v is None else JString(str(v)))(_stmt_cell(a)))
    R(SS_CLS, "getBlob", "(I)[B", lambda a: (lambda v: JArray("[B", []) if v is None else JArray("[B", list(bytes(v) if isinstance(v, (bytes, bytearray)) else str(v).encode("utf-8"))))(_stmt_cell(a)))
    R(SS_CLS, "isNull", "(I)Z", lambda a: 1 if _stmt_cell(a) is None else 0)
    R(SS_CLS, "getColumnCount", "()I", lambda a: (_stmt_exec_if_needed(a[0]), len(a[0].fields.get("_cols") or ()))[1] if a and isinstance(a[0], JObject) else 0)
    R(SS_CLS, "getColumnName", "(I)Ljava/lang/String;",
      lambda a: (_stmt_exec_if_needed(a[0]), JString((a[0].fields.get("_cols") or [])[int(a[1])]) if 0 <= int(a[1]) < len(a[0].fields.get("_cols") or ()) else JString(""))[1] if a and len(a) > 1 and isinstance(a[0], JObject) else JString(""))
    R(SS_CLS, "getColumnNames", "()Ljava/util/List;",
      lambda a: (_stmt_exec_if_needed(a[0]), JObject("Ljava/util/ArrayList;", {"__items": [JString(str(c)) for c in (a[0].fields.get("_cols") or ())] }))[1] if a and isinstance(a[0], JObject) else JObject("Ljava/util/ArrayList;", {"__items": []}))

    SSU_CLS = "Landroidx/room/util/SQLiteStatementUtil;"
    for _mn in ("getColumnIndex", "getColumnIndexOrThrow", "columnIndexOf", "columnIndexOfCommon"):
        R(SSU_CLS, _mn, "(Landroidx/sqlite/SQLiteStatement;Ljava/lang/String;)I", lambda a: _stmt_col_idx(a))

    def _room_db_perform(a, fn_idx):
        db_inst = a[0] if a and isinstance(a[0], JObject) else None
        fn1 = a[fn_idx] if len(a) > fn_idx else None
        if db_inst is None or not isinstance(fn1, JObject):
            return None
        sql_conn = db_inst.fields.get("_sql_conn")
        if not isinstance(sql_conn, JObject):
            db_o = _room_db_obj((db_inst,))
            db_name = db_inst.fields.get("_db_name", ":memory:")
            sql_conn = JObject("Landroidx/sqlite/SQLiteConnection;", {"_db_key": db_o.fields.get("_db_key", db_name), "_db_name": db_name, "_db_obj": db_o})
            db_inst.fields["_sql_conn"] = sql_conn
        vm_cur = getattr(stubs, "vm", None)
        if vm_cur is not None and hasattr(vm_cur, "invoke_virtual"):
            return vm_cur.invoke_virtual(fn1, "invoke", "(Ljava/lang/Object;)Ljava/lang/Object;", (fn1, sql_conn))
        return None

    DBU_CLS = "Landroidx/room/util/DBUtil;"
    R(DBU_CLS, "performSuspending",
      "(Landroidx/room/RoomDatabase;ZZLkotlin/jvm/functions/Function1;Lkotlin/coroutines/Continuation;)Ljava/lang/Object;",
      lambda a: _room_db_perform(a, 3))
    R(DBU_CLS, "performBlocking",
      "(Landroidx/room/RoomDatabase;ZZLkotlin/jvm/functions/Function1;)Ljava/lang/Object;",
      lambda a: _room_db_perform(a, 3))
    R(DBU_CLS, "performInTransactionSuspending",
      "(Landroidx/room/RoomDatabase;Lkotlin/jvm/functions/Function1;Lkotlin/coroutines/Continuation;)Ljava/lang/Object;",
      lambda a: _room_db_perform(a, 1))

    R("Landroidx/sqlite/db/SupportSQLiteOpenHelper;", "getWritableDatabase", "()Landroidx/sqlite/db/SupportSQLiteDatabase;",
      lambda a: a[0].fields.get("_db_obj") if a and isinstance(a[0], JObject) and a[0].fields.get("_db_obj") else open_db_obj(stubs, ":memory:", "Landroidx/sqlite/db/SupportSQLiteDatabase;"))
    R("Landroidx/sqlite/db/SupportSQLiteOpenHelper;", "getReadableDatabase", "()Landroidx/sqlite/db/SupportSQLiteDatabase;",
      lambda a: a[0].fields.get("_db_obj") if a and isinstance(a[0], JObject) and a[0].fields.get("_db_obj") else open_db_obj(stubs, ":memory:", "Landroidx/sqlite/db/SupportSQLiteDatabase;"))

    def _room_helper(a):
        r = a[0] if a and isinstance(a[0], JObject) else None
        if r is None:
            return JObject("Landroidx/sqlite/db/SupportSQLiteOpenHelper;", {"_db_name": ":memory:"})
        h = r.fields.get("_helper")
        if isinstance(h, JObject):
            return h
        db_obj = open_db_obj(stubs, r.fields.get("_db_name", ":memory:"), "Landroidx/sqlite/db/SupportSQLiteDatabase;")
        h = JObject("Landroidx/sqlite/db/SupportSQLiteOpenHelper;", {"_db_name": r.fields.get("_db_name", ":memory:"), "_db_obj": db_obj})
        r.fields["_helper"] = h
        r.fields["_db_obj"] = db_obj
        return h

    def _room_db_obj(a):
        r = a[0] if a and isinstance(a[0], JObject) else None
        if isinstance(r, JObject) and isinstance(r.fields.get("_db_obj"), JObject):
            return r.fields["_db_obj"]
        return _room_helper(a).fields.get("_db_obj")

    def _room_run_in_tx(a, has_ret: bool = False):
        if not a:
            return None
        db_obj = _room_db_obj(a)
        _db_begin_tx((db_obj,))
        res = None
        cb = a[1] if len(a) > 1 else None
        vm_cur = getattr(stubs, "vm", None)
        try:
            if isinstance(cb, JObject) and vm_cur is not None and hasattr(vm_cur, "invoke_virtual"):
                mname, msig = ("call", "()Ljava/lang/Object;") if has_ret else ("run", "()V")
                res = vm_cur.invoke_virtual(cb, mname, msig, (cb,))
            _db_set_tx_ok((db_obj,))
        finally:
            _db_end_tx((db_obj,))
        return res

    RM_CLS = "Landroidx/room/RoomDatabase;"
    R(RM_CLS, "getOpenHelper", "()Landroidx/sqlite/db/SupportSQLiteOpenHelper;", _room_helper)
    R(RM_CLS, "beginTransaction", "()V", lambda a: _db_begin_tx((_room_db_obj(a),)))
    R(RM_CLS, "setTransactionSuccessful", "()V", lambda a: _db_set_tx_ok((_room_db_obj(a),)))
    R(RM_CLS, "endTransaction", "()V", lambda a: _db_end_tx((_room_db_obj(a),)))
    R(RM_CLS, "inTransaction", "()Z", lambda a: _db_in_tx((_room_db_obj(a),)))
    R(RM_CLS, "runInTransaction", "(Ljava/lang/Runnable;)V", lambda a: _room_run_in_tx(a, False))
    R(RM_CLS, "runInTransaction", "(Ljava/util/concurrent/Callable;)Ljava/lang/Object;", lambda a: _room_run_in_tx(a, True))
    R(RM_CLS, "compileStatement", "(Ljava/lang/String;)Landroidx/sqlite/db/SupportSQLiteStatement;", lambda a: _db_compile_stmt((_room_db_obj(a), a[1] if len(a) > 1 else None)))
    R(RM_CLS, "query", "(Landroidx/sqlite/db/SupportSQLiteQuery;)Landroid/database/Cursor;", lambda a: _raw_query((_room_db_obj(a), a[1] if len(a) > 1 else None)))
    R(RM_CLS, "query", "(Landroidx/sqlite/db/SupportSQLiteQuery;Landroid/os/CancellationSignal;)Landroid/database/Cursor;", lambda a: _raw_query((_room_db_obj(a), a[1] if len(a) > 1 else None)))
    R(RM_CLS, "query", "(Ljava/lang/String;[Ljava/lang/Object;)Landroid/database/Cursor;", lambda a: _raw_query((_room_db_obj(a), a[1] if len(a) > 1 else None, a[2] if len(a) > 2 else None)))
    R(RM_CLS, "assertNotMainThread", "()V", lambda a: None)
    R(RM_CLS, "assertNotSuspendingTransaction", "()V", lambda a: None)
    R(RM_CLS, "internalInitInvalidationTracker", "(Landroidx/sqlite/db/SupportSQLiteDatabase;)V", lambda a: None)
    R(RM_CLS, "close", "()V", lambda a: None)

    # ---- ActivityResultRegistry & setResult -------------------------------
    def _set_result(a):
        act = a[0] if a and isinstance(a[0], JObject) else getattr(stubs, "_current_activity", None)
        if isinstance(act, JObject):
            act.fields["_result_code"] = int(a[1]) if len(a) > 1 else 0
            act.fields["_result_data"] = a[2] if len(a) > 2 else None
        return None

    R("Landroid/app/Activity;", "setResult", "(I)V", _set_result)
    R("Landroid/app/Activity;", "setResult", "(ILandroid/content/Intent;)V", _set_result)
    RB("setResult", "(I)V", _set_result)
    RB("setResult", "(ILandroid/content/Intent;)V", _set_result)

    AR_CLS = "Landroidx/activity/result/ActivityResult;"
    R(AR_CLS, "<init>", "(ILandroid/content/Intent;)V",
      lambda a: a[0].fields.update({"mResultCode": int(a[1]), "mData": a[2]}) if a and len(a) > 2 and isinstance(a[0], JObject) else None)
    R(AR_CLS, "getResultCode", "()I", lambda a: int(a[0].fields.get("mResultCode", 0)) if a and isinstance(a[0], JObject) else 0)
    R(AR_CLS, "getData", "()Landroid/content/Intent;", lambda a: a[0].fields.get("mData") if a and isinstance(a[0], JObject) else None)

    def _launcher_launch(a):
        launcher = a[0] if a and isinstance(a[0], JObject) else None
        inp = a[1] if len(a) > 1 else None
        if launcher is None:
            return None
        contract = launcher.fields.get("_contract")
        cb = launcher.fields.get("_cb")
        c_cls = contract.cls if isinstance(contract, JObject) else ""
        vm_cur = getattr(stubs, "vm", None)
        if "RequestPermission" in c_cls and "Multiple" not in c_cls:
            if isinstance(cb, JObject) and vm_cur is not None and hasattr(vm_cur, "invoke_virtual"):
                granted = JObject("Ljava/lang/Boolean;", {"value": 1 if getattr(stubs, "_auto_grant_permissions", True) else 0})
                try:
                    vm_cur.invoke_virtual(cb, "onActivityResult", "(Ljava/lang/Object;)V", (cb, granted))
                except Exception:
                    pass
            return None
        if "RequestMultiplePermissions" in c_cls:
            if isinstance(cb, JObject) and vm_cur is not None and hasattr(vm_cur, "invoke_virtual"):
                perms = []
                if isinstance(inp, JArray):
                    perms = [_s(x) for x in (inp.data or ()) if _s(x)]
                res_m = {JString(p): JObject("Ljava/lang/Boolean;", {"value": 1 if getattr(stubs, "_auto_grant_permissions", True) else 0}) for p in perms}
                map_obj = JObject("Ljava/util/LinkedHashMap;", {"map": res_m})
                try:
                    vm_cur.invoke_virtual(cb, "onActivityResult", "(Ljava/lang/Object;)V", (cb, map_obj))
                except Exception:
                    pass
            return None
        if isinstance(inp, JObject) and "Intent" in (inp.cls or ""):
            if isinstance(cb, JObject):
                stubs._pending_act_result_cb = cb
            sa_fn = stubs._stubs.get(("Landroid/app/Activity;", "startActivity", "(Landroid/content/Intent;)V"))
            if not callable(sa_fn):
                sa_fn = stubs._by_name.get(("startActivity", "(Landroid/content/Intent;)V"))
            if callable(sa_fn):
                sa_fn((getattr(stubs, "_current_activity", None) or launcher.fields.get("_owner"), inp))
        return None

    AL_CLS = "Landroidx/activity/result/ActivityResultLauncher;"
    R(AL_CLS, "launch", "(Ljava/lang/Object;)V", _launcher_launch)
    R(AL_CLS, "launch", "(Ljava/lang/Object;Landroidx/core/app/ActivityOptionsCompat;)V", _launcher_launch)
    RB("launch", "(Ljava/lang/Object;)V", _launcher_launch)
    RB("launch", "(Ljava/lang/Object;Landroidx/core/app/ActivityOptionsCompat;)V", _launcher_launch)
    RB("unregister", "()V", lambda a: None)

    def _register_for_act_result(a):
        owner = a[0] if a else None
        contract = a[1] if len(a) > 1 else None
        cb = a[2] if len(a) > 2 else None
        return JObject(AL_CLS, {"_owner": owner, "_contract": contract, "_cb": cb})

    _reg_sig = "(Landroidx/activity/result/contract/ActivityResultContract;Landroidx/activity/result/ActivityResultCallback;)Landroidx/activity/result/ActivityResultLauncher;"
    for _rc_cls in (
        "Landroidx/activity/ComponentActivity;",
        "Landroidx/fragment/app/FragmentActivity;",
        "Landroidx/appcompat/app/AppCompatActivity;",
        "Landroidx/fragment/app/Fragment;",
    ):
        R(_rc_cls, "registerForActivityResult", _reg_sig, _register_for_act_result)
    RB("registerForActivityResult", _reg_sig, _register_for_act_result)
    stubs._g3_register_for_act_result = _register_for_act_result

    # ---- AndroidKeyStore & JCA Cipher (G3-Grundset) ------------------------
    ks_store: Dict[str, Any] = stubs.__dict__.setdefault("_keystore_aliases", {})

    def _ensure_ks_alias_if_matching_prefs(alias: str) -> bool:
        if not alias:
            return False
        if alias in ks_store:
            return True
        kbytes = _ks_key_bytes(alias)
        expected_iv = hashlib.sha256(kbytes).digest()[:12]
        iv_b64_prefix = base64.b64encode(expected_iv).decode("ascii")
        prefs_store = getattr(stubs, "_prefs_store", None) or {}
        if isinstance(prefs_store, dict):
            for store in list(prefs_store.values()):
                if not isinstance(store, dict):
                    continue
                for val in list(store.values()):
                    sval = _s(val)
                    if sval and len(sval) >= 20 and sval.startswith(iv_b64_prefix):
                        ks_store[alias] = _make_secret_key(alias)
                        return True
        return False

    def _xor_stream(key_bytes: bytes, iv: bytes, data: bytes) -> bytes:
        if not data:
            return b""
        out = bytearray(len(data))
        ctr = 0
        pos = 0
        while pos < len(data):
            blk = hashlib.sha256(key_bytes + iv + ctr.to_bytes(4, "big")).digest()
            take = min(len(blk), len(data) - pos)
            for i in range(take):
                out[pos + i] = data[pos + i] ^ blk[i]
            pos += take
            ctr += 1
        return bytes(out)

    KS = "Ljava/security/KeyStore;"
    R(KS, "getInstance", "(Ljava/lang/String;)Ljava/security/KeyStore;",
      lambda a: JObject(KS, {"_type": _s(a[0]) if a else "AndroidKeyStore"}))
    R(KS, "load", "(Ljava/security/KeyStore$LoadStoreParameter;)V", lambda a: None)
    R(KS, "load", "(Ljava/io/InputStream;[C)V", lambda a: None)
    R(KS, "containsAlias", "(Ljava/lang/String;)Z",
      lambda a: 1 if (len(a) > 1 and _ensure_ks_alias_if_matching_prefs(_s(a[1]) or "")) else 0)
    R(KS, "deleteEntry", "(Ljava/lang/String;)V",
      lambda a: (ks_store.pop(_s(a[1]) or "", None), None)[1] if len(a) > 1 else None)
    R(KS, "getKey", "(Ljava/lang/String;[C)Ljava/security/Key;",
      lambda a: ks_store.get(_s(a[1]) or "") if (len(a) > 1 and _ensure_ks_alias_if_matching_prefs(_s(a[1]) or "")) else None)
    R(KS, "getEntry", "(Ljava/lang/String;Ljava/security/KeyStore$ProtectionParameter;)Ljava/security/KeyStore$Entry;",
      lambda a: JObject("Ljava/security/KeyStore$SecretKeyEntry;", {"_key": ks_store.get(_s(a[1]) or "")}) if (len(a) > 1 and _ensure_ks_alias_if_matching_prefs(_s(a[1]) or "")) else None)
    R("Ljava/security/KeyStore$SecretKeyEntry;", "getSecretKey", "()Ljavax/crypto/SecretKey;",
      lambda a: a[0].fields.get("_key") if a and isinstance(a[0], JObject) else None)

    KGS = "Landroid/security/keystore/KeyGenParameterSpec;"
    KGSB = "Landroid/security/keystore/KeyGenParameterSpec$Builder;"
    R(KGSB, "<init>", "(Ljava/lang/String;I)V",
      lambda a: a[0].fields.update({"_alias": _s(a[1]) or "default", "_purposes": int(a[2]) if len(a) > 2 else 3, "_key_size": 256}) if a and isinstance(a[0], JObject) else None)
    R(KGSB, "setKeySize", "(I)Landroid/security/keystore/KeyGenParameterSpec$Builder;",
      lambda a: (a[0].fields.__setitem__("_key_size", int(a[1] or 256)) if a and len(a) > 1 and isinstance(a[0], JObject) else None, a[0] if a else None)[1])
    R(KGSB, "build", f"(){KGS}",
      lambda a: JObject(KGS, {
          "_alias": a[0].fields.get("_alias", "default") if a and isinstance(a[0], JObject) else "default",
          "_key_size": int(a[0].fields.get("_key_size", 256)) if a and isinstance(a[0], JObject) else 256,
          "_purposes": int(a[0].fields.get("_purposes", 3)) if a and isinstance(a[0], JObject) else 3,
      }))
    for _bm, _bs in (
        ("setBlockModes", "([Ljava/lang/String;)Landroid/security/keystore/KeyGenParameterSpec$Builder;"),
        ("setEncryptionPaddings", "([Ljava/lang/String;)Landroid/security/keystore/KeyGenParameterSpec$Builder;"),
        ("setRandomizedEncryptionRequired", "(Z)Landroid/security/keystore/KeyGenParameterSpec$Builder;"),
        ("setUserAuthenticationRequired", "(Z)Landroid/security/keystore/KeyGenParameterSpec$Builder;"),
    ):
        R(KGSB, _bm, _bs, lambda a: a[0] if a else None)
    R(KGS, "getKeySize", "()I",
      lambda a: int(a[0].fields.get("_key_size", 256)) if a and isinstance(a[0], JObject) else 256)
    R(KGS, "getKeystoreAlias", "()Ljava/lang/String;",
      lambda a: JString(a[0].fields.get("_alias", "_androidx_security_master_key_") if a and isinstance(a[0], JObject) else "_androidx_security_master_key_"))
    R(KGS, "getPurposes", "()I",
      lambda a: int(a[0].fields.get("_purposes", 3)) if a and isinstance(a[0], JObject) else 3)
    R(KGS, "getBlockModes", "()[Ljava/lang/String;", lambda a: JArray("[Ljava/lang/String;", [JString("GCM")]))
    R(KGS, "getEncryptionPaddings", "()[Ljava/lang/String;", lambda a: JArray("[Ljava/lang/String;", [JString("NoPadding")]))
    R(KGS, "isUserAuthenticationRequired", "()Z", lambda a: 0)

    KG = "Ljavax/crypto/KeyGenerator;"
    R(KG, "getInstance", "(Ljava/lang/String;Ljava/lang/String;)Ljavax/crypto/KeyGenerator;",
      lambda a: JObject(KG, {"_alg": _s(a[0]) if a else "AES"}))
    R(KG, "getInstance", "(Ljava/lang/String;)Ljavax/crypto/KeyGenerator;",
      lambda a: JObject(KG, {"_alg": _s(a[0]) if a else "AES"}))
    R(KG, "init", "(Ljava/security/spec/AlgorithmParameterSpec;)V",
      lambda a: a[0].fields.update({"_alias": a[1].fields.get("_alias", "default")}) if a and len(a) > 1 and isinstance(a[0], JObject) and isinstance(a[1], JObject) else None)

    def _kg_generate(a):
        alias = (a[0].fields.get("_alias") if a and isinstance(a[0], JObject) else None) or "default"
        sk = JObject("Ljavax/crypto/SecretKey;", {"_alias": alias, "_key_bytes": _ks_key_bytes(alias)})
        ks_store[alias] = sk
        return sk

    R(KG, "generateKey", "()Ljavax/crypto/SecretKey;", _kg_generate)

    GCM = "Ljavax/crypto/spec/GCMParameterSpec;"
    R(GCM, "<init>", "(I[B)V",
      lambda a: a[0].fields.update({"_tlen": int(a[1]), "_iv": _to_py(a[2]) if len(a) > 2 else b""}) if a and isinstance(a[0], JObject) else None)
    R(GCM, "getIV", "()[B", lambda a: JArray("[B", list(a[0].fields.get("_iv") or b"")) if a and isinstance(a[0], JObject) else JArray("[B", []))
    R(GCM, "getTLen", "()I", lambda a: int(a[0].fields.get("_tlen", 128)) if a and isinstance(a[0], JObject) else 128)
    IVP = "Ljavax/crypto/spec/IvParameterSpec;"
    R(IVP, "<init>", "([B)V",
      lambda a: a[0].fields.update({"_iv": _to_py(a[1]) if len(a) > 1 else b""}) if a and isinstance(a[0], JObject) else None)
    R(IVP, "getIV", "()[B", lambda a: JArray("[B", list(a[0].fields.get("_iv") or b"")) if a and isinstance(a[0], JObject) else JArray("[B", []))

    CP = "Ljavax/crypto/Cipher;"
    R(CP, "getInstance", "(Ljava/lang/String;)Ljavax/crypto/Cipher;",
      lambda a: JObject(CP, {"_xform": _s(a[0]) if a else "AES/GCM/NoPadding", "_mode": 1, "_iv": b"0123456789ab"}))

    def _cipher_init(a):
        if not a or not isinstance(a[0], JObject):
            return None
        c = a[0]
        c.fields["_mode"] = int(a[1]) if len(a) > 1 else 1
        key_obj = a[2] if len(a) > 2 else None
        kbytes = (key_obj.fields.get("_key_bytes") if isinstance(key_obj, JObject) else None) or _ks_key_bytes("default")
        c.fields["_key_bytes"] = kbytes
        spec = a[3] if len(a) > 3 else None
        if isinstance(spec, JObject) and "_iv" in spec.fields:
            c.fields["_iv"] = bytes(spec.fields["_iv"] or b"0123456789ab")
        elif c.fields["_mode"] == 1:
            c.fields["_iv"] = hashlib.sha256(kbytes).digest()[:12]
        return None

    R(CP, "init", "(ILjava/security/Key;)V", _cipher_init)
    R(CP, "init", "(ILjava/security/Key;Ljava/security/spec/AlgorithmParameterSpec;)V", _cipher_init)
    R(CP, "getIV", "()[B", lambda a: JArray("[B", list(a[0].fields.get("_iv") or b"0123456789ab")) if a and isinstance(a[0], JObject) else JArray("[B", []))

    def _cipher_dofinal(a):
        if not a or len(a) < 2 or not isinstance(a[0], JObject):
            return JArray("[B", [])
        raw = _to_py(a[1]) or b""
        if len(a) >= 4 and isinstance(a[2], int) and isinstance(a[3], int):
            raw = raw[int(a[2]):int(a[2]) + int(a[3])]
        kbytes = a[0].fields.get("_key_bytes") or _ks_key_bytes("default")
        iv = a[0].fields.get("_iv") or b"0123456789ab"
        out = _xor_stream(kbytes, iv, bytes(raw))
        return JArray("[B", list(out))

    R(CP, "doFinal", "([B)[B", _cipher_dofinal)
    R(CP, "doFinal", "([BII)[B", _cipher_dofinal)


def attach_g3_vm_hook(stubs: Any, vm_obj: Any) -> None:
    """Haengt den G3-VM-Hook fuer SQLiteOpenHelper/Room/registerForActivityResult einmalig an `vm_obj`."""
    if stubs is None or vm_obj is None or not hasattr(vm_obj, "_method_hooks"):
        return
    if getattr(vm_obj, "_g3_hooks_attached", False):
        return
    install_sqlite_and_prefs_grundset(stubs)

    def _g3_hook(rcls, cls, name, sig, logical, static, _vm):
        if static or not logical or not isinstance(logical[0], JObject):
            return False, None
        recv = logical[0]
        if name == "registerForActivityResult" and "ActivityResultCallback" in sig and callable(getattr(stubs, "_g3_register_for_act_result", None)):
            return True, stubs._g3_register_for_act_result(logical)
        if name == "getOpenHelper" and ("RoomDatabase" in (cls or "") or "_helper" in recv.fields):
            h = recv.fields.get("_helper")
            if isinstance(h, JObject):
                return True, h
        if "_db_obj" in recv.fields and "RoomDatabase" not in recv.cls and name in (
            "beginTransaction", "setTransactionSuccessful", "endTransaction",
            "inTransaction", "compileStatement", "query",
        ):
            ok_r, val_r = stubs.resolve("Landroidx/room/RoomDatabase;", name, sig, logical, False)
            if ok_r:
                return True, val_r
        if "SQLiteOpenHelper" in (cls or "") or "_db_name" in recv.fields:
            if name in ("getWritableDatabase", "getReadableDatabase") and callable(getattr(stubs, "_g3_helper_get_db", None)):
                return True, stubs._g3_helper_get_db(recv, _vm)
            if name == "getDatabaseName":
                return True, JString(recv.fields.get("_db_name", ""))
        return False, None

    vm_obj._method_hooks.append(_g3_hook)
    vm_obj._g3_hooks_attached = True
