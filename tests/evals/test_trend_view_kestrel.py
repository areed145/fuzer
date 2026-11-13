"""Trend views (P5.12a) on Kestrel, a gas plant built from its seed bundle: its compressors open on the same template
as Okan's, from measurement roles."""

from fuzer.analysis import trend_view


def test_a_kestrel_compressor_opens_on_its_template_groups(kestrel_db):
    units = [r["id"] for r in kestrel_db.execute(
        """
        select distinct c.src as id from edge c join entity_label l on l.fuzer_id = c.dst
        where c.rel = 'classified_as' and l.label like 'ISO14224:CO%%'
          and exists (select 1 from measurement_role r where r.asset = c.src) order by 1
        """)]
    assert units
    v = trend_view.view(kestrel_db, units[0])
    assert v["template"]["key"] == "CO"
    groups = {g["key"] for g in v["groups"]}
    assert {"pressures", "temperatures", "vibration"} <= groups
    assert all(t["values"] for g in v["groups"] for t in g["traces"])
    assert not v["missing"], v["missing"]
