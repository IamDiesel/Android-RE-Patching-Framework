# Android RE Patching Framework (Case Study) 🔧

A Python-based automation framework designed to massively accelerate the iterative reverse engineering workflow on Android.

This tool automates the tedious cycle of unpacking, binary hex patching, signing, ADB flashing, and logcat tracing. It was originally developed as a proof-of-concept to analyze and modify statically linked SSL certificate checks (BoringSSL) in Flutter-based (Dart AOT) Android applications at the ARM64 assembly level.

⚠️ **Disclaimer:**  
*This project is for educational and security research purposes only. No copyrighted APK files, libraries, or proprietary binaries are provided or distributed in this repository. Users must supply their own legally obtained binaries. The tools provided are intended solely to automate the local testing workflow for security analysis.*

## ✨ Features

* **Dynamic Multi-Patching:** Apply multiple hex patches to specific RAM offsets within compiled shared libraries (e.g., `libflutter.so`) via a dynamic Tkinter GUI.
* **Automated Build Pipeline:** Automatically unpack split APKs (`split_config.arm64_v8a`), inject patches, and repackage via `jar`.
* **Auto-Signing & Flashing:** Seamless integration of `uber-apk-signer` to automatically resign modified APKs, followed by direct deployment to the test device via ADB (`adb install-multiple`).
* **Integrated Tracing:** Start and stop targeted `logcat` traces based on the process ID (PID) of the target app directly from the GUI.
* **Systematic Test Documentation:** All test runs, patches, and observations are versioned and stored in both machine-readable (`RE_History.json`) and structured Markdown reports (`Kippy_RE_Log.md`) for later analysis.

## 🛠️ System Requirements & Setup

1. **Python 3.x** (with `tkinter` support).
2. **Android SDK Platform-Tools:** `adb` must be available in the system PATH.
3. **Java Development Kit (JDK):** Required for repackaging (`jar`) and signing.
4. **Uber-APK-Signer:** Download the latest version of [patrickfav/uber-apk-signer](https://github.com/patrickfav/uber-apk-signer) and place it in the root directory.

### Directory Structure

The framework automatically creates the required workspace upon the first launch:

```text
├── AutoPatcher.py          # Main framework script
├── uber-apk-signer.jar     # Third-party tool (Not included in repo!)
├── source/                 # Place original APK files here
├── destination/            # Output directory for patched & signed APKs
├── archives/               # Version archive of all builds & traces
├── RE_History.json         # Machine-readable patch history
└── Kippy_RE_Log.md         # Human-readable reverse engineering logbook

```

## 🚀 Usage

1. Place the original APK files (including architecture splits like `split_config.arm64_v8a.apk`) into the `source/` folder.
2. Launch the GUI via `python AutoPatcher.py`.
3. Enter the RAM offsets to be modified, the base address (default: `00100000`), and the desired hex patch. The script automatically calculates the physical file offset.
4. Click the **Build & Sign** button to patch the binaries and repackage the app.
5. Use **Flash to Device** to push the application to a test device connected via USB/WLAN.
6. Log your analyses and crashes in the "Test Results" section to maintain a continuous record of your reverse engineering session.

## 🔬 Motivation & Methodology (Reverse Engineering Workflow)

When reverse engineering Dart/Flutter applications, traditional Man-in-the-Middle attacks or standard hooking tools (like Frida) can fail due to statically linked libraries or extreme obfuscation. Understanding the internal callbacks at the assembly level (ARM64) requires countless iterative patch attempts. A manual cycle of decompiling, patching, repacking, signing, and flashing takes minutes and disrupts the analytical flow. This framework reduces this cycle to a few seconds and automates the scientific documentation of the results.

**The methodology of this project is divided into the following phases:**

1. **Extraction:** The target application is installed on a test device via the App Store and then transferred to the development PC using ADB (`adb pull`).
2. **Static Analysis (Ghidra):** Since Flutter apps statically link their BoringSSL library, convenient JNI interfaces are missing. The entry point for analysis is purely static, searching for distinct strings (e.g., SSL error messages or certificate identifiers) within the extracted binary. The assembly code is then traced backward through the call graph using cross-references (XREFs).
3. **Control Flow Identification:** Systematic analysis of various ARM64 routines – from memory management (mutex/stack) to iterative X509 certificate parsing loops, down to the final custom verification callbacks registered by Flutter.
4. **Injection & Automation:** The identified RAM offsets and corresponding modified ARM64 hex instructions (e.g., a manipulated return value or premature return) are entered into this tool's GUI and automatically patched into the `.so` files.
5. **Deployment & Verification:** The tool handles repackaging, signing, and flashing to the smartphone. The integrated Logcat trace immediately reveals whether the patch resulted in a SIGSEGV crash, a logic error (e.g., "No internet connection"), or a successful TLS handshake.
