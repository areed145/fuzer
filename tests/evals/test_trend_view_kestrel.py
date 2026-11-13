"""Trend views (P5.12a) on Kestrel, a gas plant built from its seed bundle: its compressors open on the same template
as Okan's, from measurement roles, and one with few points says what it lacks."""

from fuzer.analysis import trend_view


def _compressors(conn):
    """Kestrel's compressors with roles, the best instrumented first."""
    return [r["id"] for r in conn.execute(
        """
        select r.asset as id from measurement_role r
        where exists (select 1 from edge c join entity_label l on l.fuzer_id = c.dst
                      where c.src = r.asset and c.rel = 'classified_as' and l.label like 'ISO14224:CO%%')
        group by r.asset order by count(*) desc, r.asset
        """)]


def test_a_kestrel_compressor_opens_on_its_template_groups(kestrel_db):
    units = _compressors(kestrel_db)
    assert units
    v = trend_view.view(kestrel_db, units[0])
    assert v["template"]["key"] == "CO"
    groups = {g["key"] for g in v["groups"]}
    assert {"pressures", "temperatures", "vibration"} <= groups
    assert all(t["values"] for g in v["groups"] for t in g["traces"])
    assert not v["missing"], v["missing"]


def test_a_compressor_with_few_points_names_what_it_lacks(kestrel_db):
    sparse = [u for u in _compressors(kestrel_db) if trend_view.view(kestrel_db, u)["missing"]]
    for u in sparse:
        v = trend_view.view(kestrel_db, u)
        lacking = {m["role"] for m in v["missing"]}
        have = {t["role"] for g in v["groups"] for t in g["traces"]}
        assert not lacking & have
