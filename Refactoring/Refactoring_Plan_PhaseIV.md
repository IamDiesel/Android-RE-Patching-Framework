# Refactoring Plan: Phase IV (Final Polish, Performance & MVC-Completion)

Dieser Plan beschreibt die strukturierten Schritte zur Behebung der verbleibenden Architektur-Inkonsistenzen, zur funktionalen Erweiterung des Hex-Patchers und zur massiven Leistungssteigerung der GUI bei großen Dateien.

---

## 1. Hex Patcher Flexibilisierung & Favoriten-Integration
**Problem:** Das Ziel für Hex-Patches ist in der UI-Synchronisation (`SessionState` und `WorkspaceTab`) hart auf `libflutter.so` kodiert[cite: 25, 47].
**Maßnahmen:**
* **UI-Update (`WorkspaceTab`):** Erweiterung der Methode `add_patch_row()` um ein neues `ttk.Entry` für den Dateinamen[cite: 25]. Standardwert bleibt "libflutter.so", ist aber nun editierbar.
* **State-Update:** Anpassung von `sync_ui_to_state()`, um den Wert aus dem neuen Textfeld auszulesen[cite: 25].
* **Favoriten-Manager (`FavoritePatchesDialog`):** Anpassung der Methoden `display_sub_patch()` und `save_current()`, damit das `ent_file`-Feld auch bei Hex-Patches korrekt ausgelesen, angezeigt und im JSON gespeichert wird[cite: 18].

---

## 2. Bereinigung der `WorkspaceTab` Altlasten (Strict MVC)
**Problem:** Das `WorkspaceTab` enthält weiterhin Geschäfts- und Threading-Logik, die in den Controller gehört[cite: 25].
**Maßnahmen:**
* **`attach_frida` verschieben:** Auslagern der Threading-Logik (`threading.Thread(target=task, daemon=True).start()`) in den `WorkspaceController`[cite: 25]. Die View ruft nur noch `self.controller.attach_frida()` auf.
* **Config-State-Binding:** Die Methoden `on_lspatch_toggled`, `on_native_lib_changed`, `on_frida_toggled` und `on_strategy_changed` (inkl. `self.app.cfg.save()`) wandern in den Controller[cite: 25].
* **`save_result` auslagern:** Das Sammeln der Session-Daten und der Aufruf von `self.app.history.add_record(record)`[cite: 25] wird als `save_session_result()` in den `WorkspaceController` überführt. Die View übergibt lediglich die Rohdaten (Name, Version, Observation, Status).

---

## 3. "Fat Dialogs" auf Kur schicken (Controller & Services für Popups)
**Problem:** Die großen Dialog-Klassen agieren parallel als View, Controller und Service (JSON I/O, Fuzzing)[cite: 14, 18].
**Maßnahmen:**
* **`services/favorite_service.py`:** Ein neuer Service übernimmt das Laden und Speichern der `favorite_patches.json` (inklusive Fehlerbehandlung)[cite: 18].
* **`FavoritePatchesController`:** Steuert die Logik für das "Batch-Anwenden" und Iterieren durch Sub-Patches. Der `FavoritePatchesDialog` wird zur Dumb View.
* **`FuzzyMatchController`:** Lagert die Orchestrierung der Fuzzing-Engine und das parallele Suchen aus dem `FuzzyMatchDialog` aus[cite: 14].

---

## 4. Optimierung der Fuzzing-Engine (Cross-App Matching)
**Problem:** Die `FuzzingEngine` nutzt derzeit in `fuzz_by_content_snippet` einen extrem strikten Ansatz (`longest_line = max(lines, key=len)` und striktes `in`), der fehlschlägt, wenn sich z.B. Register-Namen (`v0` statt `v1`) in anderen Apps ändern[cite: 58].
**Maßnahmen:**
* **Opcode-Normalisierung:** Implementierung einer Funktion in der `FuzzingEngine`, die Register (`vX`, `pX`) aus den Vergleichszeilen entfernt[cite: 58]. Der Fuzzer vergleicht dann nur noch die reine Befehlsstruktur (Opcodes).
* **Heuristische Ähnlichkeit:** Nutzung von `difflib.SequenceMatcher` oder einer Levenshtein-Distanz für den Block-Vergleich, anstatt eines booleschen Exakt-Matches[cite: 58]. Toleranzgrenze (z.B. >85% Ähnlichkeit) einführen.
* **Globale Fallback-Suche:** Wenn die Zieldatei (Klassenname) in der neuen App geändert wurde (z.B. durch Obfuskation), weitet die Engine die Suche nach dem heuristischen Block auf den gesamten RAM-Cache aus.

---

## 5. Performance-Optimierung im Smali Studio (Ladezeiten & UI Freezes)
**Problem:** Sehr große Patches/Dateien blockieren die UI[cite: 26]. Der Flaschenhals ist die Methode `apply_highlighting` im `SmaliEditorWidget`, die synchron unzählige komplexe Regex-Muster (`re.finditer`) über den gesamten Text (oft zehntausende Zeilen) laufen lässt[cite: 26]. Auch der Live-Diff im Favoriten-Manager (`_apply_diff`) rechnet synchron[cite: 18].
**Maßnahmen:**
* **Lazy Highlighting (Chunking):** Die `apply_highlighting` Funktion wird so umgebaut, dass sie nicht den gesamten Text, sondern nur den aktuell sichtbaren Bereich (Viewport) parst, oder die Verarbeitung über `self.after()` in kleinen Chunks asynchron abarbeitet.
* **Debouncing & Threading für Diffing:** Der `difflib.SequenceMatcher` in den Dialogen wird in einen Hintergrund-Thread ausgelagert[cite: 14, 18]. Das Ergebnis wird via `EventBus` an die UI geschickt, woraufhin erst das Tagging (`tag_add`) erfolgt.
* **Lade-Indikator:** Hinzufügen einer progressiven Ladeanzeige (Overlay oder im Header des Text-Widgets), solange die Hintergrund-Verarbeitung (Highlighting/Diffing) für große Dateien noch läuft.

---

## 6. Erweiterung der Unit Tests
**Problem:** Die neu geschaffenen Logik-Komponenten benötigen Testabdeckung, um Regressionen zu vermeiden.
**Maßnahmen:**
* **`test_fuzzing_engine.py` aktualisieren:** Neue Testfälle schreiben, die nachweisen, dass die Engine trotz geänderter Register (`v0` -> `v2`) den Code-Block erfolgreich findet[cite: 29].
* **`test_workspace_controller.py`:** Tests für die ausgelagerte Session-Logik (z.B. Testen der `save_result` Formatierung und Hex-Patch-State-Synchronisation).
* **`test_favorite_service.py`:** Verifizierung der korrekten JSON-Manipulation für Favoriten.