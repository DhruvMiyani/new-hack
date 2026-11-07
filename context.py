"""Inspect full repository context and evaluate an owned multi-file migration."""
import argparse
import json
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

from blindspot.repository import DEFAULT_TASK, RepositoryError, curate, load_demo, load_github, load_local
from blindspot.context_benchmark import benchmark


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--github", help="Public https://github.com/owner/repo URL")
    source.add_argument("--local", help="Local Git checkout root; reads committed revision only")
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--task", help="Required for GitHub/local sources; Atlas uses its migration task")
    parser.add_argument("--method", choices=("subq", "keyword"), default="subq")
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--generator", choices=("reference", "meta"), default="reference")
    args = parser.parse_args()
    try:
        if (args.github or args.local) and not args.task:
            raise RepositoryError("Supply --task for a real repository.")
        snapshot = load_github(args.github, args.ref) if args.github else load_local(args.local, args.ref) if args.local else load_demo()
        selection = curate(snapshot, args.task or DEFAULT_TASK, method=args.method)
        result = {"snapshot": {k: v for k, v in snapshot.public().items() if k != "files"}, "curation": selection}
        if args.benchmark:
            result["benchmark"] = benchmark(snapshot, selection, generator=args.generator)
        print(json.dumps(result, indent=2))
        return 0
    except RepositoryError as exc:
        print(json.dumps({"error": str(exc), "verdict": "INCONCLUSIVE"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
