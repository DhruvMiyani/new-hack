"""Build fictional referral packets from a Synthea sample archive and a BrainWeb volume."""
from __future__ import annotations

import argparse
import gzip
import json
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path

from PIL import Image, ImageOps
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parents[1] / "web" / "dist" / "referral-assets"

CASES = {
    "R101": ("North Clinic", "Routine", "CRITICAL", "Immediate clinician review requested by the signing reviewer."),
    "R102": ("Cedar Clinic", "Routine", "HIGH", "Priority clinician review requested by the signing reviewer."),
    "R103": ("Harbor Clinic", "Routine", "MEDIUM", "Standard review and scheduling requested by the signing reviewer."),
    "R104": ("Valley Clinic", "Routine", "LOW", "Routine follow-up requested by the signing reviewer."),
    "R105": ("Maple Clinic", "Routine", "", ""),
}


def sample_results(path):
    """Keep only numeric synthetic lab values and units from a single sample subject."""
    names = {
        "Sodium [Moles/volume] in Serum or Plasma": "Sodium",
        "Potassium [Moles/volume] in Serum or Plasma": "Potassium",
    }
    by_subject = {}
    with zipfile.ZipFile(path) as archive:
        entry = next(name for name in archive.namelist() if name.endswith("/Observation.000.ndjson"))
        with archive.open(entry) as stream:
            for line in stream:
                observation = json.loads(line)
                label = names.get(observation.get("code", {}).get("text"))
                quantity = observation.get("valueQuantity")
                subject = observation.get("subject", {}).get("reference")
                if label and quantity and subject:
                    values = by_subject.setdefault(subject, {})
                    values.setdefault(label, (quantity["value"], quantity["unit"]))
                    if len(values) == len(names):
                        return values
    raise ValueError("Could not find the selected synthetic observation types.")


def heading(pdf, case_id, title, page):
    pdf.setFillColor(HexColor("#153c41"))
    pdf.rect(0, 681, 612, 111, fill=1, stroke=0)
    pdf.setFillColor(HexColor("#effcf3"))
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(52, 749, "DEMO HEALTH NETWORK / SYNTHETIC TRAINING RECORD")
    pdf.setFont("Helvetica-Bold", 27)
    pdf.drawString(52, 709, title)
    pdf.setFillColor(HexColor("#385052"))
    pdf.setFont("Helvetica", 10)
    pdf.drawString(52, 32, f"{case_id}   |   SYNTHETIC DATA   |   Page {page}")


def lines(pdf, rows):
    y = 640
    pdf.setFont("Helvetica", 12)
    for row in rows:
        pdf.drawString(52, y, row)
        y -= 28


INK = HexColor("#172839")
MUTED = HexColor("#485564")
RULE = HexColor("#bec8d1")
FILL = HexColor("#eef2f5")


def report_header(pdf, case_id, facility, title, subtitle, page):
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(42, 745, facility)
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawRightString(570, 745, "SYNTHETIC TRAINING RECORD")
    pdf.setStrokeColor(RULE)
    pdf.line(42, 733, 570, 733)
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 21)
    pdf.drawString(42, 700, title)
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 10)
    pdf.drawString(42, 680, subtitle)
    pdf.line(42, 58, 570, 58)
    pdf.setFont("Helvetica", 8)
    pdf.drawString(42, 42, f"{case_id}  |  Fictional record  |  Not for clinical use")
    pdf.drawRightString(570, 42, f"Fax packet page {page}")


def metadata(pdf, left, right):
    pdf.setFillColor(FILL)
    pdf.roundRect(42, 584, 528, 77, 3, fill=1, stroke=0)
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica", 10)
    for index, value in enumerate(left):
        pdf.drawString(53, 641 - 19 * index, value)
    for index, value in enumerate(right):
        pdf.drawString(316, 641 - 19 * index, value)


def block(pdf, y, label, rows):
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(42, y, label)
    pdf.setFont("Helvetica", 10)
    for index, value in enumerate(rows, 1):
        pdf.drawString(42, y - 16 * index, value)
    return y - 16 * len(rows) - 29


def radiology_report(pdf, case_id, priority, instruction):
    title = "MRI Brain - Signed Addendum" if priority == "CRITICAL" else "MRI Brain - Final Report"
    report_header(pdf, case_id, "Bayview Diagnostic Imaging", title,
                  "MR brain without intravenous contrast | outpatient report", 2)
    metadata(pdf, [
        f"Patient: Training Patient {case_id}", "DOB: 01/01/1970 (fictional)",
        f"Accession: MRI-26-{case_id[1:]}",
    ], ["Exam: 11/06/2026 14:15 PT", "Ordering provider: Training Referrer, MD",
        "Status: signed"])
    y = block(pdf, 555, "CLINICAL INDICATION", [
        "Outpatient neurology referral. Review of outside imaging documentation requested.",
    ])
    y = block(pdf, y, "TECHNIQUE", [
        "Multiplanar noncontrast brain MRI. T1, T2, FLAIR and diffusion series documented.",
    ])
    y = block(pdf, y, "COMPARISON", [
        "Outside examination report dated 10/29/2026. Prior image series not supplied.",
    ])
    y = block(pdf, y, "FINDINGS", [
        "The accompanying fax contains the signed written report. Source images remain",
        "with the originating imaging center. No additional image interpretation is recorded here.",
    ])
    y = block(pdf, y, "IMPRESSION / REFERRAL COMMUNICATION", [
        instruction,
        "The intake disposition below is the explicit instruction from the signing reviewer.",
    ])
    pdf.setFillColor(HexColor("#f4e7e3") if priority == "CRITICAL" else FILL)
    pdf.rect(42, y - 23, 528, 37, fill=1, stroke=0)
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(52, y - 7, f"Routing instruction: {priority}")
    if priority == "CRITICAL":
        block(pdf, y - 54, "COMMUNICATION LOG", [
            "11/06/2026 16:21 PT | Phone | Training Referrer, MD | Receipt acknowledged.",
        ])
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica", 9)
    pdf.drawString(42, 92, "Electronically signed: Training Radiologist, MD (fictional)")
    pdf.drawString(42, 76, "Finalized 11/06/2026 16:23 PT  |  Training signature")


def critical_scan():
    case_id = "R101"
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "report.pdf"
        pdf = canvas.Canvas(str(source), pagesize=(612, 792), invariant=True)
        radiology_report(pdf, case_id, CASES[case_id][2], CASES[case_id][3])
        pdf.save()
        subprocess.run(["pdftoppm", "-r", "145", "-singlefile", "-gray", "-png",
                        str(source), str(OUT / "R101-addendum")], check=True)


def lab_report(pdf, case_id, page, final, labs):
    title = "Laboratory Result Report" if final else "Laboratory Test Requisition"
    subtitle = "Final | Serum electrolytes" if final else "Ordered | No final result attached"
    report_header(pdf, case_id, "Bayview Reference Laboratory", title, subtitle, page)
    metadata(pdf, [
        f"Patient: Training Patient {case_id}", "DOB: 01/01/1970 (fictional)",
        f"Requisition: LAB-26-{case_id[1:]}",
    ], ["Ordering provider: Training Referrer, MD",
        f"Accession: SYN-{case_id[1:]}" if final else "Accession: pending collection",
        "Specimen: serum" if final else "Specimen requested: serum"])
    if final:
        block(pdf, 554, "SPECIMEN & REPORT STATUS", [
            "Collected: 11/06/2026 08:22 PT     Received: 11/06/2026 10:05 PT",
            "Reported: 11/06/2026 12:48 PT     Document type: final laboratory result",
        ])
        columns = (52, 225, 319, 381)
        pdf.setFillColor(FILL)
        pdf.rect(42, 438, 528, 28, fill=1, stroke=0)
        pdf.setFillColor(INK)
        pdf.setFont("Helvetica-Bold", 9)
        for x, value in zip(columns, ("Analyte", "Result", "Units", "Reference interval")):
            pdf.drawString(x, 449, value)
        pdf.setFont("Helvetica", 10)
        for row, name in enumerate(("Sodium", "Potassium")):
            y = 420 - 28 * row
            value, unit = labs[name]
            for x, item in zip(columns, (name, f"{value:g}", unit, "Not supplied in sample")):
                pdf.drawString(x, y, item)
            pdf.setStrokeColor(RULE)
            pdf.line(42, y - 10, 570, y - 10)
        block(pdf, 332, "REPORT NOTES", [
            "Numeric observations are from the Synthea synthetic data sample.",
            "Reference intervals were not provided with these source observations.",
        ])
        block(pdf, 240, "RELEASED BY", [
            "Training Laboratory Reviewer | 11/06/2026 12:48 PT",
        ])
    else:
        block(pdf, 554, "ORDER DETAILS", [
            "Document type: laboratory order",
            "Requested panel: basic metabolic panel",
            "Order placed: 11/06/2026 09:10 PT",
            "Order status: ordered; specimen collection pending",
        ])
        block(pdf, 432, "COLLECTION INFORMATION", [
            "Specimen: serum. Collect through the referring office.",
            "No final laboratory result is included in this packet.",
        ])
        block(pdf, 340, "ORDER AUTHORIZATION", [
            "Training Referrer, MD (fictional) | 11/06/2026 09:10 PT",
        ])


def make_pdf(case_id, clinic, intake, priority, instruction, labs):
    pdf = canvas.Canvas(str(OUT / f"{case_id}.pdf"), pagesize=(612, 792), invariant=True)
    pdf.setTitle(f"Synthetic neurology referral {case_id}")
    heading(pdf, case_id, "Neurology referral", 1)
    lines(pdf, [f"Case: {case_id}", f"Referring clinic: {clinic}", f"Intake form priority: {intake}",
                "Request: outpatient neurology consultation.",
                "A signed reviewer report is required before routing.",
                "An MRI order and a lab order do not establish a completed report.",
                "These fields are fictional and are provided for workflow testing."])
    pdf.showPage()
    if priority:
        if case_id == "R101":
            pdf.drawImage(ImageReader(str(OUT / "R101-addendum.png")), 0, 0, width=612, height=792)
        else:
            radiology_report(pdf, case_id, priority, instruction)
        pdf.showPage()
    lab_report(pdf, case_id, 3 if priority else 2, case_id in ("R101", "R104"), labs)
    pdf.save()


def brainweb_slice(path):
    """Read the documented uint8 NIfTI derivative and save one axial teaching slice."""
    with gzip.open(path, "rb") as source:
        data = source.read(16_000_000)
    if struct.unpack_from("<i", data)[0] != 348 or struct.unpack_from("<h", data, 70)[0] != 2:
        raise ValueError("Expected the documented BrainWeb uint8 NIfTI-1 volume.")
    _, width, height, depth = struct.unpack_from("<4h", data, 40)
    start = int(struct.unpack_from("<f", data, 108)[0]) + (depth // 2 + 12) * width * height
    image = Image.frombytes("L", (width, height), data[start:start + width * height])
    image = ImageOps.autocontrast(ImageOps.flip(image)).resize((width * 3, height * 3))
    image.save(OUT / "brainweb-local.png", optimize=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("synthea", type=Path)
    parser.add_argument("--brainweb", type=Path)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    values = sample_results(args.synthea)
    critical_scan()
    for identifier, fields in CASES.items():
        make_pdf(identifier, *fields, values)
    if args.brainweb:
        brainweb_slice(args.brainweb)
