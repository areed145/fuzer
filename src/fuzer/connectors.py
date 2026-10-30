"""Connectors: how a dataset's rows arrive, separate from what the rows mean.

Every connector gives the same three things, so ingest treats a CSV export, a folder of Parquet
part files and a Delta table alike:

- fingerprint(): a cheap change marker (file sizes and times, or the Delta table version). When it
  hasn't changed since the last load, ingest skips the dataset without reading a row.
- schema(): [[column, type], ...], compared with the last load to catch renamed or retyped columns.
- rows(since=None): the rows as plain dicts. Lake connectors can filter on a watermark column
  (`since=(column, value)`) so append-only tables read only rows at or after the last position.

Tables are found by name in the data folder: `<name>/` (Delta if it has a `_delta_log`, else
Parquet part files), `<name>.parquet`, `<name>.csv` or `<name>.geojson` (GIS), in that order. ADLS later: the same pyarrow
datasets over an fsspec filesystem.

Some datasets can come from a live system instead (`live()`): with FUZER_PI_WEB_API_URL set, the PI
point list and AF tree are read through PI Web API (pi_web_api.py); with FUZER_ADX_URL, the ADX
series catalog (adx.py). They yield the same rows as the file exports, as a full snapshot.
"""

import csv
import datetime as dt
import hashlib
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

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


class GeoJsonTable:
    """A GeoJSON FeatureCollection (a GIS export): one row per feature, its properties plus `geometry`
    (the GeoJSON geometry, kept as is) and `geometry_type`. Always a full snapshot."""

    kind = "geojson"
    watermarks = False

    def __init__(self, path: Path):
        self.path = path

    def _features(self) -> list[dict]:
        import json

        return json.loads(self.path.read_text()).get("features", [])

    def fingerprint(self) -> str:
        return _stat([self.path], self.path.parent)

    def schema(self) -> list[list[str]]:
        cols = {}
        for f in self._features():
            for k, v in (f.get("properties") or {}).items():
                cols.setdefault(k, type(v).__name__ if v is not None else "null")
        return [[k, "string" if v == "str" else v] for k, v in cols.items()] + [["geometry", "geometry"],
                                                                                ["geometry_type", "string"]]

    def rows(self, since=None) -> Iterator[dict]:
        for f in self._features():
            g = f.get("geometry") or {}
            yield {**(f.get("properties") or {}), "geometry": g, "geometry_type": g.get("type")}


class LiveTable:
    """A dataset read from a live system. There is no cheap change marker, so the first call reads
    everything once (kept for the rows() that follows) and the fingerprint is a hash of the content."""

    watermarks = False
    live = True
    kind = "live"
    columns: list[str] = []

    def __init__(self, path: str):
        self.path = path  # the server URL, shown in reviews and job metadata
        self._rows: list[dict] | None = None

    def read(self) -> Iterator[dict]:
        raise NotImplementedError

    def _all(self) -> list[dict]:
        if self._rows is None:
            self._rows = list(self.read())
        return self._rows

    def fingerprint(self) -> str:
        import json

        return hashlib.sha1(json.dumps(self._all(), sort_keys=True, default=str).encode()).hexdigest()

    def schema(self) -> list[list[str]] | None:
        return [[c, "string"] for c in self.columns] if self.columns else None

    def rows(self, since=None) -> Iterator[dict]:
        yield from self._all()


def live(name: str):
    """The live connector for dataset `name` when its system is configured, else None."""
    from . import adx, pi_web_api

    if pi_web_api.configured() and name in pi_web_api.DATASETS:
        return pi_web_api.DATASETS[name]()
    if adx.configured() and name in adx.DATASETS:
        return adx.DATASETS[name]()
    return None


def find(data_dir: Path, name: str):
    """The connector for table `name` in data_dir, or None when it isn't there."""
    folder = data_dir / name
    if folder.is_dir() and ((folder / "_delta_log").is_dir() or any(folder.glob("*.parquet")) or
                            any(p.is_dir() and "=" in p.name for p in folder.iterdir())):  # key=value partitions
        return ParquetTable(folder)
    if (data_dir / f"{name}.parquet").is_file():
        return ParquetTable(data_dir / f"{name}.parquet")
    if (data_dir / f"{name}.csv").is_file():
        return CsvTable(data_dir / f"{name}.csv")
    if (data_dir / f"{name}.geojson").is_file():
        return GeoJsonTable(data_dir / f"{name}.geojson")
    return None


def schema_drift(old: list[list[str]] | None, new: list[list[str]]) -> dict:
    """Columns added, removed or retyped since the last load. Added columns are harmless; removed or
    retyped ones may break what the rows are taken to mean, so they hold the dataset for review."""
    if not old:
        return {"added": [], "removed": [], "retyped": []}
    o, n = dict(map(tuple, old)), dict(map(tuple, new))
    return {"added": [c for c in n if c not in o], "removed": [c for c in o if c not in n],
            "retyped": [{"column": c, "was": o[c], "now": n[c]} for c in n if c in o and o[c] != n[c]]}
