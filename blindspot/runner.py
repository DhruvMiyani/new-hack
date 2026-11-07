"""Runs a test suite against an implementation in a throwaway directory.

No pytest inside the sandbox: the harness collects `test_*` callables itself and
records one result per test. That keeps the dependency surface at zero and gives
per-test outcomes, which the measurement needs -- "the suite failed" is not
enough, we need to know *which* test caught the defect.

Same honesty rule as the rest of this project: a suite that errors on import is
not a suite that caught a bug, and the two are never conflated.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field

RESULT_RE = re.compile(r"::SUITE_RESULT::(\{.*\})")
MAX_OUTPUT = 12_000

# The implementation is always written to impl.py, so generated tests have one
# stable name to import. Prompts state this explicitly.
IMPL_MODULE = "impl"

_HARNESS = r'''
import json, os, sys, traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# No network from a test suite, by accident or otherwise.
try:
    import socket
    def _no_net(*a, **k):
        raise OSError("network disabled inside the test sandbox")
    for _n in ("socket", "create_connection", "getaddrinfo", "gethostbyname"):
        setattr(socket, _n, _no_net)
except Exception:
    pass

OUTCOME = {"import_error": None, "tests": []}

try:
    import tests as _suite
except BaseException as exc:
    OUTCOME["import_error"] = "%s: %s" % (type(exc).__name__, exc)
    OUTCOME["traceback"] = traceback.format_exc()[-2000:]
else:
    names = sorted(n for n in dir(_suite) if n.startswith("test"))
    for name in names:
        fn = getattr(_suite, name)
        if not callable(fn):
            continue
        entry = {"name": name, "passed": True, "error": "", "kind": ""}
        try:
            fn()
        except AssertionError as exc:
            entry.update(passed=False, kind="assertion", error=str(exc)[:400] or "assertion failed")
        except BaseException as exc:
            entry.update(passed=False, kind=type(exc).__name__, error=str(exc)[:400])
        OUTCOME["tests"].append(entry)

print("::SUITE_RESULT::" + json.dumps(OUTCOME))
'''


@dataclass
class TestOutcome:
    name: str
    passed: bool
    kind: str = ""
    error: str = ""


@dataclass
class SuiteResult:
    tests: list[TestOutcome] = field(default_factory=list)
    import_error: str = ""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration_ms: int = 0
    timed_out: bool = False

    @property
    def usable(self) -> bool:
        """A suite that would not import, or ran nothing, tells us nothing."""
        return not self.import_error and not self.timed_out and bool(self.tests)

    @property
    def failures(self) -> list[TestOutcome]:
        return [t for t in self.tests if not t.passed]

    @property
    def passed_all(self) -> bool:
        return self.usable and not self.failures

    @property
    def detects(self) -> bool:
        """Did this suite flag the code it was run against?"""
        return self.usable and bool(self.failures)

    def summary(self) -> str:
        if self.import_error:
            return f"suite did not import: {self.import_error}"
        if self.timed_out:
            return "suite exceeded its time budget"
        if not self.tests:
            return "suite defined no test functions"
        return f"{len(self.tests) - len(self.failures)}/{len(self.tests)} passed"


def _limits() -> None:
    import resource

    for res, val in (
        (resource.RLIMIT_CPU, 15),
        (resource.RLIMIT_FSIZE, 8 << 20),
        (resource.RLIMIT_CORE, 0),
    ):
        try:
            resource.setrlimit(res, (val, val))
        except (ValueError, OSError):
            pass
    try:
        os.setsid()
    except OSError:
        pass


def run_suite(impl_source: str, test_source: str, timeout: float = 20.0) -> SuiteResult:
    """Execute `test_source` against `impl_source`. Never raises."""
    with tempfile.TemporaryDirectory(prefix="blindspot-") as tmp:
        for name, body in (
            (f"{IMPL_MODULE}.py", impl_source),
            ("tests.py", test_source),
            ("harness.py", _HARNESS),
        ):
            with open(os.path.join(tmp, name), "w") as fh:
                fh.write(body)

        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": tmp,
            "TMPDIR": tmp,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        started = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(
                [sys.executable, "-I", os.path.join(tmp, "harness.py")],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                preexec_fn=_limits if os.name == "posix" else None,
            )
            out, err, code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            out = (exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            err = (exc.stderr or b"").decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            err += f"\n[killed after {timeout}s]"
            code = -9
        duration = int((time.monotonic() - started) * 1000)

    result = SuiteResult(
        stdout=RESULT_RE.sub("", out).strip()[:MAX_OUTPUT],
        stderr=err.strip()[:MAX_OUTPUT],
        exit_code=code,
        duration_ms=duration,
        timed_out=timed_out,
    )

    match = None
    for match in RESULT_RE.finditer(out):
        pass
    if match:
        try:
            payload = json.loads(match.group(1))
            result.import_error = payload.get("import_error") or ""
            result.tests = [
                TestOutcome(
                    name=t["name"],
                    passed=bool(t["passed"]),
                    kind=t.get("kind", ""),
                    error=t.get("error", ""),
                )
                for t in payload.get("tests", [])
            ]
        except json.JSONDecodeError:
            result.import_error = "harness produced malformed output"
    elif not timed_out:
        result.import_error = "harness produced no result"

    return result


def extract_code(text: str) -> str:
    """Pull Python out of a model reply, which usually arrives fenced."""
    fences = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL)
    if fences:
        return max(fences, key=len).strip() + "\n"
    return text.strip() + "\n"
