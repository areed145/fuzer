"""The work engine: runs BPMN processes (the subset in bpmn.py) with all running state in Postgres.

A process is a `work_process` row on a `work_item` (the thing people and agents see). Each step it
reaches is a `work_token` row: ready steps are moved on straight away; user, agent, timer, message,
join and call steps wait. Rows stay when done, so the tokens are also the path taken. Everything that
happens is a `work_event`.

Every public operation is one transaction that locks the process row first (`for update`), so two
people, a worker and the timer tick can't move the same process at once. Agent steps are claimed by
workers with `for update skip locked` and a lease (worker.py); timers and SLAs are fired by `tick`.

Guardrails: a `requiresHuman` step can't be completed, claimed or handed to an agent; an agent step
that keeps failing goes to a person; a step that can't go on (a condition that fails to evaluate, no
branch matching) becomes a `fix` task for the process owner, never a silent stop.

Actors are a person's name, `agent:<role>` or `system`. The implementation is split across
definitions.py, runtime.py, assignment.py, and work_items.py; this module remains the stable import facade.
"""

from .definitions import *
from .runtime import *
from .assignment import *
from .work_items import *

# Keep historical internal helpers available to package callers and existing tests.
from .definitions import (
    _definition, _definition_row, _event, _op, _parsed, _problems, _published, _published_keys,
)
from .runtime import (
    _Run, _advance, _boundaries, _cancel_activity, _cancel_boundaries, _cancel_process, _choose,
    _correlation, _end, _escalate, _fail, _finish, _fire, _leave, _new_item, _owner, _parallel,
    _process_ended, _scope, _send, _start, _start_new, _step, _sync_item, _tick_timer, _token, _update,
)
from .assignment import (
    _ITEMS, _TASKS, _agent_failed, _bulk_labels, _form_of, _locked_task, _waiting_task,
)
from .work_items import _has_process, _item
