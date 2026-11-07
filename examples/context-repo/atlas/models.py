from dataclasses import dataclass

@dataclass
class Invoice:
    tenant_id: str
    amount: int
