# CodeGate

**A verification agent that reproduces a defect before it blocks your merge — and improves its own ruleset only when the improvement survives a held-out test.**

Built for the AIE Code Summit 2026 Fetch.ai track. Discoverable through ASI:One, speaking the Agent Chat Protocol, with the whole workflow running inside the conversation.

---

## The problem

AI writes code faster than teams can decide whether it is safe to merge. Review agents answer that by producing *more opinions* — a model reads a diff and asserts that line 47 looks wrong. You still have to check it yourself, because the agent's confidence carries no information about whether it is right.

CodeGate takes the opposite position:

> **An agent that cannot reproduce a defect has not found one.**

Every blocking finding is *executed*. The report contains the probe that ran and the output it actually produced. Pattern matches that could not be reproduced are labelled unproven and are structurally incapable of blocking a merge — not by convention, but because `Finding.blocking` returns `False` unless `status is OBSERVED`.

That is the whole design. Everything below follows from it.

---

## What it looks like

```
$ python cli.py review corpus/demo_bad.py

## BLOCK — pasted snippet

Blocked: 6 defect(s) at or above the blocking bar were reproduced by executing
them (2 critical, 4 high). Each one below carries its probe and real output.

7 reproduced · 0 unproven · 0 refuted | 7 probe(s) executed | ruleset v3
```

and for one of those seven:

```
**1. SQL query built by string interpolation**
`CG006` · critical · `submitted.py:19`

    cur.execute(f"SELECT id, email FROM users WHERE name = '{username}'")

**Reproduced.** payload was inlined into the SQL text instead of being bound as a parameter

What the probe actually printed:

    SQL statements the function handed to the driver:
      sql    = "SELECT id, email FROM users WHERE name = 'bob' OR '1'='1'"
      params = None

_evidence 6cb2249662532a00 · 30ms_
```

That `sql = ...` line is not a description of what the agent thinks would happen. It is what the function handed to a fake database driver when the agent called it with `bob' OR '1'='1`. Nothing was actually executed against a database, and no shell command was run — the probes intercept the dangerous call and show you what reached it.

Run the fixed version of the same file and you get `ALLOW` with zero findings.

---

## Quickstart

Requires Python 3.10+ (the repo is set up on 3.12).

```bash
git clone <your-fork-url> && cd codegate
uv venv --python 3.12 .venv && VIRTUAL_ENV=.venv uv pip install -r requirements.txt
# or: python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt

cp .env.example .env     # then edit: add your ASI:One key and a unique AGENT_SEED
```

Everything works locally with no agent and no API key:

```bash
.venv/bin/python cli.py demo        # a buggy file, then the fixed version
.venv/bin/python cli.py metrics     # accuracy on dev and held-out splits
.venv/bin/python cli.py learn       # one gated improvement cycle
.venv/bin/python cli.py rules       # the active ruleset
.venv/bin/python cli.py review path/to/file.py
.venv/bin/python cli.py review --pr https://github.com/owner/repo/pull/123
.venv/bin/python -m pytest tests/test_codegate.py -q
```

To confirm the two-agent path really works (rather than silently falling back to
local execution), start the prover and run the live check:

```bash
.venv/bin/python prover_agent.py                  # terminal 1
.venv/bin/python -u tests/integration_prover.py   # terminal 2
```

```
[ok]   health: codegate-prover -- prover ready
[ok]   probe executed remotely in 38ms
       reproduced: True
       detail    : shared default mutated: ([],) -> ([[1, 2, 3], [1, 2, 3]],)
INTEGRATION PASSED -- the gate's probe path works over agent messaging
```

### Running it as an agent

```bash
# terminal 1 — the prover (executes probes, holds no opinions)
.venv/bin/python prover_agent.py
# copy the printed address into PROVER_ADDRESS in .env

# terminal 2 — the gate (what ASI:One talks to)
.venv/bin/python agent.py
```

On startup the gate prints an **Agent Inspector** link. Open it, click **Connect → Mailbox** to claim the agent on your Agentverse account, then find it from [asi1.ai](https://asi1.ai) with Agents enabled.

**Probe execution is off by default.** See [Security posture](#security-posture) before you turn it on.

---

## Try it inside ASI:One

| Say this | What happens |
|---|---|
| paste a Python function | full review, merge-gate card, findings carousel |
| `https://github.com/owner/repo/pull/123` | fetches the PR's changed `.py` files at the head SHA and reviews them |
| paste a `git diff` | reconstructs the post-image; fragments are marked static-only |
| `metrics` | accuracy on a held-out split the agent never tunes against |
| `learn` | finds a miss, proposes a fix, promotes it only if held-out numbers improve |
| `rules` | the active ruleset and which rules are allowed to block |

The cards are real [ASI:One Interactive Cards](https://uagents.fetch.ai), built with `uagents_core.contrib.protocols.chat.cards`:

- a **review card** — the merge gate itself, with *Override and merge* / *Uphold block*
- a **carousel** — one tile per finding, badged `reproduced` / `unproven` / `cleared`
- a **detail card** — one defect's evidence, with a button to print the probe source so you can run it yourself
- a **review card** for the learning cycle — held-out precision/recall before and after, with *Keep* / *Roll back*

---

## Agents that get better — provably

The weak version of "self-improving" is an agent that edits its own prompt and declares victory. The claim is unfalsifiable because nothing was measured on work the change had not already seen.

CodeGate splits a labelled corpus into **dev** (10 cases, what it may learn from) and **held-out** (12 cases, never consulted when writing or proposing a rule). A change ships only if it beats the incumbent on held-out. The proposer may be a model or a human; **the gate is arithmetic**:

```python
if after.false_positive_cases > before.false_positive_cases:  reject
elif after.precision < before.precision:                      reject
elif after.recall <= before.recall:                           reject
else:                                                         promote
```

Two cycles have run, and both are reproducible with `python cli.py learn`:

| version | change | held-out recall | precision | clean files wrongly flagged |
|---|---|---|---|---|
| `v1` | hand-written starting ruleset | 0.62 | 1.00 | 0 |
| `v2` | enable `CG003`, `CG009` after dev-split misses | **0.88** | 1.00 | 0 |
| `v3` | fix the probe driver for `CG001` | **1.00** | 1.00 | 0 |

`v3` is the interesting one, because it is the agent improving **its own tooling rather than its rule list**. `CG001` (mutable default argument) was matching statically but being *refuted* on `def tally(word, counts={})`. The probe driver synthesises arguments to call the function, picked `[1,2,3]` as the dict key, hit `unhashable type: 'list'`, and gave up — so the shared default was never mutated and the defect was wrongly dismissed. The fix was a `require_success` mode that keeps trying candidates until a call actually lands. It went through the same held-out gate as any rule change, and it is recorded in `rules/v3.json` with the reason.

Two tests keep this honest:

- `test_gate_rejects_a_noisy_candidate` registers a rule that fires on every function. It has perfect recall and the gate refuses it anyway, because precision drops.
- `test_promoted_ruleset_beats_its_parent_on_heldout` re-derives the metrics for every promoted version on disk. A ruleset that claims promotion without a held-out recall gain fails the suite.

---

## Architecture

```
ASI:One  ──chat protocol──▶  agent.py (gate)
                                │
                                ├─ sources.py   snippet / diff / GitHub PR  →  post-image
                                ├─ detectors.py 9 rules, AST matchers + probe builders
                                ├─ remote.py ───ProbeRequest──▶  prover_agent.py
                                │                ◀──ProbeResponse──  (sandboxed execution)
                                ├─ verdict.py   OBSERVED+severity → BLOCK, else REVIEW/ALLOW
                                ├─ cards.py     review / carousel / detail cards
                                └─ learn.py     dev misses → candidate → held-out gate
```

**Why two agents.** The component that executes submitted code is the dangerous one, so it is a separate agent with its own address and no opinions — it runs a probe and returns stdout. You can deploy it in a disposable container while the chat-facing gate runs somewhere you care about. The gate calls it with `ctx.send_and_receive` and reports which path produced each piece of evidence.

**When the prover fails.** In `local` mode the gate falls back to in-process execution and says so in the report (`Tool-call recovery: CG002: prover unavailable (...), ran locally instead`). In `prover` mode there is no fallback — the finding stays unproven, because a deployment that refuses local execution must not be talked out of it by a timeout. A crash anywhere in the pipeline produces no verdict at all, rather than a permissive one.

### What the model does and does not do

The ASI:One LLM routes ambiguous intent and writes the one-line plain-English summary. **It cannot change a verdict.** Decisions come from executed probes, so a hallucinating model can make the agent sound wrong but cannot make it block or allow wrongly. With no API key the agent degrades to keyword routing and drops the summary; everything else is unaffected.

---

## The rules

| rule | severity | proves itself by | catches |
|---|---|---|---|
| `CG001` | high | calling the function repeatedly and diffing `__defaults__` | mutable default argument |
| `CG002` | high | calling with an empty sequence | division by `len()` with no guard |
| `CG003` | high | trying five container shapes to reach the mutating branch | mutation during iteration |
| `CG005` | critical | intercepting `os.system`/`subprocess` and showing the built command | shell injection |
| `CG006` | critical | passing a fake DB driver and showing the SQL text | SQL injection |
| `CG007` | medium | patching `open` and checking `.closed` after return | leaked file handle |
| `CG009` | high | calling with a dict missing the key | attribute access on `None` |
| `CG008` | medium | *nothing — static only* | bare `except` |
| `CG010` | medium | *nothing — static only* | `assert` used for validation |

`CG008` and `CG010` are real problems that are not independently reproducible — whether a stripped `assert` matters depends on your deploy flags. They are reported as judgement calls and **can never block**, which is enforced by `test_static_only_rules_have_no_probe`.

---

## Security posture

This agent executes code that strangers send it. That is the point — it is also, stated plainly, a remote code execution service unless you constrain it. So execution is opt-in via `CODEGATE_EXEC_POLICY`:

| mode | behaviour |
|---|---|
| `off` *(default)* | never executes. Every finding is unproven, nothing can block. Safe to expose. |
| `local` | runs probes in this process's sandbox. For a machine you can throw away. |
| `prover` | runs probes **only** on the prover agent, no local fallback. Point `PROVER_ADDRESS` at a prover in a disposable container. |

An unset or unrecognised value falls back to `off`. Starting in `local` without `CODEGATE_ACK_PUBLIC_EXEC=true` logs a warning naming the exposure.

The sandbox (`codegate/sandbox.py`) gives each probe a child process in isolated mode (`-I`), a throwaway cwd, CPU/file-size/core rlimits, a wall-clock kill, a scrubbed environment, and `socket` monkeypatched to raise. Two tests hold that up: `test_sandbox_blocks_network` and `test_sandbox_kills_a_hanging_probe`.

**It is not a container and not a seccomp jail.** For genuinely untrusted input, run the prover inside one. The same caveat is printed at the bottom of every report rather than buried here.

---

## Honest limitations

- **Python only.** The detectors are AST-based and do not transfer to other languages as written.
- **The corpus is small (22 cases) and I wrote it.** The held-out discipline is real — those cases genuinely never informed a rule — but 1.00 recall on 12 held-out cases of my own construction is a sanity check, not an industry benchmark. The number to watch is whether it *drops* as the corpus grows.
- **The probe driver synthesises arguments** by trying candidate values against the signature. It can fail to reach the branch containing the defect, which produces a `REFUTED` finding for a bug that is really there. Both learning cycles above were fixes for exactly this. It fails toward *not claiming*, which is the right direction, but it is a false-negative source.
- **Diff fragments cannot be executed.** A hunk without its surrounding file is analysed statically and marked as such, so a `git diff` paste usually yields weaker evidence than a PR link, which fetches whole post-image files.
- **Session state is in-process.** Restarting the gate clears pending card interactions; the user is told to resubmit rather than shown a stale verdict.
- **Overrides are acknowledged, not yet learned from.** When a human overrides a reproduced block, that is the highest-value training signal available and currently it is only reported back in the conversation.

---

## Challenge requirements

| requirement | where |
|---|---|
| Registered on Agentverse | `agent.py`, `mailbox=True`, claimed via the inspector link |
| Implements Agent Chat Protocol | `Protocol(spec=chat_protocol_spec)`, `publish_manifest=True` |
| Discoverable and usable from ASI:One | `publish_agent_details=True` |
| Meaningful tool execution | sandboxed probe execution; GitHub API; every verdict backed by real output |
| Multi-agent orchestration | gate ↔ prover over `ProbeRequest`/`ProbeResponse` with explicit fallback |
| Full workflow inside ASI:One | review, evidence inspection, merge decision, and the learning cycle are all chat-driven |
| Interactive Cards | review, carousel and detail cards from the official `cards` module |
| Reliability / recovery | prover failover, policy-aware degradation, crash never yields a verdict |
| Public repo with instructions | this file |

## Layout

```
agent.py              gate agent — ASI:One facing, ACP
prover_agent.py       prover agent — sandboxed probe execution
cli.py                local driver, no network needed
codegate/
  models.py           Finding / Evidence / Verdict; the OBSERVED-vs-INFERRED split
  detectors.py        9 rules: AST matchers paired with probe builders
  sandbox.py          isolated child-process probe runner + the arg-synthesising driver
  prober.py           runs probes, promotes matches to OBSERVED / REFUTED
  verdict.py          the gate: only reproduced + severe may block
  sources.py          snippet / unified diff / GitHub PR adapters
  learn.py            dev misses → candidate ruleset → held-out promotion gate
  corpus.py           labelled corpus loader
  cards.py            ASI:One interactive cards
  report.py           markdown rendering
  policy.py           execution policy; off by default
  remote.py           gate → prover delegation with fallback
  llm.py              optional ASI:One assist; cannot influence a verdict
corpus/               22 labelled cases, dev / held-out split
rules/                v1 → v2 → v3, each with its promotion reason
tests/                58 unit tests + a live two-agent integration check
examples/             the Fetch.ai starter agent this repo grew out of
```
