"""Pure ledger calculation used by billing operations."""

def calculate(amount):
    return {'debit': max(0, amount), 'credit': max(0, -amount)}
