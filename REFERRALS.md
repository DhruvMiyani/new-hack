# Referral Inbox

Open [Referral Inbox](http://localhost:8765/referrals.html) after starting
`.venv/bin/python serve.py --port 8765`. Poppler (`pdftotext`) extracts PDF text.
Tesseract displays a local OCR transcript and verifies the signed scan in R101.
On macOS install both with `brew install poppler tesseract`. The Decisions API uses
`SUBQ_BASE_URL` and `SUBQ_API_KEY` from your local environment.

1. Open **North Clinic (R101)** and run **Letter only**. The signed addendum is absent
   from the input, so the case should go to Needs review.
2. Run **Full packet**. Snap receives labelled PDF text plus a PNG of the scanned
   signed addendum. Open **Scanned report** to see what changed the route. Check
   the source and confirm **Critical** to send it to the simulated clinician queue.
3. Try **Valley Clinic (R104)** to approve a routine slot. **Maple Clinic (R105)**
   has a lab order and no signed report, so it stays in Needs review.

Snap suggests a route, identifies the supporting source, and distinguishes lab
orders from results. The app independently checks the explicit signed routing
instruction before a reviewer can book. Critical and missing-report cases
cannot enter the appointment calendar through a lower priority. The five
packets, protocol, calendar, and reviewer actions are fictional training data.

## Asset provenance

- The PDF letters, signed reports, and workflow labels were authored for this demo.
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
a Python environment with ReportLab and Pillow.

```bash
curl -fsSL 'https://codeload.github.com/smart-on-fhir/sample-bulk-fhir-datasets/zip/54fa822575407ffc3255bc1d510749a28c29eb38' -o /tmp/referral-synthea.zip
curl -fsSL 'https://raw.githubusercontent.com/liamchalcroft/medtokenizers/3250f9505fc569334b981bf9154c4b18682e5d4d/src/medtokenizers/assets/t1w_brainweb.nii.gz' -o /tmp/referral-brainweb.nii.gz
python3 scripts/build_referral_fixtures.py /tmp/referral-synthea.zip --brainweb /tmp/referral-brainweb.nii.gz
```
