# Pipeline Settings & Orchestration Guide

The "Settings" tab serves as the central orchestration blueprint for your entire reverse engineering framework. While you manipulate code, fill out tables, or toggle switches in other tabs, the JSON pipelines defined in the settings dictate the exact, deterministic sequence of physical operations executed on the target APKs[cite: 17, 62].

This document provides a comprehensive, deep-dive explanation of how to understand, modify, and extend the pipeline engine to suit advanced security analysis workflows.

---

## 1. The Intersection of GUI Toggles and JSON Settings

To master the framework's architecture, you must distinguish between **Structure** and **State**. 

The JSON array in the settings defines the *structure* (the exact chronological order of operations)[cite: 62]. The checkboxes, text fields, and tables in the UI define the *state* (the actual data to inject, or whether a specific operation should apply its modifications or perform a cleanup routine)[cite: 13, 19].

When you click a build button in the graphical user interface, the `PipelineEngine` looks up the corresponding array by its key in the JSON configuration and executes it sequentially[cite: 53]:

*   **"📦 Unpack APK"** (located in Smali Studio) triggers the `"PREPARE_WORKSPACE"` pipeline[cite: 12, 62].
*   **"⚙️ Nur BUILD"** (located in the Workspace) triggers the `"BUILD_NATIVE"` pipeline[cite: 13, 62].
*   **"📱 Nur FLASH"** (located in the Workspace) triggers the `"FLASH"` pipeline[cite: 13, 62].
*   **"🚀 1-Click"** (located in the Workspace) triggers `"BUILD_NATIVE"` and, upon successful completion, immediately triggers `"FLASH"`[cite: 13, 62].

---

## 2. Pipeline Step Anatomy: `name` vs. `type`

To avoid confusion when reading or extending the JSON settings, it is crucial to understand the difference between cosmetic text and strict system commands. Every step in a pipeline is a JSON object requiring at least two mandatory keys[cite: 53, 54].

### The Blueprint of a Step

```json
{
    "name": "Any Free Text You Want To Describe This Step", 
    "type": "the_strict_system_command_keyword"
}

```

### Understanding the Keys

* **`"name"` (Cosmetic Logging):**
This is purely arbitrary free text. The underlying Python execution engine ignores this value entirely when determining what to do. It is exclusively used to print a human-readable status message to your live console during the build process (e.g., *"Currently executing: Any Free Text You Want To Describe This Step"*). You can write anything here that helps you understand your pipeline.


* **`"type"` (The Actual System Command):**
This is the strict, hardcoded internal keyword that instructs the framework which specific Python script/class to execute under the hood (e.g., `"inject_frida"`, `"smart_patch"`, or `"anchor_patch"`).



### Where does the data come from?

You will notice that most steps (like `"anchor_patch"` or `"inject_custom_libs"`) do not have any additional parameters in the JSON. This is by design: **they automatically fetch their data from the GUI state**.

When the pipeline reaches `{"type": "anchor_patch"}`, the engine queries the `SessionState`, looks at the "Hex Patcher" table in your Workspace tab, and applies whatever hex offsets you typed into that UI component. The JSON simply tells the system *when* to execute the step, while the UI tells the system *what* data to use.

> **Exception:** The `"type": "cmd"` step is unique. Because it executes raw, generic terminal commands that aren't defined by a specific GUI table, it requires additional JSON attributes like `"cmd"` (the command string) and `"cwd"` (the working directory).
> 
> 

---

## 3. Dynamic Artifacts and Environment Variables

For steps that execute raw terminal commands (`"type": "cmd"`), the framework provides a powerful templating system to inject contextual paths dynamically. These artifacts are resolved right before a step executes, ensuring cross-platform compatibility without hardcoding absolute paths.

Here are the available dynamic macros you can use inside your JSON configurations:

* **`{BASE_DIR}`**:
The absolute root directory of your current Kippy-RE workspace.


* **`{APP_PACKAGE}`**:
The Android package name of the target application (e.g., `com.example.app`).


* **`{SPLIT_NAME}`**:
The active split name currently being processed (e.g., `split_config.arm64_v8a`).


* **`{APP_SOURCE_DIR}`**:
The directory holding the pristine, original pulled APK files before any modifications.


* **`{DEST_DIR}`**:
The output directory where patched, aligned, and signed APKs are compiled and stored.


* **`{EXTRACT_DIR}`**:
The path to the specific extracted split content folder.


* **`{ARCHIVE_DIR}`**:
The backup directory used for storing generated logs, Callgraph SVGs, and historical session artifacts.


* **`{SIGNED_APKS}`**:
A dynamic macro evaluated at runtime that resolves to a space-separated list of all successfully signed APKs located in the `{DEST_DIR}`. It is heavily used for bulk `adb install-multiple` or `adb install` commands.


* **`{PID}`**:
The Process ID of the running target application. This is resolved dynamically via `adb shell pidof {APP_PACKAGE}` at runtime and is primarily used to bind Logcat tracing specifically to the target app.


* **`{SIGNER_JAR}` / `{APKEDITOR_JAR}**`:
The absolute paths to the respective Java compilation and signing tools located in the `tools/` directory.



---

## 4. The Default `BUILD_NATIVE` Settings in Detail

The default configuration ships with production-ready pipelines. To understand how the engine orchestrates a full patching lifecycle, let's break down the primary `"BUILD_NATIVE"` sequence step-by-step:

```json
"BUILD_NATIVE": [
    {
        "name": "Mirror Original Workspace", 
        "type": "mirror_workspace"
    },
    {
        "name": "Apply Smali Patches", 
        "type": "smart_patch"
    },
    {
        "name": "Inject Custom Libs", 
        "type": "inject_custom_libs"
    },
    {
        "name": "Inject Frida Gadget", 
        "type": "inject_frida"
    },
    {
        "name": "Manifest & Build (Dynamic Strategy)", 
        "type": "manifest_and_build"
    },
    {
        "name": "Apply LSPatch", 
        "type": "apply_lspatch"
    },
    {
        "name": "Clean old signatures", 
        "type": "cmd", 
        "cmd": "del /Q /S \"*-debugSigned*.apk\" 2>nul", 
        "cwd": "{DEST_DIR}"
    },
    {
        "name": "Sign all APKs", 
        "type": "cmd", 
        "cmd": "java -jar \"{SIGNER_JAR}\" -a . --skipZipAlign --allowResign", 
        "cwd": "{DEST_DIR}"
    }
]

```

### Technical Workflow Breakdown:

1. **`mirror_workspace`:**
Creates an ephemeral copy of the pristine, decompiled source code into the destination directory. This guarantees a stateless, uncontaminated foundation for every build, preventing legacy patches from breaking new compilations.


2. **`smart_patch`:**
Evaluates your UI-defined Smali modifications and applies them to the mirrored codebase. It executes heuristic opcode matching if structural deviations are detected.


3. **`inject_custom_libs`:**
Overwrites native `.so` libraries (e.g., patched Unity or Flutter engines) within the designated architecture folders based on the UI table.


4. **`inject_frida`:**
Evaluates the UI checkbox. If enabled, it obfuscates and injects the Frida Gadget (v17+) and its JSON config. If disabled, it performs an active cleanup of residual Frida artifacts.


5. **`manifest_and_build`:**
Injects the Network Security Config (for MITM proxying), alters the `android:debuggable` flag, and recompiles the APK using the selected compiler (`apktool`, `apkeditor`, or `aapt2`).


6. **`apply_lspatch`:**
Dynamically repacks the newly built APK with Non-Root Xposed modules (like TrustMeAlready) via `lspatch.jar` if toggled in the UI.


7. **`cmd` (Clean):**
Executes a shell command to delete legacy signed APKs to prevent signature collisions.


8. **`cmd` (Sign):**
Invokes `uber-apk-signer.jar` to cryptographically sign and align the repackaged binary.



---

## 5. Pipeline Step Types (`type`) and Practical Use Cases

Below is a comprehensive list of all available `type` commands registered in the `PipelineEngine`.

*Note: The examples provided under each type use different `"name"` values to illustrate various real-world use cases. However, the technical background logic executed by the framework remains exactly the same for all examples within a specific category.*

### A. General Shell Execution (`type: "cmd"`)

Executes arbitrary system terminal commands. This is the only step that requires additional JSON arguments (`cmd` and `cwd`).

✨ **Example 1: Automating ADB Transfers**
Automating post-build tasks like uploading bypass configs to SD cards to skip initial setup screens.

```json
{
    "name": "Push Bypass Config", 
    "type": "cmd", 
    "cmd": "adb push bypass.xml /sdcard/Android/data/{APP_PACKAGE}/files/", 
    "cwd": "{BASE_DIR}"
}

```

✨ **Example 2: Invoking External Python Deobfuscators**
Executing custom third-party Python scripts against the decompiled source before recompilation happens.

```json
{
    "name": "Run String Deobfuscator", 
    "type": "cmd", 
    "cmd": "python scripts/deobfuscate.py --target .", 
    "cwd": "{DEST_DIR}/{APP_PACKAGE}"
}

```

✨ **Example 3: Pre-Build Environment Cleanup**
Clearing specific cache directories on your host machine before starting a fresh build cycle.

```json
{
    "name": "Clear Temp Cache", 
    "type": "cmd", 
    "cmd": "rm -rf .tmp_build_cache/*", 
    "cwd": "{BASE_DIR}"
}

```

---

### B. Preparation & File Management

These steps handle the initial extraction, synchronization, and merging of the APK files.

#### `type: "mirror_workspace"`

Synchronizes the pristine source code to the destination directory.

✨ **Example 1: Standard Build Sync**
Used at the beginning of `BUILD_NATIVE` to ensure a clean slate before applying patches.

```json
{
    "name": "Mirror Original Workspace", 
    "type": "mirror_workspace"
}

```

✨ **Example 2: Restoring a Clean Baseline**
Can be used as a standalone pipeline to quickly discard broken modifications and restore the `{DEST_DIR}` to the original pulled state without rebuilding.

```json
{
    "name": "Restore Clean Baseline", 
    "type": "mirror_workspace"
}

```

✨ **Example 3: Baseline for Differential Patching**
Mirrored directly before injecting Frida, ensuring a clean state for debugging instrumentation without legacy code interfering.

```json
{
    "name": "Sync Clean Build", 
    "type": "mirror_workspace"
}

```

#### `type: "decompile"`

Decompiles the APK using Apktool or APKEditor based on your UI strategy.

✨ **Example 1: Standard Extraction**
The default approach to unpack resources and bytecode.

```json
{
    "name": "Decompile APK", 
    "type": "decompile"
}

```

✨ **Example 2: Extraction After Split-Merge**
Used sequentially right after a split-merge operation to unpack the freshly created `merged_base.apk`.

```json
{
    "name": "Extract Merged Binary", 
    "type": "decompile"
}

```

✨ **Example 3: Re-decompiling a Patched APK**
Useful in multi-pass pipelines where an already patched APK needs to be unpacked again for further static analysis.

```json
{
    "name": "Decompile Patched Build", 
    "type": "decompile"
}

```

#### `type: "merge_splits"`

Merges multiple Split-APKs (e.g., base, config.arm64_v8a) into a Universal APK using APKEditor.

✨ **Example 1: Standard Split Fusion**
Fuses the base APK with its architecture-specific libraries.

```json
{
    "name": "Merge Split APKs", 
    "type": "merge_splits"
}

```

✨ **Example 2: Fusing Language Packs**
Ensuring that density (`xxhdpi`) and locale (`en`, `de`) split-APKs are fused into the base binary so that all resources are indexed.

```json
{
    "name": "Fuse Language Packs", 
    "type": "merge_splits"
}

```

✨ **Example 3: Preparing for Deep Analysis**
Fusing the APKs early to ensure all layout files and localized strings are available for the static analyzer's RAM index.

```json
{
    "name": "Prepare Universal Binary", 
    "type": "merge_splits"
}

```

---

### C. Code Modifications (Patches)

These steps interact with the `SessionState` to apply the physical modifications you defined in the GUI.

#### `type: "smart_patch"`

Reads the Smali Studio patches and applies them. Triggers the Fuzzy Matcher if discrepancies occur.

✨ **Example 1: Standard Smali Injection**
Applies AST-based/Heuristic patches to Dalvik bytecode.

```json
{
    "name": "Apply Smali Patches", 
    "type": "smart_patch"
}

```

✨ **Example 2: Resolving Structural Deviations**
When an exact match fails, this step calculates the `difflib` similarity and safely applies the patch regardless of minor formatting updates from newer app versions.

```json
{
    "name": "Resolve Structural Deviations", 
    "type": "smart_patch"
}

```

✨ **Example 3: Iterative Patching**
Can be executed multiple times between different obfuscation passes or unpacking routines in complex pipelines.

```json
{
    "name": "Apply Secondary Smali Layer", 
    "type": "smart_patch"
}

```

#### `type: "anchor_patch"`

Reads the "Hex Patcher" UI table. Calculates physical offsets and overwrites binary instructions directly.

✨ **Example 1: Flutter AOT Patching**
Patching `libflutter.so` to overwrite instructions (e.g., `BNE` to `BEQ` for SSL pinning bypasses).

```json
{
    "name": "Apply SSL Bypass", 
    "type": "anchor_patch"
}

```

✨ **Example 2: Patching Unity IL2CPP**
Overwriting hardcoded structural offsets within a game's `libil2cpp.so` engine.

```json
{
    "name": "Patch IL2CPP Engine", 
    "type": "anchor_patch"
}

```

✨ **Example 3: Hardcoded API Key Replacement**
Stripping and replacing hardcoded developer tokens or URLs directly inside native compiled binaries.

```json
{
    "name": "Overwrite Hardcoded Tokens", 
    "type": "anchor_patch"
}

```

#### `type: "inject_custom_libs"`

Reads the "Native Lib Replacer" UI table. Physically replaces original `.so` files in all architecture folders.

✨ **Example 1: Anti-Debug Bypass**
Replacing a native library with a custom stripped version that removes `ptrace` checks.

```json
{
    "name": "Inject Anti-Debug Lib", 
    "type": "inject_custom_libs"
}

```

✨ **Example 2: Upgrading Native Crypto**
Swapping vulnerable OpenSSL libraries bundled inside the app for specific architecture targets.

```json
{
    "name": "Upgrade Native Crypto", 
    "type": "inject_custom_libs"
}

```

✨ **Example 3: Dropping a Hooking Engine**
Replaces native crash reporting libraries (like Crashlytics) with your own hooking payload library.

```json
{
    "name": "Replace Crashlytics Lib", 
    "type": "inject_custom_libs"
}

```

---

### D. Injection & Instrumentation

Automates the integration of dynamic analysis tools. Both steps evaluate the active checkboxes in the UI.

#### `type: "inject_frida"`

Reads the Frida checkbox. Injects and obfuscates the gadget, or actively cleans up the directory if toggled off.

✨ **Example 1: Preparing Reverse Connections**
Obfuscates `libfrida-gadget.so` to `libmetrics.so` and injects a `.config.so` JSON file to establish a TCP reverse tunnel to your host machine.

```json
{
    "name": "Inject Frida Gadget", 
    "type": "inject_frida"
}

```

✨ **Example 2: Sanitizing Frida Artifacts**
If Frida is toggled off in the GUI, this step acts as a janitor, actively stripping old gadget artifacts from the `lib/` directory to ensure a clean production build.

```json
{
    "name": "Sanitize Frida Artifacts", 
    "type": "inject_frida"
}

```

✨ **Example 3: Embedding a Standalone Agent**
Configures the gadget in "Script Mode", compiling JavaScript via Node.js into an embedded physical file loaded immediately upon app execution.

```json
{
    "name": "Embed Standalone Agent", 
    "type": "inject_frida"
}

```

#### `type: "apply_lspatch"`

Reads the LSPatch checkbox. Wraps the compiled APK with the `lspatch.jar` local engine.

✨ **Example 1: Standard Rootless Xposed Injection**
Bundles the `TrustMeAlready.apk` module into the application to disable SSL validation globally without needing a rooted device.

```json
{
    "name": "Apply LSPatch", 
    "type": "apply_lspatch"
}

```

✨ **Example 2: Injecting Custom Xposed Modules**
Utilizing LSPatch to inject privately written Xposed hooks directly into the repackaged application.

```json
{
    "name": "Inject Custom Xposed", 
    "type": "apply_lspatch"
}

```

✨ **Example 3: Bypassing Root Detection**
Combining LSPatch with specific modules (like RootCloak) during the build sequence.

```json
{
    "name": "Embed Root Cloak", 
    "type": "apply_lspatch"
}

```

---

### E. Compilation & Tracing

Handles repackaging, binary alignment, and runtime monitoring of the target application.

#### `type: "manifest_and_build"`

Reads UI strategies (ExtractNativeLibs, NSC, Debuggable), patches the `AndroidManifest.xml`, and runs the compiler daemon.

✨ **Example 1: Standard Compilation**
Injects `<network-security-config>` to trust user CA certificates (Mitmproxy) and executes Apktool or APKEditor based on your UI selection.

```json
{
    "name": "Manifest & Build", 
    "type": "manifest_and_build"
}

```

✨ **Example 2: Injecting the Debuggable Flag**
Dynamically alters or adds `android:debuggable="true"` in the manifest to allow ADB `run-as` permissions before triggering the compilation.

```json
{
    "name": "Inject Debuggable Flag", 
    "type": "manifest_and_build"
}

```

✨ **Example 3: Enforcing Legacy Lib Extraction**
Safely overrides the `android:extractNativeLibs` tag to guarantee correct binary execution based on the UI strategy toggle.

```json
{
    "name": "Enforce Legacy Lib Extraction", 
    "type": "manifest_and_build"
}

```

#### `type: "trace_start"` / `"trace_stop"`

Spawns background threads for ADB Logcat. Requires the `{PID}` dynamic variable to target the specific app.

✨ **Example 1: Standard Targeted Tracing**
Resolves the `{PID}` of the target app via ADB and spawns a background thread running `adb logcat --pid={PID}`.

```json
{
    "name": "Start Logcat", 
    "type": "trace_start", 
    "cmd": "adb logcat --pid={PID}", 
    "cwd": "{BASE_DIR}"
}

```

✨ **Example 2: Filtered Custom Tracing**
Modifying the trace command in the settings to filter for specific custom tags (e.g., Cryptography APIs) during runtime.

```json
{
    "name": "Start Crypto Trace", 
    "type": "trace_start", 
    "cmd": "adb logcat --pid={PID} | grep -iE 'Cipher|SecretKeySpec'", 
    "cwd": "{BASE_DIR}"
}

```

✨ **Example 3: Silent Background Tracing**
Halts any active logcat background processes to ensure clean terminal streams before starting a new session.

```json
{
    "name": "Stop Logcat", 
    "type": "trace_stop"
}

```

