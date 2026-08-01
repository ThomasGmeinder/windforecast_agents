#!/usr/bin/env python3
"""
serve.py — LOCAL preview server for the daily wind report (development only).

The PUBLIC report is the static GitHub Pages site
(https://thomasgmeinder.github.io/windforecast_agents/), built by build_site.py in
the daily workflow. This server just renders the same pages on the fly from the
latest local logs for local development at http://localhost:8092/ — it is not part
of the public deployment.

Run:  .venv/bin/python serve.py
"""
import os, sys, traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import render

HOST = os.environ.get("WIND_WEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("WIND_WEB_PORT", "8092"))


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        group = path.lstrip("/")
        try:
            if path in ("/", "/index.html"):
                self._send(200, render.index_html())
            elif group in render.GROUPS:
                self._send(200, render.report_html(group))
            elif path == "/health":
                self._send(200, "ok", "text/plain; charset=utf-8")
            elif path == "/favicon.ico":
                self._send(204, b"")
            else:
                self._send(404, "not found", "text/plain; charset=utf-8")
        except Exception:
            self._send(500, "<pre>render error\n\n" + traceback.format_exc() + "</pre>")

    do_HEAD = do_GET

    def log_message(self, *a):
        pass  # quiet; systemd journal captures errors via stderr


if __name__ == "__main__":
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"wind report on http://{HOST}:{PORT}/  (also http://wind.localhost:{PORT}/)", flush=True)
    srv.serve_forever()
