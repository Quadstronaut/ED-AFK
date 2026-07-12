# ed-explore

<p>
  <img alt="role" src="https://img.shields.io/badge/role-exploration%20domain-blue">
  <img alt="depends" src="https://img.shields.io/badge/depends-ed--core%20%2B%20ed--vision-informational">
  <img alt="license" src="https://img.shields.io/badge/license-AGPL--3.0--or--later-A42E2B">
</p>

In-system **exploration** domain for the ED-AFK bot. Depends on `ed-core` +
`ed-vision`; never imports a sibling domain.

Part of the ED-AFK workspace — see the [repo-root README](../../README.md).

## What it registers

`activate()` imports `steps_body_tour` as a side effect, registering the
**`body_tour`** step into `ed-core`'s merged step table. That step drives the
in-system body tour used by the `exploration` scene (the
`nav_supercruise_unexplored` loop that orbits each unexplored body in turn).
`activate()` is idempotent and auto-discovered via the `ed_autojump.plugins`
entry-point (`explore = "ed_explore:activate"`), so the CLI co-activates it
additively at startup.

> The older arrival-embedded `explore` / `station_strand_recovery` steps were
> **removed 2026-06-27** (flow redesign): the unexplored tour orbits rather than
> drops, so it needs no strand-recovery step, and the scene now lives in
> `exploration.toml` rather than inside `arrival`.

## License

**AGPL-3.0-or-later** — declared in this package's `pyproject.toml` (matching
the ED-AFK distribution it runs inside). The repo also carries a root MIT
`LICENSE` file; the per-package metadata is the binding declaration here.
