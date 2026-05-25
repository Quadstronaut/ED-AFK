"""
Launcher core — Profile registry, args builder, dryrun pre-flight,
MinEdLauncher spawn wrapper. Subprocess is injected so the tests don't
actually invoke the launcher binary.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import pytest

from ed_autojump.config import LauncherConfig
from ed_autojump.launcher import (
    DryrunOutcome,
    LaunchSpec,
    LauncherError,
    MinEdLauncher,
    Profile,
    build_args,
    cred_path_for,
    detect_min_ed_launcher,
    has_cred,
    resolve_profile,
)


# --- Profile registry / cred path ---------------------------------------


def test_resolve_profile_uses_default_mapping():
    cfg = LauncherConfig()
    p = resolve_profile("Duvrazh", cfg)
    assert isinstance(p, Profile)
    assert p.commander == "Duvrazh"
    assert p.profile_slug == "account1"


def test_resolve_profile_unknown_commander_raises():
    cfg = LauncherConfig()
    with pytest.raises(LauncherError, match="unknown commander"):
        resolve_profile("NotARealCmdr", cfg)


def test_cred_path_for_uses_localappdata_layout(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\Test\AppData\Local")
    p = cred_path_for("account2")
    assert p == Path(r"C:\Users\Test\AppData\Local\min-ed-launcher\.frontier-account2.cred")


def test_has_cred_true_when_file_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    dirp = tmp_path / "min-ed-launcher"
    dirp.mkdir()
    (dirp / ".frontier-account1.cred").write_text("dummy")
    assert has_cred("account1") is True
    assert has_cred("account99") is False


# --- detect_min_ed_launcher ----------------------------------------------


def test_detect_min_ed_launcher_explicit_path(tmp_path):
    exe = tmp_path / "MinEdLauncher.exe"
    exe.write_text("fake")
    d = detect_min_ed_launcher(explicit_path=exe)
    assert d.found is True
    assert d.path == exe


def test_detect_min_ed_launcher_explicit_path_missing(tmp_path):
    d = detect_min_ed_launcher(explicit_path=tmp_path / "nope.exe")
    # Explicit miss falls through to PATH lookup; result depends on env,
    # but the call must not crash and must return a coherent dataclass.
    assert d.found in (True, False)
    if not d.found:
        assert d.path is None


# --- build_args ----------------------------------------------------------


def _spec(**overrides):
    base = dict(
        commander="Duvrazh",
        profile_slug="account1",
        auth="frontier",
        product="edo",
        autorun=True,
        autoquit=True,
        skip_install_prompt=True,
        dryrun=False,
    )
    base.update(overrides)
    return LaunchSpec(**base)


def test_build_args_frontier_includes_profile_slug():
    args = build_args(_spec(auth="frontier", profile_slug="account2"))
    assert "/frontier" in args
    i = args.index("/frontier")
    assert args[i + 1] == "account2"


def test_build_args_steam_excludes_frontier():
    args = build_args(_spec(auth="steam"))
    assert "/frontier" not in args
    # Steam SSO is implicit when MEL is invoked via EDLaunch.exe /Steam;
    # we still want /autoquit etc.
    assert "/autoquit" in args


def test_build_args_product_edo_present():
    assert "/edo" in build_args(_spec(product="edo"))


def test_build_args_product_edh4_present():
    assert "/edh4" in build_args(_spec(product="edh4"))


def test_build_args_autorun_autoquit_skip_install_prompt():
    args = build_args(_spec(autorun=True, autoquit=True, skip_install_prompt=True))
    assert "/autorun" in args
    assert "/autoquit" in args
    assert "/skipInstallPrompt" in args


def test_build_args_no_autorun_when_disabled():
    args = build_args(_spec(autorun=False))
    assert "/autorun" not in args


def test_build_args_dryrun_adds_flag():
    args = build_args(_spec(dryrun=True))
    assert "/dryrun" in args


def test_build_args_invalid_auth_raises():
    with pytest.raises(LauncherError, match="auth"):
        build_args(_spec(auth="bogus"))


def test_build_args_invalid_product_raises():
    with pytest.raises(LauncherError, match="product"):
        build_args(_spec(product="not-a-product"))


# --- MinEdLauncher dryrun (injected subprocess) -------------------------


class _FakePopen:
    """Stand-in for subprocess.Popen. Configurable return-code + hang."""

    def __init__(self, args, *, returncode: int = 0, hang_until=None,
                 stdout=b"", stderr=b"", **_):
        self.args = args
        self._returncode = returncode
        self._hang_until = hang_until  # set to inf to simulate Console.ReadLine() hang
        self.stdout_bytes = stdout
        self.stderr_bytes = stderr
        self._killed = False

    def wait(self, timeout: Optional[float] = None) -> int:
        if self._hang_until is not None and (timeout is None or timeout < self._hang_until):
            import subprocess as _sp
            raise _sp.TimeoutExpired(self.args, timeout or 0.0)
        return self._returncode

    def kill(self) -> None:
        self._killed = True

    def communicate(self, timeout: Optional[float] = None):
        if self._hang_until is not None:
            import subprocess as _sp
            raise _sp.TimeoutExpired(self.args, timeout or 0.0)
        return (self.stdout_bytes, self.stderr_bytes)


def test_dryrun_success_returns_ok(tmp_path):
    exe = tmp_path / "MinEdLauncher.exe"
    exe.write_text("fake")
    captured: dict[str, Any] = {}
    def fake_spawn(args, **kw):
        captured["args"] = args
        return _FakePopen(args, returncode=0)
    mel = MinEdLauncher(exe_path=exe, _popen=fake_spawn)
    r = mel.dryrun(_spec(dryrun=False), timeout_s=2.0)  # dryrun flag forced by wrapper
    assert r.outcome == DryrunOutcome.OK
    assert "/dryrun" in captured["args"]


def test_dryrun_hang_is_caught_and_kills_process(tmp_path):
    """Stale .cred → Console.ReadLine() → process hangs forever.
    The dryrun wrapper must time out and kill instead of hanging the bot."""
    exe = tmp_path / "MinEdLauncher.exe"
    exe.write_text("fake")
    procs: list[_FakePopen] = []
    def fake_spawn(args, **kw):
        p = _FakePopen(args, hang_until=float("inf"))
        procs.append(p)
        return p
    mel = MinEdLauncher(exe_path=exe, _popen=fake_spawn)
    r = mel.dryrun(_spec(), timeout_s=0.1)
    assert r.outcome == DryrunOutcome.HUNG_TIMEOUT
    assert procs[0]._killed is True


def test_dryrun_nonzero_returncode_is_auth_failure(tmp_path):
    exe = tmp_path / "MinEdLauncher.exe"
    exe.write_text("fake")
    def fake_spawn(args, **kw):
        return _FakePopen(args, returncode=2)
    mel = MinEdLauncher(exe_path=exe, _popen=fake_spawn)
    r = mel.dryrun(_spec(), timeout_s=1.0)
    assert r.outcome == DryrunOutcome.AUTH_FAILED


# --- MinEdLauncher launch -----------------------------------------------


def test_launch_spawns_with_correct_args(tmp_path):
    exe = tmp_path / "MinEdLauncher.exe"
    exe.write_text("fake")
    captured = {}
    def fake_spawn(args, **kw):
        captured["args"] = args
        return _FakePopen(args, returncode=0)
    mel = MinEdLauncher(exe_path=exe, _popen=fake_spawn)
    proc = mel.launch(_spec(profile_slug="account3"))
    assert exe.name in captured["args"][0]  # first arg is the exe path
    assert "/frontier" in captured["args"]
    assert "account3" in captured["args"]


def test_launch_raises_if_exe_missing(tmp_path):
    with pytest.raises(LauncherError, match="not found"):
        MinEdLauncher(exe_path=tmp_path / "nope.exe")


@pytest.mark.requires_game
def test_full_launcher_cycle():  # pragma: no cover
    """Spawn min-ed-launcher and watch for clean game exit. Game-required."""
    raise AssertionError("Requires installed min-ed-launcher + game")
