"""The message reader scored on a bundle's recorded messages (P2.8, #1254): `fuzer eval --messages`.

The bundle's messages export (`messages.csv`) is read against the project's register with the reader production uses
(`ops.messages.read`, no model, nothing written), and each reading is compared with `answer_keys/messages.json`: a case
about the entity a key names, a question for the data steward, or nothing to do.

    precision      of the cases it would make, the share about the right entity (the first it names)
    recall         of the messages that are a case, the share it makes about the right entity
    to_steward     of the messages a person must place, the share it sends to the steward (none made a wrong case)
    noise_handled  of the messages that ask for nothing, the share it opens nothing for

A run passes with precision at least `BAR`: a wrong case sends someone to the wrong asset, a question costs the
steward a minute.
"""

import json
from pathlib import Path

from ..core import settings
from ..core.exceptions import match
from ..ops import messages

BAR = 0.9


def run(conn, pack, bundle: str, data_dir) -> dict:
    path = Path(data_dir) / "answer_keys" / "messages.json"
    if not path.is_file():
        raise ValueError(f"no answer key at {path}")
    answers = json.loads(path.read_text())
    rows = conn.execute("select source_key, payload from source_record where source = %s and record_type = 'message' "
                        "order by source_key", (messages.SOURCE,)).fetchall()
    if not rows:
        raise ValueError("no messages loaded: ingest the messages export first")
    reader, register = messages.reader(conn, pack)
    least = float(settings.get(conn, "messages.min_confidence"))
    got = {r["source_key"]: messages.read(r["payload"].get("text") or "", reader, register, r["payload"].get("subject"),
                                          least) for r in rows if r["source_key"] in answers}

    def same(a: str, b: str) -> bool:
        return match(conn, [a])[0] == match(conn, [b])[0] != []

    cases = [k for k, r in got.items() if r.outcome == "case"]
    right = [k for k in cases if answers[k][0] == "case" and same(got[k].refs[0], answers[k][1])]
    wanted = [k for k, a in answers.items() if a[0] == "case" and k in got]
    steward = [k for k, a in answers.items() if a[0] == "steward" and k in got]
    noise = [k for k, a in answers.items() if a[0] == "handled" and k in got]
    wrong = [f"{k}: {got[k].outcome}{' ' + got[k].refs[0] if got[k].refs else ''}, not "
             f"{' '.join(answers[k])}" for k in sorted(got)
             if (got[k].outcome, *got[k].refs[:1]) != tuple(answers[k]) and k not in right]
    precision = round(len(right) / len(cases), 3) if cases else None
    return {"bundle": bundle, "messages": len(got), "cases": len(cases), "right": len(right), "precision": precision,
            "recall": round(len(right) / len(wanted), 3) if wanted else None,
            "to_steward": round(sum(got[k].outcome == "steward" for k in steward) / len(steward), 3) if steward else None,
            "noise_handled": round(sum(got[k].outcome == "handled" for k in noise) / len(noise), 3) if noise else None,
            "min_confidence": least, "wrong": wrong, "passes": precision is not None and precision >= BAR}
