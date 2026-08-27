# Android RE Patching Framework (Case Study) 🔧

A Python-based automation framework designed to massively accelerate the iterative reverse engineering workflow on Android.

This tool automates the tedious cycle of unpacking, binary hex patching, Smali code modification, repackaging, signing, ADB flashing, and logcat tracing. It bridges the gap between static analysis and dynamic instrumentation, handling everything from statically linked SSL certificate checks (BoringSSL) in Flutter-based (Dart AOT) apps to complex Java/Kotlin modifications (Smali) using a built-in IDE.

⚠️ **Disclaimer:**  
*This project is for educational and security research purposes only. No copyrighted APK files, libraries, or proprietary binaries are provided or distributed in this repository. Users must supply their own legally obtained binaries. The tools provided are intended solely to automate the local testing workflow for security analysis.*

<img width="1174" height="964" alt="image" src="https://github.com/user-attachments/assets/e4a01e5a-77cc-408e-86bb-74ccba64ca9b" />
<img width="1193" height="978" alt="image" src="https://github.com/user-attachments/assets/3af764b9-6ac4-49ae-b9ac-44ef852cd2a7" />
<img width="1188" height="970" alt="image" src="https://github.com/user-attachments/assets/aaa1f447-b6e3-42a7-82ac-e95863b06303" />

---

## 🛠️ System Requirements & Setup

1. **Python 3.x** (with `tkinter` support).
2. **Android SDK Platform-Tools:** `adb` must be available in the system PATH.
3. **Java Development Kit (JDK):** Required for repackaging (`jar`, `apktool`) and signing.
4. **Apktool & APKEditor:** Required for unpacking and rebuilding APKs (AXML/ARSC manipulation).
5. **Uber-APK-Signer:** Download the latest version of [patrickfav/uber-apk-signer](https://github.com/patrickfav/uber-apk-signer) and place it in the root directory.
6. **mitmproxy:** Required for the API Inspector (`pip install mitmproxy`).

### Directory Structure

The framework automatically creates the required workspace upon the first launch:

```text
├── AutoPatcher.py          # Main framework script
├── uber-apk-signer.jar     # Third-party tool (Not included in repo!)
├── APKEditor.jar           # Third-party tool for manifest strategies
├── source/                 # Place original APK files here
├── destination/            # Output directory for patched & signed APKs
├── archives/               # Version archive of all builds & traces
├── snippets.json           # Smali code injection templates
├── favorite_patches.json   # Saved batch patches (e.g., SSL pinning bypasses)
├── RE_History.json         # Machine-readable patch history
└── Kippy_RE_Log.md         # Human-readable reverse engineering logbook

```

---

## 📱 App Manager & Workspace Extraction

No need to manually pull APKs anymore. The built-in **App Manager** allows you to:

* **Extract Directly from Device:** Automatically list all third-party apps installed on the connected device and extract the base and split APKs (`adb pull`) with a single click.
* **Architecture Detection:** The framework automatically detects the target architecture (e.g., ARM64, x86_64) and configures the workspace accordingly.
* **Auto-Merge Split APKs:** Merges extracted Split-APKs into a single Universal APK for easier decompilation and manipulation.

---

## 🔬 Module 1: Smali Studio & Advanced Static Analysis

A full-fledged IDE built directly into the framework to analyze and modify Java/Kotlin code at the Smali level without needing external editors.

### Features

* **High-Performance RAM Indexer:** Decompiles the APK and builds an in-memory index of all `.smali` files, enabling lightning-fast global searches across thousands of files.
* **Call Graph & XREF Engine:** Instantly resolve incoming and outgoing cross-references (XREFs). Track which methods call your target, and what your target calls.
* **Data Flow Graph:** Automatically tracks read (`sget`/`iget`) and write (`sput`/`iput`) operations for variables and fields.
* **Outline View:** Extracts and categorizes class methods, fields, and System APIs for quick navigation.
* **Code Injection & Struct Manager:** Right-click inside the editor to inject predefined snippets (e.g., Try-Catch blocks, Android Intents, Logcat debugging) from `snippets.json`. You can also generate entirely new custom `.smali` classes (like BroadcastReceivers) and inject them into the app.
* **Syntax Highlighting:** Live regex-based syntax highlighting for Smali instructions, registers, strings, and comments.

---

## ⭐ Module 2: Patch Favorites & Fuzzy Matching

When an app updates, hardcoded offsets and exact code blocks change. The framework solves this with an intelligent patch management system.

* **Batch Patching:** Save complex, multi-file modifications (like complete OkHttp/TrustManager SSL Pinning bypasses) into `favorite_patches.json`. Apply the entire batch to a new app with a single click.
* **Fuzzy Matching Engine:** If a saved patch doesn't exactly match the decompiled code of a new app version, the built-in Fuzzy Matcher intervenes. It uses method signature resolution and code diffing to locate the new insertion point and opens an interactive side-by-side diff editor to resolve the conflict.

---

## 🏗️ Module 3: Automated Build & Patching Pipeline

A manual cycle of patching, repacking, signing, and flashing disrupts the analytical flow. This module reduces the cycle to seconds.

* **Dynamic Manifest Strategies:** Choose between `Smali_Only` (fastest), `APKEditor` (native AXML compilation), and `AAPT2` (strict). The pipeline automatically removes Split-APK restrictions and injects a custom `Network Security Config` (NSC) to allow user-level MitM certificates.
* **Multi-Target Patching:** Apply Smali modifications alongside Hex patches for compiled shared libraries (e.g., `libflutter.so`).
* **Auto-Signing & Flashing:** Seamless integration of `uber-apk-signer` to resign modified APKs, followed by direct deployment to the test device via ADB (`adb install-multiple`).
* **Integrated Tracing:** Start and stop targeted `logcat` traces based on the process ID (PID) of the target app directly from the GUI.
* **Systematic Test Documentation:** All test runs, patches, and observations are versioned and stored in structured Markdown reports (`Kippy_RE_Log.md`) and JSON history.

---

## 🌐 Module 4: API Inspector (DAST & MITM Proxy)

The framework includes a fully integrated Dynamic Application Security Testing (DAST) suite powered by `mitmproxy` to intercept and manipulate API traffic on the fly.

### 1️⃣ Setup & Commissioning (The VPN Trick for Flutter)

Since Flutter/Dart applications typically ignore global HTTP proxy settings, this tool utilizes a local VPN routing trick:

1. **Start Proxy & Establish USB Tunnel:** Start the proxy and click **🔌 Route USB** to execute `adb reverse tcp:8080 tcp:8080`.
2. **Push Certificate:** Click **📱 Push Cert** to copy the CA certificate to the device for installation.
3. **Configure SuperProxy:** Use an app like **SuperProxy** on Android (HTTP, `127.0.0.1:8080`) to tunnel the traffic, bypassing framework-level proxy ignores.

### 2️⃣ Traffic Monitoring & Data Extraction

* **Custom Columns Engine (⚙️ Spalten-Logik):** Dynamically extract hidden data from Request/Response bodies or headers using **JSON Paths**, **Regex**, or **Byte Offsets** and display them as dedicated columns.
* **Column Display Manager (👁️ Ansicht):** Hide, show, and reorder columns on the fly. Save setups as application-specific `.json` profiles.

### 3️⃣ Traffic Manipulation

* **Intercept Rules (On-the-fly Manipulation):** Define dynamic manipulation rules based on URL matching to automatically replace Request or Response payloads before they reach the app/server.
* **Database Editing & Export:** Modify intercepted packets directly in the GUI and save them to the local SQLite database (`api_traffic.db`). Export selected packets for external analysis.

```

```