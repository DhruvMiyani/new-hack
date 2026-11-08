# Referral Desk

A local, interactive eFax referral demo using SubQ Snap. Fifty fictional packets
exercise signed priority, scanned evidence, missing reports and a sample
specialist's scheduling area. The demo stores reviewer dispositions in memory.

## Run locally

Install Poppler and Tesseract, then start the Python server. On macOS the system
tools are available with `brew install poppler tesseract`.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python serve.py --port 8765
```

Open [Referral Desk](http://localhost:8765/). Live Snap uses your locally
configured `SUBQ_API_KEY` and `SUBQ_BASE_URL`, with optional `SUBQ_MODEL`.
Set them in an ignored local `.env` or in the server's environment. The browser
receives decision results and request metadata.

1. **Classify all.** Watch 50 referrals enter priority lanes. Switch to
   **Cover pages** to see what the model can decide without the signed reports.
2. **Add a clue.** Type a query or use a suggested clue. One request scores
   every referral against all active clues. Blue cards have supporting evidence.
3. **Open a fax.** A card jumps to the evidence. R101's **Images** link opens
   previews for the signed scan, lab result, and simulated MRI if installed.
   **Verify PDF with Muse** optionally checks the original fax against the
   quoted page. Clinic rules and reviewer sign-off govern the simulated outcome.

See [the demo guide](REFERRALS.md) for the request schema, measured run and
sources for the synthetic PDFs and optional MRI preview.

```bash
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest -q
.venv/bin/python scripts/evaluate_triage.py
```

The evaluation script makes live calls with only bundled fictional cases.

## Routing policy repair experiment

```bash
.venv/bin/python selfimprove.py split
.venv/bin/python selfimprove.py cycle --from-ablation
.venv/bin/python selfimprove.py ledger
```

The experiment injects a cover-only routing fault while still supplying the
full fax packets. Muse sees aggregate errors from 35 development referrals and
proposes new wording for Snap's routing policy and priority question. Snap
scores both versions on 15 frozen held-out referrals. The gate compares the
first-pass decisions with the signed source and requires a gain with zero
regressions. The scheduling rules stay fixed.

The ledger records the policy text and held-out counts for every evaluated
proposal. An experiment can earn promotion within its own trial. Run `cycle`
without `--from-ablation` to test revisions to the active inbox policy.

In the recorded live trial, the faulty policy scored 8/15 on the held-out
split and the Muse revision scored 15/15 with zero regressions. The shipped
policy also scored 15/15. [Saved decision](rules/referral/DEMO-1.0-cover-fault.4.json)
