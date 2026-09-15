#!/usr/bin/env python3
"""
editor_server.py — Obsidian Capital JSON Editor Backend
Serves the editor UI and handles read/write of JSON data files.
Runs on port 8765. Start via systemd or: python3 editor_server.py

Endpoints:
  GET  /                          → serve obsidian_editor.html
  GET  /api/advisors              → load crm_advisors.json
  POST /api/advisors              → save crm_advisors.json
  GET  /api/clients               → load crm_clients.json
  POST /api/clients               → save crm_clients.json
  GET  /api/holdings              → load neptune_holdings.json
  POST /api/holdings              → save neptune_holdings.json
  POST /api/refresh-prices        → run update_prices.py
  GET  /api/status                → health check
"""

import json
import os
import sys
import fcntl
import logging
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# ── Paths ──────────────────────────────────────────────────────────────────
HOME       = os.path.expanduser("~")
USER       = os.path.basename(HOME)  # jay or shogun
PAPER_DIR  = os.path.join(HOME, "paper_trade")
VENUS_DIR  = os.path.join(PAPER_DIR, "venus")

FILES = {
    "advisors": os.path.join(VENUS_DIR,  "crm_advisors.json"),
    "clients":  os.path.join(VENUS_DIR,  "crm_clients.json"),
    "holdings": os.path.join(PAPER_DIR,  "neptune_holdings.json"),
}

EDITOR_HTML = os.path.join(PAPER_DIR, "obsidian_editor.html")
PORT = 8765

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [editor] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("editor")


def load_json_file(key):
    path = FILES[key]
    if not os.path.exists(path):
        return None, f"File not found: {path}"
    try:
        with open(path) as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            data = json.load(f)
            fcntl.flock(f, fcntl.LOCK_UN)
        return data, None
    except Exception as e:
        return None, str(e)


def save_json_file(key, data):
    path = FILES[key]
    # Write to temp file first, then atomic rename
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            json.dump(data, f, indent=2)
            fcntl.flock(f, fcntl.LOCK_UN)
        os.replace(tmp, path)
        return True, None
    except Exception as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        return False, str(e)


class EditorHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        log.info(f"{self.address_string()} {format % args}")

    def send_json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, code, html):
        body = html.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/" or path == "/editor":
            if os.path.exists(EDITOR_HTML):
                with open(EDITOR_HTML) as f:
                    html = f.read()
                self.send_html(200, html)
            else:
                self.send_json(404, {"error": "obsidian_editor.html not found"})

        elif path == "/api/status":
            self.send_json(200, {
                "status": "ok",
                "user": USER,
                "files": {k: os.path.exists(v) for k, v in FILES.items()},
                "paper_dir": PAPER_DIR,
            })

        elif path.startswith("/api/"):
            key = path.replace("/api/", "")
            if key not in FILES:
                self.send_json(404, {"error": f"Unknown resource: {key}"})
                return
            data, err = load_json_file(key)
            if err:
                self.send_json(500, {"error": err})
            else:
                self.send_json(200, data)

        else:
            self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/refresh-prices":
            script = os.path.join(PAPER_DIR, "update_prices.py")
            try:
                result = subprocess.run(
                    ["python3", script],
                    capture_output=True, text=True, timeout=120,
                    cwd=PAPER_DIR
                )
                self.send_json(200, {
                    "ok": result.returncode == 0,
                    "output": result.stdout[-2000:],
                    "error": result.stderr[-500:] if result.returncode != 0 else ""
                })
            except Exception as e:
                self.send_json(500, {"error": str(e)})
            return

        if path.startswith("/api/"):
            key = path.replace("/api/", "")
            if key not in FILES:
                self.send_json(404, {"error": f"Unknown resource: {key}"})
                return
            try:
                body = self.read_body()
                data = json.loads(body)
            except Exception as e:
                self.send_json(400, {"error": f"Invalid JSON: {e}"})
                return

            ok, err = save_json_file(key, data)
            if ok:
                log.info(f"Saved {key} ({len(body)} bytes)")
                self.send_json(200, {"ok": True, "saved": FILES[key]})
            else:
                self.send_json(500, {"error": err})
        else:
            self.send_json(404, {"error": "Not found"})


def main():
    log.info(f"Obsidian Capital Editor — port {PORT}")
    log.info(f"User: {USER} | Paper dir: {PAPER_DIR}")
    for k, v in FILES.items():
        exists = "✅" if os.path.exists(v) else "❌"
        log.info(f"  {exists} {k}: {v}")
    log.info(f"Open: http://localhost:{PORT}/")

    server = HTTPServer(("0.0.0.0", PORT), EditorHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down.")


if __name__ == "__main__":
    main()
