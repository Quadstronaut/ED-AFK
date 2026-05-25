"""
Interactive setup wizards.

Two flows:

  setup_frontier_creds(commanders, ...):
      For each commander without a `.frontier-<slug>.cred` file under the
      real %LOCALAPPDATA% (sandboxie creds don't count — DPAPI binds to
      user+machine context), launch MinEdLauncher interactively so the
      user can log in. MEL writes the .cred file itself on successful auth;
      we verify it appeared before declaring success.

  calibrate_menu(commander, is_owner, ...):
      Walk the user through the main menu and record how many Down arrow
      presses are needed to reach Private Group from Continue, and the
      group from the top of the group list. Returns a MenuCalibration
      that the caller serializes into config.toml.

Both wizards take injectable `input_fn` and `print_fn` so tests drive
them deterministically. Production callers pass `input` and `print`.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..config import LauncherConfig
from .launcher import (
    LaunchSpec,
    LauncherError,
    MinEdLauncher,
    cred_path_for,
    has_cred,
    resolve_profile,
)


def _spawn_kwargs_new_console() -> dict:
    """Windows: spawn MEL in its own console window so it doesn't fight
    the wizard for stdin.

    CRITICAL: do NOT also pass `stdin=DEVNULL`. CREATE_NEW_CONSOLE
    already gives the child its OWN stdin connected to the new console
    window — overriding with DEVNULL leaves MEL with no readable stdin,
    so its `Username (Email):` prompt fails its blocking read instantly
    and the process exits within ~100ms (window 'flashes briefly')
    before the user can type anything.

    On non-Windows we can't spawn a separate console window the same
    way; the cleanest fallback is to attach to the inherited terminal
    and accept that the dev would have to handle the race manually
    (this codebase is Windows-only in practice anyway)."""
    if os.name == "nt":
        # No stdin override — let MEL get its new console's stdin.
        return {"creationflags": getattr(subprocess, "CREATE_NEW_CONSOLE", 0)}
    return {}


# --- cred setup ----------------------------------------------------------


@dataclass
class CredSetupResult:
    succeeded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)   # already had cred
    failed: list[str] = field(default_factory=list)    # login didn't complete


def setup_frontier_creds(
    commanders: list[str],
    *,
    launcher_cfg: LauncherConfig,
    mel: MinEdLauncher,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    cred_poll_timeout_s: float = 600.0,
) -> CredSetupResult:
    """Interactive .cred onboarding loop.

    For each commander: skip if cred exists, otherwise launch MEL with
    /frontier <slug> (no /autoquit so MEL stays alive while user logs in),
    wait for the user to confirm completion, then verify the cred file
    appeared on disk.
    """
    result = CredSetupResult()

    for commander in commanders:
        # Unknown commander → log + skip-as-failed.
        try:
            profile = resolve_profile(commander, launcher_cfg)
        except LauncherError as exc:
            print_fn(f"[{commander}] cannot resolve profile: {exc}")
            result.failed.append(commander)
            continue

        if has_cred(profile.profile_slug):
            print_fn(f"[{commander}] cred already present at {profile.cred_path} — skipping")
            result.skipped.append(commander)
            continue

        print_fn("")
        print_fn(f"=== {commander} (profile {profile.profile_slug}) ===")
        print_fn("A NEW MinEdLauncher window will open in its own console. Log in")
        print_fn("there with this commander's Frontier email + password. After login")
        print_fn("succeeds and the .cred file is written, MEL will either start the")
        print_fn("game (let it) or you can close the MEL window manually.")
        print_fn(f"Expected cred file: {profile.cred_path}")
        input_fn("Press Enter HERE (this terminal) to spawn the MEL window...")

        spec = LaunchSpec(
            commander=commander,
            profile_slug=profile.profile_slug,
            auth="frontier",
            product=launcher_cfg.default_product,
            autorun=False,        # let user click through manually
            autoquit=False,       # keep MEL alive so user can log in
            skip_install_prompt=launcher_cfg.skip_install_prompt,
            dryrun=False,
        )
        try:
            # CRITICAL: spawn MEL in its own console window. Without this,
            # MEL's stdin+stdout share the wizard's terminal and the two
            # input() loops eat each other's keystrokes.
            proc = mel.launch(spec, **_spawn_kwargs_new_console())
        except Exception as exc:  # noqa: BLE001
            print_fn(f"[{commander}] launch failed: {exc}")
            result.failed.append(commander)
            continue

        print_fn("")
        print_fn("MEL spawned. Log in there. Polling for the .cred file...")
        ok = _poll_for_cred(profile.profile_slug, proc=proc,
                            timeout_s=cred_poll_timeout_s, print_fn=print_fn,
                            clock=clock, sleep=sleep)

        if ok:
            print_fn(f"[{commander}] OK — cred written to {profile.cred_path}")
            result.succeeded.append(commander)
        else:
            print_fn(f"[{commander}] FAILED — no .cred at {profile.cred_path}")
            result.failed.append(commander)

    return result


def _poll_for_cred(
    profile_slug: str,
    *,
    proc,
    timeout_s: float,
    poll_interval_s: float = 1.0,
    print_fn: Callable[[str], None] = print,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Wait for the .cred file to appear OR for MEL to exit OR for timeout.

    Avoids the input()-vs-MEL-stdin race entirely: the wizard never reads
    from stdin while MEL is alive. Returns True if cred lands, False
    otherwise.
    """
    deadline = clock() + timeout_s
    last_announce = 0.0
    while clock() < deadline:
        if has_cred(profile_slug):
            return True
        # If MEL exited cleanly and cred is still missing, login failed
        # or was aborted — no point waiting longer.
        rc = proc.poll() if hasattr(proc, "poll") else None
        if rc is not None:
            sleep(0.5)  # let the file system catch up if cred was just written
            return has_cred(profile_slug)
        # Heartbeat every ~15s so the user knows we're still waiting.
        if clock() - last_announce > 15.0:
            remaining = int(deadline - clock())
            print_fn(f"  ... still waiting for {cred_path_for(profile_slug).name} "
                     f"({remaining}s left, MEL still running)")
            last_announce = clock()
        sleep(poll_interval_s)
    print_fn(f"  TIMEOUT after {timeout_s}s — closing MEL")
    try:
        proc.kill()
    except Exception:
        pass
    return has_cred(profile_slug)


# --- menu calibration ----------------------------------------------------


_VALID_CONTINUE_KEYS = ("space", "enter")
_VALID_DIRECTIONS = ("down", "up", "left", "right")


@dataclass
class MenuCalibration:
    """Per-commander main-menu press counts + nav directions.

    The PG mode-select screen and the saved-group list can each be either
    horizontal (right/left arrows) or vertical (down/up arrows) depending
    on ED's current UI version — capture both so the navigator dispatches
    the correct scancode.
    """

    continue_key: str
    pg_nav_direction: str           # "down" | "up" | "left" | "right"
    pg_nav_count: int
    group_nav_direction: Optional[str] = None   # None for group owner
    group_nav_count: Optional[int] = None       # None for group owner

    def to_dict(self) -> dict:
        """Shape suitable for MenuNavConfig.calibration[commander]."""
        d = {
            "continue_key": self.continue_key,
            "pg_nav_direction": self.pg_nav_direction,
            "pg_nav_count": self.pg_nav_count,
        }
        if self.group_nav_direction is not None and self.group_nav_count is not None:
            d["group_nav_direction"] = self.group_nav_direction
            d["group_nav_count"] = self.group_nav_count
        return d


def _prompt_continue_key(input_fn, print_fn) -> str:
    while True:
        ans = input_fn("Continue key (space/enter) [space]: ").strip().lower() or "space"
        if ans in _VALID_CONTINUE_KEYS:
            return ans
        print_fn(f"  Invalid: {ans!r}. Must be one of {_VALID_CONTINUE_KEYS}.")


def _prompt_direction(input_fn, print_fn, label: str, default: str = "down") -> str:
    while True:
        raw = input_fn(f"{label} [{default}]: ").strip().lower() or default
        if raw in _VALID_DIRECTIONS:
            return raw
        print_fn(f"  Invalid: {raw!r}. Must be one of {_VALID_DIRECTIONS}.")


def _prompt_nonneg_int(input_fn, print_fn, label: str) -> int:
    while True:
        ans = input_fn(f"{label}: ").strip()
        try:
            n = int(ans)
            if n < 0:
                raise ValueError("negative")
            return n
        except ValueError:
            print_fn(f"  Invalid: {ans!r}. Must be a non-negative integer.")


def calibrate_menu(
    *,
    commander: str,
    is_owner: bool,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> MenuCalibration:
    """Walk the user through the main menu and capture press counts + arrow
    direction for each step.

    The user is expected to have ED running on the main menu while
    answering. For each question they manually press arrow keys until the
    target item is highlighted, then report how many presses + which
    direction (the PG mode-select screen is HORIZONTAL in ED 2026 — uses
    Right arrow; the saved-groups list is VERTICAL — uses Down).
    """
    print_fn(f"--- Menu calibration for {commander} ---")
    print_fn("Open Elite Dangerous and bring it to the main menu before answering.")
    print_fn("")

    continue_key = _prompt_continue_key(input_fn, print_fn)

    print_fn("")
    print_fn("Press the Continue key on ED to reach the game-mode select screen")
    print_fn("(Open / Solo / Private Group / CQC). Now figure out which arrow key")
    print_fn("moves the highlight toward 'Private Group' (in 2026 ED this is")
    print_fn("usually Right arrow), and how many presses it takes.")
    pg_dir = _prompt_direction(
        input_fn, print_fn, "Arrow direction to reach Private Group", default="right"
    )
    pg_count = _prompt_nonneg_int(
        input_fn, print_fn, f"Press {pg_dir.upper()} N times to reach Private Group; N ="
    )

    group_dir: Optional[str] = None
    group_count: Optional[int] = None
    if not is_owner:
        print_fn("")
        print_fn(f"Press Enter on Private Group. The saved-groups list appears.")
        print_fn(f"Figure out which arrow key navigates the list (usually Down) and")
        print_fn(f"how many presses to reach 'Quadstronaut'.")
        group_dir = _prompt_direction(
            input_fn, print_fn, "Arrow direction to reach Quadstronaut in the list",
            default="down",
        )
        group_count = _prompt_nonneg_int(
            input_fn, print_fn, f"Press {group_dir.upper()} N times to reach Quadstronaut; N ="
        )

    return MenuCalibration(
        continue_key=continue_key,
        pg_nav_direction=pg_dir,
        pg_nav_count=pg_count,
        group_nav_direction=group_dir,
        group_nav_count=group_count,
    )
