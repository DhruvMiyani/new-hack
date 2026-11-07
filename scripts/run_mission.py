"""Replay the mission's independent checks, optionally with live SubQ decisions."""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
from blindspot.mission import check_mission, investigate, mission_state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Call SubQ on the candidate and minimal repair")
    parser.add_argument("--compare-context", action="store_true", help="Also ask SubQ with only the changed-file clue")
    args = parser.parse_args()
    mission = mission_state()
    report = {"recorded_at": datetime.now(timezone.utc).isoformat(),
              "scope": mission["scope"], "provenance": mission["provenance"],
              "context": mission["context"], "contract_hash": mission["contract_hash"],
              "checks": {repair: check_mission({"hypothesis": "prod-public", "repair": repair})
                         for repair in ("candidate", "deny-all", "private-ui", "wrong-bucket", "revoke-public")}}
    if args.live:
        report["subq"] = {
            "full_candidate": investigate({"context_mode": "full", "repair": "candidate"}),
            "full_repaired": investigate({"context_mode": "full", "repair": "revoke-public"}),
        }
        if args.compare_context:
            report["subq"]["changed_file_only"] = investigate({"context_mode": "selected", "clues": ["A01"]})
    print(json.dumps(report, indent=2))
    return 0 if report["checks"]["revoke-public"]["won"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
