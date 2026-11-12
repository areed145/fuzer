"""Unexpected errors reported to GitHub as issues, so a crash on the deployment reaches the repository's issue list
(and the threads that work it) without anyone reading Render's log stream. Ported from coconut-barometer.

What is reported: an exception that kills any Fuzer process (the deploy supervisor, the API, the agent workers,
Dagster's webserver, daemon and run processes, since each imports `fuzer` first), a thread that dies, a request
that ends in a 500, and a failed Dagster run (`fuzer.ops.jobs.alert_on_failure`).

Off until FUZER_GITHUB_ISSUES_TOKEN (a fine-grained token for the one repository, with Issues: read and write) and
FUZER_GITHUB_ISSUES_REPO (owner/name) are set; local runs and tests report nothing.

Each error is fingerprinted by its type and the innermost frame of Fuzer's own code (file and function, not the
line, so an unrelated edit doesn't make it new). An open `auto-report` issue with that fingerprint gets a comment
instead of a twin, at most one per COMMENT_EVERY across every process and restart (the issue's own updated_at is
the clock, so a crash loop adds one comment an hour, not one per restart); close the issue and the next occurrence
opens a new one. At most NEW_PER_HOUR new issues an hour per process.

Reports from a live process go out from their own thread, so a slow or unreachable GitHub never holds up a request
or a job; a process that is dying sends its report before it exits (bounded by TIMEOUT). A report that fails is
dropped and printed. Everything sent is scrubbed of the token, the database URL, anything that looks like a secret,
and the home directory. Only the standard library is used: this module loads in every process before anything else.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import queue
import re
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

API = "https://api.github.com"
LABEL = "auto-report"
COMMENT_EVERY = 3600  # seconds between comments on one issue
NEW_PER_HOUR = 10
TIMEOUT = 15
PACKAGE = Path(__file__).resolve().parent.parent  # src/fuzer
TOKEN_ENV, REPO_ENV = "FUZER_GITHUB_ISSUES_TOKEN", "FUZER_GITHUB_ISSUES_REPO"
SECRET_ENV = (TOKEN_ENV, "FUZER_DATABASE_URL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "FUZER_MICROSOFT_CLIENT_SECRET",
              "FUZER_OWNER_BOOTSTRAP_TOKEN", "FUZER_DAGSTER_PG_URL", "FUZER_ALERT_WEBHOOK")
SECRET_PARAM = re.compile(r"(?i)\b(api_key|api_secret|token|secret|password|passwd|client_secret|sig|signature)"
                          r"(['\"]?\s*[=:]\s*['\"]?)([^&\s'\",)]+)")
BEARER = re.compile(r"(?i)\b(bearer|token)\s+[a-z0-9_\-.]{12,}")
URL_PASSWORD = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^:/@\s]+:)[^@\s]+@")
logger = logging.getLogger(__name__)


class Reported(Exception):
    """A failure that isn't a Python exception in this process (a failed Dagster run): its fingerprint is given."""

    def __init__(self, message: str, fingerprint: str, kind: str = "Failure"):
        super().__init__(message)
        self.fingerprint = fingerprint
        self.kind = kind


def fingerprint(exc: BaseException) -> str:
    """The exception's type and the innermost frame in this package (or the innermost of all, if none is)."""
    if isinstance(exc, Reported):
        return hashlib.sha1(exc.fingerprint.encode()).hexdigest()[:12]
    frames = traceback.extract_tb(exc.__traceback__)
    ours = [f for f in frames if Path(f.filename).resolve().is_relative_to(PACKAGE)]
    f = (ours or frames or [None])[-1]
    where = "?"
    if f is not None:
        where = f"{Path(f.filename).resolve().relative_to(PACKAGE) if ours else Path(f.filename).name}:{f.name}"
    return hashlib.sha1(f"{type(exc).__qualname__}@{where}".encode()).hexdigest()[:12]


def scrub(text: str) -> str:
    for name in SECRET_ENV:
        s = os.environ.get(name)
        if s and len(s) >= 6:
            text = text.replace(s, "[redacted]")
    text = URL_PASSWORD.sub(lambda m: f"{m[1]}[redacted]@", text)
    text = SECRET_PARAM.sub(lambda m: f"{m[1]}{m[2]}[redacted]", text)
    text = BEARER.sub(lambda m: f"{m[1]} [redacted]", text)
    return text.replace(str(Path.home()), "~")


def _version() -> str:
    commit = os.environ.get("RENDER_GIT_COMMIT", "")  # Render sets it
    return commit[:7] or "unknown"


def _program() -> str:
    name = Path(sys.argv[0]).name if sys.argv and sys.argv[0] else "python"
    return {"deploy.py": "deploy supervisor", "__main__.py": "python -m"}.get(name, name)


class Reporter:
    def __init__(self):
        self.lock = threading.Lock()
        self._q: queue.Queue = queue.Queue(maxsize=100)
        self._thread: threading.Thread | None = None
        self._issues: dict[str, int] = {}  # fingerprint -> open issue number, as found or opened
        self._last: dict[str, float] = {}  # fingerprint -> when this process last sent it
        self._unsent: dict[str, int] = {}  # fingerprint -> times seen since then
        self._opened: list[float] = []  # when each new issue was opened, for NEW_PER_HOUR

    @staticmethod
    def _github() -> tuple[str, str] | None:
        token, repo = os.environ.get(TOKEN_ENV, "").strip(), os.environ.get(REPO_ENV, "").strip()
        return (token, repo) if token and re.fullmatch(r"[\w.-]+/[\w.-]+", repo) else None

    def enabled(self) -> bool:
        return self._github() is not None

    def report(self, exc: BaseException, where: str, *, wait: bool = False) -> None:
        """Send exc to GitHub, from the reporting thread (or now, when `wait`: the process is about to die).
        Never raises."""
        try:
            if not self.enabled():
                return
            fp = fingerprint(exc)
            with self.lock:
                self._unsent[fp] = self._unsent.get(fp, 0) + 1
                if fp in self._last and time.time() - self._last[fp] < COMMENT_EVERY:
                    return  # counted; said with the next comment
                self._last[fp] = time.time()
            kind = exc.kind if isinstance(exc, Reported) else type(exc).__qualname__
            text = str(exc) if isinstance(exc, Reported) else \
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            item = (fp, kind, str(exc), text, f"{where} ({_program()})", time.time())
            if wait:
                self._deliver(item)
                return
            self._q.put_nowait(item)
            self._start()
        except Exception as e:  # reporting must never be the next error
            logger.warning("error report not queued: %s", e)

    def _start(self) -> None:
        with self.lock:
            if not self._thread:
                self._thread = threading.Thread(target=self._run, name="error-reports", daemon=True)
                self._thread.start()

    def _run(self) -> None:
        while True:
            self._deliver(self._q.get())

    def _deliver(self, item) -> None:
        try:
            self._send(*item)
        except Exception as e:  # dropped; the error itself is still in the process's log
            print(f"Error report not sent to GitHub: {scrub(f'{type(e).__name__}: {e}')}", file=sys.stderr, flush=True)

    def _call(self, method: str, path: str, body: dict | None = None):
        token, repo = self._github()
        req = urllib.request.Request(
            f"{API}/repos/{repo}{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "fuzer-bugreports",
                     **({"Content-Type": "application/json"} if body is not None else {})})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"GitHub {method} {path.split('?')[0]}: {e.code}") from None

    def _find(self, fp: str) -> dict | None:
        """The open issue with this fingerprint: remembered, or looked for among the open auto-reports."""
        marker = f"<!-- fingerprint: {fp} -->"
        if fp in self._issues:
            issue = self._call("GET", f"/issues/{self._issues[fp]}")
            if issue["state"] == "open":
                return issue
            del self._issues[fp]
            return None
        for issue in self._call("GET", f"/issues?state=open&labels={LABEL}&per_page=100"):
            if marker in (issue.get("body") or ""):
                self._issues[fp] = issue["number"]
                return issue
        return None

    def _send(self, fp, kind, message, text, where, at) -> None:
        text, message = scrub(text), scrub(message)
        with self.lock:
            seen, self._unsent[fp] = self._unsent.pop(fp, 1), 0
        when = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(at))
        issue = self._find(fp)
        if issue:
            updated = datetime.fromisoformat(issue["updated_at"])
            if (datetime.now(UTC) - updated).total_seconds() < COMMENT_EVERY:
                return  # another process or an earlier restart said it within the hour
            again = f"Seen again {when}" + (f" ({seen} times in this process since its last report)" if seen > 1 else "")
            self._call("POST", f"/issues/{issue['number']}/comments",
                       {"body": f"{again}, in `{where}`, commit {_version()}.\n\n```\n{text[-6000:]}\n```"})
            return
        now = time.time()
        self._opened = [t for t in self._opened if now - t < 3600]
        if len(self._opened) >= NEW_PER_HOUR:
            raise RuntimeError(f"{NEW_PER_HOUR} issues opened this hour already; not opening another")
        self._opened.append(now)
        first = message.strip().splitlines()[0][:100] if message.strip() else ""
        body = (f"Reported by the Fuzer deployment itself.\n\n"
                f"- **Where:** `{where}`\n- **When:** {when}\n- **Commit:** {_version()}\n"
                f"- **Python:** {platform.python_version()} on {platform.system()}\n\n"
                f"```\n{text[-12000:]}\n```\n\n<!-- fingerprint: {fp} -->")
        issue = self._call("POST", "/issues", {"title": f"{kind}: {first}" if first else kind,
                                               "body": body, "labels": [LABEL]})
        self._issues[fp] = issue["number"]


reporter = Reporter()
_installed = False


def install() -> None:
    """Report an exception that kills the process or one of its threads (when reporting is configured)."""
    global _installed
    if _installed or not reporter.enabled():
        return
    _installed = True
    previous_sys, previous_thread = sys.excepthook, threading.excepthook

    def process_hook(kind, value, tb):
        if not isinstance(value, (KeyboardInterrupt, SystemExit)):
            reporter.report(value, "process exited", wait=True)
        previous_sys(kind, value, tb)

    def thread_hook(args):
        if args.exc_value is not None and not isinstance(args.exc_value, SystemExit):
            reporter.report(args.exc_value, f"thread {args.thread.name if args.thread else '?'} died")
        previous_thread(args)

    sys.excepthook, threading.excepthook = process_hook, thread_hook
