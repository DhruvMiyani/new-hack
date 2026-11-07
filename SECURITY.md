# Blindspot: cloud exposure and permission verification

This workflow reproduces three high-impact configuration failures:

1. **Private storage becomes public**, exposing documents and a fictional credential object.
2. **A member gains administrator access**, through self-assigned roles or stale administrator claims.
3. **A staging-only contractor reads production backups**, because an authorization change drops the environment check. The repair must retain legitimate staging access.

It is an executable **local lab**, not a scan of an AWS account or a complete
implementation of any cloud provider's IAM. No real secrets are included and
no cloud resources, accounts, or source configurations are modified.

## Run the demo

From the repository root:

```bash
.venv/bin/python verify.py security --offline
.venv/bin/python verify.py security --offline --repair
.venv/bin/python verify.py security --scenario clean --offline
```

The first command exits **1** after reproducing failures. The second proposes
an explicitly labelled deterministic repair in memory, then reruns the same
56 frozen checks and exits **0** when all pass. The clean control passes without
repair. Invalid inputs or an unavailable/invalid model repair exit **2** for
INCONCLUSIVE. JSON evidence is available with `--json`.

Individual scenarios: `public-storage`, `privilege-escalation`, and
`environment-leak`. The older `revoked-access` CLI scenario remains available. Default: `combined`.

## Models

```bash
.venv/bin/python verify.py security --models --repair
```

Uses the existing `SUBQ_API_KEY` and `META_API_KEY` / `MODEL_API_KEY` environment
settings. No keys are needed for offline mode.

- **SubQ Snap:** reads complete labelled policy, identity, session, storage,
  application, and optional history artifacts; returns a risk-surface choice
  and an exposure probability. These are advisory; confidence cannot overrule
  executed evidence. No claim of a 384K-token benchmark is made by the small demo.
- **Meta:** proposes JSON replacements for candidate configuration. Proposals
  cannot edit identities, requirements, probes, or arbitrary files. The same
  independent checks decide whether the repair passes. No generated code runs.
- **Failure behavior:** failed model calls are disclosed. A missing or malformed
  Meta repair is INCONCLUSIVE; model mode never substitutes an offline repair.

## ASI:One

Start the existing agent with `.venv/bin/python agent.py`, then use its existing
Agentverse profile / ASI:One conversation:

```text
security
security environment-leak offline
security combined repair models
```

The result includes a review card: **Replay checks** and **Verify a repair**.
Both actions execute inside the conversation. Expired cards are rejected.
The agent acknowledges the request before moving work off the event loop.

The local chat handler is tested. Registration/discovery and a live ASI:One
round trip still require the running agent and the user's connected account.

## Assess a local bundle

```bash
.venv/bin/python verify.py security --bundle /absolute/path/to/bundle --offline
```

This reads the supplied configuration **as-is**; it does not inject demo defects.
Use [the example bundle](examples/security/) as the format reference. The
supported contract is intentionally limited to the member/admin role model with production/staging boundaries.
Unknown settings, uncovered buckets, missing artifacts, symlinked inputs,
and oversized bundles are rejected rather than silently approved.

Add optional `.md`, `.txt`, or `.json` records under `evidence/` to supply
architecture or incident history to SubQ in full. Context over the local budget
is rejected, never silently truncated. All required artifacts are mandatory.

## What the gate proves

The oracle is compiled from policy plus authoritative identities and sessions
before candidate configuration is evaluated. It checks anonymous, unknown,
active member, current admin, former employee, demoted admin, and staging-only
contractor access across production and staging resources.
Legitimate member reads and admin operations must continue working, so a
deny-everything repair fails. A privilege-escalation probe performs role
assignment in an isolated copy and then executes an administrator request.

Evidence includes each request, expected/observed permission, decision trace,
candidate fingerprint, frozen-contract fingerprint, proposed replacements, and
the results of rerunning identical probes. A passing result applies only to
this harness's supported local contract, not all possible security properties.

```bash
.venv/bin/python -m pytest tests/test_security.py -q
```
