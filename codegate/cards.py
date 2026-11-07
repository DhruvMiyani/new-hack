"""ASI:One interactive cards for the review workflow.

The whole gate is driven from inside the conversation: a review card carries
the decision and the merge/block buttons, a carousel lists the reproduced
defects, and a detail card shows one defect's evidence.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from uagents_core.contrib.protocols.chat import MetadataContent
from uagents_core.contrib.protocols.chat.cards import (
    CarouselBadge,
    CarouselCardPayload,
    CarouselItem,
    CtaAction,
    DetailCardPayload,
    DetailSummaryRow,
    ReviewCardPayload,
    ReviewSummaryRow,
    create_card_content,
)

from .models import Decision, Finding, Status, Verdict

SEVERITY_VARIANT = {"critical": "warning", "high": "warning", "medium": "info", "low": "info"}
DECISION_WORD = {Decision.BLOCK: "BLOCK", Decision.REVIEW: "REVIEW", Decision.ALLOW: "ALLOW"}


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def gate_card(v: Verdict, card_id: UUID | None = None) -> MetadataContent:
    """The merge gate: decision, the counts behind it, and the two actions."""
    rows = [
        ReviewSummaryRow(label="Decision", value=DECISION_WORD[v.decision]),
        ReviewSummaryRow(label="Change", value=_clip(v.source_ref, 60)),
        ReviewSummaryRow(label="Reproduced defects", value=str(len(v.observed))),
        ReviewSummaryRow(label="Unproven suspicions", value=str(len(v.inferred))),
        ReviewSummaryRow(label="Probed and cleared", value=str(len(v.refuted))),
        ReviewSummaryRow(label="Probes executed", value=str(v.probes_run)),
        ReviewSummaryRow(label="Ruleset", value=v.ruleset_version or "unversioned"),
    ]
    for f in v.observed[:3]:
        rows.append(ReviewSummaryRow(label=f"{f.severity.value}  {f.rule_id}", value=_clip(f.title, 58)))

    blocking = v.decision is Decision.BLOCK
    payload = ReviewCardPayload(
        title=f"{DECISION_WORD[v.decision]} — merge gate",
        summary_rows=rows,
        approve_cta=CtaAction(
            label="Override and merge" if blocking else "Approve merge",
            selection={"action": "override" if blocking else "approve", "ref": v.source_ref},
            primary=not blocking,
        ),
        reject_cta=CtaAction(
            label="Uphold block" if blocking else "Block anyway",
            selection={"action": "uphold" if blocking else "block", "ref": v.source_ref},
            primary=blocking,
        ),
    )
    return create_card_content(payload, card_id=card_id or uuid4())


def findings_carousel(v: Verdict, card_id: UUID | None = None) -> MetadataContent | None:
    """One tile per finding, reproduced ones first."""
    ordered = v.observed + v.inferred + v.refuted
    if not ordered:
        return None

    items = []
    for i, f in enumerate(ordered[:10]):
        badges = [CarouselBadge(label=f.severity.value, variant=SEVERITY_VARIANT.get(f.severity.value, "info"))]
        badges.append(
            CarouselBadge(
                label={Status.OBSERVED: "reproduced", Status.INFERRED: "unproven", Status.REFUTED: "cleared"}[f.status],
                variant="success" if f.status is Status.OBSERVED else "info",
            )
        )
        detail = (
            f.evidence.detail if (f.evidence and f.status is Status.OBSERVED)
            else (f.probe_unavailable_reason or f.rationale)
        )
        items.append(
            CarouselItem(
                id=f"{f.rule_id}-{i}",
                title=_clip(f.title, 70),
                subtitle=f"{f.path}:{f.line}  ·  {f.rule_id}",
                badges=badges,
                secondary_text=_clip(detail, 140),
                primary_cta=CtaAction(
                    label="Show the evidence",
                    selection={"action": "evidence", "index": i},
                    primary=f.status is Status.OBSERVED,
                ),
            )
        )

    payload = CarouselCardPayload(
        title="Findings",
        subtitle=f"{len(v.observed)} reproduced · {len(v.inferred)} unproven · {len(v.refuted)} cleared",
        items=items,
    )
    return create_card_content(payload, card_id=card_id or uuid4())


def evidence_card(f: Finding, index: int, card_id: UUID | None = None) -> MetadataContent:
    """One defect, with the proof or an explicit statement that there is none."""
    rows = [
        DetailSummaryRow(label="Rule", value=f.rule_id),
        DetailSummaryRow(label="Severity", value=f.severity.value),
        DetailSummaryRow(label="Location", value=f"{f.path}:{f.line}"),
        DetailSummaryRow(label="Status", value=f.status.value),
        DetailSummaryRow(label="Code", value=_clip(f.snippet, 80)),
    ]
    if f.evidence:
        rows += [
            DetailSummaryRow(label="Outcome", value=_clip(f.evidence.detail, 90)),
            DetailSummaryRow(label="Probe runtime", value=f"{f.evidence.duration_ms} ms"),
            DetailSummaryRow(label="Evidence id", value=f.evidence.digest()),
        ]
        first = next((ln for ln in f.evidence.stdout.splitlines() if ln.strip()), "")
        if first:
            rows.append(DetailSummaryRow(label="Probe output", value=_clip(first, 90)))
    else:
        rows.append(
            DetailSummaryRow(
                label="Why unproven",
                value=_clip(f.probe_unavailable_reason or "no probe available for this rule", 90),
            )
        )

    ctas = [
        CtaAction(label="Back to findings", selection={"action": "findings"}, primary=True),
    ]
    if f.evidence:
        ctas.append(CtaAction(label="Show the probe source", selection={"action": "probe", "index": index}))

    payload = DetailCardPayload(
        title=_clip(f.title, 70),
        summary_rows=rows,
        ctas=ctas,
    )
    return create_card_content(payload, card_id=card_id or uuid4())


def learning_card(report, card_id: UUID | None = None) -> MetadataContent:
    """Held-out metrics before and after a proposed ruleset change."""
    r = report
    rows = [
        ReviewSummaryRow(label="Candidate", value=f"{r.candidate.parent} -> {r.candidate.version}"),
        ReviewSummaryRow(label="Change", value=_clip(r.candidate.notes, 80)),
        ReviewSummaryRow(label="Held-out recall", value=f"{r.before.recall:.2f} -> {r.after.recall:.2f}"),
        ReviewSummaryRow(label="Held-out precision", value=f"{r.before.precision:.2f} -> {r.after.precision:.2f}"),
        ReviewSummaryRow(label="Held-out F1", value=f"{r.before.f1:.2f} -> {r.after.f1:.2f}"),
        ReviewSummaryRow(label="Clean files flagged", value=f"{r.before.false_positive_cases} -> {r.after.false_positive_cases}"),
        ReviewSummaryRow(label="Gate", value=_clip(r.reason, 90)),
    ]
    payload = ReviewCardPayload(
        title=("PROMOTED " if r.promoted else "REJECTED ") + r.candidate.version,
        summary_rows=rows,
        approve_cta=CtaAction(
            label="Keep this ruleset",
            selection={"action": "keep_ruleset", "version": r.candidate.version},
            primary=r.promoted,
        ),
        reject_cta=CtaAction(
            label="Roll back",
            selection={"action": "rollback", "version": r.candidate.parent or ""},
            primary=not r.promoted,
        ),
    )
    return create_card_content(payload, card_id=card_id or uuid4())
