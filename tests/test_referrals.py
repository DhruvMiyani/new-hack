"""Keep fictional referral scheduling behind independent policy and reviewer sign-off."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from blindspot import referrals


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
    def book(identifier):
        return referrals.review({"id": identifier, "route": "low", "confirmed": True})
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(book, ["R103", "R104"])
    assert first["slot"] and second["slot"] and first["slot"] != second["slot"]
    assert book("R103") == first
