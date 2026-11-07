# Legacy import compatibility

Legacy CSV files require the tenant_id column. Never rename this historical
input field to workspace_id. The adapter translates old input into the current
invoice record format. Archived release notes also retain old field names.
