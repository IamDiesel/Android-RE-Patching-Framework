# Refactoring Plan: Kippy RE-Framework V8

Dieses Dokument beschreibt die systematische Migration der Architektur zur Auflösung von "God-Objects", enger Kopplung und durchlässigen Abstraktionen ("Leaky Abstractions")[cite: 14]. Das Ziel ist eine testbare, ereignisgesteuerte (Event-Driven) Architektur mit zentralem State-Management und einer sauberen Verzeichnisstruktur[cite: 14]. 

Neu integriert ist der Ansatz der **Test-Driven Extraction**: Kritische Features werden exakt in dem Moment mit Unit-Tests abgesichert, in dem sie aus der UI gelöst und in Kernmodule verschoben werden[cite: 14].

---

## Vorbereitungsphase (Pre-Refactoring)

### 0.0 Ziel-Ordnerstruktur
Um das aktuelle Chaos im Hauptverzeichnis aufzulösen, wird das Framework in folgende logische Domänen unterteilt[cite: 14]:

    kippy-re-framework/
    ├── core/                   # Zentrale Business-Logik, State und Event-System
    │   ├── event_bus.py
    │   ├── session_state.py
    │   ├── data_extractor.py
    │   ├── exceptions.py
    │   ├── tool_manager.py     # Automatisierter Dependency-Downloader
    │   ├── command_runner.py
    │   └── fuzzing_engine.py
    ├── ui/                     # Präsentationsschicht (Tkinter)
    │   ├── tabs/               # Haupt-Tabs der Anwendung
    │   │   ├── api_inspector_tab.py
    │   │   ├── workspace_tab.py
    │   │   ├── history_tab.py
    │   │   ├── settings_tab.py
    │   │   ├── smali_studio_tab.py
    │   │   └── app_manager_tab.py
    │   └── dialogs/            # Modale Fenster und Popups
    │       ├── favorite_patches_dialog.py
    │       ├── column_dialogs.py
    │       ├── fuzzy_matcher_dialog.py
    │       └── struct_dialog.py
    ├── services/               # Zustandslose Services und Parser
    │   ├── patch_service.py
    │   ├── smali_parser.py
    │   ├── smali_search.py
    │   └── fs_service.py
    ├── tests/                  # Unit-Tests zur Absicherung des Refactorings
    │   ├── test_data_extractor.py
    │   ├── test_smali_parser.py
    │   ├── test_manifest_strategy.py
    │   ├── test_fuzzing_engine.py
    │   ├── test_callgraph.py
    │   └── test_config_manager.py
    ├── tools/                  # Externe Binaries (APKEditor.jar, libfrida-gadget.so etc.)
    ├── main.py                 # Bootstrapper und PATH-Injektion
    └── gui.py                  # Tkinter Root-Applikation

### 0.1 Typsicherheit (Type Hints)
Hinzufügen von Python Type Hints in kritischen Business-Logik-Klassen, um Schnittstellenfehler während des Verschiebens frühzeitig zu erkennen[cite: 14].
*   **Ziel-Klassen:** `DataExtractor`[cite: 14], `SmaliStudioParser`[cite: 14], `PipelineEngine`[cite: 14].

### 0.2 Test-Harness & Agile Testing Strategie
Erstellung grundlegender Unit-Tests für zustandslose Komponenten[cite: 14]. Weitere komplexe Logiken werden nach dem Prinzip der **Test-Driven Extraction** abgesichert, kurz bevor sie refactored werden[cite: 14].
*   **Bereits implementiert:**
    *   `tests/test_data_extractor.py`[cite: 14]
    *   `tests/test_smali_parser.py`[cite: 14]
    *   `tests/test_manifest_strategy.py`[cite: 14]
*   **Agil zu implementieren (Just-in-Time vor dem Refactoring):**
    *   **Call Graph Logik:** `tests/test_callgraph.py` sichert ab, dass `add_edge`, `make_root` und Duplikat-Filter in `CallGraphManager` fehlerfrei funktionieren[cite: 14].
    *   **Fuzzy Matcher:** `tests/test_fuzzing_engine.py` sichert die Heuristiken `_fuzz_by_method_name` und `_fuzz_by_content_snippet` ab[cite: 14].
    *   **Pfad-Auflösung:** `tests/test_fs_service.py` prüft `resolve_smali_path` für die korrekte Übersetzung von Apktool/APKEditor-Pfaden[cite: 14].
    *   **Pfade & Configs:** `tests/test_config_manager.py` sichert ab, dass `_update_paths` korrekte Arbeitsverzeichnisse basierend auf Paketnamen und Architekturen erstellt[cite: 14].

---

## Stage 1: Entkopplung durch Event-Bus (Event-Driven Architecture)

Beseitigung der übermäßigen Callback-Übergaben (`log_callback`, `update_progress_callback`) durch ein Publish/Subscribe-Pattern[cite: 14].

### 1.1 Neue Komponente: `core/event_bus.py`
*   **Klasse:** `EventBus`[cite: 14]
*   **Methoden:** `@classmethod def subscribe(...)`, `@classmethod def publish(...)`[cite: 14].

### 1.2 Anpassung der Business-Logik
Entfernen der Callback-Parameter aus den Konstruktoren (Init-Methoden)[cite: 14].
*   **`pipeline_engine.py`**[cite: 14]: Ersetze `self.log(...)` durch `EventBus.publish("LOG_INFO", ...)`[cite: 14].
*   **`smali_search.py`**[cite: 14]: Ersetze `self.update_progress(...)` durch `EventBus.publish("INDEX_PROGRESS", ...)`[cite: 14].
*   **Contoller & UI:** Entferne `log_callback` aus `SmaliCGController.__init__`[cite: 14] und `AppManagerTab.__init__`[cite: 14].

### 1.3 UI-Abonnement
*   **`gui.py`**[cite: 14]: In `KippyReFrameworkApp.__init__` füge `EventBus.subscribe("LOG_INFO", self.log)` hinzu[cite: 14].

---

## Stage 2: Zerschlagung der God-Objects & Ordnerstruktur

Trennung von UI, State und Logik durch Reorganisation in die neue Verzeichnisstruktur[cite: 14].

### 2.1 Refactoring `api_inspector.py`[cite: 14]
*   **`core/data_extractor.py`**: Verschiebe `class DataExtractor`[cite: 14].
*   **`core/column_config_manager.py`**: Verschiebe `class ColumnConfigManager`[cite: 14].
*   **`core/column_display_manager.py`**: Verschiebe `class ColumnDisplayManager`[cite: 14].
*   **`ui/dialogs/column_dialogs.py`**: Verschiebe `class ColumnDisplayDialog` und `class CustomColumnDialog`[cite: 14].
*   **`ui/tabs/api_inspector_tab.py`**: Die `class APIInspectorTab` bleibt hier als reiner View erhalten[cite: 14].

### 2.2 Refactoring `ui_workspace_tab.py`[cite: 14]
*   **`ui/dialogs/favorite_patches_dialog.py`**: Verschiebe die `class FavoritePatchesDialog`[cite: 14].
*   **`services/patch_service.py`**: Extrahiere `start_batch_fav()` und `start_single_fav()` in einen zustandslosen Service[cite: 14].

### 2.3 Refactoring `fuzzy_matcher.py`[cite: 14] (inkl. Test-Driven Extraction)
*   *Test:* Erstelle `tests/test_fuzzing_engine.py` zur Absicherung von `_fuzz_by_method_name` und `_fuzz_by_content_snippet`[cite: 14].
*   **`ui/dialogs/fuzzy_matcher_dialog.py`**: Verschiebe `class FuzzyMatchDialog`[cite: 14].
*   **`core/fuzzing_engine.py`**: Erstelle `class FuzzingEngine` und extrahiere die Suchlogik[cite: 14].

---

## Stage 3: Centralized State Management (SessionState)

Auflösung des verteilten Zustands (State), der derzeit in UI-Widgets wie `self.patch_rows`[cite: 14] oder Variablen im Controller wie `self.smali_patches`[cite: 14] verstreut ist[cite: 14].

### 3.1 Neue Komponente: `core/session_state.py`
*   **Klasse:** `SessionState`[cite: 14]
*   **Eigenschaften:** `package_name`, `architecture`, `active_hex_patches`, `active_lib_replacements`, `active_smali_patches`[cite: 14].

### 3.2 UI -> State Synchronisation
*   **`ui/tabs/workspace_tab.py`**: Synchronisiere alle Änderungen aus `add_patch_row()` / `add_lib_row()` direkt in `SessionState`[cite: 14]. Entferne `get_all_patches()`[cite: 14].
*   **`smali_studio_controller.py`**[cite: 14]: Verschiebe `self.smali_patches` in den `SessionState`[cite: 14].

### 3.3 Engine Data Binding & Konfigurations-Tests
*   *Test:* Erstelle `tests/test_config_manager.py`, um die Pfad-Auflösung in `ConfigManager` abzusichern[cite: 14].
*   **`pipeline_engine.py`**[cite: 14]: Lade Patches in den Build-Methoden direkt via `SessionState.get_all_patches()` anstatt über die GUI-Funktion[cite: 14].

---

## Stage 4: Bereinigung der Pipeline Engine (Leaky Abstractions)

Beseitigung der direkten UI-Abhängigkeiten in der Business-Logik (`PipelineEngine`)[cite: 14].

### 4.1 UI-Imports entfernen
*   **`pipeline_engine.py`**[cite: 14]: Entferne `from tkinter import messagebox`[cite: 14].

### 4.2 Inversion of Control (IoC) für Benutzerentscheidungen
*   **`core/exceptions.py`**: Erstelle `class PatchConflictException(Exception): pass`[cite: 14].
*   **`pipeline_engine.py`**[cite: 14]: Wirf bei Abweichungen `PatchConflictException` anstatt `messagebox.askyesnocancel` aufzurufen[cite: 14]. Publiziere bei Fang der Exception `EventBus.publish("PIPELINE_CONFLICT_DETECTED")`[cite: 14].
*   **`ui/tabs/workspace_tab.py`**[cite: 14]: Abonniere das Event `"PIPELINE_CONFLICT_DETECTED"`, öffne den Dialog und biete eine Resume-Funktion an[cite: 14].

---

## Stage 5: Automatisiertes Tool-Management (ToolManager)

Beseitigung von "Works on my machine"-Problemen und fehleranfälligen lokalen Pfaden durch automatische Bereitstellung von Drittanbieter-Tools.

### 5.1 Neue Komponente: `core/tool_manager.py`
*   **Klasse:** `ToolManager`
*   **Funktion:** Lädt plattformunabhängige Tools (`APKEditor.jar`, `uber-apk-signer.jar`, `lspatch.jar`, `libfrida-gadget.so`) und Windows-spezifische Binaries (`apktool`, `adb`, `zipalign`) asynchron herunter.
*   **PATH Injection:** Injiziert die heruntergeladenen Binaries zur Laufzeit dynamisch in `os.environ["PATH"]`.

### 5.2 Konfigurations-Bereinigung
*   **`config.py`**: Entfernen von versionsspezifischen Dateinamen (z.B. `uber-apk-signer-1.3.0.jar` -> `uber-apk-signer.jar`).
*   **`pipeline_engine.py`**: Ersetzen von Wildcard-Suchen (`glob.glob`) durch feste Tool-Pfade im standardisierten `tools/`-Ordner.
*   **`gui.py`**: Initialisierung des asynchronen Tool-Downloads beim Start der Applikation.

---

## Stage 6: Subprozess-Abstraktion (Command Runner)

Um die Pipeline testbar zu machen, ohne dass tatsächliche Systembefehle (Apktool, ADB) ausgeführt werden müssen[cite: 14].

### 6.1 Neue Komponente: `core/command_runner.py`
*   **Klasse:** `CommandRunner`[cite: 14]
*   **Methoden:** `run_blocking(cmd: str, cwd: str) -> CommandResult`, `run_background(cmd: str, cwd: str) -> subprocess.Popen`[cite: 14].

### 6.2 Implementierung in Controllern
*   Ersetze in `pipeline_engine.py`[cite: 14] und `app_manager.py`[cite: 14] alle direkten Aufrufe von `subprocess.Popen` und `subprocess.run` durch Instanzen des `CommandRunner`[cite: 14].