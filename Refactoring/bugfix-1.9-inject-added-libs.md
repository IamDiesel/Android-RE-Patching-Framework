# Bugfix 1.9 — Aktive LibForge-Library wird beim Build nicht angezogen

**Status:** Phase 1 + Phase 2 (P2-4/P2-3) umgesetzt, offline getestet 5/5. Live-Verifikation durch Nutzer (ein BUILD_NATIVE-Lauf) steht aus.
**Dateien geändert:** `core/pipeline/steps/apk_steps.py`, `core/pipeline/steps/lib_steps.py`, `core/pipeline/steps/patch_steps.py`, `services/native_lib_service.py`, `ui/controllers/lib_forge_controller.py`, `mcp_server/tools.py`
**Tests:** `tests/test_libforge_inject.py` (T1–T5)
**Backups:** `*.bak_19a_<ts>` (Batch A–D), `*.bak_19b_<ts>` (Batch E–F)

---

## 1. Beobachtetes Verhalten

In LibForge wurde gezielt eine **ältere** Library-Variante aktiv gesetzt (Trace **v5.8**, id `c6ed9e76`). Der `BUILD_NATIVE`-Lauf meldete zwar einen erfolgreichen Inject, im fertigen APK lag aber nachweislich eine **neuere** Variante (**v7.0**, `2471b1a8`). Die Aktivierung in der GUI war also wirkungslos.

## 2. Auswirkung

- Der Nutzer kann nicht deterministisch steuern, **welche** `.so` ins APK kommt. Eine bewusst gewählte (alte) Trace-Variante wird stillschweigend durch eine andere ersetzt.
- Für die Forschung fatal: Build-Ergebnisse sind nicht reproduzierbar/attributierbar — man weiß nicht, welcher Code tatsächlich im Gerät lief.

## 3. Analyse (Root Cause)

Forensik über sha256 + mtime (Beweis in `claude/framework-bug-inject-added-libs.md`):

| Ort | Variante | Größe | mtime |
|-----|----------|-------|-------|
| aktive LibForge-Lib (`get_active`) | v5.8 `c6ed9e76` | 27272 B | 2026-09-21 |
| Workspace `.../lib/arm64-v8a/libtrace.so` (nach Inject) | **v5.8** (korrekt!) | 27272 B | frisch |
| apktool-Cache `build/apk/lib/arm64-v8a/libtrace.so` | **v7.0** `2471b1a8` | 14040 B | 2026-10-20 |
| `base.apk` / `base-debugSigned.apk` | **v7.0** `2471b1a8` | — | — |

**Der Injector arbeitet korrekt** — er schreibt die aktive v5.8 in den Workspace. Die Ursache liegt **eine Ebene tiefer**:

1. **apktool-inkrementeller Build-Cache.** `apktool b` nutzt `<unpacked>/build/apk/` als Staging-Cache und vergleicht **mtime**: „Quelle neuer als Cache → neu packen, sonst Cache gewinnt". Der Cache enthielt noch die v7.0 aus einem früheren Build.
2. **`shutil.copy2` erhält die Quell-mtime.** Der Injector kopierte mit `copy2`; die geschriebene v5.8 bekam die **alte** mtime der Quelldatei (2026-09-21) — also **älter** als der v7.0-Cache (2026-10-20). apktool entschied: „Cache ist neuer" → packte v7.0.

**Zwei sekundäre Fehlerquellen** wurden bei der Code-Gegenprüfung (`lib_steps.py`, `patch_steps.py`, `apk_steps.py`, `native_lib_service.py`, `tools.py`) gefunden:

3. **MCP-Bypass der Exklusivität.** `mcp_server/tools.py::lib_update` setzte `lib.active = bool(active)` direkt und speicherte per `mgr.update()` — **ohne** `deactivate_same_name`. So konnten über MCP mehrere gleichnamige Libs gleichzeitig `active=True` sein; `get_active()` liefert dann mehrere, der Inject-Reihenfolge-Zufall entscheidet.
4. **Namens-Mehrdeutigkeit in `_find_lib`.** Bei Aufruf über den Namen (`lib.build("trace")`) gab `_find_lib` stur den **ersten** Treffer aus `get_all()` zurück — nicht den aktiven.
5. **Tote Schutz-Tautologie.** In `InjectAddedLibsStep` war der App-Lib-Schutz `if os.path.exists(dst) and so_name not in managed_names:` **immer False** (jede aktive Lib ist per Definition in `managed_names`), schützte also echte App-Libs gar nicht.
6. **Stilles Schlucken.** `get_active()` filtert aktive Libs ohne existierende `.so` kommentarlos heraus — der Nutzer erfährt nicht, dass „seine" aktive Lib gar nicht injiziert wurde.

## 4. Systemänderung / Bugfix (Phase 1)

### A — Zentrale Cache-Invalidierung (Wurzel, deckt alle Fälle)
`core/pipeline/steps/apk_steps.py::ManifestBuildStep.execute()` — **direkt vor** `strategy.build(engine_context)`:
```python
_cache_lib = os.path.join(engine_context.cfg.paths["DEST_DIR"],
                          engine_context.get_unpacked_dir_name(), "build", "apk", "lib")
if os.path.isdir(_cache_lib):
    import shutil as _sh
    _sh.rmtree(_cache_lib, ignore_errors=True)
    engine_context.log("[*] apktool-Lib-Cache invalidiert: build/apk/lib/ geleert (Fix 1.9).")
```
Bewusst **zentral** in `ManifestBuildStep` statt am Ende der Inject-Steps: so sind auch die **Early-Return-Fälle** (keine aktiven Libs; keine Custom-Libs) sowie **zurückgenommene `lib_replace`-Patches** abgedeckt. Nur `lib/` wird geleert — `classes*.dex` & Rest des Caches bleiben, der Build bleibt schnell. Für Strategien ohne apktool-Cache (`apkeditor`) greift `isdir`-Guard → No-Op.

### B — Frische mtime beim Inject
`InjectAddedLibsStep` und `InjectCustomLibsStep`: `shutil.copy2(...)` → `shutil.copyfile(...)` + `os.utime(dst, None)`. Die injizierte `.so` ist damit **immer jünger** als jeder Cache-Rest (doppelte Absicherung zu A).

### C — Zentrale, exklusive Aktivierung (GUI + MCP)
Neue Methode `services/native_lib_service.py::NativeLibManager.set_active(lib_id, active)`: deaktiviert beim Aktivieren alle gleichnamigen (`so_output_name`) Konkurrenten **in-memory** und speichert **genau einmal**. Durchgeleitet von:
- `ui/controllers/lib_forge_controller.py::toggle_active` (ersetzt `deactivate_same_name`+`toggle_active`, nur noch 1× `save`)
- `mcp_server/tools.py::lib_update` (kein direktes `lib.active=` mehr; zusätzlich: **`active=True` wird verweigert, wenn die `.so` fehlt**).

### D — Keine stillen Drops + Transparenz
`InjectAddedLibsStep` warnt jetzt für jede aktive Lib **ohne** vorhandene `.so` (statt stillem Filter) und loggt beim Einspielen `id/version/Größe/sha256[:8]`.

### E — `_find_lib` bevorzugt die aktive Variante
`mcp_server/tools.py::_find_lib`: bei Namens-Mehrdeutigkeit wird die **aktive** Variante gewählt; id-Match bleibt exakt und vorrangig. Rückwärtskompatibel (eindeutiger Name/kein aktiver Treffer → altes Verhalten = erster Treffer).

### F — Echter App-Lib-Schutz statt Tautologie
`InjectAddedLibsStep`: Überschreib-Schutz **und** Cleanup-Schutz prüfen jetzt gegen das **Source-Original** `APP_SOURCE_DIR/<folder>/lib/<arch>/<so_name>`. Echte App-Libs werden nie überschrieben/gelöscht; für sie weist der Log auf `lib_replace` hin. Für die aktuellen LibForge-Libs (trace/hook — kein Source-Original) bleibt das Verhalten unverändert (werden injiziert).

## 5. Seiteneffekte (Tellerrand)

- **Build-Dauer:** nur `build/apk/lib/` wird verworfen → vernachlässigbar; der teure dex-Cache bleibt.
- **apkeditor-Strategie:** kein `build/apk/lib` → `isdir`-Guard → No-Op. Kein Risiko.
- **Executables (ExeDeploy):** unberührt — Inject betrifft nur `output_kind != executable`; `set_active`-Exklusivität gilt pro `so_output_name` und damit auch sauber für Executables (eindeutiges Dropdown).
- **Doppel-`save`:** `lib_update` speichert Felder (`update`) und Aktiv-Status (`set_active`) getrennt → 2 Writes bei MCP-Updates mit Aktiv-Wechsel. Bewusst in Kauf genommen (Korrektheit > 1 Write); GUI-Pfad schreibt nur 1×.
- **Rückwärtskompatibilität `_find_lib`:** nur das mehrdeutige Namens-Szenario ändert sich (vorher Bug). id-basierte Aufrufer (Normalfall) unverändert.
- **Alte Methoden** `deactivate_same_name` / `toggle_active` bleiben erhalten (additiv, u.a. von `lib_build`-Auto-Aktivierung genutzt) — kein Breaking Change.

## 6. Vorgehen / Fixplan (umgesetzt)

1. **Batch A–D** (Kernfix + Exklusivität + Transparenz) anker-basiert, `.bak_19a`, `py_compile`. ✔
2. **Batch E–F** (Hardening) anker-basiert, `.bak_19b`, `py_compile`. ✔ — getrennt, unabhängig revertierbar.
3. **Offline-Harness** `tests/test_libforge_inject.py`: T1 Exklusivität, T2 frische mtime, T3 App-Lib-Schutz, T4 `_find_lib`-Aktiv-Präferenz → **4/4 grün**. ✔
4. **Live-Verifikation (Nutzer):** ein `BUILD_NATIVE` mit bewusst aktiver v5.8 → im APK muss v5.8 (`c6ed9e76`, 27272 B) liegen; Log zeigt „Cache invalidiert" + sha256 der injizierten Datei. ⏳

## 7. Phase 2 — Ergonomie/Robustheit

**✅ Umgesetzt 2026-10-22 (P2-4 + P2-3):**
- **P2-4 GUI-Spalten** (`ui/tabs/lib_forge_tab.py`): LibForge-Liste zeigt jetzt zusätzlich **`ID`** (kurz, 8 Zeichen), **`Ver`** (version) und **`.so-Stand`** (echte Datei-mtime der `.so`, bzw. „(fehlt)"). Behebt die Nicht-Unterscheidbarkeit mehrerer gleichnamiger `trace`-Einträge — die ergonomische Wurzel der Verwechslung. Zusätzlich ist die **Version jetzt im Editor bearbeitbar** (Feld „Version:" neben dem Namen; `ent_ver`, geladen in `_load_editor`, zurückgeschrieben in `sync_editor_into`, leer→`1.0`). Hintergrund: `version` war nur ein Modell-Freitext (Default `1.0`) und über die GUI gar nicht setzbar — nur per MCP — weshalb alle GUI-Builds auf `1.0` lagen. GUI-only, keine Pipeline-Logik berührt (Backup `.bak_19d_*`).
- **P2-3 MCP-Metadaten** (`mcp_server/tools.py`): `lib_list` liefert pro Lib zusätzlich **`so_exists`/`size`/`sha256`[:12]/`so_mtime`**; `lib_build` gibt **`sha256`/`size`** der frisch gebauten `.so` zurück → Build-Verifikation ohne `unzip | sha256sum`. Read-only, additiv. Tests: `tests/test_libforge_inject.py::test_lib_list_metadata` (T5). Backups `.bak_19c_*`.

**🔲 Weiter offen (bewusst zurückgestellt):**
- **P2-1 Auto-Compile beim Aktivieren** (wenn `.so` fehlt) — braucht GUI-Worker-Thread (compile ist synchron/blockierend), MCP-Pfad inline; besser als expliziter „Aktivieren & Bauen"-Button.
- **P2-2 Workspace-`lib/`-Sync beim (De-)Aktivieren** — **nicht empfohlen:** nach Phase 1 für die Korrektheit unnötig und würde die Aktivierung zu einem zweiten Schreiber in den Workspace-Baum machen (genau das Divergenz-Muster hinter dem MCP-Bypass). Falls Transparenz gewünscht: read-only Vorschau „diese `.so` landet im nächsten Build" statt eager Sync.
