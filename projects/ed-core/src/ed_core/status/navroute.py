"""
NavRoute.json parser.

The cleared file misleadingly contains `event: "NavRouteClear"` — we detect
no-route by `len(Route) == 0` rather than by the event field.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class NavRouteWaypoint(BaseModel):
    model_config = ConfigDict(extra="allow")
    star_system: str = Field(alias="StarSystem")
    system_address: int = Field(alias="SystemAddress")
    star_pos: list[float] = Field(alias="StarPos")
    star_class: str = Field(alias="StarClass")


class NavRoute(BaseModel):
    model_config = ConfigDict(extra="allow")
    route: list[NavRouteWaypoint] = Field(default_factory=list, alias="Route")

    @property
    def empty(self) -> bool:
        return len(self.route) == 0


def parse_navroute(obj: dict | str) -> NavRoute:
    if isinstance(obj, str):
        obj = json.loads(obj)
    return NavRoute.model_validate(obj)


class NavRouteReader:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._last_mtime: float = 0.0
        self._last_value: Optional[NavRoute] = None

    def poll(self) -> Optional[NavRoute]:
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
            return None
        try:
            self._last_value = parse_navroute(raw)
        except (json.JSONDecodeError, ValueError):
            return None
        return self._last_value

    @property
    def current(self) -> Optional[NavRoute]:
        return self._last_value
