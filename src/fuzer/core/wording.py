"""Words for people (#1072): the one display layer that turns the system's spellings into what IOC staff and field
operators read. Titles, reports, handover lines and lists all go through it, so an alarm kind, a unit, a queue or a
failure code reads the same everywhere.

- `alarm(key)`: a detection key as words, `alarm:lo` "Low alarm", `iow:hi` "High IOW limit".
- `unit(u)` / `units(text)`: unit symbols, `degC` "°C", `Sm3` "Sm³", `um` "µm".
- `queue(role)`: a routing role as a queue name, `role:data_steward` "data steward queue".
- `plural(n, word)`: "1 detection", "7 detections".
- `failure_mode(code)`: an ISO 14224 failure-mode code as its word, `VIB` "Vibration".
- `say(text)`: all of the above on text already written, for titles stored before this layer existed: it rewrites
  alarm keys, units and queue roles, and the old upset and incident title shapes.
"""

import re
from functools import lru_cache

_LIMIT_WORDS = {"hihi": "high-high", "hi": "high", "lo": "low", "lolo": "low-low"}
_BASIS_WORDS = {"alarm": "alarm", "iow": "IOW limit", "learned": "learned normal range"}
_KEY_WORDS = {"stopped": "Stopped", "step": "Step change", "drift_up": "Drifting up", "spe": "Multivariate anomaly",
              "dp_rising": "Pressure drop rising"}


def alarm(key: str | None) -> str:
    """A detection key as words a console operator reads (#405): alarm:hihi is "High-high alarm", iow:hi "High IOW
    limit", learned:lo "Below learned normal range", instrument:flatline "Instrument fault (flatline)"."""
    if not key:
        return "Process upset"
    if key in _KEY_WORDS:
        return _KEY_WORDS[key]
    head, _, tail = key.partition(":")
    if head in _BASIS_WORDS and tail in _LIMIT_WORDS:
        if head == "learned":
            return ("Above" if tail in ("hi", "hihi") else "Below") + " learned normal range"
        words = f"{_LIMIT_WORDS[tail]} {_BASIS_WORDS[head]}"
        return words[0].upper() + words[1:]
    if head == "instrument":
        return f"Instrument fault ({tail.replace('_', ' ')})"
    words = key.replace(":", " ").replace("_", " ")
    return words[0].upper() + words[1:]


def _lower_first(s: str) -> str:
    """'Low alarm' -> 'low alarm', keeping an acronym ('IOW limit') as it is."""
    return s if len(s) > 1 and s[1].isupper() else s[:1].lower() + s[1:]


# The symbol each stored unit spelling is printed with. Units not listed print as stored.
SYMBOLS = {"degC": "°C", "degF": "°F", "deg C": "°C", "um": "µm", "um/s": "µm/s", "Sm3": "Sm³", "kSm3": "kSm³",
           "MSm3": "MSm³", "Sm3/d": "Sm³/d", "kSm3/d": "kSm³/d", "MSm3/d": "MSm³/d", "Sm3/h": "Sm³/h",
           "Am3/h": "Am³/h", "Am3/d": "Am³/d", "m3": "m³", "m3/h": "m³/h", "m3/d": "m³/d", "Sm3/Sm3": "Sm³/Sm³",
           "kSm3/Sm3": "kSm³/Sm³", "degC/week": "°C/week", "kg/m3": "kg/m³"}
_UNIT = re.compile(r"(?<![\w])(?:k|M)?(?:Sm3|Am3|m3)(?!\w)|(?<![\w])degC(?!\w)|(?<=\d )um(?!\w)|(?<=\d)um(?!\w)|^um(?!\w)")


def unit(u: str | None) -> str:
    """A unit as printed: degC is °C, Sm3/d is Sm³/d, um is µm."""
    if not u:
        return ""
    return SYMBOLS.get(u) or units(u)


def units(text: str) -> str:
    """Unit spellings in running text as their symbols. `um` only after a number, where it can't be a word."""
    def sub(m):
        s = m.group(0)
        return "µm" if s == "um" else "°C" if s == "degC" else s.replace("3", "³")
    return _UNIT.sub(sub, text)


_DURATION = re.compile(r"\bP(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?\b")


def duration(iso: str | None) -> str:
    """An ISO 8601 duration as words: PT4H is "4 hours", P1DT2H "1 day 2 hours"; anything else as given."""
    m = _DURATION.fullmatch(str(iso or ""))
    if not m or not any(m.groups()):
        return str(iso or "")
    return " ".join(plural(int(n), w) for n, w in zip(m.groups(), ("week", "day", "hour", "minute")) if n)


def _local(at: str) -> str:
    """'2026-11-12 23:29' in UTC as the plant's clock reads it: '12 Nov 18:29'."""
    from datetime import UTC, datetime

    from .clock import zone

    try:
        t = datetime.strptime(at, "%Y-%m-%d %H:%M").replace(tzinfo=UTC).astimezone(zone())
    except (ValueError, KeyError):
        return f"{at} UTC"
    return f"{t.day} {t:%b %H:%M}"


def escalation(step: str, sla: str | None, due_utc: str, holder: str, to_role: str | None) -> str:
    """Why a step escalated, as a person says it (#1072): "'Work the case' was due within 4 hours, by 12 Nov 18:29,
    while it was with sam. It moved to the ops lead queue." Times on the plant's clock."""
    within = f" within {duration(sla)}" if sla else ""
    return (f"'{step}' was due{within}, by {_local(due_utc)}, while it was with {say(holder)}."
            + (f" It moved to the {queue(to_role)}." if to_role else ""))


def queue(role: str | None) -> str:
    """A routing role as the queue people see: 'role:data_steward' and 'ops_lead' both read 'data steward queue',
    'ops lead queue'."""
    name = str(role or "").removeprefix("role:").replace("_", " ").strip()
    return f"{name} queue" if name else "no queue"


def plural(n: float, word: str, many: str | None = None) -> str:
    """'1 detection', '7 detections', '1,204 notifications'."""
    count = f"{n:,}" if isinstance(n, int) else f"{n:,g}"
    return f"{count} {word if n == 1 else many or word + 's'}"


@lru_cache(maxsize=1)
def _failure_modes() -> dict[str, str]:
    from .taxonomy import load_standard

    return dict(load_standard("iso14224").get("failure_modes") or {})


def failure_mode(code: str | None) -> str | None:
    """An ISO 14224 failure-mode code as its word ('VIB' is 'Vibration'); an unknown code as given."""
    if not code:
        return None
    return _failure_modes().get(str(code).upper(), code)


# Text written before this layer: "Upset: Detected alarm:lo on OK_J21_08.WHP at 2025-12-30 00:00, reaching 25 units"
# and incidents' "OK-J21-05: detected alarm:lo on OK_J21_05.WHP".
_OLD_UPSET = re.compile(r"(?:Upset: )?Detected (?P<key>[a-z_]+(?::[a-z_]+)?) on (?P<where>\S+) at "
                        r"(?P<at>\d{4}-\d{2}-\d{2} \d{2}:\d{2}), reaching (?P<n>\d+) units?\b")
_DETECTED = re.compile(r"\bdetected (?P<key>[a-z_]+:[a-z_]+) on ")
_KEY = re.compile(r"(?<![\w:/])(?P<key>(?:alarm|iow|learned|instrument):[a-z_]+)(?!\w)")
_ROLE = re.compile(r"\brole:(?P<role>[a-z_]+)(?P<q> queue)?")
_OLD_ESCALATION = re.compile(r"SLA(?: (?P<sla>P[0-9WDTHM]+))? on '(?P<step>[^']+)' passed at "
                             r"(?P<at>\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC while it was with (?P<holder>.+?)"
                             r"(?:; moved to the (?P<to>[a-z_ ]+?) queue)?$")
_SNAKE_QUEUE = re.compile(r"\b(?P<role>[a-z]+(?:_[a-z]+)+) queue\b")


def say(text: str | None) -> str | None:
    """Text already written, in people's words: the old upset and incident title shapes, alarm keys, queue roles
    and unit spellings. Idempotent, and text with none of these comes back unchanged."""
    if not text:
        return text
    text = _OLD_UPSET.sub(lambda m: f"{alarm(m['key'])} on {m['where']} from {m['at']}"
                          + (f", across {m['n']} assets" if int(m["n"]) > 1 else ""), text)
    text = _OLD_ESCALATION.sub(lambda m: escalation(m["step"], m["sla"], m["at"], m["holder"], m["to"]), text)
    text = _DETECTED.sub(lambda m: f"{_lower_first(alarm(m['key']))} on ", text)
    text = _KEY.sub(lambda m: _lower_first(alarm(m["key"])), text)
    text = _ROLE.sub(lambda m: queue(m["role"]) if m["q"] else queue(m["role"]).removesuffix(" queue"), text)
    text = _SNAKE_QUEUE.sub(lambda m: queue(m["role"]), text)
    return units(text)
