# -*- coding: utf-8 -*-
"""os_linux_arm64 — konkreter SyscallHandler fuer die arm64mini-Engine (N2 / WS-E3).

Implementiert den `iface.SyscallHandler`-Vertrag (`dispatch(num, cpu, mem, env) -> bool`)
fuer den **bounded** Satz an Linux-AArch64-Syscalls, den `libdb0c`-`JNI_OnLoad` plus eine
generische libc-Init absetzen. Antworten werden aus einem gemeinsamen Umgebungszustand
gespeist:

  * ``env``  (iface.Environment / FakeEnvironment) — ``/proc/self/maps``, Thread-Liste,
             deterministische Uhr.
  * ``state.vfs``   — virtuelles Dateisystem (Pfad -> Bytes), Backend von openat/read.
  * ``state.mmaps`` — mmap-Buchhaltung (Bump-Allocator in einer reservierten Arena).

**Scope-Grenze (N2):** mmap/mprotect fuehren nur **Buchhaltung** (keine W^X-Erzwingung —
das ist N3); keine Threads/Signale (N4); kein Trap-Crossing (N5). Unbekannte Syscalls geben
``False`` zurueck, damit die aufrufende Service-Schicht auf Seed-Policy/Default zurueckfaellt
(ein Pfad, abwaertskompatibel).

Determinismus (NFR-DETERM): ``getrandom`` nutzt einen reproduzierbaren SplitMix64-Strom,
Zeit kommt aus der fixierbaren ``env``-Uhr.
"""
from __future__ import annotations

import struct

from . import iface
from . import linux_abi as _abi

_M64 = (1 << 64) - 1
_PAGE = 0x1000
_BRK_ARENA = 0x71000000       # OS-3: Heap-Break-Arena (unter MMAP_ARENA)
_BRK_MAX = 0x01000000        # 16 MiB Heap-Spielraum

# ---- Linux-AArch64-Syscallnummern (generische ABI, Teilmenge) ----------------
NR_OPENAT = 56
NR_CLOSE = 57
NR_LSEEK = 62
NR_READ = 63
NR_WRITE = 64
NR_READLINKAT = 78
NR_NEWFSTATAT = 79
NR_FSTAT = 80
NR_CLOCK_GETTIME = 113
NR_UNAME = 160
NR_GETTIMEOFDAY = 169
NR_GETPID = 172
NR_GETUID = 174
NR_GETEUID = 175
NR_GETTID = 178
NR_MUNMAP = 215
NR_MMAP = 222
NR_MPROTECT = 226
NR_MADVISE = 233
NR_GETRANDOM = 278


def _errno(n: int) -> int:
    """Linux-Konvention: negativer errno im Rueckgaberegister (hier als u64)."""
    return (-int(n)) & _M64


def _prot_to_perm(prot: int) -> int:
    """Linux PROT_* (R=1,W=2,X=4) -> iface PERM_* (R=4,W=2,X=1)."""
    p = 0
    if prot & 0x1:
        p |= iface.PERM_R
    if prot & 0x2:
        p |= iface.PERM_W
    if prot & 0x4:
        p |= iface.PERM_X
    return p or iface.PERM_R


def _splitmix64(x: int) -> int:
    """Ein Schritt des SplitMix64-PRNG (deterministisch, reproduzierbar)."""
    x = (x + 0x9E3779B97F4A7C15) & _M64
    z = x
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _M64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _M64
    return (z ^ (z >> 31)) & _M64, x


class LinuxArm64SyscallHandler(iface.SyscallHandler):
    """Konkreter svc-Handler. Haelt eine Referenz auf den geteilten OS-Zustand
    (``state`` = ``eng._os``), aus dem Dateien, mmap-Regionen und Konfiguration kommen.
    ``env`` wird pro Aufruf durchgereicht (identisch zu ``state.env``)."""

    def __init__(self, state):
        self.state = state
        self._by_nr = {
            NR_OPENAT: self._openat,
            _abi.NR_MKDIRAT: self._mkdirat,
            _abi.NR_UNLINKAT: self._unlinkat,
            NR_CLOSE: self._close,
            NR_LSEEK: self._lseek,
            NR_READ: self._read,
            NR_WRITE: self._write,
            NR_READLINKAT: self._readlinkat,
            NR_NEWFSTATAT: self._newfstatat,
            NR_FSTAT: self._fstat,
            NR_CLOCK_GETTIME: self._clock_gettime,
            NR_UNAME: self._uname,
            NR_GETTIMEOFDAY: self._gettimeofday,
            NR_GETPID: self._getpid,
            NR_GETUID: self._getuid,
            NR_GETEUID: self._getuid,
            NR_GETTID: self._gettid,
            NR_MUNMAP: self._munmap,
            NR_MMAP: self._mmap,
            _abi.NR_BRK: self._brk,
            _abi.NR_MREMAP: self._mremap,
            _abi.NR_FUTEX: self._futex,
            _abi.NR_CLONE: self._clone,
            _abi.NR_CLONE3: self._clone3,
            _abi.NR_PRCTL: self._prctl,
            _abi.NR_SET_TID_ADDRESS: self._set_tid_address,
            _abi.NR_SET_ROBUST_LIST: self._set_robust_list,
            _abi.NR_TGKILL: self._tgkill,
            _abi.NR_TKILL: self._tkill,
            _abi.NR_EXIT: self._exit,
            _abi.NR_EXIT_GROUP: self._exit,
            _abi.NR_SCHED_GETAFFINITY: self._sched_getaffinity,
            _abi.NR_SCHED_YIELD: self._sched_yield,
            _abi.NR_RSEQ: self._rseq,
            _abi.NR_GETPPID: self._getppid,
            _abi.NR_GETGID: self._getgid,
            _abi.NR_GETEGID: self._getegid,
            _abi.NR_GETPGID: self._getpgid,
            _abi.NR_GETGROUPS: self._getgroups,
            _abi.NR_PRLIMIT64: self._prlimit64,
            _abi.NR_GETRUSAGE: self._getrusage,
            _abi.NR_SYSINFO: self._sysinfo,
            _abi.NR_TIMES: self._times,
            _abi.NR_GETCPU: self._getcpu,
            _abi.NR_UMASK: self._umask,
            _abi.NR_GETPRIORITY: self._getpriority,
            _abi.NR_RT_SIGACTION: self._rt_sigaction,
            _abi.NR_RT_SIGPROCMASK: self._rt_sigprocmask,
            _abi.NR_RT_SIGPENDING: self._rt_sigpending,
            _abi.NR_RT_SIGRETURN: self._rt_sigreturn,
            _abi.NR_RT_SIGTIMEDWAIT: self._rt_sigtimedwait,
            _abi.NR_SIGALTSTACK: self._sigaltstack,
            _abi.NR_KILL: self._kill,
            NR_MPROTECT: self._mprotect,
            NR_MADVISE: self._madvise,
            NR_GETRANDOM: self._getrandom,
            _abi.NR_NANOSLEEP: self._nanosleep,
            _abi.NR_CLOCK_NANOSLEEP: self._clock_nanosleep,
            _abi.NR_CLOCK_GETRES: self._clock_getres,
            _abi.NR_SETITIMER: self._setitimer,
            _abi.NR_SOCKET: self._socket,
            _abi.NR_CONNECT: self._connect,
            _abi.NR_SENDTO: self._sendto,
            _abi.NR_RECVFROM: self._recvfrom,
            _abi.NR_SENDMSG: self._sendmsg,
            _abi.NR_RECVMSG: self._recvmsg,
            _abi.NR_SETSOCKOPT: self._setsockopt,
            _abi.NR_GETSOCKOPT: self._getsockopt,
            _abi.NR_GETSOCKNAME: self._getsockname,
            _abi.NR_GETPEERNAME: self._getpeername,
            _abi.NR_BIND: self._bind,
            _abi.NR_LISTEN: self._listen,
            _abi.NR_ACCEPT: self._accept,
            _abi.NR_ACCEPT4: self._accept,
            _abi.NR_SHUTDOWN: self._shutdown,
            _abi.NR_SOCKETPAIR: self._socketpair,
            _abi.NR_GETDENTS64: self._getdents64,
            _abi.NR_FACCESSAT: self._faccessat,
            _abi.NR_FACCESSAT2: self._faccessat,
            _abi.NR_STATX: self._statx,
            _abi.NR_IOCTL: self._ioctl,
            _abi.NR_FCNTL: self._fcntl,
            _abi.NR_DUP: self._dup,
            _abi.NR_DUP3: self._dup,
            _abi.NR_GETCWD: self._getcwd,
        }

    # ---- Vertrag -----------------------------------------------------------
    def dispatch(self, num: int, cpu, mem, env) -> bool:
        n = int(num) & _M64
        fn = self._by_nr.get(n)
        nm = _abi.name(n)
        _pre = [int(cpu.x[i]) & _M64 for i in range(6)]   # OS-9: Args vor Ausfuehrung
        # OS-1: Per-Syscall-Coverage (benannt)
        try:
            cov = self.state.syscall_cov
            cov[nm] = cov.get(nm, 0) + 1
        except Exception:
            pass
        cfg = self.state.config
        # G-OS-0: Live-Hook pre (Beobachtung/Streaming; darf cpu.x editieren)
        self._svc_hook_call("pre", nm, _pre, cpu)
        # G-OS-0: Syscall-Override (args/ret/skip) = der "aenderbar"-Seam (headless/MCP/GUI)
        ov = (cfg.get("syscall_overrides") or {}).get(nm)
        forced = False
        if ov:
            for _k, _v in (ov.get("args") or {}).items():
                try: cpu.x[int(_k)] = int(_v) & _M64
                except Exception: pass
            if "ret" in ov or ov.get("skip"):
                cpu.x[0] = int(ov.get("ret", cpu.x[0])) & _M64
                forced = True
        # OS-9: Per-Syscall-Policy (allow/deny/stub) + Trace
        policy = cfg.get("syscall_policy")
        act = policy.get(nm) if policy else None
        if act == "deny" and not forced:
            cpu.x[0] = _abi.errno(_abi.EPERM)
            self._trace(nm, _pre, cpu, denied=True)
            self._svc_break(nm, _pre, cpu); self._svc_hook_call("post", nm, _pre, cpu)
            return True
        if act == "stub" and not forced:
            cpu.x[0] = 0
            self._trace(nm, _pre, cpu, stubbed=True)
            self._svc_break(nm, _pre, cpu); self._svc_hook_call("post", nm, _pre, cpu)
            return True
        if forced:
            self._trace(nm, _pre, cpu)
            self._svc_break(nm, _pre, cpu); self._svc_hook_call("post", nm, _pre, cpu)
            return True
        if fn is not None:
            fn(cpu, mem, env)
            self._trace(nm, _pre, cpu)
            self._svc_break(nm, _pre, cpu); self._svc_hook_call("post", nm, _pre, cpu)
            return True
        # OS-1: konfigurierbare Unknown-Syscall-Policy
        pol = self.state.config.get("unknown_syscall", "fallthrough")
        if pol == "enosys":
            cpu.x[0] = _abi.errno(_abi.ENOSYS)
            return True
        if pol == "zero":
            cpu.x[0] = 0
            return True
        return False   # fallthrough: Service-/Survival-Layer uebernimmt (abwaertskompatibel)

    # ---- Hilfen ------------------------------------------------------------
    def _trace(self, name, pre_args, cpu, denied=False, stubbed=False):
        """OS-9: beschraenkter Syscall-Trace-Ring (nur wenn aktiviert)."""
        cfg = self.state.config
        if not cfg.get("trace_syscalls"):
            return
        tr = self.state.syscall_trace
        if tr is None:
            return
        rec = {"n": name, "args": list(pre_args), "ret": int(cpu.x[0]) & _M64}
        if denied:  rec["denied"] = True
        if stubbed: rec["stubbed"] = True
        tr.append(rec)
        cap = int(cfg.get("trace_max", 4096))
        if len(tr) > cap:
            del tr[:len(tr) - cap]

    def _svc_hook_call(self, phase, name, pre_args, cpu):
        """G-OS-0: Live-Beobachtungs-Hook (pre/post). In-Process-Consumer (GUI-Modell)."""
        hooks = getattr(self.state, "syscall_hooks", None)
        if not hooks:
            return
        fn = hooks.get(phase)
        if fn is None:
            return
        try:
            fn({"n": name, "args": list(pre_args), "ret": int(cpu.x[0]) & _M64, "phase": phase}, cpu)
        except Exception:
            pass

    def _svc_break(self, name, pre_args, cpu):
        """G-OS-0: Syscall-Breakpoint -> Pause-Signal an den Run-Loop (cpu._svc_break)."""
        bps = self.state.config.get("syscall_breakpoints")
        if not bps or name not in bps:
            return
        try:
            cpu._svc_break = {"n": name, "args": list(pre_args), "ret": int(cpu.x[0]) & _M64}
        except Exception:
            pass

    @staticmethod
    def _cstr(cpu, addr: int, maxlen: int = 4096) -> str:
        addr &= _M64
        out = bytearray()
        while len(out) < maxlen:
            chunk = bytes(cpu.m.rb(addr + len(out), 32))
            if not chunk:
                break
            i = chunk.find(b"\x00")
            if i >= 0:
                out += chunk[:i]
                break
            out += chunk
        return out.decode("utf-8", "replace")

    @staticmethod
    def _wr_u64(cpu, addr: int, val: int) -> None:
        cpu.m.wb(addr & _M64, struct.pack("<Q", val & _M64))

    def _rand_bytes(self, n: int) -> bytes:
        out = bytearray()
        s = self.state.rng
        while len(out) < n:
            val, s = _splitmix64(s)
            out += struct.pack("<Q", val)
        self.state.rng = s
        return bytes(out[:n])

    # ---- Datei-/VFS-Syscalls ----------------------------------------------
    def _fs(self):
        """VFS 2.0 (lazy aus dem OS-State aufgebaut; abwaertskompatibel zum dict-VFS)."""
        st = self.state
        if getattr(st, "fs", None) is None:
            from .vfs import VFS
            st.fs = VFS.from_state(st)
        return st.fs

    def _openat(self, cpu, mem, env):
        path = self._cstr(cpu, cpu.x[1])
        flags = int(cpu.x[2]) & _M64               # openat(dirfd=x0, path=x1, flags=x2, mode=x3)
        st = self.state
        fs = self._fs()
        ino = fs.lookup(path)
        _O_CREAT = 0o100
        _O_TRUNC = 0o1000
        if ino is None:
            # AP2.1 (B.4): O_CREAT -> fehlende Datei leer anlegen (App entpackt Libs ins VFS),
            # statt ENOENT. Nach add_file erneut aufloesen (gleiches Inode -> _write-Writeback).
            if flags & _O_CREAT:
                fs.add_file(path, b"")
                ino = fs.lookup(path)
            if ino is None:
                cpu.x[0] = _abi.errno(_abi.ENOENT)
                return
        elif (flags & _O_TRUNC) and ino.kind == "file":
            ino.data = b""                         # existierende Datei bei O_TRUNC leeren
        is_dev = (ino.kind == "dev")
        data = b"" if is_dev else fs.read(ino)
        fd = st.next_fd
        st.next_fd += 1
        st.fds[fd] = {"path": path, "ino": ino, "data": data, "pos": 0,
                      "dir": (ino.kind == "dir"), "dev": is_dev}
        cpu.x[0] = fd & _M64

    def _read(self, cpu, mem, env):
        fd = int(cpu.x[0])
        buf = int(cpu.x[1]) & _M64
        cnt = int(cpu.x[2])
        f = self.state.fds.get(fd)
        if f is None:
            cpu.x[0] = _errno(9)  # EBADF
            return
        if f.get("sock") is not None:
            cpu.x[0] = self._sock_recv(cpu, fd, buf, cnt)
            return
        ino = f.get("ino")
        if f.get("dev") and ino is not None:
            chunk = self._fs().read(ino, cnt) if cnt > 0 else b""
            if chunk:
                cpu.m.wb(buf, bytes(chunk))
            cpu.x[0] = len(chunk) & _M64
            return
        data = f["data"]
        pos = f["pos"]
        chunk = data[pos:pos + cnt] if cnt > 0 else b""
        if chunk:
            cpu.m.wb(buf, bytes(chunk))
        f["pos"] = pos + len(chunk)
        cpu.x[0] = len(chunk) & _M64

    def _write(self, cpu, mem, env):
        fd = int(cpu.x[0]); buf = int(cpu.x[1]) & _M64; cnt = int(cpu.x[2])
        if fd in (1, 2):                      # stdout/stderr
            cpu.x[0] = cnt & _M64; return
        f = self.state.fds.get(fd)
        data = bytes(cpu.m.rb(buf, cnt)) if cnt > 0 else b""
        if f is None:
            cpu.x[0] = cnt & _M64; return     # unbekannter fd: quittieren
        if f.get("sock") is not None:
            cpu.x[0] = self._sock_send(cpu, fd, buf, cnt)
            return
        ino = f.get("ino")
        if ino is not None and ino.kind == "dev":
            if ino.write_fn is not None:
                try: ino.write_fn(self._fs(), data)
                except Exception: pass
            cpu.x[0] = cnt & _M64; return
        # Datei: an Position schreiben; fd + Inode aktualisieren (Unpack-Pattern)
        pos = int(f.get("pos", 0))
        cur = bytearray(f.get("data", b""))
        if pos > len(cur): cur += b"\x00" * (pos - len(cur))
        cur[pos:pos + len(data)] = data
        f["data"] = bytes(cur); f["pos"] = pos + len(data)
        if ino is not None: ino.data = f["data"]
        cpu.x[0] = cnt & _M64

    def _close(self, cpu, mem, env):
        self.state.fds.pop(int(cpu.x[0]), None)
        cpu.x[0] = 0

    def _mkdirat(self, cpu, mem, env):
        """mkdirat(dirfd, pathname, mode) -> Verzeichnis im VFS anlegen (AP2.1: Self-Unpacking)."""
        path = self._cstr(cpu, cpu.x[1])
        try:
            self._fs().add_dir(path)
            cpu.x[0] = 0
        except Exception:
            cpu.x[0] = _abi.errno(_abi.ENOENT)

    def _unlinkat(self, cpu, mem, env):
        """unlinkat(dirfd, pathname, flags) -> Datei/Verzeichnis aus dem VFS entfernen."""
        path = self._cstr(cpu, cpu.x[1])
        try:
            ok = self._fs().unlink(path)
            cpu.x[0] = 0 if ok else _abi.errno(_abi.ENOENT)
        except Exception:
            cpu.x[0] = _abi.errno(_abi.ENOENT)

    def _lseek(self, cpu, mem, env):
        fd = int(cpu.x[0])
        off = int(cpu.x[1])
        whence = int(cpu.x[2])
        f = self.state.fds.get(fd)
        if f is None:
            cpu.x[0] = _errno(9)
            return
        if whence == 1:      # SEEK_CUR
            f["pos"] = f["pos"] + off
        elif whence == 2:    # SEEK_END
            f["pos"] = len(f["data"]) + off
        else:                # SEEK_SET
            f["pos"] = off
        if f["pos"] < 0:
            f["pos"] = 0
        cpu.x[0] = f["pos"] & _M64

    def _readlinkat(self, cpu, mem, env):
        path = self._cstr(cpu, cpu.x[1])
        buf = int(cpu.x[2]) & _M64
        bufsiz = int(cpu.x[3])
        target = self.state.config.get("links", {}).get(path)
        if target is None:
            ino = self._fs().lookup(path, follow=False)
            if ino is not None and ino.kind == "symlink":
                target = ino.target
        if target is None:
            cpu.x[0] = _abi.errno(_abi.ENOENT)
            return
        tb = target.encode("utf-8", "replace")[:bufsiz]
        cpu.m.wb(buf, tb)
        cpu.x[0] = len(tb) & _M64

    def _newfstatat(self, cpu, mem, env):
        path = self._cstr(cpu, cpu.x[1])
        statbuf = int(cpu.x[2]) & _M64
        fs = self._fs()
        ino = fs.lookup(path)
        if ino is None:
            cpu.x[0] = _abi.errno(_abi.ENOENT); return
        cpu.m.wb(statbuf, fs.stat_bytes(ino, path))
        cpu.x[0] = 0

    def _fstat(self, cpu, mem, env):
        fd = int(cpu.x[0])
        statbuf = int(cpu.x[1]) & _M64
        f = self.state.fds.get(fd)
        fs = self._fs()
        ino = f.get("ino") if f else None
        cpu.m.wb(statbuf, fs.stat_bytes(ino, f.get("path", "") if f else ""))
        cpu.x[0] = 0

    def _getdents64(self, cpu, mem, env):
        fd = int(cpu.x[0]); buf = int(cpu.x[1]) & _M64; cnt = int(cpu.x[2])
        f = self.state.fds.get(fd)
        if f is None or not f.get("dir"):
            cpu.x[0] = _abi.errno(_abi.ENOTDIR); return
        if f.get("dents") is None:
            f["dents"] = self._fs().listdir(f["path"]); f["dirpos"] = 0
        entries = f["dents"]; i = int(f.get("dirpos", 0)); out = bytearray()
        kmap = {"dir": _abi.DT_DIR, "file": _abi.DT_REG, "symlink": _abi.DT_LNK,
                "dev": _abi.DT_CHR, "dyn": _abi.DT_REG}
        while i < len(entries):
            nm, kind = entries[i]
            name = nm.encode("utf-8", "replace") + b"\x00"
            reclen = (19 + len(name) + 7) & ~7
            if len(out) + reclen > cnt:
                break
            rec = bytearray(reclen)
            struct.pack_into("<Q", rec, 0, (abs(hash(nm)) & 0xffff) + 1)   # d_ino
            struct.pack_into("<q", rec, 8, i + 1)                          # d_off
            struct.pack_into("<H", rec, 16, reclen)                        # d_reclen
            rec[18] = kmap.get(kind, _abi.DT_REG) & 0xff                   # d_type
            rec[19:19 + len(name)] = name
            out += rec; i += 1
        f["dirpos"] = i
        if out: cpu.m.wb(buf, bytes(out))
        cpu.x[0] = len(out) & _M64

    def _faccessat(self, cpu, mem, env):
        path = self._cstr(cpu, cpu.x[1])
        cpu.x[0] = 0 if self._fs().exists(path) else _abi.errno(_abi.ENOENT)

    def _statx(self, cpu, mem, env):
        path = self._cstr(cpu, cpu.x[1]); statxbuf = int(cpu.x[4]) & _M64
        fs = self._fs(); ino = fs.lookup(path)
        if ino is None:
            cpu.x[0] = _abi.errno(_abi.ENOENT); return
        b = bytearray(256)
        struct.pack_into("<I", b, 0, 0x000007ff)     # stx_mask
        struct.pack_into("<I", b, 4, 0x1000)         # stx_blksize
        struct.pack_into("<I", b, 16, 1)             # stx_nlink
        struct.pack_into("<H", b, 28, ino.mode & 0xffff)   # stx_mode
        struct.pack_into("<Q", b, 40, fs.size(ino))  # stx_size
        cpu.m.wb(statxbuf, bytes(b)); cpu.x[0] = 0

    def _ioctl(self, cpu, mem, env):
        cpu.x[0] = 0            # generischer Erfolg (bekannte Spezialfaelle spaeter)

    def _fcntl(self, cpu, mem, env):
        cpu.x[0] = 0            # F_GETFL/F_SETFD/... -> 0

    def _dup(self, cpu, mem, env):
        fd = int(cpu.x[0]); st = self.state
        f = st.fds.get(fd)
        nfd = st.next_fd; st.next_fd += 1
        st.fds[nfd] = dict(f) if f else {"path": "", "data": b"", "pos": 0}
        cpu.x[0] = nfd & _M64

    def _getcwd(self, cpu, mem, env):
        buf = int(cpu.x[0]) & _M64; size = int(cpu.x[1])
        cwd = (str(self.state.config.get("cwd", "/")) + "\x00").encode()[:max(size, 1)]
        cpu.m.wb(buf, cwd); cpu.x[0] = buf & _M64

    def _uname(self, cpu, mem, env):
        buf = int(cpu.x[0]) & _M64
        cpu.m.wb(buf, b"\x00" * 390)  # 6 * 65
        fields = [b"Linux", b"localhost", b"4.14.0", b"#1 SMP", b"aarch64", b""]
        for i, f in enumerate(fields):
            if f:
                cpu.m.wb(buf + i * 65, f[:64])
        cpu.x[0] = 0

    # ---- Identitaet --------------------------------------------------------
    def _getpid(self, cpu, mem, env):
        cpu.x[0] = int(self.state.config.get("pid", 1234)) & _M64

    def _getuid(self, cpu, mem, env):
        cpu.x[0] = int(self.state.config.get("uid", 10234)) & _M64

    def _gettid(self, cpu, mem, env):
        cpu.x[0] = int(self.state.config.get("tid", self.state.config.get("pid", 1234))) & _M64

    # ---- OS-4: Threads / Futex / prctl / Signale-nahe -----------------------
    def _futex(self, cpu, mem, env):
        uaddr = int(cpu.x[0]) & _M64
        op_raw = int(cpu.x[1])
        val = int(cpu.x[2]) & 0xffffffff
        op = op_raw & 0x7f                   # PRIVATE_FLAG/CLOCK_REALTIME ausmaskieren
        st = self.state
        if getattr(st, "syscall_trace", None) is not None and st.config.get("trace_futex"):
            st.syscall_trace.append(("futex", hex(uaddr), op, val))
        if op in (0, 9):                     # FUTEX_WAIT / WAIT_BITSET
            cur = struct.unpack("<I", bytes(cpu.m.rb(uaddr, 4)))[0]
            spins = getattr(st, "_futex_spins", None)
            if spins is None:
                spins = {}
                st._futex_spins = spins
            skey = (int(getattr(cpu, "pc", 0) or 0) & _M64, uaddr, val)
            if cur != val:
                for k in [kk for kk in spins if kk[1] == uaddr]:
                    spins.pop(k, None)
                cpu.x[0] = _abi.errno(_abi.EAGAIN)   # Wert bereits geaendert
            else:
                cnt = spins.get(skey, 0) + 1
                spins[skey] = cnt
                if cnt >= 8:
                    spins.pop(skey, None)
                    cpu.stopped = True
                    cpu.stop_reason = f"futex_stall@0x{int(getattr(cpu, 'pc', 0) or 0) & _M64:x}"
                    cpu.x[0] = _abi.errno(_abi.ETIMEDOUT)
                else:
                    cpu.x[0] = 0             # einthreadig: sofort "geweckt"
            return
        if op in (1, 10):                    # FUTEX_WAKE / WAKE_BITSET
            cpu.x[0] = 0                      # keine Warter
            return
        if op == 5:                          # FUTEX_WAKE_OP: Op auf uaddr2 ausfuehren
            uaddr2 = int(cpu.x[4]) & _M64
            oparg = int(cpu.x[3])
            self._futex_wake_op(cpu, uaddr2, oparg)
            cpu.x[0] = 0
            return
        if op in (3, 4):                     # REQUEUE / CMP_REQUEUE
            cpu.x[0] = 0
            return
        cpu.x[0] = 0

    def _futex_wake_op(self, cpu, uaddr2, encoded):
        try:
            oldval = struct.unpack("<I", bytes(cpu.m.rb(uaddr2, 4)))[0]
        except Exception:
            return
        op = (encoded >> 28) & 0xf
        oparg = (encoded >> 12) & 0xfff
        if op == 0:      newv = oparg                 # SET
        elif op == 1:    newv = oldval + oparg        # ADD
        elif op == 2:    newv = oldval | oparg        # OR
        elif op == 3:    newv = oldval & ~oparg       # ANDN
        elif op == 4:    newv = oldval ^ oparg        # XOR
        else:            newv = oldval
        cpu.m.wb(uaddr2, struct.pack("<I", newv & 0xffffffff))

    def _clone(self, cpu, mem, env):
        st = self.state
        flags = int(cpu.x[0])
        ptid = int(cpu.x[2]) & _M64
        ctid = int(cpu.x[4]) & _M64
        base = int(st.config.get("pid", 1234))
        st.next_tid += 1
        child = base + st.next_tid
        st.threads[child] = {"flags": flags, "stack": int(cpu.x[1]) & _M64,
                             "tls": int(cpu.x[3]) & _M64}
        if (flags & 0x00100000) and ptid:    # CLONE_PARENT_SETTID
            try: cpu.m.wb(ptid, struct.pack("<i", child))
            except Exception: pass
        if (flags & 0x01000000) and ctid:    # CLONE_CHILD_SETTID
            try: cpu.m.wb(ctid, struct.pack("<i", child))
            except Exception: pass
        cpu.x[0] = child & _M64              # Elternpfad: Kind-TID

    def _clone3(self, cpu, mem, env):
        cpu.x[0] = _abi.errno(_abi.ENOSYS)   # glibc faellt auf clone() zurueck

    def _prctl(self, cpu, mem, env):
        st = self.state
        option = int(cpu.x[0])
        if option == 15:                      # PR_SET_NAME
            try:
                nm = self._cstr(cpu, cpu.x[1])
                st.config["thread_name"] = nm
            except Exception:
                pass
            cpu.x[0] = 0; return
        if option == 16:                      # PR_GET_NAME
            nm = str(st.config.get("thread_name", st.config.get("procname", "app")))
            try: cpu.m.wb(int(cpu.x[1]) & _M64, nm.encode()[:15] + b"\x00")
            except Exception: pass
            cpu.x[0] = 0; return
        if option == 3:                       # PR_GET_DUMPABLE
            cpu.x[0] = int(st.config.get("dumpable", 1)) & _M64; return
        if option == 4:                       # PR_SET_DUMPABLE (Anti-Debug) -> merken
            st.config["dumpable"] = int(cpu.x[1]); cpu.x[0] = 0; return
        cpu.x[0] = 0                          # PR_SET_VMA/PTRACER/... -> Erfolg

    def _set_tid_address(self, cpu, mem, env):
        self.state.config["clear_child_tid"] = int(cpu.x[0]) & _M64
        cpu.x[0] = int(self.state.config.get("tid", self.state.config.get("pid", 1234))) & _M64

    def _set_robust_list(self, cpu, mem, env):
        cpu.x[0] = 0

    def _tgkill(self, cpu, mem, env):
        sig = int(cpu.x[2])
        # sig==0: Existenzpruefung -> Erfolg; echte Signale nicht zustellen
        if getattr(self.state, "syscall_trace", None) is not None:
            self.state.syscall_trace.append(("tgkill", int(cpu.x[1]), sig))
        cpu.x[0] = 0

    def _tkill(self, cpu, mem, env):
        if getattr(self.state, "syscall_trace", None) is not None:
            self.state.syscall_trace.append(("tkill", int(cpu.x[0]), int(cpu.x[1])))
        cpu.x[0] = 0

    def _exit(self, cpu, mem, env):
        self.state.config["exit_code"] = int(cpu.x[0])
        self.state.config["exited"] = True
        setattr(cpu, "stopped", True)         # Harness kann anhalten
        cpu.x[0] = 0

    def _sched_getaffinity(self, cpu, mem, env):
        buf = int(cpu.x[2]) & _M64
        ncpu = int(self.state.config.get("ncpu", 4))
        mask = (1 << ncpu) - 1
        try: cpu.m.wb(buf, struct.pack("<Q", mask))
        except Exception: pass
        cpu.x[0] = 8                          # geschriebene Bytes

    def _sched_yield(self, cpu, mem, env):
        cpu.x[0] = 0

    def _rseq(self, cpu, mem, env):
        cpu.x[0] = _abi.errno(_abi.ENOSYS)    # Restartable-Sequences: Fallback

    # ---- OS-5: IDs / Limits / Sysinfo / auxv --------------------------------
    def _getppid(self, cpu, mem, env):
        cpu.x[0] = int(self.state.config.get("ppid", 1)) & _M64

    def _getgid(self, cpu, mem, env):
        cpu.x[0] = int(self.state.config.get("gid", self.state.config.get("uid", 10234))) & _M64

    def _getegid(self, cpu, mem, env):
        cpu.x[0] = int(self.state.config.get("gid", self.state.config.get("uid", 10234))) & _M64

    def _getpgid(self, cpu, mem, env):
        cpu.x[0] = int(self.state.config.get("pid", 1234)) & _M64

    def _getgroups(self, cpu, mem, env):
        size = int(cpu.x[0]); buf = int(cpu.x[1]) & _M64
        gids = [int(self.state.config.get("gid", self.state.config.get("uid", 10234)))]
        if size == 0:
            cpu.x[0] = len(gids) & _M64; return
        k = min(size, len(gids))
        for i in range(k):
            try: cpu.m.wb(buf + 4 * i, struct.pack("<I", gids[i] & 0xffffffff))
            except Exception: pass
        cpu.x[0] = k & _M64

    def _prlimit64(self, cpu, mem, env):
        resource = int(cpu.x[1]); old = int(cpu.x[3]) & _M64
        # RLIMIT_STACK=3, NOFILE=7, AS=9, DATA=2, CORE=4
        limits = {0: (0xffffffffffffffff, 0xffffffffffffffff),   # CPU
                  2: (0xffffffffffffffff, 0xffffffffffffffff),   # DATA
                  3: (0x800000, 0xffffffffffffffff),             # STACK 8 MiB
                  4: (0, 0xffffffffffffffff),                    # CORE
                  7: (1024, 4096),                               # NOFILE
                  9: (0xffffffffffffffff, 0xffffffffffffffff)}   # AS
        cur, mx = limits.get(resource, (0xffffffffffffffff, 0xffffffffffffffff))
        if old:
            try: cpu.m.wb(old, struct.pack("<QQ", cur & _M64, mx & _M64))
            except Exception: pass
        cpu.x[0] = 0

    def _getrusage(self, cpu, mem, env):
        usage = int(cpu.x[1]) & _M64
        buf = bytearray(144)
        struct.pack_into("<q", buf, 32, int(self.state.config.get("ru_maxrss", 4096)))  # ru_maxrss
        try: cpu.m.wb(usage, bytes(buf))
        except Exception: pass
        cpu.x[0] = 0

    def _sysinfo(self, cpu, mem, env):
        info = int(cpu.x[0]) & _M64
        cfg = self.state.config
        buf = bytearray(112)
        struct.pack_into("<q", buf, 0, int(cfg.get("uptime", 12345)))      # uptime
        struct.pack_into("<QQQ", buf, 8, 0, 0, 0)                          # loads[3]
        struct.pack_into("<Q", buf, 32, int(cfg.get("totalram", 4 * 1024**3)))
        struct.pack_into("<Q", buf, 40, int(cfg.get("freeram", 1 * 1024**3)))
        struct.pack_into("<H", buf, 88 if False else 80, 0)               # (padding-sicher)
        struct.pack_into("<H", buf, 96, int(cfg.get("procs", 120)))        # procs
        try: cpu.m.wb(info, bytes(buf))
        except Exception: pass
        cpu.x[0] = 0

    def _times(self, cpu, mem, env):
        buf = int(cpu.x[0]) & _M64
        ticks = int(self.state.config.get("times_ticks", 100))
        if buf:
            try: cpu.m.wb(buf, struct.pack("<qqqq", ticks, ticks, 0, 0))
            except Exception: pass
        cpu.x[0] = ticks & _M64

    def _getcpu(self, cpu, mem, env):
        cpup = int(cpu.x[0]) & _M64; nodep = int(cpu.x[1]) & _M64
        if cpup:
            try: cpu.m.wb(cpup, struct.pack("<I", 0))
            except Exception: pass
        if nodep:
            try: cpu.m.wb(nodep, struct.pack("<I", 0))
            except Exception: pass
        cpu.x[0] = 0

    def _umask(self, cpu, mem, env):
        old = int(self.state.config.get("umask", 0o22))
        self.state.config["umask"] = int(cpu.x[0]) & 0o777
        cpu.x[0] = old & _M64

    def _getpriority(self, cpu, mem, env):
        cpu.x[0] = 20                         # nice 0 -> Kernel gibt 20-nice zurueck

    # ---- auxv + Initial-Stack (ELF-Start) ----------------------------------
    @staticmethod
    def _rand16(seed):
        x = seed & _M64; out = bytearray()
        while len(out) < 16:
            x = (x + 0x9E3779B97F4A7C15) & _M64
            z = x
            z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _M64
            z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _M64
            z ^= z >> 31
            out += (z & _M64).to_bytes(8, "little")
        return bytes(out[:16])

    def build_auxv(self, rnd_addr=0, plat_addr=0):
        cfg = self.state.config
        uid = int(cfg.get("uid", 10234)); gid = int(cfg.get("gid", uid))
        pairs = [
            (_abi.AT_PAGESZ, _PAGE),
            (_abi.AT_HWCAP, int(cfg.get("hwcap", 0x000000ff))),
            (_abi.AT_HWCAP2, int(cfg.get("hwcap2", 0))),
            (_abi.AT_CLKTCK, int(cfg.get("clktck", 100))),
            (_abi.AT_UID, uid), (_abi.AT_EUID, uid),
            (_abi.AT_GID, gid), (_abi.AT_EGID, gid),
            (_abi.AT_SECURE, int(cfg.get("at_secure", 0))),
            (_abi.AT_PHDR, int(cfg.get("at_phdr", 0))),
            (_abi.AT_PHENT, int(cfg.get("at_phent", 56))),
            (_abi.AT_PHNUM, int(cfg.get("at_phnum", 0))),
            (_abi.AT_ENTRY, int(cfg.get("at_entry", 0))),
            (_abi.AT_BASE, int(cfg.get("at_base", 0))),
            (_abi.AT_FLAGS, 0),
        ]
        if rnd_addr:  pairs.insert(0, (_abi.AT_RANDOM, rnd_addr))
        if plat_addr: pairs.insert(0, (_abi.AT_PLATFORM, plat_addr))
        return pairs

    def setup_initial_stack(self, cpu, argv=None, envp=None):
        """Baut argc/argv/envp/auxv am Stack auf (SysV AArch64) und setzt cpu.sp."""
        st = self.state; cfg = st.config; m = cpu.m
        argv = list(argv) if argv is not None else [cfg.get("argv0", "/system/bin/app_process64")]
        if envp is not None:
            envl = list(envp)
        else:
            envl = ["%s=%s" % (k, v) for k, v in (cfg.get("environ", {}) or {}).items()]
        top = int(cfg.get("stack_top", 0) or (cpu.sp or 0) or 0x7ffffff000)
        p = top
        plat = b"aarch64\x00"; p -= len(plat); plat_addr = p; m.wb(p, plat)
        p -= 16; rnd_addr = p; m.wb(p, self._rand16(int(cfg.get("rand_seed", 0xDEADBEEF))))
        arg_addrs = []
        for sarg in argv:
            b = sarg.encode() + b"\x00"; p -= len(b); m.wb(p, b); arg_addrs.append(p)
        env_addrs = []
        for senv in envl:
            b = senv.encode() + b"\x00"; p -= len(b); m.wb(p, b); env_addrs.append(p)
        aux = self.build_auxv(rnd_addr, plat_addr) + [(_abi.AT_NULL, 0)]
        nwords = 1 + (len(argv) + 1) + (len(envl) + 1) + (len(aux) * 2)
        sp = (p - nwords * 8) & ~0xf
        w = sp
        m.wb(w, struct.pack("<Q", len(argv))); w += 8
        for a in arg_addrs: m.wb(w, struct.pack("<Q", a & _M64)); w += 8
        m.wb(w, b"\x00" * 8); w += 8
        for a in env_addrs: m.wb(w, struct.pack("<Q", a & _M64)); w += 8
        m.wb(w, b"\x00" * 8); w += 8
        for (t, v) in aux: m.wb(w, struct.pack("<QQ", t & _M64, v & _M64)); w += 16
        cpu.sp = sp & _M64
        st.auxv = aux
        cfg["auxv_pairs"] = aux               # fuer /proc/self/auxv
        return sp & _M64

    # ---- OS-6: Signale ------------------------------------------------------
    def _rt_sigaction(self, cpu, mem, env):
        signum = int(cpu.x[0]); act = int(cpu.x[1]) & _M64; oldact = int(cpu.x[2]) & _M64
        st = self.state
        prev = st.signals.get(signum)
        if oldact:
            raw = prev.get("raw") if prev else None
            try: cpu.m.wb(oldact, raw if raw else b"\x00" * 24)
            except Exception: pass
        if act:
            try:
                raw = bytes(cpu.m.rb(act, 24))
                handler = struct.unpack("<Q", raw[:8])[0]
                st.signals[signum] = {"raw": raw, "handler": handler,
                                      "flags": struct.unpack("<Q", raw[8:16])[0]}
            except Exception:
                pass
        cpu.x[0] = 0

    def _rt_sigprocmask(self, cpu, mem, env):
        how = int(cpu.x[0]); setp = int(cpu.x[1]) & _M64; oldp = int(cpu.x[2]) & _M64
        st = self.state
        if oldp:
            try: cpu.m.wb(oldp, struct.pack("<Q", st.sigmask & _M64))
            except Exception: pass
        if setp:
            try:
                newset = struct.unpack("<Q", bytes(cpu.m.rb(setp, 8)))[0]
                if how == 0:   st.sigmask |= newset            # SIG_BLOCK
                elif how == 1: st.sigmask &= ~newset           # SIG_UNBLOCK
                elif how == 2: st.sigmask = newset             # SIG_SETMASK
            except Exception:
                pass
        cpu.x[0] = 0

    def _rt_sigpending(self, cpu, mem, env):
        setp = int(cpu.x[0]) & _M64
        if setp:
            try: cpu.m.wb(setp, b"\x00" * 8)
            except Exception: pass
        cpu.x[0] = 0

    def _rt_sigreturn(self, cpu, mem, env):
        cpu.x[0] = 0                           # kein Frame zu restaurieren (einthreadig)

    def _rt_sigtimedwait(self, cpu, mem, env):
        cpu.x[0] = _abi.errno(_abi.EAGAIN)     # nichts anstehend

    def _sigaltstack(self, cpu, mem, env):
        ss = int(cpu.x[0]) & _M64; old = int(cpu.x[1]) & _M64
        st = self.state
        if old:
            raw = st.config.get("sigaltstack_raw")
            try: cpu.m.wb(old, raw if raw else b"\x00" * 24)
            except Exception: pass
        if ss:
            try: st.config["sigaltstack_raw"] = bytes(cpu.m.rb(ss, 24))
            except Exception: pass
        cpu.x[0] = 0

    def _kill(self, cpu, mem, env):
        if getattr(self.state, "syscall_trace", None) is not None:
            self.state.syscall_trace.append(("kill", int(cpu.x[0]), int(cpu.x[1])))
        cpu.x[0] = 0                           # nicht zustellen

    # ---- OS-8: Netz-Stack (Sockets + Record/Replay) -------------------------
    def _net(self):
        st = self.state
        if getattr(st, "net", None) is None:
            st.net = {"mode": str(st.config.get("net_mode", "record")),
                      "replay": dict(st.config.get("net_replay", {}) or {}),
                      "default": st.config.get("net_default"),
                      "log": []}
        return st.net

    @staticmethod
    def _parse_sockaddr(cpu, ptr, addrlen=0):
        """(family, host, port) aus sockaddr_in/in6; host als dotted/colon-String."""
        if not ptr:
            return (0, None, 0)
        try:
            fam = struct.unpack("<H", bytes(cpu.m.rb(ptr, 2)))[0]
        except Exception:
            return (0, None, 0)
        if fam == 2:            # AF_INET
            port = struct.unpack(">H", bytes(cpu.m.rb(ptr + 2, 2)))[0]
            ip = bytes(cpu.m.rb(ptr + 4, 4))
            host = ".".join(str(b) for b in ip)
            return (fam, host, port)
        if fam == 10:           # AF_INET6
            port = struct.unpack(">H", bytes(cpu.m.rb(ptr + 2, 2)))[0]
            ip = bytes(cpu.m.rb(ptr + 8, 16))
            host = ":".join("%02x%02x" % (ip[i], ip[i + 1]) for i in range(0, 16, 2))
            return (fam, host, port)
        if fam == 1:            # AF_UNIX
            path = bytes(cpu.m.rb(ptr + 2, 108)).split(b"\x00", 1)[0].decode("latin1")
            return (fam, path, 0)
        return (fam, None, 0)

    def _socket(self, cpu, mem, env):
        st = self.state
        domain = int(cpu.x[0]); typ = int(cpu.x[1]); proto = int(cpu.x[2])
        fd = st.next_fd; st.next_fd += 1
        sock = {"domain": domain, "type": typ, "proto": proto, "peer": None,
                "rx": bytearray(), "tx": bytearray(), "connected": False}
        st.fds[fd] = {"path": "<socket>", "sock": sock, "pos": 0, "dev": False}
        st.sockets[fd] = sock
        cpu.x[0] = fd & _M64

    def _socket_of(self, fd):
        f = self.state.fds.get(fd)
        return f.get("sock") if f else None

    def _sock_preload(self, sock):
        """Replay: Antwortbytes fuer den Peer in den rx-Puffer stellen."""
        net = self._net()
        if net["mode"] != "replay" or not sock.get("peer"):
            return
        host, port = sock["peer"]
        key = "%s:%d" % (host, port)
        resp = net["replay"].get(key)
        if resp is None:
            resp = net["replay"].get(host)
        if resp is None:
            resp = net.get("default")
        if resp:
            sock["rx"].extend(resp if isinstance(resp, (bytes, bytearray)) else str(resp).encode())

    def _connect(self, cpu, mem, env):
        fd = int(cpu.x[0]); addr = int(cpu.x[1]) & _M64; alen = int(cpu.x[2])
        sock = self._socket_of(fd)
        if sock is None:
            cpu.x[0] = _abi.errno(_abi.EBADF); return
        fam, host, port = self._parse_sockaddr(cpu, addr, alen)
        sock["peer"] = (host, port); sock["connected"] = True
        self._net()["log"].append(("connect", host, port))
        self._sock_preload(sock)
        cpu.x[0] = 0

    def _sock_send(self, cpu, fd, buf, length):
        sock = self._socket_of(fd)
        if sock is None:
            return _abi.errno(_abi.EBADF)
        data = bytes(cpu.m.rb(buf, length)) if length > 0 else b""
        sock["tx"].extend(data)
        net = self._net()
        net["log"].append(("send", fd, bytes(data)))
        # optionales Request->Response-Replay
        rr = net.get("req_replay")
        if rr:
            resp = rr.get(bytes(data)) or rr.get(data.decode("latin1", "ignore"))
            if resp:
                sock["rx"].extend(resp if isinstance(resp, (bytes, bytearray)) else str(resp).encode())
        return length & _M64

    def _sock_recv(self, cpu, fd, buf, length):
        sock = self._socket_of(fd)
        if sock is None:
            return _abi.errno(_abi.EBADF)
        n = min(length, len(sock["rx"]))
        if n > 0:
            chunk = bytes(sock["rx"][:n]); del sock["rx"][:n]
            cpu.m.wb(buf, chunk)
        return n & _M64

    def _sendto(self, cpu, mem, env):
        cpu.x[0] = self._sock_send(cpu, int(cpu.x[0]), int(cpu.x[1]) & _M64, int(cpu.x[2]))

    def _recvfrom(self, cpu, mem, env):
        cpu.x[0] = self._sock_recv(cpu, int(cpu.x[0]), int(cpu.x[1]) & _M64, int(cpu.x[2]))

    @staticmethod
    def _iov_list(cpu, iov, iovcnt):
        out = []
        for i in range(iovcnt):
            base = struct.unpack("<Q", bytes(cpu.m.rb(iov + i * 16, 8)))[0]
            ln = struct.unpack("<Q", bytes(cpu.m.rb(iov + i * 16 + 8, 8)))[0]
            out.append((base, ln))
        return out

    def _sendmsg(self, cpu, mem, env):
        fd = int(cpu.x[0]); msg = int(cpu.x[1]) & _M64
        iov = struct.unpack("<Q", bytes(cpu.m.rb(msg + 16, 8)))[0]
        iovlen = struct.unpack("<Q", bytes(cpu.m.rb(msg + 24, 8)))[0]
        total = 0
        sock = self._socket_of(fd)
        if sock is None:
            cpu.x[0] = _abi.errno(_abi.EBADF); return
        for base, ln in self._iov_list(cpu, iov, iovlen):
            if ln:
                sock["tx"].extend(bytes(cpu.m.rb(base, ln))); total += ln
        self._net()["log"].append(("sendmsg", fd, total))
        cpu.x[0] = total & _M64

    def _recvmsg(self, cpu, mem, env):
        fd = int(cpu.x[0]); msg = int(cpu.x[1]) & _M64
        iov = struct.unpack("<Q", bytes(cpu.m.rb(msg + 16, 8)))[0]
        iovlen = struct.unpack("<Q", bytes(cpu.m.rb(msg + 24, 8)))[0]
        sock = self._socket_of(fd)
        if sock is None:
            cpu.x[0] = _abi.errno(_abi.EBADF); return
        total = 0
        for base, ln in self._iov_list(cpu, iov, iovlen):
            n = min(ln, len(sock["rx"]))
            if n <= 0:
                break
            cpu.m.wb(base, bytes(sock["rx"][:n])); del sock["rx"][:n]; total += n
        cpu.x[0] = total & _M64

    def _setsockopt(self, cpu, mem, env):
        cpu.x[0] = 0

    def _getsockopt(self, cpu, mem, env):
        optval = int(cpu.x[3]) & _M64; optlen = int(cpu.x[4]) & _M64
        if optval:
            try: cpu.m.wb(optval, struct.pack("<I", 0))   # z.B. SO_ERROR=0
            except Exception: pass
        cpu.x[0] = 0

    def _getsockname(self, cpu, mem, env):
        self._write_sockaddr(cpu, int(cpu.x[1]) & _M64, int(cpu.x[2]) & _M64, "0.0.0.0", 0)
        cpu.x[0] = 0

    def _getpeername(self, cpu, mem, env):
        sock = self._socket_of(int(cpu.x[0]))
        host, port = (sock["peer"] if sock and sock.get("peer") else ("0.0.0.0", 0))
        self._write_sockaddr(cpu, int(cpu.x[1]) & _M64, int(cpu.x[2]) & _M64, host, port)
        cpu.x[0] = 0

    @staticmethod
    def _write_sockaddr(cpu, addr, addrlen_ptr, host, port):
        if not addr:
            return
        try:
            octets = [int(x) for x in str(host).split(".")[:4]]
            while len(octets) < 4: octets.append(0)
            sa = struct.pack("<H", 2) + struct.pack(">H", port & 0xffff) + bytes(octets) + b"\x00" * 8
            cpu.m.wb(addr, sa)
            if addrlen_ptr:
                cpu.m.wb(addrlen_ptr, struct.pack("<I", 16))
        except Exception:
            pass

    def _bind(self, cpu, mem, env):
        cpu.x[0] = 0

    def _listen(self, cpu, mem, env):
        cpu.x[0] = 0

    def _accept(self, cpu, mem, env):
        cpu.x[0] = _abi.errno(_abi.EAGAIN)    # keine eingehenden Verbindungen

    def _shutdown(self, cpu, mem, env):
        cpu.x[0] = 0

    def _socketpair(self, cpu, mem, env):
        st = self.state; sv = int(cpu.x[3]) & _M64
        fds = []
        for _ in range(2):
            fd = st.next_fd; st.next_fd += 1
            sock = {"domain": int(cpu.x[0]), "type": int(cpu.x[1]), "proto": 0,
                    "peer": None, "rx": bytearray(), "tx": bytearray(), "connected": True}
            st.fds[fd] = {"path": "<socketpair>", "sock": sock, "pos": 0, "dev": False}
            st.sockets[fd] = sock; fds.append(fd)
        if sv:
            try: cpu.m.wb(sv, struct.pack("<ii", fds[0], fds[1]))
            except Exception: pass
        cpu.x[0] = 0

    # ---- Zeit --------------------------------------------------------------
    def _clock_gettime(self, cpu, mem, env):
        ns = int(env.clock_ns()) if env is not None else 0
        ts = int(cpu.x[1]) & _M64
        self._wr_u64(cpu, ts, ns // 1_000_000_000)      # tv_sec
        self._wr_u64(cpu, ts + 8, ns % 1_000_000_000)   # tv_nsec
        cpu.x[0] = 0

    def _gettimeofday(self, cpu, mem, env):
        ns = int(env.clock_ns()) if env is not None else 0
        tv = int(cpu.x[0]) & _M64
        self._wr_u64(cpu, tv, ns // 1_000_000_000)              # tv_sec
        self._wr_u64(cpu, tv + 8, (ns // 1000) % 1_000_000)     # tv_usec
        cpu.x[0] = 0

    def _nanosleep(self, cpu, mem, env):
        req = int(cpu.x[0]) & _M64; rem = int(cpu.x[1]) & _M64
        self._sleep_advance(cpu, env, req)
        if rem:
            try: cpu.m.wb(rem, b"\x00" * 16)
            except Exception: pass
        cpu.x[0] = 0

    def _clock_nanosleep(self, cpu, mem, env):
        req = int(cpu.x[2]) & _M64; rem = int(cpu.x[3]) & _M64
        self._sleep_advance(cpu, env, req)
        if rem:
            try: cpu.m.wb(rem, b"\x00" * 16)
            except Exception: pass
        cpu.x[0] = 0

    def _sleep_advance(self, cpu, env, ts_ptr):
        """Virtuelle Uhr um die angeforderte Dauer vorstellen (kein echter Sleep)."""
        if not ts_ptr:
            return
        try:
            sec, nsec = struct.unpack("<QQ", bytes(cpu.m.rb(ts_ptr, 16)))
        except Exception:
            return
        total = sec * 1_000_000_000 + nsec
        adv = getattr(env, "advance_clock", None)
        if callable(adv):
            adv(total)

    def _clock_getres(self, cpu, mem, env):
        res = int(cpu.x[1]) & _M64
        if res:
            try: cpu.m.wb(res, struct.pack("<QQ", 0, 1))   # 1 ns Aufloesung
            except Exception: pass
        cpu.x[0] = 0

    def _setitimer(self, cpu, mem, env):
        old = int(cpu.x[2]) & _M64
        if old:
            try: cpu.m.wb(old, b"\x00" * 32)
            except Exception: pass
        cpu.x[0] = 0

    def _getrandom(self, cpu, mem, env):
        buf = int(cpu.x[0]) & _M64
        n = int(cpu.x[1])
        if n > 0:
            cpu.m.wb(buf, self._rand_bytes(n))
        cpu.x[0] = n & _M64

    # ---- Speicher (Buchhaltung; keine Erzwingung -> N3) --------------------
    def _mmap(self, cpu, mem, env):
        length = int(cpu.x[1]) or _PAGE
        size = (length + _PAGE - 1) & ~(_PAGE - 1)
        prot = int(cpu.x[2])
        flags = int(cpu.x[3])
        raw_fd = int(cpu.x[4]) & _M64
        fd = raw_fd - (1 << 64) if (raw_fd >> 63) else raw_fd   # signed
        off = int(cpu.x[5])
        addr = int(cpu.x[0]) & _M64
        st = self.state
        if (flags & 0x10) and addr:        # MAP_FIXED
            base = addr & ~(_PAGE - 1)
        else:
            base = st.mmap_next
            st.mmap_next += size
        try:
            mem.map(base, size, _prot_to_perm(prot))
        except Exception:
            pass
        rec = {"base": base, "size": size, "prot": prot, "flags": flags}
        # Datei-Backing (Unpack-Pattern): MAP_ANONYMOUS(0x20) NICHT gesetzt, gueltiger fd.
        if not (flags & 0x20) and fd >= 0:
            f = st.fds.get(fd)
            if f is not None:
                data = f.get("data", b"") or b""
                seg = data[off:off + length]
                if seg:
                    try: cpu.m.wb(base, bytes(seg))
                    except Exception: pass
                rec["fd"] = fd; rec["off"] = off; rec["path"] = f.get("path")
        st.mmaps.append(rec)
        cpu.x[0] = base & _M64

    def _brk(self, cpu, mem, env):
        st = self.state
        new = int(cpu.x[0]) & _M64
        if st.brk_base == 0:
            st.brk_base = _BRK_ARENA
            st.brk = _BRK_ARENA
        if new == 0 or new < st.brk_base or new > _BRK_ARENA + _BRK_MAX:
            cpu.x[0] = st.brk & _M64          # brk(0)/Fehlschlag -> aktueller Break
            return
        if new > st.brk:
            base = st.brk & ~(_PAGE - 1)
            end = (new + _PAGE - 1) & ~(_PAGE - 1)
            try: mem.map(base, end - base, _prot_to_perm(0x3))   # PROT_READ|WRITE
            except Exception: pass
        st.brk = new
        cpu.x[0] = new & _M64

    def _mremap(self, cpu, mem, env):
        old = int(cpu.x[0]) & _M64
        old_sz = int(cpu.x[1])
        new_sz = int(cpu.x[2]) or _PAGE
        st = self.state
        size = (new_sz + _PAGE - 1) & ~(_PAGE - 1)
        base = st.mmap_next
        st.mmap_next += size
        try: mem.map(base, size, _prot_to_perm(0x3))
        except Exception: pass
        ncopy = min(old_sz, new_sz)
        if ncopy > 0:
            try: cpu.m.wb(base, bytes(cpu.m.rb(old, ncopy)))
            except Exception: pass
        # alten Eintrag ersetzen, falls bekannt
        st.mmaps = [m for m in st.mmaps if m.get("base") != old]
        st.mmaps.append({"base": base, "size": size, "prot": 0x3, "from": old})
        cpu.x[0] = base & _M64

    def _munmap(self, cpu, mem, env):
        addr = int(cpu.x[0]) & _M64
        st = self.state
        st.mmaps = [m for m in st.mmaps if m["base"] != addr]
        cpu.x[0] = 0

    def _mprotect(self, cpu, mem, env):
        addr = int(cpu.x[0]) & _M64
        length = int(cpu.x[1])
        prot = int(cpu.x[2])
        for m in self.state.mmaps:
            if m["base"] == addr:
                m["prot"] = prot
        try:
            mem.protect(addr, length, _prot_to_perm(prot))
        except Exception:
            pass
        if prot & 0x4:                       # PROT_EXEC -> evtl. entpackten Code neu uebersetzen
            inv = getattr(cpu, "invalidate_code", None)
            if callable(inv):
                try:
                    inv(addr, max(length, _PAGE))
                except Exception:
                    pass
        cpu.x[0] = 0

    def _madvise(self, cpu, mem, env):
        cpu.x[0] = 0
