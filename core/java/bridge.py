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

    def __init__(self, eng, jni, emu=None):
        self.eng = eng
        self.jni = jni
        if emu is None:
            from services.native_emu_service import NativeEmuService
            emu = NativeEmuService(publish=False)
        self.emu = emu

    # ---- Argument-Marshalling (Java-Wert -> JNI-Wert) ----------------------
    def _marshal_arg(self, a) -> int:
        if isinstance(a, bool):
            return 1 if a else 0
        if isinstance(a, str):
            return int(self.jni._new_handle("jstring", text=a)) & _M64
        return int(a) & _M64   # jint/jlong/jref direkt (jbyteArray-Marshalling: J2)

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
            self.emu.run(self.eng, int(fnptr), tuple(jargs), None, sp, int(maxins))
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
