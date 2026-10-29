"""Connectors: how a dataset's rows arrive, separate from what the rows mean.

Every connector gives the same three things, so ingest treats a CSV export, a folder of Parquet
part files and a Delta table alike:

- fingerprint(): a cheap change marker (file sizes and times, or the Delta table version). When it
  hasn't changed since the last load, ingest skips the dataset without reading a row.
- schema(): [[column, type], ...], compared with the last load to catch renamed or retyped columns.
- rows(since=None): the rows as plain dicts. Lake connectors can filter on a watermark column
  (`since=(column, value)`) so append-only tables read only rows at or after the last position.

Lake tables are found by name in the data folder: `<name>/` (Delta if it has a `_delta_log`, else
Parquet part files), `<name>.parquet` or `<name>.csv`, in that order. ADLS later: the same pyarrow
datasets over an fsspec filesystem.
"""

import csv
import datetime as dt
import hashlib
from decimal import Decimal
from pathlib import Path
from typing import Iterator

BATCH_ROWS = 50_000


def _stat(paths: list[Path], root: Path) -> str:
    parts = [f"{p.relative_to(root)}:{p.stat().st_size}:{p.stat().st_mtime_ns}" for p in paths]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()


def _type(t) -> str:
    s = str(t)
    return {"large_string": "string", "string_view": "string"}.get(s, s)


def _jsonable(v):
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, bytes):
        return None
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v


class CsvTable:
    """One CSV export: always a full snapshot, every column text."""

    kind = "csv"
    watermarks = False

    def __init__(self, path: Path):
        self.path = path

    def fingerprint(self) -> str:
        return _stat([self.path], self.path.parent)

    def schema(self) -> list[list[str]]:
        with self.path.open(newline="") as f:
            return [[c, "string"] for c in next(csv.reader(f), [])]

    def rows(self, since=None) -> Iterator[dict]:
        with self.path.open(newline="") as f:
            yield from csv.DictReader(f)


class ParquetTable:
    """A Parquet file, a folder of Parquet part files, or a Delta table, read in batches."""

    watermarks = True

    def __init__(self, path: Path, columns: list[str] | None = None):
        self.path, self.columns = path, columns
        self.kind = "delta" if (path / "_delta_log").is_dir() else "parquet"

    def _files(self) -> list[Path]:
        return [self.path] if self.path.is_file() else sorted(self.path.rglob("*.parquet"))

    def _dataset(self):
        if self.kind == "delta":
            from deltalake import DeltaTable

            return DeltaTable(str(self.path)).to_pyarrow_dataset()
        import pyarrow.dataset as ds

        return ds.dataset(self._files(), format="parquet")

    def fingerprint(self) -> str:
        if self.kind == "delta":
            from deltalake import DeltaTable

            return f"delta:{DeltaTable(str(self.path)).version()}"
        return _stat(self._files(), self.path.parent)

    def schema(self) -> list[list[str]]:
        return [[f.name, _type(f.type)] for f in self._dataset().schema]

    def rows(self, since: tuple[str, str] | None = None) -> Iterator[dict]:
        import pyarrow.compute as pc

        data = self._dataset()
        filt = None
        if since and since[1] is not None:
            col, value = since
            filt = pc.field(col) >= pc.scalar(value).cast(data.schema.field(col).type)
        for batch in data.to_batches(columns=self.columns, filter=filt, batch_size=BATCH_ROWS):
            for row in batch.to_pylist():
                yield {k: _jsonable(v) for k, v in row.items()}


def find(data_dir: Path, name: str):
    """The connector for table `name` in data_dir, or None when it isn't there."""
    folder = data_dir / name
    if folder.is_dir() and ((folder / "_delta_log").is_dir() or any(folder.rglob("*.parquet"))):
        return ParquetTable(folder)
    if (data_dir / f"{name}.parquet").is_file():
        return ParquetTable(data_dir / f"{name}.parquet")
    if (data_dir / f"{name}.csv").is_file():
        return CsvTable(data_dir / f"{name}.csv")
    return None


def schema_drift(old: list[list[str]] | None, new: list[list[str]]) -> dict:
    """Columns added, removed or retyped since the last load. Added columns are harmless; removed or
    retyped ones may break what the rows are taken to mean, so they hold the dataset for review."""
    if not old:
        return {"added": [], "removed": [], "retyped": []}
    o, n = dict(map(tuple, old)), dict(map(tuple, new))
    return {"added": [c for c in n if c not in o], "removed": [c for c in o if c not in n],
            "retyped": [{"column": c, "was": o[c], "now": n[c]} for c in n if c in o and o[c] != n[c]]}


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
