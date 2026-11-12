"""Across assets (P7.5b): the plant scorecard (analysis.kpis, P7.3a) side by side for every project a person may open,
so an asset manager compares fields on the same KPIs, each read in its own register for its own latest period.

It reads only the projects the person is granted (D6, docs/ot-security.md): a fleet view is a union of grants, never
a bypass. The projects it leaves out are named, so a gap is never mistaken for a field with nothing to show.

    scorecards(allowed, days)   {projects: [{project, scorecard | error}], left_out: [names], kpis: [labels]}
"""

from ..analysis import kpis


def scorecards(allowed: list[str] | None, days: int = 30) -> dict:
    """Each project in `allowed` (None: every project, for an owner or an instance without sign-in), its scorecard
    for the `days` to its own latest daily report; and the stored projects left out."""
    from ..core import db, projects

    with db.connect() as conn:
        names = projects.stored(conn)
    shown = [n for n in names if allowed is None or n in allowed]
    out = []
    for name in shown:
        with db.connect() as conn, projects.use(conn, name), db.connect() as scoped:
            try:
                out.append({"project": name, "scorecard": kpis.scorecard(scoped, None, days)})
            except Exception as e:  # one field's register failing never hides the others
                out.append({"project": name, "error": str(e).splitlines()[0][:200]})
    return {"days": days, "projects": out, "left_out": [n for n in names if n not in shown],
            "kpis": [{"key": k[0], "label": k[1], "unit": k[2], "better": k[3]} for k in kpis.KPIS],
            "note": "Each field is read in its own register for the days to its own latest daily report. Only the "
                    "projects you are granted are shown; the ones left out are named."}
