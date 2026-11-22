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
    ToolSpec("workspace.renew_id",     "A", "Workspace & Config", "P", (), "Neue Patch-Session-ID (PID-YYYYmmdd-HHMMSS) + Archiv-Ordner generieren"),
    ToolSpec("app.list_local",         "A", "Workspace & Config", "O", (), "Lokal importierte Apps in source/ samt Unpacked-/Runtime-Status auflisten"),
    ToolSpec("app.import",             "A", "Workspace & Config", "P", (), "APK/Split importieren -> Auto-Config"),
    ToolSpec("app.pull_apk",           "A", "Workspace & Config", "G", ("file_manager",), "Installierte App (inkl. Splits) vom ADB-Geraet nach source/<pkg>/ ziehen (gated)"),
    ToolSpec("workspace.prepare_hint", "A", "Workspace & Config", "O", (), "GUI-Anleitung fuer PREPARE_WORKSPACE"),

    # --- B: Analyse / Smali Studio ---
    ToolSpec("smali.index_status",     "B", "Analyse / Smali", "O", (), "Ist der RAM-Index vorhanden?"),
    ToolSpec("smali.search",           "B", "Analyse / Smali", "O", (), "Text/Regex-Suche im Smali"),
    ToolSpec("smali.xref",             "B", "Analyse / Smali", "O", (), "Eingehende XRefs auf ein Symbol"),
    ToolSpec("smali.read",             "B", "Analyse / Smali", "O", (), "Smali-Datei lesen"),
    ToolSpec("smali.methods",          "B", "Analyse / Smali", "O", (), "Methoden/Felder einer Klasse auflisten (wie 'Datei'-Reiter)"),
    ToolSpec("smali.method",           "B", "Analyse / Smali", "O", (), "Eine Methode exakt extrahieren (.method..end) -> orig fuer Favorit"),
    ToolSpec("smali.create_class",     "B", "Analyse / Smali", "P", (), "Neue Smali-Klasse/-Datei anlegen (z. B. GHOST_NET)"),
    ToolSpec("smali.callgraph",        "B", "Analyse / Smali", "O", (), "Ausgehende Callees & eingehende Caller einer Smali-Methode (depth=1..3)"),
    ToolSpec("app.manifest_info",      "B", "Analyse / Smali", "O", (), "AndroidManifest.xml (Activities, Launcher, Permissions, Exported, Deep-Links) & APK-Signaturinfo lesen"),

    # --- C: LibForge vorbereiten ---
    ToolSpec("lib.list",               "C", "LibForge", "P", (), "Alle NativeLibs auflisten"),
    ToolSpec("lib.create",             "C", "LibForge", "P", (), "Lib anlegen (PFLICHT version+description; neue Lib=neuer Pfad)"),
    ToolSpec("lib.update",             "C", "LibForge", "P", (), "C-Quelle/Felder (Bugfix: version bumpen+description; sonst WARN)"),
    ToolSpec("lib.import_so",          "C", "LibForge", "P", (), "Fertige .so eintragen (kein Build)"),
    ToolSpec("lib.build",              "C", "LibForge", "G", ("build",), "NDK-Build (gated)"),
    ToolSpec("lib.set_active",         "C", "LibForge", "P", (), "Lib aktiv/inaktiv setzen"),
    ToolSpec("lib.delete",             "C", "LibForge", "G", ("delete_ops",), "Lib loeschen (nur auf Anfrage)"),

    # --- D: Patches als Favoriten ---
    ToolSpec("favorites.list",         "D", "Patches / Favoriten", "P", (), "Favoriten auflisten (optional nach Tool-Typ gefiltert; Multi-Tool-Info)"),
    ToolSpec("favorites.get",          "D", "Patches / Favoriten", "O", (), "Einen Favoriten vollstaendig anzeigen (Index/Name)"),
    ToolSpec("favorites.add",          "D", "Patches / Favoriten", "P", (), "Patch-Satz ablegen (PFLICHT version+description; smali/hex/lib_replace/new_file/libcarve/libforge)"),
    ToolSpec("favorites.append",       "D", "Patches / Favoriten", "P", (), "Patch(es) an bestehenden Favoriten anhaengen (Multi-Tool-Kombi)"),
    ToolSpec("favorites.combine",      "D", "Patches / Favoriten", "P", (), "Mehrere Favoriten zu einem neuen Multi-Tool-Favoriten kombinieren"),
    ToolSpec("favorites.update",       "D", "Patches / Favoriten", "P", (), "Favoriten ersetzen (Bugfix: version bumpen; sonst WARN)"),
    ToolSpec("patch.evaluate",         "D", "Patches / Favoriten", "O", (), "Dry-Run gegen den RAM-Index"),
    ToolSpec("patch.active_list",      "D", "Patches / Favoriten", "O", (), "Aktive Session-Patches auflisten (Index fuer patch.remove)"),
    ToolSpec("patch.apply",            "D", "Patches / Favoriten", "P", (), "Patch(es) auf die aktive Session anwenden (headless 'Favoriten anwenden')"),
    ToolSpec("patch.apply_favorite",   "D", "Patches / Favoriten", "P", (), "Kompletten Favoriten/Bundle auf die aktive Session anwenden"),
    ToolSpec("patch.remove",           "D", "Patches / Favoriten", "P", (), "Angewendeten Patch aus der aktiven Liste streichen (nicht aus Favoriten)"),
    ToolSpec("patch.clear_active",     "D", "Patches / Favoriten", "P", (), "Alle aktiven Session-Patches verwerfen"),
    ToolSpec("patch.fuzzy_match",      "D", "Patches / Favoriten", "O", (), "Verschobene Smali-Patches nach App-Update per FuzzingEngine wiederfinden (orig/file oder favorite_ref)"),
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
    ToolSpec("app.start",              "H", "Capture & Logs", "G", ("app_start",), "App/Intent auf dem verbundenen ADB-Geraet starten (gated: app_start)"),
    ToolSpec("app.stop",               "H", "Capture & Logs", "G", ("app_start",), "App auf dem verbundenen ADB-Geraet per force-stop beenden (gated: app_start)"),

    # --- I: DAST / API-Inspector ---
    ToolSpec("api.query",              "I", "DAST / API", "O", (), "Traffic-DB abfragen (URL/Method-Filter)"),
    ToolSpec("api.get",                "I", "DAST / API", "O", (), "Einen Traffic-Datensatz komplett holen (per ID)"),
    ToolSpec("api.update",             "I", "DAST / API", "P", (), "Traffic-Datensatz (Kommentar, Request-/Response-Body) in api_traffic.db aktualisieren"),
    ToolSpec("api.replay",             "I", "DAST / API", "P", (), "Request aus api_traffic.db (req_id) oder frei per HTTP erneut senden und optional aufzeichnen"),
    ToolSpec("api.clear",              "I", "DAST / API", "G", ("delete_ops",), "Alle Eintraege in api_traffic.db loeschen (gated: delete_ops)"),
    ToolSpec("api.rules_list",         "I", "DAST / API", "O", (), "Aktive Intercept-Regeln (intercept_rules.json) auflisten"),
    ToolSpec("api.rules_set",          "I", "DAST / API", "P", (), "Intercept-Regeln setzen/ergaenzen (PFLICHT fuer GUI+MCP Paritaet)"),
    ToolSpec("api.extract_flutter_static", "I", "DAST / API", "O", (), "Statische Cognito-/GraphQL-/REST-Vorlagen aus flutter_assets & libapp.so scannen"),
    ToolSpec("api.extract_engine_static",  "I", "DAST / API", "O", (), "Statische API-/Web-Endpunkte fuer Hybrid-Web, React Native/Hermes, Flutter & Web-Assets scannen"),
    ToolSpec("api.import_templates",   "I", "DAST / API", "P", (), "Ausgewaehlte statische API-Vorlagen in den API Inspector (api_traffic.db) uebernehmen"),
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
    ToolSpec("native.decompile",  "M", "Native Studio", "O", (), "Ghidra-C einer Funktion lesen (func=Name/0xAddr); run=True startet Ghidra nicht-blockierend im Hintergrund (danach ohne run abfragen)"),
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
    ToolSpec("native.dispatch_trace",   "M", "Native Studio", "O", (), "CFF-2D-vtable Dispatch-Tracer: protokolliert jeden Dispatch (Slot/idx1/col/Wert/leer/Taint) + findet Divergenz/Trap"),
    ToolSpec("native.survive_run",      "M", "Native Studio", "O", (), "RASP Survival Mode + Syscall-Trace: JNI_OnLoad ueber die Anti-Tamper-Trap hinaus laufen lassen und die rohen Detection-Syscalls (newfstatat/openat/...) aufzeichnen"),
    ToolSpec("native.syscall_policy_get","M", "Native Studio", "O", (), "Syscall-Policy lesen (geraeteechte svc-Rueckgaben fuer survive_run)"),
    ToolSpec("native.syscall_policy_set","M", "Native Studio", "P", (), "Syscall-Policy setzen/persistieren (preset 'genuine'/'clear' oder eigenes {default_ret,by_nr})"),
    ToolSpec("native.devirt",           "M", "Native Studio", "O", (), "Devirtualizer: OLLVM-geflachte Funktion (JNI_OnLoad/onSearchResult/getOptionsList/addr) entflachen -> Handler-Sequenz + Bloecke + Nachfolger-Graph"),
    ToolSpec("native.devirt_static",    "M", "Native Studio", "O", (), "Statischer Devirtualizer: Dispatch-Graph einer Funktion ohne Lauf (1D-madd 0x134f48 + 2D-smaddl-Vtable 0x137740) -> Bloecke/Branch-Points/Traps"),
    ToolSpec("native.os_attach",        "M", "Native Studio", "O", (), "Env/Syscall-Substrat (N2) anhaengen: gefaelschte /proc/self/maps + VFS + konvergierter Syscall-Pfad (Handler/Policy/Default) -> Funktion unter kontrollierter Umgebung laufen lassen, Env/Syscall-Summary + survived + JNI (WS-E3-Steuerflaeche)"),
    ToolSpec("native.os_summary",       "M", "Native Studio", "O", (), "Read-only: letzte OS-Substrat-Summary (coverage/trace/net/threads/signals/auxv/policy) der letzten os_attach-Session lesen. Zwei Policy-Ebenen: NR-keyed Seed-Policy (syscall_policy_get/set) vs. OS-9 namens-keyed allow/deny/stub (os_attach syscall_policy=)"),
    ToolSpec("native.os_config_get",    "M", "Native Studio", "O", (), "Read-only: geteilten OS-Inspektor-Zustand (config-Delta + letzte Summary + seq/writer) lesen (Mensch<->KI)"),
    ToolSpec("native.os_config_set",    "M", "Native Studio", "G", ("os_write",), "OS-Inspektor-config setzen (writer=ki, Clobber-Schutz base_seq): Policy/Overrides/Replay/Seed/Trace/VFS. Mensch zieht in der GUI mit '⬇ OS←KI' nach (Freigabe os_write)"),
    ToolSpec("native.devirt_hybrid",    "M", "Native Studio", "O", (), "Hybrid-Devirt: statischer Dispatch-Graph ⊕ getracter Branch-Zustand (ein Lauf mit dispatch_trace+survive) -> loest Branch-Points/Traps auf; Coverage + Provenienz je Block"),
    ToolSpec("native.dalvik_run",       "M", "Native Studio", "O", (), "J1: Smali (.class/.method) parsen + eine Dalvik-Methode ausfuehren (Register-VM: Opcodes/Objektmodell/Intra-Invoke) -> Ergebnis + registrierte Methoden + Opcode-Coverage + nicht aufgeloeste Aufrufe"),
    ToolSpec("native.dex_run",          "M", "Native Studio", "O", (), "J3/MS-4: DEX laden + eine Methode ausfuehren (DalvikVM + lazy Klassen-Loader/<clinit> + android.*-Stubs) -> Ergebnis + Opcode-Coverage + nicht aufgeloeste Dalvik-/Framework-Aufrufe"),
    ToolSpec("native.jdbg_run",         "M", "Native Studio", "O", (), "X1/MS-6: Smali-Methode unter dem glaesernen Java-Debugger ausfuehren (skriptbasiert): Breakpoints (pc) beobachten + optional Register am 1. Treffer mutieren -> Beobachtungen + Timeline-Laenge + Ergebnis (Mutationseffekt sichtbar)"),
    ToolSpec("native.app_lifecycle_run", "M", "Native Studio", "O", (), "Ganze App am Lifecycle-Einstieg starten (Zone original|patched, lazy Callees)"),
    ToolSpec("native.glass_run",         "M", "Native Studio", "O", (), "Glaeserner App-Lauf: Lifecycle durch Java-Debugger mit View-/Netz-/Clock-Recorder; virtual_clock, net_mode, record_timeline, persist_session (Opt-In), action_points, net_replay, seam_profile -> View, NetLog, AppState"),
    ToolSpec("native.glass_state",       "M", "Native Studio", "O", (), "Read-only: aktuellen Live-Zustand des Glass-Debuggers (Mode, Call-Stack, Seam/Net/Clock-Config, AppState, Timeline, Missing) lesen"),
    ToolSpec("native.glass_clock",       "M", "Native Studio", "P", (), "Virtuelle Uhrzeit & Datumsvorgabe (ISO/Epoch/Zeitzone/Freeze/Shift/Reset) lesen oder setzen (Headless + Live-GUI)"),
    ToolSpec("native.glass_inspect",     "M", "Native Studio", "O", (), "Tiefen-Inspektion des Glass-Laufs: App-State (SharedPrefs/SQLite/VFS), Missing, Seams, Timeline, Statics/Heap & Befund"),
    ToolSpec("native.glass_control",     "M", "Native Studio", "P", (), "Steuerkommando an Live-GUI oder Headless-Session senden (start_app, reset_appdata, clear_appdata, set_persist_session, pause, stop, cont, step, step_over, step_out, reverse_step, goto, set_virtual_clock, set_net_mode, add_net_mock, set_options, add_action_point, generate_befund, open_browser, toggle_pw_mask)"),
    ToolSpec("native.glass_fork",        "M", "Native Studio", "P", (), "Parallele Glass-Instanzen (Forks) und Projekt-Snapshots verwalten (list|fork|merge|discard|save_project|load_project)"),
    ToolSpec("native.net_log",          "M", "Native Studio", "O", (), "Read-only: NetLog (HTTP-Requests/Responses) des letzten native.glass_run lesen"),
    ToolSpec("native.webview_inspect",  "M", "Native Studio", "O", (), "In-Phone-Browser / WebView-DOM-Inspektor: App-URLs entdecken oder URL/HTML-Asset abrufen & parsen"),
    ToolSpec("native.ui_snapshot",      "M", "Native Studio", "O", (), "Read-only (gläsern): UI-Momentaufnahme des letzten native.glass_run - Paint-Masse, sichtbare Texte, Szenegraph-Knoten (cls/text/bounds/clickable)"),
    ToolSpec("native.ui_view",          "M", "Native Studio", "O", (), "Gläsern/multimodal: zeigt der KI die aktuelle simulierte App-GUI als echtes PNG-Bild (ImageContent) samt sichtbaren Texten & nummerierten Tap-Koordinaten (Live-GUI + Glass-Run). Args: annotate?, scroll_y?, source?"),
    ToolSpec("native.ui_detect",        "M", "Native Studio", "O", (), "Gläsern/read-only: erkennt das UI-Toolkit einer entpackten App (klassisch/Compose/React Native/Hybrid Web/NativeScript/Flutter) und nennt den passenden Render-Produzenten. Args: studio_root?"),
    ToolSpec("native.ui_render",        "M", "Native Studio", "O", (), "Gläsern: rendert die zuletzt gläserne UI (lebende Surface oder last_ui.json) zu PNG - am Ende kommt echte Grafik raus. Args: path?, title?"),
    ToolSpec("native.ui_tap",           "M", "Native Studio", "O", (), "Gläsern + manipulierbar: Tap (x,y) auf die zuletzt gläserne UI zurueckspielen, persistiert neue Momentaufnahme. Args: x, y"),
    ToolSpec("native.ui_key",           "M", "Native Studio", "O", (), "Gläsern + manipulierbar: Tastentext an die fokussierte Surface geben. Args: text"),
    ToolSpec("native.workflow",        "M", "Native Studio", "O", (), "Workflow ausfuehren (open_lib/run_fn/devirt/gate2/jni) -> einheitliches Ergebnis + next_hints"),
    ToolSpec("native.workflow_meta",   "M", "Native Studio", "O", (), "Workflow-Registry (Rail + Formularfelder) fuer die GUI"),
    ToolSpec("native.session_get",     "M", "Native Studio", "O", (), "Geteilten Native-Studio-Zustand ansehen: Lib + Eingaben + letztes Ergebnis (read-only)"),
    ToolSpec("native.session_set",     "M", "Native Studio", "G", ("session_write",), "Geteilten Native-Studio-Zustand setzen (Lib/Eingaben, optional Workflow ausfuehren) + zurueckschreiben (Freigabe session_write)"),
    ToolSpec("native.recipe_list",     "M", "Native Studio", "O", (), "Gespeicherte Workflow-Rezepte lesen (Name + Workflow + Params)"),
    ToolSpec("native.recipe_run",      "M", "Native Studio", "O", (), "Gespeichertes Rezept ausfuehren (sein Workflow mit den gespeicherten Params)"),
    ToolSpec("native.plugins",         "M", "Native Studio", "O", (), "Entdeckte native_plugins/ auflisten: Namen + deklarative GUI-Beitraege (read-only)"),
    ToolSpec("native.annotations_get", "M", "Native Studio", "O", (), "Annotationen (eigene Namen/Notizen je Adresse) einer Lib lesen (read-only)"),
    ToolSpec("native.annotate",        "M", "Native Studio", "P", (), "Eigenen Namen/Notiz fuer eine Adresse setzen (geteilt mit der GUI-Outline)"),

    # --- N: LibCarve (Native Decompile & Patch Studio) ---
    ToolSpec("libcarve.libs",             "N", "LibCarve", "O", (), "Alle .so-Libs der App (APK + Self-Cache) samt ELF-Infos listen"),
    ToolSpec("libcarve.outline",          "N", "LibCarve", "O", (), "Funktions-Outline (JNI/Exports/Init/sub_) & Imports einer .so"),
    ToolSpec("libcarve.search",           "N", "LibCarve", "O", (), "RAM-Index durchsuchen (Symbol, String, ASM-Kante, Wildcard-AOB)"),
    ToolSpec("libcarve.inspect_func",     "N", "LibCarve", "O", (), "Komplettkontext einer Funktion: ARM64-Disasm, Pseudo-/Ghidra-C, Bytes, Annotationen"),
    ToolSpec("libcarve.decompile",        "N", "LibCarve", "O", (), "Ghidra-C-Decompilierung lesen/starten (cached oder run=true)"),
    ToolSpec("libcarve.xrefs",            "N", "LibCarve", "O", (), "Eingehende Code- und Reloc-XREFs auf eine Adresse/Funktion"),
    ToolSpec("libcarve.callgraph",        "N", "LibCarve", "O", (), "Ausgehende Calls & Call-Hierarchy einer Funktion"),
    ToolSpec("libcarve.datagraph",        "N", "LibCarve", "O", (), "Lese-/Schreib-Zugriffe auf Strings/Globals/Relocs"),
    ToolSpec("libcarve.match_pattern",    "N", "LibCarve", "O", (), "Wildcard-AOB-Signatur trocken gegen eine .so matchen"),
    ToolSpec("libcarve.session_get",      "N", "LibCarve", "O", (), "Aktuellen GUI/KI LibCarve-Arbeitsfokus & Entwurf lesen"),
    ToolSpec("libcarve.assemble_preview", "N", "LibCarve", "P", (), "ARM64-ASM oder C-Cave trocken kompilieren (Bytes/NOP-Check)"),
    ToolSpec("libcarve.patch_list",       "N", "LibCarve", "O", (), "Aktive LibCarve-Patches der laufenden Session listen"),
    ToolSpec("libcarve.patch_add",        "N", "LibCarve", "P", (), "LibCarve-Patch (ASM/C-Cave/Hex + AOB) zur aktiven Session hinzufuegen"),
    ToolSpec("libcarve.patch_update",     "N", "LibCarve", "P", (), "Aktiven LibCarve-Patch aktualisieren"),
    ToolSpec("libcarve.patch_remove",     "N", "LibCarve", "P", (), "Aktiven LibCarve-Patch aus der Session entfernen"),
    ToolSpec("libcarve.cave_save",        "N", "LibCarve", "P", (), "Wiederverwendbare C-Cave-Vorlage ablegen"),
    ToolSpec("libcarve.annotate",         "N", "LibCarve", "P", (), "Symbol/Funktion in LibCarve umbenennen/kommentieren"),
    ToolSpec("libcarve.gui_focus",        "N", "LibCarve", "P", (), "GUI-LibCarve auf Lib/VAddr fokussieren + optional Entwurf stagen"),
    ToolSpec("libcarve.session_set",      "N", "LibCarve", "G", ("session_write",), "Geteilten LibCarve-KI-Sync-Zustand mit seq-Schutz schreiben"),
    ToolSpec("libcarve.compile",          "N", "LibCarve", "G", ("build",), "Gepatchte .so kompilieren nach data/libcarve/ (gated)"),
]

GROUP_ORDER = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N"]
GROUP_NAMES = {s.group: s.group_name for s in TOOL_CATALOG}
CAP_KEYS = ["build", "flash", "app_start", "exec_run", "file_manager", "delete_ops", "dbg_write", "session_write", "os_write"]

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
