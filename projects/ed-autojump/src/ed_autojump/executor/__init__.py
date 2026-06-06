"""Executor helpers that the live flow imports directly.

2026-06-06 purge: perform_honk/HonkOutcome (legacy honk macro — the live
honk is procedures/honk.toml via hold_until_event), EventDriver/Outcome
(orchestrator-era scaffolding), handle_start_jump and perform_star_escape
(jump-killing / star-ramming landmines) are all DELETED. The live modules
are navpanel.py, align.py (imported by flow/steps.py) and the danger
filter below.
"""

from .jump import should_refuse_target

__all__ = [
    "should_refuse_target",
]
