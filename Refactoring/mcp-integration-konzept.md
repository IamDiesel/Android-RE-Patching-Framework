# MCP-Integration ins RE-Patch-Framework — Konzept & Interface-Liste

*Interne KB-Notiz. Stand: September 2026. Grundlage: gelesener Quelltext (`core/`, `services/`, `ui/`, `gui.py`, `config.json`). Ziel: das Framework über MCP **vorbereitbar & beobachtbar** machen; bauen/flashen/starten, Datei- und Löschaktionen bleiben genehmigungspflichtig; **jede Funktion einzeln abschaltbar**, **jede Ausführung wird protokolliert**, **Server aus der GUI steuerbar**. Getestet am Vault-Beispiel. Autor: IamDiesel + Claude.*

---

## 1. Was das Framework architektonisch ist (verinnerlicht)

**MVC + Service-orientiert, Event-getrieben, Command-Pattern-Pipeline.**

| Schicht | Ort | Rolle |
|---|---|---|
| **View** | `gui.py`, `ui/tabs/*`, `ui/controllers/*` | Tkinter, abonniert EventBus |
| **Pipeline-Engine** | `core/pipeline/engine.py` | `type` → `PipelineStep.execute()`, braucht nur `cfg` + Callback |
| **Services** | `services/*` | zustandslos / Manager-basiert, melden via EventBus |
| **Infrastructure** | `core/infrastructure/*` | `ConfigManager`, `CommandRunner`, `ToolManager` |
| **Application** | `core/application/*` | `EventBus` (Pub/Sub), `SessionState` — **der Seam** |
| **Domain** | `core/domain/*` | `NativeLib`, `FridaConfig`, `PatchConflictException` |

**Paradigmen:** EventBus (Pub/Sub), Command-Pattern-Pipeline (JSON in `config.json`), ConfigManager (Single Source of Truth), CommandRunner + „nur adb + run-as, niemals su".

**Persistenz = prozessübergreifende Übergabe:** `native_libs.json`, `favorite_patches.json`, `config.json`. `SessionState` ist RAM-only → Patches werden als **Favoriten** vorbereitet, der Nutzer wendet sie in der GUI an.

---

## 2. Betriebsmodell (vom Nutzer festgelegt)

> **MCP = „Vorbereiten & Beobachten"-Cockpit. Der Nutzer baut, flasht, startet selbst in der GUI.** Kein unsichtbares Hintergrund-Bedienen.

- **Frei (ohne Genehmigung):** lesende Analyse (Smali-Suche, XRefs, Dateien lesen), Vorbereitung (Lib-Quelle, Favoriten, Config-Flags, Dry-Run), Beobachtung (Logs, Log-Einstellungen, API-Inspector, **Log-Export**).
- **Genehmigungspflichtig:** Bauen (LibForge-Build, `BUILD_NATIVE`), Flashen, Starten (Apps/Executables), **File Manager** (jeder Geräte-Dateizugriff, analog App-Start), **Löschen** (Patches/Favoriten, Libraries, Executables — nur auf Anfrage).
- Nach jeder Vorbereitung liefere ich eine präzise **Klick-Liste** für die GUI.

---

## 3. Berechtigungs-, Audit- & Server-Modell

Alles in einem Block in `config.json`, gepflegt über die neue GUI-Seite **„🔌 MCP"**:

```json
"MCP_SETTINGS": {
    "server": {
        "enabled": false,          // Server läuft?
        "autostart": false,        // beim GUI-Start automatisch hochfahren
        "host": "127.0.0.1",
        "port": 8765,
        "transport": "http"        // lokaler HTTP-Server -> GUI besitzt den Lebenszyklus
    },
    "require_confirm_each_call": true,
    "log_export_dir": "Claude outputs/logs",
    "audit": {
        "enabled": true,           // NICHT abschaltbar für gated Tools (nur Detailgrad)
        "file": "data/mcp_audit.jsonl",
        "log_args": true
    },
    "tools": {                     // JEDE Funktion einzeln an/aus (autoritativ)
        "workspace.status": true,
        "smali.search": true,
        "lib.build": false,
        "pipeline.flash": false,
        "device.pull": false
        /* … ein Eintrag pro Tool; Default: O/P = true, G = false … */
    },
    "caps": {                      // zusätzliche Kategorie-Gates für gated Tools
        "build": false, "flash": false, "app_start": false,
        "exec_run": false, "file_manager": false, "delete_ops": false
    }
}
```

**Zwei-Ebenen-Gate pro Aufruf (`gating.py`):**
1. **Pro-Tool-Schalter** `tools["<id>"]` — ist die einzelne Funktion überhaupt an? (Standard: P/O an, G aus.)
2. Für **gated** Tools zusätzlich: `server.enabled` **und** die Kategorie `caps["<cap>"]` **und** `confirm=true`.
→ Fehlt eines: Aktion **verweigert**, stattdessen exakte **GUI-Anleitung / adb-Befehl** — und der Versuch wird trotzdem **auditiert**.

**Audit-Log (Pflicht, `audit.py`):** jede Tool-Ausführung schreibt eine JSONL-Zeile:
`{ts, tool, tier, decision: allowed|denied|hint, confirm, args_summary, result: ok|error, duration_ms, error?}`. Datei = `audit.file`. Über die MCP-Seite live einsehbar; per `mcp.audit_tail` lesbar; von `log.export` mitgenommen.

**MCP-Settings-Seite (`ui/tabs/mcp_settings_tab.py`), Inhalt:**
- **Server-Verwaltung:** Start / Stop / Restart, Status-LED (läuft? PID? Port? seit wann?), Host/Port-Feld, „Autostart"-Schalter, „Server-Log ansehen".
- **Pro-Tool-Matrix:** alle Tools gruppiert (A–K), je eine Checkbox; „Gruppe an/aus" als Komfort; gated Tools optisch markiert.
- **Kategorie-Gates** (`caps.*`) + „confirm pro Aufruf".
- **Audit-Viewer:** Live-Tail des `mcp_audit.jsonl`, Button „Audit exportieren".
- **Log-Export-Pfad.**

---

## 4. Technische Umsetzung

**Headless MCP-Server als zweite „View", vom Framework-GUI als lokaler HTTP-Prozess verwaltet.**

```
Claude (MCP-Client) ──HTTP──▶ 127.0.0.1:8765
                                   │
Framework-GUI ──startet/stoppt──▶ mcp_server/  (eigener Prozess)
   (MCP-Settings-Seite)              ├─ server.py          FastMCP (streamable-http)
                                     ├─ registry.py        Tool-Registry (id, tier, cap)
                                     ├─ app_context.py     cfg + native_lib_mgr + engine (kein Tk)
                                     ├─ eventbus_bridge.py sammelt LOG_INFO/EXEC_OUTPUT je Aufruf
                                     ├─ gating.py          liest MCP_SETTINGS (tools+caps+confirm)
                                     └─ audit.py           JSONL-Protokoll jeder Ausführung
                                           │ importiert          │ subscribe()
                                     core/ + services/      core/application/EventBus (unverändert)
```

- **D1** kein GUI-Treiben. **D2** EventBus-Bridge als Seam. **D3** `AppContext` statt `tk.Tk`. **D4** Streams simpel als `*.capture(seconds)`. **D5** Single-Flight für mutierende Aktionen. **D6** zentrales `gating.py`. **D7** `mcp_server/` neu (keine Änderung an `core/`/`services/`). **D8** Transport = **lokaler streamable-HTTP** (127.0.0.1) → die GUI besitzt Start/Stop; Prozess-Isolation (Server-Absturz betrifft die GUI nicht). **D9** GUI-Änderung: neue Settings-Seite + Notebook-`add()` in `gui.py`. **D10** **Audit** quer über alle Tools (Decorator in der Registry) — kein Tool ohne Protokolleintrag. **D11** Server-Prozess-Management via `subprocess` aus der GUI (`python -m mcp_server.server`), Status über PID/Port-Ping.

---

## 5. MCP-Interface-Liste (getiert, jede Funktion einzeln schaltbar)

**P = Prepare (frei)** · **O = Observe (frei)** · **G = Gated** (`tools[id]` + `caps` + confirm). **★ = MVP**.

### A — Workspace & Config *(P/O)*
- **A1 `workspace.status`** ★ O · **A2 `workspace.set_config`** ★ P · **A3 `app.import`** P · **A4 `workspace.prepare_hint`** ★ O

### B — Analyse / Smali Studio *(O)*
- **B1 `smali.index_status`** ★ · **B2 `smali.search`** ★ · **B3 `smali.xref`** · **B4 `smali.read`** ★ · **B5 `smali.callgraph`** *(später)*

### C — LibForge vorbereiten *(P; Build/Delete = G)* — **Element 1: hwkey**
- **C1 `lib.list`** ★ P · **C2 `lib.create`** ★ P · **C3 `lib.update`** ★ P · **C4 `lib.import_so`** P · **C5 `lib.build`** ★ G(`build`) · **C6 `lib.set_active`** P · **C7 `lib.delete`** G(`delete_ops`)

### D — Patches als Favoriten *(P; Delete = G)* — **Element 2: GHOST_NET + Boot-Patch**
- **D1 `favorites.list`** ★ P · **D2 `favorites.add`** ★ P · **D3 `patch.evaluate`** ★ O · **D4 `favorites.delete`** G(`delete_ops`)

### E — Bauen & Flashen *(G / Anleitung)*
- **E1 `build.hint`** ★ O · **E2 `pipeline.run`** G(`build`) · **E3 `pipeline.flash`** G(`flash`)

### F — File Manager (Gerät) *(G — analog App-Start, `file_manager`)*
- **F1 `device.info`** ★ O *(reine Info, frei)* · **F2 `device.ls`** ★ G · **F3 `device.pull`** ★ G · **F4 `device.vault_bundle`** ★ G · **F5 `device.push`** G · **F6 `device.delete`** G(`file_manager`+`delete_ops`)

### G — Executables (ExeDeploy) *(G)*
- **G1 `exec.list`** O · **G2 `exec.deploy_run`** G(`exec_run`) · **G3 `exec.pull_result`** G(`file_manager`) · **G4 `exec.delete`** G(`delete_ops`)

### H — Capture, Logs & Export *(O)*
- **H1 `ghostlog.capture`** ★ O · **H2 `logcat.capture`** O · **H3 `log.settings`** O · **H4 `log.export`** ★ O · **H5 `frida.*`** *(Legacy)*

### I — DAST / API-Inspector *(O; Geräteschreiben = G)*
- **I1 `api.query`** ★ O · **I2 `net.push_cert`** G(`file_manager`) · **I3 `net.route`** G(`file_manager`) · **I4 `proxy.start/stop`** O/G

### J — Historie / Reporting *(P/O)*
- **J1 `history.list`** O · **J2 `history.add`** P

### K — MCP-Selbstauskunft *(O)*
- **K1 `mcp.capabilities`** O — welche Tools/Caps sind aktuell freigeschaltet? · **K2 `mcp.audit_tail`** O — letzte Audit-Einträge lesen.

---

## 6. MVP-Pfad = Vault-Beispiel (Prepare & Observe; du baust/flashst/startest)

1. `workspace.status` → `workspace.set_config` (Frida aus, debuggable an).
2. `lib.create` + `lib.update` (hwkey-Quelle) → `lib.list`. **(Element 1)**
3. `smali.search "ResponseBody->string()"` → `favorites.add` (GHOST_NET + `loadLibrary("hwkey")` + `ExportGCViaHWHook`) → `patch.evaluate`. **(Element 2)**
4. `build.hint` → **du in der GUI:** Favoriten anwenden → hwkey bauen → `BUILD_NATIVE` → flashen.
5. **du in der GUI:** Kaltstart + Login + Glukose-Screen. Dann `ghostlog.capture` → *(mit `file_manager`+confirm)* `device.vault_bundle` → `log.export`.
6. **PC-seitig, lokal:** `vault_independence_probe` — **Element 3**, kein Gerät, kein MCP-Tool.

Jeder ausgeführte Aufruf steht anschließend im Audit-Log.

---

## 7. Baureihenfolge

1. **GUI-Erweiterung + Config:** `MCP_SETTINGS`-Defaults in `config_manager.py`; neue Seite **„🔌 MCP"** (`ui/tabs/mcp_settings_tab.py`) mit **Server-Verwaltung (Start/Stop/Restart/Status)**, **Pro-Tool-Matrix**, Kategorie-Gates, **Audit-Viewer**, Log-Export-Pfad; `add()` in `gui.py`.
2. **Server-Gerüst:** `mcp_server/` mit `registry.py` (Tool-Metadaten: id/tier/cap), `app_context.py`, `eventbus_bridge.py`, `gating.py`, `audit.py`, `server.py` (FastMCP streamable-http).
3. **Prozess-Management:** GUI startet/stoppt `python -m mcp_server.server` (subprocess), Status via Port-Ping/PID.
4. **MVP-Tools (O/P):** A1/A2/A4, B1–B4, C1–C4/C6, D1–D3, H1/H3/H4, F1, K1/K2 — jede mit Audit-Decorator.
5. **Gated-Tools:** zunächst Anleitung; echte Ausführung erst nach Freischaltung des jeweiligen Pro-Tool-Schalters **und** der Kategorie in der MCP-Seite.

## 8. Offene Punkte
1. `device.vault_bundle` zusätzlich als „Vault Export"-Pipeline-Aktion? *(Kandidat `libforge-feature-plan`.)*
2. Server-Registrierung als lokaler HTTP-MCP im Desktop-Client (URL `http://127.0.0.1:8765`).
3. Audit-Rotation (max. Größe / Tagesdatei)?

## 9. Umsetzungsstand

**Schritt ① — GUI-Seite + Config + Server-Verwaltung: FERTIG & verifiziert (21.09.2026).**
Neue Dateien im Repo: `mcp_server/__init__.py`, `mcp_server/registry.py` (46 Tools / 11 Gruppen / 15 gated), `mcp_server/server.py` (Platzhalter-HTTP-Server), `services/mcp_server_service.py` (Start/Stop/Restart/Status via subprocess). Geänderte Dateien: `core/infrastructure/config_manager.py` (`MCP_SETTINGS`-Defaults + `_ensure_mcp_settings()` tiefe Merge in `_update_paths`), `gui.py` (Tab „🔌 MCP" eingehängt), `ui/tabs/mcp_settings_tab.py` (Server-Verwaltung + Pro-Tool-Matrix + Caps + Audit-Viewer + Log-Export). Backups: `*.bak_mcp_<ts>`.
Verifiziert: py_compile aller Dateien; `ConfigManager` merged `MCP_SETTINGS` beim Laden; Registry-Auflösung (O=an, G=aus); Platzhalter-Server Start/GET/Stop.
Offen für den Nutzer: Framework starten, Tab „🔌 MCP" öffnen, Server Start/Stop testen, Toggles + Speichern prüfen. *(Autostart beim GUI-Start ist als Präferenz speicherbar, aber noch nicht beim Launch verdrahtet → Schritt ③.)*

**Schritt ② — FastMCP-Server + MVP-Tools: FERTIG & verifiziert (21.09.2026).**
Neue Module: `mcp_server/app_context.py` (Tk-freier Kontext: cfg+libs+engine), `eventbus_bridge.py` (LogCollector-Seam), `gating.py` (Zwei-Ebenen-Gate), `audit.py` (JSONL-Protokoll + tail), `runtime.py` (einheitliche Ausführungshülle), `tools.py` (MVP-Logik, frei von der `mcp`-Abhängigkeit), `server.py` (echter FastMCP, streamable-http, `--selftest`). `requirements.txt` um `mcp` ergänzt. UI: MCP-Seite auf 3 balancierte Spalten.
MVP-Tools verdrahtet: workspace.status/set_config/prepare_hint, smali.index_status/search/read, lib.list/create/update, favorites.list/add, patch.evaluate, device.info, ghostlog.capture, log.export, mcp.capabilities/audit_tail.
Verifiziert (VM gegen echtes Repo): py_compile aller Module; `--selftest` baut AppContext + liefert korrekte Capability-Map (O/P an, 15×G aus); Gating-Kette (Pro-Tool → caps → confirm) in allen 4 Kombinationen; Audit schreibt allowed/denied; `smali.index_status` findet den echten Smali-Baum.
Offen für den Nutzer: `pip install mcp` (oder Framework neu starten → `main.py` installiert aus `requirements.txt`), dann in der MCP-Seite Server starten und den Endpoint `http://127.0.0.1:8765/mcp` im Desktop-Client als MCP-Server registrieren.

**Anschlussweg (wichtig, 21.09.2026):** Zwei Transporte, bewusst getrennt.
- **HTTP (streamable-http, GUI-verwaltet):** läuft — vom Nutzer getestet auf `127.0.0.1:8770` (uvicorn). Für lokale/manuelle Nutzung und künftige lokale Clients.
- **stdio (für die Claude-Desktop-App):** der eigentliche Anschlussweg, damit die Tools in der Claude-Session erscheinen. Remote-Connectors über claude.ai verbinden von Anthropics Servern aus und erreichen `127.0.0.1` NICHT; lokale Server laufen in der Desktop-App als **stdio** und werden über die Geräte-Bridge als `mcp__remote-devices__<server>__*` durchgereicht. Deshalb `server.py --stdio` + Eintrag in `claude_desktop_config.json` (`command`=python, `args`=[-m, mcp_server.server, --stdio], `cwd`=Repo). Gating/Audit lesen weiterhin `MCP_SETTINGS` aus `config.json` — die MCP-Seite steuert also auch die stdio-Instanz.

**VERBUNDEN & LIVE (21.09.2026):** stdio-Server über `claude_desktop_config.json` angebunden — alle 17 MVP-Tools erscheinen als `mcp__remote-devices__re-framework__*`. Erster echter Aufruf `workspace.status` erfolgreich (Live-Config aus dem Framework). Zwei Fixes dabei: (a) Client startet den Server NICHT mit Repo-cwd → `server.py` legt jetzt die Repo-Wurzel selbst auf `sys.path` und wird per **absolutem Skriptpfad** (statt `-m`) gestartet; Startfehler landen in `data/mcp_server_stdio.log`. (b) `smali.search` lief in den 60-s-Bridge-Timeout → jetzt Substring auf Dateiebene (C-Speed) + Zeitbudget.

**Analyse-Befund (Vault-Beispiel):** `NetworkingModule` liegt unverschleiert vor, aber **okhttp ist in diesem Build geshaded/umbenannt** — keine `okhttp3/ResponseBody`-Referenz, kein `->string()Ljava/lang/String;`. Die GHOST_NET-Injektionsstelle ist also NICHT der naive `ResponseBody->string()`; die echte Response-Lese-Stelle muss per MCP-Analyse gefunden werden (nächster Schritt). *Hinweis: Läuft der stdio-Server, muss die Desktop-App ihn neu starten, damit Code-Änderungen an tools.py greifen.*

**Konsolen-Spiegel (21.09.2026):** Bestätigt — beide GUI-Konsolen sind EventBus-getrieben: Main Console (Workspace) = `LOG_INFO`; „Start & Live-Log (App+Exe)" = `LOGCAT_LINE`+`LOG_INFO`+`GHOST_LOG_LINE`+`EXEC_OUTPUT` (+`PROXY_LOG` im API-Inspector). Neuer `services/console_mirror_service.py` (in `gui.py` früh verdrahtet) spiegelt sie additiv nach `data/console_main.log` und `data/console_live.log` (512 KB-Cap, rotierend). Zusätzlich zwei MCP-Tools `console.main_tail`/`console.live_tail` (Stufe O; jetzt 48 Tools). Verifiziert: korrekte Aufteilung der Events auf beide Dateien. → Ich lese die Konsolen live via Bridge (`data/console_*.log`) oder via MCP.
Aktivierung: GUI neu starten (lädt den Spiegel) + Desktop-App neu starten (lädt schnellere smali.search + console-Tools).

**Filter & Logcat-Favoriten (21.09.2026):** Der Live-Log-Reiter filtert per Include (OR)/Exclude (NOT) (`_check_log_filters`), der Logcat-Aufruf ist als Favorit in `data/logger_profiles.json` editierbar (`ProfileManagerService`, Gruppen `intents`/`logcats`, Platzhalter `{PID}`/`{APP_NAME}`/`{APP_PACKAGE}`). Dafür jetzt: `console.main_tail`/`console.live_tail` mit `include`/`exclude`/`match` (any=OR wie GUI, all=AND); `log.settings` (O, Favoriten lesen); `log.add_logcat` (P, Favorit ablegen). Jetzt **49 Tools**. Verifiziert (VM): OR/AND/NOT-Filter + Favoriten-Lesen/-Schreiben.

**Konsolen-Spiegel v2 (21.09.2026):** Limit jetzt **20 MB** (Default) und konfigurierbar über `MCP_SETTINGS.console.max_bytes`; `clear_on_app_start` (Default an). Leeren-Verhalten: „Anzeige leeren" (Live) und „Konsole leeren" (Main) publizieren `CONSOLE_CLEARED` → der Spiegel schreibt einen `=== GELEERT … ===`-Marker (Historie bleibt). App-Start und die zwei Settings-Buttons publizieren `CONSOLE_CLEAR_HARD` → Datei wird wirklich truncatet. Lese-Tools `console.*_tail` haben jetzt zusätzlich `since_last_clear` (nur seit letztem Marker) und `last_minutes` (nur letzte X Minuten via `[HH:MM:SS]`), plus `n` (letzte X Zeilen) und include/exclude/match. Verifiziert (VM): Marker, Hard-Clear, since_last_clear, last_minutes, 20-MB-Limit. Geänderte Framework-Dateien: `config_manager.py` (console-Default), `gui.py` (Mirror bekommt `cfg`), `launcher_logger_tab.py` (clear+app-start), `workspace_tab.py` (main clear), `mcp_settings_tab.py` (Größe/Autostart/Leeren-Buttons).

**Nächste Schritte:** Desktop-App neu starten (lädt Tool-Params) + GUI neu starten (Spiegel v2 + Clear-Events); dann Injektionsstelle analytisch finden (smali.search nach dem geshadeten Response-Pfad in `NetworkingModule`); danach ③ Autostart (HTTP) + Gated-Tools (Stufe G).

*Querverweise: `android-re-framework-overview.md`, `komplett-anleitung-framework.md`, `libforge-feature-plan.md`.*
