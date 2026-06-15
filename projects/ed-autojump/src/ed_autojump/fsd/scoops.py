"""Fuel-scoop max-rate lookup by Loadout module item string.

Delegates to ed_core.fsd_util; kept here so existing callers
(ed_autojump.fsd.scoops.scoop_max_rate_t_s) continue to resolve.
"""

from ed_core.fsd_util import scoop_max_rate_t_s  # noqa: F401 -- re-export

__all__ = ["scoop_max_rate_t_s"]
