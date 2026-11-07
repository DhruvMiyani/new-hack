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

    sub.add_parser("status", help="show provider configuration").set_defaults(fn=cmd_status)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
