# Referral Desk

A local, interactive eFax referral demo using SubQ Snap. Five fictional packets
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

1. **Classify all.** Watch five referrals enter priority lanes from one Snap
   request. Switch to **Cover pages** to see what the model can decide without
   the signed reports.
2. **Add a clue.** Type a query or use a suggested clue. One request scores
   every referral against all active clues. Blue cards have supporting evidence.
3. **Open a fax.** A card jumps to the page that supports the result. The
   independent clinic rules and reviewer sign-off govern the simulated outcome.

See [the demo guide](REFERRALS.md) for the request schema, measured run and
sources for the synthetic PDFs and optional MRI preview.

```bash
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest -q
.venv/bin/python scripts/evaluate_triage.py
```

The evaluation script makes live calls with only bundled fictional cases.
