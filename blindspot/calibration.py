"""Can a decision model predict a test suite's gaps without running it?

SubQ Snap returns calibrated probabilities rather than text. That is only worth
anything if the probabilities mean what they say, and almost nobody can check
because judging a judge needs ground truth.

We have ground truth: every defect here has a witness proving it is real, and
running a suite tells us definitively whether it caught that defect. So we can
ask SubQ, for each (suite, defect) pair, "will this suite catch this?" --
*before* executing anything -- and then score the prediction.

That produces three things:

  accuracy     does it get the call right at a 0.5 threshold
  calibration  when it says 0.8, does it happen 80% of the time
  Brier score  mean squared error of the probabilities (lower is better)

The prediction is deliberately handicapped: SubQ sees the suite source and a
description of the injected change, but never the witness input and never the
test results. Giving it the witness would leak the answer.

If it predicts well, this is a cheap pre-filter for expensive verification --
you learn where a suite is thin without running it against anything. If it
predicts badly, that is equally a result, and we report it the same way.
"""

from __future__ import annotations

import statistics
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .equivalence import Equivalence
from .measure import Suite, kills
from .mutate import Mutant
from .providers import SubQSnap, noul, score as score_q
from .roles import Task

WORKERS = 6
BUCKETS = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01))
SEVERITY_LABELS = (
    "only on exotic input",
    "on uncommon input",
    "on common input",
    "on almost any input",
)


@dataclass
class Prediction:
    suite_name: str
    mutant: Mutant
    predicted: float | None          # SubQ's probability that the suite catches it
    actual: bool | None              # measured by execution
    severity: float | None = None     # SubQ's severity score, if asked
    # Same question asked inverted ("will every test pass?"). A self-consistent
    # model answers 1 - predicted. This is the AA-Symmetry axis of the
    # Artificial Analysis Decision Index, which needs no ground truth at all.
    predicted_negated: float | None = None
    error: str = ""

    @property
    def usable(self) -> bool:
        return self.predicted is not None and self.actual is not None

    @property
    def correct(self) -> bool | None:
        if not self.usable:
            return None
        return (self.predicted >= 0.5) == self.actual

    @property
    def brier(self) -> float | None:
        if not self.usable:
            return None
        return (self.predicted - (1.0 if self.actual else 0.0)) ** 2

    @property
    def symmetry_gap(self) -> float | None:
        """|p + p_negated - 1|. Zero means perfectly self-consistent."""
        if self.predicted is None or self.predicted_negated is None:
            return None
        return abs(self.predicted + self.predicted_negated - 1.0)


@dataclass
class Bucket:
    low: float
    high: float
    predictions: list[Prediction] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.predictions)

    @property
    def mean_predicted(self) -> float | None:
        return statistics.mean(p.predicted for p in self.predictions) if self.predictions else None

    @property
    def observed(self) -> float | None:
        if not self.predictions:
            return None
        return sum(1 for p in self.predictions if p.actual) / len(self.predictions)

    @property
    def gap(self) -> float | None:
        mp, ob = self.mean_predicted, self.observed
        return None if mp is None or ob is None else mp - ob


@dataclass
class CalibrationReport:
    task: Task
    predictions: list[Prediction]
    notes: list[str] = field(default_factory=list)
    input_tokens: int = 0
    # trial count at which differential testing first found a witness. A low
    # count means the defect shows up on ordinary input, which is the closest
    # thing we have to an objective severity, so SubQ's severity score can be
    # checked against it.
    manifest_trials: dict[str, int] = field(default_factory=dict)

    def severity_vs_manifestation(self) -> list[tuple[int, int, float]]:
        """(severity level, n, mean trials to find a witness), low trials = common."""
        by_level: dict[int, list[int]] = {}
        seen: set[tuple[str, int]] = set()
        for p in self.usable:
            if p.severity is None:
                continue
            trials = self.manifest_trials.get(p.mutant.mutant_id)
            if trials is None:
                continue
            # severity is a property of the defect, not of the suite, so count
            # each defect once per level rather than once per (suite, defect).
            key = (p.mutant.mutant_id, int(p.severity))
            if key in seen:
                continue
            seen.add(key)
            by_level.setdefault(int(p.severity), []).append(trials)
        return [
            (lvl, len(v), statistics.mean(v))
            for lvl, v in sorted(by_level.items())
        ]

    @property
    def usable(self) -> list[Prediction]:
        return [p for p in self.predictions if p.usable]

    @property
    def accuracy(self) -> float | None:
        us = self.usable
        return sum(1 for p in us if p.correct) / len(us) if us else None

    @property
    def brier(self) -> float | None:
        us = self.usable
        return statistics.mean(p.brier for p in us) if us else None

    @property
    def base_rate(self) -> float | None:
        """How often suites actually caught the defect. The number to beat."""
        us = self.usable
        return sum(1 for p in us if p.actual) / len(us) if us else None

    @property
    def baseline_brier(self) -> float | None:
        """Brier score of always predicting the base rate. Beating this is the bar."""
        br = self.base_rate
        if br is None:
            return None
        us = self.usable
        return statistics.mean((br - (1.0 if p.actual else 0.0)) ** 2 for p in us)

    def confusion(self) -> dict[str, int]:
        tp = fp = tn = fn = 0
        for p in self.usable:
            said_yes = p.predicted >= 0.5
            if said_yes and p.actual:
                tp += 1
            elif said_yes and not p.actual:
                fp += 1
            elif not said_yes and p.actual:
                fn += 1
            else:
                tn += 1
        return {"tp": tp, "fp": fp, "tn": tn, "fn": fn}

    def precision_recall(self) -> tuple[float | None, float | None]:
        c = self.confusion()
        prec = c["tp"] / (c["tp"] + c["fp"]) if (c["tp"] + c["fp"]) else None
        rec = c["tp"] / (c["tp"] + c["fn"]) if (c["tp"] + c["fn"]) else None
        return prec, rec

    @property
    def decision_score(self) -> float | None:
        """The Artificial Analysis Decision Index formula, on this domain.

        'Accuracy less the Brier score of the probability the model gives its
        chosen answer, from 0 to 100.' Computed here exactly that way so the
        number is comparable in kind to the published index -- though not in
        value, since the index aggregates six evaluations and none of them is
        software verification.
        """
        acc, br = self.accuracy, self.brier
        if acc is None or br is None:
            return None
        return (acc - br) * 100

    @property
    def symmetry(self) -> float | None:
        """Mean |p + p_negated - 1| over pairs where both framings were asked."""
        gaps = [g for p in self.predictions if (g := p.symmetry_gap) is not None]
        return statistics.mean(gaps) if gaps else None

    @property
    def symmetry_worst(self) -> Prediction | None:
        pairs = [p for p in self.predictions if p.symmetry_gap is not None]
        return max(pairs, key=lambda p: p.symmetry_gap) if pairs else None

    def buckets(self) -> list[Bucket]:
        out = [Bucket(lo, hi) for lo, hi in BUCKETS]
        for p in self.usable:
            for b in out:
                if b.low <= p.predicted < b.high:
                    b.predictions.append(p)
                    break
        return out


def _ask(snap: SubQSnap, task: Task, suite: Suite, mutant: Mutant) -> tuple[float | None, float | None, str]:
    """One prediction. SubQ sees the suite and the change, never the outcome."""
    state = (
        f"[SPEC]\n{task.spec}\n\n"
        f"[SIGNATURE]\n{task.signature}\n\n"
        f"[TEST SUITE]\n{suite.source}\n\n"
        f"[CHANGE INTRODUCED INTO THE IMPLEMENTATION]\n"
        f"At line {mutant.line}: {mutant.description}\n"
        f"The original line was: {mutant.original_line}\n\n"
        f"[IMPLEMENTATION AFTER THE CHANGE]\n{mutant.source}\n"
    )
    res = snap.decide(
        state,
        {
            "will_catch": noul(
                "If this TEST SUITE is run against the implementation after the change, will at "
                "least one of its tests fail?",
                when_true="Some test exercises an input whose result the change alters",
                when_false="Every test passes because none of them reaches the changed behaviour",
            ),
            "severity": score_q(
                "How likely is this change to cause a visible failure in ordinary production use?",
                ["only on exotic input", "on uncommon input", "on common input", "on almost any input"],
            ),
        },
    )
    if not res.ok:
        return None, None, res.error

    catch = res.get("will_catch")
    sev = res.get("severity")
    predicted = float(catch.value) if catch and catch.value is not None else None
    severity = float(sev.value) if sev and sev.value is not None else None
    return predicted, severity, ""


def _ask_negated(snap: SubQSnap, task: Task, suite: Suite, mutant: Mutant) -> float | None:
    """The same question, inverted. Tests self-consistency, not correctness.

    Criteria are swapped as well as the wording, so a model that merely pattern
    matches on phrasing has no shortcut to a consistent answer.
    """
    state = (
        f"[SPEC]\n{task.spec}\n\n"
        f"[SIGNATURE]\n{task.signature}\n\n"
        f"[TEST SUITE]\n{suite.source}\n\n"
        f"[CHANGE INTRODUCED INTO THE IMPLEMENTATION]\n"
        f"At line {mutant.line}: {mutant.description}\n"
        f"The original line was: {mutant.original_line}\n\n"
        f"[IMPLEMENTATION AFTER THE CHANGE]\n{mutant.source}\n"
    )
    res = snap.decide(
        state,
        {
            "will_all_pass": noul(
                "If this TEST SUITE is run against the implementation after the change, will "
                "every one of its tests pass?",
                when_true="No test reaches the changed behaviour, so the suite is entirely green",
                when_false="At least one test exercises an input whose result the change alters",
            )
        },
    )
    a = res.get("will_all_pass") if res.ok else None
    return float(a.value) if a and a.value is not None else None


def calibrate(
    task: Task,
    suites: list[Suite],
    defects: list[Mutant],
    snap: SubQSnap | None = None,
    classifications: dict[str, Equivalence] | None = None,
    symmetry: bool = False,
) -> CalibrationReport:
    """Predict first, then execute, then score the predictions."""
    snap = snap or SubQSnap()
    report = CalibrationReport(task=task, predictions=[])

    if not snap.available:
        report.notes.append(
            "No SUBQ_API_KEY, so there are no predictions to score. Set it in .env and re-run."
        )
        return report

    valid = [s for s in suites if s.valid]
    if not valid:
        report.notes.append("No valid suite to evaluate; all were disqualified on correct code.")
        return report

    # ---- ground truth first, by execution -------------------------------- #
    truth: dict[str, set[str]] = {}
    for s in valid:
        killed, _ = kills(s, defects)
        truth[s.name] = killed

    # ---- then the predictions, which never see that ----------------------- #
    pairs = [(s, m) for s in valid for m in defects]

    def _one(pair):
        s, m = pair
        predicted, severity, err = _ask(snap, task, s, m)
        negated = _ask_negated(snap, task, s, m) if (symmetry and predicted is not None) else None
        return Prediction(
            suite_name=s.name,
            mutant=m,
            predicted=predicted,
            actual=m.mutant_id in truth[s.name],
            severity=severity,
            predicted_negated=negated,
            error=err,
        )

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        report.predictions = list(pool.map(_one, pairs))

    report.input_tokens = snap.usage.input_tokens
    for mid, eq in (classifications or {}).items():
        if eq.witness is not None:
            report.manifest_trials[mid] = eq.trials
    errs = {p.error for p in report.predictions if p.error}
    for e in list(errs)[:4]:
        report.notes.append(f"SubQ: {e}")
    return report


def render_calibration(report: CalibrationReport) -> str:
    out: list[str] = []
    out.append("=" * 78)
    out.append(f"CAN SUBQ PREDICT TEST-SUITE GAPS WITHOUT RUNNING THEM?  --  {report.task.name}")
    out.append("=" * 78)

    us = report.usable
    if not us:
        out.append("")
        for n in report.notes:
            out.append(f"  ! {n}")
        return "\n".join(out)

    acc, brier = report.accuracy, report.brier
    base, base_brier = report.base_rate, report.baseline_brier
    prec, rec = report.precision_recall()
    c = report.confusion()

    out.append(f"\n{len(us)} prediction(s) scored against executed ground truth")
    out.append(f"  SubQ never saw the witness input or any test result.\n")
    out.append(f"  accuracy @0.5         {acc:.0%}")
    if prec is not None:
        out.append(f"  precision             {prec:.0%}   (said 'will catch' and it did)")
    if rec is not None:
        out.append(f"  recall                {rec:.0%}   (of the catches, how many it foresaw)")
    out.append(f"  confusion             tp={c['tp']} fp={c['fp']} tn={c['tn']} fn={c['fn']}")
    ds = report.decision_score
    if ds is not None:
        out.append("")
        out.append(f"  DECISION SCORE        {ds:.1f} / 100")
        out.append("    = (accuracy - Brier) x 100, the Artificial Analysis Decision Index")
        out.append("      formula, applied to software verification -- a domain none of the")
        out.append("      index's six component evaluations covers.")
    out.append("")
    out.append(f"  Brier score           {brier:.3f}   (lower is better)")
    out.append(f"  always-guess-base-rate{base_brier:>7.3f}   <- the bar to beat (base rate {base:.0%})")
    verdict = (
        "beats the trivial baseline" if brier < base_brier
        else "does NOT beat always guessing the base rate"
    )
    out.append(f"  => {verdict}")

    out.append("\ncalibration: predicted probability vs what actually happened")
    out.append(f"  {'bucket':12} {'n':>4} {'mean predicted':>15} {'observed':>10} {'gap':>8}")
    for b in report.buckets():
        if not b.n:
            continue
        out.append(
            f"  {f'{b.low:.1f}-{b.high:.1f}':12} {b.n:>4} {b.mean_predicted:>14.2f} "
            f"{b.observed:>10.2f} {b.gap:>+8.2f}"
        )
    out.append("  gap > 0 means overconfident, gap < 0 means underconfident")

    sym = report.symmetry
    if sym is not None:
        out.append("\nself-consistency (AA-Symmetry style: same question, inverted)")
        out.append('  asked "will a test fail?" and "will every test pass?" with criteria swapped.')
        out.append(f"  mean |p + p_inverted - 1|   {sym:.3f}   (0.000 = perfectly consistent)")
        worst = report.symmetry_worst
        if worst is not None:
            out.append(
                f"  worst case: p={worst.predicted:.2f} inverted={worst.predicted_negated:.2f} "
                f"gap={worst.symmetry_gap:.2f}  [{worst.mutant.operator}]"
            )
        out.append("  This needs no ground truth, so it is the one metric here that")
        out.append("  transfers to any domain without a reference implementation.")

    # Severity ranking, checked against an objective proxy: the number of inputs
    # differential testing had to try before the defect showed itself.
    rows = report.severity_vs_manifestation()
    if rows:
        out.append("\nSubQ severity vs how readily the defect actually manifests")
        out.append("  (trials = inputs tried before a witness was found; fewer means the")
        out.append("   defect shows on ordinary input, so severity should fall as trials rise)")
        out.append(f"  {'severity':20} {'defects':>8} {'mean trials to witness':>24}")
        for lvl, n, mean_trials in rows:
            label = SEVERITY_LABELS[lvl] if 0 <= lvl < len(SEVERITY_LABELS) else f"level {lvl}"
            out.append(f"  {label:20} {n:>8} {mean_trials:>24.1f}")
        if len(rows) > 1:
            lo, hi = rows[0], rows[-1]
            direction = (
                "consistent: higher severity means the defect surfaces sooner"
                if hi[2] < lo[2]
                else "inconsistent with the trial counts -- severity is not tracking manifestation"
            )
            out.append(f"  => {direction}")

    biggest = sorted(
        (p for p in us if p.brier is not None), key=lambda p: p.brier, reverse=True
    )[:4]
    if biggest:
        out.append("\nworst predictions")
        for p in biggest:
            said = "will catch" if p.predicted >= 0.5 else "will miss"
            got = "caught" if p.actual else "missed"
            out.append(
                f"  {p.suite_name[:34]:34} p={p.predicted:.2f} said {said:11} "
                f"-> actually {got}   [{p.mutant.operator}]"
            )

    if report.input_tokens:
        cost = report.input_tokens * 0.04 / 1_000_000
        out.append(f"\nsubq usage: {report.input_tokens} input tokens = ${cost:.4f} (output free)")
    if report.notes:
        out.append("\nnotes")
        for n in report.notes:
            out.append(f"  ! {n}")
    return "\n".join(out)
