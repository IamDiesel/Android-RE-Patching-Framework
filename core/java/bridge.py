# -*- coding: utf-8 -*-
"""core.java.bridge — J0 Thin Vertical Slice: Java→JNI→native→Java (Rückgabe).

Ein minimaler „Java-Aufrufer": nimmt eine registrierte native Methode (Funktionszeiger im
geladenen `.so`/Blob), marshallt die Java-seitigen Argumente in die **JNI-Konvention**
(`env`, `this`-jref, dann die Argumente) über die bestehende JNIEnv-Fassade
(`native_jni_env_service`), führt die native Methode in `arm64mini` aus und marshallt die Rückgabe
zurück (jint direkt; jstring-Handle → Python-str über die Fassade).

Damit ist das **Rückgrat** (Java-Seite ⟷ JNIEnv-Fassade ⟷ `arm64mini`) als wiederverwendbare
Komponente belegt — der volle Dalvik-Register-Interpreter (J1) baut darauf auf; native→Java-
Callbacks (J2) sind die Gegenrichtung. Zielagnostisch: keine libdb0c-Spezifika.
"""
from __future__ import annotations

from services import native_jni_env_service as J

_M64 = (1 << 64) - 1


class JavaToNativeBridge:
    """Marshalling + Aufruf einer nativen Methode über die JNIEnv-Fassade.

    ``eng`` = Engine mit geladenem Code; ``jni`` = installierte ``NativeJniEnvService``-Fassade
    (``jni.install(eng, ffi, ...)``)."""

    def __init__(self, eng, jni, emu=None, vm=None):
        self.eng = eng
        self.jni = jni
        if emu is None:
            from services.native_emu_service import NativeEmuService
            emu = NativeEmuService(publish=False)
        self.emu = emu
        self.vm = vm
        self.errors = []

    # ---- Argument-Marshalling (Java-Wert -> JNI-Wert) ----------------------
    def _marshal_arg(self, a) -> int:
        if isinstance(a, bool):
            return 1 if a else 0
        if isinstance(a, str):
            return int(self.jni._new_handle("jstring", text=a)) & _M64
        return int(a) & _M64   # jint/jlong/jref direkt (jbyteArray-Marshalling: J2)

    def _record_native_failure(self, vm, fnptr: int, this_cls: str,
                               method_name, method_sig, run_res: dict) -> tuple[str, str]:
        """Protokolliert einen fehlgeschlagenen nativen Lauf an Bridge, VM, Deviations und Debugger."""
        why = run_res.get("why") or "error"
        pc = run_res.get("pc")
        detail = (run_res.get("error") or run_res.get("mnem")
                  or run_res.get("stop_reason") or why)
        self.errors.append({
            "fnptr": int(fnptr) & _M64,
            "cls": this_cls,
            "method": method_name,
            "sig": method_sig,
            "why": why,
            "pc": pc,
            "error": detail,
        })
        if vm is not None:
            le = getattr(vm, "lenient_errors", None)
            if isinstance(le, list):
                le.append({
                    "cls": this_cls,
                    "name": method_name or f"native@0x{int(fnptr) & _M64:x}",
                    "sig": method_sig or "",
                    "error": f"{why}: {detail}",
                    "fnptr": int(fnptr) & _M64,
                    "why": why,
                    "pc": pc,
                })
            dev = getattr(vm, "deviations", None)
            if dev is not None and callable(getattr(dev, "record", None)):
                site = f"{this_cls}->{method_name or hex(int(fnptr) & _M64)}"
                try:
                    dev.record("native_fail", site=site, detail=f"{why}: {detail}")
                except TypeError:
                    try:
                        dev.record("native_fail", site, f"{why}: {detail}")
                    except Exception:
                        pass
                except Exception:
                    pass
            dbg = getattr(vm, "_dbg", None)
            if dbg is not None and isinstance(getattr(dbg, "markers", None), list):
                try:
                    step = len(getattr(dbg, "timeline", []))
                    dbg.markers.append({
                        "step": step,
                        "kind": "native_fail",
                        "label": f"native_fail@0x{int(fnptr) & _M64:x} ({why}): {detail}",
                    })
                except Exception:
                    pass
        return why, str(detail)

    # ---- Aufruf ------------------------------------------------------------
    def call_native(self, fnptr, this_cls: str = "Ljava/lang/Object;", args=(),
                    ret: str = "int", sp=None, maxins: int = 200000,
                    method_name: str = None, method_sig: str = None):
        """Ruft die native Methode ``fnptr(env, this, *args)`` auf. ``ret`` ∈
        {'int','string','jref','void'}. Rückgabe: Python-Wert (marshallt)."""
        args = list(args)
        # --- Grenz-Hook (beobachtbar + manipulierbar): Eingangs-Args VOR dem Aufruf ---
        hook = getattr(self, "boundary_hook", None)
        has_ext_hook = getattr(hook, "check_skip", None) is not None
        skip_native = False
        if hook is not None:
            try:
                if has_ext_hook:
                    args = hook.before(int(fnptr), this_cls, args,
                                       method_name=method_name, method_sig=method_sig)
                    skip_native = bool(hook.check_skip(
                        int(fnptr), this_cls, args,
                        method_name=method_name, method_sig=method_sig))
                else:
                    args = hook.before(int(fnptr), this_cls, args)
            except Exception:
                pass
        if skip_native:
            result = None if ret == "void" else 0
        else:
            this_h = int(self.jni._new_handle("jobject", cls=this_cls)) & _M64
            jargs = [J.ENV_OBJ & _M64, this_h] + [self._marshal_arg(a) for a in args]
            vm_ref = (self.vm if self.vm is not None
                      else getattr(getattr(self.jni, "java_runtime", None), "vm", None))
            run_res = self.emu.run(self.eng, int(fnptr), tuple(jargs), None, sp, int(maxins))
            eng_devs = getattr(self.eng, "_deviations", None)
            if isinstance(eng_devs, list) and eng_devs:
                pending_devs = list(eng_devs)
                eng_devs.clear()
                dev_j = getattr(vm_ref, "deviations", None) if vm_ref is not None else None
                step_now = int(getattr(vm_ref, "steps", 0) or 0) if vm_ref is not None else 0
                for d in pending_devs:
                    if isinstance(d, dict) and dev_j is not None and callable(getattr(dev_j, "record", None)):
                        dev_j.record(
                            str(d.get("kind") or "ffi_exc"),
                            site=str(d.get("site") or ""),
                            detail=str(d.get("detail") or ""),
                            step=step_now,
                        )
            failed = isinstance(run_res, dict) and (
                not run_res.get("ok", True)
                or run_res.get("why") in ("maxins", "timeout", "unknown_instr", "error", "stopped")
            )
            if failed:
                vm = vm_ref
                why, detail = self._record_native_failure(
                    vm, int(fnptr), this_cls, method_name, method_sig, run_res
                )
                if getattr(self, "strict", False) or (vm is not None and not getattr(vm, "lenient", True)):
                    msg = f"native call failed ({why}) at 0x{int(fnptr) & _M64:x}: {detail}"
                    if vm is not None:
                        from core.java.dalvik import DalvikException
                        raise DalvikException(msg)
                    raise RuntimeError(msg)
                result = None if ret in ("void", "string") else 0
            else:
                if isinstance(run_res, dict) and "ret" in run_res:
                    x0 = int(run_res["ret"]) & _M64
                else:
                    x0 = int(self.eng.cpu.x[0]) & _M64
                if ret == "void":
                    result = None
                elif ret == "jref":
                    result = x0
                elif ret == "string":
                    h = self.jni.st.handles.get(x0)
                    result = h.get("text") if (h and h.get("kind") == "jstring") else None
                else:
                    result = x0  # int/jint
        # --- Grenz-Hook: Rueckgabe NACH dem Aufruf (+ Aufzeichnung/Event/Marker) ---
        if hook is not None:
            try:
                if has_ext_hook:
                    result = hook.after(int(fnptr), this_cls, args, result,
                                        method_name=method_name, method_sig=method_sig)
                else:
                    result = hook.after(int(fnptr), this_cls, args, result)
            except Exception:
                pass
        return result
