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

<img width="1174" height="964" alt="image" src="https://github.com/user-attachments/assets/e4a01e5a-77cc-408e-86bb-74ccba64ca9b" />
<img width="1193" height="978" alt="image" src="https://github.com/user-attachments/assets/3af764b9-6ac4-49ae-b9ac-44ef852cd2a7" />
<img width="1188" height="970" alt="image" src="https://github.com/user-attachments/assets/aaa1f447-b6e3-42a7-82ac-e95863b06303" />



## 🛠️ System Requirements & Setup

1. **Python 3.x** (with `tkinter` support).
2. **Android SDK Platform-Tools:** `adb` must be available in the system PATH.
3. **Java Development Kit (JDK):** Required for repackaging (`jar`) and signing.
4. **Uber-APK-Signer:** Download the latest version of [patrickfav/uber-apk-signer](https://github.com/patrickfav/uber-apk-signer) and place it in the root directory.
5. **mitmproxy:** Required for the API Inspector (`pip install mitmproxy`).

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

---

## 🌐 API Inspector (DAST & MITM Proxy)

The framework includes a fully integrated Dynamic Application Security Testing (DAST) suite powered by `mitmproxy`. This allows you to intercept, analyze, and manipulate API traffic on the fly. Since Flutter/Dart applications typically ignore global HTTP proxy settings, this tool utilizes a local VPN routing trick to capture the traffic.

### 1️⃣ Setup & Commissioning (The VPN Trick)

To successfully capture traffic from a Flutter application, follow these exact steps:

1. **Start the Proxy:** In the framework GUI, navigate to the "API Inspector" tab and click **▶ Start Proxy**.
2. **Push the Certificate:** Click **📱 Push Cert**. This copies the `mitmproxy-ca-cert.cer` file to your device's `/Download/` folder.
3. **Install the Certificate:** On your Android device, go to *Settings -> Security -> Encryption & Credentials -> Install a certificate*. Choose **CA Certificate**, navigate to your Downloads folder, and install the pushed certificate.
4. **Establish USB Tunnel:** Connect your phone via USB and click **🔌 Route USB** in the GUI. This executes `adb reverse tcp:8080 tcp:8080`, linking your phone's local port 8080 directly to your PC's proxy.
5. **Configure SuperProxy (The Flutter Bypass):**
* Download and install a proxy-forwarding app like **SuperProxy** on your Android device.
* Add a new profile: Protocol `HTTP`, Server `127.0.0.1`, Port `8080`.
* Start the profile. Android will prompt you to allow a VPN connection.
* *Crucial Step:* Once connected to the proxy via the VPN, open SuperProxy again. You will be prompted to accept/trust the proxy's certificate for the VPN session. Accept it.



### 2️⃣ Usage & Traffic Manipulation

Once the setup is complete, all intercepted traffic from the target app will automatically populate the SQLite database and appear in the GUI in real-time.

* **Live Monitoring & Details:** Click on any request in the Treeview (top left). The bottom pane will instantly display the raw Request Headers/Body and Response Headers/Body.
* **Filtering:** Use the search bar to filter incoming traffic in real-time by HTTP Method (e.g., `POST`) or URL paths (e.g., `/api/login`).
* **Commenting:** Add custom observations or notes to specific requests via the "Request Details" tab. These are permanently saved in the local `api_traffic.db`.
* **Intercept Rules (On-the-fly Manipulation):**
Navigate to the "Intercept Regeln" tab to define dynamic manipulation rules.
* Enter a **URL Match** (e.g., `/user/status`).
* Select an **Action** (e.g., `replace_res_body`).
* Provide the new JSON **Payload** (e.g., `{"status": "premium"}`).
* Click **Regel Hinzufügen**. The proxy will immediately intercept any matching future request and swap the payload before it reaches the app or the server.



