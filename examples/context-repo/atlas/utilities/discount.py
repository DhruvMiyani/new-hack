"""Pure discount calculation used by billing operations."""

def calculate(amount):
    return max(0, amount - 5)
