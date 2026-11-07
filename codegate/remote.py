"""Delegate probe execution to the prover agent, with a local fallback.

A failed tool call must not turn into a wrong verdict. If the prover is
unreachable or errors, we run the probe in-process and say which path produced
each piece of evidence.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from .detectors import REGISTRY
from .models import Evidence, Finding, Status
from .prober import _clean, _parse_result
from .protocol import ProbeRequest, ProbeResponse
from .sandbox import run_probe


@dataclass
class ExecReport:
    remote: int = 0
    local_fallback: int = 0
    skipped: int = 0
    notes: list[str] = field(default_factory=list)

    def line(self) -> str:
        bits = []
        if self.remote:
            bits.append(f"{self.remote} on the prover agent")
        if self.local_fallback:
            bits.append(f"{self.local_fallback} locally after the prover failed")
        if self.skipped:
            bits.append(f"{self.skipped} skipped")
        return ", ".join(bits) or "no probes to run"


def _apply(f: Finding, probe_src: str, *, reproduced, detail, stdout, stderr, exit_code, duration_ms, truncated):
    f.evidence = Evidence(
        probe_source=probe_src,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        duration_ms=duration_ms,
        reproduced=reproduced,
        detail=detail,
        truncated=truncated,
    )
    f.status = Status.OBSERVED if reproduced else Status.REFUTED


def _run_local(f: Finding, probe_src: str, source: str, timeout: float) -> None:
    run = run_probe(source, probe_src, timeout=timeout)
    reproduced, detail = _parse_result(run.stdout)
    _apply(
        f, probe_src,
        reproduced=reproduced, detail=detail,
        stdout=_clean(run.stdout), stderr=run.stderr.strip(),
        exit_code=run.exit_code, duration_ms=run.duration_ms, truncated=run.truncated,
    )


async def verify_via_prover(
    findings: list[Finding],
    source: str,
    ctx,
    prover_address: str | None,
    probe_config: dict | None = None,
    timeout: float = 12.0,
    allow_local_fallback: bool = True,
) -> ExecReport:
    report = ExecReport()
    probe_config = probe_config or {}

    for f in findings:
        spec = REGISTRY.get(f.rule_id)
        if spec is None or spec.probe is None:
            f.probe_unavailable_reason = "rule has no executable probe; static judgement only"
            report.skipped += 1
            continue
        if not f.target.get("function"):
            f.probe_unavailable_reason = "defect is not inside a callable we can drive"
            report.skipped += 1
            continue
        try:
            probe_src = spec.probe(f, probe_config.get(f.rule_id, {}))
        except Exception as exc:
            f.probe_unavailable_reason = f"probe could not be built: {exc!r}"
            report.skipped += 1
            continue

        if prover_address:
            req = ProbeRequest(
                request_id=uuid.uuid4().hex[:12],
                rule_id=f.rule_id,
                target_source=source,
                probe_source=probe_src,
                timeout=timeout,
            )
            try:
                reply, status = await ctx.send_and_receive(
                    prover_address, req, response_type=ProbeResponse, timeout=int(timeout) + 8
                )
            except Exception as exc:
                reply, status = None, repr(exc)

            if isinstance(reply, ProbeResponse) and not reply.error:
                _apply(
                    f, probe_src,
                    reproduced=reply.reproduced, detail=reply.detail,
                    stdout=reply.stdout, stderr=reply.stderr,
                    exit_code=reply.exit_code, duration_ms=reply.duration_ms,
                    truncated=reply.truncated,
                )
                report.remote += 1
                continue

            why = reply.error if isinstance(reply, ProbeResponse) else str(status)
            if not allow_local_fallback:
                f.probe_unavailable_reason = (
                    f"prover unavailable ({why[:120]}) and this deployment forbids running "
                    "submitted code in the gate process, so the claim stays unproven"
                )
                report.skipped += 1
                report.notes.append(f"{f.rule_id}: prover unreachable, left unproven by policy")
                continue
            report.notes.append(f"{f.rule_id}: prover unavailable ({why[:90]}), ran locally instead")

        if not allow_local_fallback and not prover_address:
            f.probe_unavailable_reason = "no prover configured and local execution is disabled"
            report.skipped += 1
            continue

        _run_local(f, probe_src, source, timeout)
        report.local_fallback += 1

    return report
