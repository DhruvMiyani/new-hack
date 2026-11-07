"""Prover agent: executes probes and reports verbatim what happened.

Deliberately dumb. It holds no opinion about whether a change should merge --
it runs a probe and returns the output. Keeping it separate means the component
that executes submitted code can live in a disposable container, away from the
chat-facing agent.
"""

import os

from dotenv import load_dotenv
from uagents import Agent, Context

from codegate.prober import _clean, _parse_result
from codegate.protocol import HealthRequest, HealthResponse, ProbeRequest, ProbeResponse
from codegate.sandbox import run_probe

load_dotenv()

PORT = int(os.getenv("PROVER_PORT", "8002"))
USE_MAILBOX = os.getenv("PROVER_MAILBOX", "false").lower() == "true"

# Without a mailbox the prover needs an explicit endpoint, or it registers as
# unreachable and the gate's send_and_receive can never find it.
ENDPOINT = os.getenv("PROVER_ENDPOINT") or f"http://127.0.0.1:{PORT}/submit"

agent = Agent(
    name=os.getenv("PROVER_NAME", "codegate-prover"),
    seed=os.environ["PROVER_SEED"],
    port=PORT,
    mailbox=USE_MAILBOX,
    endpoint=None if USE_MAILBOX else [ENDPOINT],
    publish_agent_details=False,
)


@agent.on_message(ProbeRequest, replies={ProbeResponse})
async def on_probe(ctx: Context, sender: str, msg: ProbeRequest):
    ctx.logger.info(f"probe {msg.rule_id} for {sender[:16]}... ({msg.request_id})")
    try:
        run = run_probe(msg.target_source, msg.probe_source, timeout=msg.timeout)
        reproduced, detail = _parse_result(run.stdout)
        await ctx.send(
            sender,
            ProbeResponse(
                request_id=msg.request_id,
                rule_id=msg.rule_id,
                reproduced=reproduced,
                detail=detail,
                stdout=_clean(run.stdout),
                stderr=run.stderr.strip(),
                exit_code=run.exit_code,
                duration_ms=run.duration_ms,
                truncated=run.truncated,
            ),
        )
    except Exception as exc:
        ctx.logger.exception("probe failed")
        await ctx.send(
            sender,
            ProbeResponse(
                request_id=msg.request_id,
                rule_id=msg.rule_id,
                reproduced=False,
                detail="prover could not run the probe",
                stdout="",
                stderr="",
                exit_code=-1,
                duration_ms=0,
                error=repr(exc),
            ),
        )


@agent.on_message(HealthRequest, replies={HealthResponse})
async def on_health(ctx: Context, sender: str, msg: HealthRequest):
    await ctx.send(sender, HealthResponse(ok=True, agent=agent.name, detail="prover ready"))


@agent.on_event("startup")
async def startup(ctx: Context):
    ctx.logger.info(f"prover address: {agent.address}")
    ctx.logger.info(f"reachable at: {'mailbox' if USE_MAILBOX else ENDPOINT}")
    ctx.logger.info("set PROVER_ADDRESS to this value in .env so the gate agent can reach it")


if __name__ == "__main__":
    agent.run()
