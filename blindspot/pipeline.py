"""The end-to-end run: implement, test twice behind an information wall, inject
known defects, measure which suite catches what, then adjudicate.

Degradation is explicit at every stage. Without a Meta key there is no
generation, so the offline stand-in suites are used and the report says so.
Without a SubQ key the adjudication is deterministic and the report says so. The
measurement itself never depends on a model being reachable, which is the point:
the numbers come from executed code.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import roles, tasks as task_lib
from .measure import Scorecard, Suite, score, validate
from .mutate import generate
from .providers import DecisionResult, MetaMuse, SubQSnap, status
from .roles import Task

SELF = "implementer-self"
INDEPENDENT = "verifier-independent"


@dataclass
class Stage:
    name: str
    detail: str
    source: str = ""        # "meta" / "subq" / "offline" / "local"


@dataclass
class RunReport:
    task: Task
    implementation: str
    scorecard: Scorecard | None
    stages: list[Stage] = field(default_factory=list)
    test_judgements: dict[str, DecisionResult] = field(default_factory=dict)
    defect_judgements: list[tuple[str, DecisionResult]] = field(default_factory=list)
    generated: bool = False          # did a model write the code/tests?
    adjudicated: bool = False        # did SubQ decide anything?
    notes: list[str] = field(default_factory=list)

    @property
    def decision(self) -> str:
        """BLOCK when a real defect survived the implementer's own tests."""
        if not self.scorecard or not self.scorecard.verdicts:
            return "INCONCLUSIVE"
        missed = self.scorecard.missed(SELF)
        recovered = [v for v in missed if v.killed_by.get(INDEPENDENT)]
        if recovered:
            return "BLOCK"
        if missed:
            return "REVIEW"
        return "ALLOW"


def _usable_suite(suite: Suite, impl: str, label: str, report: RunReport) -> bool:
    validate(suite, impl)
    if suite.valid:
        return True
    report.notes.append(f"{label} suite disqualified: {suite.disqualification()}")
    return False


def run(
    task: Task,
    use_models: bool = True,
    trials: int = 400,
    operators: list[str] | None = None,
    mutant_limit: int | None = None,
) -> RunReport:
    muse, snap = MetaMuse(), SubQSnap()
    report = RunReport(task=task, implementation="", scorecard=None)
    st = status()
    report.stages.append(Stage("providers", f"meta: {st['meta']}", "local"))
    report.stages.append(Stage("providers", f"subq: {st['subq']}", "local"))

    # ---- 1. implementation ------------------------------------------------ #
    implementation = None
    if use_models and muse.available:
        implementation = roles.implement(muse, task)
        if implementation:
            report.generated = True
            report.stages.append(Stage("implement", f"Muse wrote {len(implementation.splitlines())} lines", "meta"))
        else:
            report.notes.append("Meta generation failed; falling back to the reference implementation")
    if not implementation:
        implementation = task.reference
        report.stages.append(Stage("implement", "using the known-correct reference", "offline"))
    report.implementation = implementation

    # The measurement needs a correct baseline. If the model's implementation
    # is itself wrong, mutating it would inject defects on top of a defect and
    # every number below would be meaningless -- so we mutate the reference and
    # report the model's code separately.
    baseline = task.reference or implementation

    # ---- 2. two test suites, behind the information wall ------------------ #
    self_src = ind_src = None
    if use_models and muse.available:
        self_src = roles.write_self_tests(muse, task, implementation)
        ind_src = roles.write_independent_tests(muse, task)
        report.stages.append(
            Stage(
                "author tests",
                f"self-tests {'ok' if self_src else 'FAILED'}, "
                f"independent {'ok' if ind_src else 'FAILED'} (spec+signature only)",
                "meta",
            )
        )
    if not self_src:
        self_src = task_lib.OFFLINE_SELF_TESTS.get(task.name, "")
        report.stages.append(Stage("author tests", "self-tests: offline stand-in", "offline"))
    if not ind_src:
        ind_src = task_lib.OFFLINE_INDEPENDENT_TESTS.get(task.name, "")
        report.stages.append(Stage("author tests", "independent: offline stand-in", "offline"))

    suites = [
        Suite(SELF, muse.model if report.generated else "offline stand-in", self_src, saw_implementation=True),
        Suite(INDEPENDENT, muse.model if report.generated else "offline stand-in", ind_src, saw_implementation=False),
    ]

    kept = []
    for s in suites:
        label = "self" if s.name == SELF else "independent"
        if _usable_suite(s, baseline, label, report):
            kept.append(s)
    report.stages.append(
        Stage("validate suites", f"{len(kept)}/{len(suites)} pass cleanly on correct code", "local")
    )
    if len(kept) < 2:
        report.notes.append(
            "Both suites must be valid for the comparison to mean anything; "
            "the blind-spot figures are omitted."
        )

    # ---- 3. inject known defects and measure ------------------------------ #
    mutants = generate(baseline, operators=operators, limit=mutant_limit)
    report.stages.append(Stage("inject defects", f"{len(mutants)} single-site mutants", "local"))

    card = score(task.name, baseline, mutants, suites, task.target, trials=trials)
    report.scorecard = card
    report.stages.append(
        Stage(
            "prove defects real",
            f"{len(card.verdicts)} proven by witness, {len(card.equivalent)} equivalent, "
            f"{len(card.undecided)} undecided",
            "local",
        )
    )
    report.stages.append(
        Stage(
            "measure",
            f"self kills {card.kill_rate(SELF):.0%}, independent kills {card.kill_rate(INDEPENDENT):.0%}",
            "local",
        )
    )

    # ---- 4. adjudicate with SubQ ------------------------------------------ #
    if snap.available:
        for s in kept:
            res = roles.judge_test_independence(snap, task, s.source, implementation)
            report.test_judgements[s.name] = res
            if res.ok:
                report.adjudicated = True
        recovered = [v for v in card.missed(SELF) if v.killed_by.get(INDEPENDENT)]
        for v in recovered[:4]:
            eq = card.classifications.get(v.mutant.mutant_id)
            witness = eq.witness.render() if (eq and eq.witness) else "no witness recorded"
            failing = ", ".join(v.killing_tests.get(INDEPENDENT, []))
            res = roles.judge_defect(snap, task, witness, failing, v.mutant.description)
            report.defect_judgements.append((v.mutant.mutant_id, res))
            if res.ok:
                report.adjudicated = True
        oks = sum(1 for r in report.test_judgements.values() if r.ok) + sum(
            1 for _, r in report.defect_judgements if r.ok
        )
        report.stages.append(Stage("adjudicate", f"{oks} SubQ decision call(s) returned", "subq"))
        errs = [r.error for r in report.test_judgements.values() if r.error] + [
            r.error for _, r in report.defect_judgements if r.error
        ]
        for e in dict.fromkeys(errs):
            report.notes.append(f"SubQ: {e}")
    else:
        report.stages.append(
            Stage("adjudicate", "skipped -- no SUBQ_API_KEY; decision falls back to the executed evidence", "local")
        )

    report.stages.append(Stage("decide", report.decision, "local"))
    return report


def run_all(use_models: bool = True, **kw) -> list[RunReport]:
    return [run(t, use_models=use_models, **kw) for t in task_lib.TASKS.values()]


def aggregate(reports: list[RunReport]) -> dict:
    """Pool the per-task numbers. One task is an anecdote; the pool is the result."""
    total = killed_self = killed_ind = recovered = 0
    for r in reports:
        c = r.scorecard
        if not c:
            continue
        total += len(c.verdicts)
        killed_self += len(c.killed(SELF))
        killed_ind += len(c.killed(INDEPENDENT))
        recovered += len(c.recovered(SELF, INDEPENDENT))
    missed_self = total - killed_self
    return {
        "defects": total,
        "self_kill_rate": killed_self / total if total else 0.0,
        "independent_kill_rate": killed_ind / total if total else 0.0,
        "missed_by_self": missed_self,
        "recovered_by_independent": recovered,
        "recovery_rate": recovered / missed_self if missed_self else None,
    }
