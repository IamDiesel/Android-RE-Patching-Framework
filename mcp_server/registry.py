"""
Zentrale Tool-Registry des MCP-Servers.

REINE METADATEN (keine schweren Imports) -> gemeinsam genutzt von:
  - der GUI-Seite „MCP" (baut die Pro-Tool-Matrix daraus),
  - gating.py (entscheidet erlaubt/verweigert),
  - server.py (registriert die Tools).

Aenderungen an der Tool-Liste passieren AUSSCHLIESSLICH hier.

Stufen (tier):
  P = Prepare  -> lokale Vorbereitung (frei, Standard AN)
  O = Observe  -> nur lesen/beobachten (frei, Standard AN)
  G = Gated    -> Bauen/Flashen/Starten/Dateizugriff/Loeschen
                  (Standard AUS; braucht Pro-Tool-Schalter + alle caps + confirm)

caps: nur bei G relevant. Alle genannten Kategorien muessen in
MCP_SETTINGS.caps freigeschaltet sein, sonst wird verweigert.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolSpec:
    id: str
    group: str            # "A".."K"
    group_name: str
    tier: str             # "P" | "O" | "G"
    caps: tuple = ()      # benoetigte Kategorie-Gates (nur bei tier == "G")
    summary: str = ""


# Reihenfolge = Anzeige-Reihenfolge in der Settings-Matrix.
TOOL_CATALOG = [
    # --- A: Workspace & Config ---
    ToolSpec("workspace.status",       "A", "Workspace & Config", "O", (), "Config-Kurzfassung + adb-State"),
    ToolSpec("workspace.set_config",   "A", "Workspace & Config", "P", (), "Config-Keys/Flags/Pipelines setzen"),
    ToolSpec("app.import",             "A", "Workspace & Config", "P", (), "APK/Split importieren -> Auto-Config"),
    ToolSpec("workspace.prepare_hint", "A", "Workspace & Config", "O", (), "GUI-Anleitung fuer PREPARE_WORKSPACE"),

    # --- B: Analyse / Smali Studio ---
    ToolSpec("smali.index_status",     "B", "Analyse / Smali", "O", (), "Ist der RAM-Index vorhanden?"),
    ToolSpec("smali.search",           "B", "Analyse / Smali", "O", (), "Text/Regex-Suche im Smali"),
    ToolSpec("smali.xref",             "B", "Analyse / Smali", "O", (), "Eingehende XRefs auf ein Symbol"),
    ToolSpec("smali.read",             "B", "Analyse / Smali", "O", (), "Smali-Datei lesen"),
    ToolSpec("smali.methods",          "B", "Analyse / Smali", "O", (), "Methoden/Felder einer Klasse auflisten (wie 'Datei'-Reiter)"),
    ToolSpec("smali.method",           "B", "Analyse / Smali", "O", (), "Eine Methode exakt extrahieren (.method..end) -> orig fuer Favorit"),
    ToolSpec("smali.create_class",     "B", "Analyse / Smali", "P", (), "Neue Smali-Klasse/-Datei anlegen (z. B. GHOST_NET)"),
    ToolSpec("smali.callgraph",        "B", "Analyse / Smali", "O", (), "Callgraph/Graphviz (spaeter)"),

    # --- C: LibForge vorbereiten ---
    ToolSpec("lib.list",               "C", "LibForge", "P", (), "Alle NativeLibs auflisten"),
    ToolSpec("lib.create",             "C", "LibForge", "P", (), "Lib anlegen (PFLICHT version+description; neue Lib=neuer Pfad)"),
    ToolSpec("lib.update",             "C", "LibForge", "P", (), "C-Quelle/Felder (Bugfix: version bumpen+description; sonst WARN)"),
    ToolSpec("lib.import_so",          "C", "LibForge", "P", (), "Fertige .so eintragen (kein Build)"),
    ToolSpec("lib.build",              "C", "LibForge", "G", ("build",), "NDK-Build (gated)"),
    ToolSpec("lib.set_active",         "C", "LibForge", "P", (), "Lib aktiv/inaktiv setzen"),
    ToolSpec("lib.delete",             "C", "LibForge", "G", ("delete_ops",), "Lib loeschen (nur auf Anfrage)"),

    # --- D: Patches als Favoriten ---
    ToolSpec("favorites.list",         "D", "Patches / Favoriten", "P", (), "Favoriten auflisten (Name, Anzahl, Typen)"),
    ToolSpec("favorites.get",          "D", "Patches / Favoriten", "O", (), "Einen Favoriten vollstaendig anzeigen (Index/Name)"),
    ToolSpec("favorites.add",          "D", "Patches / Favoriten", "P", (), "Patch-Satz ablegen (PFLICHT version+description; smali/hex/lib_replace/new_file)"),
    ToolSpec("favorites.update",       "D", "Patches / Favoriten", "P", (), "Favoriten ersetzen (Bugfix: version bumpen; sonst WARN)"),
    ToolSpec("patch.evaluate",         "D", "Patches / Favoriten", "O", (), "Dry-Run gegen den RAM-Index"),
    ToolSpec("patch.active_list",      "D", "Patches / Favoriten", "O", (), "Aktive Session-Patches auflisten (Index fuer patch.remove)"),
    ToolSpec("patch.apply",            "D", "Patches / Favoriten", "P", (), "Patch(es) auf die aktive Session anwenden (headless 'Favoriten anwenden')"),
    ToolSpec("patch.apply_favorite",   "D", "Patches / Favoriten", "P", (), "Kompletten Favoriten/Bundle auf die aktive Session anwenden"),
    ToolSpec("patch.remove",           "D", "Patches / Favoriten", "P", (), "Angewendeten Patch aus der aktiven Liste streichen (nicht aus Favoriten)"),
    ToolSpec("patch.clear_active",     "D", "Patches / Favoriten", "P", (), "Alle aktiven Session-Patches verwerfen"),
    ToolSpec("favorites.delete",       "D", "Patches / Favoriten", "G", ("delete_ops",), "Favorit loeschen (nur auf Anfrage)"),

    # --- E: Bauen & Flashen ---
    ToolSpec("build.hint",             "E", "Bauen & Flashen", "O", (), "GUI-Reihenfolge fuer den Build ausgeben"),
    ToolSpec("pipeline.run",           "E", "Bauen & Flashen", "G", ("build",), "Benannte Pipeline ausfuehren (gated)"),
    ToolSpec("pipeline.flash",         "E", "Bauen & Flashen", "G", ("flash",), "FLASH / adb install (gated)"),

    # --- F: File Manager (Geraet) ---
    ToolSpec("device.info",            "F", "File Manager", "O", (), "adb-State + Paketliste (kein Dateizugriff)"),
    ToolSpec("device.ls",              "F", "File Manager", "G", ("file_manager",), "Verzeichnis listen (run-as/shell)"),
    ToolSpec("device.pull",            "F", "File Manager", "G", ("file_manager",), "Datei(en) vom Geraet holen"),
    ToolSpec("device.vault_bundle",    "F", "File Manager", "G", ("file_manager",), "Vault-Bundle (tar+base64) ziehen"),
    ToolSpec("device.push",            "F", "File Manager", "G", ("file_manager",), "Datei aufs Geraet schreiben"),
    ToolSpec("device.delete",          "F", "File Manager", "G", ("file_manager", "delete_ops"), "Datei loeschen (nur auf Anfrage)"),

    # --- G: Executables (ExeDeploy) ---
    ToolSpec("exec.list",              "G", "Executables", "O", (), "Deploybare Executables auflisten"),
    ToolSpec("exec.deploy_run",        "G", "Executables", "G", ("exec_run",), "Deploy + stage + run (gated)"),
    ToolSpec("exec.pull_result",       "G", "Executables", "G", ("file_manager",), "Output-Dateien holen"),
    ToolSpec("exec.delete",            "G", "Executables", "G", ("delete_ops",), "Executable-Eintrag loeschen"),

    # --- H: Capture, Logs & Export ---
    ToolSpec("ghostlog.capture",       "H", "Capture & Logs", "O", (), "ghost.log N Sekunden mitschneiden"),
    ToolSpec("logcat.capture",         "H", "Capture & Logs", "O", (), "Logcat gefiltert N Sekunden"),
    ToolSpec("log.settings",           "H", "Capture & Logs", "O", (), "Logcat-/Intent-Favoriten (logger_profiles.json) lesen"),
    ToolSpec("log.add_logcat",         "H", "Capture & Logs", "P", (), "Logcat-Aufruf als Favorit hinzufuegen"),
    ToolSpec("log.export",             "H", "Capture & Logs", "O", (), "Logs in log_export_dir exportieren"),
    ToolSpec("console.main_tail",      "H", "Capture & Logs", "O", (), "Main-Console-Spiegel lesen (Include/Exclude-Filter)"),
    ToolSpec("console.live_tail",      "H", "Capture & Logs", "O", (), "Live-Log-Console-Spiegel lesen (Include/Exclude-Filter)"),

    # --- I: DAST / API-Inspector ---
    ToolSpec("api.query",              "I", "DAST / API", "O", (), "Traffic-DB abfragen (URL/Method-Filter)"),
    ToolSpec("api.get",                "I", "DAST / API", "O", (), "Einen Traffic-Datensatz komplett holen (per ID)"),
    ToolSpec("net.push_cert",          "I", "DAST / API", "G", ("file_manager",), "mitmproxy-CA aufs Geraet"),
    ToolSpec("net.route",              "I", "DAST / API", "G", ("file_manager",), "Proxy-Routing setzen"),
    ToolSpec("proxy.start",            "I", "DAST / API", "O", (), "Lokalen mitmproxy starten"),
    ToolSpec("proxy.stop",             "I", "DAST / API", "O", (), "Lokalen mitmproxy stoppen"),

    # --- J: Historie / Reporting ---
    ToolSpec("history.list",           "J", "Historie", "O", (), "Test-Historie lesen"),
    ToolSpec("history.add",            "J", "Historie", "P", (), "Historien-Eintrag hinzufuegen"),

    # --- K: MCP-Selbstauskunft ---
    ToolSpec("mcp.capabilities",       "K", "MCP-Selbstauskunft", "O", (), "Welche Tools/Caps sind frei?"),
    ToolSpec("mcp.audit_tail",         "K", "MCP-Selbstauskunft", "O", (), "Letzte Audit-Eintraege lesen"),
    ToolSpec("mcp.guide",              "K", "MCP-Selbstauskunft", "O", (), "Bedienungsanleitung (Workflow, Tiers, Gotchas)"),

    # --- L: Frida ---
    ToolSpec("frida.config_get",        "L", "Frida", "O", (), "Frida-Config lesen (Modus/Netz/ScriptDir/Start)"),
    ToolSpec("frida.config_set",        "L", "Frida", "P", (), "Frida-Config setzen (mode/host/port/script_directory_path/pause_on_load)"),
    ToolSpec("frida.build_toggle",      "L", "Frida", "P", (), "Frida im Build an/aus (INJECT_FRIDA)"),
    ToolSpec("frida.scripts_list",      "L", "Frida", "P", (), "Frida-Skripte auflisten"),
    ToolSpec("frida.scripts_get",       "L", "Frida", "O", (), "Ein Frida-Skript inkl. Code holen"),
    ToolSpec("frida.scripts_add",       "L", "Frida", "P", (), "Frida-Skript anlegen (PFLICHT version+description)"),
    ToolSpec("frida.scripts_update",    "L", "Frida", "P", (), "Frida-Skript aendern (Bugfix: version bumpen; sonst WARN)"),
    ToolSpec("frida.scripts_delete",    "L", "Frida", "G", ("delete_ops",), "Frida-Skript loeschen (nur auf Anfrage)"),
    ToolSpec("frida.set_active",        "L", "Frida", "P", (), "Aktives Frida-Skript (Build-Ziel) setzen"),
    ToolSpec("frida.collections_list",  "L", "Frida", "P", (), "Frida-Collections auflisten"),
    ToolSpec("frida.collections_add",   "L", "Frida", "P", (), "Collection anlegen (PFLICHT version+description)"),
    ToolSpec("frida.collections_update","L", "Frida", "P", (), "Collection aendern (name/script_ids/version/description)"),
    ToolSpec("frida.collections_delete","L", "Frida", "G", ("delete_ops",), "Collection loeschen (nur auf Anfrage)"),
    ToolSpec("frida.push_collection",   "L", "Frida", "G", ("file_manager",), "Collection aufs Geraet pushen (gated)"),
    ToolSpec("native.targets",    "M", "Native Studio", "O", (), "Gepackte/verschluesselte Libs in einem APK listen (lib/arm64-v8a/*.so)"),
    ToolSpec("native.status",     "M", "Native Studio", "O", (), "Arbeitsbereich-Status: entschluesselte .dec.so, Index-Cache, gecachtes Ghidra-C"),
    ToolSpec("native.unpack",     "M", "Native Studio", "P", (), "Gepackte Lib (APK/.so) via entdecktem Plugin entschluesseln -> .dec.so (lokal)"),
    ToolSpec("native.load",       "M", "Native Studio", "O", (), "ELF-Kurzinfo einer .so (AArch64?, Segmente, Symbole, Entry)"),
    ToolSpec("native.index",      "M", "Native Studio", "O", (), "XREF/CallGraph-Index bauen/auffrischen (SHA-gegateter Cache)"),
    ToolSpec("native.search",     "M", "Native Studio", "O", (), "In .so suchen: Symbol, String, Adresse"),
    ToolSpec("native.xref",       "M", "Native Studio", "O", (), "Wer referenziert 0xADDR? (Instruktions-/Reloc-Refs + Caller-Funktion)"),
    ToolSpec("native.disasm",     "M", "Native Studio", "O", (), "Funktion/Bereich disassemblieren (ab Adresse/Symbol)"),
    ToolSpec("native.read_bytes", "M", "Native Studio", "O", (), "Rohbytes/Hex an virtueller Adresse oder Datei-Offset lesen"),
    ToolSpec("native.callgraph",  "M", "Native Studio", "O", (), "Caller/Callees einer Funktion (symbolbasiert)"),
    ToolSpec("native.decompile",  "M", "Native Studio", "O", (), "Gecachtes Ghidra-C einer Funktion (sonst Hinweis: in GUI erzeugen)"),
    ToolSpec("native.functions",  "M", "Native Studio", "O", (), "Funktionsanfaenge listen: benannte + per Index entdeckte (sub_)"),
    ToolSpec("native.strings",    "M", "Native Studio", "O", (), "ASCII-Strings aus der .so (Krypto-/Algo-Namen, /proc, ptrace ...)"),
    ToolSpec("native.run",        "M", "Native Studio", "O", (), "Funktion emulieren (Args/Pointer/Buffer/Stubs) -> Register/ret/Out-Puffer"),
    ToolSpec("native.run_break",  "M", "Native Studio", "O", (), "Funktion emulieren bis Breakpoint(e) am Block-Eintritt -> Pause-Zustand (pc/Register) oder ret"),
    ToolSpec("native.lift",       "M", "Native Studio", "O", (), "Basisblock/Funktion nach Python heben (bbsrc Block->Python-Lifter) ab Adresse/Symbol"),
    ToolSpec("native.verify",     "M", "Native Studio", "O", (), "Emulation gegen gespeicherten Golden pruefen (Bit-Exakt-Regression); update=True speichert Golden"),
    ToolSpec("native.step",       "M", "Native Studio", "O", (), "Ein Basisblock-Schritt in einer pausierten Session (von native.run_break)"),
    ToolSpec("native.resume",     "M", "Native Studio", "O", (), "Pausierte Session bis zum naechsten Breakpoint/Ende fortsetzen"),
    ToolSpec("native.rebase",     "M", "Native Studio", "O", (), "Adressen/Symbole ueber Lib-Versionen abbilden (dynsym-Match) oder Versions-Diff"),
    ToolSpec("native.coverage",   "M", "Native Studio", "O", (), "Funktion emulieren -> Coverage (Mnemonics), Slow-Pfad-Kandidaten und unbekannte Instruktionen"),
    ToolSpec("native.snapshot",   "M", "Native Studio", "P", (), "Emu-Zustand (Mem+Register) nach einem Lauf als .pkl.xz speichern bzw. laden+ausfuehren"),
    ToolSpec("native.resolveptr", "M", "Native Studio", "O", (), "Reloc/Zeiger statisch aufloesen (addr=) oder Relocs um eine Adresse listen (around=)"),
    ToolSpec("native.dbg_state",  "M", "Native Studio", "O", (), "Geteilte Debug-Sitzung (GUI<->KI) ansehen: pc/seq/writer/Breakpoints/Trace (read-only)"),
    ToolSpec("native.dbg_lift",   "M", "Native Studio", "O", (), "Geteilte Debug-Sitzung laden und den gelaufenen Pfad nach Python liften (read-only)"),
    ToolSpec("native.dbg_step",   "M", "Native Studio", "G", ("dbg_write",), "Geteilte Debug-Sitzung n Schritte weiter + zurueckschreiben (Freigabe dbg_write)"),
    ToolSpec("native.dbg_resume", "M", "Native Studio", "G", ("dbg_write",), "Geteilte Debug-Sitzung bis Breakpoint/Budget fortsetzen + zurueckschreiben (Freigabe dbg_write)"),
    ToolSpec("native.jni_run",    "M", "Native Studio", "O", (), "JNI_OnLoad mit Fake-JNIEnv+Reloc+Imports emulieren; Fortschritt/Stop + JNI-Call-Map + RegisterNatives"),
    ToolSpec("native.jni_log",    "M", "Native Studio", "O", (), "Letzten jni_run auslesen: Call-Log + RegisterNatives-Map + Klassifizierungen (read-only)"),
    ToolSpec("native.jni_smali",  "M", "Native Studio", "O", (), "Smali-Body + Klassifizierung + Vorschlagsrueckgabe zu Klasse/Methode/Signatur (read-only)"),
    ToolSpec("native.dbg_ckpt_list",    "M", "Native Studio", "O", (), "Benannte Debug-Checkpoints auflisten (geteilte Sitzungen zum Nachstellen)"),
    ToolSpec("native.dbg_ckpt_activate","M", "Native Studio", "O", (), "Benannten Checkpoint in die Live-Debug-Sitzung kopieren (danach in der GUI ⬇ Von KI)"),
]

GROUP_ORDER = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M"]
GROUP_NAMES = {s.group: s.group_name for s in TOOL_CATALOG}
CAP_KEYS = ["build", "flash", "app_start", "exec_run", "file_manager", "delete_ops", "dbg_write"]

_BY_ID = {s.id: s for s in TOOL_CATALOG}


def tier_default(tier: str) -> bool:
    """Standardzustand einer Funktion: P/O an, G aus."""
    return tier in ("P", "O")


def get(tool_id: str):
    return _BY_ID.get(tool_id)


def by_group():
    """Liefert {group: [ToolSpec, ...]} in Anzeige-Reihenfolge."""
    out = {g: [] for g in GROUP_ORDER}
    for s in TOOL_CATALOG:
        out.setdefault(s.group, []).append(s)
    return out


def is_tool_enabled(mcp_settings: dict, tool_id: str) -> bool:
    """Pro-Tool-Schalter: expliziter Eintrag gewinnt, sonst Tier-Default."""
    spec = _BY_ID.get(tool_id)
    if not spec:
        return False
    tools = (mcp_settings or {}).get("tools", {}) or {}
    if tool_id in tools:
        return bool(tools[tool_id])
    return tier_default(spec.tier)
