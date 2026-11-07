"""Core data model.

The central discipline of this agent: a claim is either OBSERVED (we executed
something and captured the failure) or INFERRED (pattern match only). The two
are never merged, and only OBSERVED findings are allowed to block a merge.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class Status(str, Enum):
    OBSERVED = "OBSERVED"    # reproduced by executing a probe; evidence attached
    INFERRED = "INFERRED"    # pattern matched, not reproduced
    REFUTED = "REFUTED"      # probe ran and the defect did NOT manifest


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def rank(self) -> int:
        return {"critical": 4, "high": 3, "medium": 2, "low": 1}[self.value]


class Decision(str, Enum):
    BLOCK = "BLOCK"
    REVIEW = "REVIEW"
    ALLOW = "ALLOW"


@dataclass
class Evidence:
    """What actually happened when we ran the probe. Verbatim, not summarised."""

    probe_source: str
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    reproduced: bool
    detail: str = ""
    truncated: bool = False

    def digest(self) -> str:
        """Content hash so a verdict can be pinned to the exact evidence."""
        blob = json.dumps(
            {
                "probe": self.probe_source,
                "stdout": self.stdout,
                "stderr": self.stderr,
                "exit": self.exit_code,
            },
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


@dataclass
class Finding:
    rule_id: str
    title: str
    severity: Severity
    path: str
    line: int
    snippet: str
    rationale: str
    status: Status = Status.INFERRED
    target: dict[str, Any] = field(default_factory=dict)
    evidence: Optional[Evidence] = None
    probe_unavailable_reason: str = ""

    @property
    def blocking(self) -> bool:
        """Only reproduced, serious defects may block. Suspicion is not proof."""
        return self.status is Status.OBSERVED and self.severity.rank >= Severity.HIGH.rank

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        d["status"] = self.status.value
        if self.evidence:
            d["evidence_digest"] = self.evidence.digest()
        return d


@dataclass
class Verdict:
    decision: Decision
    findings: list[Finding]
    summary: str
    probes_run: int = 0
    probes_reproduced: int = 0
    source_ref: str = ""
    ruleset_version: str = ""

    @property
    def observed(self) -> list[Finding]:
        return [f for f in self.findings if f.status is Status.OBSERVED]

    @property
    def inferred(self) -> list[Finding]:
        return [f for f in self.findings if f.status is Status.INFERRED]

    @property
    def refuted(self) -> list[Finding]:
        return [f for f in self.findings if f.status is Status.REFUTED]

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "summary": self.summary,
            "source_ref": self.source_ref,
            "ruleset_version": self.ruleset_version,
            "probes_run": self.probes_run,
            "probes_reproduced": self.probes_reproduced,
            "findings": [f.to_dict() for f in self.findings],
        }
