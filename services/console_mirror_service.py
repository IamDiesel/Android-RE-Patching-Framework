"""
ConsoleMirrorService — spiegelt die GUI-Konsolen in Dateien (File-Exchange).

Beide Framework-Konsolen werden ueber den EventBus gefuellt:
  - Main Console (Workspace):            LOG_INFO
  - Start & Live-Log (App + Exe):        LOGCAT_LINE, LOG_INFO, GHOST_LOG_LINE, EXEC_OUTPUT
  - (API Inspector:                       PROXY_LOG)

Gespiegelt nach:
  data/console_main.log   (= Main Console)
  data/console_live.log   (= Start & Live-Log, inkl. PROXY)

Zusaetzliche Steuer-Events:
  CONSOLE_CLEARED    (scope: "live"|"main"|None) -> Marker-Zeile "=== GELEERT … ===",
                     Historie bleibt erhalten (fuer `since_last_clear`-Lesen).
  CONSOLE_CLEAR_HARD (scope: "live"|"main"|None) -> Datei wirklich leeren (truncate).

Groessenlimit ist konfigurierbar: MCP_SETTINGS.console.max_bytes (Default 20 MB).
Rein additiv, nie exception-werfend.
"""
import os
import time
import threading

from core.application.event_bus import EventBus

CLEAR_MARK = "GELEERT"  # Erkennungswort fuer `since_last_clear`


class ConsoleMirrorService:
    DEFAULT_MAX_BYTES = 20 * 1024 * 1024  # 20 MB (unfiltertes logcat + Minuten GUI-Interaktion)

    def __init__(self, cfg):
        self.cfg = cfg
        base = cfg.config.get("BASE_DIR", ".")
        self.dir = os.path.join(base, "data")
        os.makedirs(self.dir, exist_ok=True)
        self.main_path = os.path.join(self.dir, "console_main.log")
        self.live_path = os.path.join(self.dir, "console_live.log")
        self._lock = threading.Lock()
        self._wire()
        self._write(self.main_path, "MIRROR", "Konsolen-Spiegel gestartet.")
        self._write(self.live_path, "MIRROR", "Konsolen-Spiegel gestartet.")

    # ---------- Konfiguration ----------
    def _max_bytes(self) -> int:
        try:
            c = (self.cfg.config.get("MCP_SETTINGS", {}) or {}).get("console", {}) or {}
            v = int(c.get("max_bytes", self.DEFAULT_MAX_BYTES))
            return v if v > 0 else self.DEFAULT_MAX_BYTES
        except Exception:
            return self.DEFAULT_MAX_BYTES

    def _wire(self) -> None:
        EventBus.subscribe("LOG_INFO", self._on_log_info)
        EventBus.subscribe("LOGCAT_LINE", lambda l: self._write(self.live_path, "LOGCAT", l))
        EventBus.subscribe("GHOST_LOG_LINE", lambda l: self._write(self.live_path, "GHOST", l))
        EventBus.subscribe("EXEC_OUTPUT", self._on_exec)
        EventBus.subscribe("PROXY_LOG", lambda m: self._write(self.live_path, "PROXY", m))
        EventBus.subscribe("CONSOLE_CLEARED", self._on_cleared)
        EventBus.subscribe("CONSOLE_CLEAR_HARD", self._on_clear_hard)

    # ---------- Event-Handler ----------
    def _on_log_info(self, msg) -> None:
        # LOG_INFO erscheint in BEIDEN Konsolen -> in beide Dateien spiegeln.
        self._write(self.main_path, "LOG", msg)
        self._write(self.live_path, "LOG", msg)

    def _on_exec(self, data) -> None:
        try:
            exe = data.get("exe", "?")
            stream = data.get("stream", "out")
            text = data.get("text", "")
        except Exception:
            exe, stream, text = "?", "out", str(data)
        self._write(self.live_path, f"EXE:{exe}:{stream}", text)

    def _paths_for(self, scope):
        if scope == "live":
            return [self.live_path]
        if scope == "main":
            return [self.main_path]
        return [self.main_path, self.live_path]

    def _on_cleared(self, scope=None) -> None:
        """Soft-Clear: nur Marker schreiben, Historie bleibt (fuer since_last_clear)."""
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        for p in self._paths_for(scope):
            self._raw_append(p, f"=== {CLEAR_MARK} {stamp} ===\n")

    def _on_clear_hard(self, scope=None) -> None:
        """Hard-Clear: Datei wirklich leeren (truncate) + Kopfzeile."""
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            for p in self._paths_for(scope):
                try:
                    with open(p, "w", encoding="utf-8", errors="replace") as f:
                        f.write(f"=== HART {CLEAR_MARK} {stamp} ===\n")
                except Exception:
                    pass

    # ---------- Schreiben ----------
    def _write(self, path: str, tag: str, text) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] [{tag}] {text}\n"
        with self._lock:
            try:
                with open(path, "a", encoding="utf-8", errors="replace") as f:
                    f.write(line)
                if os.path.getsize(path) > self._max_bytes():
                    self._rotate(path)
            except Exception:
                pass

    def _raw_append(self, path: str, text: str) -> None:
        with self._lock:
            try:
                with open(path, "a", encoding="utf-8", errors="replace") as f:
                    f.write(text)
            except Exception:
                pass

    def _rotate(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                data = f.read()
            data = data[-(self._max_bytes() // 2):]
            with open(path, "w", encoding="utf-8", errors="replace") as f:
                f.write("... [gekuerzt] ...\n" + data)
        except Exception:
            pass
