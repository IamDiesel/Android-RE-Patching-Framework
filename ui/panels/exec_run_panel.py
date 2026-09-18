import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog

from ui.controllers.exec_run_controller import get_shared_controller
from ui.widgets.tooltip import add_tooltip


class ExecRunPanel(ttk.LabelFrame):
    """Kompaktes Deploy-&-Run-Panel (max. 3 Zeilen) fuer Executable-Targets.

    Detail-Optionen (Args/Env/Inputs/Runtime-Deps/Sync/Profile) liegen im Options-Popup,
    damit die Konsole sichtbar bleibt. Nutzt den gemeinsamen ExecRunController."""

    def __init__(self, parent, app, title="🚀 Executable"):
        super().__init__(parent, text=title)
        self.app = app
        self.ctrl = get_shared_controller(app)
        self._id_by_label = {}
        self.create_widgets()
        self.refresh_targets()

    # ---------------- Hauptleiste (3 Zeilen) ----------------
    def create_widgets(self):
        r1 = ttk.Frame(self); r1.pack(fill="x", padx=4, pady=(3, 1))
        ttk.Label(r1, text="Ziel:").pack(side="left")
        self.cmb_target = ttk.Combobox(r1, state="readonly", width=22)
        self.cmb_target.pack(side="left", padx=3)
        self.cmb_target.bind("<<ComboboxSelected>>", lambda e: self._remember())
        add_tooltip(self.cmb_target, "Kompiliertes Executable-Target (Ausgabe 'executable' in LibForge).")
        b_ref = ttk.Button(r1, text="🔄", width=3, command=self.refresh_targets)
        b_ref.pack(side="left", padx=1)
        add_tooltip(b_ref, "Ziel-Liste neu laden (aktualisiert die kompilierten Executables).")
        b_opt = ttk.Button(r1, text="⚙ Optionen…", command=self._open_options)
        b_opt.pack(side="left", padx=3)
        add_tooltip(b_opt, "Args, Env, Inputs, Runtime-Deps, Sync (PC↔Gerät), Ergebnis-Download und Profile.")

        # Start/Stop laufen zentral über die „Steuerung“ (Mitte). Hier nur Konfig/Wartung.
        r2 = ttk.Frame(self); r2.pack(fill="x", padx=4, pady=(1, 3))
        b_clean = ttk.Button(r2, text="🧹 Cleanup", command=self._cleanup)
        b_clean.pack(side="left", padx=(0, 2))
        add_tooltip(b_clean, "Entfernt Binary + Runtime-Deps + gestagte Inputs + Ausgabedateien aus dem "
                             "Run-Dir auf dem Gerät (nicht lokal).")
        b_exe = ttk.Button(r2, text="📂 Executable-Ordner", command=self._open_exe_folder)
        b_exe.pack(side="left", padx=2)
        add_tooltip(b_exe, "File-Explorer im Run-Dir öffnen (z. B. /data/local/tmp, Shell-Domain).")
        b_app = ttk.Button(r2, text="📁 App-Ordner", command=self._open_app_folder)
        b_app.pack(side="left", padx=2)
        add_tooltip(b_app, "File-Explorer in der App-Home öffnen (/data/data/<pkg>/, run-as).")

    # ---------------- Ziel-Liste ----------------
    def refresh_targets(self):
        targets = self.ctrl.list_targets()
        self._id_by_label = {}
        labels = []
        for t in targets:
            label = t.name or "(unbenannt)"
            if label in self._id_by_label:
                label = f"{label} [{t.id[:6]}]"
            self._id_by_label[label] = t.id
            labels.append(label)
        self.cmb_target["values"] = labels
        want = self.ctrl.last_target_id
        sel = next((l for l, i in self._id_by_label.items() if i == want), None)
        self.cmb_target.set(sel or (labels[0] if labels else ""))
        self._remember()

    def _remember(self):
        lib = self._selected_lib()
        if lib:
            self.ctrl.last_target_id = lib.id

    def _selected_lib(self):
        lib_id = self._id_by_label.get(self.cmb_target.get())
        return self.ctrl.get_target(lib_id) if lib_id else None

    def _require(self):
        lib = self._selected_lib()
        if not lib:
            messagebox.showinfo("ExeDeploy", "Kein Executable-Target gewählt.\n"
                                             "In LibForge eine Lib mit Ausgabe 'executable' bauen.")
            return None
        return lib

    # ---------------- Oeffentliche API (fuer zentrale Toggle-/Start-Buttons) ----------------
    def selected_lib(self):
        return self._selected_lib()

    def deploy_selected(self):
        self._deploy_and_run()

    def stop_selected(self):
        self._stop()

    # ---------------- Hauptaktionen ----------------
    def _deploy_and_run(self):
        lib = self._require()
        if lib:
            self.ctrl.deploy_and_run(lib)

    def _stop(self):
        lib = self._selected_lib()
        if lib:
            self.ctrl.stop(lib)

    def _cleanup(self):
        lib = self._require()
        if not lib:
            return
        files = self.ctrl.cleanup_targets(lib)
        run_dir = lib.run_dir or "/data/local/tmp"
        if messagebox.askyesno("Cleanup",
                               f"Folgende Dateien in {run_dir} auf dem Gerät löschen?\n\n"
                               + "\n".join(files)):
            self.ctrl.cleanup(lib)

    def _open_exe_folder(self):
        self.ctrl.open_executable_folder(self._selected_lib())

    def _open_app_folder(self):
        self.ctrl.open_app_folder()

    # ---------------- Options-Popup ----------------
    def _open_options(self):
        lib = self._require()
        if not lib:
            return
        win = tk.Toplevel(self)
        win.title(f"Optionen — {lib.name}")
        win.transient(self.winfo_toplevel())
        win.resizable(False, False)
        pad = {"padx": 6, "pady": 3}

        frm = ttk.Frame(win); frm.pack(fill="both", expand=True, padx=8, pady=8)

        def row(r, label):
            ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w", **pad)

        row(0, "Run-Dir:")
        e_dir = ttk.Entry(frm, width=48); e_dir.grid(row=0, column=1, columnspan=3, sticky="we", **pad)
        e_dir.insert(0, lib.run_dir or self.app.cfg.config.get("EXEC_RUN_DIR_DEFAULT", "/data/local/tmp"))

        row(1, "Args:")
        e_args = ttk.Entry(frm, width=48); e_args.grid(row=1, column=1, columnspan=3, sticky="we", **pad)
        e_args.insert(0, lib.run_args or "")
        add_tooltip(e_args, "Argumente nach dem Binary, z. B.  ./libb11bb8.so")

        row(2, "Env:")
        e_env = ttk.Entry(frm, width=48); e_env.grid(row=2, column=1, columnspan=3, sticky="we", **pad)
        e_env.insert(0, lib.run_env or "")
        add_tooltip(e_env, "Umgebungsvariablen, z. B.  LD_LIBRARY_PATH=/data/local/tmp")

        row(3, "Inputs (App-Home):")
        e_in = ttk.Entry(frm, width=48); e_in.grid(row=3, column=1, columnspan=3, sticky="we", **pad)
        e_in.insert(0, " ".join(lib.stage_inputs or []))
        add_tooltip(e_in, "Dateinamen aus /data/data/<pkg>/, die geräteintern ins Run-Dir gestaged werden "
                          "(Leerzeichen-getrennt).")

        row(4, "Pull-Globs (Output):")
        e_pull = ttk.Entry(frm, width=48); e_pull.grid(row=4, column=1, columnspan=3, sticky="we", **pad)
        e_pull.insert(0, " ".join(lib.pull_globs or []))
        add_tooltip(e_pull, "Ausgabedateien, die 'Ergebnis holen' aus dem Run-Dir lädt (Leerzeichen-getrennt).")

        row(5, "Runtime-Deps:")
        t_deps = tk.Text(frm, width=48, height=4, font=("Consolas", 9))
        t_deps.grid(row=5, column=1, columnspan=2, sticky="we", **pad)
        t_deps.insert("1.0", "\n".join(lib.runtime_deps or []))
        add_tooltip(t_deps, "Lokale Pfade (ein Pfad pro Zeile), die mitdeployt werden — z. B. der "
                            "libb11bb8.so-Dump und libc++_shared.so.")
        b_adddep = ttk.Button(frm, text="＋ Datei…",
                              command=lambda: self._add_dep_files(t_deps))
        b_adddep.grid(row=5, column=3, sticky="n", **pad)

        var_clean = tk.BooleanVar(value=bool(lib.cleanup_after))
        ttk.Checkbutton(frm, text="Nach Run automatisch aufräumen", variable=var_clean)\
            .grid(row=6, column=1, columnspan=3, sticky="w", **pad)

        # --- Presets ---
        ttk.Separator(frm, orient="horizontal").grid(row=7, column=0, columnspan=4, sticky="we", pady=6)
        row(8, "Profil:")
        cmb_preset = ttk.Combobox(frm, values=self.ctrl.list_presets(), width=28, state="readonly")
        cmb_preset.grid(row=8, column=1, sticky="w", **pad)

        def collect():
            return {
                "run_dir": e_dir.get().strip() or "/data/local/tmp",
                "run_args": e_args.get().strip(),
                "run_env": e_env.get().strip(),
                "stage_inputs": [x for x in e_in.get().split() if x],
                "pull_globs": [x for x in e_pull.get().split() if x],
                "runtime_deps": [l.strip() for l in t_deps.get("1.0", "end").splitlines() if l.strip()],
                "cleanup_after": var_clean.get(),
            }

        def fill(d):
            for e, k in ((e_dir, "run_dir"), (e_args, "run_args"), (e_env, "run_env")):
                e.delete(0, "end"); e.insert(0, d.get(k, "") or "")
            e_in.delete(0, "end"); e_in.insert(0, " ".join(d.get("stage_inputs", []) or []))
            e_pull.delete(0, "end"); e_pull.insert(0, " ".join(d.get("pull_globs", []) or []))
            t_deps.delete("1.0", "end"); t_deps.insert("1.0", "\n".join(d.get("runtime_deps", []) or []))
            var_clean.set(bool(d.get("cleanup_after", False)))

        def apply_to_lib():
            d = collect()
            for k, v in d.items():
                setattr(lib, k, v)
            lib.run_cwd = lib.run_dir
            self.ctrl.mgr.update(lib)

        def do_load():
            name = cmb_preset.get()
            data = self.ctrl._load_presets().get(name) if name else None
            if not data:
                messagebox.showinfo("Profil", "Kein Profil gewählt.")
                return
            fill(data)
            self.app.log(f"[*] ExeDeploy: Profil '{name}' in Felder geladen (noch nicht gespeichert).")

        def do_save_as():
            apply_to_lib()
            name = simpledialog.askstring("Profil speichern als", "Profilname:", parent=win)
            if not name:
                return
            self.ctrl.save_preset(name, lib)
            cmb_preset["values"] = self.ctrl.list_presets()
            cmb_preset.set(name)
            self.app.log(f"[+] ExeDeploy: Profil '{name}' gespeichert.")

        b_load = ttk.Button(frm, text="📂 Laden", command=do_load)
        b_load.grid(row=8, column=2, sticky="w", **pad)
        add_tooltip(b_load, "Ausgewähltes Profil in die Felder laden (mit 'Speichern' auf das Target übernehmen).")
        b_saveas = ttk.Button(frm, text="💾 Speichern als…", command=do_save_as)
        b_saveas.grid(row=8, column=3, sticky="w", **pad)
        add_tooltip(b_saveas, "Aktuelle Felder als benanntes Profil ablegen (data/exec_run_profiles.json).")

        # --- Sync / Ergebnis ---
        ttk.Separator(frm, orient="horizontal").grid(row=9, column=0, columnspan=4, sticky="we", pady=6)
        f_sync = ttk.Frame(frm); f_sync.grid(row=10, column=0, columnspan=4, sticky="we", **pad)
        b_push = ttk.Button(f_sync, text="🔄 Sync PC→Gerät",
                            command=lambda: (apply_to_lib(), self.ctrl.sync(lib, "push")))
        b_push.pack(side="left", padx=2)
        add_tooltip(b_push, "Lokalen Target-Ordner (data/native_libs/<id>/) ins Run-Dir auf dem Gerät pushen. "
                            "Vorher lokales versioniertes Backup.")
        b_pull = ttk.Button(f_sync, text="⬇ Sync Gerät→PC",
                            command=lambda: (apply_to_lib(), self.ctrl.sync(lib, "pull")))
        b_pull.pack(side="left", padx=2)
        add_tooltip(b_pull, "Run-Dir vom Gerät in den lokalen Target-Ordner ziehen. Vorher lokales Backup.")
        b_res = ttk.Button(f_sync, text="⬇ Ergebnis holen",
                           command=lambda: (apply_to_lib(), self._pull_result(lib)))
        b_res.pack(side="left", padx=2)
        add_tooltip(b_res, "Nur die Pull-Globs (Ausgabedateien) aus dem Run-Dir in den Target-Ordner "
                           "herunterladen (Gerät→PC).")

        # --- Footer ---
        f_btn = ttk.Frame(frm); f_btn.grid(row=11, column=0, columnspan=4, sticky="e", pady=(8, 0))
        b_save = ttk.Button(f_btn, text="💾 Speichern",
                            command=lambda: (apply_to_lib(),
                                             self.app.log(f"[*] ExeDeploy: Profil für '{lib.name}' gespeichert."),
                                             win.destroy()))
        b_save.pack(side="right", padx=3)
        add_tooltip(b_save, "Einstellungen auf dieses Target übernehmen und speichern.")
        ttk.Button(f_btn, text="Schließen", command=win.destroy).pack(side="right", padx=3)

        frm.columnconfigure(1, weight=1)

    def _add_dep_files(self, text_widget):
        paths = filedialog.askopenfilenames(title="Runtime-Deps hinzufügen")
        if not paths:
            return
        cur = text_widget.get("1.0", "end").strip()
        add = "\n".join(paths)
        text_widget.delete("1.0", "end")
        text_widget.insert("1.0", (cur + "\n" + add).strip() if cur else add)

    def _pull_result(self, lib):
        if not lib.pull_globs:
            messagebox.showinfo("Ergebnis holen", "Keine Pull-Globs (Ausgabedateien) definiert.")
            return
        self.ctrl.pull_result(lib)
