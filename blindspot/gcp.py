"""Read-only GCP inventory and canary permission probes. Never changes IAM."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


class GCPError(ValueError):
    pass


def _run(args):
    if not shutil.which("gcloud"):
        raise GCPError("Install the Google Cloud CLI and run gcloud auth login locally.")
    try:
        result = subprocess.run(["gcloud", *args, "--quiet"], capture_output=True, text=True, timeout=25)
    except subprocess.TimeoutExpired as exc:
        raise GCPError("Google Cloud request timed out; no permission verdict was issued.") from exc
    if result.returncode:
        # Never echo command stderr, credential material, or tokens to a browser.
        raise GCPError("Google Cloud refused this request. Check local sign-in, project access, required APIs, and impersonation permission.")
    return result.stdout.strip()


def _json(args, runner):
    try:
        result = json.loads(runner([*args, "--format=json"]))
    except (ValueError, TypeError) as exc:
        raise GCPError("Google Cloud returned unreadable metadata.") from exc
    if not isinstance(result, dict):
        raise GCPError("Unexpected Google Cloud metadata shape.")
    return result


def validate_target(project, bucket, actor="", object_name=""):
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project or ""):
        raise GCPError("Enter a valid GCP project ID (6–30 lowercase letters, digits, hyphens).")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]", bucket or ""):
        raise GCPError("Enter a bucket name without gs:// or a path.")
    if actor and not re.fullmatch(r"[a-z0-9-]+@[a-z0-9-]+\.iam\.gserviceaccount\.com", actor):
        raise GCPError("The test identity must be a service-account email.")
    if object_name and (len(object_name) > 512 or not object_name.startswith(("blindspot-canary", "demo/"))):
        raise GCPError("Probe only a fictional canary named blindspot-canary… or under demo/.")
    if bool(actor) != bool(object_name):
        raise GCPError("Provide both the test service account and canary object, or neither.")


def _read_canary(token, bucket, name, opener):
    url = "https://storage.googleapis.com/storage/v1/b/" + urllib.parse.quote(bucket, safe="")
    url += "/o/" + urllib.parse.quote(name, safe="") + "?alt=media"
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    try:
        with opener(request, timeout=20) as response:
            data = response.read(65_536)
            return {"access": "GRANTED", "http_status": response.status,
                    "sample_bytes": len(data), "sample_sha256": hashlib.sha256(data).hexdigest()}
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            return {"access": "DENIED", "http_status": 403}
        return {"access": "INCONCLUSIVE", "http_status": exc.code,
                "reason": "Object missing, authentication failed, or another API error; not proof of denied access."}
    except (OSError, TimeoutError):
        return {"access": "INCONCLUSIVE", "reason": "Canary request failed; no access conclusion."}


def inspect_gcp(project, bucket, *, actor="", object_name="", runner=None, opener=None):
    """Actual metadata reads, optional impersonated GET, and positive control.

    The supplied test identity is expected to have NO canary access. A denied
    request needs a successful operator control to support that narrow verdict.
    Groups, conditions, deny policies and organization inheritance are not
    fully resolved by the inventory. Uncertainty remains visible.
    """
    validate_target(project, bucket, actor, object_name)
    runner, opener = runner or _run, opener or urllib.request.urlopen
    metadata = _json(["storage", "buckets", "describe", f"gs://{bucket}", "--raw", f"--project={project}"], runner)
    bucket_policy = _json(["storage", "buckets", "get-iam-policy", f"gs://{bucket}", f"--project={project}"], runner)
    project_policy = _json(["projects", "get-iam-policy", project], runner)
    # Verify the bucket belongs to the explicitly selected project, using its number.
    project_metadata = _json(["projects", "describe", project], runner)
    expected_number = str(project_metadata.get("projectNumber", ""))
    if not expected_number or str(metadata.get("projectNumber", "")) != expected_number:
        raise GCPError("Bucket/project ownership could not be confirmed; assessment stopped.")
    pap = metadata.get("iamConfiguration", {}).get("publicAccessPrevention", "unknown")
    findings = []
    principal = "serviceAccount:" + actor if actor else None
    for source, policy in (("bucket IAM", bucket_policy), ("project IAM", project_policy)):
        for binding in policy.get("bindings", []):
            role = binding.get("role", "")
            members = binding.get("members", [])
            if not isinstance(members, list):
                raise GCPError("Malformed IAM binding.")
            for member in members:
                if member in ("allUsers", "allAuthenticatedUsers"):
                    findings.append({"kind": "public_binding", "source": source, "principal": member,
                                     "role": role, "conditional": bool(binding.get("condition")),
                                     "evidence": "Observed IAM grant; effective exposure requires access verification."
                                     + (" Bucket-level public-access prevention is enforced." if pap == "enforced" else "")})
                elif role in ("roles/owner", "roles/editor") or (principal and member == principal):
                    findings.append({"kind": "broad_role" if role in ("roles/owner", "roles/editor") else "test_identity_binding",
                                     "source": source, "principal": member, "role": role,
                                     "conditional": bool(binding.get("condition")),
                                     "evidence": "Observed configuration; this alone does not prove an unauthorized action."})
    report = {
        "mode": "gcp-read-only", "project": project, "bucket": bucket,
        "observed_at": datetime.now(timezone.utc).isoformat(), "verdict": "REVIEW",
        "public_access_prevention": pap, "findings": findings,
        "scope": "Read-only GCP snapshot; no IAM changes. Optional canary test proves only this identity/object read.",
        "artifacts": {"bucket_metadata": metadata, "bucket_iam": bucket_policy, "project_iam": project_policy},
        "notes": ["Inventory does not fully resolve groups, conditional/deny policies, or organization inheritance.",
                  "Public-access prevention does not revoke grants to specific identities.",
                  "Configuration findings are review items, not reproduced exposures."],
    }
    if actor:
        # Tokens stay in memory; never included in evidence, logs, or subprocess arguments.
        actor_token = runner(["auth", "print-access-token", f"--impersonate-service-account={actor}"])
        operator_token = runner(["auth", "print-access-token"])
        if not actor_token or not operator_token:
            raise GCPError("No usable credentials; no permission verdict issued.")
        probe = _read_canary(actor_token, bucket, object_name, opener)
        control = _read_canary(operator_token, bucket, object_name, opener)
        report["probe"] = {"identity": actor, "object": object_name, "expected": "DENIED", **probe}
        report["control"] = {"identity": "locally signed-in operator", "expected": "GRANTED", **control}
        if control["access"] != "GRANTED" or probe["access"] == "INCONCLUSIVE":
            report["verdict"] = "INCONCLUSIVE"
        elif probe["access"] == "GRANTED":
            report["verdict"] = "BLOCK"
            report["notes"].append("Confirmed: the test identity expected to be denied successfully read the canary.")
        else:
            report["verdict"] = "PASS" if not findings else "REVIEW"
            report["notes"].append("Canary access denied while the positive control succeeded; other permissions are outside this proof.")
    else:
        report["notes"].append("No canary probe requested; effective unauthorized access has not been tested.")
    return report
