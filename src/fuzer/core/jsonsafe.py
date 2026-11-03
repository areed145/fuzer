"""JSON-safe values for storing in jsonb and returning from tools: datetimes as ISO text, numpy numbers as
Python numbers, tuples as lists. Dates, Decimals and the rest pass through."""

from datetime import datetime

import numpy as np


def plain(x):
    if isinstance(x, dict):
        return {k: plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [plain(v) for v in x]
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    return x.isoformat() if isinstance(x, datetime) else x
