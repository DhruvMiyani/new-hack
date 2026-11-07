"""Pure aging calculation used by billing operations."""

def calculate(amount):
    return 'late' if amount > 60 else 'current'
