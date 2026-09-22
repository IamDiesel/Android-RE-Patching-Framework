"""
mcp_server — MCP-Anbindung des RE-Patch-Frameworks.

Zweite „View" neben der Tkinter-GUI: stellt ausgewaehlte Framework-Faehigkeiten
als MCP-Tools bereit, ohne core/ oder services/ zu veraendern. Betriebsmodell:
„Vorbereiten & Beobachten" ist frei, Bauen/Flashen/Starten/Dateizugriff/Loeschen
sind genehmigungspflichtig (siehe mcp-integration-konzept.md).

Schritt 1 liefert die Registry (Metadaten) + einen Platzhalter-Server fuer die
Lebenszyklus-Steuerung aus der GUI. Der echte FastMCP-Server folgt in Schritt 2.
"""
