# Android RE Patching Framework - Technical Documentation

A Python-based automation utility designed for Android reverse engineering workflows.

This application automates the sequential execution of unpacking, binary hex patching, native library replacements, Smali modification, repackaging, cryptographic signing, ADB sideloading, and logcat tracing. It facilitates the local integration of static code analysis, dynamic instrumentation (Frida), and MITM proxying for applications, including native Java/Kotlin builds and Dart AOT-compiled (Flutter) applications. It additionally exposes an **MCP server** for controlled AI-agent operation (see §4.10).

⚠️ **Disclaimer:**

*This project is provided strictly for educational purposes and security research. The repository does not distribute copyrighted APK files or proprietary binaries. Users must supply legally obtained binaries. The tools are intended exclusively for local testing environments during security analysis.*

---

## 1. Software Architecture & Design Patterns

The application is written in Python and utilizes `tkinter` for its graphical user interface. To prevent the main thread from blocking during file I/O or computationally intensive operations, the architecture implements the Model-View-Controller (MVC) paradigm alongside an event-driven Service-Oriented Architecture (SOA).

* **Strict MVC Implementation:** The graphical user interfaces (Views) are decoupled from business logic. All data processing and subprocess executions are delegated to specific controllers (e.g., `WorkspaceController`, `FridaManagerController`, `DeviceFileManagerController`) and stateless service classes.
* **EventBus (Pub/Sub):** Cross-module communication is handled via a centralized event bus (`EventBus`). Background threads, such as those reading the ADB logcat output, processing RPC messages from Frida, or calculating code diffs, publish events that UI components subscribe to, preventing hard dependencies and Tkinter threading conflicts.
* **Pipeline Engine (Command Pattern):** The build and modification sequence is executed based on an iterable JSON configuration (`config.json`) rather than imperative hardcoding. The `PipelineEngine` instantiates classes implementing the `PipelineStep` interface for each configured operation (e.g., `SmartPatchStep`, `FridaInjectStep`, `LSPatchInjectStep`).

---

### 1.1 Directory Structure & Core Files

The framework is organized into a strictly modular directory structure, separating state management, business logic, background services, and graphical interfaces. Below is the comprehensive map of all application components:

```text
├── main.py                             # Entry point, dependency injection, and PATH resolution[cite: 67]
├── gui.py                              # Main Tkinter application class and tab initialization[cite: 68]
├── requirements.txt                    # Python dependencies (mitmproxy, frida-tools, etc.)[cite: 69]
│
├── core/                               # Application core and data processing logic
│   ├── data_extractor.py               # Data extraction via JSONPath, Regex, or Byte-Offsets[cite: 49]
│   ├── fuzzing_engine.py               # Opcode normalization and heuristic diffing search[cite: 50]
│   ├── column_config_manager.py        # Logic and rules for custom API table columns[cite: 51]
│   ├── column_display_manager.py       # Visibility management for custom UI columns[cite: 52]
│   │
│   ├── application/                    # Global state and communication
│   │   ├── event_bus.py                # Central Pub/Sub event bus for decoupled messaging[cite: 65]
│   │   └── session_state.py            # Holds active patches (Hex, Smali, Libs)[cite: 66]
│   │
│   ├── domain/                         # Domain models and custom exceptions
│   │   ├── exceptions.py               # PatchConflictException for build interruption[cite: 64]
│   │   └── frida_models.py             # Dataclasses for Frida scripts, configs, and collections[cite: 63]
│   │
│   ├── infrastructure/                 # Low-level system interactions
│   │   ├── command_runner.py           # Subprocess wrapper for blocking and live-stream execution[cite: 61]
│   │   ├── config_manager.py           # Loads JSON configs and builds dynamic workspace paths[cite: 62]
│   │   └── tool_manager.py             # Auto-downloader for Apktool, Node.js, Graphviz, Frida, etc.[cite: 60]
│   │
│   └── pipeline/                       # Build process orchestration
│       ├── engine.py                   # Translates JSON pipelines into sequential step execution[cite: 53]
│       ├── step_interface.py           # Abstract base class (ABC) for all pipeline steps[cite: 54]
│       └── steps/                      # Modular pipeline step implementations
│           ├── apk_steps.py            # Decompilation, Split-merging, and Manifest patching[cite: 59]
│           ├── basic_steps.py          # Generic CLI commands and workspace mirroring[cite: 55]
│           ├── hook_steps.py           # Injectors for Frida Gadget and LSPosed/TrustMeAlready[cite: 56]
│           ├── patch_steps.py          # Appliers for Smali, Hex offsets, and custom native libs[cite: 57]
│           └── trace_steps.py          # Background starters for ADB logcat tracing[cite: 58]
│
├── services/                           # Stateless background services
│   ├── adb_network_service.py          # ADB proxy routing and mitmproxy certificate pushing[cite: 30]
│   ├── api_db_service.py               # SQLite database operations for intercepted HTTP traffic[cite: 26]
│   ├── callgraph_service.py            # RAM-based Callgraph edge/node relationship mapping[cite: 27]
│   ├── device_file_service.py          # Sandbox navigation (run-as) and base64 file transfers[cite: 28]
│   ├── exploration_service.py          # Recursive Callgraph traversal and deep-explore logic[cite: 29]
│   ├── favorite_service.py             # Persistent storage and loading of favorite patch sets[cite: 39]
│   ├── frida_compiler_service.py       # Node.js workspace setup and JS/TS compilation via frida-compile[cite: 40]
│   ├── frida_server_service.py         # Reverse TCP-Tunneling and PortalService daemon handling[cite: 31]
│   ├── frida_service.py                # Manager for persistent Frida scripts and snippets[cite: 32]
│   ├── frida_sync_service.py           # Device sync (Push/Delete) to Scoped Storage via run-as[cite: 33]
│   ├── ghost_log_service.py            # Native file-streaming tail listener for Logcat bypass[cite: 34]
│   ├── graphviz_service.py             # DOT-format generation and SVG exporting for Callgraphs[cite: 35]
│   ├── history_service.py              # Test session recording and Markdown report generation[cite: 36]
│   ├── logcat_service.py               # Asynchronous ADB logcat trace capturing & Frida log routing[cite: 37]
│   ├── mitm_addon.py                   # mitmdump Addon script injecting intercept logic into the proxy[cite: 38]
│   ├── patch_service.py                # Evaluates original Smali against RAM cache for exact/structural matches[cite: 46]
│   ├── profile_manager_service.py      # Manages ADB command templates (Intents & Logcat filters)[cite: 47]
│   ├── proxy_service.py                # Subprocess controller for starting/stopping mitmdump[cite: 48]
│   ├── smali_fs_service.py             # Cross-platform Dalvik path resolution and method extraction[cite: 41]
│   ├── smali_parser.py                 # Regex-based extraction of methods, fields, and data flows[cite: 42]
│   ├── smali_search_service.py         # Threaded RAM caching, pickling, and text indexing[cite: 43]
│   ├── smali_struct_service.py         # Physical creation and injection of predefined Smali templates[cite: 44]
│   └── xref_service.py                 # Cross-Reference resolution for incoming method calls[cite: 45]
│
├── ui/                                 # Graphical User Interface (MVC Implementation)
│   ├── utils.py                        # Global UI utilities (e.g., Treeview copy-to-clipboard formatting)[cite: 23]
│   │
│   ├── controllers/                    # Logic handlers receiving UI events
│   │   ├── device_file_manager_controller.py # File transfer and directory navigation coordinator[cite: 10]
│   │   ├── favorite_patches_controller.py    # Patch loading and conflict handling via FuzzyMatcher[cite: 7]
│   │   ├── frida_manager_controller.py       # Frida mode switching and device synchronization[cite: 8]
│   │   ├── fuzzy_match_controller.py         # Fuzzy search execution and side-by-side diffing[cite: 9]
│   │   ├── smali_cg_controller.py            # Treeview manipulation and live-filtering for Callgraphs[cite: 11]
│   │   ├── smali_studio_controller.py        # Central hub for static analysis and XREF delegation[cite: 12]
│   │   └── workspace_controller.py           # Main pipeline executor (Build, Flash, Uninstall)[cite: 13]
│   │
│   ├── dialogs/                        # Popup windows and configuration dialogs
│   │   ├── column_dialogs.py           # Customization dialogs for API columns and visibility[cite: 4]
│   │   ├── favorite_patches_dialog.py  # Manager UI for saved patch sets and live previews[cite: 5]
│   │   ├── frida_manager_dialog.py     # IDE-like editor with Drawer-Pattern for Frida scripts[cite: 6]
│   │   ├── fuzzy_matcher_dialog.py     # Conflict resolution UI for structural Smali deviations[cite: 1]
│   │   ├── global_search_dialog.py     # Standalone real-time search window for the RAM index[cite: 2]
│   │   └── struct_dialog.py            # Generator UI for creating new Smali classes from templates[cite: 3]
│   │
│   ├── tabs/                           # Main notebook sections
│   │   ├── api_inspector_tab.py        # HTTP traffic table, proxy controls, and ADB network routing[cite: 21]
│   │   ├── app_manager_tab.py          # Lists installed packages and handles ADB pull operations[cite: 20]
│   │   ├── device_file_manager_tab.py  # Treeview file explorer with asynchronous download progress[cite: 14]
│   │   ├── history_tab.py              # Visualizes past patching sessions and test results[cite: 15]
│   │   ├── launcher_logger_tab.py      # App launch intent controls and live color-coded console[cite: 16]
│   │   ├── settings_tab.py             # Form for base paths and JSON-based pipeline customization[cite: 17]
│   │   ├── smali_studio_tab.py         # Static analyzer interface (Editor, Outline, Callgraph, Data flows)[cite: 18]
│   │   └── workspace_tab.py            # Pipeline controls, Hex/Lib patches, and strategy configuration[cite: 19]
│   │
│   └── widgets/                        # Reusable custom UI components
│       └── smali_editor_widget.py      # Editor with viewport-based (debounced) regex syntax highlighting[cite: 22]
│
├── data/                               # Persistent JSON data, snippets, profiles, and SQLite databases
├── tools/                              # External dependencies (auto-downloaded by ToolManager)
├── source/                             # Original target APK files (and local splits)
├── destination/                        # Output directory for patched, aligned, and signed APKs
└── archives/                           # Auto-generated backup directories containing build artifacts and trace logs

```

## 2. Core Algorithms & Data Processing

### 2.1 Fuzzing Engine & Heuristic Matching

To maintain the applicability of Smali patches across different application versions, the software utilizes a two-stage heuristic search algorithm when static offsets fail.

* **Opcode Normalization:** Prior to code comparison, volatile metadata is stripped using regular expressions (e.g., `.line` directives, comments, and Dalvik registers are genericized).
* **Search Phases & Conflict Resolution:** The engine performs a fast signature-based search, followed by a global sequence-matching (`difflib`) across the RAM cache. If the pipeline encounters a `PatchConflictException` during a build, the build is paused, and an interactive Fuzzy Matcher UI is launched to let the user visually resolve the discrepancy.

### 2.2 Text Rendering & Threading (Smali Studio)

Decompiled Smali files can be excessively large. To prevent UI freezing:

* **Viewport Lazy-Highlighting:** The regex-based syntax highlighting engine evaluates only the currently visible viewport rather than the entire text buffer, debounced via scroll events.
* **Asynchronous Diffing:** For side-by-side code comparisons, the `SequenceMatcher.get_opcodes()` calculation is dispatched to a background daemon thread.

### 2.3 Call Graph & Data Flow Analysis

The framework creates a complete RAM-indexed representation of the disassembled Smali code.

* **XREF Resolution:** Cross-references (incoming/outgoing method calls) and class fields/string data flows are extracted via regex parsing (`SmaliStudioParser`).
* **Visual Exploration:** A `CallGraphExplorationService` maps the entire execution path recursively. This graph can be exported as a vector-based `.svg` file using the embedded Graphviz engine for external analysis.

---

## 3. System Requirements & Bootstrapping

The framework requires **Python 3.x** and a Java Development Kit (JDK).

**Automated Bootstrapping (`ToolManager` & `main.py`):**
Upon execution (`main.py`), the application initiates a `bootstrap_environment()` routine. It dynamically resolves Android SDK paths (like `zipalign`) and downloads missing third-party binaries:

* **Java Utilities:** `apktool`, `APKEditor.jar`, `uber-apk-signer.jar`, `lspatch.jar`, `TrustMeAlready.apk`.
* **Dynamic Instrumentation:** `libfrida-gadget.so` (v17+).
* **Web/Render Engines (Windows):** A portable Node.js runtime (for `frida-compile`) and a portable Graphviz environment (for Callgraph SVG rendering) are automatically downloaded to handle operations seamlessly without requiring system-wide installations.

Absolute paths to these executables are dynamically injected into the runtime `os.environ["PATH"]`.

---

## 4. Module Specifications

### 4.1 App Manager

Integrates ADB commands to extract APK files directly from a connected Android device (`pm list packages -3` -> `pm path` -> `adb pull`). Alternatively, users can import local APKs (including base and split configurations). It automatically detects the target CPU architecture and configures the workspace dynamically.

### 4.2 Smali Studio (Static Analyzer)

An integrated text editor for Dalvik bytecode manipulation.

* **RAM Indexing:** Caches entire application sources in memory (serialized via `pickle`) for instant global searches.
* **Structural Generation:** Allows users to inject predefined Smali templates (`snippets.json`) or create entirely new valid `.smali` class files on the disk via the `SmaliStructManager`.

### 4.3 Patch Management & Build Pipeline

Coordinates source code modifications and dynamically reconstructs the application. Supported modifications include **Smali Patches**, **Hex Patches**, and **Native Library Replacements** (`.so` exchange).

* **Manifest & Build Strategies:** The pipeline supports diverse decompilation/build strategies (`apktool`, `apkeditor`, `aapt2`). It handles Split-APK merging automatically and manipulates the `AndroidManifest.xml` on the fly to inject the Network Security Config (NSC) or the `android:debuggable` flag.
* **LSPatch Injection:** Automates the integration of Non-Root Xposed modules (like `TrustMeAlready`) into the target application by dynamically invoking the `lspatch.jar` during the build sequence.
* **RASP Evasion & SELinux Compliance:** The Frida Gadget is disguised (e.g., renamed to `libmetrics.so`) to bypass static string-based RASP memory scans. Additionally, `inotify` hooks are disabled (`"on_change": "ignore"`) to prevent SELinux-induced crashes.
* **Native Lib Alignment:** The workspace UI includes a dynamic toggle for `extractNativeLibs`. Disabling this enforces uncompressed native libraries with proper `zipalign -p -f 4`.

### 4.4 API Inspector (DAST / MITM Proxy)

Integrates `mitmdump` as a subprocess to monitor and manipulate HTTP/S traffic.

* **Live Traffic DB:** Writes all network packets into a local SQLite backing store (`api_traffic.db`).
* **Custom Extraction Logic:** Features a `ColumnConfigManager` allowing users to define custom table columns populated by extracting data dynamically from HTTP payloads via JSONPath, Regex, or specific Byte-Offsets.
* **Network Routing:** Configures Android Global Proxy settings over USB or WLAN dynamically and pushes CA-Certificates directly to the device.

### 4.5 Frida Advanced Manager (V8 & QuickJS)

A comprehensive IDE-like interface for managing and injecting dynamic instrumentation scripts written in modern JavaScript/TypeScript. It natively supports four distinct operational modes:

1. **Listen Mode:** The gadget opens a port. The framework establishes a TCP-over-USB tunnel and injects scripts on demand.
2. **Connect Mode (Reverse Connection):** The framework spawns a local `frida.PortalService` daemon. The app acts as a client, allowing hot-reloading.
3. **Script Mode (Build-Integrated):** A selected script is compiled via Node.js during the build phase and physically embedded into the APK.
4. **ScriptDirectory Mode:** The gadget monitors a specific directory on the device for on-the-fly execution (synchronization handled via `run-as`).

**The Ghost Protocol (Native File Trace):**
To bypass strict Android Zygote `stdout` restrictions, Logcat truncation, and UI-blocking timeouts during time-critical loops, the framework provides a "Ghost Log" fallback. By utilizing Frida's C-level File API (`frida_file_comm_extensive.js`) to write logs directly to `/data/data/{APP_PACKAGE}/ghost.log` and establishing a synchronous `adb shell run-as tail -f` background listener, the framework captures analysis data in real-time, completely bypassing the standard Android log buffers.

**MCP-Steuerung:** Betriebsmodi, App-Start-Verhalten (`pause_on_load`), Netzwerk/ScriptDirectory, der Build-Schalter (`INJECT_FRIDA`) sowie Skripte und Collections lassen sich vollständig über den MCP-Server steuern (§4.10); Skripte/Collections tragen dabei `version` + `description` wie die übrigen Artefakte, und der Gerätesync (Collection → Gerät) ist gated.

### 4.6 Device File Explorer

An integrated graphical file manager leveraging the `run-as` binary wrapper via ADB to bypass Android's Scoped Storage limitations, allowing direct navigation of the application's isolated sandbox (`/data/data/{APP_PACKAGE}/`) on non-rooted devices. Features include threaded downloads/uploads with progress tracking and direct file manipulation.

### 4.7 Test Management & History Tracker

Automatically records test sessions, including the deployed configurations, applied patches, and environmental observations. Data is retained persistently in `RE_History.json` and a SQLite Database. The framework also generates aggregated Markdown reports (`Kippy_RE_Log.md`) compiling injected codes and testing results suitable for vulnerability disclosures and documentation.
### 4.8 LibForge (Native Lib Builder)

Ergänzt den „Native Lib Replacer" um das **Erstellen, Kompilieren, Importieren und additive
Injizieren eigener nativer Bibliotheken**. Reiter im Workspace neben dem Replacer.

* **Editor & Compile:** C-Code mit Syntax-Highlighting; Kompilierung via NDK-`clang` zu `lib<name>.so`
  (`NativeCompilerService`). Build-Fehler erscheinen live in der Konsole (`CommandRunner.run_live` → EventBus).
* **Import:** Fertige `.so` per Filedialog importierbar (ohne Quellcode/Build).
* **Verwaltung:** Persistenz in `data/native_libs.json` + `data/native_libs/<id>/` (`NativeLibManager`).
  Libs sind **aktiv/inaktiv** schaltbar (ohne Löschen) und per Button/„Entf" **löschbar**; **Recompile
  überschreibt** die `.so`. Beschreibung + Code stehen parallel zur Lib. Jede Lib führt zusätzlich eine `version` (in die Beschreibung als `[vX]` gespiegelt); die Persistenz ist **konfliktsicher** (atomar + Three-Way-Merge, siehe §4.10), sodass GUI- und MCP-Änderungen sich nicht gegenseitig überschreiben.
* **Toolchain:** Der `ToolManager` erkennt ein installiertes NDK, lädt es bei Bedarf herunter
  (gepinnt `NDK_FALLBACK_VERSION`), verifiziert `clang` und injiziert das Toolchain-`bin` dynamisch
  in den `PATH`. Neuer Pipeline-Step `inject_added_libs` (in `BUILD_NATIVE`) spielt alle **aktiven**
  Libs additiv in `lib/<abi>/` ein (Clobber-Guard: überschreibt keine echten App-Libs).
* **Laden:** bleibt manuell per Smali-Patch. LibForge zeigt/kopiert den `System.loadLibrary`-Snippet;
  **Smali Studio** hat zusätzlich einen **loadLibrary-Helfer** mit Dropdown der aktiven Libs
  (tippfehlerfrei, fügt den Smali-Block am Cursor ein).
* **Config-Keys** (`config_manager.py` / `config.json`): `NDK_DIR`, `DEFAULT_ABI`, `DEFAULT_API_LEVEL`,
  `NDK_FALLBACK_VERSION`.

### 4.9 ExeDeploy (Executable Runner + Sync)

Ergänzt LibForge um das **Deployen, Ausführen, Synchronisieren und Aufräumen** eigener nativer
**Executables** (PIE-Binaries wie `skb_oracle`) — ohne Root, konsequent über `adb` + `run-as`.
Ein `OUTPUT_KIND = executable`-Target wird nicht per `loadLibrary` injiziert, sondern über ein
**„Deploy & Run"-Panel** gesteuert.

* **Runner (`DeviceExecService`):** Push von Binary + wählbaren Runtime-Deps in ein exec-erlaubtes
  Verzeichnis (Default `/data/local/tmp`), `chmod 755`; geräteinternes **Input-Staging** von
  App-Home-Dateien (`run-as <pkg> cat … > <run_dir>/…`, kein PC-Roundtrip); **nicht-blockierende**
  Ausführung (parallel zur App) mit Live-Stream; `Stop` beendet lokalen Prozess **und** Remote
  (`pkill`); `Cleanup`; optionaler Ergebnis-`pull`.
* **Farbige, getaggte Konsole:** stdout/stderr laufen als `[<exe>] …` in die farbcodierte Konsole
  des Reiters „🚀 App Start & Live-Log" (EventBus-Event `EXEC_OUTPUT`); Grün=stdout, Rot=stderr,
  optional eigene Farbe je Executable (`EXEC_COLOR_PER_EXE`).
* **Zwei Bedien-Orte (ein Controller):** dasselbe `ExecRunPanel` erscheint im LibForge-Reiter **und**
  im „App Start & Live-Log"-Reiter; beide nutzen den gemeinsamen `ExecRunController`
  (`app.exec_run_controller`). Ziel-Dropdown listet alle Executable-Targets
  (`NativeLibManager.get_executables()`).
* **Filemanager-Integration:** Buttons **„📂 Executable-Ordner"** (Run-Dir, Shell-Domain via
  `adb shell ls`) und **„📁 App-Ordner"** (App-Home, `run-as`) springen direkt in den File-Explorer.
  Der Filemanager unterstützt jetzt zusätzlich **Multi-Datei- und rekursiven Ordner-Upload**
  (`push_files`/`push_dir`) sowie einen **Shell-Domain-Modus** (`/data/local/tmp`).
* **Local↔Remote-Sync (`DeviceSyncService`):** Push/Pull zwischen lokalem Target-Ordner und Run-Dir
  mit **lokaler versionierter Sicherung** vor jedem Überschreiben (`data/exec_backups/<tag>/<ts>/`,
  Retention `EXEC_BACKUP_KEEP`).
* **Config-Keys** (`config_manager.py` / `config.json`): `EXEC_RUN_DIR_DEFAULT`, `EXEC_BACKUP_KEEP`,
  `EXEC_COLOR_PER_EXE`.
* **Nicht gerootet / Datenschutz:** ausschließlich `adb` + `run-as`, niemals `su`; Capture-Dateien
  bleiben durch geräteinternes Staging auf dem Gerät (Ergebnis-`pull` nur explizit).

### 4.10 MCP-Server (KI-Agent-Steuerung)

Exponiert das Framework über das **Model Context Protocol** (`mcp_server/`), sodass ein KI-Client (z. B. Claude Desktop) Analyse und Vorbereitung fernsteuern kann — im **Prepare-&-Observe-Betriebsmodell**: der Agent bereitet vor und beobachtet, der Nutzer baut/flasht/startet in der GUI.

* **Zwei Transporte:** ein **stdio-Server** (vom Desktop-Client gespawnt) und ein optionaler **HTTP-Server** (`127.0.0.1`, verwaltet über die GUI-Seite „🔌 MCP“). Beide teilen `config.json`.
* **Gating in drei Stufen (`registry.py` + `gating.py`):** **P** (Prepare) und **O** (Observe) sind frei; **G** (Gated — Bauen/Flashen/Starten, File Manager, Löschen, Gerätesync) braucht Pro-Tool-Schalter **+** Kategorie-Freigabe (`caps`) **+** `confirm=true`. Jeder Aufruf läuft durch `runtime.run` (Gating → Ausführung unter EventBus-Sammler → Audit `data/mcp_audit.jsonl`); GUI-Rechte werden pro Aufruf frisch eingelesen.
* **Tool-Katalog (Gruppen A–L):** Workspace/Config, Smali-Analyse, LibForge, Patch-Favoriten, Build/Flash, File Manager, Executables (ExeDeploy), Capture/Logs, DAST/API, Historie, Selbstauskunft und **Frida** (14 Tools: Config/Betriebsmodi/App-Start/Netz/ScriptDir, Build-Toggle `INJECT_FRIDA`, Skripte + Collections inkl. Versionierung, Aktiv-Setzen, gated Gerätesync).
* **Versionierungs-Disziplin (Pflicht):** Libs, Patch-Favoriten **und** Frida-Skripte/Collections tragen `version` + `description` (als `[vX]` in die Beschreibung gespiegelt). Bugfix am selben Pfad → `*.update` mit Versions-Bump; neuer Ansatz → `*.create`/`*.add` (Original bleibt unangetastet). Ohne Bump: Warnung, aber Speichern (warn-but-allow).
* **Konfliktsichere Persistenz (`core/infrastructure/json_store.py`):** GUI und MCP laufen als getrennte Prozesse. Alle JSON-Stores (`native_libs.json`, `favorite_patches.json`, `frida_scripts.json`) sowie `config.json` schreiben **atomar** (Temp + `os.replace`) und mergen per **Three-Way-Merge** (key = `id`/`name`) gegen die frische Platten-Version — parallele Änderungen gehen nicht verloren. Ein **mtime-gated Reload** (`reload_if_changed`) macht Fremd-Änderungen sofort sichtbar; die GUI hat je einen **„🔄 Aktualisieren“-Button** (LibForge, Favoriten- und Frida-Dialog).
* **Nicht gerootet / Datenschutz:** dieselben `adb`/`run-as`-Grenzen wie die GUI; personenbezogene Werte/Schlüssel bleiben lokal (Tools liefern Labels/Längen/Booleans statt Klartext).
