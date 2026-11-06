"""Work engine (#97): move work between agents and people with BPMN 2.0 processes on Postgres.

- bpmn.py: the supported BPMN subset, Fuzer properties, expressions, timers and publish checks.
- definitions.py: process-definition and form lifecycle, plus the workflow people/role roster.
- runtime.py: process execution, tokens, timers, messages, migration and cancellation.
- assignment.py: task claiming and hand-offs, agent failures, and work inboxes.
- work_items.py: standalone work-item operations and item/task/process reads.
- engine.py: compatibility facade exposing the workflow API from its responsibility modules.
- worker.py: claims agent steps (skip locked + lease), runs them, retries, then hands to a person.
- definitions/*.bpmn: the starting processes, imported and published by `load_defaults`.
"""
