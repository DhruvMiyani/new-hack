"""Rendering. Terminal-first, with a markdown variant for the agent."""

from __future__ import annotations

from .pipeline import INDEPENDENT, SELF, RunReport, aggregate

BAR_WIDTH = 24


def _bar(fraction: float, width: int = BAR_WIDTH) -> str:
    filled = int(round(fraction * width))
    return "#" * filled + "." * (width - filled)


def render_run(report: RunReport, verbose: bool = False) -> str:
    out: list[str] = []
    card = report.scorecard
    t = report.task

    out.append("=" * 74)
    out.append(f"TASK  {t.name}    ->  {report.decision}")
    out.append("=" * 74)

    out.append("\nhow this ran")
    for s in report.stages:
        tag = {"meta": "[meta]", "subq": "[subq]", "offline": "[offline]", "local": "[local]"}.get(s.source, "")
        out.append(f"  {tag:9} {s.name:18} {s.detail}")

    if not card or not card.verdicts:
        out.append("\nNo proven defects, so there is nothing to measure.")
        if card and card.equivalent:
            out.append(f"({len(card.equivalent)} mutant(s) were equivalent to the original.)")
        return "\n".join(out)

    out.append(f"\ninformation wall")
    for s in report.suites_sorted() if hasattr(report, "suites_sorted") else card.suites:
        saw = "saw the implementation" if s.saw_implementation else "spec + signature only"
        state = "valid" if s.valid else "DISQUALIFIED"
        out.append(f"  {s.name:22} {state:12} {saw}  (author: {s.author})")
        if not s.valid:
            out.append(f"      {s.disqualification()}")

    n = len(card.verdicts)
    sr, ir = card.kill_rate(SELF), card.kill_rate(INDEPENDENT)
    out.append(f"\n{n} proven defect(s) -- each with a witnessed input, not merely a pattern match")
    out.append(f"  self-written tests    {_bar(sr)}  {sr:>5.0%}  ({len(card.killed(SELF))}/{n} caught)")
    out.append(f"  independent tests     {_bar(ir)}  {ir:>5.0%}  ({len(card.killed(INDEPENDENT))}/{n} caught)")

    rr = card.recovery_rate(SELF, INDEPENDENT)
    ov = card.overlap(SELF, INDEPENDENT)
    if rr is not None:
        missed = len(card.missed(SELF))
        rec = len(card.recovered(SELF, INDEPENDENT))
        out.append(f"\n  blind-spot recovery   {rec}/{missed} = {rr:.0%}")
        out.append(f"      of the {missed} real defect(s) the implementer's own tests let through,")
        out.append(f"      the independent suite caught {rec}.")
    else:
        out.append("\n  the implementer's own tests caught every proven defect; nothing to recover")
    if ov is not None:
        out.append(f"  kill-set overlap      {ov:.2f} Jaccard (1.00 would mean identical blind spots)")

    out.append("\nper defect class")
    ops_self, ops_ind = card.by_operator(SELF), card.by_operator(INDEPENDENT)
    out.append(f"  {'operator':12} {'self':>9} {'independent':>12}")
    for op in sorted(ops_self):
        ks, ts = ops_self[op]
        ki, _ = ops_ind.get(op, (0, ts))
        out.append(f"  {op:12} {f'{ks}/{ts}':>9} {f'{ki}/{ts}':>12}")

    rec = card.recovered(SELF, INDEPENDENT)
    if rec:
        out.append("\ndefects ONLY the independent suite caught")
        for v in rec:
            eq = card.classifications.get(v.mutant.mutant_id)
            out.append(f"  - {v.mutant.operator} at line {v.mutant.line}: {v.mutant.description}")
            if eq and eq.witness:
                out.append(f"      witness : {eq.witness.render()}")
            tests = ", ".join(v.killing_tests.get(INDEPENDENT, []))
            out.append(f"      caught by: {tests}")

    both = [v for v in card.verdicts if not v.killed_by_any()]
    if both:
        out.append(f"\nreal defects BOTH suites missed ({len(both)}) -- a shared blind spot")
        for v in both:
            eq = card.classifications.get(v.mutant.mutant_id)
            out.append(f"  - {v.mutant.operator} line {v.mutant.line}: {v.mutant.description}")
            if eq and eq.witness:
                out.append(f"      witness: {eq.witness.render()}")

    if card.equivalent:
        out.append(
            f"\n{len(card.equivalent)} mutant(s) excluded as equivalent to the original "
            "(unkillable by construction, so scoring them would be unfair to both suites)"
        )
        if verbose:
            for m, eq in card.equivalent:
                out.append(f"  - {m.operator} line {m.line}: {eq.explain()}")
    if card.undecided:
        out.append(f"{len(card.undecided)} mutant(s) undecided and excluded:")
        for m, eq in card.undecided:
            out.append(f"  - {m.operator} line {m.line}: {eq.error}")

    if report.test_judgements:
        out.append("\nSubQ Snap adjudication of the test suites")
        for name, res in report.test_judgements.items():
            if not res.ok:
                out.append(f"  {name}: unavailable ({res.error})")
                continue
            out.append(f"  {name}  ({res.input_tokens} input tokens)")
            for a in res.answers.values():
                out.append(f"      {a.render()}")

    if report.defect_judgements:
        out.append("\nSubQ Snap adjudication of recovered defects")
        for mid, res in report.defect_judgements:
            if not res.ok:
                out.append(f"  {mid}: unavailable ({res.error})")
                continue
            bits = "  ".join(a.render() for a in res.answers.values())
            out.append(f"  {mid}: {bits}")

    if report.notes:
        out.append("\nnotes")
        for note in report.notes:
            out.append(f"  ! {note}")

    return "\n".join(out)


def render_aggregate(reports: list[RunReport]) -> str:
    agg = aggregate(reports)
    out = ["", "=" * 74, "POOLED RESULT ACROSS ALL TASKS", "=" * 74]
    out.append(f"  tasks                        {len(reports)}")
    out.append(f"  proven defects               {agg['defects']}")
    out.append(f"  caught by self-written tests {agg['self_kill_rate']:.0%}")
    out.append(f"  caught by independent tests  {agg['independent_kill_rate']:.0%}")
    if agg["recovery_rate"] is not None:
        out.append(
            f"  blind-spot recovery          {agg['recovered_by_independent']}/{agg['missed_by_self']} "
            f"= {agg['recovery_rate']:.0%}"
        )
    out.append("")
    out.append("  Read: the implementer's own tests are not a safety net for the")
    out.append("  defects the implementer was already blind to. Withholding the")
    out.append("  implementation from the test author recovers a measurable share.")
    return "\n".join(out)


def render_markdown(report: RunReport) -> str:
    """Compact markdown for the ASI:One agent."""
    card = report.scorecard
    if not card or not card.verdicts:
        return f"## {report.decision} — {report.task.name}\n\nNo proven defects to measure."

    n = len(card.verdicts)
    rr = card.recovery_rate(SELF, INDEPENDENT)
    lines = [
        f"## {report.decision} — `{report.task.name}`",
        "",
        f"**{n} proven defect(s)**, each with a witnessed distinguishing input.",
        "",
        "| test suite | saw the code? | defects caught |",
        "|---|---|---|",
        f"| implementer's own | yes | {len(card.killed(SELF))}/{n} ({card.kill_rate(SELF):.0%}) |",
        f"| independent | no — spec only | {len(card.killed(INDEPENDENT))}/{n} ({card.kill_rate(INDEPENDENT):.0%}) |",
    ]
    if rr is not None:
        lines += [
            "",
            f"**Blind-spot recovery: {rr:.0%}** — of {len(card.missed(SELF))} real defect(s) the "
            f"implementer's own tests let through, independence caught "
            f"{len(card.recovered(SELF, INDEPENDENT))}.",
        ]
    rec = card.recovered(SELF, INDEPENDENT)
    if rec:
        lines += ["", "**Caught only by the independent suite:**"]
        for v in rec:
            eq = card.classifications.get(v.mutant.mutant_id)
            w = f" — `{eq.witness.render()}`" if (eq and eq.witness) else ""
            lines.append(f"- {v.mutant.description}{w}")
    for name, res in report.test_judgements.items():
        if res.ok:
            lines += ["", f"**SubQ Snap on `{name}`:** " + "; ".join(a.render() for a in res.answers.values())]
    return "\n".join(lines)
