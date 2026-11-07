"""The experiment matrix: separate *information* independence from *model*
independence, with error bars.

The single-pair result ("spec-only tests caught more") invites an obvious
objection: maybe that is just prompt noise, or one lucky sample. So this varies
three axes independently and samples each cell k times:

    information   saw-code | spec-only     (did the author read the implementation?)
    model         any of Meta's authoring models
    reasoning     Meta's reasoningEffort, when we want to sweep it

Two different mechanisms could explain a verifier catching more defects:

  1. it was denied the implementation, so it could not inherit its assumptions
  2. it is a different model, with differently-shaped blind spots

They are usually conflated. Holding the model fixed and varying only the
information isolates (1); holding the information fixed and varying the model
isolates (2). The proven-defect set is computed once per task and reused across
every cell, so each cell differs *only* in the axis under test.
"""

from __future__ import annotations

import statistics
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import roles
from .equivalence import Equivalence, partition
from .measure import Suite, kills, validate
from .mutate import Mutant, generate
from .providers import AUTHOR_MODELS, MetaMuse
from .roles import Task

SAW_CODE = "saw-code"
SPEC_ONLY = "spec-only"
CONDITIONS = (SAW_CODE, SPEC_ONLY)

# Generation is network-bound and the published limit is 3000 RPM, so this is
# conservative. Scoring is subprocess-bound, hence the separate smaller cap.
GEN_WORKERS = 8
SCORE_WORKERS = 4


@dataclass
class Cell:
    model: str
    condition: str
    sample: int
    reasoning: str | None = None
    suite: Suite | None = None
    killed: set[str] = field(default_factory=set)
    killing_tests: dict[str, list[str]] = field(default_factory=dict)
    n_tests: int = 0
    error: str = ""

    @property
    def key(self) -> tuple[str, str, str | None]:
        return (self.model, self.condition, self.reasoning)

    @property
    def usable(self) -> bool:
        return bool(self.suite and self.suite.valid and not self.error)

    def kill_rate(self, total: int) -> float | None:
        if not self.usable or not total:
            return None
        return len(self.killed) / total


@dataclass
class CellGroup:
    """All samples for one (model, condition, reasoning) combination."""

    model: str
    condition: str
    reasoning: str | None
    cells: list[Cell]
    total_defects: int

    @property
    def usable_cells(self) -> list[Cell]:
        return [c for c in self.cells if c.usable]

    @property
    def rates(self) -> list[float]:
        return [r for c in self.usable_cells if (r := c.kill_rate(self.total_defects)) is not None]

    @property
    def mean(self) -> float | None:
        rs = self.rates
        return sum(rs) / len(rs) if rs else None

    @property
    def stdev(self) -> float | None:
        rs = self.rates
        return statistics.stdev(rs) if len(rs) > 1 else 0.0 if rs else None

    @property
    def union(self) -> set[str]:
        """Defects caught by at least one sample -- the ceiling for this cell."""
        out: set[str] = set()
        for c in self.usable_cells:
            out |= c.killed
        return out

    @property
    def intersection(self) -> set[str] | None:
        """Caught by every sample -- the reliable floor."""
        us = self.usable_cells
        if not us:
            return None
        out = set(us[0].killed)
        for c in us[1:]:
            out &= c.killed
        return out

    def label(self) -> str:
        r = f" @{self.reasoning}" if self.reasoning else ""
        return f"{self.model}{r} / {self.condition}"


@dataclass
class MatrixResult:
    task: Task
    defects: list[Mutant]
    classifications: dict[str, Equivalence]
    groups: list[CellGroup]
    implementer_model: str = ""      # who wrote the code the self-tests saw
    equivalent: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.defects)

    def group(self, model: str, condition: str, reasoning: str | None = None) -> CellGroup | None:
        for g in self.groups:
            if g.model == model and g.condition == condition and g.reasoning == reasoning:
                return g
        return None

    def information_effect(self, model: str, reasoning: str | None = None) -> float | None:
        """Same model, spec-only minus saw-code. Isolates information independence."""
        a = self.group(model, SAW_CODE, reasoning)
        b = self.group(model, SPEC_ONLY, reasoning)
        if a and b and a.mean is not None and b.mean is not None:
            return b.mean - a.mean
        return None

    def model_effect(self, condition: str, base: str, other: str) -> float | None:
        """Same information, different model. Isolates model independence."""
        a = self.group(base, condition)
        b = self.group(other, condition)
        if a and b and a.mean is not None and b.mean is not None:
            return b.mean - a.mean
        return None

    def cross_model_effect(self, condition: str) -> float | None:
        """Test author is a *different* model from the implementer, minus same-model.

        This is the axis people mean by "use a different model to review" --
        and it is only meaningful because the implementer is named.
        """
        same = self.group(self.implementer_model, condition)
        others = [
            g for g in self.groups
            if g.condition == condition and g.model != self.implementer_model and g.mean is not None
        ]
        if not same or same.mean is None or not others:
            return None
        return sum(g.mean for g in others) / len(others) - same.mean

    def cross_family_union(self, condition: str) -> set[str]:
        """Pooling every model under one condition -- an ensemble ceiling."""
        out: set[str] = set()
        for g in self.groups:
            if g.condition == condition:
                out |= g.union
        return out


def _author(
    muse: MetaMuse,
    task: Task,
    implementation: str,
    cell: Cell,
) -> Cell:
    """Generate one test suite for one cell. Never raises."""
    try:
        if cell.condition == SAW_CODE:
            src = muse.complete(
                roles.SELF_TESTER_SYSTEM,
                f"Specification:\n{task.spec}\n\nThe implementation you are testing:\n"
                f"```python\n{implementation}\n```\n\nWrite the test suite.",
                max_tokens=2000,
                temperature=0.4,
                model=cell.model,
                reasoning_effort=cell.reasoning,
                seed=1000 + cell.sample,
            )
        else:
            src = muse.complete(
                roles.INDEPENDENT_TESTER_SYSTEM,
                f"Specification:\n{task.spec}\n\nThe function you are testing (signature only):\n"
                f"```python\n{task.signature}\n```\n\n"
                "Write about 8 tests. You will never see the implementation.",
                max_tokens=2000,
                temperature=0.4,
                model=cell.model,
                reasoning_effort=cell.reasoning,
                seed=1000 + cell.sample,
            )
        if not src:
            cell.error = "generation returned nothing"
            return cell
        from .runner import extract_code

        cell.suite = Suite(
            name=f"{cell.model}:{cell.condition}:{cell.sample}",
            author=cell.model,
            source=extract_code(src),
            saw_implementation=cell.condition == SAW_CODE,
        )
    except Exception as exc:
        cell.error = f"generation failed: {exc!r}"[:180]
    return cell


def run_matrix(
    task: Task,
    models: list[str] | None = None,
    samples: int = 3,
    reasoning_levels: list[str | None] | None = None,
    trials: int = 400,
    muse: MetaMuse | None = None,
    implementer_model: str | None = None,
) -> MatrixResult:
    """Build and run the full grid for one task."""
    muse = muse or MetaMuse()
    models = models or [AUTHOR_MODELS[0]]
    reasoning_levels = reasoning_levels or [None]
    # The implementer is named explicitly: "a different model" only means
    # something relative to whoever wrote the code.
    implementer = implementer_model or models[0]

    result = MatrixResult(
        task=task, defects=[], classifications={}, groups=[], implementer_model=implementer
    )

    if not muse.available:
        result.notes.append(
            "No Meta key configured, so the matrix cannot be run -- every cell needs a "
            "freshly generated suite. Use `verify.py run --offline` for the single-pair result."
        )
        return result

    # ---- the implementation under test, written once, by a named model ---- #
    implementation = roles.implement(muse, task, model=implementer) or task.reference

    # ---- proven defects, computed once and reused by every cell ----------- #
    baseline = task.reference or implementation
    mutants = generate(baseline)
    real, equivalent, table = partition(baseline, mutants, task.target, trials)
    result.defects = real
    result.classifications = table
    result.equivalent = len(equivalent)
    if not real:
        result.notes.append("No mutant could be proven a real defect; nothing to score.")
        return result

    # ---- generate every cell's suite in parallel -------------------------- #
    cells: list[Cell] = [
        Cell(model=m, condition=c, sample=s, reasoning=r)
        for m in models
        for c in CONDITIONS
        for r in reasoning_levels
        for s in range(samples)
    ]
    with ThreadPoolExecutor(max_workers=GEN_WORKERS) as pool:
        cells = list(pool.map(lambda c: _author(muse, task, implementation, c), cells))

    # ---- validate on correct code, then score against proven defects ------ #
    def _evaluate(cell: Cell) -> Cell:
        if not cell.suite:
            return cell
        validate(cell.suite, baseline)
        cell.n_tests = len(cell.suite.baseline.tests) if cell.suite.baseline else 0
        if not cell.suite.valid:
            cell.error = cell.suite.disqualification()
            return cell
        cell.killed, cell.killing_tests = kills(cell.suite, real)
        return cell

    with ThreadPoolExecutor(max_workers=SCORE_WORKERS) as pool:
        cells = list(pool.map(_evaluate, cells))

    # ---- group ------------------------------------------------------------ #
    seen: dict[tuple, list[Cell]] = {}
    for c in cells:
        seen.setdefault(c.key, []).append(c)
    result.groups = [
        CellGroup(model=k[0], condition=k[1], reasoning=k[2], cells=v, total_defects=len(real))
        for k, v in seen.items()
    ]
    result.groups.sort(key=lambda g: (g.model, g.condition, g.reasoning or ""))

    dq = [c for c in cells if c.error]
    if dq:
        result.notes.append(f"{len(dq)}/{len(cells)} cell(s) unusable; each is listed with its reason.")
    result.notes.extend(muse.notes[:6])
    return result


def render_matrix(result: MatrixResult) -> str:
    """Terminal rendering of the grid."""
    out: list[str] = []
    t = result.task
    out.append("=" * 78)
    out.append(f"EXPERIMENT MATRIX  --  {t.name}")
    out.append("=" * 78)

    if not result.defects:
        out.append("")
        for n in result.notes:
            out.append(f"  ! {n}")
        return "\n".join(out)

    n = result.total
    out.append(f"\nimplementer: {result.implementer_model}  (the code the saw-code cells read)")
    out.append(f"{n} proven defect(s) (+{result.equivalent} excluded as equivalent), "
               f"reused unchanged across every cell")
    out.append("")
    out.append(f"  {'model / condition':44} {'mean':>7} {'sd':>6} {'floor':>7} {'ceil':>7} {'n':>3}")
    out.append("  " + "-" * 74)
    for g in result.groups:
        mean, sd = g.mean, g.stdev
        inter = g.intersection
        if mean is None:
            out.append(f"  {g.label():44} {'--':>7} {'--':>6} {'--':>7} {'--':>7} {0:>3}")
            continue
        floor = f"{len(inter)/n:.0%}" if inter is not None else "--"
        out.append(
            f"  {g.label():44} {mean:>6.0%} {sd:>6.2f} {floor:>7} "
            f"{len(g.union)/n:>6.0%} {len(g.usable_cells):>3}"
        )
    out.append("")
    out.append("  mean  average over samples      floor  caught by every sample")
    out.append("  sd    spread across samples     ceil   caught by at least one")

    # the two effects, separated
    out.append("\neffect of withholding the implementation (same model, information varies)")
    for model in sorted({g.model for g in result.groups}):
        for r in sorted({g.reasoning for g in result.groups}, key=lambda x: x or ""):
            eff = result.information_effect(model, r)
            if eff is None:
                continue
            tag = f"{model}" + (f" @{r}" if r else "")
            sign = "+" if eff >= 0 else ""
            out.append(f"  {tag:44} {sign}{eff:.0%}")

    models = sorted({g.model for g in result.groups})
    if len(models) > 1:
        out.append("\neffect of changing the model (same information, model varies)")
        base = models[0]
        for cond in CONDITIONS:
            for other in models[1:]:
                eff = result.model_effect(cond, base, other)
                if eff is None:
                    continue
                sign = "+" if eff >= 0 else ""
                out.append(f"  {cond:14} {base} -> {other:26} {sign}{eff:.0%}")

        out.append("\neffect of the test author being a different model from the implementer")
        for cond in CONDITIONS:
            eff = result.cross_model_effect(cond)
            if eff is None:
                continue
            sign = "+" if eff >= 0 else ""
            out.append(f"  {cond:14} vs implementer {result.implementer_model:24} {sign}{eff:.0%}")

        for cond in CONDITIONS:
            u = result.cross_family_union(cond)
            out.append(f"\n  all models pooled, {cond:10} catches {len(u)}/{n} = {len(u)/n:.0%}")

    bad = [c for c in (cell for g in result.groups for cell in g.cells) if c.error]
    if bad:
        out.append(f"\nunusable cells ({len(bad)})")
        for c in bad[:8]:
            out.append(f"  {c.model}/{c.condition}#{c.sample}: {c.error[:90]}")

    if result.notes:
        out.append("\nnotes")
        for note in result.notes:
            out.append(f"  ! {note}")
    return "\n".join(out)
