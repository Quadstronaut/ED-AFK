"""
EDDN publisher.

Per SPEC §14.2:
- POST to https://eddn.edcd.io:4430/upload/
- Envelope: {"$schemaRef": "<schema-url>", "header": {...}, "message": {...}}
- header carries `uploaderID`, `softwareName`, `softwareVersion`
- message is the journal event with forbidden personal fields stripped

Forbidden fields per the EDDN schema READMEs:
- `event` (event name is implicit in the schemaRef)
- `timestamp` is kept but reformatted; EDDN requires ISO-8601 Z
- Player-personal fields: `BoostUsed`, `FuelUsed`, `FuelLevel`, `JumpDist`,
  `HappiestSystem`, `HomeSystem`, `Multicrew`, ...

Reference: https://github.com/EDCD/EDDN/tree/master/schemas
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol


EDDN_UPLOAD_URL = "https://eddn.edcd.io:4430/upload/"


SCHEMA_URLS = {
    "journal": "https://eddn.edcd.io/schemas/journal/1",
    "fssdiscoveryscan": "https://eddn.edcd.io/schemas/fssdiscoveryscan/1",
    "fssallbodiesfound": "https://eddn.edcd.io/schemas/fssallbodiesfound/1",
    "fssbodysignals": "https://eddn.edcd.io/schemas/fssbodysignals/1",
    "navroute": "https://eddn.edcd.io/schemas/navroute/1",
}


# Subset of EDDN's documented forbidden-field list. Conservative — strips
# anything that could identify the player or expose ship state EDDN doesn't
# want.
FORBIDDEN_FIELDS: frozenset[str] = frozenset({
    "FuelLevel", "FuelUsed", "JumpDist", "BoostUsed",
    "ActiveFine", "CockpitBreach", "Wanted",
    "Rebuy", "HullValue", "ModulesValue",
    "HappiestSystem", "HomeSystem",
    "Multicrew",
    # Per-message localised names — not personal but bloat schemas reject
    "Name_Localised", "Body_Localised",
})


class EddnError(RuntimeError):
    """Wraps a publish failure (HTTP error or transport exception)."""


class _Transport(Protocol):
    def post_bytes(self, url: str, data: bytes, headers: dict[str, str]) -> int: ...


class _RequestsTransport:
    """Default transport that wraps `requests`."""

    def __init__(self):
        import requests

        self._requests = requests

    def post_bytes(self, url: str, data: bytes, headers: dict[str, str]) -> int:
        r = self._requests.post(url, data=data, headers=headers, timeout=15)
        r.raise_for_status()
        return r.status_code


def strip_forbidden_fields(obj: Any) -> Any:
    """
    Recursively remove forbidden EDDN fields. Returns a new structure;
    inputs are not mutated.
    """
    if isinstance(obj, dict):
        return {
            k: strip_forbidden_fields(v)
            for k, v in obj.items()
            if k not in FORBIDDEN_FIELDS
        }
    if isinstance(obj, list):
        return [strip_forbidden_fields(v) for v in obj]
    return obj


def build_envelope(
    *,
    schema_ref: str,
    message: dict[str, Any],
    uploader_id: str,
    software_name: str,
    software_version: str,
) -> dict[str, Any]:
    """
    Construct an EDDN upload envelope. Strips forbidden fields from the
    message. Validates that schema_ref is set and that message is a dict.
    """
    if not schema_ref:
        raise ValueError("schema_ref is required")
    if not isinstance(message, dict):
        raise TypeError("message must be a dict")
    cleaned = strip_forbidden_fields(message)
    return {
        "$schemaRef": schema_ref,
        "header": {
            "uploaderID": uploader_id,
            "softwareName": software_name,
            "softwareVersion": software_version,
        },
        "message": cleaned,
    }


@dataclass
class EddnPublisher:
    """
    Posts envelopes to EDDN. Honors `enabled=False` as a no-op publisher
    so the bot can be wired in unconditionally.
    """

    uploader_id: str = ""
    software_name: str = "ED-AFK / ed-autojump"
    software_version: str = "0.2.0"
    enabled: bool = True
    transport: _Transport = field(default_factory=_RequestsTransport)
    url: str = EDDN_UPLOAD_URL
    published_count: int = 0

    def publish(self, schema_key: str, message: dict[str, Any]) -> bool:
        """
        Publish `message` under `schema_key` (one of SCHEMA_URLS). Returns
        True on successful post, False if disabled or message empty.
        Raises EddnError on transport failure.
        """
        if not self.enabled:
            return False
        if not message:
            return False
        schema_ref = SCHEMA_URLS.get(schema_key)
        if schema_ref is None:
            raise ValueError(f"unknown EDDN schema key: {schema_key!r}")
        envelope = build_envelope(
            schema_ref=schema_ref,
            message=message,
            uploader_id=self.uploader_id,
            software_name=self.software_name,
            software_version=self.software_version,
        )
        body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        try:
            self.transport.post_bytes(
                self.url,
                body,
                {"Content-Type": "application/json"},
            )
        except Exception as exc:  # noqa: BLE001
            raise EddnError(f"EDDN upload failed: {exc}") from exc
        self.published_count += 1
        return True

    def publish_fss_discovery(self, message: dict[str, Any]) -> bool:
        return self.publish("fssdiscoveryscan", message)

    def publish_fss_all_bodies(self, message: dict[str, Any]) -> bool:
        return self.publish("fssallbodiesfound", message)

    def publish_journal_event(self, message: dict[str, Any]) -> bool:
        return self.publish("journal", message)
