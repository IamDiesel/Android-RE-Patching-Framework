"""
tools — die eigentliche Tool-Logik (MVP, Stufe O/P).

Bewusst FREI von der `mcp`-Abhaengigkeit -> auf jeder Maschine testbar.
Jede Funktion nimmt den AppContext + Argumente und liefert str oder dict.
Gating/Audit passieren aussen in runtime.run().
"""
import os
import re
import glob
import json
import time
import shutil
import threading
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from core.infrastructure.command_runner import CommandRunner
from services.patch_service import PatchService
from core.application.session_state import SessionState
from core.native.unpack import sha8
from services.native_annotations_service import NativeAnnotationsService
from mcp_server import registry

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _adb(ctx) -> str:
    return ctx.cfg.paths.get("ADB", "adb")


def _base_dir(ctx) -> str:
    if ctx is not None and getattr(ctx, "cfg", None) is not None:
        b = (getattr(ctx.cfg, "config", None) or {}).get("BASE_DIR")
        if b:
            return b
    return _REPO


def _source_dir(ctx) -> str:
    if ctx is not None and getattr(ctx, "cfg", None) is not None:
        sd = (getattr(ctx.cfg, "paths", None) or {}).get("SOURCE_DIR")
        if sd:
            return sd
    return os.path.join(_base_dir(ctx), "source")


# ============================================================ A: Workspace
def workspace_status(ctx):
    c = ctx.cfg.config
    st = CommandRunner.run_blocking(f'"{_adb(ctx)}" get-state', ".", timeout=6)
    device = st.stdout.strip() if st.returncode == 0 else "kein Geraet"
    m = c.get("MCP_SETTINGS", {})
    return {
        "APP_PACKAGE": c.get("APP_PACKAGE"),
        "SPLIT_NAME": c.get("SPLIT_NAME"),
        "MANIFEST_STRATEGY": c.get("MANIFEST_STRATEGY"),
        "INJECT_FRIDA": c.get("INJECT_FRIDA"),
        "INJECT_DEBUGGABLE": c.get("INJECT_DEBUGGABLE"),
        "device": device,
        "mcp_server": m.get("server", {}),
        "base_dir": c.get("BASE_DIR"),
    }


def workspace_set_config(ctx, key, value):
    global _RAM_CACHE
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "false"):
            value = (low == "true")
        elif value.strip().lstrip("-").isdigit():
            value = int(value)
    ctx.cfg.config[key] = value
    ctx.cfg.save()
    if key in ("APP_PACKAGE", "MANIFEST_STRATEGY", "BASE_DIR"):
        _RAM_CACHE = None
    return f"gesetzt: {key} = {value!r} (in config.json gespeichert)"


def workspace_prepare_hint(ctx):
    return ("PREPARE_WORKSPACE (Bauen -> Nutzer):\n"
            "GUI: 'App Manager' -> App importieren, dann 'Workspace' -> Pipeline "
            "PREPARE_WORKSPACE (Merge Split APKs + Decompile).\n"
            "Danach liegt der Smali-Baum unter source/<APP_PACKAGE>/<unpacked>/smali*/.")


def workspace_renew_id(ctx):
    """Generiert eine neue Patch-Session-ID (PID-YYYYmmdd-HHMMSS) inkl. Archiv-Ordner."""
    import datetime as _dt
    pid = f"PID-{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    arch_base = (getattr(ctx.cfg, "paths", None) or {}).get("ARCHIVE_DIR") or os.path.join(_base_dir(ctx), "archives")
    arch_path = os.path.join(arch_base, pid)
    os.makedirs(arch_path, exist_ok=True)
    ctx.cfg.config["CURRENT_PATCH_ID"] = pid
    ctx.cfg.save()
    return {"ok": True, "patch_id": pid, "archive_dir": arch_path}


def app_list_local(ctx):
    """Listet lokal importierte Apps unter source/ samt APKs, Entpack-Status und UI-Toolkit."""
    from core.java.runtime_detect import detect_runtime
    sdir = _source_dir(ctx)
    active_pkg = (getattr(ctx.cfg, "config", None) or {}).get("APP_PACKAGE", "")
    apps = []
    if os.path.isdir(sdir):
        for entry in sorted(os.listdir(sdir)):
            ad = os.path.join(sdir, entry)
            if not os.path.isdir(ad):
                continue
            apks = sorted(f for f in os.listdir(ad) if f.lower().endswith(".apk"))
            unpacked = sorted(d for d in os.listdir(ad)
                              if d.startswith("base_unpacked_") and os.path.isdir(os.path.join(ad, d)))
            rt = None
            if unpacked:
                try:
                    rt = detect_runtime(app_dir=os.path.join(ad, unpacked[0]), max_files=4000)
                except Exception:
                    rt = None
            apps.append({
                "package": entry,
                "active": (entry == active_pkg),
                "path": ad,
                "apks": apks,
                "unpacked_dirs": unpacked,
                "runtime": rt,
            })
    return {"ok": True, "source_dir": sdir, "active_package": active_pkg, "count": len(apps), "apps": apps}


def app_import(ctx, path, package_name="", switch_active=True):
    """Importiert einen lokalen APK-/Unpacked-Ordner oder eine einzelne .apk nach source/<pkg>/ + Auto-Config."""
    raw = os.path.abspath(str(path or "").strip())
    if not raw or not os.path.exists(raw):
        return {"ok": False, "hinweis": f"Pfad nicht gefunden: {path!r}"}

    src_is_file = os.path.isfile(raw)
    folder = os.path.dirname(raw) if src_is_file else raw
    if not src_is_file and os.path.basename(folder) in ("base_unpacked_apktool", "base_unpacked_apkeditor"):
        folder = os.path.dirname(folder)

    if src_is_file:
        if not raw.lower().endswith(".apk"):
            return {"ok": False, "hinweis": "Datei muss auf .apk enden"}
        apks = [os.path.basename(raw)]
        unpacked = []
    else:
        apks = sorted(f for f in os.listdir(folder) if f.lower().endswith(".apk"))
        unpacked = sorted(d for d in ("base_unpacked_apktool", "base_unpacked_apkeditor")
                          if os.path.isdir(os.path.join(folder, d)))
        if not apks and not unpacked:
            return {"ok": False, "hinweis": "Ordner enthaelt weder .apk noch base_unpacked_apktool / base_unpacked_apkeditor"}

    pkg = str(package_name or "").strip().replace(" ", "_")
    if not pkg and unpacked:
        try:
            from core.java.manifest import parse_manifest
            pkg = getattr(parse_manifest(os.path.join(folder, unpacked[0])), "package", "") or ""
        except Exception:
            pkg = ""
    if not pkg:
        pkg = (os.path.splitext(os.path.basename(raw))[0] if src_is_file else os.path.basename(folder)).replace(" ", "_")
    pkg = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in pkg).strip("._") or "imported.app"

    sdir = _source_dir(ctx)
    target = os.path.join(sdir, pkg)
    os.makedirs(target, exist_ok=True)
    copied = []
    if src_is_file:
        dst_file = os.path.join(target, os.path.basename(raw))
        if os.path.abspath(raw) != os.path.abspath(dst_file):
            shutil.copy2(raw, dst_file)
        copied.append(os.path.basename(raw))
        base_dst = os.path.join(target, "base.apk")
        if not os.path.exists(base_dst):
            shutil.copy2(dst_file, base_dst)
            copied.append("base.apk")
    else:
        for f in apks:
            sf = os.path.join(folder, f)
            df = os.path.join(target, f)
            if os.path.abspath(sf) != os.path.abspath(df):
                shutil.copy2(sf, df)
            copied.append(f)
        for d in unpacked:
            sd = os.path.join(folder, d)
            dd = os.path.join(target, d)
            if os.path.abspath(sd) != os.path.abspath(dd):
                shutil.copytree(sd, dd, dirs_exist_ok=True)
            copied.append(d + "/")

    if switch_active:
        global _RAM_CACHE
        _RAM_CACHE = None
        ctx.cfg.config["APP_PACKAGE"] = pkg
        if "split_config.arm64_v8a.apk" in copied:
            ctx.cfg.config["SPLIT_NAME"] = "split_config.arm64_v8a"
        elif "split_config.x86_64.apk" in copied:
            ctx.cfg.config["SPLIT_NAME"] = "split_config.x86_64"
        else:
            ctx.cfg.config["SPLIT_NAME"] = pkg
        ctx.cfg.save()

    return {
        "ok": True,
        "package": pkg,
        "target_folder": target,
        "copied": copied,
        "switched_active": bool(switch_active),
    }


def app_pull_apk(ctx, package="", switch_active=True):
    """Zieht Base + Split-APKs der gewaehlten oder aktiven App per ADB nach source/<pkg>/."""
    pkg = str(package or "").strip() or (ctx.cfg.config.get("APP_PACKAGE", "") or "").strip()
    if not pkg:
        return {"ok": False, "hinweis": "Kein Package-Name angegeben und APP_PACKAGE ist leer."}
    sdir = _source_dir(ctx)
    target = os.path.join(sdir, pkg)
    os.makedirs(target, exist_ok=True)

    res = CommandRunner.run_blocking(f'"{_adb(ctx)}" shell pm path {pkg}', target, timeout=20)
    if res.returncode != 0:
        return {"ok": False, "package": pkg, "hinweis": (res.stderr or res.stdout).strip() or "adb pm path fehlgeschlagen"}
    paths = [ln.replace("package:", "").strip() for ln in (res.stdout or "").splitlines() if ln.strip().startswith("package:")]
    if not paths:
        return {"ok": False, "package": pkg, "hinweis": f"Keine APK-Pfade fuer {pkg} gefunden."}

    pulled = []
    errors = []
    for rp in paths:
        pr = CommandRunner.run_blocking(f'"{_adb(ctx)}" pull "{rp}" "{target}"', target, timeout=180)
        if pr.returncode == 0:
            pulled.append(os.path.basename(rp))
        else:
            errors.append(f"{rp}: {(pr.stderr or pr.stdout).strip()}")

    arch = "ARM32"
    for rp in paths:
        if "arm64_v8a" in rp or "arm64" in rp:
            arch = "ARM64"
            break
        if "x86_64" in rp:
            arch = "x86_64"
            break

    if switch_active and pulled:
        global _RAM_CACHE
        _RAM_CACHE = None
        ctx.cfg.config["APP_PACKAGE"] = pkg
        if arch == "ARM64":
            ctx.cfg.config["SPLIT_NAME"] = "split_config.arm64_v8a"
        elif arch == "x86_64":
            ctx.cfg.config["SPLIT_NAME"] = "split_config.x86_64"
        ctx.cfg.save()

    return {
        "ok": bool(pulled) and not errors,
        "package": pkg,
        "target_folder": target,
        "architecture": arch,
        "pulled_files": pulled,
        "errors": errors,
        "switched_active": bool(switch_active and pulled),
    }


# ============================================================ B: Smali
def _unpacked_candidates(ctx):
    """Liefert (app_src, configured_dirname, [vorhandene_unpacked_dirs...]).
    Reihenfolge: zuerst die Config-Strategie, dann die anderen bekannten Varianten,
    dann alles Weitere `base_unpacked_*`. So finden wir den Baum auch, wenn mit einer
    anderen Strategie entpackt wurde als aktuell eingestellt."""
    app_src = ctx.cfg.paths.get("APP_SOURCE_DIR", "")
    try:
        configured = ctx.engine.get_unpacked_dir_name()
    except Exception:
        configured = "base_unpacked_apktool"
    names = [configured, "base_unpacked_apktool", "base_unpacked_apkeditor"]
    # zusaetzlich alles Weitere, was tatsaechlich da ist
    for d in sorted(glob.glob(os.path.join(app_src, "base_unpacked_*"))):
        nm = os.path.basename(d)
        if nm not in names:
            names.append(nm)
    dirs, seen = [], set()
    for nm in names:
        if nm in seen:
            continue
        seen.add(nm)
        d = os.path.join(app_src, nm)
        if os.path.isdir(d):
            dirs.append(d)
    return app_src, configured, dirs


def _smali_roots(ctx):
    """Waehlt den ersten vorhandenen unpacked-Ordner, der tatsaechlich smali*-Unterordner
    enthaelt (Config-Strategie bevorzugt)."""
    app_src, configured, dirs = _unpacked_candidates(ctx)
    for base in dirs:
        roots = [d for d in sorted(glob.glob(os.path.join(base, "smali*"))) if os.path.isdir(d)]
        if roots:
            return base, roots
    return os.path.join(app_src, configured), []


def smali_index_status(ctx):
    app_src, configured, dirs = _unpacked_candidates(ctx)
    base, roots = _smali_roots(ctx)
    return {
        "app_source_dir": app_src,
        "manifest_strategy": ctx.cfg.config.get("MANIFEST_STRATEGY"),
        "erwarteter_unpacked_dir": configured,
        "vorhandene_unpacked_dirs": [os.path.basename(d) for d in dirs] or "(keine)",
        "genutzter_unpacked_dir": os.path.basename(base) if roots else None,
        "smali_roots": [os.path.basename(r) for r in roots],
        "bereit": bool(roots),
        "hinweis": ("OK — Suche laeuft direkt ueber die Dateien (kein RAM-Index noetig)."
                    if roots else
                    "Kein Smali-Baum. Frischen Pull vom Handy zuerst ENTPACKEN "
                    "(GUI: App Manager importieren -> Workspace: PREPARE_WORKSPACE). "
                    "Die Strategie (apktool/apkeditor) bestimmt den Zielordner."),
    }


def smali_search(ctx, pattern, regex=False, max_results=200, time_budget_s=40):
    """Schnelle Suche: erst Substring/Regex auf der GANZEN Datei (C-Speed) —
    Zeilennummern nur fuer die wenigen Treffer-Dateien. Zeitbudget schuetzt vor
    dem 60-s-Bridge-Timeout bei sehr grossen Smali-Baeumen."""
    import time
    base, roots = _smali_roots(ctx)
    if not roots:
        return f"Kein Smali unter {base}. Erst Workspace vorbereiten (workspace.prepare_hint)."
    rx = re.compile(pattern) if regex else None
    hits, scanned = [], 0
    t0 = time.time()
    truncated = timed_out = False
    for root in roots:
        for r, _dirs, files in os.walk(root):
            for fn in files:
                if not fn.endswith(".smali"):
                    continue
                if time.time() - t0 > time_budget_s:
                    timed_out = True
                    break
                p = os.path.join(r, fn)
                scanned += 1
                try:
                    with open(p, encoding="utf-8", errors="replace") as f:
                        content = f.read()
                except Exception:
                    continue
                if (rx.search(content) if rx else (pattern in content)):
                    for i, line in enumerate(content.splitlines(), 1):
                        if (rx.search(line) if rx else (pattern in line)):
                            hits.append(f"{os.path.relpath(p, base)}:{i}: {line.strip()[:200]}")
                            if len(hits) >= max_results:
                                truncated = True
                                break
                if truncated or timed_out:
                    break
            if truncated or timed_out:
                break
        if truncated or timed_out:
            break
    return _fmt_hits(pattern, hits, scanned, truncated, timed_out, time.time() - t0)


def _fmt_hits(pattern, hits, scanned, truncated=False, timed_out=False, secs=0.0):
    head = f"Suche '{pattern}': {len(hits)} Treffer ({scanned} Dateien, {secs:.1f}s)"
    if truncated:
        head += " [max_results erreicht]"
    if timed_out:
        head += " [Zeitbudget erreicht — ggf. praeziser suchen]"
    return head + ("\n" + "\n".join(hits) if hits else "")


def _resolve_smali_file(ctx, relpath):
    """(ok, pfad|fehlermeldung). Verhindert Pfade ausserhalb des Smali-Baums."""
    base, _ = _smali_roots(ctx)
    p = os.path.normpath(os.path.join(base, relpath))
    if not p.startswith(os.path.normpath(base)):
        return False, "[Fehler] Pfad ausserhalb des Smali-Baums."
    if not os.path.isfile(p):
        return False, f"[Fehler] nicht gefunden: {relpath}"
    return True, p


def smali_read(ctx, relpath, max_bytes=20000):
    ok, p = _resolve_smali_file(ctx, relpath)
    if not ok:
        return p
    with open(p, encoding="utf-8", errors="replace") as f:
        data = f.read(int(max_bytes) + 1)
    trunc = len(data) > int(max_bytes)
    return data[:int(max_bytes)] + ("\n... [gekuerzt]" if trunc else "")


def smali_methods(ctx, relpath):
    """Listet alle Methoden/Felder einer Klasse — wie der 'Datei'-Reiter im Smali Studio."""
    ok, p = _resolve_smali_file(ctx, relpath)
    if not ok:
        return p
    from services.smali_parser import SmaliStudioParser
    with open(p, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    outline = SmaliStudioParser.parse_outline(lines, relpath.replace("\\", "/"))
    methods = [it["signature"] for it in outline if it["type"] == "[M]"]
    fields = [it["signature"] for it in outline if it["type"] == "[F]"]
    return {"file": relpath, "method_count": len(methods), "methods": methods, "fields": fields,
            "hinweis": "Fuer einen Method-Scope-Favoriten die passende Signatur an smali.method geben."}


def smali_method(ctx, relpath, signature):
    """Extrahiert EINE Methode exakt (`.method … .end method`) anhand einer (Teil-)Signatur.
    Der zurueckgegebene `block` ist direkt als `orig` eines Method-Scope-Favoriten nutzbar."""
    ok, p = _resolve_smali_file(ctx, relpath)
    if not ok:
        return p
    with open(p, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    q = (signature or "").strip()
    starts = [i for i, l in enumerate(lines) if l.strip().startswith(".method") and q in l]
    if not starts:
        avail = [l.strip().replace(".method ", "") for l in lines if l.strip().startswith(".method")]
        return ("Keine Methode passend zu '" + q + "'. Verfuegbar:\n- " + "\n- ".join(avail[:60])) if avail \
            else f"Keine Methoden in {relpath}."
    if len(starts) > 1:
        cands = [lines[i].strip().replace(".method ", "") for i in starts]
        return "Mehrdeutig ('" + q + "') — bitte praeziser. Treffer:\n- " + "\n- ".join(cands)
    i = starts[0]
    j = i
    while j < len(lines) and lines[j].strip() != ".end method":
        j += 1
    block = "\n".join(lines[i:j + 1])
    return {"file": relpath, "start_line": i + 1, "end_line": j + 1, "scope": "method",
            "signature": lines[i].strip().replace(".method ", ""),
            "block": block,
            "hinweis": "Diesen 'block' als orig eines Method-Scope-Favoriten verwenden (favorites.add)."}


def _tmpl_standard(desc):
    return (f".class public {desc}\n"
            ".super Ljava/lang/Object;\n\n"
            ".method public constructor <init>()V\n"
            "    .locals 0\n"
            "    invoke-direct {p0}, Ljava/lang/Object;-><init>()V\n"
            "    return-void\n"
            ".end method\n")


def _tmpl_receiver(desc):
    return (f".class public {desc}\n"
            ".super Landroid/content/BroadcastReceiver;\n\n"
            ".method public constructor <init>()V\n"
            "    .locals 0\n"
            "    invoke-direct {p0}, Landroid/content/BroadcastReceiver;-><init>()V\n"
            "    return-void\n"
            ".end method\n\n"
            ".method public onReceive(Landroid/content/Context;Landroid/content/Intent;)V\n"
            "    .locals 0\n"
            "    return-void\n"
            ".end method\n")


def smali_create_class(ctx, relpath, content=None, template="standard", overwrite=False):
    """Legt eine NEUE Smali-Klasse als PATCH (Typ 'new_file') an und speichert sie als
    Favorit. Es wird KEINE Datei im Source-Baum geschrieben — die Klasse entsteht beim
    Build in der Destination. relpath z. B. 'smali/com/ghost/GhostNet.smali'. `content` =
    kompletter Smali-Text (verbatim) ODER template ('standard'|'receiver') fuer ein Geruest.
    overwrite=True ersetzt einen gleichnamigen Favoriten."""
    from services.patch_service import PatchService
    rp = relpath.replace("\\", "/")
    if not rp.endswith(".smali"):
        rp += ".smali"
    norm = PatchService.normalize_path(rp)          # entfernt smali/ bzw. smali_classesN/
    descriptor = "L" + norm[:-6] + ";" if norm.endswith(".smali") else "L" + norm + ";"
    if content is None:
        content = _tmpl_receiver(descriptor) if template == "receiver" else _tmpl_standard(descriptor)
    fav = {"name": f"NEUE KLASSE {descriptor}",
           "patches": [{"type": "new_file", "scope": "new_file", "file": rp, "edit": content}]}
    try:
        fs = ctx.favorites()
        idx = _fav_index(fs, fav["name"])
        if idx >= 0:
            if not overwrite:
                return f"[Fehler] Favorit existiert bereits: {fav['name']} (overwrite=true zum Ersetzen)."
            add_res = favorites_update(ctx, idx, fav)
        else:
            add_res = favorites_add(ctx, fav)
    except Exception as e:
        return f"[Fehler] Favorit anlegen fehlgeschlagen: {e}"
    return {"created_patch": rp, "descriptor": descriptor, "bytes": len(content),
            "favorite": fav["name"], "result": add_res,
            "hinweis": ("Als Favorit (Typ new_file) abgelegt — KEINE Source-Datei geschrieben. "
                        "In der GUI anwenden; beim Build wird die Klasse in die Destination geschrieben.")}


def smali_xref(ctx, symbol, max_results=200, time_budget_s=40):
    """Findet Aufrufer/Vorkommen eines Symbols (z. B. Methoden-/Feld-Referenz) inkl.
    umschliessender Methode — wie 'Finde Aufrufer' im Smali Studio."""
    import time
    base, roots = _smali_roots(ctx)
    if not roots:
        return "Kein Smali — Workspace zuerst entpacken."
    t0 = time.time()
    res, scanned, timed_out = [], 0, False
    for root in roots:
        for r, _dirs, files in os.walk(root):
            for fn in files:
                if not fn.endswith(".smali"):
                    continue
                if time.time() - t0 > time_budget_s:
                    timed_out = True
                    break
                p = os.path.join(r, fn)
                scanned += 1
                try:
                    with open(p, encoding="utf-8", errors="replace") as f:
                        content = f.read()
                except Exception:
                    continue
                if symbol not in content:
                    continue
                rel = os.path.relpath(p, base)
                cur = "<class>"
                for i, l in enumerate(content.splitlines(), 1):
                    s = l.strip()
                    if s.startswith(".method "):
                        cur = s.replace(".method ", "")
                    if symbol in l:
                        res.append(f"{rel}  [{cur}]  L{i}")
                        if len(res) >= max_results:
                            return _xref_fmt(symbol, res, scanned, True, False)
                if timed_out:
                    break
            if timed_out:
                break
        if timed_out:
            break
    return _xref_fmt(symbol, res, scanned, False, timed_out)


def _xref_fmt(symbol, res, scanned, truncated, timed_out):
    head = f"XRef '{symbol}': {len(res)} Treffer ({scanned} Dateien)"
    if truncated:
        head += " [max_results erreicht]"
    if timed_out:
        head += " [Zeitbudget erreicht]"
    return head + ("\n" + "\n".join(res) if res else "")


def smali_callgraph(ctx, symbol, depth=1, max_nodes=200):
    """Erstellt ausgehende Callees (bis depth=1..3) und eingehende Caller einer Smali-Methode."""
    from services.callgraph_service import is_system_api
    base, roots = _smali_roots(ctx)
    if not roots:
        return {"ok": False, "hinweis": "Kein Smali-Baum vorhanden."}
    sym = str(symbol or "").strip()
    if not sym:
        return {"ok": False, "hinweis": "symbol fehlt (z. B. Lpkg/Cls;->methode oder Methodenname)."}

    max_d = max(1, min(3, int(depth or 1)))
    cap = max(10, int(max_nodes or 200))
    invoke_re = re.compile(r'invoke-\w+(?:/range)?\s+\{[^}]*\},\s*(L[^;\s]+;->[^\s(]+\([^\n]*)')
    cls_hdr_re = re.compile(r'^\s*\.class\s+.*?(L[^;\s]+;)')

    methods_by_id = {}
    ids_by_name = {}
    scanned = 0
    for rdir in roots:
        for dirpath, _dirs, files in os.walk(rdir):
            for fn in files:
                if not fn.endswith(".smali"):
                    continue
                scanned += 1
                fp = os.path.join(dirpath, fn)
                rel = os.path.relpath(fp, base).replace("\\", "/")
                try:
                    lines = open(fp, "r", encoding="utf-8", errors="replace").read().splitlines()
                except Exception:
                    continue
                cls_desc = None
                cur_id = None
                cur_callees = []
                for ln in lines:
                    s = ln.strip()
                    if cls_desc is None and s.startswith(".class "):
                        cm = cls_hdr_re.match(s)
                        if cm:
                            cls_desc = cm.group(1)
                    elif s.startswith(".method "):
                        sig_tok = s.split()[-1] if s.split() else ""
                        if cls_desc and "(" in sig_tok:
                            cur_id = f"{cls_desc}->{sig_tok}"
                            cur_callees = []
                    elif s.startswith(".end method") and cur_id:
                        methods_by_id[cur_id] = {"id": cur_id, "file": rel, "callees": cur_callees}
                        mname = cur_id.split("->", 1)[1].split("(", 1)[0]
                        ids_by_name.setdefault(mname, []).append(cur_id)
                        cur_id = None
                        cur_callees = []
                    elif cur_id:
                        im = invoke_re.search(s)
                        if im:
                            tgt = im.group(1).strip()
                            if tgt not in cur_callees:
                                cur_callees.append(tgt)

    root_ids = []
    if sym in methods_by_id:
        root_ids = [sym]
    elif "->" in sym:
        root_ids = [mid for mid in methods_by_id if mid.startswith(sym)]
    else:
        for mid in methods_by_id:
            if mid.split("->", 1)[1].split("(", 1)[0] == sym:
                root_ids.append(mid)
        if not root_ids:
            root_ids = [mid for mid in methods_by_id if sym in mid]
    root_ids = root_ids[:20]

    edges = []
    visited = set()
    frontier = list(root_ids)
    for _lvl in range(max_d):
        nxt = []
        for mid in frontier:
            if mid in visited:
                continue
            visited.add(mid)
            info = methods_by_id.get(mid)
            if not info:
                continue
            for cal in info["callees"]:
                edges.append({"from": mid, "to": cal, "system_api": is_system_api(cal)})
                if len(edges) >= cap:
                    break
                if cal in methods_by_id and cal not in visited:
                    nxt.append(cal)
            if len(edges) >= cap:
                break
        frontier = nxt
        if not frontier or len(edges) >= cap:
            break

    root_set = set(root_ids)
    callers = []
    for mid, info in methods_by_id.items():
        if any((c in root_set) or (sym in c) for c in info["callees"]):
            callers.append({"id": mid, "file": info["file"]})
            if len(callers) >= cap:
                break

    return {
        "ok": True,
        "symbol": sym,
        "depth": max_d,
        "scanned_files": scanned,
        "indexed_methods": len(methods_by_id),
        "roots": root_ids,
        "edges": edges,
        "callers": callers,
    }


def app_manifest_info(ctx, app_root=None):
    """Liest AndroidManifest.xml (Activities, Launcher, Permissions, Exported, Deep-Links) & APK-Signatur-Certs."""
    import hashlib as _hl
    import zipfile as _zf
    from xml.etree import ElementTree as _ET
    from core.java.manifest import ANDROID_NS, parse_manifest

    root = _resolve_app_root_for_extract(ctx, app_root)
    if not root:
        return {"ok": False, "hinweis": "Kein entpackter App-Root gefunden (app_root angeben oder APK entpacken)."}

    m_path = os.path.join(root, "AndroidManifest.xml") if os.path.isdir(root) else root
    if not os.path.isfile(m_path):
        return {"ok": False, "hinweis": f"Keine AndroidManifest.xml unter {root} gefunden."}

    parsed = parse_manifest(root).to_dict()
    ans = "{%s}" % ANDROID_NS
    xml_root = _ET.parse(m_path).getroot()
    app_el = xml_root.find("application")
    uses_sdk = xml_root.find("uses-sdk")

    permissions = []
    for pe in xml_root.findall("uses-permission") + xml_root.findall("uses-permission-sdk-23"):
        pn = pe.get(ans + "name") or pe.get("name")
        if pn and pn not in permissions:
            permissions.append(pn)

    exported = []
    if app_el is not None:
        for tag in ("activity", "activity-alias", "service", "receiver", "provider"):
            for comp in app_el.findall(tag):
                cname = comp.get(ans + "name") or comp.get("name") or ""
                exp = comp.get(ans + "exported")
                has_filter = bool(comp.findall("intent-filter"))
                if exp == "true" or (exp is None and has_filter):
                    exported.append({"kind": tag, "name": cname, "explicit": (exp == "true"), "intent_filter": has_filter})

    apk_certs = []
    search_dirs = [os.path.dirname(os.path.abspath(m_path)), os.path.dirname(os.path.dirname(os.path.abspath(m_path)))]
    seen_apks = set()
    for sd in search_dirs:
        if not sd or not os.path.isdir(sd):
            continue
        for fn in sorted(os.listdir(sd)):
            if not fn.lower().endswith(".apk"):
                continue
            ap = os.path.join(sd, fn)
            if ap in seen_apks or not os.path.isfile(ap):
                continue
            seen_apks.add(ap)
            try:
                with _zf.ZipFile(ap, "r") as zf:
                    for zn in zf.namelist():
                        up = zn.upper()
                        if up.startswith("META-INF/") and up.endswith((".RSA", ".DSA", ".EC")):
                            raw_cert = zf.read(zn)
                            apk_certs.append({
                                "apk": fn,
                                "entry": zn,
                                "bytes": len(raw_cert),
                                "sha256": _hl.sha256(raw_cert).hexdigest(),
                            })
            except Exception:
                pass
            if apk_certs:
                break

    return {
        "ok": True,
        "manifest_path": m_path,
        "version_code": xml_root.get(ans + "versionCode") or xml_root.get("versionCode"),
        "version_name": xml_root.get(ans + "versionName") or xml_root.get("versionName"),
        "min_sdk": (uses_sdk.get(ans + "minSdkVersion") if uses_sdk is not None else None),
        "target_sdk": (uses_sdk.get(ans + "targetSdkVersion") if uses_sdk is not None else None),
        "debuggable": (app_el.get(ans + "debuggable") == "true") if app_el is not None else False,
        "allow_backup": (app_el.get(ans + "allowBackup") if app_el is not None else None),
        "network_security_config": (app_el.get(ans + "networkSecurityConfig") if app_el is not None else None),
        "permissions": permissions,
        "exported_components": exported,
        "apk_certs": apk_certs,
        **parsed,
    }


# ============================================================ C: LibForge
def _strip_ver(text):
    import re as _re
    return _re.sub(r'^\[v[^\]]*\]\s*', '', (text or '')).strip()


def _compose_desc(version, text):
    """Version in die description spiegeln: '[v<version>] <text>'."""
    return f"[v{version}] {_strip_ver(text)}".strip()


def lib_list(ctx):
    mgr = ctx.libs()
    import os as _os, hashlib as _hl
    def _meta(l):
        sp = mgr.abspath(l.so_relpath) if l.so_relpath else ""
        if sp and _os.path.exists(sp):
            try:
                b = open(sp, "rb").read()
                return {"so_exists": True, "size": len(b),
                        "sha256": _hl.sha256(b).hexdigest()[:12],
                        "so_mtime": int(_os.path.getmtime(sp))}
            except Exception:
                pass
        return {"so_exists": False, "size": 0, "sha256": "", "so_mtime": 0}
    out = [{
        "id": l.id, "name": l.name, "version": getattr(l, "version", "1.0"),
        "kind": getattr(l, "output_kind", "shared"),
        "abi": l.abi, "active": l.active, "built": l.last_build_ok, "so": l.so_relpath,
        "description": getattr(l, "description", ""),
        **_meta(l),
    } for l in mgr.get_all()]
    return out or "keine Libs angelegt"


def lib_create(ctx, name, source_code=None, version="1.0", description="", source_file=None, output_kind=None):
    mgr = ctx.libs()
    warn = "" if _strip_ver(description) else " [WARN] ohne description angelegt — Version+Zweck angeben."
    if source_file and source_code is None:
        try:
            with open(source_file, "r", encoding="utf-8") as f:
                source_code = f.read()
        except Exception as e:
            return f"[Fehler] source_file nicht lesbar: {e}"
    lib = mgr.create(name=name, origin="built")
    if source_code is not None:
        lib.source_code = source_code
    if output_kind:
        lib.output_kind = output_kind
    lib.version = str(version)
    lib.description = _compose_desc(version, description)
    mgr.update(lib)
    return (f"angelegt: {name} v{version} (id={lib.id}) — {lib.description}. "
            f"Build in der GUI (LibForge).{warn}")


def lib_update(ctx, lib_id, source_code=None, abi=None, api_level=None,
               link_libs=None, output_kind=None, active=None, name=None,
               version=None, description=None, source_file=None):
    mgr = ctx.libs()
    lib = mgr.get(lib_id)
    if not lib:
        return f"[Fehler] keine Lib mit id={lib_id}"
    if source_file and source_code is None:
        try:
            with open(source_file, "r", encoding="utf-8") as f:
                source_code = f.read()
        except Exception as e:
            return f"[Fehler] source_file nicht lesbar: {e}"
    content_change = source_code is not None
    warn = ""
    if content_change:
        old_ver = getattr(lib, "version", "1.0")
        if version is None or str(version) == str(old_ver):
            warn += (f" [WARN] Quelle geaendert, aber Version nicht erhoeht (v{old_ver}). "
                     f"Policy: Bugfix -> Version bumpen; neuer Pfad -> lib.create. Trotzdem gespeichert.")
        if description is None:
            warn += " [WARN] description nicht aktualisiert."
    if source_code is not None:
        lib.source_code = source_code
    if abi is not None:
        lib.abi = abi
    if api_level is not None:
        lib.api_level = int(api_level)
    if link_libs is not None:
        lib.link_libs = link_libs
    if output_kind is not None:
        lib.output_kind = output_kind
    if name is not None:
        lib.name = name
    if version is not None:
        lib.version = str(version)
    if version is not None or description is not None:
        eff_ver = str(version) if version is not None else getattr(lib, "version", "1.0")
        base = description if description is not None else lib.description
        lib.description = _compose_desc(eff_ver, base)
    mgr.update(lib)
    _auto = []
    if active is not None:
        if bool(active) and not (lib.so_relpath and os.path.exists(mgr.abspath(lib.so_relpath))):
            warn += " [WARN] active=True ignoriert: keine kompilierte .so vorhanden (Fix 1.9)."
        else:
            _auto = mgr.set_active(lib.id, bool(active))
    _tail = f" (auto-deaktiviert: {len(_auto)})" if _auto else ""
    return f"aktualisiert: {lib.name} v{getattr(lib,'version','?')} (id={lib.id}) — {lib.description}{warn}{_tail}"


def lib_import_so(ctx, name, so_path, version="1.0", description="", abi="arm64-v8a"):
    """Importiert eine fertige .so ohne Quellcode/NDK-Build direkt in LibForge."""
    src = os.path.abspath(str(so_path or "").strip())
    if not src or not os.path.isfile(src):
        return {"ok": False, "hinweis": f".so-Datei nicht gefunden: {so_path!r}"}
    mgr = ctx.libs()
    desc = _compose_desc(version, description or f"Importiert aus {os.path.basename(src)}")
    lib = mgr.import_so(name=str(name or "").strip(), src_path=src, description=desc, abi=abi or "arm64-v8a")
    lib.version = str(version or "1.0")
    mgr.save()
    return {
        "ok": True,
        "id": lib.id,
        "name": lib.name,
        "version": lib.version,
        "abi": lib.abi,
        "active": bool(lib.active),
        "so": lib.so_relpath,
        "description": lib.description,
    }


def lib_set_active(ctx, ref, active=True):
    """Schaltet eine LibForge-Lib (Name oder ID) aktiv oder inaktiv."""
    mgr, lib = _find_lib(ctx, ref)
    if not lib:
        return {"ok": False, "hinweis": f"Lib nicht gefunden: {ref!r}"}
    want = bool(active)
    if want and not (lib.so_relpath and os.path.exists(mgr.abspath(lib.so_relpath))):
        return {"ok": False, "id": lib.id, "name": lib.name,
                "hinweis": "Keine kompilierte/importierte .so vorhanden — Aktivierung nicht moeglich."}
    deact = mgr.set_active(lib.id, want) or []
    return {
        "ok": True,
        "id": lib.id,
        "name": lib.name,
        "active": bool(lib.active),
        "auto_deactivated": [getattr(x, "id", x) for x in deact],
    }


# ============================================================ D: Patches/Favoriten
_FAV_FIELDS = (
    "type", "file", "orig", "edit", "patch", "ram", "base", "target", "source", "scope",
    "abi", "vaddr", "func", "orig_size", "mode", "auto_nop", "orig_hex", "aob", "anchor_strings", "rel_offset",
    "name", "version", "description", "origin", "source_code", "api_level", "link_libs",
    "extra_flags", "output_kind", "so_relpath",
)


def _fav_index(fav_service, ref):
    """ref = Index (int/str) ODER Name -> Listenindex oder -1."""
    try:
        idx = int(ref)
        if 0 <= idx < len(fav_service.favs):
            return idx
    except (ValueError, TypeError):
        pass
    for i, f in enumerate(fav_service.favs):
        if f.get("name") == ref:
            return i
    return -1


def _normalize_favorite(favorite):
    """Akzeptiert JSON-String oder dict; erlaubt Einzel-Patch-Kurzform und liefert
    {name, patches:[{type: smali|hex|lib_replace, ...}, ...]}."""
    if isinstance(favorite, str):
        favorite = json.loads(favorite)
    if not isinstance(favorite, dict):
        raise ValueError("favorite muss ein Objekt/JSON sein")
    if "patches" not in favorite:
        p = {k: favorite[k] for k in favorite if k in _FAV_FIELDS}
        p.setdefault("type", "smali")
        favorite = {"name": favorite.get("name", "(ohne Name)"), "patches": [p]}
    favorite.setdefault("name", "(ohne Name)")
    if not isinstance(favorite.get("patches"), list) or not favorite["patches"]:
        raise ValueError("favorite braucht mind. einen patch in 'patches'")
    for p in favorite["patches"]:
        t = p.get("type", "smali")
        p["type"] = t
        if t not in ("smali", "hex", "lib_replace", "new_file", "libcarve", "libforge"):
            raise ValueError(f"unbekannter patch type: {t}")
        if t == "smali":
            p.setdefault("scope", "method")
        if t == "libcarve":
            p.setdefault("mode", "asm")
            p.setdefault("abi", "arm64-v8a")
            p.setdefault("auto_nop", True)
        if t == "libforge":
            p.setdefault("abi", "arm64-v8a")
            p.setdefault("output_kind", "shared")
            p.setdefault("origin", "built")
            p.setdefault("link_libs", "log dl")
            if not p.get("name"):
                raise ValueError("libforge patch braucht 'name'")
        if t == "new_file":
            p.setdefault("scope", "new_file")
            if not p.get("file") or not p.get("edit"):
                raise ValueError("new_file patch braucht 'file' und 'edit' (Inhalt)")
    favorite["version"] = str(favorite.get("version", "1.0"))
    favorite["description"] = _compose_desc(favorite["version"], favorite.get("description", ""))
    return favorite


def favorites_list(ctx, tool_type: str = ""):
    favs = ctx.favorites().favs
    if not favs:
        return "keine Favoriten"
    flt = str(tool_type or "").strip().lower()
    out = []
    for i, f in enumerate(favs):
        patches = f.get("patches", [f])
        types = [str(p.get("type", "smali") or "smali") for p in patches]
        canon = {("smali" if t == "new_file" else t.lower()) for t in types}
        is_multi = len(canon) > 1
        if flt:
            if flt in ("kombi", "multi", "multi-tool"):
                if not is_multi:
                    continue
            else:
                norm_flt = "smali" if flt in ("smali", "new_file") else ("lib_replace" if flt in ("lib-replace", "lib_replace") else flt)
                if norm_flt not in canon and flt not in [t.lower() for t in types]:
                    continue
        out.append({"index": i, "name": f.get("name", "(ohne Name)"),
                    "version": f.get("version", "1.0"),
                    "description": f.get("description", ""),
                    "patch_count": len(patches),
                    "types": types,
                    "is_multi_tool": is_multi})
    return out


def favorites_get(ctx, ref):
    fs = ctx.favorites()
    i = _fav_index(fs, ref)
    if i < 0:
        return f"[Fehler] Favorit '{ref}' nicht gefunden."
    return fs.favs[i]


def favorites_add(ctx, favorite):
    fav = _normalize_favorite(favorite)
    warn = "" if _strip_ver(fav.get("description", "")) else " [WARN] ohne description — Version+Zweck angeben."
    ctx.favorites().add_favorite(fav)
    return (f"Favorit '{fav['name']}' v{fav['version']} hinzugefuegt "
            f"({len(fav['patches'])} Patch(es): {[p['type'] for p in fav['patches']]}).{warn}")


def favorites_update(ctx, ref, favorite):
    fs = ctx.favorites()
    i = _fav_index(fs, ref)
    if i < 0:
        return f"[Fehler] Favorit '{ref}' nicht gefunden."
    old_ver = str((fs.favs[i] or {}).get("version", "1.0"))
    fav = _normalize_favorite(favorite)
    warn = ""
    if str(fav["version"]) == old_ver:
        warn = (f" [WARN] Version nicht erhoeht (v{old_ver}). "
                f"Policy: Bugfix -> Version bumpen; neuer Pfad -> favorites.add. Trotzdem gespeichert.")
    fs.favs[i] = fav
    fs.save_favs()
    return f"Favorit [{i}] '{fav['name']}' v{fav['version']} aktualisiert ({len(fav['patches'])} Patch(es)).{warn}"


def favorites_delete(ctx, ref):
    fs = ctx.favorites()
    i = _fav_index(fs, ref)
    if i < 0:
        return f"[Fehler] Favorit '{ref}' nicht gefunden."
    name = fs.favs[i].get("name", "(ohne Name)")
    fs.delete_favorite(i)
    return f"Favorit [{i}] '{name}' geloescht."


def favorites_append(ctx, ref, patch_json):
    """Hängt einen oder mehrere Patches an einen bestehenden Favoriten an (Multi-Tool)."""
    fs = ctx.favorites()
    i = _fav_index(fs, ref)
    if i < 0:
        return f"[Fehler] Favorit '{ref}' nicht gefunden."
    incoming = _normalize_favorite(patch_json)
    fav = fs.favs[i]
    if not isinstance(fav.get("patches"), list):
        fav["patches"] = [{k: fav[k] for k in fav if k in _FAV_FIELDS}]
        for k in ("file", "orig", "edit"):
            fav.pop(k, None)
    for np in incoming["patches"]:
        fav["patches"].append(dict(np))
    fs.save_favs()
    types = [p.get("type", "smali") for p in fav["patches"]]
    return {
        "index": i,
        "name": fav.get("name", "(ohne Name)"),
        "appended": len(incoming["patches"]),
        "patch_count": len(fav["patches"]),
        "types": types,
        "is_multi_tool": len(set("smali" if t == "new_file" else t for t in types)) > 1,
    }


def favorites_combine(ctx, refs, name, version="1.0", description=""):
    """Kombiniert >= 2 bestehende Favoriten zu einem neuen Multi-Tool-Favoriten."""
    fs = ctx.favorites()
    if isinstance(refs, str):
        refs_list = json.loads(refs)
    else:
        refs_list = list(refs or [])
    if len(refs_list) < 2:
        return "[Fehler] Mindestens 2 Favoriten-Referenzen (Indizes oder Namen) angeben."
    combined = []
    resolved_names = []
    for r in refs_list:
        idx = _fav_index(fs, r)
        if idx < 0:
            return f"[Fehler] Favorit '{r}' nicht gefunden."
        f = fs.favs[idx]
        resolved_names.append(f.get("name", f"#{idx}"))
        for p in f.get("patches", [f]):
            combined.append(dict(p))
    ver = str(version or "1.0")
    desc = description or f"Multi-Tool-Kombi aus {', '.join(resolved_names)}"
    fav = {
        "name": str(name or "").strip() or f"Kombi ({len(resolved_names)})",
        "version": ver,
        "description": _compose_desc(ver, desc),
        "patches": combined,
    }
    fs.add_favorite(fav)
    types = [p.get("type", "smali") for p in combined]
    return {
        "index": len(fs.favs) - 1,
        "name": fav["name"],
        "version": ver,
        "patch_count": len(combined),
        "types": types,
        "is_multi_tool": len(set("smali" if t == "new_file" else t for t in types)) > 1,
    }


_RAM_CACHE = None


def _smali_ram_cache(ctx):
    global _RAM_CACHE
    if _RAM_CACHE is not None:
        return _RAM_CACHE
    base, roots = _smali_roots(ctx)
    cache = []
    for root in roots:
        for r, _dirs, files in os.walk(root):
            for fn in files:
                if fn.endswith((".smali", ".xml")):
                    p = os.path.join(r, fn)
                    try:
                        with open(p, encoding="utf-8", errors="replace") as f:
                            cache.append((os.path.relpath(p, base), f.read()))
                    except Exception:
                        pass
    _RAM_CACHE = cache
    return cache


def patch_evaluate(ctx, file, orig, scope="method"):
    cache = _smali_ram_cache(ctx)
    patch = {"file": file, "orig": orig, "scope": scope}
    return PatchService.evaluate_smali_patch(patch, cache, [])


# ---- Aktive Session-Patches (headless Apply/Remove) ----------------------------
# SessionState.active_* sind prozesslokal; die Pipeline-Steps (SmartPatchStep,
# AnchorPatchStep, InjectCustomLibsStep) lesen sie beim BUILD_NATIVE direkt aus.
# Da pipeline.run die Engine IM MCP-Prozess ausfuehrt, ist das Fuellen dieser
# Listen das headless-Aequivalent zu „Favoriten anwenden" in der GUI
# (vgl. ui/controllers/favorite_patches_controller.start_batch_fav).

def _apply_one_patch(ctx, p, ram_cache):
    """Wendet EINEN Patch auf die aktive Session an und liefert ein Status-dict.
    Smali/new_file werden gegen den Baum evaluiert (orig kommt aus dem Treffer),
    hex/lib_replace werden direkt uebernommen. Idempotent (already_active)."""
    ptype = p.get("type", "smali")

    if ptype == "lib_replace":
        target = (p.get("target") or "").strip()
        source = (p.get("source") or "").strip()
        if not target or not source:
            return {"ok": False, "kind": "lib_replace", "reason": "target/source fehlt"}
        entry = {"type": "lib_replace", "target": target, "source": source}
        if any(e.get("target") == target and e.get("source") == source
               for e in SessionState.active_lib_replacements):
            return {"ok": True, "kind": "lib_replace", "status": "already_active", "ref": target}
        SessionState.active_lib_replacements.append(entry)
        return {"ok": True, "kind": "lib_replace", "status": "applied", "ref": target}

    if ptype == "hex":
        entry = {"type": "hex"}
        for k in ("file", "ram", "base", "orig", "patch"):
            entry[k] = p.get(k, "")
        if not (entry.get("patch") or "").strip():
            return {"ok": False, "kind": "hex", "reason": "hex-bytes ('patch') fehlt"}
        if any(e.get("ram") == entry["ram"] and e.get("patch") == entry["patch"]
               and e.get("file") == entry["file"] for e in SessionState.active_hex_patches):
            return {"ok": True, "kind": "hex", "status": "already_active", "ref": entry.get("ram", "")}
        SessionState.active_hex_patches.append(entry)
        return {"ok": True, "kind": "hex", "status": "applied", "ref": entry.get("ram", "")}

    if ptype == "libcarve":
        entry = {
            "type": "libcarve",
            "file": os.path.basename(str(p.get("file") or "").strip()),
            "abi": str(p.get("abi") or "arm64-v8a"),
            "vaddr": str(p.get("vaddr") or ""),
            "func": str(p.get("func") or ""),
            "orig_size": int(p.get("orig_size") or 0),
            "mode": str(p.get("mode") or "asm").lower(),
            "auto_nop": bool(p.get("auto_nop", True)),
            "orig": str(p.get("orig") or ""),
            "edit": str(p.get("edit") or p.get("patch") or ""),
            "orig_hex": str(p.get("orig_hex") or ""),
            "aob": str(p.get("aob") or ""),
            "anchor_strings": list(p.get("anchor_strings") or ()),
            "rel_offset": int(p.get("rel_offset") or 0),
        }
        if not entry["file"] or not entry["edit"]:
            return {"ok": False, "kind": "libcarve", "reason": "file oder edit fehlt"}
        # Pattern-Match gegen Ziel-.so falls AOB vorhanden und Zieladresse aufgelöst werden soll
        if entry["aob"] and ctx is not None:
            try:
                so_p, img, idx = _lc_resolve_so_and_index(ctx, entry["file"], need_index=True)
                if img is not None and idx is not None:
                    from services.libcarve_pattern_service import LibCarvePatternService
                    m = LibCarvePatternService.match_patch(img, idx, entry)
                    if m.get("ok") and m.get("vaddr") is not None:
                        entry["vaddr"] = f"0x{m['vaddr']:x}"
                        entry["func"] = idx.func_at(m["vaddr"]) or entry["func"]
            except Exception:
                pass
        if not entry["vaddr"]:
            return {"ok": False, "kind": "libcarve", "reason": "vaddr fehlt / AOB nicht eindeutig gematcht"}
        if any(
            e.get("file") == entry["file"] and e.get("vaddr") == entry["vaddr"]
            and e.get("edit") == entry["edit"]
            for e in SessionState.active_libcarve_patches
        ):
            return {"ok": True, "kind": "libcarve", "status": "already_active", "ref": f"{entry['file']}@{entry['vaddr']}"}
        SessionState.active_libcarve_patches.append(entry)
        return {"ok": True, "kind": "libcarve", "status": "applied", "file": entry["file"], "vaddr": entry["vaddr"]}

    if ptype == "libforge":
        lname = str(p.get("name") or "").strip()
        if not lname or ctx is None:
            return {"ok": False, "kind": "libforge", "reason": "name oder Kontext fehlt"}
        mgr = ctx.libs()
        lib = next((l for l in mgr.get_all() if l.name == lname), None)
        if lib is None:
            lib = mgr.create(name=lname, origin=str(p.get("origin") or "built"))
        lib.version = str(p.get("version") or "1.0")
        lib.description = str(p.get("description") or "")
        if lib.origin != "imported":
            lib.source_code = str(p.get("source_code") or lib.source_code or "")
        lib.abi = str(p.get("abi") or "arm64-v8a")
        try:
            lib.api_level = int(p.get("api_level") or 30)
        except (ValueError, TypeError):
            lib.api_level = 30
        lib.link_libs = str(p.get("link_libs") or "log dl")
        lib.extra_flags = str(p.get("extra_flags") or "")
        lib.output_kind = str(p.get("output_kind") or "shared")
        if p.get("so_relpath"):
            lib.so_relpath = str(p.get("so_relpath"))
        mgr.update(lib)
        activated = False
        if lib.so_relpath and os.path.exists(mgr.abspath(lib.so_relpath)):
            mgr.set_active(lib.id, True)
            activated = True
        return {"ok": True, "kind": "libforge", "status": "applied", "name": lib.name, "active": activated}

    # smali / new_file -> gegen den (pristinen) Source-Baum evaluieren
    res = PatchService.evaluate_smali_patch(p, ram_cache, SessionState.active_smali_patches)
    if not res.get("success"):
        return {"ok": False, "kind": ptype, "reason": res.get("reason", "unbekannt"),
                "file": p.get("file", "")}
    rtype = res.get("type")
    if rtype == "already_applied":
        return {"ok": True, "kind": ptype, "status": "already_active", "file": p.get("file", "")}
    cp = dict(p)
    if res.get("file"):
        cp["file"] = res["file"]
    if "orig" in res:
        cp["orig"] = res["orig"]
    if ptype == "new_file" or rtype == "new_file":
        cp["type"] = "new_file"
        cp.setdefault("scope", "new_file")
    else:
        cp["type"] = "smali"
        cp.setdefault("scope", "method")
    SessionState.active_smali_patches.append(cp)
    return {"ok": True, "kind": cp["type"], "status": "applied",
            "match": rtype, "file": cp.get("file", "")}


def _active_view():
    """Kombinierte, stabil indizierte Sicht auf die aktive Patch-Liste.
    Reihenfolge = SessionState.get_all_patches(): hex, lib_replace, libcarve, smali."""
    view = []
    gi = 0
    for e in SessionState.active_hex_patches:
        view.append((gi, "hex", SessionState.active_hex_patches, e)); gi += 1
    for e in SessionState.active_lib_replacements:
        view.append((gi, "lib_replace", SessionState.active_lib_replacements, e)); gi += 1
    for e in SessionState.active_libcarve_patches:
        view.append((gi, "libcarve", SessionState.active_libcarve_patches, e)); gi += 1
    for e in SessionState.active_smali_patches:
        kind = "new_file" if (e.get("scope") == "new_file" or e.get("type") == "new_file") else "smali"
        view.append((gi, kind, SessionState.active_smali_patches, e)); gi += 1
    return view


def _persist_active(ctx) -> bool:
    """Aktive Liste in data/active_session.json schreiben (konfliktsicher), damit
    der GUI-Prozess sie im Fenster 'aktive Patches' spiegeln kann und sie Neustarts
    ueberlebt. True bei Erfolg."""
    try:
        ctx.active_session().save(SessionState.to_patch_list())
        return True
    except Exception as e:
        print(f"[tools] active_session.save fehlgeschlagen: {e}")
        return False


def patch_active_list(ctx):
    """Aktive Session-Patches auflisten (globaler Index -> patch.remove)."""
    out = []
    for gi, kind, _lst, e in _active_view():
        item = {"index": gi, "kind": kind}
        if kind == "lib_replace":
            item["target"] = e.get("target", "")
            item["source"] = e.get("source", "")
        elif kind == "hex":
            item["file"] = e.get("file", "")
            item["ram"] = e.get("ram", "")
            item["patch"] = e.get("patch", "")
        elif kind == "libcarve":
            item["file"] = e.get("file", "")
            item["vaddr"] = e.get("vaddr", "")
            item["func"] = e.get("func", "")
            item["mode"] = e.get("mode", "asm")
        else:
            item["file"] = e.get("file", "")
            item["scope"] = e.get("scope", "method")
        out.append(item)
    return {"active_count": len(out),
            "counts": {"hex": len(SessionState.active_hex_patches),
                       "lib_replace": len(SessionState.active_lib_replacements),
                       "libcarve": len(SessionState.active_libcarve_patches),
                       "smali": len(SessionState.active_smali_patches)},
            "patches": out}


def patch_apply(ctx, patch_json):
    """Patch(es) auf die aktive Session anwenden. patch_json = EIN Patch ODER {patches:[...]}."""
    try:
        fav = _normalize_favorite(patch_json)
    except Exception as e:
        return f"[Fehler] Patch ungueltig: {e}"
    needs_smali = any(p.get("type", "smali") in ("smali", "new_file") for p in fav["patches"])
    ram_cache = _smali_ram_cache(ctx) if needs_smali else []
    results = [_apply_one_patch(ctx, p, ram_cache) for p in fav["patches"]]
    ok = sum(1 for r in results if r.get("ok"))
    persisted = _persist_active(ctx)
    return {"applied_ok": ok, "failed": sum(1 for r in results if not r.get("ok")),
            "results": results, "active_count": len(SessionState.get_all_patches()),
            "persisted": persisted}


def patch_apply_favorite(ctx, ref):
    """Kompletten Favoriten/Bundle (Index oder Name) auf die aktive Session anwenden."""
    fs = ctx.favorites()
    i = _fav_index(fs, ref)
    if i < 0:
        return f"[Fehler] Favorit '{ref}' nicht gefunden."
    fav = fs.favs[i]
    patches = fav.get("patches", [fav])
    needs_smali = any(p.get("type", "smali") in ("smali", "new_file") for p in patches)
    ram_cache = _smali_ram_cache(ctx) if needs_smali else []
    results = [_apply_one_patch(ctx, p, ram_cache) for p in patches]
    ok = sum(1 for r in results if r.get("ok"))
    persisted = _persist_active(ctx)
    return {"favorite": fav.get("name", "(ohne Name)"), "version": fav.get("version", "1.0"),
            "applied_ok": ok, "failed": sum(1 for r in results if not r.get("ok")),
            "results": results, "active_count": len(SessionState.get_all_patches()),
            "persisted": persisted}


def patch_remove(ctx, index):
    """Einen angewendeten Patch aus der aktiven Liste streichen (NICHT aus den Favoriten)."""
    try:
        gi = int(index)
    except (ValueError, TypeError):
        return "[Fehler] index muss eine Zahl sein (siehe patch.active_list)."
    for cur_gi, kind, lst, e in _active_view():
        if cur_gi == gi:
            for j, item in enumerate(lst):
                if item is e:
                    del lst[j]
                    break
            ref = e.get("target") if kind == "lib_replace" else e.get("file", "")
            persisted = _persist_active(ctx)
            return {"removed": True, "index": gi, "kind": kind, "ref": ref,
                    "active_count": len(SessionState.get_all_patches()),
                    "persisted": persisted}
    return f"[Fehler] Kein aktiver Patch mit index={gi} (siehe patch.active_list)."


def patch_clear_active(ctx):
    """Alle aktiven Session-Patches verwerfen (Favoriten bleiben unberuehrt)."""
    n = len(SessionState.get_all_patches())
    SessionState.clear_session()
    persisted = _persist_active(ctx)
    return {"cleared": n, "active_count": 0, "persisted": persisted}


def patch_fuzzy_match(ctx, orig="", file="", favorite_ref="", deep_search=False,
                      threshold=0.80, max_results=20):
    """Findet verschobene Smali-Methoden/Patches nach App-Update per FuzzingEngine wieder."""
    import difflib
    from core.fuzzing_engine import FuzzingEngine

    target_file = str(file or "").replace("\\", "/")
    orig_code = str(orig or "")
    if favorite_ref and (not orig_code or not target_file):
        fs = ctx.favorites()
        fi = _fav_index(fs, favorite_ref)
        if fi < 0:
            return {"ok": False, "hinweis": f"Favorit '{favorite_ref}' nicht gefunden."}
        fav = fs.favs[fi]
        for p in (fav.get("patches") or [fav]):
            if (p.get("type") or "smali") == "smali" and p.get("orig"):
                orig_code = orig_code or p.get("orig", "")
                target_file = target_file or str(p.get("file", "")).replace("\\", "/")
                break
    if not orig_code.strip():
        return {"ok": False, "hinweis": "orig (Smali-Code) oder favorite_ref mit smali-Patch erforderlich."}

    base, roots = _smali_roots(ctx)
    if not roots:
        return {"ok": False, "hinweis": "Kein Smali-Baum vorhanden."}
    ram_cache = []
    for rdir in roots:
        for dirpath, _dirs, files in os.walk(rdir):
            for fn in files:
                if not fn.endswith(".smali"):
                    continue
                fp = os.path.join(dirpath, fn)
                rel = os.path.relpath(fp, base).replace("\\", "/")
                try:
                    ram_cache.append((rel, open(fp, "r", encoding="utf-8", errors="replace").read()))
                except Exception:
                    pass
    m = re.search(r'\.method.*? ([<a-zA-Z0-9_$\-]+)\(', orig_code)
    if m and not deep_search:
        cands = FuzzingEngine.fuzz_by_method_name(m.group(1), ram_cache)
    else:
        cands = FuzzingEngine.fuzz_by_content_snippet(
            orig_code, target_file, ram_cache, threshold=float(threshold), deep_search=bool(deep_search)
        )

    norm_orig = FuzzingEngine._normalize_smali(orig_code)
    scored = []
    for c in cands:
        nc = FuzzingEngine._normalize_smali(c.get("code", ""))
        sim = round(difflib.SequenceMatcher(None, norm_orig, nc).ratio(), 4) if norm_orig else 0.0
        scored.append({
            "file": str(c.get("file", "")).replace("\\", "/"),
            "sig": c.get("sig", ""),
            "similarity": sim,
            "code": c.get("code", "")[:4000],
        })
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    lim = max(1, int(max_results or 20))
    return {
        "ok": True,
        "deep_search": bool(deep_search),
        "target_file": target_file,
        "count": len(scored),
        "candidates": scored[:lim],
    }


# ============================================================ F: Device (info=O)
def device_info(ctx):
    from services.adb_devices import list_devices, resolve_serial, apply_android_serial
    adb = _adb(ctx)
    apply_android_serial(ctx.cfg, adb, probe=True)
    devs = list_devices(adb)
    online = [d for d in devs if d["state"] == "device"]
    sel = resolve_serial(ctx.cfg, adb)
    if not online:
        return {"device": "nicht verbunden", "devices": devs}
    pkgs = ctx.device_files().list_packages()
    return {"device": "verbunden", "selected": sel, "devices": devs,
            "anzahl_3rd_party": len(pkgs), "pakete": pkgs[:100]}


# ============================================================ H: Logs
def ghostlog_capture(ctx, lines=200):
    pkg = ctx.cfg.config.get("APP_PACKAGE", "")
    logf = f"/data/data/{pkg}/ghost.log"
    cmd = f'"{_adb(ctx)}" shell "run-as {pkg} tail -n {int(lines)} {logf}"'
    r = CommandRunner.run_blocking(cmd, ".", timeout=30)
    if r.returncode != 0:
        return f"[kein ghost.log lesbar] {(r.stderr or r.stdout).strip()}"
    return r.stdout.strip() or "(ghost.log leer)"


def logcat_capture(ctx, lines=300, filter_str="", package_only=True, seconds=0.0):
    """Liest gefilterten Logcat-Puffer vom verbundenen ADB-Geraet (optional mit kurzer Wartezeit)."""
    import time as _t
    wait_s = min(max(0.0, float(seconds or 0.0)), 10.0)
    if wait_s > 0.0:
        _t.sleep(wait_s)
    want = max(10, int(lines or 300))
    dump_n = max(want * 4, 500)
    r = CommandRunner.run_blocking(f'"{_adb(ctx)}" logcat -d -t {dump_n}', ".", timeout=15)
    if r.returncode != 0:
        return {"ok": False, "hinweis": (r.stderr or r.stdout).strip() or "adb logcat fehlgeschlagen"}
    raw_lines = (r.stdout or "").splitlines()
    pkg = (ctx.cfg.config.get("APP_PACKAGE", "") or "").strip()
    pid = ""
    if package_only and pkg:
        pr = CommandRunner.run_blocking(f'"{_adb(ctx)}" shell pidof -s {pkg}', ".", timeout=5)
        if pr.returncode == 0:
            pid = (pr.stdout or "").strip().split()[0] if (pr.stdout or "").strip() else ""
        filtered = []
        for ln in raw_lines:
            if (pid and f" {pid} " in f" {ln} ") or (pkg in ln):
                filtered.append(ln)
    else:
        filtered = raw_lines

    flt = str(filter_str or "").strip()
    if flt:
        try:
            rx = re.compile(flt, re.IGNORECASE)
            filtered = [ln for ln in filtered if rx.search(ln)]
        except re.error:
            low = flt.lower()
            filtered = [ln for ln in filtered if low in ln.lower()]
    tail = filtered[-want:]
    return {"ok": True, "package": pkg, "pid": pid, "count": len(tail), "lines": tail}


def app_start(ctx, package="", intent_cmd="", stop_first=False):
    """Startet die App / einen Intent per ADB auf dem verbundenen Geraet (gated: app_start)."""
    pkg = str(package or "").strip() or (ctx.cfg.config.get("APP_PACKAGE", "") or "").strip()
    if not pkg:
        return {"ok": False, "hinweis": "Kein Package angegeben und APP_PACKAGE ist leer."}
    adb = _adb(ctx)
    if stop_first:
        CommandRunner.run_blocking(f'"{adb}" shell am force-stop {pkg}', ".", timeout=8)
    ic = str(intent_cmd or "").strip()
    if ic:
        app_name = pkg.split(".")[-1] if "." in pkg else pkg
        fmt = ic.replace("{APP_PACKAGE}", pkg).replace("{APP_NAME}", app_name)
        if not fmt.startswith("shell "):
            fmt = "shell " + fmt
        cmd = f'"{adb}" {fmt}'
    else:
        cmd = f'"{adb}" shell monkey -p {pkg} -c android.intent.category.LAUNCHER 1'
    r = CommandRunner.run_blocking(cmd, ".", timeout=15)
    return {
        "ok": r.returncode == 0,
        "package": pkg,
        "command": cmd,
        "stdout": (r.stdout or "").strip(),
        "stderr": (r.stderr or "").strip(),
    }


def app_stop(ctx, package=""):
    """Stoppt die App per `adb shell am force-stop` (gated: app_start)."""
    pkg = str(package or "").strip() or (ctx.cfg.config.get("APP_PACKAGE", "") or "").strip()
    if not pkg:
        return {"ok": False, "hinweis": "Kein Package angegeben und APP_PACKAGE ist leer."}
    r = CommandRunner.run_blocking(f'"{_adb(ctx)}" shell am force-stop {pkg}', ".", timeout=10)
    return {
        "ok": r.returncode == 0,
        "package": pkg,
        "stdout": (r.stdout or "").strip(),
        "stderr": (r.stderr or "").strip(),
    }


def log_export(ctx):
    c = ctx.cfg.config
    m = c.get("MCP_SETTINGS", {})
    base = c.get("BASE_DIR", ".")
    out_dir = m.get("log_export_dir") or os.path.join("Claude outputs", "logs")
    if not os.path.isabs(out_dir):
        out_dir = os.path.join(base, out_dir)
    os.makedirs(out_dir, exist_ok=True)
    audit_rel = (m.get("audit", {}) or {}).get("file", "data/mcp_audit.jsonl")
    audit_abs = audit_rel if os.path.isabs(audit_rel) else os.path.join(base, audit_rel)
    exported = []
    for src in [audit_abs, ctx.cfg.paths.get("LOG_FILE", "")]:
        if src and os.path.exists(src):
            dst = os.path.join(out_dir, os.path.basename(src))
            try:
                shutil.copy2(src, dst)
                exported.append(dst)
            except Exception as e:
                exported.append(f"FEHLER {src}: {e}")
    return {"export_dir": out_dir, "dateien": exported}


# ============================================================ K: MCP-Selbstauskunft
def mcp_capabilities(ctx):
    m = ctx.cfg.config.get("MCP_SETTINGS", {})
    out = {"server": m.get("server", {}), "caps": m.get("caps", {}), "tools": {}}
    for s in registry.TOOL_CATALOG:
        out["tools"][s.id] = {
            "tier": s.tier,
            "enabled": registry.is_tool_enabled(m, s.id),
            "caps": list(s.caps),
        }
    return out


def mcp_audit_tail(ctx, n=50):
    from mcp_server import audit
    lines = audit.tail(ctx.cfg, int(n))
    return "".join(lines) or "(kein Audit)"


def api_query(ctx, filter="", limit=50, with_bodies=False):
    """MITM-Traffic-DB abfragen (data/api_traffic.db). filter matcht URL/Method."""
    from services.api_db_service import ApiDbService
    dbp = ctx.cfg.paths.get("API_DB")
    if not dbp or not os.path.exists(dbp):
        return "(keine Traffic-DB — MITM/Proxy wurde noch nicht genutzt.)"
    try:
        rows = ApiDbService(dbp).get_requests(filter or "")
    except Exception as e:
        return f"[Fehler] {e}"
    rows = rows[-int(limit):]
    out = []
    for (rid, ts, method, url, status, rqh, rqb, rsh, rsb, comment) in rows:
        item = {"id": rid, "method": method, "url": url, "status": status, "comment": comment}
        if with_bodies:
            item["req_body"] = (rqb or "")[:2000]
            item["res_body"] = (rsb or "")[:2000]
        else:
            item["res_len"] = len(rsb or "")
        out.append(item)
    return {"count": len(out), "requests": out,
            "hinweis": "with_bodies=true fuer Bodies (auf 2000 Zeichen gekuerzt) oder api.get(id) fuer den vollen Datensatz."}


def api_get(ctx, req_id):
    """Einen kompletten Traffic-Datensatz (inkl. Bodies/Headers) per ID holen."""
    from services.api_db_service import ApiDbService
    dbp = ctx.cfg.paths.get("API_DB")
    if not dbp or not os.path.exists(dbp):
        return "(keine Traffic-DB.)"
    try:
        rows = ApiDbService(dbp).get_full_requests_by_ids([int(req_id)])
    except Exception as e:
        return f"[Fehler] {e}"
    if not rows:
        return f"(keine Request-ID {req_id})"
    keys = ["id", "timestamp", "method", "url", "status",
            "req_headers", "req_body", "res_headers", "res_body", "comment"]
    return dict(zip(keys, rows[0]))


def _api_rules_path(ctx):
    if ctx is not None and hasattr(ctx, "cfg") and hasattr(ctx.cfg, "paths"):
        p = ctx.cfg.paths.get("API_RULES")
        if p:
            return p
    return os.path.join(_REPO, "data", "intercept_rules.json")


def api_rules_list(ctx):
    """Liest die aktiven HTTP Intercept-Regeln (intercept_rules.json)."""
    p = _api_rules_path(ctx)
    if not os.path.isfile(p):
        return {"ok": True, "rules": [], "path": p}
    try:
        with open(p, "r", encoding="utf-8") as fh:
            rules = json.load(fh) or []
        return {"ok": True, "rules": rules, "count": len(rules), "path": p}
    except Exception as e:
        return {"ok": False, "hinweis": repr(e), "path": p}


def api_rules_set(ctx, rules_json, append=False):
    """Setzt oder ergaenzt Intercept-Regeln (JSON-Liste von {url_match, action, payload})."""
    p = _api_rules_path(ctx)
    try:
        new_rules = json.loads(rules_json) if isinstance(rules_json, str) else rules_json
    except Exception as e:
        return {"ok": False, "hinweis": f"Ungueltiges JSON: {e!r}"}
    if isinstance(new_rules, dict):
        new_rules = [new_rules]
    if not isinstance(new_rules, list):
        return {"ok": False, "hinweis": "rules_json muss Liste oder Regel-Objekt sein"}
    final = []
    if append and os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                cur = json.load(fh) or []
            if isinstance(cur, list):
                final.extend(cur)
        except Exception:
            pass
    final.extend(new_rules)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(final, fh, ensure_ascii=False, indent=2)
    return {"ok": True, "count": len(final), "rules": final, "path": p}


def api_update(ctx, req_id, comment=None, req_body=None, res_body=None):
    """Aktualisiert Kommentar, Request- oder Response-Body eines Eintrags in api_traffic.db."""
    from services.api_db_service import ApiDbService
    dbp = ctx.cfg.paths.get("API_DB") or os.path.join(_base_dir(ctx), "data", "api_traffic.db")
    if not os.path.exists(dbp):
        return {"ok": False, "hinweis": "Keine Traffic-DB vorhanden."}
    svc = ApiDbService(dbp)
    row = svc.get_request_by_id(int(req_id))
    if not row:
        return {"ok": False, "hinweis": f"Request-ID {req_id} nicht gefunden."}
    _rqh, old_rqb, _rsh, old_rsb, old_cmt = row
    upd = svc.update_request(
        int(req_id),
        old_cmt if comment is None else str(comment),
        old_rqb if req_body is None else str(req_body),
        old_rsb if res_body is None else str(res_body),
    )
    keys = ["id", "timestamp", "method", "url", "status",
            "req_headers", "req_body", "res_headers", "res_body", "comment"]
    return {"ok": True, "record": dict(zip(keys, upd)) if upd else None}


def api_replay(ctx, req_id=0, method="", url="", headers_json="", body="", record=True, timeout_s=12.0):
    """Sendet einen Request aus api_traffic.db (req_id) oder frei per HTTP erneut (optional mit DB-Protokoll)."""
    import time as _t
    import urllib.error as _ue
    import urllib.request as _ur
    from services.api_db_service import ApiDbService

    dbp = ctx.cfg.paths.get("API_DB") or os.path.join(_base_dir(ctx), "data", "api_traffic.db")
    base_method, base_url, base_hdrs, base_body = "GET", "", {}, ""
    if int(req_id or 0) > 0 and os.path.exists(dbp):
        rows = ApiDbService(dbp).get_full_requests_by_ids([int(req_id)])
        if not rows:
            return {"ok": False, "hinweis": f"Request-ID {req_id} nicht gefunden."}
        _r = rows[0]
        base_method = _r[2] or "GET"
        base_url = _r[3] or ""
        try:
            hd = json.loads(_r[5] or "{}") if isinstance(_r[5], str) else (_r[5] or {})
            if isinstance(hd, dict):
                base_hdrs = {str(k): str(v) for k, v in hd.items()}
        except Exception:
            pass
        base_body = _r[6] or ""

    eff_method = (str(method or "").strip() or base_method or "GET").upper()
    eff_url = str(url or "").strip() or base_url
    if not eff_url:
        return {"ok": False, "hinweis": "url oder gueltige req_id erforderlich."}
    eff_hdrs = dict(base_hdrs)
    if headers_json:
        try:
            parsed_h = json.loads(headers_json) if isinstance(headers_json, str) else dict(headers_json)
            if isinstance(parsed_h, dict):
                eff_hdrs.update({str(k): str(v) for k, v in parsed_h.items()})
        except Exception as e:
            return {"ok": False, "hinweis": f"headers_json ungueltig: {e!r}"}
    eff_body = base_body if body == "" else str(body)
    body_bytes = eff_body.encode("utf-8", "replace") if (eff_body and eff_method not in ("GET", "HEAD")) else None

    req_obj = _ur.Request(eff_url, data=body_bytes, method=eff_method)
    for k, v in eff_hdrs.items():
        try:
            req_obj.add_header(k, v)
        except Exception:
            pass

    status_code = 0
    res_hdrs = {}
    res_text = ""
    err_msg = ""
    to = min(max(1.0, float(timeout_s or 12.0)), 30.0)
    try:
        with _ur.urlopen(req_obj, timeout=to) as resp:
            status_code = int(getattr(resp, "status", 200) or 200)
            res_hdrs = {str(k): str(v) for k, v in resp.headers.items()}
            res_text = resp.read().decode("utf-8", "replace")
    except _ue.HTTPError as he:
        status_code = int(getattr(he, "code", 500) or 500)
        res_hdrs = {str(k): str(v) for k, v in (getattr(he, "headers", None) or {}).items()}
        try:
            res_text = he.read().decode("utf-8", "replace")
        except Exception:
            res_text = str(he)
    except Exception as e:
        return {"ok": False, "method": eff_method, "url": eff_url, "hinweis": repr(e)}

    if record:
        try:
            ApiDbService(dbp).insert_packet({
                "timestamp": _t.time(),
                "method": eff_method,
                "url": eff_url,
                "status": status_code,
                "req_headers": json.dumps(eff_hdrs, ensure_ascii=False),
                "req_body": eff_body,
                "res_headers": json.dumps(res_hdrs, ensure_ascii=False),
                "res_body": res_text,
                "comment": f"MCP Replay{f' (#{req_id})' if req_id else ''}",
            })
        except Exception as rec_e:
            err_msg = repr(rec_e)
    return {
        "ok": True,
        "status": status_code,
        "method": eff_method,
        "url": eff_url,
        "res_headers": res_hdrs,
        "res_body": res_text[:8000],
        "recorded": bool(record and not err_msg),
        "record_error": err_msg or None,
    }


def api_clear(ctx):
    """Leert alle Eintraege der API-Inspector-DB (api_traffic.db). Gated: delete_ops."""
    from services.api_db_service import ApiDbService
    dbp = ctx.cfg.paths.get("API_DB") or os.path.join(_base_dir(ctx), "data", "api_traffic.db")
    ApiDbService(dbp).clear_db()
    return {"ok": True, "cleared": True, "api_db": dbp}


def history_list(ctx, limit=20):
    """Test-/Analyse-Historie lesen (RE_History.json)."""
    from services.history_service import HistoryManager
    data = HistoryManager(ctx.cfg).data[-int(limit):]
    recs = [{k: rec.get(k) for k in ("id", "name", "app_package", "app_version", "timestamp", "result")}
            for rec in data]
    return {"count": len(recs), "records": recs}


def history_add(ctx, name, result, observation="", patches=None):
    """Analyse-Ergebnis/Beobachtung als Historien-Eintrag sichern (JSON + Kippy_RE_Log.md) —
    entspricht 'Session permanent sichern'."""
    import datetime
    from services.history_service import HistoryManager
    if isinstance(patches, str):
        try:
            patches = json.loads(patches)
        except Exception:
            patches = [{"type": "note", "text": patches}]
    h = HistoryManager(ctx.cfg)
    rec = {
        "id": "MCP-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"),
        "name": name,
        "app_package": ctx.cfg.config.get("APP_PACKAGE", "Unbekannt"),
        "app_version": ctx.cfg.config.get("APP_VERSION", ""),
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "result": result,
        "observation": observation,
        "patches": patches or [],
    }
    h.add_record(rec)
    return f"Historien-Eintrag {rec['id']} gesichert (RE_History.json + Kippy_RE_Log.md)."


def mcp_guide(ctx):
    from mcp_server.guide import AGENT_GUIDE
    return AGENT_GUIDE


def _line_dt(line, now):
    """Zeitstempel '[HH:MM:SS] …' -> datetime von heute (mit Mitternachts-Korrektur)."""
    import datetime
    if len(line) >= 10 and line[0] == "[" and line[9] == "]":
        try:
            t = datetime.datetime.strptime(line[1:9], "%H:%M:%S").time()
        except Exception:
            return None
        dt = now.replace(hour=t.hour, minute=t.minute, second=t.second, microsecond=0)
        if dt > now + datetime.timedelta(seconds=5):
            dt -= datetime.timedelta(days=1)   # Zeile ist von gestern (Session ueber Mitternacht)
        return dt
    return None


def _read_filtered(path, n, include="", exclude="", match="any", since_last_clear=False, last_minutes=0):
    """Liest die letzten n passenden Zeilen. Filter-Semantik wie die GUI:
    Exclude = Zeile raus, wenn IRGENDEIN Begriff vorkommt (NOT);
    Include = Zeile bleibt, wenn IRGENDEIN Begriff vorkommt (match='any', GUI-Default)
              bzw. wenn ALLE vorkommen (match='all' = AND). Begriffe leerzeichengetrennt,
              case-insensitiv. since_last_clear=True -> nur nach dem letzten Leeren-Marker.
              last_minutes>0 -> nur Zeilen aus den letzten X Minuten (per [HH:MM:SS]-Stempel)."""
    if not os.path.exists(path):
        return f"(noch keine Daten: {os.path.basename(path)} — laeuft die GUI mit Konsolen-Spiegel?)"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return f"[Fehler] {e}"

    if since_last_clear:
        last = -1
        for idx, ln in enumerate(lines):
            if "=== GELEERT" in ln or "=== HART GELEERT" in ln:
                last = idx
        if last >= 0:
            lines = lines[last + 1:]

    if last_minutes and int(last_minutes) > 0:
        import datetime
        now = datetime.datetime.now()
        cutoff = now - datetime.timedelta(minutes=int(last_minutes))
        kept, carry = [], None
        for ln in lines:
            dt = _line_dt(ln, now)
            if dt is not None:
                carry = dt
            # Zeilen ohne Stempel erben die Zeit der vorigen Zeile (Fortsetzungen)
            if (carry is None) or (carry >= cutoff):
                kept.append(ln)
        lines = kept

    inc = [t for t in (include or "").lower().split()]
    exc = [t for t in (exclude or "").lower().split()]
    all_mode = (match == "all")
    out = []
    for line in lines:
        ll = line.lower()
        if exc and any(e in ll for e in exc):
            continue
        if inc:
            if all_mode and not all(i in ll for i in inc):
                continue
            if not all_mode and not any(i in ll for i in inc):
                continue
        out.append(line)
    out = out[-int(n):]
    head = f"({len(out)} Zeilen"
    if inc:
        head += f", include[{match}]={inc}"
    if exc:
        head += f", exclude={exc}"
    if since_last_clear:
        head += ", seit letztem Leeren"
    if last_minutes and int(last_minutes) > 0:
        head += f", letzte {int(last_minutes)} min"
    head += ")\n"
    return head + ("".join(out) if out else "(keine passenden Zeilen)")


def console_main_tail(ctx, n=100, include="", exclude="", match="any", since_last_clear=False, last_minutes=0):
    """Main Console (Workspace) — Spiegel data/console_main.log, optional gefiltert."""
    p = os.path.join(ctx.cfg.config.get("BASE_DIR", "."), "data", "console_main.log")
    return _read_filtered(p, n, include, exclude, match, since_last_clear, last_minutes)


def console_live_tail(ctx, n=100, include="", exclude="", match="any", since_last_clear=False, last_minutes=0):
    """Start & Live-Log (App+Exe) — Spiegel data/console_live.log, optional gefiltert."""
    p = os.path.join(ctx.cfg.config.get("BASE_DIR", "."), "data", "console_live.log")
    return _read_filtered(p, n, include, exclude, match, since_last_clear, last_minutes)


def log_settings(ctx):
    """Logcat-/Intent-Favoriten aus data/logger_profiles.json lesen (die editierbaren
    Combobox-Vorlagen des „Start & Live-Log"-Reiters)."""
    import json
    base = ctx.cfg.config.get("BASE_DIR", ".")
    p = os.path.join(base, "data", "logger_profiles.json")
    if not os.path.exists(p):
        return {"config_file": p, "intents": [], "logcats": [],
                "hinweis": "Datei fehlt -> GUI nutzt aktuell die eingebauten Default-Profile."}
    try:
        data = json.load(open(p, encoding="utf-8"))
    except Exception as e:
        return f"[Fehler] {e}"
    return {"config_file": p, "intents": data.get("intents", []), "logcats": data.get("logcats", [])}


def log_add_logcat(ctx, command):
    """Einen Logcat-Aufruf als Favorit ablegen (Combobox „Logcat"). Platzhalter
    {PID}/{APP_NAME}/{APP_PACKAGE} sind erlaubt. Wirksam, sobald der Log-Reiter neu geladen wird."""
    from services.profile_manager_service import ProfileManagerService
    p = os.path.join(ctx.cfg.config.get("BASE_DIR", "."), "data", "logger_profiles.json")
    mgr = ProfileManagerService(p)
    ok = mgr.add_template("logcats", command)
    return (f"{'hinzugefuegt' if ok else 'nicht hinzugefuegt (leer oder bereits vorhanden)'}: {command}\n"
            f"-> wirksam nach Neu-Laden des „Start & Live-Log\"-Reiters in der GUI.")


# ============================================================ E: Bauen & Flashen (gated)
_VALID_PIPELINES = ("PREPARE_WORKSPACE", "BUILD_FLUTTER", "BUILD_NATIVE",
                    "FLASH", "TRACE_START", "TRACE_STOP")


def build_hint(ctx):
    return ("GUI-Reihenfolge: Favoriten anwenden -> (falls Lib) LibForge bauen -> "
            "Workspace BUILD_NATIVE -> FLASH. Per MCP (gated): "
            "pipeline.run('BUILD_NATIVE'), pipeline.flash, lib.build('<name>'). "
            f"Gueltige Pipelines: {', '.join(_VALID_PIPELINES)}.")


def pipeline_run(ctx, name):
    name = (name or "").strip().upper()
    if name not in _VALID_PIPELINES:
        return f"[Fehler] Unbekannte Pipeline '{name}'. Gueltig: {', '.join(_VALID_PIPELINES)}"
    ctx.reload_config()
    # Vor dem Build die geteilte aktive Liste laden, damit ein MCP-Build genau das
    # baut, was in data/active_session.json steht (leer -> RAM-Stand behalten).
    try:
        _shared = ctx.active_session().patches()
        if _shared:
            SessionState.load_patches(_shared)
    except Exception as e:
        print(f"[tools] pipeline_run: active_session load: {e}")
    ok = ctx.engine.run_pipeline(name)
    return {"pipeline": name, "success": bool(ok)}


def pipeline_flash(ctx):
    ctx.reload_config()
    ok = ctx.engine.run_pipeline("FLASH")
    return {"pipeline": "FLASH", "success": bool(ok)}


def _find_lib(ctx, ref, only_exec=False):
    mgr = ctx.libs()
    libs = mgr.get_executables() if only_exec else mgr.get_all()
    ref = (ref or "").strip()
    for l in libs:
        if getattr(l, "id", None) == ref:
            return mgr, l
    # Fix 1.9: bei Namens-Mehrdeutigkeit die AKTIVE Variante bevorzugen
    def _pick(matches):
        act = [m for m in matches if getattr(m, "active", False)]
        return act[0] if len(act) == 1 else matches[0]
    exact = [l for l in libs if (getattr(l, "name", "") or "") == ref]
    if exact:
        return mgr, _pick(exact)
    rl = ref.lower()
    ci = [l for l in libs if (getattr(l, "name", "") or "").lower() == rl]
    if ci:
        return mgr, _pick(ci)
    return mgr, None


def lib_build(ctx, ref):
    from services.native_compiler_service import NativeCompilerService
    ctx.reload_config()
    mgr, lib = _find_lib(ctx, ref)
    if not lib:
        return f"[Fehler] Lib/Executable nicht gefunden: {ref} (lib.list zeigt die Namen)."
    if getattr(lib, "origin", "") == "imported":
        return f"[Fehler] '{lib.name}' ist importiert (kein Quellcode) - kein Build."
    ok, so_path, err = NativeCompilerService.compile(lib, mgr, ctx.cfg.config)
    activated = False
    if ok and getattr(lib, "output_kind", "shared") == "executable":
        mgr.deactivate_same_name(lib)
        lib.active = True
        mgr.update(lib)
        activated = True
    _sha = ""; _sz = 0
    if ok and so_path:
        try:
            import os as _os, hashlib as _hl
            _b = open(so_path, "rb").read(); _sz = len(_b); _sha = _hl.sha256(_b).hexdigest()[:12]
        except Exception:
            pass
    return {"lib": lib.name, "success": bool(ok),
            "output": so_path if ok else "",
            "sha256": _sha, "size": _sz,
            "auto_activated_executable": activated,
            "error": "" if ok else (err or "")[:800]}


def lib_delete(ctx, ref):
    mgr, lib = _find_lib(ctx, ref)
    if not lib:
        return f"[Fehler] nicht gefunden: {ref}"
    name = lib.name
    mgr.delete(lib.id)
    return {"deleted": name, "id": lib.id}


# ============================================================ G: Executables (ExeDeploy, gated)
def exec_list(ctx):
    mgr = ctx.libs()
    out = []
    for l in mgr.get_deployable_executables():
        out.append({"name": l.name, "id": l.id,
                    "active": bool(getattr(l, "active", False)),
                    "run_dir": getattr(l, "run_dir", "") or "",
                    "pull_globs": list(getattr(l, "pull_globs", []) or [])})
    return {"executables": out, "count": len(out)}


def exec_deploy_run(ctx, ref, wait_s=8):
    import time
    from services.device_exec_service import DeviceExecService
    ctx.reload_config()
    mgr, lib = _find_lib(ctx, ref, only_exec=True)
    if not lib:
        return f"[Fehler] Executable nicht gefunden: {ref} (exec.list zeigt die Namen)."
    try:
        wait_s = max(1, min(int(wait_s), 40))
    except Exception:
        wait_s = 8
    svc = DeviceExecService(ctx.cfg)
    pkg = ctx.cfg.config.get("APP_PACKAGE", "")
    run_dir = (getattr(lib, "run_dir", "") or
               ctx.cfg.config.get("EXEC_RUN_DIR_DEFAULT", "/data/local/tmp")).rstrip("/")
    binary = mgr.abspath(lib.so_relpath)
    if not svc.deploy(lib.name, binary, run_dir, list(getattr(lib, "runtime_deps", []) or [])):
        return {"exec": lib.name, "deployed": False, "note": "Deploy fehlgeschlagen (siehe Log)."}
    if not svc.stage_inputs(lib.name, pkg, list(getattr(lib, "stage_inputs", []) or []), run_dir):
        return {"exec": lib.name, "deployed": True, "staged": False, "note": "Staging fehlgeschlagen."}
    svc.run(lib.name, run_dir, getattr(lib, "run_cwd", "") or run_dir,
            getattr(lib, "run_env", "") or "", getattr(lib, "run_args", "") or "")
    time.sleep(wait_s)  # Live-Output (EXEC_OUTPUT) waehrend dieser Zeit wird vom Sammler erfasst
    still = svc.is_running(lib.name)
    return {"exec": lib.name, "deployed": True, "run_dir": run_dir,
            "waited_s": wait_s, "still_running": bool(still),
            "note": "Ausgabe siehe Framework-Log. still_running=true -> laeuft weiter (exec.pull_result spaeter)."}


def exec_pull_result(ctx, ref):
    from services.device_exec_service import DeviceExecService
    ctx.reload_config()
    mgr, lib = _find_lib(ctx, ref, only_exec=True)
    if not lib:
        return f"[Fehler] Executable nicht gefunden: {ref}"
    globs = list(getattr(lib, "pull_globs", []) or [])
    if not globs:
        return f"[Hinweis] '{lib.name}' hat keine pull_globs definiert - nichts zu holen."
    run_dir = (getattr(lib, "run_dir", "") or "/data/local/tmp").rstrip("/")
    local_dir = mgr.lib_dir(lib)
    svc = DeviceExecService(ctx.cfg)
    n = svc.pull_result(lib.name, run_dir, globs, local_dir)
    return {"exec": lib.name, "pulled": n, "local_dir": local_dir, "globs": globs}


def exec_delete(ctx, ref):
    mgr, lib = _find_lib(ctx, ref, only_exec=True)
    if not lib:
        return f"[Fehler] Executable nicht gefunden: {ref}"
    name = lib.name
    mgr.delete(lib.id)
    return {"deleted": name, "id": lib.id}


# ============================================================ F: File Manager (Geraet, gated)
def _pkg(ctx):
    return ctx.cfg.config.get("APP_PACKAGE", "")


def device_ls(ctx, path, domain="runas"):
    df = ctx.device_files()
    if domain == "shell":
        entries = df.list_dir_shell(path)
    else:
        pkg = _pkg(ctx)
        if not pkg:
            return "[Fehler] Kein APP_PACKAGE gesetzt (fuer domain=runas noetig)."
        entries = df.list_dir(pkg, path)
    return {"path": path, "domain": domain, "count": len(entries), "entries": entries}


def device_pull(ctx, remote_path, domain="runas"):
    import os
    df = ctx.device_files()
    pkg = _pkg(ctx)
    local_dir = os.path.join(ctx.get_archive_path(), "pull")
    os.makedirs(local_dir, exist_ok=True)
    local_path = os.path.join(local_dir, os.path.basename(remote_path.rstrip("/")) or "pulled.bin")
    ok = df.pull_file(pkg, remote_path, local_path, domain)
    return {"remote": remote_path, "local": local_path if ok else "", "success": bool(ok)}


def device_push(ctx, local_path, remote_path, domain="runas"):
    import os
    if not os.path.exists(local_path):
        return f"[Fehler] Lokale Datei fehlt: {local_path}"
    df = ctx.device_files()
    if domain == "shell":
        ok = df.push_file_shell(local_path, remote_path)
    else:
        ok = df.push_file(_pkg(ctx), local_path, remote_path)
    return {"local": local_path, "remote": remote_path, "domain": domain, "success": bool(ok)}


def device_delete(ctx, remote_path, domain="runas"):
    df = ctx.device_files()
    ok = df.delete_file(_pkg(ctx), remote_path, domain)
    return {"deleted": remote_path, "domain": domain, "success": bool(ok)}


def device_vault_bundle(ctx, subpath="."):
    """Zieht das App-Daten-Verzeichnis (oder subpath darunter) als tar+base64 via run-as
    und legt es lokal als .tar ab. Personenbezogen -> bleibt lokal."""
    import os, base64, time
    from core.infrastructure.command_runner import CommandRunner
    pkg = _pkg(ctx)
    if not pkg:
        return "[Fehler] Kein APP_PACKAGE gesetzt."
    adb = ctx.cfg.paths.get("ADB", "adb")
    base = f"/data/data/{pkg}"
    inner = f"run-as {pkg} sh -c 'tar -c -C {base} {subpath} 2>/dev/null | base64'"
    cmd = f'"{adb}" exec-out "{inner}"'
    r = CommandRunner.run_blocking(cmd, ".", timeout=180)
    b64 = (r.stdout or "").replace("\n", "").replace("\r", "").strip()
    if r.returncode != 0 or not b64:
        return {"success": False, "note": (r.stderr or r.stdout or "leer")[:300]}
    try:
        raw = base64.b64decode(b64)
    except Exception as e:
        return {"success": False, "note": f"base64-Dekodierung fehlgeschlagen: {e}"}
    out_dir = os.path.join(ctx.get_archive_path(), "vault")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"vault_{time.strftime('%Y%m%d-%H%M%S')}.tar")
    with open(out, "wb") as f:
        f.write(raw)
    return {"success": True, "local": out, "bytes": len(raw), "subpath": subpath}


# ============================================================ I: DAST net/proxy (gated/O)
def net_push_cert(ctx):
    from services.adb_network_service import AdbNetworkService
    ok, msg = AdbNetworkService.push_cert()
    return {"success": bool(ok), "message": msg}


def net_route(ctx, mode="usb", ip=""):
    from services.adb_network_service import AdbNetworkService
    mode = (mode or "usb").strip().lower()
    if mode == "usb":
        AdbNetworkService.route_usb()
    elif mode == "wlan":
        if not ip:
            return "[Fehler] mode=wlan braucht ip=<Host-IP>."
        AdbNetworkService.route_wlan(ip)
    elif mode in ("reset", "off", "clear"):
        AdbNetworkService.reset_route()
    else:
        return f"[Fehler] mode muss usb|wlan|reset sein (war '{mode}')."
    return {"routed": mode, "ip": ip or None}


def _proxy(ctx):
    from services.proxy_service import ProxyService
    p = getattr(ctx, "_proxy", None)
    if p is None:
        p = ProxyService(ctx.cfg.paths.get("API_DB", "api_traffic.db"),
                         ctx.cfg.paths.get("API_RULES", "intercept_rules.json"))
        ctx._proxy = p
    return p


def proxy_start(ctx):
    import os
    base = ctx.cfg.config.get("BASE_DIR", ".")
    addon = os.path.join(base, "services", "mitm_addon.py")
    p = _proxy(ctx)
    try:
        p.start_proxy(addon)
    except Exception as e:
        return {"success": False, "note": str(e)}
    return {"success": True, "running": p.is_running()}


def proxy_stop(ctx):
    p = _proxy(ctx)
    p.stop_proxy()
    return {"stopped": True, "running": p.is_running()}


# ============================================================ L: Frida (Skripte/Collections/Config)
from core.domain.frida_models import FridaScript, FridaCollection  # noqa: E402
import uuid as _uuid  # noqa: E402

_FRIDA_MODES = ("listen", "connect", "script", "script_directory")


def _frida_find_script(fm, ref):
    return next((s for s in fm.scripts if s.id == ref or s.name == ref), None)


def _frida_find_col(fm, ref):
    return next((c for c in fm.collections if c.id == ref or c.name == ref), None)


def _parse_script_ids(script_ids):
    if script_ids is None:
        return []
    if isinstance(script_ids, list):
        return [str(x) for x in script_ids]
    txt = str(script_ids).strip()
    if not txt:
        return []
    if txt.startswith("["):
        try:
            return [str(x) for x in json.loads(txt)]
        except Exception:
            pass
    return [t.strip() for t in txt.split(",") if t.strip()]


def frida_config_get(ctx):
    fc = ctx.cfg.frida_config
    return {"mode": fc.mode, "host": fc.host, "port": fc.port,
            "script_directory_path": fc.script_directory_path,
            "pause_on_load": fc.pause_on_load, "active_script_id": fc.active_script_id,
            "INJECT_FRIDA": bool(ctx.cfg.config.get("INJECT_FRIDA", False))}


def frida_config_set(ctx, mode=None, host=None, port=None, script_directory_path=None, pause_on_load=None):
    changes = {}
    if mode is not None:
        if mode not in _FRIDA_MODES:
            return f"[Fehler] mode muss eines von {_FRIDA_MODES} sein."
        changes["mode"] = mode
    if host is not None:
        changes["host"] = host
    if port is not None:
        try:
            changes["port"] = int(port)
        except (ValueError, TypeError):
            return "[Fehler] port muss eine Zahl sein."
    if script_directory_path is not None:
        changes["script_directory_path"] = script_directory_path
    if pause_on_load is not None:
        changes["pause_on_load"] = bool(pause_on_load)
    if not changes:
        return "[Hinweis] nichts geaendert (keine Felder angegeben)."
    ctx.cfg.update_keys({"FRIDA_SETTINGS": changes})
    fc = ctx.cfg.frida_config
    return {"ok": True, "geaendert": changes,
            "stand": {"mode": fc.mode, "host": fc.host, "port": fc.port,
                      "script_directory_path": fc.script_directory_path,
                      "pause_on_load": fc.pause_on_load}}


def frida_build_toggle(ctx, enabled):
    ctx.cfg.update_keys({"INJECT_FRIDA": bool(enabled)})
    return f"INJECT_FRIDA = {bool(enabled)} (Frida im Build {'AKTIV' if enabled else 'AUS'})."


def frida_scripts_list(ctx):
    fm = ctx.frida()
    out = [{"id": s.id, "name": s.name, "version": getattr(s, "version", "1.0"),
            "description": getattr(s, "description", ""),
            "active": (s.id == fm.active_script_id), "code_len": len(s.code or "")}
           for s in fm.scripts]
    return out or "keine Frida-Skripte"


def frida_scripts_get(ctx, ref):
    fm = ctx.frida()
    s = _frida_find_script(fm, ref)
    if not s:
        return f"[Fehler] Skript '{ref}' nicht gefunden."
    return {"id": s.id, "name": s.name, "version": getattr(s, "version", "1.0"),
            "description": getattr(s, "description", ""), "code": s.code}


def frida_scripts_add(ctx, name, code=None, version="1.0", description=""):
    fm = ctx.frida()
    warn = "" if _strip_ver(description) else " [WARN] ohne description — Version+Zweck angeben."
    sid = _uuid.uuid4().hex
    fm.scripts.append(FridaScript(id=sid, name=name, code=code or "",
                                  version=str(version), description=_compose_desc(version, description)))
    fm.save()
    return f"Frida-Skript '{name}' v{version} angelegt (id={sid}).{warn}"


def frida_scripts_update(ctx, ref, name=None, code=None, version=None, description=None):
    fm = ctx.frida()
    s = _frida_find_script(fm, ref)
    if not s:
        return f"[Fehler] Skript '{ref}' nicht gefunden."
    warn = ""
    if code is not None:
        old = getattr(s, "version", "1.0")
        if version is None or str(version) == str(old):
            warn += (f" [WARN] Code geaendert, Version nicht erhoeht (v{old}). "
                     f"Policy: Bugfix -> bumpen; neuer Ansatz -> frida.scripts_add. Trotzdem gespeichert.")
        if description is None:
            warn += " [WARN] description nicht aktualisiert."
        s.code = code
    if name is not None:
        s.name = name
    if version is not None:
        s.version = str(version)
    if version is not None or description is not None:
        eff = str(version) if version is not None else getattr(s, "version", "1.0")
        base = description if description is not None else getattr(s, "description", "")
        s.description = _compose_desc(eff, base)
    fm.save()
    return f"Frida-Skript '{s.name}' v{getattr(s,'version','?')} aktualisiert (id={s.id}).{warn}"


def frida_scripts_delete(ctx, ref):
    fm = ctx.frida()
    s = _frida_find_script(fm, ref)
    if not s:
        return f"[Fehler] Skript '{ref}' nicht gefunden."
    fm.scripts = [x for x in fm.scripts if x.id != s.id]
    if fm.active_script_id == s.id:
        fm.active_script_id = None
    fm.save()
    return f"Frida-Skript '{s.name}' geloescht (id={s.id})."


def frida_set_active(ctx, ref):
    fm = ctx.frida()
    s = _frida_find_script(fm, ref)
    if not s:
        return f"[Fehler] Skript '{ref}' nicht gefunden."
    fm.active_script_id = s.id
    fm.save()
    return f"aktives Frida-Skript: '{s.name}' (id={s.id})."


def frida_collections_list(ctx):
    fm = ctx.frida()
    out = [{"id": c.id, "name": c.name, "version": getattr(c, "version", "1.0"),
            "description": getattr(c, "description", ""),
            "script_ids": list(c.script_ids), "script_count": len(c.script_ids)}
           for c in fm.collections]
    return out or "keine Frida-Collections"


def frida_collections_add(ctx, name, script_ids=None, version="1.0", description=""):
    fm = ctx.frida()
    warn = "" if _strip_ver(description) else " [WARN] ohne description — Version+Zweck angeben."
    ids = _parse_script_ids(script_ids)
    unknown = [i for i in ids if not _frida_find_script(fm, i)]
    if unknown:
        warn += f" [WARN] unbekannte script_ids: {unknown}."
    cid = _uuid.uuid4().hex
    fm.collections.append(FridaCollection(id=cid, name=name, script_ids=ids,
                                          version=str(version), description=_compose_desc(version, description)))
    fm.save()
    return f"Frida-Collection '{name}' v{version} angelegt (id={cid}, {len(ids)} Skripte).{warn}"


def frida_collections_update(ctx, ref, name=None, script_ids=None, version=None, description=None):
    fm = ctx.frida()
    c = _frida_find_col(fm, ref)
    if not c:
        return f"[Fehler] Collection '{ref}' nicht gefunden."
    if name is not None:
        c.name = name
    if script_ids is not None:
        c.script_ids = _parse_script_ids(script_ids)
    if version is not None:
        c.version = str(version)
    if version is not None or description is not None:
        eff = str(version) if version is not None else getattr(c, "version", "1.0")
        base = description if description is not None else getattr(c, "description", "")
        c.description = _compose_desc(eff, base)
    fm.save()
    return f"Frida-Collection '{c.name}' v{getattr(c,'version','?')} aktualisiert (id={c.id})."


def frida_collections_delete(ctx, ref):
    fm = ctx.frida()
    c = _frida_find_col(fm, ref)
    if not c:
        return f"[Fehler] Collection '{ref}' nicht gefunden."
    fm.collections = [x for x in fm.collections if x.id != c.id]
    fm.save()
    return f"Frida-Collection '{c.name}' geloescht (id={c.id})."


def frida_push_collection(ctx, ref):
    """GATED: Collection aufs Geraet pushen (kompiliert je Skript + adb push)."""
    fm = ctx.frida()
    c = _frida_find_col(fm, ref)
    if not c:
        return f"[Fehler] Collection '{ref}' nicht gefunden."
    from services.frida_compiler_service import FridaCompilerService
    from services.frida_sync_service import FridaSyncService
    import os as _os
    adb = ctx.cfg.paths.get("ADB", "adb")
    pkg = ctx.cfg.config.get("APP_PACKAGE", "")
    arch = ctx.cfg.paths.get("ARCHIVE_DIR", ".")
    pushed, failed = [], []
    for sid in c.script_ids:
        s = fm.get_script_by_id(sid)
        if not s:
            failed.append(f"{sid} (unbekannt)")
            continue
        try:
            compiled = FridaCompilerService.compile_script(s.code, arch)
            if not compiled:
                failed.append(f"{s.name} (compile)")
                continue
            filename = s.name.replace(" ", "_").lower()
            if not filename.endswith(".js"):
                filename += ".js"
            target = _os.path.join(_os.path.dirname(compiled), filename)
            _os.replace(compiled, target)
            ok = FridaSyncService.push_script(adb, pkg, target, ctx.cfg.frida_config)
            (pushed if ok else failed).append(s.name)
        except Exception as e:
            failed.append(f"{s.name} ({e})")
    return {"collection": c.name, "gepusht": pushed, "fehlgeschlagen": failed}


# ============================================================ M: Native Studio
# native.* — statische Analyse nativer AArch64-Libs (Native Studio) als MCP-Tools.
# Rein lesend (O) bzw. lokal vorbereitend (P). Nutzt core.native (loader/nindex/unpack)
# und die per Ordner-Scan entdeckten Decrypter-Plugins (native_plugins/, git-ignoriert).
# Schreibpfade ausschliesslich data/native/<plugin>/  (Zonen-Policy A1/A2) — identisch
# zu den Pfaden des GUI-Controllers, damit Index-/Ghidra-Cache GUI<->MCP geteilt werden.
#
# IP-neutral: kein LLU-Name hier; die Spezifik lebt im privaten Plugin.

_NATIVE_IMG_CACHE = {}   # abspath -> (mtime, ElfImage)
_NATIVE_IDX_CACHE = {}   # (abspath, fast) -> (sha, NativeIndex)
_NATIVE_REG = {"reg": None}   # Prozess-weite UnpackRegistry (lazy)


def _native_base(ctx):
    try:
        return ctx.cfg.config.get("BASE_DIR") or os.getcwd()
    except Exception:
        return os.getcwd()


def _native_appsrc(ctx):
    try:
        p = ctx.cfg.paths.get("APP_SOURCE_DIR")
        if p:
            return p
    except Exception:
        pass
    pkg = ""
    try:
        pkg = ctx.cfg.config.get("APP_PACKAGE", "")
    except Exception:
        pass
    return os.path.join(_native_base(ctx), "source", pkg)


def _native_registry(ctx):
    """Lazy UnpackRegistry (Ordner-Scan native_plugins/). Prozess-weit gecacht."""
    reg = _NATIVE_REG.get("reg")
    if reg is None:
        from core.native.unpack import UnpackRegistry
        reg = UnpackRegistry()
        try:
            reg.discover(os.path.join(_native_base(ctx), "native_plugins"))
        except Exception:
            pass
        _NATIVE_REG["reg"] = reg
    return reg


def _native_plugin_name(ctx):
    """Plugin-Bucket im Arbeitsbereich (erstes Plugin, sonst 'native') — wie der GUI-Controller."""
    try:
        names = _native_registry(ctx).names()
    except Exception:
        names = []
    return names[0] if names else "native"


def _native_resolve_path(ctx, path):
    """Akzeptiert absoluten Pfad, relativen Pfad oder blossen Basenamen einer .dec.so/.so.
    Sucht Basenamen im Analyse-Arbeitsbereich data/native/*/ (lib/*/, _bridge/, _apktmp/*/)."""
    if not path:
        return None
    if os.path.isabs(path) and os.path.isfile(path):
        return os.path.abspath(path)
    if os.path.isfile(path):
        return os.path.abspath(path)
    base = _native_base(ctx)
    root = os.path.join(base, "data", "native")
    bn = os.path.basename(path)
    hits = []
    for pat in (os.path.join(root, "*", "lib", "*", bn),
                os.path.join(root, "*", bn),
                os.path.join(root, "*", "_apktmp", "*", bn)):
        hits += glob.glob(pat)
    return os.path.abspath(hits[0]) if hits else path


def _native_load_image(ctx, path):
    from core.native.loader import ElfImage
    rp = _native_resolve_path(ctx, path)
    if not rp or not os.path.isfile(rp):
        raise FileNotFoundError(f"Datei nicht gefunden: {path}")
    try:
        mt = os.path.getmtime(rp)
    except OSError:
        mt = 0
    c = _NATIVE_IMG_CACHE.get(rp)
    if c and c[0] == mt:
        return rp, c[1]
    img = ElfImage.from_file(rp)
    _NATIVE_IMG_CACHE[rp] = (mt, img)
    return rp, img


def _native_build_index(ctx, rp, img, fast=True):
    from core.native.nindex import NativeIndex
    from core.native import unpack as _u
    import hashlib
    sha = hashlib.sha256(img.data).hexdigest()[:12]
    key = (rp, bool(fast))
    c = _NATIVE_IDX_CACHE.get(key)
    if c and c[0] == sha:
        return c[1]
    cache = os.path.join(_u.plugin_root(_native_base(ctx), _native_plugin_name(ctx)), "index")
    idx = NativeIndex().build(img, cache_dir=cache, fast=fast)
    _NATIVE_IDX_CACHE[key] = (sha, idx)
    return idx


def _native_v2o(img, v):
    """virtuelle Adresse -> Datei-Offset (innerhalb filesz)."""
    for s in img.segments:
        if s.vaddr <= v < s.vaddr + s.filesz:
            return s.offset + (v - s.vaddr)
    return None


def _native_o2v(img, off):
    """Datei-Offset -> virtuelle Adresse."""
    for s in img.segments:
        if s.offset <= off < s.offset + s.filesz:
            return s.vaddr + (off - s.offset)
    return None


def _native_resolve_addr(img, idx, ref):
    """ref = Symbolname | 0xADDR | sub_ADDR | dezimal -> int vaddr (oder None)."""
    if ref is None:
        return None
    r = str(ref).strip()
    if not r:
        return None
    # exaktes Symbol zuerst
    sym = img.dynsyms.get(r)
    if sym is not None:
        return sym.value
    low = r.lower()
    try:
        if low.startswith("0x"):
            return int(low, 16)
        if low.startswith("sub_"):
            return int(low[4:], 16)
        if r.isdigit():
            return int(r)
    except ValueError:
        pass
    # Symbol per Index (falls vorhanden)
    if idx is not None:
        for a, nm in idx.syms:
            if nm == r:
                return a
    return None


def _native_hexdump(data, base_vaddr):
    """Kompakter Hexdump: 16 Bytes/Zeile, Adresse + Hex + ASCII."""
    out = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        hx = " ".join(f"{b:02x}" for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        out.append(f"0x{base_vaddr + i:08x}  {hx:<47}  {asc}")
    return "\n".join(out)


# ---- M.1 native.targets (O) -------------------------------------------------
def _native_apk_candidates(ctx, apk):
    if apk:
        return [apk]
    appsrc = _native_appsrc(ctx)
    pref = ["merged_base.apk", "split_config.arm64_v8a.apk", "base.apk"]
    found = []
    for nm in pref:
        p = os.path.join(appsrc, nm)
        if os.path.isfile(p):
            found.append(p)
    for p in sorted(glob.glob(os.path.join(appsrc, "*.apk"))):
        if p not in found:
            found.append(p)
    return found


def native_targets(ctx, apk=None, abi="arm64-v8a"):
    from core.native import unpack as _u
    import zipfile
    cands = _native_apk_candidates(ctx, apk)
    if not cands:
        return {"ok": False,
                "hinweis": "Kein APK gefunden. 'apk' angeben oder in der GUI importieren. "
                           "APK-Pfad siehe workspace.status -> APP_SOURCE_DIR (source/<pkg>/*.apk)."}
    chosen = None
    packed = []
    allso = []
    for c in cands:
        if not _u.is_zip(c):
            continue
        try:
            with zipfile.ZipFile(c) as z:
                libs = [n for n in z.namelist()
                        if n.startswith("lib/" + abi + "/") and n.endswith(".so")]
        except Exception:
            continue
        if libs:
            p = _u.list_packed_libs(c, abi)
            if p or chosen is None:
                chosen, allso, packed = c, libs, p
            if p:
                break
    if chosen is None:
        return {"ok": False, "geprüfte_apks": [os.path.basename(x) for x in cands],
                "hinweis": f"Keine lib/{abi}/*.so in den gefundenen APKs."}
    return {
        "ok": True,
        "apk": chosen,
        "abi": abi,
        "alle_libs": [n.split("/")[-1] for n in allso],
        "gepackt_verschluesselt": [n.split("/")[-1] for n in packed],
        "entries": packed,
        "hinweis": ("Gepackte Libs via native.unpack entschluesseln "
                    "(apk=<apk> entry=<lib/...>), dann native.load/native.index auf der .dec.so."
                    if packed else
                    "Keine gepackt/verschluesselt wirkende Lib erkannt (Attrappen-Heuristik). "
                    "Evtl. bereits normale ELFs — native.load direkt auf die entpackte .so."),
    }


# ---- M.2 native.status (O) --------------------------------------------------
def native_status(ctx):
    from core.native import unpack as _u
    base = _native_base(ctx)
    plug = _native_plugin_name(ctx)
    root = _u.plugin_root(base, plug)

    decs = []
    for p in sorted(glob.glob(os.path.join(root, "lib", "*", "*.dec.so"))):
        try:
            decs.append({"name": os.path.basename(p),
                         "sha_dir": os.path.basename(os.path.dirname(p)),
                         "size": os.path.getsize(p), "path": p})
        except OSError:
            pass

    idxs = []
    for p in sorted(glob.glob(os.path.join(root, "index", ".nidx_*.pkl.xz"))):
        idxs.append(os.path.basename(p))

    ghidra = {}
    for p in sorted(glob.glob(os.path.join(root, "ghidra", "*", "func_*.c"))):
        sha = os.path.basename(os.path.dirname(p))
        fn = os.path.basename(p)
        ghidra.setdefault(sha, []).append(fn)

    return {
        "ok": True,
        "plugin_bucket": plug,
        "arbeitsbereich": root,
        "entschluesselte_libs": decs or "(keine)",
        "index_cache": idxs or "(keiner)",
        "ghidra_c_cache": ghidra or "(keiner)",
        "hinweis": ("Analyse mit path=<pfad einer .dec.so>. native.decompile liest NUR "
                    "gecachtes Ghidra-C (oben unter ghidra_c_cache); fehlt es, in der GUI "
                    "'🧊 C' erzeugen (langer Ghidra-Lauf, bewusst nicht ueber MCP)."),
    }


# ---- M.3 native.unpack (P) --------------------------------------------------
def native_unpack(ctx, entry=None, apk=None, so_path=None, smali_loader=None,
                  sig=None, smali_root=None):
    reg = _native_registry(ctx)
    if not reg.plugins():
        return {"ok": False, "why": "no_plugin",
                "hinweis": "Kein Decrypter-Plugin in native_plugins/ gefunden "
                           "(privat, git-ignoriert). Framework ist dann entpack-frei."}
    packed = so_path or apk
    if not packed:
        cands = _native_apk_candidates(ctx, None)
        packed = cands[0] if cands else None
    if not packed or not os.path.isfile(packed):
        return {"ok": False, "why": "input",
                "hinweis": "Eingabe fehlt: 'so_path' (gepackte .so) ODER 'apk' (+optional 'entry'). "
                           "Entries via native.targets."}
    plugins = reg.find_for(packed) or reg.plugins()
    plugin = plugins[0]
    kw = {}
    if entry:
        kw["apk_entry"] = entry
    if smali_loader:
        kw["smali_loader"] = smali_loader
    if sig:
        kw["sig"] = sig
    if smali_root:
        kw["smali_root"] = smali_root
    try:
        res = plugin.unpack(packed, **kw)
    except TypeError:
        res = plugin.unpack(packed)
    except Exception as e:
        return {"ok": False, "why": "exception", "hinweis": repr(e)}
    if isinstance(res, dict) and res.get("ok"):
        return {"ok": True, "plugin": plugin.name, "out_path": res.get("out_path"),
                "loader": res.get("loader"), "sig": res.get("sig"),
                "info": res.get("info"),
                "hinweis": "Entschluesselte .dec.so im Analyse-Arbeitsbereich (git-ignoriert). "
                           "Jetzt native.load / native.index mit path=<out_path>."}
    return {"ok": False, "plugin": plugin.name,
            "why": (res or {}).get("why") if isinstance(res, dict) else "fail",
            "hinweis": (res or {}).get("hint") if isinstance(res, dict) else str(res)}


# ---- M.4 native.load (O) ----------------------------------------------------
def native_load(ctx, path):
    from core.native import unpack as _u
    try:
        rp, img = _native_load_image(ctx, path)
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}
    head = img.data[:8192]
    segs = [{"vaddr": f"0x{s.vaddr:x}", "memsz": f"0x{s.memsz:x}",
             "filesz": f"0x{s.filesz:x}", "perms": s.perm_str()} for s in img.segments]
    syms = [{"name": n, "addr": f"0x{s.value:x}", "size": s.size}
            for n, s in list(img.dynsyms.items())[:80]]
    return {
        "ok": True,
        "path": rp,
        "is_aarch64": img.is_aarch64,
        "entry": f"0x{img.entry:x}",
        "sieht_gepackt_aus": _u.looks_packed(head),
        "segmente": segs,
        "symbole_gesamt": len(img.dynsyms),
        "symbole_auszug": syms,
        "relocs": len(getattr(img, "relocs", []) or []),
        "hinweis": ("Kein AArch64-ELF (evtl. noch gepackt) — zuerst native.unpack."
                    if not img.is_aarch64 else
                    "native.index fuer XREF/CallGraph, dann native.search/xref/disasm."),
    }


# ---- M.5 native.index (O) ---------------------------------------------------
def native_index(ctx, path, fast=True):
    try:
        rp, img = _native_load_image(ctx, path)
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}
    if not img.is_aarch64:
        return {"ok": False, "hinweis": "Kein AArch64-ELF — Index nicht sinnvoll. Erst native.unpack."}
    try:
        idx = _native_build_index(ctx, rp, img, fast=fast)
    except Exception as e:
        return {"ok": False, "hinweis": f"Indexbau fehlgeschlagen: {e!r}"}
    return {
        "ok": True,
        "path": rp,
        "modus": "fast (Branch/Call+Reloc)" if fast else "x (zusaetzl. adrp/add/ldr-Daten-Refs)",
        "instruktionen": idx.count,
        "kanten": len(idx.edges),
        "symbole": len(idx.syms),
        "sha": idx.sha,
        "hinweis": "Index im RAM + SHA-gegatetem Cache (data/native/<plugin>/index/). "
                   "Suche/XREF/CallGraph jetzt verfuegbar.",
    }


# ---- M.6 native.search (O) --------------------------------------------------
def native_search(ctx, path, query, max_results=100):
    try:
        rp, img = _native_load_image(ctx, path)
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}
    q = (query or "").strip()
    if not q:
        return {"ok": False, "hinweis": "Leere Suche. query = Symbol-Substring, String oder 0xAddr."}
    out = {"ok": True, "path": rp, "query": q}

    # (a) Adresse? -> Funktion aufloesen
    addr = None
    low = q.lower()
    try:
        if low.startswith("0x"):
            addr = int(low, 16)
        elif low.startswith("sub_"):
            addr = int(low[4:], 16)
    except ValueError:
        addr = None
    if addr is not None:
        idx = None
        try:
            idx = _native_build_index(ctx, rp, img, fast=True)
        except Exception:
            pass
        fn = idx.func_at(addr) if idx else None
        exact = next((n for n, s in img.dynsyms.items() if s.value == addr), None)
        out["adresse"] = {"addr": f"0x{addr:x}", "funktion": fn, "exaktes_symbol": exact}

    # (b) Symbol-Substring (case-insensitive)
    ql = q.lower()
    syms = []
    for n, s in img.dynsyms.items():
        if ql in n.lower():
            syms.append({"name": n, "addr": f"0x{s.value:x}", "size": s.size})
            if len(syms) >= max_results:
                break
    out["symbole"] = syms

    # (c) String-Suche (ASCII) in geladenen Daten, nur bei len(q)>=3
    strings = []
    if len(q) >= 3:
        needle = q.encode("utf-8", "ignore")
        data = img.data
        start = 0
        while len(strings) < min(max_results, 50):
            i = data.find(needle, start)
            if i < 0:
                break
            v = _native_o2v(img, i)
            # Kontext: ASCII um den Treffer bis NUL
            lo = i
            while lo > 0 and 32 <= data[lo - 1] < 127 and i - lo < 80:
                lo -= 1
            hi = i
            while hi < len(data) and 32 <= data[hi] < 127 and hi - i < 80:
                hi += 1
            strings.append({"offset": f"0x{i:x}",
                            "vaddr": (f"0x{v:x}" if v is not None else None),
                            "text": data[lo:hi].decode("ascii", "replace")})
            start = i + max(1, len(needle))
    out["strings"] = strings
    out["hinweis"] = "symbole = Symbolnamen (Substring); strings = ASCII-Fundstellen; adresse = Funktion zu 0xAddr."
    return out


# ---- M.7 native.xref (O) ----------------------------------------------------
def native_xref(ctx, path, ref, max_results=200):
    try:
        rp, img = _native_load_image(ctx, path)
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}
    try:
        idx = _native_build_index(ctx, rp, img, fast=True)
    except Exception as e:
        return {"ok": False, "hinweis": f"Index noetig, Bau fehlgeschlagen: {e!r}"}
    addr = _native_resolve_addr(img, idx, ref)
    if addr is None:
        return {"ok": False, "hinweis": f"Ziel nicht aufloesbar: {ref!r} (Symbolname oder 0xAddr)."}
    refs = idx.xrefs_to(addr, limit=max_results)
    rows = []
    for src, mnem in refs:
        rows.append({"von": f"0x{src:x}", "mnemonic": mnem,
                     "in_funktion": idx.func_at(src)})
    return {
        "ok": True, "path": rp,
        "ziel": f"0x{addr:x}", "ziel_funktion": idx.func_at(addr),
        "treffer": len(rows), "refs": rows,
        "hinweis": "Eingehende Branch-/Call-/Reloc-Referenzen auf die Zieladresse.",
    }


# ---- M.8 native.disasm (O) --------------------------------------------------
def native_disasm(ctx, path, at=None, count=48):
    from core.native.engine_arm64 import Arm64Decoder
    try:
        rp, img = _native_load_image(ctx, path)
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}
    try:
        count = max(1, min(int(count), 400))
    except (TypeError, ValueError):
        count = 48
    idx = None
    try:
        idx = _native_build_index(ctx, rp, img, fast=True)
    except Exception:
        pass
    # Startadresse bestimmen
    if at:
        addr = _native_resolve_addr(img, idx, at)
        if addr is None:
            return {"ok": False, "hinweis": f"Start nicht aufloesbar: {at!r}."}
    else:
        addr = img.entry
        if not addr and idx is not None and idx.syms:
            addr = idx.syms[0][0]
        if not addr:
            xseg = [s for s in img.segments if s.flags & 0x1]
            addr = xseg[0].vaddr if xseg else 0
    off = _native_v2o(img, addr)
    if off is None:
        return {"ok": False, "hinweis": f"Adresse 0x{addr:x} liegt in keinem Datei-Segment (filesz)."}
    code = img.data[off:off + count * 4]
    md = Arm64Decoder()._md
    lines = []
    produced = 0
    for a, size, mnem, ops in md.disasm_lite(code, addr):
        lines.append(f"0x{a:08x}:  {mnem:<8} {ops}")
        produced += 1
        if produced >= count:
            break
    func = idx.func_at(addr) if idx else None
    return {
        "ok": True, "path": rp, "start": f"0x{addr:x}", "funktion": func,
        "instruktionen": produced, "disasm": "\n".join(lines),
        "hinweis": "Lineares Disassembly ab Start (capstone). Fuer Funktionsgrenzen native.callgraph/xref.",
    }


# ---- M.9 native.read_bytes (O) ----------------------------------------------
def native_read_bytes(ctx, path, addr=None, offset=None, length=64):
    try:
        rp, img = _native_load_image(ctx, path)
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}
    try:
        length = max(1, min(int(length), 4096))
    except (TypeError, ValueError):
        length = 64
    foff = None
    base_v = 0
    if addr is not None and str(addr).strip() != "":
        a = str(addr).strip().lower()
        try:
            v = int(a, 16) if a.startswith("0x") else int(a)
        except ValueError:
            return {"ok": False, "hinweis": f"addr unlesbar: {addr!r}"}
        foff = _native_v2o(img, v)
        base_v = v
        if foff is None:
            return {"ok": False, "hinweis": f"vaddr 0x{v:x} in keinem Datei-Segment (filesz)."}
    elif offset is not None and str(offset).strip() != "":
        o = str(offset).strip().lower()
        try:
            foff = int(o, 16) if o.startswith("0x") else int(o)
        except ValueError:
            return {"ok": False, "hinweis": f"offset unlesbar: {offset!r}"}
        v = _native_o2v(img, foff)
        base_v = v if v is not None else 0
    else:
        return {"ok": False, "hinweis": "addr=0x.. (virtuell) ODER offset=0x.. (Datei) angeben."}
    data = img.data[foff:foff + length]
    if not data:
        return {"ok": False, "hinweis": "Nichts gelesen (Offset ausserhalb der Datei)."}
    return {
        "ok": True, "path": rp,
        "start_vaddr": f"0x{base_v:x}", "file_offset": f"0x{foff:x}", "laenge": len(data),
        "hexdump": _native_hexdump(data, base_v),
    }


# ---- M.10 native.callgraph (O) ----------------------------------------------
def native_callgraph(ctx, path, func):
    try:
        rp, img = _native_load_image(ctx, path)
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}
    try:
        idx = _native_build_index(ctx, rp, img, fast=True)
    except Exception as e:
        return {"ok": False, "hinweis": f"Index noetig, Bau fehlgeschlagen: {e!r}"}
    name = None
    if func in img.dynsyms:
        name = func
    else:
        addr = _native_resolve_addr(img, idx, func)
        if addr is not None:
            name = idx.func_at(addr)
    if not name:
        return {"ok": False,
                "hinweis": f"Funktion nicht aufloesbar: {func!r}. Symbolname oder 0xAddr; "
                           "bei stripped/obfuskierten Libs ist der symbolbasierte CallGraph duenn."}
    edges = idx.callgraph_edges()
    callers = sorted({cf for cf, tf in edges if tf == name})
    callees = sorted({tf for cf, tf in edges if cf == name})
    return {
        "ok": True, "path": rp, "funktion": name,
        "aufgerufen_von": callers or "(keine bekannt)",
        "ruft_auf": callees or "(keine bekannt)",
        "hinweis": "Symbolbasiert (bl-Ziele). Instruktions-XREF: native.xref.",
    }


# ---- M.11 native.decompile (O, nur Cache) -----------------------------------
def native_decompile(ctx, path, func, run=False):
    from core.native import unpack as _u
    try:
        rp, img = _native_load_image(ctx, path)
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}
    idx = None
    try:
        idx = _native_build_index(ctx, rp, img, fast=True)
    except Exception:
        pass
    addr = _native_resolve_addr(img, idx, func)
    if addr is None:
        return {"ok": False, "hinweis": f"Funktion nicht aufloesbar: {func!r} (Symbolname oder 0xAddr)."}
    ws = _u.plugin_root(_native_base(ctx), _native_plugin_name(ctx))
    sha = _u.sha8(rp)
    cfile = os.path.join(ws, "ghidra", sha, "func_%x.c" % addr)
    name = idx.func_at(addr) if idx else None
    if os.path.isfile(cfile):
        try:
            with open(cfile, "r", encoding="utf-8", errors="replace") as f:
                c = f.read()
        except Exception as e:
            return {"ok": False, "hinweis": f"C-Cache nicht lesbar: {e!r}"}
        return {"ok": True, "path": rp, "funktion": name or func,
                "addr": f"0x{addr:x}", "cached": True, "c": c}
    if run:
        try:
            from services.ghidra_service import GhidraService
            import threading as _th
            _gsvc = GhidraService(getattr(ctx, "cfg", None), lambda *a, **k: None)
            _th.Thread(target=lambda: _gsvc.decompile(rp, addr, ws), daemon=True).start()
            return {"ok": False, "cached": False, "why": "started", "path": rp,
                    "funktion": name or func, "addr": f"0x{addr:x}",
                    "hinweis": ("Ghidra-Decompile im Hintergrund gestartet (Erstlauf analysiert die ganze "
                                "Lib, dauert). Bei der naechsten native.decompile-Abfrage (ohne run) ist das C da.")}
        except Exception as _e:
            return {"ok": False, "cached": False, "why": "ghidra", "path": rp,
                    "funktion": name or func, "addr": f"0x{addr:x}", "hinweis": f"Ghidra nicht startbar: {_e!r}"}
    return {
        "ok": False, "cached": False, "path": rp,
        "funktion": name or func, "addr": f"0x{addr:x}",
        "why": "not_cached",
        "hinweis": ("Kein gecachtes Ghidra-C fuer diese Funktion. native.decompile ist O "
                    "(liest nur Cache) — den langen Ghidra-Lauf in der GUI ausloesen: Native "
                    "Studio -> Funktion waehlen -> '🧊 C'. Danach liefert dieses Tool das C sofort."),
    }


# ---- M.12 native.functions (O) ---------------------------------------------
def native_functions(ctx, path, max_results=500, named_only=False):
    """Funktionsanfaenge: benannte Dynsym-Funktionen (STT_FUNC/IFUNC) + per Index
    entdeckte bl-Ziele (sub_). O."""
    try:
        rp, img = _native_load_image(ctx, path)
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}
    named = [(sy.value, nm) for nm, sy in img.dynsyms.items()
             if sy.value and (sy.info & 0xf) in (2, 10)]
    named.sort()
    subs = []
    if not named_only:
        try:
            idx = _native_build_index(ctx, rp, img, fast=True)
            namedset = {a for a, _ in named}
            starts = set()
            for src, mnem, tgt in idx.edges:
                if mnem.split(".", 1)[0] == "bl":
                    starts.add(tgt)
            subs = sorted(a for a in starts if a and a not in namedset)
        except Exception:
            subs = []
    funcs = [{"addr": f"0x{a:x}", "name": nm} for a, nm in named[:max_results]]
    extra = max_results - len(funcs)
    if extra > 0:
        funcs += [{"addr": f"0x{a:x}", "name": f"sub_{a:x}"} for a in subs[:extra]]
    return {"ok": True, "path": rp, "benannt": len(named), "sub_entdeckt": len(subs),
            "funktionen": funcs,
            "hinweis": "benannte STT_FUNC + bl-Ziele (sub_). named_only=True unterdrueckt sub_."}


# ---- M.13 native.strings (O) -----------------------------------------------
def native_strings(ctx, path, query=None, min_len=4, max_results=300):
    """ASCII-Strings aus der .so (Krypto-/Algo-Namen, /proc, ptrace ...). O."""
    try:
        rp, img = _native_load_image(ctx, path)
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}
    import re as _re
    data = img.data
    pat = _re.compile(rb"[\x20-\x7e]{%d,}" % int(min_len))
    q = (query or "").lower().encode("utf-8", "ignore")
    out = []
    for m in pat.finditer(data):
        s = m.group(0)
        if q and q not in s.lower():
            continue
        off = m.start()
        v = _native_o2v(img, off)
        out.append({"off": f"0x{off:x}", "vaddr": (f"0x{v:x}" if v is not None else None),
                    "s": s.decode("latin-1")})
        if len(out) >= max_results:
            break
    return {"ok": True, "path": rp, "treffer": len(out), "strings": out,
            "hinweis": f"min_len={min_len}" + (f", filter={query!r}" if query else "")}


# ---- M.14 native.run (O) — Emulation ---------------------------------------
def native_run(ctx, path, entry, args=None, sp=None, maxins=2000000, stubs=None, read=None, relocs=False):
    """Emuliert eine Funktion (run-to-completion) -> Register/ret/Out-Puffer. O.
    args: Liste aus int | '0x..' | {'ptr':hex}|{'buf':n}|{'int':v}.
    stubs: [{'at':addr|sym,'ret':int}]. read: [{'addr':..,'len':..}]."""
    try:
        rp, img = _native_load_image(ctx, path)
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}
    if not getattr(img, "is_aarch64", False):
        return {"ok": False, "hinweis": "kein AArch64-ELF (oder gepackt) — erst native.unpack."}
    idx = None
    try:
        idx = _native_build_index(ctx, rp, img, fast=True)
    except Exception:
        pass
    addr = _native_resolve_addr(img, idx, entry)
    if addr is None:
        return {"ok": False, "hinweis": f"entry nicht aufloesbar: {entry!r}"}
    try:
        from services.native_emu_service import NativeEmuService
    except Exception as e:
        return {"ok": False, "hinweis": f"Emu-Service nicht ladbar: {e!r}"}
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    try:
        svc.load_image(eng, img, apply_relocs=relocs)
    except Exception as e:
        return {"ok": False, "hinweis": f"Laden in Engine: {e!r}"}
    try:
        if stubs:
            svc.install_stubs(eng, stubs, image=img)
        res = svc.run(eng, addr, args or [], None, sp, int(maxins), image=img)
    except Exception as e:
        return {"ok": False, "hinweis": f"Run-Fehler: {e!r}"}
    if not res.get("ok"):
        base = {"ok": False, "path": rp, "entry": f"0x{addr:x}"}
        base.update({k: v for k, v in res.items() if k != "ok"})
        return base
    xs = res.get("x", [])
    reads = {}
    for rd in (read or []):
        try:
            ra = _native_resolve_addr(img, idx, rd.get("addr"))
            if ra is not None:
                reads[f"0x{ra:x}"] = svc.read_mem(eng, ra, int(rd.get("len", 16))).hex()
        except Exception:
            pass
    return {
        "ok": True, "path": rp, "entry": f"0x{addr:x}", "ret": f"0x{res['ret']:x}",
        "x0_7": [f"0x{xs[i]:x}" for i in range(min(8, len(xs)))],
        "sp": f"0x{res.get('sp', 0):x}", "lr": f"0x{res.get('lr', 0):x}",
        "flags": res.get("flags"), "out": res.get("out"), "read": (reads or None),
        "hinweis": ("Emulation (arm64mini). Komplexe WB-/Krypto-Funktionen brauchen ggf. Stubs/Snapshot "
                    "(GUI Run-Panel). Unbekannte Instruktion -> why=unknown_instr mit pc/raw."),
    }


# ---- M.15 native.run_break (O) — Emulation bis Breakpoint --------------------
def native_run_break(ctx, path, entry, args=None, sp=None, maxins=2000000,
                     stubs=None, breakpoints=None, read=None, relocs=False):
    """Emuliert eine Funktion und haelt VOR einer Breakpoint-Adresse an (nur an
    Basisblock-Eintritts-PCs, z. B. Funktions-/Sprungziele). O.
    breakpoints: Liste aus addr|'0x..'|Symbol. Rueckgabe: paused=True + pc/Register,
    sonst run-to-completion (ret/Out-Puffer wie native.run)."""
    try:
        rp, img = _native_load_image(ctx, path)
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}
    if not getattr(img, "is_aarch64", False):
        return {"ok": False, "hinweis": "kein AArch64-ELF (oder gepackt) — erst native.unpack."}
    idx = None
    try:
        idx = _native_build_index(ctx, rp, img, fast=True)
    except Exception:
        pass
    addr = _native_resolve_addr(img, idx, entry)
    if addr is None:
        return {"ok": False, "hinweis": f"entry nicht aufloesbar: {entry!r}"}
    bps = []
    for b in (breakpoints or []):
        ra = _native_resolve_addr(img, idx, b)
        if ra is not None:
            bps.append(ra)
    try:
        from services.native_emu_service import NativeEmuService
    except Exception as e:
        return {"ok": False, "hinweis": f"Emu-Service nicht ladbar: {e!r}"}
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    try:
        svc.load_image(eng, img, apply_relocs=relocs)
    except Exception as e:
        return {"ok": False, "hinweis": f"Laden in Engine: {e!r}"}
    try:
        if stubs:
            svc.install_stubs(eng, stubs, image=img)
        res = svc.run_with_breaks(eng, addr, args or [], None, sp, int(maxins),
                                  breakpoints=bps, image=img)
    except Exception as e:
        return {"ok": False, "hinweis": f"Run-Fehler: {e!r}"}
    if not res.get("ok"):
        base = {"ok": False, "path": rp, "entry": f"0x{addr:x}"}
        base.update({k: v for k, v in res.items() if k != "ok"})
        return base
    xs = res.get("x", [])
    reads = {}
    for rd in (read or []):
        try:
            ra = _native_resolve_addr(img, idx, rd.get("addr"))
            if ra is not None:
                reads[f"0x{ra:x}"] = svc.read_mem(eng, ra, int(rd.get("len", 16))).hex()
        except Exception:
            pass
    base = {
        "ok": True, "path": rp, "entry": f"0x{addr:x}", "paused": bool(res.get("paused")),
        "breakpoints": [f"0x{p:x}" for p in bps],
        "x0_7": [f"0x{xs[i]:x}" for i in range(min(8, len(xs)))],
        "sp": f"0x{res.get('sp', 0):x}", "lr": f"0x{res.get('lr', 0):x}",
        "pc": f"0x{res.get('pc', 0):x}", "flags": res.get("flags"), "read": (reads or None),
    }
    if res.get("paused"):
        base["bp"] = f"0x{res.get('bp', 0):x}"
        sid = _session_put({"svc": svc, "eng": eng, "img": img, "rp": rp})
        base["session"] = sid
        base["hinweis"] = (f"An Breakpoint angehalten (Basisblock-Eintritt). session={sid} "
                           "fuer native.step / native.resume.")
    else:
        base["ret"] = f"0x{res.get('ret', 0):x}"
        base["out"] = res.get("out")
        base["hinweis"] = "Kein Breakpoint getroffen -> run-to-completion."
    return base


# ---- M.16 native.lift (O) — Block->Python-Lifter ----------------------------
def native_lift(ctx, path, entry, mode="block", max_blocks=64, relocs=False):
    """Hebt einen Basisblock (mode='block') oder per BFS die erreichbaren Bloecke einer
    Funktion (mode='function') nach Python (bbsrc-Lifter). O. entry=addr|'0x..'|Symbol."""
    try:
        rp, img = _native_load_image(ctx, path)
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}
    if not getattr(img, "is_aarch64", False):
        return {"ok": False, "hinweis": "kein AArch64-ELF (oder gepackt) — erst native.unpack."}
    idx = None
    try:
        idx = _native_build_index(ctx, rp, img, fast=True)
    except Exception:
        pass
    addr = _native_resolve_addr(img, idx, entry)
    if addr is None:
        return {"ok": False, "hinweis": f"entry nicht aufloesbar: {entry!r}"}
    try:
        from services.native_emu_service import NativeEmuService
        from services.native_lift_service import NativeLiftService
    except Exception as e:
        return {"ok": False, "hinweis": f"Lift-Service nicht ladbar: {e!r}"}
    svc = NativeEmuService(publish=False); eng = svc.new_engine()
    try:
        svc.load_image(eng, img, apply_relocs=relocs)
    except Exception as e:
        return {"ok": False, "hinweis": f"Laden in Engine: {e!r}"}
    lift = NativeLiftService()
    try:
        if str(mode) == "function":
            res = lift.lift_function(eng, addr, int(max_blocks))
        else:
            res = lift.lift_block(eng, addr)
    except Exception as e:
        return {"ok": False, "hinweis": f"Lift-Fehler: {e!r}"}
    res["path"] = rp
    res.setdefault("hinweis", "Block->Python (arm64mini-bbsrc). Nachfolger intra-funktional "
                   "(Callees nicht verfolgt); dynamische Ziele (br/blr/ret) enden den Pfad.")
    return res


# ---- M.17 native.verify (O) — Bit-Exakt-Regression --------------------------
def native_verify(ctx, path, entry, args=None, sp=None, maxins=2000000, stubs=None,
                  name=None, update=False, read=None, relocs=False):
    """Emuliert und prueft gegen einen gespeicherten Golden-Zustand (ret/Register/Out).
    update=True (oder noch kein Golden) speichert den aktuellen Lauf als Golden. O."""
    if not name:
        return {"ok": False, "hinweis": "name= (Golden-Fallname) erforderlich."}
    try:
        rp, img = _native_load_image(ctx, path)
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}
    if not getattr(img, "is_aarch64", False):
        return {"ok": False, "hinweis": "kein AArch64-ELF (oder gepackt) — erst native.unpack."}
    idx = None
    try:
        idx = _native_build_index(ctx, rp, img, fast=True)
    except Exception:
        pass
    addr = _native_resolve_addr(img, idx, entry)
    if addr is None:
        return {"ok": False, "hinweis": f"entry nicht aufloesbar: {entry!r}"}
    try:
        from services.native_emu_service import NativeEmuService
        from services.native_verify_service import NativeVerifyService
    except Exception as e:
        return {"ok": False, "hinweis": f"Service nicht ladbar: {e!r}"}
    svc = NativeEmuService(publish=False); eng = svc.new_engine()
    try:
        svc.load_image(eng, img, apply_relocs=relocs)
        if stubs:
            svc.install_stubs(eng, stubs, image=img)
        res = svc.run(eng, addr, args or [], None, sp, int(maxins), image=img)
    except Exception as e:
        return {"ok": False, "hinweis": f"Run-Fehler: {e!r}"}
    if not res.get("ok"):
        base = {"ok": False, "path": rp, "entry": f"0x{addr:x}"}
        base.update({k: v for k, v in res.items() if k != "ok"})
        return base
    store = os.path.join(os.path.dirname(rp), "verify_golden.json")
    vs = NativeVerifyService(store)
    g = vs.get(name)
    if update or g is None:
        vs.set(name, vs.reduce(res), meta={"entry": f"0x{addr:x}", "args": args})
        return {"ok": True, "path": rp, "entry": f"0x{addr:x}", "name": name, "stored": True,
                "fields": sorted(vs.reduce(res).keys()),
                "hinweis": ("Golden neu gespeichert." if g is None else "Golden aktualisiert (update=True).")
                + f" ret=0x{res['ret']:x}"}
    cmp = vs.compare(g, res)
    return {"ok": True, "path": rp, "entry": f"0x{addr:x}", "name": name,
            "match": cmp["match"], "diffs": (cmp["diffs"] or None), "ret": f"0x{res['ret']:x}",
            "hinweis": ("Bit-Exakt gegen Golden." if cmp["match"]
                        else f"ABWEICHUNG in {len(cmp['diffs'])} Feld(ern) — siehe diffs.")}


# ---- M.18/19 Session-Zustand fuer interaktives Step/Resume ------------------
_NATIVE_SESSIONS = {}
_NATIVE_SESSION_SEQ = [0]


def _session_put(d):
    _NATIVE_SESSION_SEQ[0] += 1
    sid = f"s{_NATIVE_SESSION_SEQ[0]}"
    _NATIVE_SESSIONS[sid] = d
    while len(_NATIVE_SESSIONS) > 8:                 # nur die juengsten 8 Sessions halten
        old = min(_NATIVE_SESSIONS, key=lambda k: int(k[1:]))
        _NATIVE_SESSIONS.pop(old, None)
    return sid


def _fmt_native_state(res, session):
    xs = res.get("x", [])
    out = {"ok": res.get("ok", True), "session": session}
    if not res.get("ok"):
        out.update({k: v for k, v in res.items() if k != "ok"})
        return out
    for k in ("paused", "done"):
        if k in res:
            out[k] = res[k]
    out["pc"] = f"0x{res.get('pc', 0):x}"
    out["x0_7"] = [f"0x{xs[i]:x}" for i in range(min(8, len(xs)))]
    out["sp"] = f"0x{res.get('sp', 0):x}"
    out["lr"] = f"0x{res.get('lr', 0):x}"
    out["flags"] = res.get("flags")
    if isinstance(res.get("bp"), int):
        out["bp"] = f"0x{res['bp']:x}"
    if isinstance(res.get("ret"), int):
        out["ret"] = f"0x{res['ret']:x}"
    if res.get("done"):
        _NATIVE_SESSIONS.pop(session, None)
        out["session_ended"] = True
    return out


def native_step(ctx, session):
    """Ein Basisblock-Schritt in einer pausierten Session (session= von native.run_break). O."""
    s = _NATIVE_SESSIONS.get(session)
    if not s:
        return {"ok": False, "hinweis": f"session unbekannt/abgelaufen: {session!r}"}
    try:
        res = s["svc"].step(s["eng"])
    except Exception as e:
        return {"ok": False, "hinweis": f"Step-Fehler: {e!r}"}
    return _fmt_native_state(res, session)


def native_resume(ctx, session, breakpoints=None, maxins=2000000):
    """Fortsetzen bis zum naechsten Breakpoint oder Ende in einer pausierten Session. O."""
    s = _NATIVE_SESSIONS.get(session)
    if not s:
        return {"ok": False, "hinweis": f"session unbekannt/abgelaufen: {session!r}"}
    bps = []
    for b in (breakpoints or []):
        ra = _native_resolve_addr(s.get("img"), None, b)
        if ra is not None:
            bps.append(ra)
    try:
        res = s["svc"].resume(s["eng"], breakpoints=bps, maxins=int(maxins), image=s.get("img"))
    except Exception as e:
        return {"ok": False, "hinweis": f"Resume-Fehler: {e!r}"}
    return _fmt_native_state(res, session)


# ---- M.20 native.rebase (O) — Offsets ueber Lib-Versionen --------------------
def native_rebase(ctx, path_old, path_new, target=None, mode="auto", limit=40):
    """Adressen/Symbole von path_old auf path_new abbilden (dynsym-Name-Match).
    target=Symbol|'0x..' -> Mapping; mode='diff' oder target leer -> Versions-Diff. O."""
    try:
        rpo, imo = _native_load_image(ctx, path_old)
        rpn, imn = _native_load_image(ctx, path_new)
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}
    try:
        from services.native_rebase_service import NativeRebaseService
    except Exception as e:
        return {"ok": False, "hinweis": f"Service nicht ladbar: {e!r}"}
    svc = NativeRebaseService()
    if mode == "diff" or not target:
        d = svc.diff(imo, imn, int(limit))
        d.update({"old": rpo, "new": rpn})
        return d
    t = str(target).strip()
    if t.lower().startswith("0x") or t.isdigit():
        r = svc.map_address(imo, imn, int(t, 0))
    else:
        r = svc.map_symbol(imo, imn, t)
    r.update({"old": rpo, "new": rpn})
    for k in ("old_addr", "new_addr", "old_func", "new_func"):
        if isinstance(r.get(k), int):
            r[k] = f"0x{r[k]:x}"
    return r


# ---- M.21 native.coverage (O) — Coverage/Unknown-Report ---------------------
def native_coverage(ctx, path, entry, args=None, sp=None, maxins=2000000, stubs=None, top=25, relocs=False):
    """Emuliert und berichtet Coverage (Mnemonic-Zaehlung), Slow-Pfad-Mnemonics
    (Kandidaten fuer schnellen Emit) und unbekannte Instruktionen (pc/raw). O."""
    try:
        rp, img = _native_load_image(ctx, path)
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}
    if not getattr(img, "is_aarch64", False):
        return {"ok": False, "hinweis": "kein AArch64-ELF (oder gepackt) — erst native.unpack."}
    idx = None
    try:
        idx = _native_build_index(ctx, rp, img, fast=True)
    except Exception:
        pass
    addr = _native_resolve_addr(img, idx, entry)
    if addr is None:
        return {"ok": False, "hinweis": f"entry nicht aufloesbar: {entry!r}"}
    try:
        from services.native_emu_service import NativeEmuService
        from services.native_coverage_service import NativeCoverageService
    except Exception as e:
        return {"ok": False, "hinweis": f"Service nicht ladbar: {e!r}"}
    svc = NativeEmuService(publish=False); eng = svc.new_engine()
    try:
        svc.load_image(eng, img, apply_relocs=relocs)
        if stubs:
            svc.install_stubs(eng, stubs, image=img)
        cov = NativeCoverageService().run_coverage(eng, addr, args or [], sp, int(maxins), image=img)
    except Exception as e:
        return {"ok": False, "hinweis": f"Coverage-Fehler: {e!r}"}
    mnem = cov.get("mnemonics", {})
    top_m = sorted(mnem.items(), key=lambda kv: (-kv[1], kv[0]))[:int(top)]
    slow = cov.get("slow_path", {})
    return {
        "ok": True, "path": rp, "entry": f"0x{addr:x}", "ran_ok": cov.get("ran_ok"),
        "blocks_reached": cov.get("blocks_reached"), "instr_total": cov.get("instr_total"),
        "distinct_mnemonics": len(mnem),
        "top_mnemonics": [{"m": m, "n": n} for m, n in top_m],
        "slow_path": [{"m": m, "n": n} for m, n in sorted(slow.items(), key=lambda kv: (-kv[1], kv[0]))],
        "ret": (f"0x{cov['ret']:x}" if cov.get("ret") is not None else None),
        "unknown": cov.get("unknown"), "why": cov.get("why"),
        "hinweis": "slow_path = Mnemonics ohne schnellen Emit (Kandidaten); unknown = harte Luecke (pc/raw).",
    }


# ---- M.22 native.snapshot (P) — Zustand speichern/laden ---------------------
def native_snapshot(ctx, path, entry, op="save", out=None, load=None,
                    args=None, sp=None, maxins=2000000, stubs=None, relocs=False):
    """op='save': ab entry ausfuehren und Zustand (Mem+Register) als .pkl.xz speichern.
    op='load': Snapshot in eine frische Engine laden und ab entry ausfuehren. P (save schreibt Datei).
    out/load relativ -> neben der .so (sha_dir); absolut wird uebernommen."""
    try:
        rp, img = _native_load_image(ctx, path)
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}
    if not getattr(img, "is_aarch64", False):
        return {"ok": False, "hinweis": "kein AArch64-ELF (oder gepackt) — erst native.unpack."}
    idx = None
    try:
        idx = _native_build_index(ctx, rp, img, fast=True)
    except Exception:
        pass
    addr = _native_resolve_addr(img, idx, entry)
    if addr is None:
        return {"ok": False, "hinweis": f"entry nicht aufloesbar: {entry!r}"}
    try:
        from services.native_emu_service import NativeEmuService
    except Exception as e:
        return {"ok": False, "hinweis": f"Service nicht ladbar: {e!r}"}
    svc = NativeEmuService(publish=False); eng = svc.new_engine()
    try:
        svc.load_image(eng, img, apply_relocs=relocs)
        if stubs:
            svc.install_stubs(eng, stubs, image=img)
    except Exception as e:
        return {"ok": False, "hinweis": f"Laden in Engine: {e!r}"}
    base_dir = os.path.dirname(rp)

    if str(op) == "load":
        if not load:
            return {"ok": False, "hinweis": "op='load' braucht load=<snapshot.pkl.xz>"}
        lp = load if os.path.isabs(load) else os.path.join(base_dir, load)
        if not os.path.isfile(lp):
            return {"ok": False, "hinweis": f"Snapshot nicht gefunden: {lp}"}
        try:
            svc.load_snapshot(eng, lp)
        except Exception as e:
            return {"ok": False, "hinweis": f"Snapshot laden: {e!r}"}
        res = svc.run(eng, addr, args or [], None, sp, int(maxins), image=img)
        if not res.get("ok"):
            b = {"ok": False, "path": rp, "entry": f"0x{addr:x}", "loaded": lp}
            b.update({k: v for k, v in res.items() if k != "ok"}); return b
        return {"ok": True, "path": rp, "entry": f"0x{addr:x}", "loaded": lp,
                "ret": f"0x{res['ret']:x}", "hinweis": "Snapshot geladen, dann ab entry ausgefuehrt."}

    # op == 'save'
    res = svc.run(eng, addr, args or [], None, sp, int(maxins), image=img)
    if not res.get("ok"):
        b = {"ok": False, "path": rp, "entry": f"0x{addr:x}"}
        b.update({k: v for k, v in res.items() if k != "ok"}); return b
    outp = out if (out and os.path.isabs(out)) else os.path.join(base_dir, out or f"snap_0x{addr:x}.pkl.xz")
    try:
        svc.save_snapshot(eng, outp)
    except Exception as e:
        return {"ok": False, "hinweis": f"Snapshot speichern: {e!r}"}
    return {"ok": True, "path": rp, "entry": f"0x{addr:x}", "saved": outp,
            "ret": f"0x{res['ret']:x}", "hinweis": "Zustand nach dem Lauf gespeichert (Mem+Register, lzma)."}


# ---- M.23 native.resolveptr (O) — Reloc/Zeiger statisch aufloesen -----------
def native_resolveptr(ctx, path, addr=None, around=None, radius=64):
    """Loest einen Reloc-Slot auf (addr=) oder listet Relocs um eine Adresse (around=). O.
    RELATIVE-Reloc -> target=addend = echter Zeiger (statisch in der Datei NULL)."""
    try:
        rp, img = _native_load_image(ctx, path)
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}
    try:
        from services.native_reloc_service import NativeRelocService
    except Exception as e:
        return {"ok": False, "hinweis": f"Service nicht ladbar: {e!r}"}
    svc = NativeRelocService()
    if around is not None:
        r = svc.around(img, around, int(radius))
        return {"ok": True, "path": rp, "addr": f"0x{r['addr']:x}", "count": r["count"],
                "relocs": [{"site": f"0x{x['site']:x}", "target": f"0x{x['target']:x}"} for x in r["relocs"]],
                "hinweis": "Relocs (site -> target) im Umkreis."}
    if addr is not None:
        r = svc.resolve(img, addr)
        return {"ok": r["ok"], "path": rp, "addr": f"0x{r['addr']:x}",
                "target": (f"0x{r['target']:x}" if r["target"] is not None else None),
                "hinweis": ("Reloc-Ziel (RELATIVE=addend)." if r["ok"]
                            else "Keine Reloc an dieser Adresse (kein durch Reloc gefuellter Zeiger).")}
    return {"ok": True, "path": rp, "reloc_count": svc.count(img),
            "hinweis": "addr=0x.. zum Aufloesen oder around=0x.. (+radius) zum Listen."}


# ---- M.xx native.dbg_* (O) — geteilte Debug-Sitzung GUI<->KI --------------------
_DBG_SESSION_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "secrets", "probe", "out", "dbg")
_STUDIO_SESSION_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "secrets", "probe", "out", "studio")
_OS_SESSION_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "secrets", "probe", "out", "os")
_RECIPES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "secrets", "probe", "out", "workflow_recipes.json")


def _dbg_folder(folder):
    return folder or _DBG_SESSION_FOLDER


def native_dbg_state(ctx, folder=None):
    """Geteilte Debug-Sitzung ansehen (read-only). O. In der GUI vorher ⬆ An KI."""
    try:
        from services.native_debug_session_service import NativeDebugSessionService
    except Exception as e:
        return {"ok": False, "hinweis": f"Service nicht ladbar: {e!r}"}
    pk = NativeDebugSessionService().peek(_dbg_folder(folder))
    meta = pk.get("meta")
    if not meta:
        return {"ok": False, "hinweis": f"keine Sitzung in {_dbg_folder(folder)} — in der GUI erst ⬆ An KI."}
    tr = pk.get("trace") or []
    # G-OS-0: OS-Zustand (letzte os_attach-Summary) neben der Debug-Sitzung mitfuehren
    os_state = None
    try:
        import json as _json
        _fp = os.path.join(_OS_SESSION_FOLDER, "last_summary.json")
        if os.path.isfile(_fp):
            with open(_fp, encoding="utf-8") as _f:
                os_state = (_json.load(_f) or {}).get("summary")
    except Exception:
        os_state = None
    return {"ok": True, "folder": _dbg_folder(folder),
            "pc": f"0x{int(meta.get('pc', 0)):x}", "seq": meta.get("seq"), "writer": meta.get("writer"),
            "entry": meta.get("entry"), "relocs": meta.get("relocs"), "mode": meta.get("mode"),
            "flags": meta.get("flags"), "ts": meta.get("ts"),
            "bps": [f"0x{int(b):x}" for b in (meta.get("bps") or [])],
            "trace_len": len(tr), "trace_tail": [e.get("pc_hex") for e in tr[-10:]],
            "os": os_state}


def native_dbg_lift(ctx, folder=None):
    """Geteilte Debug-Sitzung laden und den gelaufenen Pfad nach Python liften (read-only). O."""
    try:
        from services.native_emu_service import NativeEmuService
        from services.native_debug_session_service import NativeDebugSessionService
        from services.native_trace_decompile_service import NativeTraceDecompileService
    except Exception as e:
        return {"ok": False, "hinweis": f"Service nicht ladbar: {e!r}"}
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    ld = NativeDebugSessionService().load(eng, _dbg_folder(folder))
    if not ld.get("ok"):
        return {"ok": False, "hinweis": "keine Sitzung — in der GUI erst ⬆ An KI."}
    lr = NativeTraceDecompileService().lift_to_function(eng)
    if not lr.get("ok"):
        return {"ok": False, "why": lr.get("why"), "residual": lr.get("residual"),
                "hinweis": "Lift unvollstaendig."}
    return {"ok": True, "entry": lr.get("entry"), "visits": lr.get("visits"),
            "unique": lr.get("unique"), "pc": ld.get("pc"),
            "seq": (ld.get("meta") or {}).get("seq"), "source": lr.get("source")}


def _dbg_open(folder):
    """Geteilte Sitzung in eine frische Engine laden. -> (sess, svc, eng, base_seq, meta) oder (None,...)."""
    from services.native_emu_service import NativeEmuService
    from services.native_debug_session_service import NativeDebugSessionService
    sess = NativeDebugSessionService()
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    ld = sess.load(eng, _dbg_folder(folder))
    if not ld.get("ok"):
        return None, None, None, None, None
    meta = dict(ld.get("meta") or {})
    return sess, svc, eng, int(meta.get("seq", 0) or 0), meta


def native_dbg_step(ctx, folder=None, mode="block", n=1):
    """Geteilte Debug-Sitzung n Schritte weiter + zurueckschreiben (writer=ki). G: dbg_write."""
    try:
        from services.native_trace_service import NativeTraceService
    except Exception as e:
        return {"ok": False, "hinweis": f"Service nicht ladbar: {e!r}"}
    sess, svc, eng, base, meta = _dbg_open(folder)
    if sess is None:
        return {"ok": False, "hinweis": "keine Sitzung — in der GUI erst ⬆ An KI."}
    tr = NativeTraceService(publish=False)
    r = None
    for _ in range(max(1, min(int(n), 2000))):
        r = (tr.step_insn if mode == "insn" else tr.step_block)(eng)
        if not r.get("ok") or r.get("done"):
            break
    sv = sess.save(eng, _dbg_folder(folder), meta=meta, writer="ki", base_seq=base)
    if not sv.get("ok") and sv.get("why") == "conflict":
        return {"ok": False, "why": "conflict",
                "hinweis": f"GUI ist weiter (Datei seq {sv['file_seq']}) — NICHT ueberschrieben."}
    d = svc.snapshot_regs(eng)
    return {"ok": True, "pc": (r or {}).get("pc_hex"), "done": bool((r or {}).get("done")),
            "x0": f"0x{d['x'][0]:x}", "seq": sv.get("seq"),
            "trace_len": len(getattr(eng, "_trace", []) or [])}


def native_dbg_resume(ctx, folder=None, budget=4000, mode=None):
    """Geteilte Debug-Sitzung bis Breakpoint/Budget fortsetzen + zurueckschreiben. G: dbg_write."""
    try:
        from services.native_trace_service import NativeTraceService
    except Exception as e:
        return {"ok": False, "hinweis": f"Service nicht ladbar: {e!r}"}
    sess, svc, eng, base, meta = _dbg_open(folder)
    if sess is None:
        return {"ok": False, "hinweis": "keine Sitzung — in der GUI erst ⬆ An KI."}
    bps = []
    for b in (meta.get("bps") or []):
        try:
            bps.append(int(b) if isinstance(b, int) else int(str(b), 0))
        except Exception:
            pass
    m = mode or ("insn" if bps else "block")
    tr = NativeTraceService(publish=False)
    r = tr.resume(eng, breakpoints=bps, maxins=int(budget), mode=m)
    sv = sess.save(eng, _dbg_folder(folder), meta=meta, writer="ki", base_seq=base)
    if not sv.get("ok") and sv.get("why") == "conflict":
        return {"ok": False, "why": "conflict",
                "hinweis": f"GUI ist weiter (Datei seq {sv['file_seq']}) — NICHT ueberschrieben."}
    stop = ("breakpoint" if r.get("paused") else "done" if r.get("done")
            else "budget" if r.get("budget") else "?")
    return {"ok": True, "stop": stop, "pc": r.get("pc_hex"),
            "bp": (f"0x{r['bp']:x}" if r.get("bp") else None),
            "steps": r.get("steps"), "seq": sv.get("seq")}


def _jni_lib_path():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "tools", "lib", "libdb0c.so")


def native_jni_run(ctx, budget=400000, stop_at_rasp=True, lib=None):
    """JNI_OnLoad mit Fake-JNIEnv + Reloc + Import-Wiring emulieren (read-only Analyse).
    Fortschritt/Stop + JNI-Call-Map; stop_at_rasp haelt an den 4 RASP-Routinen an."""
    try:
        from core.native.loader import ElfImage
        from services.native_emu_service import NativeEmuService
        from services.native_ffi_service import NativeFfiService
        from services.native_trace_service import NativeTraceService
        from services import native_jni_env_service as Jm
        from services.native_jni_env_service import NativeJniEnvService
    except Exception as e:
        return {"ok": False, "hinweis": f"Service nicht ladbar: {e!r}"}
    path = lib or _jni_lib_path()
    if not os.path.isfile(path):
        return {"ok": False, "hinweis": f"Lib fehlt: {path}"}
    img = ElfImage.from_file(path)
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    nrel = svc.load_image(eng, img, apply_relocs=True)
    ffi = NativeFfiService(publish=False)
    w = ffi.wire_imports(eng, img)
    jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)
    entry = svc.resolve_entry("JNI_OnLoad", img)
    tr = NativeTraceService(publish=False)
    tr.begin(eng, entry, args=[Jm.VM_OBJ], faithful=True)
    bps = [0x8e158, 0x3e7d8, 0x37cd4, 0x37b88] if stop_at_rasp else []
    r = tr.run(eng, breakpoints=bps, maxins=int(budget), mode="block")
    globals()["_LAST_JNI"] = jni
    stop = ("rasp_bp" if r.get("paused") else "done" if r.get("done")
            else "budget" if r.get("budget") else "error" if r.get("why") == "error" else "?")
    head = []
    for c in jni.get_calls():
        if c.get("fn") == "FindClass" or c.get("fn") == "RegisterNatives" or "method" in c:
            head.append({k: c.get(k) for k in ("fn", "class", "method", "sig", "category",
                                               "proposed", "final", "resolved", "count")
                         if c.get(k) is not None})
        if len(head) >= 40:
            break
    return {"ok": True, "stop": stop, "pc": r.get("pc_hex"),
            "bp": (f"0x{r['bp']:x}" if r.get("bp") else None), "steps": r.get("steps"),
            "trace_len": len(tr.get_trace(eng)), "relocs": nrel, "imports": w.get("count"),
            "jni": jni.summary(), "natives": jni.get_natives()[:50], "calls_head": head,
            "error": r.get("error")}


def native_dispatch_trace(ctx, budget=3000000, run_into_trap=True, lib=None,
                          vt_base=0x137740, lo=0x136000, hi=0x13a000, cap=1200):
    """CFF-2D-vtable Dispatch-Tracer: JNI_OnLoad emulieren und jeden vtable-Dispatch
    (Slot/idx1/col/Wert/leer/Taint/x19/x20) protokollieren. Liefert Summary + erste getaintete
    Divergenz + ersten leeren Slot (Trap) + Dispatch-Liste. read-only."""
    try:
        from core.native.loader import ElfImage
        from services.native_emu_service import NativeEmuService
        from services.native_ffi_service import NativeFfiService
        from services.native_trace_service import NativeTraceService
        from services import native_jni_env_service as Jm
        from services.native_jni_env_service import NativeJniEnvService
    except Exception as e:
        return {"ok": False, "hinweis": f"Service nicht ladbar: {e!r}"}
    path = lib or _jni_lib_path()
    if not os.path.isfile(path):
        return {"ok": False, "hinweis": f"Lib fehlt: {path}"}
    img = ElfImage.from_file(path)
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    svc.load_image(eng, img, apply_relocs=True)
    ffi = NativeFfiService(publish=False)
    ffi.wire_imports(eng, img)
    jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)
    entry = svc.resolve_entry("JNI_OnLoad", img)
    tr = NativeTraceService(publish=False)
    tr.dispatch_trace_enable(eng, int(vt_base), int(lo), int(hi))
    tr.begin(eng, entry, args=[Jm.VM_OBJ], faithful=True)
    bps = [] if run_into_trap else [0x374e0]
    r = tr.run(eng, breakpoints=bps, maxins=int(budget), mode="block")
    tr.dispatch_trace_disable(eng)
    summ = tr.dispatch_trace_summary(eng)
    log = tr.dispatch_trace_get(eng)

    def hx(e):
        return {"pc": hex(e["pc"]), "slot": hex(e["slot"]), "idx1": e["idx1"], "col": e["col"],
                "val": hex(e["val"]), "empty": e["empty"], "val_taint": e["val_taint"],
                "x20": hex(e["x20"]), "x20_taint": e["x20_taint"]}
    sample = [hx(e) for e in log[:int(cap)]]
    stop = ("done" if r.get("done") else "budget" if r.get("budget")
            else "trap/error" if r.get("why") == "error"
            else "paused" if r.get("paused") else "?")
    return {"ok": True, "stop": stop, "pc": r.get("pc_hex"), "steps": r.get("steps"),
            "summary": summ, "dispatch_count": len(log), "log": sample}


_SYS_AARCH64 = {56: "openat", 57: "close", 63: "read", 64: "write", 62: "lseek", 67: "pread64",
                48: "faccessat", 79: "newfstatat", 43: "statfs", 44: "fstatfs", 78: "readlinkat",
                61: "getdents64", 172: "getpid", 178: "gettid", 173: "getppid", 174: "getuid",
                167: "prctl", 117: "ptrace", 169: "gettimeofday", 113: "clock_gettime",
                129: "kill", 131: "tgkill", 93: "exit", 94: "exit_group", 220: "clone",
                98: "futex", 222: "mmap", 226: "mprotect", 215: "munmap", 160: "uname",
                179: "sysinfo", 198: "socket", 203: "connect", 278: "getrandom", 101: "nanosleep",
                233: "madvise", 73: "ppoll", 135: "rt_sigprocmask", 134: "rt_sigaction"}


def native_survive_run(ctx, budget=8000000, lib=None):
    """RASP Survival Mode + Syscall-Trace: JNI_OnLoad ueber die Anti-Tamper-Trap hinaus laufen
    lassen (tote Dispatches ueberleben) und jeden rohen svc-Syscall (nr=x8, args x0-x5) mit
    aufgeloestem Pfad-Argument aufzeichnen = die Detection-/Reaktions-Syscalls. read-only."""
    try:
        from core.native.loader import ElfImage
        from services.native_emu_service import NativeEmuService
        from services.native_ffi_service import NativeFfiService
        from services.native_trace_service import NativeTraceService
        from services import native_jni_env_service as Jm
        from services.native_jni_env_service import NativeJniEnvService
    except Exception as e:
        return {"ok": False, "hinweis": f"Service nicht ladbar: {e!r}"}
    path = lib or _jni_lib_path()
    if not os.path.isfile(path):
        return {"ok": False, "hinweis": f"Lib fehlt: {path}"}
    img = ElfImage.from_file(path)
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    svc.load_image(eng, img, apply_relocs=True)
    ffi = NativeFfiService(publish=False)
    ffi.wire_imports(eng, img)
    jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)
    entry = svc.resolve_entry("JNI_OnLoad", img)
    tr = NativeTraceService(publish=False)
    _pol = _load_syscall_policy()
    ffi.install_syscall_hook(eng, returns=(_pol.get("by_nr") or {}),
                             default_ret=int(_pol.get("default_ret", 0)))
    tr.survive_enable(eng, min_pc=0x1000, cap=50000)
    tr.begin(eng, entry, args=[Jm.VM_OBJ], faithful=True)
    r = tr.run(eng, breakpoints=[], maxins=int(budget), mode="block")
    slog = ffi.get_syscall_log(eng)
    sv = tr.survive_get(eng)
    c = eng.cpu

    def cstr(a, maxn=160):
        try:
            a = int(a)
            if not a:
                return None
            out = bytearray()
            for _ in range(maxn):
                b = c.m.rb(a, 1); a += 1
                if not b or b[0] == 0:
                    break
                if b[0] < 0x20 or b[0] > 0x7e:
                    return None
                out.append(b[0])
            return out.decode("ascii", "replace") if out else None
        except Exception:
            return None

    syscalls = []
    for e in slog[:200]:
        ar = e.get("args", [])
        p = cstr(ar[1]) if len(ar) > 1 else None
        if not p and ar:
            p = cstr(ar[0])
        syscalls.append({"nr": e["nr"], "name": _SYS_AARCH64.get(e["nr"], f"sys_{e['nr']}"),
                         "pc": hex(e.get("pc", 0)), "path": p,
                         "args": [hex(x) for x in ar[:4]]})
    stop = ("done" if r.get("done") else "survive_cap" if r.get("survive_cap")
            else "budget" if r.get("budget") else "error" if r.get("why") == "error" else "?")
    # JNI-Detection-Oberflaeche mitliefern (nicht nur Syscalls): die Fassade ist installiert,
    # also sind die JNI-Calls hinter der Trap = die JNI-vermittelte Detection sichtbar.
    try:
        jsum = jni.summary()
        jni_out = {"calls": jsum.get("calls"), "natives": jsum.get("natives"),
                   "by_fn": jsum.get("by_fn"), "by_category": jsum.get("by_category")}
    except Exception:
        jni_out = None
    # Trap-Herkunft: dead_pc/ret_to der ueberlebten Dispatches (Stichprobe)
    survived = [{"dead_pc": e.get("dead_pc") or e.get("at"), "ret_to": e.get("ret_to")}
                for e in sv[:40]]
    return {"ok": True, "stop": stop, "pc": r.get("pc_hex"), "steps": r.get("steps"),
            "survived_dead_dispatches": len(sv), "survived": survived,
            "syscall_count": len(slog), "syscalls": syscalls, "jni": jni_out}


# Plausible, geraetetypische /proc/self/maps-Stichprobe (Fake-Umgebung, WS-E3).
_DEFAULT_FAKE_MAPS = (
    "12c00000-12e00000 rw-p 00000000 00:00 0 [anon:dalvik-main space]\n"
    "7000000000-7000135000 r-xp 00000000 fd:00 1  /data/app/base.apk!/lib/arm64-v8a/libdb0c.so\n"
    "7000135000-7000140000 rw-p 00135000 fd:00 1  /data/app/base.apk!/lib/arm64-v8a/libdb0c.so\n"
    "7fffffe000-8000000000 rw-p 00000000 00:00 0 [stack]\n"
)


def _merge_os_config(cfg_d, net_replay, net_mode, trace, trace_max, syscall_policy):
    """OS-9-Komfortparameter in ein state.config-dict falten (rein, testbar)."""
    cfg_d = dict(cfg_d or {})
    if net_replay is not None:
        cfg_d["net_replay"] = {str(k): (v.encode("utf-8") if isinstance(v, str) else bytes(v))
                               for k, v in net_replay.items()}
        cfg_d.setdefault("net_mode", "replay")
    if isinstance(net_mode, str) and net_mode.strip():
        cfg_d["net_mode"] = net_mode.strip()
    if trace:
        cfg_d["trace_syscalls"] = True
        cfg_d["trace_max"] = int(trace_max)
    if syscall_policy is not None:
        cfg_d["syscall_policy"] = {str(k): str(v) for k, v in syscall_policy.items()}
    return cfg_d


def native_os_attach(ctx, entry="JNI_OnLoad", maps=None, vfs=None, config=None,
                     budget=200000, survive=True, lib=None,
                     net_replay=None, net_mode=None, trace=False, trace_max=4096,
                     syscall_policy=None):
    """N2 Env/Syscall-Substrat (native_os_service) anhaengen und eine Funktion unter
    kontrollierter Fake-Umgebung laufen lassen. Nutzt den KONVERGIERTEN Syscall-Pfad
    (Seed-Policy -> genuiner FakeEnvironment/VFS/mmap-Handler -> Default) statt des flachen
    ffi-Hooks = die WS-E3-Steuerflaeche generisch bedienbar. read-only.
      maps   = gefaelschte /proc/self/maps (str; Default = geraetetypische Stichprobe)
      vfs    = {pfad: inhalt} (dict oder JSON-String) -> VFS-Backend von openat/read
      config = {uid,pid,tid,...} (dict oder JSON-String) -> Identitaets-Syscalls
      entry  = JNI_OnLoad | Hex-Adresse (laeuft mit x0=VM/Env-Fassade, faithful).
    Hinweis Budget: Default 200k haelt den Lauf schnell (~1-2 s). Hoehere Budgets koennen die
    genuine Umgebung ueber den Init hinaus in den nativen Krypto-Decrypt lenken (langsame
    NEON-Step-Emulation) -- das ist ein echter WS-E3-Effekt, kein Hang. Fuer die schnelle, volle
    Detection-Syscall-Spur bis done nutze 'native.survive_run' (flache Policy)."""
    try:
        from core.native.loader import ElfImage
        from services.native_emu_service import NativeEmuService
        from services.native_ffi_service import NativeFfiService
        from services.native_trace_service import NativeTraceService
        from services.native_os_service import NativeOsService
        from services import native_jni_env_service as Jm
        from services.native_jni_env_service import NativeJniEnvService
    except Exception as e:
        return {"ok": False, "hinweis": f"Service nicht ladbar: {e!r}"}
    import json
    path = lib or _jni_lib_path()
    if not os.path.isfile(path):
        return {"ok": False, "hinweis": f"Lib fehlt: {path}"}

    def _asdict(v):
        if isinstance(v, dict):
            return v
        if isinstance(v, str) and v.strip():
            try:
                d = json.loads(v)
                return d if isinstance(d, dict) else None
            except Exception:
                return None
        return None

    vfs_d = _asdict(vfs) or {}
    cfg_d = _merge_os_config(_asdict(config) or {}, _asdict(net_replay), net_mode,
                             trace, trace_max, _asdict(syscall_policy))
    vfs_b = {str(k): (val.encode("utf-8") if isinstance(val, str) else bytes(val))
             for k, val in vfs_d.items()}
    maps_s = maps if isinstance(maps, str) and maps.strip() else _DEFAULT_FAKE_MAPS

    img = ElfImage.from_file(path)
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    svc.load_image(eng, img, apply_relocs=True)
    ffi = NativeFfiService(publish=False)
    ffi.wire_imports(eng, img)
    jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)

    # N2-Substrat: Env + VFS + konvergierter Syscall-Pfad (Presets bleiben als Seed-Policy wirksam)
    osv = NativeOsService(publish=False)
    osv.attach_env(eng, maps=maps_s)
    if vfs_b:
        osv.vfs_mount(eng, vfs_b)
    pol = _load_syscall_policy()
    seed = {}
    for k, val in (pol.get("by_nr") or {}).items():
        try:
            seed[int(k)] = val
        except Exception:
            pass
    osv.install(eng, seed_policy=seed, default_ret=int(pol.get("default_ret", 0)),
                config=(cfg_d or None))

    # Entry aufloesen: Symbol zuerst, dann Hex-Adresse
    entry_addr = None
    try:
        entry_addr = svc.resolve_entry(entry, img)
    except Exception:
        entry_addr = None
    if entry_addr is None:
        try:
            s = str(entry).strip()
            if s.lower().startswith("0x"):
                entry_addr = int(s, 16)
        except Exception:
            entry_addr = None
    if entry_addr is None:
        return {"ok": False, "hinweis": f"Entry nicht aufloesbar: {entry!r}"}

    tr = NativeTraceService(publish=False)
    if survive:
        tr.survive_enable(eng, min_pc=0x1000, cap=50000)
    tr.begin(eng, int(entry_addr), args=[Jm.VM_OBJ], faithful=True)
    r = tr.run(eng, breakpoints=[], maxins=int(budget), mode="block")
    stop = ("done" if r.get("done") else "survive_cap" if r.get("survive_cap")
            else "budget" if r.get("budget") else "error" if r.get("why") == "error" else "?")

    osum = osv.summary(eng)
    try:
        import json as _json, time as _time
        os.makedirs(_OS_SESSION_FOLDER, exist_ok=True)
        with open(os.path.join(_OS_SESSION_FOLDER, "last_summary.json"), "w", encoding="utf-8") as _f:
            _json.dump({"ts": _time.time(), "entry": str(entry), "stop": stop,
                        "summary": osum}, _f, ensure_ascii=False, indent=2, default=str)
        from services.native_os_session_service import NativeOsSessionService as _OSS
        _OSS().save(_OS_SESSION_FOLDER, summary=osum, writer="run")
    except Exception:
        pass
    sv = tr.survive_get(eng)
    survived = [{"dead_pc": e.get("dead_pc") or e.get("at"), "ret_to": e.get("ret_to")}
                for e in sv[:40]]
    try:
        jsum = jni.summary()
        jni_out = {"calls": jsum.get("calls"), "natives": jsum.get("natives"),
                   "by_category": jsum.get("by_category")}
    except Exception:
        jni_out = None
    return {"ok": True, "stop": stop, "pc": r.get("pc_hex"), "steps": r.get("steps"),
            "env": {"maps_bytes": len(maps_s.encode("utf-8")),
                    "vfs": sorted(vfs_b.keys()),
                    "config": {k: cfg_d.get(k) for k in ("uid", "pid", "tid") if k in cfg_d}},
            "syscalls": osum, "survived_dead_dispatches": len(sv), "survived": survived,
            "jni": jni_out}


def native_os_summary(ctx):
    """Read-only: letzte OS-Substrat-Summary (coverage/trace/net/threads/signals/auxv/policy) aus der
    letzten native.os_attach-Session lesen (secrets/probe/out/os/last_summary.json).

    POLICY-EBENEN (wichtig, zwei getrennte Schichten):
      1) Seed-Policy  — NR-NUMMERN-keyed {default_ret, by_nr}, geraeteechte svc-Rueckgaben
         (native.syscall_policy_get/set, persistiert). Fuer survive_run/genuine.
      2) OS-9-Policy  — NAMENS-keyed {name: allow|deny|stub} (config["syscall_policy"], pro Lauf).
         deny -> -EPERM, stub -> 0. Ueber native.os_attach(syscall_policy=...)."""
    import json as _json
    fp = os.path.join(_OS_SESSION_FOLDER, "last_summary.json")
    if not os.path.isfile(fp):
        return {"ok": False, "hinweis": "Noch keine os_attach-Session: zuerst native.os_attach laufen lassen."}
    try:
        with open(fp, encoding="utf-8") as f:
            data = _json.load(f)
        return {"ok": True, **data}
    except Exception as e:
        return {"ok": False, "hinweis": f"Summary nicht lesbar: {e!r}"}


def native_os_config_get(ctx):
    """Read-only: geteilten OS-Inspektor-Zustand (config-Delta + letzte Summary + seq/writer) lesen."""
    from services.native_os_session_service import NativeOsSessionService
    sess = NativeOsSessionService().peek(_OS_SESSION_FOLDER).get("session")
    if not sess:
        return {"ok": False, "hinweis": "kein OS-Zustand — in der GUI erst '⬆ OS→KI' druecken."}
    return {"ok": True, **sess}


def native_os_config_set(ctx, config=None, base_seq=None, force=False):
    """OS-Inspektor-config setzen (writer=ki, Clobber-Schutz ueber base_seq). G: os_write.
    config = {syscall_policy, syscall_overrides, net_replay, net_mode, rand_seed, trace_syscalls,
    vfs_mounts, ...} (dict oder JSON-String). Der Mensch zieht in der GUI mit '⬇ OS←KI' nach."""
    import json as _json
    cfg = config
    if isinstance(cfg, str) and cfg.strip():
        try:
            cfg = _json.loads(cfg)
        except Exception:
            return {"ok": False, "hinweis": "config kein gueltiges JSON-Objekt"}
    if not isinstance(cfg, dict):
        return {"ok": False, "hinweis": "config fehlt oder ist kein Objekt"}
    from services.native_os_session_service import NativeOsSessionService
    return NativeOsSessionService().save(_OS_SESSION_FOLDER, config=cfg, writer="ki",
                                         base_seq=base_seq, force=bool(force))


def _syscall_policy_path():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "secrets", "probe", "out", "syscall_policy.json")


# geraeteechte Default-Policy (nicht-root, SELinux vorhanden, plausible IDs)
_GENUINE_SYSCALL_POLICY = {"default_ret": 0, "by_nr": {
    "79": {"ret": 0, "zero_arg": 2, "zero_len": 128},   # newfstatat -> existiert, statbuf genullt
    "48": 0,                                             # faccessat -> ok
    "172": 12345, "178": 12345, "173": 12300,            # getpid/gettid/getppid
    "174": 10234,                                        # getuid -> App-UID (nicht root)
    "113": 0, "169": 0, "160": 0, "179": 0}}             # clock_gettime/gettimeofday/uname/sysinfo


def _load_syscall_policy():
    import json
    try:
        with open(_syscall_policy_path(), encoding="utf-8") as f:
            p = json.load(f)
        if isinstance(p, dict):
            p.setdefault("default_ret", 0)
            p.setdefault("by_nr", {})
            return p
    except Exception:
        pass
    return {"default_ret": 0, "by_nr": {}}


def native_syscall_policy_get(ctx):
    """Aktuelle Syscall-Policy lesen: {default_ret, by_nr{nr: ret|{ret,zero_arg,zero_len}}}."""
    return {"ok": True, "path": _syscall_policy_path(), "policy": _load_syscall_policy(),
            "presets": ["genuine", "clear"]}


def native_syscall_policy_set(ctx, policy=None, preset=None):
    """Syscall-Policy setzen (persistiert). preset='genuine' -> geraeteechte Default-Policy;
    preset='clear' -> leeren. Sonst policy = Dict oder JSON-String {default_ret, by_nr}."""
    import json
    if preset == "genuine":
        pol = json.loads(json.dumps(_GENUINE_SYSCALL_POLICY))
    elif preset == "clear":
        pol = {"default_ret": 0, "by_nr": {}}
    elif isinstance(policy, str):
        try:
            pol = json.loads(policy)
        except Exception as e:
            return {"ok": False, "hinweis": f"JSON-Fehler: {e!r}"}
    elif isinstance(policy, dict):
        pol = policy
    else:
        return {"ok": False, "hinweis": "policy (Dict/JSON) oder preset ('genuine'/'clear') angeben"}
    if not isinstance(pol, dict):
        return {"ok": False, "hinweis": "policy muss ein Objekt sein"}
    pol.setdefault("default_ret", 0)
    pol.setdefault("by_nr", {})
    path = _syscall_policy_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pol, f, indent=2)
    return {"ok": True, "path": path, "policy": pol}


_DEVIRT_FUNCS = {"onSearchResult": 0x3ee94, "getOptionsList": 0x43a48}


def native_devirt(ctx, entry="onSearchResult", budget=4000000, lib=None,
                  input_str="0123456789ABCDEF", max_unique=40, runs=1, lift_max=25000):
    """Devirtualizer (v2): eine OLLVM-control-flow-flattened Funktion entflachen. entry =
    'JNI_OnLoad' | 'onSearchResult' | 'getOptionsList' | Hex-Adresse. Fuer Unterfunktionen wird
    je Lauf zuerst JNI_OnLoad initialisiert (frische Engine), dann die Zielfunktion mit
    JNI-Konvention entflacht. ``runs`` > 1 fuehrt die Zielfunktion mehrfach mit variierten
    jstring-Eingaben aus und merged die Dispatch-Logs zu einer **Branch-CFG** (Fan-out +
    Branch-Points + Index-Entscheidungstabelle). Liefert zusaetzlich ausfuehrbares Python
    (L2-Lift) aus Lauf 0. read-only."""
    try:
        from core.native.loader import ElfImage
        from services.native_emu_service import NativeEmuService
        from services.native_ffi_service import NativeFfiService
        from services.native_trace_service import NativeTraceService
        from services.native_devirt_service import NativeDevirtService
        from services import native_jni_env_service as Jm
        from services.native_jni_env_service import NativeJniEnvService
    except Exception as e:
        return {"ok": False, "hinweis": f"Service nicht ladbar: {e!r}"}
    path = lib or _jni_lib_path()
    if not os.path.isfile(path):
        return {"ok": False, "hinweis": f"Lib fehlt: {path}"}
    _pol = _load_syscall_policy()
    _UNKNOWN = object()

    def _one(inp):
        """Ein vollstaendiger, unabhaengiger Lauf (frische Engine). Liefert dict oder _UNKNOWN."""
        img = ElfImage.from_file(path)
        svc = NativeEmuService(publish=False)
        eng = svc.new_engine()
        svc.load_image(eng, img, apply_relocs=True)
        sp0 = int(eng.cpu.sp)
        ffi = NativeFfiService(publish=False)
        ffi.wire_imports(eng, img)
        jni = NativeJniEnvService(publish=False)
        jni.install(eng, ffi, set_entry_reg=None)
        ffi.install_syscall_hook(eng, returns=(_pol.get("by_nr") or {}),
                                 default_ret=int(_pol.get("default_ret", 0)))
        entry_jni = svc.resolve_entry("JNI_OnLoad", img)
        tr = NativeTraceService(publish=False)
        if entry == "JNI_OnLoad":
            tr.dispatch_trace_enable(eng)
            tr.survive_enable(eng, min_pc=0x1000, cap=50000)
            tr.begin(eng, entry_jni, args=[Jm.VM_OBJ], faithful=True)
            r = tr.run(eng, breakpoints=[], maxins=int(budget), mode="block")
            fn = entry_jni
        else:
            tr.survive_enable(eng, min_pc=0x1000, cap=50000)
            tr.begin(eng, entry_jni, args=[Jm.VM_OBJ], faithful=True)
            tr.run(eng, breakpoints=[], maxins=int(budget), mode="block")   # Init
            fn = _DEVIRT_FUNCS.get(entry)
            if fn is None:
                try:
                    fn = int(entry, 0)
                except Exception:
                    return _UNKNOWN
            this_h = jni._new_handle("jobject", cls=str(entry))
            in_h = jni._new_handle("jstring", text=str(inp))
            tr.dispatch_trace_enable(eng)
            tr.survive_enable(eng, min_pc=0x1000, cap=50000)
            tr.begin(eng, fn, args=[Jm.ENV_OBJ, this_h, in_h], sp=sp0, faithful=True)
            r = tr.run(eng, breakpoints=[], maxins=int(budget), mode="block")
        return {"log": tr.dispatch_trace_get(eng),
                "trace": list(getattr(eng, "_trace", []) or []),
                "stop": r.get("pc_hex"), "fn": fn, "eng": eng}

    def _variants(base, n):
        cand = [base, base[::-1], "", "FFFFFFFFFFFFFFFF", "0000000000000000",
                base[:max(1, len(base) // 2)], base + base, "DEADBEEFCAFEBABE"]
        out = []
        seen = set()
        for c in cand:
            if c not in seen:
                seen.add(c); out.append(c)
            if len(out) >= n:
                break
        while len(out) < n:
            out.append(base)
        return out[:n]

    nruns = max(1, int(runs))
    if entry == "JNI_OnLoad":
        nruns = 1                      # Init ist deterministisch -> Mehr-Lauf zeigt keine Zweige
    inputs = _variants(str(input_str), nruns)
    results = []
    for inp in inputs:
        r1 = _one(inp)
        if r1 is _UNKNOWN:
            return {"ok": False, "hinweis": f"Unbekannter entry: {entry}"}
        results.append(r1)
    r0 = results[0]
    eng = r0["eng"]
    fn = r0["fn"]
    logs = [x["log"] for x in results]

    svcD = NativeDevirtService(publish=False)
    # Immer deflatten_multi: erkennt Fan-out auch intra-run (Schleifen, die einen Handler
    # mehrfach mit verschiedenen Nachfolgern besuchen), nicht nur ueber Eingabe-Variation.
    dv = svcD.deflatten_multi(eng, logs, max_unique=int(max_unique))
    if nruns > 1:
        dv["inputs"] = inputs
    seq = dv.get("sequence", [])
    if len(seq) > 150:
        dv["sequence"] = seq[:150] + ["..."]
    shown = set(dv.get("unique_handlers", [])[:int(max_unique)])
    dv["successors"] = {k: v for k, v in dv.get("successors", {}).items() if k in shown}
    if dv.get("branch_points"):
        _bps = [bp for bp in dv["branch_points"] if bp["handler"] in shown][:40]
        for bp in _bps:
            bp["index_table"] = bp.get("index_table", [])[:20]
        dv["branch_points"] = _bps
    # Payload-Hygiene: bei vielen Bloecken nur die Branch-relevanten behalten
    # (Branch-Point-Handler + deren Nachfolger); voller Disasm via native.disasm/decompile.
    _keep = set()
    for _bp in (dv.get("branch_points") or []):
        _keep.add(_bp["handler"])
        for _sx in _bp.get("successors", []):
            if _sx.get("target") != "TRAP":
                _keep.add(_sx["target"])
    _blocks = dv.get("blocks") or {}
    if len(_blocks) > 20 and _keep:
        dv["blocks"] = {k: v for k, v in _blocks.items() if k in _keep}
        dv["blocks_hinweis"] = ("auf Branch-relevante Handler eingedampft (%d von %d); "
                                "voller Disasm via native.disasm/decompile"
                                % (len(dv["blocks"]), len(_blocks)))
    try:
        from services.native_trace_decompile_service import NativeTraceDecompileService
        _td = NativeTraceDecompileService()
        _tr0 = [e for e in r0.get("trace", [])
                if int(e.get("pc", 0)) < 0x7A000000]
        _lmax = int(lift_max)
        _do_lift = (_lmax > 0) and (len(_tr0) <= _lmax)
        _lf = (_td.lift_to_function(eng, trace=_tr0, func_name="lifted") if _do_lift
               else {"ok": False, "why": "lift_uebersprungen", "residual": len(_tr0)})
        if not _do_lift:
            dv["lift_mode"] = "skipped_large" if _lmax > 0 else "skipped_off"
            dv["lift_trace_len"] = len(_tr0)
            dv["lift_max"] = _lmax
        elif _lf.get("ok"):
            _src = _lf.get("source") or ""
            dv["python"] = _src if len(_src) <= 60000 else (_src[:_src.rfind(chr(10), 0, 60000)] + "\n# ... (gekuerzt)")
            dv["lift_mode"] = "runnable"
            dv["lift_unique"] = _lf.get("unique")
            dv["lift_visits"] = _lf.get("visits")
        else:
            dv["lift_mode"] = "pseudocode"
            dv["lift_why"] = _lf.get("why")
            dv["lift_residual"] = _lf.get("residual") or _lf.get("addr")
            _dc = _td.decompile_trace(eng, _tr0)
            if _dc.get("ok"):
                _pc = _dc.get("pseudocode") or ""
                dv["pseudocode"] = _pc if len(_pc) <= 12000 else (_pc[:12000] + "\n... (gekuerzt)")
                dv["lift_unique"] = _dc.get("unique")
                dv["lift_visits"] = _dc.get("visits")
    except Exception as _e:
        dv["lift_error"] = repr(_e)
    dv["entry"] = str(entry)
    dv["fn"] = hex(int(fn))
    dv["stop"] = r0.get("stop")
    dv["n_runs"] = nruns
    return dv


# ====================== Workflow-Schicht (GUI-Refactoring AP2) ======================
# Deklarative Registry: die GUI baut Workflow-Rail + Eingabeformulare generisch hieraus.
_WORKFLOWS = {
    "open_lib": {"label": "Oeffnen", "icon": "📂",
                 "desc": "Lib laden + indizieren + als AKTIVE Lib setzen.",
                 "info": ("WAS: Laedt eine native .so (AArch64) ins Studio, baut den XREF/CallGraph-Index "
                          "und merkt sie als AKTIVE Lib fuer alle folgenden Workflows.\n"
                          "WIE: ELF parsen (Segmente/Symbole/Entry) + Index (SHA-gegatet gecacht); der Pfad "
                          "wird nach secrets/probe/out/active_lib.txt geschrieben.\n"
                          "ANWENDUNG: Feld 'Lib-Pfad' = Pfad zur .so (leer = zuletzt aktive, sonst Default "
                          "libdb0c). ZUERST ausfuehren, wenn du ein neues Ziel analysierst.\n"
                          "ERGEBNIS: ELF-Kurzinfo + Index-Status; danach arbeiten run_fn/devirt/gate2/jni "
                          "automatisch auf dieser Lib."),
                 "fields": [{"key": "path", "label": "Lib-Pfad", "default": "", "type": "path"}],
                 "hints": ["jni", "run_fn", "devirt"]},
    "run_fn":   {"label": "Ausfuehren", "icon": "▶",
                 "desc": "Eine Funktion emulieren.",
                 "info": ("WAS: Emuliert eine einzelne Funktion der aktiven Lib im arm64-Emulator (kein "
                          "Geraet, keine Risiken).\n"
                          "WIE: Argumente in x0.. gesetzt, ab Entry bis ret/Budget ausgefuehrt; End-Register, "
                          "Rueckgabewert und beschriebene Out-Puffer werden erfasst.\n"
                          "ANWENDUNG: 'Entry' = Symbolname oder 0xAddr; 'Args (CSV hex)' z.B. '0x1,0xdead'; "
                          "'Budget' = max. Instruktionen; 'Lib' leer = aktive Lib.\n"
                          "ERGEBNIS: Endregister/ret + Out-Puffer. Um eine isolierte Routine zu verstehen."),
                 "fields": [{"key": "entry", "label": "Entry", "default": "JNI_OnLoad", "type": "str"},
                            {"key": "args", "label": "Args (CSV hex)", "default": "", "type": "str"},
                            {"key": "budget", "label": "Budget", "default": 2000000, "type": "int"},
                            {"key": "lib", "label": "Lib (leer=aktiv)", "default": "", "type": "path"}],
                 "hints": ["devirt", "gate2"]},
    "devirt":   {"label": "Devirt", "icon": "🧩",
                 "desc": "OLLVM entflachen -> Branch-CFG + Python.",
                 "info": ("WAS: Macht OLLVM-control-flow-flattened Funktionen wieder lesbar (Deflattening).\n"
                          "WIE: Emuliert die Dispatcher-Schleife ueber mehrere Laeufe mit variierten Eingaben, "
                          "rekonstruiert eine Branch-CFG (Fan-out, Branch-Points, Index-Entscheidungstabelle) "
                          "und hebt Lauf 0 nach ausfuehrbarem Python (L2-Lift).\n"
                          "ANWENDUNG: 'Entry' = JNI_OnLoad/onSearchResult/getOptionsList/0xAddr; 'Runs'>1 "
                          "variiert Eingaben (mehr Branches); 'Lift-max'=0 = nur CFG; 'Budget' = Limit; "
                          "'Lib' leer = aktiv.\n"
                          "ERGEBNIS: Zusammenfassung + Branch-CFG + Python/Pseudocode + Sequenz. "
                          "VORAUSSETZUNG: Ziel ist OLLVM-flattened."),
                 "fields": [{"key": "entry", "label": "Entry", "default": "JNI_OnLoad", "type": "str"},
                            {"key": "runs", "label": "Runs", "default": 4, "type": "int"},
                            {"key": "lift_max", "label": "Lift-max", "default": 25000, "type": "int"},
                            {"key": "budget", "label": "Budget", "default": 4000000, "type": "int"},
                            {"key": "lib", "label": "Lib (leer=aktiv)", "default": "", "type": "path"}],
                 "hints": ["run_fn", "gate2"]},
    "gate2":    {"label": "Gate 2", "icon": "🛡",
                 "desc": "Survival + Detection-Syscalls + Policy.",
                 "info": ("WAS: Faehrt JNI_OnLoad ueber die RASP/Anti-Tamper-Trap hinaus (Survival Mode) und "
                          "zeichnet die rohen Detection-Syscalls auf.\n"
                          "WIE: Tote Dispatches werden ueberlebt; jeder svc (nr=x8, args x0-x5) mit aufgeloestem "
                          "Pfad-Argument protokolliert = die Erkennungs-/Reaktions-Aufrufe "
                          "(newfstatat/openat/...).\n"
                          "ANWENDUNG: 'Policy-Preset' = genuine (geraeteechte Rueckgaben)/clear/eigenes; "
                          "'Entry' meist JNI_OnLoad; 'Budget' hoch (Survival laeuft lang); 'Lib' leer = aktiv.\n"
                          "ERGEBNIS: Liste der Detection-Syscalls (Name/pc/Argument) + Anzahl uebersprungener "
                          "Dispatches. VORAUSSETZUNG: Ziel hat syscall-basierte RASP."),
                 "fields": [{"key": "entry", "label": "Entry", "default": "JNI_OnLoad", "type": "str"},
                            {"key": "preset", "label": "Policy-Preset", "default": "genuine", "type": "choice", "options": ["genuine", "clear"]},
                            {"key": "budget", "label": "Budget", "default": 8000000, "type": "int"},
                            {"key": "lib", "label": "Lib (leer=aktiv)", "default": "", "type": "path"}],
                 "hints": ["devirt"]},
    "jni":      {"label": "JNI-Init", "icon": "⚡",
                 "desc": "JNI_OnLoad initialisieren (RegisterNatives).",
                 "info": ("WAS: Emuliert JNI_OnLoad mit Fake-JNIEnv, loest Relocations/Imports auf und faengt "
                          "RegisterNatives ab = die Zuordnung Java-Methode -> native Funktion.\n"
                          "WIE: Fake-JNIEnv-Tabelle + Reloc-Wiring, Lauf bis zur RASP-Trap (stop_at_rasp); "
                          "jeder JNI-Call wird geloggt.\n"
                          "ANWENDUNG: 'Budget' = Instruktionslimit (JNI-Init ist kurz); 'Lib' leer = aktiv.\n"
                          "ERGEBNIS: RegisterNatives-Map (Klasse/Methode/Signatur -> Adresse) + JNI-Call-Log. "
                          "Der Einstieg fuer jede Android-Native-Lib."),
                 "fields": [{"key": "budget", "label": "Budget", "default": 400000, "type": "int"},
                            {"key": "lib", "label": "Lib (leer=aktiv)", "default": "", "type": "path"}],
                 "hints": ["devirt", "run_fn"]},
}
_WORKFLOW_ORDER = ["open_lib", "run_fn", "devirt", "gate2", "jni"]


def native_devirt_hybrid(ctx, entry="onSearchResult", budget=4000000, lib=None,
                         input_str="0123456789ABCDEF", max_blocks=400, max_unique=48):
    """Hybrid-Devirt (N5/M7): statischer Dispatch-Graph (`devirt_static`) ⊕ getracter Branch-Zustand
    (EIN Lauf mit `dispatch_trace`+`survive`). Loest register-abhaengige Branch-Points und statische
    Traps/Luecken durch den real genommenen Pfad auf; liefert Coverage + Provenienz je Block.
    entry = JNI_OnLoad | onSearchResult | getOptionsList | Hex-Adresse. read-only."""
    try:
        from core.native.loader import ElfImage
        from services.native_emu_service import NativeEmuService
        from services.native_ffi_service import NativeFfiService
        from services.native_trace_service import NativeTraceService
        from services.native_devirt_service import NativeDevirtService
        from services import native_jni_env_service as Jm
        from services.native_jni_env_service import NativeJniEnvService
    except Exception as e:
        return {"ok": False, "hinweis": f"Service nicht ladbar: {e!r}"}
    path = lib or _jni_lib_path()
    if not os.path.isfile(path):
        return {"ok": False, "hinweis": f"Lib fehlt: {path}"}
    _pol = _load_syscall_policy()
    img = ElfImage.from_file(path)
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    svc.load_image(eng, img, apply_relocs=True)
    sp0 = int(eng.cpu.sp)
    ffi = NativeFfiService(publish=False)
    ffi.wire_imports(eng, img)
    jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)
    ffi.install_syscall_hook(eng, returns=(_pol.get("by_nr") or {}),
                             default_ret=int(_pol.get("default_ret", 0)))
    tr = NativeTraceService(publish=False)
    entry_jni = svc.resolve_entry("JNI_OnLoad", img)
    if entry == "JNI_OnLoad":
        fn = entry_jni
        tr.dispatch_trace_enable(eng)
        tr.survive_enable(eng, min_pc=0x1000, cap=50000)
        tr.begin(eng, entry_jni, args=[Jm.VM_OBJ], faithful=True)
        tr.run(eng, breakpoints=[], maxins=int(budget), mode="block")
    else:
        tr.survive_enable(eng, min_pc=0x1000, cap=50000)
        tr.begin(eng, entry_jni, args=[Jm.VM_OBJ], faithful=True)
        tr.run(eng, breakpoints=[], maxins=int(budget), mode="block")   # Init
        fn = _DEVIRT_FUNCS.get(entry)
        if fn is None:
            try:
                fn = int(entry, 0)
            except Exception:
                return {"ok": False, "hinweis": f"Unbekannter entry: {entry}"}
        this_h = jni._new_handle("jobject", cls=str(entry))
        in_h = jni._new_handle("jstring", text=str(input_str))
        tr.dispatch_trace_enable(eng)
        tr.survive_enable(eng, min_pc=0x1000, cap=50000)
        tr.begin(eng, fn, args=[Jm.ENV_OBJ, this_h, in_h], sp=sp0, faithful=True)
        tr.run(eng, breakpoints=[], maxins=int(budget), mode="block")
    log = tr.dispatch_trace_get(eng)
    svcD = NativeDevirtService(publish=False)
    # devirt_static erwartet die Zieladresse (int), nicht den Symbolnamen.
    out = svcD.devirt_hybrid(eng, int(fn), dispatch_logs=[log], max_blocks=int(max_blocks),
                             max_unique=int(max_unique))
    out["entry_name"] = entry
    # Payload-Hygiene: nur die interessanten Bloecke (Branch/Trap/aufgeloest/getracet) zeigen.
    blocks = out.get("blocks", {})
    keep = {a: b for a, b in blocks.items()
            if b.get("is_branch_point") or b.get("resolved")
            or b.get("node_type") == "traced-only" or b.get("traced_successors")}
    out["blocks"] = dict(list(keep.items())[:80])
    out["n_blocks_shown"] = len(out["blocks"])
    return out


def native_devirt_static(ctx, entry="onSearchResult", max_blocks=400, lib=None):
    """Statischer Devirtualizer: baut den Dispatch-Graphen einer OLLVM-geflachten Funktion
    **ohne Lauf der Zielfunktion** — rein aus Disasm + den (einmalig per Init gesetzten)
    .data-State-Keys. Loest 1D-madd-Tabellen (0x134f48) UND 2D-smaddl-Vtable (0x137740) auf;
    folgt index/direct-Kanten und beiden csel-Branch-Kandidaten, erkennt Traps (leerer Slot).
    entry = 'JNI_OnLoad' | 'onSearchResult' | 'getOptionsList' | Hex-Adresse. read-only."""
    try:
        from core.native.loader import ElfImage
        from services.native_emu_service import NativeEmuService
        from services.native_ffi_service import NativeFfiService
        from services.native_trace_service import NativeTraceService
        from services.native_devirt_service import NativeDevirtService
        from services import native_jni_env_service as Jm
        from services.native_jni_env_service import NativeJniEnvService
    except Exception as e:
        return {"ok": False, "hinweis": f"Service nicht ladbar: {e!r}"}
    path = lib or _jni_lib_path()
    if not os.path.isfile(path):
        return {"ok": False, "hinweis": f"Lib fehlt: {path}"}
    img = ElfImage.from_file(path)
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    svc.load_image(eng, img, apply_relocs=True)
    ffi = NativeFfiService(publish=False)
    ffi.wire_imports(eng, img)
    jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)
    _pol = _load_syscall_policy()
    ffi.install_syscall_hook(eng, returns=(_pol.get("by_nr") or {}),
                             default_ret=int(_pol.get("default_ret", 0)))
    entry_jni = svc.resolve_entry("JNI_OnLoad", img)
    tr = NativeTraceService(publish=False)
    tr.survive_enable(eng, min_pc=0x1000, cap=50000)
    tr.begin(eng, entry_jni, args=[Jm.VM_OBJ], faithful=True)
    tr.run(eng, breakpoints=[], maxins=800000, mode="block")   # Init: .data-State-Keys setzen
    if entry == "JNI_OnLoad":
        fn = entry_jni
    else:
        fn = _DEVIRT_FUNCS.get(entry)
        if fn is None:
            try:
                fn = int(entry, 0)
            except Exception:
                return {"ok": False, "hinweis": f"Entry unbekannt: {entry} "
                        f"(bekannt: {sorted(_DEVIRT_FUNCS)} oder Hex-Adresse)"}
    dv = NativeDevirtService(publish=False)
    g = dv.devirt_static(eng, fn, max_blocks=int(max_blocks))
    g["entry_name"] = entry
    g["entry_addr"] = f"0x{int(fn) & ((1 << 64) - 1):x}"
    return g


_ACTIVE_LIB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "secrets", "probe", "out", "active_lib.txt")


def _active_lib():
    """Zuletzt per open_lib gesetzte aktive Lib (Pfad) oder None."""
    try:
        with open(_ACTIVE_LIB_PATH, encoding="utf-8") as f:
            p = f.read().strip()
        return p or None
    except Exception:
        return None


def _set_active_lib(path):
    try:
        os.makedirs(os.path.dirname(_ACTIVE_LIB_PATH), exist_ok=True)
        with open(_ACTIVE_LIB_PATH, "w", encoding="utf-8") as f:
            f.write(str(path or "").strip())
    except Exception:
        pass


def _wf_lib(params):
    """Lib fuer einen Workflow aufloesen: explizites Feld 'lib'/'path' -> aktive Lib -> None (Tool-Default)."""
    v = (params or {}).get("lib") or (params or {}).get("path")
    if v and str(v).strip():
        return str(v).strip()
    return _active_lib()


def _studio_folder(folder):
    return folder or _STUDIO_SESSION_FOLDER


def native_session_get(ctx, folder=None):
    """Geteilten Native-Studio-Zustand ansehen (read-only, O): Lib + Eingaben + letztes Ergebnis."""
    try:
        from services.native_studio_session_service import NativeStudioSessionService
    except Exception as e:
        return {"ok": False, "hinweis": f"Service nicht ladbar: {e!r}"}
    pk = NativeStudioSessionService().peek(_studio_folder(folder))
    sess = pk.get("session")
    if not sess:
        return {"ok": False, "hinweis": f"keine Studio-Sitzung in {_studio_folder(folder)} \u2014 in der GUI erst \u2b06 Zustand\u2192KI."}
    res = pk.get("result")
    rsum = None
    if isinstance(res, dict):
        secs = res.get("sections") or []
        rsum = {"source": res.get("source") or res.get("workflow") or res.get("name"),
                "ok": res.get("ok"),
                "titles": [s.get("title") for s in secs] if secs else None,
                "next_hints": res.get("next_hints")}
    return {"ok": True, "folder": _studio_folder(folder),
            "lib": sess.get("lib"), "inputs": sess.get("inputs"),
            "seq": sess.get("seq"), "writer": sess.get("writer"), "ts": sess.get("ts"),
            "result": res, "result_summary": rsum}


def native_session_set(ctx, folder=None, lib=None, inputs=None, run_workflow=None,
                       base_seq=None, force=False):
    """Studio-Zustand setzen (writer=ki); optional Workflow ausfuehren. G (Freigabe session_write)."""
    try:
        from services.native_studio_session_service import NativeStudioSessionService
    except Exception as e:
        return {"ok": False, "hinweis": f"Service nicht ladbar: {e!r}"}
    result = None
    if run_workflow:
        try:
            result = native_workflow(ctx, run_workflow, inputs or {})
        except Exception as e:
            result = {"ok": False, "error": repr(e)}
    r = NativeStudioSessionService().save(_studio_folder(folder), lib=lib, inputs=inputs,
                                          result=result, writer="ki",
                                          base_seq=base_seq, force=force)
    if run_workflow and isinstance(r, dict):
        r["ran_workflow"] = run_workflow
        r["workflow_ok"] = result.get("ok") if isinstance(result, dict) else None
    return r


def native_recipe_list(ctx=None):
    """Gespeicherte Workflow-Rezepte (Name + Workflow + Params) lesen. O."""
    import json
    try:
        with open(_RECIPES_PATH, encoding="utf-8") as f:
            recs = json.load(f)
    except Exception:
        recs = []
    return {"ok": True, "path": _RECIPES_PATH, "count": len(recs), "recipes": recs}


def native_recipe_run(ctx, name=None):
    """Gespeichertes Rezept ausfuehren = seinen Workflow mit den gespeicherten Params fahren. O."""
    if not name:
        return {"ok": False, "hinweis": "name= fehlt (Rezeptname)."}
    import json
    try:
        with open(_RECIPES_PATH, encoding="utf-8") as f:
            recs = json.load(f)
    except Exception:
        recs = []
    rec = next((r for r in recs if r.get("name") == name), None)
    if not rec:
        return {"ok": False, "hinweis": f"Rezept {name!r} nicht gefunden.",
                "verfuegbar": [r.get("name") for r in recs]}
    return native_workflow(ctx, rec.get("workflow"), rec.get("params") or {})


def native_plugins(ctx=None):
    """Entdeckte native_plugins/ auflisten: Namen + deklarative GUI-Beitraege (read-only, O)."""
    try:
        from core.native import unpack as _u
        reg = _u.UnpackRegistry()
        return {"ok": True, "names": reg.names(), "contributions": reg.contributions()}
    except Exception as e:
        return {"ok": False, "hinweis": f"Plugin-Registry nicht ladbar: {e!r}"}


def _anno_path_sha(ctx, path):
    from core.native import unpack as _u
    rp, _img = _native_load_image(ctx, path)
    ws = _u.plugin_root(_native_base(ctx), _native_plugin_name(ctx))
    return os.path.join(ws, "annotations.json"), _u.sha8(rp), rp


def native_annotations_get(ctx, path):
    """Annotationen (eigene Namen/Notizen je Adresse) einer Lib lesen (read-only, O)."""
    try:
        from services.native_annotations_service import NativeAnnotationsService
        apath, sha, rp = _anno_path_sha(ctx, path)
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}
    anno = NativeAnnotationsService(apath).all(sha) or {}
    return {"ok": True, "path": rp, "sha": sha, "count": len(anno), "annotations": anno}


def native_annotate(ctx, path, addr, name, note=""):
    """Eigenen Namen/Notiz fuer eine Adresse setzen (geteilt mit der GUI-Outline). P."""
    try:
        from services.native_annotations_service import NativeAnnotationsService
        apath, sha, rp = _anno_path_sha(ctx, path)
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}
    try:
        a = addr if isinstance(addr, int) else int(str(addr), 0)
        akey = f"0x{a:x}"
    except Exception:
        return {"ok": False, "hinweis": f"addr nicht interpretierbar: {addr!r}"}
    if not name:
        return {"ok": False, "hinweis": "name= fehlt."}
    NativeAnnotationsService(apath).set(sha, akey, str(name), str(note or ""))
    return {"ok": True, "path": rp, "sha": sha, "addr": akey, "name": name,
            "note": note or "", "hinweis": "Gesetzt. In der GUI nach Outline-Refresh sichtbar (✎)."}


def native_workflow_meta(ctx=None):
    """Deklarative Workflow-Registry fuer die GUI (Rail + Formulare)."""
    return {"ok": True, "order": _WORKFLOW_ORDER, "workflows": _WORKFLOWS}


def native_workflow(ctx, name=None, params=None):
    """Fuehrt einen benannten Workflow aus (verkettet bestehende native.*-Tools) und liefert
    ein einheitliches Ergebnis (+ workflow, next_hints). params: dict oder JSON-String."""
    import json as _json
    if isinstance(params, str):
        try:
            params = _json.loads(params) if params.strip() else {}
        except Exception:
            params = {}
    params = params or {}
    name = name or "devirt"
    meta = _WORKFLOWS.get(name)
    if not meta:
        return {"ok": False, "hinweis": "Unbekannter Workflow: %s" % name}
    lib = _wf_lib(params)   # explizite Lib / aktive Lib / None (-> Tool-Default libdb0c)
    eff = lib or _jni_lib_path()
    if not (eff and os.path.isfile(eff)):
        return {"ok": False, "workflow": name,
                "hinweis": ("Keine existierende Lib. Erst 'Oeffnen' (open_lib) mit einer .so "
                            "ausfuehren oder im Feld 'Lib' einen gueltigen Pfad setzen. "
                            "Aufgeloest: %r" % eff)}
    try:
        if name == "devirt":
            r = native_devirt(ctx, entry=params.get("entry", "JNI_OnLoad"),
                              budget=int(params.get("budget", 4000000)),
                              runs=int(params.get("runs", 4)),
                              lift_max=int(params.get("lift_max", 25000)), lib=lib)
        elif name == "gate2":
            if params.get("preset"):
                native_syscall_policy_set(ctx, preset=params.get("preset"))
            r = native_survive_run(ctx, budget=int(params.get("budget", 8000000)), lib=lib)
            r = dict(r) if isinstance(r, dict) else {"ok": True, "result": r}
            r.setdefault("entry", params.get("entry", "JNI_OnLoad"))
        elif name == "run_fn":
            path = lib or _jni_lib_path()
            args = [int(a, 0) for a in str(params.get("args", "")).split(",") if a.strip()]
            r = native_run(ctx, path=path, entry=params.get("entry", "JNI_OnLoad"),
                           args=args or None, maxins=int(params.get("budget", 2000000)))
        elif name == "open_lib":
            path = lib or _jni_lib_path()
            r = {"ok": True, "path": path,
                 "loaded": native_load(ctx, path=path),
                 "indexed": native_index(ctx, path=path)}
            _set_active_lib(path)   # kuenftige Workflows arbeiten auf dieser Lib
        elif name == "jni":
            r = native_jni_run(ctx, budget=int(params.get("budget", 400000)), lib=lib)
        else:
            return {"ok": False, "hinweis": "Dispatch fehlt: %s" % name}
    except Exception as e:
        return {"ok": False, "hinweis": "Workflow-Fehler: %r" % e, "workflow": name}
    if isinstance(r, dict):
        r = dict(r)
        r["workflow"] = name
        r["next_hints"] = meta["hints"]
        r["lib"] = lib or _jni_lib_path()
        r.setdefault("ok", True)
    return r


def native_jni_log(ctx, limit=100):
    """Letzten native.jni_run auslesen: Call-Log + RegisterNatives-Map + Klassifizierungen."""
    jni = globals().get("_LAST_JNI")
    if jni is None:
        return {"ok": False, "hinweis": "erst native.jni_run aufrufen"}
    return {"ok": True, "summary": jni.summary(), "natives": jni.get_natives(),
            "calls": jni.get_calls()[:int(limit)]}


def native_jni_smali(ctx, cls, name, sig):
    """Smali-Body + Klassifizierung + Vorschlagsrueckgabe zu Klasse/Methode/Signatur (fuers Popup)."""
    try:
        from services.native_jni_env_service import NativeJniEnvService
    except Exception as e:
        return {"ok": False, "hinweis": f"Service nicht ladbar: {e!r}"}
    return NativeJniEnvService(publish=False).smali_of(cls, name, sig)


def _ckpt_dir():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(root, "secrets", "probe", "out", "dbg_checkpoints")
    os.makedirs(d, exist_ok=True)
    return d


def _dbg_live_dir():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "secrets", "probe", "out", "dbg")


def native_dbg_ckpt_list(ctx):
    """Benannte Debug-Checkpoints auflisten (Ordner unter secrets/probe/out/dbg_checkpoints/)."""
    import json
    base = _ckpt_dir()
    out = []
    for name in sorted(os.listdir(base)):
        d = os.path.join(base, name)
        if not os.path.isdir(d):
            continue
        meta = {}
        try:
            with open(os.path.join(d, "meta.json"), encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            pass
        note = ""
        try:
            md = [x for x in os.listdir(d) if x.endswith(".md")]
            if md:
                note = open(os.path.join(d, md[0]), encoding="utf-8").read()[:400]
        except Exception:
            pass
        out.append({"name": name,
                    "pc": (hex(int(meta.get("pc"))) if meta.get("pc") is not None else None),
                    "writer": meta.get("writer"), "meta_note": meta.get("note"),
                    "ts": meta.get("ts"), "md": note})
    return {"ok": True, "count": len(out), "checkpoints": out}


def native_dbg_ckpt_activate(ctx, name):
    """Kopiert den benannten Checkpoint in die Live-Debug-Sitzung. Danach in der GUI ⬇ Von KI klicken.
    Ueberschreibt die aktuelle Live-Sitzung."""
    import shutil
    src = os.path.join(_ckpt_dir(), str(name))
    if not os.path.isdir(src):
        return {"ok": False, "hinweis": f"Checkpoint '{name}' nicht gefunden"}
    dst = _dbg_live_dir()
    os.makedirs(dst, exist_ok=True)
    copied = []
    for fn in ("state.pkl.xz", "meta.json", "trace.json"):
        s = os.path.join(src, fn)
        if os.path.isfile(s):
            shutil.copy2(s, os.path.join(dst, fn))
            copied.append(fn)
    if "state.pkl.xz" not in copied:
        return {"ok": False, "hinweis": f"Checkpoint '{name}' unvollstaendig (kein state.pkl.xz)"}
    return {"ok": True, "activated": name, "copied": copied,
            "hinweis": "In der GUI jetzt ⬇ Von KI klicken, um den Checkpoint zu laden."}


def native_dalvik_run(ctx, smali=None, cls=None, name=None, sig=None, args=None, maxins=200000):
    """J1: Smali (.class oder einzelne .method) parsen, im DalvikVM registrieren und eine Methode
    ausfuehren. Generisch/zielagnostisch, read-only. Liefert Ergebnis, registrierte Methoden,
    Opcode-Coverage und nicht aufgeloeste Aufrufe (Coverage-Loop).
      smali      = Smali-Text (.class-Datei oder einzelner .method-Block)
      cls/name/sig = aufzurufende Methode (Default: erste/einzige im Text)
      args       = Argumentliste (Liste oder JSON-String), Primitive als int."""
    try:
        from core.java.dalvik import DalvikVM, DalvikUnknownOpcode, DalvikException
        from core.java import smali as Sm
        from core.java.objects import JString, JObject, JArray
    except Exception as e:
        return {"ok": False, "hinweis": f"Java-Kern nicht ladbar: {e!r}"}
    import json
    if not smali or not str(smali).strip():
        return {"ok": False, "hinweis": "kein Smali-Text uebergeben"}
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else []
        except Exception:
            return {"ok": False, "hinweis": "args ist kein gueltiges JSON"}
    args = list(args or [])

    def _jval(v):
        if isinstance(v, JString):
            return {"type": "String", "value": v.text}
        if isinstance(v, JObject):
            return {"type": "Object", "cls": v.cls, "fields": {k: _jval(x) for k, x in v.fields.items()}}
        if isinstance(v, JArray):
            return {"type": "Array", "eltype": v.eltype, "data": [_jval(x) for x in v.data]}
        return v

    vm = DalvikVM()
    try:
        if ".class" in smali:
            clsname, mds = Sm.parse_class(smali)
        else:
            mds = [Sm.parse_method(smali)]
        for md in mds:
            vm.register(md)
    except Sm.SmaliUnsupported as e:
        return {"ok": False, "hinweis": f"Smali-Opcode nicht unterstuetzt: {e.op} (Zeile {e.lineno})",
                "op": e.op, "line": e.line}
    except Exception as e:
        return {"ok": False, "hinweis": f"Parse-Fehler: {e!r}"}

    methods = [{"cls": m.cls, "name": m.name, "sig": m.sig, "nregs": m.nregs, "nargs": m.nargs}
               for m in mds]
    target = None
    if name:
        for m in mds:
            if m.name == name and (sig is None or m.sig == sig) and (cls is None or m.cls == cls):
                target = m
                break
        if target is None:
            return {"ok": False, "hinweis": f"Methode {name}{sig or ''} nicht gefunden",
                    "methods": methods}
    else:
        target = mds[0] if mds else None
    if target is None:
        return {"ok": False, "hinweis": "keine Methode im Smali", "methods": methods}

    try:
        ret = vm.invoke(target.cls, target.name, target.sig, args)
    except DalvikUnknownOpcode as e:
        return {"ok": False, "hinweis": f"Dalvik-Opcode nicht implementiert: {e.op} @pc={e.pc}",
                "op": e.op, "methods": methods, "coverage": vm.coverage}
    except DalvikException as e:
        return {"ok": False, "hinweis": f"Java-Ausnahme: {e.msg}", "methods": methods}
    except Exception as e:
        return {"ok": False, "hinweis": f"Laufzeitfehler: {e!r}", "methods": methods}

    return {"ok": True,
            "called": {"cls": target.cls, "name": target.name, "sig": target.sig, "args": args},
            "result": _jval(ret), "methods": methods,
            "coverage": vm.coverage, "missing": vm.missing}


def native_dex_run(ctx, dex_path=None, cls=None, name=None, sig=None, args=None, maxins=1000000):
    """J3/MS-4: ein DEX laden, die Zielklasse (lazy, inkl. <clinit>) in die DalvikVM registrieren und
    eine Methode ausfuehren. Framework-Aufrufe (android.*/java.*) laufen ueber die Stub-Registry
    (Coverage-Loop). Generisch, read-only. Liefert Ergebnis + Coverage + nicht aufgeloeste Aufrufe.
      dex_path      = Pfad zu classes.dex
      cls/name/sig  = aufzurufende Methode (Lcls; / name / (args)ret)
      args          = Argumentliste (Liste oder JSON)."""
    try:
        from core.java.dex import DexFile
        from core.java.runtime import JavaRuntime
        from services.native_emu_service import NativeEmuService
        from services.native_ffi_service import NativeFfiService
        from services.native_jni_env_service import NativeJniEnvService
        from core.java.objects import JString, JObject, JArray
    except Exception as e:
        return {"ok": False, "hinweis": f"Java-Kern nicht ladbar: {e!r}"}
    import json
    import os
    if not dex_path or not os.path.isfile(dex_path):
        return {"ok": False, "hinweis": f"DEX fehlt: {dex_path}"}
    if not (cls and name and sig):
        return {"ok": False, "hinweis": "cls/name/sig noetig (Lcls; / name / (args)ret)"}
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else []
        except Exception:
            return {"ok": False, "hinweis": "args ist kein gueltiges JSON"}
    args = list(args or [])

    def _jv(v):
        if isinstance(v, JString):
            return {"type": "String", "value": v.text}
        if isinstance(v, JObject):
            return {"type": "Object", "cls": v.cls}
        if isinstance(v, JArray):
            return {"type": "Array", "eltype": v.eltype, "len": len(v.data)}
        return v

    dx = DexFile(open(dex_path, "rb").read())
    svc = NativeEmuService(publish=False); eng = svc.new_engine()
    ffi = NativeFfiService(publish=False); jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)
    rt = JavaRuntime(eng, jni)
    rt.attach_dex(dx)
    if not rt.loader.has_class(cls):
        return {"ok": False, "hinweis": f"Klasse {cls} nicht im DEX"}
    try:
        rt.load_dex_class(cls)
    except Exception as e:
        return {"ok": False, "hinweis": f"Klasse laden fehlgeschlagen: {e!r}"}
    if (cls, name, sig) not in rt.vm.registry:
        methods = [{"name": k[1], "sig": k[2]} for k in rt.vm.registry if k[0] == cls]
        return {"ok": False, "hinweis": f"Methode {name}{sig} nicht gefunden/decodierbar",
                "methods": methods[:50]}
    try:
        ret = rt.vm.invoke(cls, name, sig, args)
    except Exception as e:
        return {"ok": False, "hinweis": f"Laufzeitfehler: {e!r}", "coverage": rt.vm.coverage,
                "dalvik_missing": rt.vm.missing[:20], "framework_missing": rt.stubs.missing[:20]}
    return {"ok": True,
            "dex": {"version": dx.version, "classes": dx.class_defs_size, "methods": dx.method_ids_size},
            "called": {"cls": cls, "name": name, "sig": sig, "args": args}, "result": _jv(ret),
            "coverage": rt.vm.coverage, "dalvik_missing": rt.vm.missing[:20],
            "framework_missing": rt.stubs.missing[:20]}


def native_jdbg_run(ctx, smali=None, cls=None, name=None, sig=None, args=None,
                    breakpoints=None, set_regs=None, max_steps=200000,
                    studio_root=None, zone="original"):
    """X1/MS-6: eine Smali-Methode unter dem Java-Debugger (DalvikVM) ausfuehren — skriptbasiert, da
    MCP-Calls zustandslos sind. `breakpoints` = Liste von pc-Indizes der Zielmethode; an jedem Treffer
    werden die Register beobachtet. `set_regs` = {index: wert} wird am ERSTEN Treffer angewandt (Mutation).
    Liefert Breakpoint-Beobachtungen, Timeline-Laenge und Ergebnis (Mutationseffekt sichtbar)."""
    try:
        from core.java.dalvik import DalvikVM, DalvikUnknownOpcode, DalvikException
        from core.java import smali as Sm
        from core.java.debug import JavaDebugger
    except Exception as e:
        return {"ok": False, "hinweis": f"Java-Kern nicht ladbar: {e!r}"}
    import json
    if not smali or not str(smali).strip():
        return {"ok": False, "hinweis": "kein Smali-Text uebergeben"}
    for nm, val in (("args", args), ("breakpoints", breakpoints), ("set_regs", set_regs)):
        if isinstance(val, str) and val.strip():
            try:
                v = json.loads(val)
            except Exception:
                return {"ok": False, "hinweis": f"{nm} ist kein gueltiges JSON"}
            if nm == "args":
                args = v
            elif nm == "breakpoints":
                breakpoints = v
            else:
                set_regs = v
    args = list(args or [])
    bps = [int(b) for b in (breakpoints or [])]
    set_regs = {int(k): v for k, v in (set_regs or {}).items()}

    vm = DalvikVM()
    try:
        mds = Sm.parse_class(smali)[1] if ".class" in smali else [Sm.parse_method(smali)]
        for md in mds:
            vm.register(md)
    except Sm.SmaliUnsupported as e:
        return {"ok": False, "hinweis": f"Smali-Opcode nicht unterstuetzt: {e.op} (Zeile {e.lineno})"}
    except Exception as e:
        return {"ok": False, "hinweis": f"Parse-Fehler: {e!r}"}
    if studio_root:
        # Lazy-Callee-Aufloesung: App-interne Aufrufe aus dem Smali-Baum EINER Zone nachladen
        try:
            from core.java.class_provider import StudioClassProvider
            vm.class_provider = StudioClassProvider(studio_root, zone=zone)
        except Exception as e:
            return {"ok": False, "hinweis": f"Studio-Provider nicht ladbar: {e!r}"}
    target = None
    if name:
        for m in mds:
            if m.name == name and (sig is None or m.sig == sig) and (cls is None or m.cls == cls):
                target = m
                break
    else:
        target = mds[0] if mds else None
    if target is None:
        return {"ok": False, "hinweis": "Zielmethode nicht gefunden"}

    dbg = JavaDebugger(vm, max_steps=int(max_steps))
    for pc in bps:
        dbg.add_breakpoint(target.cls, target.name, target.sig, pc)
    observations = []
    applied = False
    try:
        st = dbg.start(target.cls, target.name, target.sig, args, stop_at_entry=False)
        guard = 0
        while st.get("mode") == "paused" and guard < 100000:
            guard += 1
            observations.append({"step": st.get("step"), "pc": st.get("pc"),
                                 "registers": dbg.registers()})
            if not applied and set_regs:
                for i, v in set_regs.items():
                    dbg.set_register(i, v)
                applied = True
            st = dbg.cont()
    except (DalvikUnknownOpcode, DalvikException) as e:
        return {"ok": False, "hinweis": f"Laufzeitfehler: {e!r}", "observations": observations}
    except Exception as e:
        try:
            dbg.stop()
        except Exception:
            pass
        return {"ok": False, "hinweis": f"Debugger-Fehler: {e!r}", "observations": observations}

    return {"ok": True, "called": {"cls": target.cls, "name": target.name, "sig": target.sig, "args": args},
            "result": st.get("result"), "mode": st.get("mode"), "error": st.get("error"),
            "breakpoints": bps, "set_regs": set_regs, "observations": observations[:50],
            "studio_root": studio_root, "zone": zone,
            "timeline_len": len(dbg.timeline)}


def native_app_lifecycle_run(ctx, studio_root=None, zone="original", entry=None, max_steps=400000):
    """MS-4 (erste Slice): eine ganze App am Lifecycle-Einstieg starten. Laedt die Entry-Klasse
    (`entry` = Deskriptor `Lcom/foo/MainActivity;`) lazy aus dem Smali-Baum EINER Zone
    (`studio_root` + `zone` original|patched) und ruft die vorhandenen Lifecycle-Callbacks
    (<init>, onCreate, onStart, onResume) der Reihe nach auf. App-interne Callees werden lazy
    nachgeladen. Liefert called/skipped/missing (Coverage-Loop) — zielagnostisch, read-only."""
    try:
        from core.java.dalvik import DalvikVM, DalvikUnknownOpcode, DalvikException
        from core.java.class_provider import StudioClassProvider
        from core.java.lifecycle import LifecycleRunner
    except Exception as e:
        return {"ok": False, "hinweis": f"Java-Kern nicht ladbar: {e!r}"}
    if not studio_root and ctx is not None:
        try:
            _unp = ctx.engine.get_unpacked_dir_name() if getattr(ctx, "engine", None) else "base_unpacked_apktool"
            _base = ctx.cfg.paths.get("DEST_DIR" if zone in ("destination", "patched") else "APP_SOURCE_DIR", "")
            if _base:
                cand = os.path.join(_base, _unp)
                if os.path.isdir(cand):
                    studio_root = cand
        except Exception:
            pass
    if not studio_root:
        return {"ok": False, "hinweis": "studio_root (Zonen-Wurzel der entpackten App) fehlt"}
    if not entry or not str(entry).strip():
        return {"ok": False, "hinweis": "entry-Deskriptor fehlt (z. B. Lcom/foo/MainActivity;)"}
    vm = DalvikVM(max_depth=256)
    try:
        prov = StudioClassProvider(studio_root, zone=zone)
    except Exception as e:
        return {"ok": False, "hinweis": f"Studio-Provider nicht ladbar: {e!r}"}
    try:
        report = LifecycleRunner(vm, prov).run(str(entry).strip())
    except (DalvikUnknownOpcode, DalvikException) as e:
        return {"ok": False, "hinweis": f"Laufzeitfehler: {e!r}"}
    except Exception as e:
        return {"ok": False, "hinweis": f"Lifecycle-Fehler: {e!r}"}
    return {"ok": True, "studio_root": studio_root, "zone": zone, "entry": report.get("entry"),
            "called": report.get("called", []), "skipped": report.get("skipped", []),
            "errors": report.get("errors", []), "missing": report.get("missing", [])[:50],
            "statics_keys": sorted(list(vm.statics.keys()))[:50]}


# ===== Gläserner App-Lauf (MCP-Parität zum GUI-Gläsern-Lauf) =================
_GLASS_SESSION_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     "secrets", "probe", "out", "glass")

# In-memory Zustand der letzten gläsernen UI + Session: erlaubt MCP-Interaktion
# (render/tap/key/clock/step/reverse_step/goto/inspect) ohne erneuten Lauf.
_LAST_UI = {"surface": None, "_surf_id": None, "updated_epoch_ms": 0}
_DEFAULT_VIRTUAL_CLOCK = None
_LAST_GLASS = {}

_FILE_LOCKS_GUARD = threading.Lock()
_FILE_LOCKS: Dict[str, threading.Lock] = {}
_GUI_CMD_SEQ_LOCK = threading.Lock()
_GUI_CMD_SEQ = 0


def _clean_surrogates(obj: Any) -> Any:
    """Ersetzt einsame UTF-16-Surrogates rekursiv, damit UTF-8-JSON-Dump nie scheitert."""
    if isinstance(obj, str):
        try:
            obj.encode("utf-8")
            return obj
        except UnicodeEncodeError:
            return obj.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    if isinstance(obj, dict):
        return {_clean_surrogates(k): _clean_surrogates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_surrogates(x) for x in obj]
    if isinstance(obj, tuple):
        return tuple(_clean_surrogates(x) for x in obj)
    return obj


def _atomic_write_json(path: str, payload: Any, indent: int = 2) -> str:
    """Schreibt JSON atomar ueber `<path>.<pid>.<tid>.tmp` + `os.replace`."""
    abs_path = os.path.abspath(path)
    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{abs_path}.{os.getpid()}.{threading.get_ident()}.tmp"
    cleaned = _clean_surrogates(payload)
    raw = json.dumps(cleaned, ensure_ascii=False, indent=indent, default=str)
    raw = _clean_surrogates(raw)
    try:
        with open(tmp, "w", encoding="utf-8", errors="replace") as fh:
            fh.write(raw)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        for attempt in range(8):
            try:
                os.replace(tmp, abs_path)
                return path
            except PermissionError:
                if attempt >= 7:
                    raise
                time.sleep(0.005 * (attempt + 1))
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return path


@contextmanager
def _file_lock(lock_path: str, timeout: float = 2.5):
    """Exklusives Prozess- & Thread-Lock per `O_CREAT | O_EXCL` mit Stale-Cleanup (>10s)."""
    norm = os.path.normcase(os.path.abspath(lock_path))
    parent = os.path.dirname(norm)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with _FILE_LOCKS_GUARD:
        tlock = _FILE_LOCKS.get(norm)
        if tlock is None:
            tlock = threading.Lock()
            _FILE_LOCKS[norm] = tlock

    deadline = time.monotonic() + max(0.05, float(timeout or 2.5))
    if not tlock.acquire(timeout=max(0.01, deadline - time.monotonic())):
        raise TimeoutError(f"Thread-Lock-Timeout: {lock_path}")
    try:
        acquired_fd = None
        while True:
            try:
                acquired_fd = os.open(norm, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                try:
                    st = os.stat(norm)
                    if (time.time() - st.st_mtime) > 10.0:
                        try:
                            os.remove(norm)
                            continue
                        except OSError:
                            pass
                except FileNotFoundError:
                    continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Datei-Lock-Timeout: {lock_path}")
                time.sleep(0.01)
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Datei-Lock-Timeout (Permission): {lock_path}")
                time.sleep(0.01)
        try:
            try:
                os.write(acquired_fd, f"{os.getpid()}:{threading.get_ident()}\n".encode("ascii"))
            finally:
                os.close(acquired_fd)
            yield
        finally:
            try:
                os.remove(norm)
            except OSError:
                pass
    finally:
        tlock.release()


def _merge_missing(*sources) -> List[Any]:
    """Kombiniert Missing-Eintraege mehrerer Quellen dedupliziert nach `(cls, name, sig)`."""
    merged: List[Any] = []
    seen = set()
    for src in sources:
        if not src:
            continue
        for item in src:
            if isinstance(item, dict):
                key = (
                    str(item.get("cls") or ""),
                    str(item.get("name") or ""),
                    str(item.get("sig") or ""),
                )
            elif isinstance(item, (tuple, list)) and len(item) >= 3:
                key = (str(item[0] or ""), str(item[1] or ""), str(item[2] or ""))
            else:
                key = ("", str(item), "")
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def _collect_glass_missing(disk_state: Optional[dict] = None) -> List[Any]:
    dbg = _LAST_GLASS.get("jdb")
    stubs = _LAST_GLASS.get("stubs")
    vm_miss = getattr(getattr(dbg, "vm", None), "missing", None) if dbg is not None else None
    stubs_miss = getattr(stubs, "missing", None) if stubs is not None else None
    if _LAST_GLASS:
        return _merge_missing(_LAST_GLASS.get("missing"), vm_miss, stubs_miss)
    return _merge_missing((disk_state or {}).get("missing"))


def _queue_gui_cmd(ctx, entry: dict) -> int:
    global _GUI_CMD_SEQ
    data_dir = os.path.join(_base_dir(ctx), "data")
    qpath = os.path.join(data_dir, "gui_cmd_queue.json")
    ack_path = os.path.join(data_dir, "gui_cmd_ack.json")
    os.makedirs(data_dir, exist_ok=True)
    with _file_lock(qpath + ".lock"):
        cur: List[dict] = []
        if os.path.isfile(qpath):
            try:
                with open(qpath, "r", encoding="utf-8") as fh:
                    old = json.load(fh)
                if isinstance(old, list):
                    cur = [it for it in old if isinstance(it, dict)]
            except Exception:
                cur = []
        max_q = 0
        for it in cur:
            try:
                max_q = max(max_q, int(it.get("seq", 0) or 0))
            except Exception:
                pass
        ack_seq = 0
        if os.path.isfile(ack_path):
            try:
                with open(ack_path, "r", encoding="utf-8") as afh:
                    ack_d = json.load(afh)
                if isinstance(ack_d, dict):
                    ack_seq = int(ack_d.get("last_seq", 0) or 0)
            except Exception:
                ack_seq = 0
        with _GUI_CMD_SEQ_LOCK:
            _GUI_CMD_SEQ = max(_GUI_CMD_SEQ, max_q, ack_seq) + 1
            next_seq = _GUI_CMD_SEQ
        if isinstance(entry, dict):
            entry.setdefault("ts_ms", int(time.time() * 1000))
            entry.setdefault("seq", next_seq)
        cur.append(entry)
        _atomic_write_json(qpath, cur)
        return len(cur)


def _sync_headless_glass_outputs(ctx=None):
    """Aktualisiert _LAST_UI, last_ui.json, last_net_log.json und live_state.json aus _LAST_GLASS."""
    g = _LAST_GLASS
    if not g:
        return None
    tree = g.get("tree")
    stubs = g.get("stubs")
    dbg = g.get("jdb")
    net_log = g.get("net_log")
    fusion = g.get("fusion")
    inst = g.get("instance")
    nc_obj = g.get("net_condition")
    vm_obj = getattr(dbg, "vm", None) if dbg is not None else None
    vm_miss = list(getattr(vm_obj, "missing", []) or []) if vm_obj is not None else []
    stubs_miss = list(getattr(stubs, "missing", []) or []) if stubs is not None else []
    missing = _merge_missing(g.get("missing"), vm_miss, stubs_miss)
    out_dir = _GLASS_SESSION_FOLDER

    ui_snap = None
    if tree is not None:
        try:
            from core.java.surface import SceneGraphSurface
            from core.java.ui_mcp_bridge import build_ui_snapshot_dict, persist_live_ui_snapshot
            _live = SceneGraphSurface(tree, vm=vm_obj, activity=getattr(stubs, "_current_activity", None))
            pkg = (getattr(inst, "package", None) or g.get("package") or "")
            ui_snap = build_ui_snapshot_dict(_live, package=pkg, source="headless_glass_run")
            _LAST_UI["surface"] = _live
            _LAST_UI["_surf_id"] = id(_live)
            _LAST_UI["updated_epoch_ms"] = ui_snap["updated_epoch_ms"]
            os.makedirs(out_dir, exist_ok=True)
            persist_live_ui_snapshot(ui_snap, out_dir=out_dir)
        except Exception:
            ui_snap = None

    st = dbg.state() if dbg is not None else {"mode": "idle", "step": 0}
    app_state = None
    vclock = None
    try:
        from core.java.glass_runner import extract_app_state_snapshot
        app_state = extract_app_state_snapshot(stubs, fusion=fusion)
        vclock = app_state.get("virtual_clock")
    except Exception:
        pass
    nodes = tree.nodes() if tree else []
    tl_raw = (getattr(dbg, "timeline", None) or []) if dbg is not None else []
    payload = {
        "writer": "mcp_headless",
        "mode": st.get("mode"),
        "step": st.get("step"),
        "reason": st.get("reason"),
        "cls": st.get("cls"),
        "name": st.get("name"),
        "sig": st.get("sig"),
        "pc": st.get("pc"),
        "op": st.get("op"),
        "zone": g.get("zone", "original"),
        "entry": g.get("entry"),
        "package": getattr(inst, "package", None) or g.get("package"),
        "instance_id": getattr(inst, "instance_id", None) or g.get("instance_id") or "main",
        "seam_profile": g.get("seam_profile", "self_cache"),
        "persist_session": bool(g.get("persist_session", False)),
        "net_mode": g.get("net_mode", "mock"),
        "net_condition": (nc_obj.to_dict() if nc_obj else None),
        "virtual_clock": vclock,
        "app_state": app_state,
        "callstack": (dbg.callstack() if dbg and hasattr(dbg, "callstack") else []),
        "net_count": len(net_log) if net_log is not None else 0,
        "net_entries": net_log.to_list()[-30:] if net_log is not None and hasattr(net_log, "to_list") else [],
        "views": len(nodes),
        "views_count": len(nodes),
        "missing": missing[:100],
        "missing_count": len(missing),
        "timeline_count": len(tl_raw),
        "timeline_tail": [
            {"step": r.get("step"), "cls": r.get("cls"), "name": r.get("name"), "pc": r.get("pc"), "op": r.get("op")}
            for r in tl_raw[-30:] if isinstance(r, dict)
        ],
    }
    devs_obj = getattr(vm_obj, "deviations", None) if vm_obj is not None else None
    if devs_obj is not None:
        if hasattr(devs_obj, "to_list"):
            payload["deviations"] = devs_obj.to_list()
        elif isinstance(devs_obj, (list, tuple)):
            payload["deviations"] = list(devs_obj)
    try:
        os.makedirs(out_dir, exist_ok=True)
        if net_log is not None:
            _atomic_write_json(
                os.path.join(out_dir, "last_net_log.json"),
                net_log.to_list(),
                indent=1,
            )
        _atomic_write_json(
            os.path.join(out_dir, "live_state.json"),
            payload,
            indent=1,
        )
    except Exception:
        pass
    return payload


def _parse_virtual_clock_arg(raw):
    if raw is None:
        return None, None
    if isinstance(raw, dict):
        return dict(raw), None
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None, None
        if s.startswith("{"):
            try:
                d = json.loads(s)
                if isinstance(d, dict):
                    return d, None
            except Exception as e:
                return None, f"virtual_clock ist kein gueltiges JSON: {e!r}"
        return {"iso": s, "frozen": True}, None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return {"epoch_ms": int(raw), "frozen": True}, None
    return None, "virtual_clock Format nicht unterstuetzt"


def native_glass_run(ctx, studio_root=None, zone="original", entry=None,
                     action_points=None, net_replay=None, max_steps=400000,
                     seam_profile="self_cache", java_cutoffs=None, jni_cutoffs=None,
                     net_condition=None, instance_id=None, native_fusion=False,
                     virtual_clock=None, net_mode="mock", record_timeline=False,
                     persist_session=None, max_wall_s=30.0):
    """Gläserner App-Lauf über MCP via GlassAppRunner: Lifecycle durch JavaDebugger + View-/Netz-/Clock-Recorder
    + konfigurierbare 2-Ebenen-Seams + NetCondition (online/throttled/offline/timeout) + Instanz/Fork + explizitem persist_session Opt-In."""
    import json
    try:
        from core.java.dalvik import DalvikException, DalvikUnknownOpcode
        from core.java.net_recorder import ReplayResponder, DbReplayResponder, NetResponse, NetCondition
        from core.java.seam_config import SeamConfig
        from core.java.glass_runner import GlassAppRunner, InstanceState, ProjectSnapshotService, apply_virtual_clock_spec, extract_app_state_snapshot
    except Exception as e:
        return {"ok": False, "hinweis": f"Java-Kern nicht ladbar: {e!r}"}
    if not studio_root and ctx is not None:
        try:
            _unp = ctx.engine.get_unpacked_dir_name() if getattr(ctx, "engine", None) else "base_unpacked_apktool"
            _base = ctx.cfg.paths.get("DEST_DIR" if zone in ("destination", "patched") else "APP_SOURCE_DIR", "")
            if _base:
                cand = os.path.join(_base, _unp)
                if os.path.isdir(cand):
                    studio_root = cand
        except Exception:
            pass
    if not studio_root:
        return {"ok": False, "hinweis": "studio_root (Zonen-Wurzel der entpackten App) fehlt"}

    def _parse_json_arg(nm, val):
        if isinstance(val, str) and val.strip():
            try:
                return json.loads(val), None
            except Exception:
                return None, f"{nm} ist kein gueltiges JSON"
        return val, None

    for nm, raw in (("action_points", action_points), ("net_replay", net_replay),
                    ("java_cutoffs", java_cutoffs), ("jni_cutoffs", jni_cutoffs),
                    ("net_condition", net_condition)):
        parsed, err = _parse_json_arg(nm, raw)
        if err:
            return {"ok": False, "hinweis": err}
        if nm == "action_points": action_points = parsed
        elif nm == "net_replay": net_replay = parsed
        elif nm == "java_cutoffs": java_cutoffs = parsed
        elif nm == "jni_cutoffs": jni_cutoffs = parsed
        elif nm == "net_condition": net_condition = parsed

    vclock_spec, vclock_err = _parse_virtual_clock_arg(virtual_clock)
    if vclock_err:
        return {"ok": False, "hinweis": vclock_err}
    if vclock_spec is None and isinstance(_DEFAULT_VIRTUAL_CLOCK, dict):
        vclock_spec = dict(_DEFAULT_VIRTUAL_CLOCK)

    bdir = _base_dir(ctx)
    action_points = list(action_points or [])
    scfg = SeamConfig.from_profile(seam_profile or "self_cache")
    if isinstance(java_cutoffs, list):
        scfg.java_cutoffs = java_cutoffs
    if isinstance(jni_cutoffs, list):
        scfg.jni_cutoffs = jni_cutoffs

    svc = ProjectSnapshotService(base_dir=bdir)
    inst = svc.load_fork(instance_id) if instance_id else InstanceState(app_root=studio_root, zone=zone, seam_profile=scfg.unpack_mode)
    eff_persist = bool(persist_session) if persist_session is not None else bool(instance_id and inst.is_fork())
    scfg.persist_appdata = eff_persist

    responder = None
    if net_replay:
        mapping = {}
        for k, v in dict(net_replay).items():
            if isinstance(v, dict):
                mapping[str(k)] = NetResponse(v.get("code", 200), v.get("headers") or {}, v.get("body", ""))
            else:
                mapping[str(k)] = NetResponse(200, {}, v if isinstance(v, (str, bytes)) else str(v))
        responder = ReplayResponder(mapping)

    eff_net_mode = str(net_mode or "mock").strip().lower()
    api_db_svc = None
    if "db" in eff_net_mode:
        eff_net_mode = "db_replay"
        try:
            from services.api_db_service import ApiDbService
            dbp = (ctx.cfg.paths.get("API_DB") if (ctx and getattr(ctx, "cfg", None)) else None) or os.path.join(bdir, "data", "api_traffic.db")
            if os.path.isfile(dbp):
                api_db_svc = ApiDbService(dbp)
                responder = DbReplayResponder(api_db_svc, fallback=responder)
        except Exception:
            pass
    elif "live" in eff_net_mode:
        eff_net_mode = "live"
        if responder is None:
            def _live_http_resp(req):
                import urllib.error as _ue
                import urllib.request as _ur
                try:
                    rq = _ur.Request(req.url, data=(req.body or None), method=(req.method or "GET"))
                    for hk, hv in (req.headers or {}).items():
                        try:
                            rq.add_header(str(hk), str(hv))
                        except Exception:
                            pass
                    with _ur.urlopen(rq, timeout=12) as resp:
                        return NetResponse(int(getattr(resp, "status", 200) or 200),
                                           dict(resp.headers.items()), resp.read(), getattr(resp, "reason", "OK"))
                except _ue.HTTPError as he:
                    try:
                        hb = he.read()
                    except Exception:
                        hb = b""
                    return NetResponse(int(he.code), dict((he.headers or {}).items()), hb, str(he.reason))
                except Exception as exc:
                    return NetResponse(502, {"X-Glass-Source": "live-http-error"}, str(exc).encode("utf-8", "replace"), "Bad Gateway")
            responder = _live_http_resp
    else:
        eff_net_mode = "mock"

    prev_dbg = _LAST_GLASS.get("jdb")
    if prev_dbg is not None:
        try:
            prev_thr = getattr(prev_dbg, "_thread", None)
            if getattr(prev_dbg, "mode", None) in ("run", "step", "over", "out", "paused") or (
                prev_thr is not None and prev_thr.is_alive()
            ):
                prev_dbg.stop(wait_timeout=1.0)
                if prev_thr is not None and prev_thr.is_alive():
                    prev_thr.join(timeout=0.5)
        except Exception:
            pass

    runner = GlassAppRunner(studio_root, zone=zone, seam_config=scfg, instance_state=inst, base_dir=bdir)
    try:
        built = runner.build(
            native_fusion=bool(native_fusion),
            net_mode=eff_net_mode,
            net_responder=responder,
            rules_path=_api_rules_path(ctx),
            api_db=api_db_svc,
            virtual_clock=vclock_spec,
        )
    except Exception as e:
        return {"ok": False, "hinweis": f"GlassAppRunner.build fehlgeschlagen: {e!r}"}

    stubs = built["stubs"]
    tree = built["tree"]
    net_log = built["net_log"]
    dbg = built["jdb"]
    dbg.max_steps = int(max_steps)
    dbg.record_timeline = bool(record_timeline)

    nc_obj = None
    if net_condition is not None and hasattr(stubs, "set_net_condition"):
        nc_obj = stubs.set_net_condition(net_condition)
    elif net_condition is not None:
        nc_obj = NetCondition.from_dict(net_condition)

    ap_errors = []
    for ap in action_points:
        try:
            dbg.add_action_point(ap.get("cls"), ap.get("name"), ap.get("sig"), ap.get("pc"),
                                 ap.get("assigns") or [], cond=ap.get("cond"),
                                 when_continue=bool(ap.get("when_continue")),
                                 one_shot=bool(ap.get("one_shot")), hit=ap.get("hit"))
        except Exception as e:
            ap_errors.append(repr(e))

    ent = (str(entry).strip() or None) if entry else None
    run_report = {}

    def run_fn():
        rep = runner.runner.run_app(studio_root=studio_root, launcher=ent)
        if isinstance(rep, dict):
            run_report.update(rep)
        return rep

    wall_limit = max(0.2, float(max_wall_s or 30.0))
    deadline = time.monotonic() + wall_limit
    timed_out = False
    observations = []
    try:
        st = dbg.start_app(
            run_fn,
            stop_at_entry=False,
            wait_timeout=min(5.0, max(0.1, deadline - time.monotonic())),
        )
        guard = 0
        while st.get("mode") in ("paused", "run") and time.monotonic() < deadline and guard < 100000:
            guard += 1
            if st.get("mode") == "paused":
                observations.append({"step": st.get("step"), "pc": st.get("pc"),
                                     "cls": st.get("cls"), "name": st.get("name"),
                                     "registers": dbg.registers()})
                st = dbg.cont(wait_timeout=min(5.0, max(0.1, deadline - time.monotonic())))
            else:
                thr = getattr(dbg, "_thread", None)
                rem = max(0.05, deadline - time.monotonic())
                if thr is not None and thr.is_alive():
                    thr.join(timeout=min(1.0, rem))
                else:
                    time.sleep(min(0.1, rem))
                st = dbg.state()

        thr = getattr(dbg, "_thread", None)
        if getattr(dbg, "mode", None) not in ("run", "step", "over", "out", "paused") and thr is not None and thr.is_alive():
            thr.join(timeout=0.5)
        if getattr(dbg, "mode", None) in ("run", "step", "over", "out", "paused") or (
            thr is not None and thr.is_alive()
        ):
            eng = (built.get("fusion") or {}).get("eng") if isinstance(built.get("fusion"), dict) else None
            if eng is not None and getattr(eng, "cpu", None) is not None:
                try:
                    eng.cpu.stopped = True
                except Exception:
                    pass
            dbg.stop(wait_timeout=2.0)
            if thr is not None and thr.is_alive():
                thr.join(timeout=0.5)
            st = dbg.state()
            timed_out = True
    except (DalvikUnknownOpcode, DalvikException) as e:
        return {"ok": False, "hinweis": f"Laufzeitfehler: {e!r}", "net_log": net_log.to_list()}
    except Exception as e:
        try:
            dbg.stop(wait_timeout=1.0)
        except Exception:
            pass
        return {"ok": False, "hinweis": f"Gläsern-Fehler: {e!r}", "net_log": net_log.to_list()}

    if tree is not None and getattr(tree, "root", None) is None:
        rec = getattr(stubs, "rn_mount_recorder", None)
        if rec is not None and getattr(rec, "nodes", None):
            try:
                rn_surf = rec.surface(relayout=not getattr(rec, "_has_layout", False))
                if getattr(rn_surf, "tree", None) is not None and rn_surf.tree.root is not None:
                    tree = rn_surf.tree
                    runner.tree = tree
            except Exception:
                pass

    missing = _merge_missing(
        dict(run_report).get("missing") if run_report else None,
        getattr(getattr(dbg, "vm", None), "missing", []),
        getattr(stubs, "missing", []),
    )
    _LAST_GLASS.clear()
    _LAST_GLASS.update({
        "runner": runner,
        "jdb": dbg,
        "stubs": stubs,
        "tree": tree,
        "net_log": net_log,
        "fusion": built.get("fusion"),
        "instance": inst,
        "package": inst.package,
        "instance_id": inst.instance_id,
        "studio_root": studio_root,
        "zone": zone,
        "entry": ent,
        "seam_profile": scfg.unpack_mode,
        "persist_session": eff_persist,
        "net_mode": eff_net_mode,
        "net_condition": nc_obj,
        "missing": missing,
    })

    if scfg.persist_appdata and (bool(built.get("fusion")) or os.path.abspath(bdir) != os.path.abspath(".")):
        try:
            from core.java.sqlite_prefs_bridge import persist_java_appdata_to_dir
            persist_java_appdata_to_dir(stubs, inst.pkg_appdata_dir(bdir))
        except Exception:
            pass

    payload = _sync_headless_glass_outputs(ctx) or {}
    nodes = tree.nodes() if tree else []
    view_summary = {"views": len(nodes),
                    "texts": [n.text for n in nodes if getattr(n, "text", "")][:20]}
    vclock_state = payload.get("virtual_clock")
    app_state = payload.get("app_state")
    ret = {"ok": True, "studio_root": studio_root, "zone": zone, "entry": ent,
           "mode": st.get("mode"), "step": st.get("step"), "view": view_summary,
           "seam_profile": scfg.unpack_mode, "instance_id": inst.instance_id,
           "persist_session": eff_persist,
           "net_mode": eff_net_mode,
           "net_condition": (nc_obj.to_dict() if nc_obj else None),
           "virtual_clock": vclock_state,
           "app_state": app_state,
           "missing_count": len(missing),
           "timeline_count": len(getattr(dbg, "timeline", []) or []),
           "net_log": net_log.to_list(), "net_count": len(net_log),
           "action_points": len(action_points), "ap_errors": ap_errors,
           "observations": observations[:50], "observation_count": len(observations)}
    if timed_out:
        ret["timed_out"] = True
    return ret


def native_glass_state(ctx):
    """Read-only: liest den geteilten Glass-Debugger-Status (`live_state.json`)."""
    p = os.path.join(_GLASS_SESSION_FOLDER, "live_state.json")
    if not os.path.isfile(p):
        return {"ok": True, "state": None, "hinweis": "noch kein Glass-Zustand geschrieben"}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return {"ok": True, "state": json.load(f)}
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}


def native_glass_clock(ctx, op="get", iso=None, epoch_ms=None, tz=None, frozen=None,
                       shift_days=0.0, shift_hours=0.0, shift_minutes=0.0, shift_seconds=0.0,
                       step_ms=None, forward_to_gui=True):
    """Liest, setzt, verschiebt oder resettet die Virtuelle Uhr (Headless + Live-GUI)."""
    global _DEFAULT_VIRTUAL_CLOCK
    from core.java.glass_runner import apply_virtual_clock_spec
    from core.java.virtual_clock import get_virtual_clock_state, parse_datetime_spec

    act = str(op or "get").strip().lower()
    stubs = _LAST_GLASS.get("stubs")
    eng = ((_LAST_GLASS.get("fusion") or {}).get("eng")
           if isinstance(_LAST_GLASS.get("fusion"), dict) else None)

    if act == "get":
        if stubs is not None:
            st = get_virtual_clock_state(stubs)
            return {"ok": True, "source": "headless", "clock": st}
        p = os.path.join(_GLASS_SESSION_FOLDER, "live_state.json")
        if os.path.isfile(p):
            try:
                ls = json.load(open(p, "r", encoding="utf-8"))
                if ls.get("virtual_clock"):
                    return {"ok": True, "source": "live_state", "clock": ls["virtual_clock"]}
            except Exception:
                pass
        if isinstance(_DEFAULT_VIRTUAL_CLOCK, dict):
            return {"ok": True, "source": "preset", "clock": dict(_DEFAULT_VIRTUAL_CLOCK)}
        import time as _t
        import datetime as _dt
        ms = int(_t.time() * 1000)
        return {"ok": True, "source": "host", "clock": {
            "epoch_ms": ms, "iso": _dt.datetime.fromtimestamp(ms / 1000.0).strftime("%Y-%m-%d %H:%M:%S"),
            "tz": "System", "frozen": False, "custom": False,
        }}

    if act == "reset":
        spec = {"reset": True}
        if tz is not None:
            spec["tz"] = tz
        _DEFAULT_VIRTUAL_CLOCK = None
        res_clock = apply_virtual_clock_spec(stubs, spec, eng=eng) if stubs is not None else {"reset": True, "tz": tz or "System"}
        if stubs is not None:
            _persist_ui_snapshot()
            _sync_headless_glass_outputs(ctx)
        if forward_to_gui:
            _queue_gui_cmd(ctx, {"cmd": "set_virtual_clock", **spec})
        return {"ok": True, "op": "reset", "clock": res_clock, "queued_to_gui": bool(forward_to_gui)}

    if act == "shift":
        spec = {
            "shift_days": float(shift_days or 0.0),
            "shift_hours": float(shift_hours or 0.0),
            "shift_minutes": float(shift_minutes or 0.0),
            "shift_seconds": float(shift_seconds or 0.0),
        }
        if stubs is not None:
            res_clock = apply_virtual_clock_spec(stubs, spec, eng=eng)
            _DEFAULT_VIRTUAL_CLOCK = {
                "epoch_ms": res_clock["epoch_ms"],
                "frozen": res_clock["frozen"],
                **({"tz_name": res_clock["tz"]} if res_clock.get("tz") and res_clock["tz"] != "System" else {}),
            }
            _persist_ui_snapshot()
            _sync_headless_glass_outputs(ctx)
        else:
            delta_ms = int(round(((spec["shift_days"] * 86400.0) + (spec["shift_hours"] * 3600.0)
                                  + (spec["shift_minutes"] * 60.0) + spec["shift_seconds"]) * 1000.0))
            cur = dict(_DEFAULT_VIRTUAL_CLOCK or {})
            import time as _t
            base_ms = cur.get("epoch_ms") or (parse_datetime_spec(cur["iso_str"], cur.get("tz_name")) if cur.get("iso_str") else int(_t.time() * 1000))
            cur["epoch_ms"] = int(base_ms) + delta_ms
            cur.pop("iso_str", None)
            _DEFAULT_VIRTUAL_CLOCK = cur
            res_clock = cur
        if forward_to_gui:
            _queue_gui_cmd(ctx, {"cmd": "set_virtual_clock", **spec})
        return {"ok": True, "op": "shift", "clock": res_clock, "queued_to_gui": bool(forward_to_gui)}

    if act == "set":
        spec = {}
        if iso is not None:
            try:
                parse_datetime_spec(iso, tz)
            except Exception as e:
                return {"ok": False, "hinweis": f"Ungueltiges Datums-/Zeitformat: {e}"}
            spec["iso"] = str(iso)
        if epoch_ms is not None:
            spec["epoch_ms"] = int(epoch_ms)
        if tz is not None:
            spec["tz"] = str(tz)
        if frozen is not None:
            spec["frozen"] = bool(frozen)
        if step_ms is not None:
            spec["step_ms"] = int(step_ms)
        if not spec:
            return {"ok": False, "hinweis": "Mindestens iso, epoch_ms, tz, frozen oder step_ms angeben."}

        preset = dict(_DEFAULT_VIRTUAL_CLOCK or {})
        if "iso" in spec:
            preset["iso_str"] = spec["iso"]
            preset.pop("epoch_ms", None)
        if "epoch_ms" in spec:
            preset["epoch_ms"] = spec["epoch_ms"]
            preset.pop("iso_str", None)
        if "tz" in spec:
            preset["tz_name"] = spec["tz"]
        if "frozen" in spec:
            preset["frozen"] = spec["frozen"]
        if "step_ms" in spec:
            preset["step_ms"] = spec["step_ms"]
        _DEFAULT_VIRTUAL_CLOCK = preset

        if stubs is not None:
            res_clock = apply_virtual_clock_spec(stubs, spec, eng=eng)
            _persist_ui_snapshot()
            _sync_headless_glass_outputs(ctx)
        else:
            res_clock = preset
        if forward_to_gui:
            _queue_gui_cmd(ctx, {"cmd": "set_virtual_clock", **spec})
        return {"ok": True, "op": "set", "clock": res_clock, "queued_to_gui": bool(forward_to_gui)}

    return {"ok": False, "hinweis": f"Unbekannte Operation op={op!r} (erlaubt: get|set|shift|reset)"}


def native_glass_inspect(ctx, section="all", query="", limit=100):
    """Tiefen-Inspektion des letzten Glass-Laufs (App-State, SharedPrefs, SQLite, Missing, Seams, Timeline, Heap, Befund)."""
    from core.java.glass_runner import extract_app_state_snapshot
    sec = str(section or "all").strip().lower()
    lim = max(1, int(limit or 100))
    q = str(query or "").strip()

    stubs = _LAST_GLASS.get("stubs")
    dbg = _LAST_GLASS.get("jdb")
    fusion = _LAST_GLASS.get("fusion")
    net_log = _LAST_GLASS.get("net_log")

    disk_state = None
    p = os.path.join(_GLASS_SESSION_FOLDER, "live_state.json")
    if os.path.isfile(p):
        try:
            disk_state = json.load(open(p, "r", encoding="utf-8"))
        except Exception:
            disk_state = None

    if sec in ("app_state", "prefs", "sqlite"):
        if stubs is not None:
            app_st = extract_app_state_snapshot(stubs, fusion=fusion, db_query=q)
            if sec == "prefs":
                return {"ok": True, "section": "prefs", "shared_prefs": app_st.get("shared_prefs", {})}
            if sec == "sqlite":
                return {"ok": True, "section": "sqlite", "sqlite_dbs": app_st.get("sqlite_dbs", {})}
            return {"ok": True, "section": "app_state", **app_st}
        if disk_state and disk_state.get("app_state"):
            app_st = disk_state["app_state"]
            if sec == "prefs":
                return {"ok": True, "section": "prefs", "shared_prefs": app_st.get("shared_prefs", {})}
            if sec == "sqlite":
                return {"ok": True, "section": "sqlite", "sqlite_dbs": app_st.get("sqlite_dbs", {})}
            return {"ok": True, "section": "app_state", **app_st}
        return {"ok": True, "section": sec, "hinweis": "Kein aktiver App-State vorhanden."}

    if sec == "missing":
        miss = _collect_glass_missing(disk_state)
        if q:
            miss = [m for m in miss if q.lower() in str(m).lower()]
        return {"ok": True, "section": "missing", "count": len(miss), "missing": miss[:lim]}

    if sec == "seams":
        jni_entries = []
        hook = (fusion or {}).get("hook") if isinstance(fusion, dict) else None
        if hook is not None and getattr(hook, "log", None) is not None and hasattr(hook.log, "to_list"):
            jni_entries = hook.log.to_list()[-lim:]
        elif disk_state and disk_state.get("jni_entries"):
            jni_entries = list(disk_state["jni_entries"])[-lim:]
        return {
            "ok": True,
            "section": "seams",
            "seam_profile": _LAST_GLASS.get("seam_profile") or (disk_state or {}).get("seam_profile"),
            "jni_count": len(jni_entries),
            "jni_entries": jni_entries,
        }

    if sec == "timeline":
        if dbg is not None:
            tl = list(getattr(dbg, "timeline", []) or [])
            out_tl = [
                {"step": r.get("step"), "cls": r.get("cls"), "name": r.get("name"), "pc": r.get("pc"), "op": r.get("op")}
                for r in tl[-lim:] if isinstance(r, dict)
            ]
            return {"ok": True, "section": "timeline", "step_count": int(getattr(dbg, "step_count", 0) or 0),
                    "recorded_len": len(tl), "timeline": out_tl}
        return {"ok": True, "section": "timeline",
                "step_count": (disk_state or {}).get("step", 0),
                "recorded_len": (disk_state or {}).get("timeline_count", 0),
                "timeline": list((disk_state or {}).get("timeline_tail") or [])[-lim:]}

    if sec == "heap":
        vm = getattr(dbg, "vm", None) if dbg is not None else None
        statics_out = {}
        if vm is not None:
            for c, flds in (getattr(vm, "statics", None) or {}).items():
                if q and q.lower() not in str(c).lower():
                    continue
                if isinstance(flds, dict):
                    statics_out[str(c)] = {str(fk): str(fv)[:200] for fk, fv in list(flds.items())[:40]}
                if len(statics_out) >= lim:
                    break
        regs_out = []
        if dbg is not None and hasattr(dbg, "registers"):
            try:
                for idx_r, rval in enumerate(dbg.registers()[:40]):
                    item = {"reg": f"v{idx_r}", "val": str(rval)[:200]}
                    if hasattr(rval, "fields") and isinstance(rval.fields, dict):
                        item["cls"] = getattr(rval, "cls", None)
                        item["fields"] = {str(fk): str(fv)[:120] for fk, fv in list(rval.fields.items())[:20]}
                    regs_out.append(item)
            except Exception:
                pass
        return {"ok": True, "section": "heap", "statics": statics_out,
                "registers": regs_out or (disk_state or {}).get("registers", [])}

    if sec == "befund":
        import datetime as _dt
        os.makedirs(_GLASS_SESSION_FOLDER, exist_ok=True)
        bpath = os.path.join(_GLASS_SESSION_FOLDER, "befund.md")
        st = dbg.state() if dbg is not None else (disk_state or {"mode": "idle"})
        pkg = _LAST_GLASS.get("package") or (disk_state or {}).get("package") or "-"
        nl_entries = (net_log.to_list() if net_log is not None and hasattr(net_log, "to_list")
                      else (disk_state or {}).get("net_entries", []))
        miss = _collect_glass_missing(disk_state)
        lines = [
            f"# Glass-Debugger Befund ({_dt.datetime.now().isoformat(timespec='seconds')})",
            "",
            f"- **App / Paket:** `{pkg}`",
            f"- **Zone / Instanz:** `{_LAST_GLASS.get('zone') or (disk_state or {}).get('zone') or '-'}` / `{_LAST_GLASS.get('instance_id') or (disk_state or {}).get('instance_id') or 'main'}`",
            f"- **Seam-Profil:** `{_LAST_GLASS.get('seam_profile') or (disk_state or {}).get('seam_profile') or 'self_cache'}`",
            f"- **Status:** `{st.get('mode')}` @ Step `{st.get('step')}`",
            f"- **Position:** `{st.get('cls')}->{st.get('name')}{st.get('sig') or ''} pc={st.get('pc')}`",
            "",
            "## Netz-Aufzeichnung",
            f"- Modus: `{_LAST_GLASS.get('net_mode') or (disk_state or {}).get('net_mode') or 'mock'}`",
            f"- Requests: `{len(nl_entries)}`",
        ]
        for e in nl_entries[-20:]:
            rq = e.get("req", {}) if isinstance(e, dict) else {}
            rp = e.get("resp", {}) if isinstance(e, dict) else {}
            lines.append(f"  - `{rq.get('method', 'GET')} {rq.get('url', '')}` -> `{rp.get('code', '-')}`")
        lines += ["", "## Missing Stubs", f"- Anzahl: `{len(miss)}`"]
        for m in miss[:20]:
            lines.append(f"  - `{m}`")
        md = "\n".join(lines) + "\n"
        with open(bpath, "w", encoding="utf-8") as fh:
            fh.write(md)
        return {"ok": True, "section": "befund", "path": bpath, "markdown": md}

    if sec == "all":
        app_st = (extract_app_state_snapshot(stubs, fusion=fusion, db_query=q)
                  if stubs is not None else (disk_state or {}).get("app_state"))
        miss = _collect_glass_missing(disk_state)
        tl = list(getattr(dbg, "timeline", []) or []) if dbg is not None else list((disk_state or {}).get("timeline_tail") or [])
        return {
            "ok": True,
            "section": "all",
            "state": (dbg.state() if dbg is not None else disk_state),
            "app_state": app_st,
            "missing_count": len(miss),
            "missing": miss[:30],
            "timeline_count": len(tl),
            "net_count": len(net_log) if net_log is not None else (disk_state or {}).get("net_count", 0),
        }

    return {"ok": False, "hinweis": f"Unbekannte Sektion {section!r} (all|app_state|prefs|sqlite|missing|seams|timeline|heap|befund)"}


def native_glass_control(ctx, cmd, params_json=None, target="gui"):
    """Sendet ein Steuerkommando an Live-GUI (`target='gui'`), Headless-Session (`'headless'`) oder beide (`'both'`)."""
    global _DEFAULT_VIRTUAL_CLOCK
    params = {}
    if params_json:
        try:
            params = json.loads(params_json) if isinstance(params_json, str) else dict(params_json)
        except Exception as e:
            return {"ok": False, "hinweis": f"params_json ungueltig: {e!r}"}
    entry = {"cmd": str(cmd), **(params if isinstance(params, dict) else {})}
    tgt = str(target or "gui").strip().lower()
    out = {"ok": True, "cmd": entry["cmd"], "target": tgt}

    if tgt in ("headless", "both"):
        op = entry["cmd"]
        dbg = _LAST_GLASS.get("jdb")
        stubs = _LAST_GLASS.get("stubs")
        if op == "set_virtual_clock":
            clk = native_glass_clock(
                ctx,
                op=("reset" if entry.get("reset") else "set"),
                iso=entry.get("iso") or entry.get("iso_str") or entry.get("datetime"),
                epoch_ms=entry.get("epoch_ms"),
                tz=entry.get("tz") if "tz" in entry else entry.get("tz_name"),
                frozen=entry.get("frozen"),
                step_ms=entry.get("step_ms"),
                forward_to_gui=False,
            )
            out["headless"] = clk
        elif op == "generate_befund":
            out["headless"] = native_glass_inspect(ctx, section="befund")
        elif op == "set_persist_session":
            en = bool(entry.get("enabled", entry.get("persist_session", False)))
            _LAST_GLASS["persist_session"] = en
            r_obj = _LAST_GLASS.get("runner")
            if r_obj is not None and getattr(r_obj, "seam_config", None) is not None:
                r_obj.seam_config.persist_appdata = en
            inst = _LAST_GLASS.get("instance")
            if en and stubs is not None and inst is not None:
                try:
                    from core.java.sqlite_prefs_bridge import persist_java_appdata_to_dir
                    persist_java_appdata_to_dir(stubs, inst.pkg_appdata_dir(_base_dir(ctx)))
                except Exception:
                    pass
            if dbg is not None:
                _persist_ui_snapshot()
                _sync_headless_glass_outputs(ctx)
            out["headless"] = {"ok": True, "persist_session": en}
        elif op in ("reset_appdata", "clear_appdata"):
            from core.java.glass_runner import InstanceState
            from core.java.sqlite_prefs_bridge import clear_java_appdata_state
            bdir = _base_dir(ctx)
            inst = _LAST_GLASS.get("instance")
            pkg = str(entry.get("package") or _LAST_GLASS.get("package") or (getattr(inst, "package", None) if inst else "") or "")
            iid = str(entry.get("instance_id") or _LAST_GLASS.get("instance_id") or (getattr(inst, "instance_id", None) if inst else "main") or "main")
            if not pkg:
                disk_st = _read_live_gui_state()
                if isinstance(disk_st, dict):
                    pkg = str(disk_st.get("package") or "")
            if entry.get("appdata_dir"):
                app_dir = os.path.abspath(str(entry["appdata_dir"]))
            elif inst is not None and not entry.get("package") and not entry.get("instance_id"):
                app_dir = inst.pkg_appdata_dir(bdir)
            elif pkg:
                app_dir = InstanceState(instance_id=iid, package=pkg).pkg_appdata_dir(bdir)
            else:
                app_dir = None
            res = clear_java_appdata_state(
                stubs=stubs,
                app_dir=app_dir,
                fusion=_LAST_GLASS.get("fusion"),
                package=(pkg or None),
            )
            if dbg is not None:
                _persist_ui_snapshot()
                _sync_headless_glass_outputs(ctx)
            out["headless"] = {**res, "package": pkg or None, "instance_id": iid}
        elif dbg is None:
            out["headless"] = {"ok": False, "hinweis": "Keine aktive Headless-Glass-Session (_LAST_GLASS leer)."}
        else:
            st_res = None
            if op == "step":
                st_res = dbg.step()
            elif op == "step_over":
                st_res = dbg.step_over()
            elif op == "step_out":
                st_res = dbg.step_out()
            elif op == "reverse_step":
                cur_step = int(getattr(dbg, "step_count", 0) or 0)
                if cur_step <= 0:
                    st_res = dbg.state()
                else:
                    st_res = dbg.goto(max(0, cur_step - 1))
            elif op == "goto":
                st_res = dbg.goto(max(0, int(entry.get("step", entry.get("n", 0)))))
            elif op == "pause":
                dbg.pause()
                st_res = dbg.state()
            elif op == "stop":
                dbg.stop()
                st_res = dbg.state()
            elif op == "cont":
                st_res = dbg.cont()
            elif op == "set_bp":
                dbg.add_breakpoint(entry.get("cls"), entry.get("name"), entry.get("sig", ""), entry.get("pc", 0))
                st_res = {"bp_added": entry.get("pc", 0)}
            elif op == "clear_bps":
                dbg.clear_breakpoints()
                st_res = {"bps_cleared": True}
            elif op == "add_action_point":
                dbg.add_action_point(
                    entry.get("cls"), entry.get("name"), entry.get("sig", ""), entry.get("pc", 0),
                    entry.get("assigns") or [], cond=entry.get("cond"),
                    when_continue=bool(entry.get("when_continue")),
                    one_shot=bool(entry.get("one_shot")), hit=entry.get("hit"),
                )
                st_res = {"ap_added": True}
            elif op == "clear_action_points":
                dbg.clear_action_points()
                st_res = {"aps_cleared": True}
            elif op == "set_net_condition" and stubs is not None and hasattr(stubs, "set_net_condition"):
                nc = stubs.set_net_condition({"mode": entry.get("mode", "online"),
                                              "latency_ms": entry.get("latency_ms", 0),
                                              "kbps": entry.get("kbps", 0)})
                _LAST_GLASS["net_condition"] = nc
                st_res = nc.to_dict() if nc else {}
            elif op == "add_net_mock" and stubs is not None:
                from core.java.net_recorder import ReplayResponder, NetResponse
                rr = getattr(stubs, "net_responder", None)
                if not isinstance(rr, ReplayResponder):
                    rr = ReplayResponder()
                    stubs.net_responder = rr
                key = str(entry.get("url") or "")
                if entry.get("method"):
                    key = f"{str(entry['method']).upper()} {key}"
                body_b = str(entry.get("body") or "").encode("utf-8", "replace")
                rr.mapping[key] = NetResponse(int(entry.get("code") or 200), {}, body_b, "OK")
                st_res = {"mock_added": key}
            elif op == "clear_net_mocks" and stubs is not None:
                stubs.net_responder = None
                st_res = {"mocks_cleared": True}
            elif op == "set_options":
                if "record_timeline" in entry and entry["record_timeline"] is not None:
                    dbg.record_timeline = bool(entry["record_timeline"])
                if "record_regs" in entry and entry["record_regs"] is not None:
                    dbg.record_regs = bool(entry["record_regs"])
                if "stop_on_exception" in entry and entry["stop_on_exception"] is not None:
                    dbg.stop_on_exception = bool(entry["stop_on_exception"])
                if "persist_session" in entry and entry["persist_session"] is not None:
                    _LAST_GLASS["persist_session"] = bool(entry["persist_session"])
                st_res = {"options_updated": True}
            else:
                st_res = {"hinweis": f"Headless-Kommando {op!r} nicht direkt ausfuehrbar."}
            _persist_ui_snapshot()
            _sync_headless_glass_outputs(ctx)
            out["headless"] = st_res

    if tgt in ("gui", "both"):
        qlen = _queue_gui_cmd(ctx, entry)
        out["queued"] = entry
        out["queue_len"] = qlen

    return out


def native_glass_fork(ctx, op="list", package=None, fork_id=None, label="", project_name=None):
    """Verwaltet parallele Glass-Instanzen (Fork/Merge/Discard) & Projekt-Snapshots."""
    from core.java.glass_runner import InstanceState, ProjectSnapshotService
    svc = ProjectSnapshotService(base_dir=_REPO)
    act = str(op or "list").strip().lower()
    if act == "list":
        return {"ok": True, "forks": svc.list_forks(package), "projects": svc.list_projects(package)}
    if act == "fork":
        base = InstanceState(package=package or getattr(ctx.cfg, "config", {}).get("APP_PACKAGE", "default.app"))
        inst = svc.create_fork(base, label=label or "")
        return {"ok": True, "fork": inst.to_dict()}
    if act == "merge" and fork_id:
        return svc.merge_fork(fork_id)
    if act == "discard" and fork_id:
        return {"ok": svc.discard_fork(fork_id), "fork_id": fork_id}
    if act == "save_project" and project_name:
        base = InstanceState(package=package or getattr(ctx.cfg, "config", {}).get("APP_PACKAGE", "default.app"))
        p = svc.save_project(project_name, instance=base)
        return {"ok": True, "path": p}
    if act == "load_project" and project_name:
        pkg = package or getattr(ctx.cfg, "config", {}).get("APP_PACKAGE", "default.app")
        res = svc.load_project(pkg, project_name)
        return {"ok": True, **{k: v for k, v in res.items() if k != "instance"}, "instance": res["instance"].to_dict()}
    return {"ok": False, "hinweis": f"Unbekannte/unvollstaendige Operation op={op!r}"}


def _load_disk_ui_snapshot():
    import json
    p = os.path.join(_GLASS_SESSION_FOLDER, "last_ui.json")
    if not os.path.isfile(p):
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _select_active_ui_snapshot(source="auto"):
    """Waehlt den aktuellsten UI-Snapshot (laufende Desktop-GUI `last_ui.json` vs. In-Memory Surface)."""
    from core.java.ui_mcp_bridge import build_ui_snapshot_dict
    src_mode = str(source or "auto").strip().lower()
    surf = _LAST_UI.get("surface")
    disk_snap = None
    try:
        disk_snap = _load_disk_ui_snapshot()
    except Exception:
        disk_snap = None

    if src_mode == "gui":
        return disk_snap
    if src_mode == "headless":
        if surf is not None:
            snap = build_ui_snapshot_dict(surf, source="headless_live")
            _LAST_UI["_surf_id"] = id(surf)
            _LAST_UI["updated_epoch_ms"] = snap["updated_epoch_ms"]
            return snap
        return disk_snap

    if surf is not None:
        surf_changed_ext = id(surf) != _LAST_UI.get("_surf_id")
        if disk_snap is None or surf_changed_ext or int(_LAST_UI.get("updated_epoch_ms", 0) or 0) >= int(disk_snap.get("updated_epoch_ms", 0) or 0):
            snap = build_ui_snapshot_dict(surf, source="headless_live")
            _LAST_UI["_surf_id"] = id(surf)
            _LAST_UI["updated_epoch_ms"] = snap["updated_epoch_ms"]
            return snap
    return disk_snap


def native_ui_snapshot(ctx):
    """Read-only (gläsern): UI-Momentaufnahme des letzten GUI-/Glass-Stands — Paint-Liste, Masse,
    sichtbare Texte, Szenegraph-Knoten und interaktive Tap-Koordinaten."""
    try:
        data = _select_active_ui_snapshot(source="auto")
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}
    if data is None:
        return {"ok": True, "ui": None, "hinweis": "noch kein native.glass_run oder GUI-Screen aktiv"}
    return {
        "ok": True,
        "source": data.get("source"),
        "package": data.get("package"),
        "updated_at": data.get("updated_at"),
        "width": data.get("width"),
        "height": data.get("height"),
        "scroll_y": data.get("scroll_y", 0),
        "max_scroll": data.get("max_scroll", 0),
        "dark_mode": bool(data.get("dark_mode", False)),
        "status_clock": data.get("status_clock", "12:00"),
        "toast_text": data.get("toast_text"),
        "text": data.get("text", []),
        "nodes": data.get("nodes", []),
        "interactive_elements": data.get("interactive_elements", []),
        "paint_count": len(data.get("paint", [])),
    }


def _persist_ui_snapshot():
    """Persistiert die aktuelle In-memory-Surface nach last_ui.json (nach tap/key-Aenderung)."""
    surf = _LAST_UI.get("surface")
    if surf is None:
        return None
    try:
        from core.java.ui_mcp_bridge import build_ui_snapshot_dict, persist_live_ui_snapshot
        snap = build_ui_snapshot_dict(surf, source="headless_live")
        _LAST_UI["_surf_id"] = id(surf)
        _LAST_UI["updated_epoch_ms"] = snap["updated_epoch_ms"]
        persist_live_ui_snapshot(snap, out_dir=_GLASS_SESSION_FOLDER)
        return snap
    except Exception:
        return None


def native_ui_view(ctx, annotate=False, scroll_y=None, source="auto"):
    """Gläsern/multimodal: rendert die aktuelle simulierte App-GUI als echtes PNG (`png_bytes`)
    fuer MCP-ImageContent samt sichtbaren Texten und nummerierten Tap-Koordinaten."""
    try:
        from tools.surface_raster import render_snapshot_to_png_bytes
    except Exception as e:
        return {"ok": False, "png_bytes": None, "meta": {"ok": False, "hinweis": f"Rasterizer nicht verfuegbar: {e!r}"}}

    try:
        snap = _select_active_ui_snapshot(source=source)
    except Exception as e:
        return {"ok": False, "png_bytes": None, "meta": {"ok": False, "hinweis": repr(e)}}

    if snap is None:
        return {
            "ok": True,
            "png_bytes": None,
            "meta": {
                "ok": True,
                "ui": None,
                "hinweis": "Noch keine simulierte App-GUI aktiv (weder in der Desktop-GUI noch per native_glass_run).",
            },
        }

    out_png = os.path.join(_GLASS_SESSION_FOLDER, "last_ui.png")
    try:
        png_bytes, img_w, img_h = render_snapshot_to_png_bytes(
            snap,
            scroll_y=scroll_y,
            annotate=bool(annotate),
            save_path=out_png,
        )
    except Exception as e:
        return {"ok": False, "png_bytes": None, "meta": {"ok": False, "hinweis": repr(e)}}

    eff_sy = int(snap.get("scroll_y", 0) or 0) if scroll_y is None else max(0, int(scroll_y))
    meta = {
        "ok": True,
        "source": snap.get("source"),
        "package": snap.get("package"),
        "updated_at": snap.get("updated_at"),
        "screen_width": snap.get("width"),
        "screen_height": snap.get("height"),
        "image_width": img_w,
        "image_height": img_h,
        "scroll_y": eff_sy,
        "max_scroll": snap.get("max_scroll", 0),
        "dark_mode": bool(snap.get("dark_mode", False)),
        "status_clock": snap.get("status_clock", "12:00"),
        "toast_text": snap.get("toast_text"),
        "annotate": bool(annotate),
        "png_path": out_png,
        "bytes": len(png_bytes),
        "visible_texts": snap.get("text", [])[:60],
        "interactive_elements": snap.get("interactive_elements", []),
        "node_count": len(snap.get("nodes", [])),
        "paint_count": len(snap.get("paint", [])),
    }
    return {"ok": True, "png_bytes": png_bytes, "path": out_png, "meta": meta}


def native_ui_detect(ctx, studio_root=None):
    """Gläsern/read-only: erkennt das UI-Toolkit einer entpackten App (klassisch/Compose/RN/Hybrid Web/NativeScript/Flutter)
    und nennt den passenden Render-Produzenten. Rueckgabe: {ok, runtime, producer, has_producer}."""
    try:
        from core.java.producer_wiring import ensure_default_producers
        from core.java.surface import producers
        ensure_default_producers(producers)
        from core.java.runtime_detect import producer_for_app
    except Exception as e:
        return {"ok": False, "hinweis": f"Erkennung nicht verfuegbar: {e!r}"}
    root = studio_root or (ctx.get("studio_root") if isinstance(ctx, dict) else None)
    if not root:
        root = _resolve_app_root_for_extract(ctx, None)
    try:
        key, prod = producer_for_app(app_dir=root)
        return {"ok": True, "runtime": key, "producer": key if prod is not None else None,
                "has_producer": prod is not None,
                "hinweis": "klassisch laeuft direkt ueber den ViewTree/Debugger" if prod is None else None}
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}


def native_webview_inspect(ctx, url="", app_root=None, forward_to_gui=False):
    """In-Phone-Browser / WebView-DOM-Inspektor: entdeckt App-URLs (url='') oder ruft eine URL/HTML-Asset-Datei ab und parst DOM, Links, Formulare & Skripte."""
    from services.webview_inspector_service import WebViewInspectorService, discover_app_urls
    root = _resolve_app_root_for_extract(ctx, app_root)
    web_events = getattr(_LAST_GLASS.get("tree"), "web_events", None)
    u = str(url or "").strip()
    if not u:
        discovered = discover_app_urls(root, web_events=web_events) if root else []
        if root:
            try:
                from core.java.hybrid_web_producer import analyze_hybrid_web_app
                hw = analyze_hybrid_web_app(root)
                seen_u = {it["url"] for it in discovered if isinstance(it, dict)}
                for pg in (hw.get("pages") or ()):
                    ap = pg.get("abs_path") or ""
                    if ap and ap not in seen_u:
                        discovered.append({"url": ap, "source": f"HybridPage ({pg.get('rel_path', '')})", "kind": "hybrid_page", "label": pg.get("title") or pg.get("rel_path") or ""})
                        seen_u.add(ap)
            except Exception:
                pass
        return {"ok": True, "app_root": root, "count": len(discovered), "discovered_urls": discovered}

    target = u
    if root and not target.startswith(("http://", "https://", "file:///")) and not os.path.isfile(target):
        cand = os.path.join(root, target.lstrip("/\\"))
        if os.path.isfile(cand):
            target = cand

    svc = WebViewInspectorService(base_dir=_base_dir(ctx))
    res = svc.fetch_and_inspect(target)
    if forward_to_gui:
        _queue_gui_cmd(ctx, {"cmd": "open_browser", "url": target})
    if not res.get("ok"):
        return res
    return {
        "ok": True,
        "url": res.get("url"),
        "final_url": res.get("final_url"),
        "method": res.get("method"),
        "status": res.get("status"),
        "reason": res.get("reason"),
        "elapsed_ms": res.get("elapsed_ms"),
        "title": res.get("title"),
        "links": (res.get("links") or [])[:40],
        "forms": (res.get("forms") or [])[:20],
        "scripts": (res.get("scripts") or [])[:30],
        "text_blocks": (res.get("text_blocks") or [])[:50],
        "discovered_apis": res.get("api_urls") or [],
        "resp_headers": res.get("resp_headers") or {},
        "queued_to_gui": bool(forward_to_gui),
    }


def native_ui_render(ctx, path=None, title=None):
    """Gläsern: rendert die zuletzt gläserne UI zu PNG (am Ende kommt echte Grafik raus). Nutzt die
    lebende In-memory-Surface (reflektiert tap/key), sonst den gespeicherten Snapshot in
    last_ui.json. Rueckgabe: {ok, path, bytes, width, height}."""
    try:
        from tools.surface_raster import render_surface_to_png, render_snapshot_to_png_bytes
    except Exception as e:
        return {"ok": False, "hinweis": f"Rasterizer nicht verfuegbar: {e!r}"}
    out = path or os.path.join(_GLASS_SESSION_FOLDER, "last_ui.png")
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    surf = _LAST_UI.get("surface")
    w = h = None
    try:
        if surf is not None:
            render_surface_to_png(surf, out, title=title or "glass UI")
            from core.java.surface import snapshot as _ui_snapshot
            sn = _ui_snapshot(surf)
            w, h = sn.get("width"), sn.get("height")
        else:
            p2 = os.path.join(_GLASS_SESSION_FOLDER, "last_ui.json")
            if not os.path.isfile(p2):
                return {"ok": True, "path": None, "hinweis": "noch kein native.glass_run gelaufen"}
            with open(p2, encoding="utf-8") as f:
                data = json.load(f)
            _, w_img, h_img = render_snapshot_to_png_bytes(data, title=title, save_path=out)
            w = data.get("width") or w_img
            h = data.get("height") or h_img
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}
    try:
        sz = os.path.getsize(out)
    except Exception:
        sz = None
    return {"ok": True, "path": out, "bytes": sz, "width": w, "height": h}


def native_ui_tap(ctx, x, y, forward_to_gui=False):
    """Gläsern + manipulierbar: Tap (x,y) auf die zuletzt gläserne UI zurueckspielen oder
    (mit forward_to_gui=True) in die laufende Desktop-GUI einreihen. Rueckgabe: {ok, handled}."""
    surf = _LAST_UI.get("surface")
    if surf is None:
        if forward_to_gui:
            qres = native_glass_control(ctx, "ui_tap", json.dumps({"x": x, "y": y}))
            return {"ok": bool(qres.get("ok")), "handled": bool(qres.get("ok")), "queued_to_gui": True, "x": x, "y": y}
        return {"ok": True, "handled": False, "hinweis": "noch kein native.glass_run gelaufen"}
    try:
        client = getattr(surf, "_hermes_client", None)
        if client is not None:
            # Echter Hermes-Pfad: Tap -> Event an Host -> Re-Commit -> neue Surface
            res = client.tap(x, y)
            new_surf = client.surface
            if new_surf is not None:
                try: new_surf._hermes_client = client
                except Exception: pass
                _LAST_UI["surface"] = new_surf
            _persist_ui_snapshot()
            return {"ok": True, "handled": bool(res.get("handled")), "tag": res.get("tag")}
        from core.java.surface import tap as _tap
        res = _tap(surf, x, y)
        _persist_ui_snapshot()
        return {"ok": True, "handled": bool(res.get("handled"))}
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}


def native_ui_key(ctx, text, x=None, y=None, forward_to_gui=False):
    """Gläsern + manipulierbar: Tastentext an die fokussierte Surface geben oder
    (mit forward_to_gui=True) in die laufende Desktop-GUI einreihen. Rueckgabe: {ok, handled}."""
    surf = _LAST_UI.get("surface")
    if surf is None:
        if forward_to_gui:
            payload = {"text": text}
            if x is not None:
                payload["x"] = x
            if y is not None:
                payload["y"] = y
            qres = native_glass_control(ctx, "ui_key", json.dumps(payload))
            return {"ok": bool(qres.get("ok")), "handled": bool(qres.get("ok")), "queued_to_gui": True}
        return {"ok": True, "handled": False, "hinweis": "noch kein native.glass_run gelaufen"}
    try:
        client = getattr(surf, "_hermes_client", None)
        if client is not None:
            # Zielkoordinate: explizit, sonst Mitte des ersten Eingabefeldes (EditText/TextInput)
            tx, ty = x, y
            if tx is None or ty is None:
                for n in surf.tree.nodes():
                    cls = (getattr(n, "cls", "") or "").lower()
                    if "edit" in cls or "input" in cls or getattr(n, "props", {}).get("hint"):
                        b = n.bounds; tx, ty = b[0] + b[2] / 2, b[1] + b[3] / 2; break
            if tx is None or ty is None:
                return {"ok": True, "handled": False, "hinweis": "kein Eingabefeld gefunden"}
            res = client.key(tx, ty, text)
            new_surf = client.surface
            if new_surf is not None:
                try: new_surf._hermes_client = client
                except Exception: pass
                _LAST_UI["surface"] = new_surf
            _persist_ui_snapshot()
            return {"ok": True, "handled": bool(res.get("handled")), "tag": res.get("tag")}
        from core.java.surface import key as _key
        res = _key(surf, text)
        _persist_ui_snapshot()
        return {"ok": True, "handled": bool(res.get("handled"))}
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}


def native_net_log(ctx):
    """Read-only: NetLog (Request/Response-Paare) des letzten native.glass_run lesen."""
    import json
    p = os.path.join(_GLASS_SESSION_FOLDER, "last_net_log.json")
    if not os.path.isfile(p):
        return {"ok": True, "net_log": [], "hinweis": "noch kein native.glass_run gelaufen"}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return {"ok": True, "net_log": data, "count": len(data)}
    except Exception as e:
        return {"ok": False, "hinweis": repr(e)}


# ============================================================ I-Erweiterung: Statischer Flutter/Dart-API-Extractor
def _resolve_app_root_for_extract(ctx, app_root=None):
    if app_root and os.path.isdir(str(app_root)):
        return str(app_root)
    if ctx is not None and getattr(ctx, "cfg", None) is not None:
        _, _, dirs = _unpacked_candidates(ctx)
        if dirs:
            return dirs[0]
        src = ctx.cfg.paths.get("APP_SOURCE_DIR", "")
        if src and os.path.isdir(src):
            return src
    return ""


def api_extract_flutter_static(ctx, app_root=None):
    """Liest rein statisch Cognito-/AppSync-GraphQL-/REST-Vorlagen aus flutter_assets & libapp.so (ohne Boot)."""
    from services.flutter_api_extractor_service import FlutterApiExtractorService
    root = _resolve_app_root_for_extract(ctx, app_root)
    if not root:
        return {"ok": False, "error": "Kein entpackter App-Root gefunden (app_root angeben oder APK entpacken)."}
    extra = []
    if ctx is not None and getattr(ctx, "cfg", None) is not None:
        pkg = (ctx.cfg.config.get("APP_PACKAGE") or "").strip()
        if pkg:
            safe_p = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in pkg).strip("._") or "default.app"
            sc = os.path.join(_REPO, "data", "vfs_selfcache", safe_p)
            if os.path.isdir(sc):
                extra.append(sc)
    return FlutterApiExtractorService.extract_from_app_root(root, extra_lib_dirs=extra)


def api_extract_engine_static(ctx, app_root=None, engine="auto"):
    """Extrahiert rein statisch API-/Web-Endpunkte fuer Hybrid-Web, React Native/Hermes, Flutter & Web-Assets."""
    from core.java.runtime_detect import detect_runtime
    from services.flutter_api_extractor_service import FlutterApiExtractorService
    from services.webview_inspector_service import discover_app_urls

    root = _resolve_app_root_for_extract(ctx, app_root)
    if not root:
        return {"ok": False, "error": "Kein entpackter App-Root gefunden (app_root angeben oder APK entpacken)."}

    det_rt = detect_runtime(app_dir=root)
    req_eng = str(engine or "auto").strip().lower()
    templates = []
    seen = set()

    def _add_tpl(method, url, category, name="", source="", headers=None, body=""):
        u = str(url or "").strip()
        if not u:
            return
        m = str(method or "GET").upper()
        key = (m, u)
        if key in seen:
            return
        seen.add(key)
        templates.append({
            "id": f"tpl_{len(templates) + 1}",
            "name": name or f"{m} {u[:60]}",
            "category": category,
            "method": m,
            "url": u,
            "headers": dict(headers or {}),
            "body": body or "",
            "source": source or category,
        })

    if req_eng in ("auto", "flutter") or det_rt == "flutter":
        fl_res = api_extract_flutter_static(ctx, app_root=root)
        for t in (fl_res.get("templates") or ()):
            if isinstance(t, dict) and t.get("url"):
                _add_tpl(t.get("method", "GET"), t["url"], t.get("category", "flutter"),
                         name=t.get("name", ""), source=t.get("source", "flutter"),
                         headers=t.get("headers"), body=t.get("body", ""))

    if req_eng in ("auto", "hybrid web", "hybrid_web") or det_rt == "hybrid web":
        try:
            from core.java.hybrid_web_producer import analyze_hybrid_web_app
            hw = analyze_hybrid_web_app(root)
            for ep in (hw.get("api_endpoints") or ()):
                if isinstance(ep, dict) and ep.get("url"):
                    _add_tpl(ep.get("method", "GET"), ep["url"], ep.get("kind", "hybrid_web"),
                             name=ep.get("label") or ep["url"], source="hybrid_web")
            for u in (hw.get("urls") or ()):
                _add_tpl("GET", u, "hybrid_url", name=u, source="hybrid_web")
        except Exception:
            pass

    if req_eng in ("auto", "react native (js)", "react_native", "rn") or det_rt == "react native (js)":
        try:
            from core.java.rn_app_producer import extract_rn_app_spec
            rn = extract_rn_app_spec(root)
            for u in (rn.get("endpoints") or ()):
                _add_tpl("GET", u, "react_native", name=u, source=f"rn:{rn.get('bundle_kind', 'js')}")
        except Exception:
            pass

    try:
        for it in discover_app_urls(root):
            u = str((it or {}).get("url") or "").strip()
            if u.startswith(("http://", "https://")):
                _add_tpl("GET", u, it.get("kind", "web_asset"),
                         name=it.get("label") or it.get("source") or u,
                         source=it.get("source", "discover_app_urls"))
    except Exception:
        pass

    return {
        "ok": True,
        "app_root": root,
        "runtime": det_rt,
        "engine": req_eng,
        "count": len(templates),
        "templates": templates,
    }


def api_import_templates(ctx, templates_json, app_root=None):
    """Uebernimmt ausgewaehlte statische API-Vorlagen in die API-Inspector-SQLite-DB (api_traffic.db)."""
    from services.flutter_api_extractor_service import FlutterApiExtractorService
    try:
        items = json.loads(templates_json) if isinstance(templates_json, str) else templates_json
    except Exception as e:
        return {"ok": False, "error": f"Ungueltiges JSON: {e}"}
    # Erlaubt Liste kompletter Vorlage-Dicts ODER Liste von Vorlage-IDs (z.B. ["tpl_1","tpl_2"])
    if isinstance(items, list) and items and all(isinstance(x, str) for x in items):
        scan = api_extract_engine_static(ctx, app_root=app_root)
        by_id = {t["id"]: t for t in (scan.get("templates") or ()) if isinstance(t, dict)}
        resolved = [by_id[tid] for tid in items if tid in by_id]
        if len(resolved) < len(items):
            fl_scan = api_extract_flutter_static(ctx, app_root=app_root)
            for t in (fl_scan.get("templates") or ()):
                if isinstance(t, dict) and t.get("id") in items and t.get("id") not in by_id:
                    resolved.append(t)
    elif isinstance(items, list):
        resolved = [x for x in items if isinstance(x, dict)]
    elif isinstance(items, dict):
        resolved = [items]
    else:
        return {"ok": False, "error": "templates_json muss Array von IDs oder Vorlage-Objekten sein"}

    db_path = ""
    if ctx is not None and getattr(ctx, "cfg", None) is not None:
        db_path = ctx.cfg.paths.get("API_DB", "")
    if not db_path:
        db_path = os.path.join(_base_dir(ctx), "api_traffic.db")
    n = FlutterApiExtractorService.import_templates_to_db(db_path, resolved)
    return {"ok": True, "imported": n, "api_db": db_path}


# ============================================================ N: LibCarve (Decompile & Patch Studio)
def _lc_pkg_safe(ctx) -> str:
    pkg = (ctx.cfg.config.get("APP_PACKAGE") if (ctx and getattr(ctx, "cfg", None)) else "") or "default.app"
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in str(pkg).strip()).strip("._") or "default.app"


def _lc_discover(ctx) -> list:
    libs = []
    seen = set()
    if ctx is not None and getattr(ctx, "cfg", None) is not None:
        src_dir = ctx.cfg.paths.get("APP_SOURCE_DIR", "")
        if src_dir and os.path.isdir(src_dir):
            for unp in ("base_unpacked_apkeditor", "base_unpacked_apktool"):
                lroot = os.path.join(src_dir, unp, "lib")
                if not os.path.isdir(lroot):
                    continue
                for abi in sorted(os.listdir(lroot)):
                    ad = os.path.join(lroot, abi)
                    if not os.path.isdir(ad):
                        continue
                    for fn in sorted(os.listdir(ad)):
                        if fn.endswith(".so"):
                            fp = os.path.join(ad, fn)
                            if fp not in seen:
                                seen.add(fp)
                                libs.append({"name": fn, "abi": abi, "source": "apk", "path": fp})
    sc_dir = os.path.join(_REPO, "data", "vfs_selfcache", _lc_pkg_safe(ctx))
    if os.path.isdir(sc_dir):
        for fn in sorted(os.listdir(sc_dir)):
            if fn.endswith(".so"):
                fp = os.path.join(sc_dir, fn)
                if fp not in seen:
                    seen.add(fp)
                    libs.append({"name": fn, "abi": "arm64-v8a", "source": "self_cache", "path": fp})
    return libs


def _lc_resolve_so_and_index(ctx, path_or_name: str, need_index: bool = True):
    from core.native.loader import ElfImage
    from core.native.nindex import NativeIndex

    p = str(path_or_name or "").strip()
    if p and os.path.isfile(p):
        so_path = os.path.abspath(p)
    else:
        bn = os.path.basename(p)
        cands = _lc_discover(ctx)
        hit = next((c for c in cands if c["name"] == bn or c["path"] == p), None)
        so_path = hit["path"] if hit else ""
    if not so_path or not os.path.isfile(so_path):
        return None, None, None
    img = ElfImage.from_file(so_path)
    if not need_index:
        return so_path, img, None
    cache_dir = os.path.join(_REPO, "data", "native", "_libcarve")
    idx = NativeIndex().build(img, cache_dir=cache_dir, fast=True)
    return so_path, img, idx


def _lc_resolve_addr(img, idx, func_or_addr) -> Optional[int]:
    s = str(func_or_addr or "").strip()
    if not s:
        return None
    if s.startswith("0x") or s.isdigit():
        try:
            return int(s, 0)
        except Exception:
            pass
    if s in img.dynsyms:
        return int(img.dynsyms[s].value)
    if s.startswith("sub_"):
        try:
            return int(s[4:], 16)
        except Exception:
            pass
    if idx is not None:
        low = s.lower()
        for a, nm in idx.syms:
            if nm.lower() == low:
                return int(a)
        for a, nm in idx.syms:
            if low in nm.lower():
                return int(a)
    return None


def libcarve_libs(ctx):
    from core.native.loader import ElfImage
    out = []
    for item in _lc_discover(ctx):
        entry = dict(item)
        try:
            entry["elf"] = ElfImage.from_file(item["path"]).elf_summary()
        except Exception as e:
            entry["elf_error"] = repr(e)
        out.append(entry)
    return {"ok": True, "count": len(out), "libs": out}


def libcarve_outline(ctx, path, filter="", limit=500):
    so_p, img, idx = _lc_resolve_so_and_index(ctx, path, need_index=True)
    if not so_p:
        return {"ok": False, "error": f"Bibliothek nicht gefunden: {path}"}
    flt = (filter or "").strip().lower()
    rows = []
    for a, nm, kind in idx.func_catalog:
        if flt and flt not in nm.lower() and flt not in f"0x{a:x}":
            continue
        rows.append({"kind": kind, "name": nm, "vaddr": f"0x{a:x}"})
        if len(rows) >= int(limit):
            break
    return {
        "ok": True, "path": so_p, "total_funcs": len(idx.func_catalog),
        "imports_count": len(idx.imports), "imports_preview": idx.imports[:80],
        "functions": rows, "elf": img.elf_summary(),
    }


def libcarve_search(ctx, path, term, mode="func", limit=200):
    so_p, img, idx = _lc_resolve_so_and_index(ctx, path, need_index=True)
    if not so_p:
        return {"ok": False, "error": f"Bibliothek nicht gefunden: {path}"}
    m = (mode or "func").strip().lower()
    t = str(term or "").strip()
    lim = int(limit)
    hits = []
    if m == "addr":
        a = int(t, 0)
        hits = [{"vaddr": f"0x{a:x}", "func": idx.func_at(a) or f"0x{a:x}"}]
    elif m == "func":
        low = t.lower()
        for a, nm in idx.syms:
            if low in nm.lower():
                hits.append({"vaddr": f"0x{a:x}", "name": nm})
                if len(hits) >= lim:
                    break
    elif m == "str":
        low = t.lower()
        for a, s in idx.extract_strings(img):
            if low in s.lower():
                hits.append({"vaddr": f"0x{a:x}", "text": s[:200]})
                if len(hits) >= lim:
                    break
    elif m == "asm":
        for src, mn, tgt in idx.search(t.lower(), limit=lim):
            hits.append({"vaddr": f"0x{src:x}", "func": idx.func_at(src) or "?", "mnem": mn, "target": f"0x{tgt:x}"})
    elif m == "aob":
        for a in idx.search_aob(img, t, exec_only=False, limit=lim):
            hits.append({"vaddr": f"0x{a:x}", "func": idx.func_at(a) or f"0x{a:x}"})
    return {"ok": True, "mode": m, "count": len(hits), "hits": hits}


def libcarve_inspect_func(ctx, path, func_or_addr, max_insns=200):
    from core.native.engine_arm64 import Arm64Decoder
    from services.libcarve_pattern_service import LibCarvePatternService
    so_p, img, idx = _lc_resolve_so_and_index(ctx, path, need_index=True)
    if not so_p:
        return {"ok": False, "error": f"Bibliothek nicht gefunden: {path}"}
    addr = _lc_resolve_addr(img, idx, func_or_addr)
    if addr is None:
        return {"ok": False, "error": f"Ziel nicht gefunden: {func_or_addr}"}
    fs, fe = idx.func_bounds(addr)
    fn = idx.func_at(addr) or f"sub_{fs:x}"
    span = min(max(4, fe - fs), int(max_insns) * 4)
    raw = img.read_vaddr(fs, span)
    md = Arm64Decoder()._md
    disasm = [f"0x{a:08x}: {mn} {op}" for a, _sz, mn, op in md.disasm_lite(raw, fs)]
    sig = LibCarvePatternService.generate_signature(img, idx, fs, byte_len=32)

    sha = sha8(so_p)
    gh_c_path = os.path.join(_REPO, "data", "native", "_libcarve", "ghidra", sha, f"func_{fs:x}.c")
    ghidra_c = open(gh_c_path, "r", encoding="utf-8", errors="replace").read() if os.path.isfile(gh_c_path) else None
    anno_p = os.path.join(_REPO, "data", "libcarve", _lc_pkg_safe(ctx), "annotations.json")
    anno = NativeAnnotationsService(anno_p).get(sha, f"0x{fs:x}") if os.path.isfile(anno_p) else None

    return {
        "ok": True,
        "path": so_p,
        "func": fn,
        "vaddr": f"0x{fs:x}",
        "end": f"0x{fe:x}",
        "orig_size": max(0, fe - fs),
        "disasm": disasm,
        "ghidra_c": ghidra_c,
        "signature": sig,
        "annotation": anno,
    }


def libcarve_decompile(ctx, path, func_or_addr, run=False):
    from services.ghidra_service import GhidraService
    so_p, img, idx = _lc_resolve_so_and_index(ctx, path, need_index=True)
    if not so_p:
        return {"ok": False, "error": f"Bibliothek nicht gefunden: {path}"}
    addr = _lc_resolve_addr(img, idx, func_or_addr)
    if addr is None:
        return {"ok": False, "error": f"Funktion nicht gefunden: {func_or_addr}"}
    fs, _fe = idx.func_bounds(addr)
    ws_dir = os.path.join(_REPO, "data", "native", "_libcarve")
    sha = sha8(so_p)
    out_c = os.path.join(ws_dir, "ghidra", sha, f"func_{fs:x}.c")
    if os.path.isfile(out_c):
        return {"ok": True, "cached": True, "vaddr": f"0x{fs:x}", "c": open(out_c, "r", encoding="utf-8", errors="replace").read()}
    if not run:
        return {"ok": True, "cached": False, "vaddr": f"0x{fs:x}", "hinweis": "Noch nicht gecacht — mit run=true Ghidra-Lauf im Hintergrund starten."}
    cfg = ctx.cfg if (ctx and getattr(ctx, "cfg", None)) else None
    svc = GhidraService(cfg)
    if not svc.available():
        return {"ok": False, "why": "no_ghidra"}
    threading.Thread(target=lambda: svc.decompile(so_p, fs, ws_dir), daemon=True).start()
    return {"ok": True, "started": True, "vaddr": f"0x{fs:x}", "hinweis": "Ghidra im Hintergrund gestartet — gleich erneut ohne run abfragen."}


def libcarve_xrefs(ctx, path, func_or_addr, limit=200):
    so_p, img, idx = _lc_resolve_so_and_index(ctx, path, need_index=True)
    if not so_p:
        return {"ok": False, "error": f"Bibliothek nicht gefunden: {path}"}
    addr = _lc_resolve_addr(img, idx, func_or_addr)
    if addr is None:
        return {"ok": False, "error": f"Adresse nicht gefunden: {func_or_addr}"}
    refs = [{"site": f"0x{s:x}", "mnem": m, "caller": idx.func_at(s) or f"0x{s:x}"} for s, m in idx.xrefs_to(addr, limit=int(limit))]
    return {"ok": True, "target": f"0x{addr:x}", "count": len(refs), "xrefs": refs}


def libcarve_callgraph(ctx, path, func_or_addr, depth=2):
    so_p, img, idx = _lc_resolve_so_and_index(ctx, path, need_index=True)
    if not so_p:
        return {"ok": False, "error": f"Bibliothek nicht gefunden: {path}"}
    addr = _lc_resolve_addr(img, idx, func_or_addr)
    if addr is None:
        return {"ok": False, "error": f"Adresse nicht gefunden: {func_or_addr}"}
    fs, _fe = idx.func_bounds(addr)
    edges = []
    seen = set()
    def _walk(a, d):
        if d <= 0 or a in seen:
            return
        seen.add(a)
        s0, e0 = idx.func_bounds(a)
        fn0 = idx.func_at(s0) or f"sub_{s0:x}"
        for src, mn, tgt, cname in idx.outgoing_from(s0, e0, limit=80):
            edges.append({"from": fn0, "from_addr": f"0x{s0:x}", "site": f"0x{src:x}", "mnem": mn, "to": cname, "to_addr": f"0x{tgt:x}"})
            if d > 1:
                _walk(tgt, d - 1)
    _walk(fs, max(1, min(4, int(depth))))
    return {"ok": True, "root": f"0x{fs:x}", "edge_count": len(edges), "edges": edges}


def libcarve_datagraph(ctx, path, func_or_addr, limit=200):
    so_p, img, idx = _lc_resolve_so_and_index(ctx, path, need_index=True)
    if not so_p:
        return {"ok": False, "error": f"Bibliothek nicht gefunden: {path}"}
    addr = _lc_resolve_addr(img, idx, func_or_addr)
    if addr is None:
        return {"ok": False, "error": f"Adresse nicht gefunden: {func_or_addr}"}
    fs, fe = idx.func_bounds(addr)
    refs = idx.datagraph_for(img, fs, fe, limit=int(limit))
    for r in refs:
        r["site"] = f"0x{r['site']:x}"
        r["target"] = f"0x{r['target']:x}"
    return {"ok": True, "func": idx.func_at(fs) or f"sub_{fs:x}", "count": len(refs), "refs": refs}


def libcarve_match_pattern(ctx, path, patch_json):
    from services.libcarve_pattern_service import LibCarvePatternService
    so_p, img, idx = _lc_resolve_so_and_index(ctx, path, need_index=True)
    if not so_p:
        return {"ok": False, "error": f"Bibliothek nicht gefunden: {path}"}
    patch = json.loads(patch_json) if isinstance(patch_json, str) else dict(patch_json)
    res = LibCarvePatternService.match_patch(img, idx, patch)
    if res.get("vaddr") is not None:
        res["vaddr"] = f"0x{res['vaddr']:x}"
    for c in res.get("candidates") or ():
        if isinstance(c.get("vaddr"), int):
            c["vaddr"] = f"0x{c['vaddr']:x}"
    return res


def libcarve_session_get(ctx):
    from services.ki_workspace_sync_service import KiWorkspaceSyncService
    return KiWorkspaceSyncService(_REPO).read_workspace_sync()


def libcarve_assemble_preview(ctx, code, mode="asm", vaddr=0, orig_size=0, func_name="carve_entry"):
    from services.libcarve_compiler_service import LibCarveCompilerService
    cfg = (ctx.cfg.config if (ctx and getattr(ctx, "cfg", None)) else {}) or {}
    va = int(vaddr, 0) if isinstance(vaddr, str) and vaddr else int(vaddr or 0)
    m = str(mode or "asm").lower()
    if m == "c_cave":
        res = LibCarveCompilerService.compile_c_cave(code, cave_vaddr=(va or 0x100000), func_name=func_name, cfg=cfg)
    else:
        res = LibCarveCompilerService.assemble_arm64(code, vaddr=va, orig_size=int(orig_size or 0), auto_nop=True, cfg=cfg)
    out = dict(res)
    out.pop("bytes", None)  # hex ist bereits JSON-freundlich enthalten
    return out


def libcarve_patch_list(ctx):
    out = []
    for idx, p in enumerate(SessionState.active_libcarve_patches):
        item = dict(p)
        item["index"] = idx
        out.append(item)
    return {"ok": True, "count": len(out), "patches": out}


def libcarve_patch_add(ctx, file, vaddr_or_func, code, mode="asm", description="", abi="arm64-v8a", orig_size=0, aob=""):
    from services.libcarve_compiler_service import LibCarveCompilerService
    from services.libcarve_pattern_service import LibCarvePatternService

    cfg = (ctx.cfg.config if (ctx and getattr(ctx, "cfg", None)) else {}) or {}
    so_p, img, idx = _lc_resolve_so_and_index(ctx, file, need_index=True)
    va = None
    fn = str(vaddr_or_func or "").strip()
    if img is not None and idx is not None:
        va = _lc_resolve_addr(img, idx, vaddr_or_func)
        if va is not None:
            fs, fe = idx.func_bounds(va)
            if not int(orig_size or 0):
                orig_size = max(0, fe - fs)
            fn = idx.func_at(va) or fn
            if not aob:
                sig = LibCarvePatternService.generate_signature(img, idx, va, byte_len=32)
                aob = sig.get("aob", "")
    elif fn.startswith("0x") or fn.isdigit():
        va = int(fn, 0)

    if va is None:
        return {"ok": False, "error": f"Zieladresse '{vaddr_or_func}' in '{file}' nicht aufloesbar."}

    m = str(mode or "asm").lower()
    if m == "asm":
        chk = LibCarveCompilerService.assemble_arm64(code, vaddr=va, orig_size=int(orig_size or 0), auto_nop=True, cfg=cfg)
        if not chk.get("ok"):
            return {"ok": False, "error": chk.get("error", "ASM-Compile-Fehler")}

    patch = {
        "type": "libcarve",
        "file": os.path.basename(file),
        "abi": abi or "arm64-v8a",
        "vaddr": f"0x{va:x}",
        "func": fn,
        "orig_size": int(orig_size or 0),
        "mode": m,
        "auto_nop": True,
        "orig": "",
        "edit": code,
        "orig_hex": img.read_vaddr(va, 32).hex(" ") if img is not None else "",
        "aob": aob,
        "description": description or f"LibCarve {os.path.basename(file)}@{f'0x{va:x}'}",
    }
    SessionState.active_libcarve_patches.append(patch)
    persisted = _persist_active(ctx) if ctx is not None else False
    return {"ok": True, "index": len(SessionState.active_libcarve_patches) - 1, "patch": patch, "persisted": persisted}


def libcarve_patch_update(ctx, index, code="", mode="", description=""):
    i = int(index)
    if not (0 <= i < len(SessionState.active_libcarve_patches)):
        return {"ok": False, "error": f"Ungueltiger LibCarve-Patch-Index: {i}"}
    p = SessionState.active_libcarve_patches[i]
    if code:
        p["edit"] = code
    if mode:
        p["mode"] = str(mode).lower()
    if description:
        p["description"] = description
    persisted = _persist_active(ctx) if ctx is not None else False
    return {"ok": True, "index": i, "patch": p, "persisted": persisted}


def libcarve_patch_remove(ctx, index):
    i = int(index)
    if not (0 <= i < len(SessionState.active_libcarve_patches)):
        return {"ok": False, "error": f"Ungueltiger LibCarve-Patch-Index: {i}"}
    rem = SessionState.active_libcarve_patches.pop(i)
    persisted = _persist_active(ctx) if ctx is not None else False
    return {"ok": True, "removed": rem.get("vaddr"), "count": len(SessionState.active_libcarve_patches), "persisted": persisted}


def libcarve_cave_save(ctx, name, code, file="", vaddr=""):
    d = os.path.join(_REPO, "data", "libcarve", _lc_pkg_safe(ctx))
    os.makedirs(d, exist_ok=True)
    cf = os.path.join(d, "_caves.json")
    cur = []
    if os.path.isfile(cf):
        try:
            cur = json.load(open(cf, "r", encoding="utf-8"))
        except Exception:
            cur = []
    entry = {"name": str(name).strip(), "file": str(file), "vaddr": str(vaddr), "code": str(code)}
    cur.append(entry)
    with open(cf, "w", encoding="utf-8") as fh:
        json.dump(cur, fh, indent=2, ensure_ascii=False)
    return {"ok": True, "count": len(cur), "file": cf}


def libcarve_annotate(ctx, path, func_or_addr, name, note=""):
    so_p, img, idx = _lc_resolve_so_and_index(ctx, path, need_index=True)
    if not so_p:
        return {"ok": False, "error": f"Bibliothek nicht gefunden: {path}"}
    addr = _lc_resolve_addr(img, idx, func_or_addr)
    if addr is None:
        return {"ok": False, "error": f"Adresse nicht gefunden: {func_or_addr}"}
    sha = sha8(so_p)
    anno_p = os.path.join(_REPO, "data", "libcarve", _lc_pkg_safe(ctx), "annotations.json")
    NativeAnnotationsService(anno_p).set(sha, f"0x{addr:x}", str(name).strip(), str(note or "").strip())
    return {"ok": True, "sha": sha, "vaddr": f"0x{addr:x}", "name": name}


def libcarve_gui_focus(ctx, lib="", vaddr="", draft_code="", mode="asm"):
    from services.ki_workspace_sync_service import KiWorkspaceSyncService
    staged = {
        "libcarve_draft": {
            "lib": str(lib or ""),
            "vaddr": str(vaddr or ""),
            "code": str(draft_code or ""),
            "mode": str(mode or "asm"),
        }
    }
    sync_res = KiWorkspaceSyncService(_REPO).stage_from_ki(staged)
    q = native_glass_control(ctx, "open_libcarve", json.dumps(staged["libcarve_draft"]))
    return {"ok": True, "staged": sync_res, "gui_cmd": q}


def libcarve_session_set(ctx, staged_json, base_seq=None):
    from services.ki_workspace_sync_service import KiWorkspaceSyncService
    staged = json.loads(staged_json) if isinstance(staged_json, str) else dict(staged_json or {})
    return KiWorkspaceSyncService(_REPO).stage_from_ki(staged, base_seq=base_seq)


def libcarve_compile(ctx, file, abi="arm64-v8a"):
    from services.libcarve_compiler_service import LibCarveCompilerService
    cfg = (ctx.cfg.config if (ctx and getattr(ctx, "cfg", None)) else {}) or {}
    bn = os.path.basename(str(file or "").strip())
    plist = [p for p in SessionState.active_libcarve_patches if os.path.basename(p.get("file", "")) == bn]
    if not plist:
        return {"ok": False, "error": f"Keine aktiven LibCarve-Patches fuer {bn}"}
    so_p, img, _idx = _lc_resolve_so_and_index(ctx, bn, need_index=False)
    if img is None:
        return {"ok": False, "error": f"Basis-.so '{bn}' nicht gefunden"}
    pkg = (ctx.cfg.config.get("APP_PACKAGE") if (ctx and getattr(ctx, "cfg", None)) else "") or "default.app"
    ok, out_path, reports = LibCarveCompilerService.compile_to_workspace_cache(
        _REPO, pkg, abi or "arm64-v8a", bn, img.data, plist, cfg=cfg
    )
    return {"ok": ok, "out_path": out_path, "reports": reports}
