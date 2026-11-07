"""The three roles, and the information wall between them.

The problem statement asks how a verifier can write tests without sharing the
implementer's blind spots. The mechanism here is an explicit information wall:

  implementer      sees the spec. Writes the code.
  self-tester      sees the spec AND the implementation. (What a model does
                   when asked to "write tests for this code" -- and it inherits
                   whatever the implementation assumed.)
  independent      sees the spec and the signature ONLY. Never the body.
                   It cannot inherit an assumption it was never shown.
  adjudicator      sees everything, but cannot write code or tests. It only
                   answers structured questions, so it cannot smuggle an
                   implementation assumption into a test.

Whether that wall actually buys anything is not assumed here; measure.py
settles it against injected defects with known ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass

from .providers import MetaMuse, SubQSnap, choice, noul, score
from .runner import IMPL_MODULE, extract_code

# --------------------------------------------------------------------------- #
# prompts
# --------------------------------------------------------------------------- #

IMPLEMENTER_SYSTEM = f"""You are a working Python engineer. Implement exactly what the \
specification asks, and nothing more.

Rules:
- Output a single fenced ```python block containing only the module source.
- Standard library only. No imports beyond it.
- No prints, no __main__ block, no comments explaining the obvious.
- Handle the edge cases the specification states. Do not invent requirements.
The module will be saved as {IMPL_MODULE}.py and imported by tests."""

SELF_TESTER_SYSTEM = f"""You write tests for code you have just written.

Rules:
- Output a single fenced ```python block.
- Import what you need from `{IMPL_MODULE}` (e.g. `from {IMPL_MODULE} import foo`).
- Every test is a zero-argument function whose name starts with `test_`.
- Use bare `assert`. No pytest, no fixtures, no classes, no decorators.
- No prints. No network, no file writes.
Write the tests you believe establish that this code is correct."""

INDEPENDENT_TESTER_SYSTEM = f"""You are an independent verifier. You write tests \
from a specification. You have NOT seen the implementation and you must not guess at \
its internals.

Your job is to pin down what the specification *requires*, especially where an \
implementer is likely to have been careless:
- boundary values: zero, one, negative, empty, exactly-at-the-limit
- inputs the spec mentions but a happy-path implementation would skip
- properties that must hold for all valid inputs, not just the examples
- the behaviour the spec states for invalid or degenerate input

Rules:
- Output a single fenced ```python block.
- Import from `{IMPL_MODULE}` using the signature given to you.
- Every test is a zero-argument function named `test_*`, using bare `assert`.
- Each test must follow from the specification. Do not test anything the spec \
does not state, and do not assume an internal representation.
- No pytest, no prints, no network, no file writes."""


# --------------------------------------------------------------------------- #
# generation roles (Meta Muse)
# --------------------------------------------------------------------------- #


@dataclass
class Task:
    """One verification task."""

    name: str
    spec: str
    signature: str
    target: str                  # the function the harness drives
    reference: str = ""          # a known-correct implementation, when we have one


def implement(
    muse: MetaMuse,
    task: Task,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> str | None:
    """Write the implementation. `model` is explicit in experiments, so the
    report can always say which model played implementer."""
    reply = muse.complete(
        IMPLEMENTER_SYSTEM,
        f"Specification:\n{task.spec}\n\nRequired signature:\n{task.signature}",
        max_tokens=8000,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    return extract_code(reply) if reply else None


def write_self_tests(muse: MetaMuse, task: Task, implementation: str) -> str | None:
    reply = muse.complete(
        SELF_TESTER_SYSTEM,
        f"Specification:\n{task.spec}\n\nThe implementation you are testing:\n"
        f"```python\n{implementation}\n```\n\nWrite the test suite.",
        max_tokens=8000,
    )
    return extract_code(reply) if reply else None


def write_independent_tests(muse: MetaMuse, task: Task, n_tests: int = 8) -> str | None:
    """Spec + signature only. The implementation is deliberately withheld."""
    reply = muse.complete(
        INDEPENDENT_TESTER_SYSTEM,
        f"Specification:\n{task.spec}\n\nThe function you are testing (signature only):\n"
        f"```python\n{task.signature}\n```\n\n"
        f"Write about {n_tests} tests. You will never see the implementation.",
        max_tokens=8000,
        temperature=0.4,   # a little spread helps cover more of the input space
    )
    return extract_code(reply) if reply else None


# --------------------------------------------------------------------------- #
# adjudication role (SubQ Snap) -- structured decisions, no generated text
# --------------------------------------------------------------------------- #


def judge_test_independence(snap: SubQSnap, task: Task, test_source: str, implementation: str):
    """Does this suite check the spec, or merely restate the implementation?

    This is the question that distinguishes a real verifier from a mirror, and
    it is exactly the 'does this test meet the stated criteria' job SubQ Snap
    is built for.
    """
    state = (
        f"[SPEC]\n{task.spec}\n\n"
        f"[SIGNATURE]\n{task.signature}\n\n"
        f"[IMPLEMENTATION]\n{implementation}\n\n"
        f"[TEST SUITE UNDER REVIEW]\n{test_source}\n"
    )
    return snap.decide(
        state,
        {
            "derives_from_spec": noul(
                "Does every assertion in the test suite follow from the SPEC alone, rather than "
                "from reading the IMPLEMENTATION?",
                when_true="Assertions are justified by the specification text",
                when_false="One or more assertions only make sense if you have read the implementation",
            ),
            "covers_boundaries": noul(
                "Does the suite exercise the boundary and degenerate inputs the SPEC mentions "
                "(zero, negative, empty, exactly-at-limit)?",
                when_true="Boundary and degenerate cases are covered",
                when_false="Only ordinary happy-path inputs are covered",
            ),
            "independence": score(
                "How independent is this suite from the implementation's own assumptions?",
                [
                    "mirrors the implementation",
                    "mostly restates the implementation",
                    "partly independent",
                    "largely independent of the implementation",
                ],
            ),
        },
    )


def judge_defect(snap: SubQSnap, task: Task, witness_text: str, failing_test: str, mutant_desc: str):
    """Given a reproduced divergence, does it justify blocking the merge?"""
    state = (
        f"[SPEC]\n{task.spec}\n\n"
        f"[INJECTED CHANGE]\n{mutant_desc}\n\n"
        f"[REPRODUCED DIVERGENCE]\n{witness_text}\n\n"
        f"[FAILING TEST]\n{failing_test}\n"
    )
    return snap.decide(
        state,
        {
            "violates_spec": noul(
                "Does the reproduced divergence violate the SPEC?",
                when_true="The observed behaviour contradicts what the spec requires",
                when_false="The spec does not actually constrain this input, so behaviour may differ legitimately",
            ),
            "severity": score(
                "How serious is this divergence for a production merge?",
                ["cosmetic", "minor", "serious", "critical"],
            ),
            "action": choice(
                "What should the merge gate do?",
                {
                    "block": "A real defect was reproduced and must not merge",
                    "review": "Something is wrong but a human should decide",
                    "allow": "Not a defect, or outside what the spec constrains",
                },
            ),
        },
    )


def pick_risk_surface(snap: SubQSnap, repo_context: str, file_options: dict[str, str]):
    """Use the 384K window to choose where to aim the verifier, repo-wide.

    File selection over long context is a first-class SubQ use case, and it is
    what makes this work on a repository rather than one pasted function.
    """
    return snap.decide(
        repo_context,
        {
            "riskiest_file": choice(
                "Which file in this repository most needs independent verification before merge?",
                file_options,
            ),
            "has_untested_edges": noul(
                "Does this repository contain logic with boundary conditions that the existing "
                "tests appear not to cover?",
                when_true="There is boundary logic without matching tests",
                when_false="Boundary conditions appear adequately tested",
            ),
        },
    )
