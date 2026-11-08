"""Replay the fictional routing and query expectations against live Snap."""
from pathlib import Path
import sys

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from blindspot import triage


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
    print(
        f"{label}: {request['elapsed_ms']} ms, {request['input_tokens']:,} input tokens, "
        f"{request['output_tokens']} output tokens, {request['questions']} decisions. {observed}"
    )
    if observed != expected:
        raise SystemExit(f"{label} differed from the expected synthetic outcome: {expected}")


if __name__ == "__main__":
    routes = {"R101": "critical", "R102": "high", "R103": "medium", "R104": "low", "R105": "needs_review"}
    run("Full packets", {"scope": "packet"}, routes, "route")
    run("Cover pages", {"scope": "letter"}, dict.fromkeys(routes, "needs_review"), "route")
    run("High + outside area", {"clues": ["High priority outside the scheduling area"]}, {"R102"}, "matched")
    run("Lab order only", {"clues": ["Lab order with no final result"]}, {"R102", "R103", "R105"}, "matched")
    run("Missing signed report", {"clues": ["Missing signed reviewer report"]}, {"R105"}, "matched")
    run("Two-clue query", {"clues": ["Signed critical flag", "Eligible for scheduling"]}, {"R101"}, "matched")
