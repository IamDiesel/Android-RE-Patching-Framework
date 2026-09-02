### Refactoring Plan: Phase III (Fat Views & Service Extraction)

Um die UI vollständig von der Geschäftslogik zu entkoppeln, werden die Tabs zu reinen Präsentationsschichten ("Dumb Views") degradiert. Die Logik wandert in spezialisierte Controller und Services.

#### Stage 11: API Inspector & Database Abstraction

* **Erstellung von `services/api_db_service.py`:**
* Auslagerung aller SQLite3-Operationen (`init_db`, `INSERT`, `UPDATE`, `SELECT`) aus dem `APIInspectorTab`.


* Die UI ruft nur noch Methoden wie `db_service.get_filtered_requests(query)` auf.


* **Erstellung von `services/proxy_service.py`:**
* Kapselung des `mitmdump`-Subprozesses (`start_proxy`, `stop_proxy`, Output-Reading).


* Der Service publiziert `EventBus.publish("PROXY_LOG", line)`.


* **Erstellung von `services/adb_network_service.py`:**
* Kapselung der ADB-Netzwerkbefehle (`route_usb`, `route_wlan`, `reset_route`, `push_cert`) aus der View.





#### Stage 12: Workspace Controller & Logging Services

* **Erstellung von `ui/controllers/workspace_controller.py`:**
* Auslagerung der Threading-Logik für `run_build`, `run_flash` und `push_clean_apk` aus dem `WorkspaceTab`.


* Kapselung des JSON-Speichervorgangs für `save_current_as_favorite`.


* Fix des defekten Frida-Imports: Der Controller nutzt den korrekten `frida_service.py`.


* **Erstellung von `services/logcat_service.py`:**
* Kapselung des `CommandRunner.run_background`-Aufrufs, des `stdout`-Threadings und des Schreibens der Log-Datei (`log_file_handle`) aus dem `LauncherLoggerTab`.


* Der Service streamt Log-Zeilen via `EventBus.publish("LOGCAT_LINE", line)` an die UI.


* **Erstellung von `services/profile_manager_service.py`:**
* Übernahme der JSON I/O-Logik (`load_profiles`, `save_template`) aus dem `LauncherLoggerTab`.





#### Stage 13: Smali Studio Controller Konsolidierung

* **Delegation von `unpack_apk_async`:**
* Verschiebung der asynchronen Entpack-Logik aus `ui/tabs/smali_studio_tab.py` in den `ui/controllers/smali_studio_controller.py`. Die View bindet den Button nur noch an `self.controller.unpack_apk_async()`.





Möchtest du mit Stage 11 (API Inspector & SQLite Auslagerung) beginnen, oder sollen wir zuerst den defekten Frida-Import und die Workspace-Logik in Stage 12 reparieren?