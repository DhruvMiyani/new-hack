"""Pure reminders calculation used by billing operations."""

def calculate(amount):
    return 'send' if amount > 0 else 'skip'
