"""The opportunity table on a database built before US oilfield units (#1159). Its own module: it resets the
database, which a module sharing the Okan plant must not."""


def test_a_database_from_before_us_units_keeps_its_decided_opportunities():
    """The gain column was gain_sm3d before #1159; without its move every scan on such a database failed on
    'gain_bbld', and with it the deploy's data refresh and the 5-minute detectors."""
    from fuzer.core import db as core_db

    with core_db.connect() as conn:
        core_db.reset(conn)
        core_db.init_schema(conn)
        conn.execute("alter table opportunity rename column gain_bbld to gain_sm3d")
        conn.execute("insert into opportunity (key, kind, title, gain_sm3d, detail, fingerprint, status) "
                     "values ('k', 'gas_lift', 't', 10, '{}', 'f', 'rejected')")
        conn.execute("update schema_version set hash = 'before'")
        conn.commit()
        core_db.init_schema(conn)
        row = conn.execute("select * from opportunity").fetchone()
        assert row["gain_bbld"] == 62.9 and "gain_sm3d" not in row and row["status"] == "rejected"
