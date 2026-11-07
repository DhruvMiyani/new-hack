"""Populate the public VA 10-10172 medical page for the five fictional fax packets."""
from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path
import subprocess

from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor
from reportlab.pdfgen.canvas import Canvas

ASSETS = Path(__file__).resolve().parents[1] / "web" / "dist" / "referral-assets"
CASES = (
    ("R101", "North Clinic", "San Francisco", "(415) 555-0101", "08:41 PT"),
    ("R102", "Cedar Clinic", "Alameda", "(415) 555-0102", "08:47 PT"),
    ("R103", "Harbor Clinic", "Not documented", "(650) 555-0103", "09:03 PT"),
    ("R104", "Valley Clinic", "San Mateo", "(650) 555-0104", "09:12 PT"),
    ("R105", "Maple Clinic", "Contra Costa", "(510) 555-0105", "09:19 PT"),
)


def overlay(identifier, clinic, county, fax, received):
    buffer = BytesIO()
    form = Canvas(buffer, pagesize=(612, 792), invariant=True)
    form.setFillColor(HexColor("#9c1d23"))
    form.setFont("Helvetica-Bold", 9)
    form.drawCentredString(306, 781, "DEMO ONLY / SYNTHETIC RECORD / NOT FOR SUBMISSION")
    form.setFillColor(HexColor("#173954"))
    form.setFont("Helvetica-Bold", 8.5)
    fields = (
        (39, 645, f"Training Patient {identifier}"),
        (423, 645, "01/01/1970 (fictional)"),
        (39, 621, "Bay Neurology - simulated specialty intake"),
        (423, 621, f"DEMO-{identifier}"),
        (39, 592, f"{clinic} / fictional referring office"),
        (39, 580, "Synthetic training location, California"),
        (39, 537, "(415) 555-0110"),
        (200, 537, fax),
        (351, 543, "training@example.invalid"),
        (154, 465, "Outpatient neurology"),
        (39, 428, "Training case"),
        (177, 428, "Referral review"),
        (315, 428, "Demo only"),
        (453, 428, "Specialist consultation"),
        (39, 177, "Training Referrer, MD (fictional)"),
        (423, 177, "DEMO / no NPI"),
        (39, 143, "Synthetic facsimile - no valid signature"),
        (423, 143, "11/07/2026"),
    )
    for x, y, text in fields:
        form.drawString(x, y, text)
    for x, y in ((422, 585), (38, 501), (422, 495), (74, 465)):
        form.drawString(x, y, "X")
    form.setFont("Helvetica-Bold", 10)
    rows = (
        f"Case: {identifier} / eFax received {received}",
        f"Patient county of residence: {county}",
        "Intake form priority: Routine",
        "A signed reviewer report is required before routing.",
        "An MRI order and a lab order do not establish a completed report.",
    )
    for index, line in enumerate(rows):
        form.drawString(41, 346 - index * 15, line)
    form.save()
    return PdfReader(BytesIO(buffer.getvalue())).pages[0]


def make_fax(template, case):
    identifier = case[0]
    target = ASSETS / f"{identifier}.pdf"
    previous = PdfReader(BytesIO(target.read_bytes()))
    writer = PdfWriter()
    first = writer.add_page(template.pages[0], excluded_keys=("/Annots",))
    first.merge_page(overlay(*case))
    for page in previous.pages[1:]:
        writer.add_page(page)
    writer.add_metadata({
        "/Title": f"Fictional neurology fax {identifier}",
        "/Subject": "Synthetic entries on a public VA form template; training only",
    })
    temporary = target.with_suffix(".pdf.tmp")
    with temporary.open("wb") as stream:
        writer.write(stream)
    temporary.replace(target)
    preview = ASSETS / f"{identifier}-fax"
    subprocess.run(
        ["pdftoppm", "-r", "100", "-png", str(target), str(preview)],
        check=True,
    )
    preview.with_name(f"{preview.name}-1.png").replace(preview.with_suffix(".png"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path, help="VA Form 10-10172 PDF downloaded from va.gov")
    args = parser.parse_args()
    template = PdfReader(args.template)
    if "MEDICAL REQUEST FOR SERVICE" not in " ".join(template.pages[0].extract_text().split()):
        raise ValueError("Expected the official VA 10-10172 medical request page.")
    for case in CASES:
        make_fax(template, case)


if __name__ == "__main__":
    main()
