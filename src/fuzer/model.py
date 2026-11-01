"""In-memory shapes passed between pipeline steps."""

from dataclasses import dataclass, field

# An edge endpoint is either ("ref", "<source>:<record_type>:<key>") for a
# specific source record, or (kind, canonical_key), resolved after matching.
Endpoint = tuple[str, str | None]


@dataclass
class Edge:
    src: Endpoint
    rel: str
    dst: Endpoint
    source: str
    confidence: float = 1.0
    valid_from: str | None = None
    valid_to: str | None = None
    props: dict | None = None


@dataclass
class Candidate:
    """What one source record says about one real-world thing."""

    source: str
    record_type: str
    source_key: str
    kind: str
    key: str | None  # canonical match key; None when it can't be read
    fields: dict = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    block: str | None = None  # extra key for fuzzy matching (e.g. installed tag)

    @property
    def ref(self) -> str:
        return f"{self.source}:{self.record_type}:{self.source_key}"


@dataclass
class CurvePoint:
    """One point of a reference curve for an equipment model (a pump's head at a flow, a choke's Cv at an
    opening), or a rating figure (series "bep", "cv_max", ...) with x and, for pairs, y."""

    record: str
    of: Endpoint
    curve: str  # pump | choke
    series: str
    x: float
    y: float | None
    x_unit: str | None = None
    y_unit: str | None = None


@dataclass
class Measurement:
    """One reading of one quantity on one thing: a well test's oil rate, a lab result at a sample point,
    a wall thickness at a CML. Landed and kept, unlike historian values."""

    record: str  # the source record it came from (<source>:<record_type>:<key>)
    of: list[Endpoint]  # what was measured: the first of these that resolves
    quantity: str
    at: str
    value: float
    unit: str | None
    qualifier: str | None = None  # "<" / ">" for results beyond a detection limit
    reported: str | None = None  # the value and unit as the source gave them, when converted
    method: str | None = None
    location: str | None = None  # where on the thing (a CML, a sample point)
