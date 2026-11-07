def drop_empty(records):
    for key in records:
        if not records[key]:
            records.pop(key)
    return records
