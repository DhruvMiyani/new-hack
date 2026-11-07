"""Defect detectors for AI-generated Python.

Each rule is a pair: a static matcher (cheap, finds candidates) and a probe
builder (expensive, tries to *reproduce* the defect). A rule with no probe can
only ever produce an INFERRED finding -- it is never allowed to block.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Callable, Optional

from .models import Finding, Severity

# Parameter names that look like a database handle rather than user input.
DB_PARAM_HINTS = ("conn", "connection", "cursor", "cur", "db", "database", "session", "engine")
PATH_PARAM_HINTS = ("path", "file", "filename", "filepath", "fname", "src", "dest")


@dataclass
class Rule:
    rule_id: str
    title: str
    severity: Severity
    rationale: str
    matcher: Callable[["Scanner"], None]
    probe: Optional[Callable[[Finding, dict], str]] = None


REGISTRY: dict[str, Rule] = {}


def rule(rule_id, title, severity, rationale, probe=None):
    def deco(fn):
        REGISTRY[rule_id] = Rule(rule_id, title, severity, rationale, fn, probe)
        return fn

    return deco


# --------------------------------------------------------------------------- #
# AST helpers
# --------------------------------------------------------------------------- #


def enclosing_function(parents: dict, node: ast.AST) -> Optional[ast.FunctionDef]:
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur
        cur = parents.get(cur)
    return None


def param_names(fn: ast.FunctionDef) -> list[str]:
    a = fn.args
    return [p.arg for p in (a.posonlyargs + a.args + a.kwonlyargs)]


def names_in(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def pick_param(fn: ast.FunctionDef, node: ast.AST, exclude_db=True) -> Optional[str]:
    """Which of fn's parameters does this expression actually touch?"""
    used = names_in(node)
    cands = [p for p in param_names(fn) if p in used]
    if exclude_db:
        cands = [p for p in cands if not any(h in p.lower() for h in DB_PARAM_HINTS)] or cands
    return cands[0] if cands else None


class Scanner:
    """Walks one module and collects findings."""

    def __init__(self, source: str, path: str):
        self.source = source
        self.path = path
        self.lines = source.splitlines()
        self.tree = ast.parse(source)
        self.parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(self.tree):
            for child in ast.iter_child_nodes(parent):
                self.parents[child] = parent
        self.findings: list[Finding] = []
        self._active: Optional[Rule] = None

    def snippet(self, line: int) -> str:
        idx = line - 1
        if 0 <= idx < len(self.lines):
            return self.lines[idx].strip()
        return ""

    def add(self, node: ast.AST, *, target: dict | None = None, note: str = "") -> None:
        r = self._active
        line = getattr(node, "lineno", 1)
        fn = enclosing_function(self.parents, node)
        tgt = dict(target or {})
        tgt.setdefault("function", fn.name if fn else None)
        self.findings.append(
            Finding(
                rule_id=r.rule_id,
                title=r.title,
                severity=r.severity,
                path=self.path,
                line=line,
                snippet=self.snippet(line),
                rationale=r.rationale + (f" {note}" if note else ""),
                target=tgt,
            )
        )

    def walk(self):
        return ast.walk(self.tree)

    def run(self, enabled: Optional[set[str]] = None) -> list[Finding]:
        """enabled: restrict to a ruleset version's rule ids (None = all)."""
        for r in REGISTRY.values():
            if enabled is not None and r.rule_id not in enabled:
                continue
            self._active = r
            r.matcher(self)
        self._active = None
        self.findings.sort(key=lambda f: (-f.severity.rank, f.line))
        return self.findings


# --------------------------------------------------------------------------- #
# CG001 -- mutable default argument
# --------------------------------------------------------------------------- #


def _probe_mutable_default(f: Finding, cfg: dict) -> str:
    return (
        "import target, copy\n"
        "fn = target.%(fn)s\n"
        "before = copy.deepcopy(fn.__defaults__)\n"
        "drive(fn, require_success=%(req)s); drive(fn, require_success=%(req)s); "
        "drive(fn, require_success=%(req)s)\n"
        "after = fn.__defaults__\n"
        "print('shared default BEFORE 3 calls:', before)\n"
        "print('shared default AFTER  3 calls:', after)\n"
        "report(before != after,\n"
        "       'default object is shared across calls and mutated: %%r -> %%r' %% (before, after))\n"
    ) % {"fn": f.target["function"], "req": bool(cfg.get("require_success"))}


@rule(
    "CG001",
    "Mutable default argument leaks state between calls",
    Severity.HIGH,
    "A list/dict/set default is created once at definition time, so every call "
    "that omits the argument shares and mutates the same object.",
    probe=_probe_mutable_default,
)
def _m_cg001(s: Scanner):
    for node in s.walk():
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        defaults = list(node.args.defaults) + [d for d in node.args.kw_defaults if d]
        for d in defaults:
            if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                s.add(node, target={"function": node.name})
                break
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id in ("list", "dict", "set"):
                s.add(node, target={"function": node.name})
                break


# --------------------------------------------------------------------------- #
# CG002 -- division by len() with no empty guard
# --------------------------------------------------------------------------- #


def _probe_empty_input(f: Finding, cfg: dict) -> str:
    return (
        "import target\n"
        "fn = target.%(fn)s\n"
        "called, res, exc = drive(fn, override={%(param)r: []})\n"
        "print('called with empty sequence; raised:', type(exc).__name__ if exc else None,\n"
        "      '| returned:', repr(res) if exc is None else '-')\n"
        "if exc is not None:\n"
        "    import traceback; traceback.print_exception(type(exc), exc, exc.__traceback__)\n"
        "report(isinstance(exc, (ZeroDivisionError, IndexError, StopIteration)),\n"
        "       'empty input raised %%r' %% (exc,) if exc else 'empty input handled cleanly')\n"
    ) % {"fn": f.target["function"], "param": f.target.get("param")}


@rule(
    "CG002",
    "Division by len() without an empty-input guard",
    Severity.HIGH,
    "Averaging over a caller-supplied sequence raises ZeroDivisionError the "
    "first time that sequence is empty.",
    probe=_probe_empty_input,
)
def _m_cg002(s: Scanner):
    for node in s.walk():
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.FloorDiv))):
            continue
        rhs = node.right
        is_len = (
            isinstance(rhs, ast.Call)
            and isinstance(rhs.func, ast.Name)
            and rhs.func.id == "len"
        )
        if not is_len:
            continue
        fn = enclosing_function(s.parents, node)
        if not fn or _guarded(s, fn, node):
            continue
        param = pick_param(fn, rhs)
        if param:
            s.add(node, target={"function": fn.name, "param": param})


def _guarded(s: Scanner, fn: ast.FunctionDef, node: ast.AST) -> bool:
    """Crude check for an early-return / raise guard before the risky line."""
    for n in ast.walk(fn):
        if isinstance(n, ast.If) and getattr(n, "lineno", 1 << 30) < getattr(node, "lineno", 0):
            test_src = ast.dump(n.test)
            if "len" in test_src or "Not" in test_src or "Compare" in test_src:
                if any(isinstance(b, (ast.Return, ast.Raise)) for b in ast.walk(n)):
                    return True
    return False


# --------------------------------------------------------------------------- #
# CG003 -- mutating a dict/set while iterating it
# --------------------------------------------------------------------------- #


def _probe_mutate_while_iterating(f: Finding, cfg: dict) -> str:
    """Try several container shapes: the mutating branch is usually conditional.

    A single shape is not enough -- `if counts[k] == 0: del counts[k]` never
    fires on all-nonzero input, and we would wrongly refute a real defect.
    """
    return (
        "import target\n"
        "fn = target.%(fn)s\n"
        "SHAPES = [\n"
        "    {'a': 0, 'b': 1, 'c': 0, 'd': 2},\n"
        "    {'a': None, 'b': 1, 'c': None, 'd': 2},\n"
        "    {'a': '', 'b': 'x', 'c': '', 'd': 'y'},\n"
        "    {'a': 1, 'b': 2, 'c': 3, 'd': 4},\n"
        "    {'keep': 1, 'drop': 0},\n"
        "]\n"
        "hit, log = None, []\n"
        "for shape in SHAPES:\n"
        "    called, res, exc = drive(fn, override={%(param)r: dict(shape)})\n"
        "    log.append((shape, type(exc).__name__ if exc else None, str(exc) if exc else ''))\n"
        "    if isinstance(exc, RuntimeError):\n"
        "        hit = (shape, exc)\n"
        "        break\n"
        "print('input shapes tried, and what each raised:')\n"
        "for shape, name, msg in log:\n"
        "    print('  %%r -> %%s %%s' %% (shape, name, msg))\n"
        "report(hit is not None,\n"
        "       ('input %%r raised %%r' %% hit) if hit else\n"
        "       'no tried input reached the mutating branch; defect not demonstrated')\n"
    ) % {"fn": f.target["function"], "param": f.target.get("param")}


@rule(
    "CG003",
    "Container mutated while being iterated",
    Severity.HIGH,
    "Deleting from or adding to a dict/set inside a loop over it raises "
    "RuntimeError: dictionary changed size during iteration.",
    probe=_probe_mutate_while_iterating,
)
def _m_cg003(s: Scanner):
    for node in s.walk():
        if not isinstance(node, ast.For):
            continue
        iter_names = names_in(node.iter)
        if not iter_names:
            continue
        for inner in ast.walk(node):
            mutated = None
            if isinstance(inner, ast.Delete):
                for t in inner.targets:
                    if isinstance(t, ast.Subscript):
                        mutated = names_in(t.value)
            elif (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr in ("pop", "add", "update", "remove", "clear", "setdefault")
            ):
                mutated = names_in(inner.func.value)
            if mutated and mutated & iter_names:
                fn = enclosing_function(s.parents, node)
                param = pick_param(fn, node.iter) if fn else None
                if fn and param:
                    s.add(inner, target={"function": fn.name, "param": param})
                break


# --------------------------------------------------------------------------- #
# CG005 -- shell injection through an interpolated command
# --------------------------------------------------------------------------- #


def _probe_shell_injection(f: Finding, cfg: dict) -> str:
    return (
        "import target, os, subprocess\n"
        "recorded = []\n"
        "def _rec(label):\n"
        "    def inner(*a, **k):\n"
        "        recorded.append((label, a[0] if a else k.get('args'), k.get('shell')))\n"
        "        class R:\n"
        "            returncode = 0; stdout = ''; stderr = ''\n"
        "            def communicate(self, *a, **k): return ('', '')\n"
        "            def wait(self, *a, **k): return 0\n"
        "        return R()\n"
        "    return inner\n"
        "os.system = _rec('os.system')\n"
        "os.popen = _rec('os.popen')\n"
        "for _n in ('run', 'call', 'check_call', 'check_output', 'Popen'):\n"
        "    setattr(subprocess, _n, _rec('subprocess.' + _n))\n"
        "for _mod_attr in ('system', 'popen', 'run', 'call', 'check_output', 'Popen'):\n"
        "    if hasattr(target, _mod_attr):\n"
        "        setattr(target, _mod_attr, _rec('target.' + _mod_attr))\n"
        "PAYLOAD = 'readme.txt; echo INJECTED_MARKER'\n"
        "called, res, exc = drive(target.%(fn)s, override={%(param)r: PAYLOAD})\n"
        "print('intercepted command invocations (nothing was executed):')\n"
        "for label, cmd, shell in recorded:\n"
        "    print('  %%s(shell=%%s): %%r' %% (label, shell, cmd))\n"
        "hit = any('echo INJECTED_MARKER' in str(c) for _, c, _ in recorded)\n"
        "report(hit, 'attacker-controlled text reached the shell command unescaped')\n"
    ) % {"fn": f.target["function"], "param": f.target.get("param")}


@rule(
    "CG005",
    "Shell command built from interpolated input",
    Severity.CRITICAL,
    "A caller-controlled string is concatenated into a shell command, so shell "
    "metacharacters become extra commands.",
    probe=_probe_shell_injection,
)
def _m_cg005(s: Scanner):
    for node in s.walk():
        if not isinstance(node, ast.Call):
            continue
        fname = _dotted(node.func)
        shelly = fname in ("os.system", "os.popen", "system", "popen")
        if not shelly and fname.split(".")[-1] in ("run", "call", "check_call", "check_output", "Popen"):
            shelly = any(
                kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value
                for kw in node.keywords
            )
        if not shelly or not node.args:
            continue
        if not _is_interpolated(node.args[0]):
            continue
        fn = enclosing_function(s.parents, node)
        param = pick_param(fn, node.args[0]) if fn else None
        if fn and param:
            s.add(node, target={"function": fn.name, "param": param})


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _dotted(node.value) + "." + node.attr
    return ""


def _is_interpolated(node: ast.AST) -> bool:
    """f-string, % format, .format(), or + concatenation involving a Name."""
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return bool(names_in(node))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return True
    return False


# --------------------------------------------------------------------------- #
# CG006 -- SQL built by string interpolation
# --------------------------------------------------------------------------- #


def _probe_sql_injection(f: Finding, cfg: dict) -> str:
    return (
        "import target\n"
        "statements = []\n"
        "class FakeCursor:\n"
        "    description = None\n"
        "    def execute(self, sql, params=None):\n"
        "        statements.append((sql, params)); return self\n"
        "    def executescript(self, sql):\n"
        "        statements.append((sql, None)); return self\n"
        "    def fetchall(self): return []\n"
        "    def fetchone(self): return None\n"
        "    def close(self): pass\n"
        "class FakeConn:\n"
        "    def cursor(self): return FakeCursor()\n"
        "    def execute(self, sql, params=None):\n"
        "        statements.append((sql, params)); return FakeCursor()\n"
        "    def commit(self): pass\n"
        "    def close(self): pass\n"
        "try:\n"
        "    import sqlite3; sqlite3.connect = lambda *a, **k: FakeConn()\n"
        "except Exception: pass\n"
        "if hasattr(target, 'sqlite3'):\n"
        "    target.sqlite3.connect = lambda *a, **k: FakeConn()\n"
        "PAYLOAD = \"bob' OR '1'='1\"\n"
        "overrides = {%(param)r: PAYLOAD}\n"
        "import inspect\n"
        "for p in inspect.signature(target.%(fn)s).parameters:\n"
        "    if any(h in p.lower() for h in %(db_hints)r):\n"
        "        overrides[p] = FakeConn()\n"
        "called, res, exc = drive(target.%(fn)s, override=overrides)\n"
        "print('SQL statements the function handed to the driver:')\n"
        "for sql, params in statements:\n"
        "    print('  sql    =', repr(sql))\n"
        "    print('  params =', repr(params))\n"
        "inlined = [s for s, p in statements if \"OR '1'='1\" in str(s)]\n"
        "report(bool(inlined),\n"
        "       'payload was inlined into the SQL text instead of being bound as a parameter')\n"
    ) % {
        "fn": f.target["function"],
        "param": f.target.get("param"),
        "db_hints": DB_PARAM_HINTS,
    }


@rule(
    "CG006",
    "SQL query built by string interpolation",
    Severity.CRITICAL,
    "Caller input is concatenated into SQL text rather than bound as a "
    "parameter, so quotes in the input change the query's meaning.",
    probe=_probe_sql_injection,
)
def _m_cg006(s: Scanner):
    sql_kw = ("select ", "insert ", "update ", "delete ", "drop ", "where ")
    for node in s.walk():
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in ("execute", "executescript", "executemany"):
            continue
        if not node.args or not _is_interpolated(node.args[0]):
            continue
        try:
            text = ast.unparse(node.args[0]).lower()
        except Exception:
            text = ""
        if not any(k in text for k in sql_kw):
            continue
        fn = enclosing_function(s.parents, node)
        param = pick_param(fn, node.args[0]) if fn else None
        if fn and param:
            s.add(node, target={"function": fn.name, "param": param})


# --------------------------------------------------------------------------- #
# CG007 -- file opened without being closed
# --------------------------------------------------------------------------- #


def _probe_unclosed_file(f: Finding, cfg: dict) -> str:
    return (
        "import target, builtins, os\n"
        "with builtins.open('fixture.txt', 'w') as fh:\n"
        "    fh.write('line one\\nline two\\n')\n"
        "opened = []\n"
        "_real_open = builtins.open\n"
        "def tracking_open(*a, **k):\n"
        "    fh = _real_open(*a, **k); opened.append((a[0] if a else '?', fh)); return fh\n"
        "builtins.open = tracking_open\n"
        "if hasattr(target, 'open'):\n"
        "    target.open = tracking_open\n"
        "called, res, exc = drive(target.%(fn)s, override={%(param)r: 'fixture.txt'})\n"
        "builtins.open = _real_open\n"
        "leaked = [(n, fh) for n, fh in opened if not fh.closed]\n"
        "print('file handles opened during the call:', len(opened))\n"
        "for n, fh in opened:\n"
        "    print('  %%r closed=%%s' %% (n, fh.closed))\n"
        "for _n, fh in leaked:\n"
        "    try: fh.close()\n"
        "    except Exception: pass\n"
        "report(bool(leaked), '%%d handle(s) still open after the function returned' %% len(leaked))\n"
    ) % {"fn": f.target["function"], "param": f.target.get("param")}


@rule(
    "CG007",
    "File opened without a context manager",
    Severity.MEDIUM,
    "open() outside a with-block leaks the descriptor if the function returns "
    "or raises before close().",
    probe=_probe_unclosed_file,
)
def _m_cg007(s: Scanner):
    withs = set()
    for node in s.walk():
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                for n in ast.walk(item.context_expr):
                    withs.add(id(n))
    for node in s.walk():
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open"):
            continue
        if id(node) in withs:
            continue
        fn = enclosing_function(s.parents, node)
        if not fn:
            continue
        if any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "close"
            for n in ast.walk(fn)
        ):
            continue
        param = pick_param(fn, node)
        names = param_names(fn)
        param = param or next((p for p in names if any(h in p.lower() for h in PATH_PARAM_HINTS)), None)
        if param:
            s.add(node, target={"function": fn.name, "param": param})


# --------------------------------------------------------------------------- #
# CG009 -- attribute access on a possibly-None lookup
# --------------------------------------------------------------------------- #


def _probe_none_attribute(f: Finding, cfg: dict) -> str:
    return (
        "import target\n"
        "called, res, exc = drive(target.%(fn)s, override={%(param)r: {}})\n"
        "print('called with a dict missing the key; raised:',\n"
        "      type(exc).__name__ if exc else None, '|', exc)\n"
        "if exc is not None:\n"
        "    import traceback; traceback.print_exception(type(exc), exc, exc.__traceback__)\n"
        "report(isinstance(exc, (AttributeError, TypeError)),\n"
        "       'missing key yielded None and the attribute access failed: %%r' %% (exc,))\n"
    ) % {"fn": f.target["function"], "param": f.target.get("param")}


@rule(
    "CG009",
    "Attribute or index access on an unchecked .get() result",
    Severity.HIGH,
    "dict.get() returns None for a missing key; using the result directly "
    "raises AttributeError or TypeError instead of handling the absence.",
    probe=_probe_none_attribute,
)
def _m_cg009(s: Scanner):
    for node in s.walk():
        inner = None
        if isinstance(node, ast.Attribute):
            inner = node.value
        elif isinstance(node, ast.Subscript):
            inner = node.value
        if not (
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "get"
            and len(inner.args) == 1
        ):
            continue
        fn = enclosing_function(s.parents, node)
        param = pick_param(fn, inner) if fn else None
        if fn and param:
            s.add(node, target={"function": fn.name, "param": param})


# --------------------------------------------------------------------------- #
# Static-only rules: no probe, so these can never block a merge.
# --------------------------------------------------------------------------- #


@rule(
    "CG008",
    "Bare except swallows all exceptions",
    Severity.MEDIUM,
    "A bare except also catches KeyboardInterrupt and SystemExit, hiding real "
    "failures and making the process hard to stop. Not independently "
    "reproducible, so reported as a judgement call.",
)
def _m_cg008(s: Scanner):
    for node in s.walk():
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            s.add(node, target={})


@rule(
    "CG010",
    "assert used for runtime validation",
    Severity.MEDIUM,
    "asserts are stripped under python -O, so the check silently disappears in "
    "optimised deployments. Depends on deploy flags, so not reproducible here.",
)
def _m_cg010(s: Scanner):
    for node in s.walk():
        if not isinstance(node, ast.Assert):
            continue
        fn = enclosing_function(s.parents, node)
        if fn and not fn.name.startswith("test"):
            s.add(node, target={"function": fn.name})
