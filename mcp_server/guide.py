"""
guide — Bedienungsanleitung des RE-Framework-MCP fuer KI-Clients.

EINE Quelle fuer beides: FastMCP `instructions` (automatisch beim Client) und
das Tool `mcp.guide` (on demand). Kurz halten — der Text liegt beim Client im Kontext.
"""

AGENT_GUIDE = """\
RE-Patch-Framework — MCP-Bedienung (fuer KI-Clients)

ZWECK: Zugriff auf ein Android-RE-Framework (LibForge native Libs, Smali-Analyse,
Build-Pipelines, Geraet/ADB, Logs) fuer das LibreLinkUp-Glukose-RE-Projekt.

BETRIEBSMODELL — WICHTIG:
- Du BEREITEST VOR und BEOBACHTEST. Der NUTZER baut, flasht und startet selbst in der GUI.
- Nimm nie an, dass eine genehmigungspflichtige Aktion gelaufen ist. Nach Vorbereitung
  gib dem Nutzer eine klare Klick-Liste fuer die GUI.

GOLDENE REIHENFOLGE:
1. workspace.status  -> APP_PACKAGE + adb-State pruefen.
2. Workspace MUSS vorbereitet sein (Nutzer, GUI): frischen Pull vom Handy zuerst
   ENTPACKEN — App Manager -> App importieren, dann Workspace -> PREPARE_WORKSPACE
   (Merge + Decompile). OHNE diesen Schritt gibt es KEINEN Smali-Baum -> smali.* meldet
   "kein Smali". Die Strategie bestimmt den Zielordner: MANIFEST_STRATEGY="apkeditor"
   -> source/<pkg>/base_unpacked_apkeditor, sonst -> base_unpacked_apktool. smali.* waehlt
   den passenden Ordner automatisch; smali.index_status zeigt Strategie + genutzten Ordner.
3. Analyse: smali.index_status, smali.search (Substring; regex=true fuer Regex;
   n. Treffer via max_results), smali.read. Findet die Suche nur die Klasse/Datei:
   smali.methods listet alle Methoden/Felder (wie 'Datei'-Reiter), smali.method extrahiert
   EINE Methode exakt (.method..end) -> der `block` ist der `orig` fuer einen sauberen
   Method-Scope-Favoriten (uebersichtlicher als ganze Klasse).
4. Native Libs vorbereiten: lib.create + lib.update (nur C-Quelle/Felder). Der BUILD
   ist gated -> passiert in der GUI (LibForge).
5. Patches vorbereiten & verwalten: NEUE Klasse (z. B. GHOST_NET) via smali.create_class
   -> legt einen Favoriten mit Patch-Typ 'new_file' an (schreibt KEINE Source-Datei; die
   Klasse wird beim Build in die Destination geschrieben; in der GUI anwenden). Patch-Saetze als Favoriten verwalten:
   favorites.list/get/add/update (add/update nehmen {name, patches:[...]} mit Typen
   smali|hex|lib_replace, multi-patch; Einzel-Patch-Kurzform erlaubt), favorites.delete
   (gated). Der Nutzer wendet Favoriten in der GUI an. (SessionState-Patches sind RAM-only
   -> immer ueber Favoriten arbeiten.) patch.evaluate = Dry-Run vor dem Ablegen.
   Weitere Analyse/Beobachtung: smali.xref (Aufrufer finden), api.query/api.get
   (MITM-Traffic-DB), history.list/history.add (Befunde sichern = 'Session permanent sichern').
6. Beobachten: console.live_tail / console.main_tail. Filter: include (OR; match="all"=AND),
   exclude (NOT), since_last_clear (nur seit letztem Leeren), last_minutes, n (letzte Zeilen).
   Logcat-Favoriten: log.settings (lesen), log.add_logcat (ablegen).

TIERS & GATING:
- P (Prepare) und O (Observe) sind frei.
- G (Gated: bauen/flashen/starten, File Manager, loeschen) braucht: Pro-Tool-Schalter AN
  + Kategorie (caps) AN + confirm=true. Diese Schalter setzt der Nutzer in der GUI-Seite
  "MCP". Wird ein G-Tool verweigert, liefert es die GUI-Anleitung -> weitergeben, NICHT
  wiederholt versuchen. mcp.capabilities zeigt, was gerade frei ist.

7. Frida (ueber MCP steuerbar): frida.config_get/set (Betriebsmodi listen|connect|script|
   script_directory; App-Start via pause_on_load; Netz host/port; script_directory_path),
   frida.build_toggle (INJECT_FRIDA an/aus). Skripte: frida.scripts_list/get/add/update/delete
   + frida.set_active (= Build-Ziel). Collections: frida.collections_list/add/update/delete.
   frida.push_collection = Collection aufs Geraet (gated: file_manager+confirm). Frida-Skripte
   & Collections tragen wie Patches version+description (Pflicht bei add).

VERSIONIERUNG (Libs, Patch-Favoriten & Frida) — PFLICHT:\n- Jede Lib und jeder Favorit hat version (Freitext, z.B. 1.0) + description (Zweck); die\n  Version wird in die description gespiegelt ([vX] ...). Bei create/add sind beide Pflicht.\n- BUGFIX / selber Pfad -> lib.update / favorites.update, dabei Version bumpen (z.B. 1.0->1.1)\n  UND description aktualisieren. Ohne Bump warnt das Tool, speichert aber (warn-but-allow).\n- NEUER Pfad/Ansatz -> NICHT ueberschreiben, sondern lib.create / favorites.add (neuer Eintrag,\n  eigene Version); das Original bleibt unangetastet.\n\nGOTCHAS:
- Kein Handy verbunden? adb-Aktionen scheitern ("Geraet offline") — statische Analyse
  (smali.*) geht trotzdem.
- smali.search hat ein Zeitbudget; bei "Zeitbudget erreicht" praeziser suchen.
- Alles wird auditiert: mcp.audit_tail.

8. NATIVE STUDIO (native.*) — STATISCHE Analyse nativer AArch64-Libs (.so):
   Voraussetzung: eine ENTSCHLUESSELTE .so im Analyse-Arbeitsbereich.
   - native.targets: gepackte Libs im APK finden (APK-Pfad aus workspace.status ->
     APP_SOURCE_DIR, z. B. source/<pkg>/*.apk).
   - native.unpack (P): entschluesseln via entdecktem Plugin (apk=<apk> entry=<lib/..> ODER
     so_path=<gepackte .so>) -> out_path der .dec.so.
   - native.status: was ist schon entschluesselt/indiziert/dekompiliert (keine Doppelarbeit).
   Analyse mit path=<out_path einer .dec.so>: native.load (ELF-Info), native.index (XREF/
   CallGraph, einmalig teuer, danach Cache), native.search (Symbol/String/Adresse),
   native.xref (wer referenziert 0xADDR), native.disasm (ab Symbol/Adresse), native.read_bytes
   (vtable/.rodata/Zeiger), native.callgraph (Caller/Callees). native.decompile liefert NUR
   GECACHTES Ghidra-C; fehlt es -> in der GUI '🧊 C' erzeugen (langer Ghidra-Lauf, bewusst
   nicht ueber MCP). native.functions listet Funktionsanfaenge (benannte + sub_), native.strings scannt ASCII-Strings (Krypto-Namen), native.run EMULIERT eine Funktion (Args/Pointer/Buffer/Stubs -> Register/ret/Out-Puffer), native.run_break emuliert bis Breakpoint(e) am Block-Eintritt (Pause-Zustand pc/Register), native.lift hebt einen Basisblock/eine Funktion nach Python (bbsrc Block->Python-Lifter), native.verify prueft eine Emulation gegen einen gespeicherten Golden (Bit-Exakt-Regression; update=True speichert), native.rebase bildet Adressen/Symbole ueber zwei Lib-Versionen ab (Offsets nach App-Update neu ziehen; mode='diff' fuer Versions-Diff); native.coverage berichtet Coverage (Mnemonics), Slow-Pfad-Kandidaten und unbekannte Instruktionen eines Laufs; native.snapshot (P) speichert den Emu-Zustand nach einem Lauf bzw. laedt einen Snapshot und fuehrt ab entry aus; native.resolveptr loest einen Reloc/Zeiger statisch auf (addr=) oder listet Relocs um eine Adresse (around=) — unverzichtbar bei obfuskiertem indirektem Dispatch (Zeiger in der Datei NULL, erst per Reloc gefuellt); die Emu-Tools (run/run_break/lift/verify/coverage/snapshot) akzeptieren relocs=true -> Relocations werden beim Laden angewendet (Dispatch-/Call-Zeiger gefuellt); native.run_break liefert bei Pause eine session=, mit der native.step (ein Basisblock) und native.resume (bis naechsten Breakpoint/Ende) interaktiv weiterlaufen; native.decompile run=True startet Ghidra nicht-blockierend. native.dbg_state/dbg_lift lesen die mit der GUI geteilte Debug-Sitzung (secrets/probe/out/dbg): dbg_state zeigt pc/Trace/Breakpoints/writer, dbg_lift liftet den gelaufenen Pfad nach Python (read-only; in der GUI vorher ⬆ An KI). native.dbg_step/dbg_resume (G, Freigabe 'dbg_write' + confirm) treiben die geteilte Sitzung weiter und schreiben zurueck (writer=ki), mit Clobber-Schutz ueber seq (schreibt NICHT, wenn die GUI inzwischen weiter ist). Statische Analyse + Emulation; Laufzeit/Hooking am Geraet deckt frida.* ab.

9. LIBCARVE (libcarve.* — Decompile & Patch Studio fuer bestehende .so-Libs):
   - libcarve.libs -> listet .so-Dateien (APK + Self-Cache); libcarve.outline / libcarve.search -> Funktionen, Strings, Wildcard-AOBs.
   - libcarve.inspect_func / libcarve.xrefs / libcarve.callgraph / libcarve.datagraph -> vollstaendige Funktionsanalyse.
   - libcarve.assemble_preview -> DRY-RUN (ARM64-ASM oder C-Cave) mit Byte-Budget-Pruefung VOR libcarve.patch_add!
   - libcarve.patch_add -> fuegt Patch inkl. Wildcard-AOB zur aktiven Session hinzu (sofort in GUI + zone='live').
   - libcarve.gui_focus / KI-Sync: gui_focus fokussiert die GUI und stagt einen Entwurf in data/ki_exchange/workspace_sync.json.
10. ZERO-BUILD SIMULATOR (native.glass_run zone='live') & STATISCHE ENGINE-API-EXTRAKTOREN:
   - zone='live' fuehrt aktive Smali-, LibForge-, LibCarve- und Hex-Patches im RAM/VFS 2.0 ohne APK-Build aus!
   - Virtuelle Uhr / Datumsvorgabe: native.glass_clock (op=get|set|shift|reset) oder virtual_clock= in native.glass_run steuert Dalvik-Zeit-APIs, ARM64 clock_gettime & Statusleiste gemeinsam.
   - Debugger-Steuerung & Tiefen-Inspektion: native.glass_control (auch reset_appdata/clear_appdata/set_persist_session/reverse_step/goto/set_net_mode/add_net_mock/set_options/generate_befund; target='gui'|'headless'|'both'), native.glass_inspect (app_state/prefs/sqlite/missing/seams/timeline/heap/befund).
   - api.extract_engine_static scannt statische APIs fuer Hybrid-Web, React Native/Hermes, Flutter & Web-Assets; api.extract_flutter_static spezialisiert auf Flutter; api.import_templates + api.update/replay/clear verwalten api_traffic.db.
11. SIMULIERTE APP-GUI, WEBVIEW, MANIFEST & WORKSPACE:
   - native_ui_view liefert der KI den aktuellen Screen (laufende Desktop-GUI oder letzter native_glass_run) direkt als echtes PNG-ImageContent + visible_texts + interactive_elements (tap_xy). Mit annotate=true erscheinen [1], [2], ... Nummernschilder direkt auf den Klickzielen.
   - native_ui_tap(x, y, forward_to_gui=true) / native_ui_key(text, forward_to_gui=true) sowie native_glass_control(cmd='ui_scroll'|'open_browser'|'toggle_pw_mask') steuern auch die laufende Desktop-GUI fern.
   - native.webview_inspect entdeckt App-URLs & inspiziert HTTP/HTML-Assets im DOM-Parser.
   - app.list_local / app.import / app.pull_apk (gated) / workspace.renew_id / app.manifest_info / smali.callgraph / patch.fuzzy_match / lib.import_so / lib.set_active / logcat.capture / app.start+app.stop (gated: app_start) schliessen die Studio-Paritaet.
"""
