# -*- coding: utf-8 -*-
"""core.java.class_provider — Lazy-Klassenquelle fuer die DalvikVM (X1-Erweiterung, „Bereich debuggen").

Die VM zieht fehlende App-Klassen bei `invoke` ueber einen ClassProvider nach. Dadurch ist
„eine Funktion samt aller aufgerufenen Funktionen debuggen" identisch zu „ganze App debuggen":
derselbe Mechanismus, nur ein anderer Einstieg. Zielagnostisch.

Zwei Quellen:
  - TextClassProvider: aus eingefuegtem Smali-Text (ein .class- oder mehrere .method-Bloecke).
  - StudioClassProvider: aus dem Smali-Baum des Studios. `root` = entpacktes App-Verzeichnis EINER
    Zone; Original-Wurzel vs. Destination-Wurzel (gepatcht) ist nur „welches Verzeichnis". `zone`
    ist nur ein Label. Descriptor `Lcom/foo/Bar;` -> Datei `<root>/<smali*>/com/foo/Bar.smali`.

Vertrag: `get_class(descriptor) -> Liste[MethodDef] | None` (lazy, Cache-Miss-getrieben).
"""
from __future__ import annotations

import glob
import os

from core.java import smali as Sm


def _dedupe(mds):
    if not mds:
        return None
    seen, out = set(), []
    for md in mds:
        k = (md.cls, md.name, md.sig)
        if k not in seen:
            seen.add(k)
            out.append(md)
    return out or None


class ClassProvider:
    """Basisklasse. Liefert die Methoden einer Klasse bei Bedarf, sonst None."""

    def get_class(self, descriptor):  # pragma: no cover - abstrakt
        raise NotImplementedError

    def get_super(self, descriptor):
        return None

    def get_interfaces(self, descriptor):
        return []


class TextClassProvider(ClassProvider):
    """Klassenquelle aus eingefuegtem Smali-Text (.class oder einzelne .method-Bloecke)."""

    def __init__(self, smali_text):
        self._by_cls = {}
        self._supers = {}
        self._ifaces = {}
        try:
            if ".class" in (smali_text or ""):
                clsname, mds = Sm.parse_class(smali_text)
                sup = Sm.parse_super(smali_text)
                if sup:
                    self._supers[clsname] = sup
                ifaces = Sm.parse_interfaces(smali_text)
                if ifaces:
                    self._ifaces[clsname] = ifaces
            else:
                mds = [Sm.parse_method(smali_text)] if smali_text and smali_text.strip() else []
            for md in mds:
                self._by_cls.setdefault(md.cls, []).append(md)
        except Exception:
            pass

    def get_class(self, descriptor):
        return _dedupe(self._by_cls.get(descriptor))

    def get_super(self, descriptor):
        return self._supers.get(descriptor)

    def get_interfaces(self, descriptor):
        return list(self._ifaces.get(descriptor, ()))


class StudioClassProvider(ClassProvider):
    """Klassenquelle aus dem Smali-Baum. `root` = App-Verzeichnis einer Zone (original|patched),
    `smali_dirs` optional explizit (sonst alle `smali*`-Unterordner von root). Lazy + Cache.
    Unterstuetzt ein In-Memory-Patch-Overlay (`self.patches`) fuer Live-Studio-Iteration."""

    def __init__(self, root, smali_dirs=None, zone="original"):
        self.root = root
        self.zone = zone
        self._dirs = smali_dirs
        self._cache = {}
        self._iface_cache = {}
        self.errors = {}   # descriptor -> Fehlertext (Diagnose)
        self.patches = {}  # normierter relpath oder Deskriptor -> kompletter Smali-Text

    def _roots(self):
        if self._dirs is not None:
            return list(self._dirs)
        ds = [d for d in sorted(glob.glob(os.path.join(self.root, "smali*"))) if os.path.isdir(d)]
        return ds or [self.root]

    @staticmethod
    def _desc_from_rel(rel):
        r = str(rel or "").replace("\\", "/")
        if not r.endswith(".smali"):
            return None
        parts = r[:-6].split("/")
        if parts and parts[0].startswith("smali"):
            parts = parts[1:]
        return ("L" + "/".join(parts) + ";") if parts else None

    @staticmethod
    def _extract_class_desc(smali_text):
        for ln in (smali_text or "").splitlines():
            st = ln.strip()
            if st.startswith(".class"):
                toks = st.split()
                if toks and toks[-1].startswith("L") and toks[-1].endswith(";"):
                    return toks[-1]
                break
        return None

    def _invalidate(self, descriptor=None):
        if descriptor is None:
            self._cache.clear()
            if hasattr(self, "_super_cache"):
                self._super_cache.clear()
            if hasattr(self, "_iface_cache"):
                self._iface_cache.clear()
            if hasattr(self, "_fd_cache"):
                self._fd_cache.clear()
            return
        self._cache.pop(descriptor, None)
        if hasattr(self, "_super_cache"):
            self._super_cache.pop(descriptor, None)
        if hasattr(self, "_iface_cache"):
            self._iface_cache.pop(descriptor, None)
        if hasattr(self, "_fd_cache"):
            self._fd_cache.pop(descriptor, None)

    def _candidate_files(self, descriptor):
        if not (descriptor.startswith("L") and descriptor.endswith(";")):
            return []
        rel = descriptor[1:-1].split("/")
        return [os.path.join(d, *rel) + ".smali" for d in self._roots()]

    def _candidate_rels(self, descriptor):
        if not (descriptor.startswith("L") and descriptor.endswith(";")):
            return []
        sub = descriptor[1:-1] + ".smali"
        out = [sub, f"smali/{sub}"]
        for d in self._roots():
            base = os.path.basename(d)
            if base.startswith("smali"):
                out.append(f"{base}/{sub}")
        return out

    def _read_source(self, descriptor):
        """Liefert `(source_text, origin)` fuer `descriptor`, Patch-Overlay hat Vorrang."""
        if descriptor in self.patches:
            return self.patches[descriptor], descriptor
        for rel in self._candidate_rels(descriptor):
            if rel in self.patches:
                return self.patches[rel], rel
        for k, val in list(self.patches.items()):
            norm = str(k).replace("\\", "/")
            if norm.endswith("/" + descriptor[1:-1] + ".smali") or norm == descriptor[1:-1] + ".smali":
                return val, k
        for p in self._candidate_files(descriptor):
            if os.path.isfile(p):
                try:
                    with open(p, encoding="utf-8", errors="replace") as fh:
                        return fh.read(), p
                except Exception as e:
                    self.errors[descriptor] = "%s: %r" % (p, e)
                    continue
        return None, None

    def get_source(self, descriptor):
        """Kompletter Smali-Quelltext fuer `descriptor` (inkl. Live-Patch-Overlay) oder None."""
        src, _origin = self._read_source(descriptor)
        return src

    def set_patch(self, rel_or_desc, smali_source):
        """Hinterlegt ein In-Memory-Overlay fuer `rel_or_desc` und invalidiert Caches."""
        key = str(rel_or_desc or "").replace("\\", "/")
        if not key:
            return None
        self.patches[key] = smali_source
        desc = key if (key.startswith("L") and key.endswith(";")) else (
            self._extract_class_desc(smali_source) or self._desc_from_rel(key)
        )
        if desc:
            self.patches[desc] = smali_source
            self._invalidate(desc)
        return desc

    def clear_patches(self):
        self.patches.clear()
        self._invalidate(None)

    def apply_smali_patches(self, patches_list):
        """Wendet Session-Patches (`smali` / `new_file`) in-memory im Provider an. Rueckgabe: Anzahl."""
        from core.java.sandbox_patch import apply_smali_patches as _apply_text
        applied = 0
        for p in list(patches_list or ()):
            if not isinstance(p, dict):
                continue
            ptype = (p.get("type") or p.get("scope") or "smali").lower()
            rel = str(p.get("file") or "").replace("\\", "/")
            if not rel:
                continue
            if ptype == "new_file" or p.get("scope") == "new_file":
                txt = p.get("edit", "")
                if txt:
                    self.set_patch(rel, txt)
                    applied += 1
                continue
            if ptype in ("hex", "lib_replace"):
                continue
            desc = self._desc_from_rel(rel)
            base_src = None
            if desc:
                base_src = self.get_source(desc)
            if base_src is None:
                cand = os.path.join(self.root, rel) if self.root else ""
                if cand and os.path.isfile(cand):
                    with open(cand, encoding="utf-8", errors="replace") as fh:
                        base_src = fh.read()
            if base_src is None and rel in self.patches:
                base_src = self.patches[rel]
            if base_src is None:
                continue
            patched_src, n = _apply_text(base_src, [p], file=rel)
            if n:
                self.set_patch(rel, patched_src)
                applied += 1
        return applied

    def get_class(self, descriptor):
        if descriptor in self._cache:
            return self._cache[descriptor]
        mds = None
        src, origin = self._read_source(descriptor)
        if src is not None:
            try:
                _clsname, parsed = Sm.parse_class(src)
                mds = _dedupe(parsed)
            except Exception as e:
                self.errors[descriptor] = "%s: %r" % (origin or descriptor, e)
        self._cache[descriptor] = mds
        return mds

    def get_super(self, descriptor):
        """Superklasse der Klasse (.super). Lazy + Cache; None wenn unbekannt."""
        sc = getattr(self, "_super_cache", None)
        if sc is None:
            sc = self._super_cache = {}
        if descriptor in sc:
            return sc[descriptor]
        out = None
        src, _origin = self._read_source(descriptor)
        if src is not None:
            try:
                out = Sm.parse_super(src)
            except Exception:
                out = None
        sc[descriptor] = out
        return out

    def get_interfaces(self, descriptor):
        """Implementierte Interface-Deskriptoren (.implements). Lazy + Cache."""
        ic = getattr(self, "_iface_cache", None)
        if ic is None:
            ic = self._iface_cache = {}
        if descriptor in ic:
            return list(ic[descriptor])
        out = []
        src, _origin = self._read_source(descriptor)
        if src is not None:
            try:
                out = Sm.parse_interfaces(src)
            except Exception:
                out = []
        ic[descriptor] = out
        return list(out)

    def get_field_defaults(self, descriptor):
        """Statische Feld-Defaults der Klasse (Dalvik: vor <clinit> gesetzt). Lazy + Cache."""
        fd = getattr(self, "_fd_cache", None)
        if fd is None:
            fd = self._fd_cache = {}
        if descriptor in fd:
            return fd[descriptor]
        out = {}
        src, _origin = self._read_source(descriptor)
        if src is not None:
            try:
                out = Sm.parse_field_defaults(src)
            except Exception:
                out = {}
        fd[descriptor] = out
        return out
