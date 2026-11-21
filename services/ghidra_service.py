"""ghidra_service — optionale C-Dekompilation via Ghidra analyzeHeadless (IP-neutral).

Laeuft GUI-seitig (dort, wo RE Studio startet) als Subprozess. Findet Ghidra per
Konfig (`GHIDRA_HEADLESS`), Umgebungsvariablen (`GHIDRA_HOME`/`GHIDRA_INSTALL_DIR`)
oder an gaengigen Installationsorten. Erster Lauf je Lib importiert+analysiert in ein
persistentes Projekt (data/native/<plugin>/ghidra/<sha8>/); danach ist das Projekt
gecacht und jede Funktion wird per `-process -noanalysis` schnell dekompiliert.
C-Ergebnisse werden je (Lib, Funktions-vaddr) als .c gecacht.

Zonen-Policy: Projekt + Cache liegen im Analyse-Arbeitsbereich (git-ignored), nie in
source/ oder destination/.
"""
import glob
import hashlib
import os
import subprocess
from shutil import which


def _candidates(cfg):
    exe = "analyzeHeadless.bat" if os.name == "nt" else "analyzeHeadless"
    cands = []
    try:
        p = cfg.config.get("GHIDRA_HEADLESS") if cfg else None
    except Exception:
        p = None
    if p:
        cands.append(p)
    # Projekt-lokale portable Ghidra-Installation (tools/ghidra/...)
    try:
        base_dir = cfg.config.get("BASE_DIR") if cfg else None
    except Exception:
        base_dir = None
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for root_base in (base_dir, repo_dir):
        if root_base:
            cands.extend(sorted(glob.glob(os.path.join(root_base, "tools", "ghidra*", "support", exe))))
            cands.extend(sorted(glob.glob(os.path.join(root_base, "tools", "ghidra", "ghidra*", "support", exe))))
    for env in ("GHIDRA_HOME", "GHIDRA_INSTALL_DIR"):
        d = os.environ.get(env)
        if d:
            cands.append(os.path.join(d, "support", exe))
    if os.name == "nt":
        roots = ["C:\\ghidra*", "C:\\Program Files\\ghidra*", "C:\\tools\\ghidra*",
                 os.path.expanduser("~\\ghidra*"), "C:\\Users\\*\\ghidra*"]
    else:
        roots = ["/opt/ghidra*", os.path.expanduser("~/ghidra*"), "/usr/local/ghidra*"]
    for r in roots:
        for d in glob.glob(r):
            cands.append(os.path.join(d, "support", exe))
    return cands


def find_headless(cfg=None):
    for c in _candidates(cfg):
        if c and os.path.isfile(c):
            return c
    return which("analyzeHeadless.bat") or which("analyzeHeadless")


def _sha8(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:8]


def _kill_tree(proc):
    """Beendet proc inkl. Kindprozesse (analyzeHeadless.bat -> java) — Windows via taskkill /T."""
    import sys as _sys
    try:
        if _sys.platform.startswith("win"):
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _stream_proc(svc, cmd, timeout):
    """Startet cmd via Popen, streamt wichtige Zeilen live in svc.log (+ Heartbeat alle 15s),
    mit Timeout und Abbruch (svc._cancelled / svc.cancel()). Rueckgabe: (tail_text, err|None)."""
    import threading as _th, time as _t
    _KEY = ("INFO", "ANALYZING", "Analyzing", "Packed", "Decompil", "DECOMPIL",
            "Script", "SCRIPT", "Import", "IMPORT", "Finished", "ERROR",
            "Exception", "SEVERE", "WARN", "Abort")
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", bufsize=1)
    except Exception as e:
        return "", repr(e)
    svc._proc = proc
    tail = []

    def _pump():
        try:
            for ln in proc.stdout:
                ln = ln.rstrip()
                if not ln:
                    continue
                tail.append(ln)
                if len(tail) > 600:
                    del tail[:200]
                if any(k in ln for k in _KEY):
                    try:
                        svc.log(ln)
                    except Exception:
                        pass
        except Exception:
            pass

    th = _th.Thread(target=_pump, daemon=True)
    th.start()
    start = _t.time()
    last_hb = start
    while proc.poll() is None:
        now = _t.time()
        if svc._cancelled:
            _kill_tree(proc); svc._proc = None
            return "\n".join(tail[-400:]), "cancelled"
        if timeout and (now - start) > timeout:
            _kill_tree(proc); svc._proc = None
            try:
                svc.log(f"[!] Ghidra-Timeout nach {int(now - start)}s — Prozess beendet.")
            except Exception:
                pass
            return "\n".join(tail[-400:]), "timeout"
        if now - last_hb >= 15:
            last_hb = now
            try:
                svc.log(f"[*] Ghidra laeuft … {int(now - start)}s (letzte Zeile: "
                        f"{tail[-1][:80] if tail else '—'})")
            except Exception:
                pass
        _t.sleep(0.5)
    th.join(timeout=2)
    svc._proc = None
    if svc._cancelled:
        return "\n".join(tail[-400:]), "cancelled"
    return "\n".join(tail[-400:]), None


class GhidraService:
    def __init__(self, cfg, log=print):
        self.cfg = cfg
        self.log = log
        self.headless = find_headless(cfg)
        self._proc = None
        self._cancelled = False

    def cancel(self):
        """Bricht einen laufenden Ghidra-Lauf ab (inkl. Java-Kindprozess auf Windows)."""
        self._cancelled = True
        p = self._proc
        if p is None or p.poll() is not None:
            return
        try:
            self.log("[!] Ghidra-Abbruch angefordert — beende Prozess …")
        except Exception:
            pass
        _kill_tree(p)

    def available(self):
        return bool(self.headless)

    def decompile(self, so_path, func_vaddr, workspace_dir, timeout=1200):
        if not self.headless:
            return {"ok": False, "why": "no_ghidra"}
        if not (so_path and os.path.isfile(so_path)):
            return {"ok": False, "why": "no_so", "hint": so_path}
        sha = _sha8(so_path)
        proj_dir = os.path.join(workspace_dir, "ghidra", sha)
        os.makedirs(proj_dir, exist_ok=True)
        proj = "nslib"
        script_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ghidra_scripts")
        out_file = os.path.join(proj_dir, "func_%x.c" % func_vaddr)
        if os.path.isfile(out_file):
            return {"ok": True, "cached": True,
                    "c": open(out_file, encoding="utf-8", errors="replace").read()}
        so_name = os.path.basename(so_path)
        sentinel = os.path.join(proj_dir, ".imported_" + so_name)

        def _post():
            return ["-scriptPath", script_dir, "-postScript", "DecompileOne.java",
                    "0x%x" % func_vaddr, out_file]

        self._cancelled = False

        def _run(import_mode):
            if import_mode:
                cmd = [self.headless, proj_dir, proj, "-import", so_path, "-overwrite",
                       "-analysisTimeoutPerFile", "600",
                       "-scriptPath", script_dir, "-preScript", "SpeedAnalysis.java"] + _post()
                tag = "Analyse+Decompile (Erstlauf/Import)"
                tmo = max(2400, timeout)   # Voll-Analyse grosser C++-Libs dauert -> grosszuegig
            else:
                cmd = [self.headless, proj_dir, proj, "-process", so_name, "-noanalysis"] + _post()
                tag = "Decompile (Projekt gecacht)"
                tmo = timeout
            self.log(f"[*] Ghidra analyzeHeadless: {tag} fuer 0x{func_vaddr:x} … "
                     f"(Timeout {tmo}s; ⏹ Abbrechen jederzeit moeglich)")
            return _stream_proc(self, cmd, tmo)

        # 1) bereits erfolgreich importiert -> schneller -process; sonst -import -overwrite
        import_mode = not os.path.isfile(sentinel)
        out, err = _run(import_mode)
        if err in ("timeout", "cancelled"):
            return {"ok": False, "why": err}
        # 2) -process ins Leere gelaufen (Projekt leer/kaputt)? Einmal frisch importieren.
        if (not os.path.isfile(out_file)) and (not import_mode):
            try:
                os.remove(sentinel)
            except OSError:
                pass
            out, err = _run(True)
            if err in ("timeout", "cancelled"):
                return {"ok": False, "why": err}
        if os.path.isfile(out_file):
            try:
                with open(sentinel, "w") as _s:
                    _s.write("ok")   # Import bestaetigt -> kuenftig schneller -process
            except OSError:
                pass
            return {"ok": True, "cached": False,
                    "c": open(out_file, encoding="utf-8", errors="replace").read()}
        errs = [ln.strip() for ln in (out or "").splitlines()
                if ("ERROR" in ln or "Abort" in ln or "Exception" in ln or "SEVERE" in ln)]
        return {"ok": False, "why": "no_output", "errors": errs[-6:]}
