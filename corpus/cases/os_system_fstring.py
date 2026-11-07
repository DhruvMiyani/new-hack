import os

def compress(filename):
    os.system(f"gzip -k {filename}")
    return filename + ".gz"
