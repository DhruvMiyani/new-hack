def delete_post(cursor, slug):
    cursor.execute("DELETE FROM posts WHERE slug = '%s'" % slug)
    return True
