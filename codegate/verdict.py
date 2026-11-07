"""Turns findings into a merge decision.

The gate rule, and the whole point of the agent: suspicion cannot block a
merge. Only a defect we reproduced can.
"""

from __future__ import annotations

from .models import Decision, Finding, Severity, Status, Verdict


def decide(findings: list[Finding], source_ref: str = "", ruleset_version: str = "") -> Verdict:
    observed = [f for f in findings if f.status is Status.OBSERVED]
    inferred = [f for f in findings if f.status is Status.INFERRED]
    refuted = [f for f in findings if f.status is Status.REFUTED]
    blocking = [f for f in observed if f.blocking]

    if blocking:
        decision = Decision.BLOCK
        by_sev: dict[str, int] = {}
        for f in blocking:
            by_sev[f.severity.value] = by_sev.get(f.severity.value, 0) + 1
        mix = ", ".join(f"{n} {sev}" for sev, n in sorted(by_sev.items(), key=lambda kv: -Severity(kv[0]).rank))
        summary = (
            f"Blocked: {len(blocking)} defect(s) at or above the blocking bar were reproduced "
            f"by executing them ({mix}). Each one below carries its probe and real output."
        )
    elif observed or inferred:
        decision = Decision.REVIEW
        bits = []
        if observed:
            bits.append(f"{len(observed)} reproduced lower-severity issue(s)")
        if inferred:
            bits.append(f"{len(inferred)} unproven suspicion(s)")
        summary = (
            "Needs human review: " + " and ".join(bits) + ". "
            "Nothing here was serious enough and proven enough to block on its own."
        )
    else:
        decision = Decision.ALLOW
        extra = f" {len(refuted)} suspected issue(s) were probed and did not reproduce." if refuted else ""
        summary = "Allowed: no defect reproduced and no unresolved suspicion." + extra

    probes_run = sum(1 for f in findings if f.evidence is not None)
    return Verdict(
        decision=decision,
        findings=findings,
        summary=summary,
        probes_run=probes_run,
        probes_reproduced=len(observed),
        source_ref=source_ref,
        ruleset_version=ruleset_version,
    )
