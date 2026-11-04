"""How a system writes numbers and dates (#225): its source_system row says (decimal, date_format, timezone),
and rows are read into Fuzer's form before mapping, instead of in hooks: "1.234,5" with a decimal comma
becomes "1234.5", "31.12.2024 14:00" with date_format "%d.%m.%Y %H:%M" becomes "2024-12-31T14:00", and a
local time in the system's timezone moves to the project's (FUZER_TIMEZONE). Encoding is the CSV reader's.
"""

import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

_COMMA_NUM = re.compile(r"^-?(\d{1,3}(\.\d{3})+|\d+)(,\d+)?$")
_DIGITS = re.compile(r"^[\d\s./:T-]+$")


class Formats:
    """A row normaliser for one system; `active` is False when the system writes as Fuzer reads."""

    def __init__(self, decimal: str | None = None, date_format: str | None = None, timezone: str | None = None,
                 project_tz: str | None = None):
        self.comma = decimal == ","
        self.date_format = date_format or None
        src, dst = timezone or None, project_tz or os.environ.get("FUZER_TIMEZONE") or None
        self.shift = (ZoneInfo(src), ZoneInfo(dst)) if src and dst and src != dst else None
        self.active = self.comma or bool(self.date_format)
        self._date_cols: dict[str, bool] = {}

    @classmethod
    def of(cls, d: dict) -> "Formats":
        return cls(d.get("system_decimal"), d.get("system_date_format"), d.get("system_timezone"))

    def value(self, col: str, v):
        if not isinstance(v, str) or not v:
            return v
        if self.date_format and self._date_cols.get(col, True) and _DIGITS.match(v):
            try:
                t = datetime.strptime(v.strip(), self.date_format)
            except ValueError:
                if col not in self._date_cols:
                    self._date_cols[col] = False  # its first value isn't a date: not a date column
            else:
                self._date_cols[col] = True
                timed = any(x in self.date_format for x in ("%H", "%M", "%I"))
                if timed and self.shift:
                    t = t.replace(tzinfo=self.shift[0]).astimezone(self.shift[1]).replace(tzinfo=None)
                return t.isoformat(timespec="minutes") if timed else t.date().isoformat()
        if self.comma and ("," in v or "." in v) and _COMMA_NUM.match(v.strip()):  # dots only group thousands
            return v.strip().replace(".", "").replace(",", ".")
        return v

    def row(self, row: dict) -> dict:
        return {k: self.value(k, v) for k, v in row.items()} if self.active else row
