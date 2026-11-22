# Projektplan Revision 3 (Eingefroren): Vom App-Nachbau zum echten, gläsernen Android- & ARM64-Debugger

**Erstellt am:** 2026-11-22  
**Status:** Eingefroren vor der ersten Code-Änderung im Framework.  
**Vorgänger-Dokument:** [Debugger_Architektur_Bestandsaufnahme.md](Debugger_Architektur_Bestandsaufnahme.md)  
**Verbindliche Laufzeit:** `C:\Users\Lenovo\AppData\Local\Programs\Python\Python312\python.exe` (keine `.venv`)

> **Einfrierungsregel:** Kapitel 1 bis Kapitel 4 dieses Dokuments sind verbindlich eingefroren und werden während der Umsetzung **nicht** verändert. Notwendige Abweichungen (einschließlich der Entscheidung nach Schritt 5a) werden ausschließlich chronologisch in **Kapitel 5 (`## Abweichungsprotokoll`)** protokolliert.

---

## 1. Leitprinzip & verbindliche Rahmenbedingungen

1. **Leitprinzip:** *Ersetzt wird nur die Umgebung der App, nie die App selbst.*
   - **App-Code (wird ausgeführt, nie nachgebaut):** DEX/Smali der APK (inkl. dynamisch nachgeladenen DEXe und gebündelter Drittbibliotheken wie Coroutines, Room, OkHttp, Retrofit, Moshi, Fragment, RecyclerView, Compose, ZXing), native Bibliotheken unter `lib/` (`DT_NEEDED`, `DT_INIT_ARRAY`, `JNI_OnLoad`, `libhermes.so`, `libflutter.so`, etc.) sowie Assets und Bundles der APK.
   - **Umgebung (vom Studio gestellt & beobachtbar gemacht):** Linux-ARM64-Kernel/Syscalls, Android-Systembibliotheken (`libc.so`, `libm.so`, `libdl.so`, `liblog.so`, `libz.so`, `libandroid.so`), Android-Framework-/JDK-Systemdienste (`android.*`, `java.*`, `javax.*`, `dalvik.*`, `SQLiteDatabase`, `Socket`/`SSLSocket`, System-`WebView`) sowie das CPU-Backend (`arm64mini` oder `unicorn`, ein Backend pro Lauf).
2. **Kein Löschen ohne Freigabe:** Keine Datei und kein abgeklemmter Hook werden gelöscht; überflüssig gewordene Kandidaten werden mit Verweis auf den Boundary-Trace-Nachweis in `Refactoring/Abschluss_Revision3.md` unter `## Löschkandidaten` gesammelt.
3. **Datenschutz, Netz & Zugangsdaten:**
   - Strenger Nichtzugriff auf `secrets/` und `data/vfs_appdata/`.
   - Im unbeaufsichtigten Lauf (Messskript & Tests) gilt ausnahmslos `net_mode = "offline"`, `persist_appdata = False` sowie ein frisches temporäres Verzeichnis (`tempfile.TemporaryDirectory()`) pro App-Lauf.
   - Request-Bodies werden standardmäßig **nicht** auf Platte geschrieben (`persist_request_bodies = False`); alle auf Platte geschriebenen Zustands-/Mess-/Logdateien durchlaufen zusätzlich die Schwärzung sensitiver Schlüssel und Token (`core/util/redactor.py`).
4. **Geschützte Dateien (unverändert lassen):** `tests/test_arm64mini_contract.py`, `tests/test_rn_hermes_producer.py`, `tests/test_rn_hermes_real.py`, `config.json`.
5. **Keine mutierenden Git-Befehle** (`commit`, `checkout`, `reset`, `stash`, `clean`, `push`).

---

## 2. Messplan (Eingefroren)

Der Messplan gilt unverändert für alle Messläufe (Vorab-Reproduzierbarkeit, Baseline, Kontrollmessungen nach den Schritten 2a, 2b, 3, 4a–4h, 5a/5b/5c, 6.1–6.7 sowie Abschlussmessung).

### 2.1 Eindeutige Definition der 12 Messgrößen

| # | Schlüssel (`JSON`) | Einheit | Exakte Definition | Zählstelle im Code |
|---|---|---|---|---|
| 1 | `dalvik_steps` | Instruktionen (`int`) | Gesamtzahl interpretierter Dalvik-Bytecode-Instruktionen im Lauf. | `DalvikVM.steps` (`core/java/dalvik.py`) |
| 2 | `stop_site`, `stop_reason` | String (`str`) | `stop_site`: aktuelle Methode + PC (`"Lcls;->name(sig)@pc"`) oder `"idle"` bei regulärem Ende.<br>`stop_reason`: normierter Code (`"lifecycle_done"`, `"budget_steps"`, `"budget_wall"`, `"watchpoint:<kind>"`, `"uncaught_exception:<cls>"`, `"native_fault:<why>"`, `"net_gate_blocked"`). | `LifecycleRunner.run_apk` + `JavaDebugger.state()` (`core/java/lifecycle.py`, `core/java/debug.py`) |
| 3 | `loaded_libs`, `loaded_libs_count` | Liste / Anzahl (`int`) | Eindeutige (über `sha256` deduplizierte) in `LinkerSpace.load_module` geladene **App-ELFs** ohne Android-System-SONAMEs (`_SYSTEM_SONAMES`). | `LinkerSpace.modules` (`core/native/linker.py`) |
| 4 | `bound_natives_register`, `bound_natives_static` | Bindungen (`int`) | Getrennte Zähler: (a) dynamische Bindungen via JNI `RegisterNatives`, (b) statische Bindungen via exportierten `Java_*`-Symbolen (inkl. `OpenSourceMergedSoMapping`). | Rückgabewerte von `harvest_registered_natives` & `harvest_static_jni` (`core/java/fusion_autoload.py`) |
| 5 | `jni_java_to_native`, `jni_native_to_java` | Übergänge (`int`) | **Verbindliche Zählweise bei Verschachtelung:** Jeder reale Grenzübertritt zählt einzeln (+1 am Eintritt). `Java -> NativeA -> Java -> NativeB` zählt `jni_java_to_native = 2`, `jni_native_to_java = 1` (exkl. reiner `JNI_OnLoad`-Boot; `JNI_OnLoad` -> Java zählt als `native_to_java`). | `JavaToNativeBridge.calls_java_to_native` (`core/java/bridge.py`), `VmBackedJavaRuntime.calls_native_to_java` (`core/java/runtime.py`) |
| 6 | `deviations_by_kind` | `dict[kind -> {unique, total}]` | Nach Kategorie gruppierte Einträge aus `DeviationJournal` (eindeutige `(kind, site)` und Gesamtzahl inkl. Wiederholungen). | `DalvikVM.deviations.to_list()` (`core/java/deviation_journal.py`) |
| 7 | `hook_hits_by_pkg` | `dict[pkg -> int]` | Anzahl tatsächlicher Abfangvorgänge durch `_method_hooks` oder systemische `method_overrides`, gruppiert nach dem **Paketpräfix der definierenden Klasse (`md.cls` bzw. deklarierende Interface/Native-Klasse)** (z. B. `"Lokhttp3/"`, `"Landroidx/room/"`). | Zentrale Hook-Schranke in `DalvikVM` (`invoke_virtual`, `_do_invoke`) & `AndroidStubs._method_invoke` |
| 8 | `allowed_hook_packages` | `list[str]` | Im jeweiligen Messlauf aktiv freigeschaltete Paketpräfixe (`["*ALL_BASELINE*"]` vor Schranke, `[]` im Profil `faithful`). | `DalvikVM.allowed_hook_packages` / Laufkonfiguration |
| 9 | `viewtree_nodes`, `render_source` | Anzahl (`int`), `str` | `viewtree_nodes`: `len(tree.nodes())` wenn `tree.root is not None`, sonst `0`.<br>`render_source`: `"executed"` \| `"reconstructed"` \| `"none"` (wenn `tree.root is None`). | `GlassAppRunner.tree` (`core/java/view_tree.py`) |
| 10 | `net_requests_by_origin` | `dict[origin -> int]` | Gezählte Request-Versuche gruppiert nach Herkunft: `"java_httpurlconnection"`, `"java_socket_tls"`, `"java_hook_okhttp"`, `"native_socket"`, `"framework_producer"`. | `NetLog.entries` (`core/java/net_recorder.py`) |
| 11 | `arm64_instructions`, `arm64_mips` | Instruktionen (`int`), `float` | `arm64_instructions`: dynamisch ausgeführte AArch64-Instruktionen (`arm64mini`: exakte Instruktionssumme ausgeführter Basisblöcke + `step()`; `unicorn`: im Zeitmesslauf **0 Hooks**, exakte Instruktionszahl aus deterministischem Zweitlauf mit `UC_HOOK_BLOCK` (`insns += size // 4`)).<br>`arm64_mips = arm64_instructions / max(native_wall_s, 1e-9) / 1e6`. | `arm64mini.CPU.insn_count` bzw. `UnicornArm64Backend.count_instructions` |
| 12 | `wall_time_s` | Sekunden (`float`) | Gemessene Wandzeit des App-Laufs via `time.perf_counter()` ab Abschluss des Runner-Aufbaus (oder getrennt als `build_wall_s` + `run_wall_s`). | `tools/measure_suite.py` |

### 2.2 Fester Messaufbau (`tools/measure_suite.py`)

Ein einziges Messskript `tools/measure_suite.py` führt alle Messungen mit folgenden unveränderlichen Standardparametern durch:

| Parameter | Fester Wert |
|---|---|
| Python-Interpreter | `C:\Users\Lenovo\AppData\Local\Programs\Python\Python312\python.exe` |
| `max_steps` | `500_000` |
| `max_wall_s` | `20.0` |
| `net_mode` | `"offline"` (keinerlei reale Netzverbindungen) |
| `persist_appdata` | `False` |
| `persist_request_bodies` | `False` |
| VFS-/Self-Cache-Verzeichnis | Frisches `tempfile.TemporaryDirectory()` pro App-Lauf |
| Virtuelle Uhr | Fix `2026-11-22T12:00:00Z`, `uptime_ms = 100_000` |
| Deterministischer PRNG-Seed | `0x5EED2026` |
| Zone / Patches | `"original"`, `apply_patches = False` |
| Standard-Backend / Profil | CLI-Flags `--backend arm64mini\|unicorn` und `--profile legacy\|allowlist\|faithful` |

**Feste Liste der 12 Test-Apps (`source/<app>/base_unpacked_apktool`):**

| # | Paket / App | Version (`apktool.yml`) | Laufzeit-Typ | Manifest Application / Launcher |
|---|---|---|---|---|
| 1 | `com.elink.smartlock` | `5.2.6.0` | Klassisch + JNI | `HostApplication` / `com.elink.lib.common.base.AppStart` |
| 2 | `com.marsvard.stickermakerforwhatsapp` | `1.0.12-7` | Klassisch + JNI | `App` / `main.MainActivity` |
| 3 | `com.arjonasoftware.babycam` | `2.48` | Klassisch + JNI | `App` / `SplashScreenActivity` |
| 4 | `com.example.kueckbox` | `1.0` | Jetpack Compose | `—` / `MainActivity` |
| 5 | `de.glucobridge.app` | `1.0` | Compose + JNI | `GlucoBridgeApp` / `MainActivity` |
| 6 | `digifit.virtuagym.client.android` | `12.4.2` | Compose / View / Room / Retrofit | `Virtuagym` / `HomeActivity` |
| 7 | `com.alpicoolneutral.fridge.controller` | `2.4.1` | React Native | `MainApplication` / `MainActivity` |
| 8 | `com.ecoflow` | `6.18.0.132` | React Native + JNI | `IotApplication` / `SplashActivity` |
| 9 | `org.nativescript.LibreLinkUp` | `5.2.0` | React Native + Self-Unpacking | `MainApplication` / `MainActivity` |
| 10 | `com.datamars.kippynew` | `1.3.9` | Flutter + PairIP | `com.pairip.application.Application` / `MainActivity` |
| 11 | `com.fiio.control` | `4.7.3` | Flutter | `ControlApplication` / `MainActivity` |
| 12 | `com.scinan.TieFuZX.Refrigerator` | `1.1.9` | Hybrid Web (DCloud HTML5+) | `DCloudApplication` / `io.dcloud.PandoraEntry` |

### 2.3 Reproduzierbarkeit & Ausschluss von Nichtdeterminismus
- Virtuelle Uhr fixiert über `apply_virtual_clock_spec`; `Random`, `SecureRandom`, `UUID.randomUUID()` sowie Kernel-`getrandom`/`arc4random_buf`/`/dev/urandom` arbeiten im deterministischen Messmodus mit abgeleitetem Seed (`0x5EED2026`).
- Linker-, Heap-, TLS- und Stack-Basisadressen sind fest (`0x70_0000_0000`, `0x61_0000_0000`, `0x60_0000_0000`, `0x7F_FFF0_0000`).
- **Vorab-Nachweis (Schritt 1):** 3 vollständige Wiederholungen je App (`repro_1`, `repro_2`, `repro_3`). Alle Zählgrößen (`dalvik_steps`, `loaded_libs`, `bound_natives_*`, `jni_*`, `deviations_by_kind`, `hook_hits_by_pkg`, `viewtree_nodes`, `arm64_instructions`) müssen über alle 3 Läufe **exakt identisch (`Varianz == 0`)** sein.

### 2.4 Zeit- & Durchsatzmessungen
- App-Laufzeiten: mindestens **1 Aufwärmlauf + 5 Messläufe** (für Mikrobenchmarks) bzw. Kaltlauf + 3 Läufe für die Suite, jeweils mit Angabe von **Median** und **IQR (Interquartilsabstand)** sowie Min/Max.
- **ARM64-Durchsatzmessung (Schritt 5a):** Mindestlast **`>= 50.000.000` (`5 × 10^7`) dynamische AArch64-Instruktionen** je Messreihe (`1` Kaltlauf, `1` Aufwärmlauf, `5` Warmläufe). Im gemessenen Lauf werden **keine Pro-Instruktions- oder Pro-Block-Hooks** registriert; die exakte dynamische Instruktionszahl wird in einem separaten deterministischen Zähllauf (`UC_HOOK_BLOCK`, `insns += block_size // 4`) bestimmt.

### 2.5 Vergleichsregeln (`Baseline` vs. Kontroll-/Abschlussmessung)
- **Zählgrößen:** „Identisch“ bedeutet `delta == 0`.
- **Zeiten:** „Unverändert“ bedeutet Median innerhalb `±15 %` oder `±25 ms` (bei Kurzläufen `< 150 ms`).
- **Unterscheidung Fortschritt vs. Rückschritt:**
  - *Kein Rückschritt (Ehrlicher Halt):* Ein Lauf bricht früher als die Baseline ab (`dalvik_steps < baseline_steps`), **wenn** der Grund explizit in `stop_reason` benannt ist (`watchpoint:*`, `uncaught_exception:ClassNotFoundException`, `uncaught_exception:UnsatisfiedLinkError`, `unbound_native`) und auf dem Abklemmen einer definierten Fälschung beruht.
  - *Fortschritt:* `reconstructed == 0`, weniger `hook_hits_by_pkg` auf der Ausnahmeliste bei mindestens gleich tiefer realer Ausführung, oder höherer echter `loaded_libs_count` / `bound_natives_*` / `executed` `viewtree_nodes`.
  - *Echter Rückschritt:* Unbenannter Abbruch, Absturz durch Python-Exception außerhalb der VM (`native_fault` / Framework-`AttributeError`/`TypeError`), Verlust von vorher real ausgeführten `executed`-Views oder Verlangsamung außerhalb der Toleranz bei sonst unveränderter Semantik.

### 2.6 Vergleich an der Umgebungsgrenze (`BoundaryTraceRecorder`)
Zur Ablösung einer Paketausnahme wird die Aufruffolge an der Umgebungsgrenze vor und nach Entfernung des Pakets aufgezeichnet (`core/java/boundary_trace.py`):
- **SQL-Grenze (`sqlite3`):** `(op, normalized_sql, bind_types)` — normalisiert werden Zeilenumbrüche/Whitespace sowie transiente Datenbankpfad-Präfixe.
- **Netz-/Socket-Grenze (`Socket` / `HttpURLConnection`):** `(origin, method, host_port_path, sorted_header_keys, body_len)` — normalisiert werden Query-Timestamps, zufällige Request-IDs/Nonces und Host-Uhrzeiten (`Date`); Request-Bodies werden **nicht** gespeichert.
- **ViewTree-Grenze (`Window`/`ViewGroup`/`TextView`/`Canvas`):** `(op, view_cls, res_id_hex, text_hash_and_len, child_count)` — normalisiert werden instanzspezifische `JObject`-HashCodes/Objekt-IDs; bei nebenläufiger Thread-Abarbeitung (z. B. Coroutines) wird innerhalb desselben UI-Frames kausal nach Frame-Commit kanonisch sortiert.
- **Auswertung:** Exakte Gleichheit der normalisierten Ereignissequenzen (oder streng Obermenge durch nachweislich zusätzliche echte App-Initialisierung wie z. B. Interceptor-Header statt übersprungener Pipeline) belegt die Ablösereife.

### 2.7 Selbsttest des Messverfahrens (`tests/test_measurement_counters.py`)
Vor der Baseline muss für **jede der 12 Messgrößen** ein isolierter synthetischer Test beweisen, dass der Zähler im positiven Kontrollfall gezielt von `0` (bzw. Basiswert) auf den exakten Sollwert ausschlägt.

### 2.8 Rohdatenablage
- Alle JSON-Rohdaten liegen unveränderlich unter `Refactoring/measurements/` mit eindeutigem Zeitstempel-Namen (`<seq>_<tag>_<YYYYMMDD_HHMMSS>.json`). Keine Datei wird überschrieben.
- Jede Messdatei enthält Metadaten (`timestamp`, `python_version`, `platform`, `code_tree_sha256`, `plan_sha256`, `run_params`), niemals Secrets und niemals HTTP-Request-Bodies.

---

## 3. Vollständiger Schritt-Katalog (Schritt 0 bis Schritt 6.7 + Schritt 7)

### Schritt 0 — Isolierte Vorarbeiten (`Python312`-Pakete, Multi-DEX-Index, Zähler, Schwärzung & Messskript)
- **Änderung & Dateien:**
  - Pakete `unicorn` und `pytest` in `C:\Users\Lenovo\AppData\Local\Programs\Python\Python312\python.exe` installieren; `requirements.txt` (`unicorn>=2.0.1`, `cryptography>=41.0.0`) ergänzen.
  - Eigenständige Host-I/O-Optimierung ohne Semantikänderung: Lazy Dateipfad-Index in `core/java/class_provider.py` (`StudioClassProvider._file_index` + Erfassung von `native_methods` pro geparster Klasse), mtime-Cache in `core/java/app_runtime.py` (`detect_runtime`, `inspect_engine_details`), O(1) `loaded_classes_count()` in `ui/controllers/debugger_controller.py` und `ui/tabs/display_tab.py`.
  - Redaktionsmodul `core/util/redactor.py`, `NetLog`-Herkunfts-Tagging (`origin`) & `persist_request_bodies=False` in `core/java/net_recorder.py`, Zähler `calls_java_to_native` in `core/java/bridge.py`, `calls_native_to_java` in `core/java/runtime.py`, additiver `insn_count` in `core/native/arm64mini.py`, `core/java/boundary_trace.py` (`BoundaryTraceRecorder`), `tools/measure_suite.py`.
- **Abnahmekriterium:** `test_arm64mini_contract.py` (`PASS`), `StudioClassProvider` mit 10 `smali*`-Ordnern löst `0` `os.path.isfile`-Aufrufe je unbekannter Klasse nach Indexbau aus (`O(1)`), und alle **12 Messgrößen** schlagen im Zähler-Selbsttest exakt wie erwartet aus (`12/12 PASS`).
- **Pflichttests (schlagen ohne Änderung fehl):** `tests/test_step0_multidex_index.py`, `tests/test_measurement_counters.py`.
- **Risiken & Ausweichplan:** Sollte `unicorn` als Wheel für Python 3.12 unter Windows nicht direkt laden (`uc.Uc` DLL-Fehler), sofortige Rückgabe an die Planungsschicht (`Re-plan`).

### Schritt 1 — Reproduzierbarkeit (3×) & Quantitative Baseline (12 Test-Apps)
- **Änderung & Dateien:** Determinismus-Fixes in `sdk_breadth_g15.py` (`SecureRandom`), `dex_loader.py` (`Random`/`UUID`), `os_linux_arm64.py`/`libc_stubs.py` (`getrandom`/`arc4random_buf`) sowie Messung der `hook_hits_by_pkg` in `tools/measure_suite.py`. Ablage `Refactoring/measurements/001_baseline_*.json` + `Refactoring/Baseline_Revision3.md`.
- **Abnahmekriterium:** `3/3` Wiederholungen je App liefern über alle Zählgrößen `Abweichung == 0` (oder explizit lokalisierte & dokumentierte Restursache falls `> 0`), und für alle 12 Test-Apps existiert eine vollständige Baseline-Zeile.
- **Pflichttest:** `tests/test_step1_reproducibility.py` (2× direkt aufeinanderfolgende Läufe mit identischem Seed/VFS liefern identische Zählgrößen).
- **Risiken & Ausweichplan:** Nichtdeterministische Iteration über ungeordnete Mengen (`set`) oder verbleibende `time.time()`-Aufrufe in Stubs → Ursache mittels Diff identifizieren, vor der Baseline deterministisch sortieren/an `VirtualClock` binden.

### Schritt 2a — Produzenten & Demos abklemmen, ehrliche Anzeige, Sitzungs-Netzschranke (`ask`) & Schwärzung
- **Änderung & Dateien:**
  - `core/java/app_runtime.py`: `mount_inphone_runtime_if_needed` deaktivieren (gibt `False` zurück, setzt nie `render_source = "reconstructed"`).
  - `core/java/lifecycle.py` & `core/java/seam_config.py`: Aufruf von `mount_inphone_runtime_if_needed` entfernen, `auto_finish_empty_onboarding = False`, verschluckte Lifecycle-Exceptions ins `DeviationJournal` (`lifecycle_exception`).
  - `core/java/rn_hermes_bridge.py`: `rnreal.hbc`-Fallback entfernen.
  - `ui/tabs/display_tab.py`: Kein `demo_screen()` beim Start (leerer `ViewTree`), `Demos ▾`-Menü und `[Rekonstruiert]`-Zweige abklemmen, `_render_status_overlay` als ehrliche Stop-Diagnose (`stop_reason`, `stop_site`, `dalvik_steps`, jüngste `DeviationJournal`-Einträge).
  - `ui/controllers/debugger_controller.py`: `mount_inphone_runtime_if_needed` (Z. 946) und `DEFAULT_SMALI` (`dbl`) abklemmen, Fake-`CONNECT tcp://... -> 200` (`_sync_fusion_os_net_to_log`) entfernen; **Sitzungs-Netzschranke** `net_mode = "ask"` als GUI-Default (`pause_reason = "net_gate_first_request"`, Auswahl `Offline` / `Replay` / `Live` je Sitzung, kein stilles `200 OK 0B` mehr!); Austauschpfade von `secrets/probe/out/...` nach `data/debug_sync/...` verlegen (auch `core/java/ui_mcp_bridge.py`, `native_studio_controller.py`, `mcp_server/tools.py`) + `redact_obj(...)` & `include_bodies=False` auf alle Schreibpfade.
  - `mcp_server/tools.py`: `rn_mount_recorder`-Fallback in `native_glass_run` abklemmen.
- **Abnahmekriterium:** In der Kontrollmessung (`002_step2a_*.json`) gilt bei allen **12/12 Apps**: `render_source != "reconstructed"` (`0` rekonstruierte Apps) und `net_requests_by_origin.get("framework_producer", 0) == 0`; Zählgrößen der Nicht-Onboarding-Apps identisch zur Baseline.
- **Pflichttest:** `tests/test_step2a_honest_display_and_netgate.py` (prüft: keine Rekonstruktion, `0` Producer-Requests, Halt am 1. Netzzugriff bei `ask`, `ConnectException` bei `offline`/`replay`-Miss, geschwärzte Secrets & keine Bodies in `live_state.json`).
- **Risiken & Ausweichplan:** Bestehende Tests wie `test_display_fallback.py` / `test_virtuagym_onboarding_fix.py`, die das alte `reconstructed`-/Auto-Skip-Verhalten erwarten → auf die neue ehrliche Anzeige bzw. explizite Test-Opt-ins prüfen.

### Schritt 2b — Zentrale DEX-Hook-Schranke (nach definierender Klasse!) & neuer Maintab `🎛️ Engine & Seams`
- **Änderung & Dateien:**
  - `core/java/dalvik.py` (`invoke_virtual` Z. 455, `_do_invoke` Z. 1574/1589) & `core/java/dex_loader.py` (`_method_invoke` Z. 5393):
    - **Korrigierte Regel:** Zuerst gecachtes `_resolve_method_cached(rcls, cls, name, sig)`! Findet sich eine Definition im DEX der App (`md is not None`, inkl. dynamischer DEXe), läuft **immer** der App-Bytecode `md` (`_run_mdef`), **außer** das Paket der **definierenden Klasse `md.cls`** steht auf `allowed_hook_packages` (oder ein manueller `user_method_override` greift). Nur wenn `md is None` ist (und es sich weder um eine deklarierte App-`ACC_NATIVE`-Methode noch um ein nicht-geproxytes App-DEX-Interface handelt), greifen Umgebungshooks normal (z. B. `MyView extends TextView` → `setText`!).
    - Alle Abfangvorgänge über `allowed_hook_packages` erzeugen zentral `DeviationJournal.record("hook_intercept", site, detail=pkg)` + `hook_hits_by_pkg[pkg] += 1`.
  - `core/java/engine_settings.py` `[NEW]` & `ui/tabs/engine_settings_tab.py` `[NEW]` + Einbindung in `gui.py`: neuer Hauptreiter **`🎛️ Engine & Seams`** (ARM64-Backend pro Lauf `arm64mini` | `unicorn`, Bereichs-Trace-Steuerung, Differenztest-Button, Tabelle aller Paketausnahmen mit Checkbox + Live-Trefferzähler + `Profil Faithful`-Button, Wachpunkt-Toggles, Sitzungs-Netzmodus).
  - Hotpath-Benchmark `tests/bench_hook_gate_hotpath.py` (`1.000.000` Calls, 5 Läufe, Median & IQR).
- **Abnahmekriterium:**
  1. `MyView extends TextView` (`setText`) wird bei leerer Ausnahmeliste bedient; eine im App-DEX definierte Methode auf `MyView` oder `Lokhttp3/` wird bei leerer Ausnahmeliste **nicht** abgefangen (`0` Treffer).
  2. Kontrollmessung mit Baseline-Ausnahmen (`003_step2b_allowlist_*.json`): alle 12 Apps identisch zu `002_step2a` (`Delta == 0`).
  3. Kontrollmessung im Profil `faithful` (`004_step2b_faithful_*.json`): `sum(hook_hits_by_pkg.values()) == 0` bei allen **12/12 Apps**.
  4. Hotpath-Overhead `< 15 %` gegenüber vor der Schranke (belegt durch `bench_hook_gate_hotpath.py`).
- **Pflichttest:** `tests/test_step2b_defining_class_gate.py`.
- **Risiken & Ausweichplan:** Falls Methoden in Super-Interfaces ohne Body (`md is None`, z. B. `@GET`-Retrofit-Proxy oder `Composer`) noch durchrutschen → explizite Prüfung von `_find_apk_abstract_declarer` (bereits eingeplant). Falls Hotpath-Overhead `> 15 %` → Fast-Path `if not self._method_hooks and not self.method_overrides:` bzw. separates `(rcls, cls, name, sig)`-Tupel-Dispatch-Cache.

### Schritt 3 — Wachpunkte „Halt bei Abweichung“ (GUI + MCP) & Vorgezogener LLU-Dalvik-Dekoder-Fix
- **Schritt 3a (`core/java/deviation_journal.py`, `core/java/debug.py`, `dalvik.py`, `dex_loader.py`, `native_link.py`, `ui/tabs/debugger_tab.py`, `debugger_controller.py`, `mcp_server/tools.py`):**
  - *Änderung:* Watchpoint-Callback an `DeviationJournal` (`pause_reason = "watchpoint:<kind>"`), Umsetzung der 3 Pflicht-Wachpunkte (`invalid_native_load`, `invalid_class_identifier`, `unbound_native`), neuer Dock-Reiter **`Abweichungen`** in `DebuggerTab`, Toggles in `⚙ Optionen ▾` + `🎛️ Engine & Seams`, Befund-Export & MCP-Integration (`section="deviations"`, Stopp bei `watchpoint:*` in `native_glass_run`).
  - *Abnahmekriterium:* Jeder der 3 Wachpunkte hält `JavaDebugger` sofort am exakten Auslöse-Frame & -PC (`mode == "paused"`, `pause_reason == "watchpoint:<kind>"`); Dock-Reiter & MCP melden denselben Eintrag.
  - *Pflichttest:* `tests/test_step3a_deviation_watchpoints.py`.
- **Schritt 3b (Vorgezogener Dalvik-Dekoder-Fix an `org.nativescript.LibreLinkUp`):**
  - *Änderung:* Analyse des LLU-Dekoder-Fehlers mittels Wachpunkten und generische AOSP-Korrektur in `core/java/dalvik.py` / `dex_loader.py` / `fs_bridge.py` (keine LLU-Hardcodes!).
  - *Abnahmekriterium:* `org.nativescript.LibreLinkUp` durchläuft den Dekoder ohne `invalid_native_load`/`invalid_class_identifier`-Stopp und lädt mindestens **1 reale native Bibliothek bis `JNI_OnLoad`** (`loaded_libs_count >= 1`, `bound_natives_register + bound_natives_static >= 1`).
  - *Pflichttest:* `tests/test_step3b_dalvik_decoder_fix.py`.
  - *Risiken:* Falls `SoLoader`/Packer nach dem ersten Fix zusätzlich fehlende JDK-/Bionic-Syscalls benötigt → exakt die nächste Bruchstelle per Wachpunkt isolieren und generisch nachziehen.

### Schritt 4 (4a–4h) — Einzelnes Abklemmen jeder Fälschung & AOSP-Exceptions (jeweils Kontrollmessung)
- **4a — Externe Entpacker & Regel-Resolver abklemmen + Ziel-Hardcodings säubern** (`fusion_loader.py`, `services/native_bridge/__init__.py`, `gui.py`, `native_jni_env_service.py`, `native_devirt_service.py`, `native_trace_service.py`, `apk_steps.py`, `webview_inspector_service.py`, `app_manager_tab.py`, `mcp_server/*`):
  - *Abnahmekriterium:* `allow_external_dec_cache` hat im Debugger keinen Effekt (`0` Aufrufe an `native_plugins/`), `RuleBasedResolver` liefert `0` Treffer in `build_chain()`.
  - *Test:* `tests/test_step4a_no_external_unpack_or_rules.py`.
- **4b — Echte JCA-Krypto (`javax.crypto.Cipher` & `java.security.Signature`)** (`sqlite_prefs_bridge.py`, `crypto_billing_g11.py`):
  - *Abnahmekriterium:* Standard-NIST-Testvektoren für `AES/GCM/NoPadding`, `AES/CBC/PKCS5Padding`, `AES/ECB/PKCS5Padding` und `SHA256withRSA`/`SHA256withECDSA` liefern bit-exakte Übereinstimmung mit OpenSSL/`cryptography`.
  - *Test:* `tests/test_step4b_jca_real_crypto.py`.
- **4c — Passiver TLS-Tap & Abklemmen von `boringssl_seam`/`libcurl_seam`** (`core/native/tls_passive_tap.py` `[NEW]`, `os_linux_arm64.py`, `fusion_loader.py`):
  - *Abnahmekriterium:* `install_boringssl_seam` und `install_libcurl_seam` werden nicht mehr auf `ffi` registriert (`0` Treffer). Bei dynamischem `libssl.so` schneidet der passive Probe Klartext mit, ohne Register/Verify zu fälschen; bei statisch gelinktem TLS ohne Keylog erscheint ehrlich `TLS -> <SNI/IP>:<Port> (<N> Bytes verschlüsselt)` im `NetLog`.
  - *Test:* `tests/test_step4c_passive_tls_no_seam.py`.
- **4d — Gefälschten ZXing-QR-Hook abklemmen** (`core/java/view_tree.py`):
  - *Abnahmekriterium:* Echter `com.google.zxing.*`-DEX-Code läuft in der VM und erzeugt scannbare QR-Bitmatrizen (keine SHA-256-Zufallsbits).
  - *Test:* `tests/test_step4d_real_zxing_dex.py`.
- **4e — `_maybe_lazy_inject` (`@Inject`-Erfindung) abklemmen** (`core/java/dalvik.py`):
  - *Abnahmekriterium:* Ein uninitialisiertes `@Inject`-Feld liefert `None` (`null`), `0` erfundene Instanzen.
  - *Test:* `tests/test_step4e_no_lazy_inject.py`.
- **4f — R8-`io.realm.*`-Mock, `EncryptedSharedPreferences`-Bypass, Picasso/Coil-R8-Hooks, Virtuagym-Spezialfälle & Button-Text-Mutation abklemmen** (`engine_stubs_g14.py`, `view_tree.py`, `layout_inflater.py`):
  - *Abnahmekriterium:* `0` Abfangtreffer auf R8-Realm/Picasso/Coil, `button.getText()` wird vom Inflater nie verändert.
  - *Test:* `tests/test_step4f_no_target_ui_or_realm_fakes.py`.
- **4g — RASP-Mock-Policy & `survive_enable` abklemmen** (`native_jni_env_service.py`, `native_trace_service.py`):
  - *Abnahmekriterium:* Fehlendes `java_runtime` liefert nie gefälschtes `0 ("clean")` durch `_CLASS_PATTERNS`; `survive` biegt im Debugger keine Faults auf `lr` um.
  - *Test:* `tests/test_step4g_no_rasp_mock_or_survive.py`.
- **4h — AOSP-`ClassNotFoundException` & `UnsatisfiedLinkError` scharfschalten** (`dex_loader.py`, `sdk_breadth_g15.py`, `fs_bridge.py`, `dalvik.py`, `native_link.py`, `fusion_loader.py`):
  - *Abnahmekriterium:* `Class.forName("com.missing.Fake")` wirft `ClassNotFoundException`; Aufruf einer ungebundenen `native`-Methode nach Watchpoint-Weiterlauf wirft `UnsatisfiedLinkError`; Kontrollmessung `005_step4h_*.json` dokumentiert jede dadurch sichtbar werdende reale Lücke über alle 12 Apps.
  - *Test:* `tests/test_step4h_aosp_cnfe_and_ule.py`.

### Schritt 5 — Unicorn-Backend (Großmessung >= 50M, Entscheidungspunkt, 1 Backend pro Lauf, Differenztest & Hermes-Bytecode-Debugger)

- **Schritt 5a — Großmessung (`>= 50.000.000` Instruktionen inkl. `libhermes.so`) & Vorab definierte Entscheidungsregel:**
  - *Änderung & Dateien:* `core/native/unicorn_backend.py` `[NEW]` (Minimal-Adapter mit `CPACR_EL1.FPEN = 3 << 20`, `TPIDR_EL0`, 0 Hooks im Zeitmesslauf) + `tools/bench_unicorn_50m.py` `[NEW]`.
  - *Messlasten:*
    1. `libmmkv.so`: `>= 50.000.000` dynamische C++-Instruktionen (`arm64mini` Referenz vs. `unicorn` 0 Hooks).
    2. `libhermes.so` + `libc++_shared.so` (`com.ecoflow`): `>= 50.000.000` dynamische AArch64-Instruktionen auf realem Hermes-Code (0 Hooks vs. aktiver Einzeladress-Breakpoint `[bp, bp]`).
  - *Vorab definierte Entscheidungsregel (Hochrechnung `T_hermes_bundle_s = 150_000_000 / (MIPS_hermes_median * 1e6) + T_load_s`):*
    - **`>= 15,0 MIPS`** (`T_hermes_bundle_s <= 10,0 s`): Schritt 5b, 5c und 6.6 werden wie geplant voll umgesetzt.
    - **`5,0 .. < 15,0 MIPS`**: Heiße `libc`-Leaf-Funktionen (`memcpy`, `memset`, `strlen`, `memcmp`, `pthread_mutex_lock/unlock`) als AArch64-Maschinencode auf einer System-Trampolin-Seite ablegen (keine Python-Hook-Exits!) und erneut messen; erreicht der Median `>= 15,0 MIPS`, weiter wie geplant + Protokoll im `Abweichungsprotokoll`.
    - **`< 5,0 MIPS` auch nach Trampolin:** Schritt 5b + Differenztest abschließen, gemessene Startzeit im `Abweichungsprotokoll` dokumentieren und 6.6 sauber über das Zeitbudget mit ehrlichem Stop-Grund begrenzen.
  - *Abnahmekriterium:* Rohdaten unter `Refactoring/measurements/006_step5a_unicorn_50m_*.json` mit `>= 50_000_000` Instruktionen je Last, Median & IQR aus 5 Warmläufen, Einzeladress-BP-Overhead `< 15 %`, und dokumentierte Entscheidung im `Abweichungsprotokoll`.
  - *Pflichttest:* `tools/bench_unicorn_50m.py` selbst (prüft `arm64_instructions >= 50_000_000` und schreibt valide Mess-JSON).

- **Schritt 5b — Einheitliches `Arm64Engine` (1 Backend pro Lauf!) & Bitgenauer Differenztest:**
  - *Änderung & Dateien:* `core/native/engine_arm64.py`, `core/native/unicorn_backend.py`, `core/native/arm64mini.py` (additiver Fix: Basisblock-Schnitt an `probes`-Adressen für Mid-Block-BPs!), `services/native_emu_service.py`, `core/java/bridge.py` (`jstring`/`jbyteArray`/`jobject`-Marshalling & gemeinsamer JNI-Callstack). Ein einziges wählbares Backend pro App-Lauf (`arm64mini` | `unicorn`) im Maintab `🎛️ Engine & Seams` — **niemals zwei Backends im selben Lauf**.
  - *Abnahmekriterium:* Registerabweichung (`x0..x30`, `sp`, `pc`, `nzcv`, `v0..v31`) `== 0`, Speicherabweichung geschriebener Seiten `== 0` über alle Testfunktionen inkl. `libnative-lib.so` & `libmmkv.so`; Mid-Block-Breakpoints und `count=1`-Einzelschritt funktionieren identisch auf beiden Backends; reentrantes `Java -> Native -> Java -> Native` (`depth >= 2`) arbeitet fehlerfrei.
  - *Pflichttests:* `tests/test_arm64_backend_diff.py`, `tests/test_arm64mini_contract.py`.

- **Schritt 5c — Debugger-Sicht auf Hermes-Bytecode-Ebene (In-RAM, ohne externes Bundle-Entpacken):**
  - *Änderung & Dateien:* `core/native/hermes_hbc_debug.py` `[NEW]` + Panel **`📜 Hermes / JS`** in `ui/tabs/debugger_tab.py` + `mcp_server/tools.py`.
  - *Generische Umsetzung:* Erfassung von `(hbc_base, hbc_len)` im laufenden Emulator-RAM beim Aufruf von `HermesRuntime::isHermesBytecode`/`evaluateJavaScript` bzw. HBC-Magic-Scan im Mapped-RAM; versionssensitiver In-RAM-HBC-Parser & Disassembler; JS-Funktions-Haltepunkte per 1-Byte-`UC_HOOK_MEM_READ`-Watchpoint am Ziel-Bytecode-Byte `[ip_va, ip_va]`; Dekodierung von Hermes-Callstack (`SavedIP`/`PreviousFrame`) und NaN-geboxten `HermesValue`s beim Halt.
  - *Abnahmekriterium:* Halt am Eintritt einer JS-Funktion im laufenden `libhermes.so`-Run (`bc_offset == 0`), nicht-leere Disassembly (`insns_count > 0`), Callstack-Tiefe `>= 1` und korrekte Dekodierung von `Number`/`String`/`Boolean` ohne externen Dateizugriff auf das Bundle.
  - *Pflichttest:* `tests/test_step5c_hermes_bytecode_debugger.py`.

### Schritt 6 (6.1–6.7) — Kette schließen & Paket-Ausnahmeliste paketweise abbauen

Im Profil `faithful` (Ausnahmeliste leer bzw. gezielter A/B-Nachweis über `BoundaryTraceRecorder`):

- **6.1 `kotlinx.coroutines`:** AOSP-Lücken (`Looper`, `Handler`, `Choreographer`, `ThreadLocal`, `Executor`, `Unsafe`) schließen → `Lkotlinx/coroutines/` von Ausnahmeliste abbaubar (gleiche `VIEW`/`NET`/`SQL`-Boundary-Spur). Test: `tests/test_step6_1_coroutines_real.py`.
- **6.2 `androidx.room` / `androidx.sqlite`:** AOSP-SQLite-Lücken schließen → gleiche normalisierte `SQL`-Boundary-Spur mit vs. ohne Hook → `Landroidx/room/`/`Landroidx/sqlite/` abbaubar. Test: `tests/test_step6_2_room_real.py`.
- **6.3 `okhttp3` / `retrofit2` / `moshi`:** Smali-`.annotation runtime` + generische Signaturen in Reflection (`dex_loader.py` / `class_provider.py`) + echte `Socket`/`SSLSocket`-Umgebungsgrenze (`net_recorder.py`, im Test gegen Loopback-Server, unbeaufsichtigt strikt `offline`!) → App-Interceptors/Auth/Signaturen laufen im Original-DEX; identische `NET`-Boundary-Spur → `Lokhttp3/`, `Lretrofit2/`, `Lcom/squareup/moshi/` abbaubar. Test: `tests/test_step6_3_okhttp_retrofit_real.py`.
- **6.4 `androidx.lifecycle`, `fragment`, `recyclerview`, `navigation`, `paging`, `work` & Klassische Apps (`smartlock`, `stickermaker`, `babycam`):** AOSP-UI-/Lifecycle-Lücken schließen → klassische Apps erzeugen `render_source == "executed"` (`viewtree_nodes > 0`) und laden ihre `.so`-Bibliotheken ohne `reconstructed`. Test: `tests/test_step6_4_classic_and_fragments.py`.
- **6.5 Hybrid Web (`com.scinan.TieFuZX.Refrigerator`):** Echte Activity erreicht System-`WebView` (`webview_bridge.py`) ohne `hybrid_web_producer`. Test: `tests/test_step6_5_hybrid_webview.py`.
- **6.6 React Native (`alpicool`, `ecoflow`, `LibreLinkUp`) auf `unicorn`:** Native Bootkette + Fabric/UIManager-JNI-Brücke bis reale Android-`View`s entstehen. **Abnahmekriterium:** Mindestens eine RN-Test-App erzeugt über ausgeführten Java+Native+Hermes-Fabric-JNI-Code reale Views im `ViewTree` (`viewtree_nodes > 1` und `render_source == "executed"`), oder benennt nach maximalem Durchstich exakt die blockierende Systemgrenze. Test: `tests/test_step6_6_react_native_fabric_real.py`.
- **6.7 Compose (`kueckbox`, `glucobridge`, `virtuagym`) & Flutter (`kippynew`, `fiio`):** `glucobridge` lädt & initialisiert echte Native-Libs auf `arm64mini`; Compose schreitet möglichst weit ohne Ausnahme voran (offene Restausnahmen transparent ausweisen); Flutter lädt `libflutter.so`/`libapp.so` auf `unicorn` mit ehrlicher Surface-Statusanzeige (`render_source != "reconstructed"`). Test: `tests/test_step6_7_compose_and_flutter.py`.

### Schritt 7 — Abschlussmessung & Abschlussbericht (`Refactoring/Abschluss_Revision3.md`)
- Identische Abschlussmessung mit `tools/measure_suite.py` (`999_final_arm64mini_*.json` + separater `--backend unicorn`-Vergleichslauf) + `py_compile` + vollständige Testläufe über `Python312`.
- Erstellung von `Refactoring/Abschluss_Revision3.md` nach Abschnitt 7 der Vorgabe (offene Unsicherheiten ganz oben, Punkt-für-Punkt-Auswertung `erreicht | teilweise | nicht erreicht` gegen `Plan_Revision3.md`, 12-App-Tabelle `Baseline vs. Endstand`, `Abweichungsprotokoll`, `Löschkandidaten`) und Nachtrag in `Dateikarte.md`.

---

## 4. Checkliste der zu pflegenden Dateien

Alle neu erstellten und geänderten Dateien werden laufend in `Dateikarte.md` dokumentiert.

---

## 5. Abweichungsprotokoll

*(Nach Einfrieren dieses Plans darf ausschließlich dieser Abschnitt ergänzt werden.)*

| Datum | Schritt | Was anders gemacht wurde | Warum | Auswirkung auf Abnahmekriterien |
|---|---|---|---|---|
| — | — | *(noch keine Abweichungen)* | — | — |
