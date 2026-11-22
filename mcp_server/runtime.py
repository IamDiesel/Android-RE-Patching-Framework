"""
runtime — einheitliche Ausfuehrungshuelle fuer jedes Tool.

Ablauf pro Aufruf: gating.check -> (bei Erlaubnis) fn() unter LogCollector ->
Ergebnis + Framework-Log -> audit.log. Verweigerte Aufrufe liefern den
GUI-Hinweis und werden ebenfalls auditiert.
"""
import json
import time

from mcp_server import registry, gating, audit
from mcp_server.eventbus_bridge import LogCollector


def _prepare_execution(ctx, tool_id, confirm=False):
    spec = registry.get(tool_id)
    tier = spec.tier if spec else "?"
    t0 = time.time()

    # Fix: GUI-Aenderungen an den MCP-Rechten sofort wirksam machen.
    # Ohne dies liest der Server die config.json nur beim Start -> gated
    # Freigaben aus der GUI kommen erst nach Server-Neustart an.
    try:
        ctx.reload_config()
    except Exception:
        pass

    # GUI-Aenderungen an Libs/Favoriten fuer den MCP-Server sichtbar machen.
    for _attr in ("native_lib_mgr", "_favorites", "favorite_svc", "frida_manager"):
        try:
            _m = getattr(ctx, _attr, None)
            if _m is not None and hasattr(_m, "reload_if_changed"):
                _m.reload_if_changed()
        except Exception:
            pass

    # Aktive Patch-Liste mit data/active_session.json synchronisieren.
    try:
        _as = ctx.active_session()
        if _as.reload_if_changed() or not getattr(ctx, "_active_synced", False):
            from core.application.session_state import SessionState
            SessionState.load_patches(_as.patches())
            ctx._active_synced = True
    except Exception:
        pass

    dec = gating.check(ctx.cfg, tool_id, confirm)
    return tier, t0, dec


def _mcp_image_and_text_types():
    try:
        from mcp import types as mtypes
        text_cls = mtypes.TextContent
    except Exception:
        text_cls = None
    try:
        from mcp.server.mcpserver import Image as McpImage
    except ImportError:
        try:
            from mcp.server.fastmcp import Image as McpImage
        except ImportError:
            McpImage = None
    return McpImage, text_cls


def run(ctx, tool_id, fn, args_summary="", confirm=False, capture_events=("LOG_INFO",)) -> str:
    tier, t0, dec = _prepare_execution(ctx, tool_id, confirm)
    if not dec.allowed:
        audit.log(ctx.cfg, tool_id, tier, "denied", confirm, args_summary,
                  "blocked", int((time.time() - t0) * 1000))
        return dec.hint or f"[verweigert] {dec.reason}"

    try:
        with LogCollector(capture_events) as lc:
            out = fn()
        body = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, indent=2)
        log_txt = lc.text()
        if log_txt:
            body = f"{body}\n\n--- Framework-Log ---\n{log_txt}"
        audit.log(ctx.cfg, tool_id, tier, "allowed", confirm, args_summary,
                  "ok", int((time.time() - t0) * 1000))
        return body
    except Exception as e:
        audit.log(ctx.cfg, tool_id, tier, "allowed", confirm, args_summary,
                  "error", int((time.time() - t0) * 1000), error=e)
        return f"[Fehler in {tool_id}] {e}"


def run_multimodal(ctx, tool_id, fn, args_summary="", confirm=False, capture_events=("LOG_INFO",)) -> list:
    """Fuehrt ein multimodales Tool aus (liefert [Image, TextContent] oder [TextContent])."""
    tier, t0, dec = _prepare_execution(ctx, tool_id, confirm)
    McpImage, TextContent = _mcp_image_and_text_types()

    def _mk_text(txt: str):
        if TextContent is not None:
            return TextContent(type="text", text=txt)
        return {"type": "text", "text": txt}

    if not dec.allowed:
        audit.log(ctx.cfg, tool_id, tier, "denied", confirm, args_summary,
                  "blocked", int((time.time() - t0) * 1000))
        return [_mk_text(dec.hint or f"[verweigert] {dec.reason}")]

    try:
        with LogCollector(capture_events) as lc:
            out = fn()
        data = out if isinstance(out, dict) else {"result": out}
        png_bytes = data.get("png_bytes")
        meta = dict(data.get("meta") if isinstance(data.get("meta"), dict) else {
            k: v for k, v in data.items() if k != "png_bytes"
        })
        log_txt = lc.text()
        if log_txt:
            meta["framework_log"] = log_txt
        meta_json = json.dumps(meta, ensure_ascii=False, indent=2, default=str)
        blocks = []
        if png_bytes and McpImage is not None:
            blocks.append(McpImage(data=bytes(png_bytes), format="png"))
        blocks.append(_mk_text(meta_json))
        audit.log(ctx.cfg, tool_id, tier, "allowed", confirm, args_summary,
                  "ok", int((time.time() - t0) * 1000))
        return blocks
    except Exception as e:
        audit.log(ctx.cfg, tool_id, tier, "allowed", confirm, args_summary,
                  "error", int((time.time() - t0) * 1000), error=e)
        return [_mk_text(f"[Fehler in {tool_id}] {e}")]
