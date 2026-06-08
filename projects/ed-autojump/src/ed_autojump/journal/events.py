"""
Typed journal-event models.

Field-name canon: Frontier Player Journal manual v32, cross-checked against
EDCD/EDDN schemas. We accept extra fields (forward-compat with patch bumps).
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class Event(BaseModel):
    """Common envelope. All journal lines have these two."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    timestamp: str
    event: str


class FuelCapacity(BaseModel):
    model_config = ConfigDict(extra="allow")
    main: float = Field(alias="Main")
    reserve: float = Field(alias="Reserve")


class Module(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    slot: str = Field(alias="Slot")
    item: str = Field(alias="Item")
    on: bool = Field(default=True, alias="On")
    health: float = Field(default=1.0, alias="Health")


class Loadout(Event):
    event: Literal["Loadout"]
    ship: str = Field(alias="Ship")
    ship_id: int = Field(alias="ShipID")
    ship_name: str = Field(default="", alias="ShipName")
    max_jump_range: float = Field(alias="MaxJumpRange")
    fuel_capacity: FuelCapacity = Field(alias="FuelCapacity")
    unladen_mass: float = Field(alias="UnladenMass")
    modules: list[Module] = Field(default_factory=list, alias="Modules")

    def has_module_starting_with(self, prefix: str) -> bool:
        return any(m.item.startswith(prefix) for m in self.modules)

    def fuel_scoop_present(self) -> bool:
        return self.has_module_starting_with("int_fuelscoop_")

    def detailed_surface_scanner_present(self) -> bool:
        return self.has_module_starting_with("int_detailedsurfacescanner")


class FSDTarget(Event):
    event: Literal["FSDTarget"]
    name: str = Field(alias="Name")
    system_address: int = Field(alias="SystemAddress")
    star_class: str = Field(alias="StarClass")
    remaining_jumps_in_route: int = Field(default=0, alias="RemainingJumpsInRoute")


class StartJump(Event):
    event: Literal["StartJump"]
    jump_type: str = Field(alias="JumpType")
    star_system: Optional[str] = Field(default=None, alias="StarSystem")
    system_address: Optional[int] = Field(default=None, alias="SystemAddress")
    star_class: Optional[str] = Field(default=None, alias="StarClass")


class FSDJump(Event):
    event: Literal["FSDJump"]
    star_system: str = Field(alias="StarSystem")
    system_address: int = Field(alias="SystemAddress")
    star_pos: list[float] = Field(alias="StarPos")
    body: Optional[str] = Field(default=None, alias="Body")
    body_id: Optional[int] = Field(default=None, alias="BodyID")
    body_type: Optional[str] = Field(default=None, alias="BodyType")
    jump_dist: float = Field(alias="JumpDist")
    fuel_used: float = Field(alias="FuelUsed")
    fuel_level: float = Field(alias="FuelLevel")


class FuelScoop(Event):
    event: Literal["FuelScoop"]
    scooped: float = Field(alias="Scooped")
    total: float = Field(alias="Total")


class FSSDiscoveryScan(Event):
    event: Literal["FSSDiscoveryScan"]
    progress: float = Field(alias="Progress")
    body_count: int = Field(alias="BodyCount")
    non_body_count: int = Field(alias="NonBodyCount")
    system_name: str = Field(alias="SystemName")
    system_address: int = Field(alias="SystemAddress")


class FSSAllBodiesFound(Event):
    event: Literal["FSSAllBodiesFound"]
    system_name: str = Field(alias="SystemName")
    system_address: int = Field(alias="SystemAddress")
    count: int = Field(alias="Count")


class Scan(Event):
    event: Literal["Scan"]
    scan_type: str = Field(alias="ScanType")
    body_name: str = Field(alias="BodyName")
    body_id: int = Field(alias="BodyID")
    star_type: Optional[str] = Field(default=None, alias="StarType")
    planet_class: Optional[str] = Field(default=None, alias="PlanetClass")
    was_discovered: bool = Field(default=False, alias="WasDiscovered")
    was_mapped: bool = Field(default=False, alias="WasMapped")
    distance_from_arrival_ls: Optional[float] = Field(
        default=None, alias="DistanceFromArrivalLS"
    )
    terraform_state: Optional[str] = Field(default=None, alias="TerraformState")
    mass_em: Optional[float] = Field(default=None, alias="MassEM")


class SAAScanComplete(Event):
    event: Literal["SAAScanComplete"]
    body_name: str = Field(alias="BodyName")
    body_id: int = Field(alias="BodyID")
    probes_used: int = Field(alias="ProbesUsed")
    efficiency_target: int = Field(alias="EfficiencyTarget")


class HullDamage(Event):
    event: Literal["HullDamage"]
    health: float = Field(alias="Health")
    player_pilot: bool = Field(default=True, alias="PlayerPilot")
    fighter: bool = Field(default=False, alias="Fighter")


class SupercruiseEntry(Event):
    event: Literal["SupercruiseEntry"]
    star_system: str = Field(alias="StarSystem")


class SupercruiseExit(Event):
    event: Literal["SupercruiseExit"]
    star_system: str = Field(alias="StarSystem")
    body: Optional[str] = Field(default=None, alias="Body")
    body_type: Optional[str] = Field(default=None, alias="BodyType")


class NavRouteClear(Event):
    """Written when the plotted route is cleared — emitted BOTH at the final
    hop (in witchspace, ~10s before the destination FSDJump) AND on a manual
    re-plot. It carries no payload of its own, so route-completion detection
    correlates it with the following FSDJump's SystemAddress rather than
    triggering off this event alone (see dispatcher route-complete latch)."""

    event: Literal["NavRouteClear"]


class SupercruiseDestinationDrop(Event):
    """Written the instant Supercruise Assist drops the ship at its locked
    destination — ~5s before the matching SupercruiseExit. For a station
    destination, `type` is the station name. The pair (this + SupercruiseExit
    BodyType=Station) is the dock flow's "arrived at the station" gate; there
    is no distance field in Status, so these events are the only signal."""

    event: Literal["SupercruiseDestinationDrop"]
    type: str = Field(default="", alias="Type")
    threat: Optional[int] = Field(default=None, alias="Threat")
    market_id: Optional[int] = Field(default=None, alias="MarketID")


class DockingRequested(Event):
    """Echo of a docking request the player (or our macro) just sent. Carries
    the pad inventory; we gate the actual dock on DockingGranted, not this."""

    event: Literal["DockingRequested"]
    station_name: str = Field(default="", alias="StationName")
    station_type: Optional[str] = Field(default=None, alias="StationType")
    market_id: Optional[int] = Field(default=None, alias="MarketID")


class DockingGranted(Event):
    """Docking permission granted; the assigned LandingPad follows. The
    Advanced Docking Computer then flies the ship to that pad and emits
    Docked. dock-await gates on Docked (with the Docked Status flag fallback),
    not this — this just confirms the request macro landed."""

    event: Literal["DockingGranted"]
    landing_pad: Optional[int] = Field(default=None, alias="LandingPad")
    station_name: str = Field(default="", alias="StationName")
    station_type: Optional[str] = Field(default=None, alias="StationType")
    market_id: Optional[int] = Field(default=None, alias="MarketID")


class DockingDenied(Event):
    """Docking request refused. `reason` drives recovery: "Distance" (we were
    outside the 7.5km no-fire zone) re-approaches and retries; any other
    reason (NoSpace/TooLarge/Hostile/Offences/ActiveFighter/NoReason) is a
    fail-closed abort-to-human — the bot can't resolve those itself."""

    event: Literal["DockingDenied"]
    reason: str = Field(default="", alias="Reason")
    station_name: str = Field(default="", alias="StationName")
    station_type: Optional[str] = Field(default=None, alias="StationType")
    market_id: Optional[int] = Field(default=None, alias="MarketID")


class Docked(Event):
    """Landed on a pad. The dock-complete gate (Docked Status flag, bit 0, is
    the state fallback). On Docked the Starport Services panel auto-opens."""

    event: Literal["Docked"]
    station_name: str = Field(default="", alias="StationName")
    station_type: Optional[str] = Field(default=None, alias="StationType")
    star_system: Optional[str] = Field(default=None, alias="StarSystem")
    system_address: Optional[int] = Field(default=None, alias="SystemAddress")
    market_id: Optional[int] = Field(default=None, alias="MarketID")
    station_faction: Optional[Any] = Field(default=None, alias="StationFaction")


class Undocked(Event):
    """Launched off the pad (auto-launch). Fires immediately on AUTO LAUNCH;
    the docking computer then flies the ship clear of the station. The ship is
    mass-locked by the station for ~10km after this — the FsdMassLocked flag
    clearing is the 'clear to jump' gate, NOT this event."""

    event: Literal["Undocked"]
    station_name: str = Field(default="", alias="StationName")
    market_id: Optional[int] = Field(default=None, alias="MarketID")


class ReceiveText(Event):
    """In-game comms / station-traffic text received. For docking, the
    critical message is "$STATION_NoFireZone_entered;" — the station broadcasts
    this the instant the ship crosses inside the 7.5km no-fire zone (live-
    verified 2026-06-07 by the operator from his own journal at Robigo Mines
    Orbis). This is the ONLY distance signal available in normal space; there
    is no distance field in Status.json. The dispatcher stashes a flag when
    this specific message is seen so step_dock_approach can gate on range-entry
    without re-reading the raw message string in a tight loop.

    `from_` and `channel` are optional — they vary by station/NPC comms.
    `message` is the raw $-token string or localised text."""

    event: Literal["ReceiveText"]
    message: str = Field(default="", alias="Message")
    from_: str = Field(default="", alias="From")
    channel: str = Field(default="", alias="Channel")


class Music(Event):
    """Fires every time the game's music track changes.

    For the launcher's purposes, the key value is `MusicTrack == "MainMenu"`,
    which is written ~immediately after FileHeader on a fresh launch and
    indicates the main menu is up and ready for user input. Other values
    observed include: NoTrack, Exploration, Combat_*, Supercruise, Starport,
    GalacticPowers (PowerPlay), CQC, Codex, etc.
    """

    event: Literal["Music"]
    music_track: str = Field(alias="MusicTrack")


class LoadGame(Event):
    """Fires when the player selects a mode and the game session loads in.

    For the launcher this is the handoff signal: once LoadGame is written
    we know we're past the main menu and the AFK loop can take over.
    `GameMode` is one of Open/Solo/Group; when Group, `Group` is the
    group's display name (useful for verifying we joined the right one).
    """

    event: Literal["LoadGame"]
    commander: str = Field(alias="Commander")
    fid: Optional[str] = Field(default=None, alias="FID")
    horizons: Optional[bool] = Field(default=None, alias="Horizons")
    odyssey: Optional[bool] = Field(default=None, alias="Odyssey")
    ship: Optional[str] = Field(default=None, alias="Ship")
    ship_id: Optional[int] = Field(default=None, alias="ShipID")
    game_mode: Optional[str] = Field(default=None, alias="GameMode")
    group: Optional[str] = Field(default=None, alias="Group")
    credits: Optional[int] = Field(default=None, alias="Credits")
    fuel_level: Optional[float] = Field(default=None, alias="FuelLevel")
    fuel_capacity: Optional[float] = Field(default=None, alias="FuelCapacity")


AnyEvent = Union[
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
    SupercruiseDestinationDrop,
    DockingRequested,
    DockingGranted,
    DockingDenied,
    Docked,
    Undocked,
    NavRouteClear,
    ReceiveText,
    Music,
    LoadGame,
    Event,
]


# Build a lookup from event-name to the most-specific model class.
_EVENT_MODELS: dict[str, type[Event]] = {
    "Loadout": Loadout,
    "FSDTarget": FSDTarget,
    "StartJump": StartJump,
    "FSDJump": FSDJump,
    "FuelScoop": FuelScoop,
    "FSSDiscoveryScan": FSSDiscoveryScan,
    "FSSAllBodiesFound": FSSAllBodiesFound,
    "Scan": Scan,
    "SAAScanComplete": SAAScanComplete,
    "HullDamage": HullDamage,
    "SupercruiseEntry": SupercruiseEntry,
    "SupercruiseExit": SupercruiseExit,
    "SupercruiseDestinationDrop": SupercruiseDestinationDrop,
    "DockingRequested": DockingRequested,
    "DockingGranted": DockingGranted,
    "DockingDenied": DockingDenied,
    "Docked": Docked,
    "Undocked": Undocked,
    "NavRouteClear": NavRouteClear,
    "ReceiveText": ReceiveText,
    "Music": Music,
    "LoadGame": LoadGame,
}


def parse_event(line: str | dict[str, Any]) -> Event:
    """
    Parse a journal line into the most specific typed model we know.

    Unknown events fall back to the generic Event envelope. Malformed JSON
    raises ValueError so the caller can decide whether to log-and-skip.
    """
    if isinstance(line, str):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed journal line: {exc}") from exc
    else:
        obj = line

    name = obj.get("event")
    cls = _EVENT_MODELS.get(name, Event)
    return cls.model_validate(obj)
