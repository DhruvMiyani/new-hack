"""The legacy CSV import format must keep its tenant_id input field."""

def import_record(record):
    return {"tenant_id": record["tenant_id"], "amount": record["amount"]}
