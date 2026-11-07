"""Build fictional referral packets from a Synthea sample archive and a BrainWeb volume."""
from __future__ import annotations

import argparse
import gzip
import json
import struct
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
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
                    values.setdefault(label, f"{quantity['value']} {quantity['unit']}")
                    if len(values) == len(names):
                        return [f"{name}: {value}" for name, value in values.items()]
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


def critical_scan(font_path):
    image = Image.new("RGB", (1100, 650), "#f5f4ee")
    draw = ImageDraw.Draw(image)
    bold = ImageFont.truetype(font_path, 37)
    body = ImageFont.truetype(font_path, 28)
    draw.rectangle((36, 37, 1064, 105), fill="#183c41")
    draw.text((60, 53), "SYNTHETIC TRAINING RECORD / R101", font=bold, fill="white")
    draw.text((60, 148), "Radiology report addendum", font=bold, fill="#203638")
    draw.text((60, 213), "Case: R101     Status: signed", font=body, fill="#203638")
    draw.rectangle((60, 292, 1028, 366), fill="#f1ded6")
    draw.text((78, 305), "Routing instruction: CRITICAL", font=bold, fill="#8b231d")
    draw.multiline_text((60, 404), "Immediate clinician review requested.\nRoute to the clinician escalation queue.\nSigned: Demo Radiology Reviewer", font=body, fill="#203638", spacing=19)
    image.save(OUT / "R101-addendum.png")
    return image


def make_pdf(case_id, clinic, intake, priority, instruction, scan, labs):
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
        heading(pdf, case_id, "Signed review", 2)
        if case_id == "R101":
            pdf.drawImage(ImageReader(scan), 48, 300, width=516, height=305)
        else:
            lines(pdf, [f"Case: {case_id}", "Status: signed", f"Routing instruction: {priority}",
                        instruction, "Signed: Demo Neurology Reviewer"])
        pdf.showPage()
    heading(pdf, case_id, "Laboratory documents", 3 if priority else 2)
    if case_id in ("R101", "R104"):
        lines(pdf, ["Document type: final laboratory result", "Synthetic observations imported from Synthea:",
                    *labs, "These values are displayed for review and are not routing thresholds."])
    else:
        lines(pdf, ["Document type: laboratory order", "Status: ordered", "Requested panel: basic metabolic panel",
                    "No final laboratory result is included in this packet."])
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
    parser.add_argument("--font", default="/System/Library/Fonts/Supplemental/Arial.ttf")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    values = sample_results(args.synthea)
    scanned_report = critical_scan(args.font)
    for identifier, fields in CASES.items():
        make_pdf(identifier, *fields, scanned_report, values)
    if args.brainweb:
        brainweb_slice(args.brainweb)
