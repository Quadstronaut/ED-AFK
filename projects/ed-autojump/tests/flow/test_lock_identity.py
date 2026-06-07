"""Star-lock identity verification (2026-06-07 council, 10:30Z incident):
nav_panel_target locked the NAV BEACON in populated Acihaut, the beacon's
compass dot passed the dot-only verify, and sc_assist_orbit no-oped against
it from a nose-anywhere pose — every step reported success while the ship
sat still. These tests pin the two scenes that were green in CI and dead
live: wrong-body row-0 lock, and the pose/SC guards on the orbit macro."""

from types import SimpleNamespace
from pathlib import Path

from ed_autojump.flow.context import StepContext
from ed_autojump.flow.loader import load_procedures
from ed_autojump.flow.steps import STEP_REGISTRY, _destination_is_local_star
from ed_autojump.vision.compass import CompassRead
from tests.flow import FakeSender

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"


def _status(dest_name, *, in_supercruise=True):
    dest = None if dest_name is None else SimpleNamespace(name=dest_name)
    return SimpleNamespace(destination=dest, in_supercruise=in_supercruise,
                           gui_focus=0)


# ---- _destination_is_local_star truth table ---------------------------------

def test_identity_primary_star_is_bare_system_name():
    assert _destination_is_local_star(_status("Acihaut"), "Acihaut") is True


def test_identity_secondary_star_designation():
    assert _destination_is_local_star(
        _status("Col 285 Sector AB-W c2-5 A"), "Col 285 Sector AB-W c2-5") is True


def test_identity_rejects_symbolic_beacon_name():
    # the 10:30Z lock: "$MULTIPLAYER_SCENARIO42_TITLE;" (Nav Beacon)
    assert _destination_is_local_star(
        _status("$MULTIPLAYER_SCENARIO42_TITLE;"), "Acihaut") is False


def test_identity_rejects_station_and_no_lock():
    assert _destination_is_local_star(_status("Mastracchio Base"), "Acihaut") is False
    assert _destination_is_local_star(_status(None), "Acihaut") is False


def test_identity_unknowable_without_status_or_system():
    assert _destination_is_local_star(None, "Acihaut") is None
    assert _destination_is_local_star(_status("Acihaut"), None) is None


# ---- nav_panel_target: beacon on row 0, star on row 1 ------------------------

class _DotReader:
    """Compass dot always present — the beacon renders one too (the 10:30Z
    false positive)."""
    def read(self, frame):
        return CompassRead(found=True, offset_x=0.0, offset_y=0.0,
                           in_front=True, confidence=1.0)


def _nav_ctx(status_fn, system="Acihaut"):
    sender = FakeSender()
    ctx = StepContext(
        sender=sender, sleeper=lambda s: None,
        compass_reader=_DotReader(), frame_grabber=lambda: object(),
        compass_samples=1,
        status_supplier=status_fn,
        current_system_supplier=lambda: system,
    )
    return ctx, sender


def test_nav_panel_target_scrolls_past_the_beacon_to_the_star():
    sender_holder = []
    def status():
        # row 0 lock reads as the beacon until the macro scrolls (UI_Down),
        # then the row-1 lock reads as the star
        scrolled = "UI_Down" in sender_holder[0].events
        return _status("Acihaut" if scrolled else "$MULTIPLAYER_SCENARIO42_TITLE;")
    ctx, sender = _nav_ctx(status)
    sender_holder.append(sender)
    ok = STEP_REGISTRY["nav_panel_target"](ctx, settle_s=0.0)
    assert ok is True
    assert sender.events.count("UI_Down") == 1   # scrolled exactly one row


def test_nav_panel_target_fails_closed_when_no_row_is_the_star():
    ctx, sender = _nav_ctx(lambda: _status("$MULTIPLAYER_SCENARIO42_TITLE;"))
    ok = STEP_REGISTRY["nav_panel_target"](ctx, settle_s=0.0)
    assert ok is False                            # never a blind True


def test_nav_panel_target_accepts_dot_only_when_identity_unknowable():
    # legacy degrade: no current-system wiring -> dot-only verify, loudly
    ctx, sender = _nav_ctx(lambda: _status("$MULTIPLAYER_SCENARIO42_TITLE;"),
                           system=None)
    ok = STEP_REGISTRY["nav_panel_target"](ctx, settle_s=0.0)
    assert ok is True


# ---- sc_assist_orbit guards ---------------------------------------------------

def _orbit_ctx(status_fn, system="Acihaut"):
    sender = FakeSender()
    ctx = StepContext(sender=sender, sleeper=lambda s: None,
                      status_supplier=status_fn,
                      current_system_supplier=lambda: system)
    return ctx, sender


def test_orbit_refuses_outside_supercruise():
    ctx, sender = _orbit_ctx(lambda: _status("Acihaut", in_supercruise=False))
    assert STEP_REGISTRY["sc_assist_orbit"](ctx, settle_s=0.0) is False
    assert sender.actions() == []                 # no keys against a bad scene


def test_orbit_refuses_a_non_star_destination():
    # the exact 10:30Z scene: beacon locked, macro would no-op
    ctx, sender = _orbit_ctx(lambda: _status("$MULTIPLAYER_SCENARIO42_TITLE;"))
    assert STEP_REGISTRY["sc_assist_orbit"](ctx, settle_s=0.0) is False
    assert sender.actions() == []


def test_orbit_engages_on_a_verified_star_lock():
    logs = []
    ctx, sender = _orbit_ctx(lambda: _status("Acihaut"))
    ctx.record = lambda kind, payload: logs.append((kind, payload))
    assert STEP_REGISTRY["sc_assist_orbit"](ctx, settle_s=0.0) is True
    assert sender.events[:1] == ["FocusLeftPanel"]   # macro actually ran
    sent = [p for k, p in logs if k == "ScAssistOrbitSent"]
    assert sent and sent[0]["destination"] == "Acihaut"   # loud telemetry


# ---- arrival wiring -----------------------------------------------------------

def test_arrival_lock_orient_orbit_order_and_gates():
    proc = load_procedures(PROC_DIR)["arrival"]
    actions = [s.action for s in proc.steps]
    i = actions.index("nav_panel_target")
    assert actions[i:i + 3] == ["nav_panel_target", "orient_compass",
                                "sc_assist_orbit"]
    assert proc.steps[i].required is True         # wrong lock never flows on
    # a required fail re-establishes lock + pose together
    assert proc.on_required_fail.retry_from == "nav_panel_target"
