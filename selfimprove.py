"""Referral Desk self-improvement loop, gated on held-out referrals.

    python selfimprove.py split      show the frozen dev / held-out split
    python selfimprove.py score      first-pass agreement of the active routing policy
    python selfimprove.py cycle      one round: propose with Muse, gate on held-out
    python selfimprove.py ledger     every version and whether it was promoted

Muse proposes the change, SubQ re-scores, and arithmetic decides. Ground truth
is the deterministic route derived from the fixture pages, which no model sees.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

from blindspot import improve, referrals  # noqa: E402
from blindspot.improve import MuseProposer  # noqa: E402
from blindspot.providers import SubQSnap  # noqa: E402


def cmd_split(_args) -> int:
    split = improve.load_split()
    print(f"seed {split['seed']}, frozen at {improve.SPLIT_PATH.relative_to(ROOT)}")
    print(f"  dev       {len(split['dev']):>3} referrals  {', '.join(split['dev'][:8])} ...")
    print(f"  held-out  {len(split['held_out']):>3} referrals  {', '.join(split['held_out'][:8])} ...")
    print("\nThe proposer never sees held-out referrals, and the split never changes.")
    return 0


def cmd_score(args) -> int:
    if not SubQSnap().available:
        print("Needs SUBQ_API_KEY to classify. Set it in .env.")
        return 2
    split = improve.load_split()
    version = improve.load_version(args.version) if args.version else improve.latest_promoted()
    print(f"routing policy {version.version}" + (f"  ({version.notes})" if version.notes else ""))
    unavailable = False
    for name in ("dev", "held_out"):
        s = improve.score(version, split[name], scope=args.scope)
        unavailable |= bool(s.error)
        print(f"  {name:9} {s.line()}")
        if s.disagreements:
            shown = list(s.disagreements.items())[:5]
            for identifier, (want, got) in shown:
                print(f"      {identifier}: correct {want}, model {got}")
            if len(s.disagreements) > len(shown):
                print(f"      ... {len(s.disagreements) - len(shown)} more")
    return 2 if unavailable else 0


def cmd_cycle(args) -> int:
    if not SubQSnap().available:
        print("Needs SUBQ_API_KEY to classify. Set it in .env.")
        return 2
    muse = MuseProposer()
    if not muse.available:
        print("Configure the Meta Model API credentials in your local .env.")
        return 2

    incumbent = None
    if args.from_ablation:
        if args.scope != "packet":
            print("The cover-only fault experiment needs full packets.")
            return 2
        incumbent = improve.ablate(improve.latest_promoted())
        incumbent.save()
        print(f"starting from {incumbent.version}")
        print(f"  {incumbent.notes}\n")

    result = improve.cycle(muse, scope=args.scope, incumbent=incumbent)
    if not result["ok"]:
        print(result["message"])
        if "dev" in result:
            print(f"  dev: {result['dev'].line()}")
        return 1

    print(f"incumbent {result['incumbent']}")
    print(f"  dev: {result['dev'].line()}")
    if "decision" not in result:
        print(f"  {result['message']}")
        print(f"  held-out: {result['held_out'].line()}")
        return 0

    print()
    print(result["decision"].render())
    candidate = result["decision"].candidate
    print(f"\nsaved {candidate.path().relative_to(ROOT)}")
    if candidate.experiment:
        print("This experiment is isolated from the active inbox policy.")
    print(f"\nmeta: {muse.line()}")
    return 0


def cmd_ledger(_args) -> int:
    versions = improve.all_versions()
    print(f"{'version':12} {'parent':12} {'state':10} notes")
    for v in versions:
        state = "promoted" if v.promoted else "rejected" if v.gate else "trial"
        if v.experiment:
            state += " (experiment)"
        print(f"{v.version:12} {(v.parent or '-'):12} {state:10} {v.notes[:60]}")
    print("\nA rejected version stays on disk. The record of what did not work is")
    print("the part that makes the promoted ones believable.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="selfimprove", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("split", help="show the frozen dev / held-out split").set_defaults(fn=cmd_split)

    s = sub.add_parser("score", help="first-pass agreement against ground truth")
    s.add_argument("--version", help="routing policy version (default: latest active promotion)")
    s.add_argument("--scope", default="packet", choices=["packet", "letter"])
    s.set_defaults(fn=cmd_score)

    c = sub.add_parser("cycle", help="one improvement round, gated on held-out")
    c.add_argument("--scope", default="packet", choices=["packet", "letter"])
    c.add_argument("--from-ablation", action="store_true",
                   help="try a deliberately faulty cover-only policy in an isolated experiment")
    c.set_defaults(fn=cmd_cycle)

    sub.add_parser("ledger", help="every version and whether it was promoted").set_defaults(fn=cmd_ledger)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
