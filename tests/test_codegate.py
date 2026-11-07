"""Tests for CodeGate.

The ones that matter most are the discipline tests: an unproven finding must
never block, and the promotion gate must reject a candidate that makes the
agent noisier. Those are the claims the whole project rests on.
"""

from __future__ import annotations

import pytest

from codegate import learn
from codegate.corpus import load_cases
from codegate.detectors import REGISTRY, Rule, Scanner
from codegate.models import Decision, Severity, Status
from codegate.pipeline import review
from codegate.policy import from_env
from codegate.prober import analyse, scan
from codegate.sandbox import run_probe
from codegate.sources import from_diff, resolve
from codegate.verdict import decide

ALL_RULES = set(REGISTRY)


# --------------------------------------------------------------------------- #
# static detection
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c.case_id)
def test_static_scan_matches_labels(case):
    """Every labelled rule is at least a static candidate, and clean stays clean."""
    fired = {f.rule_id for f in scan(case.source, case.path.name, ALL_RULES)}
    assert case.expect <= fired, f"{case.case_id}: missed {case.expect - fired}"
    if case.is_clean:
        assert not fired, f"{case.case_id}: false positives {fired}"


# --------------------------------------------------------------------------- #
# probes actually reproduce
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "case", [c for c in load_cases() if c.expect], ids=lambda c: c.case_id
)
def test_probeable_defects_are_reproduced(case):
    """A rule with a probe must reach OBSERVED, not merely match a pattern."""
    cfg = learn.latest_promoted().probe_config
    findings = analyse(case.source, case.path.name, True, ALL_RULES, cfg)
    for rule_id in case.expect:
        hits = [f for f in findings if f.rule_id == rule_id]
        assert hits, f"{case.case_id}: {rule_id} did not fire"
        if REGISTRY[rule_id].probe is None:
            assert all(f.status is Status.INFERRED for f in hits)
        else:
            assert any(f.status is Status.OBSERVED for f in hits), (
                f"{case.case_id}: {rule_id} matched but was not reproduced "
                f"({[f.status.value for f in hits]})"
            )


def test_clean_code_is_allowed():
    src = (
        "def mean(values):\n"
        "    if not values:\n"
        "        return 0.0\n"
        "    return sum(values) / len(values)\n"
    )
    result = review(src)
    assert result.verdict.decision is Decision.ALLOW
    assert not result.verdict.findings


def test_buggy_code_is_blocked_with_evidence():
    src = "def tally(word, counts={}):\n    counts[word] = counts.get(word, 0) + 1\n    return counts\n"
    v = review(src).verdict
    assert v.decision is Decision.BLOCK
    observed = v.observed
    assert observed and all(f.evidence and f.evidence.reproduced for f in observed)
    # the evidence must contain real output, not a summary we wrote
    assert any("AFTER" in f.evidence.stdout for f in observed)


# --------------------------------------------------------------------------- #
# the gate discipline
# --------------------------------------------------------------------------- #


def test_inferred_findings_never_block():
    """The core rule: suspicion cannot block a merge, whatever its severity."""
    src = "def risky(x):\n    try:\n        return int(x)\n    except:\n        return None\n"
    findings = scan(src, "t.py", ALL_RULES)
    assert findings, "expected the bare-except rule to match"
    for f in findings:
        f.status = Status.INFERRED
        f.severity = Severity.CRITICAL  # even at the top severity
        assert not f.blocking
    assert decide(findings).decision is Decision.REVIEW


def test_refuted_findings_do_not_block_or_warn():
    src = "def tally(word, counts={}):\n    return counts\n"
    findings = scan(src, "t.py", ALL_RULES)
    for f in findings:
        f.status = Status.REFUTED
    v = decide(findings)
    assert v.decision is Decision.ALLOW
    assert not v.observed and not v.inferred


def test_static_only_rules_have_no_probe():
    for rid in ("CG008", "CG010"):
        assert REGISTRY[rid].probe is None


# --------------------------------------------------------------------------- #
# the learning loop
# --------------------------------------------------------------------------- #


def test_gate_rejects_a_noisy_candidate():
    """A rule that fires on everything must be refused, however much it recalls."""
    noisy_id = "CGTEST_NOISE"

    def matcher(s: Scanner):
        import ast

        for node in s.walk():
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                s.add(node, target={"function": node.name})

    REGISTRY[noisy_id] = Rule(
        noisy_id, "Fires on every function", Severity.MEDIUM, "deliberately noisy", matcher, None
    )
    try:
        incumbent = learn.latest_promoted()
        candidate = learn.propose_enabling({noisy_id}, incumbent)
        report = learn.consider(candidate, incumbent, run_probes=False)
        assert not report.promoted
        assert "precision" in report.reason or "flagged" in report.reason
    finally:
        REGISTRY.pop(noisy_id, None)


def test_gate_rejects_a_candidate_with_no_recall_gain():
    incumbent = learn.latest_promoted()
    candidate = learn.propose_enabling(set(), incumbent)  # identical ruleset
    report = learn.consider(candidate, incumbent, run_probes=False)
    assert not report.promoted
    assert "no recall gain" in report.reason


def test_promoted_ruleset_beats_its_parent_on_heldout():
    """Every promoted version on disk must still justify itself."""
    import json
    from pathlib import Path

    rules_dir = Path(__file__).resolve().parent.parent / "rules"
    for path in sorted(rules_dir.glob("v*.json")):
        data = json.loads(path.read_text())
        if not data.get("promoted") or not data.get("parent"):
            continue
        child = learn.load_ruleset(data["version"])
        parent = learn.load_ruleset(data["parent"])
        before = learn.evaluate(parent, "heldout")
        after = learn.evaluate(child, "heldout")
        assert after.recall > before.recall, f"{child.version} claims promotion without a recall gain"
        assert after.false_positive_cases <= before.false_positive_cases


# --------------------------------------------------------------------------- #
# input handling
# --------------------------------------------------------------------------- #


def test_resolve_snippet_and_fences():
    s = resolve("```python\ndef f():\n    return 1\n```")
    assert s.kind == "snippet" and s.units and s.units[0].executable


def test_resolve_rejects_prose():
    s = resolve("please review my code thanks")
    assert not s.analysable and s.warnings


def test_diff_fragment_is_marked_non_executable():
    diff = (
        "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n@@ -1,3 +1,3 @@\n"
        "     total = 0\n+    return total / len(xs)\n"
    )
    sub = from_diff(diff)
    assert sub.units
    assert not sub.units[0].executable
    assert "static analysis only" in sub.units[0].note


def test_non_executable_input_cannot_block():
    diff = (
        "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n@@ -1,3 +1,3 @@\n"
        "     total = 0\n+    return total / len(xs)\n"
    )
    v = review(diff).verdict
    assert v.decision is not Decision.BLOCK
    assert all(f.status is not Status.OBSERVED for f in v.findings)


# --------------------------------------------------------------------------- #
# sandbox and policy
# --------------------------------------------------------------------------- #


def test_sandbox_blocks_network():
    probe = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('example.com', 80), timeout=2)\n"
        "    report(False, 'network reachable')\n"
        "except Exception as e:\n"
        "    report(True, 'blocked: %r' % (e,))\n"
    )
    run = run_probe("", probe)
    assert "blocked" in run.stdout


def test_sandbox_kills_a_hanging_probe():
    run = run_probe("", "while True:\n    pass\n", timeout=3.0)
    assert run.timed_out or run.exit_code != 0


def test_hanging_probe_leaves_finding_unproven():
    """A probe that never finishes must not be read as evidence either way."""
    from codegate.models import Finding
    from codegate.prober import verify

    src = "def slow(xs):\n    while True:\n        pass\n"
    f = Finding(
        rule_id="CG002", title="t", severity=Severity.HIGH, path="t.py", line=1,
        snippet="", rationale="", target={"function": "slow", "param": "xs"},
    )
    verify([f], src, timeout=3.0)
    assert f.status is Status.INFERRED
    assert not f.blocking


def test_execution_is_off_by_default(monkeypatch):
    monkeypatch.delenv("CODEGATE_EXEC_POLICY", raising=False)
    assert from_env().mode == "off"
    assert not from_env().executes


def test_unknown_policy_fails_closed(monkeypatch):
    monkeypatch.setenv("CODEGATE_EXEC_POLICY", "yolo")
    assert from_env().mode == "off"


def test_prover_mode_without_address_is_flagged(monkeypatch):
    monkeypatch.setenv("CODEGATE_EXEC_POLICY", "prover")
    monkeypatch.delenv("PROVER_ADDRESS", raising=False)
    assert any("PROVER_ADDRESS" in p for p in from_env().problems())


# --------------------------------------------------------------------------- #
# evidence integrity
# --------------------------------------------------------------------------- #


def test_evidence_digest_changes_with_output():
    src = "def tally(w, c={}):\n    c[w] = 1\n    return c\n"
    a = [f for f in analyse(src, "a.py", True, ALL_RULES) if f.evidence]
    assert a
    ev = a[0].evidence
    first = ev.digest()
    ev.stdout += "tampered"
    assert ev.digest() != first


def test_observed_findings_always_carry_evidence():
    src = (corpus_src := load_cases("dev")[0]).source
    for f in analyse(src, corpus_src.path.name, True, ALL_RULES):
        if f.status is Status.OBSERVED:
            assert f.evidence is not None and f.evidence.reproduced
