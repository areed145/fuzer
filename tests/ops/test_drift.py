"""P3.11a (#1020): the slow-drift detector. A point that moved on its own against a long baseline and against its
peers (the same measurement role on the same class of equipment) is raised as a drift, kept a day at a time so the
months it reads cost one day's read each day. On Okan, oil export pump 10-P-1001A losing head through 2025 and
OK-NWP-G3's choke plugging with scale in August; the watch reads what it raised as evidence."""

from datetime import datetime, timedelta

import numpy as np
import pytest

from fuzer.analysis import clusters


def _days(n: int, end: str = "2025-08-20") -> np.ndarray:
    last = np.datetime64(end, "D")
    return np.arange(last - np.timedelta64(n, "D"), last + np.timedelta64(1, "D")).astype("datetime64[m]")


def _pts(*names):
    return [{"id": n, "point": n} for n in names]


@pytest.mark.unit
def test_a_pump_losing_head_is_the_drift_not_its_steady_peer_and_a_standing_spare_is_not_judged():
    days = 240
    t = _days(days)
    n = len(t)
    a = 40 * (1 - 0.08 * np.linspace(0, 1, n))  # wear rings opening: 8% down over the stretch
    b = np.full(n, 40.0) + 0.05 * np.sin(np.arange(n))
    spare = np.full(n, 3.0)  # stands at suction pressure: idle
    spare[-5:] = 40.0
    X = np.column_stack([a, b, spare])
    peers = {i: ("discharge_pressure", "PU") for i in ("A", "B", "C")}
    found = clusters.drifted(_pts("A", "B", "C"), t, X, datetime(2025, 8, 20), days, 1, 0.05, 4.0, None, peers,
                             idle=0.5, min_peers=1, progressive=True)
    assert [d["point"] for d in found] == ["A"] and found[0]["change"] < -0.05 and found[0]["peers"] == 1


@pytest.mark.unit
def test_a_point_back_to_normal_after_an_upset_in_its_baseline_has_not_drifted():
    days = 240
    t = _days(days)
    n = len(t)
    v = np.full(n, 60.0)
    v[: n // 4] = 80.0  # a valve failed open early in the stretch and was fixed: the baseline holds the upset
    others = [np.full(n, 60.0) + 0.1 * np.cos(np.arange(n) + k) for k in range(4)]
    X = np.column_stack([v, *others])
    names = ["W", "P1", "P2", "P3", "P4"]
    peers = {i: ("casing_pressure", "well") for i in names}
    assert not clusters.drifted(_pts(*names), t, X, datetime(2025, 8, 20), days, 1, 0.05, 4.0, None, peers,
                                idle=0.5, min_peers=1, progressive=True)
    # the same change made slowly, still going: a drift
    X[:, 0] = 60 * (1 + 0.2 * np.linspace(0, 1, n))
    found = clusters.drifted(_pts(*names), t, X, datetime(2025, 8, 20), days, 1, 0.05, 4.0, None, peers,
                             idle=0.5, min_peers=1, progressive=True)
    assert [d["point"] for d in found] == ["W"]


@pytest.mark.unit
def test_a_choke_moved_by_hand_is_seen_but_a_closed_choke_on_a_shut_in_is_not_a_move():
    from fuzer.ops import drift

    n = 240
    moved = np.r_[np.full(n // 2, 41.5), np.full(n - n // 2, 35.0)]
    shut = np.full(n, 45.0)
    shut[100:104] = 0.0
    X = np.column_stack([moved, shut])
    assert drift.moved_by_hand(X, ["J12", "G3"]) == {"J12"}


@pytest.mark.integration
def test_okans_slow_drifts_are_raised_kept_a_day_at_a_time_and_read_by_the_watch(okan_db):
    from fuzer.core import detectors
    from fuzer.core.pack import load_pack
    from fuzer.ops import drift, watch

    conn, pack = okan_db.conn, load_pack("okan")
    out = detectors.run(conn, pack, "peer_drifts", since="2025-08-20T00:00", until="2025-08-20T06:00")
    assert out["days"] == 1 and out["new"] >= 2, repr(out)
    rows = {r["point"]: r for r in conn.execute(
        """select coalesce(l.label, d.point_id) as point, d.key, d.unit_id, d.start, d."end", d.evidence
           from detection d left join entity_label l on l.fuzer_id = d.point_id where d.detector = 'peer_drifts'""")}
    g3 = rows["OK_NWP_G3.QGAS"]
    assert g3["key"] == "peer_drift_down" and g3["evidence"]["role"] == "flow" and g3["evidence"]["class"] == "well"
    assert g3["evidence"]["change"] < -0.1 and abs(g3["evidence"]["peers_change"]) < 0.02
    pump = rows["OKA_PP.10PT1001A.PV"]  # its discharge pressure, against the other export pumps'
    assert pump["key"] == "peer_drift_down" and pump["evidence"]["role"] == "discharge_pressure"
    assert pump["evidence"]["class"] == "PU" and pump["start"] == datetime(2025, 8, 19)
    assert not any(p.startswith("OK_J12_02") for p in rows)  # its choke trimmed by hand month by month
    assert not any(p.endswith(".WHT") for p in rows)  # wellhead temperatures follow the weather and the rate
    kept = conn.execute("select count(distinct day) as n from point_daily").fetchone()["n"]
    assert kept >= 240

    # the next day reads one more day of levels, and a drift found again carries on its detection
    before = conn.execute("select count(*) as n from detection where detector = 'peer_drifts'").fetchone()["n"]
    t0 = datetime.now()
    nxt = drift.peer_drifts(conn, pack, "2025-08-21T00:00", "2025-08-21T06:00")
    assert nxt["extended"] >= 1 and (datetime.now() - t0) < timedelta(seconds=60)
    after = conn.execute("select count(*) as n from detection where detector = 'peer_drifts'").fetchone()["n"]
    assert after == before + nxt["new"]
    assert conn.execute("select count(distinct day) as n from point_daily").fetchone()["n"] == kept + 1

    # the watch reads the drift as evidence, with its baseline and its peers, in the area it is in
    nwp = next(a for a in watch.areas(conn) if a["label"] == "OKAN-50")
    ev = watch.evidence(conn, nwp, datetime(2025, 8, 20), datetime(2025, 8, 22))
    seen = next(d for d in ev["detections"] if d["detector"] == "peer_drifts" and d["point"] == "OK_NWP_G3.QGAS")
    assert seen["drift"]["change"] < -0.1 and seen["drift"]["role"] == "flow"
    assert "OK_NWP_G3.QGAS" not in {d["name"] for d in watch._undetected(conn, ev)}  # raised: not the watch's to find
