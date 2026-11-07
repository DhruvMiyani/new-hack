REQUIRED_FIELDS = ("tenant_id", "amount")

def validate(payload):
    if any(key not in payload for key in REQUIRED_FIELDS):
        raise ValueError("Missing required invoice field")
    if payload["amount"] < 0:
        raise ValueError("Amount cannot be negative")
    return True
