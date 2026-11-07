# Repository lab

Open [the local Repository lab](http://localhost:8765/workbench.html#repository) after starting
`.venv/bin/python serve.py`. It runs alongside the security workbench.

## A three-minute demo

1. Load **Atlas migration fixture**. This is an owned, synthetic 56-file repository,
   with 8,696 bytes of actual source, not a large production codebase.
2. Keep the migration task and select **SubQ Snap · full context**. Click
   **Curate context**. Each question asks whether a particular file needs editing,
   while the request contains every eligible file in full.
3. Inspect the probabilities and click any tile to read its source. Export the
   selected context to use with another coding agent if desired.
4. Choose **Meta Muse · live generation**, then **Run migration experiment**.
   The first two arms use the same generator and the same file-count budget:
   SubQ-selected context versus a lexical keyword baseline. A third, explicitly
   labelled reference control verifies the fixture can pass.
5. Inspect the checks and proposed ten-file patch. The current API, SDK, model,
   schema, authorization, query isolation, exports, legacy importer, current docs,
   and existing amount test must migrate together. Old CSV input remains supported;
   archived contracts and unrelated utilities stay unchanged.

The independent acceptance checks live outside the selector/patch-author source
snapshot. They run against temporary copies, using a restricted identifier
migration that cannot introduce arbitrary new code. No working checkout changes.
**Reference patcher · offline** is a deterministic control, not AI generation.
Missing keys or failed calls are reported; no reference patch is substituted.

## A measured run, not a general benchmark

On 7 November 2026, one live run with `subq-snap-1.1` and `muse-spark-1.3` produced:

| Context selection | Files | Required-file recall | Executed checks |
|---|---:|---:|---:|
| SubQ | 10 | 100% | 14/14 |
| Keyword baseline | 10 | 20% | 4/14 |
| Labelled reference control | 10 | 100% | 14/14 |

The SubQ call used 6,446 provider-reported input tokens and took 450 ms as measured
by the client. Selected source shrank from 8,696 to 1,804 bytes (79.3%). This is
**raw-source reduction for the patch author**, not total pipeline token/cost
savings: the selector still reads the full snapshot. The two Meta calls together
reported 1,416 input and 6,958 output tokens. Results can vary across runs.
The fixture deliberately contains historical documents with overlapping words;
the baseline is simple lexical search, not a state-of-the-art retrieval system.
See [the recorded report](docs/context-experiment.json) for the actual selections,
edits, checks, source fingerprint, and usage.

## Your real GitHub repository

Choose **Public GitHub repository**, enter `https://github.com/DhruvMiyani/new-hack`,
and load `main` or a commit SHA. Enter a task appropriate to that source before
curating. The loader resolves the revision to an immutable commit, reads the
archive without extracting it, and displays coverage/exclusions. No GitHub token
is needed for public repositories. API rate limits still apply.

A live connectivity/scale check against commit
`223e20cb23343c4b28ca81a9898dc997fddc4f11` included **86 eligible files, 482,284 source
bytes**, and **178,885 actual SubQ input tokens** in one request. This establishes
source coverage and live inference, not correctness of the resulting selection.

Click **Draft Meta patch** after curation to generate an inspectable diff from the
selected full files. Only selected existing paths and unambiguous replacements
are accepted. It is labelled **UNVERIFIED**: downloaded/local repo code and the
proposed patch are never executed or applied. Review and test the downloaded
patch in a separate checkout before using it.

**Local Git snapshot** reads committed blobs at the selected revision. Uncommitted
working-copy edits are not included. Common hidden, credential, binary, generated,
and dependency paths are excluded. These filters are not a comprehensive secret
scanner; review the eligible source before sending it to a model.

The application includes up to 400 eligible files / 900 KB, with 150 KB per file.
Larger eligible snapshots are refused rather than silently truncated. Unsupported
or oversized individual files appear in exclusions. File questions are batched
at 96 per request; **every batch receives the full eligible snapshot**. Actual
input tokens sum across calls. Line ranges are keyword inspection hints, not an
additional model prediction. Snapshots/selections are held in server memory for
up to an hour, with bounded cache sizes, and reset on restart.

## Command line

Configure `SUBQ_API_KEY` and `META_API_KEY` in your local `.env` for live calls.

```bash
# Fully offline selector/control experiment
.venv/bin/python context.py --method keyword --benchmark

# Live full-context selection and live Meta migration
.venv/bin/python context.py --method subq --benchmark --generator meta

# Public GitHub source, pinned before inference
.venv/bin/python context.py --github https://github.com/DhruvMiyani/new-hack \
  --ref main --task 'Add expiry checks to cached GCP reports and tests.'

# Local committed source, no model call
.venv/bin/python context.py --local /Users/dhruvmiyani/new-hack \
  --task 'Add expiry checks to cached GCP reports and tests.' --method keyword
```

The executable experiment is available only for the owned Atlas fixture and its
matching migration task. Real-repository selections are inspection/draft inputs;
they do not receive a fabricated acceptance score.
