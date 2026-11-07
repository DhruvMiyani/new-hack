"""One bug, hidden in up to 384K tokens of real code. Find the line.

Subquadratic's Moby-Dick demo plants a sentence Melville never wrote in 207,793
words and closes in on it. The code version of that is more useful and much
easier to grade: inject a single known defect into a large real codebase and
ask which line is guilty.

Two axes matter for a long-context claim, and neither shows up in a demo that
only runs at one size:

    context size   10K ... 384K tokens of surrounding code
    needle depth   where in that context the broken file sits, 0.0 to 1.0

Depth is the interesting one. "Lost in the middle" is the standard long-context
failure: models attend well to the start and end of a window and poorly to the
centre. If that holds here, accuracy at depth 0.5 drops below depth 0.0 and
1.0, and a verifier that only ever sees small repos would never notice.

Filler is real Python -- this project plus the standard library -- never
generated padding, so the distractors are plausible code rather than noise a
model can trivially skip.

A guard worth stating: the needle must survive into the request. The provider
truncates oversized state, and a silently truncated needle would make every
accuracy number a lie, so `build` verifies the needle is present in the final
string and refuses the trial otherwise.
"""

from __future__ import annotations

import pathlib
import statistics
import sysconfig
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .localize import MAX_OPTIONS, Candidate, _line_of_key, candidates
from .mutate import Mutant
from .providers import CHARS_PER_TOKEN, SUBQ_CONTEXT_TOKENS, SubQSnap, choice
from .roles import Task

WORKERS = 3                     # large bodies; keep concurrency modest
DEFAULT_SIZES = (10_000, 50_000, 150_000, 350_000)
DEFAULT_DEPTHS = (0.0, 0.5, 1.0)
NEEDLE_MARKER = "# ---- module under verification ----"


MIN_FILLER_CHARS = 400


def _filler_files() -> list[tuple[str, str]]:
    """Real Python: this repo, then the standard library.

    Trivially small files are skipped. A package marker contributes nothing as
    haystack material and would inflate the file count without adding anything
    the model has to read past.
    """
    out: list[tuple[str, str]] = []
    here = pathlib.Path(__file__).resolve().parent.parent
    for p in sorted(here.rglob("*.py")):
        if ".venv" in str(p) or p.name == "haystack.py":
            continue
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        if len(text) >= MIN_FILLER_CHARS:
            out.append((str(p.relative_to(here)), text))
    stdlib = pathlib.Path(sysconfig.get_paths()["stdlib"])
    for p in sorted(stdlib.glob("*.py")):
        try:
            if p.stat().st_size > 2000:
                out.append((f"stdlib/{p.name}", p.read_text(errors="replace")))
        except OSError:
            continue
    return out


@dataclass
class Haystack:
    state: str
    options: list[Candidate]
    tokens: int
    depth: float
    needle_name: str
    filler_files: int

    @property
    def window_fraction(self) -> float:
        return self.tokens / SUBQ_CONTEXT_TOKENS


def build(
    needle_source: str,
    needle_name: str,
    target_tokens: int,
    depth: float,
    filler: list[tuple[str, str]] | None = None,
) -> Haystack:
    """Assemble `target_tokens` of real code with the needle at `depth`.

    Raises ValueError if the needle would not survive into the final string --
    better to refuse a trial than to report an accuracy computed on a context
    that never contained the bug.
    """
    filler = filler if filler is not None else _filler_files()
    budget_chars = int(min(target_tokens, SUBQ_CONTEXT_TOKENS - 4000) * CHARS_PER_TOKEN)

    needle_block = (
        f"\n\n{NEEDLE_MARKER}\n"
        f"### FILE: {needle_name}\n{needle_source}\n"
        f"# ---- end module under verification ----\n\n"
    )
    room = budget_chars - len(needle_block)
    if room <= 0:
        raise ValueError("target context is too small to hold the needle at all")

    before_budget = int(room * depth)
    before: list[str] = []
    after: list[str] = []
    used_before = used_after = 0
    used_files = 0

    for name, src in filler:
        block = f"\n### FILE: {name}\n{src}\n"
        if used_before + len(block) <= before_budget:
            before.append(block)
            used_before += len(block)
            used_files += 1
        elif used_after + len(block) <= room - before_budget:
            after.append(block)
            used_after += len(block)
            used_files += 1
        if used_before >= before_budget and used_after >= room - before_budget:
            break

    state = "".join(before) + needle_block + "".join(after)

    # The guard. A truncated needle would invalidate the whole trial.
    if NEEDLE_MARKER not in state or needle_source.strip()[:60] not in state:
        raise ValueError("needle did not survive assembly; refusing the trial")

    opts = [
        Candidate(key=f"{needle_name}:{c.line}", line=c.line, text=c.text)
        for c in candidates(needle_source)
    ]
    seen = {c.text for c in opts}
    for name, src in filler:
        if len(opts) >= MAX_OPTIONS:
            break
        for c in candidates(src, MAX_OPTIONS):
            if len(opts) >= MAX_OPTIONS:
                break
            if c.text in seen or len(c.text) < 12:
                continue
            seen.add(c.text)
            opts.append(Candidate(key=f"{name}:{c.line}", line=-1, text=c.text))

    return Haystack(
        state=state,
        options=opts,
        tokens=int(len(state) / CHARS_PER_TOKEN),
        depth=depth,
        needle_name=needle_name,
        filler_files=used_files,
    )


@dataclass
class Trial:
    target_tokens: int
    actual_tokens: int
    depth: float
    truth_line: int
    picked_line: int | None
    confidence: float | None
    probability_on_truth: float | None
    n_options: int
    latency_ms: int = 0
    error: str = ""

    @property
    def usable(self) -> bool:
        return self.picked_line is not None and not self.error

    @property
    def correct(self) -> bool | None:
        return None if not self.usable else self.picked_line == self.truth_line


@dataclass
class HaystackReport:
    task: Task
    mutant: Mutant
    trials: list[Trial] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    input_tokens: int = 0

    @property
    def usable(self) -> list[Trial]:
        return [t for t in self.trials if t.usable]

    def accuracy_at_size(self, size: int) -> float | None:
        ts = [t for t in self.usable if t.target_tokens == size]
        return sum(1 for t in ts if t.correct) / len(ts) if ts else None

    def accuracy_at_depth(self, depth: float) -> float | None:
        ts = [t for t in self.usable if t.depth == depth]
        return sum(1 for t in ts if t.correct) / len(ts) if ts else None

    def cell(self, size: int, depth: float) -> Trial | None:
        return next(
            (t for t in self.usable if t.target_tokens == size and t.depth == depth), None
        )

    @property
    def largest_correct(self) -> Trial | None:
        ok = [t for t in self.usable if t.correct]
        return max(ok, key=lambda t: t.actual_tokens) if ok else None


def _run_trial(
    snap: SubQSnap, task: Task, mutant: Mutant, size: int, depth: float, filler
) -> Trial:
    import time

    try:
        hay = build(mutant.source, f"{task.name}.py", size, depth, filler)
    except ValueError as exc:
        return Trial(size, 0, depth, mutant.line, None, None, None, 0, error=str(exc))

    options = {
        c.key: (f"line {c.line}: {c.text}" if c.line > 0 else c.text)[:300] for c in hay.options
    }
    state = (
        f"[SPECIFICATION FOR {task.name}]\n{task.spec}\n\n"
        f"[CODEBASE]\nSomewhere below, inside the file marked "
        f"'{NEEDLE_MARKER}', exactly one line is wrong and violates the "
        f"specification above. Everything else is correct.\n\n" + hay.state
    )

    started = time.monotonic()
    res = snap.decide(
        state,
        {
            "defect_line": choice(
                "Which single line violates the specification?", options
            )
        },
    )
    latency = int((time.monotonic() - started) * 1000)

    if not res.ok:
        return Trial(size, hay.tokens, depth, mutant.line, None, None, None,
                     len(hay.options), latency, res.error)

    a = res.get("defect_line")
    picked = _line_of_key(a.value) if a and isinstance(a.value, str) else None
    truth_key = f"{task.name}.py:{mutant.line}"
    p_truth = a.probabilities.get(truth_key) if a and a.probabilities else None
    return Trial(
        target_tokens=size,
        actual_tokens=hay.tokens,
        depth=depth,
        truth_line=mutant.line,
        picked_line=picked,
        confidence=a.confidence if a else None,
        probability_on_truth=float(p_truth) if p_truth is not None else None,
        n_options=len(hay.options),
        latency_ms=latency,
    )


def run_haystack(
    task: Task,
    mutant: Mutant,
    sizes: tuple[int, ...] = DEFAULT_SIZES,
    depths: tuple[float, ...] = DEFAULT_DEPTHS,
    snap: SubQSnap | None = None,
) -> HaystackReport:
    snap = snap or SubQSnap()
    report = HaystackReport(task=task, mutant=mutant)

    if not snap.available:
        report.notes.append("No SUBQ_API_KEY, so there is no haystack to search. Set it and re-run.")
        return report

    filler = _filler_files()
    jobs = [(s, d) for s in sizes for d in depths]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        report.trials = list(
            pool.map(lambda j: _run_trial(snap, task, mutant, j[0], j[1], filler), jobs)
        )
    report.input_tokens = snap.usage.input_tokens
    for e in {t.error for t in report.trials if t.error}:
        report.notes.append(f"SubQ: {e}")
    return report


def render_haystack(report: HaystackReport) -> str:
    out: list[str] = []
    out.append("=" * 78)
    out.append("NEEDLE IN A CODEBASE  --  one known bug, hidden in real Python")
    out.append("=" * 78)

    us = report.usable
    if not us:
        out.append("")
        for n in report.notes:
            out.append(f"  ! {n}")
        return "\n".join(out)

    m = report.mutant
    out.append(f"\nthe needle: {m.operator} at line {m.line} -- {m.description}")
    out.append(f"  was: {m.original_line}")
    n_opts = statistics.mean(t.n_options for t in us)
    out.append(f"  answer chosen from {n_opts:.0f} candidate lines "
               f"(random guessing = {1/n_opts:.1%})")

    sizes = sorted({t.target_tokens for t in us})
    depths = sorted({t.depth for t in us})

    out.append("\naccuracy by context size and needle depth")
    header = "  " + f"{'context':>12}" + "".join(f"{'depth ' + f'{d:.1f}':>12}" for d in depths)
    out.append(header)
    out.append("  " + "-" * (12 + 12 * len(depths)))
    for s in sizes:
        row = f"  {s:>11,}"
        for d in depths:
            c = report.cell(s, d)
            row += f"{('hit' if c.correct else 'miss') if c else '--':>12}"
        out.append(row)

    out.append("\n  by size:")
    for s in sizes:
        acc = report.accuracy_at_size(s)
        cells = [t for t in us if t.target_tokens == s]
        lat = statistics.mean(t.latency_ms for t in cells) if cells else 0
        frac = statistics.mean(t.actual_tokens for t in cells) / SUBQ_CONTEXT_TOKENS
        out.append(
            f"    {s:>8,} tokens ({frac:>5.0%} of window)  accuracy {acc:>4.0%}  "
            f"mean latency {lat:>6.0f} ms"
        )

    out.append("\n  by depth (the lost-in-the-middle axis):")
    for d in depths:
        acc = report.accuracy_at_depth(d)
        where = {0.0: "at the very start", 0.5: "in the middle", 1.0: "at the very end"}.get(
            d, f"at {d:.0%}"
        )
        out.append(f"    needle {where:<20} accuracy {acc:>4.0%}")
    mid, ends = report.accuracy_at_depth(0.5), None
    if 0.0 in depths and 1.0 in depths:
        a0, a1 = report.accuracy_at_depth(0.0), report.accuracy_at_depth(1.0)
        if a0 is not None and a1 is not None:
            ends = (a0 + a1) / 2
    if mid is not None and ends is not None:
        if mid < ends:
            out.append(f"    => lost-in-the-middle IS visible here: {mid:.0%} vs {ends:.0%} at the edges")
        else:
            out.append(f"    => no middle penalty observed: {mid:.0%} vs {ends:.0%} at the edges")

    big = report.largest_correct
    if big:
        out.append(
            f"\n  largest context where the needle was still found: "
            f"{big.actual_tokens:,} tokens ({big.actual_tokens/SUBQ_CONTEXT_TOKENS:.0%} of the window)"
        )

    if report.input_tokens:
        out.append(
            f"\nsubq usage: {report.input_tokens:,} input tokens = "
            f"${report.input_tokens * 0.04 / 1_000_000:.4f} (output free)"
        )
    for n in report.notes:
        out.append(f"  ! {n}")
    return "\n".join(out)
