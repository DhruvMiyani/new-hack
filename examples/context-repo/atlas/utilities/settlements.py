"""Pure settlements calculation used by billing operations."""

def calculate(amount):
    return round(amount - amount * 0.02, 2)
