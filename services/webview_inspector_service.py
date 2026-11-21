# -*- coding: utf-8 -*-
"""services.webview_inspector_service — Browser-Vorschau, DOM/Header/API-Inspect & Chromium-CDP.

Bietet zwei nahtlos kombinierbare Ebenen:
1. Integrierte Eigenlösung: App-URLs/HTML-Assets erkennen, HTTP(S)/Asset-Seiten abrufen,
   DOM-Baum, Header, Cookies, Links, Formulare, Skripte & API-Endpunkte inspizieren.
2. Echte Chromium-Integration: Findet installierten Chrome/Edge auf Windows/Linux/macOS,
   startet ihn im isolierten Mobile-Profil direkt mit offenen DevTools (--auto-open-devtools-for-tabs)
   und optionalem MITM-Proxy, und spricht per reinem Stdlib-CDP (HTTP /json + WebSocket)
   für Live-Screenshots, DOM-Export und JS-Konsole.
"""
from __future__ import annotations

import base64
import glob
import hashlib
import html as _html
from html.parser import HTMLParser
import http.cookiejar
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


DEFAULT_MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 9 Pro) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
)
_URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")


class _DomInspectorParser(HTMLParser):
    """Baut einen kompakten DOM-Baum plus Links, Formulare, Skripte und Textbloecke."""

    def __init__(self, base_url: str = ""):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self._in_title = False
        self._in_script_or_style = False
        self.root = {"tag": "document", "attrs": {}, "text": "", "children": []}
        self._stack = [self.root]
        self.links = []
        self.forms = []
        self.scripts = []
        self.images = []
        self.text_blocks = []
        self._cur_form = None
        self._node_count = 0

    def _cur(self):
        return self._stack[-1] if self._stack else self.root

    def handle_starttag(self, tag, attrs):
        t = str(tag or "").lower()
        adict = {str(k): ("" if v is None else str(v)) for k, v in (attrs or [])}
        node = {"tag": t, "attrs": adict, "text": "", "children": []}
        if self._node_count < 2500:
            self._cur()["children"].append(node)
            self._node_count += 1
        void_tags = {"meta", "link", "br", "hr", "img", "input", "source", "wbr"}
        if t not in void_tags:
            self._stack.append(node)
        if t == "title":
            self._in_title = True
        if t in ("script", "style", "noscript"):
            self._in_script_or_style = True
        if t == "a" and adict.get("href"):
            abs_u = urllib.parse.urljoin(self.base_url, adict["href"]) if self.base_url else adict["href"]
            self.links.append({"href": abs_u, "raw": adict["href"], "text": ""})
        elif t == "img" and adict.get("src"):
            abs_u = urllib.parse.urljoin(self.base_url, adict["src"]) if self.base_url else adict["src"]
            self.images.append({"src": abs_u, "alt": adict.get("alt", "")})
        elif t == "script":
            src = adict.get("src", "")
            abs_u = urllib.parse.urljoin(self.base_url, src) if (src and self.base_url) else src
            self.scripts.append({"src": abs_u, "inline": ""})
        elif t == "form":
            act = adict.get("action", "")
            abs_act = urllib.parse.urljoin(self.base_url, act) if (act and self.base_url) else (act or self.base_url)
            self._cur_form = {
                "method": (adict.get("method") or "GET").upper(),
                "action": abs_act,
                "inputs": [],
            }
            self.forms.append(self._cur_form)
        elif t in ("input", "select", "textarea", "button") and self._cur_form is not None:
            self._cur_form["inputs"].append({
                "tag": t,
                "type": adict.get("type", "text" if t == "input" else t),
                "name": adict.get("name") or adict.get("id") or "",
                "value": adict.get("value", ""),
                "placeholder": adict.get("placeholder", ""),
            })

    def handle_endtag(self, tag):
        t = str(tag or "").lower()
        if t == "title":
            self._in_title = False
        if t in ("script", "style", "noscript"):
            self._in_script_or_style = False
        if t == "form":
            self._cur_form = None
        for idx in range(len(self._stack) - 1, 0, -1):
            if self._stack[idx]["tag"] == t:
                del self._stack[idx:]
                break

    def handle_data(self, data):
        txt = (data or "").strip()
        if not txt:
            return
        cur = self._cur()
        if len(cur["text"]) < 300:
            cur["text"] = (cur["text"] + " " + txt).strip() if cur["text"] else txt
        if self._in_title:
            self.title = (self.title + " " + txt).strip() if self.title else txt
        if cur["tag"] == "a" and self.links:
            self.links[-1]["text"] = (self.links[-1]["text"] + " " + txt).strip()
        if cur["tag"] == "script" and self.scripts and not self.scripts[-1].get("src"):
            self.scripts[-1]["inline"] += data
        if not self._in_script_or_style:
            self.text_blocks.append((cur["tag"], txt))


def discover_app_urls(app_root: str, web_events=None) -> list[dict]:
    """Sammelt Web-URLs, Web-Activities und lokale HTML-Assets aus einer entpackten App + WebView-Events."""
    items = []
    seen = set()

    def _add(url: str, source: str, kind: str = "url", label: str = ""):
        u = str(url or "").strip()
        if not u or u in seen:
            return
        seen.add(u)
        items.append({"url": u, "source": source, "kind": kind, "label": label or source})

    for ev in list(web_events or ()):
        if isinstance(ev, dict) and ev.get("url"):
            _add(ev["url"], f"Live {ev.get('kind', 'webview')}", "live")

    if not app_root or not os.path.isdir(app_root):
        return items

    def _infer_webapp_base(data_obj: dict) -> str:
        urls_dict = data_obj.get("urls")
        if isinstance(urls_dict, dict):
            for v in urls_dict.values():
                vs = str(v or "")
                if vs.startswith(("http://", "https://")) and "webapp." in vs.lower():
                    pr = urllib.parse.urlsplit(vs)
                    if pr.scheme and pr.netloc:
                        return f"{pr.scheme}://{pr.netloc}"
        return ""

    # 1) HTML-Assets & .env / Config-JSON / Translation-JSON unter assets/
    assets_dir = os.path.join(app_root, "assets")
    tr_files: list[tuple[int, str, str]] = []
    other_cfg_files: list[tuple[str, str]] = []
    if os.path.isdir(assets_dir):
        for dp, _dn, fns in os.walk(assets_dir):
            for fn in sorted(fns):
                fp = os.path.join(dp, fn)
                low = fn.lower()
                rel = os.path.relpath(fp, app_root).replace("\\", "/")
                if low.endswith((".html", ".htm")):
                    _add(fp, f"Asset ({rel})", "asset_html", fn)
                elif (".env" in low or low.endswith(".json")) and os.path.getsize(fp) <= 262_144:
                    if "/translations/" in ("/" + rel.lower()) or rel.lower().startswith("translations/"):
                        prio = 0 if low.startswith("de") else (1 if low.startswith("en") else 2)
                        if prio <= 1:
                            tr_files.append((prio, fp, rel))
                    else:
                        other_cfg_files.append((fp, rel))

    for _prio, fp, rel in sorted(tr_files):
        try:
            txt = open(fp, "r", encoding="utf-8", errors="replace").read()
            parsed = json.loads(txt)
            if isinstance(parsed, dict) and isinstance(parsed.get("urls"), dict):
                wbase = _infer_webapp_base(parsed)
                urls_sec = parsed["urls"]
                pref_keys = ("shop", "support", "kippy_support", "tutorial_support", "live_tracking", "geo", "activity_history")
                ordered_keys = [k for k in pref_keys if k in urls_sec] + [k for k in urls_sec if k not in pref_keys]
                for uk in ordered_keys:
                    uv = str(urls_sec.get(uk) or "").strip()
                    if not uv:
                        continue
                    if "{web_app_path}" in uv and wbase:
                        uv = uv.replace("{web_app_path}", wbase)
                    if uv.startswith(("http://", "https://")) and "schemas.android.com" not in uv and "w3.org" not in uv:
                        _add(uv, f"Translation ({rel}: {uk})", "translation_url", uk.replace("_", " ").title())
        except Exception:
            pass

    for fp, rel in other_cfg_files:
        try:
            txt = open(fp, "r", encoding="utf-8", errors="replace").read()
            for m in _URL_RE.findall(txt):
                if "schemas.android.com" not in m and "w3.org" not in m:
                    _add(m, f"Asset ({rel})", "asset_url")
            for ln in txt.splitlines():
                mh = re.match(r"^\s*([A-Za-z0-9_]*(?:HOST|DOMAIN|ENDPOINT|URL|POOL)[A-Za-z0-9_]*)\s*=\s*(.+?)\s*$", ln)
                if mh and "." in mh.group(2) and not mh.group(2).startswith(("http://", "https://")):
                    _add("https://" + mh.group(2).strip().strip("'\""), f"Asset ({rel}: {mh.group(1)})", "asset_url")
        except Exception:
            pass

    # 2) res/values*/strings.xml & xml/
    for sp in glob.glob(os.path.join(app_root, "res", "values*", "strings*.xml")):
        try:
            root = ET.parse(sp).getroot()
            for el in root.findall("string"):
                t = "".join(el.itertext()).strip()
                if t.startswith(("http://", "https://")):
                    _add(t, f"strings.xml ({el.get('name', '?')})", "res_url")
        except Exception:
            pass

    # 3) AndroidManifest.xml (App-Links / Web-Activities)
    man_path = os.path.join(app_root, "AndroidManifest.xml")
    if os.path.isfile(man_path):
        ans = "{http://schemas.android.com/apk/res/android}"
        try:
            root = ET.parse(man_path).getroot()
            for data_el in root.findall(".//intent-filter/data"):
                sch = data_el.get(ans + "scheme") or ""
                host = data_el.get(ans + "host") or ""
                path = data_el.get(ans + "pathPrefix") or data_el.get(ans + "path") or ""
                if sch in ("http", "https") and host and not host.startswith("${"):
                    _add(f"{sch}://{host}{path}", "Manifest (intent-filter)", "manifest")
        except Exception:
            pass
    return items


class WebViewInspectorService:
    """Inspektions-Service fuer Eigen-Browser-Vorschau + Chromium CDP."""

    def __init__(self, proxy_url: str | None = None, base_dir: str = "."):
        self.proxy_url = proxy_url
        self.base_dir = base_dir
        self.cookie_jar = http.cookiejar.CookieJar()
        self.chromium_proc = None
        self.cdp_port = None

    def fetch_and_inspect(self, target: str, *, method: str = "GET",
                          headers: dict | None = None, body: bytes | str | None = None,
                          user_agent: str = DEFAULT_MOBILE_UA, timeout: float = 12.0) -> dict:
        t0 = time.monotonic()
        raw_target = str(target or "").strip()
        if not raw_target:
            return {"ok": False, "error": "Keine URL oder Datei angegeben."}

        # Lokale HTML-Asset-Datei?
        if not raw_target.startswith(("http://", "https://")):
            local_path = raw_target
            if local_path.startswith("file:///"):
                local_path = urllib.parse.unquote(local_path[8:] if os.name == "nt" else local_path[7:])
            if os.path.isfile(local_path):
                raw_bytes = open(local_path, "rb").read()
                html_text = raw_bytes.decode("utf-8", "replace")
                return self._build_inspection_result(
                    url="file:///" + local_path.replace("\\", "/"),
                    final_url="file:///" + local_path.replace("\\", "/"),
                    method="FILE", status=200, reason="OK",
                    req_headers={}, resp_headers={"Content-Type": "text/html; charset=utf-8"},
                    raw_bytes=raw_bytes, text=html_text, elapsed_ms=int((time.monotonic() - t0) * 1000),
                )
            if "://" not in raw_target:
                raw_target = "https://" + raw_target

        req_hdrs = {"User-Agent": user_agent or DEFAULT_MOBILE_UA, "Accept": "text/html,application/xhtml+xml,application/json,*/*"}
        if headers:
            req_hdrs.update({str(k): str(v) for k, v in headers.items() if k})

        data_bytes = None
        if body is not None and method.upper() in ("POST", "PUT", "PATCH"):
            data_bytes = body if isinstance(body, bytes) else str(body).encode("utf-8")

        handlers = [urllib.request.HTTPCookieProcessor(self.cookie_jar)]
        if self.proxy_url:
            handlers.append(urllib.request.ProxyHandler({"http": self.proxy_url, "https": self.proxy_url}))
        opener = urllib.request.build_opener(*handlers)
        req = urllib.request.Request(raw_target, data=data_bytes, headers=req_hdrs, method=method.upper())

        status = 0
        reason = ""
        final_url = raw_target
        resp_hdrs = {}
        raw_bytes = b""
        err_msg = ""
        try:
            with opener.open(req, timeout=timeout) as resp:
                status = int(getattr(resp, "status", 200))
                reason = str(getattr(resp, "reason", "OK") or "OK")
                final_url = str(resp.geturl() or raw_target)
                resp_hdrs = {str(k): str(v) for k, v in resp.headers.items()}
                raw_bytes = resp.read()
        except urllib.error.HTTPError as he:
            status = int(getattr(he, "code", 500) or 500)
            reason = str(getattr(he, "reason", "HTTP Error") or "HTTP Error")
            final_url = str(getattr(he, "url", raw_target) or raw_target)
            resp_hdrs = {str(k): str(v) for k, v in (he.headers.items() if he.headers else ())}
            try:
                raw_bytes = he.read()
            except Exception:
                raw_bytes = b""
        except Exception as e:
            err_msg = repr(e)

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        if err_msg and not raw_bytes and status == 0:
            return {
                "ok": False, "url": raw_target, "final_url": final_url, "method": method.upper(),
                "status": 0, "reason": err_msg, "req_headers": req_hdrs, "resp_headers": resp_hdrs,
                "elapsed_ms": elapsed_ms, "error": err_msg, "cookies": self._cookies_list(),
                "dom": {"tag": "document", "attrs": {}, "text": "", "children": []},
                "links": [], "forms": [], "scripts": [], "api_urls": [], "text": "", "raw_bytes": b"",
            }
        text = raw_bytes.decode("utf-8", "replace")
        return self._build_inspection_result(
            url=raw_target, final_url=final_url, method=method.upper(),
            status=status, reason=reason, req_headers=req_hdrs, resp_headers=resp_hdrs,
            raw_bytes=raw_bytes, text=text, elapsed_ms=elapsed_ms,
        )

    def _cookies_list(self) -> list[dict]:
        return [
            {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path, "secure": bool(c.secure)}
            for c in self.cookie_jar
        ]

    def _build_inspection_result(self, *, url, final_url, method, status, reason,
                                 req_headers, resp_headers, raw_bytes, text, elapsed_ms) -> dict:
        ctype = ""
        for k, v in resp_headers.items():
            if k.lower() == "content-type":
                ctype = v.lower()
                break
        parser = _DomInspectorParser(base_url=final_url)
        if "json" in ctype or text.lstrip().startswith(("{", "[")):
            parser.title = "JSON Response"
        else:
            try:
                parser.feed(text)
                parser.close()
            except Exception:
                pass
        api_urls = sorted({
            u for u in _URL_RE.findall(text)
            if "w3.org" not in u and "schemas.android.com" not in u
        })[:200]
        return {
            "ok": True,
            "url": url,
            "final_url": final_url,
            "method": method,
            "status": status,
            "reason": reason,
            "req_headers": req_headers,
            "resp_headers": resp_headers,
            "cookies": self._cookies_list(),
            "elapsed_ms": elapsed_ms,
            "size": len(raw_bytes),
            "content_type": ctype,
            "title": parser.title or final_url,
            "dom": parser.root,
            "links": parser.links,
            "forms": parser.forms,
            "scripts": parser.scripts,
            "images": parser.images,
            "text_blocks": parser.text_blocks,
            "api_urls": api_urls,
            "text": text,
            "raw_bytes": raw_bytes,
        }

    @staticmethod
    def find_chromium_binary() -> str | None:
        cands = [
            shutil.which("chrome"),
            shutil.which("chromium"),
            shutil.which("google-chrome"),
            shutil.which("msedge"),
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
        for c in cands:
            if c and os.path.isfile(c):
                return c
        return None

    @staticmethod
    def _pick_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])

    def launch_chromium_with_inspect(self, url: str = "about:blank", *,
                                     use_proxy: bool = False,
                                     proxy_addr: str = "127.0.0.1:8080",
                                     mobile: bool = True) -> dict:
        exe = self.find_chromium_binary()
        if not exe:
            return {"ok": False, "error": "Kein Chromium/Chrome/Edge gefunden."}
        port = self._pick_free_port()
        prof_dir = os.path.join(self.base_dir or ".", "data", "browser_profile")
        os.makedirs(prof_dir, exist_ok=True)
        args = [
            exe,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={prof_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--auto-open-devtools-for-tabs",
            "--ignore-certificate-errors",
            "--window-size=440,880" if mobile else "--window-size=1100,820",
        ]
        if mobile:
            args.append(f"--user-agent={DEFAULT_MOBILE_UA}")
        if use_proxy and proxy_addr:
            args.append(f"--proxy-server={proxy_addr}")
        args.append(url or "about:blank")
        try:
            self.chromium_proc = subprocess.Popen(args)
            self.cdp_port = port
            return {"ok": True, "exe": exe, "cdp_port": port, "pid": self.chromium_proc.pid, "url": url}
        except Exception as e:
            return {"ok": False, "error": repr(e)}

    def render_headless_mobile_png(self, url: str, *, width: int = 364, height: int = 560,
                                   timeout: float = 8.0) -> bytes | None:
        """Rendert eine Mobile-Webseite im Hintergrund via Headless-Chromium als PNG für das Handy-Display."""
        target = str(url or "").strip()
        if not target.startswith(("http://", "https://", "file:///")):
            return None
        exe = self.find_chromium_binary()
        if not exe:
            return None
        try:
            with tempfile.TemporaryDirectory(prefix="agy_mob_vp_") as tmp_dir:
                prof_dir = os.path.join(tmp_dir, "profile")
                shot_path = os.path.join(tmp_dir, "viewport.png")
                os.makedirs(prof_dir, exist_ok=True)
                args = [
                    exe,
                    "--headless=new",
                    "--disable-gpu",
                    "--hide-scrollbars",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--ignore-certificate-errors",
                    f"--user-data-dir={prof_dir}",
                    f"--window-size={max(240, int(width))},{max(320, int(height))}",
                    f"--user-agent={DEFAULT_MOBILE_UA}",
                    f"--screenshot={shot_path}",
                ]
                if self.proxy_url:
                    args.append(f"--proxy-server={self.proxy_url}")
                args.append(target)
                cflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                subprocess.run(
                    args,
                    timeout=timeout,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=cflags,
                )
                if os.path.isfile(shot_path) and os.path.getsize(shot_path) > 64:
                    with open(shot_path, "rb") as fh:
                        return fh.read()
        except Exception:
            return None
        return None

    # --- Schlanker Stdlib-CDP-Client (HTTP /json + RFC6455 WebSocket) ---
    def list_cdp_tabs(self) -> list[dict]:
        if not self.cdp_port:
            return []
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.cdp_port}/json", timeout=2.0) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            return [t for t in data if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
        except Exception:
            return []

    @staticmethod
    def cdp_call(ws_url: str, method: str, params: dict | None = None, timeout: float = 5.0) -> dict:
        parsed = urllib.parse.urlsplit(ws_url)
        host = parsed.hostname or "127.0.0.1"
        port = int(parsed.port or 80)
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        sock = socket.create_connection((host, port), timeout=timeout)
        try:
            hs = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode("ascii")
            sock.sendall(hs)
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = sock.recv(4096)
                if not chunk:
                    raise RuntimeError("CDP Handshake abgebrochen")
                buf += chunk
            _hdr, extra = buf.split(b"\r\n\r\n", 1)

            def _send_text(txt: str):
                payload = txt.encode("utf-8")
                mask = os.urandom(4)
                n = len(payload)
                head = bytearray([0x81])
                if n < 126:
                    head.append(0x80 | n)
                elif n < 65536:
                    head.append(0x80 | 126)
                    head.extend(struct.pack("!H", n))
                else:
                    head.append(0x80 | 127)
                    head.extend(struct.pack("!Q", n))
                head.extend(mask)
                masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
                sock.sendall(bytes(head) + masked)

            def _recv_exact(cnt: int) -> bytes:
                nonlocal extra
                while len(extra) < cnt:
                    c = sock.recv(max(4096, cnt - len(extra)))
                    if not c:
                        raise EOFError("CDP Stream geschlossen")
                    extra += c
                res, extra = extra[:cnt], extra[cnt:]
                return res

            def _recv_frame():
                b1, b2 = _recv_exact(2)
                ln = b2 & 0x7F
                if ln == 126:
                    ln = struct.unpack("!H", _recv_exact(2))[0]
                elif ln == 127:
                    ln = struct.unpack("!Q", _recv_exact(8))[0]
                if b2 & 0x80:
                    _m = _recv_exact(4)
                    raw = _recv_exact(ln)
                    raw = bytes(b ^ _m[i % 4] for i, b in enumerate(raw))
                else:
                    raw = _recv_exact(ln)
                return b1 & 0x0F, raw

            msg_id = 1
            _send_text(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
            while True:
                op, raw = _recv_frame()
                if op == 8:
                    return {"error": "closed"}
                if op == 9:
                    continue
                try:
                    msg = json.loads(raw.decode("utf-8", "replace"))
                except Exception:
                    continue
                if msg.get("id") == msg_id:
                    return msg
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def capture_cdp_screenshot(self) -> bytes | None:
        tabs = self.list_cdp_tabs()
        if not tabs:
            return None
        res = self.cdp_call(tabs[0]["webSocketDebuggerUrl"], "Page.captureScreenshot", {"format": "png"})
        b64 = (res.get("result") or {}).get("data")
        return base64.b64decode(b64) if b64 else None

    def eval_cdp_js(self, expr: str) -> str:
        tabs = self.list_cdp_tabs()
        if not tabs:
            return "(Keine offene Chromium-CDP-Seite gefunden)"
        res = self.cdp_call(
            tabs[0]["webSocketDebuggerUrl"],
            "Runtime.evaluate",
            {"expression": expr, "returnByValue": True},
        )
        r = (res.get("result") or {}).get("result") or {}
        if "value" in r:
            v = r["value"]
            return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=2)
        return json.dumps(res, ensure_ascii=False, indent=2)

    def get_cdp_dom_html(self) -> str:
        return self.eval_cdp_js("document.documentElement ? document.documentElement.outerHTML : ''")
