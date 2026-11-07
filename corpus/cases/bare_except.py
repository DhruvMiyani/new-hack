def safe_parse(text):
    try:
        return int(text)
    except:
        return None
