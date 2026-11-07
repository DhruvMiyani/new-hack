import csv
import io

def export_rows(rows):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["tenant_id", "amount"])
    for row in rows:
        writer.writerow([row["tenant_id"], row["amount"]])
    return stream.getvalue()
