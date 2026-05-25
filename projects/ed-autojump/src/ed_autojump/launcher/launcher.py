"""
Launcher core: invoke `rfvgyhn/min-ed-launcher` to start Elite Dangerous
with a chosen commander profile, product, and authentication mode.

`build_args` turns a `LaunchSpec` into the MinEdLauncher.exe argv list per
the rfvgyhn README. `MinEdLauncher` wraps spawning and provides a `dryrun`
pre-flight that catches the Console.ReadLine() hang failure mode (stale
.cred → infinite blocking read on stdin), which would otherwise kill an
unattended overnight session.

We intentionally keep credential handling 100% out of process — MEL owns
the DPAPI-encrypted .cred files and we never touch them. The wizard
(launcher/wizard.py) just invokes MEL interactively so the user logs in
and MEL writes the .cred itself.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from ..config import LauncherConfig


VALID_AUTH = ("frontier", "steam")
VALID_PRODUCT = ("edo", "edh4")


class LauncherError(RuntimeError):
    """Anything we refuse to do — unknown commander, bad auth value, missing exe."""


# --- detection -----------------------------------------------------------


@dataclass
class LauncherDetection:
    found: bool
    path: Optional[Path]


# Common install locations to search when neither config.launcher.mel_path
# nor PATH yields a hit. Matches the rfvgyhn defaults + the user's known
# Steam library layout.
_KNOWN_INSTALL_DIRS = (
    r"G:\SteamLibrary\steamapps\common\Elite Dangerous",
    r"C:\Program Files (x86)\Steam\steamapps\common\Elite Dangerous",
    r"C:\Program Files\Frontier\EDLaunch",
)


def detect_min_ed_launcher(
    explicit_path: Optional[Path] = None,
    *,
    extra_dirs: Iterable[Path] = (),
) -> LauncherDetection:
    """Resolve `MinEdLauncher.exe` from explicit path → PATH → known dirs."""
    if explicit_path is not None and explicit_path.is_file():
        return LauncherDetection(found=True, path=explicit_path)
    on_path = (
        shutil.which("MinEdLauncher.exe")
        or shutil.which("MinEdLauncher")
        or shutil.which("min-ed-launcher.exe")
        or shutil.which("min-ed-launcher")
    )
    if on_path:
        return LauncherDetection(found=True, path=Path(on_path))
    for d in (*extra_dirs, *(Path(x) for x in _KNOWN_INSTALL_DIRS)):
        candidate = d / "MinEdLauncher.exe"
        if candidate.is_file():
            return LauncherDetection(found=True, path=candidate)
    return LauncherDetection(found=False, path=None)


# --- profile registry / cred path ----------------------------------------


@dataclass
class Profile:
    """A commander → on-disk MEL profile slug mapping."""

    commander: str
    profile_slug: str
    cred_path: Path


def cred_path_for(profile_slug: str) -> Path:
    """Return the DPAPI-encrypted .cred path MEL uses for a given slug.

    Layout: `%LOCALAPPDATA%\\min-ed-launcher\\.frontier-<slug>.cred`.
    The wizard checks existence before launch; this function is pure
    (doesn't touch disk) so callers can build paths regardless of whether
    the file exists yet.
    """
    localappdata = os.environ.get("LOCALAPPDATA", "")
    return Path(localappdata) / "min-ed-launcher" / f".frontier-{profile_slug}.cred"


def has_cred(profile_slug: str) -> bool:
    """True if the .cred file exists on disk for this profile slug."""
    return cred_path_for(profile_slug).is_file()


def resolve_profile(commander: str, cfg: LauncherConfig) -> Profile:
    """Look up the slug for a commander; raise LauncherError if unknown."""
    if commander not in cfg.profiles:
        raise LauncherError(
            f"unknown commander {commander!r}; known: {sorted(cfg.profiles)}"
        )
    slug = cfg.profiles[commander]
    return Profile(commander=commander, profile_slug=slug, cred_path=cred_path_for(slug))


# --- spec + args ---------------------------------------------------------


@dataclass
class LaunchSpec:
    """One invocation's worth of MEL config.

    Built by the CLI layer from LauncherConfig + per-command overrides
    (--commander, --auth, --product, etc.). The spec is what `build_args`
    consumes; MinEdLauncher takes a spec and spawns.
    """

    commander: str
    profile_slug: str
    auth: str          # "frontier" | "steam"
    product: str       # "edo" | "edh4"
    autorun: bool = True
    autoquit: bool = True
    skip_install_prompt: bool = True
    dryrun: bool = False


def build_args(spec: LaunchSpec) -> list[str]:
    """Turn a LaunchSpec into MinEdLauncher.exe argv (without the exe path).

    Raises LauncherError on invalid auth/product so the CLI fails fast
    instead of silently dispatching nonsense to MEL.
    """
    if spec.auth not in VALID_AUTH:
        raise LauncherError(f"invalid auth {spec.auth!r}; valid: {VALID_AUTH}")
    if spec.product not in VALID_PRODUCT:
        raise LauncherError(f"invalid product {spec.product!r}; valid: {VALID_PRODUCT}")

    args: list[str] = []
    if spec.auth == "frontier":
        args += ["/frontier", spec.profile_slug]
    # Steam: nothing — relying on EDLaunch.exe /Steam shortcut or MEL's
    # Steam-via-runclient logic. The bot doesn't drive Steam SSO directly.

    if spec.product == "edo":
        args.append("/edo")
    elif spec.product == "edh4":
        args.append("/edh4")

    if spec.autorun:
        args.append("/autorun")
    if spec.autoquit:
        args.append("/autoquit")
    if spec.skip_install_prompt:
        args.append("/skipInstallPrompt")
    if spec.dryrun:
        args.append("/dryrun")
    return args


# --- dryrun pre-flight ---------------------------------------------------


class DryrunOutcome(Enum):
    OK = "ok"                  # exited 0 within timeout — auth + config look healthy
    AUTH_FAILED = "auth_failed"  # exited non-zero — bad creds, server, or config
    HUNG_TIMEOUT = "hung_timeout"  # didn't exit in time — likely Console.ReadLine() block


@dataclass
class DryrunResult:
    outcome: DryrunOutcome
    returncode: Optional[int]
    stderr: str = ""


# --- spawner -------------------------------------------------------------


class MinEdLauncher:
    """Wraps MinEdLauncher.exe — spawn for launch, spawn-and-wait for dryrun.

    `_popen` is injected so tests can swap subprocess.Popen for a fake.
    """

    def __init__(
        self,
        exe_path: Path,
        *,
        _popen: Callable[..., Any] = subprocess.Popen,
    ):
        if not Path(exe_path).is_file():
            raise LauncherError(f"MinEdLauncher.exe not found at {exe_path}")
        self.exe_path = Path(exe_path)
        self._popen = _popen

    def _argv(self, spec: LaunchSpec) -> list[str]:
        return [str(self.exe_path), *build_args(spec)]

    def launch(self, spec: LaunchSpec, **popen_kwargs):
        """Spawn MEL non-blocking. Returns the Popen handle.

        Caller owns the process lifecycle (waitpid, kill, etc.). For the
        bot's overnight use case, MEL exits very quickly after handing off
        to EliteDangerous64.exe (because we pass /autoquit), so the Popen
        handle goes idle within seconds.
        """
        return self._popen(self._argv(spec), **popen_kwargs)

    def dryrun(self, spec: LaunchSpec, *, timeout_s: float) -> DryrunResult:
        """Pre-flight: force /dryrun, spawn, wait with timeout.

        The crucial property: if MEL hangs (stale .cred → blocking stdin
        read for credentials), we MUST detect that and kill, otherwise the
        bot freezes for the rest of the night. Without this, the user
        wakes up to a launcher prompt and zero exploration progress.
        """
        forced = LaunchSpec(
            commander=spec.commander, profile_slug=spec.profile_slug,
            auth=spec.auth, product=spec.product,
            autorun=spec.autorun, autoquit=spec.autoquit,
            skip_install_prompt=spec.skip_install_prompt,
            dryrun=True,
        )
        proc = self._popen(
            self._argv(forced),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            rc = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            return DryrunResult(outcome=DryrunOutcome.HUNG_TIMEOUT, returncode=None,
                                stderr="dryrun did not exit within timeout — likely blocked on Console.ReadLine() (stale .cred)")
        if rc == 0:
            return DryrunResult(outcome=DryrunOutcome.OK, returncode=0)
        return DryrunResult(outcome=DryrunOutcome.AUTH_FAILED, returncode=rc,
                            stderr=f"dryrun exited with code {rc}")
