# ed-trading

<p>
  <img alt="role" src="https://img.shields.io/badge/role-trading%20domain-blue">
  <img alt="phase" src="https://img.shields.io/badge/phase--1-scaffold-lightgrey">
  <img alt="license" src="https://img.shields.io/badge/license-AGPL--3.0--or--later-A42E2B">
</p>

Trading domain for the ED-AFK bot. **Phase 1: an empty scaffold** — its
`activate()` registers **nothing yet**. Unlike solo combat, `ed-trading` is
**auto-discovered**: it declares `trading = "ed_trading:activate"` in the
`ed_autojump.plugins` entry-point group (explore-style), so the CLI plug-in loop
co-activates it additively the moment it is installed. `activate()` carries an
`_activated` idempotency guard (the entry-point loop wraps no error handling, so
it must never raise and must be safe to call twice). Real build is Phase 2.

Depends on `ed-core` + `ed-vision`; never imports a sibling domain.

Part of the ED-AFK workspace — see the [repo-root README](../../README.md).

## Pit-stop note (OUT OF SCOPE — NOT implemented)

Reserved future concept, **no code**: a mid-route docking pit-stop that SELLS
high-value cargo at an intermediate station before the final destination, then
RE-BUYS to refill, as an optimization for multi-condition trade trips
(sell-high-then-rebuy). This is a NOTE only — Phase 1 implements none of it and
reserves no executable code, steps, or procedures.

## License

**AGPL-3.0-or-later** — declared in this package's `pyproject.toml` (matching
the ED-AFK distribution it runs inside). The repo also carries a root MIT
`LICENSE` file; the per-package metadata is the binding declaration here.
