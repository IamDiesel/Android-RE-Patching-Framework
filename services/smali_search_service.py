# Datei: smali_search.py

import os
import threading
import concurrent.futures
import pickle

from core.application.event_bus import EventBus

class SmaliSearchEngine:
    def __init__(self):
        self.ram_cache = []
        self.is_indexed = False
        self.is_indexing = False
        self.cancel_flag = False
        self.indexed_pkg = None
        self.indexed_dir = None
        self._index_seq = 0
        self._pending_build = None

    def reset(self):
        """Leert den RAM-Index beim App-/Workspace-Wechsel und bricht laufende Indexierung ab."""
        self._index_seq += 1
        self.cancel_flag = True
        self._pending_build = None
        self.ram_cache.clear()
        self.is_indexed = False
        self.indexed_pkg = None
        self.indexed_dir = None

    def log(self, msg: str) -> None:
        EventBus.publish("LOG_INFO", msg)

    def update_progress(self, msg: str) -> None:
        EventBus.publish("INDEX_PROGRESS", msg)

    # NEU: Nimmt jetzt source_smali_dir (Read-Only) UND cache_dir (Destination) entgegen
    def build_ram_index(self, source_smali_dir, cache_dir, pkg_name, on_complete_callback):
        if not source_smali_dir or not os.path.exists(source_smali_dir):
            return
        if self.is_indexing:
            # Merke neuen Ziel-Build, falls alte Indexierung noch auslaeuft (nach reset())
            self._pending_build = (source_smali_dir, cache_dir, pkg_name, on_complete_callback)
            self.cancel_flag = True
            return
        self.cancel_flag = False
        self._pending_build = None
        self._index_seq += 1
        my_seq = self._index_seq
        norm_src = os.path.abspath(source_smali_dir)

        def task():
            self.is_indexing = True
            try:
                self.ram_cache.clear()
                self.is_indexed = False
                self.indexed_pkg = None
                self.indexed_dir = None

                # NEU: Der Index wird in der DESTINATION (cache_dir) gespeichert!
                os.makedirs(cache_dir, exist_ok=True)
                index_file = os.path.join(cache_dir, f".{pkg_name}_index.pkl")

                _fresh = False
                if os.path.exists(index_file):
                    try:
                        _idx_m = os.path.getmtime(index_file)
                        _newest = 0.0
                        for _r, _, _fs in os.walk(source_smali_dir):
                            if self.cancel_flag or my_seq != self._index_seq:
                                return
                            for _f in _fs:
                                if _f.endswith((".smali", ".xml")):
                                    try:
                                        _m = os.path.getmtime(os.path.join(_r, _f))
                                        if _m > _newest:
                                            _newest = _m
                                    except OSError:
                                        pass
                        _fresh = _newest <= _idx_m
                    except OSError:
                        _fresh = False

                if _fresh:
                    self.log(f"[*] Lade Cache aus {index_file}...")
                    with open(index_file, "rb") as f:
                        loaded = pickle.load(f)
                    if self.cancel_flag or my_seq != self._index_seq:
                        return
                    self.ram_cache = loaded
                else:
                    if os.path.exists(index_file):
                        self.log("[*] Quelle neuer als Index -> baue RAM-Index neu auf...")
                    self.log("[*] Sammle Dateipfade aus Source...")
                    filepaths = [os.path.join(r, f) for r, _, fs in os.walk(source_smali_dir) for f in fs if
                                 f.endswith((".smali", ".xml"))]

                    total = len(filepaths)
                    cache = []
                    processed = 0

                    def read_file(path):
                        try:
                            with open(path, "r", encoding="utf-8") as f:
                                # Relativer Pfad basiert auf der Source
                                return (os.path.relpath(path, source_smali_dir), f.read())
                        except:
                            return None

                    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as executor:
                        for future in concurrent.futures.as_completed(
                                [executor.submit(read_file, p) for p in filepaths]):
                            if self.cancel_flag or my_seq != self._index_seq:
                                return
                            res = future.result()
                            if res: cache.append(res)
                            processed += 1
                            if processed % 1000 == 0 or processed == total:
                                self.update_progress(f"Indexiere RAM... {processed}/{total}")

                    if self.cancel_flag or my_seq != self._index_seq:
                        return
                    self.ram_cache = cache

                    # Speichern im neuen cache_dir
                    with open(index_file, "wb") as f:
                        pickle.dump(cache, f)

                if self.cancel_flag or my_seq != self._index_seq:
                    return
                self.is_indexed = True
                self.indexed_pkg = pkg_name
                self.indexed_dir = norm_src
                self.log(f"[+] RAM-Index bereit: {len(self.ram_cache)} Dateien.")
            except Exception as e:
                self.log(f"[!] Fehler beim Indexieren: {e}")
            finally:
                self.is_indexing = False
                pending = self._pending_build
                if pending is not None:
                    self._pending_build = None
                    self.build_ram_index(*pending)
                elif my_seq == self._index_seq and not self.cancel_flag and on_complete_callback:
                    on_complete_callback(len(self.ram_cache))

        threading.Thread(target=task, daemon=True).start()

    def search_xrefs_incoming(self, search_term, on_results_callback):
        self.cancel_flag = False

        def task():
            results = []
            for rel_path, content in self.ram_cache:
                if self.cancel_flag or len(results) >= 500: break
                if not rel_path.endswith(".smali"): continue

                if search_term in content:
                    lines = content.splitlines()
                    for line_no, line in enumerate(lines):
                        if search_term in line:
                            idx = line_no
                            while idx >= 0:
                                if lines[idx].strip().startswith(".method"):
                                    caller = lines[idx].strip().replace(".method ", "")
                                    results.append((rel_path, caller, line_no + 1))
                                    break
                                idx -= 1
                            if len(results) >= 500: break
            on_results_callback(results, self.cancel_flag)

        threading.Thread(target=task, daemon=True).start()