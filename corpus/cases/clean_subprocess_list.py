import subprocess

def compress(filename):
    subprocess.run(["gzip", "-k", filename], check=True)
    return filename + ".gz"
