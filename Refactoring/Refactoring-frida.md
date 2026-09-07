# Architektur & Refactoring Plan: Frida Integration (V8)

## 1. Motivation und Zielsetzung
Die aktuelle Frida-Integration im Kippy RE-Framework unterstützt primär den passiven **Listen-Modus** über eine direkte USB-Injektion beim App-Start. Um die Flexibilität bei dynamischen Analysen (DAST) zu maximieren, wird das Framework refaktorisiert, um alle vier Betriebsmodi des Frida-Gadgets (`Listen`, `Connect`, `Script`, `ScriptDirectory`) nativ, robust und automatisiert zu unterstützen[cite: 63].

## 2. Anforderungen (Betriebsmodi)

1. **Listen Modus:** Host verbindet sich via USB/ADB zur App. Skripte werden direkt aus der GUI gefeuert. Logs in beiden Konsolen[cite: 63].
2. **Connect Modus:** Framework startet lokalen TCP-Server. App agiert als Client. Skripte können live an die App gepusht werden[cite: 63].
3. **Script Modus:** Autark / Build-integriert. Das Skript wird beim Build mittels `frida-compile` transpiliert und physisch in die APK integriert[cite: 63].
4. **ScriptDirectory Modus:** Das Gadget überwacht `/data/data/{APP_PACKAGE}/files/frida_scripts/` und lädt Skripte on-the-fly[cite: 63].

**Generelle Anforderungen:**
* Alle Einstellungen (Modus, Pfade, Host, Port) werden persistent in der `config.json` verwaltet[cite: 63].
* **Nutzerführung:** Explizites Speichern der Konfiguration per Button. Bei ungespeicherten Änderungen greift ein Safety-Net (Sicherheitsabfrage) beim Schließen des Fensters.
* Verwaltung von Skripten als Sammlungen (Collections)[cite: 63].
* Saubere UI via einklappbarem Drawer-Panel (Side-Panel) für den Geräte-Sync und Collections[cite: 63].
* **Editor:** JS/TS-Syntax-Highlighting und vollwertiges horizontales/vertikales Scrollen (ohne erzwungenen Zeilenumbruch).

---

## 3. Technisches Design & Architektur
* **`core/domain/frida_models.py`**: Dataclasses (`FridaConfig`, `FridaScript`, `FridaCollection`)[cite: 63].
* **`services/frida_compiler_service.py`**: Zentraler Service für das Node.js Setup (`frida-compile`)[cite: 63].
* **`services/frida_server_service.py`**: Daemon für TCP-Tunnel und PortalService[cite: 63].
* **`services/frida_sync_service.py`**: Robuste ADB-Aufrufe via `run-as` in das isolierte App-Verzeichnis[cite: 63].
* **Erweitertes Event-Routing (`logcat_service.py`)**: Filtert native Frida-Ausgaben in beide Konsolen[cite: 63].

---

## 4. Implementierungsphasen

### Phase 1: Fundament (Abgeschlossen)
* [x] Definition der Architektur und Anforderungen[cite: 63].
* [x] Erstellung `FridaConfig`, `FridaScript` und `FridaCollection`[cite: 63].
* [x] Extraktion und Refactoring des `FridaCompilerService`[cite: 63].

### Phase 2: Core Services & Konfiguration (Abgeschlossen)
* [x] Anpassung `ConfigManager` (Integration des `FridaConfig` Modells)[cite: 63].
* [x] Implementierung `FridaServerService` (TCP-Tunnel, PortalService)[cite: 63].
* [x] Implementierung `FridaSyncService` (ADB Push/Pull/List mit `run-as`)[cite: 63].

### Phase 3: Pipeline & Logging (Abgeschlossen)
* [x] Refactoring `FridaInjectStep` in `hook_steps.py` (Dynamische Konfiguration)[cite: 63].
* [x] Anpassung `LogcatService`, um Frida-Ausgaben an den EventBus zu routen[cite: 63].

### Phase 4: GUI & Controller (Abgeschlossen)
* [x] Neuentwicklung `FridaManagerController` inkl. Collection Push-Logik[cite: 63].
* [x] GUI mit einklappbarem Drawer-Pattern für Sync/Sammlungen (inkl. Auto-Save, Shortcuts)[cite: 63].
* [x] Explizites Speichern der Konfiguration mit `askyesnocancel`-Safety-Net.
* [x] JS/TS-Syntax-Highlighting (Regex-basiert) und erweiterte Editor-Scrollbars.

### Phase 5: RASP Evasion & SELinux Stabilität (Abgeschlossen)
* [x] Tarnung der Frida-Artefakte (`libfrida-gadget.so` -> `libmetrics.so`) im Build-Prozess zur Umgehung von statischen RASP-Scans.
* [x] Deaktivierung der `inotify`-Hooks in den Autark-Modi (`"on_change": "ignore"`), um SELinux-Crashes zu vermeiden.
* [x] Anpassung der dynamischen Cleanup-Routine zur rückstandslosen Beseitigung der getarnten Artefakte bei Deaktivierung.

---

## 5. Technische Risiken & Lösungsansätze (Mitigiert)
1. **App-Permissions (ScriptDirectory):** Wir pushen Skripte nach `/data/local/tmp/` und verschieben sie via `adb shell "run-as {APP_PACKAGE} cp ..."` ins Datenverzeichnis, da ADB ohne Root nicht direkt in `/data/data/` schreiben darf.
   * **Mitigation:** Beim Speichern des `ScriptDirectory`-Modus warnt die GUI automatisch, wenn das `INJECT_DEBUGGABLE`-Flag deaktiviert ist, und bietet an, dieses automatisiert zu setzen.
2. **Kompilierungs-Overhead & Systemabhängigkeit:** Der `FridaCompilerService` cacht den Node.js Workspace, um `npm install` nur einmalig auszuführen.
   * **Mitigation:** Der `ToolManager` lädt auf Windows-Systemen ab sofort eine portable Node.js-Version (`node.exe`) vollautomatisiert herunter und injiziert sie in den lokalen Umgebungspfad (`PATH`). Es müssen keine Systempakete vorinstalliert werden.
3. **SELinux Crashes & Statische RASP-Erkennung:** Frida stürzt auf restriktiven Android-Systemen ab, wenn Dateisystem-Hooks gesetzt werden.
   * **Mitigation:** Die injizierten Dateien werden dynamisch in `libmetrics.so` etc. umbenannt, um statische String-Scans ins Leere laufen zu lassen. Durch die Konfiguration `"on_change": "ignore"` werden kritische SELinux-Verstöße im Autark-Betrieb verhindert. Der entsprechende Smali-Load-Call in der App (`invoke-static {v0}, Ljava/lang/System;->loadLibrary(...)`) muss im Workspace-Tab explizit auf `"metrics"` angepasst werden.
4. **Cross-Thread GUI Updates:** Alle Services nutzen streng den `EventBus`, sodass Tkinter nicht blockiert oder abstürzt, wenn der Server oder ADB-Prozess im Hintergrund agiert.