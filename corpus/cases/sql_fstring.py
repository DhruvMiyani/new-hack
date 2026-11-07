def lookup(conn, email):
    cur = conn.cursor()
    cur.execute(f"SELECT id FROM accounts WHERE email = '{email}'")
    return cur.fetchall()
