# ed-combat

<p>
  <img alt="role" src="https://img.shields.io/badge/role-combat%20domain-blue">
  <img alt="phase" src="https://img.shields.io/badge/phase--1-scaffold-lightgrey">
  <img alt="license" src="https://img.shields.io/badge/license-AGPL--3.0--or--later-A42E2B">
</p>

Combat domain for the ED-AFK bot. **Phase 1: an empty scaffold** — its
`activate()` registers **nothing** (no steps, no classifier, no event-routes, no
procedures) and reserves the slot to prove the plug-in contract with a no-op
domain. Unlike the auto-discovered `ed-trading`, combat runs **solo** in the
active set (it cannot co-activate with another app). Real build is Phase 2.

Depends on `ed-core` + `ed-vision`; never imports a sibling domain.

Part of the ED-AFK workspace — see the [repo-root README](../../README.md).

## License

**AGPL-3.0-or-later** — declared in this package's `pyproject.toml` (matching
the ED-AFK distribution it runs inside). The repo also carries a root MIT
`LICENSE` file; the per-package metadata is the binding declaration here.
