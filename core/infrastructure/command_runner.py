import os
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional, Any


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner:
    """Kapselt systemnahe Subprozess-Aufrufe für eine testbare Architektur."""

    @staticmethod
    def _get_startupinfo() -> Optional[Any]:
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return startupinfo

    @classmethod
    def run_blocking(cls, cmd: str, cwd: str, timeout: float = None) -> CommandResult:
        """Führt einen Befehl blockierend aus und liefert das Gesamtergebnis.

        timeout (Sekunden, optional): verhindert Einfrieren, wenn z.B. kein Gerät
        angeschlossen ist. Bei Ablauf -> returncode -1, stderr '[timeout]'."""
        try:
            res = subprocess.run(
                cmd, shell=True, cwd=cwd,
                capture_output=True, text=True, timeout=timeout,
                startupinfo=cls._get_startupinfo(),
                close_fds=True  # FIX: Verhindert, dass Frida USB-Verbindungen an den Subprozess vererbt werden
            )
            return CommandResult(res.returncode, res.stdout, res.stderr)
        except subprocess.TimeoutExpired:
            return CommandResult(-1, "", "[timeout]")

    @classmethod
    def run_background(cls, cmd: str, cwd: str, out_file=None) -> Any:
        """
        Startet einen Prozess im Hintergrund (Fire & Forget oder für asynchrones Polling).
        out_file kann ein geöffnetes Datei-Objekt sein, in das stdout/stderr umgeleitet wird.
        """
        stdout_target = out_file if out_file else subprocess.PIPE
        stderr_target = subprocess.STDOUT if out_file else subprocess.STDOUT

        return subprocess.Popen(
            cmd, shell=True, cwd=cwd,
            stdout=stdout_target, stderr=stderr_target,
            text=True, bufsize=1, errors="replace",
            startupinfo=cls._get_startupinfo(),
            close_fds=True  # FIX: Zwingt Windows dazu, Handles beim Spawnen des Kind-Prozesses zu kappen
        )

    @classmethod
    def run_live(cls, cmd: str, cwd: str, on_line: Callable[[str], None], log_file: Optional[str] = None) -> bool:
        """
        Startet einen Prozess und streamt die Ausgabe in Echtzeit zeilenweise an den Callback.
        Optional wird die Ausgabe in eine Log-Datei geschrieben.
        """
        process = cls.run_background(cmd, cwd)

        f_out = None
        if log_file:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            f_out = open(log_file, "w", encoding="utf-8", errors="replace")

        try:
            if process.stdout:
                for line in process.stdout:
                    if f_out:
                        f_out.write(line)
                    clean_line = line.strip()
                    if clean_line:
                        on_line(clean_line)
        finally:
            if f_out:
                f_out.close()

        process.wait()
        return process.returncode == 0