"""ed-core — ED-AFK engine + plumbing + shared flight primitives.

Holds the journal/keys/status plumbing, the flow engine, the registry +
active-set runtime, the shared (cross-domain) flight-primitive steps, and the
CLI host that selects and runs the active app set. Imports DOWN into ed-vision
only; never imports a domain. (Phase-1 reorg skeleton; modules relocate here in
Step 3.)
"""

from __future__ import annotations

__version__ = "0.2.0"
