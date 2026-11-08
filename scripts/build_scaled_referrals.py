"""Generate R106–R150, fictional fax fixtures and their page previews."""
from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from reportlab.pdfgen import canvas

from build_referral_fixtures import FILL, INK, OUT, RULE, block, metadata, report_header

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "blindspot" / "scaled_cases.json"

# A deterministic queue with mixed priorities, county coverage, and document status.
PRIORITIES = ("low", "medium", "high", "low", "medium", "critical", "high", "low", None)
CLINICS = (
    "Juniper Practice", "Alder Health", "Redwood Family", "Cypress Primary",
    "Linden Medical", "Pineview Clinic", "Willow Health", "Oakbridge Practice",
    "Marigold Clinic", "Birch Family", "Evergreen Health", "Manzanita Primary",
    "Sequoia Clinic", "Laurel Medical", "Sycamore Family",
)
COUNTIES = ("San Mateo", "Alameda", "Not documented", "Marin",
            "San Francisco", "Santa Clara", "Contra Costa", "Solano")
ROUTING = {
    "critical": "Immediate review by the clinician queue is requested.",
    "high": "Review in the priority neurology queue is requested.",
    "medium": "Place in the standard neurology review queue.",
    "low": "Routine outpatient follow-up is requested.",
}
CONTEXTS = (
    "Outside records submitted for follow-up of an established neurology concern.",
    "The referring office requests review of an interval symptom update.",
    "Outpatient evaluation requested after a prior specialist visit.",
    "Referring clinician requests coordination after outside testing.",
    "Referral submitted with a request to reconcile prior notes and the new order.",
)
REPORT_TYPES = (
    ("Specialist review addendum", "Referral triage communication"),
    ("Signed review note", "Outside chart review"),
    ("Neurology consultation addendum", "Referral disposition communication"),
)


@dataclass(frozen=True)
class Case:
    identifier: str
    clinic: str
    county: str
    received: str
    fax: str
    priority: str | None
    has_lab_result: bool
    index: int

    @property
    def pages(self) -> int:
        return 3 if self.priority else 2

    def manifest_entry(self) -> dict[str, str | int]:
        return {
            "clinic": self.clinic,
            "received": self.received,
            "fax": self.fax,
            "report": "text" if self.priority else "missing",
            "pages": self.pages,
        }


def make_cases() -> list[Case]:
    cases = []
    for index in range(45):
        number = 106 + index
        priority = PRIORITIES[index % len(PRIORITIES)]
        # Keep the two focal scenarios identifiable in the expanded corpus.
        # R102 is the high out-of-area example; R101 is the eligible critical example.
        if priority == "high":
            county = ("San Francisco", "San Mateo", "Not documented")[index % 3]
        elif priority == "critical":
            county = ("Alameda", "Contra Costa", "Not documented", "Marin", "Santa Clara")[index // 9]
        else:
            county = COUNTIES[index % len(COUNTIES)]
        hour, minute = divmod(9 * 60 + 24 + 4 * index, 60)
        cases.append(Case(
            identifier=f"R{number}",
            clinic=f"{CLINICS[index % len(CLINICS)]} (Demo)",
            county=county,
            received=f"{hour:02d}:{minute:02d}",
            fax=f"(415) 555-{number:04d}",
            priority=priority,
            has_lab_result=index % 4 != 0,
            index=index,
        ))
    return cases


def cover_page(pdf, case):
    report_header(pdf, case.identifier, case.clinic, "Neurology referral fax",
                  "Incoming referral to Bay Neurology | Cover sheet", 1)
    metadata(pdf, [
        f"Record: Training Patient {case.identifier}",
        f"Referral ID: REF-{case.identifier}",
        "Intake form priority: Routine",
    ], [
        f"Received: 11/07/2026 {case.received} PT",
        f"Sender fax: {case.fax}",
        f"Packet: {case.pages} pages",
    ])
    y = block(pdf, 554, "PATIENT RESIDENCE", [
        f"Patient county of residence: {case.county}",
    ])
    y = block(pdf, y, "REASON FOR REFERRAL", [
        CONTEXTS[case.index % len(CONTEXTS)],
        "Request: outpatient neurology consultation.",
    ])
    report = "Signed reviewer report attached." if case.priority else "Signed reviewer report not included."
    lab = "Final laboratory result attached." if case.has_lab_result else "Laboratory order attached; final result pending."
    block(pdf, y, "ENCLOSURES", [
        report, lab, "A signed reviewer instruction is required before routing.",
    ])


def signed_report(pdf, case):
    title, subtitle = REPORT_TYPES[case.index % len(REPORT_TYPES)]
    report_header(pdf, case.identifier, "Bay Area Training Review Service", title, subtitle, 2)
    metadata(pdf, [
        f"Record: Training Patient {case.identifier}",
        f"Report ID: REV-{case.identifier}",
        "Status: signed",
    ], [
        "Review date: 11/06/2026",
        "Reviewing service: Neurology intake",
        "Author: Training Reviewer, MD",
    ])
    y = block(pdf, 554, "RECORDS REVIEWED", [
        CONTEXTS[case.index % len(CONTEXTS)],
        "Signed intake disposition documented below.",
    ])
    y = block(pdf, y, "DISPOSITION", [ROUTING[case.priority]])
    pdf.setFillColor(FILL)
    pdf.roundRect(42, y - 24, 528, 38, 3, fill=1, stroke=0)
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(52, y - 8, f"Routing instruction: {case.priority.upper()}")
    block(pdf, y - 54, "SIGNATURE", ["Electronically signed by Training Reviewer, MD."])


def lab_page(pdf, case):
    final = case.has_lab_result
    title = "Laboratory Result Report" if final else "Laboratory Test Requisition"
    subtitle = "Final | Serum electrolytes" if final else "Ordered | No final result attached"
    report_header(pdf, case.identifier, "Fictional Reference Laboratory", title, subtitle, case.pages)
    metadata(pdf, [
        f"Record: Training Patient {case.identifier}",
        f"Requisition: LAB-{case.identifier}",
        "Specimen: serum",
    ], [
        "Ordering service: Primary care",
        f"Accession: SYN-{case.identifier}" if final else "Accession: pending collection",
        "Report date: 11/06/2026",
    ])
    if not final:
        block(pdf, 554, "ORDER DETAILS", [
            "Document type: laboratory order",
            "Requested panel: serum electrolytes",
            "Order status: pending specimen collection",
            "No final laboratory result is included in this packet.",
        ])
        return

    block(pdf, 554, "SPECIMEN & REPORT STATUS", [
        "Collected: 11/06/2026 08:15 PT     Received: 11/06/2026 10:15 PT",
        "Reported: 11/06/2026 13:05 PT     Document type: final laboratory result",
    ])
    columns = (52, 270, 392)
    pdf.setFillColor(FILL)
    pdf.rect(42, 438, 528, 28, fill=1, stroke=0)
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 9)
    for x, value in zip(columns, ("Analyte", "Result", "Units")):
        pdf.drawString(x, 449, value)
    pdf.setFont("Helvetica", 10)
    rows = (
        ("Sodium", str(137 + case.index % 7), "mmol/L"),
        ("Potassium", f"{3.8 + (case.index % 8) / 10:.1f}", "mmol/L"),
    )
    pdf.setStrokeColor(RULE)
    for index, row in enumerate(rows):
        y = 420 - 28 * index
        for x, value in zip(columns, row):
            pdf.drawString(x, y, value)
        pdf.line(42, y - 10, 570, y - 10)
    block(pdf, 332, "REPORT NOTES", ["Invented training values. No clinical interpretation supplied."])


def write_case(case):
    path = OUT / f"{case.identifier}.pdf"
    pdf = canvas.Canvas(str(path), pagesize=(612, 792), invariant=True)
    pdf.setTitle(f"Synthetic neurology fax {case.identifier}")
    cover_page(pdf, case)
    pdf.showPage()
    if case.priority:
        signed_report(pdf, case)
        pdf.showPage()
    lab_page(pdf, case)
    pdf.save()

    preview = OUT / f"{case.identifier}-fax"
    subprocess.run(
        ["pdftoppm", "-r", "72", "-gray", "-png", str(path), str(preview)],
        check=True,
    )
    preview.with_name(f"{preview.name}-1.png").replace(preview.with_suffix(".png"))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cases = make_cases()
    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(write_case, cases))
    manifest = {case.identifier: case.manifest_entry() for case in cases}
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Generated {len(cases)} synthetic fax packets and {sum(case.pages for case in cases)} previews.")


if __name__ == "__main__":
    main()
