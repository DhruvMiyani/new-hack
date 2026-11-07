# Current invoice API

POST /invoices accepts tenant_id and amount. The SDK create_invoice method
takes tenant_id. Invoice records and exports carry tenant_id. Legacy CSV
imports are documented separately and retain their historical input format.
