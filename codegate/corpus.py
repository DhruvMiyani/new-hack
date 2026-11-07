"""Labelled corpus used to measure the agent, split dev / held-out.

Held-out cases are never consulted when authoring or proposing a rule. They
exist only to answer one question: did the change actually make the agent
better, on work it had not seen?
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "corpus"


@dataclass
class Case:
    case_id: str
    path: Path
    split: str
    expect: set[str]
    source: str

    @property
    def is_clean(self) -> bool:
        return not self.expect


def load_cases(split: str | None = None) -> list[Case]:
    labels = json.loads((CORPUS_DIR / "labels.json").read_text())
    out = []
    for cid, meta in sorted(labels.items()):
        if split and meta["split"] != split:
            continue
        path = CORPUS_DIR / "cases" / meta["file"]
        out.append(
            Case(
                case_id=cid,
                path=path,
                split=meta["split"],
                expect=set(meta["expect"]),
                source=path.read_text(),
            )
        )
    return out


def split_counts() -> dict[str, int]:
    labels = json.loads((CORPUS_DIR / "labels.json").read_text())
    counts: dict[str, int] = {}
    for meta in labels.values():
        counts[meta["split"]] = counts.get(meta["split"], 0) + 1
    return counts
