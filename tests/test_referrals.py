"""Keep fictional referral scheduling behind independent policy and reviewer sign-off."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from blindspot import referrals
from blindspot.providers import DecisionResult


@pytest.fixture(autouse=True)
def isolated_calendar(monkeypatch):
    monkeypatch.setattr(referrals, "_REVIEWS", {})


def test_critical_cannot_be_downgraded_into_calendar(monkeypatch):
    monkeypatch.setattr(referrals, "_verified_route", lambda _: "critical")
    with pytest.raises(ValueError, match="clinician queue"):
        referrals.review({"id": "R101", "route": "low", "confirmed": True})
    review = referrals.review({"id": "R101", "route": "critical", "confirmed": True})
    assert review["slot"] is None


def test_missing_report_and_unconfirmed_route_cannot_book(monkeypatch):
    monkeypatch.setattr(referrals, "_verified_route", lambda _: "needs_review")
    with pytest.raises(ValueError, match="signed report"):
        referrals.review({"id": "R105", "route": "medium", "confirmed": True})
    with pytest.raises(ValueError, match="Confirm"):
        referrals.review({"id": "R105", "route": "needs_review", "confirmed": False})
    assert all(item["review"] is None for item in referrals.inbox()["cases"])


def test_simultaneous_reviews_have_distinct_slots_and_repeat_is_idempotent(monkeypatch):
    monkeypatch.setattr(referrals, "_verified_route", lambda _: "low")
    monkeypatch.setattr(referrals, "pages", lambda _: ("Patient county of residence: San Mateo",))
    def book(identifier):
        return referrals.review({"id": identifier, "route": "low", "confirmed": True})
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(book, ["R103", "R104"])
    assert first["slot"] and second["slot"] and first["slot"] != second["slot"]
    assert book("R103") == first


def test_outside_county_blocks_booking_but_not_critical_escalation(monkeypatch):
    monkeypatch.setattr(referrals, "pages", lambda _: ("Patient county of residence: Alameda",))
    monkeypatch.setattr(referrals, "_verified_route", lambda case: "high" if case == "R102" else "critical")
    with pytest.raises(ValueError, match="AREA-02"):
        referrals.review({"id": "R102", "route": "high", "confirmed": True})
    coordinated = referrals.review({"id": "R102", "route": "intake_coordination", "confirmed": True})
    assert coordinated["slot"] is None and coordinated["verified_route"] == "high"
    critical = referrals.review({"id": "R101", "route": "critical", "confirmed": True})
    assert critical["slot"] is None and critical["status"] == "Escalated to demo clinician queue"


def test_fax_sender_and_later_county_do_not_establish_residency(monkeypatch):
    monkeypatch.setattr(referrals, "pages", lambda _: (
        "Sender: San Francisco (415) 555-0102\nPatient county of residence: Not documented",
        "Patient county of residence: San Francisco",
    ))
    item = referrals.inbox()["cases"][1]
    assert item["fax"]["sender_number"].startswith("(415)")
    assert item["patient_county"] is None
    assert item["eligibility"]["status"] == "manual_review"
    assert item["eligibility"]["rule_id"] == "AREA-03"


def test_snap_receives_county_and_rules_as_separate_advisory_question(monkeypatch):
    sent = {}

    class Snap:
        model = "subq-snap-test"

        def decide(self, state, questions, **kwargs):
            sent.update(state=state, questions=questions, **kwargs)
            return DecisionResult(error="not configured")

    monkeypatch.setattr(referrals, "SubQSnap", Snap)
    monkeypatch.setattr(referrals, "pages", lambda _: (
        "Patient county of residence: Alameda", "Status: signed", "Lab order",
    ))
    result = referrals.assess({"id": "R102", "scope": "letter"})
    assert not result["available"]
    assert sent["state"]["intake_fields"]["patient_county"] == "Alameda"
    assert sent["state"]["rules_book"]["version"] == "DEMO-1.0"
    assert set(sent["questions"]["acceptance"]["criteria"]) == {"eligible", "outside_area", "manual_review"}
    assert result["request_summary"]["page_labels"] == ["P1"]
    assert not sent["images"]


def test_image_only_page_keeps_the_fax_page_number(monkeypatch):
    referrals.pages.cache_clear()
    monkeypatch.setattr(referrals, "_extract", lambda *_: "Intake form\f\fFinal lab report\f")
    try:
        assert referrals.pages("R101") == (
            "Intake form", "[Image-only page; no embedded text.]", "Final lab report",
        )
    finally:
        referrals.pages.cache_clear()
