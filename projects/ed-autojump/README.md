# ed-autojump

Autonomous exploration bot for Elite Dangerous: Odyssey. First tool in the
ED-AFK monorepo. Launches the game, joins a private group, then honks,
scoops, jumps overnight. Optionally FSS / DSS / docks.

> **Status:** v0.2 — Phases 0–6 + Phase 12 (main loop) + Phase 13
> (headless launcher) production-ready on `main`. 385 tests under
> triple-test discipline. Phases 7–10 ship as framework + offline replay
> tests; in-game evidence deferred pending live calibration. See SPEC.md
> §17 for phase exit criteria; `calibration/README.md` and
> `calibration/overnight-runbook.md` for what to validate in-game.

## Quick start

```pwsh
cd projects/ed-autojump
py -3.11 -m venv .venv      # 3.11, 3.12, 3.13, or 3.14 all work
.\.venv\Scripts\Activate.ps1
pip install -e .[dev,hotkey]  # add ,cv for tier-C CV deps (opencv, dxcam, tesseract)
pytest                       # 385 pass, 3 @requires_game deselected, recorded-sessions auto-skip if absent
ed-autojump doctor           # pre-flight: binds, journal-dir, sessions-dir, EDHM, pydirectinput
ed-autojump --help

# ── ONE-TIME CONTROLS SETUP (required, or the bot's keypresses won't move the ship) ──
# The bot drives the keyboard. ED must use a preset whose keys match what the bot
# sends. Install the bundled keyboard preset, then select it inside the game:
ed-autojump install-binds    # copies ED-AFK.4.2.binds into ED's Options\Bindings\
#   then in ED:  Options > Controls > preset dropdown > select "ED-AFK"
#   (maps pitch/throttle/FSD/honk to keys. Switch back to your own preset for manual flight.)
#   No stock preset works as-is: ED ships pitch on the mouse + honk on a mouse button,
#   which a keyboard sender can't drive — hence this dedicated keyboard preset.

# Unattended overnight capture (Tier 2 — see calibration/overnight-runbook.md)
.\scripts\nightly-run.ps1 -DurationHours 6

# Real bot run (records + drives keys; --route-plot enables Spansh auto-plotting)
ed-autojump run --record --engage-keys --route-plot --duration 21600

# Overnight one-shot: launch ED as Duvrazh, join Quadstronaut private group,
# AFK travel for 6h, record everything to ~/ed-afk-sessions/
ed-autojump run --launch --commander Duvrazh --group Quadstronaut \
    --record --engage-keys --route-plot --duration 21600
```

> **Dependency note (v0.2):** the bot uses `pydirectinput-rgx` (the fork
> with explicit `scancode_keyDown`/`scancode_keyUp`), NOT the upstream
> `pydirectinput`. If you have the wrong package, `doctor` fails loudly:
> `pip uninstall pydirectinput && pip install pydirectinput-rgx`.

## Orienting the ship — nav-compass alignment (Phase 14)

A blind key-presser can engage the FSD but can't *point* at the next system,
so it would fire while still aimed at the arrival star. Alignment closes that
loop: the bot reads the in-cockpit **nav compass** (item 13 on the HUD — the
small disc left of the radar), then pitches/yaws until the target dot is
centred and **in front** (filled, not hollow) before it jumps. A failed
alignment **blocks** the jump — vision uncertainty fails safe.

It reads the compass with a small YOLO model (reused from EDAPGui), with a
colour-free OpenCV fallback so it works regardless of your HUD colour /
EDHM mods. Backends:

- `yolo-onnx` (default) — light `onnxruntime` on the vendored `compass.onnx`.
- `ultralytics` (opt-in) — heavier PyTorch runtime on `compass.pt`; flip to
  this only if the light path misbehaves in-game.
- `opencv` — no model, colour-free shape detector; also the always-on fallback.

```pwsh
pip install -e .[vision]          # onnxruntime + opencv + dxcam (light path)
# pip install -e .[vision-heavy]  # add this ONLY for the ultralytics backend

# One-time: be in the cockpit with the nav compass visible, then:
ed-autojump calibrate-compass     # auto-locates the disc, prints a [vision] block
#   paste the printed [vision] block (enabled=true + region=[...]) into config.toml
```

> **License note:** the bundled compass model is **AGPL-3.0** (Ultralytics),
> unlike the rest of this MIT package. See `THIRD_PARTY_NOTICES.md` →
> "Bundled ML model" before redistributing a build that includes the weights.
> The `opencv` backend needs no model and avoids this entirely.

With `[vision].enabled = true` and a calibrated `region`, `run --engage-keys`
aligns before every jump and logs an `Align` outcome (offset, in_front,
aligned) to the session JSONL so the timings can be tuned.

### Nav robustness — route re-targeting + Supercruise Assist

- **Route re-targeting (`[nav].retarget_route_before_engage`, on by default):**
  the bot presses `TargetNextRouteSystem` (H) before each engage, so the next
  route star is locked deterministically — no fragile nav-panel scrolling —
  and the compass has a target to align to.
- **Supercruise Assist (in-system docking / orbit — groundwork, off):** ED has
  **no keybind** for Supercruise Assist, so the bot can't toggle it with a key.
  The supported path is throttle-mode: in ED's right panel / flight settings,
  set Supercruise Assist to engage on **blue-zone throttle**; then the bot
  engages it by locking a target and throttling into the blue zone. The full
  approach/drop flow plugs into the Phase 9/10 docking + DSS work (deferred
  pending in-game calibration); `[nav].supercruise_assist` is the switch.

## Launching the game (Phase 13)

The bot can drive `MinEdLauncher.exe` (rfvgyhn fork) end-to-end:

1. **First-time setup — per commander cred onboarding.** On the
   non-sandboxie install, each Frontier account needs a `.cred` file
   under `%LOCALAPPDATA%\min-ed-launcher\` (DPAPI binds these to your
   user+machine so sandbox copies don't transfer). Run the wizard once:

   ```pwsh
   ed-autojump setup-frontier-creds --commanders Duvrazh Bistronaut Tristronaut Quadstronaut
   ```

   For each commander missing a cred, the wizard spawns MEL interactively
   so you can log in. Once the cred file lands the wizard moves on.

2. **Calibrate the main-menu navigator (per commander).** ED's main menu
   has no CLI flag for private-group selection — the bot navigates the UI
   with arrow keys after launch. Calibration captures the press counts:

   ```pwsh
   ed-autojump calibrate-menu --commander Duvrazh
   ed-autojump calibrate-menu --commander Quadstronaut --is-owner
   ```

   The wizard prints a TOML snippet to paste into `config.toml`. Repeat
   per commander you want to launch through the bot. Set
   `[menu_nav].enabled = true` after at least one is calibrated.

3. **Standalone launch (no AFK loop after):**

   ```pwsh
   ed-autojump launch --commander Duvrazh --group Quadstronaut
   ```

   The flow: dryrun pre-flight (catches stale `.cred` hang) → spawn MEL
   → wait for `Music{MainMenu}` journal event → navigate to PG →
   verify `LoadGame` group matches → exit.

4. **All-in-one overnight:**

   ```pwsh
   ed-autojump run --launch --commander Duvrazh \
       --record --engage-keys --route-plot --duration 21600
   ```

   Launches, navigates, and hands off to the AFK loop in one invocation.

The `nightly-run.ps1` wrapper invokes `ed-autojump run --record` and tees
output to `%USERPROFILE%\ed-afk-sessions\`. The Tier-1 regression suite
(`tests/test_recorded_sessions.py`) auto-discovers those JSONL files and
asserts safety invariants: no HullDamage, no engagement on danger
StarClass, no fuel starvation, no abandoned routes.

## Layout

```
projects/ed-autojump/
  pyproject.toml
  src/ed_autojump/
    cli.py                # entry point (registered as `ed-autojump`)
    config.py             # config.toml loader
    state.py              # in-memory FSM
    binds_tool.py         # install / swap / restore StartPreset.4.start
    journal/              # journal tail + pydantic event models
    status/               # Status.json + NavRoute.json watchers
    keys/                 # binds parser + DirectInput sender (Null/Recording/real)
    fsd/                  # fuel math + danger list (coriolis-data constants)
    planner/              # Spansh integration + danger/fuel filters
    executor/             # state-driven macros (honk, jump, scoop, fss, dss)
    eddn/                 # EDDN publisher (opt-in)
    hud/                  # EDHM detect, GraphicsConfigurationOverride writer
    docking/              # v2 pre-flight predicates + permission flow
    launcher/             # Phase 13: MEL spawn + dryrun + menu nav + wizards + flow
    binds/                # bundled ED-AFK.4.2.binds preset
    data/                 # bundled FSD constants (fsd_modules.json)
    orchestrator.py       # Phase 12 main loop (JournalTail -> dispatch -> Recorder)
    panic.py              # thread-safe panic switch (poll + trip + on_trip callback)
    recorder.py           # session JSONL writer (overnight capture)
    anonymizer.py         # scrub CMDR / FID / AccountID from session JSONL
    session_audit.py      # pure functions for safety asserts on recorded sessions
    doctor.py             # pre-flight checks (binds + dirs + EDHM + pydirectinput)
  tests/
    fixtures/journals/    # anonymized real-journal samples
    test_*.py             # 200+ offline tests, 3 @requires_game stubs
  scripts/
    nightly-run.ps1       # Tier-2 unattended runner (manual or task-scheduled)
    ed-afk-nightly.xml    # Task Scheduler XML (manual import only)
  calibration/
    README.md             # what to validate in-game for tier-C behaviour
    overnight-runbook.md  # Tier-1/2 capture + morning regression-check loop
```

## Phase status

| Phase | Scope | Status |
|---|---|---|
| 0 | skeleton, tailers, journal models | implemented |
| 1 | binds preset + StartPreset swap | implemented |
| 2 | honk MVP (req 4) | implemented |
| 3 | jump + escape + route safety (req 1, 3, 7) | implemented |
| 4 | fuel scoop (req 2) | implemented |
| 5 | EDDN publisher | implemented |
| 6 | EDHM detect + calibration skeleton | implemented |
| 7 | FSS keyboard sweep (req 5) | framework — deferred pending calibration |
| 8 | FSS CV-assisted | framework — deferred pending calibration |
| 9 | DSS 6-direction (req 6) | framework — deferred pending calibration |
| 10 | docking (v2) | framework — deferred pending calibration |
| 11 | headless launcher framework (v2 stub) | superseded by Phase 13 |
| 12 | Orchestrator main loop + panic + Spansh + Status + EDDN + doctor | implemented |
| 13 | MinEdLauncher spawn + dryrun + main-menu wait + PG nav + LoadGame verify | implemented |

## Attribution

Patterns and constants borrowed from:

- **SumZer0-git/EDAPGui** (MIT) — DirectInput scancode table, .binds parser
  shape, Status.json poller pattern, NavRoute parser pattern.
- **EDCD/coriolis-data** (MIT) — FSD constants per class/rating
  (`modules/standard/frame_shift_drive.json`).
- **EDCD/EDDN** (BSD-2) — schema field reference for `fssdiscoveryscan-v1.0`,
  `fssallbodiesfound-v1.0`, `fssbodysignals-v1.0`, `journal-v1.0`,
  `navroute-v1.0`.

See `THIRD_PARTY_NOTICES.md` at repo root for full attribution.

## Safety

- The bot does **not** modify your existing `.binds`. It writes a separate
  `ED-AFK.4.2.binds` and only edits line 2 of `StartPreset.4.start`. On exit
  the original is restored.
- The bot refuses to engage on a `StarClass` in the danger list (white
  dwarfs, neutron stars, black holes, Wolf-Rayets) even if the in-game
  plotter routed through one.
- Panic hotkey (default Ctrl+Alt+P) releases all keys, throttles to zero,
  restores the binds preset, and exits.

## License

**AGPL-3.0-or-later** (see `LICENSE`).

The bot's own code began as MIT-style work, but the distribution bundles the
nav-compass detection model (`src/ed_autojump/vision/model/compass.*`), whose
weights are **AGPL-3.0** (Ultralytics). AGPL is viral over the combined work,
so the whole package is licensed AGPL-3.0 to stay honest and compliant. In
practice that means: use it freely, and if you fork it or run it as a service
for others, share your source too. See `THIRD_PARTY_NOTICES.md` for the full
attribution chain.
