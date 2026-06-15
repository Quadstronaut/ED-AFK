"""ed-autojump executors — the jump + dock low-level maneuver helpers.

2026-06-06 purge: perform_honk/HonkOutcome (legacy honk macro — the live
honk is procedures/honk.toml via hold_until_event), EventDriver/Outcome
(orchestrator-era scaffolding), handle_start_jump and perform_star_escape
(jump-killing / star-ramming landmines) are all DELETED. The live modules
are navpanel.py and the danger filter below.

The shared closed-loop align controller (align.py) moved to ed-core with the
shared flight primitives; only the jump/dock executors live here.
"""

from .jump import should_refuse_target

__all__ = [
    "should_refuse_target",
]
