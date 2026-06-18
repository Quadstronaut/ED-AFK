# Launcher DEBUG menu — Stage-0 spec (DESIGN-ONLY, note-for-later)

**Status:** DRAFT for council Stage 0. NOT a build order. No code, no edits to
`launch.ps1` / `launch_job.ps1` / any flight module. This file is the only
artifact; the repo diff is otherwise empty.
**Date:** 2026-06-18
**Arbiter:** council-v2-spec, Stage 0.

A manual test harness nested in the launcher's Settings menu that can fire every
bot capability (step / keybind / CV reader) in isolation, so the operator can
verify each still works. Motivated by the audit that found real code (pip
management) ripped out and ~80 buried council worktrees undetected — exactly the
rot a "run every capability once" harness surfaces.

---

## 0. Grounding (read, cited — every claim traces to live code)

| Fact the design leans on | Source (live tree) |
|---|---|
| Launcher is a PS menu; `Invoke-SettingsMenu` builds `$script:SettingsRows`, navigable kinds are `toggle/envtoggle/cycle/action/back`; `header/blank/soon` are decoration | `launch.ps1:358-373, 615-738` |
| Launcher shells the bot via `& $venvPython -m ed_autojump.cli <args>` with CWD=`$ProjectRoot`; long children go through `Invoke-OwnedChild` (job-object lifetime) | `launch.ps1:725, 746-750, 835`; `launch_job.ps1` |
| The action catalog = 34 `register_step(name, fn, *, input_exclusive=...)` calls across 4 live files | `ed_core/flow/steps_shared.py` (13), `ed_autojump/flow/steps.py` (18), `ed_explore/steps_{body_tour,explore,strand_recovery}.py` (3) |
| `STEP_REGISTRY: dict[str,StepFn]` + `INPUT_EXCLUSIVE_ACTIONS: set[str]` are the merged tables; `merged_step_registry()` / `input_exclusive_actions()` read them; registration is FAIL-ON-DUPLICATE | `ed_core/flow/step_registry.py:40-74` |
| A step is invoked as `fn(ctx, **step.params)` — ctx + keyword params; input-exclusive steps run inside `ctx.exclusive_guard()` | `ed_core/flow/interpreter.py:87-99` |
| `StepContext` is one dataclass: only `sender` is required; ~40 suppliers/readers all default to safe no-ops (`lambda: None/False/(0,...)`, `compass_reader=None`, etc.) | `ed_core/flow/context.py:20-205` |
| Sender duck type: `press(action,*,hold)`, `key_down/up`, `release_all()`; `NullSender` is a no-op, `DirectInputSender(binds)` sends real scancodes, `LoggingSender` wraps either | `ed_core/keys/sender.py:35-130, 222+` |
| Keybind contract = `REQUIRED_ACTIONS: frozenset` (the actions the bot's CODE presses), validated against the live preset; `validate_live_binds(path)` raises on missing/colliding | `ed_core/binds_validate.py:43-135` |
| The live preset is `ed_autojump/binds/ED-AFK.4.2.binds` (exists) | `ed_autojump/binds/ED-AFK.4.2.binds` |
| CV readers + their region grabbers: `build_vision` (compass), `build_widget_vision` (widget_ring), `build_navpanel_vision` (navpanel OCR), `build_station_menu_grabber`, `build_sun_grabber`; each returns `(None,...)` when unconfigured | `ed_vision/capture.py:255-423` |
| Live CV-diag PRECEDENT already exists: `cv-debug` / `navpanel-overlay` / `calibrate-overlay` CLI subcommands → `ed_core/cv_debug_cli.run_cv_debug / run_navpanel_overlay / run_calibration`: read a region, print the read, draw boxes on EDMCOverlay, "keep ELITE foreground", `q` to quit | `ed_autojump/cli.py:213-241, 904-941`; `ed_core/cv_debug_cli.py:26-352` |
| CV-debug-overlay design (sink, `ScreenToOverlay`, verdict colors, `set_debug_sink`) | `projects/ed-autojump/docs/.../2026-06-10-cv-debug-overlay-design.md` |
| Window focus helpers: `focus_ed_window()`, `find_ed_hwnd()`, `force_foreground()`, `GetForegroundWindow()`; launcher also has `Set-EliteForeground` | `ed_core/launcher/focus.py:34-141`; `launch.ps1:400-420` |
| G12 DAG gate: no upward/sideways cross-package import; `from ed_autojump.X` after a move is caught by the import-RESOLUTION test | `projects/ed-autojump/tests/test_import_resolution.py`; reorg `whole_tree_import_check.py` |
| CLI dispatch is a name→fn dict in `main()`; a new subcommand = one parser + one dict entry (e.g. how `cv-debug` was added) | `ed_autojump/cli.py:1019-1036` |

**Exact registered action catalog (34), grouped by source module — the menu
GENERATES from the live registry, this list is for review only:**

- core/steps_shared (13): `press, wait, set_throttle, pitch, target_ahead,
  ensure_analysis_mode, wait_cooldown_clear, hold_until_event, orient_compass,
  pitch_compass, hold_alignment, orient_widget_ring, engage_supercruise`
- autojump/flow/steps (18): `target_next_route, engage_jump,
  engage_jump_clearance, confirm_orbiting, sc_assist_orbit*, nav_panel_target*,
  scoop_refuel, dock_target_station*, dock_sc_assist*, dock_approach*,
  dock_request*, dock_await_docked, station_services*, auto_launch*,
  wait_masslock_clear, confirm_menu_item, station_services_macro*,
  dock_blind_maneuver*`
- explore (3): `body_tour, explore, station_strand_recovery`

`*` = `input_exclusive=True` (10 actions: `sc_assist_orbit, nav_panel_target,
dock_target_station, dock_sc_assist, dock_approach, dock_request,
station_services, auto_launch, station_services_macro, dock_blind_maneuver`).

---

## 1. INTERFACE

Two surfaces. The PS menu is a thin shell; ALL bot knowledge lives behind ONE
Python diagnostic entry point that goes THROUGH the existing registry/dispatch.

### 1.1 Python diagnostic entry point — `python -m ed_autojump.debug`

A NEW core-respecting CLI module. (Placement: a submodule reachable as
`python -m ed_autojump.debug` so the launcher's existing `& $venvPython -m
ed_autojump.<x>` invocation pattern and CWD/venv resolution are reused
verbatim. The IMPLEMENTATION may live in `ed_core` and re-export, identical to
how `cv_debug_cli` lives in `ed_core` but is invoked via `ed_autojump.cli`.
**BLOCKED-ON-KYLE #6** picks the exact module home.)

Subcommands (machine-readable I/O so PS can parse):

```
python -m ed_autojump.debug list   [--json]
    # Enumerate the catalog from the AUTHORITATIVE registries. NEVER hand-listed.
    # Sources, in one merged list, each entry tagged with its `kind`:
    #   kind=step    : every name in merged_step_registry() (after activate()),
    #                  with input_exclusive flag from input_exclusive_actions()
    #   kind=keybind : every action in binds_validate.REQUIRED_ACTIONS,
    #                  annotated with the key it resolves to in the live preset
    #                  (parse_binds), and a bound/unbound flag
    #   kind=cv      : the named CV readers (compass, widget_ring, navpanel, sun,
    #                  station_menu) — enumerated from the build_* probes /
    #                  a declared CV catalog, each with a configured/unconfigured flag
    # --json => one JSON array of {id, kind, label, input_exclusive, safety,
    #           configured, detail}. Default => human table.
    # Read-only. Never presses a key. Safe with the game down.

python -m ed_autojump.debug describe <id>   [--json]
    # One entry's full record: kind, the step fn's required/optional params
    # (introspected via inspect.signature — so the PS layer can prompt for args),
    # the resolved keybind, the safety class, and why (e.g. "input_exclusive →
    # drives the panel"). Read-only.

python -m ed_autojump.debug fire <id> [--arg NAME=VALUE ...] [--live] [--yes]
    # THE CRUX. Fire ONE catalog entry through the REAL dispatch surface.
    #   - DRY-RUN by default (no --live): builds the SAME StepContext but with
    #     NullSender, so a `kind=step`/`kind=keybind` entry runs its real code
    #     path and logs what it WOULD press, sending zero keys. Exit 0/!=0 by
    #     the step's bool return.
    #   - --live: swaps in DirectInputSender(parse_binds(preset)). Requires the
    #     game be FOREGROUND (focus gate, §5) AND --yes (confirm token the PS
    #     layer supplies only after its own confirm prompt). Without both → refuse,
    #     exit 3, no press.
    #   - kind=step  → look up fn in merged_step_registry(); run via the REAL
    #     interpreter path: run_procedure() on a synthetic single-step Procedure
    #     (preferred — exercises the input_exclusive/exclusive_guard wrap exactly
    #     as a flight does) OR a documented thin call of fn(ctx, **args) that
    #     reproduces interpreter.py:92-99 byte-for-byte. (BLOCKED-ON-KYLE #5:
    #     which of the two; default = synthetic Procedure, most faithful.)
    #   - kind=keybind → fire the press through the SAME sender the steps use
    #     (sender.press(action)); this is what `step_press` does
    #     (steps_shared.py:32-42). No bypass of the bind layer.
    #   - kind=cv → delegate to the live CV-diag runner (§3), NOT a bespoke read.
    #   - --arg NAME=VALUE pairs are coerced to the param's annotated type from
    #     inspect.signature; an unknown/extra arg or a missing REQUIRED param →
    #     refuse, exit 4 (do not fire a step with guessed params).

python -m ed_autojump.debug cv <reader> [--report] [--draw] [--ocr]
    # Vision diagnostics (§3). Thin alias over the existing cv-debug/navpanel
    # runners + a one-shot read-and-print. Read-only, fail-soft.
```

Exit codes (stable contract the PS layer switches on):
`0` success/true · `1` step returned false / reader miss · `2` bad
config/env (venv, binds, vision deps) · `3` refused (focus gate or missing
`--yes` on `--live`) · `4` bad/missing args · `5` unknown `<id>`.

stdout = the human/JSON payload; stderr = diagnostics/warnings; the process
re-propagates the chosen exit code (mirrors `cli.py` convention). The launcher
captures stdout, shows it, and branches on the exit code.

### 1.2 PowerShell surface — a `submenu` row kind under Settings

A NEW row `@{ Kind = 'submenu'; Key = 'Debug'; Label = 'Debug / test harness' }`
appended to `$script:SettingsRows` (before `back`), plus a `submenu` branch in
`Invoke-SettingsMenu`'s activate switch that calls a new `Invoke-DebugMenu`.
`Invoke-DebugMenu` reuses the existing `Draw-Lines` / `Build-*Lines` /
`Test-Interactive` frame machinery (launch.ps1:536-555) — same arrow/Enter/Esc
navigation, same look. It populates its rows by shelling
`debug list --json` ONCE on entry and rendering the parsed catalog (so the menu
is generated, never hardcoded — §1.4 sync invariant).

### 1.3 Catalog source & staying-in-sync (REQ 1)

The menu's row set is the parsed output of `debug list --json`, which reads
`merged_step_registry()`, `input_exclusive_actions()`, `REQUIRED_ACTIONS` +
`parse_binds(preset)`, and the CV reader catalog — AFTER calling the same
`activate()` + plugin-entry-point load the CLI run path uses
(`cli.py:487-495`), so every domain's steps are present. A step added tomorrow
appears with zero launcher edits. **Invariant INV-1.** Acceptance test AT-1
fails the build if any `register_step` name is absent from `debug list`.

### 1.4 No-bypass guarantee (REQ 2)

`fire` resolves `kind=step` via `merged_step_registry()` and `kind=keybind` via
`sender.press` — the identical surfaces `interpreter.run_procedure` and
`steps_shared.step_press` use. It MUST NOT import a step's private fn by path,
re-implement a keypress, or read the registry's backing dict directly.
**Invariant INV-2**; AT-2 asserts the entry point references
`merged_step_registry` / `input_exclusive_actions` and constructs a real
`StepContext`, and contains no second keypress implementation.

---

## 2. THE FIRE MECHANISM (the crux)

### 2.1 Building the StepContext

`StepContext` needs only `sender`; every other field defaults to a safe no-op
(`context.py:20-205`). The debug harness builds a context with:

- `sender` = `NullSender()` (dry-run) or
  `DirectInputSender(parse_binds(preset))` (`--live`), optionally wrapped in
  `LoggingSender` so the fired press is echoed to stdout (reuses
  `cli.py:353-355`).
- `should_abort` = a callable tripped by Ctrl+C / a panic hotkey, so a long
  step (`hold_until_event`, `dock_*`) can be stopped — same backstop the live
  loop relies on (`context.py:42-43`, `interpreter.py:42-49`).
- CV-dependent fields (`compass_reader`, `frame_grabber`, `nav_panel_reader`,
  `station_menu_grabber`, `widget_*`) wired from the SAME `build_*(cfg)` probes
  as `cmd_run` (`cli.py:417-479`) ONLY when `--live` and vision is configured;
  else left `None` so a vision step fails closed and SAYS so (it cannot move
  the ship through a missing reader).
- Journal/Status suppliers (`status_supplier`, `event_*`, `navroute_supplier`,
  …): default no-ops for a one-shot fire. A step that needs live journal state
  (e.g. `wait_cooldown_clear`) will report it can't confirm and return per its
  own fail-closed contract — acceptable for a manual "does the press fire" test.
  **BLOCKED-ON-KYLE #4:** whether `fire --live` should also spin up a real
  `JournalTail`/`StatusReader` so event-gated steps can actually complete, or
  stay context-light (a press-only smoke test). Default = context-light.

### 2.2 Going through the real dispatch

Preferred: wrap the one entry in a synthetic single-step `Procedure` and call
`ed_core.flow.interpreter.run_procedure(proc, ctx)`. This exercises the REAL
path including the `INPUT_EXCLUSIVE_ACTIONS` → `ctx.exclusive_guard()` wrap and
the crash-to-failed-step guard (`interpreter.py:92-113`). The harness reports
`ProcedureResult.completed/aborted` + the per-step ok. **Invariant INV-3.**

### 2.3 Arg passing

`describe <id>` exposes each step fn's params from `inspect.signature` (keyword-
only, with annotations + defaults — e.g. `step_pitch(ctx,*,dir:str,
hold_s:float)`, `step_press(ctx,*,bind:str,hold_s=0.05)`). The PS menu prompts
for any required param, passes them as `--arg dir=up --arg hold_s=0.4`; the
Python layer coerces by annotation and refuses (exit 4) on a missing required
param or an unknown name. A no-param step (`target_ahead`,
`ensure_analysis_mode`) fires with none.

### 2.4 Output capture back to PowerShell

`debug` writes the human table / JSON to stdout, warnings to stderr, and exits
with the §1.1 code. `Invoke-DebugMenu` runs it via a SHORT-LIVED capture (this
is a quick diagnostic, not the long-running flight child, so it does NOT need
`Invoke-OwnedChild`'s job object — but it MUST still set CWD=`$ProjectRoot` and
use `$venvPython`, like every other launcher shell-out). It prints captured
stdout in the menu's output region (mirrors the `Calibrate` action pattern,
launch.ps1:719-733) and colors the result by exit code. **Exception:** a
`cv --draw` / live reader session is long-running + interactive (`q` to quit) —
that one runs foreground to completion like `calibrate-compass` does today.

---

## 3. VISION DIAGNOSTICS (REQ 3) — reuse, don't reinvent

The live CV-diag pattern ALREADY EXISTS and ships: `ed_core/cv_debug_cli.py`
(`run_cv_debug`, `run_navpanel_overlay`, `run_calibration`) reads a region,
prints what it sees, draws verdict-colored boxes on EDMCOverlay, logs every UI
change, and quits on `q` (`cli.py:904-941`, `cv_debug_cli.py:26-352`). It builds
on the CV-debug-overlay design (`CvDebugSink` / `ScreenToOverlay` /
`set_debug_sink`, the 2026-06-10 doc).

The debug menu's `cv <reader>` entry is a thin router over these, NOT new CV:

- `--report` (print what it sees): grab the reader's region via its `build_*`
  grabber, run the reader, print the parsed read (compass needle angle /
  navpanel OCR rows / station-menu highlighted item) to stdout. One-shot.
- `--draw` (overlay): delegate to `run_cv_debug` (context-aware, GuiFocus-
  driven, all readers) or `run_navpanel_overlay` (per-row nav boxes). These are
  the existing runners; the menu just launches them. Boxes are flash+verdict
  color via the existing sink. Requires EDMCOverlay + ED foreground (their own
  precondition, `cv_debug_cli.py:38-41, 161`).
- `--ocr` (surface OCR text): for navpanel/station-menu, print the raw OCR
  strings the WinRT/opencv reader returns (`ed_vision/ocr_winrt.py`,
  `navpanel_reader.py`) so the operator sees exactly what text was read.

All read-only and fail-soft: a missing reader/region prints "not configured —
run calibrate-compass / set [vision]" and exits 1, never crashes, never moves
anything. **Invariant INV-6.**

---

## 4. MENU / UX (REQ 4)

- Nesting: `Settings → Debug / test harness → <catalog>`. One new `submenu` row
  kind + `Invoke-DebugMenu`, reusing `Draw-Lines`/`Test-Interactive` so the
  non-interactive (piped) fallback degrades like the rest of the launcher
  (launch.ps1:582-586, 659-671).
- Presentation of 34+ entries: grouped sections with `header` decoration rows
  reusing the existing `header` kind — proposed groups **STEPS / KEYBINDS /
  VISION** (and within STEPS, the source-module grouping shown in §0), each
  entry showing its id, a `[live]`/`[exclusive]`/`[unsafe]` tag, and
  bound-key/configured state. Arrow-scroll within the list; the frame already
  supports more rows than the screen via in-place redraw.
- Selecting an entry opens a per-entry view (from `describe`): shows params +
  safety class, then offers **Dry-run fire** (always) and **LIVE fire**
  (gated, §5). After firing, the captured stdout + PASS/FAIL (by exit code) is
  shown in place.
- Ordering: **BLOCKED-ON-KYLE #1** (below). The menu is built from an ordered
  list the Python layer emits; the ONLY open question is the sort key. Until
  answered, the harness emits registration order and the spec does not commit a
  default.

---

## 5. SAFETY (critical, REQ 5) — fail-closed

Firing a live step/keybind moves the real ship/UI. Guardrails:

1. **Dry-run is the default.** Every fire is NullSender unless `--live` is
   explicit AND the PS layer passed `--yes`. A dry-run sends zero keys
   (`NullSender.press` is a no-op, sender.py:71-94). **INV-4.**
2. **Game-foreground gate before any live press.** `fire --live` and any
   `cv --draw` first require ED to be the FOREGROUND window — check via
   `find_ed_hwnd()` + `GetForegroundWindow()` (focus.py:34-114). Not foreground
   → refuse, exit 3, no press. (The PS layer may additionally call
   `Set-EliteForeground` / the bot's `focus_ed_window()` and re-check, mirroring
   the run path `cli.py:650-655`.) Live key dispatch to the wrong window is the
   exact footgun the run path already guards. **INV-5.**
3. **Confirm-before-fire.** The PS menu shows a `Read-YesNo`
   (launch.ps1:377-382) naming the entry + its safety class before sending
   `--live --yes`. No confirm → dry-run only.
4. **Safety classification per entry**, surfaced in `list`/`describe` and
   enforced by `fire`:
   - **gated-unsafe** — moves the ship irreversibly or commits a maneuver out
     of context. At minimum the 10 `input_exclusive` steps PLUS `engage_jump`,
     `engage_jump_clearance`, `engage_supercruise`, `set_throttle`,
     `auto_launch`. These require the foreground gate + an EXTRA explicit
     confirm, and the harness prints a per-step "out of context this will …"
     warning. `engage_jump`/`engage_jump_clearance` (fires the FSD) and
     `auto_launch` (undocks) are the sharpest. **BLOCKED-ON-KYLE #2** ratifies
     the exact gated set + whether any are dry-run-ONLY (never live from the
     harness).
   - **press-safe** — single discrete keypress, harmless in the cockpit
     (`target_ahead`, `pitch`, `press`, `ensure_analysis_mode`, raw keybinds).
     Foreground gate still applies for `--live`; no extra confirm.
   - **read-only** — CV readers, `confirm_*`, `wait_*`: never press; always
     allowed.
5. **Single-ship Mandalay.** CV regions are per-hull (memory: per-ship CV
   regions); the harness states it assumes the Mandalay calibration and does
   not switch ships. No multi-ship logic.
6. **Abort path.** Ctrl+C / panic trips `should_abort`; `run_procedure` aborts
   without later steps (`interpreter.py:42-49`). Live sender's
   `release_all()` is invoked on exit so a held key from an interrupted hold is
   released (parity with `install_signal_cleanup`, cli.py:568-570).
7. **No wall-clock success gates** (standing rule): the harness does not assert
   "worked" from a timer; a fire's verdict is the step's bool / the reader's
   found-flag only.

---

## 6. INVARIANTS (carried into Stage 2/3 as gate checks)

- **INV-1 (sync):** the catalog is generated from the live registries after
  `activate()`; adding a `register_step` requires no launcher edit and appears
  in `list`.
- **INV-2 (no-bypass):** `fire` reaches steps only via
  `merged_step_registry()`/`run_procedure` and keys only via `sender.press`; no
  private-fn import, no second keypress impl.
- **INV-3 (real path):** a step fire exercises the `input_exclusive` →
  `exclusive_guard` wrap and the crash-to-failed-step guard exactly as a flight.
- **INV-4 (dry-run default):** absent `--live`, zero keys are sent (NullSender).
- **INV-5 (focus gate):** no live press unless ED is foreground; else exit 3.
- **INV-6 (vision read-only):** CV entries never press, never move, fail soft.
- **INV-7 (DAG/G12):** the Python entry point imports core/vision DOWNWARD only;
  no `ed_core`→domain import; passes the import-resolution test
  (`test_import_resolution.py`).
- **INV-8 (design-only, this stage):** no edit to `launch.ps1`/`launch_job.ps1`/
  flight code; repo diff empty but for this brief.

---

## 7. ACCEPTANCE CRITERIA

- **AC-1** `debug list` enumerates EVERY `register_step` name (34 today) plus
  every `REQUIRED_ACTIONS` keybind plus the 5 named CV readers, each with kind +
  safety + configured flags; the count of `kind=step` equals
  `len(merged_step_registry())` after activation.
- **AC-2** `debug list --json` is parseable and `Invoke-DebugMenu` renders its
  rows from that JSON (no hardcoded catalog in `launch.ps1`).
- **AC-3** `debug describe <step>` returns the fn's required + optional params
  from `inspect.signature` (verified on `pitch`, `press`, `set_throttle`).
- **AC-4** `debug fire <step>` (no `--live`) runs through `run_procedure` with
  NullSender, sends zero keys, and returns the step's bool as the exit code.
- **AC-5** `debug fire <step> --live` WITHOUT `--yes` OR with ED not foreground
  is REFUSED with exit 3 and zero presses.
- **AC-6** `debug fire <keybind> --live --yes` (ED foreground) calls
  `sender.press(action)` exactly once via the bind layer (asserted against a
  fake/recording sender in test; live press is operator-witnessed).
- **AC-7** `debug fire <id> --arg bad=1` (unknown param) or a missing required
  param exits 4 without firing.
- **AC-8** `debug cv compass --report` with no `[vision]` calibration prints a
  "not configured" message and exits 1 — no crash, no movement.
- **AC-9** `debug cv navpanel --draw` launches the existing `run_navpanel_overlay`
  / `run_cv_debug` runner (delegation asserted; live overlay operator-witnessed).
- **AC-10** The new Python module passes `test_import_resolution.py` and the
  layering gate (no core→domain edge).
- **AC-11** The launcher menu nests Debug under Settings, navigates with the
  existing keys, and the non-interactive fallback degrades gracefully.
- **AC-12 (this stage):** `git status` shows only this brief added; no
  `launch.ps1` / flight-code diff.

---

## 8. ACCEPTANCE TESTS (concrete, executable — Python pytest unless noted)

Tests run against the live registries with a fake sender; nothing presses a key
or needs the game. They are the build's contract.

```python
# tests/test_debug_harness.py  (lives with the implementing package; design-only here)
import inspect, json, subprocess, sys

def _activate_and_registry():
    from ed_autojump import activate; activate()
    import importlib.metadata as m
    for ep in m.entry_points(group="ed_autojump.plugins"): ep.load()()
    from ed_core.flow.step_registry import (merged_step_registry,
                                             input_exclusive_actions)
    return merged_step_registry(), input_exclusive_actions()

# AT-1 (AC-1, INV-1): every registered step is in `list`; counts match.
def test_list_covers_every_registered_step():
    reg, _ = _activate_and_registry()
    from ed_autojump.debug import build_catalog        # design name
    cat = build_catalog()
    step_ids = {e["id"] for e in cat if e["kind"] == "step"}
    assert set(reg) <= step_ids
    assert len([e for e in cat if e["kind"]=="step"]) == len(reg)

# AT-1b (AC-1): keybinds + CV readers present.
def test_list_covers_keybinds_and_cv():
    from ed_core.binds_validate import REQUIRED_ACTIONS
    from ed_autojump.debug import build_catalog
    cat = build_catalog()
    kb = {e["id"] for e in cat if e["kind"]=="keybind"}
    assert set(REQUIRED_ACTIONS) <= kb
    cv = {e["id"] for e in cat if e["kind"]=="cv"}
    assert {"compass","widget_ring","navpanel","sun","station_menu"} <= cv

# AT-2 (AC-1): input_exclusive flag mirrors the registry truth.
def test_exclusive_flag_matches_registry():
    _, excl = _activate_and_registry()
    from ed_autojump.debug import build_catalog
    flagged = {e["id"] for e in build_catalog()
               if e["kind"]=="step" and e["input_exclusive"]}
    assert flagged == set(excl)

# AT-3 (AC-3): describe returns the fn's keyword params.
def test_describe_params_match_signature():
    from ed_autojump.debug import describe
    d = describe("pitch")
    assert {"dir","hold_s"} <= set(d["params"])

# AT-4 (AC-4, INV-3/4): dry-run fire goes through run_procedure w/ NullSender,
# presses nothing, returns the step bool.
def test_dryrun_fire_uses_real_path_no_keys():
    from ed_core.keys import NullSender
    from ed_autojump.debug import fire_entry            # returns (rc, sent_actions)
    rc, sent = fire_entry("target_ahead", args={}, live=False)
    assert sent == []                                   # zero presses
    assert rc in (0, 1)

# AT-4b (INV-2/3): fire builds a real StepContext + dispatches via the registry.
def test_fire_path_is_real_dispatch(monkeypatch):
    import ed_core.flow.interpreter as interp
    seen = {}
    orig = interp.run_procedure
    def spy(proc, ctx, **kw):
        seen["action"] = proc.steps[0].action; return orig(proc, ctx, **kw)
    monkeypatch.setattr(interp, "run_procedure", spy)
    from ed_autojump.debug import fire_entry
    fire_entry("ensure_analysis_mode", args={}, live=False)
    assert seen["action"] == "ensure_analysis_mode"

# AT-5 (AC-5, INV-5): live fire refused without --yes / focus.
def test_live_fire_refused_without_consent_or_focus(monkeypatch):
    monkeypatch.setattr("ed_autojump.debug.ed_is_foreground", lambda: False)
    from ed_autojump.debug import fire_entry
    rc, sent = fire_entry("target_ahead", args={}, live=True, yes=True)
    assert rc == 3 and sent == []                       # not foreground → refuse

# AT-6 (AC-6): live keybind fires exactly one press via the bind layer.
def test_keybind_fire_presses_once_via_sender(monkeypatch):
    from ed_core.keys import RecordingSender, parse_binds
    monkeypatch.setattr("ed_autojump.debug.ed_is_foreground", lambda: True)
    rec = {}
    def fake_live_sender():
        s = RecordingSender(parse_binds(BINDS_PATH)); rec["s"] = s; return s
    monkeypatch.setattr("ed_autojump.debug.make_live_sender", fake_live_sender)
    from ed_autojump.debug import fire_entry
    fire_entry("SelectTarget", args={}, live=True, yes=True)   # kind=keybind id
    assert [e.action for e in rec["s"].events if e.action!="release_all"] \
           == ["SelectTarget"]

# AT-7 (AC-7): unknown/missing arg refused, no fire.
def test_bad_arg_refused():
    from ed_autojump.debug import fire_entry
    rc, sent = fire_entry("pitch", args={"bogus": "1"}, live=False)
    assert rc == 4 and sent == []

# AT-8 (AC-8, INV-6): cv report with no calibration → exit 1, no crash.
def test_cv_report_uncalibrated_is_soft(tmp_path, monkeypatch):
    from ed_autojump.debug import cv_report
    rc = cv_report("compass", cfg_with_vision_disabled())
    assert rc == 1

# AT-10 (AC-10, INV-7): the new module passes the import-resolution gate.
def test_module_imports_resolve_downward():
    import ed_autojump.debug   # must import w/o pulling a domain into core
    # plus: the existing test_import_resolution.py already scans this tree.

# AT-CLI (AC-2): the CLI subcommand exists and emits JSON.
def test_cli_list_json_parses():
    out = subprocess.check_output(
        [sys.executable, "-m", "ed_autojump.debug", "list", "--json"])
    assert isinstance(json.loads(out), list)
```

PowerShell-side (manual / Pester, design intent):

- **AT-PS-1 (AC-2/AC-11):** `Invoke-DebugMenu` shells `debug list --json`,
  parses it, renders one row per entry; no catalog literal in `launch.ps1`.
- **AT-PS-2 (AC-12):** after a full design+stub pass, `git status --porcelain`
  shows only the brief (no `launch.ps1` diff) at THIS stage.

Operator-witnessed (LIVE, can't be auto-asserted — labelled per
evidence-class discipline): a real `--live` step fire moves the ship as
expected; `cv --draw` boxes track the compass/navpanel on-screen.

---

## 9. BLOCKED-ON-KYLE (every unknown — no guessing)

1. **List ordering (REQ, explicit).** Operator wants entries "in the opposite
   order of what I just listed" — he'd listed pip directions Left, Up, Right,
   Down. Ambiguous: reverse of THAT literal four-item list (→ Down, Right, Up,
   Left)? reverse registration order? grouped-by-domain then reversed?
   alphabetical-reversed? Also: there is **no `pip`/pip-direction step in the
   live registry today** (pip management was the ripped-out code the audit
   found) — so the very example refers to a capability not yet present. Need:
   the exact sort key for the catalog, AND confirmation of whether pip-direction
   commands are to be (re)added to the catalog as first-class entries.
2. **Gated-unsafe set + live-allowed.** Confirm the exact steps that are
   live-fireable only behind the extra confirm, and whether any
   (`engage_jump`, `engage_jump_clearance`, `auto_launch`) are **dry-run-ONLY**
   from the harness (never a live press).
3. **`pip` / "test the pips" semantics.** The motivating phrase. If pip control
   returns, is it a new `register_step` ("pip_reset"/per-direction), a raw
   keybind set, or a macro? It must enter via the registry to satisfy INV-1/2;
   need the operator's intended shape (cross-ref memory: `reset_power_
   distribution`, synthetic-press timing needs ~0.8s spacers + repeats).
4. **Context depth for `fire --live`.** Press-only smoke test (context-light,
   default) vs. full `JournalTail`/`StatusReader` wiring so event-gated steps
   (`wait_cooldown_clear`, `dock_await_docked`) can actually complete?
5. **Dispatch faithfulness.** Synthetic single-step `Procedure` via
   `run_procedure` (default, most faithful — runs the exclusive-guard wrap) vs.
   a documented thin `fn(ctx, **args)`? Confirm the Procedure approach.
6. **Module home / name.** `python -m ed_autojump.debug` (matches the
   launcher's invocation style) with impl in `ed_core` re-exported (matches the
   `cv_debug_cli` precedent), or a different name/home? Confirm.
7. **CV reader catalog source.** Enumerate CV entries from a hand-kept list of
   the `build_*` probes, or introduce a declared registry of named readers in
   `ed_vision` (sturdier vs. INV-1 drift)? Confirm whether the in-sync guarantee
   must extend to CV readers (recommended: yes).
8. **Keybind list scope.** Catalog keybinds from `REQUIRED_ACTIONS` (the
   actions the CODE presses, 30-ish) only, or EVERY action bound in the preset
   (the full `.binds`, incl. ones the bot never presses)? Operator said "every
   keybind the bot has" — `REQUIRED_ACTIONS` is that set; confirm he doesn't
   also want unused preset binds.
9. **Launcher entry visibility.** Debug under Settings always, or only when an
   env flag / `-Debug` launcher switch is present (keep it out of the everyday
   menu)? Confirm.
