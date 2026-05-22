"""
Master state machine + in-memory game state.

Per SPEC §11. Hand-rolled (no `transitions` library dependency) — the FSM is
small enough that a switch statement is clearer than a DSL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from .journal.events import FSDJump, FSDTarget, Loadout, StartJump
from .status.navroute import NavRoute
from .status.status import Status


class State(Enum):
    IDLE = auto()
    BOOTING = auto()
    PLANNING = auto()
    READY = auto()
    EXECUTING_LEG = auto()
    TARGETING = auto()
    CHARGING = auto()
    JUMPING = auto()
    ARRIVED = auto()
    ESCAPE_STAR = auto()
    SCOOPING = auto()
    HONK_AND_SCAN = auto()
    FSS = auto()
    DSS = auto()
    ROUTE_NEXT = auto()
    PANIC = auto()
    ABORT = auto()


@dataclass
class GameState:
    """In-memory snapshot of what the bot knows about the game world."""

    state: State = State.IDLE

    # Ship snapshot (updated on Loadout events).
    loadout: Optional[Loadout] = None

    # Last seen Status.json.
    status: Optional[Status] = None

    # Last seen NavRoute.json.
    last_navroute: Optional[NavRoute] = None

    # Current/last system, cached from FSDJump.
    current_system: Optional[str] = None
    current_system_address: Optional[int] = None
    last_fuel_level: Optional[float] = None
    last_star_class: Optional[str] = None  # cached from preceding FSDTarget/StartJump

    # Next jump target (cached from FSDTarget, BEFORE we engage).
    next_target: Optional[FSDTarget] = None

    # The just-seen StartJump:Hyperspace, used by the throttle-zero guard.
    last_start_jump: Optional[StartJump] = None

    # Per-system FSS / DSS bookkeeping.
    bodies_in_current_system: dict[int, dict] = field(default_factory=dict)

    def transition(self, new: State) -> None:
        self.state = new

    # Apply event helpers --------------------------------------------------

    def apply_loadout(self, ev: Loadout) -> None:
        self.loadout = ev

    def apply_status(self, st: Status) -> None:
        self.status = st

    def apply_navroute(self, nr: NavRoute) -> None:
        self.last_navroute = nr

    def apply_fsd_target(self, ev: FSDTarget) -> None:
        self.next_target = ev
        self.last_star_class = ev.star_class

    def apply_start_jump(self, ev: StartJump) -> None:
        self.last_start_jump = ev
        if ev.star_class:
            self.last_star_class = ev.star_class

    def apply_fsd_jump(self, ev: FSDJump) -> None:
        self.current_system = ev.star_system
        self.current_system_address = ev.system_address
        self.last_fuel_level = ev.fuel_level
        # Body table is replaced on each arrival.
        self.bodies_in_current_system.clear()
