def load_text(filename):
    f = open(filename, "r")
    data = f.read()
    return data.upper()
