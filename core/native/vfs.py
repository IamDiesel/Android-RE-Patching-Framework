# -*- coding: utf-8 -*-
"""core.native.vfs — virtuelles Dateisystem 2.0 fuer das OS-Substrat (OS-2).

Pfad-indizierte Inodes (Datei/Verzeichnis/Symlink/Device/dyn. Datei) mit struct-stat-Erzeugung,
Verzeichnis-Listing (getdents) und dynamischen Providern fuer `/proc`, `/sys`, `/dev`. Generisch
und RASP-zentral (SELinux-Sonde, maps, cmdline). Werte kommen aus `env`/`config`/DeviceProfile.

Abwaertskompatibel: eine Legacy-`dict`-VFS (Pfad->bytes) wird als Datei-Fallback mitgefuehrt.
"""
from __future__ import annotations

import struct
from . import linux_abi as A

FILE, DIR, SYMLINK, DEV, DYN = "file", "dir", "symlink", "dev", "dyn"


class Inode:
    __slots__ = ("kind", "data", "target", "mode", "read_fn", "write_fn", "rdev")

    def __init__(self, kind, data=b"", target=None, mode=None, read_fn=None, write_fn=None, rdev=0):
        self.kind = kind
        self.data = data if isinstance(data, (bytes, bytearray)) else bytes(data or b"")
        self.target = target
        self.read_fn = read_fn       # DYN/DEV: callable(vfs) -> bytes
        self.write_fn = write_fn     # DEV: callable(vfs, bytes) -> None
        self.rdev = rdev
        if mode is None:
            mode = {DIR: A.S_IFDIR | 0o755, SYMLINK: A.S_IFLNK | 0o777,
                    DEV: A.S_IFCHR | 0o666}.get(kind, A.S_IFREG | 0o644)
        self.mode = mode


class VFS:
    def __init__(self, env=None, config=None, legacy=None):
        self.env = env
        self.config = config if config is not None else {}
        self.legacy = legacy if legacy is not None else {}   # dict Pfad->bytes (Fallback)
        self.nodes = {}                                      # Pfad -> Inode
        self._unlinked = set()
        self.add_dir("/")
        self._install_proc_sys_dev()

    # ---- Aufbau -----------------------------------------------------------
    @staticmethod
    def _norm(path):
        if not path:
            return "/"
        p = path.replace("\\", "/")
        while "//" in p:
            p = p.replace("//", "/")
        if len(p) > 1 and p.endswith("/"):
            p = p[:-1]
        return p

    def add_dir(self, path):
        path = self._norm(path)
        self._unlinked.discard(path)
        parts = [x for x in path.split("/") if x]
        cur = "/"
        self.nodes.setdefault("/", Inode(DIR))
        for seg in parts:
            cur = self._norm(cur + "/" + seg)
            self._unlinked.discard(cur)
            self.nodes.setdefault(cur, Inode(DIR))
        return self.nodes[path]

    def add_file(self, path, data=b""):
        path = self._norm(path)
        self._unlinked.discard(path)
        self.add_dir(path.rsplit("/", 1)[0] or "/")
        self.nodes[path] = Inode(FILE, data=data)

    def unlink(self, path):
        """Datei/Verzeichnis aus dem VFS entfernen (unlinkat). True, wenn etwas entfernt wurde."""
        path = self._norm(path)
        removed = False
        if path in self.nodes:
            del self.nodes[path]; removed = True
        if path in self.legacy:
            del self.legacy[path]; removed = True
        self._unlinked.add(path)
        return removed

    def add_symlink(self, path, target):
        path = self._norm(path)
        self._unlinked.discard(path)
        self.add_dir(path.rsplit("/", 1)[0] or "/")
        self.nodes[path] = Inode(SYMLINK, target=target)

    def add_dev(self, path, read_fn=None, write_fn=None):
        path = self._norm(path)
        self._unlinked.discard(path)
        self.add_dir(path.rsplit("/", 1)[0] or "/")
        self.nodes[path] = Inode(DEV, read_fn=read_fn, write_fn=write_fn)

    def add_dyn(self, path, read_fn):
        path = self._norm(path)
        self._unlinked.discard(path)
        self.add_dir(path.rsplit("/", 1)[0] or "/")
        self.nodes[path] = Inode(DYN, read_fn=read_fn)

    # ---- Aufloesung -------------------------------------------------------
    def lookup(self, path, follow=True, _depth=0):
        path = self._norm(path)
        # VFS 2.0 (zur Laufzeit entpackte/ueberschriebene Inodes) gewinnt vor Legacy-Mounts.
        ino = self.nodes.get(path)
        if ino is None and path in self.legacy and path not in self._unlinked:
            ino = Inode(FILE, data=self.legacy[path])
        if ino is not None and follow and ino.kind == SYMLINK and _depth < 16:
            tgt = ino.target
            if not tgt.startswith("/"):
                tgt = self._norm(path.rsplit("/", 1)[0] + "/" + tgt)
            return self.lookup(tgt, follow=True, _depth=_depth + 1)
        return ino

    def exists(self, path):
        return self.lookup(path, follow=False) is not None or self._norm(path) in self.legacy

    def read(self, ino, n=None):
        if ino is None:
            return b""
        if ino.kind in (DYN, DEV) and ino.read_fn is not None:
            try:
                return bytes(ino.read_fn(self, 0 if n is None else int(n)) or b"")
            except TypeError:
                try:
                    return bytes(ino.read_fn(self) or b"")   # Altsignatur (v)
                except Exception:
                    return b""
            except Exception:
                return b""
        return bytes(ino.data)

    _RAND_M = (1 << 64) - 1

    def _detrand(self, n):
        """Deterministischer Byte-Strom (splitmix64) fuer /dev/urandom|random."""
        if n <= 0:
            return b""
        seed = int(self.config.get("rand_seed", 0x9E3779B97F4A7C15)) & self._RAND_M
        ctr = int(getattr(self, "_rand_ctr", 0))
        x = (seed ^ (ctr * 0x2545F4914F6CDD1D)) & self._RAND_M
        out = bytearray()
        while len(out) < n:
            x = (x + 0x9E3779B97F4A7C15) & self._RAND_M
            z = x
            z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & self._RAND_M
            z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & self._RAND_M
            z ^= z >> 31
            out += (z & self._RAND_M).to_bytes(8, "little")
        self._rand_ctr = ctr + ((n + 7) // 8)
        return bytes(out[:n])

    def listdir(self, path):
        """(name, kind)-Liste direkter Kinder von `path`."""
        path = self._norm(path)
        base = path if path.endswith("/") else path + "/"
        out = {}
        for p, ino in self.nodes.items():
            if p == path or not p.startswith(base):
                continue
            rest = p[len(base):]
            name = rest.split("/", 1)[0]
            if name:
                child = self.nodes.get(self._norm(base + name))
                out[name] = child.kind if child else DIR
        for p in self.legacy:
            p = self._norm(p)
            if p.startswith(base):
                name = p[len(base):].split("/", 1)[0]
                if name and name not in out:
                    out[name] = FILE
        return sorted(out.items())

    def size(self, ino):
        return len(self.read(ino)) if ino else 0

    # ---- stat (Linux struct stat, AArch64) --------------------------------
    def stat_bytes(self, ino, path=""):
        """128-Byte struct stat (AArch64). Nur die von libc/RASP gelesenen Felder realistisch."""
        if ino is None:
            return b"\x00" * 128
        mode = ino.mode
        size = self.size(ino)
        ino_nr = (abs(hash(path)) & 0xffffff) or 1
        buf = bytearray(128)
        struct.pack_into("<Q", buf, 0, 0)            # st_dev
        struct.pack_into("<Q", buf, 8, ino_nr)       # st_ino
        struct.pack_into("<I", buf, 16, mode)        # st_mode
        struct.pack_into("<I", buf, 20, 1)           # st_nlink
        struct.pack_into("<I", buf, 24, int(self.config.get("uid", 10234)))  # st_uid
        struct.pack_into("<I", buf, 28, int(self.config.get("uid", 10234)))  # st_gid
        struct.pack_into("<Q", buf, 48, size)        # st_size
        struct.pack_into("<I", buf, 56, 0x1000)      # st_blksize
        struct.pack_into("<Q", buf, 64, (size + 511) // 512)  # st_blocks
        return bytes(buf)

    # ---- /proc, /sys, /dev --------------------------------------------------
    def _install_proc_sys_dev(self):
        cfg = self.config
        # /dev
        self.add_dev("/dev/null", read_fn=lambda v, n=0: b"", write_fn=lambda v, b: None)
        self.add_dev("/dev/zero", read_fn=lambda v, n=0: b"\x00" * max(int(n), 0))
        self.add_dev("/dev/urandom", read_fn=lambda v, n=0: v._detrand(int(n)))
        self.add_dev("/dev/random", read_fn=lambda v, n=0: v._detrand(int(n)))
        # /sys — RASP: SELinux-Enforce-Sonde (Buch §5)
        self.add_dyn("/sys/fs/selinux/enforce",
                     lambda v, n=0: (str(v.config.get("selinux_enforce", 1)) + "\n").encode())
        # /proc/self/*
        self.add_dyn("/proc/self/maps",
                     lambda v, n=0: (v.env.proc_self_maps().encode("utf-8", "replace")
                                if v.env is not None else b""))
        self.add_symlink("/proc/self/exe", "/data/app/base.apk!/lib/arm64-v8a/libmain.so")
        self.add_dyn("/proc/self/auxv", lambda v, n=0: self._pack_auxv(v))
        self.add_dyn("/proc/self/cmdline",
                     lambda v, n=0: (str(v.config.get("cmdline", v.config.get("package", "app"))) + "\x00").encode())
        self.add_dyn("/proc/self/environ",
                     lambda v, n=0: b"".join((k + "=" + val + "\x00").encode()
                                        for k, val in (v.config.get("environ", {}) or {}).items()))
        self.add_dyn("/proc/self/status", lambda v, n=0: self._proc_status(v))
        self.add_dyn("/proc/self/stat", lambda v, n=0: self._proc_stat(v))
        self.add_dyn("/proc/version",
                     lambda v, n=0: b"Linux version 4.14.0 (android) #1 SMP\n")
        self.add_dyn("/proc/cpuinfo", lambda v, n=0: b"processor\t: 0\nCPU architecture: 8\n")
        # /etc
        self.add_file("/etc/hosts", b"127.0.0.1 localhost\n")

    @staticmethod
    def _pack_auxv(v):
        import struct as _s
        pairs = v.config.get("auxv_pairs") or []
        out = bytearray()
        for t, val in pairs:
            out += _s.pack("<QQ", int(t) & ((1 << 64) - 1), int(val) & ((1 << 64) - 1))
        if not pairs:
            out += _s.pack("<QQ", 6, 0x1000) + _s.pack("<QQ", 0, 0)   # AT_PAGESZ, AT_NULL
        return bytes(out)

    @staticmethod
    def _proc_status(v):
        pid = int(v.config.get("pid", 1234))
        txt = ("Name:\t%s\nState:\tR (running)\nTgid:\t%d\nPid:\t%d\nPPid:\t%d\n"
               "TracerPid:\t0\nUid:\t%d %d %d %d\nThreads:\t1\n"
               % (v.config.get("procname", "app"), pid, pid, int(v.config.get("ppid", 1)),
                  int(v.config.get("uid", 10234)), int(v.config.get("uid", 10234)),
                  int(v.config.get("uid", 10234)), int(v.config.get("uid", 10234))))
        return txt.encode()

    @staticmethod
    def _proc_stat(v):
        pid = int(v.config.get("pid", 1234))
        return ("%d (%s) R %d %d\n" % (pid, v.config.get("procname", "app"),
                                       int(v.config.get("ppid", 1)), pid)).encode()

    # ---- Import einer Legacy-dict-VFS -------------------------------------
    @classmethod
    def from_state(cls, state):
        legacy = getattr(state, "vfs", {})
        vfs = cls(env=getattr(state, "env", None), config=getattr(state, "config", {}),
                  legacy=legacy)
        for k, v in dict(legacy or {}).items():
            try:
                vfs.add_file(str(k), v)
            except Exception:
                pass
        return vfs
