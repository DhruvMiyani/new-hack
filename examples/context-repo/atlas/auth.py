def can_read(user, record):
    return user["tenant_id"] == record["tenant_id"]
