# ED-AFK Action Megasheet — RETIRED (pointer only)

> **This document has been retired.** It was a Council-of-7 audit snapshot frozen
> at **2026-06-08 (master @ ee4c0f7)** and it described a flow that no longer
> exists — blind `sc_assist_orbit` / `nav_panel_target` / `engage_jump` /
> `hold_alignment` / fixed-13 s success waits, none of which any live procedure
> references any more. Keeping 400+ lines of stale per-step detail as a third copy
> alongside the canonical action reference is exactly the duplication that reference
> exists to avoid, so the body has been removed rather than left to mislead. The
> full historical snapshot (Parts A–D, the 12-defect list, the old dispatcher tree)
> is preserved in git history at `ee4c0f7` if you ever need it.

## Where the action truth lives now

| You want… | Read |
|---|---|
| **Canonical** action reference — the ~46 registered actions, their params, the ORPHANED/LEGACY marks, the control-flow keys (`skip_to`/`loop_to`/`retry_anchor`), and the routing layers | [`projects/ed-autojump/procedures/procedures.md`](../projects/ed-autojump/procedures/procedures.md) |
| The live per-scene step lists (what the bot actually runs) | the `*.toml` files in [`projects/ed-autojump/procedures/`](../projects/ed-autojump/procedures/) — dropping a `*.toml` there *is* the wiring; the filename stem is the procedure name |
| Scene-by-scene **evaluation tables** (Kind / Req / Gate columns, "did we get the basics right") | [`ACTION_TABLES.md`](ACTION_TABLES.md) |
| Project overview, maturity, licensing split, ToS warning | [root `README.md`](../README.md) |

The action registry itself is defined by the `register_step(...)` calls in
`projects/ed-core/src/ed_core/flow/steps_shared.py`,
`projects/ed-autojump/src/ed_autojump/flow/steps.py`, and
`projects/ed-explore/src/ed_explore/steps_body_tour.py`. The dispatcher / real-time
scene monitor / boot classifier live in
`projects/ed-core/src/ed_core/flow/dispatcher.py` and
`projects/ed-autojump/src/ed_autojump/flow/boot_routes.py`.
