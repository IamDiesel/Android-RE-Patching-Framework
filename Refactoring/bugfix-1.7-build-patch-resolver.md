# Bugfix-Spec 1.7 — Build-Zeit-Patch-Resolver (zwei Bugs)

**Projekt:** Android RE Patching Framework · **Backlog-Ref:** 1.7 (+ berührt 1.4, 1.6) · **Stand:** 2026-10-21
**Auslöser:** Group-A-Rebuild auf LLU 5.2.0 (4 Lifecycle-Stubs, vorab im Fuzzy-Matcher „Favorit repariert").
**Status:** Spec/Fixplan — noch nicht implementiert. Arbeitsregel: **Git liegt beim Nutzer**, keine git-Aktionen durch die KI.

---

## 1. Beobachtetes Verhalten

**Bug 1 — reparierte Patches kollidieren im Build erneut.**
Alle 4 Group-A-Patches wurden vor dem Build per „Übernehmen & Favorit reparieren" auf den 5.2.0-Body geheilt. Im Pipeline-Schritt `Apply Smali Patches` (`SmartPatchStep`) warf der Build für **jeden** Patch erneut einen Konflikt — obwohl der Favorit sichtbar den aktuellen Body trug.

**Bug 2 — Build bricht nach erfolgreichem Fix ab statt fortzusetzen.**
Der im Build interaktiv angewandte Fuzzy-Fix wird korrekt übernommen, aber die Pipeline **setzt nicht fort** — sie **endet**. Pro Patch ein kompletter Build-Neustart (4× Fix → 4× Neustart, inkl. erneutem Mirror/Decompile). Der Abbruch ist in der Konsole **nicht deutlich sichtbar** (letzte Patch-Zeile bleibt stehen) → wirkt wie ein Hänger, ist aber ein beendeter Lauf.

---

## 2. Auswirkung

- **Unbrauchbarer Rebuild-Loop bei App-Updates:** Nach jedem DexGuard-Update (obfuskierte Rümpfe ändern sich) ist das Nachziehen der Patches unnötig teuer — N Konflikte = N Vollbuilds.
- **Vertrauensverlust in die Reparatur:** „Favorit reparieren" scheint wirkungslos, weil der Build den reparierten `orig` nicht annimmt.
- **Fehldiagnose „Hänger":** die unklare Statusanzeige kostet zusätzlich Zeit (Warten auf einen bereits beendeten Build).
- Betrifft ausschließlich **Method-Scope-Smali-Patches** (Full-File, new_file, Hex, Lib-Replace sind nicht betroffen).

---

## 3. Analyse

### 3.1 Bug 1 — zwei `orig`-Quellen **und** ein zu schwacher Normalisierer

Der Konflikt entsteht in `core/pipeline/steps/patch_steps.py` (`SmartPatchStep.execute`): findet der exakte Vergleich `orig_block in content` (gegen die **Destination**-Datei) nichts, wird die Methode per Signatur gesucht und
`FuzzingEngine._normalize_smali(orig_block) == _normalize_smali(actual_block)` geprüft. Scheitert das → `raise PatchConflictException`.

Zwei Ursachen überlagern sich:

1. **Der Normalisierer ist zu schwach.** `core/fuzzing_engine.py::_normalize_smali` entfernt nur `.line`, Register (`[vp]\d+ → REG`) und Kommentare. Er kanonisiert **keine Labels** (`:cond_0`, `:goto_1`, `:sswitch_*`), kein `.locals/.registers/.prologue`, keine `packed-switch/array-data`-Blöcke. Zwei Baksmali-Renderings **derselben** Methode (Source-Tool vs. Destination-Tool bzw. zwei Läufe) unterscheiden sich fast immer in der **Label-Nummerierung** → `norm_orig != norm_actual` → **falscher Konflikt trotz identischer Methode**.

2. **Resolver und Build normalisieren unterschiedlich.** Der Favoriten-Resolver (`services/patch_service.py::evaluate_smali_patch` → `clean_smali_for_match`) **behält Register** und kennt nur `.line`/Kommentare. Der Build (`_normalize_smali`) **abstrahiert Register**. → Ein Patch kann im Favoriten-Dialog „strukturell OK" sein und im Build trotzdem kollidieren. (Backlog-Ziel: **eine** `orig`-Quelle/Normalisierung für Resolver **und** Build.)

3. **Quelle der Kandidaten ist Source/Stale, nicht Destination.** Der Fuzzy-Matcher (`ui/controllers/fuzzy_match_controller.py`) zieht Kandidaten aus `search_engine.ram_cache`. Dieser wird in `services/smali_search_service.py::build_ram_index` aus `APP_SOURCE_DIR` gelesen **und** lädt bei Vorhandensein unbedingt eine gecachte Pickle (`.{pkg}_index.pkl` in DEST), die über App-Versionen **stale** sein kann. „Favorit reparieren" schreibt damit einen Source-/Stale-Body als `orig`, während der Build gegen den **Destination-Baksmali-Body** vergleicht.

**Fazit Bug 1:** Der reparierte `orig` stammt aus einem anderen Baum/Rendering als der, gegen den der Build patcht; der Normalisierer ist zu schwach, um die reine Formatierungs-/Label-Drift zu schlucken.

### 3.2 Bug 2 — Konflikt = Exception = Pipeline-Ende

`SmartPatchStep` wirft beim **ersten** konfliktbehafteten Patch sofort `PatchConflictException`. `core/pipeline/engine.py::run_pipeline` fängt sie, publiziert `PIPELINE_CONFLICT_DETECTED` und macht `return False` → **die gesamte Pipeline endet**. `ui/tabs/workspace_tab.py::handle_patch_conflict` öffnet den Fuzzy-Dialog; nach dem Fix gibt es **keinen Resume** — der Nutzer muss den Build neu starten, der wieder bei `mirror_workspace` beginnt und beim **nächsten** Konflikt erneut abbricht. → N Konflikte = N Vollbuilds. Zusätzlich fehlt eine klare End-/Abbruch-Statuszeile.

### 3.3 Angrenzende Nebenbefunde (nicht Kern, gleiche Codestelle)
- **Smart-Append-Falle:** wird die Methode wegen Umbenennung nicht gefunden, hängt der Step sie stumm doppelt ans Dateiende (toter Code).
- **`_workspace_index`-Leak:** `handle_patch_conflict` schreibt `patch_data["_workspace_index"]` in das **lebende** Patch-Dict → wird über `active_session_service`/Favoriten mitserialisiert.
- **Idempotenz:** ein bereits angewandter Patch würde ohne Re-Mirror als Konflikt erkannt (aktuell ok, da BUILD_NATIVE immer mit `mirror_workspace` startet).

---

## 4. Systemänderung / Bugfix

### 4.1 Bug 1 — gemeinsamer, stärkerer Normalisierer (Wurzel) + gegated `actual_block`-Fallback

1. **Ein einziger `normalize()`** in `services/patch_service.py`, genutzt von Resolver **und** `SmartPatchStep` **und** `FuzzingEngine`. Er vereinheitlicht: `.line/.prologue` entfernen, Register `[vp]\d+ → REG`, Kommentare weg, Leerzeilen zusammenfalten **und zusätzlich Labels kanonisieren** (`:<name>_<n>` → laufendes `:L<k>` in Reihenfolge des Auftretens; Sprungziele konsistent mitnennen). `FuzzingEngine._normalize_smali` und `clean_smali_for_match` rufen künftig diesen einen Normalisierer auf (keine zweite Logik mehr).
   - **Wirkung:** reine Formatierungs-/Label-Drift heilt im Build **automatisch** (Auto-Fix-Zweig greift), **ohne Dialog** — der Großteil der „reparierter Patch matcht nicht"-Fälle verschwindet.

2. **`actual_block`-Fallback für echte Body-Änderungen, gegated.** Bleibt eine Abweichung nach Normalisierung, trägt die `PatchConflictException` bereits `actual_block` (den exakten Destination-Body). Repariert wird `orig = actual_block` (Edit bleibt) → Re-Apply ist exakter Treffer.
   - **Gate (wichtig, Korrektheit):** Auto-Übernahme **nur**, wenn das `edit` eine **vollständige Methode derselben Signatur** ist (Full-Body-Replace, z. B. Lifecycle-Stub). Bei **chirurgischen** Patches (Edit = modifizierte Kopie des alten Body) **kein** Auto-Accept → manueller Review im Fuzzy-Matcher erzwingen, sonst würde das alte Edit die neuen Body-Änderungen stumm überschreiben.

3. **Resolver-Quelle = Destination im Build-Konflikt.** Im Build-Konflikt-Modus speist der Fuzzy-Matcher Kandidaten aus dem **Destination**-Baum (bzw. direkt `pce.actual_block`), nicht aus dem Source-/Stale-Cache. Zusätzlich **Index-Invalidierung**: `build_ram_index` verwirft/erneuert `.{pkg}_index.pkl` bei geänderter Quelle (mtime-/Versions-Check) statt unbedingt zu laden.

### 4.2 Bug 2 — Konflikte sammeln + ein Rebuild (Option A)

1. **`SmartPatchStep` sammelt alle Konflikte in einem Durchlauf**, statt beim ersten zu werfen: konfliktbehaftete Patches überspringen, übrige anwenden, am Ende **eine** aggregierte `PatchConflictException` (Liste) werfen.
2. **`PatchConflictException`** (`core/domain/exceptions.py`) trägt eine **Liste** von Konflikten; `engine.py` und `handle_patch_conflict` konsumieren diese (Logging/Dialoge).
3. **UI arbeitet die Konflikte sequenziell ab** (Dialog schließt → nächster), danach **ein** Rebuild, der dank 4.1 sauber durchläuft. → aus N Neustarts wird **1**.
4. **Klare Statuszeile** (Bug 2c): eindeutige „Build angehalten — X Konflikte zu lösen"- bzw. „Build beendet"-Meldung in der Konsole, kein stiller Stop.

> *Option B (voller in-place Resume ohne Rebuild)* wird bewusst zurückgestellt: setzt voraus, dass die Pipeline im Worker-Thread läuft (das `self.app.after(0,…)`-Muster deutet darauf hin, **muss aber verifiziert werden**), braucht einen Re-Attempt-Loop + Button-Sperren und ist für ein Live-Werkzeug riskanter. Kandidat für einen Folgeschritt.

### 4.3 Hygiene (mit erledigen)
- `_workspace_index` nach Gebrauch aus dem Patch-Dict `pop`en (kein Leak in `active_session.json`/Favoriten).
- `return False`-Pfade (Source fehlt, keine `.method`-Signatur) bleiben **harte** Fehler (nicht sammeln) — explizit kommentiert.

---

## 5. Vorgehen / Fixplan (inkrementell, je Schritt baubar/testbar)

| # | Schritt | Dateien | Gate vor dem nächsten Schritt |
|---|---|---|---|
| **S1** | **Gemeinsamer `normalize()` + Label-Kanonisierung.** In `PatchService` zentralisieren; `_normalize_smali` und `clean_smali_for_match` darauf umstellen. | `services/patch_service.py`, `core/fuzzing_engine.py` | Nutzer baut 1× auf 5.2.0 → prüfen, wie viele der 4 Konflikte jetzt **dialogfrei** auto-heilen |
| **S2** | **Offline-Unit-Test** für den Normalisierer/Resolver (synthetisches Smali: nur Label/Register-Drift → Auto-Heal; echte Body-Änderung → Konflikt; `actual_block`-Repair → Apply). | `tests/` | Tests grün |
| **S3** | **`actual_block`-Fallback + Gate** (Full-Body-Replace erkennen) im Konflikt-/Repair-Pfad; Resolver-Quelle = Destination; Index-Invalidierung. | `ui/tabs/workspace_tab.py`, `ui/controllers/fuzzy_match_controller.py`, `services/smali_search_service.py` | echter 5.2.0-Konflikt reparierbar in 1 Durchgang |
| **S4** | **Bug 2 Option A:** Konflikte sammeln + aggregierte Exception + sequentielle UI + Statuszeile. | `core/pipeline/steps/patch_steps.py`, `core/pipeline/engine.py`, `core/domain/exceptions.py`, `ui/tabs/workspace_tab.py` | voller Group-A-Rebuild: **1** Rebuild, kein 4×-Neustart |
| **S5** | **Hygiene:** `_workspace_index`-Cleanup, Kommentare an den `return False`-Pfaden. | `ui/tabs/workspace_tab.py`, `core/pipeline/steps/patch_steps.py` | — |

**Reihenfolge-Logik:** S1 ist der kleinste, wirksamste und isoliert testbare Schritt (heilt die Mehrzahl der Konflikte an der Wurzel). Erst nach deinem Build/Test an S1 gehen wir an S3/S4. Paradigmentreu: Services/Steps/EventBus-Struktur bleibt, nur Logik wird inkrementell ergänzt.

---

## 6. Seiteneffekte & Risiken

- **S1 (Normalisierer):** stärkere Normalisierung kann theoretisch **zwei wirklich verschiedene** Methoden als „gleich" werten (Label-Abstraktion verliert Information). Mitigiert durch Beibehaltung von Opcodes/Nicht-Register-Operanden/Strings; Risiko gering, aber im Test mit einem Negativfall absichern.
- **`actual_block` ohne Gate:** stille Überschreibung der neuen Body-Änderungen bei chirurgischen Patches → **nur gegated** ausliefern (4.1 Punkt 2).
- **Option A, partielle Writes:** übersprungene Konflikte lassen Dateien teilgepatcht → unkritisch, **solange** die Pipeline mit `mirror_workspace` frisch startet (gilt für BUILD_NATIVE). Bei künftigem „Resume ab Schritt" neu bewerten.
- **Aggregierte Exception:** alle drei Consumer (raise/catch/handle) müssen die Liste konsistent behandeln — vollständig erfasst (keine weiteren Nutzer von `PatchConflictException`).

## 7. Offene Entscheidungen / Verifikation
1. **Thread-Modell der Pipeline** verifizieren (für ein späteres Option B).
2. **Label-Kanonisierung** auch auf `packed-switch`/`array-data`-Datenblöcke ausdehnen? (Rest-Fehltreffer.)
3. **Smart-Append-Falle** (Umbenennung → toter Code) als eigenes Backlog-Item (1.6-nah) führen, nicht in 1.7 mischen.

---

## 8. Definition of Done
- Voller Group-A-Rebuild auf einer frisch aktualisierten App: Formatierungs-/Label-Drift heilt dialogfrei; echte Konflikte werden **gesammelt**, in einem Durchgang gelöst, **ein** Rebuild läuft sauber durch.
- Resolver und Build nutzen **denselben** Normalisierer.
- Unit-Test grün; kein `_workspace_index` in gespeicherten Patches; klare Build-Statusanzeige.
