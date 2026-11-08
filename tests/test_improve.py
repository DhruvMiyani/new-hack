"""Tests for the referral self-improvement loop.

The ones that matter are the anti-gaming tests: the split must not drift, a
proposal that names a referral must be rejected as memorisation, and a change
that breaks something previously correct must not ship no matter what it gains.
"""
from __future__ import annotations

import json

from blindspot import improve, referrals, triage


def _version(name="DEMO-9.0", policy="p", rules=None, parent="DEMO-1.0"):
    book = {"name": "t", "version": name, "specialist": "s",
            "accepted_counties": ["San Francisco"], "rules": rules or []}
    return improve.Version(version=name, policy=policy, rules_book=book, parent=parent)


def test_split_is_frozen_and_disjoint():
    a = improve.load_split()
    b = improve.load_split()
    assert a == b, "the split must not change between calls"
    assert not set(a["dev"]) & set(a["held_out"])
    assert set(a["dev"]) | set(a["held_out"]) == set(referrals.CASES)
    assert len(a["held_out"]) >= 1


def test_ground_truth_comes_from_fixtures_not_a_model():
    ids = sorted(referrals.CASES)[:5]
    t = improve.truth(ids)
    assert set(t) == set(ids)
    assert all(v in referrals.LABELS for v in t.values())


def test_score_sends_only_the_requested_split_and_uses_first_pass(monkeypatch):
    version = improve.ablate(improve.baseline())
    identifier = "R101"

    def evaluate(data, **kwargs):
        assert kwargs == {"identifiers": [identifier], "policy": version.policy,
                          "routing_instructions": version.routing_instructions,
                          "policy_version": version.version, "recheck": False}
        assert referrals.POLICY != version.policy
        return {"available": True, "request": {}, "rows": [{"id": identifier, "route": "low"}]}

    monkeypatch.setattr(triage, "evaluate", evaluate)
    assert improve.score(version, [identifier]).disagreements == {identifier: ("critical", "low")}


def test_version_numbering_increments():
    assert improve.next_version("DEMO-1.0") == "DEMO-1.1"
    assert improve.next_version("DEMO-1.9") == "DEMO-1.10"


def test_proposal_naming_a_referral_is_rejected_as_memorisation():
    target = sorted(referrals.CASES)[0]
    bad = _version(policy=f"Always route {target} to critical.")
    assert improve.mentions_a_referral_id(bad) == [target]

    decision = improve.consider(bad, improve.baseline(), [target])
    assert not decision.promoted
    assert "memorisation" in decision.reason


def test_gate_rejects_a_change_that_breaks_something_that_worked(monkeypatch):
    held = ["A", "B", "C"]
    incumbent, candidate = _version("DEMO-1.0"), _version("DEMO-1.1")

    def fake_score(version, ids, scope="packet"):
        if version.version == "DEMO-1.0":   # A wrong, B and C right
            return improve.Score(agreed=2, total=3, disagreements={"A": ("high", "low")})
        # candidate fixes A but breaks B -- a net gain that must still be refused
        return improve.Score(agreed=2, total=3, disagreements={"B": ("low", "high")})

    monkeypatch.setattr(improve, "score", fake_score)
    d = improve.consider(candidate, incumbent, held)
    assert not d.promoted
    assert "broke" in d.reason


def test_gate_rejects_no_gain(monkeypatch):
    incumbent, candidate = _version("DEMO-1.0"), _version("DEMO-1.1")
    monkeypatch.setattr(
        improve, "score",
        lambda v, ids, scope="packet": improve.Score(agreed=2, total=3,
                                                     disagreements={"A": ("high", "low")}),
    )
    d = improve.consider(candidate, incumbent, ["A", "B", "C"])
    assert not d.promoted and "no held-out gain" in d.reason


def test_gate_promotes_a_clean_improvement(monkeypatch):
    incumbent, candidate = _version("DEMO-1.0"), _version("DEMO-1.1")

    def fake_score(version, ids, scope="packet"):
        if version.version == "DEMO-1.0":
            return improve.Score(agreed=2, total=3, disagreements={"A": ("high", "low")})
        return improve.Score(agreed=3, total=3, disagreements={})

    monkeypatch.setattr(improve, "score", fake_score)
    d = improve.consider(candidate, incumbent, ["A", "B", "C"])
    assert d.promoted and "accepted" in d.reason
    assert candidate.promoted


def test_proposer_cannot_widen_the_accepted_counties():
    """The one field a self-improving router must never edit for itself."""
    current = improve.baseline()
    reply = json.dumps({
        "notes": "widen the area",
        "policy": "revised policy",
        "routing_instructions": "read signed report",
        "accepted_counties": ["Anywhere"],
    })

    class _Muse:
        available = True
        def complete(self, system, user, **kwargs):
            assert "R101" not in user
            assert "expected high, predicted low: 1" in user
            return f"```json\n{reply}\n```"

    candidate = improve.propose(current, {"R101": ("high", "low")}, _Muse())
    assert candidate is not None
    assert candidate.rules_book == current.rules_book
    assert candidate.policy == "revised policy"          # wording did change
    assert candidate.routing_instructions == "read signed report"
    assert candidate.parent == current.version


def test_propose_returns_none_without_a_key():
    class _Absent:
        available = False
        def complete(self, *a, **k):
            return None

    assert improve.propose(improve.baseline(), {"R1": ("high", "low")}, _Absent()) is None


def test_propose_returns_none_when_there_is_nothing_to_learn_from():
    class _Muse:
        available = True
        def complete(self, *a, **k):
            raise AssertionError("should not be called with no failures")

    assert improve.propose(improve.baseline(), {}, _Muse()) is None


def test_experiment_promotion_cannot_activate_inbox_policy(tmp_path, monkeypatch):
    monkeypatch.setattr(improve, "RULES_DIR", tmp_path)
    candidate = improve.ablate(improve.baseline())
    candidate.promoted = True
    candidate.save()
    assert improve.load_version(candidate.version).experiment
    assert improve.latest_promoted().version == improve.baseline().version
