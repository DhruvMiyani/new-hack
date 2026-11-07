"""Optional ASI:One assistance.

Deliberately narrow. The model routes intent and phrases explanations; it never
decides a verdict. Verdicts come from executed probes, so a hallucinating model
can make the agent sound wrong but cannot make it block or allow wrongly.
Without a key, every function degrades to a deterministic fallback.
"""

from __future__ import annotations

import json
import os

MODEL = os.getenv("ASI_ONE_MODEL", "asi1-mini")

_client = None


PLACEHOLDERS = {"", "paste-your-asi1-key-here", "<your-api-key>", "your-api-key", "changeme"}


def available() -> bool:
    """A placeholder left in .env is not a key -- say absent rather than enabled."""
    key = (os.getenv("ASI_ONE_API_KEY") or "").strip()
    return bool(key and os.getenv("ASI_ONE_BASE_URL", "").strip()) and key.lower() not in PLACEHOLDERS


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI

        _client = OpenAI(base_url=os.environ["ASI_ONE_BASE_URL"].strip(), api_key=os.environ["ASI_ONE_API_KEY"])
    return _client


def _chat(system: str, user: str, max_tokens: int = 500) -> str | None:
    if not available():
        return None
    try:
        r = _get_client().chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=max_tokens,
        )
        return (r.choices[0].message.content or "").strip()
    except Exception:
        return None


INTENTS = ("review", "metrics", "learn", "rules", "help")


def classify(text: str) -> str:
    """Route a message that is not obviously code. Falls back to keywords."""
    lowered = text.strip().lower()
    keyword = {
        "help": "help",
        "metrics": "metrics",
        "score": "metrics",
        "scorecard": "metrics",
        "accuracy": "metrics",
        "learn": "learn",
        "improve": "learn",
        "rules": "rules",
        "ruleset": "rules",
    }
    for k, v in keyword.items():
        if lowered.startswith(k) or lowered == k:
            return v

    answer = _chat(
        "Classify the user's request for a code-verification agent into exactly one "
        f"of: {', '.join(INTENTS)}. Reply with the single word only.",
        text[:1500],
    )
    if answer:
        word = answer.strip().split()[0].lower().strip(".,")
        if word in INTENTS:
            return word
    return "review" if len(text.strip()) > 40 else "help"


def explain(verdict_dict: dict) -> str | None:
    """A short plain-English gloss. Never introduces a new claim."""
    return _chat(
        "You explain a code-review verdict to an engineer deciding whether to merge. "
        "You are given the verdict as JSON. Write at most three sentences. Use only "
        "facts present in the JSON -- never invent a defect, a line number or a cause. "
        "Say plainly what to fix first. No preamble, no markdown headings.",
        json.dumps(verdict_dict)[:6000],
        max_tokens=250,
    )
