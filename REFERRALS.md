# Referral Inbox

Open [Referral Inbox](http://localhost:8765/referrals.html) after starting
`.venv/bin/python serve.py --port 8765`. Poppler (`pdftotext`) extracts PDF text.
Tesseract displays a local OCR transcript and verifies the signed scan in R101.
On macOS install both with `brew install poppler tesseract`. The Decisions API uses
`SUBQ_BASE_URL` and `SUBQ_API_KEY` from your local environment.

The eFax worklist uses five preloaded fictional transmissions. Each has a
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

## Exactly what SubQ does

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

The PDFs are bundled. To regenerate them and install the local MRI preview, use
a Python environment with ReportLab, Pillow and pypdf.

```bash
curl -fsSL 'https://codeload.github.com/smart-on-fhir/sample-bulk-fhir-datasets/zip/54fa822575407ffc3255bc1d510749a28c29eb38' -o /tmp/referral-synthea.zip
curl -fsSL 'https://raw.githubusercontent.com/liamchalcroft/medtokenizers/3250f9505fc569334b981bf9154c4b18682e5d4d/src/medtokenizers/assets/t1w_brainweb.nii.gz' -o /tmp/referral-brainweb.nii.gz
python3 scripts/build_referral_fixtures.py /tmp/referral-synthea.zip --brainweb /tmp/referral-brainweb.nii.gz
curl -fsSL 'https://www.va.gov/vaforms/medical/pdf/VA%20Form%2010-10172.pdf' -o /tmp/referral-va-template.pdf
python3 scripts/build_referral_faxes.py /tmp/referral-va-template.pdf
```
