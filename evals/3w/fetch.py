"""Fetch this folder's slice of the 3W Dataset from the 3W repository on GitHub.

Each instance is one parquet file under `dataset/<event>/` there; it is saved as
`data/c<event>_<instance>.parquet`. Standard library only. Run: python evals/3w/fetch.py [out_dir]
"""

import sys
import urllib.request
from pathlib import Path

URL = "https://raw.githubusercontent.com/petrobras/3W/main/dataset/{event}/{instance}.parquet"
INSTANCES = [(0, "WELL-00002_20131214150127"), (0, "WELL-00003_20170820220053"), (1, "WELL-00002_20140126180030"),
             (2, "WELL-00003_20180206171508"), (2, "WELL-00011_20140726165720"), (5, "WELL-00016_20180426131710"),
             (6, "WELL-00002_20140212160333"), (6, "WELL-00002_20140325160304")]


def main(out: Path) -> None:
    (out / "data").mkdir(parents=True, exist_ok=True)
    for event, instance in INSTANCES:
        path = out / "data" / f"c{event}_{instance}.parquet"
        with urllib.request.urlopen(URL.format(event=event, instance=instance)) as r:
            path.write_bytes(r.read())
        print(path, path.stat().st_size)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent)
