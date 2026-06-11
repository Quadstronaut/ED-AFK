# ed-autojump

An assistive exploration harness for Elite Dangerous: Odyssey, and the first
tool in the ED-AFK monorepo. It flies a plotted route end to end — honk on
arrival, scoop fuel at scoopable stars, orient the ship, jump, and dock at the
end — performing the sustained, timing-critical piloting that a player's hands
or input hardware may not be able to. Optional game launch via MinEdLauncher.
FSS and DSS scanning remain framework-only stubs.

See the [repo-root README](../../README.md) for the **why** — the accessibility
motivation, the credit owed to AbleGamers / SpecialEffect / the adaptive-gaming
field, and the honest Terms-of-Service disclaimer. This file is the operator's
manual: setup, calibration, and the CLI.

> **Status: alpha.** The flight loop (arrival / startup / sc_resume /
> smack_recovery / route_complete_park), the docking lane (`dock` /
> `dock_resume`), the parallel honk track, the danger filter, Spansh
> auto-plotting, and MinEdLauncher launch all **ship on `master`** and are
> exercised by a large offline unit + replay suite. But large parts are **not
> live-tested** (the fuel-scoop pit-stop and several flows are explicitly
> marked so), and there are **open defects that can strand or crash the ship** —
> the `dock` lane now has the `dock_approach` step (defect #1 closed on master),
> `sc_resume` can throttle a star-parked ship into the star, and
> `smack_recovery` can mis-flip. The authoritative per-step audit, with every
> gate and known defect, is [`docs/ACTION_MEGASHEET.md`](../../docs/ACTION_MEGASHEET.md)
> at the repo root. `calibration/README.md` and `calibration/overnight-runbook.md`
> cover what to validate in-game.

## Quick start

```pwsh
cd projects/ed-autojump
py -3.11 -m venv .venv      # 3.11, 3.12, 3.13, or 3.14 all work
.\.venv\Scripts\Activate.ps1
pip install -e .[dev,hotkey]  # add ,cv for tier-C CV deps (opencv, dxcam, tesseract)
pytest                       # ~1240 collected, 1 @requires_game deselected by default (run it with -m requires_game), recorded-sessions auto-skip if absent
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

# Overnight one-shot: launch ED as CmdrOne, join CmdrFour private group,
# AFK travel for 6h, record everything to ~/ed-afk-sessions/
ed-autojump run --launch --commander CmdrOne --group CmdrFour \
    --record --engage-keys --route-plot --duration 21600
```

> **Dependency note:** the bot uses `pydirectinput-rgx` (the fork
> with explicit `scancode_keyDown`/`scancode_keyUp`), NOT the upstream
> `pydirectinput`. If you have the wrong package, `doctor` fails loudly:
> `pip uninstall pydirectinput && pip install pydirectinput-rgx`.

## Orienting the ship — nav-compass alignment

A blind key-presser can engage the FSD but can't *point* at the next system,
so it would fire while still aimed at the arrival star. Alignment closes that
loop: the bot reads the in-cockpit **nav compass** (item 13 on the HUD — the
small disc left of the radar), then pitches/yaws until the target dot is
centred and **in front** (filled, not hollow) before it jumps. A second,
finer pass then drives the reticle ring onto the HUD widget. A failed
alignment **blocks** the jump — vision uncertainty fails safe.

This closed-loop alignment is exactly the burden the harness exists to remove:
holding an analog axis steady on a moving target through an FSD spool is a
precise, sustained input task. The bot performs it from the compass read so the
pilot doesn't have to.

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
- **Supercruise Assist (orbit get-around + docking approach — load-bearing):**
  ED has **no keybind** for Supercruise Assist, so the bot can't toggle it with
  a key. The supported path is the nav-panel macro: lock a target, then engage
  assist through the panel. This is how `arrival` orbits the star to clear the
  next hop's geometry, and how the `dock` lane flies the station approach — so
  **the ship must have Supercruise Assist available (blue-zone throttle mode
  fitted) and an Advanced Docking Computer installed.** ED exposes no
  assist-engaged flag, so engagement is unprovable in-code; the steps post-check
  "still in supercruise" and degrade to a direct path if assist refuses.

## Launching the game

The bot can drive `MinEdLauncher.exe` (rfvgyhn fork) end-to-end:

1. **First-time setup — per commander cred onboarding.** On the
   non-sandboxie install, each Frontier account needs a `.cred` file
   under `%LOCALAPPDATA%\min-ed-launcher\` (DPAPI binds these to your
   user+machine so sandbox copies don't transfer). Run the wizard once:

   ```pwsh
   ed-autojump setup-frontier-creds --commanders CmdrOne CmdrTwo CmdrThree CmdrFour
   ```

   For each commander missing a cred, the wizard spawns MEL interactively
   so you can log in. Once the cred file lands the wizard moves on.

2. **Calibrate the main-menu navigator (per commander).** ED's main menu
   has no CLI flag for private-group selection — the bot navigates the UI
   with arrow keys after launch. Calibration captures the press counts:

   ```pwsh
   ed-autojump calibrate-menu --commander CmdrOne
   ed-autojump calibrate-menu --commander CmdrFour --is-owner
   ```

   The wizard prints a TOML snippet to paste into `config.toml`. Repeat
   per commander you want to launch through the bot. Set
   `[menu_nav].enabled = true` after at least one is calibrated.

3. **Standalone launch (no AFK loop after):**

   ```pwsh
   ed-autojump launch --commander CmdrOne --group CmdrFour
   ```

   The flow: dryrun pre-flight (catches stale `.cred` hang) → spawn MEL
   → wait for `Music{MainMenu}` journal event → navigate to PG →
   verify `LoadGame` group matches → exit.

4. **All-in-one overnight:**

   ```pwsh
   ed-autojump run --launch --commander CmdrOne \
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
    docking/              # docking pre-flight predicates + permission flow
    launcher/             # MEL spawn + dryrun + menu nav + wizards + flow
    binds/                # bundled ED-AFK.4.2.binds preset
    data/                 # bundled FSD constants (fsd_modules.json)
    orchestrator.py       # main loop (JournalTail -> dispatch -> Recorder)
    panic.py              # thread-safe panic switch (poll + trip + on_trip callback)
    recorder.py           # session JSONL writer (overnight capture)
    anonymizer.py         # scrub CMDR / FID / AccountID from session JSONL
    session_audit.py      # pure functions for safety asserts on recorded sessions
    doctor.py             # pre-flight checks (binds + dirs + EDHM + pydirectinput)
  tests/
    fixtures/journals/    # anonymized real-journal samples
    test_*.py             # ~1240 offline tests, 1 @requires_game stub
  scripts/
    nightly-run.ps1       # Tier-2 unattended runner (manual or task-scheduled)
    ed-afk-nightly.xml    # Task Scheduler XML (manual import only)
  calibration/
    README.md             # what to validate in-game for tier-C behaviour
    overnight-runbook.md  # Tier-1/2 capture + morning regression-check loop
```

## Capability status

Honest state on `master`. "Shipped" means wired into the live path and covered
by the offline suite; it does **not** mean live-proven. The authoritative
per-step audit, including every open defect, is
[`docs/ACTION_MEGASHEET.md`](../../docs/ACTION_MEGASHEET.md).

| Capability | Status |
|---|---|
| Journal / Status / NavRoute readers, in-memory FSM | shipped |
| Bundled binds preset + StartPreset swap/restore | shipped |
| Honk (parallel discovery-scan track) | shipped |
| Jump + escape + route-safety danger filter | shipped |
| Fuel scoop (`scoop_refuel`) | shipped — **NOT live-tested** |
| EDDN publisher (opt-in) | shipped |
| EDHM detect + vision calibration | shipped |
| Nav-compass + widget-ring alignment (fail-closed) | shipped |
| Orchestrator main loop + panic + Spansh + doctor | shipped |
| MinEdLauncher launch + main-menu / private-group nav | shipped |
| Docking lane (`dock` / `dock_resume`) + Starport Services | shipped — `dock_approach` step merged on master; **not yet live-tested end-to-end** |
| `sc_resume` fast-resume lane | shipped — **can ram a star** (defect #2) |
| `smack_recovery` exclusion-zone escape | shipped — **can mis-flip** (defect #3) |
| FSS keyboard sweep / FSS CV-assisted | framework stub — not built |
| DSS 6-direction surface scan | framework stub — not built |
| Ships without SC-assist / Advanced Docking Computer | unsupported |

## CV debug overlay (opt-in)

See what the bot is looking at, live: with [EDMCOverlay](https://github.com/inorton/EDMCOverlay)
installed, every CV read (compass, widget-ring, nav-panel, sun probe, …)
flashes an outlined box over the captured region in-game — white = looked,
green = detector hit, red = looked but found nothing. Cosmetic and fail-soft;
it can never slow or crash a flight, and it ships **off**.

Turn it on (pick one):

- `config.toml` / `config.local.toml` (the latter is gitignored —
  machine-local): `[overlay]` → `cv_debug = true`
- env var: `ED_AUTOJUMP_OVERLAY_CV_DEBUG=1` (also honored from a gitignored
  `.env` file next to `config.toml`)

Overlay coordinates aren't 1:1 with screen pixels, so first run
`ed-autojump calibrate-overlay` (in-game, docked or in a menu): nudge the
test outline with the arrow keys (shift+arrows scales, PgUp/PgDn changes the
step) until it hugs the target, then press `s` to save. Keep Elite focused
while tuning — the overlay only renders over the foreground game window.

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
