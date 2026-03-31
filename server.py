#!/usr/bin/env python3
"""Live diff server — file tree + stacked diff view."""

import hashlib
import json
import os
import time
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

from vcs import detect_vcs, get_diff, get_diff_fingerprint
from page import make_shell_html, make_content

PORT = 8777
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

MIME_TYPES = {
    ".css": "text/css",
    ".js": "application/javascript",
    ".svg": "image/svg+xml",
}


class DiffHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path.startswith("/static/"):
            self._serve_static(parsed.path[len("/static/"):])
            return

        if parsed.path == "/context":
            self._handle_context(params)
            return

        if parsed.path == "/hash":
            self._handle_hash(params)
            return

        if parsed.path == "/content":
            self._handle_content(params)
            return

        self._handle_page(params)

    def _serve_static(self, filename):
        filepath = os.path.join(STATIC_DIR, filename)
        if not os.path.isfile(filepath):
            self.send_error(404)
            return
        ext = os.path.splitext(filename)[1]
        mime = MIME_TYPES.get(ext, "application/octet-stream")
        with open(filepath, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_context(self, params):
        repo_path = params.get("path", [None])[0]
        file_path = params.get("file", [None])[0]
        start = int(params.get("start", [1])[0])
        end = int(params.get("end", [0])[0])
        if repo_path and file_path:
            repo_path = os.path.expanduser(repo_path)
            _, root = detect_vcs(repo_path)
            if root:
                full = os.path.join(root, file_path)
                lines_out = []
                try:
                    with open(full, "r", errors="replace") as f:
                        all_lines = f.readlines()
                    if end == 0:
                        end = min(start + 20, len(all_lines))
                    elif end < 0:
                        end = len(all_lines)
                    end = min(end, len(all_lines))
                    for i in range(start - 1, end):
                        lines_out.append({"num": i + 1, "text": all_lines[i].rstrip("\n")})
                except Exception:
                    pass
                self._json_response({"lines": lines_out})
                return
        self._json_response({"lines": []})

    def _handle_hash(self, params):
        path = params.get("path", [None])[0]
        if path:
            path = os.path.expanduser(path)
            fingerprint = get_diff_fingerprint(path)
            self._json_response({"hash": hashlib.md5(fingerprint.encode()).hexdigest()})

    def _handle_content(self, params):
        path = params.get("path", [None])[0]
        if not path:
            self._json_response({"error": "no path"})
            return
        path = os.path.expanduser(path)
        if not os.path.isdir(path):
            self._json_response({"error": "not a directory"})
            return
        t0 = time.monotonic()
        vcs, root, staged, unstaged = get_diff(path)
        t1 = time.monotonic()
        result = make_content(vcs, root, staged, unstaged, path)
        t2 = time.monotonic()
        print(f"[perf] get_diff={t1-t0:.3f}s  render={t2-t1:.3f}s  staged={len(staged)}b  unstaged={len(unstaged)}b", flush=True)
        self._json_response(result)

    def _handle_page(self, params):
        path = params.get("path", [None])[0]
        refresh = int(params.get("refresh", [3])[0])

        if not path:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"""<!DOCTYPE html>
<html><body style="background:#0d1117;color:#e6edf3;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<div style="text-align:center">
<h2 style="font-weight:600">diff-server</h2>
<p style="color:#8b949e;margin-top:8px">Usage: <code style="background:#161b22;padding:2px 6px;border-radius:4px">?path=/your/repo</code></p>
</div></body></html>""")
            return

        path = os.path.expanduser(path)
        if not os.path.isdir(path):
            self.send_error(400, f"Not a directory: {path}")
            return

        vcs, _ = detect_vcs(path)
        html = make_shell_html(vcs, path, refresh)

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _json_response(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        if any(x in args[0] for x in ['/content', '/hash']) if args else False:
            return
        pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def main():
    server = ThreadedHTTPServer(("127.0.0.1", PORT), DiffHandler)
    print(f"diff-server on http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
