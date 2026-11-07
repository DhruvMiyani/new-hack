def display_owner(record):
    owner = record.get("owner")
    return owner.upper() if owner else "unknown"
