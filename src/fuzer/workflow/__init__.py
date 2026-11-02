"""Work engine (#97): move work between agents and people with BPMN 2.0 processes on Postgres.

- bpmn.py: the supported BPMN subset, Fuzer properties, expressions, timers and the publish checks.
- engine.py: definitions (draft -> checked -> published), running processes, tasks, timers, messages,
  hand-offs, escalation and the reads the inbox and board use.
- worker.py: claims agent steps (skip locked + lease), runs them, retries, then hands to a person.
- definitions/*.bpmn: the starting processes, imported and published by `load_defaults`.
"""
