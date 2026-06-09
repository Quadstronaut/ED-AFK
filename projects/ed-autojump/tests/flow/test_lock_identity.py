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


# ---- nav_panel_target: decoupled row scan (F2, the 11:23Z Lyncis incident) --

def test_star_past_row_three_is_reachable():
    """Rows 0-4 all wrong bodies (station/USS), the star on row 5. The old
    `for attempt in range(max_toggles)` with max_rows=4 made this structurally
    unreachable — this FAILS on that code and PASSES with the decoupled row
    counter. The held-up pin fires once per macro run, so its count tells us
    which row this run targets (run N -> row N-1)."""
    sender_holder = []

    def status():
        pins = len([h for a, h in sender_holder[0].holds
                    if a == "UI_Up" and h >= 1.0])
        # wrong body for runs 1-5 (rows 0-4); correct star on run 6 (row 5)
        return _status("Acihaut" if pins >= 6
                       else "$MULTIPLAYER_SCENARIO42_TITLE;")

    ctx, sender = _nav_ctx(status)
    sender_holder.append(sender)
    ok = STEP_REGISTRY["nav_panel_target"](ctx, settle_s=0.0, pin_hold_s=4.0)
    assert ok is True


def test_all_rows_wrong_fails_closed():
    """Every row is a wrong body through exhaustion -> False (fail closed
    preserved): a wrong lock must never flow on to the orbit."""
    ctx, sender = _nav_ctx(lambda: _status("$MULTIPLAYER_SCENARIO42_TITLE;"))
    ok = STEP_REGISTRY["nav_panel_target"](ctx, settle_s=0.0)
    assert ok is False


class _MissThenDotReader:
    """No dot on the FIRST macro run's reads; a dot from the second run on.
    The pin's held UI_Up fires once per macro run, so the first run's pin is
    on the sender before its dot reads happen — we gate on the pin count."""
    def __init__(self, sender_holder):
        self._sh = sender_holder

    def read(self, frame):
        pins = len([h for a, h in self._sh[0].holds
                    if a == "UI_Up" and h >= 1.0])
        if pins <= 1:
            return CompassRead(found=False, offset_x=0.0, offset_y=0.0,
                               in_front=False, confidence=0.0)
        return CompassRead(found=True, offset_x=0.0, offset_y=0.0,
                           in_front=True, confidence=1.0)


def test_dot_miss_retoggles_same_row_without_advancing():
    """A dot miss is a SAME-row re-toggle (the UNLOCK-toggle case), not a row
    advance: first macro yields no dot, second yields a dot + correct identity
    at row 0 -> True, and the row walk never advanced (zero UI_Down beyond the
    one pin-tap-down per macro run)."""
    sender_holder = []
    ctx, sender = _nav_ctx(lambda: _status("Acihaut"))
    sender_holder.append(sender)
    ctx.compass_reader = _MissThenDotReader(sender_holder)
    ok = STEP_REGISTRY["nav_panel_target"](ctx, settle_s=0.0, pin_hold_s=4.0)
    assert ok is True
    # Each macro run does exactly one pin tap-down; a row advance would add a
    # walk UI_Down. Two macro runs (one dot-miss, then success) at row 0 means
    # UI_Down count == pin count (2), never more.
    pins = len([h for a, h in sender.holds if a == "UI_Up" and h >= 1.0])
    assert sender.events.count("UI_Down") == pins


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

def test_arrival_lock_then_orbit_order_and_gates():
    proc = load_procedures(PROC_DIR)["arrival"]
    actions = [s.action for s in proc.steps]
    # The arrival star is now locked TWICE: an EARLY bare lock during the scoop
    # idle (no skip_to), and the BOUNDED get-around gate right before the orbit.
    # Pin the BOUNDED gate (the one carrying skip_to), not the first
    # nav_panel_target by name — actions.index() would return the early lock,
    # whose successor is scoop_refuel.
    i = next(idx for idx, s in enumerate(proc.steps)
             if s.action == "nav_panel_target" and s.skip_to == "target_next_route")
    # 3b orient DROPPED (operator, 2026-06-07 phase-1: post-refuel pose
    # engages the assist fine — "I think it's moot")
    assert actions[i:i + 2] == ["nav_panel_target", "sc_assist_orbit"]
    # LOCK-SPEED redesign (2026-06-07): nav_panel_target is NOW non-required
    # with a forward skip — a star NOT found in the bounded scan (far) vaults
    # the get-around block to target_next_route instead of retrying/aborting. A
    # wrong lock still never flows on (the identity check INSIDE the step never
    # returns a beacon as True — see test_nav_panel_target_fails_closed_*).
    assert proc.steps[i].required is False
    assert proc.steps[i].skip_to == "target_next_route"
    # the scan is BOUNDED tight so a far star returns False fast (no grind)
    assert proc.steps[i].params.get("max_rows") == 3
    # target_next_route is the FIRST step past the get-around block (so the skip
    # vaults exactly sc_assist_orbit + its wait, nothing more)
    assert actions[i + 1] == "sc_assist_orbit"
    assert "target_next_route" in actions[i + 1:]
    # retry_from anchors retries at the scoop (re-establishes pose + lock both —
    # arrival.toml [on_required_fail] retry_from = "scoop_refuel").
    assert proc.on_required_fail.retry_from == "scoop_refuel"


def test_nav_panel_target_pins_cursor_with_held_up_never_taps():
    """Operator-tested (2026-06-07): the panel cursor persists across jumps
    (opened at ~row 10 one system after the first refuel); a HELD up-key
    saturates at the top but TAPS at the top WRAP to the bottom. The pin is
    the operator's exact sequence — tap DOWN once, then HOLD up — and must
    never regress into a tap burst."""
    ctx, sender = _nav_ctx(lambda: _status("Acihaut"))
    ok = STEP_REGISTRY["nav_panel_target"](ctx, settle_s=0.0, pin_hold_s=4.0)
    assert ok is True
    first_select = sender.events.index("UI_Select")
    pre = sender.events[1:first_select]
    assert pre == ["UI_Down", "UI_Up"]             # tap down, then up ONCE
    assert ("UI_Up", 4.0) in sender.holds          # ...and that up was HELD


def test_row_walk_happens_after_the_pin():
    """When the verified lock needs a scroll (beacon on row 0), the walk's
    UI_Down lands AFTER the held-up pin, from a true row-0 origin."""
    sender_holder = []
    def status():
        scrolled = [a for a, h in sender_holder[0].holds
                    if a == "UI_Up" and h >= 1.0]
        # wrong body until the SECOND macro run (one pin-hold per run)
        return _status("Acihaut" if len(scrolled) >= 2
                       else "$MULTIPLAYER_SCENARIO42_TITLE;")
    ctx, sender = _nav_ctx(status)
    sender_holder.append(sender)
    ok = STEP_REGISTRY["nav_panel_target"](ctx, settle_s=0.0, pin_hold_s=4.0)
    assert ok is True
    # second macro run: ... UI_Up(held) -> UI_Down (the row-1 walk) -> select
    last_up = len(sender.events) - 1 - sender.events[::-1].index("UI_Up")
    walk = sender.events[last_up + 1:]
    assert walk.count("UI_Down") == 1
    assert walk.index("UI_Down") < walk.index("UI_Select")
