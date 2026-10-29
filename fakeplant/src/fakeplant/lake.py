"""Fake exports as data-lake tables: each CSV table rewritten as a folder of Parquet part files."""

import csv
import shutil
from pathlib import Path


def write_parquet(csv_path: Path, out_dir: Path, rows_per_file: int = 100_000) -> int:
    """A CSV export as a lake table: a folder of Parquet part files, every column text."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    out_dir.mkdir(parents=True, exist_ok=True)
    with csv_path.open(newline="") as f:
        reader = csv.DictReader(f)
        schema = pa.schema([(c, pa.string()) for c in reader.fieldnames or []])
        part, n, chunk = 0, 0, []
        for row in reader:
            chunk.append(row)
            if len(chunk) == rows_per_file:
                pq.write_table(pa.Table.from_pylist(chunk, schema), out_dir / f"part-{part:05d}.parquet")
                part, n, chunk = part + 1, n + len(chunk), []
        if chunk or part == 0:
            pq.write_table(pa.Table.from_pylist(chunk, schema), out_dir / f"part-{part:05d}.parquet")
            n += len(chunk)
    return n


def to_parquet(out: Path) -> int:
    """Rewrite every table Fuzer has a mapping for as a lake-style Parquet folder."""
    from fuzer import ingest

    n = 0
    for name in ingest.tables():
        if (csv_path := out / f"{name}.csv").exists():
            shutil.rmtree(out / name, ignore_errors=True)
            write_parquet(csv_path, out / name)
            csv_path.unlink()
            n += 1
    return n


def clear_lake_folders(out: Path) -> None:
    """A lake folder left from an earlier --format parquet run would win over new CSV exports."""
    from fuzer import ingest

    for name in ingest.tables():
        shutil.rmtree(out / name, ignore_errors=True)
