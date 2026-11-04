# Bugfix-Spec 1.8 — „Active Session": fehlende Single Source of Truth

**Projekt:** Android RE Patching Framework · **Backlog-Ref:** 1.8 (neu) · berührt 1.1 (MCP↔GUI-Sync), 6.2/6.3 (Hygiene/Gruppe-C) · **Stand:** 2026-10-21
**Auslöser:** Beim GUI-Start sind Patches „aktiv", die der Nutzer nie geladen hat — und die **nicht** vom letzten Start stammen; manuell gelöschte Patches sind nach dem Neustart wieder da.
**Status:** T1, T2, T4 **umgesetzt** (2026-10-21), T3 optional/zurueckgestellt — Details in §10. Arbeitsregel: **Git liegt beim Nutzer**, keine git-Aktionen durch die KI.

---

## 1. Beobachtetes Verhalten

1. **Geister-Patches beim Start.** Direkt nach dem Workspace-Load sind ~10 Smali-Patches „aktiv", obwohl keiner geladen wurde — ein **Mix aus zwei Favoriten-Sätzen**: Group-A-RASP-Stubs (`MainApplication`/`ReactActivity`/`MainActivity`) **und** Key-Export/Ghost-Logger-Patches (`MediatorImpl`, `SecureDataStoreModule`, `NativeMediatorBridgeManager`).
2. **~~Dubletten~~ — Korrektur (2026-10-21):** Die ~10 Einträge sind **keine** Duplikate, sondern **verschiedene Methoden pro Datei** (z. B. `ReactActivity.onStart` **und** `.attachBaseContext`; `MediatorImpl.request…` **und** `.publish`; `SecureDataStoreModule.associatedDataForItem` **und** `.decryptKeysetWithKeystore`) — legitim distinkt. Ein echtes Dubletten-Problem droht nur **latent** bei Versions-Drift *derselben* (Datei, Methode); siehe §3.4 (T3 = Vorsorge).
3. **Löschen hält nicht.** Im letzten Lauf manuell aus der aktiven Liste entfernte Patches sind beim nächsten Start **wieder vorhanden**.

---

## 2. Auswirkung

- **Verwirrung / kein verlässlicher Startzustand:** die „aktive Liste" spiegelt eine über Sessions gewachsene Vereinigung, nicht die Absicht des Nutzers.
- **⚠ Sicherheits-/Footprint-Risiko (das eigentlich Kritische):** unter den Altlasten sind **Gruppe-C-Key-Export-Patches** (`SecureDataStoreModule.decryptKeysetWithKeystore` u. a.). Die laufen bei jedem Build **stillschweigend mit** und schreiben Schlüsselmaterial auf die Platte — genau das, was Backlog 6.3 ausdrücklich vermeiden will. Ein unbeabsichtigter Key-Export ist schwerwiegender als die reine Verwirrung.
- **Verlorene Arbeit:** GUI-Löschungen/-Edits an der aktiven Liste gehen bei Neustart **und** bei jedem MCP-getriggerten Poll-Refresh verloren.

---

## 3. Analyse

### 3.1 Prozessmodell (die Wurzel)
MCP läuft als **eigener Prozess** (`services/mcp_server_service.py` → `subprocess.Popen`, stdio). GUI-Prozess und MCP-Prozess halten **je eine eigene** `SessionState` (Klassen-Attribute, nicht geteilt). Die **einzige** Brücke ist die Datei `data/active_session.json`.

### 3.2 Asymmetrische Schreibhoheit — die fehlende Single Truth
- **MCP schreibt** nach jedem patch-ändernden Tool via `mcp_server/tools.py::_persist_active` → `ActiveSessionService.save(SessionState.to_patch_list())` (atomar + Drei-Wege-Merge).
- **MCP liest** in `mcp_server/runtime.py` (einmalig beim ersten Tool-Call + `reload_if_changed`).
- **Die GUI liest nur.** `gui.py::_init_active_session_reflection` lädt die Datei beim Start und spiegelt sie (`_reflect_active_session` → `workspace_tab.load_patches_from_record(append=False)` → **ersetzt** die aktive Liste), plus 1,5-s-Poll (`_poll_active_session` → `reload_if_changed` → erneutes Spiegeln). **Es gibt keinen einzigen `active_session_svc.save()`-Aufruf im GUI-Prozess.**
- `ui/controllers/smali_studio_controller.py::remove_smali_patch` macht nur `del self.smali_patches[idx]` — und `smali_patches` ist bloß ein Property auf `SessionState.active_smali_patches` (RAM). **Keine Persistenz.**

→ **Zwei Wahrheiten:** GUI-RAM vs. Datei. Die GUI ist ein **reiner Read-/Reflect-Client**; ihre Mutationen (Add/Delete/Apply) landen nie in der Datei. Deshalb „überlebt" eine GUI-Löschung den Neustart nicht (die Datei, zuletzt von MCP geschrieben, wird unverändert neu gespiegelt), und ein MCP-Schreibvorgang kann GUI-RAM-Edits sogar **mitten in der Session** per Poll-Refresh überschreiben.

### 3.3 Warum die Datei nur wächst (Akkumulation)
`ActiveSessionService.save()` nutzt `three_way_merge(disk, baseline, mine)`. Eine **leere** lokale Liste löscht **nichts**, weil Disk-Einträge, die nicht in der Baseline stehen, als „vom anderen Prozess hinzugefügt" gewertet und **behalten** werden. Es gibt zudem **kein Clear** bei einer frischen Session. → Die Datei ist faktisch **append-only** über Sessions/Favoriten hinweg (Group-A + alter Vault-/Key-Export-Favorit aus einer früheren MCP-Session).

### 3.4 Dubletten — nur latent (Korrektur)
Die aktuell beobachteten 10 Einträge sind **distinkte (Datei, Methode)-Patches**, keine Duplikate. **Latent** droht aber eine Dublette *derselben* (Datei, Methode): `_patch_key` enthält `(orig or '')[:64]`; ändert sich über einen App-Versionssprung der **obfuskierte `orig`**, bekommt derselbe logische Patch einen **neuen Key** → Alt- und Neu-Eintrag könnten beide liegenbleiben. Das ist **Vorsorge** (T3), kein aktueller Fehler.

---

## 4. Systemänderung / Bugfix

**Leitidee:** `active_session.json` wird die **eine** Single Source of Truth, und die **GUI wird vollwertiger Read-/Write-Client** (nicht nur Spiegel). Löschungen werden zu echten Deltas, die der vorhandene Merge honoriert.

1. **GUI schreibt zurück.** Nach jeder aktiven-Listen-Mutation (`add_smali_patch`, `remove_smali_patch`, Favorit anwenden, Konflikt-Repair) ruft die GUI `active_session_svc.save(SessionState.to_patch_list())` — denselben konfliktsicheren Pfad wie MCP. Voraussetzung: die GUI-Instanz hält eine **korrekte Baseline** (wird bei `load()`/`save()` gesetzt), damit ein entfernter Key im `three_way_merge` als **Löschung** gilt und propagiert.
2. **Echtes Leeren.** Button **„Aktive Session leeren"** → `SessionState.clear_session()` + ein `save([])`, das den Merge **umgeht** (direkter atomarer Write von `{"patches": []}`), da der Merge bewusst nicht löscht.
3. **Stabiler Dedup-Key.** `_patch_key` ohne `orig[:64]` — stattdessen `type|file|scope|method-signature` (Signatur aus der ersten `.method`-Zeile). Zusätzlich **Dedup beim Laden/Spiegeln**. → keine Versions-Dubletten mehr.
4. **Start-Reconciliation statt stiller Übernahme.** Ist die Datei beim Start nicht leer, **einmalige, nicht-blockierende Meldung** „N Patches aus vorheriger Session übernommen — [Leeren]" (kein Fokus-Klau). Optional als Setting: „Aktive Session bei Start leeren".
5. **Gruppe-C-Schutz (Hygiene, koppelt an 6.3):** beim Spiegeln Key-Export-Patches (bekannte Zielklassen/Favoriten-Tag) **markieren/warnen**, damit sie nicht unbemerkt in einen Build geraten.

---

## 5. Vorgehen / Fixplan (inkrementell, je Schritt testbar)

| # | Schritt | Dateien | Gate |
|---|---|---|---|
| **T1** | **GUI-Write-Back**: `save()` nach Add/Remove/Apply/Repair; Baseline korrekt halten. | `ui/controllers/smali_studio_controller.py`, `ui/tabs/workspace_tab.py`, `gui.py` | GUI-Löschung überlebt Neustart |
| **T2** | **„Aktive Session leeren"**-Button + merge-umgehendes `clear()` im Service. | `services/active_session_service.py`, `ui/tabs/workspace_tab.py` | Button leert Datei wirklich |
| **T3** | **Stabiler `_patch_key`** + Dedup beim Laden/Spiegeln; einmalige Migration/Dedup der bestehenden Datei. | `services/active_session_service.py`, `ui/tabs/workspace_tab.py` | keine Dubletten mehr |
| **T4** | **Start-Reconciliation** (nicht-blockierende Meldung + optionales „bei Start leeren"). | `gui.py`, Settings | Start zeigt klaren Zustand |
| **T5** | **Offline-Tests**: three-way-merge-Delete (Löschung propagiert), Dedup-Key, clear-umgeht-Merge. | `tests/` | grün |

**Reihenfolge-Logik:** T1 behebt den Kernschmerz (Löschen hält), T2/T3 räumen auf, T4 macht den Start transparent. T5 sichert die Merge-/Clear-Semantik dauerhaft ab (die ist subtil und darf nicht erneut „anti-clear" werden).

---

## 6. Seiteneffekte & Risiken

- **GUI-Write-Back + MCP gleichzeitig:** beide nutzen denselben `three_way_merge`. Löscht die GUI einen Key, den MCP zeitgleich neu hinzufügt → Merge-Konflikt am Key; „eigene Version gewinnt, fremde evtl. überschrieben" wird bereits geloggt. Akzeptabel, aber im Log sichtbar machen.
- **Poll-Refresh stomp:** der 1,5-s-Poll ersetzt die Liste (`append=False`). Schreibt die GUI selbst, aktualisiert sie die mtime → der nächste Poll spiegelt den **eigenen** Stand (harmlos). Ein echter MCP-Schreibvorgang während einer GUI-Bearbeitung spiegelt weiterhin — das ist die gewollte Live-Reflektion; die Deltas bleiben durch den Merge erhalten.
- **`_patch_key`-Änderung:** ändert die Merge-Identität bestehender Einträge → **einmalige Migration/Dedup** der vorhandenen Datei nötig, sonst kurzzeitig „alles neu" beim ersten Lauf.
- **Clear-umgeht-Merge:** bewusst, sonst lässt sich die Liste nie leeren. Risiko: ein zeitgleicher MCP-Write könnte nach dem Clear sofort wieder schreiben → Clear sollte `SessionState` **und** Datei in einem Zug setzen und der Poll den leeren Stand spiegeln.
- **Merge-Semantik-Test (T5) ist Pflicht:** Die Löschlogik ist die subtilste Stelle; ohne Test droht ein Rückfall in „Delete propagiert nicht".

---

## 7. Designfragen (deine Punkte) — Einordnung

**„Sollte es nicht eine Single Truth geben?"** — Ja. Soll = `active_session.json`. Ist-Zustand = zwei Wahrheiten (GUI-RAM vs. Datei), weil die GUI nur liest. Der Fix macht die GUI zum vollwertigen Read-/Write-Client → eine Wahrheit, und Löschungen propagieren.

**„Braucht MCP einen Zwischenpuffer?"** — Nein, der **Drei-Wege-Merge ist bereits der Puffer/Reconciler**. Was fehlt, ist die **Lösch-Absicht von der GUI-Seite**: mit korrekter Baseline ist ein entfernter Key im Merge eine echte Löschung. Kein neuer Puffer, nur GUI-Write-Back + Baseline-Disziplin (ggf. Tombstones, falls man Löschungen über konkurrierende Re-Adds hinweg hart durchsetzen will).

**„Beim Neustart Session beenden & leeren?"** — Zwei Modelle:
- **M1 (empfohlen): persistente SoT.** Datei überlebt Neustarts, GUI schreibt zurück, **expliziter Leeren-Button** + Start-Reconciliation. Erhält den MCP↔GUI-Handoff, beseitigt Akkumulation durch echte Löschungen statt Zwangs-Clear.
- **M2: ephemere Session.** Beim GUI-Start leeren. Einfach, killt Akkumulation — schwächt aber den (gelegentlich nützlichen) Cross-Prozess-Handoff. Nur als **optionales Setting**, nicht als Default.

**„Muss die KI nach einem Absturz Tokens aufwenden, um den Zustand wiederherzustellen?"** — Nein. `active_session.json` ist **nicht** das Gedächtnis der KI. Das belastbare Wissen liegt in **Favoriten + Docs**; eine aktive Liste wird durch **einen** deterministischen `patch.apply_favorite`-Aufruf wiederhergestellt — kein token-teures Re-Deriving. Der Crash-Recovery-Wert der Akkumulation ist also **gering**, die Kosten (stille Gruppe-C-Key-Exporte in Builds) sind real. Einzige Ausnahme: ein **laufender** headless-MCP-Lauf über einen GUI-Neustart hinweg — deshalb sollte ein „bei Start leeren" nicht bedingungslos sein (z. B. nur, wenn kürzlich kein MCP-Write erfolgte, oder nur per Button).

**Fazit der Designfrage:** **M1** (persistente SoT + GUI-Write-Back + expliziter Clear + Dedup) ist der robusteste Weg; ein Zwangs-Clear bei jedem Start ist weder nötig für die KI noch wünschenswert für den Handoff.

---

## 8. Sofort-Linderung (ohne Code, auf Nutzer-Wort)
`data/active_session.json` auf `{"patches": []}` setzen leert die aktive Liste sofort (Favoriten/History bleiben unberührt). Schreibzugriff auf Nutzer-Daten → nur auf ausdrückliche Freigabe.

## 9. Definition of Done
- Eine GUI-Löschung überlebt Neustart **und** MCP-Poll.
- „Aktive Session leeren" leert die Datei wirklich.
- Keine Versions-Dubletten; Start zeigt einen klaren, nachvollziehbaren Zustand.
- Gruppe-C-Key-Export-Patches geraten nicht mehr unbemerkt in einen Build.
- Merge-Delete-/Clear-Semantik durch Offline-Tests abgesichert.


---

## 10. Umsetzungsstand (2026-10-21)
- **Sofort-Linderung ✅** `data/active_session.json` geleert (Backup daneben).
- **T1 ✅ GUI-Write-Back** — `gui.py::persist_active_session()` + Aufrufe nach Remove/Add/Apply/Repair (`smali_studio_controller`, `favorite_patches_controller`, `fuzzy_match_controller`). GUI-Löschung überlebt Neustart (live bestätigt).
- **T2 ✅ „Aktive Session leeren"** — `ActiveSessionService.clear()` (merge-umgehend) + `WorkspaceController.clear_active_session()` + Button im „⭐ Patch Verwaltung"-Frame.
- **T4 ✅ Start-Reconciliation** — nicht-blockierende Konsolen-Meldung „N Patch(es) aus vorheriger Session übernommen" statt stiller Übernahme; optionales `CLEAR_ACTIVE_ON_START` (config.json) leert beim Start.
- **T3 ⏸ zurückgestellt (optional)** — stabiler `_patch_key`/Dedup ist **Vorsorge** gegen Versions-Drift-Dubletten derselben (Datei, Methode); **kein** aktueller Fehler.
- **Tests:** `tests/test_active_session.py` (4/4): delete-propagation, clear-with-baseline, cross-process-keep, clear-bypasses-merge.
- Backups: `*.bak_t1_*`, `*.bak_t2_*`, `*.bak_t4_*`, `active_session.json.bak_1_8_*`. **Kein git.**
