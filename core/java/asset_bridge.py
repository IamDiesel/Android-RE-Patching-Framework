# -*- coding: utf-8 -*-
"""core.java.asset_bridge — AssetManager -> VFS: die App liest ihre (gepackten) Assets wie auf dem
Geraet. `AssetManager.open(name)` liefert einen InputStream ueber `stubs.vfs`; `list(prefix)` die
Eintraege. Teil des app-eigenen Entpack-Wegs (die App liest die gepackte Lib selbst, das OS entpackt
nichts). InputStream-Reads sind VERKETTET: ist das Objekt kein Asset-Stream, delegiert der Handler an
den zuvor registrierten (z. B. Netz-HTTP-InputStream), damit beide koexistieren."""
from __future__ import annotations

from core.java.objects import JObject, JString, JArray


def _s(v):
    return v.text if isinstance(v, JString) else ("" if v is None else str(v))


def _is_asset_stream(o):
    return isinstance(o, JObject) and "$data" in o.fields


def install_asset_stubs(stubs, eng=None):
    vfs = stubs.vfs
    if eng is not None:
        stubs._vfs_eng = eng
    bn = stubs._by_name

    def _get_fs():
        e = eng if eng is not None else getattr(stubs, "_vfs_eng", None)
        if e is None:
            return None
        try:
            from core.java.fs_bridge import get_shared_fs
            return get_shared_fs(e)
        except Exception:
            return None

    def _read_asset(name: str):
        clean = (name or "").lstrip("/")
        fs = _get_fs()
        if fs is not None and clean:
            for vpath in (f"/data/app/assets/{clean}", f"/assets/{clean}"):
                ino = fs.lookup(vpath)
                if ino is not None:
                    return fs.read(ino)
        return vfs.read(name)

    def _list_assets(prefix: str):
        clean = (prefix or "").strip("/")
        seen = []
        seen_set = set()
        fs = _get_fs()
        if fs is not None:
            for root in ("/data/app/assets", "/assets"):
                vdir = f"{root}/{clean}" if clean else root
                for name, _kind in fs.listdir(vdir):
                    if name not in seen_set:
                        seen_set.add(name)
                        seen.append(name)
        for name in vfs.list(prefix):
            if name not in seen_set:
                seen_set.add(name)
                seen.append(name)
        return seen

    def _chain(name, sig, fn):
        prev = bn.get((name, sig))

        def _wrap(a):
            if a and _is_asset_stream(a[0]):
                return fn(a)
            if prev is not None:
                return prev(a)
            return -1 if sig.endswith(")I") else None
        stubs.register_by_name(name, sig, _wrap)

    # --- AssetManager.open / list ---
    def _open(a):
        name = _s(a[1]) if len(a) > 1 else ""
        data = _read_asset(name)
        return JObject("Ljava/io/InputStream;",
                       {"$data": bytearray(data if data is not None else b""), "$pos": 0})
    stubs.register_by_name("open", "(Ljava/lang/String;)Ljava/io/InputStream;", _open)

    def _list(a):
        prefix = _s(a[1]) if len(a) > 1 else ""
        return JArray("Ljava/lang/String;", [JString(n) for n in _list_assets(prefix)])
    stubs.register_by_name("list", "(Ljava/lang/String;)[Ljava/lang/String;", _list)

    # --- InputStream-Reads (verkettet) ---
    def _read0(a):
        o = a[0]; d = o.fields["$data"]; p = o.fields.get("$pos", 0)
        if p < len(d):
            o.fields["$pos"] = p + 1
            return d[p]
        return -1
    _chain("read", "()I", _read0)

    def _readb(a):
        o = a[0]; arr = a[1] if len(a) > 1 else None
        if not isinstance(arr, JArray):
            return -1
        d = o.fields["$data"]; p = o.fields.get("$pos", 0)
        n = min(len(arr.data), len(d) - p)
        if n <= 0:
            return -1
        for i in range(n):
            arr.data[i] = d[p + i]
        o.fields["$pos"] = p + n
        return n
    _chain("read", "([B)I", _readb)

    def _readbii(a):
        o = a[0]; arr = a[1] if len(a) > 1 else None
        off = int(a[2]) if len(a) > 2 else 0
        ln = int(a[3]) if len(a) > 3 else 0
        if not isinstance(arr, JArray):
            return -1
        d = o.fields["$data"]; p = o.fields.get("$pos", 0)
        n = min(ln, len(d) - p)
        if n <= 0:
            return -1
        for i in range(n):
            arr.data[off + i] = d[p + i]
        o.fields["$pos"] = p + n
        return n
    _chain("read", "([BII)I", _readbii)

    def _available(a):
        o = a[0]
        return max(0, len(o.fields["$data"]) - o.fields.get("$pos", 0))
    _chain("available", "()I", _available)

    _chain("close", "()V", lambda a: None)
    return vfs


def seed_assets_from_dir(stubs, root, rel="assets", max_bytes=64 * 1024 * 1024,
                        total_max=256 * 1024 * 1024, eng=None):
    """Mountet die Assets der entpackten App in `stubs.vfs` (Java-Seite) und optional ins VFS 2.0.
    `max_bytes` Pro-Datei, `total_max` Gesamt-Limit. Rueckgabe: Anzahl."""
    import os
    base = os.path.join(root, rel)
    if not os.path.isdir(base):
        return 0
    fs = None
    e = eng if eng is not None else getattr(stubs, "_vfs_eng", None)
    if e is not None:
        try:
            from core.java.fs_bridge import get_shared_fs
            fs = get_shared_fs(e)
        except Exception:
            fs = None
    n = 0
    total = 0
    for dp, _dirs, fns in os.walk(base):
        for fn in fns:
            fp = os.path.join(dp, fn)
            try:
                sz = os.path.getsize(fp)
                if sz > max_bytes or total + sz > total_max:
                    continue
                with open(fp, "rb") as fh:
                    data = fh.read()
            except Exception:
                continue
            total += len(data)
            relp = os.path.relpath(fp, base).replace(os.sep, "/")
            stubs.vfs.mount(relp, data)
            if fs is not None:
                fs.add_file(f"/data/app/assets/{relp}", data)
            n += 1
    return n
