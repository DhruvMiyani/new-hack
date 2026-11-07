"""Messages between the gate agent and the prover agent.

Probe execution is split onto its own agent so that the component which runs
submitted code is isolated from the chat-facing one, and can be deployed
somewhere disposable.
"""

from __future__ import annotations

from uagents import Model


class ProbeRequest(Model):
    """Run one probe against one file."""

    request_id: str
    rule_id: str
    target_source: str
    probe_source: str
    timeout: float = 12.0


class ProbeResponse(Model):
    request_id: str
    rule_id: str
    reproduced: bool
    detail: str
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    truncated: bool = False
    error: str = ""


class HealthRequest(Model):
    ping: str = "ping"


class HealthResponse(Model):
    ok: bool
    agent: str
    detail: str = ""
