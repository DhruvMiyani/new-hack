"""Pure tax calculation used by billing operations."""

def calculate(amount):
    return round(amount * 1.08, 2)
