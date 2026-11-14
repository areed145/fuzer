"""Read queries shared by the REST API and the MCP server, by domain (#135). Every name is here as before."""

from .documents import (  # noqa: F401
    documents_with_several_current_revisions,
    drawing,
    drawings_for,
)
from .downtime import (  # noqa: F401
    _DOWNTIME,
    downtime,
    downtime_ranking,
    downtime_reason_checks,
    incident_timeline,
    incidents,
    similar_incidents,
)
from .maintenance import (  # noqa: F401
    events,
    maintenance,
    spares,
)
from .network import (  # noqa: F401
    _EDGE_DATES,
    _subgraph,
    compare_networks,
    members,
    neighborhood,
    network,
    network_rels,
)
from .records import (  # noqa: F401
    SEARCH_FIELDS,
    AmbiguousRef,
    conversations,
    entity,
    link_text,
    lookup_source,
    names,
    ontology,
    record_history,
    resolve_ref,
    reviews,
    search,
    source_values,
    stats,
)
from .status import profile, status  # noqa: F401
