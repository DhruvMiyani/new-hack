"""Exercise the mission through the same local HTTP boundary used by the UI."""
import json
import urllib.error
import urllib.request

import pytest

from test_workbench import local_server


def post(origin, path, body, *, request_origin=None):
    return urllib.request.urlopen(urllib.request.Request(
        origin + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Origin": request_origin or origin},
    ))


def test_http_mission_load_and_before_after_execute_same_contract():
    with local_server() as origin:
        with urllib.request.urlopen(origin + "/api/mission") as response:
            mission = json.load(response)
        assert mission["artifacts"] and mission["hypotheses"] and mission["repairs"]
        reports = []
        for repair, verdict in [("candidate", "BLOCK"), ("deny-all", "BLOCK"), ("revoke-public", "PASS")]:
            with post(origin, "/api/mission/check", {"hypothesis": "prod-public", "repair": repair}) as response:
                report = json.load(response)
            assert report["verdict"] == verdict
            assert report["won"] == (verdict == "PASS")
            assert report["checks"] and report["contract_hash"] == mission["contract_hash"]
            reports.append(report)
        assert len({r["candidate_hash"] for r in reports}) == 3


def test_mission_http_rejects_forged_verdict_or_repair_and_cross_origin():
    with local_server() as origin:
        with pytest.raises(urllib.error.HTTPError) as error:
            post(origin, "/api/mission/check", {"repair": "invented", "won": True})
        assert error.value.code == 400
        with pytest.raises(urllib.error.HTTPError) as error:
            post(origin, "/api/mission/check", {}, request_origin="https://other.example")
        assert error.value.code == 403


def test_mission_image_bytes_are_validated_before_any_model_call():
    from serve import perform_action
    with pytest.raises(ValueError, match="bytes"):
        perform_action("/api/mission/investigate", {"images": [{"mediaType": "image/png", "base64": "bm90LWEtcG5n"}]})
