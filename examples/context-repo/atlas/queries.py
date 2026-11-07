def list_invoices(rows, tenant_id):
    return [row for row in rows if row["tenant_id"] == tenant_id]
