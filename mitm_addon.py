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

    def load(self, loader):
        loader.add_option("api_db", str, "api_traffic.db", "SQLite DB Path")
        loader.add_option("rules_file", str, "intercept_rules.json", "Rules JSON Path")

    def running(self):
        self.db_path = ctx.options.api_db
        self.rules_path = ctx.options.rules_file

    def get_rules(self):
        if not os.path.exists(self.rules_path): return []
        try:
            with open(self.rules_path, "r") as f:
                return json.load(f)
        except:
            return []

    def request(self, flow: http.HTTPFlow):
        rules = self.get_rules()
        for rule in rules:
            if rule.get("url_match", "") in flow.request.pretty_url:
                if rule.get("action") == "replace_req_body":
                    flow.request.text = rule.get("payload", "")

    def response(self, flow: http.HTTPFlow):
        rules = self.get_rules()
        for rule in rules:
            if rule.get("url_match", "") in flow.request.pretty_url:
                if rule.get("action") == "replace_res_body":
                    flow.response.text = rule.get("payload", "")

        # Save traffic to SQLite
        if self.db_path:
            try:
                conn = sqlite3.connect(self.db_path, timeout=5)
                c = conn.cursor()
                
                req_headers = json.dumps(dict(flow.request.headers))
                req_body = flow.request.text or ""
                res_headers = json.dumps(dict(flow.response.headers))
                res_body = flow.response.text or ""
                
                c.execute('''INSERT INTO requests (timestamp, method, url, status, req_headers, req_body, res_headers, res_body, comment)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                          (time.time(), flow.request.method, flow.request.pretty_url, flow.response.status_code,
                           req_headers, req_body, res_headers, res_body, ""))
                conn.commit()
                conn.close()
            except Exception as e:
                ctx.log.error(f"DB Insert Error: {e}")

addons = [APIInterceptor()]
