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
"""
