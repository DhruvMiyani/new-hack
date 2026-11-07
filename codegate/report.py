"""Markdown rendering for an ASI:One conversation."""

from __future__ import annotations

from .models import Decision, Finding, Status, Verdict
from .pipeline import ReviewResult
from .sandbox import SANDBOX_CAVEAT

BADGE = {Decision.BLOCK: "BLOCK", Decision.REVIEW: "REVIEW", Decision.ALLOW: "ALLOW"}
MAX_LOG_LINES = 14


def _log_block(text: str, limit: int = MAX_LOG_LINES) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    clipped = lines[:limit]
    if len(lines) > limit:
        clipped.append(f"... {len(lines) - limit} more line(s)")
    return "\n".join(clipped)


def render_finding(f: Finding, index: int, verbose: bool = False) -> str:
    out = [f"**{index}. {f.title}**", f"`{f.rule_id}` · {f.severity.value} · `{f.path}:{f.line}`"]
    out.append(f"```python\n{f.snippet}\n```")
    out.append(f"{f.rationale}")

    if f.status is Status.OBSERVED and f.evidence:
        out.append(f"**Reproduced.** {f.evidence.detail}")
        out.append("What the probe actually printed:")
        out.append(f"```\n{_log_block(f.evidence.stdout)}\n```")
        out.append(f"_evidence {f.evidence.digest()} · {f.evidence.duration_ms}ms_")
        if verbose:
            out.append("The probe that produced it:")
            out.append(f"```python\n{f.evidence.probe_source}\n```")
    elif f.status is Status.REFUTED and f.evidence:
        out.append(f"**Probed, did not reproduce** — {f.evidence.detail}")
        out.append(f"```\n{_log_block(f.evidence.stdout, 8)}\n```")
    else:
        why = f.probe_unavailable_reason or "no probe available"
        out.append(f"**Not verified** — {why}. Treat as a judgement call, not a proven defect.")
    return "\n\n".join(out)


def render(result: ReviewResult, verbose: bool = False) -> str:
    v: Verdict = result.verdict
    parts = [f"## {BADGE[v.decision]} — {v.source_ref}", v.summary]

    parts.append(
        f"**{v.probes_reproduced} reproduced · {len(v.inferred)} unproven · "
        f"{len(v.refuted)} refuted** | {v.probes_run} probe(s) executed | ruleset `{v.ruleset_version}`"
    )

    if v.observed:
        parts.append("---\n### Reproduced defects\nEach of these was executed; the output is real.")
        for i, f in enumerate(v.observed, 1):
            parts.append(render_finding(f, i, verbose))

    if v.inferred:
        parts.append(
            "---\n### Unproven suspicions\nPattern matched, not demonstrated. "
            "These never block on their own."
        )
        for i, f in enumerate(v.inferred, len(v.observed) + 1):
            parts.append(render_finding(f, i, verbose))

    if v.refuted:
        names = ", ".join(f"`{f.rule_id}` at {f.path}:{f.line}" for f in v.refuted)
        parts.append(
            f"---\n### Cleared\n{len(v.refuted)} candidate(s) were probed and did not "
            f"reproduce, so they are dropped: {names}"
        )

    if not v.findings:
        parts.append("No candidate defects matched. That is not a proof of correctness — "
                     "it means none of this agent's rules fired.")

    parts.append("---\n_" + SANDBOX_CAVEAT + "_")
    return "\n\n".join(parts)


def render_steps(result: ReviewResult) -> str:
    rows = "\n".join(f"- **{s.name}** — {s.detail}" for s in result.steps)
    return f"### How this verdict was produced\n{rows}"
