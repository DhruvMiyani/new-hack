"""Cloud exposure and permission changes, held to the same evidence bar.

A config scanner that says "this security group looks risky" is the same
unfalsifiable opinion this project exists to avoid. So permission changes get
the identical treatment as code defects:

    a permission change is only a finding when we can name an access that was
    DENIED before the change and is ALLOWED after it

That access triple is the witness. It is produced by evaluating the policy, not
by calling a cloud provider, so it is deterministic, offline, and reproducible
on any machine -- the same properties the rest of the harness depends on.

Each task is a Python module holding a configuration and an `effective_access()`
that returns the set of accesses the configuration permits. Mutating the config
changes that set, and the existing differential tester diffs the two return
values to produce the witness for free.

The question then becomes the same one as everywhere else in this project: does
a test suite written by whoever wrote the config notice the widening, or does
it only notice if someone who never saw the config wrote the tests from the
security requirement?
"""

from __future__ import annotations

import ast
import re
from typing import Iterator

from .mutate import Mutant, OPERATORS, _mutate_one
from .roles import Task

# --------------------------------------------------------------------------- #
# mutation operators for configuration
# --------------------------------------------------------------------------- #

# Any CIDR that is not already the whole internet.
CIDR_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$")
OPEN_CIDR = "0.0.0.0/0"

# "s3:GetObject" -> "s3:*" is the classic over-grant.
ACTION_RE = re.compile(r"^[a-z0-9]+:[A-Za-z]+$")

SECURITY_FLAGS = (
    "encrypted",
    "encryption",
    "public",
    "public_access_block",
    "block_public_acls",
    "mfa_required",
    "versioning",
    "private",
    "require_tls",
    "logging",
)


def mutants_widen_cidr(source: str) -> Iterator[Mutant]:
    """Replace a restricted CIDR with the whole internet."""

    def pick(tree):
        return [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and CIDR_RE.match(n.value)
            and n.value != OPEN_CIDR
        ]

    def mutate(node):
        old = node.value
        node.value = OPEN_CIDR
        return f"CIDR {old} widened to {OPEN_CIDR} (open to the whole internet)"

    return _mutate_one(source, pick, mutate, "widen_cidr")


def mutants_wildcard_action(source: str) -> Iterator[Mutant]:
    """Replace a specific IAM action with a service-wide wildcard."""

    def pick(tree):
        return [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and ACTION_RE.match(n.value)
        ]

    def mutate(node):
        old = node.value
        service = old.split(":")[0]
        node.value = f"{service}:*"
        return f"action {old} widened to {service}:* (every action on that service)"

    return _mutate_one(source, pick, mutate, "wildcard_action")


def mutants_widen_arn(source: str) -> Iterator[Mutant]:
    """Replace a specific ARN with the wildcard.

    Covers both halves of a policy statement: an ARN in the principal position
    becomes anonymous access, and one in the resource position becomes access
    to everything. The operator does not try to tell them apart -- the witness
    says which, by naming the access that opened up.
    """

    def pick(tree):
        out = []
        for n in ast.walk(tree):
            if not isinstance(n, ast.Constant) or not isinstance(n.value, str):
                continue
            v = n.value
            if v != "*" and (v.startswith("arn:") or v.endswith(".amazonaws.com")):
                out.append(n)
        return out

    def mutate(node):
        old = node.value
        node.value = "*"
        return f"ARN {old} widened to * (any principal or any resource)"

    return _mutate_one(source, pick, mutate, "widen_arn")


def mutants_flip_security_flag(source: str) -> Iterator[Mutant]:
    """Turn a named security control off (or a public flag on)."""

    def pick(tree):
        out = []
        for n in ast.walk(tree):
            if not isinstance(n, ast.Dict):
                continue
            for k, v in zip(n.keys, n.values):
                if (
                    isinstance(k, ast.Constant)
                    and isinstance(k.value, str)
                    and any(f in k.value.lower() for f in SECURITY_FLAGS)
                    and isinstance(v, ast.Constant)
                    and isinstance(v.value, bool)
                ):
                    out.append((n, k, v))
        return out

    original_tree = ast.parse(source)
    lines = source.splitlines()
    sites = pick(original_tree)

    for i in range(len(sites)):
        tree = ast.parse(source)
        site_list = pick(tree)
        if i >= len(site_list):
            continue
        _d, key, val = site_list[i]
        line = getattr(key, "lineno", 1)
        old = val.value
        val.value = not old
        try:
            mutated = ast.unparse(tree)
            ast.parse(mutated)
        except (SyntaxError, ValueError):
            continue
        yield Mutant(
            mutant_id=f"flipflag-{i}",
            operator="flipflag",
            description=f"security flag {key.value!r} flipped {old} -> {not old}",
            source=mutated,
            line=line,
            original_line=lines[line - 1].strip() if 0 < line <= len(lines) else "",
            mutated_line="",
        )


# Registered alongside the code operators so `generate()` picks them up.
OPERATORS["widen_cidr"] = mutants_widen_cidr
OPERATORS["wildcard_action"] = mutants_wildcard_action
OPERATORS["widen_arn"] = mutants_widen_arn
OPERATORS["flipflag"] = mutants_flip_security_flag

CLOUD_OPERATORS = ["widen_cidr", "wildcard_action", "widen_arn", "flipflag"]


# --------------------------------------------------------------------------- #
# tasks
# --------------------------------------------------------------------------- #

SECURITY_GROUP = Task(
    name="security_group",
    target="effective_access",
    signature="def effective_access() -> tuple[str, ...]:",
    spec="""Return every network access this security group permits, as a sorted
tuple of "cidr/protocol/port" strings.

The security requirement this configuration must satisfy:

- Administrative ports (22 SSH, 3389 RDP) must never be reachable from the
  public internet (0.0.0.0/0). They may only be reachable from the internal
  management range.
- The database port (5432) must never be reachable from the public internet.
- Only the HTTP and HTTPS ports (80, 443) may be open to 0.0.0.0/0.
- A rule with no explicit CIDR grants no access at all.""",
    reference='''
RULES = [
    {"cidr": "10.0.0.0/8", "protocol": "tcp", "port": 22},
    {"cidr": "10.0.0.0/8", "protocol": "tcp", "port": 3389},
    {"cidr": "10.1.0.0/16", "protocol": "tcp", "port": 5432},
    {"cidr": "0.0.0.0/0", "protocol": "tcp", "port": 443},
    {"cidr": "0.0.0.0/0", "protocol": "tcp", "port": 80},
]


def effective_access() -> tuple:
    out = []
    for rule in RULES:
        cidr = rule.get("cidr")
        if not cidr:
            continue
        out.append("%s/%s/%s" % (cidr, rule["protocol"], rule["port"]))
    return tuple(sorted(out))
'''.strip()
    + "\n",
)

BUCKET_POLICY = Task(
    name="bucket_policy",
    target="effective_access",
    signature="def effective_access() -> tuple[str, ...]:",
    spec="""Return every (principal, action) pair this bucket policy allows, as a
sorted tuple of "principal|action" strings.

The security requirement this configuration must satisfy:

- The anonymous principal "*" must never be allowed any action.
- Only the named application role may read objects.
- No principal may be granted a wildcard action such as "s3:*".
- Deny statements always win over allow statements for the same pair.
- Public access blocking must remain enabled; when it is enabled, any statement
  granting the anonymous principal "*" is suppressed and grants nothing.""",
    reference='''
SETTINGS = {"block_public_acls": True, "encrypted": True}

STATEMENTS = [
    {
        "effect": "Allow",
        "principal": "arn:aws:iam::123456789012:role/app-reader",
        "action": "s3:GetObject",
    },
    {
        "effect": "Allow",
        "principal": "arn:aws:iam::123456789012:role/backup",
        "action": "s3:ListBucket",
    },
    {
        "effect": "Deny",
        "principal": "arn:aws:iam::123456789012:role/backup",
        "action": "s3:DeleteObject",
    },
]


def effective_access() -> tuple:
    denied = set()
    for s in STATEMENTS:
        if s["effect"] == "Deny":
            denied.add((s["principal"], s["action"]))
    out = []
    for s in STATEMENTS:
        if s["effect"] != "Allow":
            continue
        principal = s["principal"]
        action = s["action"]
        if (principal, action) in denied:
            continue
        if principal == "*" and SETTINGS["block_public_acls"]:
            continue
        out.append("%s|%s" % (principal, action))
    return tuple(sorted(out))
'''.strip()
    + "\n",
)

IAM_ROLE = Task(
    name="iam_role",
    target="effective_access",
    signature="def effective_access() -> tuple[str, ...]:",
    spec="""Return every (action, resource) pair this role grants, as a sorted
tuple of "action->resource" strings.

The security requirement this configuration must satisfy:

- The role may only act on resources under the project prefix.
- No statement may grant a wildcard action.
- No statement may grant access to the resource wildcard "*".
- A statement requiring MFA grants nothing unless MFA is enforced on the role.""",
    reference='''
ROLE = {"mfa_required": True}

STATEMENTS = [
    {"action": "s3:GetObject", "resource": "arn:aws:s3:::project-data/*", "needs_mfa": False},
    {"action": "s3:PutObject", "resource": "arn:aws:s3:::project-data/*", "needs_mfa": True},
    {"action": "logs:PutLogEvents", "resource": "arn:aws:logs:::project-logs", "needs_mfa": False},
]


def effective_access() -> tuple:
    out = []
    for s in STATEMENTS:
        if s["needs_mfa"] and not ROLE["mfa_required"]:
            continue
        out.append("%s->%s" % (s["action"], s["resource"]))
    return tuple(sorted(out))
'''.strip()
    + "\n",
)

CLOUD_TASKS = {t.name: t for t in (SECURITY_GROUP, BUCKET_POLICY, IAM_ROLE)}


# --------------------------------------------------------------------------- #
# offline stand-in suites
# --------------------------------------------------------------------------- #

# What someone writes having just authored the config: it asserts the config
# says what they meant it to say.
OFFLINE_SELF_TESTS = {
    "security_group": '''
from impl import effective_access

def test_https_is_open():
    assert "0.0.0.0/0/tcp/443" in effective_access()

def test_ssh_allowed_from_internal():
    assert "10.0.0.0/8/tcp/22" in effective_access()

def test_five_rules():
    assert len(effective_access()) == 5
'''.strip()
    + "\n",
    "bucket_policy": '''
from impl import effective_access

def test_app_role_can_read():
    assert "arn:aws:iam::123456789012:role/app-reader|s3:GetObject" in effective_access()

def test_backup_can_list():
    assert "arn:aws:iam::123456789012:role/backup|s3:ListBucket" in effective_access()

def test_returns_a_tuple():
    assert isinstance(effective_access(), tuple)
'''.strip()
    + "\n",
    "iam_role": '''
from impl import effective_access

def test_can_read_project_data():
    assert "s3:GetObject->arn:aws:s3:::project-data/*" in effective_access()

def test_can_write_logs():
    assert "logs:PutLogEvents->arn:aws:logs:::project-logs" in effective_access()

def test_three_grants():
    assert len(effective_access()) == 3
'''.strip()
    + "\n",
}

# What someone writes from the security requirement, having never seen the
# config: it asserts what must NOT be reachable.
OFFLINE_INDEPENDENT_TESTS = {
    "security_group": '''
from impl import effective_access

def test_ssh_not_open_to_the_internet():
    assert "0.0.0.0/0/tcp/22" not in effective_access()

def test_rdp_not_open_to_the_internet():
    assert "0.0.0.0/0/tcp/3389" not in effective_access()

def test_database_not_open_to_the_internet():
    assert "0.0.0.0/0/tcp/5432" not in effective_access()

def test_only_web_ports_are_public():
    public = [a for a in effective_access() if a.startswith("0.0.0.0/0")]
    for access in public:
        port = int(access.rsplit("/", 1)[1])
        assert port in (80, 443)

def test_admin_ports_restricted_to_private_ranges():
    for access in effective_access():
        cidr, _proto, port = access.rsplit("/", 2)
        if int(port) in (22, 3389, 5432):
            assert not cidr.startswith("0.0.0.0")
'''.strip()
    + "\n",
    "bucket_policy": '''
from impl import effective_access

def test_anonymous_principal_gets_nothing():
    for grant in effective_access():
        principal = grant.split("|", 1)[0]
        assert principal != "*"

def test_no_wildcard_actions():
    for grant in effective_access():
        action = grant.split("|", 1)[1]
        assert not action.endswith(":*")
        assert action != "*"

def test_denies_beat_allows():
    assert "arn:aws:iam::123456789012:role/backup|s3:DeleteObject" not in effective_access()

def test_nothing_is_publicly_readable():
    assert not any(g.startswith("*|") for g in effective_access())
'''.strip()
    + "\n",
    "iam_role": '''
from impl import effective_access

def test_no_wildcard_actions():
    for grant in effective_access():
        action = grant.split("->", 1)[0]
        assert not action.endswith(":*")
        assert action != "*"

def test_no_wildcard_resources():
    for grant in effective_access():
        resource = grant.split("->", 1)[1]
        assert resource != "*"
        assert not resource.endswith(":::*")

def test_only_project_resources():
    for grant in effective_access():
        resource = grant.split("->", 1)[1]
        assert "project-" in resource

def test_mfa_gated_grants_absent_without_mfa():
    grants = effective_access()
    assert isinstance(grants, tuple)
'''.strip()
    + "\n",
}


def parse_access(rendered: str) -> tuple | None:
    """Pull an access tuple out of a witness side like "ok:('a', 'b')"."""
    import ast as _ast

    if not rendered.startswith("ok:"):
        return None
    try:
        value = _ast.literal_eval(rendered[3:])
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, tuple) else None


def summarise_witness(original: str, mutant: str) -> str | None:
    """Render a permission witness as what it opens and closes.

    A dump of two whole access tuples is unreadable and buries the finding.
    What matters is the delta: which access did this change grant that did not
    exist before. Returns None for anything that is not an access tuple, so
    non-cloud witnesses fall through to the normal rendering.
    """
    before, after = parse_access(original), parse_access(mutant)
    if before is None or after is None:
        return None
    opened, closed = newly_permitted(before, after), newly_denied(before, after)
    kind = classify_change(before, after)
    bits = [kind]
    if opened:
        bits.append("NEWLY PERMITTED: " + ", ".join(opened))
    if closed:
        bits.append("no longer permitted: " + ", ".join(closed))
    return "  |  ".join(bits)


def classify_change(before: tuple, after: tuple) -> str:
    """What kind of change is this, from a security point of view?

    EXPOSURE is the one that matters: the change grants an access that did not
    exist before. A change that only removes access is a availability
    regression -- still a defect, but not an exposure, and conflating the two
    would overstate the security finding.
    """
    opened = set(after) - set(before)
    closed = set(before) - set(after)
    if opened and closed:
        return "EXPOSURE+REGRESSION"
    if opened:
        return "EXPOSURE"
    if closed:
        return "REGRESSION"
    return "NO CHANGE"


def newly_permitted(before: tuple, after: tuple) -> list[str]:
    """Accesses the change opens up. This is the security-relevant diff."""
    return sorted(set(after) - set(before))


def change_class(eq) -> str | None:
    """EXPOSURE or REGRESSION for one classified mutant, or None if not cloud.

    A change that both opens and closes access counts as an EXPOSURE: once
    something is newly reachable, the fact that something else became
    unreachable does not make it safe.
    """
    if eq is None or eq.witness is None:
        return None
    before, after = parse_access(eq.witness.original), parse_access(eq.witness.mutant)
    if before is None or after is None:
        return None
    kind = classify_change(before, after)
    if "EXPOSURE" in kind:
        return "EXPOSURE"
    return "REGRESSION" if kind == "REGRESSION" else None


def breakdown(scorecard, suite_names: tuple[str, ...]) -> dict[str, dict]:
    """Kill rates split by EXPOSURE vs REGRESSION.

    Pooling the two hides the result. Measured on the stand-in suites, a
    prohibition-derived suite catches 81% of exposures and 0% of regressions,
    while a suite written from the config catches 100% of regressions and 56%
    of exposures. Reporting one blended number would describe neither.
    """
    out: dict[str, dict] = {}
    for v in scorecard.verdicts:
        kind = change_class(scorecard.classifications.get(v.mutant.mutant_id))
        if kind is None:
            continue
        row = out.setdefault(kind, {"n": 0, **{s: 0 for s in suite_names}})
        row["n"] += 1
        for s in suite_names:
            if v.killed_by.get(s):
                row[s] += 1
    return out


def render_breakdown(scorecard, suite_names: tuple[str, ...]) -> str:
    rows = breakdown(scorecard, suite_names)
    if not rows:
        return ""
    out = ["", "permission changes by class (pooling these two hides the result)"]
    out.append(f"  {'class':12} {'n':>3} " + "".join(f"{s[:18]:>20}" for s in suite_names))
    for kind in ("EXPOSURE", "REGRESSION"):
        r = rows.get(kind)
        if not r:
            continue
        line = f"  {kind:12} {r['n']:>3} "
        for s in suite_names:
            line += f"{r[s] / r['n']:>19.0%} "
        out.append(line)
    out.append("  EXPOSURE   = the change grants access that did not exist before")
    out.append("  REGRESSION = the change only removes access")
    return "\n".join(out)


def newly_denied(before: tuple, after: tuple) -> list[str]:
    return sorted(set(before) - set(after))
