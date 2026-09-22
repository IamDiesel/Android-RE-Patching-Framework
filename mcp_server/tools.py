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
import shutil

from core.infrastructure.command_runner import CommandRunner
from services.patch_service import PatchService
from mcp_server import registry


def _adb(ctx) -> str:
    return ctx.cfg.paths.get("ADB", "adb")


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
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "false"):
            value = (low == "true")
        elif value.strip().lstrip("-").isdigit():
            value = int(value)
    ctx.cfg.config[key] = value
    ctx.cfg.save()
    return f"gesetzt: {key} = {value!r} (in config.json gespeichert)"


def workspace_prepare_hint(ctx):
    return ("PREPARE_WORKSPACE (Bauen -> Nutzer):\n"
            "GUI: 'App Manager' -> App importieren, dann 'Workspace' -> Pipeline "
            "PREPARE_WORKSPACE (Merge Split APKs + Decompile).\n"
            "Danach liegt der Smali-Baum unter source/<APP_PACKAGE>/<unpacked>/smali*/.")


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


# ============================================================ C: LibForge
def _strip_ver(text):
    import re as _re
    return _re.sub(r'^\[v[^\]]*\]\s*', '', (text or '')).strip()


def _compose_desc(version, text):
    """Version in die description spiegeln: '[v<version>] <text>'."""
    return f"[v{version}] {_strip_ver(text)}".strip()


def lib_list(ctx):
    mgr = ctx.libs()
    out = [{
        "id": l.id, "name": l.name, "version": getattr(l, "version", "1.0"),
        "kind": getattr(l, "output_kind", "shared"),
        "abi": l.abi, "active": l.active, "built": l.last_build_ok, "so": l.so_relpath,
        "description": getattr(l, "description", ""),
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
    if active is not None:
        lib.active = bool(active)
    if version is not None:
        lib.version = str(version)
    if version is not None or description is not None:
        eff_ver = str(version) if version is not None else getattr(lib, "version", "1.0")
        base = description if description is not None else lib.description
        lib.description = _compose_desc(eff_ver, base)
    mgr.update(lib)
    return f"aktualisiert: {lib.name} v{getattr(lib,'version','?')} (id={lib.id}) — {lib.description}{warn}"


# ============================================================ D: Patches/Favoriten
_FAV_FIELDS = ("type", "file", "orig", "edit", "patch", "ram", "base", "target", "source", "scope")


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
        if t not in ("smali", "hex", "lib_replace", "new_file"):
            raise ValueError(f"unbekannter patch type: {t}")
        if t == "smali":
            p.setdefault("scope", "method")
        if t == "new_file":
            p.setdefault("scope", "new_file")
            if not p.get("file") or not p.get("edit"):
                raise ValueError("new_file patch braucht 'file' und 'edit' (Inhalt)")
    favorite["version"] = str(favorite.get("version", "1.0"))
    favorite["description"] = _compose_desc(favorite["version"], favorite.get("description", ""))
    return favorite


def favorites_list(ctx):
    favs = ctx.favorites().favs
    if not favs:
        return "keine Favoriten"
    out = []
    for i, f in enumerate(favs):
        patches = f.get("patches", [f])
        out.append({"index": i, "name": f.get("name", "(ohne Name)"),
                    "version": f.get("version", "1.0"),
                    "description": f.get("description", ""),
                    "patch_count": len(patches),
                    "types": [p.get("type", "smali") for p in patches]})
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
    for l in libs:
        if (getattr(l, "name", "") or "") == ref:
            return mgr, l
    rl = ref.lower()
    for l in libs:
        if (getattr(l, "name", "") or "").lower() == rl:
            return mgr, l
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
    return {"lib": lib.name, "success": bool(ok),
            "output": so_path if ok else "",
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
