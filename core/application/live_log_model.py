# -*- coding: utf-8 -*-
"""core.application.live_log_model — persistenter Zustand des Live-Logs (App + Logcat + Exe).

Der Zustand (laufende Captures, Log-Historie, Auswahl, Session-Datei, Status) lebt hier auf
App-Ebene, NICHT im Widget. Die View (LauncherLoggerTab) bindet sich nur an dieses Modell und
rendert daraus. So ueberlebt der Zustand jeden Neuaufbau der View (andocken, teilen, abdocken):
"Der Zustand darf nicht von der Anzeige abhaengen."

Mehrere Views duerfen gleichzeitig gebunden sein (z. B. unten + abgedockt); sie teilen sich
Historie und Dienste. Neue Zeilen werden an alle gebundenen Views gerendert.
"""
import os
import time

from core.application.event_bus import EventBus
from services.logcat_service import LogcatService
from services.ghost_log_service import GhostLogService
from services.profile_manager_service import ProfileManagerService


class LiveLogModel:
    def __init__(self, app):
        self.app = app
        self.raw_logs = []          # list[(line:str, rtag:str)] — vollstaendige Historie
        self.logcat_service = LogcatService()
        self.ghost_log_service = GhostLogService()
        data_dir = os.path.join(app.cfg.config.get("BASE_DIR", ""), "data")
        self.profile_mgr = ProfileManagerService(os.path.join(data_dir, "logger_profiles.json"))
        self.selections = {}        # Feldwerte der View (Intent, Logcat, Filter, Checkboxen, ...)
        self.app_launched = False
        self.exec_rtags = {}        # exe -> rtag-Name (stabil)
        self.rtag_colors = {}       # rtag-Name -> Hexfarbe (dynamische Exec-Tags)
        self._session_fh = None
        self._views = []            # gebundene Views
        self._subscribed = False
        self.subscribe()

    # ---- View-Bindung --------------------------------------------------
    def add_view(self, view):
        if view not in self._views:
            self._views.append(view)

    def remove_view(self, view):
        try:
            self._views.remove(view)
        except ValueError:
            pass

    # ---- EventBus (einmalig, App-Ebene) --------------------------------
    def subscribe(self):
        if self._subscribed:
            return
        EventBus.subscribe("LOGCAT_LINE", self._on_logcat)
        EventBus.subscribe("GHOST_LOG_LINE", self._on_ghost)
        EventBus.subscribe("EXEC_OUTPUT", self._on_exec)
        EventBus.subscribe("LOG_INFO", self._on_frida)
        self._subscribed = True

    def _marshal(self, fn):
        try:
            self.app.after(0, fn)
        except Exception:
            try:
                fn()
            except Exception:
                pass

    def _on_logcat(self, line):
        self._marshal(lambda: self.emit_now(line, self._auto_tag(line)))

    def _on_ghost(self, line):
        self._marshal(lambda: self.emit_now(line, "ghost_log"))

    def _on_exec(self, data):
        exe = (data or {}).get("exe", "exe")
        stream = (data or {}).get("stream", "out")
        text = (data or {}).get("text", "")
        line = f"[{exe}] {text}"
        if stream == "err":
            rtag = "error_log"
        elif stream == "sys":
            rtag = "exec_sys"
        else:
            rtag = self._exec_rtag(exe)
        self._marshal(lambda: self.emit_now(line, rtag))

    def _on_frida(self, msg):
        if isinstance(msg, str) and (msg.startswith("[Frida]") or msg.startswith("[Frida ERROR]")):
            self._marshal(lambda: self.emit_now(msg, "frida_log"))
        elif isinstance(msg, str) and msg.startswith("[!]"):
            self._marshal(lambda: self.emit_now(msg, "error_log"))

    # ---- Emittieren (Historie + Session + alle Views) ------------------
    def emit(self, line, rtag=""):
        """Thread-sicher: marshalt in den UI-Thread."""
        self._marshal(lambda: self.emit_now(line, rtag))

    def emit_now(self, line, rtag=""):
        self.raw_logs.append((line, rtag))
        self.write_session(line)
        for v in list(self._views):
            try:
                v.on_line(line, rtag)
            except Exception:
                pass

    def clear_logs(self):
        self.raw_logs.clear()

    # ---- Tag-Logik -----------------------------------------------------
    def _auto_tag(self, line):
        l = (line or "").lower()
        if "[ghost]" in l:
            return "ghost_log"
        if "frida" in l:
            return "frida_log"
        if "fatal" in l or "crash" in l or " exception " in l:
            return "error_log"
        return ""

    def _exec_rtag(self, exe):
        if not self.app.cfg.config.get("EXEC_COLOR_PER_EXE", True):
            return "exec_out"
        rtag = self.exec_rtags.get(exe)
        if rtag is None:
            palette = ["#98C379", "#61AFEF", "#C678DD", "#56B6C2", "#D19A66", "#E5C07B"]
            color = palette[len(self.exec_rtags) % len(palette)]
            rtag = f"exec_c{len(self.exec_rtags)}"
            self.exec_rtags[exe] = rtag
            self.rtag_colors[rtag] = color
        return rtag

    # ---- Session-Datei -------------------------------------------------
    def _session_path(self):
        archive_dir = getattr(self.app, "current_archive_path", "") or \
            self.app.cfg.paths.get("ARCHIVE_DIR", "")
        os.makedirs(archive_dir, exist_ok=True)
        return os.path.join(archive_dir, f"session_{time.strftime('%Y%m%d-%H%M%S')}.log")

    def write_session(self, line):
        try:
            if self._session_fh is None:
                self._session_fh = open(self._session_path(), "a", encoding="utf-8", errors="replace")
            self._session_fh.write(line + "\n")
            self._session_fh.flush()
        except Exception:
            pass

    def close_session(self):
        if self._session_fh:
            try:
                self._session_fh.close()
            except Exception:
                pass
            self._session_fh = None

    def shutdown(self):
        """Beim App-Ende: laufende Captures stoppen und Session schliessen."""
        try:
            self.logcat_service.stop_capture()
        except Exception:
            pass
        try:
            self.ghost_log_service.stop_capture()
        except Exception:
            pass
        self.close_session()
