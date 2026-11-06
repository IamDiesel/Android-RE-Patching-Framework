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
from core.application.session_state import SessionState
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
    Reihenfolge = SessionState.get_all_patches(): hex, lib_replace, smali."""
    view = []
    gi = 0
    for e in SessionState.active_hex_patches:
        view.append((gi, "hex", SessionState.active_hex_patches, e)); gi += 1
    for e in SessionState.active_lib_replacements:
        view.append((gi, "lib_replace", SessionState.active_lib_replacements, e)); gi += 1
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
        else:
            item["file"] = e.get("file", "")
            item["scope"] = e.get("scope", "method")
        out.append(item)
    return {"active_count": len(out),
            "counts": {"hex": len(SessionState.active_hex_patches),
                       "lib_replace": len(SessionState.active_lib_replacements),
                       "smali": len(SessionState.active_smali_patches)},
            "patches": out}


def patch_apply(ctx, patch_json):
    """Patch(es) auf die aktive Session anwenden. patch_json = EIN Patch ODER {patches:[...]}."""
    try:
        fav = _normalize_favorite(patch_json)
    except Exception as e:
        return f"[Fehler] Patch ungueltig: {e}"
    ram_cache = _smali_ram_cache(ctx)
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
    ram_cache = _smali_ram_cache(ctx)
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


def _dbg_folder(folder):
    return folder or _DBG_SESSION_FOLDER


def native_dbg_state(ctx, folder=None):
    """Geteilte Debug-Sitzung ansehen (read-only). O. In der GUI vorher 🔗 Sync."""
    try:
        from services.native_debug_session_service import NativeDebugSessionService
    except Exception as e:
        return {"ok": False, "hinweis": f"Service nicht ladbar: {e!r}"}
    pk = NativeDebugSessionService().peek(_dbg_folder(folder))
    meta = pk.get("meta")
    if not meta:
        return {"ok": False, "hinweis": f"keine Sitzung in {_dbg_folder(folder)} — in der GUI erst 🔗 Sync."}
    tr = pk.get("trace") or []
    return {"ok": True, "folder": _dbg_folder(folder),
            "pc": f"0x{int(meta.get('pc', 0)):x}", "seq": meta.get("seq"), "writer": meta.get("writer"),
            "entry": meta.get("entry"), "relocs": meta.get("relocs"), "mode": meta.get("mode"),
            "flags": meta.get("flags"), "ts": meta.get("ts"),
            "bps": [f"0x{int(b):x}" for b in (meta.get("bps") or [])],
            "trace_len": len(tr), "trace_tail": [e.get("pc_hex") for e in tr[-10:]]}


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
        return {"ok": False, "hinweis": "keine Sitzung — in der GUI erst 🔗 Sync."}
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
