"""Pure backoff calculation used by billing operations."""

def calculate(amount):
    return 2 ** min(amount, 10)
