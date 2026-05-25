"""
End-to-end launch flow: dryrun → spawn → wait main-menu → navigate → wait LoadGame.

Tests use fake MEL + FakeTail + fake navigator + injected clock/sleep so
the whole flow runs in milliseconds without touching disk or wall-clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pytest

from ed_autojump.config import LauncherConfig, MenuNavConfig
from ed_autojump.journal.events import parse_event
from ed_autojump.launcher import DryrunOutcome, DryrunResult, LaunchSpec
from ed_autojump.launcher.flow import FlowStatus, LaunchFlowResult, launch_and_enter_game


# --- fakes ---------------------------------------------------------------


class _FakeMel:
    def __init__(self, *, dryrun_outcome: DryrunOutcome = DryrunOutcome.OK):
        self._dryrun_outcome = dryrun_outcome
        self.dryrun_called = 0
        self.launch_called = 0

    def dryrun(self, spec, *, timeout_s: float):
        self.dryrun_called += 1
        return DryrunResult(outcome=self._dryrun_outcome, returncode=0)

    def launch(self, spec, **_):
        self.launch_called += 1
        class _P:
            def wait(self, timeout=None): return 0
            def kill(self): pass
        return _P()


class _FakeTail:
    def __init__(self, batches):
        self._batches = list(batches)

    def step(self):
        if not self._batches:
            return []
        return self._batches.pop(0)


class _FakeNavigator:
    def __init__(self, *, raises=None):
        self.calls: list[str] = []
        self._raises = raises

    def navigate(self, *, commander: str):
        self.calls.append(commander)
        if self._raises is not None:
            raise self._raises


def _fileheader():
    return parse_event({"timestamp": "2026-05-23T00:00:00Z",
                        "event": "Fileheader", "part": 1,
                        "language": "English/UK", "Odyssey": True,
                        "gameversion": "4.3.3.0", "build": "r0"})


def _audio_present():
    """Audio probe that always reports a non-silent peak.

    Suitable for tests that only exercise the no-nav single-burst path
    or that pass menu_navigator=None. For tests with the full dance
    (menu_navigator+enabled), use _AudioDance/dance.probe instead."""
    return lambda: 0.5


def _audio_silent():
    """Audio probe that always reports silence (session exists but quiet)."""
    return lambda: 0.0


def _no_focus():
    """Stub focus function — does nothing, returns True."""
    return lambda: True


def _loadgame(group="Quadstronaut", commander="Duvrazh"):
    return parse_event({"timestamp": "2026-05-23T00:01:00Z",
                        "event": "LoadGame", "Commander": commander,
                        "GameMode": "Group", "Group": group})


class _Clock:
    def __init__(self):
        self.t = 0.0
    def now(self):
        return self.t
    def sleep(self, dt):
        self.t += dt


def _spec(commander="Duvrazh", profile_slug="account1"):
    return LaunchSpec(
        commander=commander, profile_slug=profile_slug,
        auth="frontier", product="edo",
    )


def _make_cfg_l():
    """LauncherConfig with all delays zeroed for fast tests."""
    c = LauncherConfig()
    c.post_fileheader_wait_s = 0.0
    c.menu_audio_sustain_s = 0.0  # fire on first audio sample in tests
    return c


# --- happy path ----------------------------------------------------------


def test_full_flow_succeeds_when_nav_enabled():
    mel = _FakeMel(dryrun_outcome=DryrunOutcome.OK)
    nav = _FakeNavigator()
    tail = _FakeTail([[_loadgame()]])
    clock = _Clock()
    cfg_nav = MenuNavConfig(enabled=True, post_main_menu_buffer_s=0.0)
    cfg_l = _make_cfg_l()
    r = launch_and_enter_game(
        spec=_spec(), mel=mel, tail=tail,
        menu_navigator=nav, menu_nav_cfg=cfg_nav, launcher_cfg=cfg_l,
        expected_group="Quadstronaut", expected_commander="Duvrazh",
        pre_flight_dryrun=True,
        audio_probe=_audio_present(), focus_fn=_no_focus(),
        clock=clock.now, sleep=clock.sleep, print_fn=lambda _: None,
    )
    assert r.status == FlowStatus.OK
    assert mel.dryrun_called == 1
    assert mel.launch_called == 1
    assert nav.calls == ["Duvrazh"]
    assert r.load_game_event.commander == "Duvrazh"


def test_default_focus_waits_for_window_with_launch_timeout(monkeypatch):
    """Regression: when no focus_fn is injected, the pre-nav focus must wait
    for ED's window using launch_timeout_s — not focus_ed_window's 5s default.
    Even though by nav time ED is up, using the generous timeout keeps focus
    robust (observed earlier: 5s focus timing out with 'could not focus ED')."""
    import ed_autojump.launcher.flow as flow_mod

    seen_timeouts = []

    def fake_focus(*, timeout_s=5.0, **_kw):
        seen_timeouts.append(timeout_s)
        return True

    monkeypatch.setattr(flow_mod, "focus_ed_window", fake_focus)

    mel = _FakeMel(dryrun_outcome=DryrunOutcome.OK)
    nav = _FakeNavigator()
    tail = _FakeTail([[_loadgame()]])
    clock = _Clock()
    cfg_l = _make_cfg_l()
    cfg_l.launch_timeout_s = 99.0
    r = launch_and_enter_game(
        spec=_spec(), mel=mel, tail=tail,
        menu_navigator=nav,
        menu_nav_cfg=MenuNavConfig(enabled=True, post_main_menu_buffer_s=0.0),
        launcher_cfg=cfg_l,
        expected_group="Quadstronaut", expected_commander="Duvrazh",
        pre_flight_dryrun=False,
        audio_probe=_audio_present(),  # focus_fn intentionally NOT injected
        clock=clock.now, sleep=clock.sleep, print_fn=lambda _: None,
    )
    assert r.status == FlowStatus.OK
    assert seen_timeouts, "default focus path was never exercised"
    assert all(t == 99.0 for t in seen_timeouts), \
        f"focus must wait launch_timeout_s, got {seen_timeouts}"


def test_brief_audio_blip_does_not_count_as_menu_ready():
    """The cutscene is NOT skipped; its ~0.1s audio blip must not satisfy the
    menu gate. A probe that spikes once then goes silent → MAIN_MENU_TIMEOUT,
    proving the flow requires SUSTAINED audio (menu_audio_sustain_s)."""
    mel = _FakeMel(dryrun_outcome=DryrunOutcome.OK)
    nav = _FakeNavigator()
    tail = _FakeTail([])
    clock = _Clock()

    seq = iter([0.5])  # one blip, then silence forever
    def blip_then_silence():
        try:
            return next(seq)
        except StopIteration:
            return 0.0

    cfg_l = _make_cfg_l()
    cfg_l.launch_timeout_s = 5.0
    cfg_l.menu_audio_sustain_s = 2.0
    r = launch_and_enter_game(
        spec=_spec(), mel=mel, tail=tail,
        menu_navigator=nav,
        menu_nav_cfg=MenuNavConfig(enabled=True, post_main_menu_buffer_s=0.0),
        launcher_cfg=cfg_l,
        pre_flight_dryrun=False,
        audio_probe=blip_then_silence, focus_fn=_no_focus(),
        clock=clock.now, sleep=clock.sleep, print_fn=lambda _: None,
    )
    assert r.status == FlowStatus.MAIN_MENU_TIMEOUT
    assert nav.calls == []  # never navigated — the blip was rejected


# --- dryrun failure paths -----------------------------------------------


def test_dryrun_hung_aborts_before_launch():
    """Stale .cred caught at dryrun — the actual launch must NOT happen,
    or we'd hang the whole overnight session waiting for stdin."""
    mel = _FakeMel(dryrun_outcome=DryrunOutcome.HUNG_TIMEOUT)
    nav = _FakeNavigator()
    tail = _FakeTail([])
    clock = _Clock()
    cfg_nav = MenuNavConfig(enabled=True)
    cfg_l = LauncherConfig()
    r = launch_and_enter_game(
        spec=_spec(), mel=mel, tail=tail,
        menu_navigator=nav, menu_nav_cfg=cfg_nav, launcher_cfg=cfg_l,
        pre_flight_dryrun=True,
        clock=clock.now, sleep=clock.sleep, print_fn=lambda _: None,
    )
    assert r.status == FlowStatus.DRYRUN_FAILED
    assert mel.launch_called == 0
    assert nav.calls == []


def test_dryrun_auth_failed_aborts_before_launch():
    mel = _FakeMel(dryrun_outcome=DryrunOutcome.AUTH_FAILED)
    tail = _FakeTail([])
    clock = _Clock()
    r = launch_and_enter_game(
        spec=_spec(), mel=mel, tail=tail,
        menu_navigator=None,
        menu_nav_cfg=MenuNavConfig(), launcher_cfg=LauncherConfig(),
        pre_flight_dryrun=True,
        clock=clock.now, sleep=clock.sleep, print_fn=lambda _: None,
    )
    assert r.status == FlowStatus.DRYRUN_FAILED
    assert mel.launch_called == 0


def test_can_skip_dryrun_check():
    """When user passes --no-dryrun, launch proceeds even if a dryrun
    would have failed (useful when MEL has issues but the actual launch
    works, or for one-off debugging)."""
    mel = _FakeMel(dryrun_outcome=DryrunOutcome.HUNG_TIMEOUT)
    tail = _FakeTail([[_fileheader()]])
    clock = _Clock()
    r = launch_and_enter_game(
        spec=_spec(), mel=mel, tail=tail,
        menu_navigator=None,
        menu_nav_cfg=MenuNavConfig(post_main_menu_buffer_s=0.0),
        launcher_cfg=LauncherConfig(),
        pre_flight_dryrun=False,  # skip
        clock=clock.now, sleep=clock.sleep, print_fn=lambda _: None,
    )
    assert mel.dryrun_called == 0
    assert mel.launch_called == 1


# --- main-menu wait paths ------------------------------------------------


def test_main_menu_timeout_returns_status():
    """When ED never produces audio (silent or process never opens session)
    the gate times out with MAIN_MENU_TIMEOUT."""
    mel = _FakeMel(dryrun_outcome=DryrunOutcome.OK)
    tail = _FakeTail([])
    clock = _Clock()
    cfg_l = LauncherConfig()
    cfg_l.launch_timeout_s = 1.0
    r = launch_and_enter_game(
        spec=_spec(), mel=mel, tail=tail,
        menu_navigator=None,
        menu_nav_cfg=MenuNavConfig(), launcher_cfg=cfg_l,
        pre_flight_dryrun=False,
        audio_probe=_audio_silent(), focus_fn=_no_focus(),
        clock=clock.now, sleep=clock.sleep, print_fn=lambda _: None,
    )
    assert r.status == FlowStatus.MAIN_MENU_TIMEOUT


def test_nav_disabled_returns_main_menu_ready_for_operator_handoff():
    """When menu_nav.enabled=False the bot still launches + waits for menu
    but stops there, leaving the human to navigate manually. Status:
    MAIN_MENU_READY signals 'launcher did its job; over to you'."""
    mel = _FakeMel(dryrun_outcome=DryrunOutcome.OK)
    tail = _FakeTail([[_fileheader()]])
    clock = _Clock()
    r = launch_and_enter_game(
        spec=_spec(), mel=mel, tail=tail,
        menu_navigator=None,
        menu_nav_cfg=MenuNavConfig(enabled=False, post_main_menu_buffer_s=0.0),
        launcher_cfg=_make_cfg_l(),
        pre_flight_dryrun=False,
        audio_probe=_audio_present(), focus_fn=_no_focus(),
        clock=clock.now, sleep=clock.sleep, print_fn=lambda _: None,
    )
    assert r.status == FlowStatus.MAIN_MENU_READY


def test_no_nav_path_waits_for_sustained_audio_then_hands_off():
    """No navigator → wait for sustained menu audio, then hand off at the menu
    (no cutscene skip, no key sending)."""
    mel = _FakeMel(dryrun_outcome=DryrunOutcome.OK)
    tail = _FakeTail([])
    clock = _Clock()
    r = launch_and_enter_game(
        spec=_spec(), mel=mel, tail=tail,
        menu_navigator=None,
        menu_nav_cfg=MenuNavConfig(enabled=False, post_main_menu_buffer_s=0.0),
        launcher_cfg=_make_cfg_l(),
        pre_flight_dryrun=False,
        audio_probe=_audio_present(), focus_fn=_no_focus(),
        clock=clock.now, sleep=clock.sleep, print_fn=lambda _: None,
    )
    assert r.status == FlowStatus.MAIN_MENU_READY


# --- nav + LoadGame paths -----------------------------------------------


def test_nav_failure_bubbles_up_as_nav_failed():
    from ed_autojump.launcher.menu_nav import MenuNavError
    mel = _FakeMel(dryrun_outcome=DryrunOutcome.OK)
    nav = _FakeNavigator(raises=MenuNavError("no calibration"))
    tail = _FakeTail([])
    clock = _Clock()
    r = launch_and_enter_game(
        spec=_spec(), mel=mel, tail=tail,
        menu_navigator=nav,
        menu_nav_cfg=MenuNavConfig(enabled=True, post_main_menu_buffer_s=0.0),
        launcher_cfg=_make_cfg_l(),
        pre_flight_dryrun=False,
        audio_probe=_audio_present(), focus_fn=_no_focus(),
        clock=clock.now, sleep=clock.sleep, print_fn=lambda _: None,
    )
    assert r.status == FlowStatus.NAV_FAILED


def test_load_game_mismatch_returns_distinct_status():
    """Wrong group joined → DON'T let the AFK loop start in someone else's session."""
    mel = _FakeMel(dryrun_outcome=DryrunOutcome.OK)
    nav = _FakeNavigator()
    tail = _FakeTail([[_loadgame(group="SomeoneElse")]])
    clock = _Clock()
    r = launch_and_enter_game(
        spec=_spec(), mel=mel, tail=tail,
        menu_navigator=nav,
        menu_nav_cfg=MenuNavConfig(enabled=True, post_main_menu_buffer_s=0.0),
        launcher_cfg=_make_cfg_l(),
        expected_group="Quadstronaut",
        pre_flight_dryrun=False,
        audio_probe=_audio_present(), focus_fn=_no_focus(),
        clock=clock.now, sleep=clock.sleep, print_fn=lambda _: None,
    )
    assert r.status == FlowStatus.LOAD_GAME_MISMATCH


def test_load_game_timeout_returns_status():
    """Audio dance succeeds + nav runs, but LoadGame never appears in journal."""
    mel = _FakeMel(dryrun_outcome=DryrunOutcome.OK)
    nav = _FakeNavigator()
    tail = _FakeTail([])  # no LoadGame ever
    clock = _Clock()
    cfg_nav = MenuNavConfig(enabled=True, post_main_menu_buffer_s=0.0,
                            load_game_timeout_s=1.0)
    r = launch_and_enter_game(
        spec=_spec(), mel=mel, tail=tail,
        menu_navigator=nav,
        menu_nav_cfg=cfg_nav, launcher_cfg=_make_cfg_l(),
        pre_flight_dryrun=False,
        audio_probe=_audio_present(), focus_fn=_no_focus(),
        clock=clock.now, sleep=clock.sleep, print_fn=lambda _: None,
    )
    assert r.status == FlowStatus.LOAD_GAME_TIMEOUT
