# ed-autojump

Autonomous exploration bot for Elite Dangerous: Odyssey. First tool in the
ED-AFK monorepo. Honks, scoops, jumps, optionally FSS / DSS / docks.

> **Status:** v0.2 — phases 0–6 ship as production-ready (offline-verified);
> phases 7–11 ship as framework + offline replay tests. In-game evidence
> for phases 7–11 is deferred pending live calibration. See SPEC.md for
> the design and §17 for phase exit criteria; `calibration/README.md` for
> what to validate in-game.

## Quick start

```pwsh
cd projects/ed-autojump
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
pytest
```

## Layout

```
projects/ed-autojump/
  pyproject.toml
  src/ed_autojump/
    cli.py                # entry point
    config.py             # config.toml loader
    state.py              # in-memory FSM
    journal/              # journal tail + event models
    status/               # Status.json + NavRoute.json watchers
    keys/                 # binds parser + DirectInput sender
    fsd/                  # fuel math + danger list
    planner/              # Spansh integration + filters
    executor/             # state-driven macros (honk, escape, scoop, jump)
    eddn/                 # EDDN publisher (opt-in)
    hud/                  # EDHM detect, GraphicsConfigurationOverride writer
    vision/               # tier-C CV (FSS / DSS) — gated
    fixtures_internal/    # bundled .binds + EDHM preset + FSD constants
  tests/
    fixtures/journals/    # anonymized real-journal samples
    test_*.py
  calibration/
    README.md             # what to run in-game to validate tier-C behaviour
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
