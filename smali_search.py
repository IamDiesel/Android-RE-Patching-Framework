import os
import threading
import concurrent.futures
import pickle


class SmaliSearchEngine:
    def __init__(self, log_callback, update_progress_callback):
        self.log = log_callback
        self.update_progress = update_progress_callback
        self.ram_cache = []
        self.is_indexed = False
        self.is_indexing = False
        self.cancel_flag = False

    # NEU: Nimmt jetzt source_smali_dir (Read-Only) UND cache_dir (Destination) entgegen
    def build_ram_index(self, source_smali_dir, cache_dir, pkg_name, on_complete_callback):
        if not os.path.exists(source_smali_dir) or self.is_indexing:
            return

        def task():
            self.is_indexing = True
            try:
                self.ram_cache.clear()
                self.is_indexed = False

                # NEU: Der Index wird in der DESTINATION (cache_dir) gespeichert!
                os.makedirs(cache_dir, exist_ok=True)
                index_file = os.path.join(cache_dir, f".{pkg_name}_index.pkl")

                if os.path.exists(index_file):
                    self.log(f"[*] Lade Cache aus {index_file}...")
                    with open(index_file, "rb") as f:
                        self.ram_cache = pickle.load(f)
                else:
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
                            res = future.result()
                            if res: cache.append(res)
                            processed += 1
                            if processed % 1000 == 0 or processed == total:
                                self.update_progress(f"Indexiere RAM... {processed}/{total}")

                    self.ram_cache = cache

                    # Speichern im neuen cache_dir
                    with open(index_file, "wb") as f:
                        pickle.dump(cache, f)

                self.is_indexed = True
                self.log(f"[+] RAM-Index bereit: {len(self.ram_cache)} Dateien.")
            except Exception as e:
                self.log(f"[!] Fehler beim Indexieren: {e}")
            finally:
                self.is_indexing = False
                if on_complete_callback:
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