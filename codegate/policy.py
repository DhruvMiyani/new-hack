"""Execution policy.

This agent's whole value is that it *runs* submitted code -- that is what
separates a reproduced defect from a guess. But once the agent is registered on
Agentverse it is reachable by strangers, and "run code any stranger sends me"
is a remote code execution service, not a feature.

So execution is opt-in and the operator has to say so out loud. The default is
static-only: the agent still reviews, but every finding comes back INFERRED and
nothing can block, because nothing was proven.

    off     never execute. Static analysis only. The safe default.
    local   execute probes in-process, in the sandbox. For your own machine.
    prover  execute only on the remote prover agent, never in this process.
            Point PROVER_ADDRESS at a prover running in a disposable container.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

VALID = ("off", "local", "prover")

OFF_NOTICE = (
    "Probe execution is disabled on this deployment (`CODEGATE_EXEC_POLICY=off`), so "
    "nothing below was reproduced and nothing can block. The operator enables execution "
    "by setting `CODEGATE_EXEC_POLICY=local` on a trusted machine, or `prover` with a "
    "`PROVER_ADDRESS` pointing at a prover in a disposable container."
)


@dataclass
class Policy:
    mode: str
    prover_address: str | None
    allow_public: bool

    @property
    def executes(self) -> bool:
        return self.mode in ("local", "prover")

    @property
    def may_run_locally(self) -> bool:
        """Falling back to in-process execution is only allowed in local mode."""
        return self.mode == "local"

    def describe(self) -> str:
        if self.mode == "off":
            return "static analysis only (execution disabled)"
        if self.mode == "prover":
            where = self.prover_address[:18] + "..." if self.prover_address else "UNSET"
            return f"probes execute on the prover agent only ({where}); no local fallback"
        return "probes execute in-process in the sandbox"

    def problems(self) -> list[str]:
        out = []
        if self.mode == "prover" and not self.prover_address:
            out.append(
                "CODEGATE_EXEC_POLICY=prover but PROVER_ADDRESS is unset; "
                "no probes can run until you set it."
            )
        if self.mode == "local" and not self.allow_public:
            out.append(
                "CODEGATE_EXEC_POLICY=local executes code sent by anyone who can reach this "
                "agent. Keep it to a machine you can throw away, or switch to prover mode."
            )
        return out


def from_env() -> Policy:
    mode = (os.getenv("CODEGATE_EXEC_POLICY") or "off").strip().lower()
    if mode not in VALID:
        mode = "off"
    return Policy(
        mode=mode,
        prover_address=(os.getenv("PROVER_ADDRESS") or "").strip() or None,
        allow_public=(os.getenv("CODEGATE_ACK_PUBLIC_EXEC") or "").lower() == "true",
    )
