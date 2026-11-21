# -*- coding: utf-8 -*-
"""core.java.fs_bridge — AP2.1: GEMEINSAMES VFS fuer Java-Datei-I/O und das Native-OS.

Bindet `java.io.File` / `FileInputStream` / `FileOutputStream` / `RandomAccessFile` und
`java.util.zip.ZipFile` an **dasselbe** VFS 2.0 (`eng._os.fs`), auf dem auch die nativen
`openat`/`mmap`-Syscalls und `_fusion_load_path` operieren. Damit sieht der Linker genau die
Dateien, die der App-eigene Java-Loader (SoLoader/ApkSoSource/Packer) entpackt und schreibt —
die App entpackt sich selbst, wir unpacken NICHTS extern (ip_guard).

Zielagnostisch: kein LLU-Wissen, nur spezifikationstreue JDK/Android-Datei-API ueber das VFS.
"""
from __future__ import annotations

import io as _io
import os as _os
import posixpath
import zipfile

from core.java.objects import JString, JObject, JArray


# ---- VFS 2.0 des laufenden OS beschaffen (lazy aufbauen, SELBES Objekt wie Syscalls) ----
def get_shared_fs(eng):
    st = getattr(eng, "_os", None)
    if st is None:
        return None
    fs = getattr(st, "fs", None)
    if fs is None:
        try:
            from core.native.vfs import VFS
            fs = VFS.from_state(st)
            st.fs = fs
        except Exception:
            return None
    return fs


def _norm(p):
    if not p:
        return "/"
    p = str(p).replace("\\", "/")
    while "//" in p:
        p = p.replace("//", "/")
    if len(p) > 1 and p.endswith("/"):
        p = p[:-1]
    return p


def _txt(x):
    """String aus JString ODER konstruiertem String-JObject (fields['text'])."""
    if isinstance(x, JString):
        return x.text
    if isinstance(x, JObject):
        t = x.fields.get("text")
        if t is not None:
            return t
        p = x.fields.get("path")
        if p is not None:
            return p
    if isinstance(x, str):
        return x
    return None


def _file_path(o):
    return o.fields.get("path") if isinstance(o, JObject) else None


def install_fs_stubs(stubs, eng, pkg="com.example.app", native_lib_dir=None, apk_bytes=None,
                     apk_vfs_path="/data/app/base.apk"):
    """Verdrahtet die Java-Datei-/Zip-Stubs mit dem gemeinsamen VFS des Laufs.
    Legt die App-Verzeichnisse an, mountet (optional) die echte base.apk und setzt die
    ApplicationInfo-Pfade (nativeLibraryDir/dataDir/sourceDir). Rueckgabe: das fs-Objekt."""
    if apk_bytes and getattr(stubs, "_apk_cert_bytes", None) is None:
        try:
            with zipfile.ZipFile(_io.BytesIO(bytes(apk_bytes))) as _zf:
                certs = [
                    n for n in sorted(_zf.namelist())
                    if n.upper().startswith("META-INF/")
                    and n.upper().endswith((".RSA", ".DSA", ".EC"))
                ]
                if certs:
                    stubs._apk_cert_bytes = bytes(_zf.read(certs[0]))
        except Exception:
            pass
    fs = get_shared_fs(eng)
    if fs is None:
        return None
    stubs._shared_fs = fs

    data_dir = "/data/data/%s" % pkg
    native_lib_dir = _norm(native_lib_dir or "/data/app/%s/lib/arm64" % pkg)
    for d in (data_dir, data_dir + "/files", data_dir + "/cache", data_dir + "/app_lib",
              data_dir + "/lib-main", "/data/app", native_lib_dir):
        try:
            fs.add_dir(d)
        except Exception:
            pass
    if apk_bytes:
        try:
            fs.add_file(_norm(apk_vfs_path), bytes(apk_bytes))
        except Exception:
            pass

    stubs._app_info = {
        "nativeLibraryDir": native_lib_dir,
        "dataDir": data_dir,
        "sourceDir": _norm(apk_vfs_path),
        "publicSourceDir": _norm(apk_vfs_path),
        "packageName": pkg,
        "flags": 0,
    }

    _reg_file(stubs)
    _reg_streams(stubs)
    _reg_filter_streams(stubs)
    _reg_bytearray_streams(stubs)
    _reg_zip(stubs)
    _reg_appinfo(stubs)
    _reg_os_fd(stubs)
    _reg_context_storage(stubs, pkg, data_dir)
    _reg_dexloaders(stubs)
    _ensure_fs_stream_hook(stubs)
    return fs


# ---- java.io.File -------------------------------------------------------------
def _reg_file(stubs):
    R = stubs.register
    fs = stubs._shared_fs

    def _init1(a):
        if a and isinstance(a[0], JObject):
            a[0].fields["path"] = _norm(_txt(a[1]) if len(a) > 1 else "/")
        return None

    def _init2(a):
        # File(parent, child) ODER File(parentPathString, child)
        if a and isinstance(a[0], JObject):
            parent = a[1] if len(a) > 1 else None
            child = _txt(a[2]) if len(a) > 2 else ""
            pp = _file_path(parent) if isinstance(parent, JObject) else _txt(parent)
            a[0].fields["path"] = _norm((pp or "") + "/" + (child or ""))
        return None

    R("Ljava/io/File;", "<init>", "(Ljava/lang/String;)V", _init1)
    R("Ljava/io/File;", "<init>", "(Ljava/io/File;Ljava/lang/String;)V", _init2)
    R("Ljava/io/File;", "<init>", "(Ljava/lang/String;Ljava/lang/String;)V", _init2)

    def _path(a):
        return JString(_file_path(a[0]) or "")
    R("Ljava/io/File;", "getPath", "()Ljava/lang/String;", _path)
    R("Ljava/io/File;", "getAbsolutePath", "()Ljava/lang/String;", _path)
    R("Ljava/io/File;", "getCanonicalPath", "()Ljava/lang/String;", _path)
    R("Ljava/io/File;", "toString", "()Ljava/lang/String;", _path)

    def _mkfile(p):
        return JObject("Ljava/io/File;", {"path": _norm(p)})
    R("Ljava/io/File;", "getAbsoluteFile", "()Ljava/io/File;", lambda a: _mkfile(_file_path(a[0]) or ""))
    R("Ljava/io/File;", "getCanonicalFile", "()Ljava/io/File;", lambda a: _mkfile(_file_path(a[0]) or ""))

    def _name(a):
        return JString(posixpath.basename(_file_path(a[0]) or ""))
    R("Ljava/io/File;", "getName", "()Ljava/lang/String;", _name)

    def _parent(a):
        return JString(posixpath.dirname(_file_path(a[0]) or ""))
    R("Ljava/io/File;", "getParent", "()Ljava/lang/String;", _parent)
    R("Ljava/io/File;", "getParentFile", "()Ljava/io/File;",
      lambda a: _mkfile(posixpath.dirname(_file_path(a[0]) or "")))

    def _exists(a):
        p = _file_path(a[0])
        if not p:
            return 0
        try:
            return 1 if fs.lookup(p) is not None or fs.exists(p) else 0
        except Exception:
            return 0
    R("Ljava/io/File;", "exists", "()Z", _exists)
    R("Ljava/io/File;", "canRead", "()Z", _exists)
    R("Ljava/io/File;", "canWrite", "()Z", _exists)
    R("Ljava/io/File;", "canExecute", "()Z", _exists)

    def _kind(a, want):
        p = _file_path(a[0])
        ino = fs.lookup(p) if p else None
        return 1 if (ino is not None and getattr(ino, "kind", None) == want) else 0
    R("Ljava/io/File;", "isFile", "()Z", lambda a: _kind(a, "file"))
    R("Ljava/io/File;", "isDirectory", "()Z", lambda a: _kind(a, "dir"))

    def _len(a):
        p = _file_path(a[0])
        ino = fs.lookup(p) if p else None
        if ino is None:
            return 0
        try:
            return len(bytes(fs.read(ino)))
        except Exception:
            return 0
    R("Ljava/io/File;", "length", "()J", _len)

    def _mkdirs(a):
        p = _file_path(a[0])
        if not p:
            return 0
        try:
            fs.add_dir(p); return 1
        except Exception:
            return 0
    R("Ljava/io/File;", "mkdirs", "()Z", _mkdirs)
    R("Ljava/io/File;", "mkdir", "()Z", _mkdirs)

    def _delete(a):
        p = _file_path(a[0])
        try:
            if p and fs.unlink(p):
                return 1
            return 0
        except Exception:
            return 0
    R("Ljava/io/File;", "delete", "()Z", _delete)

    def _createNewFile(a):
        p = _file_path(a[0])
        if not p:
            return 0
        try:
            if fs.lookup(p) is None:
                fs.add_file(p, b"")
            return 1
        except Exception:
            return 0
    R("Ljava/io/File;", "createNewFile", "()Z", _createNewFile)

    def _listnames(a):
        p = _file_path(a[0])
        try:
            names = [(n[0] if isinstance(n, tuple) else n) for n in (fs.listdir(p) if p else [])]
        except Exception:
            names = []
        return JArray("[Ljava/lang/String;", [JString(n) for n in names])
    R("Ljava/io/File;", "list", "()[Ljava/lang/String;", _listnames)

    def _listfiles(a):
        p = _file_path(a[0])
        try:
            names = [(n[0] if isinstance(n, tuple) else n) for n in (fs.listdir(p) if p else [])]
        except Exception:
            names = []
        return JArray("[Ljava/io/File;", [_mkfile(_norm(p + "/" + nm)) for nm in names])
    R("Ljava/io/File;", "listFiles", "()[Ljava/io/File;", _listfiles)

    R("Ljava/io/File;", "setReadable", "(Z)Z", lambda a: 1)
    R("Ljava/io/File;", "setWritable", "(Z)Z", lambda a: 1)
    R("Ljava/io/File;", "setExecutable", "(Z)Z", lambda a: 1)
    R("Ljava/io/File;", "setExecutable", "(ZZ)Z", lambda a: 1)
    R("Ljava/io/File;", "setReadOnly", "()Z", lambda a: 1)
    R("Ljava/io/File;", "lastModified", "()J", lambda a: 0)


# ---- FileInputStream / FileOutputStream / RandomAccessFile --------------------
def _bytes_of_array(arr):
    if isinstance(arr, JArray):
        return bytes([int(x) & 0xFF for x in arr.data])
    return b""


def _reg_streams(stubs):
    R = stubs.register
    fs = stubs._shared_fs

    def _open_in(a):
        src = a[1] if len(a) > 1 else None
        p = _file_path(src) if isinstance(src, JObject) and src.cls == "Ljava/io/File;" else _txt(src)
        a[0].fields["path"] = _norm(p or "")
        a[0].fields["pos"] = 0
        return None
    for sig in ("(Ljava/io/File;)V", "(Ljava/lang/String;)V"):
        R("Ljava/io/FileInputStream;", "<init>", sig, _open_in)

    def _lookup_fd_blob(path):
        import re as _re
        m = _re.match(r"^/proc/(?:self|\d+)/(?:task/\d+/)?fd/(\d+)$", str(path or ""))
        if not m:
            return None
        blobs = getattr(fs, "_fd_blobs", None) or {}
        return blobs.get(int(m.group(1)))

    def _read_all(o):
        if "$data" in o.fields:
            return bytes(o.fields.get("$data") or b"")
        mem = o.fields.get("_mem")
        if mem is not None:
            return bytes(mem)
        p = o.fields.get("path")
        fdb = _lookup_fd_blob(p)
        if fdb is not None:
            return bytes(fdb)
        ino = fs.lookup(p)
        if ino is not None:
            return bytes(fs.read(ino))
        ret = getattr(fs, "_retained_files", None) or {}
        return bytes(ret.get(p, b""))

    def _get_pos(o):
        if "$data" in o.fields and "pos" not in o.fields:
            return int(o.fields.get("$pos", 0))
        return int(o.fields.get("pos", 0))

    def _set_pos(o, pos):
        o.fields["pos"] = pos
        if "$data" in o.fields:
            o.fields["$pos"] = pos

    def _read1(a):
        o = a[0]; data = _read_all(o); pos = _get_pos(o)
        if pos >= len(data):
            return -1
        _set_pos(o, pos + 1)
        return data[pos] & 0xFF
    R("Ljava/io/FileInputStream;", "read", "()I", _read1)

    def _readb(a):
        o = a[0]; arr = a[1] if len(a) > 1 else None
        data = _read_all(o); pos = _get_pos(o)
        if pos >= len(data) or not isinstance(arr, JArray):
            return -1
        cap = len(arr.data)
        off = int(a[2]) if len(a) > 3 else 0
        cnt = int(a[3]) if len(a) > 3 else cap
        n = min(cnt, cap - off, len(data) - pos)
        chunk = data[pos:pos + n]
        for i, b in enumerate(chunk):
            arr.data[off + i] = b
        _set_pos(o, pos + n)
        return n if n > 0 else -1
    R("Ljava/io/FileInputStream;", "read", "([B)I", _readb)
    R("Ljava/io/FileInputStream;", "read", "([BII)I", _readb)

    def _skip(a):
        o = a[0]; n = int(a[1]) if len(a) > 1 else 0
        data = _read_all(o); pos = _get_pos(o)
        adv = max(0, min(n, len(data) - pos))
        _set_pos(o, pos + adv)
        return adv
    R("Ljava/io/FileInputStream;", "skip", "(J)J", _skip)

    def _avail(a):
        o = a[0]; return max(0, len(_read_all(o)) - _get_pos(o))
    R("Ljava/io/FileInputStream;", "available", "()I", _avail)
    R("Ljava/io/FileInputStream;", "close", "()V", lambda a: None)

    def _stream_mark(a):
        o = a[0] if a else None
        if isinstance(o, JObject):
            if "in" in o.fields and isinstance(o.fields.get("in"), JObject):
                return _stream_mark([o.fields["in"]] + list(a[1:]))
            o.fields["mark"] = _get_pos(o)
        return None

    def _stream_reset(a):
        o = a[0] if a else None
        if isinstance(o, JObject):
            if "in" in o.fields and isinstance(o.fields.get("in"), JObject):
                return _stream_reset([o.fields["in"]])
            _set_pos(o, int(o.fields.get("mark", 0)))
        return None

    for _scls in ("Ljava/io/InputStream;", "Ljava/io/FileInputStream;"):
        R(_scls, "mark", "(I)V", _stream_mark)
        R(_scls, "reset", "()V", _stream_reset)
        R(_scls, "markSupported", "()Z", lambda a: 1)

    # Abstrakte InputStream-Sicht (invoke-virtual auf Ljava/io/InputStream;)
    for nm, sg, fn in (("read", "()I", _read1), ("read", "([B)I", _readb),
                       ("read", "([BII)I", _readb), ("skip", "(J)J", _skip),
                       ("available", "()I", _avail), ("close", "()V", lambda a: None)):
        R("Ljava/io/InputStream;", nm, sg, fn)

    # FileOutputStream: Writeback ins gemeinsame VFS
    def _open_out(a):
        dst = a[1] if len(a) > 1 else None
        p = _file_path(dst) if isinstance(dst, JObject) and dst.cls == "Ljava/io/File;" else _txt(dst)
        append = bool(a[2]) if len(a) > 2 and isinstance(a[2], int) else False
        p = _norm(p or "")
        a[0].fields["path"] = p
        buf = b""
        if append:
            ino = fs.lookup(p)
            if ino is not None:
                buf = bytes(fs.read(ino))
        a[0].fields["buf"] = bytearray(buf)
        a[0].fields["_persisted_len"] = len(buf)
        try:
            fs.add_file(p, bytes(buf))      # Datei existiert sofort (O_CREAT-Semantik)
        except Exception:
            pass
        return None
    for sig in ("(Ljava/io/File;)V", "(Ljava/lang/String;)V",
                "(Ljava/io/File;Z)V", "(Ljava/lang/String;Z)V"):
        R("Ljava/io/FileOutputStream;", "<init>", sig, _open_out)

    def _persist(o):
        try:
            path = o.fields.get("path")
            raw = bytes(o.fields.get("buf", b""))
            o.fields["_persisted_len"] = len(raw)
            fs.add_file(path, raw)
            # Entpackte Native-Libs, Hermes-Bundles, DEXs, ZIPs im Shadow-Cache behalten (auch nach File.delete)
            if len(raw) >= 16 and (
                raw[:4] == b"\x7fELF"
                or raw[:8] == b"\xc6\x1f\xbc\x03\xc1\x03\x19\x1f"
                or raw[:4] == b"dex\n"
                or raw[:4] == b"PK\x03\x04"
            ):
                ret = getattr(fs, "_retained_files", None)
                if ret is None:
                    ret = fs._retained_files = {}
                prev = ret.get(path)
                if prev is None or len(raw) >= len(prev):
                    ret[path] = raw
        except Exception:
            pass

    def _maybe_persist(o):
        buf = o.fields.get("buf")
        if buf is None:
            return
        cur_len = len(buf)
        prev_len = int(o.fields.get("_persisted_len", 0))
        if cur_len <= 65536 or (cur_len - prev_len) >= 2097152:
            _persist(o)

    def _write1(a):
        o = a[0]; o.fields.setdefault("buf", bytearray()).append(int(a[1]) & 0xFF); _maybe_persist(o); return None
    R("Ljava/io/FileOutputStream;", "write", "(I)V", _write1)

    def _writeb(a):
        o = a[0]; arr = a[1] if len(a) > 1 else None
        if isinstance(arr, JArray):
            off = int(a[2]) if len(a) > 3 else 0
            cnt = int(a[3]) if len(a) > 3 else len(arr.data)
            chunk = bytes(int(x) & 0xFF for x in arr.data[off:off + cnt])
            o.fields.setdefault("buf", bytearray()).extend(chunk)
            _maybe_persist(o)
        return None
    R("Ljava/io/FileOutputStream;", "write", "([B)V", _writeb)
    R("Ljava/io/FileOutputStream;", "write", "([BII)V", _writeb)
    R("Ljava/io/FileOutputStream;", "flush", "()V", lambda a: _persist(a[0]))
    R("Ljava/io/FileOutputStream;", "close", "()V", lambda a: _persist(a[0]))

    def _fos_getfd(a):
        o = a[0] if a else None
        if not isinstance(o, JObject):
            return JObject("Ljava/io/FileDescriptor;", {"descriptor": -1})
        _persist(o)
        fd = o.fields.get("_fd")
        if fd is None:
            nxt = getattr(fs, "_next_fd", 10)
            fs._next_fd = nxt + 1
            fd = nxt
            o.fields["_fd"] = fd
        path = o.fields.get("path")
        blobs = getattr(fs, "_fd_blobs", None)
        if blobs is None:
            blobs = fs._fd_blobs = {}
        blobs[fd] = bytes(o.fields.get("buf", b""))
        fd_paths = getattr(fs, "_fd_paths", None)
        if fd_paths is None:
            fd_paths = fs._fd_paths = {}
        if path:
            fd_paths[fd] = path
        fd_obj = JObject("Ljava/io/FileDescriptor;", {"descriptor": fd, "_path": path})
        return fd_obj
    R("Ljava/io/FileOutputStream;", "getFD", "()Ljava/io/FileDescriptor;", _fos_getfd)
    R("Ljava/io/FileDescriptor;", "sync", "()V", lambda a: None)

    def _fis_getfd(a):
        o = a[0] if a else None
        if not isinstance(o, JObject):
            return JObject("Ljava/io/FileDescriptor;", {"descriptor": -1})
        fd = o.fields.get("_fd")
        if fd is None:
            nxt = getattr(fs, "_next_fd", 10)
            fs._next_fd = nxt + 1
            fd = nxt
            o.fields["_fd"] = fd
        path = o.fields.get("path")
        data = _read_all(o)
        blobs = getattr(fs, "_fd_blobs", None)
        if blobs is None:
            blobs = fs._fd_blobs = {}
        blobs[fd] = bytes(data)
        fd_paths = getattr(fs, "_fd_paths", None)
        if fd_paths is None:
            fd_paths = fs._fd_paths = {}
        if path:
            fd_paths[fd] = path
        return JObject("Ljava/io/FileDescriptor;", {"descriptor": fd, "_path": path})
    R("Ljava/io/FileInputStream;", "getFD", "()Ljava/io/FileDescriptor;", _fis_getfd)

    # Abstrakte OutputStream-Sicht (invoke-virtual auf Ljava/io/OutputStream;)
    R("Ljava/io/OutputStream;", "write", "(I)V", _write1)
    R("Ljava/io/OutputStream;", "write", "([B)V", _writeb)
    R("Ljava/io/OutputStream;", "write", "([BII)V", _writeb)
    R("Ljava/io/OutputStream;", "flush", "()V", lambda a: _persist(a[0]))
    R("Ljava/io/OutputStream;", "close", "()V", lambda a: _persist(a[0]))

    # RandomAccessFile (read/seek/length) — fuer SoSources, die ELF-Header pruefen
    def _raf_init(a):
        src = a[1] if len(a) > 1 else None
        p = _file_path(src) if isinstance(src, JObject) and src.cls == "Ljava/io/File;" else _txt(src)
        a[0].fields["path"] = _norm(p or ""); a[0].fields["pos"] = 0
        return None
    R("Ljava/io/RandomAccessFile;", "<init>", "(Ljava/io/File;Ljava/lang/String;)V", _raf_init)
    R("Ljava/io/RandomAccessFile;", "<init>", "(Ljava/lang/String;Ljava/lang/String;)V", _raf_init)
    R("Ljava/io/RandomAccessFile;", "read", "([B)I", _readb)
    R("Ljava/io/RandomAccessFile;", "read", "([BII)I", _readb)
    R("Ljava/io/RandomAccessFile;", "read", "()I", _read1)
    R("Ljava/io/RandomAccessFile;", "length", "()J", lambda a: len(_read_all(a[0])))
    R("Ljava/io/RandomAccessFile;", "seek", "(J)V",
      lambda a: a[0].fields.__setitem__("pos", int(a[1]) if len(a) > 1 else 0))
    R("Ljava/io/RandomAccessFile;", "getFilePointer", "()J", lambda a: int(a[0].fields.get("pos", 0)))
    R("Ljava/io/RandomAccessFile;", "close", "()V", lambda a: None)


# ---- Struktureller Stream-Cipher-Beschleuniger (zielagnostisch) -----------------
def _unwrap_backing_bytes(stream_obj, fs):
    """Entwickelt Wrapper-Streams (Filter/Buffered/Data/Custom-Cipher-Streams) bis zur rohen Byte-Quelle."""
    seen = set()
    cur = stream_obj
    while isinstance(cur, JObject) and id(cur) not in seen:
        seen.add(id(cur))
        if cur is not stream_obj:
            ac = cur.fields.get("__accel_stream")
            if isinstance(ac, dict) and "data" in ac:
                data = ac["data"]
                pos = int(ac.get("pos", 0))
                ac["pos"] = len(data)
                return bytes(data[pos:])
        if cur.cls not in ("Ljava/io/FileInputStream;", "Ljava/io/ByteArrayInputStream;") and "in" in cur.fields:
            nxt = cur.fields.get("in")
            if isinstance(nxt, JObject):
                cur = nxt
                continue
        if "buf" in cur.fields:   # ByteArrayInputStream
            data = bytes(cur.fields.get("buf", b""))
            pos = int(cur.fields.get("pos", 0))
            cur.fields["pos"] = len(data)
            return data[pos:]
        if "$data" in cur.fields:  # InputStream (net_recorder)
            data = bytes(cur.fields.get("$data", b""))
            pos = int(cur.fields.get("$pos", 0))
            cur.fields["$pos"] = len(data)
            return data[pos:]
        mem = cur.fields.get("_mem")
        if mem is not None:
            data = bytes(mem)
            pos = int(cur.fields.get("pos", 0))
            cur.fields["pos"] = len(data)
            return data[pos:]
        p = cur.fields.get("path")
        if p and fs is not None:
            ino = fs.lookup(p)
            if ino is not None:
                data = bytes(fs.read(ino))
                pos = int(cur.fields.get("pos", 0))
                cur.fields["pos"] = len(data)
                return data[pos:]
        return None
    return None


def _find_cipher_method_defs(cls, vm):
    return [md for (mc, _mn, _ms), md in vm.registry.items() if mc == cls and md.insns]


def _analyze_cipher_layout(cls, vm):
    """Bestimmt Feldrollen eines Stream-Ciphers rein strukturell aus dem geparsten MethodDef-IR."""
    cache = getattr(vm, "_cipher_layout_cache", None)
    if cache is None:
        cache = vm._cipher_layout_cache = {}
    if cls in cache:
        return cache[cls]

    mds = _find_cipher_method_defs(cls, vm)
    inits = [m for m in mds if m.name == "<init>" and any(ins[0] == "new-array" for ins in m.insns)]
    if not inits:
        # PCG32 oder einfaches FilterInputStream ohne new-array pruefen
        all_inits = [m for m in mds if m.name == "<init>"]
        has_pcg = any(
            ins[0] == "const" and (int(ins[2]) & 0xFFFFFFFFFFFFFFFF) == 0x5851F42D4C957F2D
            for m in mds for ins in m.insns
        )
        if has_pcg and all_inits:
            init_m = max(all_inits, key=lambda m: len(m.insns))
            long_fields = []
            phase_field = None
            for ins in init_m.insns:
                if ins[0] == "iput-wide" and ins[4] not in long_fields:
                    long_fields.append(ins[4])
                elif ins[0] == "iput" and phase_field is None:
                    phase_field = ins[4]
            if len(long_fields) == 2 and phase_field:
                # LCG: state *= mult; state += inc (step method zeigt inc/state-Reihenfolge)
                state_f, inc_f = long_fields[0], long_fields[1]
                for m in mds:
                    if m.name not in ("<init>", "read", "skip", "available", "close", "markSupported"):
                        gets = [ins[4] for ins in m.insns if ins[0] == "iget-wide" and ins[3] == cls]
                        if len(gets) >= 2 and gets[0] in long_fields and gets[1] in long_fields and gets[0] != gets[1]:
                            state_f, inc_f = gets[0], gets[1]
                            break
                res = {"kind": "pcg32", "state": state_f, "inc": inc_f, "phase": phase_field}
                cache[cls] = res
                return res
        cache[cls] = None
        return None

    init_m = max(inits, key=lambda m: len(m.insns))
    const_vals = {}
    byte_bufs = []
    block_sz = 0
    look_f = step_f = pos_f = len_f = nr_f = mode_f = None
    iv_f = rk_f = perm_f = cipher_obj_f = None
    has_aes_perm = has_bf = has_tea = False
    consts_seen = {}

    for idx, ins in enumerate(init_m.insns):
        op = ins[0]
        if op == "const":
            const_vals[int(ins[1])] = int(ins[2]) & 0xFFFFFFFF
        elif op == "new-array" and ins[3] == "[B":
            sz = const_vals.get(int(ins[2]))
            if sz in (8, 16):
                block_sz = sz
        elif op == "invoke-static" and ins[2] == "Ljava/lang/System;" and ins[3] == "arraycopy":
            # Ziel-Feld suchen (v_dst wird kurz vorher per iget geholt)
            dst_r = ins[1][2] if len(ins[1]) > 2 else -1
            for k in range(idx - 1, max(-1, idx - 8), -1):
                pk = init_m.insns[k]
                if pk[0] == "iget-object" and pk[1] == dst_r and pk[3] == cls:
                    iv_f = pk[4]
                    break
        elif op in ("iput", "iput-object"):
            val_r, fn = int(ins[1]), ins[4]
            cval = const_vals.get(val_r)
            if op == "iput-object":
                # Typen ueber vorherigen new-array / invoke pruefen
                for k in range(idx - 1, max(-1, idx - 5), -1):
                    pk = init_m.insns[k]
                    if pk[0] == "new-array" and pk[1] == val_r and pk[3] == "[B":
                        byte_bufs.append(fn)
                        break
                    if pk[0] in ("invoke-static", "invoke-direct"):
                        sig = pk[4]
                        if sig.endswith(")[[B"):
                            perm_f = fn
                            has_aes_perm = True
                            break
                        if sig.endswith(")[I"):
                            rk_f = fn
                            break
                    if pk[0] == "new-instance" and pk[1] == val_r:
                        cipher_obj_f = fn
                        has_bf = True
                        break
            else:
                if cval == 0x7FFFFFFF:
                    look_f = fn
                elif cval == 1 and step_f is None:
                    step_f = fn
                elif cval in (8, 16):
                    if pos_f is None:
                        pos_f = fn
                    elif len_f is None:
                        len_f = fn

    # available() bestaetigt (len_f, pos_f)
    for m in mds:
        if m.name == "available":
            igets = [ins[4] for ins in m.insns if ins[0] == "iget" and ins[3] == cls]
            if len(igets) >= 2:
                len_f, pos_f = igets[0], igets[1]

    # interval1, interval2, mode, nr aus Hilfsmethoden / statics identifizieren
    i1_f = i2_f = None
    for m in mds:
        if m.name not in ("<init>", "read", "skip", "available", "close", "markSupported"):
            insns = m.insns
            # Intervall-Muster: iget a; iget b; if-ne a, b -> (a, b) = (i1, i2)
            for j in range(len(insns) - 2):
                if (insns[j][0] == "iget" and insns[j + 1][0] == "iget" and insns[j + 2][0] == "if-ne"
                        and insns[j][3] == cls and insns[j + 1][3] == cls
                        and insns[j][4] not in (look_f, pos_f, len_f)):
                    i1_f, i2_f = insns[j][4], insns[j + 1][4]
                    break
            # TEA-Pruefung: sget-short (golden ratio delta) in der Klasse
            if any(ins[0] == "sget-short" for ins in insns):
                has_tea = True

    if has_aes_perm and block_sz == 16 and rk_f and perm_f:
        # Nr in <init> (p2 nach iput)
        for j in range(len(init_m.insns)):
            ins = init_m.insns[j]
            if ins[0] == "iput" and ins[4] not in (step_f, look_f, pos_f, len_f, i1_f, i2_f):
                if nr_f is None:
                    nr_f = ins[4]
        if i1_f is None or i2_f is None:
            rem = [ins[4] for ins in init_m.insns if ins[0] == "iput" and ins[4] not in (step_f, look_f, pos_f, len_f, nr_f)]
            if len(rem) >= 2:
                i1_f, i2_f = rem[-2], rem[-1]
        if len(byte_bufs) >= 2 and nr_f:
            res = {
                "kind": "aes_ttable", "in_buf": byte_bufs[0], "out_buf": byte_bufs[1],
                "rk": rk_f, "perm": perm_f, "nr": nr_f, "look": look_f,
                "pos": pos_f, "len": len_f, "step": step_f, "i1": i1_f, "i2": i2_f,
            }
            cache[cls] = res
            return res

    if has_bf and block_sz == 8 and cipher_obj_f and len(byte_bufs) >= 3:
        # Nr nach Math.min/max, mode vor Arraycopy
        for j, ins in enumerate(init_m.insns):
            if ins[0] == "iput" and ins[4] not in (step_f, look_f, pos_f, len_f, i1_f, i2_f):
                if nr_f is None:
                    nr_f = ins[4]
                elif mode_f is None:
                    mode_f = ins[4]
        res = {
            "kind": "blowfish", "work_buf": byte_bufs[0], "iv_buf": iv_f or byte_bufs[1],
            "prev_buf": byte_bufs[2], "cipher_obj": cipher_obj_f, "nr": nr_f, "mode": mode_f,
            "look": look_f, "pos": pos_f, "len": len_f, "step": step_f, "i1": i1_f, "i2": i2_f,
        }
        cache[cls] = res
        return res

    if has_tea and block_sz == 8 and len(byte_bufs) >= 3:
        # TEA key schedule: nr, mode, und 4 keys (k2, k3, k0, k1) in Feistel-Schleife
        kfields = []
        nr_loop_f = None
        for m in mds:
            insns = m.insns
            if any(ins[0] == "sget-short" for ins in insns):
                for j, ins in enumerate(insns):
                    if ins[0] == "if-ge" and j > 0 and insns[j - 1][0] == "iget":
                        nr_loop_f = insns[j - 1][4]
                    if nr_loop_f and ins[0] == "iget" and ins[3] == cls and ins[4] != nr_loop_f:
                        if ins[4] not in kfields:
                            kfields.append(ins[4])
                        if len(kfields) == 4:
                            break
        delta_s = 0
        for st_v in vm.statics.get(cls, {}).values():
            if isinstance(st_v, int):
                v = st_v & 0xFFFF
                delta_s = v - 0x10000 if (v & 0x8000) else v
                break
        if nr_loop_f and len(kfields) == 4:
            other_i = [ins[4] for ins in init_m.insns
                       if ins[0] == "iput" and ins[4] not in (step_f, look_f, pos_f, len_f, nr_loop_f, i1_f, i2_f)]
            mode_f = other_i[0] if other_i else None
            res = {
                "kind": "tea", "work_buf": byte_bufs[0], "iv_buf": iv_f or byte_bufs[1],
                "prev_buf": byte_bufs[2], "nr": nr_loop_f, "mode": mode_f, "delta": delta_s,
                "keys": tuple(kfields), "look": look_f, "pos": pos_f, "len": len_f,
                "step": step_f, "i1": i1_f, "i2": i2_f,
            }
            cache[cls] = res
            return res

    cache[cls] = None
    return None


def _ror8(x: int) -> int:
    x &= 0xFFFFFFFF
    return ((x >> 8) | ((x & 0xFF) << 24)) & 0xFFFFFFFF


def _s32_c(x: int) -> int:
    x &= 0xFFFFFFFF
    return x - 0x100000000 if (x & 0x80000000) else x


def _order_aes_ttables(statics: dict):
    sbox = None
    int_tabs = []
    for v in statics.values():
        if isinstance(v, JArray) and len(v.data) == 256:
            if v.eltype in ("B", "[B") and sbox is None:
                sbox = [int(b) & 0xFF for b in v.data]
            elif v.eltype in ("I", "[I"):
                int_tabs.append([int(x) & 0xFFFFFFFF for x in v.data])
    if sbox is None or len(int_tabs) < 4:
        return None
    # T0..T3 via 8-Bit-Rechtsrotation identifizieren (T1 = ror8(T0), T2 = ror8(T1), T3 = ror8(T2))
    for t0 in int_tabs:
        m = [t0]
        cur = t0
        ok = True
        for _ in range(3):
            nxt = next((c for c in int_tabs if c is not cur and _ror8(cur[1]) == c[1] and _ror8(cur[5]) == c[5]), None)
            if nxt is None:
                ok = False
                break
            m.append(nxt)
            cur = nxt
        if ok:
            return m[0], m[1], m[2], m[3], sbox
    return None


def _try_accelerate_cipher_stream(stream_obj, stubs):
    """Erkennt strukturell verschluesselte FilterInputStreams (AES/Blowfish/TEA/PCG32) und liefert
    einen vor-entschluesselten Stream ({data, pos}) in Python-Geschwindigkeit."""
    if not isinstance(stream_obj, JObject):
        return None
    if stream_obj.cls in (
        "Ljava/util/zip/ZipInputStream;",
        "Ljava/util/jar/JarInputStream;",
        "Ljava/util/zip/InflaterInputStream;",
        "Ljava/util/zip/GZIPInputStream;",
        "Ljava/io/FileInputStream;",
        "Ljava/io/ByteArrayInputStream;",
    ):
        return None
    if stream_obj.fields.get("__accel_failed"):
        return None
    if isinstance(stream_obj.fields.get("__accel_stream"), dict):
        return stream_obj.fields["__accel_stream"]
    # BufferedInputStream / FilterInputStream auf inneres Objekt abwickeln
    cur = stream_obj
    if cur.cls in ("Ljava/io/FilterInputStream;", "Ljava/io/BufferedInputStream;",
                   "Ljava/io/DataInputStream;", "Ljava/io/PushbackInputStream;"):
        inner = cur.fields.get("in")
        return _try_accelerate_cipher_stream(inner, stubs) if isinstance(inner, JObject) else None

    vm = getattr(stubs, "vm", None)
    fs = getattr(stubs, "_shared_fs", None)
    if vm is None:
        return None
    layout = _analyze_cipher_layout(cur.cls, vm)
    if layout is None:
        inner = cur.fields.get("in")
        if isinstance(inner, JObject):
            ac_inner = _try_accelerate_cipher_stream(inner, stubs)
            if ac_inner is not None:
                cur.fields["__accel_stream"] = ac_inner
                return ac_inner
        cur.fields["__accel_failed"] = True
        return None

    kind = layout["kind"]
    f = cur.fields

    # --- PCG32 XOR ---
    if kind == "pcg32":
        raw = _unwrap_backing_bytes(cur, fs)
        if raw is None:
            return None
        state = int(f.get(layout["state"], 0)) & 0xFFFFFFFFFFFFFFFF
        inc = int(f.get(layout["inc"], 1)) & 0xFFFFFFFFFFFFFFFF
        phase = int(f.get(layout["phase"], 0)) & 7
        out = bytearray(raw)
        n_raw = len(raw)
        start = (8 - phase) & 7 if phase else 0
        for i in range(start, n_raw, 8):
            old = state
            state = (old * 0x5851F42D4C957F2D + inc) & 0xFFFFFFFFFFFFFFFF
            xs = (((old >> 18) ^ old) >> 27) & 0xFFFFFFFF
            rot = (old >> 59) & 31
            k = ((xs >> rot) | (xs << ((-rot) & 31))) & 0xFF
            out[i] ^= k
        phase = (phase + n_raw) & 7
        f[layout["state"]] = state
        f[layout["phase"]] = phase
        ac = {"data": bytes(out), "pos": 0}
        f["__accel_stream"] = ac
        return ac

    # --- Block-Chiffren (AES T-table / Blowfish / TEA) ---
    # Zuerst Tabellen/Feld-Layout validieren, bevor _unwrap_backing_bytes den inneren Stream konsumiert!
    if kind == "aes_ttable":
        tables = _order_aes_ttables(vm.statics.get(cur.cls, {}))
        rk_arr = f.get(layout["rk"])
        perm_arr = f.get(layout["perm"])
        out_arr = f.get(layout["out_buf"])
        nr = int(f.get(layout["nr"], 4))
        if tables is None or not isinstance(rk_arr, JArray) or not isinstance(perm_arr, JArray):
            if tables is None:
                cur.fields["__accel_failed"] = True
            return None
    elif kind == "blowfish":
        cobj = f.get(layout["cipher_obj"])
        iv_arr = f.get(layout["iv_buf"])
        work_arr = f.get(layout["work_buf"])
        nr = int(f.get(layout["nr"], 3))
        mode = int(f.get(layout["mode"], 1))
        if not isinstance(cobj, JObject):
            return None
        P_arr = next(
            (v for v in cobj.fields.values()
             if isinstance(v, JArray) and v.eltype in ("I", "[I") and 4 <= len(v.data) <= 18),
            None,
        )
        S_arr = next(
            (v for v in cobj.fields.values()
             if isinstance(v, JArray) and len(v.data) == 4
             and all(isinstance(r, JArray) and len(r.data) == 256 for r in v.data)),
            None,
        )
        if P_arr is None or S_arr is None:
            return None
    elif kind == "tea":
        iv_arr = f.get(layout["iv_buf"])
        work_arr = f.get(layout["work_buf"])
        nr = int(f.get(layout["nr"], 5))
        mode = int(f.get(layout["mode"], 0))
        delta = int(layout["delta"])
        if len(layout.get("keys") or ()) != 4:
            return None
    else:
        return None

    look_f = layout.get("look")
    pos_f = layout.get("pos")
    len_f = layout.get("len")
    step_f = layout.get("step")
    i1_f = layout.get("i1")
    i2_f = layout.get("i2")
    look = int(f.get(look_f, 0x7FFFFFFF)) if look_f else 0x7FFFFFFF
    look_s = _s32_c(look)
    cur_pos = int(f.get(pos_f, 0)) if pos_f else 0
    cur_len = int(f.get(len_f, 0)) if len_f else 0

    raw_tail = _unwrap_backing_bytes(cur, fs)
    if raw_tail is None:
        return None
    if look_s == 0x7FFFFFFF:
        raw_ct = raw_tail
    elif look_s < 0:
        raw_ct = b""
    else:
        raw_ct = bytes([look_s & 0xFF]) + raw_tail

    step = int(f.get(step_f, 1)) if step_f else 1
    i1 = int(f.get(i1_f, 100)) if i1_f else 100
    i2 = int(f.get(i2_f, 100)) if i2_f else 100

    out_chunks = []
    if kind == "aes_ttable":
        T0, T1, T2, T3, sbox = tables
        rk = [int(x) & 0xFFFFFFFF for x in rk_arr.data]
        perm = [[int(b) & 0xFF for b in row.data] for row in perm_arr.data]
        if 0 <= cur_pos < cur_len and isinstance(out_arr, JArray) and cur_len <= len(out_arr.data):
            out_chunks.append(bytes(int(out_arr.data[k]) & 0xFF for k in range(cur_pos, cur_len)))
        total = (len(raw_ct) // 16) * 16
        out_buf = bytearray(total)
        mv = memoryview(raw_ct)
        for off in range(0, total, 16):
            do_dec = (i1 == i2) or (step <= i1)
            if do_dec:
                s0 = int.from_bytes(mv[off:off + 4], "big") ^ rk[0]
                s1 = int.from_bytes(mv[off + 4:off + 8], "big") ^ rk[1]
                s2 = int.from_bytes(mv[off + 8:off + 12], "big") ^ rk[2]
                s3 = int.from_bytes(mv[off + 12:off + 16], "big") ^ rk[3]
                s = (s0, s1, s2, s3)
                k = 4
                for _r in range(1, nr):
                    p0, p1, p2, p3 = perm[0], perm[1], perm[2], perm[3]
                    s = (
                        T0[(s[p0[0]] >> 24) & 0xFF] ^ T1[(s[p1[0]] >> 16) & 0xFF] ^ T2[(s[p2[0]] >> 8) & 0xFF] ^ T3[s[p3[0]] & 0xFF] ^ rk[k],
                        T0[(s[p0[1]] >> 24) & 0xFF] ^ T1[(s[p1[1]] >> 16) & 0xFF] ^ T2[(s[p2[1]] >> 8) & 0xFF] ^ T3[s[p3[1]] & 0xFF] ^ rk[k + 1],
                        T0[(s[p0[2]] >> 24) & 0xFF] ^ T1[(s[p1[2]] >> 16) & 0xFF] ^ T2[(s[p2[2]] >> 8) & 0xFF] ^ T3[s[p3[2]] & 0xFF] ^ rk[k + 2],
                        T0[(s[p0[3]] >> 24) & 0xFF] ^ T1[(s[p1[3]] >> 16) & 0xFF] ^ T2[(s[p2[3]] >> 8) & 0xFF] ^ T3[s[p3[3]] & 0xFF] ^ rk[k + 3],
                    )
                    k += 4
                p0, p1, p2, p3 = perm[0], perm[1], perm[2], perm[3]
                for c in range(4):
                    kc = rk[k + c]
                    base = off + c * 4
                    out_buf[base] = sbox[(s[p0[c]] >> 24) & 0xFF] ^ ((kc >> 24) & 0xFF)
                    out_buf[base + 1] = sbox[(s[p1[c]] >> 16) & 0xFF] ^ ((kc >> 16) & 0xFF)
                    out_buf[base + 2] = sbox[(s[p2[c]] >> 8) & 0xFF] ^ ((kc >> 8) & 0xFF)
                    out_buf[base + 3] = sbox[s[p3[c]] & 0xFF] ^ (kc & 0xFF)
            else:
                out_buf[off:off + 16] = raw_ct[off:off + 16]
            if i1 != i2:
                step = (step + 1) if step < i2 else 1
        if total >= 16:
            pad = out_buf[total - 1] & 0xFF
            if 0 < pad <= 16:
                del out_buf[total - pad:]
        out_chunks.append(bytes(out_buf))

    elif kind == "blowfish":
        P_rev = [int(x) & 0xFFFFFFFF for x in reversed(P_arr.data)]
        S = [[int(x) & 0xFFFFFFFF for x in row.data] for row in S_arr.data]
        s0, s1, s2, s3 = S[0], S[1], S[2], S[3]
        if 0 <= cur_pos < cur_len and isinstance(work_arr, JArray) and cur_len <= len(work_arr.data):
            out_chunks.append(bytes(int(work_arr.data[k]) & 0xFF for k in range(cur_pos, cur_len)))
        iv = bytearray(int(x) & 0xFF for x in iv_arr.data[:8]) if isinstance(iv_arr, JArray) else bytearray(8)
        total = (len(raw_ct) // 8) * 8
        out_buf = bytearray(total)
        mv = memoryview(raw_ct)
        pm2, pm1 = P_rev[-2], P_rev[-1]
        for off in range(0, total, 8):
            do_dec = (i1 == i2) or (step <= i1)
            blk = mv[off:off + 8]
            if do_dec:
                L = int.from_bytes(blk[:4], "big")
                R = int.from_bytes(blk[4:8], "big")
                for i in range(nr):
                    L ^= P_rev[i]
                    fval = (((s0[(L >> 24) & 0xFF] + s1[(L >> 16) & 0xFF]) & 0xFFFFFFFF) ^ s2[(L >> 8) & 0xFF]) + s3[L & 0xFF]
                    R ^= (fval & 0xFFFFFFFF)
                    L, R = R, L
                L ^= pm2
                R ^= pm1
                pt = R.to_bytes(4, "big") + L.to_bytes(4, "big")
                if mode == 2:
                    pt = bytes(pt[k] ^ iv[k] for k in range(8))
                    iv[:] = blk
                out_buf[off:off + 8] = pt
            else:
                out_buf[off:off + 8] = blk
            if i1 != i2:
                step = (step + 1) if step < i2 else 1
        if total >= 8:
            pad = out_buf[total - 1] & 0xFF
            if 0 < pad <= 8:
                del out_buf[total - pad:]
        out_chunks.append(bytes(out_buf))

    elif kind == "tea":
        k2, k3, k0, k1 = (int(f.get(kf, 0)) for kf in layout["keys"])
        if 0 <= cur_pos < cur_len and isinstance(work_arr, JArray) and cur_len <= len(work_arr.data):
            out_chunks.append(bytes(int(work_arr.data[k]) & 0xFF for k in range(cur_pos, cur_len)))
        iv = bytearray(int(x) & 0xFF for x in iv_arr.data[:8]) if isinstance(iv_arr, JArray) else bytearray(8)
        total = (len(raw_ct) // 8) * 8
        out_buf = bytearray(total)
        mv = memoryview(raw_ct)
        for off in range(0, total, 8):
            do_dec = (i1 == i2) or (step <= i1)
            blk = mv[off:off + 8]
            if do_dec:
                v0 = int.from_bytes(blk[:4], "big")
                v1 = int.from_bytes(blk[4:8], "big")
                for r in range(nr):
                    dm = _s32_c(delta * (nr - r))
                    t0 = ((v0 + dm) & 0xFFFFFFFF) ^ (((v0 << 4) + k2) & 0xFFFFFFFF) ^ (((v0 >> 5) + k3) & 0xFFFFFFFF)
                    v1 = (v1 - t0) & 0xFFFFFFFF
                    t1 = ((v1 + dm) & 0xFFFFFFFF) ^ (((v1 << 4) + k0) & 0xFFFFFFFF) ^ (((v1 >> 5) + k1) & 0xFFFFFFFF)
                    v0 = (v0 - t1) & 0xFFFFFFFF
                pt = v0.to_bytes(4, "big") + v1.to_bytes(4, "big")
                if mode == 3:
                    pt = bytes(pt[k] ^ iv[k] for k in range(8))
                    iv[:] = blk
                out_buf[off:off + 8] = pt
            else:
                out_buf[off:off + 8] = blk
            if i1 != i2:
                step = (step + 1) if step < i2 else 1
        if total >= 8:
            pad = out_buf[total - 1] & 0xFF
            if 0 < pad <= 8:
                del out_buf[total - pad:]
        out_chunks.append(bytes(out_buf))

    if look_f:
        f[look_f] = -1 & 0xFFFFFFFF
    if pos_f and len_f:
        f[pos_f] = f[len_f]
    ac = {"data": b"".join(out_chunks), "pos": 0}
    f["__accel_stream"] = ac
    return ac


def _accel_stream_op(ac: dict, name: str, sig: str, args):
    data = ac["data"]
    pos = int(ac.get("pos", 0))
    if name == "read" and sig == "()I":
        if pos >= len(data):
            return -1 & 0xFFFFFFFF
        ac["pos"] = pos + 1
        return data[pos] & 0xFF
    if name == "read" and sig in ("([B)I", "([BII)I"):
        arr = args[0] if args else None
        if not isinstance(arr, JArray):
            return -1 & 0xFFFFFFFF
        if pos >= len(data):
            return -1 & 0xFFFFFFFF
        off = int(args[1]) if len(args) > 2 else 0
        cnt = int(args[2]) if len(args) > 2 else len(arr.data)
        n = min(max(0, cnt), len(arr.data) - off, len(data) - pos)
        if n <= 0:
            return -1 & 0xFFFFFFFF
        chunk = data[pos:pos + n]
        for i, bv in enumerate(chunk):
            arr.data[off + i] = bv
        ac["pos"] = pos + n
        return n
    if name == "skip" and sig == "(J)J":
        req = max(0, int(args[0]) if args else 0)
        adv = min(req, max(0, len(data) - pos))
        ac["pos"] = pos + adv
        return adv
    if name == "available" and sig == "()I":
        return max(0, len(data) - pos)
    if name == "close" and sig == "()V":
        ac["pos"] = len(data)
        return None
    return 0


def _ensure_fs_stream_hook(stubs):
    vm = getattr(stubs, "vm", None)
    if vm is None or getattr(stubs, "_fs_stream_hook_installed", False):
        return
    def _hook(rcls, cls, name, sig, logical, static, vm_obj):
        if static or name not in ("read", "skip", "available", "close") or not logical:
            return False, None
        recv = logical[0]
        if not isinstance(recv, JObject):
            return False, None
        if recv.cls in ("Ljava/io/FileInputStream;", "Ljava/io/ByteArrayInputStream;",
                        "Ljava/util/zip/ZipInputStream;"):
            return False, None
        ac = _try_accelerate_cipher_stream(recv, stubs)
        if ac is None:
            return False, None
        return True, _accel_stream_op(ac, name, sig, logical[1:])
    hooks = getattr(vm, "_method_hooks", None)
    if isinstance(hooks, list):
        hooks.append(_hook)
        stubs._fs_stream_hook_installed = True


# ---- Generische Stream-Delegation (Filter/Buffered) + ByteArray-Streams --------
def _dispatch(stubs, recv, name, sig, args):
    """Virtueller Aufruf auf `recv` -> echte Methode (Smali via _resolve_method, sonst Stub).
    So delegieren FilterInputStream & Co. an BELIEBIGE innere Streams (App-Decrypter/Inflater)."""
    if isinstance(recv, JObject) and name in ("read", "skip", "available", "close"):
        ac = _try_accelerate_cipher_stream(recv, stubs)
        if ac is not None:
            return _accel_stream_op(ac, name, sig, args)
    vm = getattr(stubs, "vm", None)
    rcls = (recv.cls if isinstance(recv, JObject)
            else ("Ljava/lang/String;" if isinstance(recv, JString) else "Ljava/lang/Object;"))
    if vm is not None:
        md = None
        try:
            md = vm._resolve_method(rcls, rcls, name, sig)
        except Exception:
            md = None
        if md is not None:
            return vm._run_mdef(md, [recv] + list(args))
    try:
        ok, ret = stubs.resolve(rcls, name, sig, [recv] + list(args), static=False)
        return ret if ok else -1
    except Exception:
        return -1


def _reg_filter_streams(stubs):
    R = stubs.register
    _FILTER_BASE = ("Ljava/io/FilterInputStream;", "Ljava/io/BufferedInputStream;",
                    "Ljava/io/DataInputStream;", "Ljava/io/PushbackInputStream;")

    def _fin_init(a):
        _ensure_fs_stream_hook(stubs)
        if a and isinstance(a[0], JObject):
            a[0].fields["in"] = a[1] if len(a) > 1 else None
        return None

    def _fin(name, sig):
        def fn(a):
            o = a[0]
            if isinstance(o, JObject) and name == "read" and sig == "([B)I" and o.cls not in _FILTER_BASE:
                arr = a[1] if len(a) > 1 else None
                if isinstance(arr, JArray):
                    return _dispatch(stubs, o, "read", "([BII)I", [arr, 0, len(arr.data)])
            inner = o.fields.get("in") if isinstance(o, JObject) else None
            if inner is None:
                return -1 if sig.endswith("I") or sig.endswith("J") else None
            return _dispatch(stubs, inner, name, sig, list(a[1:]))
        return fn

    def _fin_mark(a):
        o = a[0] if a else None
        inner = o.fields.get("in") if isinstance(o, JObject) else None
        if isinstance(inner, JObject):
            return _dispatch(stubs, inner, "mark", "(I)V", list(a[1:]))
        return None

    def _fin_reset(a):
        o = a[0] if a else None
        inner = o.fields.get("in") if isinstance(o, JObject) else None
        if isinstance(inner, JObject):
            return _dispatch(stubs, inner, "reset", "()V", [])
        return None

    for cls in _FILTER_BASE:
        R(cls, "<init>", "(Ljava/io/InputStream;)V", _fin_init)
        R(cls, "<init>", "(Ljava/io/InputStream;I)V", _fin_init)
        for nm, sg in (("read", "()I"), ("read", "([B)I"), ("read", "([BII)I"),
                       ("skip", "(J)J"), ("available", "()I"), ("close", "()V")):
            R(cls, nm, sg, _fin(nm, sg))
        R(cls, "mark", "(I)V", _fin_mark)
        R(cls, "reset", "()V", _fin_reset)
        R(cls, "markSupported", "()Z", lambda a: 1)

    # DataInputStream binaeres Lesen (readFully, readInt/Long/Short/Byte)
    def _read_exact(inner, n: int) -> bytes:
        if inner is None or n <= 0:
            return b""
        buf = JArray("[B", [0] * n)
        got = 0
        while got < n:
            k = _dispatch(stubs, inner, "read", "([BII)I", [buf, got, n - got])
            if isinstance(k, int) and k > 0:
                got += k
            else:
                b0 = _dispatch(stubs, inner, "read", "()I", [])
                if not isinstance(b0, int) or b0 < 0:
                    break
                buf.data[got] = b0 & 0xFF
                got += 1
        return bytes(int(x) & 0xFF for x in buf.data[:got])

    def _dis_read_fully(a):
        o = a[0]; arr = a[1] if len(a) > 1 else None
        inner = o.fields.get("in") if isinstance(o, JObject) else None
        if not isinstance(arr, JArray) or inner is None:
            return None
        off = int(a[2]) if len(a) > 3 else 0
        ln = int(a[3]) if len(a) > 3 else len(arr.data)
        raw = _read_exact(inner, max(0, ln))
        for i, b in enumerate(raw):
            if off + i < len(arr.data):
                arr.data[off + i] = b
        return None

    def _dis_read_int(a, nbytes: int, signed: bool):
        o = a[0] if a else None
        inner = o.fields.get("in") if isinstance(o, JObject) else None
        raw = _read_exact(inner, nbytes)
        if len(raw) < nbytes:
            return 0
        return int.from_bytes(raw, "big", signed=signed)

    R("Ljava/io/DataInputStream;", "readFully", "([B)V", _dis_read_fully)
    R("Ljava/io/DataInputStream;", "readFully", "([BII)V", _dis_read_fully)
    R("Ljava/io/DataInputStream;", "readInt", "()I", lambda a: _dis_read_int(a, 4, True))
    R("Ljava/io/DataInputStream;", "readLong", "()J", lambda a: _dis_read_int(a, 8, True))
    R("Ljava/io/DataInputStream;", "readShort", "()S", lambda a: _dis_read_int(a, 2, True))
    R("Ljava/io/DataInputStream;", "readUnsignedShort", "()I", lambda a: _dis_read_int(a, 2, False))
    R("Ljava/io/DataInputStream;", "readByte", "()B", lambda a: _dis_read_int(a, 1, True))
    R("Ljava/io/DataInputStream;", "readUnsignedByte", "()I", lambda a: _dis_read_int(a, 1, False))
    R("Ljava/io/DataInputStream;", "skipBytes", "(I)I",
      lambda a: int(_fin("skip", "(J)J")([a[0], int(a[1]) if len(a) > 1 else 0]) or 0))

    def _fout_init(a):
        if a and isinstance(a[0], JObject):
            a[0].fields["out"] = a[1] if len(a) > 1 else None
        return None

    def _fout(name, sig):
        def fn(a):
            o = a[0]; inner = o.fields.get("out") if isinstance(o, JObject) else None
            if inner is None:
                return None
            return _dispatch(stubs, inner, name, sig, list(a[1:]))
        return fn

    for cls in ("Ljava/io/FilterOutputStream;", "Ljava/io/BufferedOutputStream;",
                "Ljava/io/DataOutputStream;"):
        R(cls, "<init>", "(Ljava/io/OutputStream;)V", _fout_init)
        R(cls, "<init>", "(Ljava/io/OutputStream;I)V", _fout_init)
        for nm, sg in (("write", "(I)V"), ("write", "([B)V"), ("write", "([BII)V"),
                       ("flush", "()V"), ("close", "()V")):
            R(cls, nm, sg, _fout(nm, sg))


def _reg_bytearray_streams(stubs):
    R = stubs.register

    def _bais_init(a):
        o = a[0]; arr = a[1] if len(a) > 1 else None
        data = _bytes_of_array(arr)
        off = int(a[2]) if len(a) > 3 else 0
        cnt = int(a[3]) if len(a) > 3 else None
        o.fields["buf"] = data[off:(off + cnt) if cnt is not None else len(data)]
        o.fields["pos"] = 0
        o.fields["mark"] = 0
        return None
    R("Ljava/io/ByteArrayInputStream;", "<init>", "([B)V", _bais_init)
    R("Ljava/io/ByteArrayInputStream;", "<init>", "([BII)V", _bais_init)

    def _bais_read1(a):
        o = a[0]; buf = o.fields.get("buf", b""); pos = int(o.fields.get("pos", 0))
        if pos >= len(buf): return -1
        o.fields["pos"] = pos + 1; return buf[pos] & 0xFF
    R("Ljava/io/ByteArrayInputStream;", "read", "()I", _bais_read1)

    def _bais_readb(a):
        o = a[0]; arr = a[1] if len(a) > 1 else None
        buf = o.fields.get("buf", b""); pos = int(o.fields.get("pos", 0))
        if pos >= len(buf) or not isinstance(arr, JArray): return -1
        off = int(a[2]) if len(a) > 3 else 0
        cnt = int(a[3]) if len(a) > 3 else len(arr.data)
        n = min(cnt, len(arr.data) - off, len(buf) - pos)
        for i in range(n):
            arr.data[off + i] = buf[pos + i]
        o.fields["pos"] = pos + n; return n if n > 0 else -1
    R("Ljava/io/ByteArrayInputStream;", "read", "([B)I", _bais_readb)
    R("Ljava/io/ByteArrayInputStream;", "read", "([BII)I", _bais_readb)
    R("Ljava/io/ByteArrayInputStream;", "available", "()I",
      lambda a: max(0, len(a[0].fields.get("buf", b"")) - int(a[0].fields.get("pos", 0))))
    R("Ljava/io/ByteArrayInputStream;", "skip", "(J)J",
      lambda a: (lambda o, n: (o.fields.__setitem__("pos", min(len(o.fields.get("buf", b"")),
                 int(o.fields.get("pos", 0)) + max(0, int(n)))) or max(0, int(n))))(a[0], a[1] if len(a) > 1 else 0))
    R("Ljava/io/ByteArrayInputStream;", "mark", "(I)V",
      lambda a: a[0].fields.__setitem__("mark", int(a[0].fields.get("pos", 0))) if a and isinstance(a[0], JObject) else None)
    R("Ljava/io/ByteArrayInputStream;", "reset", "()V",
      lambda a: a[0].fields.__setitem__("pos", int(a[0].fields.get("mark", 0))) if a and isinstance(a[0], JObject) else None)
    R("Ljava/io/ByteArrayInputStream;", "markSupported", "()Z", lambda a: 1)
    R("Ljava/io/ByteArrayInputStream;", "close", "()V", lambda a: None)

    def _baos_init(a):
        a[0].fields["buf"] = bytearray(); return None
    R("Ljava/io/ByteArrayOutputStream;", "<init>", "()V", _baos_init)
    R("Ljava/io/ByteArrayOutputStream;", "<init>", "(I)V", _baos_init)
    R("Ljava/io/ByteArrayOutputStream;", "write", "(I)V",
      lambda a: a[0].fields.setdefault("buf", bytearray()).append(int(a[1]) & 0xFF))

    def _baos_writeb(a):
        o = a[0]; arr = a[1] if len(a) > 1 else None
        if isinstance(arr, JArray):
            off = int(a[2]) if len(a) > 3 else 0
            cnt = int(a[3]) if len(a) > 3 else len(arr.data)
            o.fields.setdefault("buf", bytearray()).extend(
                bytes([int(x) & 0xFF for x in arr.data[off:off + cnt]]))
        return None
    R("Ljava/io/ByteArrayOutputStream;", "write", "([B)V", _baos_writeb)
    R("Ljava/io/ByteArrayOutputStream;", "write", "([BII)V", _baos_writeb)
    R("Ljava/io/ByteArrayOutputStream;", "toByteArray", "()[B",
      lambda a: JArray("[B", [b for b in a[0].fields.get("buf", b"")]))
    R("Ljava/io/ByteArrayOutputStream;", "size", "()I", lambda a: len(a[0].fields.get("buf", b"")))
    R("Ljava/io/ByteArrayOutputStream;", "reset", "()V",
      lambda a: a[0].fields.__setitem__("buf", bytearray()))
    R("Ljava/io/ByteArrayOutputStream;", "toString", "()Ljava/lang/String;",
      lambda a: JString(bytes(a[0].fields.get("buf", b"")).decode("utf-8", "replace")))
    R("Ljava/io/ByteArrayOutputStream;", "flush", "()V", lambda a: None)
    R("Ljava/io/ByteArrayOutputStream;", "close", "()V", lambda a: None)
    R("Ljava/io/ByteArrayOutputStream;", "writeTo", "(Ljava/io/OutputStream;)V",
      lambda a: _dispatch(stubs, a[1], "write", "([B)V",
                          [JArray("[B", [b for b in a[0].fields.get("buf", b"")])]) if len(a) > 1 else None)


# ---- Context-App-Speicher (getDir/openFileOutput/openFileInput) + File.renameTo ----
def _reg_context_storage(stubs, pkg, data_dir):
    fs = stubs._shared_fs
    R = stubs.register
    bn = stubs.register_by_name

    def _getdir(a):
        nm = _txt(a[1]) if len(a) > 1 else "d"
        path = _norm("%s/app_%s" % (data_dir, nm or "d"))
        try: fs.add_dir(path)
        except Exception: pass
        return JObject("Ljava/io/File;", {"path": path})
    bn("getDir", "(Ljava/lang/String;I)Ljava/io/File;", _getdir)

    def _openout(a):
        nm = _txt(a[1]) if len(a) > 1 else "f"
        mode = int(a[2]) if len(a) > 2 and isinstance(a[2], int) else 0
        path = _norm("%s/files/%s" % (data_dir, nm or "f"))
        try: fs.add_dir(data_dir + "/files")
        except Exception: pass
        buf = b""
        if mode & 0x8000 or mode == 32768:        # MODE_APPEND
            ino = fs.lookup(path)
            if ino is not None: buf = bytes(fs.read(ino))
        try: fs.add_file(path, bytes(buf))
        except Exception: pass
        return JObject("Ljava/io/FileOutputStream;", {"path": path, "buf": bytearray(buf)})
    bn("openFileOutput", "(Ljava/lang/String;I)Ljava/io/FileOutputStream;", _openout)

    def _openin(a):
        nm = _txt(a[1]) if len(a) > 1 else "f"
        path = _norm("%s/files/%s" % (data_dir, nm or "f"))
        return JObject("Ljava/io/FileInputStream;", {"path": path, "pos": 0})
    bn("openFileInput", "(Ljava/lang/String;)Ljava/io/FileInputStream;", _openin)

    bn("getFileStreamPath", "(Ljava/lang/String;)Ljava/io/File;",
       lambda a: JObject("Ljava/io/File;", {"path": _norm("%s/files/%s" % (data_dir, _txt(a[1]) if len(a) > 1 else "f"))}))
    bn("getDatabasePath", "(Ljava/lang/String;)Ljava/io/File;",
       lambda a: JObject("Ljava/io/File;", {"path": _norm("%s/databases/%s" % (data_dir, _txt(a[1]) if len(a) > 1 else "d"))}))
    bn("getCodeCacheDir", "()Ljava/io/File;",
       lambda a: JObject("Ljava/io/File;", {"path": _norm(data_dir + "/code_cache")}))
    bn("getNoBackupFilesDir", "()Ljava/io/File;",
       lambda a: JObject("Ljava/io/File;", {"path": _norm(data_dir + "/no_backup")}))

    def _renameto(a):
        src = _file_path(a[0]); dst = _file_path(a[1]) if len(a) > 1 else None
        if not src or not dst: return 0
        ino = fs.lookup(src)
        if ino is None: return 0
        try:
            fs.add_file(dst, bytes(fs.read(ino)))
            fs.unlink(src)
            return 1
        except Exception:
            return 0
    R("Ljava/io/File;", "renameTo", "(Ljava/io/File;)Z", _renameto)


# ---- java.util.zip.ZipFile (echtes Zip-Parsing ueber die gemounteten APK-Bytes) ----
def _reg_zip(stubs):
    R = stubs.register
    fs = stubs._shared_fs
    import zipfile

    def _zopen(a):
        src = a[1] if len(a) > 1 else None
        p = _file_path(src) if isinstance(src, JObject) and src.cls == "Ljava/io/File;" else _txt(src)
        p = _norm(p or "")
        ino = fs.lookup(p)
        z = None
        if ino is not None:
            try:
                z = zipfile.ZipFile(_io.BytesIO(bytes(fs.read(ino))))
            except Exception:
                z = None
        a[0].fields["path"] = p
        a[0].fields["_zip"] = z
        return None
    R("Ljava/util/zip/ZipFile;", "<init>", "(Ljava/io/File;)V", _zopen)
    R("Ljava/util/zip/ZipFile;", "<init>", "(Ljava/lang/String;)V", _zopen)

    def _getentry(a):
        o = a[0]; z = o.fields.get("_zip"); nm = _txt(a[1]) if len(a) > 1 else None
        if z is None or not nm:
            return None
        try:
            zi = z.getinfo(nm)
        except Exception:
            return None
        return JObject("Ljava/util/zip/ZipEntry;",
                       {"name": nm, "size": zi.file_size, "_zipref": o})
    R("Ljava/util/zip/ZipFile;", "getEntry",
      "(Ljava/lang/String;)Ljava/util/zip/ZipEntry;", _getentry)

    def _getstream(a):
        o = a[0]; ent = a[1] if len(a) > 1 else None
        z = o.fields.get("_zip"); nm = ent.fields.get("name") if isinstance(ent, JObject) else None
        data = b""
        if z is not None and nm:
            try:
                data = z.read(nm)
            except Exception:
                data = b""
        # als FileInputStream-artiges Objekt mit In-Memory-Daten
        s = JObject("Ljava/io/FileInputStream;", {"path": "", "pos": 0, "_mem": data})
        return s
    R("Ljava/util/zip/ZipFile;", "getInputStream",
      "(Ljava/util/zip/ZipEntry;)Ljava/io/InputStream;", _getstream)
    R("Ljava/util/zip/ZipFile;", "close", "()V", lambda a: None)
    R("Ljava/util/zip/ZipFile;", "size", "()I",
      lambda a: len(a[0].fields.get("_zip").namelist()) if a[0].fields.get("_zip") else 0)

    R("Ljava/util/zip/ZipEntry;", "getName", "()Ljava/lang/String;",
      lambda a: JString(a[0].fields.get("name", "")))
    R("Ljava/util/zip/ZipEntry;", "getSize", "()J", lambda a: int(a[0].fields.get("size", 0)))
    R("Ljava/util/zip/ZipEntry;", "isDirectory", "()Z",
      lambda a: 1 if str(a[0].fields.get("name", "")).endswith("/") else 0)

    # ZipInputStream (liest komprimierte Streams ueber beliebige FilterInputStreams, z. B. DEX-Decrypter)
    def _zis_parse_entries(raw: bytes):
        entries = []
        if not raw:
            return entries
        try:
            z = zipfile.ZipFile(_io.BytesIO(raw))
            for zi in z.infolist():
                try:
                    entries.append((zi.filename, z.read(zi.filename)))
                except Exception:
                    pass
            if entries:
                return entries
        except Exception:
            pass
        # Streaming Fallback: PK\x03\x04 Local File Headers (falls Central Directory gestrippt)
        import struct, zlib
        pos = 0
        n = len(raw)
        while pos + 30 <= n:
            if raw[pos:pos + 4] != b"PK\x03\x04":
                nxt = raw.find(b"PK\x03\x04", pos + 1)
                if nxt < 0:
                    break
                pos = nxt
                continue
            (_sig, _ver, _flg, method, _mt, _md, _crc, csz, usz, nlen, elen) = struct.unpack_from("<IHHHHHIIIHH", raw, pos)
            fn_start = pos + 30
            data_start = fn_start + nlen + elen
            nm = raw[fn_start:fn_start + nlen].decode("utf-8", errors="replace")
            if method == 0 and csz >= 0 and data_start + csz <= n:
                entries.append((nm, raw[data_start:data_start + csz]))
                pos = data_start + csz
            elif method == 8:
                try:
                    dc = zlib.decompressobj(-15)
                    out = dc.decompress(raw[data_start:])
                    out += dc.flush()
                    entries.append((nm, out))
                    consumed = len(raw[data_start:]) - len(dc.unused_data)
                    pos = data_start + max(1, consumed)
                except Exception:
                    break
            else:
                break
        return entries

    def _zis_drain_inner(o):
        if o.fields.get("_entries") is not None:
            return o.fields["_entries"]
        inner = o.fields.get("in")
        ac = _try_accelerate_cipher_stream(inner, stubs) if isinstance(inner, JObject) else None
        if ac is not None:
            data = ac["data"]
            pos = int(ac.get("pos", 0))
            raw = data[pos:]
            ac["pos"] = len(data)
            entries = _zis_parse_entries(raw)
            o.fields["_entries"] = entries
            return entries
        chunks = []
        if inner is not None:
            buf = JArray("[B", [0] * 4096)
            for _ in range(16384):
                k = _dispatch(stubs, inner, "read", "([BII)I", [buf, 0, 4096])
                if not isinstance(k, int) or k <= 0:
                    break
                chunks.append(bytes(int(x) & 0xFF for x in buf.data[:k]))
        raw = b"".join(chunks)
        entries = _zis_parse_entries(raw)
        o.fields["_entries"] = entries
        return entries

    def _zis_init(a):
        _ensure_fs_stream_hook(stubs)
        if a and isinstance(a[0], JObject):
            a[0].fields["in"] = a[1] if len(a) > 1 else None
            a[0].fields["_entries"] = None
            a[0].fields["_idx"] = -1
            a[0].fields["_cur_data"] = b""
            a[0].fields["_cur_pos"] = 0
        return None
    R("Ljava/util/zip/ZipInputStream;", "<init>", "(Ljava/io/InputStream;)V", _zis_init)

    def _zis_next(a):
        o = a[0] if a else None
        if not isinstance(o, JObject):
            return None
        entries = _zis_drain_inner(o)
        idx = int(o.fields.get("_idx", -1)) + 1
        o.fields["_idx"] = idx
        if 0 <= idx < len(entries):
            nm, data = entries[idx]
            o.fields["_cur_data"] = data
            o.fields["_cur_pos"] = 0
            return JObject("Ljava/util/zip/ZipEntry;", {"name": nm, "size": len(data)})
        o.fields["_cur_data"] = b""
        o.fields["_cur_pos"] = 0
        return None
    R("Ljava/util/zip/ZipInputStream;", "getNextEntry", "()Ljava/util/zip/ZipEntry;", _zis_next)

    def _zis_read1(a):
        o = a[0]
        d = o.fields.get("_cur_data", b""); p = int(o.fields.get("_cur_pos", 0))
        if p >= len(d):
            return -1
        o.fields["_cur_pos"] = p + 1
        return d[p]
    R("Ljava/util/zip/ZipInputStream;", "read", "()I", _zis_read1)

    def _zis_readb(a):
        o = a[0]; arr = a[1] if len(a) > 1 else None
        if not isinstance(arr, JArray):
            return -1
        d = o.fields.get("_cur_data", b""); p = int(o.fields.get("_cur_pos", 0))
        if p >= len(d):
            return -1
        off = int(a[2]) if len(a) > 3 else 0
        cnt = int(a[3]) if len(a) > 3 else len(arr.data)
        n = min(max(0, cnt), len(arr.data) - off, len(d) - p)
        if n <= 0:
            return -1
        chunk = d[p:p + n]
        for i, b in enumerate(chunk):
            arr.data[off + i] = b
        o.fields["_cur_pos"] = p + n
        return n
    R("Ljava/util/zip/ZipInputStream;", "read", "([B)I", _zis_readb)
    R("Ljava/util/zip/ZipInputStream;", "read", "([BII)I", _zis_readb)
    R("Ljava/util/zip/ZipInputStream;", "available", "()I",
      lambda a: 1 if int(a[0].fields.get("_cur_pos", 0)) < len(a[0].fields.get("_cur_data", b"")) else 0)
    R("Ljava/util/zip/ZipInputStream;", "closeEntry", "()V",
      lambda a: a[0].fields.__setitem__("_cur_pos", len(a[0].fields.get("_cur_data", b""))) if a and isinstance(a[0], JObject) else None)
    R("Ljava/util/zip/ZipInputStream;", "close", "()V",
      lambda a: _dispatch(stubs, a[0].fields.get("in"), "close", "()V", [])
      if a and isinstance(a[0], JObject) and a[0].fields.get("in") is not None else None)


# ---- DexClassLoader-Familie: VFS-.dex/.jar/.apk + ByteBuffer -> laufende DalvikVM ----
def _reg_dexloaders(stubs):
    fs = stubs._shared_fs
    R = stubs.register

    def _dex_from_bytes(data):
        """Rohbytes (.dex) ODER ZIP (.apk/.jar mit classesN.dex) -> Liste core-DexClassLoader."""
        from core.java.dex import DexFile
        from core.java.dex_loader import DexClassLoader as _CoreDCL
        out = []
        data = bytes(data or b"")
        if data[:8].startswith(b"dex\n") or data[:4] == b"dex\n":
            try: out.append(_CoreDCL(DexFile(data)))
            except Exception: pass
            return out
        try:
            import zipfile
            z = zipfile.ZipFile(_io.BytesIO(data))
            names = [n for n in z.namelist() if n.startswith("classes") and n.endswith(".dex")]
            for n in sorted(names):
                try: out.append(_CoreDCL(DexFile(z.read(n))))
                except Exception: pass
        except Exception:
            pass
        return out

    def _dex_from_path(path):
        p = _norm(path or "")
        ino = fs.lookup(p)
        if ino is None:
            return []
        return _dex_from_bytes(fs.read(ino))

    def _buf_bytes(bb):
        """ByteBuffer-JObject -> bytes (fields['buf'] oder umhuelltes [B)."""
        if isinstance(bb, JObject):
            b = bb.fields.get("buf")
            if b is not None:
                return bytes(b)
            arr = bb.fields.get("array")
            if isinstance(arr, JArray):
                return _bytes_of_array(arr)
        if isinstance(bb, JArray):
            return _bytes_of_array(bb)
        return b""

    def _register_dcls(o, dcls):
        if hasattr(stubs, "_make_dex_path_list"):
            o.fields.setdefault("pathList", stubs._make_dex_path_list())
        o.fields["_dcls"] = dcls
        if not dcls:
            return
        vm = getattr(stubs, "vm", None)
        dyn = list(getattr(stubs, "_dynamic_dcls", []))
        for dcl in dcls:
            if dcl not in dyn:
                dyn.append(dcl)
            try:
                idx = dcl._ensure_index()
                if vm is not None:
                    dcl._vm = vm
                    prov = getattr(vm, "class_provider", None)
                    for cname, cd in idx.items():
                        if cd.get("super"):
                            dcl._supers[cname] = cd["super"]
                            if prov is not None and isinstance(getattr(prov, "_super_cache", None), dict):
                                prov._super_cache.setdefault(cname, cd["super"])
                        if cd.get("interfaces"):
                            dcl._ifaces[cname] = list(cd["interfaces"])
                            if prov is not None and isinstance(getattr(prov, "_iface_cache", None), dict):
                                prov._iface_cache.setdefault(cname, list(cd["interfaces"]))
                    for cname in list(idx.keys()):
                        try:
                            dcl.register_class(vm, cname, run_clinit=False)
                        except Exception:
                            pass
            except Exception:
                pass
        stubs._dynamic_dcls = dyn

    # --- path-basierte Loader (DexClassLoader / PathClassLoader / BaseDexClassLoader) ---
    def _path_init(a):
        o = a[0]
        p = _txt(a[1]) if len(a) > 1 else ""
        dcls = []
        for part in str(p or "").split(":"):     # dexPath kann ':'-getrennt sein
            if part.strip():
                dcls += _dex_from_path(part.strip())
        _register_dcls(o, dcls)
        return None
    for cls in ("Ldalvik/system/DexClassLoader;", "Ldalvik/system/PathClassLoader;",
                "Ldalvik/system/BaseDexClassLoader;"):
        R(cls, "<init>", "(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/ClassLoader;)V", _path_init)
        R(cls, "<init>", "(Ljava/lang/String;Ljava/io/File;Ljava/lang/String;Ljava/lang/ClassLoader;)V", _path_init)
        R(cls, "<init>", "(Ljava/lang/String;Ljava/lang/ClassLoader;)V", _path_init)
        R(cls, "<init>", "(Ljava/lang/String;Ljava/lang/String;Ljava/lang/ClassLoader;)V", _path_init)

    # --- InMemoryDexClassLoader (ByteBuffer / ByteBuffer[]) ---
    def _mem_init(a):
        o = a[0]; src = a[1] if len(a) > 1 else None
        dcls = []
        sample = b""
        raw_total = 0
        if isinstance(src, JArray):
            for bb in src.data:
                raw_b = _buf_bytes(bb)
                raw_total += len(raw_b)
                if not sample and raw_b:
                    sample = raw_b[:16]
                dcls += _dex_from_bytes(raw_b)
        else:
            raw_b = _buf_bytes(src)
            raw_total = len(raw_b)
            sample = raw_b[:16]
            dcls += _dex_from_bytes(raw_b)
        if raw_total > 64 and not dcls:
            ws = getattr(stubs, "_unpack_warnings", None)
            if ws is None:
                ws = []
                stubs._unpack_warnings = ws
            ws.append({
                "kind": "invalid_dex_magic",
                "size": raw_total,
                "head_hex": bytes(sample).hex(),
                "recent_missing": list(getattr(stubs, "missing", ()) or ())[-8:],
            })
        _register_dcls(o, dcls)
        return None
    R("Ldalvik/system/InMemoryDexClassLoader;", "<init>",
      "(Ljava/nio/ByteBuffer;Ljava/lang/ClassLoader;)V", _mem_init)
    R("Ldalvik/system/InMemoryDexClassLoader;", "<init>",
      "([Ljava/nio/ByteBuffer;Ljava/lang/ClassLoader;)V", _mem_init)

    # --- loadClass / findClass: Klasse in die laufende VM registrieren + Class liefern ---
    def _loadclass(a):
        o = a[0]; nm = _txt(a[1]) if len(a) > 1 else None
        if not nm:
            return None
        dotted = nm
        desc = ("L" + nm.replace(".", "/") + ";") if not nm.startswith("L") else nm
        vm = getattr(stubs, "vm", None)
        dcls = o.fields.get("_dcls") if isinstance(o, JObject) else None
        for dcl in (dcls or []):
            try:
                if vm is not None and dcl.has_class(desc):
                    dcl.register_class(vm, desc)
                    return JObject("Ljava/lang/Class;", {"name": dotted})
            except Exception:
                continue
        if dcls:
            if vm is not None and getattr(vm, "class_provider", None) is not None:
                try:
                    if vm.class_provider.get_class(desc):
                        return JObject("Ljava/lang/Class;", {"name": dotted})
                except Exception:
                    pass
            try:
                from core.java.dex_loader import is_framework
                if is_framework(desc):
                    return JObject("Ljava/lang/Class;", {"name": dotted})
            except Exception:
                pass
            return None
        # Fallback fuer Parent-/Boot-ClassLoader ohne explizites _dcls: auch _dynamic_dcls pruefen
        for dcl in list(getattr(stubs, "_dynamic_dcls", ()) or ()):
            try:
                if vm is not None and dcl.has_class(desc):
                    dcl.register_class(vm, desc)
                    return JObject("Ljava/lang/Class;", {"name": dotted})
            except Exception:
                continue
        return JObject("Ljava/lang/Class;", {"name": dotted})
    bn = stubs.register_by_name
    bn("loadClass", "(Ljava/lang/String;)Ljava/lang/Class;", _loadclass)
    bn("loadClass", "(Ljava/lang/String;Z)Ljava/lang/Class;", _loadclass)
    bn("findClass", "(Ljava/lang/String;)Ljava/lang/Class;", _loadclass)

    # --- minimale ByteBuffer-Unterstuetzung (allocate/wrap/put/position/flip) fuer InMemory-Loader ---
    def _bb_new(buf, lim=None):
        ba = bytearray(buf)
        return JObject("Ljava/nio/ByteBuffer;", {
            "buf": ba, "pos": 0, "limit": len(ba) if lim is None else int(lim), "order": "big",
        })

    def _bb_put_bytes(a):
        o = a[0] if a else None
        if not isinstance(o, JObject):
            return o
        src = a[1] if len(a) > 1 else None
        if isinstance(src, JArray):
            off = int(a[2]) if len(a) > 3 else 0
            cnt = int(a[3]) if len(a) > 3 else len(src.data)
            chunk = bytes(int(x) & 0xFF for x in src.data[off:off + max(0, cnt)])
        elif isinstance(src, JObject):
            raw = _buf_bytes(src)
            chunk = raw[int(src.fields.get("pos", 0)):int(src.fields.get("limit", len(raw)))]
        elif isinstance(src, int):
            chunk = bytes([src & 0xFF])
        else:
            chunk = b""
        ba = o.fields.get("buf")
        if not isinstance(ba, bytearray):
            ba = bytearray(ba or b"")
            o.fields["buf"] = ba
        pos = int(o.fields.get("pos", 0))
        end = pos + len(chunk)
        if end > len(ba):
            ba.extend(b"\x00" * (end - len(ba)))
        ba[pos:end] = chunk
        o.fields["pos"] = end
        if end > int(o.fields.get("limit", len(ba))):
            o.fields["limit"] = end
        return o

    def _bb_pos(a):
        o = a[0] if a else None
        if not isinstance(o, JObject):
            return 0
        if len(a) > 1 and isinstance(a[1], int):
            o.fields["pos"] = max(0, int(a[1]))
            return o
        return int(o.fields.get("pos", 0))

    R("Ljava/nio/ByteBuffer;", "allocate", "(I)Ljava/nio/ByteBuffer;",
      lambda a: _bb_new(bytearray(max(0, int(a[0])) if a else 0)))
    R("Ljava/nio/ByteBuffer;", "allocateDirect", "(I)Ljava/nio/ByteBuffer;",
      lambda a: _bb_new(bytearray(max(0, int(a[0])) if a else 0)))
    R("Ljava/nio/ByteBuffer;", "wrap", "([B)Ljava/nio/ByteBuffer;",
      lambda a: _bb_new(_bytes_of_array(a[0] if a else None)))
    R("Ljava/nio/ByteBuffer;", "wrap", "([BII)Ljava/nio/ByteBuffer;",
      lambda a: _bb_new(_bytes_of_array(a[0] if a else None)[
          int(a[1]) if len(a) > 1 else 0: (int(a[1]) + int(a[2])) if len(a) > 2 else None]))
    R("Ljava/nio/ByteBuffer;", "put", "([B)Ljava/nio/ByteBuffer;", _bb_put_bytes)
    R("Ljava/nio/ByteBuffer;", "put", "([BII)Ljava/nio/ByteBuffer;", _bb_put_bytes)
    R("Ljava/nio/ByteBuffer;", "put", "(Ljava/nio/ByteBuffer;)Ljava/nio/ByteBuffer;", _bb_put_bytes)
    R("Ljava/nio/ByteBuffer;", "put", "(B)Ljava/nio/ByteBuffer;", _bb_put_bytes)
    R("Ljava/nio/ByteBuffer;", "position", "()I", _bb_pos)
    R("Ljava/nio/ByteBuffer;", "position", "(I)Ljava/nio/Buffer;", _bb_pos)
    R("Ljava/nio/ByteBuffer;", "position", "(I)Ljava/nio/ByteBuffer;", _bb_pos)
    R("Ljava/nio/Buffer;", "position", "()I", _bb_pos)
    R("Ljava/nio/Buffer;", "position", "(I)Ljava/nio/Buffer;", _bb_pos)
    R("Ljava/nio/ByteBuffer;", "limit", "()I",
      lambda a: int(a[0].fields.get("limit", len(a[0].fields.get("buf", b"")))) if a and isinstance(a[0], JObject) else 0)
    R("Ljava/nio/ByteBuffer;", "limit", "(I)Ljava/nio/Buffer;",
      lambda a: (a[0].fields.__setitem__("limit", int(a[1])) or a[0]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    R("Ljava/nio/ByteBuffer;", "capacity", "()I",
      lambda a: len(a[0].fields.get("buf", b"")) if a and isinstance(a[0], JObject) else 0)
    R("Ljava/nio/ByteBuffer;", "remaining", "()I",
      lambda a: max(0, int(a[0].fields.get("limit", len(a[0].fields.get("buf", b"")))) - int(a[0].fields.get("pos", 0)))
      if a and isinstance(a[0], JObject) else 0)
    R("Ljava/nio/ByteBuffer;", "flip", "()Ljava/nio/Buffer;",
      lambda a: (a[0].fields.update({"limit": int(a[0].fields.get("pos", 0)), "pos": 0}) or a[0])
      if a and isinstance(a[0], JObject) else None)
    R("Ljava/nio/ByteBuffer;", "flip", "()Ljava/nio/ByteBuffer;",
      lambda a: (a[0].fields.update({"limit": int(a[0].fields.get("pos", 0)), "pos": 0}) or a[0])
      if a and isinstance(a[0], JObject) else None)
    R("Ljava/nio/ByteBuffer;", "clear", "()Ljava/nio/Buffer;",
      lambda a: (a[0].fields.update({"limit": len(a[0].fields.get("buf", b"")), "pos": 0}) or a[0])
      if a and isinstance(a[0], JObject) else None)
    R("Ljava/nio/ByteBuffer;", "rewind", "()Ljava/nio/Buffer;",
      lambda a: (a[0].fields.__setitem__("pos", 0) or a[0]) if a and isinstance(a[0], JObject) else None)
    R("Ljava/nio/ByteBuffer;", "hasArray", "()Z", lambda a: 1)
    R("Ljava/nio/ByteBuffer;", "array", "()[B",
      lambda a: JArray("[B", list(a[0].fields.get("buf", b""))) if a and isinstance(a[0], JObject) else JArray("[B", []))
    R("Ljava/nio/ByteBuffer;", "order", "(Ljava/nio/ByteOrder;)Ljava/nio/ByteBuffer;",
      lambda a: (a[0].fields.__setitem__("order", a[1].fields.get("order", "big") if isinstance(a[1], JObject) else "big") or a[0])
      if len(a) > 1 and isinstance(a[0], JObject) else None)
    R("Ljava/nio/ByteBuffer;", "order", "()Ljava/nio/ByteOrder;",
      lambda a: JObject("Ljava/nio/ByteOrder;", {"order": a[0].fields.get("order", "big") if a and isinstance(a[0], JObject) else "big"}))

    def _sb_view(a, view_cls):
        o = a[0] if a else None
        if not isinstance(o, JObject):
            return JObject(view_cls, {"buf": b"", "pos": 0, "order": "big"})
        pos = int(o.fields.get("pos", 0))
        lim = int(o.fields.get("limit", len(o.fields.get("buf", b""))))
        raw = bytes(o.fields.get("buf", b""))[pos:lim]
        return JObject(view_cls, {"buf": raw, "pos": 0, "order": o.fields.get("order", "big")})

    def _view_get_arr(a, signed: bool):
        import struct as _st
        o = a[0] if a else None
        dst = a[1] if len(a) > 1 else None
        off = int(a[2]) if len(a) > 3 else 0
        cnt = int(a[3]) if len(a) > 3 else (len(dst.data) if isinstance(dst, JArray) else 0)
        if not isinstance(o, JObject) or not isinstance(dst, JArray):
            return o
        raw = o.fields.get("buf", b"")
        bpos = int(o.fields.get("pos", 0)) * 2
        fmt = ("<" if o.fields.get("order", "big") == "little" else ">") + ("h" if signed else "H")
        for i in range(max(0, cnt)):
            if bpos + 2 > len(raw) or off + i >= len(dst.data):
                break
            dst.data[off + i] = _st.unpack_from(fmt, raw, bpos)[0]
            bpos += 2
        o.fields["pos"] = bpos // 2
        return o

    R("Ljava/nio/ByteBuffer;", "asShortBuffer", "()Ljava/nio/ShortBuffer;", lambda a: _sb_view(a, "Ljava/nio/ShortBuffer;"))
    R("Ljava/nio/ShortBuffer;", "get", "([S)Ljava/nio/ShortBuffer;", lambda a: _view_get_arr(a, True))
    R("Ljava/nio/ShortBuffer;", "get", "([SII)Ljava/nio/ShortBuffer;", lambda a: _view_get_arr(a, True))
    R("Ljava/nio/ByteBuffer;", "asCharBuffer", "()Ljava/nio/CharBuffer;", lambda a: _sb_view(a, "Ljava/nio/CharBuffer;"))
    R("Ljava/nio/CharBuffer;", "get", "([C)Ljava/nio/CharBuffer;", lambda a: _view_get_arr(a, False))
    R("Ljava/nio/CharBuffer;", "get", "([CII)Ljava/nio/CharBuffer;", lambda a: _view_get_arr(a, False))


# ---- android.content.pm.ApplicationInfo (nativeLibraryDir etc.) ---------------
def _reg_appinfo(stubs):
    info = dict(getattr(stubs, "_app_info", {}) or {})

    def _mk_appinfo():
        return JObject("Landroid/content/pm/ApplicationInfo;", {
            "nativeLibraryDir": JString(info.get("nativeLibraryDir", "")),
            "dataDir": JString(info.get("dataDir", "")),
            "sourceDir": JString(info.get("sourceDir", "")),
            "publicSourceDir": JString(info.get("publicSourceDir", "")),
            "packageName": JString(info.get("packageName", "")),
            "flags": int(info.get("flags", 0)),
            "metaData": None,
        })
    stubs._mk_appinfo = _mk_appinfo
    bn = stubs.register_by_name
    bn("getApplicationInfo", "()Landroid/content/pm/ApplicationInfo;", lambda a: _mk_appinfo())


def _reg_os_fd(stubs):
    """POSIX / android.system.Os FD-Dup/Remove fuer /proc/self/(task/tid/)fd/<n> Selbst-Lader."""
    fs = stubs._shared_fs
    R = stubs.register

    def _os_dup(a):
        fd_obj = a[0] if a else None
        blobs = getattr(fs, "_fd_blobs", None)
        if blobs is None:
            blobs = fs._fd_blobs = {}
        orig_fd = fd_obj.fields.get("descriptor") if isinstance(fd_obj, JObject) else None
        path = fd_obj.fields.get("_path") if isinstance(fd_obj, JObject) else None
        data = blobs.get(orig_fd) if orig_fd is not None else None
        if data is None and path:
            ino = fs.lookup(path)
            if ino is not None:
                data = bytes(fs.read(ino))
            else:
                data = (getattr(fs, "_retained_files", None) or {}).get(path)
        nxt = getattr(fs, "_next_fd", 10)
        fs._next_fd = nxt + 1
        if data is not None:
            blobs[nxt] = bytes(data)
        fd_paths = getattr(fs, "_fd_paths", None)
        if fd_paths is None:
            fd_paths = fs._fd_paths = {}
        if path:
            fd_paths[nxt] = path
        return JObject("Ljava/io/FileDescriptor;", {"descriptor": nxt, "_path": path})

    def _os_remove(a):
        p = _txt(a[0]) if a else None
        if p:
            try:
                fs.unlink(_norm(p))
            except Exception:
                pass
        return None

    R("Landroid/system/Os;", "dup", "(Ljava/io/FileDescriptor;)Ljava/io/FileDescriptor;", _os_dup)
    R("Landroid/system/Os;", "remove", "(Ljava/lang/String;)V", _os_remove)
    R("Landroid/system/Os;", "gettid", "()I", lambda a: int(getattr(fs, "config", {}).get("tid", 1234)))
    R("Landroid/system/Os;", "getpid", "()I", lambda a: int(getattr(fs, "config", {}).get("pid", 1234)))
    R("Landroid/system/Os;", "getuid", "()I", lambda a: int(getattr(fs, "config", {}).get("uid", 10234)))
    R("Landroid/system/Os;", "getppid", "()I", lambda a: int(getattr(fs, "config", {}).get("ppid", 1)))
    R("Ljava/io/FileDescriptor;", "getInt$", "()I",
      lambda a: int(a[0].fields.get("descriptor", a[0].fields.get("fd", -1))) if a and isinstance(a[0], JObject) else -1)
    R("Ljava/io/FileDescriptor;", "valid", "()Z",
      lambda a: 1 if (a and isinstance(a[0], JObject) and int(a[0].fields.get("descriptor", a[0].fields.get("fd", -1))) >= 0) else 0)
