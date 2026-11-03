"""Azure Data Explorer: the series catalog as a live dataset (#16), and process values read where they
already are (#17). Fuzer keeps a pointer on each timeseries entity (cluster, database, table, series
id), never the values.

The catalog is a table with one row per series landed in ADX: SeriesId, PiPoint, Description, Units,
ValuesTable. The values table has SeriesId, Timestamp, Value. Both names follow the common PI-to-ADX
landing layout; FUZER_ADX_CATALOG names a different catalog table.

Settings: FUZER_ADX_URL (cluster URI, e.g. https://mycluster.westeurope.kusto.windows.net),
FUZER_ADX_DATABASE, FUZER_ADX_TOKEN (a bearer token; Entra ID sign-in comes with #12). A fake
cluster to try it on: `uv run fakeplant historian`.
"""

import os
import re
from collections.abc import Iterator
from datetime import datetime

import httpx

from .connectors import LiveTable

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def configured() -> bool:
    return bool(os.environ.get("FUZER_ADX_URL"))


def quote(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


class Client:
    def __init__(self, url: str | None = None, database: str | None = None, http: httpx.Client | None = None):
        if not (url := url or os.environ.get("FUZER_ADX_URL")):
            raise ValueError("FUZER_ADX_URL isn't set")
        self.url = url.rstrip("/")
        self.database = database or os.environ.get("FUZER_ADX_DATABASE", "")
        if http is None:
            token = os.environ.get("FUZER_ADX_TOKEN")
            http = httpx.Client(headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=120)
        self.http = http

    def query(self, csl: str) -> list[dict]:
        """Rows of the first result table as dicts (REST API v1)."""
        r = self.http.post(f"{self.url}/v1/rest/query", json={"db": self.database, "csl": csl})
        if r.status_code >= 400:
            err = (r.json().get("error") or {}) if "json" in r.headers.get("content-type", "") else {}
            raise RuntimeError(f"ADX query failed ({r.status_code}): {err.get('@message') or err.get('message') or r.text}")
        table = r.json()["Tables"][0]
        names = [c["ColumnName"] for c in table["Columns"]]
        return [dict(zip(names, row)) for row in table["Rows"]]


class SeriesCatalog(LiveTable):
    kind = "adx"
    columns = ["series_id", "pi_point", "description", "units", "values_table", "cluster", "database"]

    def __init__(self, client: Client | None = None):
        self.client = client or Client()
        super().__init__(f"{self.client.url}/{self.client.database}")

    def read(self) -> Iterator[dict]:
        catalog = os.environ.get("FUZER_ADX_CATALOG", "SeriesCatalog")
        if not IDENT.match(catalog):
            raise ValueError(f"bad catalog table name {catalog!r}")
        for r in self.client.query(f"{catalog} | project SeriesId, PiPoint, Description, Units, ValuesTable"):
            yield {"series_id": r["SeriesId"], "pi_point": r["PiPoint"], "description": r["Description"] or "",
                   "units": r["Units"] or "", "values_table": r["ValuesTable"], "cluster": self.client.url,
                   "database": self.client.database}


DATASETS = {"adx_series": SeriesCatalog}


class Provider:
    """Values for a series through its ADX pointer, averaged into step-sized bins by ADX itself."""

    name = "adx"

    def __init__(self, client: Client | None = None):
        self.client = client
        self._clients: dict[tuple[str, str], Client] = {}

    def _for(self, s: dict) -> Client:
        if self.client is not None:
            return self.client
        key = (s["adx_cluster"], s["adx_database"])
        if key not in self._clients:
            self._clients[key] = Client(*key)
        return self._clients[key]

    def series(self, s: dict, start: datetime, end: datetime, step_minutes: int) -> list[tuple[str, float]]:
        if not s.get("adx_series_id"):
            return []  # this point isn't landed in ADX
        table = s.get("adx_table") or "PiValues"
        if not IDENT.match(table):
            raise ValueError(f"bad ADX table name {table!r}")
        rows = self._for(s).query(
            f"{table} | where SeriesId == {quote(s['adx_series_id'])} and Timestamp between "
            f"(datetime({start.isoformat()}) .. datetime({end.isoformat()})) "
            f"| summarize Value = avg(Value) by bin(Timestamp, {step_minutes}m) | order by Timestamp asc")
        return [(r["Timestamp"][:16], round(float(r["Value"]), 3)) for r in rows if r["Value"] is not None]
