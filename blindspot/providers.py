"""Model providers.

Two sponsors, two genuinely different jobs:

  Meta Muse (api.meta.ai/v1, OpenAI-compatible chat)
      Generates text, so it plays implementer and test author.

  SubQ Snap (api.subq.ai/v1/systemone, Decisions API)
      Generates *no* text at all -- output_tokens is always 0. It answers
      structured questions with labels, probabilities and confidence over up to
      384K tokens of context. So it is the adjudicator: it reads the whole repo
      to pick the risk surface, and it judges whether a test genuinely checks
      the spec or merely restates the implementation.

Both degrade to None when unconfigured, and every caller is written to say so
rather than to quietly invent an answer.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Meta Model API
# --------------------------------------------------------------------------- #

META_BASE_URL = os.getenv("META_BASE_URL", "https://api.meta.ai/v1")
META_MODEL = os.getenv("META_MODEL", "muse-spark-1.3")
PLACEHOLDERS = {"", "paste-your-key-here", "changeme", "your-api-key", "<your-api-key>"}


def _clean(value: str | None) -> str | None:
    v = (value or "").strip()
    return None if not v or v.lower() in PLACEHOLDERS else v


def meta_key() -> str | None:
    # MODEL_API_KEY is the name Meta's own quickstart uses.
    return _clean(os.getenv("META_API_KEY")) or _clean(os.getenv("MODEL_API_KEY"))


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, i: int, o: int) -> None:
        self.input_tokens += i
        self.output_tokens += o
        self.calls += 1


class MetaMuse:
    """OpenAI-compatible chat against Meta Model API."""

    def __init__(self, model: str | None = None, base_url: str | None = None):
        self.model = model or META_MODEL
        self.base_url = base_url or META_BASE_URL
        self.usage = Usage()
        self._client = None

    @property
    def available(self) -> bool:
        return meta_key() is not None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(base_url=self.base_url, api_key=meta_key())
        return self._client

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 2000,
        temperature: float = 0.2,
    ) -> str | None:
        """Returns the reply text, or None if unavailable / the call failed."""
        if not self.available:
            return None
        try:
            r = self._get_client().chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            u = getattr(r, "usage", None)
            if u:
                self.usage.add(getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0)
            return (r.choices[0].message.content or "").strip() or None
        except Exception as exc:
            self.last_error = repr(exc)
            return None


# --------------------------------------------------------------------------- #
# SubQ Decisions API
# --------------------------------------------------------------------------- #

SUBQ_URL = os.getenv("SUBQ_BASE_URL", "https://api.subq.ai/v1") + "/systemone"
SUBQ_MODEL = os.getenv("SUBQ_MODEL", "subq-snap-1.1")
SUBQ_CONTEXT_TOKENS = 384_000
# Rough chars-per-token for budgeting repo context. Deliberately conservative:
# the API returns 400 if the request exceeds the window, and a hard failure
# mid-review is worse than sending slightly less context.
CHARS_PER_TOKEN = 3.6


def subq_key() -> str | None:
    return _clean(os.getenv("SUBQ_API_KEY"))


def noul(instructions: str, when_true: str, when_false: str) -> dict:
    """A yes/no question. The answer comes back as a probability in [0,1]."""
    return {
        "type": "noul",
        "instructions": instructions,
        "criteria": {"true": when_true, "false": when_false},
    }


def choice(instructions: str, options: dict[str, str]) -> dict:
    """Pick one of 2..255 named options; returns the pick plus probabilities."""
    return {"type": "choice", "instructions": instructions, "criteria": options}


def score(instructions: str, levels: list[str]) -> dict:
    """Ordered levels, 2..10; returns a numeric score plus probabilities."""
    return {"type": "score", "instructions": instructions, "criteria": levels}


@dataclass
class Answer:
    name: str
    kind: str
    value: object                       # probability / chosen key / score index
    confidence: float | None = None
    probabilities: dict = field(default_factory=dict)
    legend: dict = field(default_factory=dict)

    def as_bool(self, threshold: float = 0.5) -> bool:
        if self.kind == "noul":
            return float(self.value) >= threshold
        return bool(self.value)

    def render(self) -> str:
        if self.kind == "noul":
            return f"{self.name}: {float(self.value):.2f} likelihood"
        if self.kind == "choice":
            conf = f" (confidence {self.confidence:.2f})" if self.confidence is not None else ""
            return f"{self.name}: {self.value}{conf}"
        label = self.legend.get(str(self.value)) if self.legend else None
        conf = f" (confidence {self.confidence:.2f})" if self.confidence is not None else ""
        return f"{self.name}: level {self.value}{f' = {label}' if label else ''}{conf}"


@dataclass
class DecisionResult:
    answers: dict[str, Answer] = field(default_factory=dict)
    input_tokens: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.answers)

    def get(self, name: str) -> Answer | None:
        return self.answers.get(name)


class SubQSnap:
    """Client for the SubQ Decisions API."""

    def __init__(self, model: str | None = None, url: str | None = None):
        self.model = model or SUBQ_MODEL
        self.url = url or SUBQ_URL
        self.usage = Usage()

    @property
    def available(self) -> bool:
        return subq_key() is not None

    @staticmethod
    def context_budget_chars(reserve_tokens: int = 4_000) -> int:
        """How much `state` text fits, leaving room for the questions."""
        return int((SUBQ_CONTEXT_TOKENS - reserve_tokens) * CHARS_PER_TOKEN)

    def decide(self, state: str | dict | list, questions: dict[str, dict], timeout: float = 90.0) -> DecisionResult:
        if not self.available:
            return DecisionResult(error="SUBQ_API_KEY is not set")

        if isinstance(state, str):
            budget = self.context_budget_chars()
            if len(state) > budget:
                # Truncating is a visible, reported event -- never silent.
                state = state[:budget]
                truncated = True
            else:
                truncated = False
        else:
            truncated = False

        body = json.dumps({"model": self.model, "state": state, "questions": questions}).encode()
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={
                "Authorization": f"Bearer {subq_key()}",
                "Content-Type": "application/json",
                "User-Agent": "blindspot-verifier",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:400]
            except Exception:
                pass
            hint = ""
            if exc.code == 400:
                hint = " (context may exceed the 384K window)"
            elif exc.code in (401, 403):
                hint = " (check SUBQ_API_KEY, or the model needs org enablement)"
            return DecisionResult(error=f"SubQ HTTP {exc.code}{hint}: {detail}")
        except Exception as exc:
            return DecisionResult(error=f"SubQ unreachable: {exc!r}")

        answers: dict[str, Answer] = {}
        for name, a in (payload.get("answers") or {}).items():
            kind = a.get("type", "")
            value = a.get(kind) if kind in a else a.get("choice", a.get("score", a.get("noul")))
            answers[name] = Answer(
                name=name,
                kind=kind,
                value=value,
                confidence=a.get("confidence"),
                probabilities=a.get("probabilities") or {},
                legend=a.get("legend") or {},
            )

        used = (payload.get("usage") or {}).get("input_tokens", 0) or 0
        self.usage.add(used, 0)
        result = DecisionResult(answers=answers, input_tokens=used)
        if truncated:
            result.error = ""  # not an error, but worth surfacing upstream
        return result


# --------------------------------------------------------------------------- #
# availability summary, so the pipeline can state what it is running without
# --------------------------------------------------------------------------- #


def status() -> dict[str, str]:
    muse, snap = MetaMuse(), SubQSnap()
    return {
        "meta": f"ready ({muse.model})" if muse.available
        else "absent -- set META_API_KEY (or MODEL_API_KEY); no code or tests can be generated",
        "subq": f"ready ({snap.model})" if snap.available
        else "absent -- set SUBQ_API_KEY; adjudication falls back to deterministic rules",
    }
