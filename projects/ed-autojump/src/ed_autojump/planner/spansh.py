"""
Spansh route API client.

Endpoints used (SPEC §8.6.1):

    POST https://www.spansh.co.uk/api/route        -> {"job": uuid}
    GET  https://www.spansh.co.uk/api/results/<uuid> -> {"result": {"system_jumps":[...]}}

The route POST returns a job UUID; we poll `/api/results/<uuid>` until the
status flips from "queued" to "ok". Implementation is small and synchronous;
unit tests use a fake transport.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol


class SpanshError(RuntimeError):
    """Wraps any Spansh-side failure (HTTP, malformed result, timeout)."""


@dataclass
class SpanshRouteWaypoint:
    system: str
    system_address: Optional[int]
    star_class: str
    distance_jumped: float
    fuel_used: Optional[float]
    fuel_left: Optional[float]
    distance_to_arrival: Optional[float]


@dataclass
class SpanshRouteResult:
    waypoints: list[SpanshRouteWaypoint]
    total_jumps: int
    total_distance_ly: float


class _Transport(Protocol):
    def post_json(self, url: str, data: dict[str, Any]) -> dict[str, Any]: ...
    def get_json(self, url: str) -> dict[str, Any]: ...


class _RequestsTransport:
    """Default transport that wraps `requests`."""

    def __init__(self):
        import requests

        self._requests = requests

    def post_json(self, url: str, data: dict[str, Any]) -> dict[str, Any]:
        r = self._requests.post(url, data=data, timeout=30)
        r.raise_for_status()
        return r.json()

    def get_json(self, url: str) -> dict[str, Any]:
        r = self._requests.get(url, timeout=30)
        r.raise_for_status()
        return r.json()


@dataclass
class SpanshClient:
    """Synchronous Spansh route client."""

    base_url: str = "https://www.spansh.co.uk"
    transport: _Transport = field(default_factory=_RequestsTransport)
    poll_interval_s: float = 2.0
    poll_timeout_s: float = 120.0
    sleeper: Callable[[float], None] = field(default=time.sleep)
    clock: Callable[[], float] = field(default=time.monotonic)

    def plot_route(
        self,
        *,
        source: str,
        destination: str,
        range_ly: float,
        efficiency: int = 60,
    ) -> SpanshRouteResult:
        """
        Plot a route between two systems by name. Returns a parsed result.
        Raises SpanshError on any failure.
        """
        url = f"{self.base_url}/api/route"
        params = {
            "efficiency": str(efficiency),
            "range": f"{range_ly:.2f}",
            "from": source,
            "to": destination,
        }
        try:
            posted = self.transport.post_json(url, params)
        except Exception as exc:  # noqa: BLE001 — wrap and surface
            raise SpanshError(f"POST {url} failed: {exc}") from exc

        job = posted.get("job")
        if not job:
            raise SpanshError(f"Spansh did not return a job UUID: {posted!r}")

        result_url = f"{self.base_url}/api/results/{job}"
        deadline = self.clock() + self.poll_timeout_s
        while True:
            try:
                payload = self.transport.get_json(result_url)
            except Exception as exc:  # noqa: BLE001
                raise SpanshError(f"GET {result_url} failed: {exc}") from exc

            status = payload.get("status", "queued")
            if status == "ok":
                return _parse_route_payload(payload)
            if status == "error":
                raise SpanshError(f"Spansh reported error: {payload!r}")
            if self.clock() >= deadline:
                raise SpanshError("Spansh route poll timed out")
            self.sleeper(self.poll_interval_s)


def _parse_route_payload(payload: dict[str, Any]) -> SpanshRouteResult:
    result = payload.get("result") or {}
    raw_jumps = (
        result.get("system_jumps")
        or result.get("jumps")
        or []
    )
    waypoints: list[SpanshRouteWaypoint] = []
    for j in raw_jumps:
        waypoints.append(
            SpanshRouteWaypoint(
                system=j.get("system") or j.get("name") or "",
                system_address=j.get("id64") or j.get("system_address"),
                star_class=j.get("star_class") or j.get("StarClass") or "",
                distance_jumped=float(j.get("distance_jumped") or 0.0),
                fuel_used=j.get("fuel_used"),
                fuel_left=j.get("fuel_left"),
                distance_to_arrival=j.get("distance_to_arrival"),
            )
        )
    return SpanshRouteResult(
        waypoints=waypoints,
        total_jumps=int(result.get("total_jumps") or len(waypoints)),
        total_distance_ly=float(result.get("total_distance") or 0.0),
    )
