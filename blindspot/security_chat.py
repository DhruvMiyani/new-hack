"""ASI:One security workflow; all actions are local and evidence-producing."""
from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from .security import SCENARIOS, render_security, run_security

SESSIONS: dict[str, dict] = {}
MAX_SESSIONS = 200


def security_card(report, run_id):
    from uagents_core.contrib.protocols.chat.cards import (
        CtaAction, ReviewCardPayload, ReviewSummaryRow, create_card_content,
    )
    observations = report.repaired_observations if report.repaired_observations is not None else report.observations
    rows = [
        ReviewSummaryRow(label="Scope", value="Local security lab; no cloud changes"),
        ReviewSummaryRow(label="Scenario", value=report.scenario),
        ReviewSummaryRow(label="Frozen probes", value=str(len(observations))),
        ReviewSummaryRow(label="Failed checks", value=str(sum(not o.passed for o in observations))),
        ReviewSummaryRow(label="Contract fingerprint", value=report.contract_hash[:16]),
    ]
    return create_card_content(ReviewCardPayload(
        title=f"Security verification: {report.final_verdict}",
        summary_rows=rows,
        approve_cta=CtaAction(label="Replay checks", selection={"action": "security_replay", "run_id": run_id}),
        reject_cta=CtaAction(label="Verify a repair", selection={"action": "security_repair", "run_id": run_id}, primary=True),
    ), card_id=uuid4())


async def run_chat(ctx, sender, say, *, scenario="combined", repair=False, models=False):
    if scenario not in SCENARIOS:
        await ctx.send(sender, say("Choose a security scenario: " + ", ".join(SCENARIOS)))
        return
    await ctx.send(sender, say(
        f"Checking **{scenario}** in the local object-storage/permissions lab. "
        + ("SubQ will rank evidence; Meta will propose a repair if requested. " if models
           else "Offline mode: no API calls. ")
        + "Every verdict comes from executed, frozen probes, including legitimate-access controls. No cloud changes."
    ))
    report = await asyncio.to_thread(run_security, scenario, repair=repair, use_models=models)
    if len(SESSIONS) >= MAX_SESSIONS:
        SESSIONS.pop(next(iter(SESSIONS)))
    run_id = uuid4().hex
    SESSIONS[sender] = {"run_id": run_id, "scenario": scenario, "models": models}
    await ctx.send(sender, say(render_security(report), [security_card(report, run_id)]))


async def handle_security_action(ctx, sender, selection, say):
    action = selection.get("action")
    if action not in ("security_replay", "security_repair"):
        return False
    session = SESSIONS.get(sender)
    if not session or session["run_id"] != selection.get("run_id"):
        await ctx.send(sender, say("That security run has expired. Send `security` to run it again."))
        return True
    await run_chat(ctx, sender, say, scenario=session["scenario"],
                   repair=action == "security_repair", models=session["models"])
    return True


async def handle_security_text(ctx, sender, text, say):
    # Direct @mention card selections can arrive as JSON TextContent.
    try:
        selection = json.loads(text)
    except (ValueError, TypeError):
        selection = None
    if isinstance(selection, dict) and await handle_security_action(ctx, sender, selection, say):
        return True
    words = text.lower().split()
    if not words or words[0] != "security":
        return False
    known = set(SCENARIOS) | {"security", "repair", "models", "offline"}
    if set(words) - known or ("models" in words and "offline" in words):
        await ctx.send(sender, say("Use `security [combined|public-storage|privilege-escalation|revoked-access|clean] [repair] [models|offline]`."))
        return True
    scenarios = [word for word in words if word in SCENARIOS]
    if len(scenarios) > 1:
        await ctx.send(sender, say("Choose one scenario per security run."))
        return True
    await run_chat(ctx, sender, say, scenario=scenarios[0] if scenarios else "combined",
                   repair="repair" in words, models="models" in words)
    return True
