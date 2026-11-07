"""Export actual local execution evidence for the hosted, explicitly replay-only sandbox."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blindspot.security import SCENARIOS, load_bundle, run_security, scenario_bundle

data = {"mode": "verified-replay", "description": "Recorded results from the Python verifier. Hosted interactions replay these fixtures; run serve.py for fresh local execution.", "scenarios": {}}
base = load_bundle()
for scenario in SCENARIOS:
    data["scenarios"][scenario] = {
        "before": run_security(scenario).to_dict(),
        "repair": run_security(scenario, repair=True).to_dict(),
        "baseline": base, "candidate": scenario_bundle(base, scenario),
    }
target = ROOT / "web" / "dist" / "scenarios.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(data, indent=2) + "\n")
print(f"Exported {len(SCENARIOS)} executed scenarios to {target}")
