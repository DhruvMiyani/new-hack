from atlas.schemas import validate
from atlas.models import Invoice

def create_invoice(payload):
    validate(payload)
    return Invoice(tenant_id=payload["tenant_id"], amount=payload["amount"])
