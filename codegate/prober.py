"""Promotes static matches into reproduced defects by executing probes."""

from __future__ import annotations

import json
import re

from .detectors import REGISTRY, Scanner
from .models import Evidence, Finding, Status
from .sandbox import run_probe

RESULT_RE = re.compile(r"::PROBE_RESULT::(\{.*\})")


def scan(source: str, path: str = "submitted.py", enabled: set[str] | None = None) -> list[Finding]:
    """Static pass only. Raises SyntaxError if the code does not parse."""
    return Scanner(source, path).run(enabled)


def _parse_result(stdout: str) -> tuple[bool, str]:
    match = None
    for match in RESULT_RE.finditer(stdout):
        pass
    if not match:
        return False, "probe produced no result marker"
    try:
        payload = json.loads(match.group(1))
        return bool(payload.get("reproduced")), str(payload.get("detail", ""))
    except json.JSONDecodeError:
        return False, "probe result was not valid JSON"


def _clean(stdout: str) -> str:
    """Strip the machine marker so the user sees only the human-readable log."""
    return RESULT_RE.sub("", stdout).strip()


def verify(
    findings: list[Finding],
    source: str,
    timeout: float = 12.0,
    probe_config: dict | None = None,
) -> list[Finding]:
    """Run each finding's probe and set OBSERVED / REFUTED / INFERRED.

    A probe that cannot be built, or that errors out, leaves the finding
    INFERRED -- we never upgrade a claim we failed to demonstrate.
    """
    for f in findings:
        spec = REGISTRY.get(f.rule_id)
        if spec is None or spec.probe is None:
            f.probe_unavailable_reason = "rule has no executable probe; static judgement only"
            continue
        if not f.target.get("function"):
            f.probe_unavailable_reason = "defect is not inside a callable we can drive"
            continue

        try:
            probe_src = spec.probe(f, (probe_config or {}).get(f.rule_id, {}))
        except Exception as exc:  # a broken template must not sink the scan
            f.probe_unavailable_reason = f"probe could not be built: {exc!r}"
            continue

        run = run_probe(source, probe_src, timeout=timeout)
        reproduced, detail = _parse_result(run.stdout)
        f.evidence = Evidence(
            probe_source=probe_src,
            stdout=_clean(run.stdout),
            stderr=run.stderr.strip(),
            exit_code=run.exit_code,
            duration_ms=run.duration_ms,
            reproduced=reproduced,
            detail=detail,
            truncated=run.truncated,
        )
        if run.timed_out:
            f.status = Status.INFERRED
            f.probe_unavailable_reason = "probe exceeded its time budget; result inconclusive"
        elif reproduced:
            f.status = Status.OBSERVED
        else:
            f.status = Status.REFUTED
    return findings


def analyse(
    source: str,
    path: str = "submitted.py",
    run_probes: bool = True,
    enabled: set[str] | None = None,
    probe_config: dict | None = None,
) -> list[Finding]:
    findings = scan(source, path, enabled)
    if run_probes:
        verify(findings, source, probe_config=probe_config)
    return findings
