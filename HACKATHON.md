# AIE Code Summit 2026 — event context & judging strategy

Working notes for this submission. Event facts first, then the part that
actually decides the outcome: the four judging criteria and how we score
against each.

> Venue WiFi credentials from the participant guide are deliberately **not**
> recorded here — this repository is public.

**Status at last update (12:31 PT):** 174 tests passing, six scored
experiments plus a cloud-exposure domain, **zero live API calls made**. See §9.

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
rolls back otherwise. We disclose that as prior work (§4) and do not lead with it.

Not targeted: **Software Archaeology**.

---

## 3. Anti-project check

| banned | us |
|---|---|
| basic RAG app | no retrieval anywhere |
| Streamlit app | no Streamlit; CLI + ASI:One agent |
| dashboard as the main feature | **no dashboard at all** — output is a measurement |
| image analyser / personality / mental health / education chatbot / job screener / nutrition / sports / medical advice | none of these |

**The dashboard ban deserves active care.** All three of Subquadratic's own
showcases are dashboards. It is tempting to imitate the presentation layer; we
imitate the *decision structure* instead and keep the deliverable a terminal
measurement. Do not build a web UI before the deadline.

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

1. **Git provenance.** The first commit is that pre-hackathon code, unmodified,
   with its real authored date in the message:
   `eea395e Pre-hackathon baseline (authored 2026-11-06, BEFORE the 10:45 PT start)`.
   `git diff eea395e HEAD` is an auditable record of exactly what was built today.
2. **The demo leads with today's work** — everything in `blindspot/`.
3. **We say so out loud**, in the README and in the demo.

**Say this, verbatim, around 0:20 into the demo:**

> "The verification engine and the Fetch.ai agent were built last night, before
> the start — that's the first commit, and it's labelled. Everything I'm showing
> you now is today's work. `git diff` against that first commit is the whole of it."

Also required: **repository must be public** before submitting.

---

## 5. Sponsor tracks targeted

One project, four prize pools.

| pool | prize | our claim |
|---|---|---|
| **Overall** | $3,000 / $2,000 / $1,000 | Trust but Verify, answered as a measurement |
| **Best Use of Meta Model API** | $5,000 | Muse writes the implementation, its own tests, *and* independent spec-only tests across four models and two reasoning levels — it is the *subject* of the experiment |
| **Best Use of Subquadratic** | $5,000 / $1,000 | four separate scored experiments, one per capability they advertise |
| **Best Use of Fetch.ai** | $2,500 / $2,500 | the whole workflow runs inside an ASI:One conversation via ACP, with interactive cards |

Credits: Meta $100 (dev.meta.ai), Subquadratic 100M tokens (console.subq.ai),
MongoDB $100 code `MKT-BUILDERS-100` (**unused** — we have no database need, and
bolting one on would read as prize-chasing).

### Why the sponsor usage should read as earned

Each product does the job it is uniquely able to do. Remove any one and the
project changes shape.

- **Meta generates.** It is the only one that can — SubQ emits no text at all.
- **SubQ decides.** Chosen partly *because* it cannot generate: an adjudicator
  that cannot write text cannot smuggle an implementation assumption into a test.
- **Fetch.ai** makes it reachable and interactive.

---

## 6. The six experiments

Everything is scored against ground truth, because every defect is proven real
by a **witness** — a concrete input where original and mutant provably differ.

| command | what it measures | sponsor capability |
|---|---|---|
| `run` | the core result: self-written vs independent tests | — (local, always works) |
| `matrix` | information independence vs model independence, with error bars | Meta: 4 models, `reasoningEffort`, seeds |
| `calibrate` | Decision Score `(accuracy − Brier) × 100` + symmetry | SubQ: calibrated probabilities |
| `localize` | which line holds the defect, confidence-gated | SubQ: 255-option `choice` |
| `queue` | merge-queue triage vs FIFO and random | SubQ: curation + multimodal + triage |
| `haystack` | one bug hidden in up to 384K tokens | SubQ: long context |
| `run --cloud` | exposure vs regression on IaC and IAM policy | — (local, always works) |

### The headline, from executed code

```
3 code tasks | 27 proven defects
  caught by the implementer's own tests   26%
  caught by independent spec-only tests   74%
  blind-spot recovery                     13/20 = 65%
```

### The second domain: cloud exposure (`verify.py run --cloud`)

Security-group, bucket-policy and IAM-role configurations get the same
treatment, with the same evidence bar: a permission change counts only when we
can name an access that was **denied before and allowed after**. The witness is
produced by evaluating the policy, so it needs no cloud account.

**The result here contradicts the code result, and that is the interesting
part.** Pooled across 24 proven permission changes the independent suite scores
*worse* — 54% against 71%. Splitting by what the change actually did explains
why:

```
change class     n     self  independent
EXPOSURE        16     56%         81%     <- access newly granted
REGRESSION       8    100%          0%     <- access only removed
```

A suite written from a security requirement is a list of **prohibitions**, so
it dominates on access that gets opened and is structurally blind to access
that gets removed. A suite written from the config asserts what should be
there, so it has the opposite profile.

**They are complementary, not ranked.** For configuration you need both. An
earlier version of the test asserted a blanket win for independence; the data
did not support it, and the test was rewritten rather than the corpus tuned.

### The anti-gaming rules, each with a test

1. A suite that fails on *correct* code is **disqualified, not credited** —
   otherwise `assert False` everywhere scores 100%.
2. A mutant is not a defect until a **witness** proves it. On `page_range`,
   18 mutants reduced to 5 real defects and 13 equivalents.
3. A triage ordering worse than FIFO prints **`SLOWER than doing nothing`**.
4. A constant predictor must **lose to the base-rate baseline**.
5. The haystack **refuses a trial** unless the needle survives into the prompt.
6. On cloud, **EXPOSURE and REGRESSION are scored separately** — blending them
   would let a suite that only catches regressions look like a security control.

---

## 7. Judging criteria — honest self-assessment

Round one weights these; round two weights all four equally.

### Technical Demo — 35% (largest single slice)

**Strong.** Everything runs live in ~15 seconds and prints real numbers. 123
tests. **Runs with zero API keys** — conference WiFi cannot break the demo.
Every defect shown carries a witness: `f(1, [0]) -> original [0] | mutant []`.

**Risk:** with no sponsor keys the Meta and SubQ experiments print `absent`,
and the two $5,000 tracks lose their evidence. **Set both keys and do one live
run before demoing.**

**If a key fails on stage:** `--offline` produces the same headline number.
Say that out loud — designed degradation is an engineering point in our favour.

### Implementation Difficulty — 30% (our best criterion)

Nothing here is promptable:

- **Witness-based equivalence testing.** The known hole in mutation testing is
  equivalent mutants; we close it by differential-testing every mutant over a
  boundary-heavy input space and only counting it once we hold a distinguishing
  input.
- 6 AST mutation operators including `dropguard`
- Isolated execution: child process, `-I`, scrubbed env, rlimits, wall-clock
  kill, network off, per-test outcomes
- Type-aware input synthesis from annotations and parameter names
- A 384K-token haystack assembler with a needle-survival guard
- An information wall enforced in code, not in a prompt

Line to use: *"the hard part isn't calling two APIs — it's knowing a mutation
score is meaningless until you've proven your mutants aren't equivalent."*

### Impact Potential — 20%

**A finding, not an opinion.** Everyone tells their coding agent to write its
own tests. That caught **26%** of real defects. The same model, denied sight of
the implementation, caught **74%**. Adopting it costs nothing — it is an
ordering constraint, not a new tool.

Also say: **both suites missed some defects.** Independence is an improvement,
not a cure. Admitting that raises credibility; overclaiming loses Q&A.

### Creativity — 15% (weakest, lowest weight)

Mutation testing is old and LLM review is everywhere. What judges are unlikely
to have seen:

- treating "can a verifier avoid the implementer's blind spots" as an
  experiment with a **control group**
- using a model that **cannot generate text** as the adjudicator *because* it can't
- **witness-gated** mutation scoring
- running the **Decision Index formula on a domain the index omits**

Lead with difficulty and demo, not this.

---

## 8. Three-minute demo script

Six experiments, three minutes. **You cannot show them all — do not try.**
Show one end-to-end, name the rest in one breath.

**0:00–0:20 — the problem**
> "When you ask a coding agent to write code and then write its own tests, how
> much safety does that actually buy? Nobody measures it. We did."

**0:20–0:35 — provenance** (mandatory, see §4)

**0:35–1:30 — the live run.** `python verify.py run`
Narrate while it runs: inject known defects → prove each real with a witness →
two suites behind an information wall → measure.

**1:30–2:05 — the result.** Land on 26% vs 74%. Show one defect only the
independent suite caught, with its witness. Mention the shared blind spot.

**2:05–2:35 — why it is hard to game.** Three sentences from §6. Mention 123 tests.

**2:35–3:00 — the sponsors, one breath each.** Meta generates (4 models, two
reasoning levels). SubQ decides and *cannot* generate — scored four ways,
including one bug in 350K tokens. Fetch.ai makes it a conversation. Stop talking.

### If you get a second run (round two, or a deep Q&A)

Pick by who is asking:

- Subquadratic judge → `verify.py haystack` (long context) or `queue` (triage vs FIFO)
- Meta judge → `verify.py matrix --models muse-spark-1.3,muse-wonder-1.0`
- Fetch.ai judge → the ASI:One agent, `blindspot` command

### Q&A — likely questions

- *"Isn't this just mutation testing?"* → Mutation testing scores one suite. We
  compare two that differ only in what they were allowed to see, and we gate on
  witnesses, which standard mutation testing does not.
- *"How do you know the mutants are real bugs?"* → Show a witness.
- *"Could a suite cheat to a perfect score?"* → `assert False` everywhere.
  Caught by baseline validation; there is a test for exactly that.
- *"Why is SubQ a good fit?"* → Its output token count is always zero. An
  adjudicator that cannot write text cannot smuggle an implementation
  assumption into a test. Plus 384K context.
- *"Only 3 tasks?"* → Yes, the main limitation. 27 proven defects shows the
  effect and is too few to size it precisely.
- *"Did you measure Subquadratic's own claims?"* → Yes — Decision Index
  formula, on software verification, which none of its six components covers.

---

## 9. Current status and what is left

**Built and tested (123 passing):** all six experiments, both provider clients,
the ASI:One agent command, the anti-gaming rules.

**Not yet done — the gap between "working" and "demonstrated":**

| blocker | effect |
|---|---|
| **No Meta key** | `matrix` prints `absent`; the headline becomes a claim about stand-in suites rather than about Muse |
| **No SubQ key** | `calibrate`, `localize`, `queue`, `haystack` all refuse to run; $5,000 track has no evidence |
| **Repo not public** | instant DQ |
| **ASI:One submission agent** | separate from the CV form; required by the Fetch.ai track |

The two keys are independent — `calibrate` needs only SubQ, `matrix` only Meta.
Whichever arrives first unlocks a real number.

**Scope discipline from here:** cloud exposure was added on request and is
done (3 tasks, 4 config mutation operators, 174 tests). No further domains
before the deadline — a web UI in particular would walk into the banned
"dashboard as the main feature" category. The remaining time goes to live runs,
the video, and making the repo public.

## 10. Submission checklist

- [ ] **repository public** (DQ if not)
- [ ] **both sponsor keys set, one live run each** — unlocks $10,000 of tracks
- [ ] headline numbers in README updated from a live run, with mode stated
- [ ] 1-minute demo video showing code and functionality built today
- [ ] submitted on the Cerebral Valley dashboard
- [ ] **registered through the AIE Summit ASI:One Submission Agent**
- [ ] all team members added to the submission page
- [ ] README provenance section accurate
- [ ] `pytest tests/ -q` green on the demo machine
