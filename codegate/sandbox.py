"""Runs a probe script against submitted code in a throwaway directory.

Defence in depth, not a jail. See SANDBOX_CAVEAT below -- we state the limits
rather than implying containment we do not have.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

SANDBOX_CAVEAT = (
    "Probes run as a child python process with: isolated mode (-I), a throwaway "
    "cwd, CPU/file-size/core rlimits, a wall-clock kill, scrubbed env, and socket "
    "calls monkeypatched to raise. That stops accidental damage and network "
    "egress. It is NOT a container or a seccomp jail -- for untrusted third-party "
    "code, run this agent inside a disposable container."
)

MAX_OUTPUT = 16_000  # chars kept per stream before truncation

_PREAMBLE = r'''
import json, os, sys

# -I (isolated mode) implies -P, which keeps the script's own directory off
# sys.path. The probe needs to import target.py sitting next to it.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# --- defence in depth: no network from probe or target code -----------------
try:
    import socket
    class _NetBlocked(OSError):
        pass
    def _no_net(*a, **k):
        raise _NetBlocked("network disabled inside probe sandbox")
    for _n in ("socket", "create_connection", "getaddrinfo", "gethostbyname"):
        setattr(socket, _n, _no_net)
except Exception:
    pass

_RESULT = {"reproduced": False, "detail": "probe did not report a result"}

def report(reproduced, detail=""):
    """Probes call this exactly once to state whether the defect manifested."""
    _RESULT["reproduced"] = bool(reproduced)
    _RESULT["detail"] = str(detail)

def _emit():
    sys.stdout.flush()
    print("::PROBE_RESULT::" + json.dumps(_RESULT))

# --- tiny type-guided driver -------------------------------------------------
# Probes need to *call* the submitted function without knowing its signature.
# We try candidate arguments for each required parameter until a call gets past
# argument binding. This is a driver, not a fuzzer: first binding call wins.
_CANDIDATES = {
    "list":  [[1, 2, 3], []],
    "dict":  [{"a": 1}, {}],
    "str":   ["x", ""],
    "int":   [1, 0],
    "float": [1.0],
    "bool":  [True],
    None:    [[1, 2, 3], "x", 1, {"a": 1}, 1.0, True, None],
}

def _candidates_for(param):
    import inspect
    ann = param.annotation
    name = getattr(ann, "__name__", None) if ann is not inspect.Parameter.empty else None
    return _CANDIDATES.get(name, _CANDIDATES[None])

def drive(fn, override=None, skip_defaults=True, require_success=False):
    """Call fn with synthesised arguments.

    override: {param_name: value} to force specific arguments.
    skip_defaults: leave defaulted params unpassed (needed to expose default-arg bugs).
    require_success: keep trying candidates until a call returns without raising.
        Needed by probes that observe a side effect, where an argument that
        crashes on the way in tells us nothing.
    Returns (called, result, exception).
    """
    import inspect, itertools
    override = override or {}
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        try:
            return True, fn(), None
        except Exception as exc:
            return True, None, exc

    _last = None
    required, pools = [], []
    for pname, p in sig.parameters.items():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if pname in override:
            required.append(pname)
            pools.append([override[pname]])
        elif p.default is not inspect.Parameter.empty:
            if not skip_defaults:
                required.append(pname)
                pools.append([p.default])
        else:
            required.append(pname)
            pools.append(_candidates_for(p))

    for combo in itertools.islice(itertools.product(*pools) if pools else [()], 64):
        kwargs = dict(zip(required, combo))
        try:
            return True, fn(**kwargs), None
        except TypeError as exc:
            # Could be bad binding (try next combo) or a real defect in the body.
            if "argument" in str(exc) and fn.__name__ in str(exc):
                continue
            _last = exc
            if require_success:
                continue
            return True, None, exc
        except Exception as exc:
            _last = exc
            if require_success:
                continue
            return True, None, exc
    return False, None, _last
'''


def _limits() -> None:
    """Applied in the child before exec. Best-effort; platforms differ."""
    import resource

    for res, soft, hard in (
        (resource.RLIMIT_CPU, 10, 10),
        (resource.RLIMIT_FSIZE, 8 << 20, 8 << 20),
        (resource.RLIMIT_CORE, 0, 0),
    ):
        try:
            resource.setrlimit(res, (soft, hard))
        except (ValueError, OSError):
            pass
    try:
        os.setsid()
    except OSError:
        pass


@dataclass
class RunResult:
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    timed_out: bool
    truncated: bool


def run_probe(target_source: str, probe_body: str, timeout: float = 12.0) -> RunResult:
    """Write target + probe into a temp dir and execute the probe."""
    script = _PREAMBLE + "\n\ntry:\n"
    script += "".join("    " + ln + "\n" for ln in probe_body.splitlines())
    script += (
        "except Exception as _e:\n"
        "    import traceback; traceback.print_exc()\n"
        "    report(False, 'probe itself raised: %r' % (_e,))\n"
        "finally:\n"
        "    _emit()\n"
    )

    with tempfile.TemporaryDirectory(prefix="codegate-probe-") as tmp:
        with open(os.path.join(tmp, "target.py"), "w") as fh:
            fh.write(target_source)
        probe_path = os.path.join(tmp, "probe.py")
        with open(probe_path, "w") as fh:
            fh.write(script)

        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": tmp,
            "TMPDIR": tmp,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONWARNINGS": "default",
        }
        started = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(
                [sys.executable, "-I", probe_path],
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
            out = exc.stdout or ""
            err = (exc.stderr or "") + f"\n[probe killed after {timeout}s wall clock]"
            if isinstance(out, bytes):
                out = out.decode(errors="replace")
            if isinstance(err, bytes):
                err = err.decode(errors="replace")
            code = -9
        duration = int((time.monotonic() - started) * 1000)

    truncated = len(out) > MAX_OUTPUT or len(err) > MAX_OUTPUT
    return RunResult(
        stdout=out[:MAX_OUTPUT],
        stderr=err[:MAX_OUTPUT],
        exit_code=code,
        duration_ms=duration,
        timed_out=timed_out,
        truncated=truncated,
    )
