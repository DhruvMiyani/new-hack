# AIE Code Summit 2026 — event context & judging strategy

Working notes for this submission. Event facts first, then the part that
actually decides the outcome: the four judging criteria and how we score
against each.

> Venue WiFi credentials from the participant guide are deliberately **not**
> recorded here — this repository is public.

---

## 1. Hard deadlines

| when (PT) | what |
|---|---|
| Sat 7 Nov, 10:45 | hacking begins — **nothing before this counts as hackathon work** |
| Sat 7 Nov, 15:00 | Meta credit claims close |
| **Sat 7 Nov, 17:00** | **submissions due** (incl. 1-minute demo video) |
| 17:00–18:45 | round one judging, ~3 min live demo + 1–2 min Q&A |
| 19:00–20:00 | round two, top six, on stage |
| 20:15 | winners |

Location: Shack15, 1 Ferry Building Suite 201, SF.
Questions: gina@cerebralvalley.ai or the Cerebral Valley Discord.

---

## 2. Problem statement we are building in

Three were offered. We target **Trust but Verify**, with a secondary claim on
**The System That Gets Better**.

> **Trust but Verify** — AI writes code faster than teams can check whether
> it's safe to merge. Build an independent verifier that evaluates AI-generated
> changes, catches defects, and blocks or rolls back anything below the required
> standard. *How can a verifier write its own tests without sharing the
> implementer's blind spots, and stay independent, reproducible and hard to
> game?*

That italicised question is the whole project. We do not answer it with an
architecture diagram; we answer it with a number, measured from executed code.

**The System That Gets Better** is a secondary fit: the pre-hackathon CodeGate
engine promotes a ruleset change only when held-out evaluation confirms it, and
rolls back otherwise. We disclose that as prior work (see §4) and do not lead
with it.

Not targeted: **Software Archaeology**.

---

## 3. Anti-project check

The guide bans a specific list. Verifying we are clear:

| banned | us |
|---|---|
| basic RAG app | no retrieval anywhere |
| Streamlit app | no Streamlit; CLI + agent |
| dashboard as the main feature | **no dashboard at all** — the deliverable is a measurement and a merge decision |
| image analyser / personality / mental health / education chatbot / job screener / nutrition / sports / medical advice | none of these |

The "dashboard" ban is the one worth being careful about. Our terminal output
is a *report of executed results*, not a UI, and the demo shows code running.

---

## 4. Rules compliance — the disqualification risk, handled

Two rules carry immediate disqualification:

> **Demo Requirements:** your demo must only highlight the specific features,
> code and functionality your team built during the hackathon.
>
> **New Work Only:** you may not present an existing project as your own work.

**The risk.** A verification engine (`codegate/`, plus the uAgents agent) was
written on **6 Nov**, before the 10:45 start. Presenting it as hackathon work
would be a DQ.

**How we handle it.**

1. **Git provenance.** The first commit in this repository is the pre-hackathon
   code, unmodified, with its real authored date in the message:
   `eea395e Pre-hackathon baseline (authored 2026-11-06, BEFORE the 10:45 PT start)`.
   Everything built during the event is in later commits. `git diff eea395e HEAD`
   is an auditable record of exactly what was built today.
2. **The demo leads with today's work.** `blindspot/` — defect injection,
   witness-based equivalence testing, the information wall, the measurement, and
   both sponsor integrations — is all hackathon work.
3. **We say so out loud.** The README has a provenance section, and the demo
   states in one sentence what predates the event.

**What to say in the demo (do not skip this):**

> "The verification engine and the Fetch.ai agent were built last night, before
> the start — that's the first commit, and it's labelled. Everything I'm showing
> you now is today's work: the defect injection, the witness prover, the
> information wall, and the measurement. `git diff` against that first commit is
> the whole of it."

Also required: **repository must be public** before submitting.

---

## 5. Sponsor tracks targeted

One project, four prize pools.

| pool | prize | our claim |
|---|---|---|
| **Overall** | $3,000 / $2,000 / $1,000 | Trust but Verify, answered as a measurement |
| **Best Use of Meta Model API** | $5,000 | Muse writes the implementation, its own tests, *and* independent spec-only tests — it is the subject of the experiment, not a chat wrapper |
| **Best Use of Subquadratic** | $5,000 / $1,000 | SubQ Snap's Decisions API adjudicates test independence and defect severity. Chosen *because* it emits no text, so it structurally cannot leak an implementation assumption into a test |
| **Best Use of Fetch.ai** | $2,500 / $2,500 | the whole workflow runs inside an ASI:One conversation via ACP, with interactive cards |

Credits: Meta $100 (dev.meta.ai), Subquadratic 100M tokens (console.subq.ai),
MongoDB $100 with code `MKT-BUILDERS-100` (unused — we have no database need,
and bolting one on would be transparent prize-chasing).

**Why the sponsor usage should read as earned, not bolted on:** each product does
the job it is actually best at. Meta generates (it is the only one that can).
SubQ decides (384K context, structured answers, output tokens free — and its own
docs name "checking whether a test or result meets specified criteria" as a use
case). Fetch.ai makes it reachable and interactive. Swapping any one of them out
would change what the project *is*.

---

## 6. Judging criteria — honest self-assessment

Round one weights these; round two weights all four equally.

### Technical Demo — 35% (the largest single slice)

*"How is the actual demo? Does it impress you? Is it well-engineered and working?"*

**Strong.** Everything runs live, offline, in about 15 seconds, and prints real
numbers:

```
3 tasks | 27 proven defects (each with a witnessed input)
  caught by the implementer's own tests   26%
  caught by independent spec-only tests   74%
  blind-spot recovery                     13/20 = 65%
```

Engineering evidence to point at:
- **83 tests passing** (`pytest tests/ -q`), including anti-cheat tests
- runs with **zero API keys** — the demo cannot be broken by conference WiFi or
  a rate limit, which is the single most common live-demo failure
- every defect shown comes with a **witness**: `f(1, [0]) -> original [0] | mutant []`

**Risk:** if the sponsor keys are unset, the demo shows `absent` for Meta and
SubQ and the two $5,000 tracks lose their evidence. **Set both keys and do one
live run before demoing.**

**Mitigation if a key fails on stage:** `--offline` produces the same headline
number, and the fallback is a designed, reported behaviour rather than a crash.
Say that out loud: it is an engineering point in our favour.

### Implementation Difficulty — 30%

*"How hard is this to recreate? Is the technical depth more than a few AI prompts away?"*

**Strong — this is our best criterion.** Nothing here is promptable:

- **AST mutation operators** (6 of them) that produce single-site, parseable,
  realistic defects — including `dropguard`, which deletes an early-return and
  is the operator that most cleanly separates the two suites
- **Witness-based equivalence testing.** The known hole in mutation testing is
  equivalent mutants. We close it by differential-testing every mutant against
  the original over a boundary-heavy input space and only counting it as a defect
  once we hold a distinguishing input. On `page_range`, 18 mutants reduced to
  **5 real defects + 13 equivalents** — without this the numbers are simply wrong.
- **Isolated execution**: child process, `-I`, scrubbed env, rlimits, wall-clock
  kill, network monkeypatched off, per-test outcomes
- **Type-aware input synthesis** driven by annotations and parameter names
- **An information wall** enforced in code, not in a prompt

Say: *"the hard part isn't calling two APIs, it's knowing that a mutation score
is meaningless until you've proven your mutants aren't equivalent."*

### Impact Potential — 20%

*"Does it address a significant real-world issue?"*

**Strong premise, and we have a finding rather than an opinion.** Everyone
shipping AI-generated code asks the model to write its own tests. We measured
what that buys: **the implementer's own tests caught 26% of real defects.** The
same model, denied sight of the implementation, caught 74%.

That is a directly actionable change to how teams configure a coding agent, and
it costs nothing to adopt — it is an ordering constraint, not a new tool.

Also honest and worth saying: **both suites missed some defects** (`allocate(0, [0])`).
Independence is an improvement, not a cure. Saying that *raises* credibility
with technical judges; overclaiming here is how you lose Q&A.

### Creativity — 15%

*"Is this something you have never seen before?"*

**Moderate to strong.** Mutation testing is 1970s-vintage and LLM code review is
everywhere. What judges are unlikely to have seen:

- treating **"can the verifier avoid the implementer's blind spots"** as an
  experiment with a control group, rather than a design claim
- using a model that **cannot generate text** as the adjudicator *specifically
  because* it cannot — the inability is the security property
- **witness-gated** mutation scoring

Weakest criterion, and the lowest weight. Lead with difficulty and demo.

---

## 7. Three-minute demo script

Time is the binding constraint. Rehearse this; do not improvise.

**0:00–0:20 — the problem, concretely**
> "When you ask a coding agent to write code and then write its own tests, how
> much safety does that actually buy? Nobody measures it. We did."

**0:20–0:35 — provenance (mandatory)**
> "The engine and the agent are from last night — first commit, labelled.
> Everything after it is today."

**0:35–1:30 — the live run.** `python verify.py run`
Narrate while it runs: inject known defects → prove each is real with a witness
→ two suites behind an information wall → measure.

**1:30–2:10 — the result.** Land on 26% vs 74%, then show one specific defect
only the independent suite caught, with its witness.

**2:10–2:40 — why it's hard to game.** Three sentences: suites validated on
correct code first (a suite that fails everything is disqualified, not
credited); equivalent mutants excluded by witness; adjudicator cannot write
tests. Mention the 83 tests.

**2:40–3:00 — the sponsors, in one breath each.** Meta generates. SubQ decides
and cannot generate. Fetch.ai makes it a conversation. Then stop talking.

### Q&A — likely questions

- *"Isn't this just mutation testing?"* → Mutation testing scores one suite.
  We compare two suites that differ only in what they were allowed to see, and
  we gate on witnesses, which standard mutation testing does not.
- *"How do you know the mutants are real bugs?"* → Show a witness. Every one has
  an input where original and mutant disagree.
- *"Could a suite cheat to a perfect score?"* → `assert False` everywhere.
  Caught by baseline validation; there's a test for exactly that.
- *"Why is SubQ a good fit?"* → Its output token count is always zero. An
  adjudicator that cannot write text cannot smuggle an implementation
  assumption into a test. Plus 384K context for whole-repo reads.
- *"Only 3 tasks?"* → Yes, and that's the main limitation. 27 proven defects is
  enough to show the effect and too few to size it precisely.

---

## 8. Submission checklist

- [ ] **repository public** (DQ if not)
- [ ] **both sponsor keys set, one live run completed** — unlocks $10,000 of tracks
- [ ] 1-minute demo video recorded, showing code and functionality built today
- [ ] submitted on the Cerebral Valley dashboard
- [ ] **registered through the AIE Summit ASI:One Submission Agent** (separate
      step, required by the Fetch.ai track, easy to forget)
- [ ] all team members added to the submission page
- [ ] README provenance section accurate
- [ ] `pytest tests/ -q` green on the machine you demo from
