"""
Main-menu navigator — drives ED's main menu from "Continue" to in-game.

Flow:
    Continue → Game-Mode menu → Private Group → (select group) → Launch

The select-group step is skipped for the group OWNER (Quadstronaut) — they
land directly in their own lobby after entering Private Group mode.
Members (Duvrazh, Bistronaut, Tristronaut) navigate to "Quadstronaut" in
the saved-groups list using arrow keys.

Calibration (the per-commander press counts) is read from MenuNavConfig.
Until a commander has calibration recorded, `navigate()` raises so we
never silently launch into the wrong session.

Sender + sleep are injected so the navigator is fully testable without
SendInput or wall-clock sleep.
"""

from __future__ import annotations

from typing import Callable, Protocol

from ..config import MenuNavConfig


# DirectInput Set 1 scancodes for the keys we send. The arrow keys need
# the extended (0xE0) prefix; Enter / Space / Escape don't.
SPACE_SCANCODE = 0x39
ENTER_SCANCODE = 0x1C
ESCAPE_SCANCODE = 0x01
DOWN_ARROW_SCANCODE = 0x50
UP_ARROW_SCANCODE = 0x48
LEFT_ARROW_SCANCODE = 0x4B
RIGHT_ARROW_SCANCODE = 0x4D

# Map calibration direction strings → scancode. Used by the navigator
# when the mode-select / group-select screen is horizontal vs vertical.
_DIRECTION_SCANCODE = {
    "down": DOWN_ARROW_SCANCODE,
    "up": UP_ARROW_SCANCODE,
    "left": LEFT_ARROW_SCANCODE,
    "right": RIGHT_ARROW_SCANCODE,
}
VALID_DIRECTIONS = tuple(_DIRECTION_SCANCODE.keys())


class MenuNavError(RuntimeError):
    """Anything that prevents safe navigation — disabled, uncalibrated, etc."""


class _SenderProtocol(Protocol):
    """Minimal sender interface — DirectInputSender.press_raw matches."""

    def press_raw(self, scancode: int, *, extended: bool = False, hold: float = 0.05): ...


class MenuNavigator:
    """Stateless driver — every call to `navigate()` runs the full sequence.

    Construction takes the sender + config + sleep callable; navigation
    decisions all come from `config.calibration[commander]`. The
    group-owner branch is decided by comparing commander to
    `config.group_owner_commander`.
    """

    def __init__(
        self,
        *,
        sender: _SenderProtocol,
        config: MenuNavConfig,
        sleep: Callable[[float], None],
    ):
        self.sender = sender
        self.config = config
        self.sleep = sleep

    # ---- public API ---------------------------------------------------

    def navigate(self, *, commander: str) -> None:
        """Drive the menu for `commander`. Raises if disabled or uncalibrated."""
        if not self.config.enabled:
            raise MenuNavError(
                "menu_nav is disabled in config — run `ed-autojump calibrate-menu` "
                "then set [menu_nav].enabled = true"
            )
        cal = self.config.calibration.get(commander)
        if cal is None:
            raise MenuNavError(
                f"no calibration for commander {commander!r} — run "
                f"`ed-autojump calibrate-menu --commander {commander}`"
            )
        is_owner = (commander == self.config.group_owner_commander)
        self._validate_calibration(commander, cal, is_owner=is_owner)

        if self.config.dismiss_dialogs:
            self._dismiss_dialogs()

        self._press_continue(cal)
        self.sleep(float(cal.get("post_continue_wait_s", 0.5)))

        self._navigate_to_private_group(cal)
        self.sleep(float(cal.get("post_pg_wait_s", 0.5)))

        if not is_owner:
            self._select_group_in_list(cal)
            self.sleep(float(cal.get("post_group_wait_s", 0.5)))

        self._press_launch()

    # ---- internals ----------------------------------------------------

    def _validate_calibration(self, commander: str, cal: dict, *, is_owner: bool) -> None:
        # Newer schema uses `pg_nav_direction` + `pg_nav_count`; old
        # schema used `down_to_pg_count` (Down assumed). Accept either.
        has_new_pg = "pg_nav_direction" in cal and "pg_nav_count" in cal
        has_legacy_pg = "down_to_pg_count" in cal
        if not (has_new_pg or has_legacy_pg):
            raise MenuNavError(
                f"calibration for {commander!r} missing keys: "
                f"['pg_nav_direction'+'pg_nav_count' or 'down_to_pg_count']"
            )
        if has_new_pg and cal["pg_nav_direction"] not in VALID_DIRECTIONS:
            raise MenuNavError(
                f"calibration for {commander!r} has invalid pg_nav_direction "
                f"{cal['pg_nav_direction']!r}; valid: {VALID_DIRECTIONS}"
            )
        if "continue_key" not in cal:
            raise MenuNavError(f"calibration for {commander!r} missing 'continue_key'")
        if not is_owner:
            has_new_group = "group_nav_direction" in cal and "group_nav_count" in cal
            has_legacy_group = "down_to_group_in_list" in cal
            if not (has_new_group or has_legacy_group):
                raise MenuNavError(
                    f"calibration for {commander!r} missing keys: "
                    f"['group_nav_direction'+'group_nav_count' or 'down_to_group_in_list']"
                )
            if has_new_group and cal["group_nav_direction"] not in VALID_DIRECTIONS:
                raise MenuNavError(
                    f"calibration for {commander!r} has invalid group_nav_direction "
                    f"{cal['group_nav_direction']!r}; valid: {VALID_DIRECTIONS}"
                )

    def _press(self, scancode: int, *, extended: bool = False) -> None:
        self.sender.press_raw(scancode, extended=extended, hold=0.05)
        delay = self.config.key_delay_ms / 1000.0
        if delay > 0:
            self.sleep(delay)

    def _dismiss_dialogs(self) -> None:
        """One Escape — usually enough to clear patch-notes / cosmetics nag."""
        self._press(ESCAPE_SCANCODE)

    def _press_continue(self, cal: dict) -> None:
        key = cal.get("continue_key", "space").lower()
        if key == "enter":
            self._press(ENTER_SCANCODE)
        else:
            self._press(SPACE_SCANCODE)

    def _navigate_to_private_group(self, cal: dict) -> None:
        # New schema (direction-aware) takes priority over legacy down-only.
        if "pg_nav_direction" in cal and "pg_nav_count" in cal:
            direction = cal["pg_nav_direction"]
            count = int(cal["pg_nav_count"])
        else:
            direction = "down"
            count = int(cal["down_to_pg_count"])
        sc = _DIRECTION_SCANCODE[direction]
        for _ in range(count):
            self._press(sc, extended=True)
        self._press(ENTER_SCANCODE)

    def _select_group_in_list(self, cal: dict) -> None:
        if "group_nav_direction" in cal and "group_nav_count" in cal:
            direction = cal["group_nav_direction"]
            count = int(cal["group_nav_count"])
        else:
            direction = "down"
            count = int(cal["down_to_group_in_list"])
        sc = _DIRECTION_SCANCODE[direction]
        for _ in range(count):
            self._press(sc, extended=True)
        self._press(ENTER_SCANCODE)

    def _press_launch(self) -> None:
        self._press(ENTER_SCANCODE)
