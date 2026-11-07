"""Security outcomes, repair integrity, and the ASI:One execution path."""
import asyncio
import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from blindspot.providers import Answer, DecisionResult
from blindspot.security import (
    ACCESS, DEFAULT_BUNDLE, DIRECTORY, POLICY, STORAGE, BundleError,
    apply_repair, compile_contract, digest, evaluate, load_bundle,
    run_security, scenario_bundle,
)


@pytest.mark.parametrize("scenario,actor,action", [
    ("public-storage", "anonymous", "read_document"),
    ("public-storage", "alice", "read_credential"),
    ("privilege-escalation", "alice", "assign_admin"),
    ("privilege-escalation", "demoted-admin", "administer"),
    ("revoked-access", "former-employee", "read_document"),
])
def test_reproduces_specific_unauthorized_request(scenario, actor, action):
    report = run_security(scenario)
    observed = next(o for o in report.observations if o.probe.actor == actor and o.probe.action == action)
    assert report.verdict == "BLOCK"
    assert observed.allowed and not observed.probe.expected_allowed and not observed.passed
    assert observed.trace


def test_clean_candidate_preserves_member_and_admin_access():
    report = run_security("clean")
    assert report.final_verdict == "PASS"
    assert all(o.passed for o in report.observations)
    assert any(o.allowed and o.probe.actor == "alice" for o in report.observations)
    assert any(o.allowed and o.probe.action == "administer" for o in report.observations)


@pytest.mark.parametrize("scenario", ["combined", "public-storage", "privilege-escalation", "revoked-access"])
def test_repair_is_retested_against_identical_frozen_contract(scenario):
    report = run_security(scenario, repair=True)
    assert report.verdict == "BLOCK" and report.repair_verdict == "PASS"
    assert [o.probe for o in report.observations] == [o.probe for o in report.repaired_observations]
    assert report.candidate_hash != report.repaired_hash
    assert "offline" in report.repair_source and "not AI-generated" in report.repair_source


def test_contract_is_independent_of_candidate_settings():
    base = load_bundle()
    assert compile_contract(base) == compile_contract(scenario_bundle(base, "combined"))


def test_privilege_transition_runs_without_contaminating_other_probes():
    bundle = scenario_bundle(load_bundle(), "privilege-escalation")
    before = copy.deepcopy(bundle)
    observations = evaluate(bundle, compile_contract(bundle))
    escalation = next(o for o in observations if o.probe.actor == "alice" and o.probe.action == "assign_admin")
    direct_admin = next(o for o in observations if o.probe.actor == "alice" and o.probe.action == "administer")
    assert escalation.allowed and not direct_admin.allowed
    assert "replaying administrator request" in " ".join(escalation.trace)
    assert bundle == before


@pytest.mark.parametrize("name,pointer,value", [
    (POLICY, "/privileged_roles", ["member", "admin"]),
    (DIRECTORY, "/users/0/role", "admin"),
    ("../../.env", "/key", "anything"),
    (STORAGE, "/buckets/0/id", "unprotected"),
    (ACCESS, "/role_source", "unrecognized-engine"),
    (STORAGE, "/buckets/0/public_read", "false"),
])
def test_repair_cannot_rewrite_or_bypass_the_oracle(name, pointer, value):
    base = load_bundle()
    before = digest(base)
    with pytest.raises(BundleError):
        apply_repair(base, [{"file": name, "pointer": pointer, "value": value}])
    assert digest(base) == before


def test_deny_everything_fix_fails_legitimate_access_controls():
    candidate = scenario_bundle(load_bundle(), "combined")
    bad_fix = apply_repair(candidate, [
        {"file": STORAGE, "pointer": "/buckets/0/public_read", "value": False},
        {"file": STORAGE, "pointer": "/buckets/0/read_roles", "value": []},
        {"file": STORAGE, "pointer": "/buckets/0/credential_roles", "value": []},
    ])
    observations = evaluate(bad_fix, compile_contract(candidate))
    assert any(o.probe.expected_allowed and not o.allowed for o in observations)


class SnapStub:
    def __init__(self, error=""):
        self.context = None
        self.error = error

    def decide(self, state, questions, **kwargs):
        self.context = state
        return DecisionResult(
            answers={"exposure": Answer("exposure", "noul", 0.0)},
            input_tokens=2000, error=self.error,
        )


class MuseStub:
    model = "stub-meta"

    def __init__(self, response):
        self.response = response

    def complete(self, *args, **kwargs):
        return self.response


def test_model_confidence_cannot_overrule_reproduced_exposure():
    report = run_security("public-storage", use_models=True, snap=SnapStub())
    assert report.model_decisions["exposure"]["value"] == 0.0
    assert report.final_verdict == "BLOCK"


@pytest.mark.parametrize("response", [None, "not json", "{}", "[]", '[{"file":"policy/requirements.json","pointer":"/version","value":2}]'])
def test_missing_or_malformed_model_repair_never_passes(response):
    report = run_security("combined", repair=True, use_models=True,
                          snap=SnapStub(), muse=MuseStub(response))
    assert report.verdict == "BLOCK" and report.final_verdict == "INCONCLUSIVE"
    assert report.repaired_observations is None
    assert "offline" not in report.repair_source


def test_partial_model_repair_remains_blocked():
    patch = [{"file": STORAGE, "pointer": "/buckets/0/public_read", "value": False}]
    report = run_security("combined", repair=True, use_models=True,
                          snap=SnapStub(), muse=MuseStub(json.dumps(patch)))
    assert report.repair_verdict == "BLOCK"


def test_valid_model_repair_is_executed_and_credited():
    from blindspot.security import deterministic_repair
    patch = deterministic_repair(scenario_bundle(load_bundle(), "combined"))
    report = run_security("combined", repair=True, use_models=True,
                          snap=SnapStub(), muse=MuseStub(json.dumps(patch)))
    assert report.repair_source == "Meta stub-meta" and report.repair_verdict == "PASS"


def test_supporting_artifacts_reach_subq_in_full(tmp_path):
    root = tmp_path / "bundle"
    shutil.copytree(DEFAULT_BUNDLE, root)
    (root / "evidence").mkdir()
    history = "Incident history: session admin claims outlive directory demotion. " * 200
    (root / "evidence" / "history.md").write_text(history)
    snap = SnapStub()
    run_security("combined", root=root, use_models=True, snap=snap)
    assert history in snap.context
    assert all(name in snap.context for name in (STORAGE, ACCESS, DIRECTORY, POLICY))


def test_failed_advisor_is_explicit_and_does_not_invent_results():
    report = run_security("combined", use_models=True, snap=SnapStub(error="timeout"))
    assert report.final_verdict == "BLOCK" and not report.model_decisions
    assert any("timeout" in n for n in report.notes)


def test_uncovered_bucket_and_unknown_schema_are_not_approved(tmp_path):
    root = tmp_path / "bundle"
    shutil.copytree(DEFAULT_BUNDLE, root)
    storage = json.loads((root / STORAGE).read_text())
    storage["buckets"].append({**storage["buckets"][0], "id": "uncovered"})
    (root / STORAGE).write_text(json.dumps(storage))
    with pytest.raises(BundleError, match="Every bucket"):
        run_security(root=root)


def test_bundle_does_not_follow_symlinked_policy(tmp_path):
    root = tmp_path / "bundle"
    shutil.copytree(DEFAULT_BUNDLE, root)
    (root / POLICY).unlink()
    (root / POLICY).symlink_to(DEFAULT_BUNDLE / POLICY)
    with pytest.raises(BundleError, match="unsafe"):
        load_bundle(root)


@pytest.mark.parametrize("args,expected_code,verdict", [
    (["--offline"], 1, "BLOCK"),
    (["--offline", "--repair"], 0, "PASS"),
    (["--offline", "--scenario", "clean"], 0, "PASS"),
])
def test_cli_gate_has_real_exit_status(args, expected_code, verdict):
    proc = subprocess.run([sys.executable, "verify.py", "security", "--json", *args],
                          cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert proc.returncode == expected_code, proc.stderr
    assert json.loads(proc.stdout)["final_verdict"] == verdict


def test_cli_assesses_custom_bundle_without_injecting_demo_faults():
    proc = subprocess.run([sys.executable, "verify.py", "security", "--json", "--bundle", str(DEFAULT_BUNDLE)],
                          cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["verdict"] == "PASS"


class ChatContext:
    def __init__(self):
        self.messages = []

    async def send(self, sender, message):
        self.messages.append(message)


def say(text, extras=None):
    return {"text": text, "cards": extras or []}


def test_asi_chat_runs_checks_and_repairs_from_a_card_without_leaving_chat():
    from blindspot.security_chat import SESSIONS, handle_security_text
    ctx, sender = ChatContext(), "security-test-user"
    assert asyncio.run(handle_security_text(ctx, sender, "security revoked-access offline", say))
    assert "BLOCK" in ctx.messages[-1]["text"] and ctx.messages[-1]["cards"]
    selection = json.dumps({"action": "security_repair", "run_id": SESSIONS[sender]["run_id"]})
    assert asyncio.run(handle_security_text(ctx, sender, selection, say))
    assert "-> PASS" in ctx.messages[-1]["text"]
    assert "SAME 24" in ctx.messages[-1]["text"]
    # A card from the previous run cannot launch another job.
    asyncio.run(handle_security_text(ctx, sender, selection, say))
    assert "expired" in ctx.messages[-1]["text"]
    SESSIONS.pop(sender)


def test_invalid_chat_scenario_is_handled_without_a_model_call():
    from blindspot.security_chat import handle_security_text
    ctx = ChatContext()
    assert asyncio.run(handle_security_text(ctx, "bad-command", "security made-up", say))
    assert "Choose" in ctx.messages[-1]["text"] or "Use" in ctx.messages[-1]["text"]
