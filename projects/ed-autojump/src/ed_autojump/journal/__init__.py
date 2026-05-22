"""Journal tailer + typed event models."""

from .events import (
    AnyEvent,
    Event,
    Loadout,
    FSDTarget,
    StartJump,
    FSDJump,
    FuelScoop,
    FSSDiscoveryScan,
    FSSAllBodiesFound,
    Scan,
    SAAScanComplete,
    HullDamage,
    SupercruiseEntry,
    SupercruiseExit,
    parse_event,
)
from .tail import JournalTail

__all__ = [
    "AnyEvent",
    "Event",
    "Loadout",
    "FSDTarget",
    "StartJump",
    "FSDJump",
    "FuelScoop",
    "FSSDiscoveryScan",
    "FSSAllBodiesFound",
    "Scan",
    "SAAScanComplete",
    "HullDamage",
    "SupercruiseEntry",
    "SupercruiseExit",
    "parse_event",
    "JournalTail",
]
