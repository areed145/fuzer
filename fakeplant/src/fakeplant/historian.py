"""A fake plant historian: the parts of PI Web API and Azure Data Explorer that Fuzer uses.

    uv run fakeplant historian                    # http://127.0.0.1:8100

One small web server, two faces over the same signals:

- PI Web API (`/piwebapi/...`): data server and point list (with point attributes through /batch),
  AF server, database, element tree and element attributes, and interpolated / recorded values. Bad values
  and interface outages planted in `instrument_faults.json` come back as PI system digital states, and
  recorded values are the swinging-door compressed archive (#868).
- ADX REST v1 (`POST /v1/rest/query`): a `SeriesCatalog` table (one row per PI point landed in ADX:
  series id, PI point, description, units, values table) and a `PiValues` table (SeriesId,
  Timestamp, Value; one raw sample every 5 minutes). Queries are a small subset of KQL: `where`
  (==, in, between datetime), `project`, `summarize ... avg/min/max/count ... by bin(Timestamp, Nm)`,
  `order by`, `take` and `print`. Anything else gets an ADX-style 400 error. Responses carry the four v1 tables
  ADX sends (result, @ExtendedProperties, QueryStatus, TableOfContents), as in Microsoft's REST reference.

The point list and AF tree come from the fake exports (pi_points, pi_af.json). Values are the
deterministic synthetic signals (fuzer.sources.timeseries.SyntheticProvider), and the upsets in them come
from the plant's own records, not from Fuzer: each downtime record and each operator log entry about
a trip, vibration, leak, alarm etc. upsets the points whose tag shares the area and loop number of
the first tag it names (20-K-2200B -> 20-VT-2200B, 20PT2200B). So trends lead into incidents the way they would on a real
historian. Tags are read with the bundle's own tag grammar when the exports sit in a seed bundle
(fakeplant/bundles/<name>/exports: Kestrel's lettered trains, KRGP:B1.PT2001.PV is loop 2001 in B1), else Okan's
numeric areas.

    uv run fakeplant historian --data fakeplant/bundles/kestrel/exports
"""

import base64
import json
import re
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from fuzer.sources import connectors, instruments
from fuzer.sources.timeseries import SyntheticProvider

RAW_STEP_MINUTES = 5
VALUES_TABLE = "PiValues"
UPSET_WORDS = re.compile(r"trip|vibration|leak|alarm|fault|high|low |shut|hydrate|carryover|surge|foul", re.IGNORECASE)
LOOP = re.compile(r"(?<![0-9])(\d{2})[-_ ]?[A-Z]{1,4}[-_ ]?(\d{3,4}[A-Z]?)(?![0-9A-Z])")


def loops(text: str, grammar=None) -> list[tuple[str, str]]:
    """(area, loop number) for every tag in text, first named first: 20-P-1001B, 20VT1001B and
    OK_GGCP:20-PT-1001B.PV all give ('20', '1001B'). With a pack's tag grammar, its own tags: KRGP:B1.VT2003.PV
    gives ('B1', '2003')."""
    if grammar is not None:
        return [(m.area, m.loop + m.suffix) for m in grammar.find_all(text) if m.area]
    return [(m.group(1), m.group(2)) for m in LOOP.finditer(text.upper())]


def bundle_grammar(data_dir: Path):
    """The tag grammar of the seed bundle whose exports these are, or None (Okan's generated folder has none)."""
    if not (Path(data_dir).parent / "bundle.yaml").is_file():
        return None
    from fuzer.core import bundles

    return bundles.read(Path(data_dir).parent)["pack"].tags


def _web_id(prefix: str, path: str) -> str:
    """Opaque, stable WebIDs (real ones are encoded too; clients never parse them)."""
    return prefix + base64.urlsafe_b64encode(path.encode()).decode().rstrip("=")


def _path_of(web_id: str) -> str:
    raw = web_id[2:]
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _time(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=None)


def _interval_minutes(s: str) -> int:
    m = re.fullmatch(r"\s*(\d+)\s*(s|m|min|h|d)\s*", s or "")
    if not m:
        raise ValueError(f"bad interval {s!r}")
    n, unit = int(m.group(1)), m.group(2)
    return max(1, {"s": n // 60, "m": n, "min": n, "h": n * 60, "d": n * 1440}[unit])


class Plant:
    """What the historian knows: points, AF elements and upset times, read once from the exports."""

    def __init__(self, data_dir: Path):
        data_dir = Path(data_dir)
        table = connectors.find(data_dir, "pi_points")
        self.points = {r["tag"]: r for r in table.rows()} if table else {}
        af = data_dir / "pi_af.json"
        self.elements = json.loads(af.read_text()) if af.is_file() else []
        self.grammar = bundle_grammar(data_dir)
        root = (self.elements[0]["path"].split("\\")[0] if self.elements else data_dir.name.upper())
        self.data_server, self.af_server, self.af_database = f"{root}-PI", f"{root}-AF", root
        self.upsets = self._upsets(data_dir)
        self.point_ids = {p: i for i, p in enumerate(self.points, 1)}
        self.series_ids = {p: str(uuid.uuid5(uuid.NAMESPACE_URL, f"adx:{p}")) for p in self.points}
        self.by_series = {s: p for p, s in self.series_ids.items()}
        self._signal = SyntheticProvider(data_dir)

    def _upsets(self, data_dir: Path) -> dict[tuple[str, str], list[datetime]]:
        out = defaultdict(set)
        for name, when, text in (("ec_downtime", "start", "comment"), ("oplogs", "timestamp", "text")):
            if (table := connectors.find(data_dir, name)) is None:
                continue
            for r in table.rows():
                body = str(r.get(text) or "")
                # the first tag named is the one in trouble ("20-P-1001B tripped ... Started 20-P-1001A")
                if r.get(when) and (name == "ec_downtime" or UPSET_WORDS.search(body)) and (lp := loops(body, self.grammar)):
                    out[lp[0]].add(_time(str(r[when])))
        return {k: sorted(v) for k, v in out.items()}

    def states(self, point: str, start: datetime, end: datetime) -> list[tuple[float, float, str]]:
        """Digital states stored instead of values (instrument faults, interface outages): [(from, to, state)]."""
        return self._signal.states(point, start, end)

    def recorded(self, point: str, start: datetime, end: datetime) -> list[dict]:
        """What the PI archive holds: the raw 5-minute values after swinging-door compression (deviation
        `compdev` from the point list, else 0.2 % of the point's level), and a digital state event where a
        state starts, the next good value ending it."""
        vals = self.values(point, start, end, RAW_STEP_MINUTES)
        states = [(a, b, x) for a, b, x in self.states(point, start, end)]
        if not vals and not states:
            return []
        level = float(np.median(np.abs([v for _, v in vals]))) if vals else 0.0
        dev = float(self.points[point].get("compdev") or 0) or max(0.002 * level, 0.001)
        secs = np.array([instruments.epoch(t) for t, _ in vals])
        v = np.array([x for _, x in vals])
        items = [(vals[i][0], {"Timestamp": _iso(vals[i][0]), "Value": vals[i][1], "Good": True})
                 for i in instruments.swinging_door(secs, v, dev)] if vals else []
        items += [(max(instruments.naive(a), start), _state(max(instruments.naive(a), start), x))
                  for a, b, x in states]
        return [i for _, i in sorted(items, key=lambda x: x[0])]

    def values(self, point: str, start: datetime, end: datetime, step_minutes: int) -> list[tuple[datetime, float]]:
        p = self.points[point]
        near = [u for lp in set(loops(point, self.grammar)) for u in self.upsets.get(lp, [])
                if start - timedelta(hours=12) <= u <= end + timedelta(hours=6)]
        return [(_time(t), v) for t, v in
                self._signal.values(point, p.get("engunits") or "", start, end, step_minutes, near,
                                    p.get("descriptor") or "")]


# PI's system digital states the fake historian stores (name: code in the System state set).
SYSTEM_STATES = {"I/O Timeout": 246, "Bad Input": 307, "Comm Fail": 313}


def _state(t: datetime, name: str) -> dict:
    return {"Timestamp": _iso(t), "Value": {"Name": name, "Value": SYSTEM_STATES.get(name, 307), "IsSystem": True},
            "Good": False, "Questionable": False, "Substituted": False}


def app(data_dir) -> FastAPI:
    plant = Plant(data_dir)
    api = FastAPI(title="Fake historian (PI Web API + ADX)")
    base = "/piwebapi"

    def point_obj(request: Request, name: str) -> dict:
        p = plant.points[name]
        path = f"\\\\{plant.data_server}\\{name}"
        wid = _web_id("P1", path)
        return {"WebId": wid, "Id": plant.point_ids[name], "Name": name, "Path": path,
                "Descriptor": p.get("descriptor") or "", "PointClass": "classic", "PointType": "Float32",
                "DigitalSetName": "", "EngineeringUnits": p.get("engunits") or "", "Step": False, "Future": False,
                "Links": {"Self": f"{request.base_url}piwebapi/points/{wid}",
                          "InterpolatedData": f"{request.base_url}piwebapi/streams/{wid}/interpolated"}}

    def page(items: list, start: int, count: int, request: Request | None = None) -> dict:
        """One page with PI Web API's pagination Links (First, Previous, Next, Last), as the real server sends."""
        links = {}
        if request is not None:
            at = lambda i: str(request.url.include_query_params(startIndex=max(i, 0), maxCount=count))
            last = max(len(items) - 1, 0) // max(count, 1) * count
            links = {"First": at(0), **({"Previous": at(start - count)} if start > 0 else {}),
                     **({"Next": at(start + count)} if start + count < len(items) else {}), "Last": at(last)}
        return {"Links": links, "Items": items[start:start + count]}

    def missing(what: str) -> JSONResponse:
        return JSONResponse({"Errors": [f"{what} not found."]}, status_code=404)

    @api.get(base)
    def home(request: Request):
        return {"Links": {"DataServers": f"{request.base_url}piwebapi/dataservers",
                          "AssetServers": f"{request.base_url}piwebapi/assetservers"}}

    @api.get(f"{base}/dataservers")
    def dataservers(request: Request):
        path = f"\\\\{plant.data_server}"
        wid = _web_id("F1", path)
        return {"Items": [{"WebId": wid, "Name": plant.data_server, "Path": path, "IsConnected": True,
                           "Links": {"Points": f"{request.base_url}piwebapi/dataservers/{wid}/points"}}]}

    @api.get(f"{base}/dataservers/{{web_id}}/points")
    def points(request: Request, web_id: str, nameFilter: str = "*", startIndex: int = 0, maxCount: int = 1000):
        pattern = re.compile(fnmatch_regex(nameFilter), re.IGNORECASE)
        names = [n for n in plant.points if pattern.fullmatch(n)]
        listed = page(names, startIndex, maxCount, request)
        return {**listed, "Items": [point_obj(request, n) for n in listed["Items"]]}

    @api.get(f"{base}/points")
    def point_by_path(request: Request, path: str):
        name = path.rsplit("\\", 1)[-1]
        return point_obj(request, name) if name in plant.points else missing(f"PI Point '{path}'")

    @api.get(f"{base}/points/{{web_id}}")
    def point(request: Request, web_id: str):
        name = _path_of(web_id).rsplit("\\", 1)[-1]
        return point_obj(request, name) if name in plant.points else missing("PI Point")

    @api.get(f"{base}/points/{{web_id}}/attributes")
    def point_attributes(web_id: str):
        name = _path_of(web_id).rsplit("\\", 1)[-1]
        if name not in plant.points:
            return missing("PI Point")
        p = plant.points[name]
        return {"Items": [{"Name": k, "Value": v} for k, v in
                          (("tag", name), ("descriptor", p.get("descriptor") or ""),
                           ("engunits", p.get("engunits") or ""), ("pointsource", p.get("pointsource") or ""),
                           ("pointtype", "Float32"))]}

    @api.get(f"{base}/assetservers")
    def assetservers(request: Request):
        path = f"\\\\{plant.af_server}"
        wid = _web_id("RS", path)
        return {"Items": [{"WebId": wid, "Name": plant.af_server, "Path": path, "IsConnected": True,
                           "Links": {"Databases": f"{request.base_url}piwebapi/assetservers/{wid}/assetdatabases"}}]}

    @api.get(f"{base}/assetservers/{{web_id}}/assetdatabases")
    def databases(request: Request, web_id: str):
        path = f"\\\\{plant.af_server}\\{plant.af_database}"
        wid = _web_id("RD", path)
        return {"Items": [{"WebId": wid, "Name": plant.af_database, "Path": path,
                           "Links": {"Elements": f"{request.base_url}piwebapi/assetdatabases/{wid}/elements"}}]}

    def element_obj(el: dict) -> dict:
        path = f"\\\\{plant.af_server}\\{plant.af_database}\\{el['path']}"
        return {"WebId": _web_id("E1", path), "Name": el["path"].split("\\")[-1], "Path": path,
                "TemplateName": el.get("template") or "", "HasChildren": any(
                    e["path"].startswith(el["path"] + "\\") for e in plant.elements)}

    @api.get(f"{base}/assetdatabases/{{web_id}}/elements")
    def elements(request: Request, web_id: str, searchFullHierarchy: bool = False, startIndex: int = 0,
                 maxCount: int = 1000):
        els = plant.elements if searchFullHierarchy else [e for e in plant.elements if "\\" not in e["path"]]
        return page([element_obj(e) for e in els], startIndex, maxCount, request)

    @api.get(f"{base}/elements/{{web_id}}/attributes")
    def element_attributes(web_id: str):
        path = _path_of(web_id).split("\\", 4)[-1]
        el = next((e for e in plant.elements if e["path"] == path), None)
        if el is None:
            return missing("AF Element")
        items = []
        for a in el.get("attributes") or []:
            apath = f"{_path_of(web_id)}|{a['name']}"
            items.append({"WebId": _web_id("Ab", apath), "Name": a["name"], "Path": apath,
                          "DataReferencePlugIn": "PI Point" if a.get("pi_point") else "",
                          "ConfigString": f"\\\\{plant.data_server}\\{a['pi_point']};ReadOnly=False"
                          if a.get("pi_point") else "",
                          "DefaultUnitsName": a.get("uom") or ""})
        return {"Items": items}

    def stream(web_id: str):
        name = _path_of(web_id).rsplit("\\", 1)[-1]
        return name if name in plant.points else None

    @api.get(f"{base}/streams/{{web_id}}/interpolated")
    def interpolated(web_id: str, startTime: str, endTime: str, interval: str = "1h", maxCount: int = 150000):
        if (name := stream(web_id)) is None:
            return missing("PI Point")
        if (m := re.fullmatch(r"\s*(\d+)\s*s\s*", interval or "")) and int(m.group(1)) % 60:
            # every few seconds (#358): what the process model records that fast, across a loop's step tests
            fast = plant._signal.high_rate({"point": name}, _time(startTime), _time(endTime), int(m.group(1)))
            return {"Items": [{"Timestamp": _iso(_time(t)), "Value": v, "Good": True} for t, v in fast[:maxCount]]}
        try:
            step = _interval_minutes(interval)
        except ValueError as e:
            return JSONResponse({"Errors": [str(e)]}, status_code=400)
        start, end = _time(startTime), _time(endTime)
        vals = dict(plant.values(name, start, end, step))
        states = plant.states(name, start, end)
        items, t = [], start
        while t <= end:
            if t in vals:
                items.append({"Timestamp": _iso(t), "Value": vals[t], "UnitsAbbreviation": plant.points[name].get("engunits"),
                              "Good": True, "Questionable": False, "Substituted": False})
            elif state := next((x for a, b, x in states if a <= instruments.epoch(t) < b), None):
                items.append(_state(t, state))
            t += timedelta(minutes=step)
        return {"Items": items}

    @api.get(f"{base}/streams/{{web_id}}/recorded")
    def recorded(web_id: str, startTime: str, endTime: str, maxCount: int = 1000):
        if (name := stream(web_id)) is None:
            return missing("PI Point")
        return {"Items": plant.recorded(name, _time(startTime), _time(endTime))[:maxCount]}

    @api.post(f"{base}/batch")
    async def batch(request: Request):
        """Independent GET requests only (no parent / child substitution)."""
        out = {}
        for key, req in (await request.json()).items():
            resource = str(req.get("Resource", ""))
            m = re.search(r"/piwebapi/(points|elements)/([^/?]+)/attributes", resource)
            if req.get("Method", "GET") != "GET" or not m:
                out[key] = {"Status": 400, "Content": {"Errors": [f"unsupported batch request {resource}"]}}
                continue
            res = point_attributes(m.group(2)) if m.group(1) == "points" else element_attributes(m.group(2))
            out[key] = ({"Status": res.status_code, "Content": json.loads(res.body)} if isinstance(res, JSONResponse)
                        else {"Status": 200, "Content": res})
        # Like PI Web API: a batch with any failed sub-request answers 207 Multi-Status (R.2a).
        return JSONResponse(out, status_code=207 if any(r["Status"] >= 400 for r in out.values()) else 200)

    @api.post("/v1/rest/query")
    async def adx_query(request: Request):
        body = await request.json()
        try:
            cols, rows = kql(plant, body.get("csl") or "")
        except KqlError as e:
            return JSONResponse({"error": {"code": "General_BadRequest", "message": "Request is invalid and cannot "
                                           "be executed.", "@message": str(e)}}, status_code=400)
        return v1_tables(cols, rows)

    return api


# The scalar type ADX names for each .NET DataType in a v1 response (Microsoft's REST reference, R.2b).
COLUMN_TYPE = {"String": "string", "DateTime": "datetime", "Double": "real", "Int64": "long", "Int32": "int",
               "Boolean": "bool", "Object": "dynamic", "TimeSpan": "timespan"}


def v1_tables(cols: list[tuple[str, str]], rows: list[list]) -> dict:
    """A v1 query response as ADX sends one: the result, @ExtendedProperties, QueryStatus and the TableOfContents
    that names them (R.2b; it was one table before)."""
    def table(name, columns, rows_):
        return {"TableName": name, "Columns": [{"ColumnName": c, "DataType": t, "ColumnType": COLUMN_TYPE[t]}
                                               for c, t in columns], "Rows": rows_}

    activity = str(uuid.uuid4())
    status = [[_iso(datetime.now(UTC).replace(tzinfo=None)), 4, "Info", 0, "Query completed successfully", 1, activity, activity, activity,
               "fakeplant.historian"]]
    return {"Tables": [
        table("Table_0", cols, rows),
        table("Table_1", [("Value", "String")], [['{"Visualization":null}']]),
        table("Table_2", [("Timestamp", "DateTime"), ("Severity", "Int32"), ("SeverityName", "String"),
                          ("StatusCode", "Int32"), ("StatusDescription", "String"), ("Count", "Int32"),
                          ("RequestId", "String"), ("ActivityId", "String"), ("SubActivityId", "String"),
                          ("ClientActivityId", "String")], status),
        table("Table_3", [("Ordinal", "Int64"), ("Kind", "String"), ("Name", "String"), ("Id", "String"),
                          ("PrettyName", "String")],
              [[0, "QueryResult", "PrimaryResult", activity, ""], [1, "QueryProperties", "@ExtendedProperties",
                                                                   activity, ""],
               [2, "QueryStatus", "QueryStatus", "00000000-0000-0000-0000-000000000000", ""]]),
    ]}


def fnmatch_regex(pattern: str) -> str:
    return "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern)


# --- a very small KQL --------------------------------------------------------------------------

class KqlError(ValueError):
    pass


CATALOG = [("SeriesId", "String"), ("PiPoint", "String"), ("Description", "String"), ("Units", "String"),
           ("ValuesTable", "String")]
VALUES = [("SeriesId", "String"), ("Timestamp", "DateTime"), ("Value", "Double")]
STR = r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\""


def _split(csl: str) -> list[str]:
    parts, buf, quote = [], "", None
    for c in csl:
        if quote:
            quote = None if c == quote else quote
        elif c in "'\"":
            quote = c
        elif c == "|":
            parts.append(buf.strip())
            buf = ""
            continue
        buf += c
    return [*parts, buf.strip()]


def _strings(s: str) -> list[str]:
    return [a or b for a, b in re.findall(STR, s)]


def _conditions(where: str) -> list[tuple[str, str, object]]:
    out = []
    for cond in re.split(r"\s+and\s+", where.strip()):
        if m := re.fullmatch(r"(\w+)\s*==\s*(?:" + STR + r")", cond.strip()):
            out.append((m.group(1), "in", {m.group(2) or m.group(3) or ""}))
        elif m := re.fullmatch(r"(\w+)\s+in\s*\((.*)\)", cond.strip()):
            out.append((m.group(1), "in", set(_strings(m.group(2)))))
        elif m := re.fullmatch(r"(\w+)\s+between\s*\(\s*datetime\(([^)]+)\)\s*\.\.\s*datetime\(([^)]+)\)\s*\)",
                               cond.strip()):
            out.append((m.group(1), "between", (_time(m.group(2).strip()), _time(m.group(3).strip()))))
        else:
            raise KqlError(f"unsupported condition: {cond}")
    return out


def _keep(row: dict, conds) -> bool:
    for col, op, arg in conds:
        v = row.get(col)
        if op == "in" and v not in arg:
            return False
        if op == "between" and not (arg[0] <= v <= arg[1]):
            return False
    return True


def kql(plant: Plant, csl: str) -> tuple[list[tuple[str, str]], list[list]]:
    """Run one query against the catalog or the values table."""
    stages = _split(csl)
    table, ops = stages[0], stages[1:]
    if m := re.fullmatch(r"print\s+(\w+)\s*=\s*(\d+)", table):  # the connection test's sign-in check
        return [(m.group(1), "Int64")], [[int(m.group(2))]]
    if table.startswith("."):
        raise KqlError("management commands are not sent to the query endpoint")
    wheres = [_conditions(op[5:]) for op in ops if op.startswith("where ")]
    conds = [c for w in wheres for c in w]
    if table == "SeriesCatalog":
        cols = CATALOG
        rows = [{"SeriesId": sid, "PiPoint": p, "Description": plant.points[p].get("descriptor") or "",
                 "Units": plant.points[p].get("engunits") or "", "ValuesTable": VALUES_TABLE}
                for p, sid in plant.series_ids.items()]
    elif table == VALUES_TABLE:
        cols = VALUES
        ids = next((a for c, op, a in conds if c == "SeriesId" and op == "in"), None)
        window = next((a for c, op, a in conds if c == "Timestamp" and op == "between"), None)
        if ids is None or window is None:
            raise KqlError("PiValues queries need SeriesId == / in and Timestamp between (...) filters")
        if (window[1] - window[0]) > timedelta(days=60):
            raise KqlError("time window over 60 days")
        rows = [{"SeriesId": sid, "Timestamp": t, "Value": v}
                for sid in sorted(ids) if sid in plant.by_series
                for t, v in plant.values(plant.by_series[sid], window[0], window[1], RAW_STEP_MINUTES)]
    else:
        raise KqlError(f"unknown table '{table}'")
    rows = [r for r in rows if _keep(r, conds)]
    for op in ops:
        if op.startswith("where "):
            continue
        if m := re.fullmatch(r"project\s+(.+)", op):
            names = [c.strip() for c in m.group(1).split(",")]
            types = dict(cols)
            if bad := [n for n in names if n not in types]:
                raise KqlError(f"unknown column(s) {bad}")
            cols, rows = [(n, types[n]) for n in names], [{n: r[n] for n in names} for r in rows]
        elif m := re.fullmatch(r"summarize\s+(.+?)\s+by\s+(.+)", op):
            cols, rows = _summarize(cols, rows, m.group(1), m.group(2))
        elif m := re.fullmatch(r"(?:order|sort)\s+by\s+(\w+)(?:\s+(asc|desc))?", op):
            rows.sort(key=lambda r: r[m.group(1)], reverse=(m.group(2) or "desc") == "desc")
        elif m := re.fullmatch(r"(?:take|limit)\s+(\d+)", op):
            rows = rows[:int(m.group(1))]
        else:
            raise KqlError(f"unsupported operator: {op}")
    out = [[(_iso(v) if isinstance(v, datetime) else v) for v in (r[c] for c, _ in cols)] for r in rows]
    return cols, out


def _commas(s: str) -> list[str]:
    """Split on commas outside parentheses: "avg(Value), bin(Timestamp, 30m)" -> two parts."""
    parts, depth, buf = [], 0, ""
    for c in s:
        depth += (c == "(") - (c == ")")
        if c == "," and depth == 0:
            parts.append(buf.strip())
            buf = ""
        else:
            buf += c
    return [*parts, buf.strip()]


def _summarize(cols, rows, aggs: str, by: str):
    groups_by = []
    for b in _commas(by):
        if m := re.fullmatch(r"bin\(\s*(\w+)\s*,\s*(\d+)(m|h|d)\s*\)", b):
            size = int(m.group(2)) * {"m": 1, "h": 60, "d": 1440}[m.group(3)]
            groups_by.append((m.group(1), size))
        elif b in dict(cols):
            groups_by.append((b, None))
        else:
            raise KqlError(f"unsupported group: {b}")
    specs = []
    for a in _commas(aggs):
        m = re.fullmatch(r"(?:(\w+)\s*=\s*)?(avg|min|max|count|sum)\((\w*)\)", a)
        if not m:
            raise KqlError(f"unsupported aggregation: {a}")
        specs.append((m.group(1) or f"{m.group(2)}_{m.group(3)}", m.group(2), m.group(3)))

    def key(r):
        out = []
        for col, size in groups_by:
            v = r[col]
            if size:
                epoch = datetime(1970, 1, 1)
                v = epoch + timedelta(minutes=(int((v - epoch).total_seconds() // 60) // size) * size)
            out.append(v)
        return tuple(out)

    groups = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)
    fns = {"avg": lambda xs: round(sum(xs) / len(xs), 3), "min": min, "max": max, "sum": sum, "count": len}
    types = dict(cols)
    out_cols = [(c, types[c]) for c, _ in groups_by] + [(n, "Int64" if f == "count" else "Double") for n, f, _ in specs]
    out_rows = []
    for k, rs in groups.items():
        row = dict(zip([c for c, _ in groups_by], k))
        for name, fn, col in specs:
            row[name] = len(rs) if fn == "count" else fns[fn]([r[col] for r in rs])
        out_rows.append(row)
    return out_cols, out_rows


def main(data_dir, host: str = "127.0.0.1", port: int = 8100) -> None:
    import uvicorn

    print(f"Fake historian for {data_dir}\n  PI Web API: http://{host}:{port}/piwebapi\n"
          f"  ADX:        http://{host}:{port}  (database: any name)\n")
    uvicorn.run(app(data_dir), host=host, port=port)
