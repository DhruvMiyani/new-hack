"""CodeGate -- a verification agent that reproduces defects before it blocks them.

Discoverable through ASI:One, speaking the Agent Chat Protocol. The whole
workflow runs inside the conversation: paste code or a PR link, get a merge
decision backed by executed evidence, inspect each defect, and watch the agent
improve its own ruleset against a held-out set it never tunes on.

Execution of submitted code is opt-in; see codegate/policy.py. By default this
agent reviews without running anything, and says so.
"""

import os
from datetime import datetime, timezone
from uuid import uuid4

from dotenv import load_dotenv
from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    MetadataContent,
    StartSessionContent,
    TextContent,
    chat_protocol_spec,
)
from uagents_core.contrib.protocols.chat.cards import extract_card_response

from codegate import cards, learn, llm, policy
from codegate.models import Status
from codegate.pipeline import ReviewResult, Step
from codegate.prober import scan
from codegate.remote import verify_via_prover
from codegate.report import render, render_steps
from codegate.sources import resolve
from codegate.verdict import decide

load_dotenv()

POLICY = policy.from_env()

agent = Agent(
    name=os.getenv("AGENT_NAME", "codegate-verifier"),
    seed=os.environ["AGENT_SEED"],
    port=int(os.getenv("AGENT_PORT", "8001")),
    mailbox=True,
    publish_agent_details=True,
)

protocol = Protocol(spec=chat_protocol_spec)

# Last review per correspondent, so the carousel and detail cards have
# something to point at. In-process only: a restart clears it, and the user is
# told to resubmit rather than being shown a stale verdict.
SESSIONS: dict[str, ReviewResult] = {}
MAX_SESSIONS = 200


HELP = """**CodeGate** — I verify AI-generated code by *reproducing* its defects, then gate the merge.

**Send me any of:**
- a Python function or file (paste it directly)
- a `git diff` / unified diff
- a GitHub PR link — `https://github.com/owner/repo/pull/123`

**What you get back:** a BLOCK / REVIEW / ALLOW decision where every blocking defect was
executed, with the real output attached. Anything I could not reproduce is labelled
unproven and is never allowed to block on its own.

**Other commands:**
- `metrics` — my accuracy on a held-out set I never tune against
- `learn` — find a defect class I miss, propose a fix, promote it only if held-out numbers improve
- `rules` — the active ruleset
"""


def _say(text: str, extras=None, end: bool = False) -> ChatMessage:
    content = [TextContent(type="text", text=text)]
    content.extend(extras or [])
    if end:
        content.append(EndSessionContent(type="end-session"))
    return ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(), content=content)


def _remember(sender: str, result: ReviewResult) -> None:
    if len(SESSIONS) >= MAX_SESSIONS:
        SESSIONS.pop(next(iter(SESSIONS)))
    SESSIONS[sender] = result


# --------------------------------------------------------------------------- #
# workflows
# --------------------------------------------------------------------------- #


async def do_review(ctx: Context, sender: str, text: str) -> None:
    """resolve -> scan -> prove -> decide -> report."""
    rs = learn.latest_promoted()
    sub = resolve(text)
    steps = [Step("resolve input", f"{sub.kind}: {sub.ref} -> {len(sub.analysable)} Python file(s)")]
    for w in sub.warnings:
        steps.append(Step("input warning", w))

    if not sub.analysable:
        await ctx.send(
            sender,
            _say(
                "I could not get any analysable Python out of that.\n\n"
                + "\n".join(f"- {w}" for w in sub.warnings)
                + "\n\n"
                + HELP
            ),
        )
        return

    await ctx.send(
        sender,
        _say(
            f"Analysing **{sub.ref}** ({len(sub.analysable)} file(s)) with ruleset `{rs.version}`.\n\n"
            f"Execution policy: _{POLICY.describe()}_."
        ),
    )

    all_findings, exec_notes = [], []
    for unit in sub.analysable:
        try:
            found = scan(unit.source, unit.path, rs.enabled_set)
        except SyntaxError as exc:
            steps.append(Step("scan", f"{unit.path}: does not parse ({exc.msg}, line {exc.lineno})"))
            continue
        steps.append(Step("static scan", f"{unit.path}: {len(found)} candidate(s)"))
        if not found:
            continue

        if not POLICY.executes:
            for f in found:
                f.probe_unavailable_reason = "probe execution disabled on this deployment"
            steps.append(Step("execute probes", f"{unit.path}: skipped — execution policy is off"))
        elif not unit.executable:
            why = unit.note or "file is not executable standalone"
            for f in found:
                f.probe_unavailable_reason = why
            steps.append(Step("execute probes", f"{unit.path}: skipped — {why}"))
        else:
            rep = await verify_via_prover(
                found,
                unit.source,
                ctx,
                POLICY.prover_address,
                rs.probe_config,
                allow_local_fallback=POLICY.may_run_locally,
            )
            steps.append(Step("execute probes", f"{unit.path}: {rep.line()}"))
            exec_notes.extend(rep.notes)
        all_findings.extend(found)

    all_findings.sort(key=lambda f: (f.status is not Status.OBSERVED, -f.severity.rank, f.path, f.line))
    verdict = decide(all_findings, sub.ref, rs.version)
    steps.append(Step("decide", f"{verdict.decision.value}: {verdict.summary}"))
    result = ReviewResult(verdict=verdict, submission=sub, steps=steps, ruleset_version=rs.version)
    _remember(sender, result)

    body = render(result)
    if not POLICY.executes:
        body += "\n\n> " + policy.OFF_NOTICE
    if exec_notes:
        body += "\n\n_Tool-call recovery: " + "; ".join(exec_notes) + "._"

    gloss = llm.explain(verdict.to_dict())
    if gloss:
        body += f"\n\n**In short:** {gloss}"

    extras = [cards.gate_card(verdict)]
    carousel = cards.findings_carousel(verdict)
    if carousel:
        extras.append(carousel)

    await ctx.send(sender, _say(body, extras))
    await ctx.send(sender, _say(render_steps(result)))


async def do_metrics(ctx: Context, sender: str) -> None:
    rs = learn.latest_promoted()
    held = learn.evaluate(rs, "heldout")
    dev = learn.evaluate(rs, "dev")
    text = (
        f"### Scorecard — ruleset `{rs.version}`\n\n"
        f"**Held-out split** (never used to write or tune a rule)\n\n> {held.line()}\n\n"
        f"**Dev split** (what I am allowed to learn from)\n\n> {dev.line()}\n\n"
        f"Active rules: {', '.join(rs.enabled)}\n\n"
        "A clean file that gets flagged counts against me; that is the number I refuse to let rise."
    )
    await ctx.send(sender, _say(text))


async def do_learn(ctx: Context, sender: str) -> None:
    rs = learn.latest_promoted()
    await ctx.send(
        sender,
        _say(
            f"Looking for defect classes ruleset `{rs.version}` misses on the dev split, "
            "then testing any fix against the held-out split."
        ),
    )

    ms = learn.misses(rs, "dev")
    if not ms:
        held = learn.evaluate(rs, "heldout")
        await ctx.send(
            sender,
            _say(
                f"No misses left on the dev split for `{rs.version}`.\n\nHeld-out: {held.line()}\n\n"
                "To keep improving I need new labelled cases — add them under `corpus/cases/` "
                "with labels in `corpus/labels.json`."
            ),
        )
        return

    missed = set().union(*[m for _, m in ms])
    lines = "\n".join(f"- `{cid}` → missed {', '.join(sorted(m))}" for cid, m in ms)
    candidate = learn.propose_enabling(missed, rs)
    report = learn.consider(candidate, rs)

    text = (
        f"### Improvement cycle\n\n**Misses on the dev split**\n{lines}\n\n"
        f"**Candidate `{candidate.version}`** — {candidate.notes}\n\n"
        f"```\n{report.render()}\n```\n\n"
    )
    if report.promoted:
        learn.save_ruleset(candidate)
        text += (
            f"Promoted. `{candidate.version}` is now active because it beat `{rs.version}` "
            "on cases it had never seen. If it had not, it would have been discarded."
        )
    else:
        text += "Rejected, so the ruleset is unchanged. A proposal that cannot prove itself does not ship."

    await ctx.send(sender, _say(text, [cards.learning_card(report)]))


async def do_rules(ctx: Context, sender: str) -> None:
    from codegate.detectors import REGISTRY

    rs = learn.latest_promoted()
    rows = []
    for rid, rule in REGISTRY.items():
        on = "on" if rid in rs.enabled_set else "off"
        prove = "executable probe" if rule.probe else "static only, cannot block"
        rows.append(f"| `{rid}` | {on} | {rule.severity.value} | {prove} | {rule.title} |")
    body = (
        f"### Ruleset `{rs.version}`\n\n{rs.notes}\n\n"
        "| rule | state | severity | how it proves itself | what it catches |\n"
        "|---|---|---|---|---|\n" + "\n".join(rows)
    )
    await ctx.send(sender, _say(body))


async def handle_card_action(ctx: Context, sender: str, selection: dict) -> bool:
    """CTA presses from the review, carousel and detail cards."""
    action = str(selection.get("action", ""))
    result = SESSIONS.get(sender)

    if action in ("evidence", "probe"):
        if not result:
            await ctx.send(sender, _say("That review has expired — send me the code again."))
            return True
        v = result.verdict
        ordered = v.observed + v.inferred + v.refuted
        try:
            idx = int(selection.get("index", 0))
            f = ordered[idx]
        except (ValueError, IndexError):
            await ctx.send(sender, _say("I no longer have that finding."))
            return True
        if action == "evidence":
            await ctx.send(sender, _say(f"Evidence for `{f.rule_id}`:", [cards.evidence_card(f, idx)]))
        else:
            src = f.evidence.probe_source if f.evidence else "(no probe was run)"
            await ctx.send(
                sender,
                _say(f"The probe I ran for `{f.rule_id}` — run it yourself:\n\n```python\n{src}\n```"),
            )
        return True

    if action == "findings":
        if not result:
            await ctx.send(sender, _say("That review has expired — send me the code again."))
            return True
        card = cards.findings_carousel(result.verdict)
        await ctx.send(sender, _say("Findings:", [card] if card else []))
        return True

    if action in ("override", "approve", "uphold", "block"):
        ref = selection.get("ref", "this change")
        if action == "override":
            await ctx.send(
                sender,
                _say(
                    f"Recorded: you are merging **{ref}** over a block.\n\n"
                    "An override on a defect I *reproduced* is the signal worth auditing — either "
                    "the probe modelled your code wrongly, or the risk was accepted deliberately."
                ),
            )
        elif action == "uphold":
            await ctx.send(sender, _say(f"Block upheld for **{ref}**. Fix the reproduced defects and resubmit."))
        elif action == "approve":
            await ctx.send(sender, _say(f"Approved **{ref}**. Nothing blocking was reproduced."))
        else:
            await ctx.send(sender, _say(f"Blocked **{ref}** on your judgement, not mine."))
        return True

    if action in ("keep_ruleset", "rollback"):
        version = str(selection.get("version", ""))
        if action == "rollback" and version:
            try:
                rs = learn.load_ruleset(version)
                rs.promoted = True
                learn.save_ruleset(rs)
                await ctx.send(sender, _say(f"Rolled back to `{version}`."))
            except FileNotFoundError:
                await ctx.send(sender, _say(f"No ruleset `{version}` on disk."))
        else:
            await ctx.send(sender, _say(f"Keeping `{version}`."))
        return True

    return False


# --------------------------------------------------------------------------- #
# chat protocol
# --------------------------------------------------------------------------- #


def _looks_like_code(text: str) -> bool:
    from codegate.sources import PR_URL, looks_like_diff, parses

    stripped = text.strip()
    if PR_URL.search(stripped) or "github.com" in stripped:
        return True
    if looks_like_diff(stripped):
        return True
    body = stripped[3:].rsplit("```", 1)[0] if stripped.startswith("```") else stripped
    return parses(body) and any(k in body for k in ("def ", "class ", "import ", "return "))


@protocol.on_message(ChatMessage)
async def handle_message(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(
        sender,
        ChatAcknowledgement(timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id),
    )

    text = ""
    for item in msg.content:
        if isinstance(item, TextContent):
            text += item.text
        elif isinstance(item, MetadataContent):
            response = extract_card_response(item)
            if response and response.selection:
                if await handle_card_action(ctx, sender, dict(response.selection)):
                    return
        elif isinstance(item, StartSessionContent):
            continue

    text = text.strip()
    if not text:
        await ctx.send(sender, _say(HELP))
        return

    try:
        intent = "review" if _looks_like_code(text) else llm.classify(text)
        if intent == "metrics":
            await do_metrics(ctx, sender)
        elif intent == "learn":
            await do_learn(ctx, sender)
        elif intent == "rules":
            await do_rules(ctx, sender)
        elif intent == "help":
            await ctx.send(sender, _say(HELP))
        else:
            await do_review(ctx, sender, text)
    except Exception as exc:
        ctx.logger.exception("request failed")
        await ctx.send(
            sender,
            _say(
                f"That request failed on my side: `{exc!r}`\n\n"
                "Nothing was approved or blocked as a result — a crash never produces a verdict. "
                "Try again, or send `help`."
            ),
        )


@protocol.on_message(ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


@agent.on_event("startup")
async def startup(ctx: Context):
    rs = learn.latest_promoted()
    ctx.logger.info(f"CodeGate ready at {agent.address}")
    ctx.logger.info(f"active ruleset {rs.version}: {', '.join(rs.enabled)}")
    ctx.logger.info(f"execution policy: {POLICY.mode} -- {POLICY.describe()}")
    for problem in POLICY.problems():
        ctx.logger.warning(problem)
    ctx.logger.info(f"ASI:One LLM: {'enabled' if llm.available() else 'absent (deterministic fallbacks)'}")


agent.include(protocol, publish_manifest=True)

if __name__ == "__main__":
    agent.run()
