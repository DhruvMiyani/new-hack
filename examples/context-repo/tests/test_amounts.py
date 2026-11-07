import unittest
from atlas.api import create_invoice

class AmountTests(unittest.TestCase):
    def test_negative_amount_is_rejected(self):
        with self.assertRaises(ValueError):
            create_invoice({"tenant_id": "a", "amount": -1})
