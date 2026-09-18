import os

from core.infrastructure.command_runner import CommandRunner
from core.infrastructure.tool_manager import ToolManager
from core.application.event_bus import EventBus

ABI_TRIPLE = {
    "arm64-v8a": "aarch64-linux-android",
    "armeabi-v7a": "armv7a-linux-androideabi",
    "x86_64": "x86_64-linux-android",
    "x86": "i686-linux-android",
}


class NativeCompilerService:
    """Kompiliert eine NativeLib via NDK-clang zu lib<name>.so. Live-Log via EventBus."""

    @classmethod
    def compile(cls, lib, manager, config):
        """Returns (ok: bool, so_path: str, error_tail: str). Recompile ueberschreibt bewusst."""
        clang = ToolManager.resolve_clang(config)
        if not clang:
            return (False, "", "NDK/clang nicht verfuegbar. Siehe Konsole / Einstellungen (NDK-Pfad).")

        if not (lib.name or "").strip():
            return (False, "", "Bitte einen Lib-Namen angeben (Ausgabe wird lib<name>.so).")

        lib_dir = manager.lib_dir(lib)
        src = os.path.join(lib_dir, "source.c")
        try:
            with open(src, "w", encoding="utf-8") as f:
                f.write(lib.source_code or "")
        except Exception as e:
            return (False, "", f"Quelle konnte nicht geschrieben werden: {e}")

        out = os.path.join(lib_dir, manager.so_output_name(lib))
        triple = ABI_TRIPLE.get(lib.abi, "aarch64-linux-android")
        target = f"{triple}{int(lib.api_level)}"
        links = " ".join(f"-l{tok}" for tok in (lib.link_libs or "").split())
        extra = (lib.extra_flags or "").strip()

        # 16-KB-Page-Alignment (Android 15/16, N6): sonst meldet der Loader auf
        # 16-KB-Geraeten "LOAD-Segment stimmt nicht ueberein". Global ueber Config
        # gesteuert; 0 = aus.
        try:
            page = int(config.get("NATIVE_MAX_PAGE_SIZE", 16384))
        except (TypeError, ValueError):
            page = 16384
        page_flag = f"-Wl,-z,max-page-size={page}" if page > 0 else ""

        # Ausgabe-Art: Shared-Lib (Standard) oder eigenstaendiges PIE-Executable
        # (z.B. das Weg-3-Standalone-Orakel skb_oracle). Android verlangt fuer
        # Executables PIE (-fPIE -pie); Shared-Libs brauchen -shared -fPIC.
        kind = getattr(lib, "output_kind", "shared")
        mode_flags = "-fPIE -pie" if kind == "executable" else "-shared -fPIC"

        cmd = f'"{clang}" --target={target} {mode_flags} -O2 -s {page_flag} -o "{out}" "{src}" {links} {extra}'.strip()

        log_file = os.path.join(lib_dir, "build_log.txt")
        EventBus.publish("LOG_INFO", f"[*] Kompiliere {manager.so_output_name(lib)} ({lib.abi}, API {lib.api_level}, {kind}) ...")
        EventBus.publish("LOG_INFO", f"> {cmd}")

        try:
            if os.path.exists(out):
                os.remove(out)
        except Exception:
            pass

        buf = []

        def on_line(line):
            buf.append(line)
            EventBus.publish("LOG_INFO", f"[CC] {line.rstrip()}")

        ok = CommandRunner.run_live(cmd, lib_dir, on_line, log_file)

        if ok and os.path.exists(out):
            manager.set_build_result(lib, True, out)
            EventBus.publish("LOG_INFO", f"[+] Build OK: {os.path.basename(out)}")
            return (True, out, "")

        manager.set_build_result(lib, False, "")
        tail = "".join(buf[-20:]).strip() or "Unbekannter Compile-Fehler (siehe build_log.txt)."
        EventBus.publish("LOG_INFO", "[!] Build fehlgeschlagen.")
        return (False, "", tail)
