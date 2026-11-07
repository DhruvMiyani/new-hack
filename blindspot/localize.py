"""Confidence-gated defect localisation.

Subquadratic's own demos all have one shape: a probability distribution over a
large option set in a single call, and acting only when sure. Clue Map weighs
241 countries. Chess scores every legal move. Sudoku "fills a cell only when
it's sure." Moby-Dick finds a planted sentence in 207,793 words.

The one thing those demos cannot do is grade themselves -- there is no ground
truth for "which chess move is correct". Here there is. We injected the defect,
so we know exactly which line is guilty. That turns the same interaction into a
measurement:

    state     the whole file (or repo), plus a failing test and its real output
    question  choice over every candidate line -- which one holds the defect?
    truth     the line we mutated

From which:

    top-1 accuracy          did it point at the right line
    mean p(correct)         how much mass landed on the truth
    calibration             when it says 0.8, is it right 80% of the time
    coverage/precision      the Sudoku rule: answer only above a confidence
                            threshold. Plot accuracy against how often it is
                            willing to answer at all.

That last curve is the useful artefact. A localiser that is right 95% of the
time on the 40% of cases it is confident about is deployable. One that is right
60% of the time always is not.
"""

from __future__ import annotations

import ast
import statistics
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .mutate import Mutant
from .providers import SubQSnap, choice
from .roles import Task

WORKERS = 6
MAX_OPTIONS = 255          # the Decisions API ceiling for a choice question
THRESHOLDS = (0.0, 0.3, 0.5, 0.7, 0.9)


@dataclass
class Candidate:
    key: str               # "L7"
    line: int
    text: str


def candidates(source: str, limit: int = MAX_OPTIONS) -> list[Candidate]:
    """Every line that could plausibly hold a defect.

    Blank lines, comments and bare docstrings are excluded: offering them as
    options would pad the option set and flatter the top-1 number.
    """
    out: list[Candidate] = []
    try:
        tree = ast.parse(source)
        doc_lines = set()
        for node in ast.walk(tree):
            doc = ast.get_docstring(node) if isinstance(
                node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ) else None
            if doc and getattr(node, "body", None):
                first = node.body[0]
                for ln in range(first.lineno, (first.end_lineno or first.lineno) + 1):
                    doc_lines.add(ln)
    except SyntaxError:
        doc_lines = set()

    for i, raw in enumerate(source.splitlines(), start=1):
        text = raw.strip()
        if not text or text.startswith("#") or i in doc_lines:
            continue
        out.append(Candidate(key=f"L{i}", line=i, text=text))
        if len(out) >= limit:
            break
    return out


def repo_candidates(
    primary_source: str,
    primary_name: str,
    extra_files: list[tuple[str, str]],
    limit: int = MAX_OPTIONS,
) -> list[Candidate]:
    """Candidate lines pooled across a whole repository.

    The point of a large option set is that picking correctly is hard: with ten
    candidates a coin-flip looks competent. Pooling real lines from the rest of
    the codebase pushes the option count toward the 255 ceiling and the random
    baseline toward noise, which is the regime Subquadratic's own Clue Map demo
    operates in (241 countries in one call).

    The guilty file's lines always come first so they are never crowded out.
    """
    out = [
        Candidate(key=f"{primary_name}:{c.line}", line=c.line, text=c.text)
        for c in candidates(primary_source, limit)
    ]
    seen_text = {c.text for c in out}
    for name, src in extra_files:
        if len(out) >= limit:
            break
        for c in candidates(src, limit):
            if len(out) >= limit:
                break
            if c.text in seen_text:
                continue
            seen_text.add(c.text)
            out.append(Candidate(key=f"{name}:{c.line}", line=-1, text=c.text))
    return out


@dataclass
class Localisation:
    mutant: Mutant
    truth_line: int
    picked_line: int | None
    confidence: float | None
    probability_on_truth: float | None
    n_options: int
    modality: str = "text"
    error: str = ""

    @property
    def usable(self) -> bool:
        return self.picked_line is not None and not self.error

    @property
    def correct(self) -> bool | None:
        return None if not self.usable else self.picked_line == self.truth_line


@dataclass
class LocalisationReport:
    task: Task
    results: list[Localisation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    input_tokens: int = 0

    @property
    def usable(self) -> list[Localisation]:
        return [r for r in self.results if r.usable]

    def accuracy(self, min_confidence: float = 0.0) -> float | None:
        answered = [r for r in self.usable if (r.confidence or 0.0) >= min_confidence]
        if not answered:
            return None
        return sum(1 for r in answered if r.correct) / len(answered)

    def coverage(self, min_confidence: float) -> float:
        """Fraction of cases it is willing to answer at this threshold."""
        if not self.usable:
            return 0.0
        answered = [r for r in self.usable if (r.confidence or 0.0) >= min_confidence]
        return len(answered) / len(self.usable)

    @property
    def mean_probability_on_truth(self) -> float | None:
        vals = [r.probability_on_truth for r in self.usable if r.probability_on_truth is not None]
        return statistics.mean(vals) if vals else None

    @property
    def mean_options(self) -> float | None:
        return statistics.mean(r.n_options for r in self.usable) if self.usable else None

    @property
    def random_baseline(self) -> float | None:
        """1/n averaged -- what blind guessing would score. The bar."""
        if not self.usable:
            return None
        return statistics.mean(1 / r.n_options for r in self.usable if r.n_options)

    def by_modality(self, modality: str) -> list[Localisation]:
        return [r for r in self.usable if r.modality == modality]


def _line_of_key(key: str) -> int | None:
    """Keys are 'L7' for single-file options or 'path.py:7' at repo scale.

    A key from another file has line -1 and can never match the truth line,
    which is the intended behaviour: picking the wrong file is a wrong answer.
    """
    tail = key.rsplit(":", 1)[-1] if ":" in key else key
    tail = tail[1:] if tail.startswith("L") else tail
    try:
        return int(tail)
    except ValueError:
        return None


def _ask(
    snap: SubQSnap,
    task: Task,
    mutant: Mutant,
    failing_test: str,
    failure_output: str,
    opts: list[Candidate],
    image_b64: str | None = None,
) -> Localisation:
    options = {
        c.key: (f"line {c.line}: {c.text}" if c.line > 0 else c.text)[:300] for c in opts
    }
    state = (
        f"[SPEC]\n{task.spec}\n\n"
        f"[IMPLEMENTATION UNDER TEST]\n{mutant.source}\n\n"
        f"[A TEST THAT FAILS AGAINST IT]\n{failing_test}\n\n"
        f"[OBSERVED FAILURE]\n{failure_output}\n"
    )
    questions = {
        "defect_line": choice(
            "Exactly one line of the implementation is wrong and causes this failure. "
            "Which line is it?",
            options,
        )
    }
    kwargs = {}
    if image_b64:
        kwargs["images"] = [{"mediaType": "image/png", "base64": image_b64}]

    res = snap.decide(state, questions, **kwargs) if kwargs else snap.decide(state, questions)
    if not res.ok:
        return Localisation(mutant, mutant.line, None, None, None, len(opts),
                            "image" if image_b64 else "text", res.error)

    a = res.get("defect_line")
    picked = None
    if a and isinstance(a.value, str):
        picked = _line_of_key(a.value)

    truth_key = next((c.key for c in opts if c.line == mutant.line), f"L{mutant.line}")
    p_truth = a.probabilities.get(truth_key) if a and a.probabilities else None
    return Localisation(
        mutant=mutant,
        truth_line=mutant.line,
        picked_line=picked,
        confidence=a.confidence if a else None,
        probability_on_truth=float(p_truth) if p_truth is not None else None,
        n_options=len(opts),
        modality="image" if image_b64 else "text",
    )


def localise(
    task: Task,
    defects: list[Mutant],
    failing_tests: dict[str, tuple[str, str]],
    snap: SubQSnap | None = None,
    images: dict[str, str] | None = None,
    distractors: list[tuple[str, str]] | None = None,
) -> LocalisationReport:
    """For each proven defect, ask which line is guilty and score the answer.

    failing_tests maps mutant_id -> (test source, observed failure text).
    images optionally maps mutant_id -> base64 PNG, to run the same question
    through the multimodal path for comparison.
    """
    snap = snap or SubQSnap()
    report = LocalisationReport(task=task)

    if not snap.available:
        report.notes.append("No SUBQ_API_KEY, so there is nothing to localise. Set it and re-run.")
        return report

    jobs = []
    for m in defects:
        if m.mutant_id not in failing_tests:
            continue
        test_src, failure = failing_tests[m.mutant_id]
        opts = (
            repo_candidates(m.source, f"{task.name}.py", distractors)
            if distractors
            else candidates(m.source)
        )
        if not opts:
            continue
        jobs.append((m, test_src, failure, opts, None))
        if images and m.mutant_id in images:
            jobs.append((m, test_src, failure, opts, images[m.mutant_id]))

    if not jobs:
        report.notes.append("No defect had a failing test to localise from.")
        return report

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        report.results = list(
            pool.map(lambda j: _ask(snap, task, j[0], j[1], j[2], j[3], j[4]), jobs)
        )
    report.input_tokens = snap.usage.input_tokens
    for e in {r.error for r in report.results if r.error}:
        report.notes.append(f"SubQ: {e}")
    return report


def render_localisation(report: LocalisationReport) -> str:
    out: list[str] = []
    out.append("=" * 78)
    out.append(f"DEFECT LOCALISATION  --  {report.task.name}")
    out.append("=" * 78)

    us = report.usable
    if not us:
        out.append("")
        for n in report.notes:
            out.append(f"  ! {n}")
        return "\n".join(out)

    base = report.random_baseline
    out.append(f"\n{len(us)} localisation(s), averaging {report.mean_options:.0f} candidate lines each")
    out.append(f"  one call per defect; the whole implementation is in the prompt\n")
    out.append(f"  top-1 accuracy        {report.accuracy():.0%}")
    if base is not None:
        out.append(f"  random guessing       {base:.0%}   <- the bar")
    mp = report.mean_probability_on_truth
    if mp is not None:
        out.append(f"  mean p(correct line)  {mp:.2f}")

    out.append("\nconfidence gating -- the Sudoku rule: answer only when sure")
    out.append(f"  {'threshold':>10} {'coverage':>10} {'accuracy':>10}")
    for t in THRESHOLDS:
        cov = report.coverage(t)
        acc = report.accuracy(t)
        acc_s = f"{acc:.0%}" if acc is not None else "--"
        out.append(f"  {t:>10.1f} {cov:>10.0%} {acc_s:>10}")
    out.append("  A localiser that is right most of the time on the cases it is confident")
    out.append("  about is deployable; one that always answers at chance is not.")

    text_r = report.by_modality("text")
    image_r = report.by_modality("image")
    if text_r and image_r:
        t_acc = sum(1 for r in text_r if r.correct) / len(text_r)
        i_acc = sum(1 for r in image_r if r.correct) / len(image_r)
        out.append("\nmultimodal comparison -- same question, context delivered two ways")
        out.append(f"  as text         {t_acc:>5.0%}  ({len(text_r)} case(s))")
        out.append(f"  as a screenshot {i_acc:>5.0%}  ({len(image_r)} case(s))")
        delta = i_acc - t_acc
        out.append(
            f"  => the image path {'matches' if abs(delta) < 0.1 else 'differs from'} the text "
            f"path ({delta:+.0%})"
        )

    wrong = [r for r in us if r.correct is False]
    if wrong:
        out.append(f"\nmislocalised ({len(wrong)})")
        for r in wrong[:5]:
            p = f"{r.probability_on_truth:.2f}" if r.probability_on_truth is not None else "--"
            out.append(
                f"  {r.mutant.operator:11} truth L{r.truth_line} picked L{r.picked_line} "
                f"(conf {r.confidence or 0:.2f}, p on truth {p})"
            )

    if report.input_tokens:
        out.append(
            f"\nsubq usage: {report.input_tokens} input tokens = "
            f"${report.input_tokens * 0.04 / 1_000_000:.4f} (output free)"
        )
    for n in report.notes:
        out.append(f"  ! {n}")
    return "\n".join(out)
