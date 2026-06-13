"""Optional local dashboard server (Section 3g).

Serves ``dashboard.html`` from the project root and exposes the lead data at
both ``/leads.json`` and ``/api/leads`` (read from OUTPUT_DIR). The dashboard
also works by opening ``dashboard.html`` directly if a ``leads.json`` sits
beside it; this server is the zero-copy way to view live data.

Usage:
    python serve.py            # http://localhost:8000
    python serve.py 9000       # custom port
"""

from __future__ import annotations

import json
import os
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # serve the dashboard with zero third-party deps
    def load_dotenv():
        return False

ROOT = Path(__file__).parent


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, data_path: Path, **kwargs):
        self.data_path = data_path
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):  # noqa: N802 - stdlib naming
        if self.path in ("/api/leads", "/leads.json"):
            self._serve_leads()
            return
        if self.path in ("/", ""):
            self.path = "/dashboard.html"
        super().do_GET()

    def _serve_leads(self):
        try:
            payload = self.data_path.read_bytes() if self.data_path.exists() else b"[]"
            json.loads(payload or b"[]")  # validate
        except (OSError, ValueError):
            payload = b"[]"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):  # quieter logs
        pass


def main() -> None:
    load_dotenv()
    data_path = Path(os.getenv("OUTPUT_DIR") or "./leads") / "leads.json"
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    handler = partial(DashboardHandler, data_path=data_path)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"Serving dashboard at http://localhost:{port}  (data: {data_path})")
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
