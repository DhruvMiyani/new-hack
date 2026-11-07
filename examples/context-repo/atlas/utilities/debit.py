"""Pure debit calculation used by billing operations."""

def calculate(amount):
    return min(0, amount)
