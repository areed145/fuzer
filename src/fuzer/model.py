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
