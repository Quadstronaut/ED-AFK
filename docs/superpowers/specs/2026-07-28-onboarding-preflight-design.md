# Self-healing preflight — design

**Date:** 2026-07-28
**Status:** approved, ready for planning
**Goal:** clone → run → first live jump, with the tool fixing its own prerequisites.

---

## Problem

Getting ED-AFK running currently takes a correctly-ordered sequence of manual
steps, and the two most failure-prone ones fail *silently*.

1. **Calibration is copy-paste.** `calibrate-compass` prints a `[vision]` block
   that the user must paste into `config.toml` and set `enabled = true` by hand.
   A wrong or missing paste means the bot flies blind and — failing closed —
   refuses to throttle forward. The symptom (ship does nothing) is far from the
   cause (a config block).
2. **`doctor` does not check the thing most likely to be wrong.** It validates
   journal dir, sessions dir, binds preset, `Status.json`, `pydirectinput` and
   the panic hotkey. It never checks whether vision is calibrated or enabled.
3. **The display lock is invisible.** `require_sdr`, `require_borderless_windowed`
   and `target_resolution = [1920, 1080]` live in `ed_core/config.py` and appear
   only in the Configuration wiki page — not in Installation prerequisites. A
   1440p or HDR user completes install cleanly, then fails at CV with no signpost.
4. **The default OCR engine needs a native binary.** `config.toml` ships
   `ocr_engine = "tesseract"` even though `ed_vision/ocr_winrt.py` exists and
   needs no external install.
5. **Ordering is implicit.** Calibration requires being in the cockpit; doctor's
   `status_files` check requires the game to have run once. The docs present
   these as a flat list.

## Goals

- A new user clones, runs `.\launch.ps1`, and the tool installs or repairs every
  prerequisite it can without prompting.
- Anything it cannot fix produces one precise, actionable instruction with real
  observed values.
- Every check and fix is verifiable offline, with no game and no account.

## Non-goals

- **Resolution independence.** The 1920×1080 SDR borderless lock is *gated
  loudly*, not solved. Scaling CV regions to arbitrary resolutions is a separate
  future spec.
- **No interactive wizard.** No prompts, no menus, no step-through UI. Preflight
  runs, fixes, reports.
- Automating in-game UI navigation (preset selection, HUD widget mode).

---

## Design

### Extend `doctor.py`, do not replace it

`ed_core/doctor.py` already has the right shape: `check_*(injected) -> CheckResult`
pure functions, plus `run_all_checks`, `format_results`, `overall_status`. The
design adds to that module rather than introducing a parallel system.

Two additions:

```python
# A fix is a pure-ish side effect keyed to a check id. None == not auto-fixable.
Fix = Callable[[FixContext], bool]      # returns True if it believes it fixed it
FIXES: dict[str, Fix]                   # check name -> remediation

def run_all_checks(cfg, binds_path=None, *, fix: bool = False) -> list[CheckResult]
```

When `fix=True`, the runner walks checks in order; on a FAIL with a registered
fix it invokes the fix, then **re-runs that check** and records the post-fix
result. A check with no registered fix is reported as-is. The runner never
prompts and never partially applies — each fix is independently idempotent, so
re-running preflight is always safe.

`FixContext` carries the resolved paths and config the fixes need (project root,
venv python, config path, binds dir, journal dir) so fixes stay injectable and
testable rather than reaching for globals.

### Check inventory

Existing checks keep their current names and behaviour. New checks marked ✚.

| Check | Auto-fix | Fix action / instruction |
|---|---|---|
| `journal_dir` | ✗ | ED has never run — start it once |
| `sessions_dir` | ✓ | create the directory |
| `binds_preset` | ✓ | run the existing `install-binds` copy |
| `status_files` | ✗ | ED has never run — start it once |
| `pydirectinput` | ✓ | uninstall upstream, install `pydirectinput-rgx` |
| `panic_hotkey` | ✓ | install the `hotkey` extra |
| ✚ `config_present` | ✓ | seed `config.toml` from the shipped example |
| ✚ `vision_calibrated` | ✓* | run compass calibration and write the block (*only when ED is in the cockpit; otherwise instruct) |
| ✚ `vision_enabled` | ✓ | set `[vision].enabled = true` |
| ✚ `ocr_engine` | ✓ | fall back to the WinRT engine when tesseract is absent |
| ✚ `display_mode` | ✗ | report observed vs required: `ED is 2560×1440 HDR → set 1920×1080 SDR borderless in Options → Graphics` |
| ✚ `binds_selected` | ✗ | read the active preset from `StartPreset.start`; instruct Options → Controls → ED-AFK |
| ✚ `hud_widget` | ✗ | WARN only. Probed via CV once vision is up; the ED-AFK preset already sets point mode, so this is a late safety net, not a gate |

`binds_selected` is the key one: the step happens in ED's UI and cannot be
automated, but `StartPreset.start` records which preset is live, so it can be
*verified* rather than assumed. Same principle for `display_mode` — read from the
game window handle, not from the user.

`hud_widget` is deliberately WARN, not FAIL. Detecting it needs a CV probe, which
needs vision already working — so gating on it would create a dependency loop with
`vision_calibrated`. The ED-AFK preset sets point mode anyway, so a passing
`binds_selected` almost always implies it. It runs last and only advises.

**The venv and editable install stay in `launch.ps1` and are not preflight checks.**
Preflight is Python that runs *inside* the venv, so it cannot create the
interpreter it executes under. The existing PowerShell bootstrap (venv creation,
offline build-backend seeding, stale-editable `.pth` repair) remains the outer
layer and runs first; preflight is everything after a working interpreter exists.
This split is why `pydirectinput` and `panic_hotkey` *can* be preflight fixes —
they are package installs into an already-live venv.

### Calibration writes itself

`calibrate-compass` gains `--write`:

- Writes the computed `[vision]` block into `config.toml` and sets `enabled = true`.
- Writes atomically: serialize to a temp file in the same directory, copy the
  existing file to `config.toml.bak`, then replace.
- Preserves unrelated keys, comments and ordering; only the `[vision]` table is
  rewritten.
- Idempotent — running twice yields the same file.

The `vision_calibrated` fix invokes this. The manual paste path stays documented
for anyone who wants it, but is no longer the default route.

### Default OCR engine flips to WinRT

`config.toml` changes `ocr_engine = "tesseract"` → `"winrt"`. The `ocr_engine`
check verifies the selected engine is importable and falls back to WinRT if a
config pins tesseract without the binary present. This removes a native
dependency from the prerequisites entirely.

### Entry points — one implementation, two callers

- **`launch.ps1`** runs preflight with `fix=True` before Jump, in the same place
  it already bootstraps the venv and repairs the stale editable `.pth`. A
  blocking FAIL halts before the focus countdown, so nothing is sent to the game.
- **`ed-autojump doctor`** runs the same list read-only. **`doctor --fix`**
  remediates. The launcher and doctor share one registry, so they can never drift.

### Output

One block, fixes first, then blockers, then a single verdict line:

```
[preflight] fixed:
  - pydirectinput      replaced upstream package with pydirectinput-rgx
  - vision_calibrated  calibrated compass and wrote [vision] to config.toml

[preflight] you must do these yourself:
  - display_mode   ED is 2560x1440 HDR. Set 1920x1080 SDR borderless
                   in Options > Graphics, then re-run.
  - binds_selected Active preset is "Custom". Select "ED-AFK" in
                   Options > Controls, then re-run.

[preflight] BLOCKED - 2 items need you. Nothing was sent to the game.
```

## Error handling

- A fix that raises is caught, logged, and reported as a FAIL with its exception
  text. One failing fix never aborts the remaining checks.
- A fix that returns success but whose re-check still fails is reported as
  `fix attempted, still failing` — never silently swallowed.
- Preflight failure blocks the run. It never degrades into "try anyway": the
  existing fail-closed posture applies here too.
- `--no-preflight` on `launch.ps1` skips the whole pass, for debugging.

## Testing

The reason this design is shaped around pure functions: none of it needs the game.

- **Checks:** each `check_*` takes injected inputs (a path, a window-info struct,
  file contents), so each gets a table test over pass/warn/fail inputs using
  tmp dirs and fake structs. Covers the display matrix (1080p SDR borderless vs
  HDR vs 1440p vs fullscreen) with no display attached.
- **Fixes:** each `fix_*` runs against a tmp project tree and asserts the
  post-state, plus an idempotence assertion (apply twice → same result).
- **Runner:** fake registries prove ordering, re-check-after-fix, exception
  isolation, and that a fixless FAIL is reported rather than skipped.
- **Config writer:** round-trip tests that unrelated keys, comments and ordering
  survive; that `.bak` is produced; that a crash mid-write leaves the original
  intact.

No live-game test is required for any of it. The only thing that cannot be
verified offline is the wording of instruction strings.

## Files touched

| File | Change |
|---|---|
| `projects/ed-core/src/ed_core/doctor.py` | new checks, `FIXES` registry, `fix=` param |
| `projects/ed-core/src/ed_core/config.py` | `ocr_engine` default → `winrt` |
| `projects/ed-autojump/config.toml` | `ocr_engine = "winrt"` |
| `projects/ed-autojump/src/ed_autojump/cli.py` | `calibrate-compass --write`, `doctor --fix` |
| `launch.ps1` | call preflight before Jump; `-NoPreflight` |
| `projects/ed-core/tests/`, `projects/ed-autojump/tests/` | new suites per above |
| Wiki `Installation.md` | prerequisites gain the display requirement; calibration section switches to `--write` |

## Acceptance criteria

1. On a clean clone with ED running in the cockpit at 1920×1080 SDR borderless
   and no `config.toml`, `.\launch.ps1` reaches a runnable state with no manual
   file editing.
2. On a 1440p or HDR display, preflight blocks with the observed resolution and
   HDR state named in the message.
3. With the ED-AFK preset installed but not selected in-game, preflight blocks
   and names the currently active preset.
4. `doctor` and `launch.ps1` report identical check results for identical state.
5. Preflight is idempotent: two consecutive runs on a good setup fix nothing and
   report the same all-clear.
6. Full suite passes offline with no game installed.
