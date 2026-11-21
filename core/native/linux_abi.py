# -*- coding: utf-8 -*-
"""core.native.linux_abi — generische AArch64-Linux-ABI-Referenz (OS-Substrat, OS-1).

Benannte Syscall-Nummern (asm-generic/unistd), Errno-Konstanten, Flags (O_/PROT_/MAP_/AT_/
CLOCK_/FUTEX_/SIG). Zielagnostisch, nur Zahlen/Namen — keine Logik. Wird vom SyscallHandler,
VFS, Threads/Signalen etc. genutzt, damit Nummern/Errno an EINER Stelle stehen.
"""
from __future__ import annotations

_M64 = (1 << 64) - 1

# ---- Syscall-Nummern (generische AArch64-ABI) -------------------------------
NR = {
    17: "getcwd", 19: "eventfd2", 23: "dup", 24: "dup3", 25: "fcntl", 29: "ioctl",
    34: "mkdirat", 35: "unlinkat", 36: "symlinkat", 37: "linkat", 38: "renameat",
    46: "ftruncate", 48: "faccessat", 49: "chdir", 53: "fchmodat", 54: "fchownat",
    56: "openat", 57: "close", 59: "pipe2", 61: "getdents64", 62: "lseek",
    63: "read", 64: "write", 65: "readv", 66: "writev", 67: "pread64", 68: "pwrite64",
    71: "sendfile", 72: "pselect6", 73: "ppoll", 74: "signalfd4",
    78: "readlinkat", 79: "newfstatat", 80: "fstat", 82: "fsync", 83: "fdatasync",
    93: "exit", 94: "exit_group", 96: "set_tid_address", 98: "futex",
    99: "set_robust_list", 101: "nanosleep", 103: "setitimer", 113: "clock_gettime",
    114: "clock_getres", 115: "clock_nanosleep", 117: "ptrace",
    122: "sched_setaffinity", 123: "sched_getaffinity", 124: "sched_yield",
    125: "sched_get_priority_max", 126: "sched_get_priority_min",
    129: "kill", 130: "tkill", 131: "tgkill", 132: "sigaltstack",
    134: "rt_sigaction", 135: "rt_sigprocmask", 136: "rt_sigpending",
    137: "rt_sigtimedwait", 138: "rt_sigqueueinfo", 139: "rt_sigreturn",
    141: "getpriority", 142: "setpriority",
    153: "times", 154: "setpgid", 155: "getpgid", 157: "setsid", 158: "getgroups",
    166: "umask", 168: "getcpu",
    160: "uname", 165: "getrusage", 167: "prctl", 169: "gettimeofday",
    172: "getpid", 173: "getppid", 174: "getuid", 175: "geteuid",
    176: "getgid", 177: "getegid", 178: "gettid", 179: "sysinfo",
    198: "socket", 199: "socketpair", 200: "bind", 201: "listen", 202: "accept",
    203: "connect", 204: "getsockname", 205: "getpeername", 206: "sendto",
    207: "recvfrom", 208: "setsockopt", 209: "getsockopt", 210: "shutdown",
    211: "sendmsg", 212: "recvmsg", 213: "readahead",
    214: "brk", 215: "munmap", 216: "mremap", 220: "clone", 221: "execve",
    222: "mmap", 226: "mprotect", 227: "msync", 228: "mlock", 229: "munlock",
    233: "madvise", 242: "accept4", 260: "wait4", 261: "prlimit64",
    278: "getrandom", 279: "memfd_create", 281: "execveat", 291: "statx",
    293: "rseq", 435: "clone3", 436: "close_range", 439: "faccessat2",
}
NAME_TO_NR = {v: k for k, v in NR.items()}

# Named constants (die, die Handler direkt brauchen)
NR_GETCWD = 17; NR_EVENTFD2 = 19; NR_DUP = 23; NR_DUP3 = 24; NR_FCNTL = 25; NR_IOCTL = 29
NR_MKDIRAT = 34; NR_UNLINKAT = 35; NR_FTRUNCATE = 46; NR_FACCESSAT = 48
NR_OPENAT = 56; NR_CLOSE = 57; NR_PIPE2 = 59; NR_GETDENTS64 = 61; NR_LSEEK = 62
NR_READ = 63; NR_WRITE = 64; NR_READV = 65; NR_WRITEV = 66; NR_PREAD64 = 67; NR_PWRITE64 = 68
NR_PPOLL = 73; NR_READLINKAT = 78; NR_NEWFSTATAT = 79; NR_FSTAT = 80
NR_FSYNC = 82; NR_FDATASYNC = 83
NR_EXIT = 93; NR_EXIT_GROUP = 94; NR_SET_TID_ADDRESS = 96; NR_FUTEX = 98
NR_SET_ROBUST_LIST = 99; NR_NANOSLEEP = 101; NR_CLOCK_GETTIME = 113; NR_CLOCK_GETRES = 114
NR_CLOCK_NANOSLEEP = 115; NR_PTRACE = 117
NR_SCHED_SETAFFINITY = 122; NR_SCHED_GETAFFINITY = 123; NR_SCHED_YIELD = 124
NR_SCHED_GET_PRIORITY_MAX = 125; NR_SCHED_GET_PRIORITY_MIN = 126
NR_KILL = 129; NR_TKILL = 130; NR_TGKILL = 131; NR_SIGALTSTACK = 132
NR_RT_SIGACTION = 134; NR_RT_SIGPROCMASK = 135; NR_RT_SIGTIMEDWAIT = 137; NR_RT_SIGRETURN = 139
NR_TIMES = 153; NR_UNAME = 160; NR_GETRUSAGE = 165; NR_PRCTL = 167; NR_GETTIMEOFDAY = 169
NR_GETPID = 172; NR_GETPPID = 173; NR_GETUID = 174; NR_GETEUID = 175
NR_GETGID = 176; NR_GETEGID = 177; NR_GETTID = 178; NR_SYSINFO = 179
NR_GETPGID = 155; NR_GETGROUPS = 158; NR_UMASK = 166; NR_GETCPU = 168
NR_GETPRIORITY = 141; NR_SETPRIORITY = 142; NR_PRLIMIT64 = 261; NR_GETRUSAGE = 165
NR_RT_SIGACTION = 134; NR_RT_SIGPROCMASK = 135; NR_RT_SIGPENDING = 136
NR_RT_SIGTIMEDWAIT = 137; NR_RT_SIGRETURN = 139; NR_SIGALTSTACK = 132; NR_KILL = 129
NR_NANOSLEEP = 101; NR_SETITIMER = 103; NR_CLOCK_GETRES = 114; NR_CLOCK_NANOSLEEP = 115
NR_SOCKET = 198; NR_SOCKETPAIR = 199; NR_BIND = 200; NR_LISTEN = 201; NR_ACCEPT = 202
NR_CONNECT = 203; NR_GETSOCKNAME = 204; NR_GETPEERNAME = 205; NR_SENDTO = 206
NR_RECVFROM = 207; NR_SETSOCKOPT = 208; NR_GETSOCKOPT = 209; NR_SHUTDOWN = 210
NR_SENDMSG = 211; NR_RECVMSG = 212; NR_ACCEPT4 = 242
NR_TIMES = 153; NR_SYSINFO2 = 179
NR_SOCKET = 198; NR_BIND = 200; NR_LISTEN = 201; NR_ACCEPT = 202; NR_CONNECT = 203
NR_GETSOCKNAME = 204; NR_GETPEERNAME = 205; NR_SENDTO = 206; NR_RECVFROM = 207
NR_SETSOCKOPT = 208; NR_GETSOCKOPT = 209; NR_SHUTDOWN = 210; NR_SENDMSG = 211; NR_RECVMSG = 212
NR_BRK = 214; NR_MUNMAP = 215; NR_MREMAP = 216; NR_CLONE = 220; NR_EXECVE = 221
NR_MMAP = 222; NR_MPROTECT = 226; NR_MSYNC = 227; NR_MLOCK = 228; NR_MUNLOCK = 229
NR_MADVISE = 233; NR_WAIT4 = 260; NR_PRLIMIT64 = 261; NR_GETRANDOM = 278
NR_MEMFD_CREATE = 279; NR_STATX = 291; NR_RSEQ = 293; NR_CLONE3 = 435; NR_FACCESSAT2 = 439

# ---- Errno ------------------------------------------------------------------
EPERM = 1; ENOENT = 2; ESRCH = 3; EINTR = 4; EIO = 5; ENXIO = 6; EBADF = 9
EAGAIN = 11; ENOMEM = 12; EACCES = 13; EFAULT = 14; EBUSY = 16; EEXIST = 17
ENODEV = 19; ENOTDIR = 20; EISDIR = 21; EINVAL = 22; ENFILE = 23; EMFILE = 24
ENOSPC = 28; ESPIPE = 29; EROFS = 30; EPIPE = 32; ERANGE = 34; ENAMETOOLONG = 36
ENOSYS = 38; ENOTEMPTY = 39; ELOOP = 40; EOPNOTSUPP = 95; EAFNOSUPPORT = 97
ECONNREFUSED = 111; EINPROGRESS = 115; ETIMEDOUT = 110


def errno(n: int) -> int:
    """Linux-Konvention: negativer errno im Rueckgaberegister (als u64)."""
    return (-int(n)) & _M64


# ---- Flags / Konstanten -----------------------------------------------------
# open
O_RDONLY = 0; O_WRONLY = 1; O_RDWR = 2; O_CREAT = 0o100; O_EXCL = 0o200
O_TRUNC = 0o1000; O_APPEND = 0o2000; O_NONBLOCK = 0o4000; O_DIRECTORY = 0o200000
O_CLOEXEC = 0o2000000
AT_FDCWD = -100 & _M64
AT_SYMLINK_NOFOLLOW = 0x100; AT_EMPTY_PATH = 0x1000
# mmap prot/flags
PROT_NONE = 0; PROT_READ = 1; PROT_WRITE = 2; PROT_EXEC = 4
MAP_SHARED = 0x1; MAP_PRIVATE = 0x2; MAP_FIXED = 0x10; MAP_ANONYMOUS = 0x20
# lseek whence
SEEK_SET = 0; SEEK_CUR = 1; SEEK_END = 2
# clock ids
CLOCK_REALTIME = 0; CLOCK_MONOTONIC = 1; CLOCK_PROCESS_CPUTIME_ID = 2
CLOCK_THREAD_CPUTIME_ID = 3; CLOCK_MONOTONIC_RAW = 4; CLOCK_BOOTTIME = 7
# futex
FUTEX_WAIT = 0; FUTEX_WAKE = 1; FUTEX_PRIVATE_FLAG = 128
FUTEX_WAIT_PRIVATE = FUTEX_WAIT | FUTEX_PRIVATE_FLAG
FUTEX_WAKE_PRIVATE = FUTEX_WAKE | FUTEX_PRIVATE_FLAG
# prctl
PR_SET_NAME = 15; PR_GET_NAME = 16; PR_SET_DUMPABLE = 4; PR_GET_DUMPABLE = 3
PR_SET_VMA = 0x53564d41
# dirent type
DT_DIR = 4; DT_REG = 8; DT_LNK = 10; DT_CHR = 2
# stat mode bits
S_IFDIR = 0o040000; S_IFREG = 0o100000; S_IFLNK = 0o120000; S_IFCHR = 0o020000
# auxv
AT_NULL = 0; AT_PHDR = 3; AT_PHENT = 4; AT_PHNUM = 5; AT_PAGESZ = 6; AT_BASE = 7
AT_FLAGS = 8; AT_ENTRY = 9; AT_UID = 11; AT_EUID = 12; AT_GID = 13; AT_EGID = 14
AT_HWCAP = 16; AT_CLKTCK = 17; AT_SECURE = 23; AT_RANDOM = 25; AT_HWCAP2 = 26
AT_EXECFN = 31; AT_PLATFORM = 15; AT_PAGESZ_VAL = 0x1000


def name(num: int) -> str:
    return NR.get(int(num) & _M64, "sys_%d" % (int(num) & _M64))
