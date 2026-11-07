"""Build an owned, inspectable repository fixture. No generated padding."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "examples" / "context-repo"
FILES = {
"README.md": """# Atlas Billing

A fictional billing service with current API, SDK, exports, and legacy imports.
The current API is being migrated from tenant_id to workspace_id. Legacy CSV
input must still use tenant_id. Archive documents describe old releases and
must remain unchanged. Every directory is part of this owned demo fixture.
""",
"atlas/__init__.py": '"""Atlas billing service."""\n',
"atlas/models.py": '''from dataclasses import dataclass

@dataclass
class Invoice:
    tenant_id: str
    amount: int
''',
"atlas/schemas.py": '''REQUIRED_FIELDS = ("tenant_id", "amount")

def validate(payload):
    if any(key not in payload for key in REQUIRED_FIELDS):
        raise ValueError("Missing required invoice field")
    if payload["amount"] < 0:
        raise ValueError("Amount cannot be negative")
    return True
''',
"atlas/api.py": '''from atlas.schemas import validate
from atlas.models import Invoice

def create_invoice(payload):
    validate(payload)
    return Invoice(tenant_id=payload["tenant_id"], amount=payload["amount"])
''',
"atlas/sdk.py": '''class Client:
    def create_invoice(self, tenant_id, amount):
        return {"tenant_id": tenant_id, "amount": amount}
''',
"atlas/auth.py": '''def can_read(user, record):
    return user["tenant_id"] == record["tenant_id"]
''',
"atlas/queries.py": '''def list_invoices(rows, tenant_id):
    return [row for row in rows if row["tenant_id"] == tenant_id]
''',
"atlas/exports.py": '''import csv
import io

def export_rows(rows):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["tenant_id", "amount"])
    for row in rows:
        writer.writerow([row["tenant_id"], row["amount"]])
    return stream.getvalue()
''',
"atlas/legacy.py": '''"""The legacy CSV import format must keep its tenant_id input field."""

def import_record(record):
    return {"tenant_id": record["tenant_id"], "amount": record["amount"]}
''',
"docs/current-api.md": """# Current invoice API

POST /invoices accepts tenant_id and amount. The SDK create_invoice method
takes tenant_id. Invoice records and exports carry tenant_id. Legacy CSV
imports are documented separately and retain their historical input format.
""",
"docs/legacy-csv.md": """# Legacy import compatibility

Legacy CSV files require the tenant_id column. Never rename this historical
input field to workspace_id. The adapter translates old input into the current
invoice record format. Archived release notes also retain old field names.
""",
"tests/test_amounts.py": '''import unittest
from atlas.api import create_invoice

class AmountTests(unittest.TestCase):
    def test_negative_amount_is_rejected(self):
        with self.assertRaises(ValueError):
            create_invoice({"tenant_id": "a", "amount": -1})
''',
}

# Small but real utility modules: their behavior remains outside this migration.
UTILITIES = {
"money": "return round(amount * 100)",
"discount": "return max(0, amount - 5)",
"tax": "return round(amount * 1.08, 2)",
"refund": "return -abs(amount)",
"rounding": "return round(amount, 2)",
"credit": "return max(0, amount)",
"debit": "return min(0, amount)",
"balance": "return amount == 0",
"limits": "return 0 <= amount <= 1000000",
"fees": "return amount + 0.30",
"currency": "return f'USD {amount:.2f}'",
"reminders": "return 'send' if amount > 0 else 'skip'",
"overdue": "return amount > 30",
"installments": "return amount / 4",
"annual": "return amount * 12",
"monthly": "return amount / 12",
"retries": "return min(amount, 3)",
"backoff": "return 2 ** min(amount, 10)",
"quotas": "return amount < 100",
"usage": "return amount * 0.001",
"thresholds": "return amount >= 1000",
"percentage": "return amount / 100",
"commissions": "return amount * 0.05",
"receipt": "return f'Paid: {amount:.2f}'",
"aging": "return 'late' if amount > 60 else 'current'",
"ledger": "return {'debit': max(0, amount), 'credit': max(0, -amount)}",
"reserves": "return amount * 0.1",
"settlements": "return round(amount - amount * 0.02, 2)",
"shipping": "return 0 if amount > 50 else 5",
"loyalty": "return int(amount // 10)",
}
for name, body in UTILITIES.items():
    FILES[f"atlas/utilities/{name}.py"] = f'"""Pure {name} calculation used by billing operations."""\n\ndef calculate(amount):\n    {body}\n'
FILES["atlas/utilities/__init__.py"] = '"""Billing arithmetic utilities."""\n'
for version in range(1, 13):
    FILES[f"docs/archive/v{version}.md"] = (
        f"# Atlas release {version} archive\n\n"
        f"Historical release {version} used tenant_id across the invoice API, SDK, "
        "authorization, queries, exports, and schema validation. This archived "
        "contract remains unchanged when the current API migrates to workspace_id.\n"
    )

if __name__ == "__main__":
    for name, content in FILES.items():
        path = ROOT / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    print(f"Built {len(FILES)} owned fixture files in {ROOT}")
