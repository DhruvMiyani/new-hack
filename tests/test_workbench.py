"""Read-only cloud proof boundaries and actual local HTTP integration."""
import io
import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager

import pytest

from blindspot.gcp import GCPError, inspect_gcp


ACTOR = "former-demo@demo-project.iam.gserviceaccount.com"


def runner(args):
    if args[:3] == ["storage", "buckets", "describe"]:
        return json.dumps({"name": "demo-bucket", "projectNumber": "123456", "iamConfiguration": {"publicAccessPrevention": "enforced"}})
    if args[:3] == ["storage", "buckets", "get-iam-policy"]:
        return json.dumps({"bindings": []})
    if args[:2] == ["projects", "get-iam-policy"]:
        return json.dumps({"bindings": []})
    if args[:2] == ["projects", "describe"]:
        return json.dumps({"projectNumber": "123456"})
    if args[:2] == ["auth", "print-access-token"]:
        return "actor-token" if len(args) > 2 else "operator-token"
    raise AssertionError(f"Unexpected cloud operation: {args}")


class Response(io.BytesIO):
    status = 200


def opener_for(actor_status=200, operator_status=200):
    def opener(req, **kwargs):
        status = actor_status if req.headers["Authorization"] == "Bearer actor-token" else operator_status
        if status != 200:
            raise urllib.error.HTTPError(req.full_url, status, "test error", {}, None)
        return Response(b"BLINDSPOT_DEMO_ONLY")
    return opener


def test_live_inventory_never_claims_executed_access_without_a_probe():
    result = inspect_gcp("demo-project", "demo-bucket", runner=runner)
    assert result["verdict"] == "REVIEW"
    assert "probe" not in result
    assert "No canary probe" in " ".join(result["notes"])


@pytest.mark.parametrize("actor_status,control_status,verdict", [
    (200, 200, "BLOCK"), (403, 200, "PASS"),
    (404, 200, "INCONCLUSIVE"), (401, 200, "INCONCLUSIVE"),
    (403, 403, "INCONCLUSIVE"), (200, 404, "INCONCLUSIVE"),
])
def test_cloud_verdict_requires_the_expected_identity_and_positive_control(actor_status, control_status, verdict):
    result = inspect_gcp("demo-project", "demo-bucket", actor=ACTOR, object_name="blindspot-canary.txt",
                         runner=runner, opener=opener_for(actor_status, control_status))
    assert result["verdict"] == verdict
    serialized = json.dumps(result)
    assert "actor-token" not in serialized and "operator-token" not in serialized
    assert "BLINDSPOT_DEMO_ONLY" not in serialized  # hashes only; no object content exported


def test_overridden_public_binding_is_not_reported_as_confirmed_public_access():
    def public_runner(args):
        if args[:3] == ["storage", "buckets", "get-iam-policy"]:
            return json.dumps({"bindings": [{"role": "roles/storage.objectViewer", "members": ["allUsers"]}]})
        return runner(args)
    result = inspect_gcp("demo-project", "demo-bucket", runner=public_runner)
    assert result["verdict"] == "REVIEW"
    assert "prevention is enforced" in result["findings"][0]["evidence"]


def test_bucket_must_belong_to_explicit_project():
    def wrong_project(args):
        if args[:2] == ["projects", "describe"]:
            return json.dumps({"projectNumber": "999999"})
        return runner(args)
    with pytest.raises(GCPError, match="ownership"):
        inspect_gcp("demo-project", "demo-bucket", runner=wrong_project)


@pytest.mark.parametrize("project,bucket,actor,obj", [
    ("--help", "demo-bucket", "", ""),
    ("demo-project", "gs://demo-bucket", "", ""),
    ("demo-project", "demo-bucket", "--flags-file=bad", "blindspot-canary.txt"),
    ("demo-project", "demo-bucket", ACTOR, "customer-secrets.txt"),
    ("demo-project", "demo-bucket", ACTOR, ""),
])
def test_unscoped_or_command_like_cloud_inputs_never_reach_cli(project, bucket, actor, obj):
    def no_run(*args):
        pytest.fail("Invalid input reached cloud command")
    with pytest.raises(GCPError):
        inspect_gcp(project, bucket, actor=actor, object_name=obj, runner=no_run)


def test_cloud_calls_are_read_only_and_tokens_are_not_command_arguments():
    calls = []
    def record(args):
        calls.append(args)
        return runner(args)
    inspect_gcp("demo-project", "demo-bucket", actor=ACTOR, object_name="demo/canary.txt",
                runner=record, opener=opener_for())
    assert not any(word in ("set-iam-policy", "add-iam-policy-binding", "delete", "create") for args in calls for word in args)
    assert not any("actor-token" in word or "operator-token" in word for args in calls for word in args)


@contextmanager
def local_server():
    from http.server import ThreadingHTTPServer
    from serve import Handler
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_workbench_http_executes_before_after_instead_of_replaying():
    with local_server() as origin:
        with urllib.request.urlopen(origin + "/") as res:
            assert b"Security Workbench" in res.read()
        for repair, verdict in ((False, "BLOCK"), (True, "PASS")):
            req = urllib.request.Request(origin + "/api/security", data=json.dumps({"scenario": "combined", "repair": repair}).encode(),
                                         headers={"Content-Type": "application/json", "Origin": origin})
            with urllib.request.urlopen(req) as res:
                evidence = json.load(res)
            assert evidence["final_verdict"] == verdict
            assert len(evidence["observations"]) == 24


def test_workbench_blocks_cross_origin_actions_and_dns_rebinding():
    with local_server() as origin:
        req = urllib.request.Request(origin + "/api/security", data=b"{}",
                                     headers={"Content-Type": "application/json", "Origin": "https://other.example"})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)
        assert exc.value.code == 403
        req = urllib.request.Request(origin + "/api/status", headers={"Host": "other.example"})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)
        assert exc.value.code == 403


def test_screenshot_mode_is_explicit_and_images_are_validated():
    from serve import perform_action, validate_images
    with pytest.raises(ValueError, match="bytes"):
        validate_images([{"mediaType": "image/png", "base64": "bm90LWEtcG5n"}])
    with pytest.raises(ValueError, match="model mode"):
        perform_action("/api/security", {"images": [{"mediaType": "image/png", "base64": "iVBORw0KGgo="}]})


def test_hosted_fixture_matches_current_executed_results():
    from pathlib import Path
    from blindspot.security import run_security
    data = json.loads((Path(__file__).resolve().parents[1] / "web/dist/scenarios.json").read_text())
    assert data["mode"] == "verified-replay"
    for scenario, stored in data["scenarios"].items():
        assert stored["before"] == run_security(scenario).to_dict()
        assert stored["repair"] == run_security(scenario, repair=True).to_dict()
