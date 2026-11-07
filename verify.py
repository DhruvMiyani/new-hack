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
from blindspot.haystack import DEFAULT_DEPTHS, DEFAULT_SIZES, render_haystack, run_haystack  # noqa: E402
from blindspot.localize import localise, render_localisation  # noqa: E402
from blindspot.matrix import render_matrix, run_matrix     # noqa: E402
from blindspot.queue_triage import build_queue, render_triage, triage  # noqa: E402
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
    if args.cloud:
        from blindspot.cloud import (
            CLOUD_TASKS,
            OFFLINE_INDEPENDENT_TESTS as CLOUD_IND,
            OFFLINE_SELF_TESTS as CLOUD_SELF,
        )

        bank = (CLOUD_SELF, CLOUD_IND)
        pool = CLOUD_TASKS
    else:
        bank = (None, None)
        pool = TASKS

    if args.task:
        if args.task not in pool:
            print(f"unknown task {args.task!r}; have: {', '.join(pool)}", file=sys.stderr)
            return 2
        chosen = [pool[args.task]]
    else:
        chosen = list(pool.values())

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
            self_tests=bank[0],
            independent_tests=bank[1],
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
        report = calibrate(
            task, suites, real, snap=snap, classifications=table, symmetry=args.symmetry
        )
        print(render_calibration(report))
        print()

    print(f"subq usage: {snap.usage.input_tokens} input tokens = "
          f"${snap.usage.input_tokens * 0.04 / 1_000_000:.4f} (output free)")
    return 0


def cmd_localize(args) -> int:
    """Point at the guilty line over hundreds of candidates, only when sure."""
    from blindspot.equivalence import partition
    from blindspot.measure import Suite, validate
    from blindspot.mutate import generate
    from blindspot.runner import run_suite
    from blindspot.screenshot import context_image
    from blindspot.tasks import OFFLINE_INDEPENDENT_TESTS

    snap = SubQSnap()
    if not snap.available:
        print("This needs a SubQ key. Get 100M free tokens at console.subq.ai,")
        print("set SUBQ_API_KEY in .env, then re-run.")
        return 2

    tasks = [TASKS[args.task]] if args.task in TASKS else list(TASKS.values())

    for task in tasks:
        baseline = task.reference
        suite = Suite("spec-only", "offline", OFFLINE_INDEPENDENT_TESTS[task.name])
        validate(suite, baseline)
        if not suite.valid:
            print(f"{task.name}: suite invalid on correct code, skipping")
            continue

        real, _, _ = partition(baseline, generate(baseline), task.target, args.trials)

        # A localisation needs something to localise FROM: a test that actually
        # fails, plus its real output. Defects no suite catches are skipped --
        # inventing a failure would make the measurement meaningless.
        failing: dict[str, tuple[str, str]] = {}
        images: dict[str, str] = {}
        for m in real:
            res = run_suite(m.source, suite.source)
            if not res.detects:
                continue
            f = res.failures[0]
            output = f"{f.name} failed: {f.kind}: {f.error}"
            failing[m.mutant_id] = (suite.source, output)
            if args.multimodal:
                images[m.mutant_id] = context_image(m.source, suite.source, output)

        if not failing:
            print(f"{task.name}: no defect produced a failing test, skipping")
            continue

        distractors = None
        if args.repo_scale:
            import pathlib

            root = pathlib.Path("codegate")
            distractors = [
                (p.name, p.read_text())
                for p in sorted(root.glob("*.py"))
                if p.name != "__init__.py"
            ]

        print(f"{task.name}: localising {len(failing)} defect(s)"
              + (" (text and screenshot)" if args.multimodal else "")
              + (f" against lines pooled from {len(distractors)} repo file(s)"
                 if distractors else ""))
        report = localise(
            task, real, failing, snap=snap, images=images or None, distractors=distractors
        )
        print(render_localisation(report))
        print()

    print(f"subq usage: {snap.usage.input_tokens} input tokens = "
          f"${snap.usage.input_tokens * 0.04 / 1_000_000:.4f} (output free)")
    return 0


def cmd_queue(args) -> int:
    """Triage a merge queue: context curation + multimodal + ranking, scored."""
    import pathlib

    from blindspot.equivalence import partition
    from blindspot.mutate import generate

    snap = SubQSnap()
    if not snap.available:
        print("This needs a SubQ key. Get 100M free tokens at console.subq.ai,")
        print("set SUBQ_API_KEY in .env, then re-run.")
        return 2

    tasks = [TASKS[args.task]] if args.task in TASKS else list(TASKS.values())

    pairs = []
    for task in tasks:
        real, _, table = partition(
            task.reference, generate(task.reference), task.target, args.trials
        )
        if args.max_per_task:
            real = real[: args.max_per_task]
        pairs.append((task, real, table, task.reference))

    items = build_queue(
        pairs, clean_per_task=args.clean, multimodal_every=args.multimodal_every
    )
    print(f"queue: {len(items)} change(s), {sum(1 for i in items if i.is_defective)} defective "
          f"(ground truth, each with a witness)")

    # Context curation: offer the real files of this repo as the risk surface,
    # the way the "which files should change?" demo sweeps a whole project.
    file_options = None
    if args.curate:
        files = sorted(pathlib.Path("codegate").glob("*.py"))
        file_options = {
            p.name: (p.read_text().strip().splitlines() or [""])[0][:160]
            for p in files
            if p.name != "__init__.py"
        }
        print(f"context curation: choosing a risk surface among {len(file_options)} repo file(s)")
    if args.multimodal_every:
        print(f"multimodal: every {args.multimodal_every}th change arrives as a screenshot")
    print()

    report = triage(items, snap=snap, file_options=file_options)
    print(render_triage(report))
    print(f"\nsubq usage: {snap.usage.input_tokens} input tokens = "
          f"${snap.usage.input_tokens * 0.04 / 1_000_000:.4f} (output free)")
    return 0


def cmd_haystack(args) -> int:
    """Hide one known bug in up to 384K tokens of real code and ask for the line."""
    from blindspot.equivalence import partition
    from blindspot.mutate import generate

    snap = SubQSnap()
    if not snap.available:
        print("This needs a SubQ key. Get 100M free tokens at console.subq.ai,")
        print("set SUBQ_API_KEY in .env, then re-run.")
        return 2

    task = TASKS[args.task] if args.task in TASKS else TASKS["allocate"]
    real, _, table = partition(
        task.reference, generate(task.reference), task.target, args.trials
    )
    if not real:
        print(f"{task.name}: no provable defect to hide")
        return 1

    # Use a defect we have proved real, so a miss is a retrieval failure and
    # never an argument about whether the needle was a bug at all.
    mutant = real[0]
    eq = table.get(mutant.mutant_id)
    print(f"needle: {mutant.description}")
    if eq and eq.witness:
        print(f"proved real by witness: {eq.witness.render()}")

    sizes = tuple(int(x) for x in args.sizes.split(",")) if args.sizes else DEFAULT_SIZES
    depths = tuple(float(x) for x in args.depths.split(",")) if args.depths else DEFAULT_DEPTHS
    est = sum(sizes) * len(depths) * 0.04 / 1_000_000
    print(f"{len(sizes) * len(depths)} trial(s); estimated cost ${est:.3f}\n")

    report = run_haystack(task, mutant, sizes=sizes, depths=depths, snap=snap)
    print(render_haystack(report))
    return 0


def cmd_security(args) -> int:
    """Reproduce storage exposure and permission regressions without cloud writes."""
    import json
    from pathlib import Path
    from blindspot.security import BundleError, DEFAULT_BUNDLE, render_security, run_security

    try:
        report = run_security(
            args.scenario, root=Path(args.bundle) if args.bundle else DEFAULT_BUNDLE,
            repair=args.repair, use_models=args.models,
            use_bundle_as_is=bool(args.bundle),
        )
    except (BundleError, OSError, ValueError, TypeError, KeyError) as exc:
        if args.json:
            print(json.dumps({"final_verdict": "INCONCLUSIVE", "error": str(exc)}))
        else:
            print(f"BLINDSPOT SECURITY — INCONCLUSIVE\n{exc}", file=sys.stderr)
        return 2
    print(json.dumps(report.to_dict(), indent=2) if args.json else render_security(report))
    return {"PASS": 0, "BLOCK": 1, "INCONCLUSIVE": 2}[report.final_verdict]


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
    r.add_argument("--cloud", action="store_true",
                   help="run the cloud exposure tasks (security group, bucket policy, IAM role) "
                        "instead of the code tasks")
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
    cal.add_argument("--symmetry", action="store_true",
                     help="also ask each question inverted, to measure self-consistency "
                          "(doubles the call count; needs no ground truth)")
    cal.set_defaults(fn=cmd_calibrate)

    loc = sub.add_parser(
        "localize", help="which line holds the defect? confidence-gated, over the whole file"
    )
    loc.add_argument("--task", help=f"one of: {', '.join(TASKS)}")
    loc.add_argument("--trials", type=int, default=400)
    loc.add_argument("--repo-scale", action="store_true",
                     help="pool candidate lines from the whole repo, up to the 255-option "
                          "ceiling, so the random baseline is near zero")
    loc.add_argument("--multimodal", action="store_true",
                     help="also ask with the context as a screenshot, to compare modalities")
    loc.set_defaults(fn=cmd_localize)

    q = sub.add_parser(
        "queue", help="triage a merge queue -- curation + multimodal + ranking, all scored"
    )
    q.add_argument("--task", help=f"one of: {', '.join(TASKS)}")
    q.add_argument("--trials", type=int, default=400)
    q.add_argument("--clean", type=int, default=2, help="clean changes per task (default 2)")
    q.add_argument("--max-per-task", type=int, default=4, help="cap defective changes per task")
    q.add_argument("--curate", action="store_true",
                   help="also ask which repo file each change puts at risk")
    q.add_argument("--multimodal-every", type=int, default=0,
                   help="send every Nth change as a screenshot instead of text")
    q.set_defaults(fn=cmd_queue)

    hay = sub.add_parser(
        "haystack", help="one known bug hidden in up to 384K tokens of real code"
    )
    hay.add_argument("--task", help=f"one of: {', '.join(TASKS)}")
    hay.add_argument("--sizes", help="comma-separated context sizes in tokens")
    hay.add_argument("--depths", help="comma-separated needle depths 0.0-1.0")
    hay.add_argument("--trials", type=int, default=400)
    hay.set_defaults(fn=cmd_haystack)

    sec = sub.add_parser("security", help="verify private storage, admin permissions, and revoked access")
    from blindspot.security import SCENARIOS
    sec.add_argument("--scenario", choices=SCENARIOS, default="combined")
    sec.add_argument("--bundle", help="assess this local bundle as-is instead of injecting a demo defect")
    sec.add_argument("--repair", action="store_true", help="propose and verify a repair in memory")
    modes = sec.add_mutually_exclusive_group()
    modes.add_argument("--models", action="store_true", help="use SubQ decisions and Meta repair generation")
    modes.add_argument("--offline", action="store_true", help="no API calls (default)")
    sec.add_argument("--json", action="store_true", help="emit machine-readable evidence and gate decision")
    sec.set_defaults(fn=cmd_security)

    sub.add_parser("status", help="show provider configuration").set_defaults(fn=cmd_status)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
