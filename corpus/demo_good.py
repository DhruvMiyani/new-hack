import subprocess


def collect_tags(tag, acc=None):
    acc = [] if acc is None else acc
    acc.append(tag)
    return acc


def average_score(scores):
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def find_user(conn, username):
    cur = conn.cursor()
    cur.execute("SELECT id, email FROM users WHERE name = ?", (username,))
    return cur.fetchall()


def archive_report(filename):
    subprocess.run(["tar", "-czf", "archive.tgz", filename], check=True)
    return "ok"


def read_header(path):
    with open(path) as fh:
        return fh.readline()


def owner_name(record):
    owner = record.get("owner")
    return owner.upper() if owner else ""


def prune(counts):
    return {k: v for k, v in counts.items() if v != 0}
