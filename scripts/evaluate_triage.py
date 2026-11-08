"""Replay the fictional routing and query expectations against live Snap."""
from pathlib import Path
from collections import Counter
import sys

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from blindspot import referrals, triage


def run(label, data, expected, field):
    result = triage.evaluate(data)
    if not result["available"]:
        raise SystemExit(f"{label}: {result['message']}")
    request = result["request"]
    rows = result["rows"]
    observed = (
        {row["id"] for row in rows if row["matched"]}
        if field == "matched" else {row["id"]: row[field] for row in rows}
    )
    summary = sorted(observed) if field == "matched" else dict(Counter(observed.values()))
    print(
        f"{label}: {request['elapsed_ms']} ms, {request['input_tokens']:,} input tokens, "
        f"{request['output_tokens']} output tokens, {request['questions']} decisions. {summary}"
    )
    if observed != expected:
        if field == "matched":
            raise SystemExit(f"{label} missing {sorted(expected - observed)}; extra {sorted(observed - expected)}")
        diffs = {key: (expected.get(key), observed.get(key)) for key in expected.keys() | observed.keys()
                 if expected.get(key) != observed.get(key)}
        raise SystemExit(f"{label} differed from local source checks: {diffs}")


if __name__ == "__main__":
    routes = {identifier: referrals._verified_route(identifier) for identifier in referrals.CASES}
    orders = {identifier for identifier in routes if referrals._lab_document(referrals.pages(identifier)[-1]) == "order"}
    missing = {identifier for identifier, route in routes.items() if route == "needs_review"}
    run("Full packets", {"scope": "packet"}, routes, "route")
    run("Cover pages", {"scope": "letter"}, dict.fromkeys(routes, "needs_review"), "route")
    run("High + outside area", {"clues": ["High priority outside the scheduling area"]}, {"R102"}, "matched")
    run("Lab order only", {"clues": ["Lab order with no final result"]}, orders, "matched")
    run("Missing signed report", {"clues": ["Missing signed reviewer report"]}, missing, "matched")
    run("Two-clue query", {"clues": ["Signed critical flag", "Eligible for scheduling"]}, {"R101"}, "matched")
