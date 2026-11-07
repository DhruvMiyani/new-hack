"""Pure shipping calculation used by billing operations."""

def calculate(amount):
    return 0 if amount > 50 else 5
