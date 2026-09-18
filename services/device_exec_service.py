import os
import threading
import subprocess

from core.infrastructure.command_runner import CommandRunner
from core.application.event_bus import EventBus


class DeviceExecService:
    """Deployt und faehrt LibForge-Executables (PIE) auf einem NICHT gerooteten Geraet.

    Konsequent ueber `adb` + `run-as`, niemals `su`. Die Ausgabe (stdout/stderr) wird
    zeilenweise ueber den EventBus als `EXEC_OUTPUT` publiziert:
        {"exe": <name>, "stream": "out"|"err"|"sys", "text": <zeile>}
    Laufende Prozesse werden je Executable-Name gehalten (parallele Laeufe moeglich).
    """

    def __init__(self, cfg_manager):
        self.cfg = cfg_manager
        self._procs = {}          # exe_name -> Popen
        self._lock = threading.Lock()

    # ---------- intern ----------
    def _adb(self) -> str:
        return self.cfg.paths.get("ADB", "adb")

    def _emit(self, exe: str, stream: str, text: str) -> None:
        EventBus.publish("EXEC_OUTPUT", {"exe": exe, "stream": stream, "text": text})

    @staticmethod
    def _startupinfo():
        if os.name == "nt":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            return si
        return None

    # ---------- Deploy ----------
    def deploy(self, exe_name: str, binary_local: str, run_dir: str, deps_local: list) -> bool:
        """Push von Binary + Runtime-Deps ins Run-Dir; chmod 755 auf das Binary."""
        adb = self._adb()
        if not binary_local or not os.path.exists(binary_local):
            self._emit(exe_name, "err", f"Binary nicht gefunden: {binary_local}")
            return False

        self._emit(exe_name, "sys", f"Deploy nach {run_dir} ...")
        dst_bin = f"{run_dir}/{exe_name}"
        r = CommandRunner.run_blocking(f'"{adb}" push "{binary_local}" "{dst_bin}"', ".", timeout=180)
        if r.returncode != 0:
            self._emit(exe_name, "err", f"push fehlgeschlagen: {r.stderr.strip() or r.stdout.strip()}")
            return False

        for dep in deps_local or []:
            if not dep or not os.path.exists(dep):
                self._emit(exe_name, "err", f"Runtime-Dep fehlt (uebersprungen): {dep}")
                continue
            rd = CommandRunner.run_blocking(
                f'"{adb}" push "{dep}" "{run_dir}/{os.path.basename(dep)}"', ".", timeout=180)
            if rd.returncode != 0:
                self._emit(exe_name, "err", f"Dep-push fehlgeschlagen: {os.path.basename(dep)}")
            else:
                self._emit(exe_name, "sys", f"Dep bereit: {os.path.basename(dep)}")

        CommandRunner.run_blocking(f'"{adb}" shell "chmod 755 {dst_bin}"', ".", timeout=15)
        self._emit(exe_name, "sys", "Deploy abgeschlossen (chmod 755).")
        return True

    # ---------- Input-Staging (App-Home -> Run-Dir, geraeteintern) ----------
    def stage_inputs(self, exe_name: str, pkg: str, inputs: list, run_dir: str) -> bool:
        """Kopiert App-Home-Dateien per `run-as cat > tmp` ins Run-Dir. Kein PC-Roundtrip."""
        if not inputs:
            return True
        if not pkg:
            self._emit(exe_name, "err", "Kein APP_PACKAGE gesetzt - Staging nicht moeglich.")
            return False
        adb = self._adb()
        names = " ".join(os.path.basename(i) for i in inputs)
        inner = (f"for f in {names}; do "
                 f"run-as {pkg} cat /data/data/{pkg}/$f > {run_dir}/$f; done")
        self._emit(exe_name, "sys", f"Stage {len(inputs)} Eingabe(n) aus App-Home ...")
        r = CommandRunner.run_blocking(f'"{adb}" shell "{inner}"', ".", timeout=60)
        if r.returncode != 0:
            self._emit(exe_name, "err", f"Staging-Fehler: {r.stderr.strip() or r.stdout.strip()}")
            return False
        self._emit(exe_name, "sys", "Staging abgeschlossen.")
        return True

    # ---------- Ausfuehren (nicht-blockierend, Live-Stream) ----------
    def run(self, exe_name: str, run_dir: str, run_cwd: str, run_env: str, run_args: str) -> bool:
        """Startet das Binary via Popen und streamt stdout/stderr live als EXEC_OUTPUT."""
        adb = self._adb()
        with self._lock:
            if exe_name in self._procs and self._procs[exe_name].poll() is None:
                self._emit(exe_name, "err", "Laeuft bereits - erst Stop.")
                return False

        cwd = run_cwd or run_dir
        env = (run_env or "").strip()
        args = (run_args or "").strip()
        inner = f"cd {cwd} && "
        if env:
            inner += f"{env} "
        inner += f"./{exe_name}"
        if args:
            inner += f" {args}"
        cmd = f'"{adb}" shell "{inner}"'

        self._emit(exe_name, "sys", f"RUN: {inner}")
        try:
            proc = subprocess.Popen(
                cmd, shell=True, cwd=".",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1, errors="replace",
                startupinfo=self._startupinfo(), close_fds=True)
        except Exception as e:
            self._emit(exe_name, "err", f"Start fehlgeschlagen: {e}")
            return False

        with self._lock:
            self._procs[exe_name] = proc

        threading.Thread(target=self._pump, args=(exe_name, proc.stdout, "out"), daemon=True).start()
        threading.Thread(target=self._pump, args=(exe_name, proc.stderr, "err"), daemon=True).start()
        threading.Thread(target=self._waiter, args=(exe_name, proc), daemon=True).start()
        return True

    def _pump(self, exe_name: str, stream, kind: str) -> None:
        try:
            for line in stream:
                line = line.rstrip("\n")
                if line:
                    self._emit(exe_name, kind, line)
        except Exception:
            pass

    def _waiter(self, exe_name: str, proc) -> None:
        rc = proc.wait()
        self._emit(exe_name, "sys", f"Prozess beendet (rc={rc}).")
        with self._lock:
            if self._procs.get(exe_name) is proc:
                self._procs.pop(exe_name, None)

    # ---------- Stop ----------
    def stop(self, exe_name: str) -> None:
        """Beendet den lokalen adb-Prozess UND den Remote-Prozess (sonst Waise auf dem Geraet)."""
        adb = self._adb()
        with self._lock:
            proc = self._procs.pop(exe_name, None)
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        # Remote-Prozess sicher beenden
        CommandRunner.run_blocking(f'"{adb}" shell "pkill -f {exe_name}"', ".", timeout=10)
        self._emit(exe_name, "sys", "Stop gesendet (lokal + pkill auf Geraet).")

    def is_running(self, exe_name: str) -> bool:
        with self._lock:
            p = self._procs.get(exe_name)
        return bool(p and p.poll() is None)

    # ---------- Cleanup ----------
    def cleanup(self, exe_name: str, run_dir: str, names: list) -> None:
        adb = self._adb()
        targets = " ".join(names) if names else exe_name
        CommandRunner.run_blocking(f'"{adb}" shell "cd {run_dir} && rm -f {targets}"', ".", timeout=20)
        self._emit(exe_name, "sys", f"Cleanup in {run_dir}: {targets}")

    # ---------- Ergebnis holen (optional, explizit) ----------
    def pull_result(self, exe_name: str, run_dir: str, files: list, local_dir: str) -> int:
        adb = self._adb()
        os.makedirs(local_dir, exist_ok=True)
        ok = 0
        for fn in files or []:
            base = os.path.basename(fn)
            r = CommandRunner.run_blocking(
                f'"{adb}" pull "{run_dir}/{base}" "{os.path.join(local_dir, base)}"', ".", timeout=180)
            if r.returncode == 0:
                ok += 1
                self._emit(exe_name, "sys", f"Ergebnis geholt: {base}")
            else:
                self._emit(exe_name, "err", f"Pull fehlgeschlagen: {base}")
        return ok
