# Android RE Patching Framework (Case Study) 🔧

A Python-based automation framework designed to massively accelerate the iterative reverse engineering workflow on Android.[cite: 3]

This tool automates the tedious cycle of unpacking, binary hex patching, signing, ADB flashing, and logcat tracing.[cite: 3] It was originally developed as a proof-of-concept to analyze and modify statically linked SSL certificate checks (BoringSSL) in Flutter-based (Dart AOT) Android applications at the ARM64 assembly level.[cite: 3]

⚠️ **Disclaimer:**  
*This project is for educational and security research purposes only. No copyrighted APK files, libraries, or proprietary binaries are provided or distributed in this repository. Users must supply their own legally obtained binaries. The tools provided are intended solely to automate the local testing workflow for security analysis.*[cite: 3]

<img width="1174" height="964" alt="image" src="https://github.com/user-attachments/assets/e4a01e5a-77cc-408e-86bb-74ccba64ca9b" />
<img width="1193" height="978" alt="image" src="https://github.com/user-attachments/assets/3af764b9-6ac4-49ae-b9ac-44ef852cd2a7" />
<img width="1188" height="970" alt="image" src="https://github.com/user-attachments/assets/aaa1f447-b6e3-42a7-82ac-e95863b06303" />

---

## 🛠️ System Requirements & Setup

1. **Python 3.x** (with `tkinter` support).[cite: 3]
2. **Android SDK Platform-Tools:** `adb` must be available in the system PATH.[cite: 3]
3. **Java Development Kit (JDK):** Required for repackaging (`jar`) and signing.[cite: 3]
4. **Uber-APK-Signer:** Download the latest version of [patrickfav/uber-apk-signer](https://github.com/patrickfav/uber-apk-signer) and place it in the root directory.[cite: 3]
5. **mitmproxy:** Required for the API Inspector (`pip install mitmproxy`).[cite: 3]

### Directory Structure

The framework automatically creates the required workspace upon the first launch:[cite: 3]

```text
├── AutoPatcher.py          # Main framework script
├── uber-apk-signer.jar     # Third-party tool (Not included in repo!)
├── source/                 # Place original APK files here
├── destination/            # Output directory for patched & signed APKs
├── archives/               # Version archive of all builds & traces
├── RE_History.json         # Machine-readable patch history
└── Kippy_RE_Log.md         # Human-readable reverse engineering logbook

```

---

## 🔬 Module 1: Automated Patching Pipeline

When reverse engineering Dart/Flutter applications, traditional Man-in-the-Middle attacks or standard hooking tools (like Frida) can fail due to statically linked libraries or extreme obfuscation. Understanding the internal callbacks at the assembly level (ARM64) requires countless iterative patch attempts. A manual cycle of decompiling, patching, repacking, signing, and flashing takes minutes and disrupts the analytical flow. This framework reduces this cycle to a few seconds and automates the scientific documentation of the results.

### Features

* **Dynamic Multi-Patching:** Apply multiple hex patches to specific RAM offsets within compiled shared libraries (e.g., `libflutter.so`) via a dynamic Tkinter GUI.


* **Automated Build Pipeline:** Automatically unpack split APKs (`split_config.arm64_v8a`), inject patches, and repackage via `jar`.


* **Auto-Signing & Flashing:** Seamless integration of `uber-apk-signer` to automatically resign modified APKs, followed by direct deployment to the test device via ADB (`adb install-multiple`).


* **Integrated Tracing:** Start and stop targeted `logcat` traces based on the process ID (PID) of the target app directly from the GUI.


* **Systematic Test Documentation:** All test runs, patches, and observations are versioned and stored in both machine-readable (`RE_History.json`) and structured Markdown reports (`Kippy_RE_Log.md`) for later analysis.



### Workflow & Usage

1. **Extraction:** Install the target application on a test device via the App Store and transfer it to the development PC using ADB (`adb pull`). Place the original APK files (including architecture splits) into the `source/` folder.


2. **Static Analysis (Ghidra):** Search for distinct strings (e.g., SSL error messages) within the extracted binary and trace the assembly code backward through the call graph using cross-references (XREFs).


3. **Injection:** Launch the GUI via `python AutoPatcher.py`. Enter the identified RAM offsets, the base address (default: `00100000`), and the desired hex patch.


4. **Deployment:** Click the **Build & Sign** button to patch the binaries and repackage the app. Use **Flash to Device** to push the application to the test device connected via USB/WLAN.


5. **Verification:** The integrated Logcat trace immediately reveals whether the patch resulted in a SIGSEGV crash, a logic error, or a successful bypass. Log your analyses in the "Test Results" section.



---

## 🌐 Module 2: API Inspector (DAST & MITM Proxy)

The framework includes a fully integrated Dynamic Application Security Testing (DAST) suite powered by `mitmproxy`. This allows you to intercept, analyze, and manipulate API traffic on the fly.

### 1️⃣ Setup & Commissioning (The VPN Trick for Flutter)

Since Flutter/Dart applications typically ignore global HTTP proxy settings, this tool utilizes a local VPN routing trick to capture the traffic:

1. **Start the Proxy:** In the framework GUI, navigate to the "API Inspector" tab and click **▶ Start Proxy**.


2. **Push & Install Certificate:** Click **📱 Push Cert**. This copies the `mitmproxy-ca-cert.cer` file to your device's `/Download/` folder. On your Android device, go to *Settings -> Security -> Encryption & Credentials -> Install a certificate*. Choose **CA Certificate** and install it.


3. **Establish USB Tunnel:** Connect your phone via USB and click **🔌 Route USB** in the GUI. This executes `adb reverse tcp:8080 tcp:8080`, linking your phone's local port 8080 directly to your PC's proxy.


4. **Configure SuperProxy (The Bypass):** Download and install a proxy-forwarding app like **SuperProxy** on your Android device. Add a new profile: Protocol `HTTP`, Server `127.0.0.1`, Port `8080`.


5. **Trust the Session:** Start the SuperProxy profile. Once connected, open SuperProxy again and accept/trust the proxy's certificate for the VPN session.



### 2️⃣ Traffic Monitoring & Data Extraction

Once the setup is complete, all intercepted traffic from the target app will automatically populate the local SQLite database (`api_traffic.db`) and appear in the GUI in real-time.

* **Smart Autoscroll & Focus Retention:** The grid automatically follows new incoming packets. However, if you scroll up or select a packet to analyze, the autoscroll pauses so you don't lose focus. It resumes automatically when you scroll back to the bottom.
* **Custom Columns Engine (⚙️ Spalten-Logik):** Dynamically extract hidden data from Request/Response bodies or headers and display them as dedicated columns in your grid. The engine supports three extraction methods:
* **JSON Path:** Extract data from complex JSON structures (e.g., `data.user.id`).
* **Regex:** Use Regular Expressions to capture dynamic tokens from unformatted text.
* **Byte Offset:** Extract raw bytes (Hex, Int, String) from specific positions and lengths (crucial for reverse engineering custom binary protocols).


* **Column Display Manager (👁️ Ansicht):** Hide, show, and reorder your columns on the fly without reloading the database.
* **Profile Management:** Save your custom column and extraction configurations as `.json` profiles and load them depending on the application you are currently analyzing.

### 3️⃣ Traffic Manipulation & Portability

* **Intercept Rules (On-the-fly Manipulation):** Navigate to the "Intercept Regeln" tab to define dynamic manipulation rules. Enter a **URL Match**, select an **Action** (e.g., `replace_res_body`), and provide the new **Payload**. The proxy will immediately intercept any matching future request and swap the payload before it reaches the app or the server.


* **Database Editing:** Modify intercepted Request/Response bodies or add custom analytical comments directly in the GUI. Click **💾 Änderungen speichern** to permanently update the database entry.
* **Export & Import:** Export selected packets to a `.json` file for backup or external analysis, and import them back into the framework at any time.
* **Clipboard Integration:** Select multiple packets (or use `STRG+A`) and hit `STRG+C` to copy a beautifully formatted, readable text report of all selected requests directly to your clipboard.

```
