# ed-autojump

Autonomous exploration bot for Elite Dangerous: Odyssey. First tool in the
ED-AFK monorepo. Honks, scoops, jumps, optionally FSS / DSS / docks.

> **Status:** v0.2 — all 11 phases shipped on `main`. Phases 0–6
> production-ready (offline-verified, 179 tests passing under triple-test
> discipline). Phases 7–11 ship as framework + offline replay tests;
> in-game evidence deferred pending live calibration. See SPEC.md §17
> for phase exit criteria; `calibration/README.md` for what to validate
> in-game.

## Quick start

```pwsh
cd projects/ed-autojump
py -3.11 -m venv .venv      # 3.11, 3.12, 3.13, or 3.14 all work
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]       # add ,cv for tier-C CV deps (opencv, dxcam, tesseract)
pytest                       # 200+ pass, 3 @requires_game deselected, recorded-sessions auto-skip if absent
ed-autojump --help          # console entry point

# Unattended overnight capture (Tier 2 — see calibration/overnight-runbook.md)
.\scripts\nightly-run.ps1 -DurationHours 6
```

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
    launcher/             # v2 min-ed-launcher detection + argv
    binds/                # bundled ED-AFK.4.2.binds preset
    data/                 # bundled FSD constants (fsd_modules.json)
    recorder.py           # session JSONL writer (overnight capture)
    anonymizer.py         # scrub CMDR / FID / AccountID from session JSONL
    session_audit.py      # pure functions for safety asserts on recorded sessions
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
| 11 | headless launcher (v2) | framework — deferred pending calibration |

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

MIT.
