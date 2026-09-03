import sqlite3
import json
import time
import os
from mitmproxy import http
from mitmproxy import ctx


class APIInterceptor:
    def __init__(self):
        self.db_path = ""
        self.rules_path = ""
        self._last_rules_mtime = 0
        self._cached_rules = []

    def load(self, loader):
        # Optionen für die Parameterübergabe vom Host-Prozess konfigurieren
        loader.add_option("api_db", str, "api_traffic.db", "SQLite DB Path")
        loader.add_option("rules_file", str, "intercept_rules.json", "Rules JSON Path")

    def running(self):
        self.db_path = ctx.options.api_db
        self.rules_path = ctx.options.rules_file
        self._init_db()

    def _init_db(self):
        if not self.db_path: return
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS requests (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            timestamp REAL,
                            method TEXT,
                            url TEXT,
                            status INTEGER,
                            req_headers TEXT,
                            req_body TEXT,
                            res_headers TEXT,
                            res_body TEXT,
                            comment TEXT
                         )''')
            conn.commit()
            conn.close()
        except Exception as e:
            ctx.log.error(f"DB Init Error: {e}")

    def get_rules(self):
        if not os.path.exists(self.rules_path):
            return []
        try:
            current_mtime = os.path.getmtime(self.rules_path)
            if current_mtime != self._last_rules_mtime:
                with open(self.rules_path, "r", encoding="utf-8") as f:
                    self._cached_rules = json.load(f)
                self._last_rules_mtime = current_mtime
            return self._cached_rules
        except Exception as e:
            ctx.log.error(f"Rules Load Error: {e}")
            return self._cached_rules

    def request(self, flow: http.HTTPFlow):
        rules = self.get_rules()
        for rule in rules:
            if rule.get("url_match") and rule.get("url_match") in flow.request.pretty_url:
                if rule.get("action") == "replace_req_body":
                    flow.request.text = rule.get("payload", "")
                    ctx.log.info(f"[+] Replaced Request Body for {flow.request.pretty_url}")

    def response(self, flow: http.HTTPFlow):
        rules = self.get_rules()
        for rule in rules:
            if rule.get("url_match") and rule.get("url_match") in flow.request.pretty_url:
                if rule.get("action") == "replace_res_body":
                    flow.response.text = rule.get("payload", "")
                    ctx.log.info(f"[+] Replaced Response Body for {flow.request.pretty_url}")

        if not self.db_path:
            return

        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            c = conn.cursor()

            req_headers = json.dumps(dict(flow.request.headers))
            try:
                req_body = flow.request.text or ""
            except ValueError:
                req_body = "<Binary Data>"

            res_headers = json.dumps(dict(flow.response.headers)) if flow.response else "{}"
            try:
                res_body = flow.response.text or "" if flow.response else ""
            except ValueError:
                res_body = "<Binary Data>"

            status_code = flow.response.status_code if flow.response else 0

            c.execute('''INSERT INTO requests (timestamp, method, url, status, req_headers, req_body, res_headers, res_body, comment)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                      (time.time(), flow.request.method, flow.request.pretty_url, status_code,
                       req_headers, req_body, res_headers, res_body, ""))
            conn.commit()
            conn.close()
        except Exception as e:
            ctx.log.error(f"DB Insert Error: {e}")


addons = [APIInterceptor()]