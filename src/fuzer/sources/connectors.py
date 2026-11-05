"""Connectors: how a dataset's rows arrive, separate from what the rows mean.

Every connector gives the same three things, so ingest treats a CSV export, a folder of Parquet
part files and a Delta table alike:

- fingerprint(): a cheap change marker (file sizes and times, or the Delta table version). When it
  hasn't changed since the last load, ingest skips the dataset without reading a row.
- schema(): [[column, type], ...], compared with the last load to catch renamed or retyped columns.
- rows(since=None): the rows as plain dicts. Lake connectors can filter on a watermark column
  (`since=(column, value)`) so append-only tables read only rows at or after the last position.

Tables are found by name in the data folder: `<name>/` (Delta if it has a `_delta_log`, else
Parquet part files), `<name>.parquet`, `<name>.csv`, `<name>.geojson` (GIS), `<name>.xlsx` (its first
sheet; `<book>.<sheet>` names one sheet of `<book>.xlsx`), `<name>.jsonl` or `<name>.json` (a list of
objects), in that order (#132). ADLS later: the same pyarrow datasets over an fsspec filesystem.

Some datasets can come from a live system instead (`live()`): with FUZER_PI_WEB_API_URL set, the PI
point list and AF tree are read through PI Web API (pi_web_api.py); with FUZER_ADX_URL, the ADX
series catalog (adx.py). They yield the same rows as the file exports, as a full snapshot.
"""

import csv
import datetime as dt
import hashlib
import re
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

    def __init__(self, path: Path, encoding: str | None = None):
        self.path, self.encoding = path, encoding or "utf-8"

    def fingerprint(self) -> str:
        return _stat([self.path], self.path.parent)

    def schema(self) -> list[list[str]]:
        with self.path.open(newline="", encoding=self.encoding) as f:
            return [[c, "string"] for c in next(csv.reader(f), [])]

    def rows(self, since=None) -> Iterator[dict]:
        with self.path.open(newline="", encoding=self.encoding) as f:
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


def _schema_of(rows: list[dict]) -> list[list[str]]:
    """Columns in first-seen order, typed by their first non-empty value."""
    cols: dict[str, str] = {}
    for r in rows:
        for k, v in r.items():
            if cols.get(k, "null") == "null":
                cols[k] = "null" if v is None else {"str": "string", "datetime": "timestamp"}.get(type(v).__name__,
                                                                                                    type(v).__name__)
    return [[k, v] for k, v in cols.items()]


def _jtype(v) -> str:
    if isinstance(v, bool) or v is None:
        return "null" if v is None else "bool"
    return "number" if isinstance(v, int | float) else "string" if isinstance(v, str) else "array" \
        if isinstance(v, list) else type(v).__name__


_FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_ ]*$")


def json_shape(obj) -> list[list[str]]:
    """The fields of a nested JSON document as paths (`[].attributes[].name`) typed by their first non-null
    value, so nested exports and live trees get the same drift check as tables. A dict keyed by data (tags,
    `{"10-V-2001": ...}`) is a map: its values share one path (`positions{}`), so a new key isn't drift."""
    out: dict[str, str] = {}

    def walk(v, path: str) -> None:
        if isinstance(v, dict):
            if v and not all(_FIELD.match(str(k)) for k in v):
                for x in v.values():
                    walk(x, f"{path}{{}}")
                return
            for k, x in v.items():
                walk(x, f"{path}.{k}" if path else k)
        elif isinstance(v, list) and any(isinstance(x, dict | list) for x in v):
            for x in v:
                walk(x, f"{path}[]")
        elif path and not (isinstance(v, list) and not v) and out.get(path, "null") == "null":
            out[path] = _jtype(v)

    walk(obj, "")
    return [[k, v] for k, v in out.items()]


def _flatten(obj: dict, prefix: str = "") -> dict:
    """Nested objects become dotted fields (`site.area`); lists stay as they are."""
    out = {}
    for k, v in obj.items():
        if isinstance(v, dict):
            out |= _flatten(v, f"{prefix}{k}.")
        else:
            out[f"{prefix}{k}"] = v
    return out


class ExcelTable:
    """One sheet of an Excel workbook. The header is the first of the top 20 rows with the most text cells
    (title rows and blank rows above a table are skipped); the table runs to the last non-empty row. Always
    a full snapshot."""

    kind = "excel"
    watermarks = False

    def __init__(self, path: Path, sheet: str | None = None):
        self.path, self.sheet = path, sheet

    def _rows(self) -> list[dict]:
        from openpyxl import load_workbook

        wb = load_workbook(self.path, read_only=True, data_only=True)
        try:
            ws = wb[self.sheet] if self.sheet else wb.worksheets[0]
            grid = [list(r) for r in ws.iter_rows(values_only=True)]
        finally:
            wb.close()
        texts = [sum(isinstance(v, str) and v.strip() != "" for v in r) for r in grid[:20]]
        if not texts or max(texts) == 0:
            return []
        h = texts.index(max(texts))
        cols = [(i, str(v).strip()) for i, v in enumerate(grid[h]) if v is not None and str(v).strip()]
        out = []
        for r in grid[h + 1:]:
            row = {c: _jsonable(r[i]) if i < len(r) else None for i, c in cols}
            if any(v not in (None, "") for v in row.values()):
                out.append(row)
        return out

    def fingerprint(self) -> str:
        return _stat([self.path], self.path.parent) + (f":{self.sheet}" if self.sheet else "")

    def schema(self) -> list[list[str]]:
        return _schema_of(self._rows()[:200])

    def rows(self, since=None) -> Iterator[dict]:
        yield from self._rows()

    @staticmethod
    def sheets(path: Path) -> list[str]:
        from openpyxl import load_workbook

        wb = load_workbook(path, read_only=True)
        try:
            return list(wb.sheetnames)
        finally:
            wb.close()


class JsonTable:
    """Records in JSON: a `.jsonl` file (one object per line), or a `.json` file holding a list of objects
    (or an object with them under records, data, items or rows; any other object is a document, not a
    table). Nested objects are flattened to dotted fields. Always a full snapshot."""

    kind = "json"
    watermarks = False

    def __init__(self, path: Path):
        self.path = path

    def _rows(self) -> list[dict]:
        import json

        text = self.path.read_text()
        if self.path.suffix == ".jsonl":
            items = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            items = json.loads(text)
            if isinstance(items, dict):  # a wrapper object: only its conventional records key
                items = next((items[k] for k in ("records", "data", "items", "rows") if isinstance(items.get(k), list)), [])
        return [_flatten(x) for x in items if isinstance(x, dict)]

    def fingerprint(self) -> str:
        return _stat([self.path], self.path.parent)

    def schema(self) -> list[list[str]]:
        return _schema_of(self._rows()[:200])

    def rows(self, since=None) -> Iterator[dict]:
        yield from self._rows()


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
        """Flat tables: their columns. Nested ones (the AF tree) have no columns; their shape stands in."""
        return [[c, "string"] for c in self.columns] if self.columns else json_shape(self._all()) or None

    def rows(self, since=None) -> Iterator[dict]:
        yield from self._all()


def live(name: str):
    """The live connector for dataset `name` when its system is configured, else None."""
    from . import adx, pi_web_api

    # no catalog here (a caller without a database): only the settings say whether a live system is set
    if name in pi_web_api.DATASETS and (cfg := pi_web_api._from_settings()).get("url"):
        return pi_web_api.DATASETS[name](pi_web_api.Client(cfg=cfg))
    if name in adx.DATASETS and (cfg := adx._from_settings()).get("url"):
        return adx.DATASETS[name](adx.Client(cfg=cfg))
    return None


def is_table_folder(folder: Path) -> bool:
    """A folder that is itself one table: a Delta table, Parquet part files, or key=value partitions."""
    return folder.is_dir() and ((folder / "_delta_log").is_dir() or any(folder.glob("*.parquet")) or
                                any(p.is_dir() and "=" in p.name for p in folder.iterdir()))


def find(data_dir: Path, name: str):
    """The connector for table `name` in data_dir, or None when it isn't there."""
    folder = data_dir / name
    if is_table_folder(folder):
        return ParquetTable(folder)
    if (data_dir / f"{name}.parquet").is_file():
        return ParquetTable(data_dir / f"{name}.parquet")
    if (data_dir / f"{name}.csv").is_file():
        return CsvTable(data_dir / f"{name}.csv")
    if (data_dir / f"{name}.geojson").is_file():
        return GeoJsonTable(data_dir / f"{name}.geojson")
    if (data_dir / f"{name}.xlsx").is_file():
        return ExcelTable(data_dir / f"{name}.xlsx")
    book, _, sheet = name.rpartition(".")
    if book and (data_dir / f"{book}.xlsx").is_file() and sheet in ExcelTable.sheets(data_dir / f"{book}.xlsx"):
        return ExcelTable(data_dir / f"{book}.xlsx", sheet)
    if (data_dir / f"{name}.jsonl").is_file():
        return JsonTable(data_dir / f"{name}.jsonl")
    if (data_dir / f"{name}.json").is_file():
        t = JsonTable(data_dir / f"{name}.json")
        return t if t._rows() else None  # nested manifests (pi_af.json, ...) have their own loaders
    return None


def at(path: Path, kind: str | None = None):
    """The connector for a table at an explicit location (a mapping's `location`), or None when it isn't
    there. `kind` picks the reader; without it, the file's suffix or the folder's contents decide."""
    path, _, sheet = str(path).partition("#")
    path = Path(path)
    if not path.exists():
        return None
    kind = kind or ("excel" if path.suffix == ".xlsx" else path.suffix.lstrip(".") if path.is_file() else
                    "delta" if (path / "_delta_log").is_dir() else "parquet")
    if kind in ("parquet", "delta"):
        return ParquetTable(path)
    if kind == "excel":
        return ExcelTable(path, sheet or None)
    if kind in ("json", "jsonl"):
        return JsonTable(path)
    return {"csv": CsvTable, "geojson": GeoJsonTable}[kind](path) if kind in ("csv", "geojson") else None


def datasets(data_dir: Path) -> list[str]:
    """Every table name in data_dir a connector can read (an Excel workbook: one per sheet). A `.json` file
    is read when a mapping names it, but isn't offered as a new table: fake plants and exports keep other
    JSON beside their tables (manifests, process models). Inside a Parquet or Delta table folder there are no
    tables of their own: its part files are one table (the folder)."""
    if not data_dir.is_dir() or is_table_folder(data_dir):
        return []
    names = {p.stem for p in data_dir.glob("*.csv")} | {p.stem for p in data_dir.glob("*.parquet")} | \
            {p.stem for p in data_dir.glob("*.geojson")} | {p.stem for p in data_dir.glob("*.jsonl")}
    names |= {p.name for p in data_dir.iterdir() if p.is_dir() and find(data_dir, p.name)}
    for book in data_dir.glob("*.xlsx"):
        if not book.name.startswith("~$"):  # Excel's lock file
            sheets = ExcelTable.sheets(book)
            names |= {book.stem} if len(sheets) == 1 else {f"{book.stem}.{s}" for s in sheets}
    return sorted(names)


def schema_drift(old: list[list[str]] | None, new: list[list[str]]) -> dict:
    """Columns added, removed or retyped since the last load. Added columns are harmless; removed or
    retyped ones may break what the rows are taken to mean, so they hold the dataset for review."""
    if not old:
        return {"added": [], "removed": [], "retyped": []}
    o, n = dict(map(tuple, old)), dict(map(tuple, new))
    return {"added": [c for c in n if c not in o], "removed": [c for c in o if c not in n],
            "retyped": [{"column": c, "was": o[c], "now": n[c]} for c in n if c in o and o[c] != n[c]]}
