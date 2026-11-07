"""Mission outcomes come from executed access, never a model's confidence."""
import copy
import json

import pytest

from blindspot.mission import MissionError, check_mission, investigate, mission_state
from blindspot.providers import Answer, DecisionResult


def run(repair="candidate", hypothesis="prod-public"):
    return check_mission({"repair": repair, "hypothesis": hypothesis})


def observation(report, actor, resource):
    return next(c for c in report["checks"] if c["actor"] == actor and c["resource"] == resource)


def test_candidate_reproduces_anonymous_read_of_private_production_backup():
    report = run()
    leak = observation(report, "anonymous", "prod-backups")
    assert report["verdict"] == "BLOCK" and not report["won"]
    assert leak["expected"] == "DENIED" and leak["actual"] == "ALLOWED"
    assert not leak["passed"] and leak["trace"]


def test_minimal_repair_blocks_exposure_and_preserves_real_functional_controls():
    report = run("revoke-public")
    assert report["won"] and report["verdict"] == "PASS"
    assert all(c["passed"] for c in report["checks"])
    assert observation(report, "anonymous", "prod-backups")["actual"] == "DENIED"
    assert observation(report, "anonymous", "public-assets")["actual"] == "ALLOWED"
    assert observation(report, "app-service", "prod-backups")["actual"] == "ALLOWED"


def test_deny_everything_cannot_win_by_breaking_the_product():
    report = run("deny-all")
    assert report["verdict"] == "BLOCK" and not report["won"]
    assert observation(report, "anonymous", "prod-backups")["actual"] == "DENIED"
    assert any(c["expected"] == "ALLOWED" and not c["passed"] for c in report["checks"])


@pytest.mark.parametrize("repair", ["private-ui", "wrong-bucket"])
def test_cosmetic_or_unrelated_repair_leaves_the_access_failure(repair):
    report = run(repair)
    assert report["verdict"] == "BLOCK" and not report["won"]
    assert observation(report, "anonymous", "prod-backups")["actual"] == "ALLOWED"


def test_all_repairs_use_identical_frozen_expected_checks_without_mutating_baseline():
    original = run()
    expected = [(c["id"], c["expected"]) for c in original["checks"]]
    for repair in ["revoke-public", "deny-all", "private-ui", "wrong-bucket"]:
        report = run(repair)
        assert report["contract_hash"] == original["contract_hash"]
        assert [(c["id"], c["expected"]) for c in report["checks"]] == expected
        assert report["candidate_hash"] != original["candidate_hash"]
    assert run() == original


@pytest.mark.parametrize("data", [
    {"repair": "../../.env", "hypothesis": "prod-public"},
    {"repair": "revoke-public", "hypothesis": "not-a-hypothesis"},
    {"repair": {"custom": "settings"}, "hypothesis": "prod-public"},
])
def test_client_cannot_submit_unknown_repairs_or_hypotheses(data):
    with pytest.raises((MissionError, TypeError)):
        check_mission(data)


class SnapStub:
    available = True
    model = "test-snap-not-live"

    def __init__(self, *, error="", transform=None):
        self.calls = []
        self.error = error
        self.transform = transform

    def decide(self, state, questions, **kwargs):
        self.calls.append((state, questions, kwargs))
        answers = {}
        for key, question in questions.items():
            kind = question["type"]
            if kind == "noul":
                answers[key] = Answer(key, kind, 0.75)
            elif kind == "choice":
                options = question["criteria"]
                picked = next((p for p in ["prod-public", "revoke-public"] if p in options), next(iter(options)))
                answers[key] = Answer(key, kind, picked, confidence=1.0,
                                      probabilities={p: float(p == picked) for p in options})
            else:
                raise AssertionError(f"Unexpected question type: {kind}")
        if self.transform:
            answers = self.transform(answers)
        return DecisionResult(answers=answers, input_tokens=1234, error=self.error)


def investigate_with(snap, **kwargs):
    return investigate({"context_mode": "full", "clues": [], **kwargs}, snap=snap)


def test_live_result_has_actual_usage_and_all_artifact_scores():
    snap = SnapStub()
    report = investigate_with(snap)
    assert report["source"] == "live"
    assert report["usage"]["input_tokens"] == 1234
    assert report["usage"]["output_tokens"] == 0
    assert report["usage"]["calls"] == 1
    assert len(report["evidence"]) == len(mission_state()["artifacts"])
    assert {h["id"] for h in report["hypotheses"]} == {h["id"] for h in mission_state()["hypotheses"]}
    assert sum(h["probability"] for h in report["hypotheses"]) == pytest.approx(1)


def test_full_context_really_contains_all_eligible_artifacts_and_no_result_labels():
    snap = SnapStub()
    investigate_with(snap, clues=["A01"])
    state = snap.calls[0][0]
    if not isinstance(state, str):
        state = json.dumps(state)
    for artifact in mission_state()["artifacts"]:
        assert artifact["path"] in state
    assert '"prediction_correct"' not in state and '"won"' not in state
    assert '"actual": "ALLOWED"' not in state


def test_selected_clues_are_a_distinct_smaller_context():
    full, partial = SnapStub(), SnapStub()
    investigate_with(full)
    investigate_with(partial, context_mode="selected", clues=["A01"])
    full_text = str(full.calls[0][0])
    selected_text = str(partial.calls[0][0])
    assert len(selected_text) < len(full_text)
    assert mission_state()["artifacts"][0]["path"] in selected_text


def test_missing_key_never_fabricates_model_probabilities():
    snap = SnapStub()
    snap.available = False
    report = investigate_with(snap)
    assert report["source"] == "unavailable" and report["error"]
    assert not snap.calls
    assert not report.get("hypotheses") and not report.get("evidence")


def test_failed_call_never_falls_back_to_a_fake_live_result():
    report = investigate_with(SnapStub(error="temporary test outage"))
    assert report["source"] == "unavailable" and report["error"]
    assert not report.get("hypotheses") and not report.get("evidence")


def test_incomplete_answers_are_unavailable_instead_of_partial_predictions():
    report = investigate_with(SnapStub(transform=lambda a: {}))
    assert report["source"] == "unavailable" and report["error"]


@pytest.mark.parametrize("value", [float("nan"), -0.1, 1.1, "0.75"])
def test_invalid_relevance_probabilities_are_not_accepted(value):
    def change(answers):
        key = next(k for k, answer in answers.items() if answer.kind == "noul")
        answers[key] = Answer(key, "noul", value)
        return answers
    report = investigate_with(SnapStub(transform=change))
    assert report["source"] == "unavailable" and report["error"]


@pytest.mark.parametrize("mode", ["unknown-choice", "missing-option", "nan", "unnormalized"])
def test_invalid_choice_distributions_do_not_reach_the_probability_visualization(mode):
    def change(answers):
        answer = next(a for a in answers.values() if a.kind == "choice")
        if mode == "unknown-choice":
            answer.value = "not-in-the-candidate-space"
        elif mode == "missing-option":
            del answer.probabilities[next(iter(answer.probabilities))]
        elif mode == "nan":
            answer.probabilities[next(iter(answer.probabilities))] = float("nan")
        else:
            answer.probabilities = {p: 1.0 for p in answer.probabilities}
        return answers
    report = investigate_with(SnapStub(transform=change))
    assert report["source"] == "unavailable" and report["error"]
    assert not report["hypotheses"] and not report["evidence"]


def test_upstream_error_content_is_not_echoed_to_the_browser():
    report = investigate_with(SnapStub(error="Provider error might echo synthetic-secret-for-test"))
    assert "synthetic-secret-for-test" not in json.dumps(report)


def test_model_advice_cannot_change_the_independent_verdict():
    before = copy.deepcopy(run())
    investigate_with(SnapStub())
    assert run() == before
    assert run()["verdict"] == "BLOCK"
    assert run("revoke-public")["verdict"] == "PASS"


@pytest.mark.parametrize("data", [
    {"context_mode": "invalid", "clues": []},
    {"context_mode": "selected", "clues": ["../../.env"]},
    {"context_mode": "full", "clues": "A01"},
])
def test_invalid_context_requests_never_reach_the_model(data):
    snap = SnapStub()
    with pytest.raises((MissionError, TypeError)):
        investigate(data, snap=snap)
    assert not snap.calls
