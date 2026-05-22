"""
Status.json parser + Flags bitfield decoder.

Status.json is rewritten in place by the game roughly every 0.5 seconds, more
often on state change. We poll on file modification time and retry on
JSONDecodeError because the file may briefly be empty mid-write.

Flag-bit positions cross-checked against:
- EDAPGui/StatusParser.py (the most-tested implementation; MIT)
- elite-journal.readthedocs.io
- Frontier Player Journal manual v32 §10 (Status File)
"""

from __future__ import annotations

import json
from enum import IntFlag
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class StatusFlags(IntFlag):
    """Status.json `Flags` uint32 bitfield. Names per Frontier manual."""

    Docked = 1 << 0
    Landed = 1 << 1
    LandingGearDown = 1 << 2
    ShieldsUp = 1 << 3
    Supercruise = 1 << 4
    FlightAssistOff = 1 << 5
    HardpointsDeployed = 1 << 6
    InWing = 1 << 7
    LightsOn = 1 << 8
    CargoScoopDeployed = 1 << 9
    SilentRunning = 1 << 10
    ScoopingFuel = 1 << 11
    SrvHandbrake = 1 << 12
    SrvTurret = 1 << 13
    SrvUnderShip = 1 << 14
    SrvDriveAssist = 1 << 15
    FsdMassLocked = 1 << 16
    FsdCharging = 1 << 17
    FsdCooldown = 1 << 18
    LowFuel = 1 << 19
    OverHeating = 1 << 20
    HasLatLong = 1 << 21
    IsInDanger = 1 << 22
    BeingInterdicted = 1 << 23
    InMainShip = 1 << 24
    InFighter = 1 << 25
    InSRV = 1 << 26
    AnalysisMode = 1 << 27
    NightVision = 1 << 28
    AltitudeFromAverageRadius = 1 << 29
    FsdJump = 1 << 30
    SrvHighBeam = 1 << 31


class StatusFlags2(IntFlag):
    """Status.json `Flags2` — Odyssey on-foot + telepresence states."""

    OnFoot = 1 << 0
    InTaxi = 1 << 1
    InMulticrew = 1 << 2
    OnFootInStation = 1 << 3
    OnFootOnPlanet = 1 << 4
    AimDownSight = 1 << 5
    LowOxygen = 1 << 6
    LowHealth = 1 << 7
    Cold = 1 << 8
    Hot = 1 << 9
    VeryCold = 1 << 10
    VeryHot = 1 << 11
    GlideMode = 1 << 12
    OnFootInHangar = 1 << 13
    OnFootSocialSpace = 1 << 14
    OnFootExterior = 1 << 15
    BreathableAtmosphere = 1 << 16


class _Fuel(BaseModel):
    model_config = ConfigDict(extra="allow")
    fuel_main: float = Field(default=0.0, alias="FuelMain")
    fuel_reservoir: float = Field(default=0.0, alias="FuelReservoir")


class Status(BaseModel):
    """Parsed Status.json. All fields optional — file is sparse."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    timestamp: Optional[str] = None
    event: Optional[str] = None
    flags: int = Field(default=0, alias="Flags")
    flags2: int = Field(default=0, alias="Flags2")
    pips: Optional[list[int]] = Field(default=None, alias="Pips")
    fire_group: Optional[int] = Field(default=None, alias="FireGroup")
    gui_focus: Optional[int] = Field(default=None, alias="GuiFocus")
    fuel: Optional[_Fuel] = Field(default=None, alias="Fuel")
    cargo: Optional[float] = Field(default=None, alias="Cargo")
    legal_state: Optional[str] = Field(default=None, alias="LegalState")
    body_name: Optional[str] = Field(default=None, alias="BodyName")
    heat: Optional[float] = Field(default=None, alias="Heat")
    altitude: Optional[float] = Field(default=None, alias="Altitude")
    heading: Optional[int] = Field(default=None, alias="Heading")

    # convenience helpers -------------------------------------------------

    def flag(self, bit: StatusFlags) -> bool:
        return bool(self.flags & bit.value)

    def flag2(self, bit: StatusFlags2) -> bool:
        return bool(self.flags2 & bit.value)

    @property
    def docked(self) -> bool:
        return self.flag(StatusFlags.Docked)

    @property
    def in_supercruise(self) -> bool:
        return self.flag(StatusFlags.Supercruise)

    @property
    def fsd_charging(self) -> bool:
        return self.flag(StatusFlags.FsdCharging)

    @property
    def fsd_mass_locked(self) -> bool:
        return self.flag(StatusFlags.FsdMassLocked)

    @property
    def fsd_cooldown(self) -> bool:
        return self.flag(StatusFlags.FsdCooldown)

    @property
    def scooping_fuel(self) -> bool:
        return self.flag(StatusFlags.ScoopingFuel)

    @property
    def overheating(self) -> bool:
        return self.flag(StatusFlags.OverHeating)

    @property
    def low_fuel(self) -> bool:
        return self.flag(StatusFlags.LowFuel)

    @property
    def is_in_danger(self) -> bool:
        return self.flag(StatusFlags.IsInDanger)

    @property
    def hardpoints_deployed(self) -> bool:
        return self.flag(StatusFlags.HardpointsDeployed)

    @property
    def analysis_mode(self) -> bool:
        return self.flag(StatusFlags.AnalysisMode)

    @property
    def in_main_ship(self) -> bool:
        return self.flag(StatusFlags.InMainShip)

    @property
    def in_wing(self) -> bool:
        return self.flag(StatusFlags.InWing)


def parse_status(obj: dict | str) -> Status:
    if isinstance(obj, str):
        obj = json.loads(obj)
    return Status.model_validate(obj)


class StatusReader:
    """Reads Status.json on demand; tolerates mid-write empty file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._last_mtime: float = 0.0
        self._last_value: Optional[Status] = None

    def poll(self) -> Optional[Status]:
        """Return Status if the file changed since last call, else None."""
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return None
        if stat.st_mtime == self._last_mtime and self._last_value is not None:
            return None
        self._last_mtime = stat.st_mtime
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = fh.read().strip()
        except (FileNotFoundError, PermissionError):
            return None
        if not raw:
            # Mid-write zero-length window. Keep the previous value.
            return None
        try:
            self._last_value = parse_status(raw)
        except (json.JSONDecodeError, ValueError):
            return None
        return self._last_value

    @property
    def current(self) -> Optional[Status]:
        return self._last_value
