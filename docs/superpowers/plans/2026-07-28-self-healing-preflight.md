# Self-Healing Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new user clones the repo, runs `.\launch.ps1`, and the tool installs or repairs every prerequisite it can — reporting one precise instruction for the few it cannot.

**Architecture:** Extend the existing `ed_core/doctor.py` check registry rather than building a parallel system. Checks stay pure functions over injected inputs returning `CheckResult`. A new `FIXES` dict maps check name → remediation callable; `run_all_checks(..., fix=True)` applies a fix on FAIL and re-runs that check. Two callers share one registry: `launch.ps1` (before Jump) and `ed-autojump doctor --fix`.

**Tech Stack:** Python 3.11+, stdlib only for new code (`dataclasses`, `pathlib`, `subprocess`, `shutil`, `os`), pytest, PowerShell 5.1 for `launch.ps1`.

## Global Constraints

- Target platform is Windows; `launch.ps1` is PowerShell 5.1 (no `&&`, no ternary, no `??`).
- New code adds **no third-party dependencies**. The `config.toml` writer is line-based, not `tomlkit`.
- Every check is a pure function over injected arguments — never reads globals or probes hardware directly. Hardware probing lives in `window_info.py` and is injected as a `WindowInfo`.
- Every fix is idempotent: applying it twice leaves the same state.
- Preflight never prompts. It fixes or it reports.
- Preflight failure blocks the run. No "try anyway" path.
- `CheckResult.status` is one of the exact strings `"PASS"`, `"FAIL"`, `"WARN"`. WARN never blocks.
- Existing check names (`journal_dir`, `sessions_dir`, `binds_preset`, `status_files`, `pydirectinput`, `panic_hotkey`) keep their current names and semantics.
- Resolution independence is **out of scope**. The 1920×1080 SDR borderless lock is gated, not solved.

---

### Task 1: Vision-state checks

Two checks that catch the project's worst silent failure: an uncalibrated or disabled `[vision]` means the bot flies blind and, failing closed, refuses to throttle.

**Files:**
- Modify: `projects/ed-core/src/ed_core/doctor.py`
- Test: `projects/ed-autojump/tests/test_doctor_vision.py`

**Interfaces:**
- Consumes: `Config` from `ed_core.config`; `_pass` / `_fail` helpers already in `doctor.py`.
- Produces: `check_vision_calibrated(cfg) -> CheckResult`, `check_vision_enabled(cfg) -> CheckResult`. Both used by Task 7's runner.

The sentinel for "never calibrated" is `VisionConfig.region == (0, 0, 0, 0)`, documented in `config.py:261`.

- [ ] **Step 1: Write the failing test**

```python
# projects/ed-autojump/tests/test_doctor_vision.py
from ed_core.config import Config
from ed_core.doctor import check_vision_calibrated, check_vision_enabled


def _cfg(region=(0, 0, 0, 0), enabled=False) -> Config:
    cfg = Config()
    cfg.vision.region = region
    cfg.vision.enabled = enabled
    return cfg


def test_uncalibrated_sentinel_fails():
    r = check_vision_calibrated(_cfg(region=(0, 0, 0, 0)))
    assert r.status == "FAIL"
    assert "calibrate-compass" in r.detail


def test_calibrated_region_passes():
    r = check_vision_calibrated(_cfg(region=(100, 200, 300, 300)))
    assert r.status == "PASS"


def test_degenerate_region_fails():
    r = check_vision_calibrated(_cfg(region=(100, 200, 0, 300)))
    assert r.status == "FAIL"


def test_vision_disabled_fails():
    r = check_vision_enabled(_cfg(enabled=False))
    assert r.status == "FAIL"
    assert "throttle" in r.detail


def test_vision_enabled_passes():
    assert check_vision_enabled(_cfg(enabled=True)).status == "PASS"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd projects/ed-autojump && pytest tests/test_doctor_vision.py -v`
Expected: FAIL with `ImportError: cannot import name 'check_vision_calibrated'`

- [ ] **Step 3: Write minimal implementation**

Add to `projects/ed-core/src/ed_core/doctor.py`, after `check_binds_preset`:

```python
def check_vision_calibrated(cfg: Config) -> CheckResult:
    """[vision].region still at the (0,0,0,0) sentinel means never calibrated."""
    region = tuple(cfg.vision.region)
    if region == (0, 0, 0, 0):
        return _fail(
            "vision_calibrated",
            "[vision].region is the uncalibrated sentinel (0,0,0,0) -- be in the "
            "cockpit and run `ed-autojump calibrate-compass --write`",
        )
    if len(region) != 4 or region[2] <= 0 or region[3] <= 0:
        return _fail(
            "vision_calibrated",
            f"[vision].region {region} has non-positive width/height -- recalibrate",
        )
    return _pass("vision_calibrated", f"region={region}")


def check_vision_enabled(cfg: Config) -> CheckResult:
    """Vision off == the bot never orients and, failing closed, never throttles."""
    if not cfg.vision.enabled:
        return _fail(
            "vision_enabled",
            "[vision].enabled is false -- the bot will not orient before jumping "
            "and, failing closed, will refuse to throttle forward",
        )
    return _pass("vision_enabled", "on")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd projects/ed-autojump && pytest tests/test_doctor_vision.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add projects/ed-core/src/ed_core/doctor.py projects/ed-autojump/tests/test_doctor_vision.py
git commit -m "feat(doctor): check vision calibration and enabled state"
```

---

### Task 2: Display-mode probe and check

The CV is pinned to 1920×1080 SDR borderless. Today a mismatch fails silently at CV time. This turns it into a named, actionable block.

**Files:**
- Create: `projects/ed-core/src/ed_core/window_info.py`
- Modify: `projects/ed-core/src/ed_core/doctor.py`
- Test: `projects/ed-autojump/tests/test_window_info.py`, `projects/ed-autojump/tests/test_doctor_display.py`

**Interfaces:**
- Consumes: `CvConfig.target_resolution`, `.require_sdr`, `.require_borderless_windowed` (`config.py:148-153`).
- Produces: `WindowInfo(width, height, borderless, hdr, found)` dataclass and `probe_elite_window() -> WindowInfo | None`; `check_display_mode(cfg, window) -> CheckResult`. Task 7 passes `window` into the runner.

**HDR is best-effort.** Resolution and border style come from `GetWindowRect` / `GetWindowLong`. HDR requires DXGI output inspection, which is out of scope — `hdr` is `None` when undetermined and the check only fails when it is explicitly `True`.

- [ ] **Step 1: Write the failing test**

```python
# projects/ed-autojump/tests/test_window_info.py
from ed_core.window_info import WindowInfo


def test_defaults_are_unknown_not_false():
    w = WindowInfo(width=1920, height=1080, borderless=True)
    assert w.hdr is None
    assert w.found is True
```

```python
# projects/ed-autojump/tests/test_doctor_display.py
from ed_core.config import Config
from ed_core.doctor import check_display_mode
from ed_core.window_info import WindowInfo

GOOD = WindowInfo(width=1920, height=1080, borderless=True, hdr=False)


def test_missing_window_warns_not_fails():
    r = check_display_mode(Config(), None)
    assert r.status == "WARN"


def test_correct_display_passes():
    assert check_display_mode(Config(), GOOD).status == "PASS"


def test_wrong_resolution_names_both_values():
    w = WindowInfo(width=2560, height=1440, borderless=True, hdr=False)
    r = check_display_mode(Config(), w)
    assert r.status == "FAIL"
    assert "2560x1440" in r.detail and "1920x1080" in r.detail


def test_hdr_on_fails():
    w = WindowInfo(width=1920, height=1080, borderless=True, hdr=True)
    r = check_display_mode(Config(), w)
    assert r.status == "FAIL"
    assert "HDR" in r.detail


def test_hdr_unknown_does_not_fail():
    w = WindowInfo(width=1920, height=1080, borderless=True, hdr=None)
    assert check_display_mode(Config(), w).status == "PASS"


def test_not_borderless_fails():
    w = WindowInfo(width=1920, height=1080, borderless=False, hdr=False)
    assert check_display_mode(Config(), w).status == "FAIL"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd projects/ed-autojump && pytest tests/test_window_info.py tests/test_doctor_display.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ed_core.window_info'`

- [ ] **Step 3: Write minimal implementation**

```python
# projects/ed-core/src/ed_core/window_info.py
"""Read the live Elite Dangerous window geometry and style.

Kept separate from doctor.py so every check stays a pure function over an
injected WindowInfo -- the whole display matrix is then testable with no
display attached.
"""

from __future__ import annotations

from dataclasses import dataclass

WINDOW_TITLE = "Elite - Dangerous (CLIENT)"
_WS_BORDER = 0x00800000
_WS_THICKFRAME = 0x00040000
_GWL_STYLE = -16


@dataclass(frozen=True)
class WindowInfo:
    width: int
    height: int
    borderless: bool
    hdr: "bool | None" = None   # None == could not determine; never assume False
    found: bool = True


def probe_elite_window() -> "WindowInfo | None":
    """Best-effort live probe. Returns None when the window is not present."""
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:       # noqa: BLE001 -- non-Windows
        return None

    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, WINDOW_TITLE)
    if not hwnd:
        return None

    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    style = user32.GetWindowLongW(hwnd, _GWL_STYLE)
    borderless = not bool(style & (_WS_BORDER | _WS_THICKFRAME))
    return WindowInfo(
        width=rect.right - rect.left,
        height=rect.bottom - rect.top,
        borderless=borderless,
        hdr=None,
        found=True,
    )
```

Add to `doctor.py`:

```python
def check_display_mode(cfg: Config, window: "WindowInfo | None") -> CheckResult:
    """The CV is calibrated for one display mode; anything else reads garbage."""
    if window is None or not window.found:
        return _warn(
            "display_mode",
            "Elite Dangerous window not found -- start the game, then re-run",
        )
    want_w, want_h = cfg.cv.target_resolution
    problems = []
    if (window.width, window.height) != (want_w, want_h):
        problems.append(
            f"resolution is {window.width}x{window.height}, needs {want_w}x{want_h}"
        )
    if cfg.cv.require_sdr and window.hdr is True:
        problems.append("HDR is on, needs SDR")
    if cfg.cv.require_borderless_windowed and not window.borderless:
        problems.append("window is not borderless windowed")
    if problems:
        return _fail(
            "display_mode",
            "; ".join(problems) + " -- fix in Options > Graphics, then re-run",
        )
    return _pass("display_mode", f"{window.width}x{window.height} borderless")
```

Add the import at the top of `doctor.py`:

```python
from .window_info import WindowInfo
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd projects/ed-autojump && pytest tests/test_window_info.py tests/test_doctor_display.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add projects/ed-core/src/ed_core/window_info.py projects/ed-core/src/ed_core/doctor.py projects/ed-autojump/tests/test_window_info.py projects/ed-autojump/tests/test_doctor_display.py
git commit -m "feat(doctor): gate on display mode with observed values"
```

---

### Task 3: Binds-selected check

Selecting the ED-AFK preset happens in ED's own UI and cannot be automated — but `StartPreset.start` records which preset is live, so it can be verified instead of assumed.

**Files:**
- Modify: `projects/ed-core/src/ed_core/doctor.py`
- Test: `projects/ed-autojump/tests/test_doctor_binds_selected.py`

**Interfaces:**
- Consumes: `BindsConfig.preset_name` (default `"ED-AFK"`, `config.py:135`).
- Produces: `check_binds_selected(start_preset_path, expected) -> CheckResult`.

**Documented assumption:** ED 4.x writes the active preset name to `StartPreset.start`, repeating it on several lines. The check compares the **first non-empty line**, stripped. If a future ED build changes that format the check degrades to WARN rather than blocking — encoded in the test below.

- [ ] **Step 1: Write the failing test**

```python
# projects/ed-autojump/tests/test_doctor_binds_selected.py
from ed_core.doctor import check_binds_selected


def test_missing_file_warns(tmp_path):
    r = check_binds_selected(tmp_path / "StartPreset.start", "ED-AFK")
    assert r.status == "WARN"


def test_matching_preset_passes(tmp_path):
    p = tmp_path / "StartPreset.start"
    p.write_text("ED-AFK\n", encoding="utf-8")
    assert check_binds_selected(p, "ED-AFK").status == "PASS"


def test_repeated_lines_form_passes(tmp_path):
    p = tmp_path / "StartPreset.start"
    p.write_text("ED-AFK\nED-AFK\nED-AFK\n", encoding="utf-8")
    assert check_binds_selected(p, "ED-AFK").status == "PASS"


def test_other_preset_fails_and_names_it(tmp_path):
    p = tmp_path / "StartPreset.start"
    p.write_text("Custom\n", encoding="utf-8")
    r = check_binds_selected(p, "ED-AFK")
    assert r.status == "FAIL"
    assert "Custom" in r.detail
    assert "Options > Controls" in r.detail


def test_empty_file_warns(tmp_path):
    p = tmp_path / "StartPreset.start"
    p.write_text("\n\n", encoding="utf-8")
    assert check_binds_selected(p, "ED-AFK").status == "WARN"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd projects/ed-autojump && pytest tests/test_doctor_binds_selected.py -v`
Expected: FAIL with `ImportError: cannot import name 'check_binds_selected'`

- [ ] **Step 3: Write minimal implementation**

```python
def check_binds_selected(start_preset_path: Path, expected: str) -> CheckResult:
    """Verify the ED-AFK preset is the ACTIVE one in game.

    Installing the file is not the same as selecting it; ED records the live
    preset in StartPreset.start. Format assumption: the preset name on the
    first non-empty line (4.x repeats it on several). Anything unreadable or
    empty degrades to WARN so a format change never hard-blocks a run.
    """
    if not start_preset_path.is_file():
        return _warn(
            "binds_selected",
            f"{start_preset_path.name} not found -- ED has not written a preset yet",
        )
    try:
        raw = start_preset_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return _warn("binds_selected", f"cannot read {start_preset_path}: {exc}")
    active = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")
    if not active:
        return _warn("binds_selected", f"{start_preset_path.name} is empty")
    if active != expected:
        return _fail(
            "binds_selected",
            f'active preset is "{active}", needs "{expected}" -- select it in '
            f"Options > Controls, then re-run",
        )
    return _pass("binds_selected", expected)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd projects/ed-autojump && pytest tests/test_doctor_binds_selected.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add projects/ed-core/src/ed_core/doctor.py projects/ed-autojump/tests/test_doctor_binds_selected.py
git commit -m "feat(doctor): verify the ED-AFK preset is actually selected"
```

---

### Task 4: OCR engine check and default flip to WinRT

`config.toml` ships `ocr_engine = "tesseract"`, which needs a native binary, even though `ed_vision/ocr_winrt.py` needs none. Flipping the default deletes a prerequisite outright.

**Files:**
- Modify: `projects/ed-core/src/ed_core/config.py:153`
- Modify: `projects/ed-autojump/config.toml:56`
- Modify: `projects/ed-core/src/ed_core/doctor.py`
- Test: `projects/ed-autojump/tests/test_doctor_ocr.py`

**Interfaces:**
- Consumes: `CvConfig.ocr_engine`.
- Produces: `check_ocr_engine(cfg, which=shutil.which) -> CheckResult`. `which` is injected so the test never depends on the host PATH.

- [ ] **Step 1: Write the failing test**

```python
# projects/ed-autojump/tests/test_doctor_ocr.py
from ed_core.config import Config
from ed_core.doctor import check_ocr_engine


def _cfg(engine: str) -> Config:
    cfg = Config()
    cfg.cv.ocr_engine = engine
    return cfg


def test_default_is_winrt():
    assert Config().cv.ocr_engine == "winrt"


def test_tesseract_without_binary_fails_and_suggests_winrt():
    r = check_ocr_engine(_cfg("tesseract"), which=lambda _n: None)
    assert r.status == "FAIL"
    assert "winrt" in r.detail


def test_tesseract_with_binary_passes():
    r = check_ocr_engine(_cfg("tesseract"), which=lambda _n: r"C:\tesseract.exe")
    assert r.status == "PASS"


def test_unknown_engine_warns():
    assert check_ocr_engine(_cfg("magic"), which=lambda _n: None).status == "WARN"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd projects/ed-autojump && pytest tests/test_doctor_ocr.py -v`
Expected: FAIL — `test_default_is_winrt` asserts `"tesseract" == "winrt"`, and `check_ocr_engine` does not exist.

- [ ] **Step 3: Write minimal implementation**

In `config.py:153` change the default:

```python
    ocr_engine: str = "winrt"   # WinRT needs no native binary; tesseract is a fallback
```

In `projects/ed-autojump/config.toml:56` change:

```toml
ocr_engine = "winrt"
```

Add to `doctor.py`:

```python
def check_ocr_engine(cfg: Config, which=None) -> CheckResult:
    """WinRT needs no native install; tesseract needs a binary on PATH."""
    import shutil
    if which is None:
        which = shutil.which
    engine = (cfg.cv.ocr_engine or "").strip().lower()
    if engine == "winrt":
        try:
            import ed_vision.ocr_winrt  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            return _warn("ocr_engine", f"winrt selected but not importable: {exc}")
        return _pass("ocr_engine", "winrt (no native binary required)")
    if engine == "tesseract":
        if which("tesseract") is None:
            return _fail(
                "ocr_engine",
                "config selects tesseract but no tesseract binary is on PATH -- "
                'set [cv].ocr_engine = "winrt", which needs no native install',
            )
        return _pass("ocr_engine", "tesseract binary found")
    return _warn("ocr_engine", f"unknown ocr_engine {engine!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd projects/ed-autojump && pytest tests/test_doctor_ocr.py tests/test_config.py -v`
Expected: all passed. If `tests/test_config.py` asserts the old default, update that assertion to `"winrt"` in the same commit.

- [ ] **Step 5: Commit**

```bash
git add projects/ed-core/src/ed_core/config.py projects/ed-core/src/ed_core/doctor.py projects/ed-autojump/config.toml projects/ed-autojump/tests/test_doctor_ocr.py projects/ed-autojump/tests/test_config.py
git commit -m "feat: default OCR to WinRT and check engine availability"
```

---

### Task 5: Atomic `[vision]` table writer

The dependency-free config writer that kills the copy-paste step. Line-based on purpose: it touches only the keys it owns and leaves every comment, ordering and unrelated key byte-identical.

**Files:**
- Create: `projects/ed-core/src/ed_core/config_writer.py`
- Test: `projects/ed-autojump/tests/test_config_writer.py`

**Interfaces:**
- Produces: `render_vision_values(region, radius, capture_backend) -> dict[str, str]`, `update_vision_table(text, values) -> str`, `write_config_atomic(path, text) -> None`. Tasks 6 and 8 consume all three.

- [ ] **Step 1: Write the failing test**

```python
# projects/ed-autojump/tests/test_config_writer.py
from ed_core.config_writer import (
    render_vision_values,
    update_vision_table,
    write_config_atomic,
)

SAMPLE = """\
[ship]
expected_ship = "mandalay"

[vision]
# keep this comment
enabled = false
align_tol = 0.20
region = [0, 0, 0, 0]

[safety]
hull_panic_threshold = 0.70
"""


def test_updates_owned_keys_only():
    out = update_vision_table(SAMPLE, render_vision_values((10, 20, 30, 40), 15.0, "gdi"))
    assert "enabled = true" in out
    assert "region = [10, 20, 30, 40]" in out
    assert "compass_radius = 15.0" in out


def test_preserves_comments_and_foreign_keys():
    out = update_vision_table(SAMPLE, render_vision_values((10, 20, 30, 40), 15.0, "gdi"))
    assert "# keep this comment" in out
    assert "align_tol = 0.20" in out
    assert 'expected_ship = "mandalay"' in out
    assert "hull_panic_threshold = 0.70" in out


def test_does_not_leak_into_next_table():
    out = update_vision_table(SAMPLE, render_vision_values((1, 2, 3, 4), 5.0, "gdi"))
    safety = out.split("[safety]", 1)[1]
    assert "region" not in safety
    assert "enabled" not in safety


def test_appends_table_when_absent():
    out = update_vision_table("[ship]\nx = 1\n", render_vision_values((1, 2, 3, 4), 5.0, "gdi"))
    assert "[vision]" in out
    assert "region = [1, 2, 3, 4]" in out


def test_idempotent():
    vals = render_vision_values((10, 20, 30, 40), 15.0, "gdi")
    once = update_vision_table(SAMPLE, vals)
    assert update_vision_table(once, vals) == once


def test_partial_update_leaves_other_owned_keys(tmp_path):
    out = update_vision_table(SAMPLE, {"enabled": "true"})
    assert "enabled = true" in out
    assert "region = [0, 0, 0, 0]" in out


def test_atomic_write_makes_backup(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(SAMPLE, encoding="utf-8")
    write_config_atomic(p, "new = 1\n")
    assert p.read_text(encoding="utf-8") == "new = 1\n"
    assert (tmp_path / "config.toml.bak").read_text(encoding="utf-8") == SAMPLE
    assert not (tmp_path / "config.toml.tmp").exists()


def test_atomic_write_creates_new_file(tmp_path):
    p = tmp_path / "config.toml"
    write_config_atomic(p, "a = 1\n")
    assert p.read_text(encoding="utf-8") == "a = 1\n"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd projects/ed-autojump && pytest tests/test_config_writer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ed_core.config_writer'`

- [ ] **Step 3: Write minimal implementation**

```python
# projects/ed-core/src/ed_core/config_writer.py
"""Surgical, dependency-free edits to config.toml.

Line-based rather than a TOML round-trip: we own exactly five keys inside the
[vision] table and must leave every comment, blank line, ordering choice and
unrelated key byte-identical. tomllib is read-only and tomlkit would be a new
dependency for one table.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

VISION_KEYS = ("enabled", "backend", "capture_backend", "region", "compass_radius")


def render_vision_values(
    region: "tuple[int, int, int, int]", radius: float, capture_backend: str
) -> "dict[str, str]":
    """TOML-literal right-hand sides for the keys calibration owns."""
    x, y, w, h = region
    return {
        "enabled": "true",
        "backend": '"cyan"',
        "capture_backend": f'"{capture_backend}"',
        "region": f"[{x}, {y}, {w}, {h}]",
        "compass_radius": str(float(radius)),
    }


def update_vision_table(text: str, values: "dict[str, str]") -> str:
    """Replace `values` inside [vision]; append the table if it is absent."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == "[vision]"), None)
    if start is None:
        block = ["", "[vision]"] + [f"{k} = {v}" for k, v in values.items()]
        return "\n".join(lines + block) + "\n"

    end = len(lines)
    for j in range(start + 1, len(lines)):
        s = lines[j].strip()
        if s.startswith("[") and s.endswith("]"):
            end = j
            break

    seen = set()
    for j in range(start + 1, end):
        s = lines[j].strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key = s.split("=", 1)[0].strip()
        if key in values:
            lines[j] = f"{key} = {values[key]}"
            seen.add(key)

    missing = [f"{k} = {v}" for k, v in values.items() if k not in seen]
    if missing:
        at = end
        while at > start + 1 and not lines[at - 1].strip():
            at -= 1
        lines[at:at] = missing
    return "\n".join(lines) + "\n"


def write_config_atomic(path: Path, text: str) -> None:
    """Write via temp + os.replace, keeping a .bak of the previous contents."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    os.replace(tmp, path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd projects/ed-autojump && pytest tests/test_config_writer.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add projects/ed-core/src/ed_core/config_writer.py projects/ed-autojump/tests/test_config_writer.py
git commit -m "feat(config): atomic surgical writer for the [vision] table"
```

---

### Task 6: `calibrate-compass --write`

Wire the writer into the calibration command so the block lands in `config.toml` instead of on the terminal.

**Files:**
- Modify: `projects/ed-autojump/src/ed_autojump/cli.py:216-228` (parser), `:1072-1140` (`cmd_calibrate_compass`)
- Test: `projects/ed-autojump/tests/test_calibrate_write.py`

**Interfaces:**
- Consumes: `render_vision_values`, `update_vision_table`, `write_config_atomic` from Task 5.
- Produces: `apply_compass_calibration(config_path, region, radius, capture_backend) -> None` — a seam in `cli.py` that Task 8's fix also calls, so the write path is tested without a screen grab.

- [ ] **Step 1: Write the failing test**

```python
# projects/ed-autojump/tests/test_calibrate_write.py
from ed_autojump.cli import apply_compass_calibration

BASE = "[vision]\nenabled = false\nregion = [0, 0, 0, 0]\n"


def test_writes_region_and_enables(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(BASE, encoding="utf-8")
    apply_compass_calibration(p, (11, 22, 33, 44), 17.5, "gdi")
    out = p.read_text(encoding="utf-8")
    assert "region = [11, 22, 33, 44]" in out
    assert "enabled = true" in out
    assert "compass_radius = 17.5" in out


def test_keeps_a_backup(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(BASE, encoding="utf-8")
    apply_compass_calibration(p, (1, 2, 3, 4), 5.0, "gdi")
    assert (tmp_path / "config.toml.bak").read_text(encoding="utf-8") == BASE


def test_idempotent(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(BASE, encoding="utf-8")
    apply_compass_calibration(p, (1, 2, 3, 4), 5.0, "gdi")
    first = p.read_text(encoding="utf-8")
    apply_compass_calibration(p, (1, 2, 3, 4), 5.0, "gdi")
    assert p.read_text(encoding="utf-8") == first
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd projects/ed-autojump && pytest tests/test_calibrate_write.py -v`
Expected: FAIL with `ImportError: cannot import name 'apply_compass_calibration'`

- [ ] **Step 3: Write minimal implementation**

Add near the top-level functions of `cli.py`:

```python
def apply_compass_calibration(
    config_path: Path,
    region: "tuple[int, int, int, int]",
    radius: float,
    capture_backend: str,
) -> None:
    """Write a calibrated [vision] block into config.toml, atomically."""
    from ed_core.config_writer import (
        render_vision_values,
        update_vision_table,
        write_config_atomic,
    )
    text = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    values = render_vision_values(region, radius, capture_backend)
    write_config_atomic(config_path, update_vision_table(text, values))
```

At `cli.py:216` the `calibrate-compass` parser is **anonymous** (`sub.add_parser(...)`
with no assignment), and the nearby `sub_cal` variable belongs to
**`calibrate-menu`** — do not add the flag to that one. Give the compass parser a
name and hang `--write` off it:

```python
    # ed-autojump calibrate-compass — auto-locate the nav compass on screen.
    sub_calcompass = sub.add_parser(
        "calibrate-compass",
        help="auto-locate the nav compass and write a [vision] region block",
    )
    sub_calcompass.add_argument(
        "--write",
        action="store_true",
        help="write the [vision] block into config.toml instead of printing it",
    )
```

In `cmd_calibrate_compass`, replace the print block that starts at
`print("=== Compass located -- add this to your config.toml ===")` with:

```python
    if getattr(args, "write", False):
        apply_compass_calibration(args.config, (x, y, w, h), float(r), cfg.vision.capture_backend)
        print("")
        print(f"=== Compass located -- wrote [vision] into {args.config} ===")
        print(f"    region = [{x}, {y}, {w}, {h}]  compass_radius = {float(r)}")
        print(f"    previous contents saved to {args.config.name}.bak")
        return 0

    print("")
    print("=== Compass located -- add this to your config.toml ===")
    print("")
    print("[vision]")
    print("enabled = true")
    print('backend = "cyan"')
    print(f'capture_backend = "{cfg.vision.capture_backend}"')
    print(f"region = [{x}, {y}, {w}, {h}]")
    print(f"compass_radius = {r}")
    print("")
    print("(or re-run with --write to have this written for you)")
    return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd projects/ed-autojump && pytest tests/test_calibrate_write.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add projects/ed-autojump/src/ed_autojump/cli.py projects/ed-autojump/tests/test_calibrate_write.py
git commit -m "feat(cli): calibrate-compass --write ends the copy-paste step"
```

---

### Task 7: Fix registry and fix-aware runner

The core mechanism. Note the invariant in Step 3 — it is the subtle bug this design would otherwise ship.

**Files:**
- Modify: `projects/ed-core/src/ed_core/doctor.py`
- Test: `projects/ed-autojump/tests/test_doctor_runner.py`

**Interfaces:**
- Consumes: every `check_*` from Tasks 1–4.
- Produces: `FixContext`, `Fix` alias, `FIXES` dict, `CheckResult.fixed` field, and
  `run_all_checks(cfg, binds_path=None, *, window=None, start_preset_path=None, fix=False, fix_ctx=None) -> list[CheckResult]`. Tasks 8–10 consume these.

**INVARIANT — a config-writing fix must update both the file and the in-memory `ctx.cfg`, and `fix_ctx.cfg` must be the same object passed as `cfg`.** Checks read the in-memory `Config`; a fix that only writes the file leaves the re-check reading stale state and reporting a false failure. Step 1 tests this directly.

- [ ] **Step 1: Write the failing test**

```python
# projects/ed-autojump/tests/test_doctor_runner.py
import pytest
from ed_core.doctor import CheckResult, FixContext, _fail, _pass, _run_with_fixes


def _mk(seq):
    """Return a thunk yielding each status in seq on successive calls."""
    box = {"i": 0}

    def thunk():
        s = seq[min(box["i"], len(seq) - 1)]
        box["i"] += 1
        return CheckResult("t", s, "detail")

    return thunk


def test_passing_check_is_not_fixed():
    calls = []
    out = _run_with_fixes(
        [("t", _mk(["PASS"]))], {"t": lambda _c: calls.append(1) or True}, None, fix=True
    )
    assert out[0].status == "PASS"
    assert calls == []


def test_fail_then_fix_then_pass_marks_fixed():
    out = _run_with_fixes([("t", _mk(["FAIL", "PASS"]))], {"t": lambda _c: True}, None, fix=True)
    assert out[0].status == "PASS"
    assert out[0].fixed is True


def test_fix_that_does_not_help_is_reported():
    out = _run_with_fixes([("t", _mk(["FAIL", "FAIL"]))], {"t": lambda _c: True}, None, fix=True)
    assert out[0].status == "FAIL"
    assert "still failing" in out[0].detail


def test_raising_fix_is_isolated_and_reported():
    def boom(_ctx):
        raise RuntimeError("nope")

    out = _run_with_fixes(
        [("t", _mk(["FAIL"])), ("u", _mk(["PASS"]))], {"t": boom}, None, fix=True
    )
    assert out[0].status == "FAIL"
    assert "nope" in out[0].detail
    assert out[1].status == "PASS"   # later checks still run


def test_fixless_failure_is_reported_not_skipped():
    out = _run_with_fixes([("t", _mk(["FAIL"]))], {}, None, fix=True)
    assert out[0].status == "FAIL"
    assert out[0].fixed is False


def test_fix_false_never_invokes_fixes():
    calls = []
    out = _run_with_fixes(
        [("t", _mk(["FAIL"]))], {"t": lambda _c: calls.append(1) or True}, None, fix=False
    )
    assert out[0].status == "FAIL"
    assert calls == []


def test_config_fix_must_mutate_in_memory_cfg():
    """The invariant: a fix that only writes the file leaves the re-check stale."""
    from ed_core.config import Config
    cfg = Config()
    cfg.vision.enabled = False
    from ed_core.doctor import check_vision_enabled

    def fix(ctx):
        ctx.cfg.vision.enabled = True     # MUST mutate memory, not only disk
        return True

    ctx = FixContext(cfg=cfg, config_path=None, venv_python=None, binds_path=None)
    out = _run_with_fixes(
        [("vision_enabled", lambda: check_vision_enabled(cfg))],
        {"vision_enabled": fix},
        ctx,
        fix=True,
    )
    assert out[0].status == "PASS"
    assert out[0].fixed is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd projects/ed-autojump && pytest tests/test_doctor_runner.py -v`
Expected: FAIL with `ImportError: cannot import name 'FixContext'`

- [ ] **Step 3: Write minimal implementation**

In `doctor.py`, extend the dataclass and add the machinery:

```python
@dataclass
class CheckResult:
    name: str
    status: str  # "PASS" | "FAIL" | "WARN"
    detail: str = ""
    fixed: bool = False   # set by the runner when a fix repaired this check


@dataclass
class FixContext:
    """Everything a fix may need, injected so fixes stay testable.

    INVARIANT: `cfg` is the SAME object the checks read. A fix that edits
    config.toml must also mutate this object, or the re-check reads stale
    state and reports a false failure.
    """
    cfg: "Config | None" = None
    config_path: "Path | None" = None
    venv_python: "Path | None" = None
    binds_path: "Path | None" = None


Fix = Callable[[FixContext], bool]


def _run_with_fixes(checks, fixes, ctx, *, fix: bool) -> list[CheckResult]:
    """Walk (name, thunk) pairs; on FAIL with a registered fix, fix and re-run."""
    results: list[CheckResult] = []
    for name, thunk in checks:
        r = thunk()
        if not (fix and r.status == "FAIL" and name in fixes):
            results.append(r)
            continue
        try:
            fixes[name](ctx)
        except Exception as exc:  # noqa: BLE001 -- one bad fix must not abort the pass
            results.append(_fail(name, f"{r.detail} | fix raised: {exc}"))
            continue
        r2 = thunk()
        if r2.status == "FAIL":
            results.append(_fail(name, f"{r2.detail} | fix attempted, still failing"))
        else:
            r2.fixed = True
            results.append(r2)
    return results
```

Then rewrite `run_all_checks` to build named thunks and delegate:

```python
def run_all_checks(
    cfg: Config,
    binds_path: "Path | None" = None,
    *,
    window: "WindowInfo | None" = None,
    start_preset_path: "Path | None" = None,
    fix: bool = False,
    fix_ctx: "FixContext | None" = None,
) -> list[CheckResult]:
    from . import __file__ as pkg_file
    if binds_path is None:
        binds_path = Path(pkg_file).parent / "binds" / "ED-AFK.4.2.binds"
    journal_dir = cfg.paths.journal_dir_expanded()
    sessions_dir = Path(os.environ.get(
        "ED_AFK_SESSIONS_DIR", Path.home() / "ed-afk-sessions",
    ))
    if start_preset_path is None:
        # binds_tool already knows where ED keeps this; reuse it rather than
        # re-deriving the path. Rename the helper to `start_preset_path` (drop
        # the leading underscore) as part of this task and update its callers.
        from .binds_tool import _start_preset_path
        try:
            start_preset_path = _start_preset_path(cfg)
        except Exception:  # noqa: BLE001 -- never let path derivation break the pass
            start_preset_path = Path("StartPreset.start")

    checks = [
        ("journal_dir",       lambda: check_journal_dir_readable(journal_dir)),
        ("sessions_dir",      lambda: check_sessions_dir_writable(sessions_dir)),
        ("binds_preset",      lambda: check_binds_preset(binds_path)),
        ("binds_selected",    lambda: check_binds_selected(start_preset_path, cfg.binds.preset_name)),
        ("status_files",      lambda: check_status_files(journal_dir)),
        ("pydirectinput",     check_pydirectinput),
        ("panic_hotkey",      check_panic_hotkey),
        ("ocr_engine",        lambda: check_ocr_engine(cfg)),
        ("display_mode",      lambda: check_display_mode(cfg, window)),
        ("vision_calibrated", lambda: check_vision_calibrated(cfg)),
        ("vision_enabled",    lambda: check_vision_enabled(cfg)),
    ]
    if fix_ctx is None:
        fix_ctx = FixContext(cfg=cfg, binds_path=binds_path)
    return _run_with_fixes(checks, FIXES, fix_ctx, fix=fix)
```

Add an empty registry for now — Task 8 fills it:

```python
FIXES: "dict[str, Fix]" = {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd projects/ed-autojump && pytest tests/test_doctor_runner.py tests/test_doctor.py -v`
Expected: all passed. `tests/test_doctor.py` must still pass — `run_all_checks(cfg, binds_path)` keeps its positional signature.

- [ ] **Step 5: Commit**

```bash
git add projects/ed-core/src/ed_core/doctor.py projects/ed-autojump/tests/test_doctor_runner.py
git commit -m "feat(doctor): fix registry and fix-aware runner"
```

---

### Task 8: The auto-fixes

**Files:**
- Modify: `projects/ed-core/src/ed_core/doctor.py`
- Test: `projects/ed-autojump/tests/test_doctor_fixes.py`

**Interfaces:**
- Consumes: `FixContext` (Task 7), `update_vision_table` / `write_config_atomic` (Task 5).
- Produces: `fix_vision_enabled`, `fix_ocr_engine`, `fix_pydirectinput`, `fix_panic_hotkey`, `fix_binds_preset`, and a populated `FIXES`.

- [ ] **Step 1: Write the failing test**

```python
# projects/ed-autojump/tests/test_doctor_fixes.py
from ed_core.config import Config
from ed_core.doctor import FIXES, FixContext, fix_ocr_engine, fix_vision_enabled

BASE = "[vision]\nenabled = false\nregion = [1, 2, 3, 4]\n"


def test_registry_covers_the_auto_fixable_checks():
    assert set(FIXES) == {
        "vision_enabled", "ocr_engine", "pydirectinput", "panic_hotkey", "binds_preset",
    }


def test_display_and_binds_selected_are_not_auto_fixable():
    assert "display_mode" not in FIXES
    assert "binds_selected" not in FIXES


def test_fix_vision_enabled_writes_file_and_memory(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(BASE, encoding="utf-8")
    cfg = Config()
    cfg.vision.enabled = False
    assert fix_vision_enabled(FixContext(cfg=cfg, config_path=p)) is True
    assert "enabled = true" in p.read_text(encoding="utf-8")
    assert cfg.vision.enabled is True          # the INVARIANT from Task 7


def test_fix_vision_enabled_is_idempotent(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(BASE, encoding="utf-8")
    cfg = Config()
    ctx = FixContext(cfg=cfg, config_path=p)
    fix_vision_enabled(ctx)
    once = p.read_text(encoding="utf-8")
    fix_vision_enabled(ctx)
    assert p.read_text(encoding="utf-8") == once


def test_fix_ocr_engine_switches_to_winrt(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[cv]\nocr_engine = "tesseract"\n', encoding="utf-8")
    cfg = Config()
    cfg.cv.ocr_engine = "tesseract"
    assert fix_ocr_engine(FixContext(cfg=cfg, config_path=p)) is True
    assert 'ocr_engine = "winrt"' in p.read_text(encoding="utf-8")
    assert cfg.cv.ocr_engine == "winrt"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd projects/ed-autojump && pytest tests/test_doctor_fixes.py -v`
Expected: FAIL with `ImportError: cannot import name 'fix_vision_enabled'`

- [ ] **Step 3: Write minimal implementation**

`fix_ocr_engine` needs a generic table editor. Add this to `config_writer.py` first:

```python
def update_table_key(text: str, table: str, key: str, value: str) -> str:
    """Set one key inside one table, appending the table or key if absent."""
    lines = text.splitlines()
    header = f"[{table}]"
    start = next((i for i, ln in enumerate(lines) if ln.strip() == header), None)
    if start is None:
        return "\n".join(lines + ["", header, f"{key} = {value}"]) + "\n"
    end = len(lines)
    for j in range(start + 1, len(lines)):
        s = lines[j].strip()
        if s.startswith("[") and s.endswith("]"):
            end = j
            break
    for j in range(start + 1, end):
        s = lines[j].strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        if s.split("=", 1)[0].strip() == key:
            lines[j] = f"{key} = {value}"
            return "\n".join(lines) + "\n"
    at = end
    while at > start + 1 and not lines[at - 1].strip():
        at -= 1
    lines[at:at] = [f"{key} = {value}"]
    return "\n".join(lines) + "\n"
```

Then in `doctor.py`:

```python
def _edit_config(ctx: FixContext, editor) -> bool:
    """Read config.toml, apply `editor(text) -> text`, write atomically."""
    from .config_writer import write_config_atomic
    if ctx.config_path is None:
        return False
    path = Path(ctx.config_path)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    write_config_atomic(path, editor(text))
    return True


def fix_vision_enabled(ctx: FixContext) -> bool:
    from .config_writer import update_vision_table
    ok = _edit_config(ctx, lambda t: update_vision_table(t, {"enabled": "true"}))
    if ok and ctx.cfg is not None:
        ctx.cfg.vision.enabled = True      # INVARIANT: memory too, not just disk
    return ok


def fix_ocr_engine(ctx: FixContext) -> bool:
    from .config_writer import update_table_key
    ok = _edit_config(ctx, lambda t: update_table_key(t, "cv", "ocr_engine", '"winrt"'))
    if ok and ctx.cfg is not None:
        ctx.cfg.cv.ocr_engine = "winrt"
    return ok


def _pip(ctx: FixContext, *args: str) -> bool:
    import subprocess
    import sys
    exe = str(ctx.venv_python) if ctx.venv_python else sys.executable
    return subprocess.run([exe, "-m", "pip", *args], check=False).returncode == 0


def fix_pydirectinput(ctx: FixContext) -> bool:
    _pip(ctx, "uninstall", "-y", "pydirectinput")
    return _pip(ctx, "install", "pydirectinput-rgx>=2.0")


def fix_panic_hotkey(ctx: FixContext) -> bool:
    return _pip(ctx, "install", "keyboard")


def fix_binds_preset(ctx: FixContext) -> bool:
    """Copy the bundled preset into ED's bindings dir.

    binds_tool.install_binds_preset takes the CONFIG (not a path) and returns
    the installed Path -- the wrapper exists to keep every fix a
    one-argument Callable[[FixContext], bool].
    """
    from .binds_tool import install_binds_preset
    if ctx.cfg is None:
        return False
    install_binds_preset(ctx.cfg)
    return True


FIXES: "dict[str, Fix]" = {
    "vision_enabled": fix_vision_enabled,
    "ocr_engine": fix_ocr_engine,
    "pydirectinput": fix_pydirectinput,
    "panic_hotkey": fix_panic_hotkey,
    "binds_preset": fix_binds_preset,
}
```

If `binds_tool.install_binds` has a different name or signature, adapt the call and keep the wrapper — `fix_binds_preset` must stay a one-argument `Fix`.

`vision_calibrated` is deliberately **not** in `FIXES`: fixing it requires a live screen grab with the ship in the cockpit, which belongs to `calibrate-compass --write`, not to an unattended fix pass. Preflight reports it with that exact instruction.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd projects/ed-autojump && pytest tests/test_doctor_fixes.py tests/test_config_writer.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add projects/ed-core/src/ed_core/doctor.py projects/ed-core/src/ed_core/config_writer.py projects/ed-autojump/tests/test_doctor_fixes.py
git commit -m "feat(doctor): auto-fixes for config, packages and binds"
```

---

### Task 9: `doctor --fix` and the preflight report

**Files:**
- Modify: `projects/ed-core/src/ed_core/doctor.py` (formatter)
- Modify: `projects/ed-autojump/src/ed_autojump/cli.py:43` (parser), `:1190-1202` (`cmd_doctor`)
- Test: `projects/ed-autojump/tests/test_preflight_format.py`

**Interfaces:**
- Consumes: `CheckResult.fixed` (Task 7).
- Produces: `format_preflight(results) -> str`. Task 10 relies on `cmd_doctor` returning non-zero on any FAIL.

- [ ] **Step 1: Write the failing test**

```python
# projects/ed-autojump/tests/test_preflight_format.py
from ed_core.doctor import CheckResult, format_preflight


def test_all_clear_message():
    out = format_preflight([CheckResult("a", "PASS", "ok")])
    assert "READY" in out


def test_lists_fixed_items():
    out = format_preflight([CheckResult("pydirectinput", "PASS", "ok", fixed=True)])
    assert "fixed:" in out
    assert "pydirectinput" in out


def test_lists_blockers_with_detail():
    out = format_preflight([CheckResult("display_mode", "FAIL", "resolution is 2560x1440")])
    assert "you must do these yourself:" in out
    assert "2560x1440" in out
    assert "BLOCKED" in out


def test_warn_never_blocks():
    out = format_preflight([CheckResult("status_files", "WARN", "no Status.json")])
    assert "READY" in out
    assert "BLOCKED" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd projects/ed-autojump && pytest tests/test_preflight_format.py -v`
Expected: FAIL with `ImportError: cannot import name 'format_preflight'`

- [ ] **Step 3: Write minimal implementation**

Add to `doctor.py`:

```python
def format_preflight(results: Iterable[CheckResult]) -> str:
    """Grouped report: what got fixed, then what the user must do."""
    results = list(results)
    fixed = [r for r in results if r.fixed]
    blocked = [r for r in results if r.status == "FAIL"]
    out: list[str] = []
    if fixed:
        out.append("[preflight] fixed:")
        for r in fixed:
            out.append(f"  - {r.name:18} {r.detail}")
        out.append("")
    if blocked:
        out.append("[preflight] you must do these yourself:")
        for r in blocked:
            out.append(f"  - {r.name:18} {r.detail}")
        out.append("")
        out.append(
            f"[preflight] BLOCKED - {len(blocked)} item(s) need you. "
            "Nothing was sent to the game."
        )
    else:
        out.append("[preflight] READY")
    return "\n".join(out)
```

In `cli.py:43` replace the doctor parser line with:

```python
    sub_doctor = sub.add_parser("doctor", help="check environment + config + binds + EDHM")
    sub_doctor.add_argument(
        "--fix", action="store_true",
        help="repair what can be repaired, then report what you must do yourself",
    )
```

Rewrite `cmd_doctor`:

```python
def cmd_doctor(args) -> int:
    from ed_core.doctor import (
        FixContext, format_preflight, format_results, overall_status, run_all_checks,
    )
    from ed_core.window_info import probe_elite_window

    cfg = load_config(args.config if args.config.is_file() else None)
    binds_path = Path(__file__).parent / "binds" / "ED-AFK.4.2.binds"
    do_fix = bool(getattr(args, "fix", False))
    ctx = FixContext(
        cfg=cfg,
        config_path=args.config,
        venv_python=Path(sys.executable),
        binds_path=binds_path,
    )
    results = run_all_checks(
        cfg, binds_path=binds_path,
        window=probe_elite_window(), fix=do_fix, fix_ctx=ctx,
    )
    print(format_preflight(results) if do_fix else format_results(results))
    return overall_status(results)
```

Ensure `import sys` is present at the top of `cli.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd projects/ed-autojump && pytest tests/test_preflight_format.py -v && python -m ed_autojump.cli doctor`
Expected: 4 passed; `doctor` prints the classic list as before.

- [ ] **Step 5: Commit**

```bash
git add projects/ed-core/src/ed_core/doctor.py projects/ed-autojump/src/ed_autojump/cli.py projects/ed-autojump/tests/test_preflight_format.py
git commit -m "feat(cli): doctor --fix with grouped preflight report"
```

---

### Task 10: `launch.ps1` integration

**Files:**
- Modify: `launch.ps1` (param block; new preflight section before the single-instance guard)

**Interfaces:**
- Consumes: `ed-autojump doctor --fix` exit code from Task 9 (0 = go, non-zero = block).

- [ ] **Step 1: Add the opt-out switch**

In the `param(...)` block, after `[switch]$Force`:

```powershell
    [switch]$NoPreflight,           # skip the self-healing prerequisite pass (debugging only)
```

- [ ] **Step 2: Add the preflight section**

Insert immediately **before** the `# --- single-instance guard` comment block:

```powershell
# --- preflight: repair what we can, block on what we cannot ------------------
# Runs doctor --fix inside the venv: it installs/repairs prerequisites without
# prompting and returns non-zero when something needs the operator (wrong
# display mode, ED-AFK preset not selected in game, uncalibrated compass).
# Deliberately BEFORE the focus countdown so a blocked run never sends a key.
if (-not $NoPreflight) {
    Write-Host ""
    Write-Host "[launch] preflight..."
    & $venvPython -m ed_autojump.cli doctor --fix
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "[launch] preflight blocked the run. Fix the items above and re-run."
        Write-Host "         (override with -NoPreflight if you know what you are doing)"
        exit 1
    }
}
```

- [ ] **Step 3: Document the switch in the help text**

In `Show-FriendlyHelp`, under `THE SEED FLAGS`, after the `-Yes` line:

```
  -NoPreflight       skip the prerequisite check/repair pass (debugging only).
```

- [ ] **Step 4: Verify the script still parses**

Run:
```powershell
$e = $null
[void][System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path .\launch.ps1), [ref]$null, [ref]$e)
if ($e) { $e } else { "clean" }
```
Expected: `clean`

- [ ] **Step 5: Commit**

```bash
git add launch.ps1
git commit -m "feat(launch): run self-healing preflight before Jump"
```

---

### Task 11: Documentation

**Files:**
- Modify: wiki `Installation.md` (prerequisites table; calibration section)
- Modify: `projects/ed-autojump/README.md` (capability table)

The wiki is a separate git repo (`ED-AFK.wiki.git`); clone it, edit, push.

- [ ] **Step 1: Add the display requirement to prerequisites**

In the `## Prerequisites` table of `Installation.md`, add:

```markdown
| **1920×1080, SDR, borderless windowed** | The CV regions are calibrated for exactly this mode. Preflight checks the live window and blocks with the observed values if it differs. Other resolutions are not supported yet. |
```

- [ ] **Step 2: Replace the copy-paste calibration step**

Replace step 3 of `### 2. Calibrate the nav compass` with:

```markdown
3. Run it with `--write` and it edits `config.toml` for you:

```pwsh
ed-autojump calibrate-compass --write
```

This sets `[vision] enabled/region/compass_radius` and saves the previous file
as `config.toml.bak`. To paste it yourself instead, omit `--write` and it prints
the block as before.
```

- [ ] **Step 3: Document the self-healing first run**

Add after the `## Install the workspace` section:

```markdown
## First run

`.\launch.ps1` runs a preflight pass before it flies anything. It repairs what
it can without asking — wrong `pydirectinput` package, missing `config.toml`,
absent binds preset, `[vision].enabled`, an OCR engine you do not have — and
then blocks with one precise instruction per item only you can fix (display
mode, selecting the ED-AFK preset in game, calibrating the compass).

Run the same pass any time with `ed-autojump doctor --fix`. `-NoPreflight`
skips it.
```

- [ ] **Step 4: Fix the OCR row in the capability table**

In `projects/ed-autojump/README.md`, ensure no row claims tesseract is required; the default engine is now WinRT and needs no native install.

- [ ] **Step 5: Commit both repos**

```bash
git add projects/ed-autojump/README.md
git commit -m "docs: self-healing first run, display requirement, --write calibration"
# then in the wiki clone:
git add Installation.md
git commit -m "docs: display requirement, --write calibration, preflight first run"
```

---

## Self-Review

**Spec coverage:**

| Spec item | Task |
|---|---|
| `vision_calibrated`, `vision_enabled` checks | 1 |
| `display_mode` gate with observed values | 2 |
| `binds_selected` via `StartPreset.start` | 3 |
| `ocr_engine` check + WinRT default | 4 |
| Atomic `[vision]` writer, `.bak`, preserves keys/comments | 5 |
| `calibrate-compass --write` | 6 |
| `FIXES` registry, `run_all_checks(fix=True)`, re-check after fix | 7 |
| Fix exception isolation, "fix attempted, still failing" | 7 |
| `config_present` | 8 (`_edit_config` seeds an empty file) |
| Auto-fixes for pydirectinput/panic_hotkey/binds/config | 8 |
| `doctor --fix`, grouped output | 9 |
| `launch.ps1` preflight, `-NoPreflight` | 10 |
| Docs: prerequisites, calibration, first run | 11 |
| Idempotence | tested in 5, 6, 8 |
| Offline testability | every task's tests run with no game |
| `hud_widget` WARN check | **deferred** — needs a live CV probe, which cannot be tested offline; the spec marks it a late safety net, not a gate. Revisit when live testing is possible. |

**Type consistency:** `CheckResult(name, status, detail, fixed)` is used identically in Tasks 1–9. `FixContext(cfg, config_path, venv_python, binds_path)` — all four fields default to `None`, so the partial constructions in Tasks 7 and 8 are valid. Every `fix_*` is `Callable[[FixContext], bool]`. `WindowInfo(width, height, borderless, hdr, found)` is constructed identically in Tasks 2 and 9.

**Deviation from spec, noted deliberately:** `config_present` is not a standalone check. `_edit_config` treats a missing file as empty text and writes it, which covers the requirement without a check that can never fail independently.
