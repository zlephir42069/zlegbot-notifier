"""Shared fixtures. HTTP stays on 127.0.0.1."""

from __future__ import annotations

import threading
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# http.server closes the accepted fd from the makefile, and CPython then warns
# that the socket wrapper is still around. The fd is already closed.
warnings.filterwarnings(
    "ignore",
    message=r"unclosed <socket\.socket",
    category=ResourceWarning,
)

REPO = Path(__file__).resolve().parents[1]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b""
        self.server.posts.append(
            {
                "path": self.path,
                "body": raw,
                "authorization": self.headers.get("Authorization"),
                "content_type": self.headers.get("Content-Type"),
                "user_agent": self.headers.get("User-Agent"),
            }
        )
        if self.server.statuses:
            status = self.server.statuses.pop(0)
        elif self.server.fail_remaining > 0:
            self.server.fail_remaining -= 1
            status = 500
        else:
            status = 200
        if status == 302:
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:9/stolen")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"ok" if status < 400 else b"err"
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


class LocalServer:
    def __init__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.httpd.posts = []
        self.httpd.statuses = []
        self.httpd.fail_remaining = 0
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self._thread.join(timeout=5)
