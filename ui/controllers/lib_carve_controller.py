# -*- coding: utf-8 -*-
"""ui.controllers.lib_carve_controller — Controller fuer LibCarve (C/ASM/ELF Patch Studio)."""
from __future__ import annotations

import json
import os
import re
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
from typing import Any, Dict, List, Optional

from core.application.session_state import SessionState
from core.native.engine_arm64 import Arm64Decoder
from core.native.loader import ElfImage
from core.native.nindex import NativeIndex
from core.native.unpack import sha8
from services.favorite_service import get_shared_favorites
from services.ghidra_service import GhidraService
from services.libcarve_compiler_service import LibCarveCompilerService
from services.libcarve_pattern_service import LibCarvePatternService
from services.native_annotations_service import NativeAnnotationsService


def _safe_pkg(value: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in str(value or "").strip())
    return clean.strip("._") or "default.app"


class LibCarveController:
    def __init__(self, view, app):
        self.view = view
        self.app = app
        self.image: Optional[ElfImage] = None
        self.image_path: str = ""
        self.image_abi: str = "arm64-v8a"
        self.image_sha: str = ""
        self.index: Optional[NativeIndex] = None
        self._indexing: bool = False
        self._decompiling: bool = False
        self._ghidra_svc: Optional[GhidraService] = None

        self.current_vaddr: Optional[int] = None
        self.current_func_name: str = ""
        self.current_func_bounds = (0, 0)
        self.editing_patch_idx: Optional[int] = None

        # Branching Historie (analog SmaliStudioController)
        self.history_nodes: Dict[str, dict] = {}
        self.history_root_id: Optional[str] = None
        self.current_history_id: Optional[str] = None
        self.history_counter: int = 0

        # Call Graph State
        self._cg_nodes: Dict[str, dict] = {}     # item_iid -> {addr, name}
        self._cg_stop: bool = False
        self._cg_hits: List[str] = []
        self._cg_hit_idx: int = -1
        self._search_win = None

        # Snippets (streng IP-neutral)
        self.snippets = {
            "ARM64 Standard-Stubs": {
                "Return 1 / True (mov w0,#1; ret)": "    mov w0, #1\n    ret",
                "Return 0 / False (mov w0,#0; ret)": "    mov w0, #0\n    ret",
                "Void Return (ret)": "    ret",
                "NOP-Block (4x nop)": "    nop\n    nop\n    nop\n    nop",
            },
            "C-Cave Vorlagen": {
                "Minimal-Hook (C)": (
                    "#include <stdint.h>\n\n"
                    "int64_t carve_entry(int64_t x0, int64_t x1, int64_t x2, int64_t x3) {\n"
                    "    return 1;\n"
                    "}\n"
                ),
                "Original-Aufruf Wrapper (C)": (
                    "#include <stdint.h>\n\n"
                    "// Hilfsfunktion: typedef int64_t (*orig_fn_t)(int64_t, int64_t);\n"
                    "int64_t carve_entry(int64_t x0, int64_t x1, int64_t x2, int64_t x3) {\n"
                    "    return x0;\n"
                    "}\n"
                ),
            },
        }

    # ---- Hilfsfunktionen ---------------------------------------------------
    def _base_dir(self) -> str:
        try:
            return self.app.cfg.config.get("BASE_DIR") or os.getcwd()
        except Exception:
            return os.getcwd()

    def _pkg(self) -> str:
        try:
            return self.app.cfg.config.get("APP_PACKAGE") or "default.app"
        except Exception:
            return "default.app"

    def _cfg_dict(self) -> dict:
        try:
            return dict(self.app.cfg.config or {})
        except Exception:
            return {}

    def _log(self, msg: str) -> None:
        try:
            self.app.log(f"[LibCarve] {msg}")
        except Exception:
            print(f"[LibCarve] {msg}")

    def _anno_service(self) -> NativeAnnotationsService:
        p = os.path.join(self._base_dir(), "data", "libcarve", _safe_pkg(self._pkg()), "annotations.json")
        return NativeAnnotationsService(p)

    def _caves_file(self) -> str:
        d = os.path.join(self._base_dir(), "data", "libcarve", _safe_pkg(self._pkg()))
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "_caves.json")

    def _disp_sym(self, addr: int, default_name: str) -> str:
        try:
            anno = self._anno_service().get(self.image_sha or "?", f"0x{addr:x}")
            if anno and anno.get("name"):
                return f"✎ {anno['name']}"
        except Exception:
            pass
        return default_name

    # ---- App-Wechsel & Lib-Discovery --------------------------------------
    def discover_libraries(self) -> List[Dict[str, str]]:
        """Listet alle .so Dateien aus entpackter APK und Laufzeit-Self-Cache."""
        libs: List[Dict[str, str]] = []
        seen = set()
        try:
            src_dir = self.app.cfg.paths.get("APP_SOURCE_DIR", "")
        except Exception:
            src_dir = ""
        if src_dir and os.path.isdir(src_dir):
            for unp in ("base_unpacked_apkeditor", "base_unpacked_apktool"):
                lib_root = os.path.join(src_dir, unp, "lib")
                if not os.path.isdir(lib_root):
                    continue
                for abi in sorted(os.listdir(lib_root)):
                    abi_dir = os.path.join(lib_root, abi)
                    if not os.path.isdir(abi_dir):
                        continue
                    for fn in sorted(os.listdir(abi_dir)):
                        if fn.endswith(".so"):
                            fp = os.path.join(abi_dir, fn)
                            if fp not in seen:
                                seen.add(fp)
                                libs.append({
                                    "label": f"[{abi}] {fn}",
                                    "path": fp,
                                    "abi": abi,
                                    "name": fn,
                                    "source": "apk",
                                })

        # Zusaetzlich im Self-Cache entpackte Bibliotheken
        sc_dir = os.path.join(self._base_dir(), "data", "vfs_selfcache", _safe_pkg(self._pkg()))
        if os.path.isdir(sc_dir):
            for fn in sorted(os.listdir(sc_dir)):
                if fn.endswith(".so"):
                    fp = os.path.join(sc_dir, fn)
                    if fp not in seen:
                        seen.add(fp)
                        libs.append({
                            "label": f"[self_cache] {fn}",
                            "path": fp,
                            "abi": "arm64-v8a",
                            "name": fn,
                            "source": "self_cache",
                        })
        return libs

    def on_app_changed(self, pkg=None):
        self.image = None
        self.image_path = ""
        self.image_sha = ""
        self.index = None
        self.current_vaddr = None
        self.current_func_name = ""
        self.editing_patch_idx = None
        self.history_nodes.clear()
        self.history_root_id = None
        self.current_history_id = None
        self.history_counter = 0
        try:
            self.view.refresh_discovered_libs()
            self.view.clear_views()
            self.refresh_caves_list()
            self.view.refresh_patch_tree()
        except Exception:
            pass

    def open_external_so(self):
        p = filedialog.askopenfilename(
            title="Native Bibliothek (.so) fuer LibCarve oeffnen",
            filetypes=[("Shared Libraries", "*.so"), ("Alle Dateien", "*.*")],
        )
        if p:
            self.load_so_path(p)

    def load_so_path(self, path: str, abi: str = "arm64-v8a", target_vaddr: Optional[int] = None,
                     on_ready=None):
        if not path or not os.path.isfile(path):
            messagebox.showwarning("LibCarve", f"Datei nicht gefunden:\n{path}")
            return False
        try:
            img = ElfImage.from_file(path)
        except Exception as e:
            messagebox.showerror("LibCarve", f"ELF64 konnte nicht geparst werden:\n{e}")
            return False

        self.image = img
        self.image_path = os.path.abspath(path)
        self.image_abi = abi or ("arm64-v8a" if img.is_aarch64 else "other")
        try:
            self.image_sha = sha8(self.image_path)
        except Exception:
            self.image_sha = "?"
        self.index = None
        self.current_vaddr = None
        self.current_func_name = ""
        self.view.set_status(f"Indexiere {os.path.basename(path)} …", "blue")
        self.view.set_file_label(f"{os.path.basename(path)} [{self.image_abi}] ({img.elf_summary()['size_bytes']:,} B)")
        self._populate_elf_info_tab()

        cache_dir = os.path.join(self._base_dir(), "data", "native", "_libcarve")

        def _worker():
            self._indexing = True
            idx = NativeIndex()
            try:
                idx.build(img, cache_dir=cache_dir, fast=True)
            except Exception as e:
                self._indexing = False
                self.app.after(0, lambda: self.view.set_status(f"Index-Fehler: {e!r}", "red"))
                return
            self._indexing = False
            self.index = idx

            def _ui_done():
                self.refresh_outline()
                self.refresh_caves_list()
                self.view.set_status(
                    f"Bereit: {len(idx.syms):,} Funktionen, {idx.count:,} Instr.",
                    "#2e7d32",
                )
                if target_vaddr is not None:
                    self.navigate_to(target_vaddr, add_to_history=True)
                elif idx.syms:
                    # Sinnvollen Startpunkt waehlen: JNI_OnLoad > erste Java_ > erste Funktion
                    first_addr = idx.syms[0][0]
                    for a, nm in idx.syms:
                        if nm == "JNI_OnLoad":
                            first_addr = a
                            break
                        if nm.startswith("Java_"):
                            first_addr = a
                            break
                    self.navigate_to(first_addr, add_to_history=True)
                if callable(on_ready):
                    try:
                        on_ready()
                    except Exception:
                        pass

            self.app.after(0, _ui_done)

        threading.Thread(target=_worker, daemon=True).start()
        return True

    # ---- Outline, Library Info & Navigation --------------------------------
    def refresh_outline(self, filter_text: str = ""):
        tr = self.view.tree_outline
        for i in tr.get_children():
            tr.delete(i)
        if self.image is None:
            return
        flt = (filter_text or "").strip().lower()
        cat = list(getattr(self.index, "func_catalog", ()) or ())
        if not cat and self.image.dynsyms:
            for nm, sym in sorted(self.image.dynsyms.items(), key=lambda kv: kv[1].value):
                if sym.value:
                    cat.append((sym.value, nm, "jni" if nm.startswith("Java_") else "export"))

        # Gruppieren nach Art (nicht blockierend, gekappt auf 6000 Eintraege pro Ansicht bei Riesen-Libs)
        cnt = 0
        for addr, name, kind in cat:
            disp = self._disp_sym(addr, name)
            if flt and flt not in disp.lower() and flt not in f"0x{addr:x}":
                continue
            tag = "jni" if kind == "jni" else ("sub" if kind == "sub" else "export")
            tr.insert("", "end", values=(kind.upper(), disp, f"0x{addr:x}"), tags=(tag, f"0x{addr:x}"))
            cnt += 1
            if cnt >= 6000:
                tr.insert("", "end", values=("INFO", f"… (Filter nutzen, insgesamt {len(cat):,} Eintraege)", ""), tags=("info",))
                break

        if not flt and getattr(self.index, "imports", None):
            for imp in self.index.imports[:400]:
                tr.insert("", "end", values=("IMP", imp, "PLT"), tags=("system_api",))

    def _populate_elf_info_tab(self):
        tr = self.view.tree_file
        for i in tr.get_children():
            tr.delete(i)
        if self.image is None:
            return
        info = self.image.elf_summary()
        rows = [
            ("ARCH", "AArch64 (arm64)" if info["is_aarch64"] else "Nicht-ARM64", "-"),
            ("ALIGN", "16 KB OK ✓" if info["is_16kb_aligned"] else f"Nur {info['max_align']} B (Warnung Android 15+)", "-"),
            ("SEC", "WX-Segment gefunden (!)" if info["has_wx"] else "NX OK ✓", "-"),
            ("ENTRY", "ELF Entrypoint", info["entry"]),
            ("JNI", "JNI_OnLoad", info["jni_onload"] or "nicht exportiert"),
            ("SYMS", f"Exports: {info['dynsym_count']} | Imports: {info['import_count']}", "-"),
            ("RELOC", f"Relokationen: {info['reloc_count']}", "-"),
        ]
        for typ, name, addr in rows:
            tr.insert("", "end", values=(typ, name, addr))
        for dep in info["needed"]:
            tr.insert("", "end", values=("NEED", dep, "DT_NEEDED"), tags=("system_api",))
        for sec_name, sec in self.image.sections.items():
            tr.insert("", "end", values=("SEC", f"{sec_name} ({sec['size']:,} B)", f"0x{sec['addr']:x}"), tags=("sec", f"0x{sec['addr']:x}"))

    def navigate_to(self, vaddr: int, *, add_to_history: bool = True, highlight_addr: Optional[int] = None):
        if self.image is None:
            return
        addr = int(vaddr)
        if self.index is not None:
            start, end = self.index.func_bounds(addr)
            sym = self.index.func_at(addr) or f"sub_{start:x}"
        else:
            start, end = addr, addr + 64 * 4
            sym = f"0x{addr:x}"

        disp_name = self._disp_sym(start, sym)
        self.current_vaddr = start
        self.current_func_name = sym
        self.current_func_bounds = (start, end)
        self.editing_patch_idx = None

        # Historie aktualisieren
        if add_to_history:
            new_id = f"lch_{self.history_counter}"
            self.history_counter += 1
            node = {
                "id": new_id,
                "lib": self.image_path,
                "vaddr": start,
                "func": disp_name,
                "parent": self.current_history_id,
                "children": [],
            }
            dup = False
            if self.current_history_id and self.current_history_id in self.history_nodes:
                par = self.history_nodes[self.current_history_id]
                if par["children"]:
                    last_id = par["children"][-1]
                    last = self.history_nodes[last_id]
                    if last["lib"] == node["lib"] and last["vaddr"] == node["vaddr"]:
                        self.current_history_id = last_id
                        self.history_counter -= 1
                        dup = True
            if not dup:
                self.history_nodes[new_id] = node
                if self.current_history_id and self.current_history_id in self.history_nodes:
                    self.history_nodes[self.current_history_id]["children"].append(new_id)
                elif not self.history_root_id:
                    self.history_root_id = new_id
                self.current_history_id = new_id
            self._refresh_history_tree()

        self.view.set_file_label(f"{os.path.basename(self.image_path)} -> {disp_name} @ 0x{start:x} ({max(0, end - start)} B)")
        self._render_function_code(start, end, sym, mark_addr=highlight_addr or addr)
        self._refresh_xrefs_and_graphs(start, end)

    def _render_function_code(self, start: int, end: int, sym: str, mark_addr: int):
        scope = self.view.editor.var_scope_mode.get() if hasattr(self.view.editor, "var_scope_mode") else "func"
        span = min(max(4, end - start), 8192 if scope == "func" else 32768)
        raw = self.image.read_vaddr(start, span)
        md = Arm64Decoder()._md
        asm_lines = [f"// {sym} @ 0x{start:x} .. 0x{start + len(raw):x} ({len(raw)} Bytes)"]
        if raw:
            for a, sz, mnem, op in md.disasm_lite(raw, start):
                mark = "▶ " if a == mark_addr else "  "
                bhex = raw[a - start:a - start + sz].hex(" ")
                asm_lines.append(f"{mark}0x{a:08x}:  {bhex:<11s} | {mnem:<8s} {op}")
            # Falls nicht alle Bytes dekodiert wurden
            if len(asm_lines) == 1:
                for i in range(0, len(raw), 4):
                    w = raw[i:i + 4]
                    asm_lines.append(f"  0x{start + i:08x}:  {w.hex(' '):<11s} | .word    0x{int.from_bytes(w, 'little'):08x}")
        else:
            asm_lines.append("// (Adresse liegt ausserhalb des Datei-Segments, z.B. .bss)")

        # Hex-Dump
        hex_lines = []
        for off in range(0, len(raw), 16):
            ch = raw[off:off + 16]
            hx = " ".join(f"{b:02x}" for b in ch)
            asc = "".join(chr(b) if 32 <= b < 127 else "." for b in ch)
            hex_lines.append(f"0x{start + off:08x}:  {hx:<47s}  |{asc}|")

        # Sofort-Pseudocode + gecachtes Ghidra-C pruefen
        c_text = self._cached_or_pseudo_c(start, sym, raw)
        self.view.editor.load_function_views(
            "\n".join(asm_lines),
            c_text=c_text,
            hex_text="\n".join(hex_lines),
            clear_edit=(self.editing_patch_idx is None),
        )

    def _ghidra_cache_dir(self) -> str:
        return os.path.join(self._base_dir(), "data", "native", "_libcarve")

    def _cached_or_pseudo_c(self, start: int, sym: str, raw: bytes) -> str:
        # 1) Pruefe gecachtes Ghidra-Decompilat
        if self.image_sha:
            cfile = os.path.join(self._ghidra_cache_dir(), "ghidra", self.image_sha, f"func_{start:x}.c")
            if os.path.isfile(cfile):
                try:
                    with open(cfile, "r", encoding="utf-8", errors="replace") as fh:
                        return fh.read()
                except Exception:
                    pass

        # 2) Sofortiger Pseudocode-Lifter aus Instruktions-Kontext (keine Wartezeit!)
        lines = [
            f"// Sofort-Pseudocode (klicke '⚡ Ghidra C dekompilieren' fuer vollstaendige Typrekonstruktion)",
            f"int64_t {re.sub(r'[^A-Za-z0-9_]', '_', sym)}(int64_t x0, int64_t x1, int64_t x2, int64_t x3) {{",
        ]
        md = Arm64Decoder()._md
        calls = []
        for a, _sz, mnem, op in md.disasm_lite(raw[:256], start):
            base = mnem.split(".", 1)[0].lower()
            if base == "bl":
                tgt_s = op.strip().lstrip("#")
                try:
                    ta = int(tgt_s, 16)
                    cname = self.index.func_at(ta) if self.index else f"0x{ta:x}"
                except Exception:
                    cname = op
                calls.append(f"    {cname}();  // @0x{a:x}")
            elif base == "ret":
                calls.append(f"    return x0;  // @0x{a:x}")
                break
        if calls:
            lines.extend(calls)
        else:
            lines.append("    // ...")
        lines.append("}")
        return "\n".join(lines)

    def decompile_current_with_ghidra(self):
        if self.image is None or self.current_vaddr is None:
            messagebox.showinfo("LibCarve", "Bitte zuerst eine Bibliothek und Funktion auswaehlen.")
            return
        if self._decompiling:
            if self._ghidra_svc:
                self._ghidra_svc.cancel()
            return
        vaddr = self.current_vaddr
        so_path = self.image_path
        ws_dir = self._ghidra_cache_dir()
        svc = GhidraService(self.app.cfg, log=self._log)
        if not svc.available():
            messagebox.showwarning(
                "Ghidra nicht gefunden",
                "Ghidra analyzeHeadless wurde nicht gefunden (tools/ghidra oder GHIDRA_HEADLESS).\n"
                "Die Sofort-Pseudocode- und ASM-Ansichten stehen weiterhin voll zur Verfuegung.",
            )
            return

        self._ghidra_svc = svc
        self._decompiling = True
        self.view.editor.btn_ghidra.config(text="⏹ Ghidra abbrechen")
        self.view.set_status(f"Ghidra dekompiliert 0x{vaddr:x} …", "#d97706")

        def _work():
            res = svc.decompile(so_path, vaddr, ws_dir)
            def _done():
                self._decompiling = False
                self._ghidra_svc = None
                try:
                    self.view.editor.btn_ghidra.config(text="⚡ Ghidra C dekompilieren")
                except Exception:
                    pass
                if res.get("ok") and res.get("c"):
                    self.view.editor.set_c_text(res["c"])
                    self.view.set_status(f"Ghidra C bereit ({'Cache' if res.get('cached') else 'neu'})", "#2e7d32")
                else:
                    why = res.get("why", "?")
                    self.view.set_status(f"Ghidra: {why}", "#b91c1c")
                    if res.get("errors"):
                        self._log("Ghidra-Fehler: " + " | ".join(res["errors"]))
            self.app.after(0, _done)

        threading.Thread(target=_work, daemon=True).start()

    # ---- XREF, Outgoing, DataGraph, Call Graph, Historie --------------------
    def _refresh_xrefs_and_graphs(self, start: int, end: int):
        # Incoming XREFs
        tr_inc = self.view.tree_incoming
        for i in tr_inc.get_children():
            tr_inc.delete(i)
        if self.index is not None:
            for src, mnem in self.index.xrefs_to(start, limit=400):
                cf = self.index.func_at(src) or f"0x{src:x}"
                cf = self._disp_sym(src, cf)
                tr_inc.insert("", "end", values=(f"0x{src:x}", f"{cf} ({mnem})"), tags=(f"0x{src:x}",))

        # Outgoing Calls
        tr_out = self.view.tree_outgoing
        for i in tr_out.get_children():
            tr_out.delete(i)
        if self.index is not None:
            for src, mnem, tgt, cname in self.index.outgoing_from(start, end, limit=400):
                c_disp = self._disp_sym(tgt, cname)
                tr_out.insert("", "end", values=(f"0x{src:x}", f"{mnem} -> {c_disp} (0x{tgt:x})"), tags=(f"0x{tgt:x}",))

        # Data Graph
        tr_dg = self.view.tree_datagraph
        for i in tr_dg.get_children():
            tr_dg.delete(i)
        if self.index is not None and self.image is not None:
            for ref in self.index.datagraph_for(self.image, start, end, limit=300):
                acc = ref["access"].upper()
                tag = "write" if acc == "WRITE" else "read"
                tr_dg.insert(
                    "", "end",
                    values=(f"{acc} ({ref['mnem']})", f"0x{ref['target']:x}  {ref['preview']}"),
                    tags=(tag, f"0x{ref['target']:x}"),
                )

    def find_incoming_xrefs(self):
        if self.index is None or self.current_vaddr is None:
            return
        self._refresh_xrefs_and_graphs(self.current_vaddr, self.current_func_bounds[1])
        self._log(f"XREFs fuer 0x{self.current_vaddr:x} aktualisiert.")

    # Call Graph Helpers
    def start_auto_explore(self):
        if self.index is None or self.current_vaddr is None:
            return
        self._cg_stop = False
        tr = self.view.tree_callstack
        for i in tr.get_children():
            tr.delete(i)
        self._cg_nodes.clear()
        root_addr = self.current_vaddr
        root_name = self._disp_sym(root_addr, self.current_func_name or f"sub_{root_addr:x}")
        root_iid = tr.insert("", "end", text=root_name, values=(f"0x{root_addr:x}",), open=True)
        self._cg_nodes[root_iid] = {"addr": root_addr, "name": root_name}
        self._expand_cg_node(root_iid, depth=2, seen={root_addr})

    def _expand_cg_node(self, parent_iid: str, depth: int = 1, seen: Optional[set] = None):
        if self.index is None or self._cg_stop or depth <= 0:
            return
        meta = self._cg_nodes.get(parent_iid)
        if not meta:
            return
        seen = seen if seen is not None else {meta["addr"]}
        fs, fe = self.index.func_bounds(meta["addr"])
        callees = self.index.outgoing_from(fs, fe, limit=120)
        added_targets = set()
        for _src, _mn, tgt, cname in callees:
            if tgt in added_targets:
                continue
            added_targets.add(tgt)
            disp = self._disp_sym(tgt, cname)
            iid = self.view.tree_callstack.insert(parent_iid, "end", text=disp, values=(f"0x{tgt:x}",))
            self._cg_nodes[iid] = {"addr": tgt, "name": disp}
            if depth > 1 and tgt not in seen:
                self._expand_cg_node(iid, depth=depth - 1, seen=seen | {tgt})

    def stop_auto_explore(self):
        self._cg_stop = True

    def clear_callgraph(self):
        for i in self.view.tree_callstack.get_children():
            self.view.tree_callstack.delete(i)
        self._cg_nodes.clear()
        self._cg_hits.clear()
        self._cg_hit_idx = -1

    def export_callgraph(self):
        if not self._cg_nodes:
            messagebox.showinfo("Call Graph Export", "Call Graph ist leer (erst Explore starten).")
            return
        dot_lines = ["digraph LibCarveCallGraph {", '  node [shape=box, fontname="Consolas"];']
        tr = self.view.tree_callstack
        def _walk(iid):
            p_lbl = tr.item(iid, "text")
            for ch in tr.get_children(iid):
                c_lbl = tr.item(ch, "text")
                dot_lines.append(f'  "{p_lbl}" -> "{c_lbl}";')
                _walk(ch)
        for top in tr.get_children(""):
            _walk(top)
        dot_lines.append("}")
        p = filedialog.asksaveasfilename(title="Call Graph als DOT speichern", defaultextension=".dot", filetypes=[("Graphviz DOT", "*.dot")])
        if p:
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("\n".join(dot_lines) + "\n")
            self._log(f"Call Graph exportiert: {p}")

    def apply_cg_filter(self):
        flt = (self.view.ent_cg_filter.get() or "").strip().lower()
        tr = self.view.tree_callstack
        self._cg_hits.clear()
        self._cg_hit_idx = -1
        for iid, meta in self._cg_nodes.items():
            if not tr.exists(iid):
                continue
            if not flt:
                tr.item(iid, tags=())
            elif flt in meta["name"].lower() or flt in f"0x{meta['addr']:x}":
                tr.item(iid, tags=("match_exact",))
                self._cg_hits.append(iid)
            else:
                tr.item(iid, tags=("dimmed",))
        self.view.lbl_cg_hits.config(text=f"0/{len(self._cg_hits)}")
        if self._cg_hits:
            self.next_cg_hit()

    def next_cg_hit(self):
        if not self._cg_hits:
            return
        self._cg_hit_idx = (self._cg_hit_idx + 1) % len(self._cg_hits)
        iid = self._cg_hits[self._cg_hit_idx]
        self.view.tree_callstack.selection_set(iid)
        self.view.tree_callstack.see(iid)
        self.view.lbl_cg_hits.config(text=f"{self._cg_hit_idx + 1}/{len(self._cg_hits)}")

    def prev_cg_hit(self):
        if not self._cg_hits:
            return
        self._cg_hit_idx = (self._cg_hit_idx - 1) % len(self._cg_hits)
        iid = self._cg_hits[self._cg_hit_idx]
        self.view.tree_callstack.selection_set(iid)
        self.view.tree_callstack.see(iid)
        self.view.lbl_cg_hits.config(text=f"{self._cg_hit_idx + 1}/{len(self._cg_hits)}")

    # Historie Navigation
    def navigate_back(self):
        if not self.current_history_id:
            return
        node = self.history_nodes.get(self.current_history_id)
        if node and node["parent"]:
            self.load_history_node(node["parent"])

    def navigate_forward(self):
        if not self.current_history_id:
            return
        node = self.history_nodes.get(self.current_history_id)
        if node and node["children"]:
            self.load_history_node(node["children"][-1])

    def load_history_node(self, node_id: str):
        node = self.history_nodes.get(node_id)
        if not node:
            return
        self.current_history_id = node_id
        if node["lib"] != self.image_path:
            self.load_so_path(node["lib"], target_vaddr=node["vaddr"])
        else:
            self.navigate_to(node["vaddr"], add_to_history=False)
            self._refresh_history_tree()

    def _refresh_history_tree(self):
        tr = self.view.tree_history
        for i in tr.get_children():
            tr.delete(i)
        if not self.history_root_id or self.history_root_id not in self.history_nodes:
            return

        def _rec(parent_iid, nid):
            nd = self.history_nodes.get(nid)
            if not nd:
                return
            lbl = f"{os.path.basename(nd['lib'])} -> {nd['func']} (0x{nd['vaddr']:x})"
            tags = ("current",) if nid == self.current_history_id else ("method",)
            iid = tr.insert(parent_iid, "end", text=lbl, values=(nd["lib"], f"0x{nd['vaddr']:x}", nid), open=True, tags=tags)
            for ch in nd["children"]:
                _rec(iid, ch)

        _rec("", self.history_root_id)

    def open_global_search(self):
        if self.image is None or self.index is None:
            messagebox.showinfo("Suche", "Bitte zuerst eine .so-Bibliothek laden.")
            return
        from ui.dialogs.native_search_dialog import NativeGlobalSearchWindow
        if self._search_win and self._search_win.winfo_exists():
            self._search_win.lift()
            return
        self._search_win = NativeGlobalSearchWindow(
            self.view.winfo_toplevel(), self.index, self.image,
            lambda addr: self.navigate_to(addr, add_to_history=True),
        )

    def rename_current_symbol(self):
        if self.image is None or self.current_vaddr is None:
            return
        cur = self.current_func_name or f"sub_{self.current_vaddr:x}"
        new_name = simpledialog.askstring("Symbol umbenennen", f"Neuer Name fuer 0x{self.current_vaddr:x}:", initialvalue=cur, parent=self.view)
        if not new_name:
            return
        note = simpledialog.askstring("Kommentar (optional)", "Notiz zur Funktion:", parent=self.view) or ""
        self._anno_service().set(self.image_sha or "?", f"0x{self.current_vaddr:x}", new_name.strip(), note.strip())
        self.refresh_outline(self.view.ent_outline_filter.get())
        self.navigate_to(self.current_vaddr, add_to_history=False)
        self._log(f"Symbol 0x{self.current_vaddr:x} umbenannt zu '{new_name.strip()}'.")

    # ---- Eigene Caves ------------------------------------------------------
    def refresh_caves_list(self):
        tr = self.view.tree_caves
        for i in tr.get_children():
            tr.delete(i)
        path = self._caves_file()
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                caves = json.load(fh)
        except Exception:
            caves = []
        for idx, cv in enumerate(caves if isinstance(caves, list) else ()):
            tr.insert("", "end", iid=str(idx), values=(cv.get("name", "?"), cv.get("file", ""), cv.get("vaddr", "")))

    def create_new_cave_dialog(self):
        name = simpledialog.askstring("Neue C-Cave", "Name der Cave-Vorlage (z. B. custom_hook):", parent=self.view)
        if not name:
            return
        c_code = self.view.editor.get_edit_text()
        if not c_code or self.view.editor.var_patch_mode.get() != "c_cave":
            c_code = self.snippets["C-Cave Vorlagen"]["Minimal-Hook (C)"]
        entry = {
            "name": name.strip(),
            "file": os.path.basename(self.image_path) if self.image_path else "",
            "vaddr": f"0x{self.current_vaddr:x}" if self.current_vaddr is not None else "",
            "code": c_code,
        }
        path = self._caves_file()
        cur = []
        if os.path.isfile(path):
            try:
                cur = json.load(open(path, "r", encoding="utf-8"))
            except Exception:
                cur = []
        cur.append(entry)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cur, fh, indent=2, ensure_ascii=False)
        self.refresh_caves_list()
        self.view.editor.var_patch_mode.set("c_cave")
        self.view.editor.set_edit_text(c_code)

    def load_cave_into_editor(self, idx_str: str):
        path = self._caves_file()
        if not os.path.isfile(path):
            return
        try:
            cur = json.load(open(path, "r", encoding="utf-8"))
            cv = cur[int(idx_str)]
            self.view.editor.var_patch_mode.set("c_cave")
            self.view.editor.set_edit_text(cv.get("code", ""))
        except Exception:
            pass

    # ---- Assemblieren, Patch-Liste, Favoriten & Compile --------------------
    def check_editor_budget(self):
        ed = self.view.editor
        code = ed.get_edit_text()
        if not code:
            ed.set_budget_status("Bytes: 0 (kein Edit)", "#666")
            return
        mode = ed.var_patch_mode.get()
        fs, fe = self.current_func_bounds
        orig_sz = max(0, fe - fs)
        if mode == "hex":
            hx = code.replace(" ", "")
            if len(hx) % 2 != 0 or not re.fullmatch(r"[0-9a-fA-F]+", hx):
                ed.set_budget_status("Hex ungültig!", "#c62828")
                return
            blen = len(hx) // 2
            ed.set_budget_status(f"Hex: {blen} Bytes (Orig: {orig_sz} B)", "#2e7d32" if not orig_sz or blen <= orig_sz else "#d97706")
            return
        if mode == "c_cave":
            ed.set_budget_status(f"C-Cave-Modus (4B-Trampolin @0x{self.current_vaddr or 0:x})", "#1565c0")
            return
        res = LibCarveCompilerService.assemble_arm64(
            code, vaddr=self.current_vaddr or 0, orig_size=orig_sz, auto_nop=True, cfg=self._cfg_dict()
        )
        if not res.get("ok"):
            ed.set_budget_status(f"Fehler: {str(res.get('error', '?'))[:48]}", "#c62828")
            return
        if res.get("needs_cave"):
            ed.set_budget_status(
                f"ASM {res['raw_len']}B > {orig_sz}B -> automatische Code-Cave!",
                "#d97706",
            )
        else:
            ed.set_budget_status(
                f"ASM OK ({res['backend']}): {res['raw_len']} / {orig_sz or res['raw_len']} B (+{res['nops_added']} NOPs)",
                "#2e7d32",
            )

    def _build_current_patch_dict(self, *, quiet: bool = False) -> Optional[dict]:
        """Erzeugt ein LibCarve-Patch-Dict inkl. AOB-Signatur aus dem aktuellen Editor."""
        if self.image is None or self.current_vaddr is None:
            if not quiet:
                messagebox.showinfo("LibCarve", "Bitte zuerst eine .so und eine Zieladresse laden.")
            return None
        edit_txt = self.view.editor.get_edit_text()
        if not edit_txt:
            if not quiet:
                messagebox.showinfo("LibCarve", "Der Patch-Editor unten ist leer.")
            return None

        mode = self.view.editor.var_patch_mode.get()
        fs, fe = self.current_func_bounds
        orig_sz = max(0, fe - fs)
        if mode == "asm":
            chk = LibCarveCompilerService.assemble_arm64(
                edit_txt, vaddr=self.current_vaddr, orig_size=orig_sz, auto_nop=True, cfg=self._cfg_dict()
            )
            if not chk.get("ok"):
                if not quiet:
                    messagebox.showerror("Assembler-Fehler", chk.get("error", "Unbekannt"))
                return None
        elif mode == "hex":
            try:
                bytes.fromhex(edit_txt.replace(" ", ""))
            except ValueError as e:
                if not quiet:
                    messagebox.showerror("Hex-Fehler", str(e))
                return None

        sig_info = LibCarvePatternService.generate_signature(self.image, self.index, self.current_vaddr, byte_len=32)
        return {
            "type": "libcarve",
            "file": os.path.basename(self.image_path),
            "abi": self.image_abi,
            "vaddr": f"0x{self.current_vaddr:x}",
            "func": self.current_func_name or f"sub_{self.current_vaddr:x}",
            "orig_size": orig_sz,
            "mode": mode,
            "auto_nop": True,
            "orig": self.view.editor.get_orig_text(),
            "edit": edit_txt,
            "orig_hex": sig_info.get("orig_hex", ""),
            "aob": sig_info.get("aob", ""),
            "anchor_strings": sig_info.get("anchor_strings", []),
            "rel_offset": sig_info.get("rel_offset", 0),
        }

    def add_libcarve_patch(self) -> Optional[dict]:
        patch = self._build_current_patch_dict(quiet=False)
        if patch is None:
            return None

        if self.editing_patch_idx is not None and 0 <= self.editing_patch_idx < len(SessionState.active_libcarve_patches):
            SessionState.active_libcarve_patches[self.editing_patch_idx] = patch
        else:
            SessionState.active_libcarve_patches.append(patch)

        self.editing_patch_idx = None
        self.view.editor.mark_clean()
        self.view.refresh_patch_tree()
        if hasattr(self.app, "persist_active_session"):
            self.app.persist_active_session()
        self._log(f"Patch zur Liste hinzugefuegt: {patch['file']} {patch['func']} ({patch['vaddr']}, {patch['mode']})")
        return patch

    def remove_libcarve_patch(self, idx: int):
        if 0 <= idx < len(SessionState.active_libcarve_patches):
            rem = SessionState.active_libcarve_patches.pop(idx)
            self.view.refresh_patch_tree()
            if hasattr(self.app, "persist_active_session"):
                self.app.persist_active_session()
            self._log(f"Patch entfernt: {rem.get('file')} {rem.get('vaddr')}")

    def save_current_as_favorite(self):
        patch = self.add_libcarve_patch()
        if patch is None:
            return
        name = simpledialog.askstring("Als Favorit speichern", "Name fuer den LibCarve-Favoriten:", parent=self.view)
        if not name or not name.strip():
            return
        fav = {
            "name": name.strip(),
            "version": "1.0",
            "description": f"[v1.0] LibCarve {patch['file']} {patch['func']}",
            "patches": [patch],
        }
        get_shared_favorites(self.app).add_favorite(fav)
        self._log(f"LibCarve-Favorit '{name.strip()}' mit Wildcard-AOB gespeichert.")

    def append_current_to_favorite(self):
        patch = self.add_libcarve_patch()
        if patch is None:
            return
        fav_svc = get_shared_favorites(self.app)
        if not fav_svc.favs:
            name = simpledialog.askstring("Als Favorit speichern", "Name fuer den LibCarve-Favoriten:", parent=self.view)
            if not name or not name.strip():
                return
            fav_svc.add_favorite({
                "name": name.strip(),
                "version": "1.0",
                "description": f"[v1.0] LibCarve {patch['file']} {patch['func']}",
                "patches": [patch],
            })
            return
        from ui.dialogs.favorite_patches_dialog import choose_existing_favorite_dialog
        idx = choose_existing_favorite_dialog(
            self.view.winfo_toplevel(), fav_svc, title="LibCarve-Patch zu Favorit hinzufügen"
        )
        if idx is None:
            return
        fav_svc.favs[idx].setdefault("patches", []).append(patch)
        fav_svc.save_favs()
        self._log(f"LibCarve-Patch an Favorit '{fav_svc.favs[idx].get('name', '')}' angehängt.")

    def save_workspace_bundle_as_favorite(self):
        ws = getattr(self.app, "workspace_tab", None)
        ctrl = getattr(ws, "controller", None)
        if ctrl is not None:
            ctrl.save_current_as_favorite()

    def open_favorites_manager(self):
        ws = getattr(self.app, "workspace_tab", None)
        if ws is None:
            return
        from ui.dialogs.favorite_patches_dialog import FavoritePatchesDialog
        FavoritePatchesDialog(self.view.winfo_toplevel(), ws, initial_filter="LibCarve")

    def open_native_fuzzy_matcher_for_patch(self, fav_patch: dict):
        if self.image is None or self.index is None:
            messagebox.showinfo("Pattern Matcher", "Bitte zuerst die Ziel-.so in LibCarve laden.")
            return
        from ui.dialogs.native_fuzzy_match_dialog import NativeFuzzyMatchDialog
        NativeFuzzyMatchDialog(self.view.winfo_toplevel(), self.app, self.view, fav_patch)

    def compile_active_lib(self):
        if self.image is None:
            messagebox.showinfo("LibCarve", "Bitte zuerst eine .so-Bibliothek laden.")
            return
        fname = os.path.basename(self.image_path)
        plist = [p for p in SessionState.active_libcarve_patches if os.path.basename(p.get("file", "")) == fname]
        if not plist:
            messagebox.showinfo("LibCarve", f"Keine aktiven LibCarve-Patches fuer '{fname}' vorhanden.")
            return
        try:
            ok, out_path, reports = LibCarveCompilerService.compile_to_workspace_cache(
                self._base_dir(), self._pkg(), self.image_abi, fname,
                self.image.data, plist, cfg=self._cfg_dict(),
            )
            fails = [r for r in reports if not r.get("ok")]
            if ok and not fails:
                self._log(f"Kompilierung OK -> {out_path} ({len(reports)} Patch(es))")
                messagebox.showinfo("LibCarve Compile", f"Bibliothek erfolgreich kompiliert:\n{out_path}\n({len(reports)} Patch(es))")
            else:
                msg = "\n".join(str(r.get("error")) for r in fails)
                self._log(f"Kompilierung mit Fehlern: {msg}")
                messagebox.showerror("LibCarve Compile Fehler", msg)
        except Exception as e:
            messagebox.showerror("LibCarve Compile", str(e))

    # ---- Debugger & Smali Studio Bruecken ----------------------------------
    def open_in_debugger(self):
        dbg = getattr(getattr(self.app, "debugger_tab", None), "ctrl", None)
        if dbg is None:
            return
        if self.image is not None:
            lib = os.path.basename(self.image_path)
            vaddr = self.current_vaddr or 0
            dbg.inspect_mode = "arm64"
            dbg._log(f"LibCarve Fokus: {lib} @ 0x{vaddr:x} ({self.current_func_name})")
        if hasattr(self.app, "notebook") and hasattr(self.app, "debugger_tab"):
            self.app.notebook.select(self.app.debugger_tab)

    def set_breakpoint_in_debugger(self):
        dbg = getattr(getattr(self.app, "debugger_tab", None), "ctrl", None)
        if dbg is None or self.image is None or self.current_vaddr is None:
            return
        lib = os.path.basename(self.image_path)
        if hasattr(dbg, "add_native_bp"):
            dbg.add_native_bp(lib, self.current_vaddr, self.current_func_name)
        else:
            dbg.native_bps.append({"lib": lib, "vaddr": self.current_vaddr, "name": self.current_func_name})
        self._log(f"Nativer Breakpoint im Debugger registriert: {lib}@0x{self.current_vaddr:x}")

    def run_live_in_simulator(self):
        dbg = getattr(getattr(self.app, "debugger_tab", None), "ctrl", None)
        if dbg is not None and hasattr(dbg, "run_live_with_patches"):
            dbg.run_live_with_patches()

    def jump_to_jni_smali(self):
        fn = self.current_func_name or ""
        if not fn.startswith("Java_"):
            messagebox.showinfo("JNI -> Smali", "Die aktuelle Funktion hat keinen Java_<pkg>_<cls>_<meth>-Namen.")
            return
        # Java_com_foo_Bar_baz -> Lcom/foo/Bar; + baz
        raw = fn[5:]
        parts = raw.split("__")[0].split("_")
        if len(parts) < 2:
            return
        meth = parts[-1]
        rel = "/".join(parts[:-1]) + ".smali"
        studio = getattr(getattr(self.app, "workspace_tab", None), "smali_studio", None)
        if studio is not None and hasattr(studio, "controller"):
            resolved = studio.controller.fs_service.resolve_smali_path(rel) if hasattr(studio.controller, "fs_service") else None
            if resolved:
                studio.controller.load_method(resolved, method_signature=meth, add_as_root=True)
                try:
                    ws = self.app.workspace_tab
                    # Notebook Tab von Smali Studio waehlen
                    self.view.master.select(studio)
                except Exception:
                    pass
                return
        messagebox.showinfo("JNI -> Smali", f"Smali-Klasse '{rel}' nicht aufgeloest (ggf. zuerst APK entpacken).")
