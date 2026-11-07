"""Defect injection by AST mutation.

A verifier's test suite is only as good as the defects it catches, so we need
defects with known ground truth. Mutation testing supplies them: take code that
is correct, break it in one specific, realistic way, and ask whether a test
suite notices. A suite that passes on a mutant has a hole in exactly that spot.

The operators here are chosen to mirror how generated code actually fails --
boundary conditions, inverted guards, swapped arguments -- rather than to be
exhaustive. An operator that produces an obviously-dead mutant teaches us
nothing about blind spots.
"""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass
from typing import Iterator


@dataclass
class Mutant:
    """One known defect injected at one site."""

    mutant_id: str
    operator: str
    description: str
    source: str
    line: int
    original_line: str
    mutated_line: str

    def summary(self) -> str:
        return f"{self.mutant_id} [{self.operator}] line {self.line}: {self.description}"


# --------------------------------------------------------------------------- #
# operators
# --------------------------------------------------------------------------- #

CMP_SWAPS = {
    ast.Lt: (ast.LtE, "< becomes <= (off-by-one at the boundary)"),
    ast.LtE: (ast.Lt, "<= becomes < (drops the boundary case)"),
    ast.Gt: (ast.GtE, "> becomes >= (off-by-one at the boundary)"),
    ast.GtE: (ast.Gt, ">= becomes > (drops the boundary case)"),
    ast.Eq: (ast.NotEq, "== becomes != (inverted equality)"),
    ast.NotEq: (ast.Eq, "!= becomes == (inverted equality)"),
    ast.Is: (ast.IsNot, "is becomes is not"),
    ast.IsNot: (ast.Is, "is not becomes is"),
}

BOOL_SWAPS = {
    ast.And: (ast.Or, "and becomes or (guard weakened)"),
    ast.Or: (ast.And, "or becomes and (guard narrowed)"),
}

ARITH_SWAPS = {
    ast.Add: (ast.Sub, "+ becomes -"),
    ast.Sub: (ast.Add, "- becomes +"),
    ast.Mult: (ast.Div, "* becomes /"),
    ast.Div: (ast.FloorDiv, "/ becomes // (silent truncation)"),
    ast.FloorDiv: (ast.Div, "// becomes /"),
}


class _Replacer(ast.NodeTransformer):
    """Rewrites exactly one node, identified by position in a traversal order."""

    def __init__(self, target_index: int, apply):
        self.target_index = target_index
        self.apply = apply
        self.seen = -1
        self.hit_line = None

    def generic_visit(self, node):
        return super().generic_visit(node)


def _sites(tree: ast.AST, predicate) -> list[ast.AST]:
    return [n for n in ast.walk(tree) if predicate(n)]


def _render(tree: ast.AST) -> str:
    return ast.unparse(tree)


def _line_of(node: ast.AST, default: int = 0) -> int:
    return getattr(node, "lineno", default)


def _mutate_one(source: str, pick, mutate, operator: str) -> Iterator[Mutant]:
    """Generic driver: for each matching site, emit one single-site mutant."""
    original_tree = ast.parse(source)
    sites = pick(original_tree)
    original_lines = source.splitlines()

    for i in range(len(sites)):
        tree = ast.parse(source)
        site_list = pick(tree)
        if i >= len(site_list):
            continue
        node = site_list[i]
        line = _line_of(node)
        desc = mutate(node)
        if desc is None:
            continue
        try:
            mutated = _render(tree)
            ast.parse(mutated)  # a mutant that will not parse is not a defect
        except (SyntaxError, ValueError):
            continue
        if mutated.strip() == _render(original_tree).strip():
            continue

        yield Mutant(
            mutant_id=f"{operator}-{i}",
            operator=operator,
            description=desc,
            source=mutated,
            line=line,
            original_line=original_lines[line - 1].strip() if 0 < line <= len(original_lines) else "",
            mutated_line="",
        )


def mutants_comparison(source: str) -> Iterator[Mutant]:
    def pick(tree):
        out = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Compare):
                for op in n.ops:
                    if type(op) in CMP_SWAPS:
                        out.append(n)
                        break
        return out

    def mutate(node):
        for idx, op in enumerate(node.ops):
            if type(op) in CMP_SWAPS:
                new_op, desc = CMP_SWAPS[type(op)]
                node.ops[idx] = new_op()
                return desc
        return None

    return _mutate_one(source, pick, mutate, "comparison")


def mutants_boolean(source: str) -> Iterator[Mutant]:
    def pick(tree):
        return [n for n in ast.walk(tree) if isinstance(n, ast.BoolOp) and type(n.op) in BOOL_SWAPS]

    def mutate(node):
        new_op, desc = BOOL_SWAPS[type(node.op)]
        node.op = new_op()
        return desc

    return _mutate_one(source, pick, mutate, "boolean")


def mutants_arithmetic(source: str) -> Iterator[Mutant]:
    """Covers both `a - b` and `a -= b`; accumulator updates are a common defect site."""

    def pick(tree):
        return [
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.BinOp, ast.AugAssign)) and type(n.op) in ARITH_SWAPS
        ]

    def mutate(node):
        new_op, desc = ARITH_SWAPS[type(node.op)]
        node.op = new_op()
        kind = "augmented assignment" if isinstance(node, ast.AugAssign) else "expression"
        return f"{desc} in {kind}"

    return _mutate_one(source, pick, mutate, "arithmetic")


def mutants_off_by_one(source: str) -> Iterator[Mutant]:
    """Perturb integer literals by one -- the classic boundary defect."""

    def pick(tree):
        return [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, int) and not isinstance(n.value, bool)
        ]

    def mutate(node):
        old = node.value
        node.value = old + 1
        return f"integer literal {old} becomes {old + 1}"

    return _mutate_one(source, pick, mutate, "offbyone")


def mutants_drop_guard(source: str) -> Iterator[Mutant]:
    """Delete an early-return/raise guard. Models add these; mutants remove them.

    This is the operator that most often separates a self-written test suite
    from an independent one: the implementer tests the happy path it had in
    mind, and the guard it wrote is the only thing covering the edge.
    """

    def pick(tree):
        out = []
        for n in ast.walk(tree):
            for field in ("body", "orelse", "finalbody"):
                block = getattr(n, field, None)
                if not isinstance(block, list):
                    continue
                for stmt in block:
                    if isinstance(stmt, ast.If) and any(
                        isinstance(b, (ast.Return, ast.Raise)) for b in stmt.body
                    ):
                        out.append((n, field, stmt))
        return out

    def mutate(site):
        parent, field, stmt = site
        block = getattr(parent, field)
        if stmt not in block or len(block) <= 1:
            return None
        block.remove(stmt)
        try:
            cond = ast.unparse(stmt.test)
        except Exception:
            cond = "<guard>"
        return f"removed the guard `if {cond}` and its early exit"

    def pick_nodes(tree):
        return pick(tree)

    # site objects are tuples, so _line_of needs the statement
    original_tree = ast.parse(source)
    sites = pick_nodes(original_tree)
    original_lines = source.splitlines()

    for i in range(len(sites)):
        tree = ast.parse(source)
        site_list = pick_nodes(tree)
        if i >= len(site_list):
            continue
        site = site_list[i]
        line = _line_of(site[2])
        desc = mutate(site)
        if desc is None:
            continue
        try:
            mutated = _render(tree)
            ast.parse(mutated)
        except (SyntaxError, ValueError):
            continue
        yield Mutant(
            mutant_id=f"dropguard-{i}",
            operator="dropguard",
            description=desc,
            source=mutated,
            line=line,
            original_line=original_lines[line - 1].strip() if 0 < line <= len(original_lines) else "",
            mutated_line="",
        )


def mutants_swap_args(source: str) -> Iterator[Mutant]:
    """Swap the first two positional arguments of a call."""

    def pick(tree):
        return [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and len(n.args) >= 2 and not any(isinstance(a, ast.Starred) for a in n.args)
        ]

    def mutate(node):
        node.args[0], node.args[1] = node.args[1], node.args[0]
        try:
            name = ast.unparse(node.func)
        except Exception:
            name = "call"
        return f"swapped the first two arguments to {name}()"

    return _mutate_one(source, pick, mutate, "swapargs")


OPERATORS = {
    "comparison": mutants_comparison,
    "boolean": mutants_boolean,
    "arithmetic": mutants_arithmetic,
    "offbyone": mutants_off_by_one,
    "dropguard": mutants_drop_guard,
    "swapargs": mutants_swap_args,
}


def generate(source: str, operators: list[str] | None = None, limit: int | None = None) -> list[Mutant]:
    """All single-site mutants of `source`, deduplicated by resulting text."""
    chosen = operators or list(OPERATORS)
    seen: set[str] = set()
    out: list[Mutant] = []
    baseline = _render(ast.parse(source)).strip()

    for name in chosen:
        for m in OPERATORS[name](source):
            key = m.source.strip()
            if key in seen or key == baseline:
                continue
            seen.add(key)
            out.append(m)
            if limit and len(out) >= limit:
                return out
    return out
