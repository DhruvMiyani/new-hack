"""The improvement loop.

A miss is turned into a candidate ruleset, and the candidate is only promoted
if it measurably beats the incumbent on the held-out split. The proposal step
may be a model or a human; the promotion gate is arithmetic, which is what
makes "the agent got better" a checkable claim rather than a vibe.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .corpus import Case, load_cases
from .models import Status
from .prober import analyse

ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = ROOT / "rules"


@dataclass
class Ruleset:
    version: str
    enabled: list[str]
    notes: str = ""
    parent: str | None = None
    promoted: bool = False
    probe_config: dict = field(default_factory=dict)

    @property
    def enabled_set(self) -> set[str]:
        return set(self.enabled)


@dataclass
class Metrics:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    cases: int = 0
    false_positive_cases: int = 0  # clean files that were flagged at all

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 1.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def line(self) -> str:
        return (
            f"precision {self.precision:.2f}  recall {self.recall:.2f}  "
            f"F1 {self.f1:.2f}  (tp={self.tp} fp={self.fp} fn={self.fn}, "
            f"{self.cases} cases, {self.false_positive_cases} clean file(s) wrongly flagged)"
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(precision=round(self.precision, 4), recall=round(self.recall, 4), f1=round(self.f1, 4))
        return d


def load_ruleset(version: str) -> Ruleset:
    data = json.loads((RULES_DIR / f"{version}.json").read_text())
    return Ruleset(
        version=data["version"],
        enabled=data["enabled"],
        notes=data.get("notes", ""),
        parent=data.get("parent"),
        promoted=data.get("promoted", False),
        probe_config=data.get("probe_config", {}),
    )


def save_ruleset(rs: Ruleset) -> Path:
    path = RULES_DIR / f"{rs.version}.json"
    path.write_text(json.dumps(asdict(rs), indent=2, sort_keys=True) + "\n")
    return path


def latest_promoted() -> Ruleset:
    versions = sorted(
        (p.stem for p in RULES_DIR.glob("v*.json")),
        key=lambda v: int(v.lstrip("v")),
    )
    best = None
    for v in versions:
        rs = load_ruleset(v)
        if rs.promoted:
            best = rs
    return best or load_ruleset(versions[0])


def next_version() -> str:
    versions = [int(p.stem.lstrip("v")) for p in RULES_DIR.glob("v*.json")]
    return f"v{max(versions) + 1 if versions else 1}"


def fired_rules(case: Case, ruleset: Ruleset, run_probes: bool = True) -> set[str]:
    """Rule ids this ruleset reports on a case. REFUTED does not count."""
    try:
        findings = analyse(
            case.source, case.path.name, run_probes,
            ruleset.enabled_set, ruleset.probe_config,
        )
    except SyntaxError:
        return set()
    return {f.rule_id for f in findings if f.status is not Status.REFUTED}


def evaluate(ruleset: Ruleset, split: str = "heldout", run_probes: bool = True) -> Metrics:
    m = Metrics()
    for case in load_cases(split):
        fired = fired_rules(case, ruleset, run_probes)
        m.cases += 1
        m.tp += len(fired & case.expect)
        m.fp += len(fired - case.expect)
        m.fn += len(case.expect - fired)
        if case.is_clean and fired:
            m.false_positive_cases += 1
    return m


def misses(ruleset: Ruleset, split: str = "dev", run_probes: bool = True) -> list[tuple[str, set[str]]]:
    """Dev-split cases whose expected rule did not fire. Input to a proposal."""
    out = []
    for case in load_cases(split):
        missed = case.expect - fired_rules(case, ruleset, run_probes)
        if missed:
            out.append((case.case_id, missed))
    return out


@dataclass
class PromotionReport:
    candidate: Ruleset
    before: Metrics
    after: Metrics
    promoted: bool
    reason: str

    def render(self) -> str:
        arrow = "PROMOTED" if self.promoted else "REJECTED"
        return (
            f"{self.candidate.parent} -> {self.candidate.version}: {arrow}\n"
            f"  held-out before: {self.before.line()}\n"
            f"  held-out after : {self.after.line()}\n"
            f"  gate: {self.reason}"
        )


def consider(candidate: Ruleset, incumbent: Ruleset, run_probes: bool = True) -> PromotionReport:
    """The gate. Promote only on a held-out improvement with no new false alarms."""
    before = evaluate(incumbent, "heldout", run_probes)
    after = evaluate(candidate, "heldout", run_probes)

    if after.false_positive_cases > before.false_positive_cases:
        ok, why = False, (
            f"rejected: clean files wrongly flagged rose "
            f"{before.false_positive_cases} -> {after.false_positive_cases}"
        )
    elif after.precision < before.precision - 1e-9:
        ok, why = False, f"rejected: precision regressed {before.precision:.2f} -> {after.precision:.2f}"
    elif after.recall <= before.recall + 1e-9:
        ok, why = False, f"rejected: no recall gain ({before.recall:.2f} -> {after.recall:.2f})"
    else:
        ok, why = True, (
            f"accepted: recall {before.recall:.2f} -> {after.recall:.2f} "
            f"with precision held at {after.precision:.2f} and no new false alarms"
        )

    candidate.promoted = ok
    return PromotionReport(candidate, before, after, ok, why)


def propose_enabling(missed_rules: set[str], incumbent: Ruleset, note: str = "") -> Ruleset:
    """Candidate ruleset that turns on rules which missed a confirmed defect."""
    return Ruleset(
        version=next_version(),
        enabled=sorted(incumbent.enabled_set | missed_rules),
        parent=incumbent.version,
        notes=note or f"Enable {', '.join(sorted(missed_rules))} after a confirmed miss on the dev split.",
        promoted=False,
        probe_config=dict(incumbent.probe_config),
    )


def propose_probe_fix(rule_id: str, config: dict, incumbent: Ruleset, note: str) -> Ruleset:
    """Candidate that changes how a rule proves itself, not which rules run.

    This is the agent improving its own tooling; it faces the same held-out
    gate as a rule change.
    """
    merged = {k: dict(v) for k, v in incumbent.probe_config.items()}
    merged.setdefault(rule_id, {}).update(config)
    return Ruleset(
        version=next_version(),
        enabled=sorted(incumbent.enabled_set),
        parent=incumbent.version,
        notes=note,
        promoted=False,
        probe_config=merged,
    )
