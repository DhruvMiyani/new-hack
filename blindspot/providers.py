"""Small server-side client for SubQ Snap's Decisions API."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field


def _configured(name: str) -> str:
    value = os.getenv(name, "").strip()
    return "" if value.lower() in {"changeme", "paste-your-key-here", "your-api-key", "<your-api-key>"} else value


def subq_key() -> str | None:
    return _configured("SUBQ_API_KEY") or None


def noul(instructions: str, when_true: str, when_false: str) -> dict:
    return {
        "type": "noul",
        "instructions": instructions,
        "criteria": {"true": when_true, "false": when_false},
    }


def choice(instructions: str, options: dict[str, str]) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": options}


@dataclass
class Answer:
    name: str
    kind: str
    value: object
    probabilities: dict = field(default_factory=dict)


@dataclass
class DecisionResult:
    answers: dict[str, Answer] = field(default_factory=dict)
    input_tokens: int = 0
    error: str = ""
    output_tokens: int | None = None
    elapsed_ms: int = 0

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.answers)

    def get(self, name: str) -> Answer | None:
        return self.answers.get(name)


class SubQSnap:
    MAX_QUESTIONS = 128
    MAX_IMAGES = 4
    MAX_IMAGE_BYTES = 4 * 1024 * 1024
    MAX_TOTAL_IMAGE_BYTES = 8 * 1024 * 1024
    MAX_BODY_BYTES = 13 * 1024 * 1024

    def __init__(self, model: str | None = None, url: str | None = None):
        self.model = model or _configured("SUBQ_MODEL") or "subq-snap-1.1"
        base_url = _configured("SUBQ_BASE_URL").rstrip("/")
        self.url = url or (f"{base_url}/systemone" if base_url else "")
        self.key = subq_key()

    @property
    def available(self) -> bool:
        return bool(self.url and self.key)

    def decide(
        self,
        state: str | dict | list,
        questions: dict[str, dict],
        timeout: float = 22.0,
        images: list[dict] | None = None,
    ) -> DecisionResult:
        if not 1 <= len(questions) <= self.MAX_QUESTIONS:
            return DecisionResult(error="Snap accepts 1 to 128 questions per request.")
        sizes = [len(image["base64"].rstrip("=")) * 3 // 4 for image in images or ()]
        if len(sizes) > self.MAX_IMAGES or any(size > self.MAX_IMAGE_BYTES for size in sizes):
            return DecisionResult(error="Snap accepts up to four images at 4 MiB each.")
        if sum(sizes) > self.MAX_TOTAL_IMAGE_BYTES:
            return DecisionResult(error="Snap accepts up to 8 MiB of decoded images per request.")
        if not self.available:
            return DecisionResult(error="SubQ credentials or base URL are missing in the local environment.")

        payload = {"model": self.model, "state": state, "questions": questions}
        if images:
            payload["images"] = images
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        if len(body) > self.MAX_BODY_BYTES:
            return DecisionResult(error="The Snap request exceeds 13 MiB.")
        request = urllib.request.Request(
            self.url, data=body, method="POST",
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read(2 * 1024 * 1024))
        except urllib.error.HTTPError as exc:
            exc.close()
            return DecisionResult(error=f"Snap returned HTTP {exc.code}.")
        except (OSError, ValueError):
            return DecisionResult(error="Snap was unreachable or returned invalid JSON.")

        elapsed_ms = round((time.perf_counter() - started) * 1000)
        if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
            return DecisionResult(error="Snap returned an invalid answer envelope.")
        answers = {}
        for name, item in result["answers"].items():
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind in ("noul", "choice"):
                probabilities = item.get("probabilities")
                answers[name] = Answer(
                    name, kind, item.get(kind),
                    probabilities if isinstance(probabilities, dict) else {},
                )
        usage = result.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        return DecisionResult(
            answers=answers,
            input_tokens=input_tokens if type(input_tokens) is int and input_tokens >= 0 else 0,
            output_tokens=output_tokens if type(output_tokens) is int and output_tokens >= 0 else None,
            elapsed_ms=elapsed_ms,
        )
