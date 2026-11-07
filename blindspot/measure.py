"""Blind-spot measurement.

The question from the problem statement is: can a verifier write its own tests
without sharing the implementer's blind spots? That is an empirical question, so
this module answers it with a number.

Method (standard mutation testing, with one addition that matters):

  1. Start from an implementation that is correct, and a spec.
  2. Inject known single-site defects (mutants).
  3. **Validate every suite against the correct implementation first.** A suite
     that fails on correct code would "kill" every mutant for free, which would
     make the whole metric a lie. Those suites are disqualified, not scored.
  4. Run each surviving suite against each mutant. A suite kills a mutant if at
     least one of its tests fails.
  5. Report kill rates, and the number that actually answers the question:
     of the mutants the implementer's own tests missed, what share did the
     independent suite catch?

That last figure is the blind-spot recovery rate. It is the difference between
a verifier that is genuinely independent and one that merely re-states the
implementer's assumptions in test form.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .equivalence import Equivalence, partition
from .mutate import Mutant
from .runner import SuiteResult, run_suite


@dataclass
class Suite:
    """One test suite, with where it came from."""

    name: str          # e.g. "implementer-self" / "verifier-independent"
    author: str        # the model or human that wrote it
    source: str
    saw_implementation: bool = True   # did the author see the code under test?
    baseline: SuiteResult | None = None

    @property
    def valid(self) -> bool:
        """Usable only if it runs and passes on the correct implementation."""
        return bool(self.baseline and self.baseline.passed_all)

    def disqualification(self) -> str:
        if self.baseline is None:
            return "never validated against the correct implementation"
        if not self.baseline.usable:
            return f"unusable on correct code -- {self.baseline.summary()}"
        if self.baseline.failures:
            names = ", ".join(t.name for t in self.baseline.failures[:3])
            return (
                f"false alarm: {len(self.baseline.failures)} test(s) fail on the correct "
                f"implementation ({names}); cannot be scored"
            )
        return ""


@dataclass
class MutantVerdict:
    mutant: Mutant
    killed_by: dict[str, bool] = field(default_factory=dict)
    killing_tests: dict[str, list[str]] = field(default_factory=dict)

    def killed_by_any(self) -> bool:
        return any(self.killed_by.values())


@dataclass
class Scorecard:
    task: str
    suites: list[Suite]
    verdicts: list[MutantVerdict]              # proven-real defects only
    equivalent: list[tuple[Mutant, Equivalence]] = field(default_factory=list)
    undecided: list[tuple[Mutant, Equivalence]] = field(default_factory=list)
    classifications: dict[str, Equivalence] = field(default_factory=dict)

    @property
    def real_defects(self) -> int:
        return len(self.verdicts)

    def witness(self, mutant_id: str):
        eq = self.classifications.get(mutant_id)
        return eq.witness if eq else None

    def kill_rate(self, suite_name: str) -> float:
        if not self.verdicts:
            return 0.0
        killed = sum(1 for v in self.verdicts if v.killed_by.get(suite_name))
        return killed / len(self.verdicts)

    def killed(self, suite_name: str) -> list[MutantVerdict]:
        return [v for v in self.verdicts if v.killed_by.get(suite_name)]

    def missed(self, suite_name: str) -> list[MutantVerdict]:
        return [v for v in self.verdicts if not v.killed_by.get(suite_name)]

    def recovered(self, baseline_suite: str, other_suite: str) -> list[MutantVerdict]:
        """Mutants the other suite caught and the baseline suite did not."""
        return [
            v
            for v in self.verdicts
            if v.killed_by.get(other_suite) and not v.killed_by.get(baseline_suite)
        ]

    def recovery_rate(self, baseline_suite: str, other_suite: str) -> float | None:
        """P(caught by other | missed by baseline). None if nothing was missed."""
        missed = self.missed(baseline_suite)
        if not missed:
            return None
        rec = [v for v in missed if v.killed_by.get(other_suite)]
        return len(rec) / len(missed)

    def overlap(self, a: str, b: str) -> float | None:
        """Jaccard overlap of the two kill sets -- how correlated the blind spots are."""
        ka = {v.mutant.mutant_id for v in self.killed(a)}
        kb = {v.mutant.mutant_id for v in self.killed(b)}
        if not (ka | kb):
            return None
        return len(ka & kb) / len(ka | kb)

    def by_operator(self, suite_name: str) -> dict[str, tuple[int, int]]:
        """operator -> (killed, total). Shows *which* defect classes are missed."""
        out: dict[str, tuple[int, int]] = {}
        for v in self.verdicts:
            k, t = out.get(v.mutant.operator, (0, 0))
            out[v.mutant.operator] = (k + (1 if v.killed_by.get(suite_name) else 0), t + 1)
        return out


def validate(suite: Suite, correct_impl: str, timeout: float = 20.0) -> Suite:
    """Run the suite against the correct implementation. Must pass cleanly."""
    suite.baseline = run_suite(correct_impl, suite.source, timeout)
    return suite


def score(
    task: str,
    correct_impl: str,
    mutants: list[Mutant],
    suites: list[Suite],
    target: str,
    timeout: float = 20.0,
    trials: int = 400,
) -> Scorecard:
    """Run every valid suite against every *proven* defect.

    Mutants are first differential-tested against the original. Only those with
    a witness -- a concrete input where behaviour diverges -- enter the
    denominator. Equivalent mutants are unkillable by construction, so scoring
    them would penalise correct test suites for missing a non-bug.
    """
    for s in suites:
        if s.baseline is None:
            validate(s, correct_impl, timeout)

    real, equivalent, table = partition(correct_impl, mutants, target, trials)
    undecided = [(m, eq) for m, eq in equivalent if eq.error]
    equivalent = [(m, eq) for m, eq in equivalent if not eq.error]

    valid = [s for s in suites if s.valid]
    verdicts: list[MutantVerdict] = []

    for m in real:
        v = MutantVerdict(mutant=m)
        for s in valid:
            result = run_suite(m.source, s.source, timeout)
            v.killed_by[s.name] = result.detects
            v.killing_tests[s.name] = [t.name for t in result.failures]
        verdicts.append(v)

    return Scorecard(
        task=task,
        suites=suites,
        verdicts=verdicts,
        equivalent=equivalent,
        undecided=undecided,
        classifications=table,
    )
