# Refactoring Plan: Kippy RE-Framework V8 (Phase II)

Dieses Dokument beschreibt die systematische Migration der Architektur zur Auflösung von "God-Objects", enger Kopplung und durchlässigen Abstraktionen ("Leaky Abstractions"). Das Ziel ist eine testbare, ereignisgesteuerte (Event-Driven) Architektur mit zentralem State-Management und einer sauberen Verzeichnisstruktur.

Nach dem erfolgreichen Abschluss der ersten 6 Stages stehen nun die Beseitigung der verbleibenden Monolithen im Root-Verzeichnis, die Strukturierung des `core/`-Ordners und vor allem die hochpriorisierte Modularisierung der `PipelineEngine` (Stage 9) an.

---

## Architektur-Analyse & Anti-Patterns im aktuellen Stand

Bevor wir die nächsten Stages definieren, hier die Analyse der aktuellen Verstöße gegen saubere Software-Architektur-Prinzipien:

1. **Anti-Pattern: "Mülleimer-Verzeichnisse" (Namespace Pollution)**
* **Root-Verzeichnis:** Beinhaltet noch immer Logik-Klassen wie `config.py`, `history.py`, `cg_manager.py` und `frida_manager.py`. Das Root-Level sollte ausschließlich Bootstrapper (`main.py`, `gui.py`) enthalten.


* **Core-Verzeichnis:** Droht der nächste "Mülleimer" zu werden. Es mischt aktuell Infrastruktur (`command_runner.py`, `tool_manager.py`), Domänen-Logik (`fuzzing_engine.py`) und State (`session_state.py`).




2. **Anti-Pattern: "God Object" & "Long Method" (Die `PipelineEngine`)**
* Die `pipeline_engine.py` ist ein massiver Monolith. Die Methode `run_pipeline` ist ein gigantischer `if-elif`-Router. Jede neue Funktionalität (Frida, LSPatch, NSC-Injektion) führt dazu, dass diese Klasse weiter anwächst. Dies verstößt gegen das **Open-Closed Principle (OCP)** (Klassen sollten offen für Erweiterungen, aber geschlossen für Modifikationen sein).




3. **Anti-Pattern: "Fat Controller" (`SmaliStudioController`)**
* Der `SmaliStudioController` steuert nicht nur die UI-Komponenten, sondern betreibt auch Threading für die rekursive CallGraph-Exploration (`_exploration_thread`) und behandelt Datei-I/O (`unpack_apk_async`). Dies verletzt das **Single Responsibility Principle (SRP)**.




4. **Anti-Pattern: Fehlende "Service Layer"-Kapselung**
* Klassen wie der `FridaManager` und `HistoryManager` agieren als Services, liegen aber ohne klare Trennung im Root-Verzeichnis.





---

## Vorbereitungsphase (Pre-Refactoring)

### Ziel-Ordnerstruktur (Phase II)

Um auch Projektübergreifend sauber zu bleiben, adaptieren wir Prinzipien aus dem **Domain-Driven Design (DDD)** und der **Clean Architecture**:

```
kippy-re-framework/
├── core/                           # Systemweite Kernkomponenten (Clean Architecture)
│   ├── application/                # Applikations-Logik und State
│   │   ├── event_bus.py
│   │   └── session_state.py
│   ├── domain/                     # Reine Business-Regeln
│   │   ├── exceptions.py
│   │   └── models.py               # (Optional für Data-Classes)
│   ├── infrastructure/             # Interaktion mit OS und externen Tools
│   │   ├── command_runner.py
│   │   ├── tool_manager.py
│   │   └── config_manager.py
│   └── pipeline/                   # Kapselung der modularen Pipeline Engine
│       ├── engine.py
│       ├── step_interface.py
│       └── steps/                  # Einzelne Pipeline-Module
├── services/                       # Fachliche, zustandslose Services
│   ├── callgraph_service.py
│   ├── frida_service.py
│   ├── history_service.py
│   ├── patch_service.py
│   ├── smali_parser.py
│   └── smali_search_service.py
├── ui/                             # Tkinter Präsentationsschicht
│   ├── controllers/                # UI-Logik (MVC)
│   ├── dialogs/                    # Popups
│   ├── tabs/                       # Haupt-Views
│   └── widgets/                    # Wiederverwendbare UI-Komponenten
├── tools/                          # Automatisierte Downloads (APKEditor, Frida etc.)
├── main.py                         # Bootstrapper
└── gui.py                          # Tkinter Root

```

---

## Abgeschlossene Meilensteine (Stage 1 - 6)

* ✅ **Stage 1:** Event-Driven Architecture (`EventBus`) implementiert.


* ✅ **Stage 2:** Erste GUI-God-Objects zerschlagen.
* ✅ **Stage 3:** Centralized State Management (`SessionState`) eingeführt.


* ✅ **Stage 4:** Leaky Abstractions durch `PatchConflictException` eliminiert.


* ✅ **Stage 5:** Automatisierter `ToolManager` implementiert.


* ✅ **Stage 6:** Systemnahe OS-Aufrufe durch `CommandRunner` ersetzt.



---

## Stage 7: Root-Directory Purge & Core-Strukturierung

Bereinigung des Hauptverzeichnisses und saubere Unterteilung des `core/`-Ordners zur Vorbereitung der Modularisierung.

### 7.1 Aufbau der Clean Architecture im `core/`

Verschiebe bestehende Kernkomponenten in ihre logischen Unterverzeichnisse:

* Verschiebe `core/event_bus.py` ➔ `core/application/event_bus.py`


* Verschiebe `core/session_state.py` ➔ `core/application/session_state.py`


* Verschiebe `core/command_runner.py` ➔ `core/infrastructure/command_runner.py`


* Verschiebe `core/tool_manager.py` ➔ `core/infrastructure/tool_manager.py`


* Verschiebe `core/exceptions.py` ➔ `core/domain/exceptions.py`



### 7.2 Bereinigung des Root-Verzeichnisses

Verschiebe die verbliebenen Dateien aus dem Root in die entsprechenden Schichten:

* Verschiebe `config.py` ➔ `core/infrastructure/config_manager.py`


* Verschiebe `history.py` ➔ `services/history_service.py`


* Verschiebe `cg_manager.py` ➔ `services/callgraph_service.py`


* Verschiebe `frida_manager.py` ➔ `services/frida_service.py`


* Verschiebe `mitm_addon.py` ➔ `tools/mitm_addon.py` (Als eigenständiges Proxy-Skript behandeln).



---

## Stage 8: Priorisiert: Modularisierung der PipelineEngine (Command Pattern)

Lösung des OCP-Verstoßes (Open-Closed Principle). Die gigantische `pipeline_engine.py` wird in kleine, unabhängige und testbare Befehls-Module zerschlagen.

### 8.1 Definition des Step-Interfaces

* Erstelle `core/pipeline/step_interface.py`.
* Definiere die Basis-Klasse `class PipelineStep(ABC):` mit der abstrakten Methode `@abstractmethod def execute(self, engine_context: Any) -> bool:`.

### 8.2 Extraktion der Pipeline-Schritte (Command Objects)

Lagere die harten `_inject_X` und `_apply_Y` Methoden als eigenständige Klassen in den Ordner `core/pipeline/steps/` aus:

* `steps/decompile_step.py` (beinhaltet Logik von `_decompile`)


* `steps/merge_splits_step.py` (beinhaltet Logik von `_merge_splits`)


* `steps/smali_patch_step.py` (beinhaltet Logik von `_apply_smart_patches`)


* `steps/frida_inject_step.py` (beinhaltet Logik von `_inject_frida`)


* `steps/lspatch_inject_step.py` (beinhaltet Logik von `_apply_lspatch`)


* `steps/manifest_build_step.py` (beinhaltet das Strategy-Pattern für Apktool/APKEditor)



### 8.3 Reduzierung der Engine zum Executor

* Verschiebe `pipeline_engine.py` ➔ `core/pipeline/engine.py`.


* Die Methode `run_pipeline` mappt die JSON-Konfiguration ("type": "decompile") dynamisch auf die neuen Klassen-Instanzen aus `steps/` und ruft in einer simplen Schleife nur noch `step.execute(self)` auf.


* **Vorteil:** Neue Pipeline-Schritte können in Zukunft einfach als neue Datei hinzugefügt werden, ohne die `engine.py` jemals wieder anfassen zu müssen.

---

## Stage 9: Zerschlagung des SmaliStudioController (Fat Controller)

Der `SmaliStudioController` muss sich wieder auf die reine UI-Steuerung fokussieren. Asynchrone Analyse-Aufgaben wandern in dedizierte Services.

### 9.1 Auslagerung der CallGraph-Exploration

* Erstelle `services/exploration_service.py`.
* Verschiebe die Methoden `start_auto_explore`, `stop_auto_explore` und den Thread `_exploration_thread` in diesen Service.


* Der Service nutzt den `EventBus`, um UI-Updates (wie "Fortschritt aktualisiert" oder "Graph erneuern") an den Controller zu funken, anstatt hart verdrahtet `self.app.after` aufzurufen.



### 9.2 Auslagerung der Cross-Reference-Suche (XREF)

* Erstelle `services/xref_service.py`.
* Verschiebe `find_incoming_xrefs` und `_process_xref_results` hierhin.



### 9.3 Verschiebung in MVC-Struktur

* Verschiebe die bereinigte `smali_studio_controller.py` ➔ `ui/controllers/smali_studio_controller.py`.



---

## Stage 10: Letzte UI-Komponenten Kapselung

Alle noch nicht MVC-konformen oder lose gekoppelten UI-Skripte werden sauber in den `ui/`-Namespace eingegliedert.

### 10.1 UI Utils & Widgets

* Erstelle `ui/widgets/smali_editor_widget.py` und verschiebe die Klasse `SmaliEditorWidget` (aus `smali_editor.py`) dorthin. Dies ermöglicht die saubere Wiederverwendbarkeit des Syntax-Highlighters.


* Verschiebe `ui_utils.py` ➔ `ui/utils.py`.



### 10.2 Finaler Import-Check

* Passe alle Modul-Importe systemweit an die neue Clean Architecture an (z.B. in `gui.py` und den Tab-Initialisierungen).


* Führe das Test-Framework (`pytest tests/`) aus, um sicherzustellen, dass die Extraktion von Pfaden und Modulen die Geschäftslogik nicht gebrochen hat.