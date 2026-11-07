# Pitch

Every number here came from a live run. Nothing is projected.

---

## The one-liner

> **Blindspot turns "our AI wrote tests" from a claim into a number.**

Alternatives, same idea, different angle:

- *"We plant bugs we can prove are real, then measure which test suite actually catches them."*
- *"Your coding agent writes its own tests. Nobody checks whether they catch anything. We did."*
- *"A verifier that refuses to report a defect it cannot reproduce."*

---

## 30 seconds

> Coding agents write code and then write their own tests. Everyone ships on that.
> Nobody measures whether those tests catch anything — to measure it, you'd need to
> know where the bugs actually are.
>
> So we built that. We inject defects and only count one once we hold a **witness**:
> a concrete input where the original and the broken version provably disagree.
>
> Then we tested the advice everyone repeats — *let an independent reviewer write the
> tests.* For code, it's wrong: 96% for the implementer's own tests, 81% for
> spec-only. For cloud permissions it's right, and decisively: 81% versus 56%.
>
> The rule underneath is **independence pays when the requirement is a prohibition.**

---

## 60 seconds

Add, in this order:

1. **The scale it works at.** One bug hidden in **350,000 tokens** of real Python —
   91% of the window, 255 candidate lines so guessing scores 0.4%. **Nine out of
   nine**, including dead centre of the context where long-context models classically
   fail. Under four seconds. Six cents for the sweep.

2. **Why it can't be gamed.** A suite that fails on correct code is disqualified, not
   credited. A mutant isn't a defect until a witness proves it — on one task that cut
   18 mutants to 5 real defects and 13 equivalents. A triage worse than doing nothing
   prints `SLOWER`.

3. **The honest part.** We also scored Subquadratic on their own Decision Index
   formula, in a domain its six components don't cover. 50.2/100 — beats the
   base-rate baseline by 0.012 and is systematically overconfident: predicted 0.93,
   observed 0.60. Ten predictions, so indicative only.

---

## The numbers you may quote

| claim | value | source |
|---|---|---|
| self-written tests, live | **96%** | `verify.py run`, muse-spark-1.3, 27 proven defects |
| independent tests, live | **81%** | same run |
| cloud exposures, independent | **81%** vs 56% self | `run --cloud`, 16 exposures |
| cloud regressions, independent | **0%** vs 100% self | same, 8 regressions |
| needle in 350K tokens | **9/9**, 3.9s, $0.0595 | `haystack`, 255 options |
| defect localisation | **100%** vs 10% random | `localize`, $0.0001 |
| merge-queue triage | first defect **3 verifications sooner** than FIFO | `queue` |
| SubQ Decision Score | **50.2/100**, overconfident | `calibrate`, n=10 |
| tests | 221 passing | `pytest tests/ -q` |

## Do not say

- "independence catches 74%" — that came from hand-written stand-in suites and the
  live run refuted it
- "blind-spot recovery 65%" — live it is 0%, off a single missed defect
- any precision claim on n=10 or n=27

---

## The line that wins the room

> "We ran the experiment and it contradicted us. Twice. Both results are in the repo."

Almost every pitch is *we built X and it works*. This one is *we built an instrument,
pointed it at our own hypothesis, and published what came back*. That is not fakeable
in Q&A, and it is the difference the Implementation Difficulty judges are looking for.

---

## Provenance line — say it, around 0:20

> "The verification engine and the Fetch.ai agent were built last night, before the
> start — that's the first commit, and it's labelled. Everything else is today.
> `git diff` against that commit is the whole of it."

---

## Q&A

**"Your result contradicts your premise — isn't that a failed project?"**
The project is the instrument, not the hypothesis. It produced a falsifiable claim and
falsified it. That's the deliverable.

**"Isn't mutation testing biased toward implementation-derived tests?"**
Yes — say it first. A mutant is a perturbation *of* the implementation, so a suite
written from that implementation is matched to the perturbation space by construction.
It's a real limit on what this can say about blind spots, and it's in the write-up.

**"Only 27 defects?"**
Yes. Enough to show the effect, too few to size it. The self-suite missed exactly one,
so don't read the recovery rate as precise.

**"Why these two models?"**
Meta generates — it's the only one that can. SubQ generates no text at all, which is
why it adjudicates: an adjudicator that cannot write text cannot smuggle an
implementation assumption into a test.

**"Could a test suite cheat to a perfect score?"**
`assert False` everywhere. Caught by baseline validation — there's a test for exactly
that case.

**"Did independence always win?"**
No. On code it lost. On cloud exposures it won. That split is the finding.
