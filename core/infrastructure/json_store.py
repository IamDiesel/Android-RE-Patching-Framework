"""
json_store — konfliktsichere Persistenz fuer die JSON-Stores (native_libs, favorites).

Kernproblem: GUI-Prozess und MCP-stdio-Server halten je eine eigene, beim Start
geladene Kopie der Liste und schrieben bisher die KOMPLETTE Kopie zurueck. Wer
zuletzt speichert, ueberschrieb die Aenderungen des anderen (Datenverlust).

Loesung: Beim Speichern die aktuelle Platten-Version frisch lesen und nur die
EIGENEN Deltas (relativ zu einer beim Laden gezogenen Baseline) darauf anwenden
(Three-Way-Merge, key-basiert). Fremde Aenderungen bleiben so erhalten. Zusaetzlich
atomares Schreiben (Temp + os.replace) und ein mtime-basierter Reload-Guard.
"""
import os
import json


def file_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _fp(item) -> str:
    """Wert-Fingerabdruck eines Items (fuer Aenderungs-Erkennung)."""
    return json.dumps(item, sort_keys=True, ensure_ascii=False)


def snapshot_fingerprints(items, key_fn) -> dict:
    """Baseline: {key: fingerprint} — was wir beim Laden gesehen haben."""
    return {key_fn(it): _fp(it) for it in items}


def atomic_write_text(path: str, text: str) -> None:
    """Atomar schreiben: Temp-Datei + fsync + os.replace (kein truncatetes File)."""
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def three_way_merge(disk_items, baseline_fp, memory_items, key_fn):
    """Eigene Deltas (memory ggue. baseline_fp) auf die frische disk-Liste anwenden.

    key_fn(item) -> stabiler Schluessel (Lib: id, Favorit: name).
    Rueckgabe (merged_list, conflicts):
      - Reihenfolge: bestehende Platten-Reihenfolge bleibt; unsere NEUEN Keys werden
        angehaengt (memory-Reihenfolge).
      - conflicts: Keys, die ein anderer Prozess ggue. der Baseline geaendert hat und
        die wir ebenfalls anfassen -> unsere Version gewinnt, wird aber gemeldet.
    """
    mem_by_key, mem_order = {}, []
    for it in memory_items:
        k = key_fn(it)
        mem_by_key[k] = it
        mem_order.append(k)
    mem_fp = {k: _fp(v) for k, v in mem_by_key.items()}

    our_deletes = set(baseline_fp) - set(mem_by_key)
    our_upserts = {k for k in mem_by_key if k not in baseline_fp or mem_fp[k] != baseline_fp[k]}

    disk_by_key, disk_order = {}, []
    for it in disk_items:
        k = key_fn(it)
        disk_by_key[k] = it
        disk_order.append(k)
    disk_fp = {k: _fp(v) for k, v in disk_by_key.items()}

    conflicts = [k for k in (our_upserts | our_deletes)
                 if k in baseline_fp and k in disk_fp and disk_fp[k] != baseline_fp[k]]

    merged, seen = [], set()
    for k in disk_order:
        if k in our_deletes:
            continue
        seen.add(k)
        merged.append(mem_by_key[k] if k in our_upserts else disk_by_key[k])
    for k in mem_order:
        if k in our_upserts and k not in seen:
            merged.append(mem_by_key[k])
            seen.add(k)
    return merged, conflicts
