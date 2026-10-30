"""Fake documents for the knowledge features: OEM manuals per equipment model, site troubleshooting
guides per process area, and procedures per equipment class, as Markdown and text-layer PDFs, with
a manifest shaped like an EDMS export (document number, revision, status, effective date, and the
classification / tag references an EDMS or CFIHOS handover carries). Some documents have a
superseded revision.
"""

import json
import re
from pathlib import Path

TROUBLESHOOTING = {
    "P": [("High vibration", "Check coupling alignment and bearing condition; look for cavitation at low suction "
                             "pressure; check the impeller for damage or fouling."),
          ("Seal leakage", "Check seal flush flow and pressure; inspect the seal faces and secondary seals; "
                           "confirm the pump is not running dry."),
          ("Fails to start", "Check the motor starter and interlocks; confirm suction valve open and casing primed."),
          ("Low discharge pressure", "Check for worn wear rings or impeller; confirm suction strainer is clean.")],
    "K": [("High discharge temperature", "Check the after-cooler and cooling medium flow; inspect valves for leakage; "
                                         "check for recycle valve passing."),
          ("High vibration", "Check alignment, bearing temperatures and the anti-surge controller."),
          ("Trips on start", "Check lube oil pressure permissive and seal gas supply.")],
    "E": [("High outlet temperature", "Check cooling or heating medium flow and fouling; check bypass valve position."),
          ("Internal leak", "Compare process and utility side pressures; plan a pressure test of the tube bundle.")],
    "GT": [("Fails to start", "Check fuel gas pressure, igniters and the starter motor; review the start sequence log."),
           ("High exhaust temperature", "Check exhaust thermocouple spread, inlet filter differential pressure and "
                                        "compressor washing interval."),
           ("High vibration", "Check bearing temperatures, alignment to the driven compressor and the anti-surge "
                              "controller.")],
    "V": [("Level indication faulty", "Compare level transmitters with the gauge glass; check impulse lines and "
                                      "displacer calibration."),
          ("Internal leak", "Check weir and internals at the next entry; review carry-over in downstream samples.")],
}
# Troubleshooting symptom -> ISO 14224 failure mode (answer key for section tagging).
SYMPTOM_MODES = {"High vibration": "VIB", "Seal leakage": "ELP", "Fails to start": "FTS",
                 "Low discharge pressure": "LOO", "High discharge temperature": "OHE", "Trips on start": "FTS",
                 "High outlet temperature": "OHE", "Internal leak": "INL", "Level indication faulty": "AIR",
                 "High exhaust temperature": "OHE"}
TYPE_NAMES = {"P": "centrifugal pump", "K": "centrifugal compressor", "E": "shell and tube heat exchanger",
              "V": "pressure vessel", "GT": "gas turbine"}
CLASS_CODES = {"P": "PU.CE", "K": "CO.CE", "E": "HE.ST", "V": "VE", "GT": "GT"}


def write_pdf(path: Path, pages: list[list[str]]) -> None:
    """A minimal PDF with a real text layer (Helvetica, one column)."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None,
               "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    kids = []
    for lines in pages:
        body = "BT /F1 10 Tf 14 TL 50 790 Td " + " ".join(f"({esc(line)}) Tj T*" for line in lines) + " ET"
        objects.append(f"<< /Length {len(body.encode('latin-1'))} >>\nstream\n{body}\nendstream")
        content = len(objects)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {content} 0 R "
                       f"/Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(len(objects))
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{obj}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(out)


def _write(out: Path, name: str, title: str, sections: list[tuple[str, list[str]]], pdf: bool) -> str:
    """Markdown, or a PDF with one page per section and numbered headings."""
    if pdf:
        pages = [[title, ""] if i == 0 else [] for i in range(len(sections))]
        for i, (heading, paras) in enumerate(sections):
            pages[i] += [f"{i + 1} {heading}", ""] + [line for p in paras for line in _wrap(p)] + [""]
        write_pdf(out / f"{name}.pdf", pages)
        return f"documents/{name}.pdf"
    md = [f"# {title}", ""]
    for heading, paras in sections:
        md += [f"## {heading}", ""] + [p + "\n" for p in paras]
    (out / f"{name}.md").write_text("\n".join(md))
    return f"documents/{name}.md"


def _wrap(text: str, width: int = 95) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return lines + ([line] if line else [])


def generate(out: Path, equipment: list[dict], areas: dict[str, tuple[str, str]], facility: str,
             guides: dict | None = None) -> list[dict]:
    """Write documents under out/documents and return the manifest rows. `guides` (guide id -> (title,
    tags, steps)) replaces the offshore demo's site troubleshooting guides; a guide id starts with
    its area code."""
    docs_dir = out / "documents"
    docs_dir.mkdir(parents=True, exist_ok=True)
    manifest = []

    # OEM manuals: one per equipment model in the fleet.
    models: dict[tuple[str, str], str] = {}
    for e in equipment:
        func = re.match(r"[A-Z]+-\d{2}-([A-Z]+)", e["floc"]).group(1)  # OSB-20-P1001B -> P
        models.setdefault((e["manufacturer"], e["model"]), func)
    for n, ((maker, model), func) in enumerate(sorted(models.items())):
        number = f"OEM-{model}"
        kind = TYPE_NAMES[func]
        revisions = ["A", "B"] if n < 3 else ["A"]
        for r, rev in enumerate(revisions):
            current = r == len(revisions) - 1
            troubleshooting = [f"{symptom}: {action}" for symptom, action in TROUBLESHOOTING[func]]
            if rev == "B":
                troubleshooting.append("Revision B adds: record vibration spectra before and after any bearing change.")
            sections = [
                ("Safety", [f"Isolate and depressurise the {kind} before any work. Follow site permit to work."]),
                ("Description", [f"The {maker} {model} is a {kind} for offshore process duty."]),
                ("Installation", ["Check foundation levelness and alignment before first start."]),
                ("Operation", [f"Start the {kind} only with suction or inlet valves open and permissives healthy."]),
                ("Troubleshooting", troubleshooting),
                ("Maintenance", ["Inspect every 4,000 running hours; overhaul every 24,000 hours."]),
                ("Spare parts", [f"Seal kit {model}-SK1, bearing set {model}-BS2, gasket set {model}-GS3."]),
            ]
            file = _write(docs_dir, f"{number}_rev{rev}", f"{maker} {model} {kind} - operation and maintenance manual",
                          sections, pdf=n % 2 == 1)
            manifest.append({"doc_number": number, "revision": rev, "doc_type": "oem_manual",
                             "title": f"{maker} {model} {kind} manual", "status": "current" if current else "superseded",
                             "effective_date": f"{2018 + r * 4}-01-15", "file": file, "manufacturer": maker,
                             "model": model, **({"supersedes": revisions[r - 1]} if r else {})})

    # Site troubleshooting guides: one per template area, naming its tags.
    own_guides = guides is not None
    guides = guides or {
        "20": ("Separation train troubleshooting", ["20-V-2001", "20-LT-2001", "20-PT-2001", "20-V-2002", "20-LT-2002"],
               ["If 20-LT-2001 disagrees with the gauge glass, switch control to the healthy transmitter and raise a "
                "work order.", "High pressure on 20-V-2001: check the gas outlet valve and the flare header."]),
        "23": ("Gas compression trips", ["23-K-1001A", "23-K-1001B", "23-TT-1001A"],
               ["After a high discharge temperature trip of 23-K-1001A, check the cooler 23-E-3001A before restart.",
                "Swap duty to 23-K-1001B if the trip repeats within 24 hours."]),
        "27": ("Oil export pump problems", ["27-P-1001A", "27-P-1001B", "27-P-1001C", "27-FT-3001"],
               ["High vibration on any export pump: start the standby pump, then check alignment.",
                "Fiscal meter 27-FT-3001 deviations must be reported to the allocation engineer."]),
        "44": ("Water injection pump trips", ["44-P-1001A", "44-P-1001B", "44-V-2001"],
               ["Seal leaks on 44-P-1001B are usually seal flush blockage: flush the cyclone separator.",
                "Low level in deaerator 44-V-2001 trips the pumps: check the level control valve."]),
    }
    for area, (title, tags, steps) in guides.items():
        if area not in areas and not own_guides:
            continue
        number = f"TG-{area}-001"
        file = _write(docs_dir, number, f"{facility} area {area[:2]}: {title}",
                      [("Purpose", [f"Troubleshooting guide for area {area[:2]} ({areas.get(area[:2], ('', title))[1]})."]),
                       ("Symptoms and actions", steps), ("Escalation", ["Call the duty engineer if unresolved."])],
                      pdf=False)
        manifest.append({"doc_number": number, "revision": "A", "doc_type": "troubleshooting_guide", "title": title,
                         "status": "current", "effective_date": "2022-06-01", "file": file, "tags": tags})

    # Procedures per equipment class.
    for func, steps in {"P": ["Start the standby pump and confirm discharge pressure.", "Stop the duty pump.",
                              "Isolate the stopped pump if maintenance is planned."],
                        "K": ["Confirm the trip cause is cleared.", "Reset the trip and check lube oil and seal gas.",
                              "Restart on recycle, then load slowly."],
                        "V": ["Isolate inlet and outlet.", "Drain to the closed drain system.",
                              "Purge with nitrogen before opening."]}.items():
        number = f"PR-{CLASS_CODES[func].split('.')[0]}-001"
        title = {"P": "Centrifugal pump changeover", "K": "Compressor restart after trip",
                 "V": "Pressure vessel isolation and draining"}[func]
        file = _write(docs_dir, number, title, [("Scope", [f"All {TYPE_NAMES[func]}s on {facility}."]),
                                                ("Steps", steps)], pdf=func == "K")
        manifest.append({"doc_number": number, "revision": "A", "doc_type": "procedure", "title": title,
                         "status": "current", "effective_date": "2021-03-01", "file": file,
                         "equipment_class": CLASS_CODES[func]})

    (out / "documents.json").write_text(json.dumps(manifest, indent=1))
    _answer_keys(out, manifest, equipment, guides)
    return manifest


def _answer_keys(out: Path, manifest: list[dict], equipment: list[dict], guides: dict) -> None:
    """What correct linking and search should find, for `fuzer eval`."""
    keys = out / "answer_keys"
    keys.mkdir(exist_ok=True)
    tag_of = lambda floc: re.sub(r"^[A-Z]+-(\d{2})-([A-Z]+)(\d+)([A-Z]?)$", r"\1-\2-\3\4", floc)
    installed = {}
    for e in equipment:
        if not e["valid_to"]:
            installed.setdefault((e["manufacturer"], e["model"]), tag_of(e["floc"]))
    linking, modes, search = [], [], []
    for d in manifest:
        if d["status"] != "current":
            continue
        doc = f"{d['doc_number']} rev {d['revision']}"
        if d["doc_type"] == "oem_manual":
            linking.append({"document": doc, "applies_to": [f"{d['manufacturer'].upper()} {d['model'].upper()}"]})
            func = next(f for f, n in TYPE_NAMES.items() if n in d["title"])
            symptoms = [sym for sym, _ in TROUBLESHOOTING[func]]
            expected = sorted({SYMPTOM_MODES[sym] for sym in symptoms} | ({"VIB"} if d["revision"] == "B" else set()))
            modes.append({"document": doc, "heading_contains": "Troubleshooting", "failure_modes": expected})
            if tag := installed.get((d["manufacturer"], d["model"])):
                for sym in symptoms[:2]:
                    search.append({"question": f"what to do about {sym.lower()}", "ref": tag,
                                   "expected": {"document": doc, "heading_contains": "Troubleshooting"}})
        elif d["doc_type"] == "procedure":
            linking.append({"document": doc, "applies_to": [f"ISO14224:{d['equipment_class']}"]})
        elif d["doc_type"] == "troubleshooting_guide":
            linking.append({"document": doc, "applies_to": d["tags"]})
            area = d["doc_number"].split("-")[1]
            for step in guides[area][2]:
                tag = next((t for t in d["tags"] if t in step), d["tags"][0])
                words = " ".join(re.sub(r"\b\d{2}-[A-Z]+-\d+[A-Z]?\b", "", step).replace(":", " ").split()[:14])
                search.append({"question": words.strip(" .,"), "ref": tag,
                               "expected": {"document": doc, "heading_contains": "Symptoms"}})
    (keys / "linking.json").write_text(json.dumps(linking, indent=1))
    (keys / "section_modes.json").write_text(json.dumps(modes, indent=1))
    (keys / "search.json").write_text(json.dumps(search, indent=1))
