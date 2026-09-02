import sqlite3
import time


class ApiDbService:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS requests
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL, method TEXT, url TEXT, status INTEGER,
                      req_headers TEXT, req_body TEXT, res_headers TEXT, res_body TEXT, comment TEXT)''')
        conn.commit()
        conn.close()

    def clear_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM requests")
        conn.commit()
        conn.close()

    def get_requests(self, filter_text=""):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        if filter_text:
            c.execute(
                "SELECT id, timestamp, method, url, status, req_headers, req_body, res_headers, res_body, comment FROM requests WHERE url LIKE ? OR method LIKE ? ORDER BY id ASC",
                (f"%{filter_text}%", f"%{filter_text}%"))
        else:
            c.execute(
                "SELECT id, timestamp, method, url, status, req_headers, req_body, res_headers, res_body, comment FROM requests ORDER BY id ASC")
        rows = c.fetchall()
        conn.close()
        return rows

    def get_request_by_id(self, req_id):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT req_headers, req_body, res_headers, res_body, comment FROM requests WHERE id=?", (req_id,))
        row = c.fetchone()
        conn.close()
        return row

    def get_full_requests_by_ids(self, ids):
        if not ids: return []
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(f"SELECT * FROM requests WHERE id IN ({','.join(['?'] * len(ids))})", ids)
        rows = c.fetchall()
        conn.close()
        return rows

    def update_request(self, req_id, comment, req_body, res_body):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("UPDATE requests SET comment=?, req_body=?, res_body=? WHERE id=?",
                  (comment, req_body, res_body, req_id))
        conn.commit()

        c.execute(
            "SELECT id, timestamp, method, url, status, req_headers, req_body, res_headers, res_body, comment FROM requests WHERE id=?",
            (req_id,))
        updated_row = c.fetchone()
        conn.close()
        return updated_row

    def insert_packet(self, packet_dict):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''INSERT INTO requests (timestamp, method, url, status, req_headers, req_body, res_headers, res_body, comment)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (packet_dict.get("timestamp", time.time()), packet_dict.get("method", ""), packet_dict.get("url", ""),
                   packet_dict.get("status", 200),
                   packet_dict.get("req_headers", ""), packet_dict.get("req_body", ""),
                   packet_dict.get("res_headers", ""),
                   packet_dict.get("res_body", ""), packet_dict.get("comment", "")))
        conn.commit()
        conn.close()