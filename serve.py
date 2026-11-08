"""Serve the fictional Referral Desk locally: python serve.py --port 8765."""
from __future__ import annotations

import argparse
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

from blindspot import improve, muse, referrals

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web" / "dist"
ACTIONS = {
    "/api/referrals/document": referrals.document,
    "/api/referrals/assess": referrals.assess,
    "/api/referrals/triage": improve.evaluate_inbox,
    "/api/referrals/review": referrals.review,
    "/api/referrals/reset": lambda _data: _capabilities(referrals.reset()),
    "/api/referrals/muse": muse.inspect,
}
MODEL_ACTIONS = {"/api/referrals/assess", "/api/referrals/triage", "/api/referrals/muse"}
MODEL_LIMIT = threading.BoundedSemaphore(2)
MAX_BODY = 8192


def _capabilities(data):
    return {**data, "muse_available": muse.available()}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def _local_hosts(self):
        port = self.server.server_port
        return (f"localhost:{port}", f"127.0.0.1:{port}")

    def _host_ok(self):
        return self.headers.get("Host") in self._local_hosts()

    def respond(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._host_ok():
            return self.respond(403, {"error": "Local access only."})
        path = urlsplit(self.path).path
        if path == "/api/referrals":
            return self.respond(200, _capabilities(referrals.inbox()))
        if path.startswith("/api/"):
            return self.respond(404, {"error": "Unknown API route."})
        if path == "/":
            self.path = "/referrals.html"
        return super().do_GET()

    def do_HEAD(self):
        if not self._host_ok():
            self.send_error(403)
            return
        if urlsplit(self.path).path == "/":
            self.path = "/referrals.html"
        return super().do_HEAD()

    def do_POST(self):
        origin = self.headers.get("Origin")
        if not self._host_ok() or (origin and origin not in {f"http://{host}" for host in self._local_hosts()}):
            return self.respond(403, {"error": "Local origin required."})
        path = urlsplit(self.path).path
        action = ACTIONS.get(path)
        if action is None:
            return self.respond(404, {"error": "Unknown API route."})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.respond(415, {"error": "Send JSON."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= MAX_BODY:
                return self.respond(413, {"error": "Request too large or empty."})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                return self.respond(400, {"error": "Expected a JSON object."})
        except (ValueError, OSError):
            return self.respond(400, {"error": "Invalid JSON request."})

        uses_model = path in MODEL_ACTIONS
        if uses_model and not MODEL_LIMIT.acquire(blocking=False):
            return self.respond(429, {"error": "Two model requests are running. Try again shortly."})
        try:
            result = action(data)
        except ValueError as exc:
            return self.respond(400, {"error": str(exc)})
        except Exception:
            return self.respond(500, {"error": "Could not complete this referral action."})
        finally:
            if uses_model:
                MODEL_LIMIT.release()
        self.respond(200, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        print(f"Referral Desk: http://localhost:{args.port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
