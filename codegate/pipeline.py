"""The multi-step workflow the agent runs for one review request."""

from __future__ import annotations

from dataclasses import dataclass, field

from .learn import latest_promoted
from .models import Finding, Status, Verdict
from .prober import scan, verify
from .sources import Submission, resolve
from .verdict import decide


@dataclass
class Step:
    name: str
    detail: str


@dataclass
class ReviewResult:
    verdict: Verdict
    submission: Submission
    steps: list[Step] = field(default_factory=list)
    ruleset_version: str = ""


def review(text: str, run_probes: bool = True, ruleset=None) -> ReviewResult:
    """resolve -> static scan -> build probes -> execute -> decide.

    Each stage is recorded so the agent can show its work rather than only its
    conclusion.
    """
    rs = ruleset or latest_promoted()
    steps: list[Step] = []

    sub = resolve(text)
    steps.append(
        Step("resolve input", f"{sub.kind}: {sub.ref} -> {len(sub.analysable)} Python file(s)")
    )
    for w in sub.warnings:
        steps.append(Step("input warning", w))

    all_findings: list[Finding] = []
    probed = 0

    for unit in sub.analysable:
        try:
            found = scan(unit.source, unit.path, rs.enabled_set)
        except SyntaxError as exc:
            steps.append(Step("scan", f"{unit.path}: could not parse ({exc.msg} at line {exc.lineno})"))
            continue
        steps.append(
            Step("static scan", f"{unit.path}: {len(found)} candidate(s) from ruleset {rs.version}")
        )
        if not found:
            continue

        if run_probes and unit.executable:
            verify(found, unit.source, probe_config=rs.probe_config)
            probed += sum(1 for f in found if f.evidence is not None)
            repro = sum(1 for f in found if f.status is Status.OBSERVED)
            steps.append(
                Step("execute probes", f"{unit.path}: ran {probed} probe(s), reproduced {repro}")
            )
        else:
            why = (
                "probes disabled for this run"
                if not run_probes
                else unit.note or "file is not executable standalone"
            )
            for f in found:
                f.probe_unavailable_reason = why
            steps.append(Step("execute probes", f"{unit.path}: skipped -- {why}"))

        all_findings.extend(found)

    all_findings.sort(
        key=lambda f: (f.status is not Status.OBSERVED, -f.severity.rank, f.path, f.line)
    )
    v = decide(all_findings, sub.ref, rs.version)
    steps.append(Step("decide", f"{v.decision.value}: {v.summary}"))
    return ReviewResult(verdict=v, submission=sub, steps=steps, ruleset_version=rs.version)
