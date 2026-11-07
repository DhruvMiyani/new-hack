# Blindspot

**When you ask a coding agent to write code *and its own tests*, how much safety does that actually buy? We measured it.**

```
3 tasks | 27 proven defects, each with a witnessed input
  caught by the implementer's own tests   26%
  caught by independent spec-only tests   74%
  blind-spot recovery                     13/20 = 65%
```

AIE Code Summit 2026 — problem statement: **Trust but Verify**.

> *"How can a verifier write its own tests without sharing the implementer's blind spots, and stay independent, reproducible and hard to game?"*

That question has an empirical answer, so this project answers it with a number measured from executed code rather than with a design claim.

---

## Provenance — what was built during the hackathon

The hackathon began **10:45 PT on 7 Nov 2026**. Being precise about this because the rules require it:

| | |
|---|---|
| **Built today (hackathon work)** | `blindspot/` — defect injection, witness-based equivalence testing, the information wall, the measurement, the six experiments, both sponsor integrations · `verify.py` · `tests/test_blindspot.py` · `HACKATHON.md` · this README |
| **Built 6 Nov, before the start** | `codegate/` — a reproduce-before-you-block review engine · `prover_agent.py` · the uAgents/ASI:One chat agent · `tests/test_codegate.py` |

The first commit in this repository is that pre-hackathon code, unmodified, labelled as such:

```bash
git log --oneline | tail -1     # eea395e Pre-hackathon baseline (authored 2026-11-06...)
git diff --stat eea395e HEAD    # exactly what was built during the event
```

---

## The idea

The standard workflow is: model writes the code, then model writes tests for the code. The problem is that the tests are authored by something that has already read the implementation — so they inherit whatever the implementation assumed. A test suite written against code you just wrote tends to confirm what you meant, not discover what you missed.

Blindspot tests that claim with a control group:

```
implementer    sees the spec.                      Writes the code.
self-tester    sees the spec AND the code.         ← what "write tests for this" gives you
independent    sees the spec and signature ONLY.   ← cannot inherit an assumption it never saw
adjudicator    sees everything, writes nothing.    ← cannot leak an assumption into a test
```

Then it injects defects with known ground truth and asks which suite catches them.

---

## Why the number is hard to game

Three failure modes would make a measurement like this worthless. Each is closed in code, and each has a test.

**1. A suite could fail on everything.** `assert False` in every test "kills" every mutant. So every suite is first run against the *correct* implementation, and one that fails there is **disqualified, not credited** — it is excluded from scoring entirely. (`test_suite_that_false_alarms_is_disqualified_not_credited`)

**2. A mutant might not be a bug.** The known hole in mutation testing is *equivalent mutants* — changes that alter the text but not the behaviour. Counting those as "missed defects" slanders a suite that was fine. So nothing counts as a defect until we hold a **witness**: a concrete input where the original and the mutant provably disagree, with both outputs recorded.

```
offbyone line 2: integer literal 0 becomes 1
  witness: f(1, [0])  ->  original ok:[0]  |  mutant ok:[]
```

On `page_range` this mattered enormously: **18 mutants reduced to 5 real defects and 13 equivalents.** Without this step the kill rates are simply wrong. (`test_equivalent_mutant_is_not_counted_as_a_defect`)

**3. The adjudicator could smuggle in an assumption.** SubQ Snap was chosen partly *because* it cannot generate text — `output_tokens` is always 0. It answers structured questions and nothing else, so it structurally cannot author a test that encodes implementation knowledge.

---

## Quickstart

Python 3.10+ (set up here on 3.12).

```bash
uv venv --python 3.12 .venv && VIRTUAL_ENV=.venv uv pip install -r requirements.txt
cp .env.example .env            # all keys optional

.venv/bin/python verify.py run --offline      # the core result, no API calls
.venv/bin/python verify.py status             # which providers are configured
.venv/bin/python -m pytest tests/ -q          # 123 tests
```

### The six experiments

Every one is scored against ground truth, because each defect is proven real by
a **witness** — a concrete input where the original and the mutant provably differ.

| command | what it measures | needs |
|---|---|---|
| `run` | self-written vs independent tests — the core result | nothing |
| `matrix` | information independence vs *model* independence, with error bars | Meta |
| `calibrate` | Decision Score `(accuracy − Brier) × 100`, plus self-consistency | SubQ |
| `localize` | which line holds the defect, confidence-gated over 255 options | SubQ |
| `queue` | merge-queue triage against FIFO and random | SubQ |
| `haystack` | one known bug hidden in up to 384K tokens of real code | SubQ |

```bash
.venv/bin/python verify.py matrix --models muse-spark-1.3,muse-wonder-1.0 --samples 3
.venv/bin/python verify.py calibrate --symmetry
.venv/bin/python verify.py localize --repo-scale --multimodal
.venv/bin/python verify.py queue --curate --multimodal-every 3
.venv/bin/python verify.py haystack --sizes 10000,50000,150000,350000
```

**The measurement never depends on a model being reachable.** The numbers come from executed code, so `--offline` produces the same headline result — which also means conference WiFi cannot break the demo.

---

## What one run looks like

```
TASK  allocate    ->  BLOCK

how this ran
  [offline] implement          using the known-correct reference
  [offline] author tests       self-tests: offline stand-in
  [local]   validate suites    2/2 pass cleanly on correct code
  [local]   inject defects     5 single-site mutants
  [local]   prove defects real 5 proven by witness, 0 equivalent, 0 undecided
  [local]   measure            self kills 0%, independent kills 60%

information wall
  implementer-self       valid   saw the implementation
  verifier-independent   valid   spec + signature only

5 proven defect(s) -- each with a witnessed input, not merely a pattern match
  self-written tests    ........................     0%  (0/5 caught)
  independent tests     ##############..........    60%  (3/5 caught)

defects ONLY the independent suite caught
  - comparison at line 7: <= becomes < (drops the boundary case)
      witness : f(1, [1])  ->  original ok:[1]  |  mutant ok:[]
      caught by: test_budget_consumed_exactly, test_request_exactly_equal_to_budget_is_granted

real defects BOTH suites missed (2) -- a shared blind spot
  - dropguard line 2: removed the guard `if budget <= 0` and its early exit
      witness: f(0, [0])  ->  original ok:[]  |  mutant ok:[0]
```

That last block matters: **independence is an improvement, not a cure.** Reporting the shared blind spots is part of the result.

---

## How the sponsors are used

Each product does the job it is uniquely suited to. Remove any one and the project changes shape.

**Meta Model API** (`api.meta.ai/v1`, OpenAI-compatible) — the only component that generates, so it plays all three authoring roles: implementation, self-written tests, and independent spec-only tests. It is the *subject* of the experiment, not a chat wrapper around it.

`verify.py matrix` uses what the API offers beyond chat — four authoring models (`muse-spark-1.1/1.2/1.3`, `muse-wonder-1.0`), the Meta-specific `reasoningEffort`, and per-sample seeds — to separate two mechanisms that are normally conflated:

- **information effect** — same model, implementation withheld
- **model effect** — same information, a *different model from the implementer*

The implementer model is named explicitly, because "a different model" is meaningless otherwise. The proven-defect set is computed once per task and reused across every cell, so cells differ only in the axis under test. Cost is reported per run from published rates (~$0.005 per suite on spark-1.3).

**Subquadratic — SubQ Snap 1.1** (`api.subq.ai/v1/systemone`, Decisions API) — the adjudicator. Not a chat model: it answers typed questions (`noul` / `choice` / `score`) with probabilities and confidence over up to 384K tokens, and emits no text at all.

- *is this suite derived from the spec, or does it only make sense if you read the implementation?*
- *does this reproduced divergence violate the spec, and how severely?*
- *which file in this repo most needs independent verification?* (384K context, whole-repo file selection)

Its own docs name "checking whether a test or result meets specified criteria" as a use case, which is exactly this job. Input-only billing makes whole-repo reads cheap.

Subquadratic is #1 on the [Artificial Analysis Decision Index](https://artificialanalysis.ai/evaluations/artificial-analysis-decision-index), which scores `accuracy − Brier` and aggregates six evaluations — document extraction, workplace judgments, tool calling, hallucination, multiple-choice reasoning, and symmetry. **None of the six is software verification.** So `verify.py calibrate` computes the same formula on that missing domain, against defects we can prove are real, and reports whatever comes out — including a bad result, with a base-rate baseline a constant predictor must lose to.

The other three SubQ experiments each target a capability they advertise:

- **`localize`** — `choice` over 255 candidate lines (their Clue Map weighs 241 countries), with the confidence-gating curve their Sudoku demo implies: accuracy at each threshold against how often it is willing to answer at all.
- **`queue`** — their support-triage showcase, on a merge queue. Same shape as their "7 min vs 12 min", but scored: *verifications performed before the first real defect*, against FIFO and a 20-shuffle random baseline. An ordering worse than doing nothing prints `SLOWER`.
- **`haystack`** — their Moby-Dick demo for code. One proven defect buried in real Python, verified to assemble at **350K tokens (91% of the window)**, swept across context size *and* needle depth to test the lost-in-the-middle failure mode. A trial is refused unless the needle survives into the prompt.

**Fetch.ai / ASI:One** — the workflow runs inside a conversation over the Agent Chat Protocol. Send `blindspot` to the agent and the measurement runs and reports with interactive cards (review card carrying the merge decision, carousel of findings, detail cards with evidence).

```bash
.venv/bin/python agent.py       # then open the printed Agent Inspector link
```

Without a key, every integration degrades to a stated fallback rather than a silent guess — `verify.py status` tells you exactly what is configured.

---

## How it works

```
spec ──> Meta: implementation ──┐
                                ├──> self-tests        (saw the code)
spec + signature only ──────────┴──> independent tests (did not)
                 │
reference impl ──┴──> mutate.py      6 AST operators -> single-site defects
                      equivalence.py differential test -> witness or discard
                      runner.py      isolated child process, per-test outcomes
                      measure.py     validate on correct code, then score
                      roles.py       SubQ adjudicates independence + severity
```

| module | job |
|---|---|
| `mutate.py` | 6 operators: comparison, boolean, arithmetic, off-by-one, drop-guard, swap-args |
| `equivalence.py` | type-aware input synthesis; proves a mutant real by witness or excludes it |
| `runner.py` | executes suites with `-I`, scrubbed env, rlimits, wall-clock kill, network off |
| `measure.py` | baseline validation, kill rates, recovery rate, kill-set overlap, per-operator breakdown |
| `roles.py` | the information wall and all prompts; SubQ question schemas |
| `providers.py` | Meta chat client + SubQ Decisions client, both degrading explicitly |
| `pipeline.py` | the end-to-end run and the pooled aggregate |
| `matrix.py` | the model × information × reasoning grid, with per-cell spread |
| `calibration.py` | Decision Score, calibration buckets, symmetry |
| `localize.py` | confidence-gated line localisation, repo-scale option sets |
| `queue_triage.py` | merge-queue ranking against a written policy |
| `haystack.py` | 384K-token needle assembly with a survival guard |
| `screenshot.py` | renders context as a PNG for the multimodal comparison |

---

## Honest limitations

- **Three tasks, 27 proven defects.** Enough to demonstrate the effect, too few to size it precisely. The pooled number is a finding, not a benchmark.
- **The offline stand-in suites are mine.** With no Meta key, the "self-written" and "independent" suites are hand-written in the style each author produces. I wrote them before measuring, but they are not model output — the honest version of the headline claim requires a live run with a Meta key, and the report always states which mode produced it.
- **Equivalence detection is sound but not complete.** No distinguishing input within the budget means *likely* equivalent, not provably so. A mutant could need an input the synthesiser never tries.
- **Input synthesis is heuristic**, driven by type annotations and parameter names. Unannotated code with unusual parameter names gets a weaker input space.
- **Python only**, single-function tasks. The repo-scale SubQ path is implemented (`pick_risk_surface`) but the measurement itself has not been run across a multi-file repository.
- **The mutants stand in for AI-generated defects.** They are realistic and have ground truth, which real AI defects lack — but they are injected, not naturally occurring.
- **Sandboxing is defence in depth, not a jail.** Child process isolation, rlimits and no network stop accidents; untrusted third-party code belongs in a container.

---

## Repository layout

```
verify.py             the measurement CLI  <- start here
blindspot/            hackathon work (see Provenance)
  mutate.py           defect injection
  equivalence.py      witness-based real-defect proof
  runner.py           isolated suite execution
  measure.py          scoring and the anti-cheat rules
  roles.py            information wall, prompts, SubQ questions
  providers.py        Meta + SubQ clients
  pipeline.py         orchestration
  tasks.py            3 tasks with specs, references, offline stand-ins
  report.py           terminal + markdown rendering
HACKATHON.md          event context, judging-criteria self-assessment, demo script
agent.py              ASI:One agent (pre-existing; `blindspot` command added today)
codegate/             pre-hackathon review engine
tests/                123 tests
```
