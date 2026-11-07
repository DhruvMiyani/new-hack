"""Local driver for CodeGate -- the same engine the agent uses, no network.

    python cli.py review <file.py|->       verify a file (or stdin)
    python cli.py review --pr <url>        verify a GitHub PR
    python cli.py metrics                  accuracy on dev and held-out splits
    python cli.py learn                    one improvement cycle, gated on held-out
    python cli.py rules                    the active ruleset
    python cli.py demo                     buggy file, then its fixed version
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from codegate import learn
from codegate.pipeline import review
from codegate.report import render, render_steps

ROOT = Path(__file__).resolve().parent


def cmd_review(args) -> int:
    if args.pr:
        text = args.pr
    elif args.target == "-":
        text = sys.stdin.read()
    else:
        text = Path(args.target).read_text()

    result = review(text, run_probes=not args.no_probes)
    print(render(result, verbose=args.show_probes))
    print()
    print(render_steps(result))
    return 1 if result.verdict.decision.value == "BLOCK" else 0


def cmd_metrics(_args) -> int:
    rs = learn.latest_promoted()
    print(f"ruleset {rs.version}: {', '.join(rs.enabled)}")
    if rs.probe_config:
        print(f"probe config: {rs.probe_config}")
    print()
    for split in ("dev", "heldout"):
        print(f"{split:8} {learn.evaluate(rs, split).line()}")
    return 0


def cmd_learn(_args) -> int:
    rs = learn.latest_promoted()
    print(f"incumbent: {rs.version}\n")

    ms = learn.misses(rs, "dev")
    if not ms:
        print("No misses on the dev split. Add labelled cases to corpus/ to keep improving.")
        print(f"held-out: {learn.evaluate(rs, 'heldout').line()}")
        return 0

    for cid, missed in ms:
        print(f"  miss: {cid} -> {', '.join(sorted(missed))}")
    print()

    missed_rules = set().union(*[m for _, m in ms])
    candidate = learn.propose_enabling(missed_rules, rs)
    report = learn.consider(candidate, rs)
    print(report.render())
    if report.promoted:
        learn.save_ruleset(candidate)
        print(f"\nwrote rules/{candidate.version}.json")
    else:
        print("\ncandidate discarded; ruleset unchanged")
    return 0


def cmd_rules(_args) -> int:
    from codegate.detectors import REGISTRY

    rs = learn.latest_promoted()
    print(f"ruleset {rs.version} -- {rs.notes}\n")
    for rid, rule in REGISTRY.items():
        state = "on " if rid in rs.enabled_set else "off"
        how = "probe " if rule.probe else "static"
        print(f"  {state} {rid}  {how}  {rule.severity.value:8} {rule.title}")
    print("\nStatic-only rules can never block a merge: they are not reproducible.")
    return 0


def cmd_demo(args) -> int:
    for label, path in (
        ("A pull request full of plausible AI-generated bugs", "corpus/demo_bad.py"),
        ("The same file after the fixes", "corpus/demo_good.py"),
    ):
        print("=" * 78)
        print(label)
        print("=" * 78)
        result = review((ROOT / path).read_text(), run_probes=not args.no_probes)
        print(render(result, verbose=args.show_probes))
        print()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="codegate", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("review", help="verify a file, stdin, or a PR")
    r.add_argument("target", nargs="?", default="-")
    r.add_argument("--pr", help="GitHub PR URL")
    r.add_argument("--no-probes", action="store_true", help="static analysis only")
    r.add_argument("--show-probes", action="store_true", help="include probe source in the report")
    r.set_defaults(fn=cmd_review)

    sub.add_parser("metrics", help="accuracy on dev and held-out").set_defaults(fn=cmd_metrics)
    sub.add_parser("learn", help="one gated improvement cycle").set_defaults(fn=cmd_learn)
    sub.add_parser("rules", help="show the active ruleset").set_defaults(fn=cmd_rules)

    d = sub.add_parser("demo", help="buggy file, then the fixed version")
    d.add_argument("--no-probes", action="store_true")
    d.add_argument("--show-probes", action="store_true")
    d.set_defaults(fn=cmd_demo)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
