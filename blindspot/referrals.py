"""Local, synthetic neurology intake simulator and Snap decision adapter."""
from __future__ import annotations

import base64
import math
import re
import subprocess
import threading
from functools import lru_cache
from pathlib import Path

from .providers import SubQSnap, choice, noul

ASSETS = Path(__file__).resolve().parents[1] / "web" / "dist" / "referral-assets"
CASES = {
    "R101": {"clinic": "North Clinic", "received": "08:41", "report": "scan", "lab": "result"},
    "R102": {"clinic": "Cedar Clinic", "received": "08:47", "report": "text", "lab": "order"},
    "R103": {"clinic": "Harbor Clinic", "received": "09:03", "report": "text", "lab": "order"},
    "R104": {"clinic": "Valley Clinic", "received": "09:12", "report": "text", "lab": "result"},
    "R105": {"clinic": "Maple Clinic", "received": "09:19", "report": "missing", "lab": "order"},
}
LABELS = {
    "critical": "Critical — clinician escalation, no booking",
    "high": "High — priority appointment after reviewer approval",
    "medium": "Medium — standard appointment after reviewer approval",
    "low": "Low — routine appointment after reviewer approval",
    "needs_review": "Needs review — required signed report is missing, unreadable, or inconsistent",
}
POLICY = (
    "Fictional clinic protocol. A signed reviewer routing instruction overrides intake priority. "
    "A signed CRITICAL instruction goes to critical clinician review, not appointment booking. "
    "Signed HIGH, MEDIUM, or LOW instructions go to those queues. "
    "Without a legible signed instruction for this case, choose needs_review. "
    "Never infer urgency from anatomy, lab values, a lab order, or an unsigned referral. "
    "A lab order is not a completed result. Do not give medical advice."
)
SLOTS = {
    "high": ("Mon · 09:00 · Priority", "Mon · 11:30 · Priority"),
    "medium": ("Wed · 10:30 · Standard", "Wed · 14:00 · Standard"),
    "low": ("Fri · 13:30 · Routine", "Fri · 15:00 · Routine"),
}
_LOCK = threading.Lock()
_REVIEWS: dict[str, dict] = {}


def _case(identifier):
    if not isinstance(identifier, str) or identifier not in CASES:
        raise ValueError("Choose a fictional demo referral.")
    return CASES[identifier]


def inbox():
    with _LOCK:
        reviews = dict(_REVIEWS)
    return {
        "cases": [
            {"id": identifier, "clinic": info["clinic"], "received": info["received"],
             "review": reviews.get(identifier), "scanned": info["report"] == "scan"}
            for identifier, info in CASES.items()
        ],
        "policy": POLICY,
        "mri_available": (ASSETS / "brainweb-local.png").is_file(),
        "model_available": SubQSnap().available,
    }


def _extract(command, error):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=12, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(error) from exc
    return result.stdout


@lru_cache(maxsize=5)
def pages(identifier):
    _case(identifier)
    source = _extract(
        ["pdftotext", "-layout", str(ASSETS / f"{identifier}.pdf"), "-"],
        "Install Poppler to extract text from the fictional PDF.",
    )
    return tuple(page.strip() for page in source.split("\f") if page.strip())


@lru_cache(maxsize=1)
def scanned_text():
    return _extract(
        ["tesseract", str(ASSETS / "R101-addendum.png"), "stdout", "--psm", "6"],
        "Install Tesseract to view the scanned report transcript.",
    ).strip()


def document(data):
    identifier = data.get("id")
    info = _case(identifier)
    result = {"pages": list(pages(identifier)), "ocr": None}
    if info["report"] == "scan":
        try:
            result["ocr"] = scanned_text()
        except ValueError as exc:
            result["ocr_error"] = str(exc)
    return result


def _distribution(answer, options):
    if not answer or answer.kind != "choice" or answer.value not in options:
        return None
    values = answer.probabilities
    if set(values) != set(options) or any(
        type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1
        for p in values.values()
    ):
        return None
    return dict(values)


def assess(data):
    identifier = data.get("id")
    info = _case(identifier)
    scope = data.get("scope")
    if scope not in ("letter", "packet"):
        raise ValueError("Choose letter only or full packet.")
    text = pages(identifier)
    chosen = text[:1] if scope == "letter" else text
    evidence = {f"P{i}": f"Page {i} of the attached PDF" for i in range(1, len(chosen) + 1)}
    images = None
    if scope == "packet" and info["report"] == "scan":
        image = ASSETS / "R101-addendum.png"
        images = [{"mediaType": "image/png", "base64": base64.b64encode(image.read_bytes()).decode()}]
        evidence["SCAN"] = "Signed radiology addendum image associated with PDF page 2"
    evidence["NONE"] = "No supplied source establishes a signed routing instruction"
    lab_options = {"result": "A final laboratory result is supplied",
                   "order": "A lab order is supplied, with no final result",
                   "missing": "No lab document is supplied"}
    questions = {
        "route": choice("Select the route supported by the fictional clinic protocol.", LABELS),
        "source": choice("Which labelled source contains the decisive signed routing instruction? Use NONE if absent.", evidence),
        "lab": choice("Distinguish a laboratory order from a final laboratory result in the supplied evidence.", lab_options),
        "complete": noul("Is a signed routing instruction for this case actually readable in the provided sources?",
                         "A signed instruction is supplied and legible", "It is absent or unclear"),
    }
    snap = SubQSnap()
    state = {"policy": POLICY, "case": identifier,
             "pages": {f"P{i}": page for i, page in enumerate(chosen, 1)},
             "image_label": "SCAN is the signed addendum image from page 2." if images else "No images."}
    result = snap.decide(state, questions, timeout=22, images=images)
    route, source, lab = (result.get(key) for key in ("route", "source", "lab"))
    distribution = _distribution(route, LABELS)
    complete = result.get("complete")
    if not (result.ok and distribution and _distribution(source, evidence)
            and _distribution(lab, lab_options) and complete and complete.kind == "noul"
            and type(complete.value) in (int, float) and math.isfinite(complete.value)
            and 0 <= complete.value <= 1):
        return {"available": False, "message": "Live Snap decision unavailable. Review the source packet manually."}
    return {
        "available": True, "scope": scope, "route": route.value,
        "probabilities": distribution, "source": source.value, "lab": lab.value,
        "complete": complete.value, "input_tokens": result.input_tokens, "model": snap.model,
        "pages": len(chosen), "images": len(images or []),
    }


def _verified_route(identifier):
    info = _case(identifier)
    if info["report"] == "missing":
        return "needs_review"
    try:
        text = scanned_text() if info["report"] == "scan" else pages(identifier)[1]
    except ValueError:
        return "needs_review"
    match = re.search(r"Routing instruction:\s*(CRITICAL|HIGH|MEDIUM|LOW)\b", text, re.I)
    return match.group(1).lower() if match and "status: signed" in text.lower() else "needs_review"


def review(data):
    identifier = data.get("id")
    _case(identifier)
    route = data.get("route")
    if not isinstance(route, str) or route not in LABELS:
        raise ValueError("Select a valid reviewer disposition.")
    if data.get("confirmed") is not True:
        raise ValueError("Confirm that you checked the source before routing.")
    verified = _verified_route(identifier)
    order = ("low", "medium", "high", "critical")
    if verified == "critical" and route != "critical":
        raise ValueError("The signed critical flag must go to the clinician queue.")
    if verified == "needs_review" and route not in ("needs_review", "critical"):
        raise ValueError("Booking is blocked until a legible signed report is available.")
    if verified in order and route in order and order.index(route) < order.index(verified):
        raise ValueError("The demo blocks lowering a signed report's priority.")
    reason = data.get("reason", "")
    if not isinstance(reason, str) or len(reason) > 240:
        raise ValueError("Use a short reviewer note (240 characters maximum).")
    if route != verified and not reason.strip():
        raise ValueError("Add a reviewer note when changing the verified route.")
    with _LOCK:
        if identifier in _REVIEWS:
            return _REVIEWS[identifier]
        booked = {entry["slot"] for entry in _REVIEWS.values() if entry["slot"]}
        slot = next((slot for slot in SLOTS.get(route, ()) if slot not in booked), None)
        if route in SLOTS and slot is None:
            raise ValueError("No slot remains in that demo queue. Choose Needs review and add a note.")
        record = {"id": identifier, "route": route, "verified_route": verified, "slot": slot,
                  "status": "Escalated to demo clinician queue" if route == "critical"
                            else "Held for missing evidence" if route == "needs_review"
                            else "Booked in simulated calendar", "reason": reason.strip()}
        _REVIEWS[identifier] = record
    return record


def reset():
    with _LOCK:
        _REVIEWS.clear()
    return inbox()
