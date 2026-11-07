def withdraw(balance, amount):
    assert amount <= balance, "insufficient funds"
    return balance - amount
