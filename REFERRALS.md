# Referral Inbox

Open [Referral Inbox](http://localhost:8765/referrals.html) after starting
`.venv/bin/python serve.py --port 8765`. Poppler (`pdftotext`) extracts PDF text.
Tesseract displays a local OCR transcript and verifies the signed scan in R101.
On macOS install both with `brew install poppler tesseract`. The Decisions API uses
`SUBQ_BASE_URL` and `SUBQ_API_KEY` from your local environment.

The eFax worklist uses 50 preloaded fictional transmissions. Each has a
sender, fax number, receive time, page count, and preview. The
[UCSF Neurology fax instructions](https://www.ucsfhealth.org/locations/neurology-complex-diagnosis-mission-bay/refer)
and [eClinicalWorks fax inbox pattern](https://www.eclinicalworks.com/products-services/ehr/)
inform the workflow. See [design notes](docs/referrals/DESIGN.md).

1. **North Clinic (R101).** Compare **Letter only** with **Full packet**. Snap
   moves from Needs review to Critical when it sees the signed addendum on
   page 2. Confirm the clinician escalation.
2. **Cedar Clinic (R102).** The signed instruction is High. Page 1 documents
   Alameda County, outside the sample specialist scheduling area. The rules
   check sends it to intake coordination without booking. Harbor Clinic
   (R103) has no documented county, so intake must verify it.
3. **Valley Clinic (R104).** San Mateo County is eligible. Confirm Low to
   book a routine slot in the simulated calendar.

## Inbox-wide live decisions

The default view starts with one request containing 100 typed `choice`
questions, asking for priority and supporting source for each of the 50
referrals. Full packet mode uses 144 labelled PDF pages and the R101 scan.
The app checks the proposed priority and source against the signed evidence.
If a decision conflicts with the source, Snap gets one smaller follow-up
request covering only the flagged faxes. Unresolved conflicts stay flagged.
The selected-case workflow evaluates lab document type and administrative
eligibility separately.

Each clue change sends one request with 100 questions, using a `noul` match
score and a source `choice` for each referral. The request uses page excerpts
and indexed intake facts with source labels. Adding a second clue requires
both clues to match. The query highlights the existing cards and preserves
their priority. Clicking a result opens the corresponding evidence page.
For recognized example clues, the app separately checks the relevant intake
facts before highlighting a match and keeps Snap's original score visible.
`Inspect live request` shows the case labels, question count, measured
latency and actual token use.

In one live run, all 50 priorities matched the source checks after a follow-up
on two faxes. The two calls used 56,060 input tokens and took 2.29 seconds of
combined API time. Cover pages alone put all 50 cases in Needs review.
Four example searches took 0.79–0.87 seconds each, with zero output tokens.
Reproduce the checks with `.venv/bin/python scripts/evaluate_triage.py`.

## Exactly what SubQ does per referral

The server uses `subq-snap-1.1` by default through `POST /v1/systemone`.
Poppler extracts the PDF into labelled pages. The model receives those pages,
the patient county field extracted from page 1, and the fictional clinic
protocol and rules book. For R101 in Full packet mode it also gets a PNG
of the scanned addendum labelled `SCAN`. Letter only sends page 1 with
zero images. The MRI preview stays in the viewer.

The request asks for `route` (clinical queue), `source` (supporting page),
`lab` (order versus result), `complete` (legibility of a signed instruction),
and `acceptance` (administrative eligibility). The UI exposes the
question list, evidence labels, token count and returned scores. A model
result is advisory. The server separately reads county from the PDF and
applies the rules. Reviewer sign-off is required to book. A signed critical
flag goes to the clinician queue regardless of county. This is a fictional
office scheduling rule, and outside-area cases get intake coordination.

## Image attachments and the original PDF check

The attachment strip shows the signed addendum scan, the locally installed
BrainWeb phantom where available, and page previews of final lab reports.
Click an image to open the underlying evidence. The BrainWeb slice is
illustrative and stays in the viewer.

Configure `META_API_KEY` (or `MODEL_API_KEY`) and `META_BASE_URL` locally to
enable **Verify PDF with Muse**. That button sends the selected fictional fax
as a native PDF to `muse-spark-1.3` through the Meta Model API. The app checks
the returned priority, physical page number and quote against the fax text
or its local OCR transcript. In a live R101 run, Muse returned
`Routing instruction: CRITICAL` on page 2 in 4.78 seconds, and local OCR
verified the quote. The result appears beside the selected fax for reviewer
inspection. [Meta PDF file handling](https://dev.meta.ai/docs/file-handling)

## Asset provenance

- The fax first page comes from the public [VA Form 10-10172 medical
  request for service](https://www.va.gov/forms/10-10172/), with fictional fields
  added and the page stamped DEMO ONLY. The attached signed report and lab
  pages were authored for this demo using the [ACR report structure](https://www.acr.org/-/media/ACR/Files/Practice-Parameters/CommunicationDiag.pdf) and the [NIDDK annotated lab result example](https://repository.niddk.nih.gov/media/studies/grade/Manuals/GRADE%20Lab%20MOP.pdf#page=46).
  Two numeric lab observations and their units were taken from the [SMART on FHIR
  Synthea sample archive](https://github.com/smart-on-fhir/sample-bulk-fhir-datasets)
  at commit `54fa822575407ffc3255bc1d510749a28c29eb38`. The source repository uses
  Apache-2.0. No identifying fields were copied. Those observations do not set
  referral priority.
- The local MRI preview is an axial slice from a normal simulated T1 volume from
  the [BrainWeb Simulated Brain Database](https://www.mcgill.ca/bic/software/brainweb-mri-simulator)
  at McGill's McConnell Brain Imaging Centre. The [documented NIfTI derivative](https://github.com/liamchalcroft/medtokenizers/blob/3250f9505fc569334b981bf9154c4b18682e5d4d/src/medtokenizers/assets/README.md)
  converts `t1_icbm_normal_1mm_pn3_rf20.mnc` to a canonical uint8 volume. The app
  displays one locally cached slice for illustration. Snap receives the separate
  written addendum image. The MRI slice is excluded from inference and Git.
  BrainWeb references: Collins et al., *Design and Construction of a Realistic
  Digital Brain Phantom*, IEEE TMI 17(3), 1998. Kwan, Evans, and Pike,
  *MRI Simulation-Based Evaluation of Image-Processing and Classification
  Methods*, IEEE TMI 18(11), 1999.

The PDFs are bundled. Generate the 45 additional fictional packets with
`python3 scripts/build_scaled_referrals.py` using ReportLab, Pillow and
Poppler. To regenerate the original five and install the local MRI preview,
use a Python environment with ReportLab, Pillow and pypdf.

```bash
curl -fsSL 'https://codeload.github.com/smart-on-fhir/sample-bulk-fhir-datasets/zip/54fa822575407ffc3255bc1d510749a28c29eb38' -o /tmp/referral-synthea.zip
curl -fsSL 'https://raw.githubusercontent.com/liamchalcroft/medtokenizers/3250f9505fc569334b981bf9154c4b18682e5d4d/src/medtokenizers/assets/t1w_brainweb.nii.gz' -o /tmp/referral-brainweb.nii.gz
python3 scripts/build_referral_fixtures.py /tmp/referral-synthea.zip --brainweb /tmp/referral-brainweb.nii.gz
curl -fsSL 'https://www.va.gov/vaforms/medical/pdf/VA%20Form%2010-10172.pdf' -o /tmp/referral-va-template.pdf
python3 scripts/build_referral_faxes.py /tmp/referral-va-template.pdf
```
