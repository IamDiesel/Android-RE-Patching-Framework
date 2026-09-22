import os
import threading
from tkinter import filedialog, simpledialog, messagebox

from services.native_lib_service import NativeLibManager, get_shared_manager
from services.native_compiler_service import NativeCompilerService


class LibForgeController:
    """Logik fuer den LibForge-Reiter (MVC): CRUD + Compile + Import."""

    def __init__(self, view, app):
        self.view = view
        self.app = app
        # Gemeinsamer app-weiter Manager (siehe get_shared_manager): verhindert, dass
        # LibForge und ExeDeploy sich mit veraltetem Stand gegenseitig ueberschreiben.
        self.mgr = get_shared_manager(app)

    def reload_from_disk(self):
        """Liste neu von der Platte laden (uebernimmt externe Aenderungen, z.B. per MCP)."""
        changed = self.mgr.reload_if_changed()
        self.view.refresh_list()
        self.app.log("[LibForge] Liste aktualisiert"
                     + (" — externe Aenderungen uebernommen." if changed else " (bereits aktuell)."))

    # ---------- CRUD ----------
    def new_lib(self):
        lib = self.mgr.create(name="")
        self.view.refresh_list(select_id=lib.id)

    def import_so(self):
        path = filedialog.askopenfilename(
            title="Fertige .so importieren",
            filetypes=[("Shared Objects", "*.so"), ("All Files", "*.*")])
        if not path:
            return
        default_name = os.path.basename(path)
        if default_name.startswith("lib"):
            default_name = default_name[3:]
        if default_name.endswith(".so"):
            default_name = default_name[:-3]
        name = simpledialog.askstring("Lib-Name", "Name der Lib (ohne 'lib'/'.so'):",
                                      initialvalue=default_name, parent=self.view)
        if not name:
            return
        abi = self.app.cfg.config.get("DEFAULT_ABI", "arm64-v8a")
        try:
            lib = self.mgr.import_so(name.strip(), path, description="Importierte .so", abi=abi)
            self.app.log(f"[+] LibForge: '{self.mgr.so_output_name(lib)}' importiert.")
            self.view.refresh_list(select_id=lib.id)
        except Exception as e:
            messagebox.showerror("Import fehlgeschlagen", str(e))

    def delete_selected(self):
        lib = self.view.current_lib()
        if not lib:
            return
        if messagebox.askyesno("Loeschen",
                               f"'{self.mgr.so_output_name(lib)}' inkl. Quellcode und .so endgueltig loeschen?"):
            self.mgr.delete(lib.id)
            self.app.log(f"[*] LibForge: '{lib.name}' geloescht.")
            self.view.refresh_list()

    def toggle_active(self):
        lib = self.view.current_lib()
        if not lib:
            return
        # Aktivieren erfordert ein vorhandenes Binary (shared .so ODER executable).
        if not lib.active and not (lib.so_relpath and os.path.exists(self.mgr.abspath(lib.so_relpath))):
            messagebox.showwarning("Nicht aktivierbar",
                                   "Dieses Target hat noch kein gebautes Binary. Bitte zuerst kompilieren.")
            return
        # Modell A: exklusiv-aktiv pro Ausgabe-Name. Beim Aktivieren werden gleichnamige
        # Targets deaktiviert -- fuer shared = welche .so injiziert wird, fuer executable
        # = welches Binary ExeDeploy deployt. So kann trotz vieler gleichnamiger
        # Build-Iterationen immer nur EINES aktiv (und damit deploybar) sein.
        if not lib.active:
            n = self.mgr.deactivate_same_name(lib)
            if n:
                self.app.log(f"[*] LibForge: {n} andere aktive Target(s) mit gleichem Namen deaktiviert.")
        self.mgr.toggle_active(lib.id, not lib.active)
        self.view.refresh_list(select_id=lib.id)

    def save_editor(self):
        lib = self.view.current_lib()
        if not lib:
            return
        self.view.sync_editor_into(lib)
        self.mgr.update(lib)
        self.app.log(f"[*] LibForge: '{lib.name}' gespeichert.")

    # ---------- Compile ----------
    def compile_selected(self):
        self.app.log("[*] LibForge: Compile geklickt ...")
        lib = self.view.current_lib()
        if not lib:
            messagebox.showinfo("LibForge",
                                "Keine Lib ausgewaehlt. Bitte links einen Eintrag anklicken "
                                "oder zuerst '+ Neu'.")
            self.app.log("[!] LibForge: keine Lib ausgewaehlt.")
            return
        if lib.origin == "imported":
            messagebox.showinfo("Import", "Importierte Libs haben keinen Quellcode und werden nicht kompiliert.")
            return
        self.view.sync_editor_into(lib)
        if not (lib.name or "").strip():
            messagebox.showwarning("Name fehlt", "Bitte zuerst einen Lib-Namen angeben.")
            return
        self.mgr.update(lib)
        self.view.set_status("… kompiliere …", "orange")

        def task():
            try:
                ok, so_path, err = NativeCompilerService.compile(lib, self.mgr, self.app.cfg.config)
            except Exception as ex:
                import traceback
                self.app.log("[!] LibForge Compile-Exception:\n" + traceback.format_exc())
                ok, so_path, err = False, "", str(ex)

            def done():
                # Modell A: ein erfolgreich gebautes EXECUTABLE wird automatisch das
                # aktive Target seines Namens; gleichnamige alte Iterationen werden
                # deaktiviert. So deployt ExeDeploy immer genau das zuletzt Gebaute
                # ("build = aktiv"), ohne manuelles Umschalten. Shared-Libs behalten
                # ihre bewusste manuelle Aktivierung (Inject).
                if ok and getattr(lib, "output_kind", "shared") == "executable":
                    n = self.mgr.deactivate_same_name(lib)
                    lib.active = True
                    self.mgr.update(lib)
                    if n:
                        self.app.log(f"[*] LibForge: '{lib.name}' ist jetzt aktiv "
                                     f"({n} gleichnamige(s) Executable deaktiviert).")
                    else:
                        self.app.log(f"[*] LibForge: '{lib.name}' als aktives Executable gesetzt.")
                self.view.refresh_list(select_id=lib.id)
                if ok:
                    self.view.set_status(f"OK · {self.mgr.so_output_name(lib)}", "green")
                    self.view.show_load_hint(lib.name)
                else:
                    self.view.set_status("Build-Fehler (siehe Konsole)", "red")
                    self.view.show_build_error(err)

            self.app.after(0, done)

        threading.Thread(target=task, daemon=True).start()

    # ---------- Load-Snippet ----------
    def load_snippet(self, name: str) -> str:
        return ('const-string v0, "%s"\n'
                'invoke-static {v0}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V' % name)

    def copy_load_snippet(self):
        lib = self.view.current_lib()
        if not lib or not lib.name:
            return
        snippet = self.load_snippet(lib.name)
        try:
            self.view.clipboard_clear()
            self.view.clipboard_append(snippet)
            self.app.log(f"[*] LibForge: loadLibrary-Snippet fuer '{lib.name}' kopiert.")
        except Exception:
            pass
