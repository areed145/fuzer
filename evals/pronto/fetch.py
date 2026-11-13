"""Fetch this folder's slice of the PRONTO benchmark from Zenodo without downloading the whole 1.7 GB zip.

The archive is one zip, so this reads its central directory with HTTP range requests and pulls only the
three labelled days the authors aligned and labelled ("Pre-processed data/Aligned and labelled alarm and
process data/Testday{2,3,4}_merged.csv"), and gzips each as it is. Standard library only.
Run: python evals/pronto/fetch.py [out_dir]
"""

import gzip
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

URL = "https://zenodo.org/records/1341583/files/PRONTO%20benchmark%20case%20study.zip?download=1"
FOLDER = "PRONTO benchmark case study/Pre-processed data/Aligned and labelled alarm and process data/"
DAYS = ["Testday2_merged.csv", "Testday3_merged.csv", "Testday4_merged.csv"]


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


def main(out: Path) -> None:
    (out / "data").mkdir(parents=True, exist_ok=True)
    z = zipfile.ZipFile(io.BufferedReader(_Http(URL), 1 << 16))
    for name in DAYS:
        path = out / "data" / f"{name}.gz"
        path.write_bytes(gzip.compress(z.read(FOLDER + name), 9, mtime=0))
        print(path, path.stat().st_size)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent)
