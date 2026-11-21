# -*- coding: utf-8 -*-
"""OS-Inspektor — Dock-Views fuer das OS-Substrat (OS-1..OS-9).

Duenne Views (Console-MVC): Zustand liegt im ``OsInspectorModel``, die Views
rendern daraus und abonnieren Aenderungen. Registriert eine Dock-Gruppe von
neun Flaechen in den Debugger-Dock. tkinter wird erst auf Windows gerendert;
headless nur py_compile.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox


class OsInspectorViews:
    """Baut die OS-Inspektor-Dock-Views und bindet sie ans Modell.

    ``controller`` liefert optional Aktionen (os_attach-Lauf, push/pull); fehlt er,
    bleiben die Flaechen read/edit am Modell (Edits landen im config_delta)."""

    def __init__(self, dm, model, controller=None, reader=None):
        self.dm = dm
        self.model = model
        self.ctrl = controller
        # reader (optional, live): read_file(path)->bytes, read_bytes(addr,n)->bytes, patch_bytes(addr,data)
        self.reader = reader
        self._widgets = {}      # name -> Treeview/Widget zum Neurendern
        self._base_seq = 0

    # ---- Registrierung in den Dock -----------------------------------------
    def register(self):
        dm = self.dm
        dm.register_view("os_syscalls", "OS · Syscalls", self._v_syscalls, "right", "D-")
        dm.register_view("os_vfs", "OS · VFS", self._v_vfs, "right", "D-")
        dm.register_view("os_mem", "OS · Speicher", self._v_mem, "right", "D-")
        dm.register_view("os_threads", "OS · Threads", self._v_threads, "right", "D-")
        dm.register_view("os_signals", "OS · Signale", self._v_signals, "right", "D-")
        dm.register_view("os_time", "OS · Zeit", self._v_time, "right", "D-")
        dm.register_view("os_net", "OS · Netz", self._v_net, "right", "D-")
        dm.register_view("os_proc", "OS · Prozess/auxv", self._v_proc, "right", "D-")
        dm.register_view("os_determinism", "OS · Determinismus", self._v_determinism, "right", "D-")
        dm.register_view("os_collab", "OS · Austausch (KI)", self._v_collab, "right", "D-")

    # ---- Helfer ------------------------------------------------------------
    def _tree(self, parent, cols, widths, heads, height=8, tag="os"):
        f = ttk.Frame(parent)
        tr = ttk.Treeview(f, columns=cols, show="headings", height=height)
        for c, w, h in zip(cols, widths, heads):
            tr.heading(c, text=h); tr.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(f, orient="vertical", command=tr.yview)
        tr.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y"); tr.pack(side="left", fill="both", expand=True)
        tr.tag_configure("deny", background="#ffd0d0")
        tr.tag_configure("over", background="#fff0c0")
        return f, tr

    def _fill(self, tr, rows):
        for i in tr.get_children():
            tr.delete(i)
        for vals, tag in rows:
            tr.insert("", "end", values=vals, tags=(tag,) if tag else ())

    def _bind_refresh(self, name, render):
        self._widgets[name] = render
        self.model.subscribe(render)
        render()

    # ---- Syscalls (Coverage + Trace + vereinheitlichte Policy) -------------
    def _v_syscalls(self, parent):
        m = self.model
        nb = ttk.Notebook(parent)

        # Coverage
        fc, cov = self._tree(nb, ("name", "count"), (160, 70), ("Syscall", "Aufrufe"))
        nb.add(fc, text="Coverage")

        # Trace
        ft, trc = self._tree(nb, ("seq", "name", "args", "ret"), (40, 130, 160, 80),
                             ("#", "Syscall", "args", "ret"), height=10)
        nb.add(ft, text="Trace")

        # Policy (vereinheitlicht: OS-9 allow/deny/stub + NR-Seed-Rueckgabe)
        fp, pol = self._tree(nb, ("name", "os9", "override_ret", "nr_ret"),
                             (140, 90, 90, 90), ("Syscall", "OS-9", "Override-ret", "NR-ret"))
        pol.bind("<Double-1>", lambda e: self._edit_policy(pol))
        bar = ttk.Frame(fp)
        ttk.Button(bar, text="+ Policy", command=lambda: self._edit_policy(pol, add=True)).pack(side="left")
        bar.pack(side="bottom", fill="x")
        nb.add(fp, text="Policy")

        def render():
            self._fill(cov, [((n, c), None) for n, c in m.coverage_rows()])
            self._fill(trc, [((i + 1, r.get("n"), str(r.get("args")), r.get("ret")),
                              ("deny" if r.get("denied") else ("over" if r.get("stubbed") else None)))
                             for i, r in enumerate(m.trace_rows())])
            prows = []
            for r in m.policy_rows():
                tag = "deny" if r["os9"] == "deny" else ("over" if (r["override_ret"] != "" or r["os9"] == "stub") else None)
                prows.append(((r["name"], r["os9"], r["override_ret"], r["nr_ret"]), tag))
            self._fill(pol, prows)

        nb.pack(fill="both", expand=True)
        self._bind_refresh("syscalls", render)
        return nb

    def _edit_policy(self, tree, add=False):
        name = None
        if not add:
            sel = tree.selection()
            if sel:
                name = tree.item(sel[0], "values")[0]
        if name is None:
            name = simpledialog.askstring("Policy", "Syscall-Name (z.B. ptrace):")
        if not name:
            return
        act = simpledialog.askstring("Policy", "Aktion: allow | deny | stub (leer=loeschen):", initialvalue="deny")
        self.model.set_policy(name, (act or "").strip())

    # ---- restliche Flaechen (gefuellt in G-OS-2..4; rendern schon jetzt) ---
    def _v_vfs(self, parent):
        f, tr = self._tree(parent, ("name", "typ"), (200, 80), ("Pfad", "Typ"))
        tr.bind("<Double-1>", lambda e: self._peek_vfs(tr))
        bar = ttk.Frame(f)
        ttk.Button(bar, text="+ Mount/Override", command=self._mount_vfs).pack(side="left")
        ttk.Button(bar, text="Peek", command=lambda: self._peek_vfs(tr)).pack(side="left")
        bar.pack(side="bottom", fill="x")
        self._bind_refresh("vfs", lambda: self._fill(tr, [((n, k), None) for n, k in self.model.vfs_rows()]))
        return f

    def _mount_vfs(self):
        path = simpledialog.askstring("VFS", "Pfad (z.B. /sys/fs/selinux/enforce):")
        if not path:
            return
        content = simpledialog.askstring("VFS", "Inhalt (Text):", initialvalue="0")
        self.model.set_vfs(path, (content or "").encode("utf-8"))

    def _peek_vfs(self, tr):
        sel = tr.selection()
        if not sel or self.reader is None or not hasattr(self.reader, "read_file"):
            return
        name = tr.item(sel[0], "values")[0]
        path = "/" + str(name)
        try:
            data = self.reader.read_file(path) or b""
        except Exception:
            data = b""
        self._show_hex("VFS: " + path, data)

    def _show_hex(self, title, data):
        w = tk.Toplevel(); w.title(title)
        t = tk.Text(w, wrap="none", font=("Consolas", 9), width=72, height=20)
        t.pack(fill="both", expand=True)
        lines = []
        for off in range(0, len(data), 16):
            chunk = data[off:off + 16]
            hexs = " ".join("%02x" % b for b in chunk)
            asci = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append("%08x  %-47s  %s" % (off, hexs, asci))
        t.insert("1.0", "\n".join(lines) or "(leer)")
        t.configure(state="disabled")

    def _v_mem(self, parent):
        f, tr = self._tree(parent, ("base", "size", "prot", "fd"), (140, 90, 50, 50),
                           ("Basis", "Groesse", "prot", "fd"))
        bar = ttk.Frame(f)
        av = tk.StringVar(value="0x")
        ttk.Label(bar, text="addr:").pack(side="left")
        ttk.Entry(bar, textvariable=av, width=12).pack(side="left")
        ttk.Button(bar, text="Hex-Peek", command=lambda: self._peek_mem(av.get())).pack(side="left", padx=2)
        ttk.Button(bar, text="Patch", command=lambda: self._patch_mem(av.get())).pack(side="left")
        bar.pack(side="bottom", fill="x")
        self._bind_refresh("mem", lambda: self._fill(
            tr, [((r.get("base"), r.get("size"), r.get("prot"), r.get("fd")), None) for r in self.model.mmap_rows()]))
        return f

    def _peek_mem(self, addr_s):
        if self.reader is None or not hasattr(self.reader, "read_bytes"):
            return
        try:
            addr = int(str(addr_s), 16)
            data = self.reader.read_bytes(addr, 256) or b""
        except Exception:
            data = b""
        self._show_hex("Speicher @%s" % addr_s, data)

    def _patch_mem(self, addr_s):
        if self.reader is None or not hasattr(self.reader, "patch_bytes"):
            return
        hexs = simpledialog.askstring("Patch", "Bytes (hex, z.B. 90 90 90):")
        if not hexs:
            return
        try:
            addr = int(str(addr_s), 16)
            data = bytes(int(x, 16) for x in hexs.split())
            self.reader.patch_bytes(addr, data)
        except Exception:
            pass

    def _v_threads(self, parent):
        f, tr = self._tree(parent, ("tid", "flags"), (90, 120), ("tid", "flags"))
        self._bind_refresh("threads", lambda: self._fill(
            tr, [((r.get("tid"), r.get("flags")), None) for r in self.model.thread_rows()]))
        return f

    def _v_signals(self, parent):
        f, tr = self._tree(parent, ("sig", "handler"), (60, 160), ("Signal", "Handler"))
        self._bind_refresh("signals", lambda: self._fill(
            tr, [((r.get("sig"), r.get("handler")), None) for r in self.model.signal_rows()]))
        return f

    def _v_time(self, parent):
        f = ttk.Frame(parent)
        lbl = ttk.Label(f, text="virtuelle Uhr —"); lbl.pack(side="top", anchor="w")
        row = ttk.Frame(f); row.pack(side="top", fill="x")
        ttk.Label(row, text="advance (ns):").pack(side="left")
        v = tk.StringVar(value="1000000")
        ttk.Entry(row, textvariable=v, width=12).pack(side="left")
        ttk.Button(row, text="Vorstellen", command=lambda: self.model.set_clock_advance(int(v.get() or 0))).pack(side="left", padx=4)
        return f

    def _v_net(self, parent):
        f, tr = self._tree(parent, ("fd", "peer", "tx", "rx"), (40, 150, 50, 50),
                           ("fd", "Peer", "tx", "rx"))
        bar = ttk.Frame(f)
        ttk.Button(bar, text="+ Replay", command=self._edit_replay).pack(side="left")
        bar.pack(side="bottom", fill="x")
        self._bind_refresh("net", lambda: self._fill(
            tr, [((r.get("fd"), str(r.get("peer")), r.get("tx"), r.get("rx")), None) for r in self.model.socket_rows()]))
        return f

    def _edit_replay(self):
        key = simpledialog.askstring("Replay", "host:port")
        if not key:
            return
        data = simpledialog.askstring("Replay", "Antwort (Text):", initialvalue="")
        self.model.set_net_replay(key, (data or "").encode("utf-8"))

    def _v_proc(self, parent):
        f, tr = self._tree(parent, ("typ", "wert"), (80, 160), ("auxv-Typ", "Wert"))
        self._bind_refresh("proc", lambda: self._fill(
            tr, [((t, v), None) for t, v in self.model.auxv_rows()]))
        return f

    def _v_determinism(self, parent):
        f = ttk.Frame(parent)
        row = ttk.Frame(f); row.pack(side="top", fill="x")
        ttk.Label(row, text="rand_seed:").pack(side="left")
        sv = tk.StringVar(value="")
        ttk.Entry(row, textvariable=sv, width=14).pack(side="left")
        ttk.Button(row, text="Setzen", command=lambda: self.model.set_seed(int(sv.get() or 0))).pack(side="left", padx=4)
        row2 = ttk.Frame(f); row2.pack(side="top", fill="x")
        tv = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="Trace an", variable=tv,
                        command=lambda: self.model.set_trace(tv.get())).pack(side="left")
        row3 = ttk.Frame(f); row3.pack(side="top", fill="x")
        mv = tk.StringVar(value="record")
        ttk.Label(row3, text="net_mode:").pack(side="left")
        ttk.Combobox(row3, textvariable=mv, values=["record", "replay", "off"], width=8,
                     state="readonly").pack(side="left")
        ttk.Button(row3, text="Setzen", command=lambda: self.model.set_net_mode(mv.get())).pack(side="left", padx=4)
        return f

    # ---- Mensch <-> KI (G-OS-5) -------------------------------------------
    def _os_folder(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(root, "secrets", "probe", "out", "os")

    def _v_collab(self, parent):
        f = ttk.Frame(parent)
        ttk.Label(f, text="Geteilter OS-Zustand (eine Wahrheit, seq/writer/Clobber)").pack(
            side="top", anchor="w", pady=(2, 4))
        row = ttk.Frame(f); row.pack(side="top", fill="x")
        ttk.Button(row, text="⬆ OS→KI", command=self._collab_push).pack(side="left", padx=2)
        ttk.Button(row, text="⬇ OS←KI", command=self._collab_pull).pack(side="left", padx=2)
        ttk.Button(row, text="⇅ Abgleich", command=self._collab_status).pack(side="left", padx=2)
        self._collab_lbl = ttk.Label(f, text="—"); self._collab_lbl.pack(side="top", anchor="w", pady=(4, 0))
        return f

    def _collab_svc(self):
        from services.native_os_session_service import NativeOsSessionService
        return NativeOsSessionService()

    def _collab_say(self, msg):
        try:
            self._collab_lbl.config(text=msg)
        except Exception:
            pass
        try:
            self.ctrl.app.log("[OS-Austausch] " + msg)
        except Exception:
            pass

    def _collab_push(self):
        r = self._collab_svc().save(self._os_folder(), config=self.model.build_config(),
                                    writer="gui", base_seq=self._base_seq)
        if not r.get("ok"):
            messagebox.showwarning("OS→KI", "Konflikt: Datei seq=%s (writer=%s). Erst ⇅ Abgleich."
                                   % (r.get("file_seq"), r.get("file_writer")))
            return
        self._base_seq = r["seq"]
        self._collab_say("gepusht (seq=%s)" % r["seq"])

    def _collab_pull(self):
        sess = self._collab_svc().peek(self._os_folder()).get("session")
        if not sess:
            self._collab_say("kein KI-Zustand vorhanden")
            return
        self.model.load_config(sess.get("config") or {})
        if sess.get("summary"):
            self.model.ingest(sess["summary"])
        self._base_seq = int(sess.get("seq", 0) or 0)
        self._collab_say("gezogen (seq=%s, writer=%s)" % (sess.get("seq"), sess.get("writer")))

    def _collab_status(self):
        sess = self._collab_svc().peek(self._os_folder()).get("session") or {}
        fseq = int(sess.get("seq", 0) or 0)
        rel = "aktuell" if fseq == self._base_seq else ("KI neuer (⬇)" if fseq > self._base_seq else "GUI neuer (⬆)")
        self._collab_say("Datei seq=%s writer=%s | lokal base=%s -> %s"
                         % (fseq, sess.get("writer"), self._base_seq, rel))
