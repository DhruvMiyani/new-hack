"""Separating real defects from equivalent mutants.

Mutation testing's standard weakness: some mutants are semantically identical
to the original, so no test can kill them, and counting those as "missed
defects" slanders every test suite that was actually fine.

    if budget <= 0: return []      ->    if budget < 0: return []

For positive requests those behave identically, because a zero budget cannot
fit anything anyway. A suite that "misses" it has missed nothing.

So we do not take a mutant's word for being a defect either. Each mutant is run
against the original on synthesised inputs, and is only counted as a real
defect once we hold a **witness**: a concrete input where the two disagree, with
both outputs recorded. No witness, no defect -- the same rule the rest of this
project runs on.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

from .mutate import Mutant

WITNESS_RE = re.compile(r"::WITNESS::(\{.*\})")

_DIFF_HARNESS = r'''
import itertools, json, os, random, sys, traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import socket
    def _no_net(*a, **k):
        raise OSError("network disabled")
    for _n in ("socket", "create_connection", "getaddrinfo"):
        setattr(socket, _n, _no_net)
except Exception:
    pass

import original, mutated

random.seed(1729)

# Input pools per annotation/name heuristic. Deliberately heavy on boundaries,
# because that is where mutants hide: 0, 1, -1, empty, single-element.
INT_POOL   = [0, 1, -1, 2, 3, 5, 10, 100, -5]
FLOAT_POOL = [0.0, 1.0, -1.0, 0.5, 2.5]
STR_POOL   = ["", "a", "ab", "hello", "0", " "]
LIST_POOL  = [[], [0], [1], [1, 2], [2, 1], [1, 1, 1], [3, 3], [5, 10], [0, 1, 2], [-1, 2]]
DICT_POOL  = [{}, {"a": 0}, {"a": 1}, {"a": 1, "b": 0}, {"a": 1, "b": 2}]
BOOL_POOL  = [True, False]

def pool_for(param):
    import inspect
    ann = param.annotation
    name = getattr(ann, "__name__", "").lower() if ann is not inspect.Parameter.empty else ""
    pname = param.name.lower()
    if "int" in name: return INT_POOL
    if "float" in name: return FLOAT_POOL
    if "str" in name: return STR_POOL
    if "list" in name or "seq" in name: return LIST_POOL
    if "dict" in name or "map" in name: return DICT_POOL
    if "bool" in name: return BOOL_POOL
    # fall back on the parameter's name
    if any(h in pname for h in ("count", "n", "size", "budget", "limit", "amount", "index",
                                "idx", "drop", "page", "per_page", "total", "offset", "k")):
        return INT_POOL
    if any(h in pname for h in ("items", "values", "requests", "entries", "xs", "nums", "scores", "list")):
        return LIST_POOL
    if any(h in pname for h in ("name", "text", "word", "key", "path", "s")):
        return STR_POOL
    if any(h in pname for h in ("record", "payload", "counts", "mapping", "data")):
        return DICT_POOL
    return INT_POOL + LIST_POOL + STR_POOL

def call(fn, args):
    """Returns ('ok', value) or ('raise', 'ExcName')."""
    try:
        return ("ok", fn(*[_copy(a) for a in args]))
    except Exception as exc:
        return ("raise", type(exc).__name__)

def _copy(x):
    import copy
    try:
        return copy.deepcopy(x)
    except Exception:
        return x

def render(x):
    try:
        return repr(x)
    except Exception:
        return "<unrepr>"

def differs(a, b):
    if a[0] != b[0]:
        return True
    if a[0] == "raise":
        return a[1] != b[1]
    try:
        return not (a[1] == b[1])
    except Exception:
        return render(a[1]) != render(b[1])

TARGET = os.environ["BLINDSPOT_TARGET"]
BUDGET = int(os.environ.get("BLINDSPOT_TRIALS", "400"))

result = {"target": TARGET, "witness": None, "trials": 0, "error": None}

try:
    import inspect
    fo = getattr(original, TARGET)
    fm = getattr(mutated, TARGET)
    sig = inspect.signature(fo)
    params = [p for p in sig.parameters.values()
              if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)]
    pools = [pool_for(p) for p in params]

    combos = itertools.product(*pools) if params else [()]
    for i, args in enumerate(combos):
        if i >= BUDGET:
            break
        result["trials"] = i + 1
        ro, rm = call(fo, args), call(fm, args)
        if differs(ro, rm):
            result["witness"] = {
                "args": [render(a) for a in args],
                "original": f"{ro[0]}:{render(ro[1]) if ro[0]=='ok' else ro[1]}",
                "mutant":   f"{rm[0]}:{render(rm[1]) if rm[0]=='ok' else rm[1]}",
            }
            break
except Exception as exc:
    result["error"] = f"{type(exc).__name__}: {exc}"
    result["traceback"] = traceback.format_exc()[-800:]

print("::WITNESS::" + json.dumps(result))
'''


@dataclass
class Witness:
    """A concrete input that distinguishes a mutant from the original."""

    args: list[str]
    original: str
    mutant: str

    def render(self) -> str:
        call = ", ".join(self.args)
        return f"f({call})  ->  original {self.original}  |  mutant {self.mutant}"


@dataclass
class Equivalence:
    mutant_id: str
    is_real_defect: bool
    witness: Witness | None
    trials: int
    error: str = ""

    @property
    def status(self) -> str:
        if self.error:
            return "UNDECIDED"
        return "REAL_DEFECT" if self.is_real_defect else "LIKELY_EQUIVALENT"

    def explain(self) -> str:
        if self.error:
            return f"could not decide: {self.error}"
        if self.witness:
            return f"distinguished after {self.trials} input(s): {self.witness.render()}"
        return (
            f"no distinguishing input found in {self.trials} trials; treated as "
            "equivalent and excluded from kill-rate denominators"
        )


def _limits() -> None:
    import resource

    for res, val in ((resource.RLIMIT_CPU, 15), (resource.RLIMIT_FSIZE, 8 << 20), (resource.RLIMIT_CORE, 0)):
        try:
            resource.setrlimit(res, (val, val))
        except (ValueError, OSError):
            pass
    try:
        os.setsid()
    except OSError:
        pass


def classify(
    correct_impl: str,
    mutant: Mutant,
    target: str,
    trials: int = 400,
    timeout: float = 20.0,
) -> Equivalence:
    """Differential-test one mutant against the original."""
    with tempfile.TemporaryDirectory(prefix="blindspot-eq-") as tmp:
        for name, body in (
            ("original.py", correct_impl),
            ("mutated.py", mutant.source),
            ("diff.py", _DIFF_HARNESS),
        ):
            with open(os.path.join(tmp, name), "w") as fh:
                fh.write(body)

        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": tmp,
            "TMPDIR": tmp,
            "PYTHONDONTWRITEBYTECODE": "1",
            "BLINDSPOT_TARGET": target,
            "BLINDSPOT_TRIALS": str(trials),
        }
        try:
            proc = subprocess.run(
                [sys.executable, "-I", os.path.join(tmp, "diff.py")],
                cwd=tmp, env=env, capture_output=True, text=True,
                timeout=timeout, preexec_fn=_limits if os.name == "posix" else None,
            )
            out = proc.stdout
        except subprocess.TimeoutExpired:
            return Equivalence(mutant.mutant_id, False, None, 0, "differential test timed out")

    match = None
    for match in WITNESS_RE.finditer(out):
        pass
    if not match:
        return Equivalence(mutant.mutant_id, False, None, 0, "differential harness produced no result")

    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return Equivalence(mutant.mutant_id, False, None, 0, "malformed differential output")

    if payload.get("error"):
        return Equivalence(mutant.mutant_id, False, None, payload.get("trials", 0), payload["error"])

    w = payload.get("witness")
    witness = Witness(args=w["args"], original=w["original"], mutant=w["mutant"]) if w else None
    return Equivalence(
        mutant_id=mutant.mutant_id,
        is_real_defect=witness is not None,
        witness=witness,
        trials=payload.get("trials", 0),
    )


def partition(
    correct_impl: str,
    mutants: list[Mutant],
    target: str,
    trials: int = 400,
) -> tuple[list[Mutant], list[tuple[Mutant, Equivalence]], dict[str, Equivalence]]:
    """Split mutants into real defects and likely-equivalents.

    Returns (real, equivalent_with_reason, all_classifications).
    """
    real: list[Mutant] = []
    equivalent: list[tuple[Mutant, Equivalence]] = []
    table: dict[str, Equivalence] = {}

    for m in mutants:
        eq = classify(correct_impl, m, target, trials)
        table[m.mutant_id] = eq
        if eq.is_real_defect:
            real.append(m)
        else:
            equivalent.append((m, eq))
    return real, equivalent, table
