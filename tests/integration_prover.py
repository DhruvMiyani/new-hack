"""Live check of the gate -> prover round trip.

Not part of the unit suite: it needs two processes, open ports and Almanac
resolution. Run it when you want to confirm the multi-agent path really works
rather than taking the fallback.

    terminal 1:  .venv/bin/python prover_agent.py
    terminal 2:  .venv/bin/python tests/integration_prover.py
"""

import functools
import os
import sys

from dotenv import load_dotenv
from uagents import Agent, Context
from uagents_core.identity import Identity

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codegate.protocol import HealthRequest, HealthResponse, ProbeRequest, ProbeResponse  # noqa: E402

load_dotenv()

print = functools.partial(__builtins__["print"] if isinstance(__builtins__, dict) else __builtins__.print, flush=True)

PROVER = os.getenv("PROVER_ADDRESS") or Identity.from_seed(os.environ["PROVER_SEED"], 0).address

TARGET = "def remember(name, log=[]):\n    log.append(name)\n    return log\n"
PROBE = (
    "import target, copy\n"
    "fn = target.remember\n"
    "before = copy.deepcopy(fn.__defaults__)\n"
    "drive(fn, require_success=True); drive(fn, require_success=True)\n"
    "after = fn.__defaults__\n"
    "print('before:', before)\n"
    "print('after :', after)\n"
    "report(before != after, 'shared default mutated: %r -> %r' % (before, after))\n"
)

client = Agent(
    name="codegate-integration-client",
    seed=os.getenv("CLIENT_SEED", "codegate-integration-client-seed-only-for-tests"),
    port=int(os.getenv("CLIENT_PORT", "8010")),
    endpoint=[f"http://127.0.0.1:{os.getenv('CLIENT_PORT', '8010')}/submit"],
)

FAILURES = []


@client.on_event("startup")
async def run(ctx: Context):
    print(f"\nprover: {PROVER}\n")

    health, status = await ctx.send_and_receive(PROVER, HealthRequest(), response_type=HealthResponse, timeout=25)
    if isinstance(health, HealthResponse) and health.ok:
        print(f"[ok]   health: {health.agent} -- {health.detail}")
    else:
        FAILURES.append(f"health check failed: {status}")
        print(f"[FAIL] health: {status}")

    reply, status = await ctx.send_and_receive(
        PROVER,
        ProbeRequest(request_id="itest-1", rule_id="CG001", target_source=TARGET, probe_source=PROBE),
        response_type=ProbeResponse,
        timeout=40,
    )
    if isinstance(reply, ProbeResponse):
        print(f"[ok]   probe executed remotely in {reply.duration_ms}ms")
        print(f"       reproduced: {reply.reproduced}")
        print(f"       detail    : {reply.detail}")
        print("       stdout    :")
        for line in reply.stdout.splitlines():
            print("         " + line)
        if not reply.reproduced:
            FAILURES.append("prover ran the probe but did not reproduce a known defect")
    else:
        FAILURES.append(f"no ProbeResponse: {status}")
        print(f"[FAIL] probe: {status}")

    print()
    sys.stdout.flush()
    if FAILURES:
        print("INTEGRATION FAILED")
        for f in FAILURES:
            print("  -", f)
    else:
        print("INTEGRATION PASSED -- the gate's probe path works over agent messaging")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(1 if FAILURES else 0)


if __name__ == "__main__":
    client.run()
