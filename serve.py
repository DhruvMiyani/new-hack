"""Run Blindspot's local workbench: python serve.py --port 8765."""
from __future__ import annotations

import argparse
import base64
import json
import os
import threading
import time
from collections import OrderedDict
from uuid import uuid4
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

from blindspot.gcp import GCPError, inspect_gcp
from blindspot.providers import MetaMuse, SubQSnap
from blindspot.security import BundleError, SCENARIOS, run_security
from blindspot.repository import DEFAULT_TASK, RepositoryError, curate, load_demo, load_github, load_local, propose_change
from blindspot.context_benchmark import benchmark
from blindspot.mission import mission_state, investigate, check_mission
from blindspot import referrals

REFERRAL_ACTIONS = {
    "/api/referrals/document": referrals.document,
    "/api/referrals/assess": referrals.assess,
    "/api/referrals/review": referrals.review,
    "/api/referrals/reset": lambda data: referrals.reset(),
}
BUSY = threading.BoundedSemaphore(2)
SNAPSHOTS = OrderedDict()
SNAPSHOT_LOCK = threading.Lock()


def repository_action(path, data):
    if path == "/api/repository/load":
        source = data.get("source", "demo")
        if source == "demo":
            snapshot = load_demo()
        elif source == "github":
            snapshot = load_github(data.get("url", ""), data.get("ref", "HEAD"))
        elif source == "local":
            snapshot = load_local(data.get("path", ""), data.get("ref", "HEAD"))
        else:
            raise RepositoryError("Choose demo, public GitHub, or a local Git checkout.")
        key = uuid4().hex
        with SNAPSHOT_LOCK:
            while len(SNAPSHOTS) >= 8:
                SNAPSHOTS.popitem(last=False)
            SNAPSHOTS[key] = {"snapshot": snapshot, "created": time.monotonic(), "selections": {}}
        return {"id": key, "default_task": DEFAULT_TASK if snapshot.demo else "", **snapshot.public()}
    with SNAPSHOT_LOCK:
        cached = SNAPSHOTS.get(data.get("id"))
        if not cached or time.monotonic() - cached["created"] > 3600:
            raise RepositoryError("Snapshot expired. Reload the repository.")
    if path == "/api/repository/curate":
        result = curate(cached["snapshot"], data.get("task", ""), method=data.get("method", "subq"),
                        threshold=data.get("threshold", 0.5), top_k=data.get("top_k", 10))
        selection_id = uuid4().hex
        with SNAPSHOT_LOCK:
            if len(cached["selections"]) >= 8:
                del cached["selections"][next(iter(cached["selections"]))]
            cached["selections"][selection_id] = result
        return {"selection_id": selection_id, **result}
    if path in ("/api/repository/benchmark", "/api/repository/propose"):
        with SNAPSHOT_LOCK:
            selection = cached["selections"].get(data.get("selection_id"))
        if selection is None:
            raise RepositoryError("Run context curation before the migration experiment.")
        if path.endswith("/propose"):
            return propose_change(cached["snapshot"], selection)
        return benchmark(cached["snapshot"], selection, generator=data.get("generator", "reference"))
    raise RepositoryError("Unknown repository action.")


def validate_images(images):
    if not isinstance(images, list) or len(images) > 1:
        raise ValueError("Attach at most one screenshot.")
    for image in images:
        if not isinstance(image, dict) or set(image) != {"mediaType", "base64"}:
            raise ValueError("Invalid image payload.")
        kind = image["mediaType"]
        if kind not in ("image/png", "image/jpeg", "image/webp"):
            raise ValueError("Use a PNG, JPEG, or WebP image.")
        try:
            raw = base64.b64decode(image["base64"], validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid image encoding.") from exc
        if not raw or len(raw) > 2 * 1024 * 1024:
            raise ValueError("Screenshot must be at most 2 MiB.")
        valid = ((kind == "image/png" and raw.startswith(b"\x89PNG\r\n\x1a\n"))
                 or (kind == "image/jpeg" and raw.startswith(b"\xff\xd8\xff"))
                 or (kind == "image/webp" and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"))
        if not valid:
            raise ValueError("Image bytes do not match the stated format.")
    return images


def perform_action(path, data):
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object.")
    if path in REFERRAL_ACTIONS:
        return REFERRAL_ACTIONS[path](data)
    if path == "/api/mission/investigate":
        payload = dict(data)
        payload["images"] = validate_images(data.get("images", []))
        return investigate(payload)
    if path == "/api/mission/check":
        return check_mission(data)
    if path.startswith("/api/repository/"):
        return repository_action(path, data)
    if path == "/api/security":
        scenario = data.get("scenario", "combined")
        if scenario not in SCENARIOS or type(data.get("repair", False)) is not bool or type(data.get("models", False)) is not bool:
            raise ValueError("Invalid scenario or mode.")
        models = data.get("models", False)
        images = validate_images(data.get("images", []))
        if images and not models:
            raise ValueError("Screenshot reasoning needs model mode; offline verification uses configuration only.")
        report = run_security(scenario, repair=data.get("repair", False), use_models=models, images=images or None)
        return report.to_dict()
    if path == "/api/gcp":
        return inspect_gcp(data.get("project", ""), data.get("bucket", ""),
                           actor=data.get("actor", ""), object_name=data.get("object_name", ""))
    raise ValueError("Unknown action.")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "web" / "dist"), **kwargs)

    def _host_ok(self):
        return self.headers.get("Host", "") in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def respond(self, code, value):
        body = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if self.path.startswith("/api/referrals"):
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._host_ok():
            return self.respond(403, {"error": "Local access only."})
        if self.path == "/api/status":
            return self.respond(200, {"mode": "local", "meta": MetaMuse().available, "subq": SubQSnap().available})
        if self.path == "/api/referrals":
            return self.respond(200, referrals.inbox())
        if self.path == "/api/mission":
            return self.respond(200, mission_state())
        route = urlsplit(self.path).path
        if route in ("/", "/mission", "/mission/"):
            self.path = "/mission.html"
        elif route == "/workbench.html":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        origin = self.headers.get("Origin")
        allowed_origins = (f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}")
        if not self._host_ok() or (origin and origin not in allowed_origins):
            return self.respond(403, {"error": "Local origin required."})
        if self.path not in REFERRAL_ACTIONS and self.path not in ("/api/security", "/api/gcp", "/api/repository/load", "/api/repository/curate", "/api/repository/benchmark", "/api/repository/propose", "/api/mission/investigate", "/api/mission/check"):
            return self.respond(404, {"error": "Unknown action."})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.respond(415, {"error": "Send JSON."})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 1 or size > 3 * 1024 * 1024:
                return self.respond(413, {"error": "Request too large or empty."})
            data = json.loads(self.rfile.read(size))
        except (ValueError, OSError):
            return self.respond(400, {"error": "Invalid request."})
        if not BUSY.acquire(blocking=False):
            return self.respond(429, {"error": "Two checks are already running. Try again shortly."})
        try:
            self.respond(200, perform_action(self.path, data))
        except (BundleError, GCPError, ValueError, TypeError, KeyError) as exc:
            self.respond(400, {"error": str(exc), "final_verdict": "INCONCLUSIVE"})
        except Exception:
            self.respond(500, {"error": "Verification failed. No approval issued.", "final_verdict": "INCONCLUSIVE"})
        finally:
            BUSY.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Blindspot workbench: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
