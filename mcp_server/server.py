"""
MCP-Server (Schritt 2) — echter FastMCP-Server.

Registriert die MVP-Tools (Stufe O/P) und leitet jeden Aufruf durch runtime.run
(-> gating + EventBus-Sammler + audit). Transport = lokaler streamable-HTTP,
Lebenszyklus wird von der GUI-Seite „🔌 MCP" gesteuert.

Start:     python -m mcp_server.server --host 127.0.0.1 --port 8765
Selftest:  python -m mcp_server.server --selftest   (ohne mcp-Paket)
"""
import os
import sys

# Arbeitsverzeichnis-unabhaengig machen: Repo-Wurzel (Elternordner von mcp_server/)
# auf sys.path legen, damit `mcp_server`, `core`, `services` immer importierbar sind
# — egal, mit welchem cwd der Client den Server startet.
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import argparse
from typing import Optional

from mcp_server.app_context import AppContext
from mcp_server import tools, runtime


def _summ(**kw) -> str:
    import json
    try:
        return json.dumps(kw, ensure_ascii=False)[:300]
    except Exception:
        return str(kw)[:300]


def build_server(ctx, host, port):
    from mcp.server.fastmcp import FastMCP
    from mcp_server.guide import AGENT_GUIDE
    try:
        mcp = FastMCP("re-framework", instructions=AGENT_GUIDE)
    except TypeError:
        # aeltere FastMCP-Versionen ohne instructions-Parameter
        mcp = FastMCP("re-framework")
    try:
        mcp.settings.host = host
        mcp.settings.port = int(port)
    except Exception:
        pass

    # ---------------- A: Workspace & Config ----------------
    @mcp.tool(name="workspace_status")
    def workspace_status() -> str:
        """Config-Kurzfassung + adb-State."""
        return runtime.run(ctx, "workspace.status", lambda: tools.workspace_status(ctx))

    @mcp.tool(name="workspace_set_config")
    def workspace_set_config(key: str, value: str) -> str:
        """Setzt einen Config-Key (Flag/Strategie) und speichert config.json."""
        return runtime.run(ctx, "workspace.set_config",
                           lambda: tools.workspace_set_config(ctx, key, value),
                           _summ(key=key, value=value))

    @mcp.tool(name="workspace_prepare_hint")
    def workspace_prepare_hint() -> str:
        """GUI-Anleitung fuer PREPARE_WORKSPACE (Bauen macht der Nutzer)."""
        return runtime.run(ctx, "workspace.prepare_hint", lambda: tools.workspace_prepare_hint(ctx))

    # ---------------- B: Analyse / Smali ----------------
    @mcp.tool(name="smali_index_status")
    def smali_index_status() -> str:
        """Ist der Smali-Baum da? welche smali-Roots?"""
        return runtime.run(ctx, "smali.index_status", lambda: tools.smali_index_status(ctx))

    @mcp.tool(name="smali_search")
    def smali_search(pattern: str, regex: bool = False, max_results: int = 200) -> str:
        """Text/Regex-Suche im Smali-Baum (Datei:Zeile)."""
        return runtime.run(ctx, "smali.search",
                           lambda: tools.smali_search(ctx, pattern, regex, max_results),
                           _summ(pattern=pattern, regex=regex))

    @mcp.tool(name="smali_read")
    def smali_read(relpath: str, max_bytes: int = 20000) -> str:
        """Eine Smali-Datei (relativ zum unpacked dir) lesen."""
        return runtime.run(ctx, "smali.read",
                           lambda: tools.smali_read(ctx, relpath, max_bytes), _summ(relpath=relpath))

    @mcp.tool(name="smali_methods")
    def smali_methods(relpath: str) -> str:
        """Methoden/Felder einer Klasse auflisten (wie der 'Datei'-Reiter im Smali Studio)."""
        return runtime.run(ctx, "smali.methods", lambda: tools.smali_methods(ctx, relpath), _summ(relpath=relpath))

    @mcp.tool(name="smali_method")
    def smali_method(relpath: str, signature: str) -> str:
        """Eine Methode exakt extrahieren (.method..end method) — als orig fuer einen Method-Scope-Favoriten."""
        return runtime.run(ctx, "smali.method",
                           lambda: tools.smali_method(ctx, relpath, signature), _summ(relpath=relpath, signature=signature))

    @mcp.tool(name="smali_create_class")
    def smali_create_class(relpath: str, content: Optional[str] = None,
                           template: str = "standard", overwrite: bool = False) -> str:
        """Neue Smali-Klasse/-Datei anlegen. content=kompletter Smali-Text (verbatim) ODER
        template ('standard'|'receiver'). relpath z. B. 'smali/com/ghost/GhostNet.smali'."""
        return runtime.run(ctx, "smali.create_class",
                           lambda: tools.smali_create_class(ctx, relpath, content, template, overwrite),
                           _summ(relpath=relpath, template=template, overwrite=overwrite, has_content=bool(content)))

    @mcp.tool(name="smali_xref")
    def smali_xref(symbol: str, max_results: int = 200) -> str:
        """Aufrufer/Vorkommen eines Symbols finden (inkl. umschliessender Methode)."""
        return runtime.run(ctx, "smali.xref",
                           lambda: tools.smali_xref(ctx, symbol, max_results), _summ(symbol=symbol))

    # ---------------- C: LibForge ----------------
    @mcp.tool(name="lib_list")
    def lib_list() -> str:
        """Alle LibForge-Libs auflisten."""
        return runtime.run(ctx, "lib.list", lambda: tools.lib_list(ctx))

    @mcp.tool(name="lib_create")
    def lib_create(name: str, version: str, description: str,
                   source_code: Optional[str] = None, source_file: Optional[str] = None,
                   output_kind: Optional[str] = None) -> str:
        """Neue LibForge-Lib anlegen. PFLICHT: version (Freitext, z.B. '1.0') + description (Zweck).
        NEUE Lib = NEUER Pfad/Ansatz; bestehende NICHT ueberschreiben (dafuer lib.update, Bugfix).
        Quelle via source_code ODER source_file (lokaler Pfad auf dem PC). Build spaeter in der GUI."""
        return runtime.run(ctx, "lib.create",
                           lambda: tools.lib_create(ctx, name, source_code=source_code, version=version,
                                                    description=description, source_file=source_file,
                                                    output_kind=output_kind),
                           _summ(name=name, version=version))
    @mcp.tool(name="lib_update")
    def lib_update(lib_id: str, source_code: Optional[str] = None, abi: Optional[str] = None,
                   api_level: Optional[int] = None, link_libs: Optional[str] = None,
                   output_kind: Optional[str] = None, active: Optional[bool] = None,
                   name: Optional[str] = None, version: Optional[str] = None,
                   description: Optional[str] = None, source_file: Optional[str] = None) -> str:
        """C-Quelle / Build-Felder einer Lib setzen — nur BUGFIX/selber Pfad. Bei Quell-Aenderung
        version bumpen + description aktualisieren (sonst WARN). Neuer Pfad -> lib.create."""
        return runtime.run(ctx, "lib.update",
                           lambda: tools.lib_update(ctx, lib_id, source_code, abi, api_level,
                                                    link_libs, output_kind, active, name,
                                                    version=version, description=description,
                                                    source_file=source_file),
                           _summ(lib_id=lib_id, version=version))
    @mcp.tool(name="favorites_list")
    def favorites_list() -> str:
        """Favoriten-Patch-Saetze auflisten."""
        return runtime.run(ctx, "favorites.list", lambda: tools.favorites_list(ctx))

    @mcp.tool(name="favorites_get")
    def favorites_get(ref: str) -> str:
        """Einen Favoriten vollstaendig anzeigen (Index oder Name)."""
        return runtime.run(ctx, "favorites.get", lambda: tools.favorites_get(ctx, ref), _summ(ref=ref))

    @mcp.tool(name="favorites_add")
    def favorites_add(favorite_json: str) -> str:
        """Patch-Satz als Favorit ablegen. favorite_json = JSON-Objekt mit PFLICHT 'version'
        (z.B. '1.0') + 'description' (Zweck) auf Top-Ebene, dazu 'name' und 'patches':
        [{"type":"smali","file":..,"orig":..,"edit":..,"scope":"method"},
        {"type":"hex","file":..,"ram":..,"base":..,"orig":..,"patch":..},
        {"type":"lib_replace","target":..,"source":..},
        {"type":"new_file","file":..,"edit":..}]. Einzel-Patch-Kurzform ok.
        NEUER Favorit = neuer Patch-Pfad; Bugfix am selben -> favorites.update mit Version-Bump."""
        return runtime.run(ctx, "favorites.add", lambda: tools.favorites_add(ctx, favorite_json), "favorite")
    @mcp.tool(name="favorites_update")
    def favorites_update(ref: str, favorite_json: str) -> str:
        """Favoriten ersetzen (Index/Name). Gleiches JSON-Schema wie favorites_add (inkl. PFLICHT
        'version' + 'description'). Bei Aenderung version bumpen (sonst WARN)."""
        return runtime.run(ctx, "favorites.update",
                           lambda: tools.favorites_update(ctx, ref, favorite_json), _summ(ref=ref))
    @mcp.tool(name="favorites_delete")
    def favorites_delete(ref: str, confirm: bool = False) -> str:
        """Einen Favoriten loeschen (Index oder Name). Gated: delete_ops + confirm."""
        return runtime.run(ctx, "favorites.delete",
                           lambda: tools.favorites_delete(ctx, ref), _summ(ref=ref), confirm=confirm)

    @mcp.tool(name="patch_evaluate")
    def patch_evaluate(file: str, orig: str, scope: str = "method") -> str:
        """Dry-Run eines Smali-Patches gegen den Baum (exact/structural/append/conflict)."""
        return runtime.run(ctx, "patch.evaluate",
                           lambda: tools.patch_evaluate(ctx, file, orig, scope), _summ(file=file, scope=scope))

    # ---------------- F: File Manager (nur info=O im MVP) ----------------
    # ---------------- L: Frida ----------------
    @mcp.tool(name="frida_config_get")
    def frida_config_get() -> str:
        """Frida-Config lesen (mode/host/port/script_directory_path/pause_on_load + INJECT_FRIDA)."""
        return runtime.run(ctx, "frida.config_get", lambda: tools.frida_config_get(ctx))

    @mcp.tool(name="frida_config_set")
    def frida_config_set(mode: Optional[str] = None, host: Optional[str] = None,
                         port: Optional[int] = None, script_directory_path: Optional[str] = None,
                         pause_on_load: Optional[bool] = None) -> str:
        """Frida-Config setzen. mode: listen|connect|script|script_directory. Merge-sicher."""
        return runtime.run(ctx, "frida.config_set",
                           lambda: tools.frida_config_set(ctx, mode, host, port, script_directory_path, pause_on_load),
                           _summ(mode=mode, host=host, port=port, pause_on_load=pause_on_load))

    @mcp.tool(name="frida_build_toggle")
    def frida_build_toggle(enabled: bool) -> str:
        """Frida im Build aktivieren/deaktivieren (INJECT_FRIDA)."""
        return runtime.run(ctx, "frida.build_toggle", lambda: tools.frida_build_toggle(ctx, enabled), _summ(enabled=enabled))

    @mcp.tool(name="frida_scripts_list")
    def frida_scripts_list() -> str:
        """Frida-Skripte auflisten (id/name/version/aktiv)."""
        return runtime.run(ctx, "frida.scripts_list", lambda: tools.frida_scripts_list(ctx))

    @mcp.tool(name="frida_scripts_get")
    def frida_scripts_get(ref: str) -> str:
        """Ein Frida-Skript inkl. Code holen (id oder name)."""
        return runtime.run(ctx, "frida.scripts_get", lambda: tools.frida_scripts_get(ctx, ref), _summ(ref=ref))

    @mcp.tool(name="frida_scripts_add")
    def frida_scripts_add(name: str, version: str, description: str, code: Optional[str] = None) -> str:
        """Frida-Skript anlegen. PFLICHT version+description; code optional."""
        return runtime.run(ctx, "frida.scripts_add",
                           lambda: tools.frida_scripts_add(ctx, name, code=code, version=version, description=description),
                           _summ(name=name, version=version))

    @mcp.tool(name="frida_scripts_update")
    def frida_scripts_update(ref: str, name: Optional[str] = None, code: Optional[str] = None,
                             version: Optional[str] = None, description: Optional[str] = None) -> str:
        """Frida-Skript aendern (Bugfix: version bumpen + description; sonst WARN)."""
        return runtime.run(ctx, "frida.scripts_update",
                           lambda: tools.frida_scripts_update(ctx, ref, name, code, version, description),
                           _summ(ref=ref, version=version))

    @mcp.tool(name="frida_scripts_delete")
    def frida_scripts_delete(ref: str, confirm: bool = False) -> str:
        """Frida-Skript loeschen (id/name). Gated: delete_ops + confirm."""
        return runtime.run(ctx, "frida.scripts_delete", lambda: tools.frida_scripts_delete(ctx, ref), _summ(ref=ref), confirm=confirm)

    @mcp.tool(name="frida_set_active")
    def frida_set_active(ref: str) -> str:
        """Aktives Frida-Skript (Build-Ziel) setzen (id/name)."""
        return runtime.run(ctx, "frida.set_active", lambda: tools.frida_set_active(ctx, ref), _summ(ref=ref))

    @mcp.tool(name="frida_collections_list")
    def frida_collections_list() -> str:
        """Frida-Collections auflisten."""
        return runtime.run(ctx, "frida.collections_list", lambda: tools.frida_collections_list(ctx))

    @mcp.tool(name="frida_collections_add")
    def frida_collections_add(name: str, version: str, description: str, script_ids: Optional[str] = None) -> str:
        """Collection anlegen. PFLICHT version+description; script_ids = JSON-Liste ODER Komma-Liste."""
        return runtime.run(ctx, "frida.collections_add",
                           lambda: tools.frida_collections_add(ctx, name, script_ids=script_ids, version=version, description=description),
                           _summ(name=name, version=version))

    @mcp.tool(name="frida_collections_update")
    def frida_collections_update(ref: str, name: Optional[str] = None, script_ids: Optional[str] = None,
                                 version: Optional[str] = None, description: Optional[str] = None) -> str:
        """Collection aendern (name/script_ids/version/description)."""
        return runtime.run(ctx, "frida.collections_update",
                           lambda: tools.frida_collections_update(ctx, ref, name, script_ids, version, description), _summ(ref=ref))

    @mcp.tool(name="frida_collections_delete")
    def frida_collections_delete(ref: str, confirm: bool = False) -> str:
        """Collection loeschen (id/name). Gated: delete_ops + confirm."""
        return runtime.run(ctx, "frida.collections_delete", lambda: tools.frida_collections_delete(ctx, ref), _summ(ref=ref), confirm=confirm)

    @mcp.tool(name="frida_push_collection")
    def frida_push_collection(ref: str, confirm: bool = False) -> str:
        """Collection aufs Geraet pushen (kompiliert + adb push). Gated: file_manager + confirm."""
        return runtime.run(ctx, "frida.push_collection", lambda: tools.frida_push_collection(ctx, ref), _summ(ref=ref), confirm=confirm)

    @mcp.tool(name="device_info")
    def device_info() -> str:
        """adb-State + 3rd-Party-Paketliste (kein Dateizugriff)."""
        return runtime.run(ctx, "device.info", lambda: tools.device_info(ctx))

    # ---------------- H: Logs ----------------
    @mcp.tool(name="ghostlog_capture")
    def ghostlog_capture(lines: int = 200) -> str:
        """Aktuellen ghost.log-Inhalt (letzte N Zeilen) holen."""
        return runtime.run(ctx, "ghostlog.capture", lambda: tools.ghostlog_capture(ctx, lines), _summ(lines=lines))

    @mcp.tool(name="log_export")
    def log_export() -> str:
        """Audit + Kippy_RE_Log in den Log-Export-Ordner kopieren."""
        return runtime.run(ctx, "log.export", lambda: tools.log_export(ctx))

    # ---------------- K: MCP-Selbstauskunft ----------------
    @mcp.tool(name="mcp_capabilities")
    def mcp_capabilities() -> str:
        """Welche Tools/Caps sind aktuell freigeschaltet?"""
        return runtime.run(ctx, "mcp.capabilities", lambda: tools.mcp_capabilities(ctx))

    @mcp.tool(name="mcp_audit_tail")
    def mcp_audit_tail(n: int = 50) -> str:
        """Letzte N Audit-Eintraege lesen."""
        return runtime.run(ctx, "mcp.audit_tail", lambda: tools.mcp_audit_tail(ctx, n), _summ(n=n))

    @mcp.tool(name="mcp_guide")
    def mcp_guide() -> str:
        """Bedienungsanleitung fuer KI-Clients (Workflow, Tiers, Gotchas)."""
        return runtime.run(ctx, "mcp.guide", lambda: tools.mcp_guide(ctx))

    @mcp.tool(name="console_main_tail")
    def console_main_tail(n: int = 100, include: str = "", exclude: str = "", match: str = "any",
                          since_last_clear: bool = False, last_minutes: int = 0) -> str:
        """Main Console (Workspace) lesen. include/exclude = leerzeichengetrennte Begriffe
        (exclude=NOT, include=OR bzw. match='all' fuer AND). since_last_clear=nur seit letztem
        Leeren; last_minutes=nur die letzten X Minuten."""
        return runtime.run(ctx, "console.main_tail",
                           lambda: tools.console_main_tail(ctx, n, include, exclude, match, since_last_clear, last_minutes),
                           _summ(n=n, include=include, exclude=exclude, match=match,
                                 since_last_clear=since_last_clear, last_minutes=last_minutes))

    @mcp.tool(name="console_live_tail")
    def console_live_tail(n: int = 100, include: str = "", exclude: str = "", match: str = "any",
                          since_last_clear: bool = False, last_minutes: int = 0) -> str:
        """Start & Live-Log (App+Exe) lesen. include/exclude wie bei console_main_tail
        (deckt LOGCAT/GHOST/EXEC/PROXY/LOG ab). since_last_clear + last_minutes wie dort."""
        return runtime.run(ctx, "console.live_tail",
                           lambda: tools.console_live_tail(ctx, n, include, exclude, match, since_last_clear, last_minutes),
                           _summ(n=n, include=include, exclude=exclude, match=match,
                                 since_last_clear=since_last_clear, last_minutes=last_minutes))

    @mcp.tool(name="log_settings")
    def log_settings() -> str:
        """Logcat-/Intent-Favoriten (logger_profiles.json) lesen."""
        return runtime.run(ctx, "log.settings", lambda: tools.log_settings(ctx))

    @mcp.tool(name="log_add_logcat")
    def log_add_logcat(command: str) -> str:
        """Einen Logcat-Aufruf als Favorit ablegen (Platzhalter {PID}/{APP_NAME}/{APP_PACKAGE} erlaubt)."""
        return runtime.run(ctx, "log.add_logcat", lambda: tools.log_add_logcat(ctx, command), _summ(command=command))

    # ---------------- I: DAST / API-Inspector ----------------
    @mcp.tool(name="api_query")
    def api_query(filter: str = "", limit: int = 50, with_bodies: bool = False) -> str:
        """MITM-Traffic-DB abfragen (URL/Method-Filter). with_bodies=true fuer gekuerzte Bodies."""
        return runtime.run(ctx, "api.query",
                           lambda: tools.api_query(ctx, filter, limit, with_bodies),
                           _summ(filter=filter, limit=limit, with_bodies=with_bodies))

    @mcp.tool(name="api_get")
    def api_get(req_id: int) -> str:
        """Einen Traffic-Datensatz komplett (Bodies/Headers) per ID holen."""
        return runtime.run(ctx, "api.get", lambda: tools.api_get(ctx, req_id), _summ(req_id=req_id))

    # ---------------- J: Historie / Reporting ----------------
    @mcp.tool(name="history_list")
    def history_list(limit: int = 20) -> str:
        """Test-/Analyse-Historie lesen (RE_History.json)."""
        return runtime.run(ctx, "history.list", lambda: tools.history_list(ctx, limit), _summ(limit=limit))

    @mcp.tool(name="history_add")
    def history_add(name: str, result: str, observation: str = "", patches_json: str = "") -> str:
        """Analyse-Ergebnis/Beobachtung sichern (RE_History.json + Kippy_RE_Log.md) — 'Session permanent sichern'."""
        return runtime.run(ctx, "history.add",
                           lambda: tools.history_add(ctx, name, result, observation, patches_json or None),
                           _summ(name=name, result=result))

    # ---------------- E: Bauen & Flashen (gated: build/flash + confirm) ----------------
    @mcp.tool(name="build_hint")
    def build_hint() -> str:
        """GUI-Reihenfolge fuer den Build ausgeben (reine Info)."""
        return runtime.run(ctx, "build.hint", lambda: tools.build_hint(ctx))

    @mcp.tool(name="pipeline_run")
    def pipeline_run(name: str, confirm: bool = False) -> str:
        """Benannte Pipeline ausfuehren (PREPARE_WORKSPACE/BUILD_NATIVE/FLASH/...). Gated: build + confirm."""
        return runtime.run(ctx, "pipeline.run", lambda: tools.pipeline_run(ctx, name),
                           _summ(name=name), confirm=confirm)

    @mcp.tool(name="pipeline_flash")
    def pipeline_flash(confirm: bool = False) -> str:
        """FLASH-Pipeline (adb install der gebauten APK). Gated: flash + confirm."""
        return runtime.run(ctx, "pipeline.flash", lambda: tools.pipeline_flash(ctx), confirm=confirm)

    @mcp.tool(name="lib_build")
    def lib_build(ref: str, confirm: bool = False) -> str:
        """NativeLib/Executable via NDK bauen (Name oder ID). Gated: build + confirm."""
        return runtime.run(ctx, "lib.build", lambda: tools.lib_build(ctx, ref),
                           _summ(ref=ref), confirm=confirm)

    @mcp.tool(name="lib_delete")
    def lib_delete(ref: str, confirm: bool = False) -> str:
        """NativeLib-/Executable-Eintrag loeschen (Name oder ID). Gated: delete_ops + confirm."""
        return runtime.run(ctx, "lib.delete", lambda: tools.lib_delete(ctx, ref),
                           _summ(ref=ref), confirm=confirm)

    # ---------------- G: Executables (ExeDeploy) ----------------
    @mcp.tool(name="exec_list")
    def exec_list() -> str:
        """Deploybare (aktive) Executables auflisten."""
        return runtime.run(ctx, "exec.list", lambda: tools.exec_list(ctx))

    @mcp.tool(name="exec_deploy_run")
    def exec_deploy_run(ref: str, wait_s: int = 8, confirm: bool = False) -> str:
        """Deploy + Stage + Run eines Executables; sammelt wait_s Sekunden Live-Ausgabe. Gated: exec_run + confirm."""
        return runtime.run(ctx, "exec.deploy_run", lambda: tools.exec_deploy_run(ctx, ref, wait_s),
                           _summ(ref=ref, wait_s=wait_s), confirm=confirm,
                           capture_events=("LOG_INFO", "EXEC_OUTPUT"))

    @mcp.tool(name="exec_pull_result")
    def exec_pull_result(ref: str, confirm: bool = False) -> str:
        """Output-Dateien (pull_globs) eines Executables vom Geraet holen. Gated: file_manager + confirm."""
        return runtime.run(ctx, "exec.pull_result", lambda: tools.exec_pull_result(ctx, ref),
                           _summ(ref=ref), confirm=confirm)

    @mcp.tool(name="exec_delete")
    def exec_delete(ref: str, confirm: bool = False) -> str:
        """Executable-Eintrag loeschen (Name oder ID). Gated: delete_ops + confirm."""
        return runtime.run(ctx, "exec.delete", lambda: tools.exec_delete(ctx, ref),
                           _summ(ref=ref), confirm=confirm)

    # ---------------- F: File Manager (Geraet, gated) ----------------
    @mcp.tool(name="device_ls")
    def device_ls(path: str, domain: str = "runas", confirm: bool = False) -> str:
        """Verzeichnis auf dem Geraet listen (domain=runas App-Home / shell z.B. /data/local/tmp). Gated: file_manager + confirm."""
        return runtime.run(ctx, "device.ls", lambda: tools.device_ls(ctx, path, domain),
                           _summ(path=path, domain=domain), confirm=confirm)

    @mcp.tool(name="device_pull")
    def device_pull(remote_path: str, domain: str = "runas", confirm: bool = False) -> str:
        """Datei vom Geraet holen (nach data/.../mcp_session/pull). Gated: file_manager + confirm."""
        return runtime.run(ctx, "device.pull", lambda: tools.device_pull(ctx, remote_path, domain),
                           _summ(remote_path=remote_path, domain=domain), confirm=confirm)

    @mcp.tool(name="device_push")
    def device_push(local_path: str, remote_path: str, domain: str = "runas", confirm: bool = False) -> str:
        """Lokale Datei aufs Geraet schreiben. Gated: file_manager + confirm."""
        return runtime.run(ctx, "device.push", lambda: tools.device_push(ctx, local_path, remote_path, domain),
                           _summ(local_path=local_path, remote_path=remote_path, domain=domain), confirm=confirm)

    @mcp.tool(name="device_vault_bundle")
    def device_vault_bundle(subpath: str = ".", confirm: bool = False) -> str:
        """App-Datenverzeichnis (oder subpath) als tar+base64 ziehen -> lokal .tar. Gated: file_manager + confirm."""
        return runtime.run(ctx, "device.vault_bundle", lambda: tools.device_vault_bundle(ctx, subpath),
                           _summ(subpath=subpath), confirm=confirm)

    @mcp.tool(name="device_delete")
    def device_delete(remote_path: str, domain: str = "runas", confirm: bool = False) -> str:
        """Datei auf dem Geraet loeschen. Gated: file_manager + delete_ops + confirm."""
        return runtime.run(ctx, "device.delete", lambda: tools.device_delete(ctx, remote_path, domain),
                           _summ(remote_path=remote_path, domain=domain), confirm=confirm)

    # ---------------- I: net / proxy ----------------
    @mcp.tool(name="net_push_cert")
    def net_push_cert(confirm: bool = False) -> str:
        """mitmproxy-CA dieses Rechners nach Download/ pushen. Gated: file_manager + confirm."""
        return runtime.run(ctx, "net.push_cert", lambda: tools.net_push_cert(ctx), confirm=confirm)

    @mcp.tool(name="net_route")
    def net_route(mode: str = "usb", ip: str = "", confirm: bool = False) -> str:
        """Proxy-Routing setzen: mode=usb|wlan(ip=...)|reset. Gated: file_manager + confirm."""
        return runtime.run(ctx, "net.route", lambda: tools.net_route(ctx, mode, ip),
                           _summ(mode=mode, ip=ip), confirm=confirm)

    @mcp.tool(name="proxy_start")
    def proxy_start() -> str:
        """Lokalen mitmdump (mitm_addon) starten."""
        return runtime.run(ctx, "proxy.start", lambda: tools.proxy_start(ctx))

    @mcp.tool(name="proxy_stop")
    def proxy_stop() -> str:
        """Lokalen mitmdump stoppen."""
        return runtime.run(ctx, "proxy.stop", lambda: tools.proxy_stop(ctx))

    return mcp


def main() -> None:
    # Jeden Startfehler in eine Datei im Repo schreiben (stdout ist im stdio-Modus
    # der Protokollkanal und darf nicht benutzt werden -> Datei statt Konsole).
    try:
        _main()
    except SystemExit:
        raise
    except BaseException:
        import traceback
        import time
        try:
            logp = os.path.join(_REPO, "data", "mcp_server_stdio.log")
            os.makedirs(os.path.dirname(logp), exist_ok=True)
            with open(logp, "a", encoding="utf-8") as f:
                f.write(f"\n=== CRASH {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
                f.write("argv: " + " ".join(sys.argv) + "\n")
                f.write("cwd: " + os.getcwd() + "\n")
                f.write("python: " + sys.executable + "\n")
                traceback.print_exc(file=f)
        except Exception:
            pass
        raise


def _main() -> None:
    ap = argparse.ArgumentParser(description="RE-Framework MCP-Server (Schritt 2)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--transport", default=None,
                    help="stdio | http  (ueberschreibt die Config; stdio = von der Desktop-App gestartet)")
    ap.add_argument("--stdio", action="store_true", help="Kurzform fuer --transport stdio")
    ap.add_argument("--selftest", action="store_true",
                    help="ohne mcp-Paket: AppContext + Faehigkeiten pruefen")
    args = ap.parse_args()

    ctx = AppContext()

    if args.selftest:
        print("[SELFTEST] AppContext ok.")
        print(tools.mcp_capabilities(ctx))
        return

    # Transport bestimmen: --stdio / --transport > Config
    transport = "stdio" if args.stdio else args.transport
    if not transport:
        transport = (ctx.cfg.config.get("MCP_SETTINGS", {}).get("server", {}) or {}).get("transport", "http")
    transport = {"http": "streamable-http"}.get(transport, transport)

    try:
        mcp = build_server(ctx, args.host, args.port)
    except ModuleNotFoundError as e:
        # Im stdio-Modus NICHT nach stdout schreiben (stdout ist der Protokollkanal).
        print("[MCP] Python-Paket 'mcp' fehlt. Bitte installieren:  pip install mcp", file=sys.stderr, flush=True)
        print(f"[MCP] Detail: {e}", file=sys.stderr, flush=True)
        sys.exit(2)

    if transport == "stdio":
        # stdio: kein stdout-Geschwaetz, sonst wird das JSON-RPC-Protokoll zerstoert.
        mcp.run(transport="stdio")
    else:
        print(f"[MCP] Server startet ({transport}) auf {args.host}:{args.port}", flush=True)
        mcp.run(transport=transport)


if __name__ == "__main__":
    main()
