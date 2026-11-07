# Blindspot security workbench

## Local

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python serve.py
```

Open http://localhost:8765. The server binds to loopback only and rejects
cross-origin actions. It serves only `web/dist/`, never the repository or `.env`.

The primary workflow is **inspect change → execute probes → inspect evidence →
verify a repair**. Three scenarios cover public storage, privilege escalation,
and staging-to-production access. A clean control demonstrates preserved legitimate
access. Evidence can be exported as JSON.

Local mode calls the Python verifier afresh. Offline repairs are labelled as
deterministic, not AI generated. Configure `SUBQ_API_KEY` and `META_API_KEY` in
the local `.env`, restart, and enable **Use Meta + SubQ** for model mode.
An optional console screenshot is sent to SubQ with complete labelled artifacts;
image judgments are advisory and cannot override executed checks. Images are
limited to PNG/JPEG/WebP under 2 MiB and are not persisted.

## Local execution only

The workbench runs at http://localhost:8765 with no ChatGPT login or hosted
website. Verification and repairs always call the local Python backend. If it
is unavailable, verification is disabled; recorded evidence is never used as
a substitute for fresh execution.

The HTML/CSS/JavaScript lives in `web/dist/`; no JavaScript build is needed.
Reference snapshots in `scenarios.json` supply the initial change review and
artifact display. Regenerate these after changing the scenario configuration:

```bash
.venv/bin/python scripts/export_sandbox.py
```

## Real GCP, read-only

1. Create or select your own sandbox project and a private bucket.
2. Put a fictional text file called `blindspot-canary.txt` in that bucket.
3. Sign in locally: `gcloud auth login`.
4. Open **Google Cloud** in the local workbench and enter the project ID and
   bucket name. Metadata-only inspection needs no test service account.
5. To test a permission boundary, supply a service-account identity that is
   **expected to be denied** and the canary object name. The signed-in operator
   must be authorized to impersonate that service account. The current operator
   also reads the same canary as a positive control.

The adapter reads bucket metadata, bucket IAM, project IAM, and project
metadata. It verifies bucket/project ownership. Optional canary reads use
short-lived tokens held in memory. It never changes IAM, creates a public
bucket, writes cloud resources, or saves service-account keys. Returned evidence
contains status codes and content hashes, not object contents or access tokens.

Necessary inspection permissions include `storage.buckets.get`,
`storage.buckets.getIamPolicy`, `resourcemanager.projects.get`, and
`resourcemanager.projects.getIamPolicy`. Optional impersonation needs
`iam.serviceAccounts.getAccessToken`. The positive-control operator needs
`storage.objects.get` on the canary. Provide these through your project's
approved access-management process; the application does not grant them.

Cloud configuration findings are **review items**, not proof that an action is
possible. A successful unexpected canary read with a successful positive control
is **BLOCK**. A denied test read needs the successful positive control to support
that narrow result. Missing objects, bad authentication, timeouts, or failed
controls are **INCONCLUSIVE**. Other configuration findings can keep a result
at **REVIEW** even after the canary is denied.

The adapter does not fully resolve groups, conditional bindings, deny policies,
organization inheritance, or every privilege-escalation path. It is not a full
GCP security audit. GCP may take time to propagate IAM changes; rerun after the
change has taken effect. No live GCP result is claimed without a connected
project and executed API responses.

Official references:
- https://docs.cloud.google.com/sdk/gcloud/reference/storage/buckets/get-iam-policy
- https://docs.cloud.google.com/sdk/gcloud/reference/storage/buckets/describe
- https://docs.cloud.google.com/sdk/gcloud/reference/projects/get-iam-policy
- https://docs.cloud.google.com/iam/docs/service-account-impersonation#test-permissions

## Verification

```bash
.venv/bin/python -m pytest tests/ -q
node --check web/dist/app.js
```
