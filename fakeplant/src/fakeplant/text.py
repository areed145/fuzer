"""Messy maintenance and log text over a fake plant's clean records, with the answer key kept (#890).

Applied last, with its own random sequence, so every other record and answer key stays as it was:

- **Authors write differently.** Each log author has a style: full sentences, terse shorthand ("press",
  "chkd", "comp"), or terse in capitals, on the entries that name nothing a reader must link (the rest
  keep their words, so the log answer key, searches and the records checks hold).
- **The same fault raised twice.** A few background notifications get a second one from another shift a
  day or two later, on the same item, worded the second author's way.
- **Filed on the wrong place.** A few are filed on the area instead of the item (no equipment, the
  area's floc), or on its twin (2200A for 2200B), with the real item named only in the text.
- **Closed without saying what was done.** Closed orders carry a confirmation text; some are blank or
  just "done".

Only background maintenance is touched, never a story's notification or order. The answer key
(`answer_keys/records_text.json`) lists every duplicate, misfiled notification and order closed without
a confirmation, with the truth.
"""

import random
import re

STYLES = {"GGCP CRO": "terse", "PP CRO": "full", "OIM": "full", "Production Engineer": "full",
          "Night Supervisor": "terse", "Field Operator": "caps"}
SHORT = [("pressure", "press"), ("temperature", "temp"), ("compressor", "comp"), ("checked", "chkd"),
         ("normal", "nml"), ("maintenance", "mtce"), ("adjusted", "adj"), ("steady", "stdy"),
         ("separator", "sep"), ("planned", "plnd"), ("reading", "rdg"), ("approximately", "approx"),
         ("injection", "inj"), ("export", "exp"), ("with", "w/"), ("and", "&")]
TRIVIAL = ["done", "Done.", "ok", "complete", "as per WO", "job done"]
CONFIRM = ["{t}. Completed and tested, equipment returned to service.", "{t} completed. No further issues found.",
           "{t}: job complete, area left clean and handed back to operations.",
           "Completed {t_lower}. Checked on restart, running normally."]
DUPLICATE = {"terse": "{d} again - {tag}", "caps": "{TAG} {D}", "full": "{d}, raised again by night shift"}


def terse(text: str) -> str:
    for long, short in SHORT:
        text = re.sub(rf"\b{long}\b", short, text, flags=re.IGNORECASE)
    return re.sub(r"\.$", "", text)


def styled(text: str, style: str) -> str:
    if style == "full":
        return text
    t = terse(text)
    return t.upper() if style == "caps" else t


def _short_tag(floc: str) -> str:
    """How an operator types an item: OKAN-20-K2200B -> K2200B."""
    return floc.rsplit("-", 1)[-1]


def apply(notifications: list[dict], orders: list[dict], logs: list[dict], log_truth: list[dict], background: set,
          equipment: list[dict], seed: int) -> dict:
    """Mess up the records in place (only background notifications and orders, by qmnum / aufnr in
    `background`), and return the answer key."""
    rng = random.Random(seed)
    truth = {"styles": STYLES, "duplicates": [], "misfiled": [], "no_confirmation": []}

    # Authors' styles, on the entries that name nothing a reader must link.
    mentions = {t["entry_id"]: t["mentions"] for t in log_truth}
    for e in logs:
        if not mentions.get(e["entry_id"]):  # entries naming things keep their words: readers and searches key on them
            e["text"] = styled(e["text"], STYLES.get(e["author"], "full"))

    by_floc = {e["floc"]: e for e in equipment}
    twins = {}
    for floc in by_floc:
        if floc[-1] in "ABC":
            for x in "ABC":
                if x != floc[-1] and floc[:-1] + x in by_floc:
                    twins.setdefault(floc, floc[:-1] + x)
    pool = [n for n in notifications if n["qmnum"] in background]
    picked = rng.sample(pool, k=max(2, len(pool) * 12 // 100))
    dupes, misfiled = picked[: len(picked) // 2], picked[len(picked) // 2:]

    nxt = max(int(n["qmnum"]) for n in notifications) + 1
    for n in dupes:
        style = rng.choice(["terse", "caps", "full"])
        d = n["short_text"][0].lower() + n["short_text"][1:]
        text = DUPLICATE[style].format(d=d, D=d.upper(), tag=_short_tag(n["floc"]), TAG=_short_tag(n["floc"]))
        day = int(n["created"][8:10]) + rng.randint(0, 2)
        dup = {**n, "qmnum": str(nxt), "created": f"{n['created'][:8]}{min(day, 28):02d}", "short_text": text}
        notifications.append(dup)
        truth["duplicates"].append({"qmnum": dup["qmnum"], "duplicate_of": n["qmnum"], "equnr": n["equnr"]})
        nxt += 1
    for n in misfiled:
        true = {"true_floc": n["floc"], "true_equnr": n["equnr"]}
        if n["floc"] in twins and rng.random() < 0.6:
            twin = by_floc[twins[n["floc"]]]
            n["short_text"] = f"{n['short_text']} ({_short_tag(n['floc'])})"
            n["floc"], n["equnr"], kind = twin["floc"], twin["equnr"], "twin"
        else:
            n["floc"], n["equnr"], kind = n["floc"].rsplit("-", 1)[0], "", "area"
        truth["misfiled"].append({"qmnum": n["qmnum"], "kind": kind, "filed_on": n["floc"], **true})

    for o in orders:
        if o["status"] != "TECO":
            o["confirmation"] = ""
            continue
        r = rng.random()
        if o["aufnr"] in background and r < 0.12:
            o["confirmation"] = ""
        elif o["aufnr"] in background and r < 0.2:
            o["confirmation"] = rng.choice(TRIVIAL)
        else:
            o["confirmation"] = rng.choice(CONFIRM).format(t=o["text"], t_lower=o["text"][0].lower() + o["text"][1:])
            continue
        truth["no_confirmation"].append({"aufnr": o["aufnr"], "confirmation": o["confirmation"]})
    return truth
