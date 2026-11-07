"""Pure retries calculation used by billing operations."""

def calculate(amount):
    return min(amount, 3)
