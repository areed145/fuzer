---
name: integrated-planning
description: Integrated planning - the critical path of a turnaround, project or campaign, its schedule levelled against shared resources (crews, cranes, beds, seats, vessels, deck space, capacity), what drives the finish, and a Gantt.
---

# Integrated planning

The schedule lives on Fuzer's integrated plan. Planners maintain its activities, durations, links and resource needs
on Plan, beside SAP orders and other planned work, so one schedule answers both the planner and you.

1. **Find the schedule.** `critical_path(project)` takes a turnaround's revision (OK-TAR-2026), a project or a campaign.
   With no project it reads the next one. An error lists the schedules that exist. If none fits, say the plan has no
   schedule for it and that a planner adds one on Plan. Don't build one from order hours.
2. **Critical path.** `critical_path` is the chain with zero total float, using logic only. Name the activities in
   order with their codes, and give `unconstrained_finish`. Total float is in working hours on the project's calendar.
3. **Levelled schedule.** `levelled_finish` is the finish once every activity waits for room on the resources it needs,
   counting other planned work that holds them. `levelled_chain` is what really sets the finish: an entry with
   `waited_for` waited for that resource. When levelling moves the finish, lead with that and name the resource.
   For example: "the mechanical crew, not the overhaul's logic, sets the finish."
4. **Overloads.** `overloads_if_unlevelled` shows where demand would exceed capacity if everything started as early as
   its logic allows. Give the resource, the dates, the need against the capacity, and the activities. These are the
   options a planner has: more people, a second crane, or resequencing.
5. **Window.** Compare the levelled finish with `project.finish_by`. Give `overruns_window_by_days` when it is over.
6. **Gantt.** `plot_gantt(project)` draws the levelled schedule with the critical path in red. Use
   `levelled=false` for early dates. Don't write a Mermaid gantt: the chart Fuzer draws comes from the same numbers.
7. **Materials and scope.** `material_readiness` and `turnaround_scope` still give the parts and the scope. A part
   that arrives after an activity's start is a constraint the schedule doesn't know yet, so say which activity it
   holds.

**Report:** the critical path, the unconstrained and levelled finish against the window, what drives the levelled
finish, the overloads with options, then the Gantt.

**Unknown here.** Weather windows, permits and isolations, and logistics (beds, seats and vessels) count only when
the plan holds them as resources with needs. Otherwise say they are not modelled. Never state that work is
scheduled or committed: the planner applies a schedule on Plan.

**Production capacity.** A resource of kind capacity is the most the system can handle online at once, in BOE/d.
Its load is the wells at their latest test rates, less the wells a job has down, plus what a job brings on from the
day it finishes. Levelling does not move work for it. Plan > Resources shows where a job would bring on more than
the system can take.
