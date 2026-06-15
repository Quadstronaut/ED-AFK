"""Shared nav/destination predicates used across domains.

Relocated from ed_autojump.flow.steps (Phase-1 reorg) so ed_core and
ed_explore can use them without upward imports into domain packages.
"""

from __future__ import annotations

from typing import Any


def _destination_is_local_star(st: Any, system_name: "str | None") -> "bool | None":
    """Is Status.Destination the CURRENT system's star?

    The 2026-06-07 10:30Z incident: nav_panel_target locked the NAV BEACON
    (journal-identically to a star lock — the compass dot renders for any
    locked target) and the orbit no-oped. Destination.Name is the only live
    discriminator: the primary star carries the BARE system name ("Acihaut"),
    secondaries the "<system> A".."<system> D" designation; beacons and
    scenario rows carry "$..." symbol names; stations carry unrelated names.

    Returns True (it's the star), False (it's something else / nothing is
    locked), or None (no status or system unknown — cannot judge; callers
    degrade to dot-only verification, loudly)."""
    if st is None or not system_name:
        return None
    dest = getattr(st, "destination", None)
    if dest is None:
        return False          # nothing locked at all -> the lock didn't take
    name = (getattr(dest, "name", "") or "").strip()
    if not name or name.startswith("$"):
        return False          # symbolic = beacon / scenario / signal row
    if name == system_name:
        return True           # primary star = bare system name
    # secondary star designation: "<system> A".."<system> Z" (one letter)
    if (name.startswith(system_name + " ")
            and len(name) == len(system_name) + 2
            and name[-1].isalpha()):
        return True
    return False


def _dest_is_named_station(st: Any) -> bool:
    """True iff Status.Destination is a locked BODY with a non-symbolic name —
    the station the route-complete decision already identified. Used to confirm
    a target press actually landed on the station (the T-then-fallback path)."""
    dest = getattr(st, "destination", None) if st is not None else None
    if dest is None:
        return False
    if getattr(dest, "body", 0) == 0:
        return False                       # an FSD route hop / star, not a body
    name = (getattr(dest, "name", "") or "").strip()
    return bool(name) and not name.startswith("$")
