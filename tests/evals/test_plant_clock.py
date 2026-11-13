"""The plant's clock (#882): a time without a zone is the plant's wall clock, in Python (core/clock) and in Postgres
(every connection runs in its project's timezone), so `today 07:00` at Kestrel is 07:00 in Denver whatever the
server's clock says, and a second project in the same database keeps its own zone."""

from datetime import UTC, datetime
from pathlib import Path

from fuzer.core import bundles, clock, db, projects

BUNDLES = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles"


def test_connections_and_naive_times_run_on_each_plants_clock(kestrel_db):
    conn = kestrel_db
    bundles.init(conn, BUNDLES / "harrier", by="test")
    for project, zone in (("kestrel", "America/Denver"), ("harrier", "America/Edmonton")):
        with projects.use(conn, project), db.connect() as there:
            assert clock.name() == zone
            assert there.execute("select current_setting('TimeZone') as z").fetchone()["z"] == zone
            if project == "harrier":
                continue  # Postgres's bundled tz database predates Alberta's 2026 change; Python's is newer (#882)
            # a naive time and a date cast in the database read the same as clock.aware does in Python
            row = there.execute("select '2026-11-12 07:00'::timestamptz as t, '2026-11-12'::date::timestamptz as d"
                                ).fetchone()
            assert row["t"] == clock.aware(datetime(2026, 11, 12, 7)) == datetime(2026, 11, 12, 14, tzinfo=UTC)
            assert row["d"] == datetime(2026, 11, 12, 7, tzinfo=UTC)
            got = there.execute("select now() as n").fetchone()["n"]
            assert abs((got.astimezone(clock.zone()).replace(tzinfo=None) - clock.now()).total_seconds()) < 60
