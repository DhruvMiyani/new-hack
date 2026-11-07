import subprocess

def count_lines(path):
    out = subprocess.check_output("wc -l " + path, shell=True)
    return out
