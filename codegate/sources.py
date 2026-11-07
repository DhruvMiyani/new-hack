"""Turns whatever the user pasted into ASI:One into analysable Python.

Three shapes are accepted: a bare snippet, a unified diff, or a GitHub PR URL.
Probes need the *post-image* of a file -- the code as it will exist after the
merge. When we can only recover fragments (a hunk without context), we say so
and fall back to static-only analysis rather than pretending we can execute it.
"""

from __future__ import annotations

import ast
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

PR_URL = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)")
BLOB_URL = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)/blob/([^/]+)/(.+\.py)")
RAW_URL = re.compile(r"raw\.githubusercontent\.com/([\w.-]+)/([\w.-]+)/([^/]+)/(.+\.py)")
USER_AGENT = "codegate-verification-agent"


@dataclass
class Unit:
    """One analysable Python file."""

    path: str
    source: str
    executable: bool = True   # False => reconstructed fragment, static analysis only
    note: str = ""


@dataclass
class Submission:
    units: list[Unit] = field(default_factory=list)
    ref: str = "pasted snippet"
    kind: str = "snippet"
    warnings: list[str] = field(default_factory=list)

    @property
    def analysable(self) -> list[Unit]:
        return [u for u in self.units if u.source.strip()]


def _get(url: str, token: str | None = None, accept: str = "application/vnd.github+json") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def parses(src: str) -> bool:
    try:
        ast.parse(src)
        return True
    except SyntaxError:
        return False


# --------------------------------------------------------------------------- #
# unified diff
# --------------------------------------------------------------------------- #


def looks_like_diff(text: str) -> bool:
    head = text.lstrip()
    return head.startswith(("diff --git", "--- ", "Index: ", "@@")) or "\n@@ " in text


def from_diff(text: str) -> Submission:
    """Reconstruct each file's post-image from a unified diff.

    A diff only contains changed hunks, so for a modified file the result is a
    fragment: real code, but missing everything the diff did not touch. We mark
    those non-executable unless they happen to parse as a whole module.
    """
    sub = Submission(ref="unified diff", kind="diff")
    current: str | None = None
    buckets: dict[str, list[str]] = {}
    whole_file: dict[str, bool] = {}

    for line in text.splitlines():
        m = re.match(r"diff --git a/(\S+) b/(\S+)", line)
        if m:
            current = m.group(2)
            buckets.setdefault(current, [])
            whole_file[current] = False
            continue
        m = re.match(r"\+\+\+ b/(\S+)", line) or re.match(r"\+\+\+ (\S+)", line)
        if m and m.group(1) != "/dev/null":
            current = m.group(1)
            buckets.setdefault(current, [])
            whole_file.setdefault(current, False)
            continue
        if line.startswith("new file mode") and current:
            whole_file[current] = True
            continue
        if current is None or line.startswith(("---", "index ", "diff ", "similarity", "rename")):
            continue
        if line.startswith("@@"):
            continue
        if line.startswith("+"):
            buckets[current].append(line[1:])
        elif line.startswith(" "):
            buckets[current].append(line[1:])
        # '-' lines are pre-image; dropped.

    for path, lines in buckets.items():
        if not path.endswith(".py"):
            continue
        src = "\n".join(lines) + "\n"
        if not src.strip():
            continue
        ok = parses(src)
        sub.units.append(
            Unit(
                path=path,
                source=src,
                executable=ok,
                note=""
                if ok
                else "reconstructed from diff hunks and does not parse standalone; static analysis only",
            )
        )
    if not sub.units:
        sub.warnings.append("No Python post-image could be reconstructed from the diff.")
    return sub


# --------------------------------------------------------------------------- #
# GitHub
# --------------------------------------------------------------------------- #


def from_github_pr(owner: str, repo: str, number: str, token: str | None = None, limit: int = 8) -> Submission:
    """Fetch the PR's changed .py files at the head SHA -- real post-images."""
    sub = Submission(ref=f"{owner}/{repo}#{number}", kind="github_pr")
    api = "https://api.github.com"
    try:
        pr = json.loads(_get(f"{api}/repos/{owner}/{repo}/pulls/{number}", token))
        head_sha = pr["head"]["sha"]
        files = json.loads(_get(f"{api}/repos/{owner}/{repo}/pulls/{number}/files?per_page=100", token))
    except urllib.error.HTTPError as exc:
        hint = ""
        if exc.code in (401, 403):
            hint = " GitHub rate-limited or denied the request; set GITHUB_TOKEN for a higher limit."
        elif exc.code == 404:
            hint = " Not found -- is the repository private?"
        sub.warnings.append(f"GitHub API returned {exc.code}.{hint}")
        return sub
    except Exception as exc:
        sub.warnings.append(f"Could not reach GitHub: {exc!r}")
        return sub

    py = [f for f in files if f["filename"].endswith(".py") and f["status"] != "removed"]
    if len(py) > limit:
        sub.warnings.append(f"PR touches {len(py)} Python files; analysing the first {limit}.")
        py = py[:limit]

    for f in py:
        raw = f"https://raw.githubusercontent.com/{owner}/{repo}/{head_sha}/{f['filename']}"
        try:
            src = _get(raw, token, accept="text/plain").decode("utf-8", "replace")
        except Exception as exc:
            sub.warnings.append(f"Could not fetch {f['filename']}: {exc!r}")
            continue
        sub.units.append(Unit(path=f["filename"], source=src, executable=parses(src)))

    if not py:
        sub.warnings.append("This PR changes no Python files.")
    sub.ref = f"{owner}/{repo}#{number} @ {head_sha[:8]}"
    return sub


def from_github_file(owner: str, repo: str, ref: str, path: str, token: str | None = None) -> Submission:
    sub = Submission(ref=f"{owner}/{repo}/{path}@{ref}", kind="github_file")
    raw = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
    try:
        src = _get(raw, token, accept="text/plain").decode("utf-8", "replace")
    except Exception as exc:
        sub.warnings.append(f"Could not fetch {path}: {exc!r}")
        return sub
    sub.units.append(Unit(path=path, source=src, executable=parses(src)))
    return sub


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #


def resolve(text: str, token: str | None = None) -> Submission:
    """Work out what the user gave us and return analysable units."""
    token = token or os.getenv("GITHUB_TOKEN") or None
    text = _strip_fences(text)

    m = PR_URL.search(text)
    if m:
        return from_github_pr(*m.groups(), token=token)
    m = BLOB_URL.search(text) or RAW_URL.search(text)
    if m:
        owner, repo, ref, path = m.groups()
        return from_github_file(owner, repo, ref, path, token)
    if looks_like_diff(text):
        return from_diff(text)

    sub = Submission(ref="pasted snippet", kind="snippet")
    if parses(text):
        sub.units.append(Unit(path="submitted.py", source=text, executable=True))
    else:
        sub.warnings.append(
            "That is not valid Python, a unified diff, or a GitHub PR URL. "
            "Paste a function, a `git diff`, or a link like "
            "https://github.com/owner/repo/pull/123"
        )
    return sub


def _strip_fences(text: str) -> str:
    """Chat clients wrap code in markdown fences; strip one layer if present."""
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines)
    return text
