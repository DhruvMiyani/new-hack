class Client:
    def create_invoice(self, tenant_id, amount):
        return {"tenant_id": tenant_id, "amount": amount}
