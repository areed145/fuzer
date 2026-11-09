"""The event outbox: what happened, written in the same transaction as the change, and handed to subscribers after.

    publish(conn, "exception.raised", ref, payload)   in the caller's transaction, so an event exists only if the
                                                      change committed
    @subscriber("exception.raised")                   a handler, declared next to the code that reacts
    deliver(conn)                                     run handlers for pending events (the work tick calls it)

A module reacts to another's events instead of being called by it, so the module that raises an exception need
not know who tells the field, posts to Teams or writes back to SAP. Handlers run in a savepoint each; one that
fails leaves its event pending to retry, up to MAX_ATTEMPTS, with the error kept on the row.

Subscribers are declared where they live. `SUBSCRIBERS` names their modules so `deliver` loads them by name (core
does not import the modules above it); a module with a subscriber adds itself there, and a test checks it did.
"""

from collections.abc import Callable
from importlib import import_module

from psycopg.types.json import Jsonb

SUBSCRIBERS = ("fuzer.ops.exceptions", "fuzer.sources.alerts")
MAX_ATTEMPTS = 5
Handler = Callable[[object, dict], None]  # (conn, event row)
HANDLERS: dict[str, list[Handler]] = {}


def subscriber(topic: str) -> Callable[[Handler], Handler]:
    """Run the decorated function for each delivered event on `topic`."""

    def register(fn: Handler) -> Handler:
        HANDLERS.setdefault(topic, []).append(fn)
        return fn

    return register


def publish(conn, topic: str, ref: str | None, payload: dict | None = None) -> int:
    """Record an event in the caller's transaction; returns its id."""
    return conn.execute("insert into event_outbox (topic, ref, payload) values (%s, %s, %s) returning id",
                        (topic, ref, Jsonb(payload or {}))).fetchone()["id"]


def load_subscribers() -> None:
    for module in SUBSCRIBERS:
        import_module(module)


def deliver(conn, limit: int = 200) -> dict:
    """Hand pending events to their handlers, oldest first, and commit. An event no handler wants is delivered."""
    load_subscribers()
    rows = conn.execute(
        "select * from event_outbox where delivered_at is null and attempts < %s order by id limit %s "
        "for update skip locked", (MAX_ATTEMPTS, limit)).fetchall()
    done = failed = 0
    for row in rows:
        error = None
        for handler in HANDLERS.get(row["topic"], []):
            conn.execute("savepoint event_handler")
            try:
                handler(conn, row)
                conn.execute("release savepoint event_handler")
            except Exception as e:  # noqa: BLE001  (a handler's failure must not stop the others)
                conn.execute("rollback to savepoint event_handler")
                error = f"{handler.__module__}.{handler.__name__}: {e}"
        if error is None:
            conn.execute("update event_outbox set delivered_at = now(), attempts = attempts + 1 where id = %s",
                         (row["id"],))
            done += 1
        else:
            conn.execute("update event_outbox set attempts = attempts + 1, last_error = %s where id = %s",
                         (error[:2000], row["id"]))
            failed += 1
    conn.commit()
    return {"delivered": done, "failed": failed}


def pending(conn, topic: str | None = None) -> list[dict]:
    return conn.execute("select * from event_outbox where delivered_at is null and (%(t)s::text is null or topic = %(t)s) "
                        "order by id", {"t": topic}).fetchall()
