# Konzept- und Umsetzungsplan: Smali Studio UX & Advanced Patching

Dieses Dokument beschreibt die Architektur-Updates und den schrittweisen Implementierungsplan für die erweiterten Smali-Studio-Funktionen, das hierarchische Patching und die neuen UX-Elemente. Alle Anpassungen respektieren das bestehende MVC-Muster und die zustandslose Service-Architektur.

---

## Phase 1: Navigations-Historie (Treeview + Vor/Zurück)

### Anforderungen

* Lückenlose Aufzeichnung aller besuchten Smali-Methoden und -Dateien während einer Session.
* Vor- und Zurück-Buttons (◀ / ▶) zur schnellen linearen Navigation zwischen den zuletzt besuchten Code-Blöcken.
* Ein neuer Reiter "Historie" im linken Notebook des Smali Studios.
* Die Historie soll als Baumstruktur (Package -> Datei -> Methode) dargestellt werden. Doppelklick auf einen Knoten lädt den Code erneut.

### Konzept & Architektur

* **State Management:** Einführung zweier Stacks (`back_stack`, `forward_stack`) im `SmaliStudioController`. Jeder Eintrag speichert den Pfad und die Signatur.
* **UI-Erweiterung (`smali_studio_tab.py`):** Einbau der Buttons über dem Editor und Hinzufügen des neuen "Historie"-Tabs mit einer `ttk.Treeview`.
* **Datenaufbereitung:** Eine neue Hilfsmethode zerlegt die Dateipfade (z.B. `smali_classes2/com/app/Main.smali`) und baut daraus dynamisch die Parent-Child-Beziehungen für die Treeview.

### Umsetzungsplan

1. `SmaliStudioController` anpassen: Stacks initialisieren und `load_method` so erweitern, dass der aktuelle Aufruf auf den `back_stack` gelegt wird (sofern es keine "Back/Forward"-Aktion war).
2. `smali_studio_tab.py` anpassen: Die UI-Elemente rendern und die Events (`<Double-1>`, Button-Commands) an den Controller binden.
3. Rendering-Logik für die Historien-Treeview implementieren, sodass identische Klassen nicht mehrfach auftauchen, sondern deren Methoden gruppiert werden.

---

## Phase 2: "Ganze Datei" Ansicht & Editor-Toggle

### Anforderungen

* Möglichkeit, zwischen der isolierten Methoden-Ansicht und der kompletten Dateiansicht umzuschalten.
* Der Nutzer soll an beliebiger Stelle in der Datei neuen Code (z.B. neue Methoden oder Felder) hinzufügen können.
* Die bestehende Outline-Funktionalität soll erhalten bleiben und idealerweise einen "[Komplette Datei]"-Knoten erhalten.

### Konzept & Architektur

* **Service-Erweiterung (`smali_fs_service.py`):** Neben `extract_method_block` wird eine Methode benötigt, die den gesamten Dateiinhalt als Block zurückgibt.
* **UI-Toggle (`smali_editor_widget.py`):** Ein Segmented Button oder eine Checkbox über dem Original-Textfeld wechselt den Anzeigemodus.
* **Controller-Logik (`smali_studio_controller.py`):** Beim Laden einer Methode wird geprüft, welcher Modus aktiv ist. Ist der Modus "Ganze Datei" aktiv, wird die Datei im Editor gerendert, aber die Outline hebt weiterhin die angeklickte Methode hervor.

### Umsetzungsplan

1. Umschalter in `SmaliEditorWidget` integrieren.
2. `smali_fs_service.py` anpassen, um den Full-File-Read zu ermöglichen.
3. `load_method` im Controller so umbauen, dass abhängig vom Toggle-Status entweder der Methoden-Schnipsel oder die komplette Datei in das `txt_orig` Feld geladen wird.

---

## Phase 3: Hierarchisches Full-File-Patching

### Anforderungen

* Wenn im Editor die "Ganze Datei" bearbeitet und gespeichert wird, entsteht ein "Full-File-Patch".
* **Präzedenz-Regel:** In der Build-Pipeline wird zuerst ein vorhandener Full-File-Patch auf die Datei angewendet. Erst danach werden (falls vorhanden) isolierte Methoden-/Struktur-Patches auf dieselbe Datei angewendet.
* **Smart Append:** Wenn ein isolierter Methoden-Patch auf eine Datei angewendet werden soll, die Methode aber nach dem Full-File-Patch nicht mehr existiert (oder nie existierte), wird der Methoden-Patch intelligent am Ende der Datei angehängt.

### Konzept & Architektur

* **Patch-Modellierung:** Das Patch-Dictionary im `SessionState` erhält ein neues Feld `"scope": "file" | "method"`.
* **Pipeline-Erweiterung (`patch_steps.py` / `SmartPatchStep`):**
* Schritt 1: Gruppiere alle Patches nach Datei.
* Schritt 2: Wende für jede Datei zuerst den Patch mit `"scope": "file"` an (falls existent).
* Schritt 3: Wende alle Patches mit `"scope": "method"` an.


* **Missing Method Fallback (`patch_service.py`):** Schlägt der reguläre und der heuristische Match für eine Methode fehl, wird geprüft, ob es sich um eine Neuanlage handelt. Der Code wird dann validiert und vor das EOF (End of File) injiziert.

### Umsetzungsplan

1. `SmaliStudioController.add_smali_patch` anpassen, um den `"scope"` basierend auf dem aktiven Editor-Modus zu setzen.
2. `SmartPatchStep.execute` so refaktorieren, dass Patches pro Datei gruppiert und hierarchisch verarbeitet werden.
3. `PatchService.evaluate_smali_patch` erweitern, um das "Append"-Szenario für fehlende Methoden als gültigen Rückgabewert (`"type": "append"`) zuzulassen.

---

## Phase 4: Workspace-interner Package-Picker Dialog

### Anforderungen

* Bei Klick auf "Neue Struktur" soll ein Dialog erscheinen, der die bestehende Package-Struktur der App als Baum (Treeview) darstellt.
* Auswahl eines Ziel-Packages durch einfachen Klick.
* Ein Textfeld am unteren Rand für den neuen Dateinamen (z.B. `MyNewClass.smali`).
* Automatische Zusammensetzung des relativen Zielpfades.

### Konzept & Architektur

* **Neuer Dialog (`ui/dialogs/package_picker_dialog.py`):** Entkoppelt von System-Dateidialogen.
* **Datenquelle:** Die Pfade werden blitzschnell aus dem bereits existierenden `self.search_engine.ram_cache` extrahiert. Alle Dateinamen werden weggeworfen, die reinen Verzeichnispfade werden dedupliziert und in einen hierarchischen Baum übersetzt.
* **Integration:** Ersetzt den Aufruf von `filedialog.asksaveasfilename` in `struct_dialog.py`.

### Umsetzungsplan

1. Neue Datei `package_picker_dialog.py` erstellen, Treeview-Logik für Pfad-Visualisierung implementieren.
2. `struct_dialog.py` anpassen, sodass der neue Picker den alten Dateidialog ersetzt und den generierten relativen Pfad direkt in das Eingabefeld (`self.ent_path`) übernimmt.

---
