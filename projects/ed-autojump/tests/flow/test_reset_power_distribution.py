"""Focus-gate + behavior tests for the restored pip steps (reset_power_distribution,
pips_engines). Pure-Python, no game/CV/network. Restores the 2026-06-08 (b047155)
coverage after the pip-rip; the two steps were re-added 2026-06-18 (operator: pips
back, wireable via pips.md). The FOCUS GATE: a flaky nav-panel close can leave the
left panel open, so each step presses UI_Back to cockpit focus before any arrow tap
and SKIPS (best-effort, return True) rather than fire an arrow into an open panel."""

from types import SimpleNamespace

from ed_core.flow.context import StepContext
from ed_autojump.flow.steps import STEP_REGISTRY
from tests.flow import FakeSender


class _FocusStatus:
    """status_supplier whose gui_focus starts at `focus` and drops to 0 (cockpit)
    after `backs_needed` reads (read 1 = the pre-check in _ensure_cockpit_focus;
    each later read follows one UI_Back press)."""

    def __init__(self, focus, backs_needed=1):
        self.focus = focus
        self.backs = backs_needed
        self.reads = 0

    def __call__(self):
        self.reads += 1
        current = self.focus if self.reads <= self.backs else 0
        return SimpleNamespace(gui_focus=current, in_supercruise=True)


# ---- reset_power_distribution ------------------------------------------------

def test_reset_power_distribution_fires_when_focus_already_zero():
    """Happy path (GuiFocus==0): _ensure_cockpit_focus is a no-op and
    ResetPowerDistribution fires exactly once -- identical to the pre-gate path."""
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _: None,
                      status_supplier=lambda: SimpleNamespace(gui_focus=0))
    assert STEP_REGISTRY["reset_power_distribution"](ctx) is True
    assert s.actions() == ["ResetPowerDistribution"]


def test_reset_power_distribution_closes_panel_then_fires():
    """GuiFocus==2 (panel open): UI_Back restores focus, THEN the reset fires --
    UI_Back must come BEFORE the reset tap."""
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _: None,
                      status_supplier=_FocusStatus(focus=2, backs_needed=1))
    assert STEP_REGISTRY["reset_power_distribution"](ctx) is True
    actions = s.actions()
    assert "UI_Back" in actions
    assert "ResetPowerDistribution" in actions
    assert actions.index("UI_Back") < actions.index("ResetPowerDistribution")


def test_reset_power_distribution_skips_when_focus_stuck():
    """Focus never clears: the reset MUST NOT fire (an arrow into a panel mis-
    navigates it). Returns True (best-effort skip), logs PipResetSkippedNoFocus."""
    s = FakeSender()
    logged = []
    ctx = StepContext(sender=s, sleeper=lambda _: None,
                      status_supplier=_FocusStatus(focus=2, backs_needed=99),
                      record=lambda kind, payload: logged.append((kind, payload)))
    assert STEP_REGISTRY["reset_power_distribution"](ctx) is True
    assert "ResetPowerDistribution" not in s.actions()
    assert any(k == "PipResetSkippedNoFocus" for k, _ in logged)


def test_reset_power_distribution_fires_when_status_unwired():
    """status_supplier returns None (no live Status.json): _ensure_cockpit_focus is
    a no-op (legacy blind behavior preserved) and the reset fires."""
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _: None, status_supplier=lambda: None)
    assert STEP_REGISTRY["reset_power_distribution"](ctx) is True
    assert s.actions() == ["ResetPowerDistribution"]


# ---- pips_engines ------------------------------------------------------------

def test_pips_engines_fires_when_focus_already_zero():
    """Happy path: Reset + 4x IncreaseEnginesPower, no UI_Back."""
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _: None,
                      status_supplier=lambda: SimpleNamespace(gui_focus=0))
    assert STEP_REGISTRY["pips_engines"](ctx, presses=4) is True
    assert s.actions() == [
        "ResetPowerDistribution",
        "IncreaseEnginesPower", "IncreaseEnginesPower",
        "IncreaseEnginesPower", "IncreaseEnginesPower",
    ]


def test_pips_engines_closes_panel_then_fires_sequence():
    """GuiFocus==2 (panel open): UI_Back first, THEN the full pip sequence."""
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _: None,
                      status_supplier=_FocusStatus(focus=2, backs_needed=1))
    assert STEP_REGISTRY["pips_engines"](ctx, presses=4) is True
    actions = s.actions()
    assert actions[0] == "UI_Back"
    assert "ResetPowerDistribution" in actions
    assert "IncreaseEnginesPower" in actions
    assert actions.index("UI_Back") < actions.index("ResetPowerDistribution")


def test_pips_engines_skips_sequence_when_focus_stuck():
    """Focus stuck open: NO arrows fire (five into a panel would mis-navigate it).
    Returns True (best-effort), logs PipsEnginesSkippedNoFocus."""
    s = FakeSender()
    logged = []
    ctx = StepContext(sender=s, sleeper=lambda _: None,
                      status_supplier=_FocusStatus(focus=2, backs_needed=99),
                      record=lambda kind, payload: logged.append((kind, payload)))
    assert STEP_REGISTRY["pips_engines"](ctx, presses=4) is True
    assert "ResetPowerDistribution" not in s.actions()
    assert "IncreaseEnginesPower" not in s.actions()
    assert any(k == "PipsEnginesSkippedNoFocus" for k, _ in logged)
