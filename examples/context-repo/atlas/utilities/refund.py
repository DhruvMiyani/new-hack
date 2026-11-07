"""Pure refund calculation used by billing operations."""

def calculate(amount):
    return -abs(amount)
