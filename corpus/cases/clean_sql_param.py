def lookup(conn, email):
    cur = conn.cursor()
    cur.execute("SELECT id FROM accounts WHERE email = ?", (email,))
    return cur.fetchall()
