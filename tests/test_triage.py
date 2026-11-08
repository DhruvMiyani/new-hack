"""Batch inference contracts over the fixed synthetic referral corpus."""
import pytest

from blindspot import referrals, triage
from blindspot.providers import Answer, DecisionResult


@pytest.fixture
def sent(monkeypatch):
    calls = []
    fifty = {f"R{number}": {"clinic": f"Training clinic {number}", "report": "scan" if number == 101 else "text"}
             for number in range(101, 151)}
    monkeypatch.setattr(referrals, "CASES", fifty)

    def pages(case):
        return (f"Referral letter {case}", f"Signed report {case}", f"Lab document {case}")

    class FakeSnap:
        model = "snap-test"

        def decide(self, state, questions, **kwargs):
            calls.append((state, questions, kwargs))
            answers = {}
            for name, question in questions.items():
                if question["type"] == "noul":
                    answers[name] = Answer(name, "noul", 0.9)
                else:
                    options = question["criteria"]
                    selected = next(iter(options))
                    case = name.rsplit("_", 1)[-1]
                    cover_only = len(state["referrals"][case]["pages"]) == 1
                    if name.startswith("route_"):
                        selected = "needs_review" if cover_only else "high"
                    elif name.startswith("source_"):
                        selected = "NONE" if cover_only else (
                            f"{case}-SCAN" if f"{case}-SCAN" in options else f"{case}-P2"
                        )
                    answers[name] = Answer(name, "choice", selected, {key: float(key == selected) for key in options})
            return DecisionResult(answers=answers, input_tokens=123, output_tokens=0, elapsed_ms=20)

    monkeypatch.setattr(referrals, "pages", pages)
    monkeypatch.setattr(referrals, "_patient_county", lambda _: "San Mateo")
    monkeypatch.setattr(referrals, "_signed_scan", lambda: {"mediaType": "image/png", "base64": "c2Nhbg=="})
    monkeypatch.setattr(referrals, "_verified_route", lambda _: "high")
    monkeypatch.setattr(triage, "SubQSnap", FakeSnap)
    return calls


def test_classifies_in_one_call_and_binds_scan_to_own_fax(sent):
    result = triage.evaluate({"scope": "packet"})
    assert result["available"] and len(sent) == 1
    state, questions, kwargs = sent[0]
    assert len(questions) == 100 and len(result["rows"]) == 50
    assert result["request"]["calls"] == 1
    assert "local_intake_facts" not in state["referrals"]["R101"]
    assert state["image_bindings"] == {"image_1": "R101-SCAN"}
    assert len(kwargs["images"]) == 1
    assert "R101-SCAN" in questions["source_R101"]["criteria"]
    assert "R101-SCAN" not in questions["source_R102"]["criteria"]
    assert result["request"]["output_tokens"] == 0


def test_cover_scope_omits_other_pages_and_the_scan(sent):
    result = triage.evaluate({"scope": "letter"})
    state, _, kwargs = sent[0]
    assert result["request"]["pages"] == 50 and result["request"]["images"] == 0
    assert not kwargs["images"]
    assert all(len(record["pages"]) == 1 for record in state["referrals"].values())
    assert all(row["verified_route"] == "needs_review" for row in result["rows"])


def test_query_returns_matching_scores_without_changing_priority(sent):
    result = triage.evaluate({"clues": ["  high   priority ", "outside area", "HIGH PRIORITY"]})
    state, questions, kwargs = sent[0]
    assert result["clues"] == ["high priority", "outside area"]
    assert state["search_clues"] == result["clues"]
    assert len(questions) == 100 and not any(key.startswith("route_") for key in questions)
    assert state["referrals"]["R101"]["local_intake_facts"]["signed_priority"] == {
        "value": "high", "source": "R101-SCAN",
    }
    assert result["request"]["context"] == "search_index" and not kwargs["images"]
    assert all(row["matched"] and "route" not in row for row in result["rows"])
    assert len(sent) == 1


def test_invalid_clue_is_rejected_before_provider_call(sent):
    with pytest.raises(ValueError, match="120"):
        triage.evaluate({"clues": ["x" * 121]})
    assert sent == []


def test_missing_or_wrong_case_evidence_is_not_accepted():
    evidence = {"R101-P1": "Page 1", "NONE": "No source"}
    source = Answer("match_source_R101", "choice", "R102-P1", {"R102-P1": 1.0})
    result = DecisionResult(answers={
        "match_R101": Answer("match_R101", "noul", 1.0),
        "match_source_R101": source,
    })
    assert triage._match("R101", evidence, result) is None


def test_preset_query_checks_county_without_replacing_snap_score(sent):
    clue = "High priority outside the scheduling area"
    result = triage.evaluate({"clues": [clue]})
    assert result["request"]["verified_clues"] == [clue]
    row = result["rows"][0]
    assert row["score"] == 0.9 and row["model_matched"]
    assert not row["verified_match"] and not row["matched"]


def test_classification_rechecks_only_a_disagreeing_case(sent, monkeypatch):
    original = triage.SubQSnap

    class DisagreesOnce(original):
        def decide(self, state, questions, **kwargs):
            result = super().decide(state, questions, **kwargs)
            if len(state["referrals"]) == 50:
                answer = result.answers["route_R145"]
                answer.value = "critical"
                answer.probabilities = {label: float(label == "critical") for label in answer.probabilities}
            return result

    monkeypatch.setattr(triage, "SubQSnap", DisagreesOnce)
    result = triage.evaluate({"scope": "packet"})
    assert len(sent) == 2 and list(sent[1][0]["referrals"]) == ["R145"]
    assert result["request"]["calls"] == 2 and result["request"]["questions"] == 102
    assert result["request"]["recheck"]["resolved_ids"] == ["R145"]
    row = next(row for row in result["rows"] if row["id"] == "R145")
    assert row["route"] == "high" and row["first_pass_route"] == "critical"
    assert row["rechecked"] and row["agrees_with_local"]
