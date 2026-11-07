"""Tests for the blind-spot measurement.

The important ones are the anti-cheat tests. The headline number is only worth
anything if a suite cannot inflate it -- so: a suite that fails on correct code
is disqualified rather than credited, an equivalent mutant is never counted as
a missed defect, and a defect is never counted without a witness.
"""

from __future__ import annotations

import pytest

from blindspot.equivalence import classify, partition
from blindspot.measure import Suite, score, validate
from blindspot.mutate import OPERATORS, generate
from blindspot.pipeline import INDEPENDENT, SELF, aggregate, run
from blindspot.providers import choice, noul, score as score_q
from blindspot.runner import extract_code, run_suite
from blindspot.tasks import OFFLINE_INDEPENDENT_TESTS, OFFLINE_SELF_TESTS, TASKS

CORRECT = TASKS["allocate"].reference


# --------------------------------------------------------------------------- #
# mutation
# --------------------------------------------------------------------------- #


def test_generates_mutants_that_parse():
    import ast

    mutants = generate(CORRECT)
    assert mutants
    for m in mutants:
        ast.parse(m.source)                      # a mutant must be valid Python
        assert m.source.strip() != CORRECT.strip()


def test_mutants_are_single_site_and_deduplicated():
    mutants = generate(CORRECT)
    sources = [m.source for m in mutants]
    assert len(sources) == len(set(sources))


def test_every_operator_is_reachable():
    """An operator that never fires is dead weight and would silently narrow coverage."""
    rich = (
        "def f(a: int, b: int, items: list) -> int:\n"
        "    if a < 1 or b > 2:\n"
        "        return 0\n"
        "    total = a + b\n"
        "    total -= 1\n"
        "    return max(total, len(items))\n"
    )
    fired = {m.operator for m in generate(rich)}
    assert fired, "no operator fired at all"
    assert fired <= set(OPERATORS)
    for expected in ("comparison", "boolean", "arithmetic", "offbyone", "swapargs", "dropguard"):
        assert expected in fired, f"{expected} never fired on code that contains its pattern"


# --------------------------------------------------------------------------- #
# execution
# --------------------------------------------------------------------------- #


def test_suite_passes_on_correct_code():
    r = run_suite(CORRECT, OFFLINE_INDEPENDENT_TESTS["allocate"])
    assert r.usable and r.passed_all, r.summary()


def test_suite_detects_a_broken_implementation():
    broken = CORRECT.replace("if r <= remaining:", "if r < remaining:")
    r = run_suite(broken, OFFLINE_INDEPENDENT_TESTS["allocate"])
    assert r.detects
    assert any(t.kind == "assertion" for t in r.failures)


def test_import_error_is_not_a_detection():
    """A suite that cannot import has found nothing -- never conflate the two."""
    r = run_suite(CORRECT, "from impl import does_not_exist\n\ndef test_x():\n    assert True\n")
    assert r.import_error
    assert not r.usable and not r.detects


def test_empty_suite_is_unusable():
    r = run_suite(CORRECT, "x = 1\n")
    assert not r.usable and not r.detects


def test_runner_kills_a_hanging_suite():
    r = run_suite(CORRECT, "def test_hang():\n    while True:\n        pass\n", timeout=3.0)
    assert r.timed_out or not r.usable
    assert not r.detects


def test_sandbox_blocks_network_in_tests():
    suite = (
        "import socket\n"
        "def test_net():\n"
        "    try:\n"
        "        socket.create_connection(('example.com', 80), timeout=2)\n"
        "        assert False, 'network reachable'\n"
        "    except OSError:\n"
        "        pass\n"
    )
    r = run_suite(CORRECT, suite)
    assert r.usable and r.passed_all


def test_extract_code_prefers_the_fenced_block():
    reply = "Sure!\n\n```python\ndef f():\n    return 1\n```\n\nHope that helps."
    assert extract_code(reply).strip() == "def f():\n    return 1"


# --------------------------------------------------------------------------- #
# equivalence: no witness, no defect
# --------------------------------------------------------------------------- #


def test_real_defect_gets_a_witness():
    mutants = [m for m in generate(CORRECT) if m.operator == "arithmetic"]
    assert mutants
    eq = classify(CORRECT, mutants[0], "allocate", trials=400)
    assert eq.is_real_defect
    assert eq.witness is not None
    assert eq.witness.original != eq.witness.mutant


def test_equivalent_mutant_is_not_counted_as_a_defect():
    """A no-op change must be classified equivalent, not blamed on the tests."""
    source = (
        "def f(a: int) -> int:\n"
        "    unused = 1\n"          # mutating this literal changes nothing observable
        "    return a * 2\n"
    )
    mutants = [m for m in generate(source) if m.operator == "offbyone"]
    assert mutants
    real, equivalent, _ = partition(source, mutants, "f", trials=200)
    assert equivalent, "a dead-store mutation should be equivalent"
    assert all(eq.witness is None for _, eq in equivalent)


def test_partition_splits_without_losing_mutants():
    mutants = generate(CORRECT)
    real, equivalent, table = partition(CORRECT, mutants, "allocate", trials=300)
    assert len(real) + len(equivalent) == len(mutants)
    assert len(table) == len(mutants)


# --------------------------------------------------------------------------- #
# the anti-cheat rules
# --------------------------------------------------------------------------- #


def test_suite_that_false_alarms_is_disqualified_not_credited():
    """The one way to fake a perfect score: fail on everything. It must not work."""
    always_fails = "def test_always_fails():\n    assert False\n"
    suite = Suite("cheater", "test", always_fails)
    validate(suite, CORRECT)
    assert not suite.valid
    assert "false alarm" in suite.disqualification()

    card = score("allocate", CORRECT, generate(CORRECT), [suite], "allocate", trials=200)
    # Disqualified suites are not scored at all, so they kill nothing.
    assert card.kill_rate("cheater") == 0.0
    assert all("cheater" not in v.killed_by for v in card.verdicts)


def test_only_proven_defects_are_scored():
    card = score(
        "allocate",
        CORRECT,
        generate(CORRECT),
        [Suite(SELF, "t", OFFLINE_SELF_TESTS["allocate"])],
        "allocate",
        trials=300,
    )
    for v in card.verdicts:
        eq = card.classifications[v.mutant.mutant_id]
        assert eq.is_real_defect and eq.witness is not None


def test_kill_rate_denominator_excludes_equivalents():
    mutants = generate(CORRECT)
    card = score(
        "allocate",
        CORRECT,
        mutants,
        [Suite(SELF, "t", OFFLINE_SELF_TESTS["allocate"])],
        "allocate",
        trials=300,
    )
    assert len(card.verdicts) <= len(mutants)
    assert len(card.verdicts) + len(card.equivalent) + len(card.undecided) == len(mutants)


# --------------------------------------------------------------------------- #
# the result the project claims
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("task_name", list(TASKS))
def test_independent_suite_beats_self_suite(task_name):
    """The central claim, re-derived per task from executed code."""
    report = run(TASKS[task_name], use_models=False, trials=300)
    card = report.scorecard
    assert card and card.verdicts, "expected at least one proven defect"
    assert card.kill_rate(INDEPENDENT) > card.kill_rate(SELF), (
        f"{task_name}: independent {card.kill_rate(INDEPENDENT):.2f} "
        f"vs self {card.kill_rate(SELF):.2f}"
    )


def test_recovered_defects_carry_a_witness_and_a_named_test():
    report = run(TASKS["allocate"], use_models=False, trials=300)
    card = report.scorecard
    for v in card.recovered(SELF, INDEPENDENT):
        eq = card.classifications[v.mutant.mutant_id]
        assert eq.witness is not None, "a recovered defect must be witnessed"
        assert v.killing_tests[INDEPENDENT], "must name the test that caught it"


def test_pipeline_runs_with_no_api_keys(monkeypatch):
    for var in ("META_API_KEY", "MODEL_API_KEY", "SUBQ_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    report = run(TASKS["allocate"], use_models=True, trials=200)
    assert report.scorecard is not None
    assert not report.generated and not report.adjudicated
    assert report.decision in ("BLOCK", "REVIEW", "ALLOW")


def test_aggregate_matches_the_per_task_numbers():
    reports = [run(TASKS[n], use_models=False, trials=200) for n in ("allocate", "trim_mean")]
    agg = aggregate(reports)
    assert agg["defects"] == sum(len(r.scorecard.verdicts) for r in reports)
    assert 0.0 <= agg["self_kill_rate"] <= 1.0
    assert 0.0 <= agg["independent_kill_rate"] <= 1.0
    if agg["recovery_rate"] is not None:
        assert 0.0 <= agg["recovery_rate"] <= 1.0


# --------------------------------------------------------------------------- #
# providers degrade rather than invent
# --------------------------------------------------------------------------- #


def test_subq_question_builders_match_the_documented_schema():
    q = noul("ask", "yes case", "no case")
    assert q["type"] == "noul" and set(q["criteria"]) == {"true", "false"}

    c = choice("pick", {"a": "first", "b": "second"})
    assert c["type"] == "choice" and len(c["criteria"]) == 2

    s = score_q("rate", ["low", "high"])
    assert s["type"] == "score" and isinstance(s["criteria"], list)


def test_subq_reports_missing_key_instead_of_guessing(monkeypatch):
    from blindspot.providers import SubQSnap

    monkeypatch.delenv("SUBQ_API_KEY", raising=False)
    snap = SubQSnap()
    assert not snap.available
    res = snap.decide("state", {"q": noul("x", "t", "f")})
    assert not res.ok and "SUBQ_API_KEY" in res.error


def test_meta_placeholder_key_is_not_treated_as_configured(monkeypatch):
    from blindspot.providers import MetaMuse

    monkeypatch.setenv("META_API_KEY", "paste-your-key-here")
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    assert not MetaMuse().available


# --------------------------------------------------------------------------- #
# the experiment matrix, driven by a stub author so the grid logic is tested
# without needing a live key
# --------------------------------------------------------------------------- #


class _StubMuse:
    """Stands in for Meta. Returns a suite whose strength depends on the cell.

    saw-code cells get happy-path tests; spec-only cells get boundary tests.
    Sample index perturbs the suite so the matrix sees real spread.
    """

    model = "stub"
    available = True

    def __init__(self):
        from blindspot.providers import Usage

        self.usage = Usage()
        self.notes = []
        self.calls = []

    def complete(self, system, user, **kw):
        self.calls.append({"model": kw.get("model"), "reasoning": kw.get("reasoning_effort"),
                           "seed": kw.get("seed")})
        self.usage.add(100, 200, 0, "")
        sample = (kw.get("seed") or 1000) - 1000
        if "signature only" in user:
            extra = (
                "\ndef test_zero_request_is_grantable():\n    assert allocate(1, [0]) == [0]\n"
                if sample > 0 else ""
            )
            return (
                "```python\nfrom impl import allocate\n\n"
                "def test_exact_fit():\n    assert allocate(3, [3]) == [3]\n\n"
                "def test_never_overspends():\n    assert sum(allocate(5, [3, 3, 3])) <= 5\n"
                + extra + "```"
            )
        return (
            "```python\nfrom impl import allocate\n\n"
            "def test_happy_path():\n    assert allocate(10, [1, 2, 3]) == [1, 2, 3]\n```"
        )


def test_matrix_isolates_information_and_model_effects():
    from blindspot.matrix import SAW_CODE, SPEC_ONLY, run_matrix

    stub = _StubMuse()
    res = run_matrix(
        TASKS["allocate"], models=["stub-a", "stub-b"], samples=2, trials=300, muse=stub
    )

    assert res.defects, "expected proven defects"
    # 2 models x 2 conditions x 1 reasoning level
    assert len(res.groups) == 4
    for g in res.groups:
        assert len(g.cells) == 2
        assert g.mean is not None, f"{g.label()} produced no usable sample"

    # the stub's spec-only suites are strictly stronger, so the information
    # effect must be positive for both models
    for model in ("stub-a", "stub-b"):
        eff = res.information_effect(model)
        assert eff is not None and eff > 0, f"{model}: information effect {eff}"

    # every requested cell was asked for by name, and the implementer is recorded
    models_called = {c["model"] for c in stub.calls}
    assert models_called == {"stub-a", "stub-b"}, "no cell may use the provider default"
    assert res.implementer_model == "stub-a"


def test_matrix_reports_spread_across_samples():
    from blindspot.matrix import SPEC_ONLY, run_matrix

    stub = _StubMuse()
    res = run_matrix(TASKS["allocate"], models=["stub-a"], samples=3, trials=300, muse=stub)
    g = res.group("stub-a", SPEC_ONLY)
    assert g is not None and g.stdev is not None
    # sample 0 is weaker than samples 1 and 2, so there must be real spread
    assert g.stdev > 0
    # floor <= mean <= ceiling
    inter, union = g.intersection, g.union
    assert inter is not None
    assert len(inter) <= len(union)
    assert len(inter) / res.total <= g.mean <= len(union) / res.total + 1e-9


def test_matrix_passes_reasoning_effort_through():
    from blindspot.matrix import run_matrix

    stub = _StubMuse()
    run_matrix(
        TASKS["allocate"], models=["stub-a"], samples=1,
        reasoning_levels=["low", "high"], trials=200, muse=stub,
    )
    efforts = {c["reasoning"] for c in stub.calls if c["reasoning"]}
    assert efforts == {"low", "high"}


def test_matrix_reuses_one_proven_defect_set_for_every_cell():
    """Cells must differ only in the axis under test, never in the denominator."""
    from blindspot.matrix import run_matrix

    stub = _StubMuse()
    res = run_matrix(TASKS["allocate"], models=["stub-a", "stub-b"], samples=2, trials=300, muse=stub)
    totals = {g.total_defects for g in res.groups}
    assert totals == {res.total}


def test_matrix_without_a_key_explains_itself():
    from blindspot.matrix import run_matrix
    from blindspot.providers import MetaMuse

    class _Absent(MetaMuse):
        @property
        def available(self):
            return False

    res = run_matrix(TASKS["allocate"], muse=_Absent())
    assert not res.defects and not res.groups
    assert any("Meta key" in n for n in res.notes)


def test_cost_estimate_matches_published_rates():
    from blindspot.providers import cost_usd

    # muse-spark-1.3: $1.25/M in, $4.25/M out
    assert abs(cost_usd("muse-spark-1.3", 1_000_000, 0) - 1.25) < 1e-9
    assert abs(cost_usd("muse-spark-1.3", 0, 1_000_000) - 4.25) < 1e-9
    # cached input is billed at the cached rate, not the full rate
    cached = cost_usd("muse-spark-1.3", 1_000_000, 0, cached_tokens=1_000_000)
    assert abs(cached - 0.15) < 1e-9
    assert cost_usd("unknown-model", 1_000_000, 1_000_000) == 0.0


# --------------------------------------------------------------------------- #
# calibration: scoring SubQ's predictions against executed truth
# --------------------------------------------------------------------------- #


class _StubSnap:
    """Stands in for SubQ. Predicts from a rule we control, so the scoring
    machinery can be checked against a known-correct answer."""

    available = True

    def __init__(self, probability=0.9):
        from blindspot.providers import Usage

        self.usage = Usage()
        self.probability = probability
        self.seen = []

    def decide(self, state, questions, timeout=90.0):
        from blindspot.providers import Answer, DecisionResult

        self.seen.append(state)
        self.usage.add(1000, 0, 0, "")
        return DecisionResult(
            answers={
                "will_catch": Answer("will_catch", "noul", self.probability),
                "severity": Answer("severity", "score", 2, confidence=0.7),
            },
            input_tokens=1000,
        )


def _two_suites():
    from blindspot.measure import Suite, validate

    a = Suite("saw-code", "stub", OFFLINE_SELF_TESTS["allocate"], saw_implementation=True)
    b = Suite("spec-only", "stub", OFFLINE_INDEPENDENT_TESTS["allocate"], saw_implementation=False)
    for s in (a, b):
        validate(s, CORRECT)
    return [a, b]


def test_calibration_scores_predictions_against_execution():
    from blindspot.calibration import calibrate

    real, _, table = partition(CORRECT, generate(CORRECT), "allocate", trials=300)
    snap = _StubSnap(probability=0.9)
    rep = calibrate(TASKS["allocate"], _two_suites(), real, snap=snap, classifications=table)

    assert rep.usable
    assert len(rep.usable) == len(real) * 2
    # the stub always says 0.9, so accuracy must equal the observed base rate
    assert rep.accuracy == pytest.approx(rep.base_rate)
    assert 0.0 <= rep.brier <= 1.0


def test_calibration_detects_an_overconfident_predictor():
    """A predictor that always says 0.9 must show a positive calibration gap
    wherever the real catch rate is lower."""
    from blindspot.calibration import calibrate

    real, _, table = partition(CORRECT, generate(CORRECT), "allocate", trials=300)
    rep = calibrate(TASKS["allocate"], _two_suites(), real, snap=_StubSnap(0.9), classifications=table)

    top = [b for b in rep.buckets() if b.n and b.low >= 0.8]
    assert top, "expected predictions in the 0.8-1.0 bucket"
    assert top[0].gap > 0, "always saying 0.9 against a lower base rate is overconfidence"


def test_a_useless_predictor_fails_to_beat_the_base_rate_baseline():
    """The honesty check: a constant predictor must not look good."""
    from blindspot.calibration import calibrate

    real, _, table = partition(CORRECT, generate(CORRECT), "allocate", trials=300)
    rep = calibrate(TASKS["allocate"], _two_suites(), real, snap=_StubSnap(0.9), classifications=table)
    assert rep.brier > rep.baseline_brier, (
        "a constant 0.9 predictor should lose to always guessing the base rate"
    )


def test_calibration_never_leaks_the_witness_to_the_predictor():
    """SubQ must not be shown the answer it is being asked to predict."""
    from blindspot.calibration import calibrate

    real, _, table = partition(CORRECT, generate(CORRECT), "allocate", trials=300)
    snap = _StubSnap()
    calibrate(TASKS["allocate"], _two_suites(), real, snap=snap, classifications=table)

    witnesses = [eq.witness.render() for eq in table.values() if eq.witness]
    for state in snap.seen:
        assert "original ok:" not in state, "witness output leaked into the prompt"
        for w in witnesses:
            assert w not in state


def test_severity_is_compared_against_manifestation():
    from blindspot.calibration import calibrate

    real, _, table = partition(CORRECT, generate(CORRECT), "allocate", trials=300)
    rep = calibrate(TASKS["allocate"], _two_suites(), real, snap=_StubSnap(), classifications=table)
    assert rep.manifest_trials, "witness trial counts should be recorded"
    rows = rep.severity_vs_manifestation()
    assert rows
    for lvl, n, mean_trials in rows:
        assert n > 0 and mean_trials > 0


def test_calibration_without_a_key_explains_itself():
    from blindspot.calibration import calibrate
    from blindspot.providers import SubQSnap

    class _Absent(SubQSnap):
        @property
        def available(self):
            return False

    rep = calibrate(TASKS["allocate"], _two_suites(), [], snap=_Absent())
    assert not rep.usable
    assert any("SUBQ_API_KEY" in n for n in rep.notes)
