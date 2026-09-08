# Android RE Patching Framework - Technical Documentation

A Python-based automation utility designed for Android reverse engineering workflows.

This application automates the sequential execution of unpacking, binary hex patching, Smali modification, repackaging, cryptographic signing, ADB sideloading, and logcat tracing. It facilitates the local integration of static code analysis and dynamic instrumentation for applications, including native Java/Kotlin builds and Dart AOT-compiled (Flutter) applications.

⚠️ **Disclaimer:**

*This project is provided strictly for educational purposes and security research. The repository does not distribute copyrighted APK files or proprietary binaries. Users must supply legally obtained binaries. The tools are intended exclusively for local testing environments during security analysis*.

---

## 1. Software Architecture & Design Patterns

The application is written in Python and utilizes `tkinter` for its graphical user interface. To prevent the main thread from blocking during file I/O or computationally intensive operations, the architecture implements the Model-View-Controller (MVC) paradigm alongside an event-driven Service-Oriented Architecture (SOA).

* **Strict MVC Implementation:** The graphical user interfaces (Views) are decoupled from business logic. All data processing and subprocess executions are delegated to specific controllers (e.g., `WorkspaceController`, `FridaManagerController`) and stateless service classes.
* **EventBus (Pub/Sub):** Cross-module communication is handled via a centralized event bus (`EventBus`). Background threads, such as those reading the ADB logcat output, processing RPC messages from Frida, or calculating code diffs, publish events that UI components subscribe to, preventing hard dependencies and Tkinter threading conflicts.
* **Pipeline Engine (Command Pattern):** The build and modification sequence is executed based on an iterable JSON configuration (`config.json`) rather than imperative hardcoding. The `PipelineEngine` instantiates classes implementing the `PipelineStep` interface for each configured operation.

### 1.1 Directory Structure & Core Files

The framework is organized into a modular directory structure, separating state management, business logic, background services, and graphical interfaces.

```text
├── main.py                     # Entry point and environment bootstrapper (dependency injection, PATH resolution)
├── gui.py                      # Main Tkinter application class and tab initialization
├── requirements.txt            # Python dependencies (mitmproxy, frida-tools, etc.)
│
├── core/                       # Application core and business logic
│   ├── application/            # Global state and communication (EventBus, SessionState)
│   ├── domain/                 # Domain models (e.g., FridaConfig, FridaScript) and exceptions
│   ├── infrastructure/         # Low-level system interactions (CommandRunner, ConfigManager, ToolManager)
│   ├── pipeline/               # Build process orchestration (PipelineEngine, PipelineStep)
│   └── fuzzing_engine.py       # Opcode normalization and difflib-based heuristic search
│
├── services/                   # Stateless background services
│   ├── adb_network_service.py  # ADB proxy routing and certificate pushing
│   ├── api_db_service.py       # SQLite database operations for intercepted HTTP traffic
│   ├── frida_compiler_service.py # Node.js workspace setup and JS/TS compilation via frida-compile
│   ├── frida_server_service.py # TCP-Tunnel and PortalService daemon for reverse connections
│   ├── frida_sync_service.py   # ADB push/pull logic bypassing Scoped Storage via run-as
│   ├── logcat_service.py       # Asynchronous ADB logcat trace capturing & Frida log routing
│   ├── smali_search_service.py # Threaded RAM caching and text indexing
│   └── ...
│
├── ui/                         # Graphical User Interface (MVC Implementation)
│   ├── controllers/            # Logic handlers receiving UI events (Workspace, FridaManager, etc.)
│   ├── dialogs/                # Popup windows (e.g., FridaManagerDialog with Drawer-Pattern)
│   ├── tabs/                   # Main notebook sections (Workspace, Smali Studio, API Inspector)
│   └── widgets/                # Reusable UI components (e.g., SmaliEditorWidget)
│
├── data/                       # Persistent JSON data and databases (auto-generated)
├── tools/                      # External dependencies (auto-downloaded by ToolManager)
├── source/                     # Original target APK files
├── destination/                # Output directory for patched, aligned, and signed APKs
└── archives/                   # Auto-generated backup directories containing build artifacts and trace logs

```

---

## 2. Core Algorithms & Data Processing

### 2.1 Fuzzing Engine & Heuristic Matching

To maintain the applicability of Smali patches across different application versions, the software utilizes a two-stage heuristic search algorithm when static offsets fail.

* **Opcode Normalization:** Prior to code comparison, volatile metadata is stripped using regular expressions (e.g., `.line` directives, comments, and Dalvik registers are genericized).
* **Search Phases:** The engine performs a fast signature-based search, followed by a global sequence-matching (`difflib`) across the RAM cache, pre-filtered by string literals to optimize CPU usage.

### 2.2 Text Rendering & Threading (Smali Studio)

Decompiled Smali files can be excessively large. To prevent UI freezing:

* **Viewport Lazy-Highlighting:** The regex-based syntax highlighting engine evaluates only the currently visible viewport rather than the entire text buffer, debounced via scroll events.
* **Asynchronous Diffing:** For side-by-side code comparisons, the `SequenceMatcher.get_opcodes()` calculation is dispatched to a background daemon thread.

---

## 3. System Requirements & Bootstrapping

The framework requires **Python 3.x**, the Android SDK Platform-Tools (`adb`), and a Java Development Kit (JDK).

**Automated Bootstrapping (`ToolManager`):**
Upon execution (`main.py`), the application initiates a `bootstrap_environment()` routine. It dynamically resolves or downloads missing third-party binaries:

* **Java Utilities:** `apktool`, `APKEditor.jar`, `uber-apk-signer.jar`
* **Dynamic Instrumentation:** `libfrida-gadget.so` (v17+)
* **Node.js Environment:** On Windows, a portable Node.js runtime is automatically downloaded to handle `frida-compile` operations seamlessly without requiring a system-wide installation.

Absolute paths to these executables are dynamically injected into the runtime `os.environ["PATH"]`.

---

## 4. Module Specifications

### 4.1 App Manager

Integrates ADB commands to extract APK files directly from a connected Android device (`pm list packages -3` -> `pm path` -> `adb pull`). Detects the target CPU architecture and configures the workspace automatically.

### 4.2 Smali Studio (Static Analyzer)

An integrated text editor for Dalvik bytecode manipulation. Features a threaded RAM indexer, a Callgraph & XREF engine (distinguishing between app methods and System APIs), and allows the injection of predefined Smali templates (`snippets.json`).

### 4.3 Patch Management & Build Pipeline

Coordinates source code modifications (Smali, Hex, Native Libs) and dynamically reconstructs the application.

* **RASP Evasion & SELinux Compliance:** During the build pipeline, the Frida Gadget is disguised (e.g., renamed to `libmetrics.so`) to bypass static string-based RASP memory scans. Additionally, `inotify` hooks are disabled (`"on_change": "ignore"`) to prevent SELinux-induced crashes on restrictive devices.

### 4.4 API Inspector (DAST / MITM Proxy)

Integrates `mitmdump` as a subprocess to monitor and manipulate HTTP/S traffic. Features dynamic payload interception rules and an extraction engine using JSONPath or Byte-Offsets to populate UI tables with data from the SQLite backing store (`api_traffic.db`).

### 4.5 Frida Advanced Manager (V8)

A comprehensive IDE-like interface for managing and injecting dynamic instrumentation scripts written in modern JavaScript/TypeScript. It natively supports four distinct operational modes for the Frida Gadget:

1. **Listen Mode:** The gadget opens a port on the device. The framework establishes a TCP-over-USB tunnel and injects scripts on demand.
2. **Connect Mode (Reverse Connection):** The framework spawns a local `frida.PortalService` daemon. The app acts as a client and connects to the host, allowing hot-reloading and live script pushes.
3. **Script Mode (Build-Integrated):** For autonomous operation, a selected script is compiled via Node.js during the build phase, embedded physically into the APK, and loaded automatically by the gadget upon app initialization.
4. **ScriptDirectory Mode:** The gadget monitors a specific directory on the device for on-the-fly execution.
* *Bypassing Scoped Storage:* The built-in Device Sync utilizes `adb shell run-as {APP_PACKAGE}` to securely push script collections into the isolated `/data/data/...` directory of the target app.



**UI Features:** Includes a multi-tab layout with an integrated JS/TS editor (featuring custom syntax highlighting), persistent collection management, and a collapsable side-panel (Drawer-Pattern) for device synchronization. Logs from both RPC-based and native `__android_log_print` executions are harmonized and routed to the central workspace consoles.
