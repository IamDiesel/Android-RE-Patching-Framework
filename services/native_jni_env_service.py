# -*- coding: utf-8 -*-
"""native_jni_env_service — Fake-JNIEnv/JavaVM-Fassade fuer die arm64mini-Emulation (A3.2).

Setzt additiv auf der FFI-Trap-Schicht (A3.1, ``NativeFfiService``) auf: Wir uebernehmen die Rolle
von ART und stellen die **vollstaendige JNI-Schnittstelle** als Fassade bereit, damit ``libdb0c``
ueber den ersten ``env->FindClass`` hinaus laeuft und die RASP-Logik beobachtbar wird.

Drei Stufen in einer kohaerenten Schicht:
  Stufe 1 — Struktur + Resolver + Log: volle 233-Slot-JNINativeInterface + 8-Slot-JNIInvokeInterface
            als FFI-Stubs; Handle-Registry (jclass/jmethodID/...); ``FindClass``/``GetMethodID``/
            ``RegisterNatives`` werden gegen das **echte App-Smali** (datei-basiert, 4 Roots) aufgeloest;
            jeder Call wird protokolliert.
  Stufe 2 — Klassifikator + Trivial-Evaluator: der Smali-Body der aufgeloesten Methode wird klassifiziert
            (root/debug/signature/proc/exec/frida/emulator/file) und ein **Vorschlags-Rueckgabewert**
            je Rueckgabetyp bestimmt; triviale Bodies (const/return) direkt ausgewertet.
  Stufe 3 — Policy: Default auto-pass mit Vorschlag; Watchlist markiert ``pending``; Overrides pro
            ``Klasse->Methode(Sig)`` erzwingen einen Wert (manipulieren).

Engine-Kontrakt bleibt bit-exakt: nur additive Nutzung von ``cpu.stub`` (via FFI) + Writes in einen
eigenen Fassaden-Speicherbereich. SOA: der Zustand (Handles, Call-Log, RegisterNatives-Map, Policy)
liegt an der Service-Instanz (1:1 pro ``install``) und zusaetzlich an ``eng._jni`` fuer Leser.
"""
from __future__ import annotations

import os
import re
import struct
from typing import Dict, List, Optional

_M64 = (1 << 64) - 1

# ---- Fassaden-Speicherlayout (weit ausserhalb der Lib, <0x15b958) --------------
ENV_OBJ = 0x66000000       # JNIEnv*  -> *(ENV_OBJ) = ENV_TABLE
ENV_TABLE = 0x66001000     # JNINativeInterface (233 Slots)
VM_OBJ = 0x66010000        # JavaVM*  -> *(VM_OBJ) = VM_TABLE
VM_TABLE = 0x66011000      # JNIInvokeInterface (8 Slots)
HANDLE_BASE = 0x55000000   # getaggte Fake-Handles (jclass/jmethodID/...)
SCRATCH = 0x67000000       # Bump-Allocator fuer Rueckgabe-Puffer (GetStringUTFChars ...)

# JNINativeInterface in fester ABI-Reihenfolge (Index == Offset/8). FindClass=6 (0x30) bestaetigt.
JNI_FUNCS = [
    "reserved0", "reserved1", "reserved2", "reserved3",
    "GetVersion", "DefineClass", "FindClass",
    "FromReflectedMethod", "FromReflectedField", "ToReflectedMethod",
    "GetSuperclass", "IsAssignableFrom", "ToReflectedField",
    "Throw", "ThrowNew", "ExceptionOccurred", "ExceptionDescribe", "ExceptionClear",
    "FatalError", "PushLocalFrame", "PopLocalFrame",
    "NewGlobalRef", "DeleteGlobalRef", "DeleteLocalRef", "IsSameObject",
    "NewLocalRef", "EnsureLocalCapacity",
    "AllocObject", "NewObject", "NewObjectV", "NewObjectA",
    "GetObjectClass", "IsInstanceOf", "GetMethodID",
    "CallObjectMethod", "CallObjectMethodV", "CallObjectMethodA",
    "CallBooleanMethod", "CallBooleanMethodV", "CallBooleanMethodA",
    "CallByteMethod", "CallByteMethodV", "CallByteMethodA",
    "CallCharMethod", "CallCharMethodV", "CallCharMethodA",
    "CallShortMethod", "CallShortMethodV", "CallShortMethodA",
    "CallIntMethod", "CallIntMethodV", "CallIntMethodA",
    "CallLongMethod", "CallLongMethodV", "CallLongMethodA",
    "CallFloatMethod", "CallFloatMethodV", "CallFloatMethodA",
    "CallDoubleMethod", "CallDoubleMethodV", "CallDoubleMethodA",
    "CallVoidMethod", "CallVoidMethodV", "CallVoidMethodA",
    "CallNonvirtualObjectMethod", "CallNonvirtualObjectMethodV", "CallNonvirtualObjectMethodA",
    "CallNonvirtualBooleanMethod", "CallNonvirtualBooleanMethodV", "CallNonvirtualBooleanMethodA",
    "CallNonvirtualByteMethod", "CallNonvirtualByteMethodV", "CallNonvirtualByteMethodA",
    "CallNonvirtualCharMethod", "CallNonvirtualCharMethodV", "CallNonvirtualCharMethodA",
    "CallNonvirtualShortMethod", "CallNonvirtualShortMethodV", "CallNonvirtualShortMethodA",
    "CallNonvirtualIntMethod", "CallNonvirtualIntMethodV", "CallNonvirtualIntMethodA",
    "CallNonvirtualLongMethod", "CallNonvirtualLongMethodV", "CallNonvirtualLongMethodA",
    "CallNonvirtualFloatMethod", "CallNonvirtualFloatMethodV", "CallNonvirtualFloatMethodA",
    "CallNonvirtualDoubleMethod", "CallNonvirtualDoubleMethodV", "CallNonvirtualDoubleMethodA",
    "CallNonvirtualVoidMethod", "CallNonvirtualVoidMethodV", "CallNonvirtualVoidMethodA",
    "GetFieldID",
    "GetObjectField", "GetBooleanField", "GetByteField", "GetCharField", "GetShortField",
    "GetIntField", "GetLongField", "GetFloatField", "GetDoubleField",
    "SetObjectField", "SetBooleanField", "SetByteField", "SetCharField", "SetShortField",
    "SetIntField", "SetLongField", "SetFloatField", "SetDoubleField",
    "GetStaticMethodID",
    "CallStaticObjectMethod", "CallStaticObjectMethodV", "CallStaticObjectMethodA",
    "CallStaticBooleanMethod", "CallStaticBooleanMethodV", "CallStaticBooleanMethodA",
    "CallStaticByteMethod", "CallStaticByteMethodV", "CallStaticByteMethodA",
    "CallStaticCharMethod", "CallStaticCharMethodV", "CallStaticCharMethodA",
    "CallStaticShortMethod", "CallStaticShortMethodV", "CallStaticShortMethodA",
    "CallStaticIntMethod", "CallStaticIntMethodV", "CallStaticIntMethodA",
    "CallStaticLongMethod", "CallStaticLongMethodV", "CallStaticLongMethodA",
    "CallStaticFloatMethod", "CallStaticFloatMethodV", "CallStaticFloatMethodA",
    "CallStaticDoubleMethod", "CallStaticDoubleMethodV", "CallStaticDoubleMethodA",
    "CallStaticVoidMethod", "CallStaticVoidMethodV", "CallStaticVoidMethodA",
    "GetStaticFieldID",
    "GetStaticObjectField", "GetStaticBooleanField", "GetStaticByteField", "GetStaticCharField",
    "GetStaticShortField", "GetStaticIntField", "GetStaticLongField", "GetStaticFloatField",
    "GetStaticDoubleField",
    "SetStaticObjectField", "SetStaticBooleanField", "SetStaticByteField", "SetStaticCharField",
    "SetStaticShortField", "SetStaticIntField", "SetStaticLongField", "SetStaticFloatField",
    "SetStaticDoubleField",
    "NewString", "GetStringLength", "GetStringChars", "ReleaseStringChars",
    "NewStringUTF", "GetStringUTFLength", "GetStringUTFChars", "ReleaseStringUTFChars",
    "GetArrayLength",
    "NewObjectArray", "GetObjectArrayElement", "SetObjectArrayElement",
    "NewBooleanArray", "NewByteArray", "NewCharArray", "NewShortArray", "NewIntArray",
    "NewLongArray", "NewFloatArray", "NewDoubleArray",
    "GetBooleanArrayElements", "GetByteArrayElements", "GetCharArrayElements",
    "GetShortArrayElements", "GetIntArrayElements", "GetLongArrayElements",
    "GetFloatArrayElements", "GetDoubleArrayElements",
    "ReleaseBooleanArrayElements", "ReleaseByteArrayElements", "ReleaseCharArrayElements",
    "ReleaseShortArrayElements", "ReleaseIntArrayElements", "ReleaseLongArrayElements",
    "ReleaseFloatArrayElements", "ReleaseDoubleArrayElements",
    "GetBooleanArrayRegion", "GetByteArrayRegion", "GetCharArrayRegion", "GetShortArrayRegion",
    "GetIntArrayRegion", "GetLongArrayRegion", "GetFloatArrayRegion", "GetDoubleArrayRegion",
    "SetBooleanArrayRegion", "SetByteArrayRegion", "SetCharArrayRegion", "SetShortArrayRegion",
    "SetIntArrayRegion", "SetLongArrayRegion", "SetFloatArrayRegion", "SetDoubleArrayRegion",
    "RegisterNatives", "UnregisterNatives", "MonitorEnter", "MonitorExit", "GetJavaVM",
    "GetStringRegion", "GetStringUTFRegion",
    "GetPrimitiveArrayCritical", "ReleasePrimitiveArrayCritical",
    "GetStringCritical", "ReleaseStringCritical",
    "NewWeakGlobalRef", "DeleteWeakGlobalRef", "ExceptionCheck",
    "NewDirectByteBuffer", "GetDirectBufferAddress", "GetDirectBufferCapacity",
    "GetObjectRefType",
]  # 233 Eintraege

VM_FUNCS = ["reserved0", "reserved1", "reserved2", "DestroyJavaVM", "AttachCurrentThread",
            "DetachCurrentThread", "GetEnv", "AttachCurrentThreadAsDaemon"]

# Klassifikations-Muster (Smali-Body-Substrings) -> Kategorie
_CLASS_PATTERNS = [
    ("root", ("/system/xbin/su", "/system/bin/su", "/sbin/su", "magisk", "superuser",
              "Superuser.apk", "busybox", "/su/bin")),
    ("exec", ("Ljava/lang/Runtime;->exec", "Ljava/lang/ProcessBuilder;", "/system/bin/sh")),
    ("debug", ("Landroid/os/Debug;->isDebuggerConnected", "TracerPid", "waitForDebugger",
               "Landroid/os/Debug;->isDebuggerConnected()Z")),
    ("signature", ("GET_SIGNATURES", "GET_SIGNING_CERTIFICATES", "Landroid/content/pm/Signature;",
                   "getPackageInfo", "signatures")),
    ("frida", ("frida", "gum-js-loop", "gmain", "linjector", "27042", "re.frida", "gadget")),
    ("emulator", ("goldfish", "ranchu", "generic_x86", "vbox", "Genymotion",
                  "Landroid/os/Build;->FINGERPRINT", "ro.kernel.qemu", "sdk_gphone")),
    ("proc", ("/proc/self/", "/proc/", "/proc/net/tcp", "maps")),
    ("file-exists", ("Ljava/io/File;->exists", "Ljava/io/File;->canRead")),
    ("xposed", ("de/robv/android/xposed", "XposedBridge", "EdXposed", "LSPosed")),
]


class _JniState:
    __slots__ = ("handles", "next_handle", "calls", "natives", "classes", "max_calls",
                 "pending_exc", "exc_handle", "exc_events")

    def __init__(self):
        self.handles: Dict[int, dict] = {}   # id -> {kind, cls, name, sig, category, proposed, ret}
        self.next_handle = HANDLE_BASE
        self.calls: List[dict] = []          # geordnetes Call-Log
        self.natives: List[dict] = []        # RegisterNatives-Landkarte
        self.classes: Dict[int, str] = {}    # jclass-handle -> classname
        self.max_calls = 100000
        self.pending_exc: Optional[dict] = None  # {type, detail, handle} oder None (ART-Pending-Exception)
        self.exc_handle = 0                  # aktuelles jthrowable-Handle (0 == keins)
        self.exc_events: List[dict] = []     # Historie gesetzter/gecleareter Exceptions (Beobachtung)


class NativeJniEnvService:
    def __init__(self, publish: bool = True, app_base: Optional[str] = None):
        self.publish = publish
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.app_base = app_base or os.path.join(
            root, "source", "org.nativescript.LibreLinkUp_5_1_1", "base_unpacked_apktool")
        self.smali_roots = [os.path.join(self.app_base, r)
                            for r in ("smali", "smali_classes2", "smali_classes3", "smali_classes4")]
        self.st = _JniState()
        self.scratch_next = SCRATCH
        self.overrides: Dict[str, int] = {}   # "cls->name(sig)" -> forced return
        self.watchlist = set()                # Kategorien, die pending markieren
        self.default_watch_categories = {"root", "debug", "signature", "frida", "emulator",
                                         "exec", "proc", "file-exists", "xposed"}

    # ================= Installation =================
    def install(self, eng, ffi, set_entry_reg: Optional[int] = 0) -> dict:
        """Baut JNIEnv+JavaVM im Emulator-Speicher, haengt alle Slots als FFI-Stubs ein.
        set_entry_reg: wenn nicht None, wird x[set_entry_reg] = ENV_OBJ gesetzt (Default x0)."""
        m = eng.cpu.m
        m.wb(ENV_OBJ, ENV_TABLE.to_bytes(8, "little"))
        for i, name in enumerate(JNI_FUNCS):
            stub = ffi.install_named_stub(eng, "JNIEnv::" + name)
            m.wb(ENV_TABLE + i * 8, stub.to_bytes(8, "little"))
            ffi.set_handler(eng, "JNIEnv::" + name, self._mk(name, "env"))
        m.wb(VM_OBJ, VM_TABLE.to_bytes(8, "little"))
        for i, name in enumerate(VM_FUNCS):
            stub = ffi.install_named_stub(eng, "JavaVM::" + name)
            m.wb(VM_TABLE + i * 8, stub.to_bytes(8, "little"))
            ffi.set_handler(eng, "JavaVM::" + name, self._mk(name, "vm"))
        if set_entry_reg is not None:
            eng.cpu.x[set_entry_reg] = ENV_OBJ
        eng._jni = self.st
        self.watchlist = set(self.default_watch_categories)
        return {"ok": True, "env": ENV_OBJ, "vm": VM_OBJ, "slots": len(JNI_FUNCS)}

    def _mk(self, name, kind):
        def h(cpu, ffi):
            return self._dispatch(cpu, name, kind)
        return h

    # ================= Hilfen: Speicher / Strings =================
    @staticmethod
    def _cstr(cpu, addr, maxn=512):
        if not addr:
            return ""
        out = bytearray()
        a = int(addr) & _M64
        for _ in range(maxn):
            b = cpu.m.rb(a, 1)
            if not b or b[0] == 0:
                break
            out.append(b[0])
            a += 1
        return out.decode("utf-8", "replace")

    def _alloc(self, cpu, data: bytes) -> int:
        a = self.scratch_next
        cpu.m.wb(a, bytes(data))
        self.scratch_next = (a + len(data) + 8) & ~0x7
        return a

    def _new_handle(self, kind, **kw) -> int:
        hid = self.st.next_handle
        self.st.next_handle += 0x10
        d = {"kind": kind}
        d.update(kw)
        self.st.handles[hid] = d
        return hid

    # ================= JNI-Exception-Zustand (ART-treu) =================
    def _set_pending(self, kind: str, detail: str = "") -> int:
        """Setzt eine Pending-Exception (wie ART nach einem fehlgeschlagenen Lookup) und gibt das
        jthrowable-Handle zurueck. ExceptionCheck/Occurred spiegeln den Zustand bis ExceptionClear."""
        h = self._new_handle("jthrowable", exc=kind, detail=detail)
        self.st.pending_exc = {"type": kind, "detail": detail, "handle": h}
        self.st.exc_handle = h
        self.st.exc_events.append({"set": kind, "detail": detail, "handle": hex(h)})
        return h

    def _clear_pending(self):
        if self.st.pending_exc is not None:
            self.st.exc_events.append({"clear": self.st.pending_exc.get("type")})
        self.st.pending_exc = None
        self.st.exc_handle = 0

    # ================= Smali-Resolver (Stufe 1) =================
    def _class_file(self, classname: str) -> Optional[str]:
        """classname in Slash-Form ('com/foo/Bar') -> .smali-Pfad oder None."""
        rel = classname.strip()
        if rel.startswith("L") and rel.endswith(";"):
            rel = rel[1:-1]
        rel = rel.replace(".", "/")
        for root in self.smali_roots:
            p = os.path.join(root, rel + ".smali")
            if os.path.isfile(p):
                return p
        return None

    def _method_body(self, classfile: str, name: str, sig: str):
        """Body-Zeilen der Methode name+sig im .smali oder None."""
        try:
            with open(classfile, encoding="utf-8", errors="replace") as f:
                txt = f.read()
        except Exception:
            return None
        pat = re.compile(r"^\.method\b.*?\b" + re.escape(name) + re.escape(sig) + r"\s*$",
                         re.MULTILINE)
        m = pat.search(txt)
        if not m:
            return None
        start = m.end()
        end = txt.find(".end method", start)
        body = txt[start:end if end >= 0 else len(txt)]
        return body

    # ================= Klassifikator + Evaluator (Stufe 2) =================
    @staticmethod
    def _ret_char(sig: str) -> str:
        i = sig.rfind(")")
        r = sig[i + 1:] if i >= 0 else "V"
        return r[0] if r else "V"

    def _classify(self, body: Optional[str]) -> Optional[str]:
        if not body:
            return None
        low = body
        for cat, pats in _CLASS_PATTERNS:
            for p in pats:
                if p in low:
                    return cat
        return None

    def _trivial_eval(self, body: Optional[str], ret_char: str):
        """Einfache Bodies direkt auswerten: const vX, n; return vX  /  return-void. Sonst None."""
        if body is None:
            return None
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()
                 and not ln.strip().startswith(("#", ".line", ".prologue", ".registers", ".locals",
                                                ".param", ".annotation", ".end"))]
        if not lines:
            return None
        if ret_char == "V" and lines and lines[-1].startswith("return-void"):
            return 0
        # Muster: ... const(/4|/16|-wide)? vR, LIT ; return(-wide)? vR
        m = re.match(r"return(?:-wide|-object)?\s+([vp]\d+)", lines[-1])
        if not m:
            return None
        reg = m.group(1)
        for ln in reversed(lines[:-1]):
            cm = re.match(r"const(?:/4|/16|/high16|-wide(?:/16|/high16|/32)?)?\s+" +
                          re.escape(reg) + r",\s*(-?0x[0-9a-fA-F]+|-?\d+)", ln)
            if cm:
                try:
                    return int(cm.group(1), 0) & _M64
                except Exception:
                    return None
            if reg in ln:  # Register wird anders geschrieben -> nicht trivial
                return None
        return None

    def _propose(self, ret_char: str, category: Optional[str], trivial):
        """Vorschlags-Rueckgabewert: trivial > sauber(0/false/null)."""
        if trivial is not None:
            return trivial, "trivial"
        # saubere Default-Antwort: 0 == false == null (bei Detection-Checks = 'nicht erkannt')
        return 0, ("clean" if category else "default")

    # ================= Policy (Stufe 3) =================
    def set_override(self, cls, name, sig, value):
        self.overrides["%s->%s%s" % (cls, name, sig)] = int(value) & _M64

    def _apply_policy(self, cls, name, sig, proposed, category):
        key = "%s->%s%s" % (cls, name, sig)
        if key in self.overrides:
            return self.overrides[key], True, False
        pending = bool(category and category in self.watchlist)
        return proposed, False, pending

    def _extract_jni_args(self, cpu, ptypes: List[str], arg_reg0: int,
                          is_va: bool, is_arr: bool) -> list:
        """Extrahiert JNI-Argumente fuer Call*Method / MethodV / MethodA nach AAPCS64."""
        x = cpu.x
        out = []
        if is_arr:
            jval_ptr = int(x[arg_reg0]) & _M64
            for i, pt in enumerate(ptypes):
                slot = int(cpu.m.r64((jval_ptr + 8 * i) & _M64)) & _M64 if jval_ptr else 0
                if pt == "F":
                    out.append(struct.unpack("<f", struct.pack("<I", slot & 0xffffffff))[0])
                elif pt == "D":
                    out.append(struct.unpack("<d", struct.pack("<Q", slot & _M64))[0])
                else:
                    out.append(slot)
            return out
        if is_va:
            va_ptr = int(x[arg_reg0]) & _M64
            if not va_ptr:
                return [0.0 if pt in ("F", "D") else 0 for pt in ptypes]
            stack = int(cpu.m.r64(va_ptr)) & _M64
            gr_top = int(cpu.m.r64((va_ptr + 8) & _M64)) & _M64
            vr_top = int(cpu.m.r64((va_ptr + 16) & _M64)) & _M64
            gr_raw = int(cpu.m.r32((va_ptr + 24) & _M64))
            vr_raw = int(cpu.m.r32((va_ptr + 28) & _M64))
            gr_offs = gr_raw - 0x100000000 if (gr_raw & 0x80000000) else gr_raw
            vr_offs = vr_raw - 0x100000000 if (vr_raw & 0x80000000) else vr_raw
            for pt in ptypes:
                if pt in ("F", "D"):
                    if vr_offs < 0 and vr_offs + 16 <= 0:
                        addr = (vr_top + vr_offs) & _M64
                        vr_offs += 16
                    else:
                        vr_offs = 0
                        addr = stack
                        stack = (stack + 8) & _M64
                    if pt == "F":
                        out.append(struct.unpack("<f", struct.pack("<I", int(cpu.m.r32(addr)) & 0xffffffff))[0])
                    else:
                        out.append(struct.unpack("<d", struct.pack("<Q", int(cpu.m.r64(addr)) & _M64))[0])
                else:
                    if gr_offs < 0 and gr_offs + 8 <= 0:
                        addr = (gr_top + gr_offs) & _M64
                        gr_offs += 8
                    else:
                        gr_offs = 0
                        addr = stack
                        stack = (stack + 8) & _M64
                    out.append(int(cpu.m.r64(addr)) & _M64)
            cpu.m.w64(va_ptr, stack & _M64)
            cpu.m.w32((va_ptr + 24) & _M64, gr_offs & 0xffffffff)
            cpu.m.w32((va_ptr + 28) & _M64, vr_offs & 0xffffffff)
            return out
        gp_idx = arg_reg0
        fp_idx = 0
        stack_off = 0
        for pt in ptypes:
            if pt in ("F", "D"):
                nb = 4 if pt == "F" else 8
                if fp_idx < 8:
                    raw_b = cpu.vget(fp_idx, nb) if hasattr(cpu, "vget") else (b"\x00" * nb)
                    fp_idx += 1
                    out.append(struct.unpack("<f" if pt == "F" else "<d", raw_b)[0])
                else:
                    addr = (int(cpu.sp) + stack_off) & _M64
                    stack_off += 8
                    if pt == "F":
                        out.append(struct.unpack("<f", struct.pack("<I", int(cpu.m.r32(addr)) & 0xffffffff))[0])
                    else:
                        out.append(struct.unpack("<d", struct.pack("<Q", int(cpu.m.r64(addr)) & _M64))[0])
            else:
                if gp_idx < 8:
                    out.append(int(x[gp_idx]) & _M64)
                    gp_idx += 1
                else:
                    addr = (int(cpu.sp) + stack_off) & _M64
                    stack_off += 8
                    out.append(int(cpu.m.r64(addr)) & _M64)
        return out

    @staticmethod
    def _marshal_fp_ret(cpu, val, rchar: str) -> int:
        if rchar == "F":
            if isinstance(val, float):
                raw = struct.pack("<f", val)
                bits = int.from_bytes(raw, "little")
            else:
                bits = int(val or 0) & 0xffffffff
                raw = struct.pack("<I", bits)
            if hasattr(cpu, "vset"):
                cpu.vset(0, raw)
            return bits
        if rchar == "D":
            if isinstance(val, float):
                raw = struct.pack("<d", val)
                bits = int.from_bytes(raw, "little")
            else:
                bits = int(val or 0) & _M64
                raw = struct.pack("<Q", bits)
            if hasattr(cpu, "vset"):
                cpu.vset(0, raw)
            return bits
        return int(val or 0) & _M64

    # ================= Dispatcher =================
    def _log(self, entry):
        if len(self.st.calls) < self.st.max_calls:
            self.st.calls.append(entry)

    def _dispatch(self, cpu, name, kind):
        x = cpu.x
        ret = 0
        info = {"fn": name, "kind": kind}

        if kind == "vm":
            if name == "GetEnv":                       # (vm, void** penv, version) -> 0
                cpu.m.wb(int(x[1]) & _M64, ENV_OBJ.to_bytes(8, "little"))
                ret = 0
            elif name == "AttachCurrentThread":
                cpu.m.wb(int(x[1]) & _M64, ENV_OBJ.to_bytes(8, "little"))
                ret = 0
            else:
                ret = 0
            self._log({**info, "ret": ret})
            return ret & _M64

        # ---- env ----
        if name == "GetVersion":
            ret = 0x00010006
        elif name == "FindClass":
            cls = self._cstr(cpu, x[1])
            cf = self._class_file(cls)
            if not cls:
                # ART-treu: FindClass("") -> NULL + Pending-Exception (NoClassDefFoundError).
                # Rueckgabe 0 (statt Nicht-Null-Handle) liefert dem OLLVM-Dispatcher den echten
                # Index (const - 0) und damit den not-found-Zweig statt des NULL-Slots (vgl. 0x39414).
                self._set_pending("NoClassDefFoundError", "(empty name)")
                self._log({**info, "class": "", "resolved": False, "handle": "0x0", "null": True,
                           "exception": "NoClassDefFoundError", "name_ptr": hex(int(x[1]) & _M64),
                           "lr": hex(int(cpu.lr) & _M64), "x23": hex(int(x[23]) & _M64)})
                return 0
            hid = self._new_handle("jclass", cls=cls, file=cf)
            self.st.classes[hid] = cls
            ret = hid
            self._log({**info, "class": cls, "resolved": bool(cf), "handle": hex(hid),
                       "name_ptr": hex(int(x[1]) & _M64), "lr": hex(int(cpu.lr) & _M64),
                       "x23": hex(int(x[23]) & _M64)})
            return ret & _M64
        elif name in ("GetMethodID", "GetStaticMethodID"):
            clsh = int(x[1]) & _M64
            mname = self._cstr(cpu, x[2])
            sig = self._cstr(cpu, x[3])
            cls = self.st.classes.get(clsh) or (self.st.handles.get(clsh, {}) or {}).get("cls", "?")
            cf = self._class_file(cls) if cls and cls != "?" else None
            body = self._method_body(cf, mname, sig) if cf else None
            rchar = self._ret_char(sig)
            cat = self._classify(body)
            triv = self._trivial_eval(body, rchar)
            proposed, psrc = self._propose(rchar, cat, triv)
            hid = self._new_handle("jmethodID", cls=cls, name=mname, sig=sig, static=(name[3] == "S"),
                                   category=cat, proposed=proposed, propsrc=psrc, retchar=rchar,
                                   resolved=bool(body))
            ret = hid
            self._log({**info, "class": cls, "method": mname, "sig": sig, "category": cat,
                       "proposed": proposed, "propsrc": psrc, "resolved": bool(body),
                       "handle": hex(hid)})
            return ret & _M64
        elif name in ("GetFieldID", "GetStaticFieldID"):
            clsh = int(x[1]) & _M64
            fname = self._cstr(cpu, x[2])
            sig = self._cstr(cpu, x[3])
            cls = self.st.classes.get(clsh) or (self.st.handles.get(clsh, {}) or {}).get("cls", "?")
            hid = self._new_handle("jfieldID", cls=cls, name=fname, sig=sig)
            ret = hid
            self._log({**info, "class": cls, "field": fname, "sig": sig, "handle": hex(hid)})
            return ret & _M64
        elif name == "RegisterNatives":
            clsh = int(x[1]) & _M64
            arr = int(x[2]) & _M64
            cnt = int(x[3]) & 0xffffffff
            cls = self.st.classes.get(clsh) or (self.st.handles.get(clsh, {}) or {}).get("cls", "?")
            recs = []
            for i in range(min(cnt, 4096)):
                base = arr + i * 24
                np= int.from_bytes(cpu.m.rb(base, 8), "little")
                sp = int.from_bytes(cpu.m.rb(base + 8, 8), "little")
                fp = int.from_bytes(cpu.m.rb(base + 16, 8), "little")
                rec = {"class": cls, "name": self._cstr(cpu, np), "sig": self._cstr(cpu, sp),
                       "fnptr": hex(fp & _M64)}
                recs.append(rec)
                self.st.natives.append(rec)
            self._log({**info, "class": cls, "count": int(cnt), "registered": recs})
            return 0
        elif name.startswith("Call") and ("Method" in name):
            is_static = "Static" in name
            is_nonvirtual = "Nonvirtual" in name
            is_va = name.endswith("MethodV")
            is_arr = name.endswith("MethodA")
            if is_nonvirtual:
                obj_h = int(x[1]) & _M64
                clazz_h = int(x[2]) & _M64
                mid = int(x[3]) & _M64
                arg_reg0 = 4
                h = self.st.handles.get(mid, {})
                cls = (self.st.classes.get(clazz_h)
                       or (self.st.handles.get(clazz_h, {}) or {}).get("cls")
                       or h.get("cls", "?"))
            else:
                mid = int(x[2]) & _M64
                arg_reg0 = 3
                obj_h = (int(x[1]) & _M64) if not is_static else None
                h = self.st.handles.get(mid, {})
                cls = h.get("cls", "?")
            mname = h.get("name", "?"); sig = h.get("sig", "?")
            rchar = h.get("retchar") or (
                self._ret_char(sig) if sig and sig != "?"
                else ("F" if "Float" in name else ("D" if "Double" in name else "I"))
            )
            # --- J2: native->Java-Rueckruf, falls eine JavaRuntime eine registrierte Dalvik-Methode
            #     bereitstellt. Default-off (keine Runtime -> unveraenderter RASP-Policy-Pfad). ---
            rt = getattr(self, "java_runtime", None)
            target_cls = cls
            if not is_static and not is_nonvirtual and rt is not None and obj_h:
                oh = self.st.handles.get(obj_h) or {}
                ocls = oh.get("cls")
                if not ocls or ocls == "?":
                    py_obj = getattr(rt, "_h2py", {}).get(obj_h)
                    ocls = getattr(py_obj, "cls", None) or ("Ljava/lang/String;" if type(py_obj).__name__ == "JString" else None)
                if ocls and ocls != "?" and rt.has_method(ocls, mname, sig):
                    target_cls = ocls
            if (rt is not None and target_cls not in (None, "?") and mname not in (None, "?")
                    and sig not in (None, "?") and rt.has_method(target_cls, mname, sig)):
                try:
                    from core.java.runtime import sig_param_types
                    ptypes = sig_param_types(sig)
                    extracted = self._extract_jni_args(cpu, ptypes, arg_reg0, is_va, is_arr)
                    call_args = ([obj_h] + extracted) if not is_static else extracted
                    co = getattr(rt.call_from_native, "__code__", None)
                    if co is not None and "param_types" in getattr(co, "co_varnames", ()):
                        jret = rt.call_from_native(target_cls, mname, sig, call_args, is_static,
                                                   param_types=ptypes, ret_type=rchar,
                                                   nonvirtual=is_nonvirtual)
                    else:
                        jret = rt.call_from_native(target_cls, mname, sig, call_args, is_static)
                    jret_i = self._marshal_fp_ret(cpu, jret, rchar)
                    self._log({**info, "class": target_cls, "method": mname, "sig": sig,
                               "j2": True, "ret": jret_i})
                    return jret_i
                except Exception as e:
                    self._log({**info, "class": target_cls, "method": mname, "sig": sig,
                               "j2_error": repr(e)})   # Fallthrough auf die RASP-Policy
            proposed = h.get("proposed", 0); cat = h.get("category")
            final, overridden, pending = self._apply_policy(cls, mname, sig, proposed, cat)
            ret_i = self._marshal_fp_ret(cpu, final, rchar)
            self._log({**info, "class": cls, "method": mname, "sig": sig, "category": cat,
                       "proposed": proposed, "final": final, "overridden": overridden,
                       "pending": pending})
            return ret_i & _M64
        elif name == "ExceptionCheck":
            ret = 1 if self.st.pending_exc else 0
        elif name == "ExceptionOccurred":
            ret = self.st.exc_handle if self.st.pending_exc else 0
        elif name == "ExceptionClear":
            self._clear_pending()
            ret = 0
        elif name == "ExceptionDescribe":
            ret = 0  # ART loggt die Exception (ohne zu clearen); wir protokollieren nur
        elif name in ("Throw", "ThrowNew"):
            self._set_pending(name)  # native setzt selbst eine Exception -> Pending markieren
            ret = 0
        elif name == "GetJavaVM":                       # (env, JavaVM** vm) -> 0
            cpu.m.wb(int(x[1]) & _M64, VM_OBJ.to_bytes(8, "little"))
            ret = 0
        elif name == "GetObjectClass":
            ret = self._new_handle("jclass", cls="?")
        elif name in ("NewGlobalRef", "NewLocalRef", "NewWeakGlobalRef"):
            ret = int(x[1]) & _M64 or self._new_handle("jobject")
        elif name == "NewStringUTF":
            s = self._cstr(cpu, x[1])
            ret = self._new_handle("jstring", text=s)
        elif name == "GetStringUTFChars":
            h = self.st.handles.get(int(x[1]) & _M64, {})
            data = (h.get("text", "") or "").encode("utf-8") + b"\x00"
            ret = self._alloc(cpu, data)
            if x[2]:
                cpu.m.wb(int(x[2]) & _M64, (0).to_bytes(1, "little"))  # isCopy=false
        elif name == "GetStringUTFLength":
            h = self.st.handles.get(int(x[1]) & _M64, {})
            ret = len((h.get("text", "") or "").encode("utf-8"))
        elif name == "NewByteArray":
            n = int(x[1]) & 0xffffffff
            ret = self._new_handle("jbyteArray", data=bytearray(n))
        elif name == "GetArrayLength":
            h = self.st.handles.get(int(x[1]) & _M64, {})
            d = h.get("data")
            ret = len(d) if d is not None else int(h.get("length", 0))
        elif name == "GetByteArrayElements":
            h = self.st.handles.get(int(x[1]) & _M64, {})
            d = h.get("data") or bytearray()
            ptr = self._alloc(cpu, bytes(d))
            h["elem_ptr"] = ptr
            if x[2]:
                cpu.m.wb(int(x[2]) & _M64, (0).to_bytes(1, "little"))  # isCopy=false
            ret = ptr
        elif name == "ReleaseByteArrayElements":
            h = self.st.handles.get(int(x[1]) & _M64, {})
            d = h.get("data")
            mode = int(x[3]) & 0xffffffff
            ptr = int(x[2]) & _M64
            if d is not None and mode != 2 and ptr:      # 2 = JNI_ABORT
                h["data"] = bytearray(cpu.m.rb(ptr, len(d)))
            ret = 0
        elif name == "GetByteArrayRegion":
            h = self.st.handles.get(int(x[1]) & _M64, {})
            d = h.get("data") or bytearray()
            start = int(x[2]) & 0xffffffff
            n = int(x[3]) & 0xffffffff
            buf = int(x[4]) & _M64
            if buf:
                cpu.m.wb(buf, bytes(d[start:start + n]))
            ret = 0
        elif name == "SetByteArrayRegion":
            h = self.st.handles.get(int(x[1]) & _M64, {})
            d = h.get("data")
            if d is None:
                d = bytearray(); h["data"] = d
            start = int(x[2]) & 0xffffffff
            n = int(x[3]) & 0xffffffff
            buf = int(x[4]) & _M64
            if buf:
                chunk = cpu.m.rb(buf, n)
                if start + n > len(d):
                    d.extend(b"\x00" * (start + n - len(d)))
                d[start:start + n] = chunk
            ret = 0
        elif name.startswith("Get") and name.endswith("Field"):
            ret = 0
        else:
            ret = 0  # generischer, protokollierter Default
        self._log({**info, "ret": ret})
        return ret & _M64

    # ================= Leser =================
    def get_calls(self):
        return list(self.st.calls)

    def get_natives(self):
        return list(self.st.natives)

    def summary(self):
        from collections import Counter
        cats = Counter(c.get("category") for c in self.st.calls if c.get("category"))
        fns = Counter(c.get("fn") for c in self.st.calls)
        return {"calls": len(self.st.calls), "natives": len(self.st.natives),
                "by_fn": dict(fns), "by_category": dict(cats),
                "exc_events": list(self.st.exc_events),
                "pending_exc": (self.st.pending_exc or {}).get("type")}

    def smali_of(self, cls, name, sig):
        """Fuer das GUI-Popup: Smali-Body + Klassifizierung + Vorschlag + Datei/Zeile."""
        cf = self._class_file(cls)
        body = self._method_body(cf, name, sig) if cf else None
        rchar = self._ret_char(sig)
        cat = self._classify(body)
        triv = self._trivial_eval(body, rchar)
        proposed, psrc = self._propose(rchar, cat, triv)
        return {"ok": bool(body), "file": cf, "class": cls, "name": name, "sig": sig,
                "category": cat, "proposed": proposed, "propsrc": psrc, "retchar": rchar,
                "body": body or ""}
