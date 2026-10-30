"""PI Web API: the PI point list and the PI AF tree as live datasets (#15), and process values (#17).

The datasets yield the same rows as the file exports (`pi_points`: tag, descriptor, engunits,
pointsource; `pi_af`: one record per element with its template and PI point attributes), so the
mappings and everything after them don't change. Point attributes and element attributes are
fetched through /batch, a few hundred per request, so a 300k-point server takes hundreds of
requests, not hundreds of thousands.

Settings: FUZER_PI_WEB_API_URL (e.g. https://pi.example.com/piwebapi), FUZER_PI_DATA_SERVER,
FUZER_PI_AF_SERVER and FUZER_PI_AF_DATABASE (default: the first one), FUZER_PI_USER /
FUZER_PI_PASSWORD (basic auth), FUZER_PI_VERIFY_TLS=0 for a self-signed test server. A fake server
to try it on: `uv run fakeplant historian`.
"""

import os
from collections.abc import Iterator
from datetime import datetime

import httpx

from .connectors import LiveTable

PAGE = 1000
BATCH = 500


def configured() -> bool:
    return bool(os.environ.get("FUZER_PI_WEB_API_URL"))


class Client:
    def __init__(self, url: str | None = None, http: httpx.Client | None = None):
        if not (url := url or os.environ.get("FUZER_PI_WEB_API_URL")):
            raise ValueError("FUZER_PI_WEB_API_URL isn't set")
        self.url = url.rstrip("/")
        if http is None:
            user = os.environ.get("FUZER_PI_USER")
            http = httpx.Client(auth=(user, os.environ.get("FUZER_PI_PASSWORD", "")) if user else None,
                                verify=os.environ.get("FUZER_PI_VERIFY_TLS", "1") != "0", timeout=120)
        self.http = http

    def get(self, resource: str, **params) -> dict:
        r = self.http.get(f"{self.url}/{resource}", params=params)
        r.raise_for_status()
        return r.json()

    def items(self, path: str, **params) -> Iterator[dict]:
        start = 0
        while True:
            page = self.get(path, startIndex=start, maxCount=PAGE, **params)["Items"]
            yield from page
            if len(page) < PAGE:
                return
            start += PAGE

    def batch(self, resources: list[str]) -> list[dict]:
        """GET each resource in one /batch request; the Content of each, in order (None on failure)."""
        out = []
        for i in range(0, len(resources), BATCH):
            chunk = resources[i:i + BATCH]
            r = self.http.post(f"{self.url}/batch",
                               json={str(n): {"Method": "GET", "Resource": res} for n, res in enumerate(chunk)})
            r.raise_for_status()
            body = r.json()
            out += [(body.get(str(n)) or {}).get("Content") if (body.get(str(n)) or {}).get("Status") == 200
                    else None for n in range(len(chunk))]
        return out

    def _pick(self, path: str, env: str) -> dict:
        items = self.get(path)["Items"]
        want = os.environ.get(env)
        found = next((x for x in items if x["Name"] == want), None) if want else (items[0] if items else None)
        if found is None:
            raise LookupError(f"{env}={want!r} not found at {self.url}/{path}" if want else f"nothing at {path}")
        return found

    def data_server(self) -> dict:
        return self._pick("dataservers", "FUZER_PI_DATA_SERVER")

    def af_database(self) -> dict:
        server = self._pick("assetservers", "FUZER_PI_AF_SERVER")
        return self._pick(f"assetservers/{server['WebId']}/assetdatabases", "FUZER_PI_AF_DATABASE")


class PiPoints(LiveTable):
    kind = "pi_web_api"
    columns = ["tag", "descriptor", "engunits", "pointsource"]

    def __init__(self, client: Client | None = None):
        self.client = client or Client()
        super().__init__(self.client.url)

    def read(self) -> Iterator[dict]:
        server = self.client.data_server()
        page: list[dict] = []
        for p in self.client.items(f"dataservers/{server['WebId']}/points"):
            page.append(p)
            if len(page) == BATCH:
                yield from self._rows_for(page)
                page = []
        yield from self._rows_for(page)

    def _rows_for(self, points: list[dict]) -> Iterator[dict]:
        attrs = self.client.batch([f"{self.client.url}/points/{p['WebId']}/attributes" for p in points])
        for p, a in zip(points, attrs):
            extra = {x["Name"]: x["Value"] for x in (a or {}).get("Items", [])}
            yield {"tag": p["Name"], "descriptor": p.get("Descriptor") or "",
                   "engunits": p.get("EngineeringUnits") or "", "pointsource": str(extra.get("pointsource") or "")}


class PiAf(LiveTable):
    """The AF element tree. Like the pi_af.json export, it is a nested dataset with no column schema."""

    kind = "pi_web_api"

    def __init__(self, client: Client | None = None):
        self.client = client or Client()
        super().__init__(self.client.url)

    def read(self) -> Iterator[dict]:
        db = self.client.af_database()
        prefix = db["Path"] + "\\"
        elements = list(self.client.items(f"assetdatabases/{db['WebId']}/elements", searchFullHierarchy="true"))
        attrs = self.client.batch([f"{self.client.url}/elements/{e['WebId']}/attributes" for e in elements])
        for e, a in zip(elements, attrs):
            yield {"path": e["Path"].removeprefix(prefix), "template": e.get("TemplateName") or "",
                   "attributes": [_attribute(x) for x in (a or {}).get("Items", [])]}

    def records(self):
        for el in self.rows():
            yield "pi_af", "element", el["path"], el


def _attribute(a: dict) -> dict:
    out = {"name": a["Name"]}
    if a.get("DataReferencePlugIn") == "PI Point" and a.get("ConfigString"):
        out["pi_point"] = a["ConfigString"].split(";")[0].rsplit("\\", 1)[-1]
    return out | {"uom": a.get("DefaultUnitsName") or ""}


DATASETS = {"pi_points": PiPoints, "pi_af": PiAf}


class Provider:
    """Interpolated values for a PI point, looked up by name on the data server."""

    name = "pi"

    def __init__(self, client: Client | None = None):
        self.client = client or Client()
        self._server: str | None = None
        self._web_ids: dict[str, str | None] = {}

    def _web_id(self, point: str) -> str | None:
        if point not in self._web_ids:
            self._server = self._server or self.client.data_server()["Path"]
            try:
                self._web_ids[point] = self.client.get("points", path=f"{self._server}\\{point}")["WebId"]
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 404:
                    raise
                self._web_ids[point] = None
        return self._web_ids[point]

    def series(self, s: dict, start: datetime, end: datetime, step_minutes: int) -> list[tuple[str, float]]:
        if (wid := self._web_id(s["point"])) is None:
            return []
        items = self.client.get(f"streams/{wid}/interpolated", startTime=start.isoformat() + "Z",
                                endTime=end.isoformat() + "Z", interval=f"{step_minutes}m")["Items"]
        # digital states and bad values come back as objects; trends show numbers only
        return [(i["Timestamp"][:16], float(i["Value"])) for i in items
                if isinstance(i.get("Value"), (int, float)) and i.get("Good", True)]
