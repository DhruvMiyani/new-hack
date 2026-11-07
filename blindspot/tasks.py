"""Verification tasks.

Each task carries a spec, a signature, and a known-correct reference. The
reference is what mutants are derived from, so it is also the ground truth the
whole measurement hangs on -- these are written to be obviously right.

The `offline_*` suites let the pipeline run with no API keys at all. They stand
in for model output so the measurement itself can be demonstrated and tested
independently of whether a sponsor API is reachable.
"""

from __future__ import annotations

from .roles import Task

ALLOCATE = Task(
    name="allocate",
    target="allocate",
    signature="def allocate(budget: int, requests: list[int]) -> list[int]:",
    spec="""Grant requests from a fixed budget, in the order given.

Walk the requests in order. Grant a request if its amount is less than or equal
to the budget still remaining, and subtract it from the remaining budget.
Skip any request that does not fit, and keep going -- a request that does not
fit must not stop later, smaller requests from being granted.

Return the list of granted amounts, in the order they were granted.

If the budget is zero or negative, grant nothing and return an empty list.
A request of zero is grantable whenever the remaining budget is zero or more.
The total granted must never exceed the original budget.""",
    reference='''
def allocate(budget: int, requests: list) -> list:
    if budget <= 0:
        return []
    granted = []
    remaining = budget
    for r in requests:
        if r <= remaining:
            granted.append(r)
            remaining -= r
    return granted
'''.strip()
    + "\n",
)

TRIM_MEAN = Task(
    name="trim_mean",
    target="trim_mean",
    signature="def trim_mean(values: list[float], drop: int) -> float:",
    spec="""Return the arithmetic mean of `values` after discarding the `drop`
smallest and `drop` largest entries.

Sort the values, remove `drop` entries from each end, and average what is left.

Rules:
- If `drop` is zero, average all the values.
- If `drop` is negative, treat it as zero.
- If removing 2*drop entries would leave nothing, return 0.0.
- If `values` is empty, return 0.0.
- The result is a float.""",
    reference='''
def trim_mean(values: list, drop: int) -> float:
    if not values:
        return 0.0
    if drop < 0:
        drop = 0
    if len(values) - 2 * drop <= 0:
        return 0.0
    ordered = sorted(values)
    kept = ordered[drop:len(ordered) - drop]
    return sum(kept) / len(kept)
'''.strip()
    + "\n",
)

PAGE_RANGE = Task(
    name="page_range",
    target="page_range",
    signature="def page_range(total: int, page: int, per_page: int) -> tuple[int, int]:",
    spec="""Return the half-open index range (start, end) of items on one page.

Pages are 1-indexed. Page 1 starts at index 0. `end` is exclusive and must
never exceed `total`.

Rules:
- If `page` is less than 1, treat it as page 1.
- If `per_page` is less than 1, return (0, 0).
- If `total` is zero or negative, return (0, 0).
- If the requested page starts at or beyond `total`, return (total, total) --
  an empty range at the end, not a negative one.
- start must never be greater than end.""",
    reference='''
def page_range(total: int, page: int, per_page: int) -> tuple:
    if total <= 0 or per_page < 1:
        return (0, 0)
    if page < 1:
        page = 1
    start = (page - 1) * per_page
    if start >= total:
        return (total, total)
    end = start + per_page
    if end > total:
        end = total
    return (start, end)
'''.strip()
    + "\n",
)

TASKS = {t.name: t for t in (ALLOCATE, TRIM_MEAN, PAGE_RANGE)}


# --------------------------------------------------------------------------- #
# offline stand-ins for model output
# --------------------------------------------------------------------------- #

# Written in the style a model actually produces when it has just written the
# code: confident, happy-path, derived from what it meant to do.
OFFLINE_SELF_TESTS = {
    "allocate": '''
from impl import allocate

def test_grants_all_when_affordable():
    assert allocate(10, [1, 2, 3]) == [1, 2, 3]

def test_skips_requests_that_do_not_fit():
    assert allocate(5, [10, 2]) == [2]

def test_returns_a_list():
    assert isinstance(allocate(10, [1]), list)
'''.strip()
    + "\n",
    "trim_mean": '''
from impl import trim_mean

def test_simple_average():
    assert trim_mean([1, 2, 3], 0) == 2.0

def test_trims_extremes():
    assert trim_mean([1, 2, 3, 4, 100], 1) == 3.0

def test_returns_float():
    assert isinstance(trim_mean([1, 2, 3], 0), float)
'''.strip()
    + "\n",
    "page_range": '''
from impl import page_range

def test_first_page():
    assert page_range(100, 1, 10) == (0, 10)

def test_second_page():
    assert page_range(100, 2, 10) == (10, 20)

def test_partial_last_page():
    assert page_range(25, 3, 10) == (20, 25)
'''.strip()
    + "\n",
}

# Written from the spec alone, aimed at the boundaries the spec names.
OFFLINE_INDEPENDENT_TESTS = {
    "allocate": '''
from impl import allocate

def test_zero_budget_grants_nothing():
    assert allocate(0, [1, 2]) == []

def test_negative_budget_grants_nothing():
    assert allocate(-5, [1]) == []

def test_request_exactly_equal_to_budget_is_granted():
    assert allocate(3, [3]) == [3]

def test_budget_consumed_exactly():
    assert allocate(6, [1, 2, 3]) == [1, 2, 3]

def test_zero_request_is_grantable():
    assert allocate(1, [0]) == [0]

def test_oversized_request_does_not_stop_later_ones():
    assert allocate(5, [99, 1]) == [1]

def test_never_overspends():
    assert sum(allocate(5, [3, 3, 3])) <= 5

def test_empty_requests():
    assert allocate(10, []) == []
'''.strip()
    + "\n",
    "trim_mean": '''
from impl import trim_mean

def test_empty_values():
    assert trim_mean([], 0) == 0.0

def test_negative_drop_treated_as_zero():
    assert trim_mean([1, 2, 3], -1) == 2.0

def test_drop_removes_nothing_left():
    assert trim_mean([1, 2], 1) == 0.0

def test_drop_exactly_empties():
    assert trim_mean([1, 2, 3], 2) == 0.0

def test_drops_both_ends():
    assert trim_mean([0, 5, 5, 5, 100], 1) == 5.0

def test_single_value_no_drop():
    assert trim_mean([7], 0) == 7.0

def test_result_is_float():
    assert isinstance(trim_mean([1, 2, 3, 4], 1), float)
'''.strip()
    + "\n",
    "page_range": '''
from impl import page_range

def test_zero_total():
    assert page_range(0, 1, 10) == (0, 0)

def test_negative_total():
    assert page_range(-5, 1, 10) == (0, 0)

def test_per_page_below_one():
    assert page_range(100, 1, 0) == (0, 0)

def test_page_below_one_treated_as_first():
    assert page_range(100, 0, 10) == (0, 10)

def test_page_past_the_end_is_empty_at_end():
    assert page_range(25, 99, 10) == (25, 25)

def test_end_never_exceeds_total():
    start, end = page_range(25, 3, 10)
    assert end <= 25

def test_start_never_after_end():
    start, end = page_range(25, 4, 10)
    assert start <= end

def test_exact_page_boundary():
    assert page_range(20, 2, 10) == (10, 20)
'''.strip()
    + "\n",
}
