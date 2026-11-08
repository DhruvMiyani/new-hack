"""Self-improvement for referral routing, gated on held-out referrals.

The loop only means something if the thing that proposes a change is not the
thing that decides whether it ships, and if the change is judged on referrals
it has never seen. So:

    Muse proposes      it is the only model here that generates text; SubQ
                       emits zero output tokens and cannot write a rule
    SubQ re-scores     50 referrals in one request, input-only billing, so a
                       full evaluation round costs well under a cent
    arithmetic decides neither model votes on its own promotion

Ground truth is `referrals._verified_route`, which derives the correct lane
from the fixture pages deterministically. The model never sees it.

The split is frozen to disk the first time it is created. If the held-out set
could drift, "it improved" would mean nothing -- you would simply be choosing
the referrals that flatter the latest rulebook. The proposer is shown dev
failures only, never a held-out referral.
"""

from __future__ import annotations

import copy
import json
import os
import random
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import referrals

ROOT = Path(__file__).resolve().parents[1]
RULES_DIR = ROOT / "rules" / "referral"
SPLIT_PATH = RULES_DIR / "split.json"
HELD_OUT_FRACTION = 0.3
SPLIT_SEED = 20261107


# --------------------------------------------------------------------------- #
# a minimal Muse chat client
#
# blindspot/muse.py is purpose-built for PDF evidence extraction through the
# Responses API and has no general completion call. Rather than reshape it,
# the proposer carries its own small client. Note the token budget: Muse models
# reason natively and reasoning is billed against the same max_tokens, so a
# small budget returns finish_reason="length" with null content.
# --------------------------------------------------------------------------- #


class MuseProposer:
    """Chat completion against the Meta Model API."""

    def __init__(self, model: str = "muse-spark-1.3"):
        self.model = model
        self.base = (os.getenv("META_BASE_URL") or "https://api.meta.ai/v1").rstrip("/")
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls = 0
        self.last_error = ""

    @property
    def key(self) -> str:
        return (os.getenv("META_API_KEY") or os.getenv("MODEL_API_KEY") or "").strip()

    @property
    def available(self) -> bool:
        return bool(self.key)

    def line(self) -> str:
        return f"{self.calls} call(s): {self.input_tokens}in/{self.output_tokens}out"

    def complete(self, system: str, user: str, max_tokens: int = 8000) -> str | None:
        if not self.available:
            return None
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }).encode()
        req = urllib.request.Request(
            f"{self.base}/chat/completions", data=body, method="POST",
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            self.last_error = f"HTTP {exc.code}: {exc.read()[:200].decode(errors='replace')}"
            return None
        except Exception as exc:
            self.last_error = repr(exc)
            return None

        self.calls += 1
        usage = payload.get("usage") or {}
        self.input_tokens += usage.get("prompt_tokens", 0) or 0
        self.output_tokens += usage.get("completion_tokens", 0) or 0
        choices = payload.get("choices") or [{}]
        text = ((choices[0].get("message") or {}).get("content") or "").strip()
        if not text:
            self.last_error = (
                f"empty content (finish_reason={choices[0].get('finish_reason')!r}); "
                "reasoning may have consumed the whole max_tokens budget"
            )
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
    RULES_DIR.mkdir(parents=True, exist_ok=True)
    SPLIT_PATH.write_text(json.dumps(split, indent=2) + "\n")
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

    def path(self) -> Path:
        return RULES_DIR / f"{self.version}.json"

    def save(self) -> Path:
        RULES_DIR.mkdir(parents=True, exist_ok=True)
        p = self.path()
        p.write_text(
            json.dumps(
                {
                    "version": self.version,
                    "parent": self.parent,
                    "notes": self.notes,
                    "promoted": self.promoted,
                    "policy": self.policy,
                    "rules_book": self.rules_book,
                },
                indent=2,
            )
            + "\n"
        )
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
    data = json.loads((RULES_DIR / f"{name}.json").read_text())
    return Version(
        version=data["version"],
        policy=data["policy"],
        rules_book=data["rules_book"],
        parent=data.get("parent"),
        notes=data.get("notes", ""),
        promoted=data.get("promoted", False),
    )


def all_versions() -> list[Version]:
    if not RULES_DIR.exists():
        return [baseline()]
    out = [baseline()]
    for p in sorted(RULES_DIR.glob("*.json")):
        if p.name == "split.json":
            continue
        try:
            out.append(load_version(p.stem))
        except (KeyError, json.JSONDecodeError):
            continue
    return out


def latest_promoted() -> Version:
    promoted = [v for v in all_versions() if v.promoted]
    return promoted[-1] if promoted else baseline()


def next_version(parent: str) -> str:
    """DEMO-1.0 -> DEMO-1.1 -> DEMO-1.2"""
    head, _, tail = parent.rpartition(".")
    try:
        return f"{head}.{int(tail) + 1}"
    except ValueError:
        return f"{parent}-next"


class applied:
    """Temporarily swap the live rulebook. Always restores, even on error."""

    def __init__(self, version: Version):
        self.version = version
        self._policy = None
        self._rules = None

    def __enter__(self):
        self._policy, self._rules = referrals.POLICY, referrals.RULES_BOOK
        referrals.POLICY = self.version.policy
        referrals.RULES_BOOK = self.version.rules_book
        return self.version

    def __exit__(self, *exc):
        referrals.POLICY = self._policy
        referrals.RULES_BOOK = self._rules
        return False


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
    """Classify `ids` under `version` and compare with the deterministic route."""
    from . import triage

    expected = truth(ids)
    with applied(version):
        result = triage.evaluate({"scope": scope})
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

PROPOSER_SYSTEM = """You revise the written intake rules for a referral routing system.

You are given the current policy text, the current rules book, and a list of
referrals the system routed to the WRONG lane. For each failure you see the
correct lane and the lane the system chose. You are NOT shown the referral
contents, and you are NOT shown the evaluation set your change will be judged on.

Revise the wording so the system makes the correct call. You may:
- reword the policy text
- reword the `text` of an existing rule
- add a rule with a new id

You may NOT:
- change `accepted_counties`, or any list of allowed values
- reference a specific referral id
- add anything that names a lane for a particular clinic or fax number

A change that encodes the answer for one referral instead of fixing the rule
will be rejected, because it is judged on referrals you have not seen.

Reply with ONE fenced ```json block:
{"notes": "<one sentence on what you changed and why>",
 "policy": "<full revised policy text>",
 "rules": [{"id": "...", "title": "...", "text": "..."}]}
`rules` must contain every rule, revised or unchanged."""


def _extract_json(text: str) -> dict | None:
    import re

    blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    for block in reversed(blocks or [text]):
        try:
            data = json.loads(block.strip())
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return None


def propose(current: Version, failures: dict[str, tuple[str, str]], muse=None) -> Version | None:
    """Ask Muse for a revised rulebook. Returns None when unavailable."""
    muse = muse or MuseProposer()
    if not muse.available:
        return None
    if not failures:
        return None

    lines = "\n".join(
        f"- referral {i}: correct lane `{want}`, system chose `{got}`"
        for i, (want, got) in sorted(failures.items())
    )
    reply = muse.complete(
        PROPOSER_SYSTEM,
        f"CURRENT POLICY:\n{current.policy}\n\n"
        f"CURRENT RULES BOOK:\n{json.dumps(current.rules_book, indent=2)}\n\n"
        f"FAILURES ON THE DEVELOPMENT SET:\n{lines}\n",
        max_tokens=8000,
    )
    if not reply:
        return None
    data = _extract_json(reply)
    if not data or "policy" not in data or "rules" not in data:
        return None

    book = copy.deepcopy(current.rules_book)
    # Guard rails the proposer is told about, enforced here rather than trusted.
    protected = {"accepted_counties", "specialist", "name"}
    for key in protected:
        if key in current.rules_book:
            book[key] = current.rules_book[key]
    book["rules"] = [
        {"id": r.get("id", ""), "title": r.get("title", ""), "text": r.get("text", "")}
        for r in data["rules"]
        if isinstance(r, dict)
    ]
    version = next_version(current.version)
    book["version"] = version
    return Version(
        version=version,
        policy=str(data["policy"]),
        rules_book=book,
        parent=current.version,
        notes=str(data.get("notes", ""))[:300],
    )


def mentions_a_referral_id(version: Version) -> list[str]:
    """A proposal that names a referral has memorised, not generalised."""
    blob = version.policy + json.dumps(version.rules_book)
    return sorted(i for i in referrals.CASES if i in blob)


# --------------------------------------------------------------------------- #
# ablation
# --------------------------------------------------------------------------- #


def ablate(current: Version, rule_id: str | None = None) -> Version:
    """Deliberately weaken the rulebook so the loop has something to repair.

    The shipped rulebook already routes every referral correctly, so a repair
    loop run against it demonstrates nothing. Removing a rule creates real,
    honestly-labelled failures. This is stated in the demo rather than hidden:
    the interesting claim is not that the system is broken, it is that the loop
    can earn the lost accuracy back and must prove it on referrals it has not
    seen.
    """
    book = copy.deepcopy(current.rules_book)
    rules = book.get("rules", [])
    target = rule_id or (rules[0]["id"] if rules else None)
    kept = [r for r in rules if r.get("id") != target]
    if len(kept) == len(rules):
        raise ValueError(f"no rule with id {target!r} to remove")
    book["rules"] = kept
    version = f"{current.version}-ablated-{(target or 'rule').lower()}"
    book["version"] = version
    return Version(
        version=version,
        policy=current.policy,
        rules_book=book,
        parent=current.version,
        notes=f"Ablation: rule {target} removed on purpose, to give the loop a real failure to repair.",
        promoted=False,
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


def consider(candidate: Version, incumbent: Version, held_out: list[str]) -> Decision:
    """Promote only on a held-out improvement that breaks nothing that worked."""
    named = mentions_a_referral_id(candidate)
    if named:
        return Decision(
            candidate, Score(), Score(), False,
            f"rejected: the proposal names specific referrals {named}, which is memorisation",
        )

    before = score(incumbent, held_out)
    after = score(candidate, held_out)
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
            "message": "Muse could not propose a revision (no META_API_KEY, or the reply did not parse).",
            "dev": dev,
        }

    decision = consider(candidate, incumbent, split["held_out"])
    if decision.promoted:
        candidate.save()
    return {"ok": True, "incumbent": incumbent.version, "dev": dev, "decision": decision}
