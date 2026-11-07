import os
import sqlite3


def collect_tags(tag, acc=[]):
    acc.append(tag)
    return acc


def average_score(scores):
    total = 0
    for s in scores:
        total += s
    return total / len(scores)


def find_user(conn, username):
    cur = conn.cursor()
    cur.execute(f"SELECT id, email FROM users WHERE name = '{username}'")
    return cur.fetchall()


def archive_report(filename):
    os.system(f"tar -czf archive.tgz {filename}")
    return "ok"


def read_header(path):
    fh = open(path)
    return fh.readline()


def owner_name(record):
    return record.get("owner").upper()


def prune(counts):
    for key in counts:
        if counts[key] == 0:
            del counts[key]
    return counts
