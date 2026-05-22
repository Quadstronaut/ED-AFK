"""Phase 5: EDDN publisher."""

from __future__ import annotations

import json
from typing import Any

import pytest

from ed_autojump.eddn import (
    EDDN_UPLOAD_URL,
    EddnError,
    EddnPublisher,
    build_envelope,
    strip_forbidden_fields,
)


# --- strip_forbidden_fields ----------------------------------------------


def test_strip_removes_personal_fields():
    out = strip_forbidden_fields(
        {"event": "FSDJump", "FuelLevel": 30.0, "FuelUsed": 4.0, "JumpDist": 24.5,
         "StarSystem": "Anon", "Rebuy": 100}
    )
    assert "FuelLevel" not in out
    assert "FuelUsed" not in out
    assert "JumpDist" not in out
    assert "Rebuy" not in out
    assert out["StarSystem"] == "Anon"


def test_strip_removes_localised_helpers():
    out = strip_forbidden_fields(
        {"Name": "iron", "Name_Localised": "Iron", "Body_Localised": "B1"}
    )
    assert "Name_Localised" not in out
    assert "Body_Localised" not in out
    assert out["Name"] == "iron"


def test_strip_recurses_into_nested_lists_and_dicts():
    obj = {
        "Modules": [
            {"Item": "x", "Rebuy": 1, "Sub": {"FuelUsed": 1.0, "OK": True}}
        ]
    }
    out = strip_forbidden_fields(obj)
    assert "Rebuy" not in out["Modules"][0]
    assert "FuelUsed" not in out["Modules"][0]["Sub"]
    assert out["Modules"][0]["Sub"]["OK"] is True


def test_strip_does_not_mutate_input():
    obj = {"FuelLevel": 30, "OK": 1}
    snapshot = json.dumps(obj, sort_keys=True)
    strip_forbidden_fields(obj)
    assert json.dumps(obj, sort_keys=True) == snapshot


# --- build_envelope -------------------------------------------------------


def test_build_envelope_basic():
    env = build_envelope(
        schema_ref="https://eddn.edcd.io/schemas/journal/1",
        message={"event": "Scan", "StarSystem": "Anon"},
        uploader_id="cmdr-1",
        software_name="ED-AFK",
        software_version="0.2.0",
    )
    assert env["$schemaRef"] == "https://eddn.edcd.io/schemas/journal/1"
    assert env["header"]["uploaderID"] == "cmdr-1"
    assert env["header"]["softwareName"] == "ED-AFK"
    assert env["header"]["softwareVersion"] == "0.2.0"
    assert env["message"]["StarSystem"] == "Anon"


def test_build_envelope_strips_message():
    env = build_envelope(
        schema_ref="https://x",
        message={"FuelLevel": 30, "StarSystem": "Anon"},
        uploader_id="u",
        software_name="s",
        software_version="1",
    )
    assert "FuelLevel" not in env["message"]
    assert env["message"]["StarSystem"] == "Anon"


def test_build_envelope_validates_inputs():
    with pytest.raises(ValueError):
        build_envelope(
            schema_ref="",
            message={"x": 1},
            uploader_id="u",
            software_name="s",
            software_version="1",
        )
    with pytest.raises(TypeError):
        build_envelope(
            schema_ref="x",
            message="not a dict",  # type: ignore[arg-type]
            uploader_id="u",
            software_name="s",
            software_version="1",
        )


# --- Publisher ------------------------------------------------------------


class _FakeTransport:
    def __init__(self, *, raise_on_post: bool = False):
        self.posts: list[tuple[str, bytes, dict[str, str]]] = []
        self.raise_on_post = raise_on_post

    def post_bytes(self, url: str, data: bytes, headers: dict[str, str]) -> int:
        if self.raise_on_post:
            raise RuntimeError("network down")
        self.posts.append((url, data, headers))
        return 200


def test_publisher_disabled_returns_false():
    transport = _FakeTransport()
    pub = EddnPublisher(enabled=False, transport=transport)
    assert pub.publish_fss_discovery({"event": "FSSDiscoveryScan"}) is False
    assert transport.posts == []


def test_publisher_publishes_to_upload_url():
    transport = _FakeTransport()
    pub = EddnPublisher(uploader_id="cmdr", transport=transport)
    ok = pub.publish_journal_event(
        {"event": "Scan", "StarSystem": "Anon", "FuelLevel": 30.0}
    )
    assert ok is True
    assert len(transport.posts) == 1
    url, body, headers = transport.posts[0]
    assert url == EDDN_UPLOAD_URL
    assert headers["Content-Type"] == "application/json"
    parsed = json.loads(body.decode("utf-8"))
    assert parsed["$schemaRef"].endswith("/journal/1")
    # Stripped.
    assert "FuelLevel" not in parsed["message"]


def test_publisher_increments_count():
    pub = EddnPublisher(transport=_FakeTransport())
    pub.publish_fss_discovery({"event": "FSSDiscoveryScan", "SystemName": "X"})
    pub.publish_fss_all_bodies({"event": "FSSAllBodiesFound", "Count": 5})
    assert pub.published_count == 2


def test_publisher_unknown_schema_raises():
    pub = EddnPublisher(transport=_FakeTransport())
    with pytest.raises(ValueError):
        pub.publish("not-a-schema", {"event": "X"})


def test_publisher_transport_failure_wraps_as_eddn_error():
    pub = EddnPublisher(transport=_FakeTransport(raise_on_post=True))
    with pytest.raises(EddnError):
        pub.publish_fss_discovery({"event": "FSSDiscoveryScan", "SystemName": "X"})


def test_publisher_empty_message_skipped():
    pub = EddnPublisher(transport=_FakeTransport())
    assert pub.publish_journal_event({}) is False
