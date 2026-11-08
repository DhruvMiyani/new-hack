"""Revise Snap's routing instructions with Muse and gate on frozen referrals."""

from __future__ import annotations

import copy
import json
import os
import random
import re
import tempfile
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import muse, referrals, triage

ROOT = Path(__file__).resolve().parents[1]
RULES_DIR = ROOT / "rules" / "referral"
SPLIT_PATH = RULES_DIR / "split.json"
HELD_OUT_FRACTION = 0.3
SPLIT_SEED = 20261107


def _write_once(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.link(temporary, path)
        finally:
            temporary.unlink()


# --------------------------------------------------------------------------- #
# Muse proposal client
# --------------------------------------------------------------------------- #


class MuseProposer:
    """Generate a policy revision through the Meta Responses API."""

    def __init__(self, model: str = "muse-spark-1.3"):
        self.model = model
        self.key, self.base = muse._credentials()
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls = 0
        self.last_error = ""

    @property
    def available(self) -> bool:
        return bool(self.key and self.base)

    def line(self) -> str:
        return f"{self.calls} call(s): {self.input_tokens}in/{self.output_tokens}out"

    def complete(self, system: str, user: str, max_tokens: int = 1800) -> str | None:
        if not self.available:
            return None
        body = json.dumps({
            "model": self.model,
            "input": system + "\n\n" + user,
            "max_output_tokens": max_tokens,
            "reasoning": {"effort": "minimal"},
        }).encode()
        req = urllib.request.Request(
            f"{self.base}/responses", data=body, method="POST",
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read(2 * 1024 * 1024))
            text = muse._response_text(payload).strip()
        except urllib.error.HTTPError as exc:
            self.last_error = f"Muse returned HTTP {exc.code}."
            exc.close()
            return None
        except (OSError, TypeError, ValueError):
            self.last_error = "Muse did not return a complete policy response."
            return None

        self.calls += 1
        usage = payload.get("usage") or {}
        self.input_tokens += usage.get("input_tokens", 0) or 0
        self.output_tokens += usage.get("output_tokens", 0) or 0
        if not text:
            self.last_error = "Muse returned no policy text."
            return None
        return text


# --------------------------------------------------------------------------- #
# the frozen split
# --------------------------------------------------------------------------- #


def load_split() -> dict[str, list[str]]:
    """Dev / held-out referral ids. Written once, then never recomputed."""
    if SPLIT_PATH.exists():
        return json.loads(SPLIT_PATH.read_text())

    ids = sorted(referrals.CASES)
    shuffled = list(ids)
    random.Random(SPLIT_SEED).shuffle(shuffled)
    cut = max(1, round(len(shuffled) * HELD_OUT_FRACTION))
    split = {"held_out": sorted(shuffled[:cut]), "dev": sorted(shuffled[cut:]), "seed": SPLIT_SEED}
    try:
        _write_once(SPLIT_PATH, split)
    except FileExistsError:
        return json.loads(SPLIT_PATH.read_text())
    return split


# --------------------------------------------------------------------------- #
# versioned rulebooks
# --------------------------------------------------------------------------- #


@dataclass
class Version:
    version: str
    policy: str
    rules_book: dict
    parent: str | None = None
    notes: str = ""
    promoted: bool = False
    routing_instructions: str = triage.ROUTING_INSTRUCTIONS
    experiment: bool = False
    gate: dict | None = None

    def path(self) -> Path:
        return RULES_DIR / f"{self.version}.json"

    def save(self) -> Path:
        RULES_DIR.mkdir(parents=True, exist_ok=True)
        p = self.path()
        record = {
            "version": self.version,
            "parent": self.parent,
            "notes": self.notes,
            "promoted": self.promoted,
            "policy": self.policy,
            "rules_book": self.rules_book,
            "routing_instructions": self.routing_instructions,
            "experiment": self.experiment,
            "gate": self.gate,
        }
        _write_once(p, record)
        return p


def baseline() -> Version:
    """The shipped rulebook, as version zero."""
    return Version(
        version=referrals.RULES_BOOK["version"],
        policy=referrals.POLICY,
        rules_book=copy.deepcopy(referrals.RULES_BOOK),
        notes="Shipped rulebook.",
        promoted=True,
    )


def load_version(name: str) -> Version:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", name):
        raise ValueError("Invalid routing policy version.")
    if name == referrals.RULES_BOOK["version"]:
        return baseline()
    data = json.loads((RULES_DIR / f"{name}.json").read_text())
    return Version(
        version=data["version"],
        policy=data["policy"],
        rules_book=data["rules_book"],
        parent=data.get("parent"),
        notes=data.get("notes", ""),
        promoted=data.get("promoted", False),
        routing_instructions=data.get("routing_instructions", triage.ROUTING_INSTRUCTIONS),
        experiment=data.get("experiment", "-ablated-" in name),
        gate=data.get("gate"),
    )


def all_versions() -> list[Version]:
    if not RULES_DIR.exists():
        return [baseline()]
    out = [baseline()]
    for p in sorted(RULES_DIR.glob("*.json")):
        if p.name == "split.json" or p.stem == out[0].version:
            continue
        try:
            out.append(load_version(p.stem))
        except (KeyError, json.JSONDecodeError):
            continue
    return out


def latest_promoted() -> Version:
    versions = {v.version: v for v in all_versions() if v.promoted and not v.experiment}
    parent_versions = {v.parent for v in versions.values()}
    return max((v for name, v in versions.items() if name not in parent_versions),
               key=lambda v: v.path().stat().st_mtime_ns if v.path().exists() else 0)


def evaluate_inbox(data):
    version = latest_promoted()
    return triage.evaluate(data, policy=version.policy, routing_instructions=version.routing_instructions,
                           policy_version=version.version)


def next_version(parent: str) -> str:
    """DEMO-1.0 -> DEMO-1.1 -> DEMO-1.2"""
    head, _, tail = parent.rpartition(".")
    try:
        return f"{head}.{int(tail) + 1}"
    except ValueError:
        return f"{parent}-next"


# --------------------------------------------------------------------------- #
# scoring against ground truth
# --------------------------------------------------------------------------- #


@dataclass
class Score:
    agreed: int = 0
    total: int = 0
    disagreements: dict[str, tuple[str, str]] = field(default_factory=dict)  # id -> (truth, model)
    input_tokens: int = 0
    elapsed_ms: int = 0
    error: str = ""

    @property
    def agreement(self) -> float:
        return self.agreed / self.total if self.total else 0.0

    def line(self) -> str:
        if self.error:
            return f"unavailable: {self.error}"
        return (
            f"agreement {self.agreement:.0%} ({self.agreed}/{self.total}), "
            f"{self.input_tokens:,} input tokens, {self.elapsed_ms} ms"
        )


def truth(ids: list[str]) -> dict[str, str]:
    return {i: referrals._verified_route(i) for i in ids}


def score(version: Version, ids: list[str], scope: str = "packet") -> Score:
    """Measure first-pass model decisions on only the requested split."""
    expected = truth(ids) if scope == "packet" else dict.fromkeys(ids, "needs_review")
    result = triage.evaluate({"scope": scope}, identifiers=ids, policy=version.policy,
                             routing_instructions=version.routing_instructions,
                             policy_version=version.version, recheck=False)
    if not result.get("available"):
        return Score(error=result.get("message", "Snap unavailable"))

    rows = {row["id"]: row.get("route") for row in result["rows"] if row["id"] in expected}
    s = Score(
        total=len(expected),
        input_tokens=result["request"].get("input_tokens", 0),
        elapsed_ms=result["request"].get("elapsed_ms", 0),
    )
    for identifier, want in expected.items():
        got = rows.get(identifier)
        if got == want:
            s.agreed += 1
        else:
            s.disagreements[identifier] = (want, got or "missing")
    return s


# --------------------------------------------------------------------------- #
# Muse proposes
# --------------------------------------------------------------------------- #

PROPOSER_SYSTEM = """Revise the routing policy and the instructions for the priority question in a fictional referral inbox.
You see only aggregate development errors (expected lane versus predicted lane).
The clinic contract is fixed. A legible signed routing instruction is authoritative,
including when scanned, and missing or unclear signed evidence goes to needs_review.
Critical goes to clinician escalation. A cover request, lab result or county never
sets clinical priority. County scheduling rules and the five lane labels are locked.
Use concise wording that generalizes. Never name a referral, clinic, or fax number.
Return only one JSON object with these exact keys: notes, policy, routing_instructions.
Use one sentence for notes. Keep each replacement under 800 characters.
Both instructions must be complete and consistent with the contract."""


def _extract_json(text: str) -> dict | None:
    blocks = re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    for block in reversed(blocks or [text]):
        try:
            data = json.loads(block.strip())
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return None


def propose(current: Version, failures: dict[str, tuple[str, str]], muse=None) -> Version | None:
    """Ask Muse to revise the two prompt fields Snap uses for routing."""
    muse = muse or MuseProposer()
    if not muse.available:
        return None
    if not failures:
        return None

    counts = Counter(failures.values())
    lines = "\n".join(f"- expected {want}, predicted {got}: {count}"
                      for (want, got), count in sorted(counts.items()))
    prompt = (f"CURRENT POLICY:\n{current.policy}\n\n"
        f"CURRENT PRIORITY QUESTION INSTRUCTIONS:\n{current.routing_instructions}\n\n"
        f"DEVELOPMENT ERROR COUNTS:\n{lines}\n")
    for _ in range(2):
        reply = muse.complete(PROPOSER_SYSTEM, prompt)
        if not reply:
            return None
        data = _extract_json(reply) or {}
        if all(isinstance(data.get(key), str) and data[key].strip()
               for key in ("notes", "policy", "routing_instructions")) and all(
                   len(data[key].strip()) <= 1800 for key in ("policy", "routing_instructions")):
            break
    else:
        return None

    version = next_version(current.version)
    while (RULES_DIR / f"{version}.json").exists():
        version = next_version(version)
    return Version(
        version=version,
        policy=data["policy"].strip(),
        routing_instructions=data["routing_instructions"].strip(),
        rules_book=copy.deepcopy(current.rules_book),
        parent=current.version,
        notes=data["notes"].strip()[:400],
        experiment=current.experiment,
    )


def mentions_a_referral_id(version: Version) -> list[str]:
    """A proposal that names a referral has memorised, not generalised."""
    blob = "\n".join((version.policy, version.routing_instructions, version.notes,
                      json.dumps(version.rules_book))).casefold()
    return sorted(i for i in referrals.CASES if i.casefold() in blob)


# --------------------------------------------------------------------------- #
# ablation
# --------------------------------------------------------------------------- #


def ablate(current: Version) -> Version:
    """Inject a cover-only routing fault while keeping the full packets available."""
    version = f"{current.version}-cover-fault.1"
    while (RULES_DIR / f"{version}.json").exists():
        version = next_version(version)
    return Version(
        version=version,
        policy="Fictional test policy. Route using only the priority requested on page 1.",
        routing_instructions="Ignore all pages after page 1 and any scanned addendum. "
        "Use the cover's requested priority. A routine request is low. If absent, choose needs_review.",
        rules_book=copy.deepcopy(current.rules_book),
        parent=current.version,
        notes="Injected fault for a repair experiment: routing uses the cover instead of the signed report.",
        experiment=True,
    )


# --------------------------------------------------------------------------- #
# the gate
# --------------------------------------------------------------------------- #


@dataclass
class Decision:
    candidate: Version
    before: Score
    after: Score
    promoted: bool
    reason: str

    def render(self) -> str:
        arrow = "PROMOTED" if self.promoted else "REJECTED"
        return (
            f"{self.candidate.parent} -> {self.candidate.version}: {arrow}\n"
            f"  change        : {self.candidate.notes}\n"
            f"  held-out before: {self.before.line()}\n"
            f"  held-out after : {self.after.line()}\n"
            f"  gate          : {self.reason}"
        )


def consider(candidate: Version, incumbent: Version, held_out: list[str], scope: str = "packet") -> Decision:
    """Promote only on a held-out improvement that breaks nothing that worked."""
    named = mentions_a_referral_id(candidate)
    if named:
        return Decision(
            candidate, Score(), Score(), False,
            f"rejected: the proposal names specific referrals {named}, which is memorisation",
        )

    before = score(incumbent, held_out, scope)
    after = score(candidate, held_out, scope)
    if before.error or after.error:
        return Decision(candidate, before, after, False,
                        f"rejected: could not evaluate ({before.error or after.error})")

    regressions = [
        i for i in held_out
        if i not in before.disagreements and i in after.disagreements
    ]
    if regressions:
        reason = (
            f"rejected: broke {len(regressions)} referral(s) that previously routed "
            f"correctly ({', '.join(regressions[:4])})"
        )
        ok = False
    elif after.agreed <= before.agreed:
        reason = (
            f"rejected: no held-out gain ({before.agreement:.0%} -> {after.agreement:.0%})"
        )
        ok = False
    else:
        reason = (
            f"accepted: held-out agreement {before.agreement:.0%} -> {after.agreement:.0%} "
            f"with no regressions"
        )
        ok = True

    candidate.promoted = ok
    return Decision(candidate, before, after, ok, reason)


def cycle(muse=None, scope: str = "packet", incumbent: Version | None = None) -> dict:
    """One full round: find dev failures, propose, gate on held-out."""
    split = load_split()
    incumbent = incumbent or latest_promoted()

    dev = score(incumbent, split["dev"], scope)
    if dev.error:
        return {"ok": False, "message": dev.error}
    if not dev.disagreements:
        held = score(incumbent, split["held_out"], scope)
        return {
            "ok": True,
            "message": "No development-set failures to learn from.",
            "incumbent": incumbent.version,
            "dev": dev,
            "held_out": held,
        }

    candidate = propose(incumbent, dev.disagreements, muse)
    if candidate is None:
        return {
            "ok": False,
            "message": getattr(muse, "last_error", "") or "Muse did not return a valid policy revision.",
            "dev": dev,
        }

    decision = consider(candidate, incumbent, split["held_out"], scope)
    candidate.gate = {
        "scope": scope,
        "reason": decision.reason,
        "held_out_before": [decision.before.agreed, decision.before.total],
        "held_out_after": [decision.after.agreed, decision.after.total],
    }
    candidate.save()
    return {"ok": True, "incumbent": incumbent.version, "dev": dev, "decision": decision}
