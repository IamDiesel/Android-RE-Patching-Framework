import os
import shutil
from typing import Dict, Any

from core.pipeline.step_interface import PipelineStep
from services.native_lib_service import NativeLibManager


class InjectAddedLibsStep(PipelineStep):
    """Fuegt aktive LibForge-Libs ADDITIV in die APK ein.

    - Ueberschreibt eigene (von LibForge verwaltete) Libs aus frueheren Builds.
    - Entfernt deaktivierte LibForge-Libs, die noch im Workspace liegen.
    - Ueberschreibt NIEMALS echte App-Libs (Name nicht von LibForge verwaltet).
    Manager-getrieben (liest data/native_libs.json), unabhaengig von der UI.
    """

    def execute(self, step_config: Dict[str, Any], engine_context: Any) -> bool:
        base_dir = engine_context.cfg.config.get("BASE_DIR", "")
        mgr = NativeLibManager(base_dir)

        def _is_shared(l):
            return getattr(l, "output_kind", "shared") != "executable"

        # Executables werden NIE in die APK injiziert (sie laufen standalone via ExeDeploy).
        active = [l for l in mgr.get_active() if _is_shared(l)]
        # Fix 1.9: aktive-aber-.so-fehlt nicht still schlucken
        for _l in mgr.get_all():
            if getattr(_l, "active", False) and _is_shared(_l) \
                    and not (_l.so_relpath and os.path.exists(mgr.abspath(_l.so_relpath))):
                engine_context.log(f"[!] WARNUNG: aktive Lib '{_l.name}' (id={_l.id[:8]}) hat keine kompilierte .so ({_l.so_relpath}) -> wird NICHT injiziert!")
        managed_names = {mgr.so_output_name(l) for l in mgr.get_all() if _is_shared(l)}
        active_names = {mgr.so_output_name(l) for l in active}

        folder_name = engine_context.get_unpacked_dir_name()
        apk_lib_dir = os.path.join(engine_context.cfg.paths["DEST_DIR"], folder_name, "lib")
        if not os.path.exists(apk_lib_dir):
            engine_context.log("[*] LibForge: kein 'lib'-Ordner in der APK. Ueberspringe.")
            return True

        arch_dirs = [d for d in os.listdir(apk_lib_dir)
                     if os.path.isdir(os.path.join(apk_lib_dir, d))]
        if not arch_dirs:
            engine_context.log("[*] LibForge: keine Arch-Ordner in 'lib/'. Ueberspringe.")
            return True

        # 1) Cleanup: eigene, aber NICHT (mehr) aktive Libs aus dem Workspace entfernen
        removed = 0
        for arch in arch_dirs:
            arch_path = os.path.join(apk_lib_dir, arch)
            for fn in os.listdir(arch_path):
                _src_orig = os.path.join(engine_context.cfg.paths.get("APP_SOURCE_DIR", ""), folder_name, "lib", arch, fn)
                if fn in managed_names and fn not in active_names and not os.path.exists(_src_orig):
                    try:
                        os.remove(os.path.join(arch_path, fn))
                        removed += 1
                        engine_context.log(f"[*] LibForge: alte/inaktive '{fn}' aus lib/{arch}/ entfernt.")
                    except Exception:
                        pass

        if not active:
            if removed:
                engine_context.log(f"[i] LibForge: {removed} inaktive Lib(s) bereinigt, keine aktiven zum Hinzufuegen.")
            else:
                engine_context.log("[*] LibForge: keine aktiven Libs zum Hinzufuegen.")
            return True

        # 2) Aktive Libs einspielen (eigene ueberschreiben, echte App-Libs schuetzen)
        added = 0
        for lib in active:
            so_src = mgr.abspath(lib.so_relpath)
            so_name = mgr.so_output_name(lib)
            if not os.path.exists(so_src):
                engine_context.log(f"[!] LibForge: .so fehlt fuer '{lib.name}' - uebersprungen.")
                continue
            targets = [lib.abi] if lib.abi in arch_dirs else arch_dirs
            for arch in targets:
                dst = os.path.join(apk_lib_dir, arch, so_name)
                _src_orig = os.path.join(engine_context.cfg.paths.get("APP_SOURCE_DIR", ""), folder_name, "lib", arch, so_name)
                if os.path.exists(_src_orig):
                    engine_context.log(
                        f"[!] LibForge: '{so_name}' ist eine echte App-Lib (im Source vorhanden) "
                        f"-> Skip (kein Ueberschreiben, nutze lib_replace).")
                    continue
                try:
                    shutil.copyfile(so_src, dst)
                    os.utime(dst, None)  # Fix 1.9: frische mtime -> schlaegt apktool-Cache
                    added += 1
                    try:
                        import hashlib as _hl
                        _sz = os.path.getsize(dst)
                        with open(dst, "rb") as _f:
                            _sha = _hl.sha256(_f.read()).hexdigest()[:8]
                        engine_context.log(f"[+] LibForge: '{so_name}' -> lib/{arch}/ (id={lib.id[:8]}, v={getattr(lib,'version','?')}, {_sz} B, sha256={_sha})")
                    except Exception:
                        engine_context.log(f"[+] LibForge: '{so_name}' -> lib/{arch}/ (neu/ueberschrieben)")
                except Exception as e:
                    engine_context.log(f"[!] LibForge: Fehler beim Kopieren von '{so_name}': {e}")
                    return False

        if added:
            names = ", ".join(l.name for l in active)
            engine_context.log(
                f"[i] LibForge: {added} Datei(en) eingespielt. "
                f"Laden per Smali: System.loadLibrary(\"<name>\") fuer: {names}")
        return True
