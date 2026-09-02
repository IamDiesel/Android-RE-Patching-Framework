import os
import json


class FridaManager:
    """Verwaltet Frida-Skripte (Laden, Speichern, aktives Skript bereitstellen)."""

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        # NEU: Data-Ordner nutzen
        data_dir = os.path.join(base_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        self.json_file = os.path.join(data_dir, "frida_scripts.json")
        self.scripts = []
        self.active_script_id = None
        self.load()

    def load(self):
        if os.path.exists(self.json_file):
            try:
                with open(self.json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.scripts = data.get("scripts", [])
                    self.active_script_id = data.get("active_script_id")
            except Exception:
                self.scripts = []
                self.active_script_id = None
        else:
            self._create_default_script()

    def save(self):
        data = {
            "active_script_id": self.active_script_id,
            "scripts": self.scripts
        }
        with open(self.json_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    def _create_default_script(self):
        default_code = """// @ts-nocheck
    console.log("[*] Native RASP Hunter gestartet.");

    // 1. Dateizugriffe (I/O) überwachen
    // Wir hooken die C-Funktionen open() und openat() direkt im Linux-Kernel
    const openPtr = Module.findExportByName("libc.so", "open");
    const openatPtr = Module.findExportByName("libc.so", "openat");

    function hookFileAccess(ptr, pathIdx) {
        if (!ptr) return;
        Interceptor.attach(ptr, {
            onEnter: function (args) {
                try {
                    this.path = args[pathIdx].readUtf8String();
                    if (!this.path) return;

                    // Filtere das Android-Grundrauschen heraus. 
                    // Wir suchen nur nach Pfaden, die typischerweise von RASPs gelesen werden.
                    if (this.path.includes("/proc/") || 
                        this.path.includes("base.apk") || 
                        this.path.includes("frida") ||
                        this.path.includes("magisk") ||
                        this.path.includes("su")) {

                        console.log("[RASP-SCAN] Dateizugriff erkannt: " + this.path);
                    }
                } catch (e) {}
            }
        });
    }

    hookFileAccess(openPtr, 0);   // Signatur: int open(const char *pathname, int flags)
    hookFileAccess(openatPtr, 1); // Signatur: int openat(int dirfd, const char *pathname, int flags)

    // 2. Das Laden der RASP-Bibliothek abfangen
    const dlopenPtr = Module.findExportByName(null, "android_dlopen_ext") || Module.findExportByName(null, "dlopen");
    if (dlopenPtr) {
        Interceptor.attach(dlopenPtr, {
            onEnter: function (args) {
                try {
                    this.libName = args[0].readUtf8String();
                } catch (e) {}
            },
            onLeave: function (retval) {
                if (this.libName && this.libName.includes("libdb0c.so")) {
                    console.log("[!] RASP PROTEKTOR (libdb0c.so) IN DEN RAM GELADEN!");
                    console.log("[*] Base Address: " + retval);
                }
            }
        });
    }
    """
        self.scripts = [{
            "id": "default_rasp",
            "name": "Native RASP Hunter (I/O & dlopen)",
            "code": default_code
        }]
        self.active_script_id = "default_rasp"
        self.save()

    def get_active_code(self):
        for s in self.scripts:
            if s["id"] == self.active_script_id:
                return s["code"]
        return ""