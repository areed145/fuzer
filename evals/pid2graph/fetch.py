"""Fetch this folder's slice of PID2Graph from Zenodo without downloading the whole 9.3 GB zip.

The archive is one zip, so this reads its central directory with HTTP range requests and pulls only
the members kept here: the 12 real OPEN100 drawings and the first 20 synthetic ones, from the
"Complete" plans. Standard library only. Run: python evals/pid2graph/fetch.py [out_dir]
"""

import io
import os
import sys
import urllib.request
import zipfile

URL = "https://zenodo.org/records/14803338/files/PID2Graph.zip?download=1"
SYNTHETIC = 20


class _Http(io.RawIOBase):
    def __init__(self, url):
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD")) as r:
            self.size, self.url = int(r.headers["Content-Length"]), r.geturl()
        self.pos = 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = {0: off, 1: self.pos + off, 2: self.size + off}[whence]
        return self.pos

    def readinto(self, b):
        if self.pos >= self.size or not len(b):
            return 0
        end = min(self.pos + len(b), self.size) - 1
        req = urllib.request.Request(self.url, headers={"Range": f"bytes={self.pos}-{end}"})
        with urllib.request.urlopen(req) as r:
            data = r.read()
        b[: len(data)] = data
        self.pos += len(data)
        return len(data)


def _kept(name):
    if "/Complete/PID2Graph OPEN100/" in name:
        return "open100"
    if "/Complete/PID2Graph Synthetic/" in name and int(os.path.basename(name).split(".")[0]) < SYNTHETIC:
        return "synthetic"
    return None


def main(out):
    z = zipfile.ZipFile(io.BufferedReader(_Http(URL), buffer_size=1 << 20))
    n = 0
    for info in z.infolist():
        sub = info.file_size and _kept(info.filename)
        if sub:
            os.makedirs(os.path.join(out, sub), exist_ok=True)
            with open(os.path.join(out, sub, os.path.basename(info.filename)), "wb") as f:
                f.write(z.read(info))
            n += 1
    print(f"{n} files to {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__)))
