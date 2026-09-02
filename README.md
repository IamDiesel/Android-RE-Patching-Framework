
# Android RE Patching Framework - Technical Documentation

A Python-based automation utility designed for Android reverse engineering workflows.

This application automates the sequential execution of unpacking, binary hex patching, Smali modification, repackaging, cryptographic signing, ADB sideloading, and logcat tracing. It facilitates the local integration of static code analysis and dynamic instrumentation for applications, including native Java/Kotlin builds and Dart AOT-compiled (Flutter) applications.

⚠️ **Disclaimer:**

*This project is provided strictly for educational purposes and security research. The repository does not distribute copyrighted APK files or proprietary binaries. Users must supply legally obtained binaries. The tools are intended exclusively for local testing environments during security analysis*.

---

## 1. Software Architecture & Design Patterns

The application is written in Python and utilizes `tkinter` for its graphical user interface. To prevent the main thread from blocking during file I/O or computationally intensive operations, the architecture implements the Model-View-Controller (MVC) paradigm alongside an event-driven Service-Oriented Architecture (SOA).

* **Strict MVC Implementation:** The graphical user interfaces (Views) are decoupled from business logic. All data processing and subprocess executions are delegated to specific controllers (e.g., `WorkspaceController`, `FuzzyMatchController`) and stateless service classes.


* **EventBus (Pub/Sub):** Cross-module communication is handled via a centralized event bus (`EventBus`). Background threads, such as those reading the ADB logcat output or calculating code diffs, publish events that UI components subscribe to, preventing hard dependencies and Tkinter threading conflicts.


* **Pipeline Engine (Command Pattern):** The build and modification sequence is executed based on an iterable JSON configuration (`config.json`) rather than imperative hardcoding. The `PipelineEngine` instantiates classes implementing the `PipelineStep` interface (e.g., `DecompileStep`, `InjectFridaStep`) for each configured operation.

### 1.1 Directory Structure & Core Files

The framework is organized into a modular directory structure, separating state management, business logic, background services, and graphical interfaces. The workspace and data directories are dynamically generated during the initial execution.

```text
├── main.py                     # Entry point and environment bootstrapper (dependency injection, PATH resolution)[cite: 51]
├── gui.py                      # Main Tkinter application class and tab initialization[cite: 52]
├── requirements.txt            # Python dependencies (mitmproxy, lief, frida-tools)[cite: 50]
├── .gitignore                  # Exclusion rules for caches, APKs, and local workspaces[cite: 55]
│
├── core/                       # Application core and business logic
│   ├── application/            # Global state and communication
│   │   ├── event_bus.py        # Central Pub/Sub event dispatcher[cite: 39]
│   │   └── session_state.py    # Singleton holding active patches and UI states[cite: 38]
│   ├── domain/                 # Domain models and custom exceptions
│   │   └── exceptions.py       # E.g., PatchConflictException for structural mismatch[cite: 40]
│   ├── infrastructure/         # Low-level system interactions
│   │   ├── command_runner.py   # Wrapper for blocking/background subprocess executions[cite: 41]
│   │   ├── config_manager.py   # JSON configuration and absolute path resolution[cite: 42]
│   │   └── tool_manager.py     # Automated downloader and PATH injector for third-party binaries[cite: 43]
│   ├── pipeline/               # Build process orchestration
│   │   ├── engine.py           # Command pattern executor reading from config.json[cite: 44]
│   │   ├── step_interface.py   # Abstract base class for all pipeline steps[cite: 45]
│   │   └── steps/              # Specific build implementations (e.g., DecompileStep, SmartPatchStep)[cite: 44]
│   ├── fuzzing_engine.py       # Opcode normalization and difflib-based heuristic search[cite: 49]
│   └── data_extractor.py       # Regex, JSONPath, and offset parser for the API Inspector[cite: 48]
│
├── services/                   # Stateless background services
│   ├── adb_network_service.py  # ADB proxy routing and certificate pushing[cite: 27]
│   ├── api_db_service.py       # SQLite database operations for intercepted HTTP traffic[cite: 28]
│   ├── callgraph_service.py    # Method node and edge mapping for Smali references[cite: 23]
│   ├── logcat_service.py       # Asynchronous ADB logcat trace capturing[cite: 36]
│   ├── proxy_service.py        # mitmdump subprocess management[cite: 30]
│   ├── smali_search_service.py # Threaded RAM caching and text indexing[cite: 33]
│   ├── smali_parser.py         # Regex parsing for methods, fields, and instructions[cite: 32]
│   └── patch_service.py        # Evaluation logic for hex, smali, and library replacements[cite: 37]
│
├── ui/                         # Graphical User Interface (MVC Implementation)
│   ├── controllers/            # Logic handlers receiving UI events
│   │   ├── workspace_controller.py      # Pipeline and session execution logic[cite: 60]
│   │   ├── fuzzy_match_controller.py    # Asynchronous fuzzing and diffing operations[cite: 59]
│   │   └── favorite_patches_controller.py # Batch patch orchestration[cite: 57]
│   ├── dialogs/                # Popup windows ("Dumb Views")
│   │   ├── favorite_patches_dialog.py   # UI for batch patch execution and management[cite: 57]
│   │   └── fuzzy_matcher_dialog.py      # UI for resolving patch conflicts via diffing[cite: 59]
│   ├── tabs/                   # Main notebook sections ("Dumb Views")
│   │   ├── workspace_tab.py    # Build controls, patch staging, and manifest strategies[cite: 57]
│   │   ├── smali_studio_tab.py # IDE layout integrating the editor, outline, and graphs[cite: 15]
│   │   └── api_inspector_tab.py# DAST interface displaying intercepted database traffic[cite: 10]
│   └── widgets/                # Reusable UI components
│       └── smali_editor_widget.py # Custom Tkinter Text widget with lazy-highlighting[cite: 17]
│
├── data/                       # Persistent JSON data and databases (auto-generated)[cite: 42]
│   ├── snippets.json           # Smali code injection templates[cite: 34]
│   ├── favorite_patches.json   # Stored hex and smali patches for cross-version application[cite: 60]
│   ├── RE_History.json         # Structured JSON array documenting build runs and results[cite: 26]
│   ├── Kippy_RE_Log.md         # Formatted markdown output of the patching history[cite: 26]
│   └── api_traffic.db          # SQLite storage for mitmproxy captures[cite: 28]
│
├── tools/                      # External dependencies (auto-downloaded by ToolManager)[cite: 43]
│   ├── APKEditor.jar           # AXML compilation and manifest manipulation[cite: 42]
│   ├── uber-apk-signer.jar     # v1/v2/v3 cryptographic signing utility[cite: 42]
│   ├── platform-tools/         # Directory containing the ADB binary (Windows)[cite: 43]
│   └── libfrida-gadget.so      # Frida native dynamic instrumentation payload[cite: 43]
│
├── source/                     # Original target APK files[cite: 42]
├── destination/                # Output directory for patched, aligned, and signed APKs[cite: 42]
└── archives/                   # Auto-generated backup directories containing build artifacts and trace logs[cite: 42]

```

---

## 2. Core Algorithms & Data Processing

### 2.1 Fuzzing Engine & Heuristic Matching

To maintain the applicability of Smali patches across different application versions, the software utilizes a two-stage heuristic search algorithm when static offsets fail.

* **Opcode Normalization:** Prior to code comparison, volatile metadata is stripped using regular expressions. This includes removing `.line` directives, comments (`#`), and normalizing Dalvik registers (e.g., converting `v0` or `p1` to a generic `REG` token) to focus solely on the structural logic of the opcodes.


* **Search Phases:**
1. **Fast Search:** The engine initially searches strictly within the defined target file based on the method signature.


2. **Sequence Matching:** If an exact signature is not found, the algorithm utilizes `difflib.SequenceMatcher` to evaluate the similarity of normalized code blocks, applying a defined threshold (e.g., ratio `>0.85`).


3. **Deep Search (Pre-Filtered):** In cases involving file renaming or obfuscation, the engine executes a global RAM cache search. To optimize processing, it pre-filters the cache by extracting string literals (length > 3) from the original patch and excluding files that lack these strings, before executing the CPU-intensive `difflib` calculations on the remaining set.





### 2.2 Text Rendering & Threading (Smali Studio)

Decompiled Smali files can be excessively large, necessitating specific rendering techniques to prevent UI freezing:

* **Viewport Lazy-Highlighting:** The regex-based syntax highlighting engine evaluates only the currently visible viewport rather than the entire text buffer. This is achieved by mapping the Tkinter screen coordinates (`@0,0` to the widget height) and debouncing the execution via `after()` events bound to scroll actions.


* **Asynchronous Diffing:** For side-by-side code comparisons in the conflict resolution dialogs, the `SequenceMatcher.get_opcodes()` calculation is dispatched to a background daemon thread. Upon completion, the main thread safely applies the text tags (`diff_add`, `diff_del`).



---

## 3. System Requirements & Bootstrapping

The execution of the framework requires the following dependencies:

1. **Python 3.x** (with `tkinter` support).


2. **mitmproxy:** Required for the DAST API inspector module (`pip install mitmproxy`).


3. **Android SDK Platform-Tools:** The `adb` binary is necessary for device communication.


4. **Java Development Kit (JDK):** Required for executing `.jar`-based utilities like Apktool and APK-Signer.



**Automated Bootstrapping:**
Upon execution (`main.py`), the application initiates a `bootstrap_environment()` routine. It dynamically resolves or downloads missing third-party binaries (e.g., Apktool, APKEditor, uber-apk-signer, and libfrida-gadget.so). The absolute paths to these executables are injected into the runtime `os.environ["PATH"]`, enabling the Python subprocesses to invoke them directly without requiring system-wide environment variable configurations.

---

## 4. Module Specifications

### 4.1 App Manager

A UI frontend integrating ADB commands to extract APK files from a connected Android device.

* Executes `adb shell pm list packages -3` to retrieve third-party applications.


* Retrieves base and split APKs using `adb shell pm path` followed by `adb pull`.


* Detects the target CPU architecture (e.g., `arm64_v8a`, `x86_64`) based on the extracted split APK filenames and configures the workspace accordingly.



### 4.2 Smali Studio (Static Analyzer)

An integrated text editor specialized for Smali code manipulation.

* **RAM Indexer:** Loads decompiled `.smali` files into system memory using a `concurrent.futures.ThreadPoolExecutor` and caches the state via `pickle` serialization to accelerate file I/O operations across sessions.


* **XREF & Callgraph Engine:** Parses method invocations (matching regex `invoke-.*`) to construct a hierarchical call graph. It differentiates between application methods and System APIs (e.g., `Ljava/`, `Landroid/`).


* **Data Flow Analysis:** Extracts variable read operations (`iget`/`sget`) and write operations (`iput`/`sput`) from smali blocks to map data flow.


* **Code Struct Injection:** Permits the insertion of predefined Smali templates from `snippets.json` or the programmatic creation of entirely new `.smali` class files (e.g., Android BroadcastReceivers) into the workspace.



### 4.3 Patch Management & Build Pipeline

Coordinates source code modifications and native binary replacements.

* **Favorites Manager (`favorite_patches.json`):** Stores structured JSON definitions for multiple patch types, including Smali edits, hexadecimal byte replacements for native `.so` libraries (e.g., `libflutter.so`), and entire native library replacements. These are applied consecutively via a batch processing loop.


* **Manifest Strategies:** Allows the user to select the compilation engine (`Apktool` or `APKEditor`) to circumvent AXML parsing errors during the build phase.


* **Pipeline Modifications:** Automates the injection of Frida Gadget `.so` files, applies LSPatch Xposed modules, modifies the Network Security Config (NSC) to accept user-level certificates, signs the resulting APK, and executes `adb install`.



### 4.4 API Inspector (DAST / MITM Proxy)

Integrates `mitmdump` as a subprocess to monitor and manipulate HTTP/S traffic.

* **Traffic Routing:** Executes `adb reverse tcp:8080 tcp:8080` or manipulates `adb shell settings put global http_proxy` to route application traffic through the host machine's proxy instance.


* **Rule-based Interception:** Users can define URL-matching rules to dynamically alter request or response payloads before they reach the client or server.


* **Custom Data Extraction:** Features a parsing engine that isolates specific values from recorded traffic. It utilizes JSONPath expressions, Regex capturing groups, or byte offset specifications (converting extracted bytes to string, hex, or int arrays) to populate dynamic columns in the UI.


* **Persistence:** Intercepted packets are serialized into a local SQLite database (`api_traffic.db`) to enable post-execution analysis and modification.