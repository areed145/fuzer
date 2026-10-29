"""Extract -> resolve -> golden -> graph. Safe to rerun at any time."""

from . import golden, graph
from .extract import extract
from .pack import Pack
from .resolve import resolve


def rebuild(conn, pack: Pack) -> dict:
    cands, extra = [], []
    for rec in conn.execute("select source, record_type, source_key, payload from source_record"):
        c, e = extract(rec, pack)
        cands += c
        extra += e

    assigned, key_index, stats = resolve(conn, cands, pack)
    stats |= golden.build(conn, cands, assigned, pack)
    stats |= graph.build(conn, cands, extra, assigned, key_index)
    conn.commit()
    return stats
