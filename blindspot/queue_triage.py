"""Triage a merge queue: which AI-generated change do you verify first?

This is the three Subquadratic showcases combined into one workflow, on a queue
where the right answer is known.

  context curation   one pass over the repo picks which file each change puts
                     at risk, the way "which files should change?" sweeps 56
                     files in a single call
  multimodal         a change can arrive with a screenshot of its failing
                     build instead of text, and both paths are scored
  queue triage       rank the queue against a written merge policy, with
                     calibrated urgency, exactly like triaging tickets against
                     an SLA

The reason to do this on a merge queue rather than support tickets: verification
is expensive. Running a full suite against every open change does not scale, so
order matters, and a good triage finds the real defects sooner. That is
measurable here because every item's status is known in advance -- defective
items carry a witness proving it.

The headline is the same shape as the support demo's "7 min vs 12 min":

    verifications performed before the first real defect was found
    triaged order  vs  first-come-first-served  vs  random

A queue where you must verify 7 changes to find the first bug is a queue nobody
works through. One where you verify 2 is a queue that gets worked.
"""

from __future__ import annotations

import random
import statistics
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .equivalence import Equivalence
from .mutate import Mutant
from .providers import SubQSnap, choice, noul, score as score_q
from .roles import Task

WORKERS = 6

# The policy the triage is asked to apply, cited in the prompt the way an SLA
# is cited in the support-queue demo. Keeping it as text, not code, is the
# point: the model has to apply a written rule.
MERGE_POLICY = """MERGE POLICY

A change is BLOCKING if it alters observable behaviour that the specification
constrains -- a different return value, a new exception, or a changed boundary
condition for any input the spec describes.

A change is NOT blocking if it only affects inputs the specification leaves
undefined, or if behaviour is identical for every input the spec constrains.

Priority for verification:
  1. changes touching guard clauses and boundary comparisons
  2. changes to arithmetic that accumulates across a loop
  3. changes that only affect inputs outside the specification

Verification capacity is limited. Rank so that the changes most likely to be
blocking are verified first."""


@dataclass
class QueueItem:
    """One change waiting to merge."""

    item_id: str
    task: Task
    title: str
    source: str
    is_defective: bool              # ground truth
    mutant: Mutant | None = None
    witness: str = ""
    image_b64: str | None = None

    # filled in by triage
    risk: float | None = None
    blocking_probability: float | None = None
    confidence: float | None = None
    error: str = ""

    @property
    def triaged(self) -> bool:
        return self.risk is not None or self.blocking_probability is not None

    @property
    def rank_key(self) -> float:
        """Higher is more urgent. Falls back to the middle when untriaged."""
        if self.blocking_probability is not None:
            return self.blocking_probability
        if self.risk is not None:
            return self.risk / 10.0
        return 0.5


@dataclass
class Ordering:
    name: str
    items: list[QueueItem]

    @property
    def defective_positions(self) -> list[int]:
        return [i for i, it in enumerate(self.items, start=1) if it.is_defective]

    @property
    def to_first_defect(self) -> int | None:
        pos = self.defective_positions
        return pos[0] if pos else None

    @property
    def to_all_defects(self) -> int | None:
        pos = self.defective_positions
        return pos[-1] if pos else None

    def found_after(self, k: int) -> int:
        return sum(1 for i, it in enumerate(self.items, start=1) if it.is_defective and i <= k)

    def precision_at(self, k: int) -> float:
        if k <= 0:
            return 0.0
        return self.found_after(k) / min(k, len(self.items))


@dataclass
class TriageReport:
    items: list[QueueItem]
    orderings: list[Ordering] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    input_tokens: int = 0
    latency_ms: int = 0

    @property
    def defective(self) -> int:
        return sum(1 for i in self.items if i.is_defective)

    @property
    def triaged(self) -> list[QueueItem]:
        return [i for i in self.items if i.triaged]

    def ordering(self, name: str) -> Ordering | None:
        return next((o for o in self.orderings if o.name == name), None)


def _triage_one(snap: SubQSnap, item: QueueItem, file_options: dict[str, str] | None) -> QueueItem:
    """One decision call per queue item: is it blocking, how urgent, where."""
    state = (
        f"[{MERGE_POLICY}]\n\n"
        f"[SPECIFICATION THE CODE MUST SATISFY]\n{item.task.spec}\n\n"
        f"[CHANGE AWAITING MERGE: {item.title}]\n{item.source}\n"
    )
    questions = {
        "is_blocking": noul(
            "Applying the MERGE POLICY above, is this change blocking?",
            when_true="It alters behaviour the specification constrains",
            when_false="Behaviour is unchanged for every input the specification constrains",
        ),
        "priority": score_q(
            "Using the policy's priority list, how urgently should this change be verified?",
            ["verify last", "verify when convenient", "verify soon", "verify first"],
        ),
    }
    if file_options:
        questions["risk_surface"] = choice(
            "Which part of the codebase does this change put at risk?", file_options
        )

    images = [{"mediaType": "image/png", "base64": item.image_b64}] if item.image_b64 else None
    res = snap.decide(state, questions, images=images) if images else snap.decide(state, questions)

    if not res.ok:
        item.error = res.error
        return item

    blocking = res.get("is_blocking")
    priority = res.get("priority")
    item.blocking_probability = float(blocking.value) if blocking and blocking.value is not None else None
    item.risk = float(priority.value) if priority and priority.value is not None else None
    item.confidence = priority.confidence if priority else None
    return item


def triage(
    items: list[QueueItem],
    snap: SubQSnap | None = None,
    file_options: dict[str, str] | None = None,
    seeds: int = 20,
) -> TriageReport:
    """Rank the queue, then score the ranking against what is actually broken."""
    import time

    snap = snap or SubQSnap()
    report = TriageReport(items=items)

    if not snap.available:
        report.notes.append("No SUBQ_API_KEY, so the queue cannot be triaged. Set it and re-run.")
        return report

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        report.items = list(pool.map(lambda it: _triage_one(snap, it, file_options), items))
    report.latency_ms = int((time.monotonic() - started) * 1000)
    report.input_tokens = snap.usage.input_tokens

    for e in {i.error for i in report.items if i.error}:
        report.notes.append(f"SubQ: {e}")

    triaged = [i for i in report.items if i.triaged]
    if not triaged:
        report.notes.append("No item was triaged; there is no ordering to score.")
        return report

    # The orderings under comparison. FIFO is the honest baseline -- it is what
    # a queue does with no triage at all.
    report.orderings.append(
        Ordering("triaged", sorted(report.items, key=lambda i: -i.rank_key))
    )
    report.orderings.append(Ordering("first-come-first-served", list(report.items)))

    # Random is averaged over seeds, so a lucky shuffle cannot flatter it.
    firsts, alls = [], []
    for s in range(seeds):
        shuffled = list(report.items)
        random.Random(s).shuffle(shuffled)
        o = Ordering(f"random-{s}", shuffled)
        if o.to_first_defect:
            firsts.append(o.to_first_defect)
        if o.to_all_defects:
            alls.append(o.to_all_defects)
    if firsts:
        report.notes.append(
            f"random baseline over {seeds} shuffles: first defect after "
            f"{statistics.mean(firsts):.1f} verification(s), all after {statistics.mean(alls):.1f}"
        )
    return report


def render_triage(report: TriageReport) -> str:
    out: list[str] = []
    out.append("=" * 78)
    out.append("MERGE QUEUE TRIAGE  --  which change do you verify first?")
    out.append("=" * 78)

    if not report.orderings:
        out.append("")
        for n in report.notes:
            out.append(f"  ! {n}")
        return "\n".join(out)

    n_items = len(report.items)
    out.append(
        f"\n{n_items} change(s) awaiting merge, {report.defective} of them actually defective"
    )
    out.append(f"  triaged in {report.latency_ms} ms, one decision call each")
    out.append("  every defective item carries a witness proving it is a real defect\n")

    tri = report.ordering("triaged")
    fifo = report.ordering("first-come-first-served")

    out.append(f"  {'ordering':28} {'to 1st defect':>14} {'to all defects':>16}")
    out.append("  " + "-" * 60)
    for o in (tri, fifo):
        if not o:
            continue
        f, a = o.to_first_defect, o.to_all_defects
        out.append(
            f"  {o.name:28} {str(f or '--'):>14} {str(a or '--'):>16}"
        )
    for note in report.notes:
        if note.startswith("random baseline"):
            out.append(f"  {note}")

    if tri and fifo and tri.to_first_defect and fifo.to_first_defect:
        saved = fifo.to_first_defect - tri.to_first_defect
        if saved > 0:
            out.append(
                f"\n  => triage reached the first real defect {saved} verification(s) sooner"
            )
        elif saved == 0:
            out.append("\n  => triage matched first-come-first-served on time to first defect")
        else:
            out.append(
                f"\n  => triage was {-saved} verification(s) SLOWER than doing nothing"
            )

    out.append("\nprecision at k (of the first k verified, how many were really broken)")
    out.append(f"  {'k':>4} {'triaged':>10} {'fifo':>10}")
    for k in (1, 2, 3, 5, min(10, n_items)):
        if k > n_items:
            break
        t = f"{tri.precision_at(k):.0%}" if tri else "--"
        f = f"{fifo.precision_at(k):.0%}" if fifo else "--"
        out.append(f"  {k:>4} {t:>10} {f:>10}")

    out.append("\nthe queue, in triaged order")
    out.append(f"  {'#':>3} {'p(blocking)':>12} {'priority':>9} {'truth':>10}  title")
    if tri:
        for i, it in enumerate(tri.items[:14], start=1):
            p = f"{it.blocking_probability:.2f}" if it.blocking_probability is not None else "--"
            pr = f"{it.risk:.0f}" if it.risk is not None else "--"
            truth = "DEFECTIVE" if it.is_defective else "clean"
            out.append(f"  {i:>3} {p:>12} {pr:>9} {truth:>10}  {it.title[:38]}")

    mis = [
        it for it in report.items
        if it.blocking_probability is not None
        and (it.blocking_probability >= 0.5) != it.is_defective
    ]
    if mis:
        out.append(f"\nmisjudged ({len(mis)} of {len(report.triaged)})")
        for it in mis[:5]:
            said = "blocking" if it.blocking_probability >= 0.5 else "clean"
            truth = "defective" if it.is_defective else "clean"
            out.append(f"  p={it.blocking_probability:.2f} said {said:9} but it is {truth:9} | {it.title[:34]}")
            if it.witness:
                out.append(f"      {it.witness}")

    imgs = [i for i in report.items if i.image_b64]
    if imgs:
        out.append(f"\n{len(imgs)} item(s) were triaged from a screenshot rather than text")

    if report.input_tokens:
        out.append(
            f"\nsubq usage: {report.input_tokens} input tokens = "
            f"${report.input_tokens * 0.04 / 1_000_000:.4f} (output free)"
        )
    for note in report.notes:
        if not note.startswith("random baseline"):
            out.append(f"  ! {note}")
    return "\n".join(out)


def build_queue(
    pairs: list[tuple[Task, list[Mutant], dict[str, Equivalence], str]],
    clean_per_task: int = 1,
    multimodal_every: int = 0,
) -> list[QueueItem]:
    """Assemble a realistic queue: some changes broken, some genuinely fine.

    A queue of nothing but defects would make any ordering look good, so clean
    changes -- the untouched reference -- are mixed in and the triage has to
    tell them apart.
    """
    from .screenshot import context_image

    items: list[QueueItem] = []
    counter = 0
    for task, mutants, table, reference in pairs:
        for m in mutants:
            counter += 1
            eq = table.get(m.mutant_id)
            items.append(
                QueueItem(
                    item_id=f"chg-{counter:03d}",
                    task=task,
                    title=f"{task.name}: {m.description[:46]}",
                    source=m.source,
                    is_defective=True,
                    mutant=m,
                    witness=eq.witness.render() if (eq and eq.witness) else "",
                    image_b64=(
                        context_image(m.source, "(no test attached)", "build failed")
                        if multimodal_every and counter % multimodal_every == 0
                        else None
                    ),
                )
            )
        for j in range(clean_per_task):
            counter += 1
            items.append(
                QueueItem(
                    item_id=f"chg-{counter:03d}",
                    task=task,
                    title=f"{task.name}: no functional change (refactor {j + 1})",
                    source=reference,
                    is_defective=False,
                )
            )
    return items
