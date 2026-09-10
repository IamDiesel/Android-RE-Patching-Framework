# Feature-Dokumentation: Call Graph Visualisierung (Graphviz)

Dieses Dokument beschreibt die Konzeption und Implementierung des visuellen Call Graph Exports im Android RE Patching Framework.

---

## 1. Anforderungsanalyse

### 1.1 Problemstellung
Die aktuelle Darstellung des Call Graphs im `SmaliStudioTab` erfolgt über einen hierarchischen Tkinter-Treeview. Bei tiefen Aufrufketten und großen Smali-Projekten wird diese Baumstruktur schnell unübersichtlich. Nutzer verlieren bei der Navigation durch verschachtelte Nodes (`caller`/`callee`) den Überblick über den globalen Ausführungsfluss und die Zusammenhänge der Methoden. Das reine Kopieren von Treeview-Ebenen reicht für eine komplexe Architekturanalyse nicht aus.

### 1.2 Zielsetzung
Integration einer Funktion, die den im RAM gehaltenen Call Graph auf Knopfdruck in eine grafische, zweidimensionale Netzwerkstruktur überführt. Die Darstellung muss den Ausführungsfluss von oben nach unten (Top-Down) visualisieren und sofort interaktiv nutzbar sein.

### 1.3 Funktionale Anforderungen
*   **Export-Generierung:** Übersetzung des internen `CallGraphManager`-Zustands in ein standardisiertes Graphen-Format.
*   **Visuelle Unterscheidung:** Optische Hervorhebung von eigenem App-Code gegenüber System-APIs (z. B. farbliche Trennung).
*   **Vektor-Ausgabe:** Rendering als skalierbare `.svg`-Datei, um Text-Suche (STRG+F im Browser) und verlustfreien Zoom zu ermöglichen.
*   **Auto-Open:** Automatisches Öffnen der generierten Grafik im Standard-Browser des Betriebssystems.
*   **Fallback-Logik:** Sicheres Abfangen fehlender System-Abhängigkeiten mit entsprechender Nutzerführung.

---

## 2. Konzept

Als Rendering-Engine wird **Graphviz** (speziell die `dot`-Engine) gewählt. Es ist der weltweite Industrie-Standard für das Layouten gerichteter (directed) und hierarchischer Graphen im Reverse Engineering. 

*   **Entkoppelte Generierung:** Das Framework zeichnet den Graphen nicht selbst, sondern generiert lediglich eine `.dot`-Syntax-Datei, die Kanten (Edges) und Knoten (Nodes) beschreibt.
*   **Subprozess-Rendering:** Die fertige `.dot`-Datei wird über den bestehenden `CommandRunner` an die lokale Graphviz-Installation übergeben und in ein `.svg` übersetzt.
*   **Performance:** Durch die Auslagerung an ein C-basiertes CLI-Tool bleibt das Python-Framework blockierungsfrei und speichereffizient, selbst bei tausenden Methoden-Knoten.

---

## 3. Architekturplan

Das Feature wird nahtlos in die bestehende MVC/SOA-Architektur eingegliedert:

### 3.1 View-Layer (`ui/tabs/smali_studio_tab.py`)
*   Erweiterung der Callgraph-Toolbar (`cg_toolbar_top`) um einen nativen Tkinter-Button (`🕸️ Export`).
*   Der Button triggert zustandslos den Controller.

### 3.2 Controller-Layer (`ui/controllers/smali_studio_controller.py`)
*   Prüft, ob der `CallGraphManager` (`self.app.cg`) Daten enthält.
*   Bestimmt das Output-Verzeichnis (Workspace-Archivordner).
*   Delegiert die Daten an den neuen Service.
*   Verarbeitet die Antwort: Öffnet das `.svg` über OS-spezifische Befehle (`os.startfile`, `subprocess.Popen(['open', ...])`) oder zeigt eine Fehlermeldung bei fehlendem System-PATH.

### 3.3 Service-Layer (`services/graphviz_service.py`) [NEU]
*   Erhält die Referenz auf den `CallGraphManager`.
*   Iteriert über `nodes` und `callees`.
*   Filtert Signaturen für eine saubere Darstellung (Entfernung von Parameterlisten im Display-Namen).
*   Nutzt `is_system_api` aus dem `callgraph_service.py`, um Knoten farblich zu markieren (z. B. Grau für System, Hellblau für App-Code).
*   Generiert die `.dot`-Datei und feuert den Kommandozeilenbefehl `dot -Tsvg`.

---

## 4. Umsetzungsplan

### Schritt 1: Service Implementierung
*   Anlegen der Datei `services/graphviz_service.py`.
*   Programmierung der Methode `export_to_svg(cg_manager, output_dir)`, welche die `.dot`-Syntax (digraph, nodes, edges) als String aufbaut.
*   Integration des `CommandRunner.run_blocking('dot -Tsvg ...')`.

### Schritt 2: UI-Anpassung
*   Modifikation in `smali_studio_tab.py` innerhalb der Methode `create_widgets()`.
*   Platzierung des `Export`-Buttons zwischen "Explore" und "Clear".

### Schritt 3: Controller-Logik
*   Erstellen der Methode `export_callgraph()` in `smali_studio_controller.py`.
*   Implementierung der OS-Routen (Windows, MacOS, Linux) zum Starten der Standardanwendung für `.svg`.
*   Implementierung der Error-Handling-Dialoge (Hinweis auf Graphviz-Installation).

### Schritt 4: Deployment & Abhängigkeiten
*   Aktualisierung der Framework-Dokumentation: Nutzer müssen `Graphviz` installieren und zum System-PATH hinzufügen. (Ein automatischer Download wie bei Apktool ist hier nicht empfehlenswert, da Graphviz eine tiefe OS-Integration benötigt).