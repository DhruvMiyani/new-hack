"""Blindspot -- measure whether an independent verifier catches what the
implementer's own tests miss.

    python verify.py run                 all tasks (uses models if keys are set)
    python verify.py run --task allocate one task
    python verify.py run --offline       no API calls at all
    python verify.py status              which providers are configured
"""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

load_dotenv()

from blindspot import pipeline            # noqa: E402  (after load_dotenv)
from blindspot.providers import MetaMuse, SubQSnap, status  # noqa: E402
from blindspot.calibration import calibrate, render_calibration  # noqa: E402
from blindspot.matrix import render_matrix, run_matrix     # noqa: E402
from blindspot.providers import AUTHOR_MODELS              # noqa: E402
from blindspot.report import render_aggregate, render_run   # noqa: E402
from blindspot.tasks import TASKS         # noqa: E402


def cmd_status(_args) -> int:
    for name, state in status().items():
        print(f"{name:6} {state}")
    print()
    print("Meta writes code and tests. SubQ adjudicates -- it generates no text,")
    print("so it cannot smuggle an implementation assumption into a test.")
    print()
    print("The measurement runs from executed code, so it works with neither key set.")
    return 0


def cmd_run(args) -> int:
    if args.task:
        if args.task not in TASKS:
            print(f"unknown task {args.task!r}; have: {', '.join(TASKS)}", file=sys.stderr)
            return 2
        chosen = [TASKS[args.task]]
    else:
        chosen = list(TASKS.values())

    use_models = not args.offline
    if use_models and not MetaMuse().available:
        print("note: no Meta key found, so the offline stand-in suites are used.\n")

    reports = []
    for task in chosen:
        report = pipeline.run(
            task,
            use_models=use_models,
            trials=args.trials,
            mutant_limit=args.limit,
        )
        reports.append(report)
        print(render_run(report, verbose=args.verbose))
        print()

    if len(reports) > 1:
        print(render_aggregate(reports))

    muse, snap = MetaMuse(), SubQSnap()
    if muse.usage.calls or snap.usage.calls:
        print(
            f"\napi usage: meta {muse.usage.calls} call(s), "
            f"{muse.usage.input_tokens}in/{muse.usage.output_tokens}out | "
            f"subq {snap.usage.calls} call(s), {snap.usage.input_tokens}in "
            f"(output always free)"
        )

    return 1 if any(r.decision == "BLOCK" for r in reports) else 0


def cmd_matrix(args) -> int:
    """Separate information independence from model independence, with error bars."""
    muse = MetaMuse()
    if not muse.available:
        print("The matrix needs a Meta key: every cell is a freshly generated suite.")
        print("Set META_API_KEY (or MODEL_API_KEY) in .env, then re-run.")
        print("\nWithout a key, `verify.py run --offline` still gives the single-pair result.")
        return 2

    models = args.models.split(",") if args.models else [AUTHOR_MODELS[0]]
    reasoning = args.reasoning.split(",") if args.reasoning else [None]
    reasoning = [None if r in ("", "none") else r for r in reasoning]

    tasks = [TASKS[args.task]] if args.task in TASKS else list(TASKS.values())
    cells = len(tasks) * len(models) * 2 * len(reasoning) * args.samples
    print(f"grid: {len(tasks)} task(s) x {len(models)} model(s) x 2 conditions "
          f"x {len(reasoning)} reasoning level(s) x {args.samples} sample(s) = {cells} suites")
    print(f"models: {', '.join(models)}")
    print("estimated cost: well under $1 at published rates; exact spend reported below.\n")

    results = []
    for task in tasks:
        res = run_matrix(
            task,
            models=models,
            samples=args.samples,
            reasoning_levels=reasoning,
            trials=args.trials,
            muse=muse,
        )
        results.append(res)
        print(render_matrix(res))
        print()

    print(f"meta api: {muse.usage.line()}")
    return 0


def cmd_calibrate(args) -> int:
    """Score SubQ's predictions about test-suite gaps against executed truth."""
    from blindspot.equivalence import partition
    from blindspot.measure import Suite, validate
    from blindspot.mutate import generate
    from blindspot.roles import write_independent_tests, write_self_tests
    from blindspot.runner import extract_code
    from blindspot.tasks import OFFLINE_INDEPENDENT_TESTS, OFFLINE_SELF_TESTS

    snap = SubQSnap()
    if not snap.available:
        print("This needs a SubQ key: it scores SubQ's own predictions.")
        print("Get 100M free tokens at console.subq.ai, set SUBQ_API_KEY in .env, re-run.")
        return 2

    muse = MetaMuse()
    tasks = [TASKS[args.task]] if args.task in TASKS else list(TASKS.values())

    for task in tasks:
        baseline = task.reference
        impl = baseline

        # Suites come from Meta when available, otherwise the stand-ins. Either
        # way they are only the *subject* here -- what is being scored is SubQ.
        if muse.available and not args.offline_suites:
            self_src = write_self_tests(muse, task, impl) or OFFLINE_SELF_TESTS[task.name]
            ind_src = write_independent_tests(muse, task) or OFFLINE_INDEPENDENT_TESTS[task.name]
            author = muse.model
        else:
            self_src = OFFLINE_SELF_TESTS[task.name]
            ind_src = OFFLINE_INDEPENDENT_TESTS[task.name]
            author = "offline stand-in"

        suites = [
            Suite("saw-code", author, self_src, saw_implementation=True),
            Suite("spec-only", author, ind_src, saw_implementation=False),
        ]
        for sui in suites:
            validate(sui, baseline)

        real, equivalent, table = partition(baseline, generate(baseline), task.target, args.trials)
        if not real:
            print(f"{task.name}: no provable defect, skipping")
            continue

        print(f"{task.name}: {len(real)} proven defect(s) x {len([s for s in suites if s.valid])} "
              f"suite(s) = {len(real) * len([s for s in suites if s.valid])} predictions to score\n")
        report = calibrate(task, suites, real, snap=snap, classifications=table)
        print(render_calibration(report))
        print()

    print(f"subq usage: {snap.usage.input_tokens} input tokens = "
          f"${snap.usage.input_tokens * 0.04 / 1_000_000:.4f} (output free)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="verify", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the blind-spot measurement")
    r.add_argument("--task", help=f"one of: {', '.join(TASKS)}")
    r.add_argument("--offline", action="store_true", help="no API calls; use stand-in suites")
    r.add_argument("--trials", type=int, default=400, help="differential-test inputs per mutant")
    r.add_argument("--limit", type=int, default=None, help="cap mutants per task")
    r.add_argument("--verbose", action="store_true", help="include equivalence reasoning")
    r.set_defaults(fn=cmd_run)

    m = sub.add_parser("matrix", help="information vs model independence, with error bars")
    m.add_argument("--task", help=f"one of: {', '.join(TASKS)}")
    m.add_argument("--models", help=f"comma-separated; available: {','.join(AUTHOR_MODELS)}")
    m.add_argument("--samples", type=int, default=3, help="suites per cell (default 3)")
    m.add_argument("--reasoning", help="comma-separated reasoningEffort levels, e.g. low,high")
    m.add_argument("--trials", type=int, default=400)
    m.set_defaults(fn=cmd_matrix)

    cal = sub.add_parser(
        "calibrate", help="can SubQ predict test-suite gaps without running them?"
    )
    cal.add_argument("--task", help=f"one of: {', '.join(TASKS)}")
    cal.add_argument("--trials", type=int, default=400)
    cal.add_argument("--offline-suites", action="store_true",
                     help="use the stand-in suites even if a Meta key is set")
    cal.set_defaults(fn=cmd_calibrate)

    sub.add_parser("status", help="show provider configuration").set_defaults(fn=cmd_status)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
