"""Built files from earlier deploys, kept so a page opened before a deploy keeps working (P5.18a, #1171).

A page asks for its built files by content-hashed name. After a deploy the new build has new names, so a page left
open (an iPad on the desk) asking for a file it hasn't loaded yet, such as the Gantt part of the diagram library,
got a 404 and showed the diagram's source. On a deployment with a persistent disk, each start copies the build's
files into a kept folder and the server answers from it when the current build lacks a name:

    keep(build, kept, days)   copy the build's files in (marking each as current) and drop files no build has
                              named for `days`
    folder()                  the kept folder from FUZER_KEPT_ASSETS, if set and present

Files keep their content hash in the name, so a kept file is always the right one for the page that asks for it.
"""

import os
import shutil
import time
from pathlib import Path

ENV = "FUZER_KEPT_ASSETS"
DAYS = 14  # longer than anyone keeps a tab open without reloading


def keep(build: Path, kept: Path, days: int = DAYS) -> dict:
    """Copy the build's files into `kept` (each written whole, then renamed into place) and mark them current; drop
    kept files that no build has named for `days`."""
    kept.mkdir(parents=True, exist_ok=True)
    now = time.time()
    names = set()
    copied = 0
    for f in build.iterdir():
        if not f.is_file():
            continue
        names.add(f.name)
        target = kept / f.name
        if not target.exists():
            part = kept / f".{f.name}.part"
            shutil.copyfile(f, part)
            part.replace(target)
            copied += 1
        os.utime(target, (now, now))  # the last start whose build named it
    dropped = 0
    for f in kept.iterdir():
        if f.is_file() and f.name not in names and (f.name.startswith(".") or now - f.stat().st_mtime > days * 86400):
            f.unlink(missing_ok=True)
            dropped += 1
    return {"copied": copied, "kept": sum(1 for f in kept.iterdir() if f.is_file()), "dropped": dropped}


def folder() -> Path | None:
    path = os.environ.get(ENV)
    return Path(path) if path and Path(path).is_dir() else None
