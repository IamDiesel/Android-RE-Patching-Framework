# -*- coding: utf-8 -*-
"""native_java_service — stateless Service über die Java-Seite (J0 Thin Slice).

Dünne Service-Schicht (A.0) um `core.java.bridge.JavaToNativeBridge`: ruft eine registrierte native
Methode wie ein Java-Aufrufer über die JNIEnv-Fassade in `arm64mini` auf. Der volle DalvikVM (J1)
und native→Java-Callbacks (J2) docken später hier an.
"""
from __future__ import annotations


class NativeJavaService:
    def __init__(self, publish: bool = True):
        self.publish = publish

    def bridge(self, eng, jni):
        from core.java.bridge import JavaToNativeBridge
        return JavaToNativeBridge(eng, jni)

    def call_native(self, eng, jni, fnptr, this_cls: str = "Ljava/lang/Object;",
                    args=(), ret: str = "int", sp=None, maxins: int = 200000):
        """Java→JNI→native→Java (Rückgabe). Siehe JavaToNativeBridge.call_native."""
        return self.bridge(eng, jni).call_native(fnptr, this_cls, args, ret, sp, maxins)
