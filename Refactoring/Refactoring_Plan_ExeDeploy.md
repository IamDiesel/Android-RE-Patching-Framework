# ExeDeploy — Refactoring-/Feature-Plan (LibForge Executable Runner)

> Erweiterung des RE-Frameworks um **Deployen, Ausführen, Sync und Aufräumen eigener
> nativer Executables** (PIE-Binaries wie `skb_oracle`) direkt aus dem Framework —
> ohne Root, konsequent über `adb` + `run-as`. Ergänzt LibForge (das bisher nur
> *Shared Libraries* per Smali-`loadLibrary` einschleust) um den fehlenden
> **Executable-Pfad**.

Status: Entwurf v1 · Autor: Claude · Ziel-Repo: `Android RE Patching Framework`
Basis: **CR-001** (`claude/CR-001-executable-deploy-run.md`) — dieser Plan setzt CR-001 um
und erweitert ihn um **Local↔Remote-Sync mit lokaler Backup-Versionierung** und
**farbige, getaggte Konsolenausgabe** (`[<exe>] …`).

---

## 1. Konzept

LibForge kann heute native `.so`-Libraries bauen und per Smali-`loadLibrary` in die App
injizieren (`OUTPUT_KIND = shared`). Für ein **eigenständiges Binary** (`OUTPUT_KIND = executable`,
z. B. das Weg-3-Orakel `skb_oracle`) ist das der falsche Mechanismus: Es soll nicht *geladen*,
sondern **deployt und ausgeführt** werden.

**ExeDeploy** schließt diese Lücke mit fünf Bausteinen, konsequent entlang der bestehenden
Architektur:

1. **Runner (Service):** Push von Binary + Runtime-Deps in ein **exec-erlaubtes** Verzeichnis
   (Default `/data/local/tmp`), `chmod 755`, geräteinternes **Staging** von App-Home-Eingaben
   (`run-as cat > tmp`, kein PC-Umweg), **nicht-blockierende Ausführung** (parallel zur App
   möglich) und **Cleanup**.
2. **Farbige, getaggte Konsole:** stdout **und** stderr laufen live in die bestehende
   farbcodierte Konsole — jede Zeile mit Tag `[<exe>] …` und Farbe (out/err unterscheidbar,
   optional pro Executable eine eigene Farbe für parallele Läufe).
3. **Local↔Remote-Sync (Service):** bidirektionaler Abgleich zwischen einem lokalen Ordner
   und einem Remote-Ziel (Deploy-Dir oder App-Home), mit **lokaler versionierter Sicherung**
   vor jedem Überschreiben (Rollback möglich).
4. **Filemanager-Erweiterung:** Upload mehrerer Dateien **und** ganzer Ordner (rekursiv) —
   Gegenstück zum bereits vorhandenen rekursiven Download; wiederverwendet für Deploy/Staging.
5. **Typ-Verzweigung in der UI:** `OUTPUT_KIND = executable` zeigt ein **„Deploy & Run"-Panel**
   statt des „Aktiv/Inaktiv"-Inject-Schalters; `shared` bleibt unverändert.
6. **Ziel-Auswahl:** ein **Dropdown der kompilierten Executable-Targets** (aus
   `NativeLibManager`, `output_kind == "executable"`) bestimmt, welches Binary deployt/ausgeführt
   wird — in beiden Bedien-Orten (siehe 7).
7. **Zweiter Bedien-Ort + zwei Filemanager-Shortcuts:** Die Runner-Steuerung (Ziel-Dropdown +
   Deploy&Run/Stop/Cleanup) erscheint **auch im Reiter „🚀 App Start & Live-Log"**
   (`LauncherLoggerTab`) — wo die farbige Konsole ohnehin sitzt. Dazu **zwei Shortcut-Buttons**
   in den File Explorer: **„📂 Executable-Ordner"** (Run-Dir, shell-Domain) und
   **„📁 App-Ordner"** (App-Home der Ziel-App, `run-as`). Beide Bedien-Orte teilen sich
   **einen** Controller (kein doppelter Code).

---

## 2. Architektur-Einordnung (bestehende Paradigmen)

| Paradigma im Framework | Nutzung durch ExeDeploy |
|---|---|
| **MVC** (View ↔ Controller ↔ Service) | **zwei** Views (Deploy&Run-Panel im `LibForgeTab` **und** Runner-Zeile im `LauncherLoggerTab`) → **ein** gemeinsamer `ExecRunController` → `DeviceExecService` / `DeviceSyncService` (DRY: eine Logik, zwei Bedien-Orte) |
| **SOA / stateless Services** | `DeviceExecService` (neu), `DeviceSyncService` (neu, Vorbild: bestehender `frida_sync_service`) |
| **`CommandRunner`-Kapselung** | `run_background`/Popen für den **nicht-blockierenden** Run + Live-Stream; `run_blocking` für Deploy/Stage/Cleanup |
| **EventBus (Pub/Sub)** | neues Event `EXEC_OUTPUT` (strukturiert: exe/stream/text); Konsole abonniert wie `GHOST_LOG_LINE` |
| **Farbige Konsole via `tag_configure`** | neue Tags `exec_out`/`exec_err` (+ optional Palette pro exe) in `launcher_logger_tab` — analog zu `frida_log`/`ghost_log` |
| **Persistenz `data/*.json` + Manager** | Run-Profil an `NativeLib` (in `native_libs.json`); Backups unter `data/exec_backups/` |
| **Command-Pattern über `config.json`** | keine neue Pipeline-Stufe nötig (Runner ist interaktiv), aber neue Default-Keys im `config_manager` |
| **Auto-Bootstrap / `run-as`-Bridge** | Wiederverwendung der `device_file_service`-Primitive (`push`→tmp→`run-as cp`, base64-`run-as cat`) |

ExeDeploy **ersetzt nichts**: `InjectAddedLibsStep` (shared-Libs) und der Filemanager-Download
bleiben unverändert; der Runner koexistiert.

---

## 3. Anforderungsdefinition

### 3.1 Funktionale Anforderungen
- **F1 Executable-Deploy:** Push von Binary + wählbaren Runtime-Deps in das **Run-Dir**
  (Default `/data/local/tmp`), anschließend `chmod 755` auf das Binary.
- **F2 Input-Staging (geräteintern):** ausgewählte App-Home-Dateien per
  `run-as <pkg> cat <src> > <run_dir>/<name>` ins Run-Dir kopieren — **ohne PC-Roundtrip**;
  Mehrfachauswahl.
- **F3 Ausführen (nicht-blockierend):** Start mit konfigurierbarem `argv`, `env`
  (Default `LD_LIBRARY_PATH=<run_dir>`) und `cwd` (Default `<run_dir>`) via Popen; läuft
  **parallel zur App** und zu anderen Runs.
- **F4 Live-Ausgabe farbig + getaggt:** stdout **und** stderr zeilenweise in die bestehende
  Konsole; jede Zeile als `[<exe>] <text>`; Farbe unterscheidet out/err, optional je exe
  eine eigene Farbe.
- **F5 Stop:** laufenden Run beenden — lokalen Popen **und** den Remote-Prozess auf dem Gerät
  (sonst läuft das Binary nach dem Kappen der adb-Shell weiter).
- **F6 Cleanup:** Ein-Klick-Entfernen von Binary, Deps und gestagten Eingaben aus dem Run-Dir.
- **F7 Ergebnis-Rückholung (optional, explizit):** definierte Output-Dateien per `adb pull`
  in den lokalen Build-/Zielordner holen (enthält ggf. Klartext → nur auf Aktion).
- **F8 Typ-Verzweigung:** `OUTPUT_KIND = executable` → „Deploy & Run"-Panel;
  `OUTPUT_KIND = shared` → bestehender Inject-Schalter.
- **F9 Konfigurierbares Run-Dir:** pro Target einstellbar, Default `/data/local/tmp`, mit
  **Validierung**, dass das Verzeichnis exec-erlaubt ist (App-Home kann keine Binaries `execve`n).
- **F10 Default-Profil „skb_oracle":** Deps/Inputs/Args/Env vorbelegt (siehe §6).
- **F11 Multi-/Rekursiv-Upload (Filemanager):** Upload mehrerer Dateien **und** ganzer Ordner
  (rekursiv) — für `run-as`-Ziele **und** `/data/local/tmp`.
- **F12 Local↔Remote-Sync:** bidirektionaler Abgleich lokaler Ordner ↔ Remote-Verzeichnis
  (Push/Pull), Auswahl der Richtung.
- **F13 Backup-Versionierung (lokal):** vor jedem überschreibenden Sync/Deploy Snapshot der
  betroffenen lokalen Dateien nach `data/exec_backups/<target>/<UTC-Zeitstempel>/`; die letzten
  **N** Stände werden behalten (Default N=10), ältere werden beschnitten.
- **F14 Executable-Auswahl:** ein Dropdown listet alle kompilierten Executable-Targets
  (`NativeLibManager`, `output_kind == "executable"`); die Auswahl bestimmt Deploy-/Run-Ziel.
  Verfügbar in beiden Bedien-Orten (F15); die zuletzt gewählte Auswahl wird gemerkt.
- **F15 Zweiter Bedien-Ort (Live-Log-Reiter):** die Runner-Steuerung (Ziel-Dropdown +
  `🚀 Deploy & Run` / `⏹ Stop` / `🧹 Cleanup`) erscheint **auch** im Reiter „🚀 App Start &
  Live-Log" (`LauncherLoggerTab`). Beide Bedien-Orte delegieren an denselben
  `ExecRunController` — identisches Verhalten, kein duplizierter Code.
- **F16 Zwei Filemanager-Shortcuts:** zwei Buttons öffnen den File-Explorer-Reiter und navigieren
  direkt zum jeweiligen Ort:
  - **„📂 Executable-Ordner"** → **Run-Dir** der deployten Executable (Default `/data/local/tmp`).
    Da dieses Verzeichnis zur **shell-Domain** gehört, erhält der Filemanager einen
    **Shell-Domain-Browse-Modus** (`adb shell ls`, ohne `run-as`).
  - **„📁 App-Ordner"** → **App-Home** der Ziel-App (`/data/data/<pkg>/`, `run-as` — bestehender Modus).

### 3.2 Nicht-funktionale Anforderungen
- **N1 Kein Root:** ausschließlich `adb` + `run-as`, niemals `su`.
- **N2 Datenschutz:** Capture-/Schlüsseldateien (`decrypt.*`, `cdfe*`, `unwrap*`, `ctx*`) bleiben
  auf dem Gerät; Staging ist geräteintern. Kein automatischer PC-Upload — F7/Pull nur explizit.
- **N3 Robustheit:** Fehler von `adb`/`run-as` (Gerät weg, App nicht debuggable, „exec denied",
  Ziel nicht exec-erlaubt) werden klar in der Konsole gemeldet — **kein stiller Fehlschlag**.
- **N4 Kein UI-Freeze:** Deploy/Run/Sync in Daemon-Threads; Ausgabe über EventBus.
- **N5 Idempotenz:** erneutes Deploy überschreibt sauber; Run und Cleanup sind wiederholbar.
- **N6 Binärkompatibilität:** PIE + 16-KB-Align (bereits durch LibForge/`native_compiler` gesetzt);
  `libc++_shared.so` muss zur NDK-Build-Version passen (r27c).

---

## 4. Datenmodell

### 4.1 Run-Profil am `NativeLib` (in `native_libs.json`)
Kein zweiter Store — die Executable-spezifischen Felder hängen am bestehenden `NativeLib`
(nur relevant, wenn `output_kind == "executable"`):

```python
# Ergänzungen in core/domain/native_models.py (NativeLib)
run_dir: str = "/data/local/tmp"     # exec-erlaubtes Zielverzeichnis (Default)
run_args: str = ""                   # argv nach dem Binary, z.B. "./libb11bb8.so"
run_env: str = "LD_LIBRARY_PATH=/data/local/tmp"
run_cwd: str = "/data/local/tmp"
runtime_deps: list = []              # lokale Pfade, die mitdeployt werden (.so-Dump, libc++_shared.so)
stage_inputs: list = []              # App-Home-Pfade, die geräteintern ins run_dir gestaged werden
pull_globs: list = []                # optionale Output-Dateien für F7 (adb pull)
cleanup_after: bool = False          # nach dem Run automatisch aufräumen
```

### 4.2 Sync-/Backup-Modell
```python
# DeviceSyncService – Aufrufparameter (kein persistenter State im Service selbst)
sync(local_dir, remote_dir, direction="push"|"pull", pkg=None, keep=10)
#   pkg=None  -> Remote ist shell-Domain (/data/local/tmp) via adb push/pull
#   pkg set   -> Remote ist App-Home via run-as (base64-Bridge)
```

**Backup-Layout (lokal, versioniert):**
```
data/
  exec_backups/
    <target-oder-syncpaar>/
      2026-09-17T07-40-12Z/     # Snapshot der betroffenen lokalen Dateien vor Überschreiben
        <datei…>
      2026-09-17T08-05-33Z/
      …                          # letzte N Stände; ältere werden beschnitten
```

### 4.3 Neue Config-Defaults (`config_manager.DEFAULT_CONFIG`)
```python
"EXEC_RUN_DIR_DEFAULT": "/data/local/tmp",
"EXEC_BACKUP_KEEP": 10,
"EXEC_COLOR_PER_EXE": True,   # True = eigene Farbe je Executable, False = fixe out/err-Farben
```

---

## 5. Komponenten (neue & berührte Dateien)

### Neu
| Datei | Zweck |
|---|---|
| `services/device_exec_service.py` | `DeviceExecService`: `deploy()`, `stage_inputs()`, `run()` (Popen, Live-Stream), `stop()`, `cleanup()`, `pull_result()` |
| `services/device_sync_service.py` | `DeviceSyncService`: `sync(push/pull)` + lokale Backup-Versionierung; Vorbild `frida_sync_service` |
| `ui/controllers/exec_run_controller.py` | **gemeinsamer** Controller für beide Bedien-Orte: `select_target()`, `deploy_and_run()`, `stop_run()`, `cleanup()`, `pull_result()`, `sync()`, `open_in_filemanager()` (alle threaded) |
| `ui/panels/exec_run_panel.py` | wiederverwendbares „Deploy & Run"-Widget (Ziel-Dropdown, Args/Env/Deps/Inputs/Run-Dir, Buttons) — eingebettet **sowohl** im `LibForgeTab` **als auch** im `LauncherLoggerTab` |

### Berührt
| Datei | Änderung |
|---|---|
| `core/domain/native_models.py` | Run-Profil-Felder (§4.1) |
| `services/native_lib_service.py` | speichert/liest die neuen Felder (bestehendes `save/load` deckt Dataclass automatisch ab; ggf. Helfer `get_executables()`) |
| `ui/tabs/lib_forge_tab.py` | `OUTPUT_KIND`-Verzweigung: `executable` → `ExecRunPanel` statt Inject-Schalter; Panel spricht `ExecRunController` an |
| `ui/controllers/lib_forge_controller.py` | delegiert die Executable-Bedienung an den neuen `ExecRunController` (keine eigene Runner-Logik) |
| **`ui/tabs/launcher_logger_tab.py`** | (a) `EXEC_OUTPUT` abonnieren; Tags `exec_out`/`exec_err` (+ Palette pro exe); `[<exe>]`-Präfix. (b) **F15:** `ExecRunPanel` als Steuer-Zeile einhängen. (c) **F16:** Buttons „📂 Executable-Ordner" + „📁 App-Ordner" |
| `core/infrastructure/config_manager.py` | neue Keys (§4.3) + Safety-Check (additiv einfügen, bestehende Config nicht überschreiben) |
| `services/device_file_service.py` | **F11:** `push_files()` / `push_dir()` (rekursiv). **F16:** `list_dir_shell()` (`adb shell ls -ln`, **ohne** `run-as`) für exec-Verzeichnisse wie `/data/local/tmp` |
| `ui/controllers/device_file_manager_controller.py` | `upload_file` → `askopenfilenames` (mehrere) + neuer `upload_dir` (`askdirectory`, rekursiv); **F16:** `open_at(path, domain)` (Domain `run-as`/`shell`) für den Shortcut |
| `ui/tabs/device_file_manager_tab.py` | Buttons „📤 Upload (mehrere)" + „📁 Ordner hochladen"; **F16:** Domain-Umschaltung (App-Home ↔ shell/`/data/local/tmp`) |
| `gui.py` / `ui/tabs/workspace_tab.py` | Shortcut-Verdrahtung: File-Explorer-Tab selektieren (`notebook.select(app.device_file_tab)`) und `open_at(run_dir, "shell")` aufrufen |
| `README.md` | Modul „4.9 ExeDeploy (Executable Runner + Sync)" + Filemanager-Upload/Shell-Modus dokumentieren |

> Hinweis: `native_compiler_service` erzeugt das `executable` (PIE, 16-KB-Align) bereits —
> hier ist **keine** Änderung nötig (nur `output_kind` wird schon gesetzt).

---

## 6. Runner-Ablauf (adb + run-as, kein Root)

**Deploy (shell-Domain):**
```
adb push <build>/skb_oracle        <run_dir>/skb_oracle
adb push <ndk>/libc++_shared.so    <run_dir>/libc++_shared.so
adb push <dump>/libb11bb8.so       <run_dir>/libb11bb8.so
adb shell "chmod 755 <run_dir>/skb_oracle"
```
**Staging App-Home → Run-Dir (geräteintern):**
```
adb shell 'for f in <inputs…>; do run-as <pkg> cat /data/data/<pkg>/$f > <run_dir>/$f; done'
```
(Die Umleitung `>` führt die äußere shell-Domain aus; `run-as … cat` liefert nur stdout — App-Home-Material wandert ins Run-Dir, ohne den PC zu berühren.)

**Run (nicht-blockierend, Live-Stream):**
```
adb shell 'cd <run_cwd> && <run_env> ./<exe> <run_args>'
```
→ Popen (`CommandRunner.run_background`); ein Daemon-Thread liest zeilenweise und
`EventBus.publish("EXEC_OUTPUT", {"exe": <name>, "stream": "out"|"err", "text": line})`.

**Stop (F5):** lokalen Popen beenden **und** `adb shell "pkill -f <exe>"` (sonst Waise auf dem Gerät).

**Cleanup (F6):**
```
adb shell 'cd <run_dir> && rm -f <exe> <deps…> <inputs…> <pull_globs…>'
```

**Default-Profil „skb_oracle"** (vorbelegt, aus CR-001 §6):
- Deps: `libb11bb8.so` (Dump), `libc++_shared.so` (NDK r27c)
- Inputs (App-Home): `cdfe1.bin cdfe2.bin unwrap0.bin unwrap0.nonce unwrap1.bin unwrap1.nonce ctx.bin ctx.base decrypt.ct decrypt.iv decrypt.tag decrypt.pt`
- Args: `./libb11bb8.so` · Env: `LD_LIBRARY_PATH=/data/local/tmp` · Cwd: `/data/local/tmp`

---

## 7. Konsolenausgabe — farbig + getaggt

**Mechanik (paradigmentreu, wie `ghost_log`):**
- `DeviceExecService.run()` publiziert je Zeile `EXEC_OUTPUT` mit `{exe, stream, text}`.
- `launcher_logger_tab` abonniert `EXEC_OUTPUT`, formatiert `[<exe>] <text>` und wählt den Text-Tag:
  - `exec_err` (stderr) → Rot (`#E06C75`, wie `error_log`).
  - `exec_out` (stdout) → Grün (`#98C379`).
  - Bei `EXEC_COLOR_PER_EXE=True`: stabile Farbe je Executable aus einer kleinen Palette
    (Hash des Namens → Palette-Index), damit **parallele** Läufe optisch trennbar sind;
    stderr bleibt immer rot.
- Neue `tag_configure`-Einträge analog zu den bestehenden (`frida_log`/`ghost_log`).

Beispiel:
```
[skb_oracle] [P2] GetInstance -> rc=0x5203ed4e (SKB_OK)
[skb_oracle] [P6] *** BYTE-IDENTISCH — Weg 3 GESCHLOSSEN & VERIFIZIERT ***
[libtrace]   [SK2] Glukose-Key erkannt
```

---

## 8. UI-Spezifikation

Zentrales, wiederverwendbares Widget: **`ExecRunPanel`** (in beide Bedien-Orte eingebettet,
8.1 und 8.4), gesteuert vom gemeinsamen `ExecRunController`.

### 8.1 LibForge — „Deploy & Run"-Panel (nur `OUTPUT_KIND=executable`)
- **Ziel-Auswahl (F14):** Dropdown der Executable-Targets aus `NativeLibManager.get_executables()`.
- **Run-Dir** (Default `/data/local/tmp`, editierbar, exec-Validierung).
- **Args**, **Env** (Default `LD_LIBRARY_PATH=<run_dir>`), **Cwd**.
- **Runtime-Deps**: Liste lokaler Dateien (Hinzufügen per Filedialog, mehrfach).
- **Stage-Inputs**: Liste von App-Home-Pfaden (Auswahl über den bestehenden Filemanager-Browser).
- **Buttons:** `🚀 Deploy & Run`, `⏹ Stop`, `🧹 Cleanup`, `⬇ Ergebnis holen` (F7), `🔄 Sync`,
  `📂 Executable-Ordner`, `📁 App-Ordner` (beide F16).
- **Status-Label** + Live-Ausgabe in der (farbigen) Konsole.
- Für `OUTPUT_KIND=shared` bleibt alles wie bisher (Inject-Schalter).

### 8.1b Reiter „🚀 App Start & Live-Log" — Runner-Zeile (F15)
- Dasselbe `ExecRunPanel` (kompakt) als zusätzliche Zeile im `LauncherLoggerTab`, direkt über/unter
  der Konsole — so lässt sich das Binary genau dort starten, wo die farbige Ausgabe erscheint,
  **parallel** zum laufenden App-Start/Logcat.
- Enthält Ziel-Dropdown (F14) + `🚀 Deploy & Run` / `⏹ Stop` / `🧹 Cleanup` + `📂 Executable-Ordner` / `📁 App-Ordner`.
- Identisches Verhalten wie 8.1 (gemeinsamer Controller); Auswahl ist zwischen beiden Orten synchron
  (zuletzt gewähltes Target, F14).

### 8.1c Filemanager-Shortcuts (F16) — zwei Buttons
- **`📂 Executable-Ordner`** → `notebook.select(app.device_file_tab)` + `open_at(<run_dir>, domain="shell")`.
  Im Shell-Modus blendet der Filemanager die `run-as`-Paketauswahl aus und listet per
  `adb shell ls` (neuer `list_dir_shell`).
- **`📁 App-Ordner`** → `notebook.select(app.device_file_tab)` + `open_at("/data/data/<pkg>/", domain="run-as")`
  (bestehendes Verhalten, `<pkg>` = `APP_PACKAGE`).

### 8.2 Filemanager — Multi-/Rekursiv-Upload (F11)
- „📤 Upload (mehrere)" → `askopenfilenames`.
- „📁 Ordner hochladen" → `askdirectory`, rekursiver Walk; Zielstruktur wird auf dem Gerät nachgebaut.
- Fortschrittsanzeige analog zum bestehenden Download-Progress.

### 8.3 Sync-Panel (F12/F13)
- Lokaler Ordner ↔ Remote-Ziel (Run-Dir oder App-Home via `pkg`), Richtung Push/Pull.
- `🔄 Sync`-Button; vor Überschreiben automatisch lokale versionierte Sicherung (§4.2).
- Status/Log in der Konsole.

---

## 9. Sync & Backup-Versionierung (Detail)

- **Push (lokal→remote):** vor dem Push Snapshot der lokalen Quelldateien nach
  `data/exec_backups/<paar>/<ts>/` (so ist reproduzierbar, *was* deployt wurde). Transfer:
  Run-Dir → `adb push`; App-Home → `run-as cp` über den `/data/local/tmp`-Zwischenschritt
  (bestehende `push_file`-Bridge).
- **Pull (remote→lokal):** vor dem Überschreiben lokaler Dateien Snapshot der **lokalen**
  Zielstände nach `data/exec_backups/<paar>/<ts>/` (Rollback). Transfer: Run-Dir → `adb pull`;
  App-Home → `run-as base64` (bestehende `pull_file`-Bridge).
- **Remote-Listing:** Run-Dir via `adb shell ls -ln`; App-Home via `run-as … find` (wie
  `resolve_files_for_download`).
- **Retention:** letzte `EXEC_BACKUP_KEEP` Stände behalten, ältere Ordner löschen.
- **Datenschutz (N2):** Pull sensibler Capture-Dateien nur explizit; kein Auto-Pull.

---

## 10. Edge Cases & Risiken

- **Exec-Verzeichnis:** nur exec-erlaubte Pfade (Default `/data/local/tmp`); App-Home (`/data/data/...`)
  kann keine Binaries ausführen (W^X/SELinux) → UI-Validierung + klare Meldung.
- **App muss debuggable sein** (für `run-as`-Staging) — im aktuellen Setup gegeben.
- **Stop-Semantik:** adb-Shell kappen beendet den Remote-Prozess nicht zwingend → zusätzlich
  `pkill -f <exe>`; Rückmeldung, falls kein Treffer.
- **base64-Overhead** beim `run-as`-Transfer großer App-Home-Dateien (Deploy ins Run-Dir nutzt
  schnelleres `adb push`).
- **Parallele Läufe:** je Run eigener Popen + eigener Tag/Farbe; Cleanup nur auf bekannte Namen.
- **Pfade mit Leerzeichen (Windows):** alle Pfade gequotet.
- **libc++_shared.so-Version** muss zum NDK (r27c) passen — sonst Loader-Fehler.
- **Backup-Speicher:** Retention begrenzt Plattenverbrauch; großer Dumps (`libb11bb8.so` ~13 MB)
  → Backups nur für Quelldateien, nicht für jeden Deploy dupliziert (nur bei Änderung).

---

## 11. Umsetzungsplan (Phasen)

- **Phase 0 – Sofort-Workaround (kein Code):** die adb-Kommandos aus §6 einmalig manuell fahren
  → P6-Verifikation entkoppeln (siehe CR-001 Anhang A).
- **Phase 1 – Runner-Kern (MVP):** `device_exec_service` (deploy/stage/run/stop/cleanup) +
  `EXEC_OUTPUT` farbig/getaggt + minimaler „Deploy & Run"-Button. Deckt F1–F6, N1–N5.
  *Akzeptanz:* Ein Klick deployt+staged+führt `skb_oracle` aus; Konsole zeigt live
  `[skb_oracle] … *** BYTE-IDENTISCH ***`.
- **Phase 2 – UI & Config:** `OUTPUT_KIND`-Verzweigung, `ExecRunPanel` + gemeinsamer
  `ExecRunController`, Ziel-Dropdown (F14), zweiter Bedien-Ort im Live-Log-Reiter (F15),
  Run-Profil persistent (F8/F9/F10),
  Felder Args/Env/Deps/Inputs, Default-Profil „skb_oracle", neue Config-Keys + Safety-Check.
- **Phase 3 – Filemanager:** Multi-/Rekursiv-Upload (F11), Shell-Domain-Browse + `open_at`-Shortcut (F16), Ergebnis-Pull (F7).
- **Phase 4 – Sync & Backup:** `device_sync_service` + lokale Versionierung (F12/F13) + Sync-Panel.
- **Phase 5 – Politur:** Fehlerbilder/Statusanzeige, Palette pro exe, optionaler Auto-Cleanup.

Nach **Phase 1** ist die P6-Verifikation im Framework möglich (früh verifizierbar).

---

## 12. Entscheidungen (Vorschlag) & offene Punkte

**Vorschlag (aus der Abstimmung übernommen bzw. empfohlen):**
- **Runner-Architektur:** neuer `device_exec_service`, der `device_file_service`-Primitive
  wiederverwendet (klare SOA-Trennung Exec vs. File).
- **Run-Dir:** konfigurierbar, Default `/data/local/tmp`, mit exec-Validierung.
- **Ausführung:** nicht-blockierend (Popen) + Stop, parallel-fähig.
- **Persistenz:** Run-Profil am `NativeLib` (kein zweiter Store).
- **Konsole:** neues `EXEC_OUTPUT`-Event, `[<exe>]`-Tag, Grün=stdout / Rot=stderr,
  optional Farbe pro Executable.
- **Sync:** MVP Push + Pull mit lokaler versionierter Sicherung (Default N=10).

**Offen (bitte bestätigen):**
1. **Backup-Retention N** — Default 10 ok, oder anderer Wert / unbegrenzt?
2. **Farbe pro Executable** (Palette) gewünscht, oder feste out/err-Farben genügen?
3. **Sync-Richtung Default** — Push (lokal→Gerät) als Standard ok?
4. **Ort des Plans/Backups** — Plan liegt unter `Refactoring/Refactoring_Plan_ExeDeploy.md`,
   Backups unter `data/exec_backups/` — passt das?
5. **Filemanager-Shortcut-Ziel** — Standard „Run-Dir" (`/data/local/tmp`, shell-Domain) korrekt,
   oder soll der Shortcut primär die **App-Home** der Ziel-App (`run-as`) öffnen?
