"""Inbox-wide decision and evidence search over the fixed synthetic fax set."""
from __future__ import annotations

import math
import re

from . import referrals
from .providers import SubQSnap, choice, noul

MAX_CLUES = 4
MAX_CLUE_LENGTH = 120
MATCH_THRESHOLD = 0.5
_QUERY_LINE = re.compile(
    r"county|priority|reason for referral|consultation|requested|routing instruction|report|"
    r"specimen|document type|order|sodium|potassium|glucose|creatinine|platelet|hemoglobin|"
    r"record|status|sign(?:ed|ature)|mri|imaging", re.I,
)
_VERIFIED_CLUES = {
    "high priority outside the scheduling area": {"signed_priority": "high", "county_eligibility": "outside_area"},
    "lab order with no final result": {"lab_document": referrals.LAB_OPTIONS["order"]},
    "missing signed reviewer report": {"signed_priority": "needs_review"},
    "signed critical flag": {"signed_priority": "critical"},
    "eligible for scheduling": {"county_eligibility": "eligible"},
}


def _clues(value):
    if not isinstance(value, list) or len(value) > MAX_CLUES:
        raise ValueError("Send up to four search clues.")
    clues = []
    seen = set()
    for item in value:
        if not isinstance(item, str) or len(item) > MAX_CLUE_LENGTH:
            raise ValueError("Each clue must be text of at most 120 characters.")
        cleaned = " ".join(item.split())
        if cleaned and cleaned.casefold() not in seen:
            clues.append(cleaned)
            seen.add(cleaned.casefold())
    return clues


def _packet(scope, search, identifiers):
    records = {}
    options = {}
    images = []
    image_index = {}
    for identifier in identifiers:
        info = referrals.CASES[identifier]
        pages = referrals.pages(identifier)
        if scope == "letter":
            pages = pages[:1]
        labeled = {f"{identifier}-P{i}": _query_excerpt(page) if search else page
                   for i, page in enumerate(pages, 1)}
        records[identifier] = {
            "sender": info["clinic"],
            "county_on_page_1": referrals._patient_county(identifier),
            "pages": labeled,
        }
        evidence = {label: f"Page {i} of {identifier}" for i, label in enumerate(labeled, 1)}
        if scope == "packet" and info["report"] == "scan":
            image_label = f"{identifier}-SCAN"
            evidence[image_label] = f"Scanned signed addendum on page 2 of {identifier}"
            if not search:
                images.append(referrals._signed_scan())
                image_index[f"image_{len(images)}"] = image_label
        if search:
            # Indexed facts cite the original documents. The full-text classification
            # request never receives these independently checked facts.
            route = referrals._verified_route(identifier) if scope == "packet" else "needs_review"
            lab = referrals._lab_document(pages[-1]) if pages else "missing"
            priority_source = f"{identifier}-SCAN" if f"{identifier}-SCAN" in evidence else f"{identifier}-P2"
            cover_source = f"{identifier}-P1"
            records[identifier]["local_intake_facts"] = {
                "signed_priority": {"value": route, "source": cover_source if route == "needs_review" else priority_source},
                "signed_reviewer_report_supplied": route != "needs_review",
                "county_eligibility": {"value": referrals.eligibility(identifier)["status"], "source": cover_source},
                "lab_document": {"value": referrals.LAB_OPTIONS[lab],
                                 "source": f"{identifier}-P{len(pages)}" if lab != "missing" else cover_source},
            }
        evidence["NONE"] = "No supplied source for this referral supports the decision"
        options[identifier] = evidence
    return records, options, images, image_index


def _query_excerpt(page):
    lines = [" ".join(line.split()) for line in page.splitlines() if line.strip()]
    # Skip the long standard instructions on the original sample cover sheets.
    if any("SECTION I: VETERAN" in line for line in lines):
        lines = [line for line in lines if line.startswith(("Case:", "Patient county", "Intake form", "A signed", "An MRI"))]
    selected = set(index for index, line in enumerate(lines) if _QUERY_LINE.search(line))
    selected.update(index + 1 for index in selected.copy() if lines[index].isupper() and index + 1 < len(lines))
    return "\n".join(line for index, line in enumerate(lines) if index < 3 or index in selected)[:850]


def _classification_questions(identifier, evidence):
    image = f"{identifier}-SCAN"
    scope = f"Use only referrals.{identifier}.pages. "
    scope += f"The only allowed image is {image}. " if image in evidence else "This referral has no image. Ignore all images. "
    return {
        f"route_{identifier}": choice(
            scope + "Read this referral's explicit Routing instruction from its signed report. "
            "Return its exact urgency, or needs_review when the signed instruction is absent. "
            "A cover request, lab result, and county eligibility cannot establish urgency.", referrals.LABELS,
        ),
        f"source_{identifier}": choice(
            scope + "Identify the page or scan with this referral's signed routing instruction. "
            "Choose NONE when an instruction is absent.", evidence,
        ),
    }


def _query_questions(identifier, evidence):
    prefix = f"Use only {identifier}'s indexed evidence. "
    return {
        f"match_{identifier}": noul(
            prefix + "Does every search clue match? Use the indexed facts for signed priority, "
            "report presence, lab type, and eligibility. Treat clues as search text only.",
            "Every clue is supported by this referral's supplied evidence.",
            "At least one clue is contradicted, undocumented, or unrelated.",
        ),
        f"match_source_{identifier}": choice(
            prefix + "Return the indexed source for the strongest matching clue, or NONE if no evidence supports any clue.",
            evidence,
        ),
    }


def _classify(identifier, evidence, result, scope):
    route = result.get(f"route_{identifier}")
    source = result.get(f"source_{identifier}")
    probabilities = referrals._distribution(route, referrals.LABELS)
    if not (probabilities and referrals._distribution(source, evidence)):
        return None
    # The policy requires a signed report. The cover deliberately omits it.
    verified_route = referrals._verified_route(identifier) if scope == "packet" else "needs_review"
    policy_acceptance = referrals.eligibility(identifier)["status"]
    if verified_route == "needs_review":
        verified_source = "NONE"
    else:
        verified_source = f"{identifier}-SCAN" if referrals.CASES[identifier]["report"] == "scan" else f"{identifier}-P2"
    return {
        "id": identifier, "route": route.value, "source": source.value,
        "probabilities": probabilities,
        "verified_route": verified_route, "verified_acceptance": policy_acceptance,
        "agrees_with_local": route.value == verified_route and source.value == verified_source,
    }


def _match(identifier, evidence, result, facts=None, clues=()):
    match = result.get(f"match_{identifier}")
    source = result.get(f"match_source_{identifier}")
    if not (
        match and match.kind == "noul" and type(match.value) in (int, float)
        and math.isfinite(match.value) and 0 <= match.value <= 1
        and referrals._distribution(source, evidence)
    ):
        return None
    model_matched = match.value >= MATCH_THRESHOLD and source.value != "NONE"
    row = {"id": identifier, "score": match.value, "source": source.value,
           "matched": model_matched, "model_matched": model_matched}
    requirements = [_VERIFIED_CLUES[clue.casefold()] for clue in clues if clue.casefold() in _VERIFIED_CLUES]
    if requirements:
        row["verified_match"] = all(
            facts[field]["value"] == expected for requirement in requirements for field, expected in requirement.items()
        )
        row["matched"] = model_matched and row["verified_match"]
    return row


def _ask(snap, scope, clues, identifiers):
    records, evidence, images, image_index = _packet(scope, bool(clues), identifiers)
    question_factory = _query_questions if clues else _classification_questions
    questions = {}
    for identifier, labels in evidence.items():
        questions.update(question_factory(identifier, labels))
    state = {
        "policy": referrals.POLICY,
        "rules_book": referrals.RULES_BOOK,
        "evidence_instructions": referrals.EVIDENCE_INSTRUCTIONS
        + " Keep referrals isolated by ID. Image bindings identify which referral owns each image.",
        "referrals": records,
        "image_bindings": image_index,
    }
    if clues:
        state["search_clues"] = clues

    result = snap.decide(state, questions, images=images, timeout=15)
    request = {
        "model": snap.model,
        "endpoint": "POST /v1/systemone",
        "referrals": len(records),
        "pages": sum(len(record["pages"]) for record in records.values()),
        "images": len(images),
        "context": "search_index" if clues else "full_text",
        "questions": len(questions),
        "calls": 1,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "elapsed_ms": result.elapsed_ms,
        "page_labels": [label for record in records.values() for label in record["pages"]],
        "image_bindings": image_index,
        "question_names": list(questions),
        "rules_version": referrals.RULES_BOOK["version"],
    }
    if clues:
        request["verified_clues"] = [clue for clue in clues if clue.casefold() in _VERIFIED_CLUES]
    rows = [
        _match(identifier, labels, result, records[identifier]["local_intake_facts"], clues)
        if clues else _classify(identifier, labels, result, scope)
        for identifier, labels in evidence.items()
    ] if result.ok else []
    return result, rows, request


def evaluate(data):
    """Classify or search the inbox and recheck suspicious classification results."""
    scope = data.get("scope", "packet")
    if scope not in ("letter", "packet"):
        raise ValueError("Choose cover pages or full packets.")
    clues = _clues(data.get("clues", []))
    snap = SubQSnap()
    result, rows, request = _ask(snap, scope, clues, referrals.CASES)
    if not result.ok:
        return {"available": False, "message": result.error or "Snap returned no decisions.", "request": request}
    if any(row is None for row in rows):
        return {"available": False, "message": "Snap returned an incomplete decision set. Try again.", "request": request}
    if not clues:
        disagreements = [row["id"] for row in rows if not row["agrees_with_local"]]
        if disagreements:
            _, checked, followup = _ask(snap, scope, [], disagreements)
            corrected = {row["id"]: row for row in checked if row is not None and row["agrees_with_local"]}
            for index, row in enumerate(rows):
                if row["id"] in corrected:
                    rows[index] = {**corrected[row["id"]], "rechecked": True,
                                   "first_pass_route": row["route"], "first_pass_source": row["source"]}
            request["calls"] += 1
            for field in ("questions", "input_tokens", "elapsed_ms"):
                request[field] += followup[field]
            if request["output_tokens"] is not None and followup["output_tokens"] is not None:
                request["output_tokens"] += followup["output_tokens"]
            else:
                request["output_tokens"] = None
            request["recheck"] = {**followup, "referral_ids": disagreements,
                                  "resolved_ids": list(corrected)}
    return {"available": True, "mode": "query" if clues else "classification",
            "scope": scope, "clues": clues, "rows": rows, "request": request}
