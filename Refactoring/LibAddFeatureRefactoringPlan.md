# LibForge — Feature- & Refactoring-Plan (Native Lib Builder)

> Erweiterung des RE-Frameworks um das **Erstellen, Kompilieren, Importieren, Verwalten und
> Injizieren eigener nativer Bibliotheken** — als Ergänzung zum bestehenden „Native Lib Replacer".
> Das **Laden** der Lib bleibt bewusst manuell (Smali-Patch); das Framework liefert dafür
> den fertigen Snippet.

Status: Entwurf v2 (nach Abstimmung) · Autor: Claude · Ziel-Repo: `Android RE Patching Framework`
Name final: **LibForge**

---

## 1. Konzept

Heute kann das Framework native Libs nur **ersetzen** (`InjectCustomLibsStep`: sucht eine
existierende `.so` in der APK und überschreibt sie) oder den Frida-Gadget einschleusen.
Es fehlt der Weg, eine **komplett neue** `.so` in die APK zu bringen — z. B. unsere
`libhook`/`libglucose` (Inline-Hook auf `adcskb_gcm_decrypt`).

**LibForge** schließt diese Lücke mit vier Bausteinen, konsequent entlang der bestehenden
Architektur:

1. **Editor + Compiler (UI + Service):** Neuer Reiter neben „Native Lib Replacer" mit
   C-Code-Editor (Syntax-Highlighting), Namensvorgabe, Beschreibung und „Compile"-Button →
   kompiliert via NDK-`clang` zu `lib<name>.so`. Build-Fehler erscheinen in der Konsole.
2. **Import (ohne Quellcode):** Eine **fertige `.so`** kann per **Filedialog** importiert werden —
   ganz ohne Quellcode/Build-Schritt.
3. **Verwaltung (Manager-Service + JSON):** Libs werden persistent gehalten
   (`data/native_libs.json` + `data/native_libs/<id>/`), sind **aktiv/inaktiv** schaltbar
   (ohne Löschen), lassen sich **explizit löschen** (Button **oder „Entf"-Taste** → entfernt `.so`
   + Quelle + Eintrag), tragen eine Beschreibung und zeigen Code **parallel** zur Lib.
   **Recompile überschreibt** die bestehende `.so`.
4. **Toolchain-Integration (Pipeline-Step):** Neuer Step `inject_added_libs` kopiert alle
   **aktiven** Libs beim Build zusätzlich in die APK. Laden erfolgt manuell per Smali-Patch —
   LibForge zeigt/kopiert den passenden `System.loadLibrary`-Snippet.

---

## 2. Architektur-Einordnung (bestehende Paradigmen)

| Paradigma im Framework | Nutzung durch LibForge |
|---|---|
| **MVC** (View ↔ Controller ↔ Service) | `LibForgeTab` (View) → `LibForgeController` → `NativeLibManager` / `NativeCompilerService` |
| **SOA / stateless Services** | `NativeCompilerService` (Compile), NDK-Auflösung über erweiterten `ToolManager` |
| **Persistenz als `data/*.json` + Manager** | `NativeLibManager` analog zu `FridaManager` (`frida_scripts.json`) |
| **Domain-Model als `@dataclass`** | `NativeLib` analog zu `FridaScript` |
| **Pipeline = Command-Pattern über `config.json`** | neuer Step `inject_added_libs` in `BUILD_NATIVE` |
| **EventBus + `CommandRunner.run_live`** | Live-Build-Log in die bestehende Konsole (`LOG_INFO`) |
| **Viewport-Regex-Highlighting** (`SmaliEditorWidget`) | `CCodeEditorWidget` nach demselben Debounce-Muster |
| **Auto-Bootstrap + PATH-Injection** (`ToolManager`) | NDK/`clang` wird erkannt bzw. heruntergeladen und in den PATH injiziert |

LibForge **ersetzt nichts** am `InjectCustomLibsStep` — Replace und Add koexistieren.

---

## 3. Anforderungsdefinition

### 3.1 Funktionale Anforderungen
- **F1** Neuer Reiter „LibForge" neben „Native Lib Replacer" (im `patch_book` von `WorkspaceTab.build_editor`).
- **F2** C-Code-Editor mit Syntax-Highlighting (Keywords, Typen, Präprozessor, Strings, Kommentare, Zahlen).
- **F3** Namensvorgabe (`name` → Ausgabe `lib<name>.so`) plus optionale Beschreibung.
- **F4** „Compile" → baut `lib<name>.so` via NDK-`clang`; **Recompile überschreibt** die vorhandene `.so`.
- **F5** Bei Build-Fehlern: klare Ausgabe (Exit-Code + `clang`-stderr) in der Konsole und Status am Reiter.
- **F6** Nach Build: Hinweis „manuell per Smali-Patch laden" inkl. fertigem `System.loadLibrary`-Snippet (Kopier-Button).
- **F7** Libs **aktiv/inaktiv** schaltbar; Deaktivieren **löscht nicht** (Datei + Code bleiben).
- **F8** Nur **aktive** Libs mit vorhandener `.so` werden beim Build in die APK aufgenommen.
- **F9** Code **parallel** zur Lib sichtbar/gespeichert (Editor = Quelle der Wahrheit; `.so` = Artefakt).
- **F10** Persistenz über App-Neustarts (`data/native_libs.json`).
- **F11** **Löschen** eines Eintrags per **Button oder „Entf"-Taste** auf dem ausgewählten Listeneintrag →
  entfernt Metadaten **+ `.so` + Quelle** endgültig (mit Bestätigungsdialog).
- **F12** Toolchain-Integration: neuer Pipeline-Step in `BUILD_NATIVE`, spielt aktive Libs additiv ein.
- **F13** **Import fertiger `.so`** per **Filedialog** — ohne Quellcode/Build. Solche Einträge sind sofort
  aktivierbar; der Editor zeigt „importierte Lib (kein Quellcode)".
- **F14** **ABI pro Lib** einstellbar; **Default `arm64-v8a`** (globaler Standard, individuell überschreibbar).
- **F15** **Smali-Studio-Integration (loadLibrary-Helfer):** In Smali Studio eine Aktion, die den
  `System.loadLibrary`-Aufruf **direkt als Smali einfügt** — mit **Dropdown der aktiven LibForge-Libs**
  (Namen automatisch aus `NativeLibManager.get_active()`), sodass **Tippfehler ausgeschlossen** sind.
  Registerwahl (`vN`) wird passend zum Kontext gesetzt/gewählt.

### 3.2 Nicht-funktionale Anforderungen
- **N1** Kein UI-Freeze: Compile im Daemon-Thread, Log via EventBus.
- **N2** Rückwärtskompatibel: bestehende `config.json` wird beim Laden automatisch um den neuen Step erweitert
  (Safety-Check in `ConfigManager.load`, wie schon für „Inject Custom Libs").
- **N3** **Kein Clobbering:** eine aktive Add-Lib darf **keine bestehende APK-Lib überschreiben**
  (Namenskollision → Warnung + Skip). Schützt echte App-Libs.
- **N4** ABI-Korrektheit: gebaute Lib-ABI muss zur App-Architektur passen; Default aus `SessionState.architecture`.
- **N5 (kritisch) Robuste Toolchain-Bereitstellung:** Der `ToolManager` stellt **alle** Build-Tools sicher —
  erkennt ein vorhandenes NDK, lädt es bei Bedarf herunter, **verifiziert** `clang` (`clang --version`)
  und injiziert den Toolchain-Pfad **dynamisch** in `os.environ["PATH"]`. Jeder Fehlerpfad ist explizit
  behandelt und wird klar geloggt — **kein stiller Fehlschlag**.

---

## 4. Datenmodell

`core/domain/native_models.py` (neu):
```python
@dataclass
class NativeLib:
    id: str                     # uuid4
    name: str                   # Basisname ohne "lib"/".so"  -> Ausgabe lib<name>.so
    description: str = ""
    origin: str = "built"       # "built" (aus Quellcode) | "imported" (fertige .so)
    source_code: str = ""       # C-Quelle (nur bei origin=="built"); Source of Truth im JSON
    active: bool = False
    abi: str = "arm64-v8a"      # arm64-v8a | armeabi-v7a | x86_64 | x86
    api_level: int = 30
    link_libs: str = "log dl"   # -l Flags (Leerzeichen-getrennt)
    extra_flags: str = ""       # zusätzliche clang-Flags (optional)
    last_build_ok: bool = False # bei origin=="imported": True nach erfolgreichem Import
    last_build_at: str = ""
    so_relpath: str = ""        # data/native_libs/<id>/lib<name>.so (relativ zu BASE_DIR)
```

**Persistenz-Layout:**
```
data/
  native_libs.json                 # {"libs":[ NativeLib... ]}
  native_libs/
    <id>/
      source.c                     # nur origin=="built"
      lib<name>.so                 # Build-/Import-Artefakt
      build_log.txt                # letzter clang-Output (nur built)
```

---

## 5. Komponenten (neue & berührte Dateien)

### Neu
| Datei | Zweck |
|---|---|
| `core/domain/native_models.py` | `NativeLib`-Dataclass |
| `services/native_lib_service.py` | `NativeLibManager`: load/save/CRUD/`get_active()`/`import_so()`/`delete()` |
| `services/native_compiler_service.py` | `NativeCompilerService.compile(lib) -> (ok, so_path, err)` |
| `core/pipeline/steps/lib_steps.py` | `InjectAddedLibsStep` (Typ `inject_added_libs`) |
| `ui/widgets/c_editor_widget.py` | C-Editor mit Viewport-Regex-Highlighting |
| `ui/tabs/lib_forge_tab.py` | Reiter-UI (als Sub-Tab im `patch_book`) |
| `ui/controllers/lib_forge_controller.py` | Logik: new/save/delete/toggle/compile/import |

### Berührt
| Datei | Änderung |
|---|---|
| `core/pipeline/engine.py` | `"inject_added_libs": InjectAddedLibsStep()` registrieren |
| **`core/infrastructure/config_manager.py`** | **Enthält `DEFAULT_CONFIG` (= steuert `config.json`).** Step in `BUILD_NATIVE`-Default + Safety-Check aufnehmen; neue Keys `NDK_DIR` (Override, leer=auto), `DEFAULT_ABI="arm64-v8a"`, `DEFAULT_API_LEVEL=30`, `NDK_FALLBACK_VERSION="r27c"` |
| **`core/infrastructure/tool_manager.py`** | **NDK bereitstellen:** Detect → Download-Fallback → `clang` verifizieren → Toolchain-`bin` in PATH injizieren |
| `ui/tabs/workspace_tab.py` | in `build_editor()` neuen Sub-Tab `LibForgeTab` neben „Native Lib Replacer" einhängen |
| `ui/tabs/settings_tab.py` | Feld „NDK-Pfad" (Override) + Status-Anzeige „NDK gefunden: …" |
| `ui/tabs/smali_studio_tab.py` (+ ggf. `services/smali_struct_service.py` / `ui/dialogs/struct_dialog.py`) | **loadLibrary-Helfer (F15):** Dropdown der aktiven LibForge-Libs → fügt `System.loadLibrary`-Smali ein |
| `core/application/session_state.py` | optional (Inject ist Manager-getrieben, kein UI-Sync nötig) |
| `README.md` | Modul „4.8 LibForge" dokumentieren |

> Hinweis: Es gibt **keine separate `config.py`** — die von dir gemeinte „Default-Config" ist
> `DEFAULT_CONFIG` in `config_manager.py`. Genau dort erfolgen die Config-Änderungen.

---

## 6. Pipeline-Integration

### 6.1 Neuer Step `InjectAddedLibsStep` (Manager-getrieben — Antwort auf Q5)
Der Step liest **direkt** `NativeLibManager.get_active()` (persistente „aktive" Libs aus
`data/native_libs.json`) — unabhängig davon, welcher Reiter offen ist, exakt wie der Frida-Step
seine Skripte aus `frida_scripts.json` liest. Kein Workspace-UI-Sync nötig.

- Zielverzeichnis: die in der APK vorhandenen Arch-Ordner (analog `InjectCustomLibsStep`),
  standardmäßig `lib/arm64-v8a/`.
- Pro aktiver Lib mit vorhandener `.so`:
  - **Clobber-Guard (N3):** existiert `lib<name>.so` schon im Ziel und stammt nicht aus LibForge → **Warnung + Skip**.
  - sonst `.so` hineinkopieren, Erfolg loggen.
- Abschluss-Hinweis: „X Lib(s) hinzugefügt — Laden nicht vergessen: `System.loadLibrary("<name>")`".

### 6.2 Platzierung in `BUILD_NATIVE`
```
Mirror Original Workspace
Apply Smali Patches
Inject Custom Libs        (Replace – bestehend)
Inject Added Libs         (NEU – Add, Manager-getrieben)
Inject Frida Gadget
Manifest & Build
Apply LSPatch
Clean old signatures
Sign all APKs
```
Nach `mirror_workspace` (lib-Ordner existiert), vor `manifest_and_build`. `zipalign -p -f 4` /
`NATIVE_LIB_STRATEGY` behandeln die neuen Libs automatisch mit.

### 6.3 Auto-Upgrade bestehender `config.json`
`ConfigManager.load` erzwingt schon eine Neuschreibung von `BUILD_NATIVE`, wenn Step-Namen fehlen.
Wir ergänzen `"Inject Added Libs"` in diese Prüfliste → Bestands-Configs bekommen den Step automatisch.

---

## 7. Toolchain-Bereitstellung (ToolManager-Erweiterung, N5 — kritisch)

Der `ToolManager` lädt heute Jars, platform-tools, **build-tools**, Node und Graphviz und injiziert
sie in den PATH. Wir ergänzen die **NDK/clang-Bereitstellung** nach demselben Muster.

**Auflösungsreihenfolge (Detect-first):**
1. `config["NDK_DIR"]` (Settings-Override), falls gesetzt & gültig.
2. `%LOCALAPPDATA%\Android\Sdk\ndk\<neueste>` (Windows) — beim Nutzer vorhanden (r30).
3. `$ANDROID_HOME/ndk/<neueste>` bzw. `$ANDROID_SDK_ROOT/ndk`.
4. Bereits von uns geladenes NDK unter `tools/ndk/<version>/`.
5. **Download-Fallback (abgestimmt: aktiv):** gepinntes NDK-ZIP (**r27c**,
   `https://dl.google.com/android/repository/android-ndk-r27c-<host>.zip`) nach `tools/ndk/`
   entpacken (analog build-tools; großes Archiv ~600 MB–1 GB → Fortschritts-Log, einmalig).
   Version über `config["NDK_FALLBACK_VERSION"]` änderbar.

**Robustheit (kein stiller Fehlschlag):**
- Nach der Auflösung `clang(.exe) --version` ausführen; scheitert das → klarer Fehler + Abbruch des Builds
  mit Hinweis (NDK in Android Studio installieren oder Pfad in Settings setzen).
- Toolchain-`bin` (`.../toolchains/llvm/prebuilt/<host>/bin`) **dynamisch** in `os.environ["PATH"]`
  voranstellen (wie bei Node/Graphviz), damit `clang` im Build-Subprozess auffindbar ist.
- Host-Ordner plattformabhängig: `windows-x86_64` / `linux-x86_64` / `darwin-x86_64`.

**`clang`-Aufruf (verifiziert lauffähig, NDK r30):**
```
clang(.exe) --target=<triple><api> -shared -fPIC -O2 -s \
   -o lib<name>.so source.c -l<link1> -l<link2> <extra_flags>
```
ABI→Triple: `arm64-v8a→aarch64-linux-android`, `armeabi-v7a→armv7a-linux-androideabi`,
`x86_64→x86_64-linux-android`, `x86→i686-linux-android`. **Immer `clang --target=`** verwenden
(die per-API-Wrapper `aarch64-linux-android<api>-clang` gibt es in neueren NDKs nicht mehr).

`NativeCompilerService.compile(lib)`:
- schreibt `source.c`, ruft `clang` via `CommandRunner.run_live` (Zeilen als `[CC] …` auf den EventBus),
- schreibt vollen Output nach `build_log.txt`,
- Erfolg: `.so` in `data/native_libs/<id>/`; setzt `last_build_ok/at`, `so_relpath`,
- Rückgabe `(ok, so_path, err_tail)` für Status-Label + Fehlerbox.

---

## 8. UI-Spezifikation (`LibForgeTab`)

Sub-Tab „**LibForge**" im `patch_book` (neben „Native Lib Replacer").

Horizontales PanedWindow:
- **Links – Lib-Liste** (`Treeview`): Spalten `Aktiv (✓/–)`, `Name`, `Typ (built/imported)`, `letzter Build`, `Beschreibung`.
  Buttons: `＋ Neu`, `📁 .so importieren`, `🗑 Löschen`, `⏻ Aktiv/Inaktiv`.
  **Tastatur:** `Entf` auf ausgewähltem Eintrag → Löschen (mit Bestätigung).
- **Rechts – Detail/Editor:**
  - Kopf: `Name` (→ `lib<name>.so`-Preview), `Beschreibung`; ausklappbar „Erweitert": `ABI` (Default `arm64-v8a`),
    `API-Level`, `Link-Libs` (Default `log dl`), `Extra-Flags`.
  - Mitte: `CCodeEditorWidget` (C-Highlighting, dark). Bei `origin=="imported"`: schreibgeschützt mit
    Hinweis „importierte Lib – kein Quellcode".
  - Aktionsleiste: `⚙️ Compile` (bzw. `Recompile → überschreibt`), Status-Label (grün „OK · libhook.so" / rot „Build-Fehler").
  - **Hinweis-Banner** (nach Build/Import gefüllt):
    > „ℹ️ Diese Lib wird beim Build hinzugefügt, aber **nicht automatisch geladen**.
    > Füge in Smali Studio ein: `System.loadLibrary("<name>")`" — mit **Kopier-Button**.
- **Build-Output:** primär die bestehende Haupt-Konsole (EventBus); Kurz-Status am Reiter.

„Neu"-Vorlage: Starter-C mit `__attribute__((constructor))` + optional `JNI_OnLoad` (JNI_VERSION_1_6)
+ `#include <android/log.h>`.

### 8.1 Smali-Load-Snippet (F6) + Smali-Studio-Helfer (F15)
LibForge zeigt/kopiert den Snippet:
```smali
const-string v0, "<name>"
invoke-static {v0}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V
```
**Zusätzlich (abgestimmt, F15):** In **Smali Studio** gibt es einen **loadLibrary-Helfer** —
ein kleiner Dialog/Menüpunkt mit **Dropdown der aktiven LibForge-Libs** (Namen aus
`NativeLibManager.get_active()`). Auswahl → der `loadLibrary`-Block wird **an der Cursor-/Anker-Stelle
als Smali eingefügt** (bzw. als Smali-Patch-Kandidat angelegt). So werden **Tippfehler beim
Lib-Namen ausgeschlossen**. Dies nutzt den bestehenden Snippet-/Template-Mechanismus
(`SmaliStructManager` / `struct_dialog`).

### 8.2 Linking (Antwort auf Q7)
`-l<lib>` bindet **System-Bibliotheken**, die dein C-Code aufruft: `__android_log_print`→`-llog`,
`dlopen`/`dlsym`→`-ldl`, Mathe→`-lm`, C++→`libc++_shared`. `libc` (malloc/fopen/mmap/pthread) wird
**automatisch** gelinkt. Der Frida-Gadget „läuft ohne Linken", weil er eine **fertig gelinkte**
`.so` ist (seine `NEEDED`-Deps sind schon eingebacken) — **importierte** `.so` brauchen daher auch
kein Linken. Nur **selbst kompilierte** Libs müssen ihre System-Libs angeben. Default **`log dl`**
deckt unseren `libhook`-Fall; das „Erweitert"-Feld erlaubt Extras.

---

## 9. Edge Cases & Risiken

- **Namenskollision mit echter APK-Lib (N3):** Add darf nichts überschreiben → Warnung + Skip;
  UI validiert den Namen gegen bekannte APK-Libs (soweit entpackt).
- **ABI-Mismatch:** Lib-ABI ≠ App-ABI → Build/Inject überspringen + Warnung; ABI-Default = `SessionState.architecture`.
- **NDK fehlt / mehrere NDKs:** Detect-first, „neuestes" per Namенssortierung, Download-Fallback, Override in Settings, `clang`-Verifikation.
- **Löschen vs. Deaktivieren:** Deaktivieren behält alles; Löschen (Button/Entf) entfernt `.so`+Quelle+Eintrag nach Rückfrage.
- **Recompile:** überschreibt vorhandene `.so` bewusst (F4).
- **Import ohne Quelle:** `origin="imported"`, Editor read-only, kein Compile möglich (nur Aktiv/Löschen).
- **Dirty-State:** Quelle geändert, nicht neu gebaut → UI markiert „veraltet – neu kompilieren".
- **Pfade mit Leerzeichen (Windows):** alle `clang`-Pfade gequotet.
- **Großer NDK-Download:** nur wenn keins gefunden; mit Fortschritts-Log und einmalig.

---

## 10. Umsetzungsplan (Phasen)

1. **Domänen- & Persistenzschicht** — `native_models.py`, `native_lib_service.py` (inkl. `import_so`, `delete`), JSON.
   *Akzeptanz:* Libs anlegen/importieren/aktiv-schalten/löschen (headless testbar).
2. **ToolManager-NDK + Compiler-Service** — NDK Detect/Download/Verify/PATH; `native_compiler_service.py`.
   *Akzeptanz:* eine `.c` baut headless zu `.so`; Fehlerfall liefert stderr; fehlendes NDK klar gemeldet.
3. **Pipeline-Step + Wiring** — `lib_steps.py`, `engine.py`, `config_manager.py` (BUILD_NATIVE + Safety-Check + neue Keys).
   *Akzeptanz:* aktive Lib landet nach `run_build` in `lib/arm64-v8a/`; Clobber-Guard greift.
4. **C-Editor-Widget** — `c_editor_widget.py` (Highlighting, Debounce).
5. **Tab + Controller** — `lib_forge_tab.py`, `lib_forge_controller.py`; Einhängen in `workspace_tab.build_editor`;
   Liste inkl. Aktiv-Toggle, Import-Filedialog, Löschen (Button + Entf), Recompile, Load-Snippet.
   *Akzeptanz:* Code → Compile → aktiv → Build → Lib in APK; Import-`.so` → aktiv → Build; Fehler sichtbar.
6. **Settings-Feld NDK-Pfad** + Status; README „4.8 LibForge".
7. **End-to-End-Test** an LibreLinkUp: `libhook.c` bauen, aktiv, Build/Flash, `ghost.log` prüfen.

Nach **Phase 3** funktioniert die Toolchain bereits headless (früh verifizierbar).

---

## 11. Entscheidungen (abgestimmt) & Restfragen

**Abgestimmt (alle offenen Punkte geklärt):**
- **Name:** LibForge.
- **Inject-Quelle (Q5):** Manager-getrieben (aktive Libs aus `data/native_libs.json`).
- **Load (Q6/F15):** LibForge zeigt+kopiert den Snippet **und** Smali Studio bekommt einen
  loadLibrary-Helfer mit **Dropdown der aktiven Libs** (tippfehlerfrei).
- **Linking (Q7):** Default `log dl`, „Erweitert"-Feld für Extras; Import-`.so` braucht kein Linken.
- **Artefakt-Ort:** `data/native_libs/<id>/`.
- **ABI:** Default `arm64-v8a`, pro Lib überschreibbar.
- **Löschen:** Button **und** „Entf"; entfernt `.so`+Quelle+Eintrag. Deaktivieren behält alles.
- **Recompile:** überschreibt bestehende `.so`.
- **Import:** fertige `.so` per Filedialog, ohne Quellcode/Build.
- **R1 – NDK:** Detect-first **+ Auto-Download-Fallback** (gepinnt **r27c**, konfigurierbar).
- **R2 – API-Level:** Default **30** (pro Lib überschreibbar).
- **R3 – Umfang v1:** eine `.c` pro Lib; Mehrdatei/Header später.

→ Der Plan ist damit vollständig abgestimmt und umsetzungsbereit.
