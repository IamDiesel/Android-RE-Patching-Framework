# -*- coding: utf-8 -*-
"""services.flutter_api_extractor_service — Optionaler statischer Flutter/Dart-API-Extractor.

Extrahiert rein statisch (ohne 27-MB Flutter-Engine-Boot) aus `assets/flutter_assets/.env*`,
JSON-Configs, `strings.xml` und `libapp.so`-Strings ausführbare Request-Vorlagen
(AWS Cognito, AppSync/GraphQL, REST) zur optionalen Uebernahme in den API Inspector.
"""
from __future__ import annotations

import glob
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

_IGNORED_HOSTS = (
    "schemas.android.com",
    "www.w3.org",
    "ns.adobe.com",
    "apache.org",
    "xmlpull.org",
    "github.com",
    "opensource.org",
    "gnu.org",
    "ietf.org",
    "dart.dev",
    "flutter.dev",
    "pub.dev",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "play.google.com",
    "market.android.com",
    "localhost",
    "127.0.0.1",
)

_COGNITO_POOL_RE = re.compile(r"\b((?:eu|us|ap|sa|ca|me|af|il)-[a-z]+-\d+)_[A-Za-z0-9]{6,16}\b")
_APPSYNC_URL_RE = re.compile(r"https?://[a-zA-Z0-9.-]+\.appsync-api\.[a-z0-9-]+\.amazonaws\.com/graphql")
_APPSYNC_KEY_RE = re.compile(r"\bda2-[a-z0-9]{20,32}\b")
_URL_RE = re.compile(r"https?://[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(?::\d+)?(?:/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*)?")
_PATH_RE = re.compile(r"^/(?:api|v[1-9]|auth|oauth|token|user|users|device|devices|graphql)[A-Za-z0-9._/{}/?-]*$")
_GQL_OP_RE = re.compile(r"\b(query|mutation|subscription)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:\([^)]{0,300}\))?\s*\{[^{}]{8,800}\}", re.DOTALL)


def _extract_ascii_strings(blob: bytes, min_len: int = 5, max_items: int = 120000) -> List[str]:
    out: List[str] = []
    for m in re.finditer(rb"[\x20-\x7e]{%d,512}" % min_len, blob):
        out.append(m.group(0).decode("ascii", "replace"))
        if len(out) >= max_items:
            break
    return out


def _host_ignored(url: str) -> bool:
    low = url.lower()
    return any(h in low for h in _IGNORED_HOSTS)


def _parse_env_text(text: str) -> Dict[str, str]:
    env: Dict[str, str] = {}
    for ln in (text or "").splitlines():
        st = ln.strip()
        if not st or st.startswith("#") or "=" not in st:
            continue
        k, v = st.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            env[k] = v
    return env


def _collect_json_kv(obj: Any, out: Dict[str, str], prefix: str = "") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (str, int, float, bool)):
                out[str(k)] = str(v)
                out[key] = str(v)
            else:
                _collect_json_kv(v, out, key)
    elif isinstance(obj, list):
        for it in obj[:30]:
            _collect_json_kv(it, out, prefix)


class FlutterApiExtractorService:
    """Rein statischer Scanner fuer Flutter/Dart Assets & libapp.so."""

    @classmethod
    def extract_from_app_root(cls, app_root: str, *, extra_lib_dirs: Optional[List[str]] = None) -> Dict[str, Any]:
        if not app_root or not os.path.isdir(app_root):
            return {"ok": False, "error": "App-Root nicht gefunden", "is_flutter": False, "templates": []}

        scanned_files: List[str] = []
        env_kv: Dict[str, str] = {}
        raw_strings: List[str] = []

        # 1) Prüfe auf Flutter-Struktur & lese Assets (.env, JSON configs)
        fa_dir = os.path.join(app_root, "assets", "flutter_assets")
        is_flutter = os.path.isdir(fa_dir) or bool(glob.glob(os.path.join(app_root, "lib", "*", "libapp.so")))

        asset_roots = [os.path.join(app_root, "assets")]
        for ar in asset_roots:
            if not os.path.isdir(ar):
                continue
            for dp, _dirs, fns in os.walk(ar):
                for fn in sorted(fns):
                    fp = os.path.join(dp, fn)
                    rel = os.path.relpath(fp, app_root).replace("\\", "/")
                    low = fn.lower()
                    if low.startswith(".env") or low.endswith(".env"):
                        try:
                            txt = open(fp, "r", encoding="utf-8", errors="replace").read()
                            env_kv.update(_parse_env_text(txt))
                            raw_strings.extend(txt.splitlines())
                            scanned_files.append(rel)
                        except Exception:
                            pass
                    elif low.endswith(".json") and os.path.getsize(fp) <= 2 * 1024 * 1024:
                        try:
                            txt = open(fp, "r", encoding="utf-8", errors="replace").read()
                            data = json.loads(txt)
                            _collect_json_kv(data, env_kv)
                            raw_strings.append(txt)
                            scanned_files.append(rel)
                        except Exception:
                            pass

        # res/values/strings.xml (z.B. google_api_key, firebase_database_url, server_url)
        str_xml = os.path.join(app_root, "res", "values", "strings.xml")
        if os.path.isfile(str_xml):
            try:
                txt = open(str_xml, "r", encoding="utf-8", errors="replace").read()
                for m in re.finditer(r'<string\s+name="([^"]+)">([^<]+)</string>', txt):
                    env_kv.setdefault(m.group(1), m.group(2).strip())
                raw_strings.append(txt)
                scanned_files.append("res/values/strings.xml")
            except Exception:
                pass

        # 2) libapp.so Strings scannen
        libapp_cands = sorted(glob.glob(os.path.join(app_root, "lib", "*", "libapp.so")))
        for d in (extra_lib_dirs or ()):
            if d and os.path.isdir(d):
                libapp_cands.extend(sorted(glob.glob(os.path.join(d, "libapp.so"))))
        for lp in libapp_cands[:2]:
            if os.path.isfile(lp):
                try:
                    with open(lp, "rb") as fh:
                        blob = fh.read()
                    raw_strings.extend(_extract_ascii_strings(blob))
                    scanned_files.append(os.path.relpath(lp, app_root) if lp.startswith(app_root) else os.path.basename(lp))
                except Exception:
                    pass

        joined = "\n".join(raw_strings)

        # ---- Kategorisierte Vorlagen bauen ----
        templates: List[dict] = []
        seen_keys = set()

        def _add_tpl(cat: str, method: str, url: str, headers: dict, body: str, hint: str, desc: str):
            k = (cat, method, url, body[:120])
            if k in seen_keys:
                return
            seen_keys.add(k)
            templates.append({
                "id": f"tpl_{len(templates) + 1}",
                "category": cat,
                "method": method,
                "url": url,
                "headers": headers,
                "body": body,
                "source_hint": hint,
                "description": desc,
            })

        # A) AWS Cognito
        pool_ids = list(dict.fromkeys(_COGNITO_POOL_RE.findall(joined)))
        # _COGNITO_POOL_RE hat Gruppe 1 = region, volle PoolID via finditer
        full_pools: List[Tuple[str, str]] = []
        for m in _COGNITO_POOL_RE.finditer(joined):
            pair = (m.group(0), m.group(1))
            if pair not in full_pools:
                full_pools.append(pair)
        client_id = ""
        for k in ("CLIENT_ID", "COGNITO_CLIENT_ID", "APP_CLIENT_ID", "aws_user_pools_web_client_id", "USER_POOL_CLIENT_ID", "ClientID", "clientId"):
            if env_kv.get(k):
                client_id = env_kv[k]
                break
        if not client_id:
            # Suche nach clientId=... oder JSON "AppClientId":"..."
            mc = re.search(r'(?:client_?id|AppClientId)["\'\s:=]+([a-z0-9]{20,32})', joined, re.I)
            if mc:
                client_id = mc.group(1)

        if full_pools:
            for pool_id, region in full_pools[:3]:
                cog_url = f"https://cognito-idp.{region}.amazonaws.com/"
                _add_tpl(
                    "Cognito", "POST", cog_url,
                    {
                        "Content-Type": "application/x-amz-json-1.1",
                        "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
                    },
                    json.dumps({
                        "AuthFlow": "USER_PASSWORD_AUTH",
                        "ClientId": client_id or "<COGNITO_CLIENT_ID>",
                        "AuthParameters": {
                            "USERNAME": "<EMAIL_OR_USER>",
                            "PASSWORD": "<PASSWORD>",
                        },
                    }, indent=2),
                    f"Pool {pool_id}",
                    f"Cognito InitiateAuth ({pool_id})",
                )
                _add_tpl(
                    "Cognito", "POST", cog_url,
                    {
                        "Content-Type": "application/x-amz-json-1.1",
                        "X-Amz-Target": "AWSCognitoIdentityProviderService.GetUser",
                    },
                    json.dumps({"AccessToken": "<COGNITO_ACCESS_TOKEN>"}, indent=2),
                    f"Pool {pool_id}",
                    f"Cognito GetUser ({pool_id})",
                )

        # B) GraphQL / AppSync
        gql_urls = list(dict.fromkeys(_APPSYNC_URL_RE.findall(joined)))
        for k, v in env_kv.items():
            if "graphql" in str(k).lower() or ("graphql" in str(v).lower() and v.startswith("http")):
                if v not in gql_urls:
                    gql_urls.append(v)
        for u in _URL_RE.findall(joined):
            if "/graphql" in u.lower() and not _host_ignored(u) and u not in gql_urls:
                gql_urls.append(u)

        api_key = ""
        for k in ("API_KEY", "APPSYNC_API_KEY", "GRAPHQL_API_KEY", "X_API_KEY", "apiKey"):
            if env_kv.get(k):
                api_key = env_kv[k]
                break
        if not api_key:
            mk = _APPSYNC_KEY_RE.search(joined)
            if mk:
                api_key = mk.group(0)

        gql_ops: List[Tuple[str, str, str]] = []
        seen_op = set()
        for m in _GQL_OP_RE.finditer(joined):
            kind, op_name = m.group(1), m.group(2)
            snippet = re.sub(r"\s+", " ", m.group(0)).strip()
            if (kind, op_name) not in seen_op:
                seen_op.add((kind, op_name))
                gql_ops.append((kind, op_name, snippet))

        if gql_urls:
            g_url = gql_urls[0]
            g_hdrs = {"Content-Type": "application/json"}
            if api_key:
                g_hdrs["x-api-key"] = api_key
            else:
                g_hdrs["Authorization"] = "Bearer <ID_TOKEN>"
            if gql_ops:
                for kind, op_name, snippet in gql_ops[:25]:
                    _add_tpl(
                        "GraphQL", "POST", g_url, dict(g_hdrs),
                        json.dumps({"operationName": op_name, "query": snippet, "variables": {}}, indent=2),
                        f"{kind} {op_name}",
                        f"GraphQL {kind} {op_name}",
                    )
            else:
                _add_tpl(
                    "GraphQL", "POST", g_url, dict(g_hdrs),
                    json.dumps({"query": "query Introspection { __schema { queryType { name } } }", "variables": {}}, indent=2),
                    "Endpoint",
                    f"GraphQL Endpoint ({g_url})",
                )

        # C) REST / Base-URLs + Route-Pfade
        base_urls: List[str] = []
        for k, v in env_kv.items():
            if any(t in k.lower() for t in ("base_url", "api_url", "endpoint", "host", "server_url")) and v.startswith("http"):
                if v not in base_urls and not _host_ignored(v):
                    base_urls.append(v.rstrip("/"))
        for m_url in _URL_RE.findall(joined):
            if _host_ignored(m_url) or m_url in gql_urls or "cognito-idp." in m_url:
                continue
            clean_u = m_url.rstrip("/.,;\"')")
            if clean_u not in base_urls:
                base_urls.append(clean_u)
            if len(base_urls) >= 30:
                break

        route_paths: List[str] = []
        for ln in raw_strings:
            st = ln.strip()
            if st.startswith("/") and len(st) <= 120 and _PATH_RE.match(st):
                if st not in route_paths:
                    route_paths.append(st)
                    if len(route_paths) >= 25:
                        break

        rest_bases = [u for u in base_urls if u.startswith("http")]
        if rest_bases and route_paths:
            root_api = rest_bases[0].split("/", 3)[:3]
            root_host = "/".join(root_api)
            for rp in route_paths[:15]:
                _add_tpl(
                    "REST", "GET", root_host + rp,
                    {"Accept": "application/json", "Authorization": "Bearer <TOKEN>"},
                    "",
                    "Route aus libapp.so",
                    f"REST Route {rp}",
                )
        for bu in rest_bases[:20]:
            _add_tpl(
                "REST", "GET", bu,
                {"Accept": "application/json"},
                "",
                ".env / libapp.so URL",
                f"REST Endpoint {bu}",
            )

        return {
            "ok": True,
            "is_flutter": is_flutter,
            "scanned_files": scanned_files,
            "env_keys": sorted(env_kv.keys()),
            "template_count": len(templates),
            "templates": templates,
        }

    @classmethod
    def import_templates_to_db(cls, api_db_path: str, templates: List[dict]) -> int:
        """Schreibt ausgewaehlte statische Vorlagen sauber in die API-Inspector-DB."""
        if not api_db_path or not templates:
            return 0
        from services.api_db_service import ApiDbService
        db = ApiDbService(api_db_path)
        count = 0
        for tpl in templates:
            if not isinstance(tpl, dict):
                continue
            hdrs = tpl.get("headers") or {}
            hdr_str = json.dumps(hdrs, indent=2, ensure_ascii=False) if isinstance(hdrs, dict) else str(hdrs)
            cat = tpl.get("category", "API")
            desc = tpl.get("description", tpl.get("url", ""))
            db.insert_packet({
                "method": str(tpl.get("method") or "GET").upper(),
                "url": str(tpl.get("url") or ""),
                "status": 0,
                "req_headers": hdr_str,
                "req_body": str(tpl.get("body") or ""),
                "res_headers": "{}",
                "res_body": "",
                "comment": f"[Statische Vorlage: {cat}] {desc}",
            })
            count += 1
        return count
