import threading
import os
import tempfile
import subprocess
import sys
from tkinter import messagebox, filedialog
from services.device_file_service import DeviceFileService


class DeviceFileManagerController:
    def __init__(self, view, app):
        self.view = view
        self.app = app
        self.service = DeviceFileService(app.cfg)
        self.current_pkg = ""
        self.current_path = ""
        self.cancel_download_flag = False

    def get_downloads_dir(self):
        if os.name == 'nt':
            import winreg
            try:
                sub_key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub_key) as key:
                    return winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")[0]
            except Exception:
                pass
        return os.path.join(os.path.expanduser("~"), "Downloads")

    def format_size(self, size_bytes):
        """Formatiert Byte-Werte lesbar (B, KB, MB, GB)."""
        try:
            s = float(size_bytes)
            for unit in ['B', 'KB', 'MB', 'GB']:
                if s < 1024.0:
                    return f"{s:.1f} {unit}" if unit != 'B' else f"{int(s)} B"
                s /= 1024.0
            return f"{s:.2f} TB"
        except Exception:
            return size_bytes

    def load_packages(self):
        def task():
            pkgs = self.service.list_packages()
            self.app.after(0, lambda: self.view.update_packages(pkgs))

        threading.Thread(target=task, daemon=True).start()

    def load_directory(self, pkg: str, path: str):
        if not path.endswith("/"): path += "/"
        self.current_pkg, self.current_path = pkg, path
        self.view.update_status(f"Lade {path}...")

        def task():
            lines = self.service.list_dir(pkg, path)
            parsed = self._parse_ls(lines)
            self.app.after(0, lambda: self.view.render_files(parsed, path))

        threading.Thread(target=task, daemon=True).start()

    def _parse_ls(self, lines: list):
        files = []
        for line in lines:
            if not line or line.startswith("total "): continue
            parts = line.split(maxsplit=7)
            if len(parts) >= 8:
                perms, size_raw, date, name = parts[0], parts[4], f"{parts[5]} {parts[6]}", parts[7]
                if name in [".", ".."]: continue
                size_str = self.format_size(size_raw) if not perms.startswith("d") else "-"
                files.append(
                    {"name": name, "is_dir": perms.startswith("d"), "size": size_str, "date": date, "perms": perms})
        return sorted(files, key=lambda x: (not x["is_dir"], x["name"].lower()))

    def open_file(self, filename: str):
        remote_path = self.current_path + filename
        tmp_dir = os.path.join(tempfile.gettempdir(), "kippy_re")
        os.makedirs(tmp_dir, exist_ok=True)
        local_path = os.path.join(tmp_dir, filename)

        self.view.update_status(f"Lade '{filename}' in Temp-Ordner...")

        def task():
            success = self.service.pull_file(self.current_pkg, remote_path, local_path)
            if success:
                try:
                    if os.name == 'nt':
                        os.startfile(local_path)
                    elif sys.platform == 'darwin':
                        subprocess.Popen(['open', local_path])
                    else:
                        subprocess.Popen(['xdg-open', local_path])
                    self.app.after(0, lambda: self.view.update_status(f"Geöffnet: {filename}"))
                except Exception as e:
                    self.app.after(0, lambda: messagebox.showerror("Fehler", f"Konnte Datei nicht öffnen:\n{e}"))
            else:
                self.app.after(0, lambda: messagebox.showerror("Fehler", "Fehler beim Herunterladen der Datei."))

        threading.Thread(target=task, daemon=True).start()

    def cancel_download(self):
        self.cancel_download_flag = True

    def download_selected(self, selections: list):
        if not selections: return

        # Einzelfall: Genau eine Datei gewählt (Speichern unter...)
        if len(selections) == 1 and not selections[0]["is_dir"]:
            remote_path = self.current_path + selections[0]["name"]
            local_path = filedialog.asksaveasfilename(initialdir=self.get_downloads_dir(),
                                                      initialfile=selections[0]["name"])
            if not local_path: return
            self._execute_download([remote_path], os.path.dirname(local_path), single_file_local_path=local_path)
        else:
            # Ordner oder mehrere Dateien -> In Verzeichnis laden
            local_dir = filedialog.askdirectory(initialdir=self.get_downloads_dir(), title="Zielverzeichnis auswählen")
            if not local_dir: return
            remote_paths = [self.current_path + s["name"] for s in selections]
            self._execute_download(remote_paths, local_dir)

    def _execute_download(self, remote_paths, local_dir, single_file_local_path=None):
        self.cancel_download_flag = False
        self.view.show_download_progress()

        def task():
            # Rekursive Auflistung und Größenberechnung
            self.app.after(0, lambda: self.view.update_download_status("Scanne Dateien und Ordner..."))
            files = self.service.resolve_files_for_download(self.current_pkg, remote_paths)

            total_size = sum(f["size"] for f in files)
            copied_size = 0
            base_remote_dir = self.current_path

            for i, f in enumerate(files):
                if self.cancel_download_flag:
                    self.app.after(0, lambda: self.view.update_download_status("Vorgang abgebrochen."))
                    break

                remote_file = f["remote_path"]
                file_size = f["size"]

                if single_file_local_path and len(files) == 1:
                    local_file = single_file_local_path
                else:
                    rel_path = remote_file[len(base_remote_dir):] if remote_file.startswith(
                        base_remote_dir) else os.path.basename(remote_file)
                    local_file = os.path.join(local_dir, rel_path)

                # UI Update vor dem Pull
                self.app.after(0, lambda rf=remote_file, idx=i, cs=copied_size:
                self.view.update_download_progress(rf, idx + 1, len(files), cs, total_size))

                success = self.service.pull_file(self.current_pkg, remote_file, local_file)
                if success:
                    copied_size += file_size

            # Letztes UI Update nach Schleife
            self.app.after(0, lambda: self.view.update_download_progress("Fertig.", len(files), len(files), copied_size,
                                                                         total_size))
            self.app.after(500, self.view.close_download_progress)

        threading.Thread(target=task, daemon=True).start()

    def upload_file(self):
        default_dir = self.get_downloads_dir()
        local_path = filedialog.askopenfilename(initialdir=default_dir)
        if not local_path: return
        filename = os.path.basename(local_path)
        remote_path = self.current_path + filename
        self.view.update_status(f"Lade {filename} hoch...")

        def task():
            success = self.service.push_file(self.current_pkg, local_path, remote_path)
            if success:
                self.app.after(0, lambda: messagebox.showinfo("Upload", f"{filename} erfolgreich hochgeladen!"))
            else:
                self.app.after(0, lambda: messagebox.showerror("Upload", f"Upload von {filename} fehlgeschlagen!"))
            self.app.after(0, lambda: self.load_directory(self.current_pkg, self.current_path))
            self.app.after(0, lambda: self.view.update_status("Bereit."))

        threading.Thread(target=task, daemon=True).start()

    def delete_file(self, items: list):
        if not messagebox.askyesno("Löschen", f"Wirklich {len(items)} Elemente löschen?"): return

        def task():
            for item in items:
                self.service.delete_file(self.current_pkg, self.current_path + item["name"])
            self.app.after(0, lambda: self.load_directory(self.current_pkg, self.current_path))

        threading.Thread(target=task, daemon=True).start()