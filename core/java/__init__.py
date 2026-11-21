# -*- coding: utf-8 -*-
"""core.java — die Java-Seite des App-Emulators (Keystone, ab J0).

J0 (Thin Vertical Slice): `bridge.JavaToNativeBridge` ruft eine registrierte native Methode wie ein
Java-Aufrufer über die JNIEnv-Fassade in `arm64mini` auf (Java→JNI→native→Java-Rückgabe). Der volle
DalvikVM-Register-Interpreter (J1) und die native→Java-Callback-Richtung (J2) folgen.
"""
