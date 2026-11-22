# -*- coding: utf-8 -*-
"""core.java.ui_mcp_bridge — JSON-sichere UI-Momentaufnahmen & Live-Sync zwischen GUI und MCP.

Entkoppelt das Serialisieren von ViewTree-/DisplayList-Zustaenden (inkl. Binaerbildern als
Base64, interaktiven Tap-Koordinaten, virtueller Uhrzeit, Dark-Mode, Fokus & Toasts), sodass
sowohl die Desktop-GUI (`DisplayTab` / `DebuggerController`) als auch Headless-MCP-Laeufe
dieselbe `last_ui.json`-Struktur schreiben und `tools/surface_raster.py` daraus echte PNGs baut.
"""
from __future__ import annotations

import base64
import json
import os
import time
from typing import Any, Optional

DEFAULT_SCREEN_W = 380
DEFAULT_SCREEN_H = 800
DEFAULT_STATUS_H = 24
DEFAULT_VIEW_H = DEFAULT_SCREEN_H - DEFAULT_STATUS_H

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_GLASS_OUT_DIR = os.path.join(_REPO_ROOT, "secrets", "probe", "out", "glass")


def _clean_text(val: Any) -> str:
    if val is None:
        return ""
    s = str(val)
    try:
        return s.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    except Exception:
        return s.encode("utf-8", "replace").decode("utf-8", "replace")


def serialize_paint_commands(cmds: Optional[list]) -> list[dict]:
    """Wandelt eine `paint_commands`-Liste in strikt JSON-serialisierbare Dicts um."""
    out: list[dict] = []
    for raw in (cmds or ()):
        if not isinstance(raw, dict):
            continue
        item: dict[str, Any] = {}
        for k, v in raw.items():
            if k == "data" and isinstance(v, (bytes, bytearray, memoryview)):
                item["data_b64"] = base64.b64encode(bytes(v)).decode("ascii")
            elif isinstance(v, (bytes, bytearray, memoryview)):
                item[k] = base64.b64encode(bytes(v)).decode("ascii")
            elif isinstance(v, tuple):
                item[k] = list(v)
            elif isinstance(v, str):
                item[k] = _clean_text(v)
            else:
                item[k] = v
        out.append(item)
    return out


def extract_interactive_elements(tree: Any, scroll_y: int = 0, view_h: int = DEFAULT_VIEW_H) -> list[dict]:
    """Extrahiert sichtbare klickbare/fokussierbare Elemente mit nummerierten Tap-Koordinaten."""
    if tree is None or getattr(tree, "root", None) is None:
        return []
    try:
        from core.java.view_tree import _is_fixed_y_node
    except Exception:
        _is_fixed_y_node = lambda _n: False
    nodes = tree.visible_nodes() if hasattr(tree, "visible_nodes") else tree.nodes()
    sy = int(scroll_y or 0)
    elements: list[dict] = []
    idx = 0
    for nd in nodes:
        if not getattr(nd, "visible", True):
            continue
        clickable = bool(getattr(nd, "clickable", False))
        focusable = bool(getattr(nd, "focusable", False))
        if not (clickable or focusable):
            continue
        bounds = getattr(nd, "bounds", (0, 0, 0, 0)) or (0, 0, 0, 0)
        bx, by, bw, bh = int(bounds[0]), int(bounds[1]), int(bounds[2]), int(bounds[3])
        if bw <= 0 or bh <= 0:
            continue
        idx += 1
        props = getattr(nd, "props", None) or {}
        vy = by if _is_fixed_y_node(nd) else (by - sy)
        tap_y = vy + (bh // 2)
        elements.append({
            "index": idx,
            "cls": str(getattr(nd, "cls", "") or ""),
            "id": getattr(nd, "vid", None),
            "text": _clean_text(getattr(nd, "text", None) or "")[:120],
            "hint": _clean_text(props.get("hint", "") or props.get("a11y_label", "") or "")[:80],
            "clickable": clickable,
            "focusable": focusable,
            "bounds": [bx, by, bw, bh],
            "viewport_bounds": [bx, vy, bw, bh],
            "tap_xy": [bx + (bw // 2), tap_y],
            "in_viewport": bool((vy + bh) > 0 and vy < view_h),
        })
    return elements


def _extract_toast_text(tree: Any) -> Optional[str]:
    if tree is None:
        return None
    explicit = getattr(tree, "_toast_text", None)
    if explicit:
        return _clean_text(explicit)
    toasts = getattr(tree, "_toasts", None)
    if toasts:
        last_t = toasts[-1]
        return _clean_text(last_t.get("text") if isinstance(last_t, dict) else last_t)
    last_toast = getattr(tree, "last_toast", None)
    if last_toast:
        return _clean_text(last_toast)
    stubs = getattr(tree, "_stubs_ref", None)
    st_toasts = getattr(stubs, "_toasts", None) if stubs is not None else None
    if st_toasts:
        last_s = st_toasts[-1]
        return _clean_text(last_s.get("text") if isinstance(last_s, dict) else last_s)
    return None


def build_ui_snapshot_dict(
    surface: Any = None,
    *,
    tree: Any = None,
    dl: Any = None,
    scroll_y: int = 0,
    max_scroll: Optional[int] = None,
    status_clock: Optional[str] = None,
    focused_bounds: Optional[list[int] | tuple[int, ...]] = None,
    toast_text: Optional[str] = None,
    package: str = "",
    source: str = "glass",
) -> dict[str, Any]:
    """Erstellt einen vollstaendigen, JSON-sicheren UI-Snapshot fuer MCP & Rasterizer."""
    if tree is None and surface is not None:
        tree = getattr(surface, "tree", None)

    raw_cmds = []
    width = int(getattr(surface, "width", DEFAULT_SCREEN_W) or DEFAULT_SCREEN_W)
    height = int(getattr(surface, "height", DEFAULT_SCREEN_H) or DEFAULT_SCREEN_H)

    if tree is not None and getattr(tree, "root", None) is not None:
        root_b = getattr(tree.root, "bounds", (0, 0, 0, 0)) or (0, 0, 0, 0)
        if root_b[2] <= 0 or root_b[3] <= 0:
            if not getattr(tree, "width", None):
                tree.width = DEFAULT_SCREEN_W
            if not getattr(tree, "height", None):
                tree.height = DEFAULT_VIEW_H
            tree.layout()
            root_b = getattr(tree.root, "bounds", (0, 0, 0, 0)) or (0, 0, 0, 0)
        view_h_eff = int(getattr(tree, "height", None) or DEFAULT_VIEW_H)
        width = int(getattr(tree, "width", None) or width)
        if max_scroll is None:
            content_h = int(root_b[3])
            max_scroll = max(0, content_h - view_h_eff)
        if surface is not None:
            raw_cmds = surface.paint()
        else:
            from core.java.view_tree import paint_commands
            raw_cmds = paint_commands(tree)
    elif surface is not None:
        raw_cmds = surface.paint()
    elif dl is not None:
        raw_cmds = dl.paint()

    sy = max(0, int(scroll_y or 0))
    ms = max(0, int(max_scroll or 0))
    if sy > ms and ms > 0:
        sy = ms

    paint = serialize_paint_commands(raw_cmds)
    texts = [_clean_text(c.get("text")) for c in paint if c.get("op") == "text" and c.get("text")]

    dark_mode = bool(
        (tree.effective_dark() if hasattr(tree, "effective_dark") else getattr(tree, "dark_mode", False))
        if tree is not None else False
    )
    window_bg = getattr(tree, "window_bg", None) if tree is not None else None
    window_bg_dark = bool(getattr(tree, "window_bg_dark", False)) if tree is not None else False

    stubs = getattr(tree, "_stubs_ref", None) if tree is not None else None
    if status_clock is None:
        if stubs is not None and hasattr(stubs, "format_status_clock"):
            status_clock = str(stubs.format_status_clock())
        else:
            status_clock = "12:00"

    if not package and stubs is not None:
        package = str(getattr(getattr(stubs, "profile", None), "package", "") or "")

    if toast_text is None:
        toast_text = _extract_toast_text(tree)

    foc_b = list(focused_bounds) if focused_bounds is not None else None
    if foc_b is None and tree is not None:
        foc = getattr(tree, "_focus", None)
        if foc is not None and getattr(foc, "bounds", None):
            foc_b = [int(v) for v in foc.bounds]

    nodes = []
    if tree is not None and getattr(tree, "root", None) is not None:
        for n in tree.nodes():
            nb = getattr(n, "bounds", (0, 0, 0, 0)) or (0, 0, 0, 0)
            nodes.append({
                "cls": str(getattr(n, "cls", "") or ""),
                "text": _clean_text(getattr(n, "text", None) or ""),
                "vid": getattr(n, "vid", None),
                "bounds": [int(nb[0]), int(nb[1]), int(nb[2]), int(nb[3])],
                "clickable": bool(getattr(n, "clickable", False)),
                "focusable": bool(getattr(n, "focusable", False)),
                "visible": bool(getattr(n, "visible", True)),
            })

    interactive = extract_interactive_elements(tree, scroll_y=sy, view_h=DEFAULT_VIEW_H)
    now_ms = int(time.time() * 1000)

    return {
        "width": width,
        "height": height,
        "scroll_y": sy,
        "max_scroll": ms,
        "dark_mode": dark_mode,
        "window_bg": window_bg,
        "window_bg_dark": window_bg_dark,
        "status_clock": _clean_text(status_clock),
        "focused_bounds": foc_b,
        "toast_text": _clean_text(toast_text) if toast_text else None,
        "package": _clean_text(package),
        "source": str(source or "glass"),
        "render_source": str(getattr(tree, "render_source", "executed") or "executed"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_epoch_ms": now_ms,
        "paint": paint,
        "text": texts,
        "nodes": nodes,
        "interactive_elements": interactive,
    }


def persist_live_ui_snapshot(snap: dict[str, Any], out_dir: Optional[str] = None) -> str:
    """Schreibt `snap` atomar nach `<out_dir>/last_ui.json` und liefert den Dateipfad."""
    target_dir = out_dir or DEFAULT_GLASS_OUT_DIR
    os.makedirs(target_dir, exist_ok=True)
    tmp_path = os.path.join(target_dir, "last_ui.json.tmp")
    dst_path = os.path.join(target_dir, "last_ui.json")
    raw = json.dumps(snap, ensure_ascii=False, indent=1, default=str)
    raw = raw.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    with open(tmp_path, "w", encoding="utf-8", errors="replace") as fh:
        fh.write(raw)
    os.replace(tmp_path, dst_path)
    return dst_path
