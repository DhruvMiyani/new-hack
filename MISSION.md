# Stop the Leak — mission 01

An interactive cloud-security verification challenge powered by **SubQ Snap 1.1**.
The player inspects an authored permission change, predicts the exposure, runs
independent access checks, and selects a prepared repair. Winning requires both
private-data protection and preserved legitimate access.

## Run locally

```bash
.venv/bin/python serve.py --port 8765
```

Open [the mission](http://localhost:8765/). It uses fictional data and a local
object-storage permission model. It does not connect to GCP, change cloud IAM,
execute generated code, or read real customer files. A GCP account is not needed.

Set `SUBQ_API_KEY` and `SUBQ_BASE_URL` in the local `.env` and restart the
server to enable live investigation. `SUBQ_MODEL=subq-snap-1.1` is the default. Keep the key on the
server; it must never appear in a browser or submitted evidence. No Meta key or
other generative model is used by this mission.

## The one-minute demo

1. **Inspect:** a fictional SaaS company enabled public previews. Explore the
   cloud map and inspect its deployment, storage, application, and policy clues.
2. **Predict:** choose the resource/access path you suspect is exposed.
3. **Investigate:** ask SubQ to weigh the hypotheses and select relevant evidence.
   Its probabilities are advisory predictions, shown separately from test results.
4. **Reproduce:** execute the local access checks and inspect the unexpected read.
5. **Repair:** choose the minimal permission repair and rerun the same contract.
   Try deny-everything to demonstrate why blocked legitimate traffic cannot win.

The permission change and repair options are authored fixture content, not
AI-generated work. The verifier's expected outcomes come from a frozen declared
contract, independent of the candidate settings and model answers. The same
contract fingerprint is used before and after repair. A model cannot rewrite it.

## What SubQ does

The complete eligible scenario artifacts are supplied as labelled context.
Snap returns typed decisions: hypothesis probabilities, evidence relevance,
the next useful check, and a choice among prepared repairs. The player can
compare full context with selected clues. Token use and latency describe the
actual request, not a claim about full production repositories or the model's
maximum context window.

Missing credentials, failed calls, or incomplete responses are shown as
unavailable. The application does not fabricate probabilities or silently
substitute deterministic output as AI output. Independent local checks remain
available regardless of model connectivity.

Official references: [Quickstart](https://docs.subq.ai/),
[Decisions API](https://docs.subq.ai/endpoints/decisions-api/), and
[model availability](https://docs.subq.ai/using-the-api/models/).

## Scope and submission provenance

This mission demonstrates one supported local object-storage contract. A passing
result does not establish the safety of arbitrary cloud IAM policies. Public
assets and application reads are positive controls: keeping private data safe
while breaking the application does not count as a successful repair.

The existing verifier and prior experiments remain separately documented. In a
submission, identify the mission UI, scenario logic, model integration, and tests
created during the event, and distinguish any pre-event engine code. Use the
repository history to substantiate that boundary.

```bash
.venv/bin/python -m pytest tests/test_mission.py -q
```

## Recorded live evidence

[Recorded run](docs/mission-live.json), 7 November 2026, one owned fixture:

- Full candidate context: production exposure hypothesis 99.89%; 3,124 input tokens, 404 ms.
- Full repaired context: no-exposure hypothesis 99.37%; 3,233 input tokens, 419 ms.
- Changed-file clue alone: insufficient evidence 85.59%; 742 input tokens, 335 ms.
- Independent checks: candidate 17/21; targeted repair 21/21; deny-all 9/21.

These are single-run model predictions, not measured real-world security accuracy.
The fixture contains 12 meaningful authored files (5,188 source bytes), not a
384K-token production repository. The UI always makes fresh calls; this evidence
file is not used to replace failed model requests.

Reproduce local checks without model calls:

```bash
.venv/bin/python scripts/run_mission.py
# Live SubQ comparison, with SUBQ_API_KEY configured locally:
.venv/bin/python scripts/run_mission.py --live --compare-context
```
