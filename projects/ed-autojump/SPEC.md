# ed-autojump — Specification (v0.2)

Autonomous exploration bot for Elite Dangerous: Odyssey. Designed to be left
running unattended for long routes while honking, scooping, scanning, and
mapping. First tool in the **ED-AFK** monorepo.

> **Status:** design draft, not implemented. Numbers cited from real journal
> samples on the developer's machine (`Journal.2026-01-09T194605.01.log`,
> Cutter, MaxJumpRange 31.29 LY, 64 t tank, `int_hyperdrive_overcharge_size7_class5`)
> and from the cited primary sources at the end of this document. Items marked
> **`VERIFY`** must be re-confirmed against a supervised calibration session
> before the related code is trusted.

---

## Table of contents

1. [Goals & non-goals](#1-goals--non-goals)
2. [Repository context](#2-repository-context)
3. [Architecture](#3-architecture)
4. [Inputs — what we read](#4-inputs--what-we-read)
5. [Outputs — how we drive the game](#5-outputs--how-we-drive-the-game)
6. [CV-friendly HUD via EDHM](#6-cv-friendly-hud-via-edhm)
7. [Vision pipeline](#7-vision-pipeline-tier-c-only)
8. [FSD math + routing](#8-fsd-math--routing)
9. [Per-requirement implementation](#9-per-requirement-implementation)
10. [Docking — designed for v2](#10-docking--designed-for-v2)
11. [Master state machine](#11-master-state-machine)
12. [Safety & abort](#12-safety--abort)
13. [Configuration](#13-configuration)
14. [Telemetry & community data](#14-telemetry--community-data)
15. [Testing strategy](#15-testing-strategy)
16. [Risks & open questions](#16-risks--open-questions)
17. [Implementation phases](#17-implementation-phases)
18. [References](#18-references)

---

## 1. Goals & non-goals

### 1.1 Requirements (verbatim from user)

| # | Requirement | Tier* | Source of truth |
|---|---|---|---|
| 1 | Jump to any destination | A | Spansh route + journal `FSDTarget` / `FSDJump` |
| 2 | Refuel on every KGBFOAM star | A | journal `FSDJump.StarClass` + `Status.Flags.ScoopingFuel` |
| 3 | Never plot a route that runs out of fuel | A | pre-route filter + per-leg check |
| 4 | Honk every system | A | journal `FSSDiscoveryScan` |
| 5 | FSS every body in every system | C | journal `FSSAllBodiesFound` + screen CV |
| 6 | DSS-map every body | C | journal `SAAScanComplete` + screen CV |
| 7 | Never collide with the star on exit | A | pre-jump throttle + post-exit pitch macro |

*Tiers: **A** = journal-driven, deterministic. **B** = journal + input simulation,
deterministic. **C** = screen capture + CV; non-deterministic, requires iterative
tuning.

### 1.2 Non-goals for v1

- Combat / interdiction handling beyond a "panic boost-and-flee" abort.
- Docking, repairs, refit, cartographic data sale — designed for v2 (§10).
- Surface landings, SRV operations, exobiology footfall scans — out of scope.
- DSS mapping of bodies that miss the efficiency target — one shot, no retry.
- Anti-detection / evasion. The bot uses public files and DirectInput; we
  accept that Frontier *could* detect input cadence patterns. The project
  owner has run similar automation on fleet carriers and even streamed it
  without action. We proceed on that basis.

### 1.3 Anti-goals (do not do)

- Do **not** use vanilla `SendInput` or `keybd_event`. ED ignores synthetic
  input unless it comes via DirectInput scancodes (`KEYEVENTF_SCANCODE`).
  All prior art confirms this (skai2/EDAutopilot, EDAPGui, EDAutopilot-v2).
- Do **not** trust Supercruise Assist for body approach — it has known
  collision bugs (rams planets) per ED wiki and community testing.
- Do **not** treat the in-game route plotter as fuel-safe. The reference
  journal samples show it routing through `StarClass:"DA"` (white dwarf,
  non-scoopable, exclusion-zone hazard) without warning. Route filtering must
  happen at the planner layer.
- Do **not** auto-install EDHM or write to the game's executable directory
  without explicit user consent.
- Do **not** modify the player's existing `.binds` files. Always use a
  separate named preset (`ED-AFK`) and swap via `StartPreset.4.start`.

---

## 2. Repository context

This spec lives at `projects/ed-autojump/SPEC.md` inside the **ED-AFK** monorepo
([github.com/Quadstronaut/ED-AFK](https://github.com/Quadstronaut/ED-AFK)). The
monorepo will eventually host additional AFK tools (mining bot, Robigo runner,
exobiology hunter, etc.). Shared concerns — journal parsing, key injection,
Status watcher, route math, CV calibration — will graduate into
`packages/edafk-core/` once the second tool starts. For v1, all code lives
under `projects/ed-autojump/src/`.

**License:** MIT, with the caveat that if we incorporate code from GPL'd
projects (notably EDMC and EDDI) we will relicense or partition the affected
modules before merge. The default borrow list (§3.4) is structured to keep us
MIT-clean.

---

## 3. Architecture

### 3.1 Process layout

```
┌──────────────────────────────────────────────────────────────────┐
│                        ed-autojump (single process)              │
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐        │
│  │ JournalTail  │───►│  GameState   │◄───│ StatusTail   │        │
│  │ (file watch) │    │  (in-mem FSM)│    │ (0.5 Hz poll)│        │
│  └──────────────┘    └──────┬───────┘    └──────────────┘        │
│         ▲                   │                   ▲                │
│         │                   ▼                   │                │
│  ┌──────┴───────┐    ┌──────────────┐    ┌──────┴───────┐        │
│  │ NavRoute     │    │  Planner     │    │ Screen       │        │
│  │ watcher      │    │  (Spansh +   │    │ Reader (CV)  │        │
│  │              │    │   filters)   │    │ [tier C only]│        │
│  └──────────────┘    └──────┬───────┘    └──────────────┘        │
│                             │                   ▲                │
│                             ▼                   │                │
│                      ┌──────────────┐           │                │
│                      │   Executor   │───────────┘                │
│                      │ (state-driven│                            │
│                      │   key macros)│                            │
│                      └──────┬───────┘                            │
│                             │                                    │
│                             ▼ pydirectinput scancodes            │
│                      ┌──────────────┐                            │
│                      │  Elite       │                            │
│                      │  Dangerous   │                            │
│                      └──────────────┘                            │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │ Optional sidecar: EDDN publisher (Scan/FSS events relay) │    │
│  └──────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

### 3.2 Why this shape (vs prior art)

- **Journal-first**: deterministic, robust to HDR/resolution. skai2/EDAutopilot
  uses CV for everything and is reported "lighting-dependent" with "alpha-quality"
  runs. We route around that pattern except where the journal is silent
  (FSS resolve, DSS probe aim, station nav bobble).
- **Single process with in-memory FSM**: Auto_Neutron's clipboard+AHK pattern
  is robust for "one shot" jump sequences but composes poorly for an always-on
  bot reacting to mid-flight events. EDMC's plugin architecture is elegant but
  observer-only — plugins cannot send keys. We keep the FSM in-process.
- **Planner separated from Executor**: route planning is a pure function of
  ship + system + galaxy data; the Executor only steps through approved
  waypoints. This lets us unit-test routing without simulating the game.
- **Screen reader is opt-in**: tier C requirements can be disabled. The bot
  remains useful as a tier-A honker even with CV off.
- **EDDN sidecar**: we relay our scan data back to the community by default
  (with opt-out). Frontier and the EDCD community benefit.

### 3.3 Language / runtime

- **Python 3.11+**, Windows-only (DirectInput is Windows-specific).
- Key dependencies (pin at first install):
  - `watchdog` — directory file-change events for journal / status / navroute
  - `pydirectinput` (or `pydirectinput-rgx` fork) — scancode key injection
  - `requests` — Spansh / EDSM / EDDN HTTP
  - `pydantic` v2 — event parsing with strong types
  - `numpy`, `opencv-python` — tier C only
  - `dxcam-cpp` — screen capture, tier C only
  - `pytesseract` (Tesseract 5) + optional `paddleocr` — OCR, tier C only
  - `pytest` + `pytest-asyncio` — tests
- Distribution: PyInstaller single-exe + sidecar config + binds preset +
  optional EDHM-UI profile JSON.

### 3.4 Borrowing plan

A separate document at `docs/shared/prior-art.md` (TODO — not yet written)
will catalogue 30 community projects in depth; the table below is the action
list for v1.

| # | Source | License | What we take | How |
|---|---|---|---|---|
| 1 | [EDAPGui](https://github.com/SumZer0-git/EDAPGui) — `directinput.py` | MIT | Full DirectInput SCANCODE table + `SendInput` ctypes wrapper | Direct copy with attribution comment |
| 2 | EDAPGui — `EDKeys.py` | MIT | `.binds` XML parser + key dispatcher with hold/repeat timing | Direct copy, strip GUI dependencies |
| 3 | EDAPGui — `StatusParser.py` | MIT | Status.json poller with full Flags/Flags2 bitmask decoder | Direct copy + extend for Odyssey 4.2 flags |
| 4 | EDAPGui — `NavRouteParser.py` | MIT | NavRoute.json + NavRouteClear handling | Direct copy |
| 5 | EDAPGui — `Screen.py` + `Screen_Regions.py` | MIT | mss-based capture w/ percentage ROIs | Borrow ROI definitions; replace `mss` with `dxcam-cpp` |
| 6 | EDAPGui — `EDJournal.py` | MIT | `getmtime`-change-detection journal tailer + ship-state dict | Study + reimplement clean (current is GUI-tangled) |
| 7 | EDAPGui — `Robigo.py` | MIT | Mission-loop state machine pattern | Study (don't copy verbatim) |
| 8 | [EDAutopilot-v2](https://github.com/Matrixchung/EDAutopilot-v2) — `utils/status.py` | MIT | Leaner alternate Status parser | Cross-reference for completeness |
| 9 | [EDDN schemas](https://github.com/EDCD/EDDN/tree/master/schemas) — `fssdiscoveryscan-v1.0.json`, `fssbodysignals-v1.0.json`, `fssallbodiesfound-v1.0.json`, `navroute-v1.0.json` | MIT | Canonical field-name reference for pydantic models | Direct copy as `schemas/` reference |
| 10 | [coriolis-data](https://github.com/EDCD/coriolis-data) — `modules/standard/frame_shift_drive.json` | MIT | FSD constants per class/rating | Direct copy as `data/fsd_modules.json` |
| 11 | [FDevIDs](https://github.com/EDCD/FDevIDs) | MIT | Journal event field reference CSVs (`commodity.csv`, `outfitting.csv`, `shipyard.csv`) | Embed as static data |
| 12 | [rster2002/ed-journals](https://github.com/rster2002/ed-journals) | MIT | Reference spec for typed journal events | Borrow event taxonomy when defining pydantic models |
| 13 | [COVAS:NEXT](https://github.com/RatherRude/Elite-Dangerous-AI-Integration) | MIT | Journal→event-routing pipeline architecture | Study, especially keystroke emulation patterns |
| 14 | [BioScan species deduction](https://github.com/Silarn/EDMC-BioScan) | GPL-2.0 | Body-type × star × region → organism filtering logic | Study, reimplement (don't copy — GPL contaminates) |
| 15 | [Pioneer value formulas](https://github.com/Silarn/EDMC-Pioneer) | GPL-2.0 | First-discovery / first-mapped credit math | Study, reimplement |

**Avoid copying from:** Numerlor/Auto_Neutron (GPL-3), EDMC (GPL-2), EDDI
(non-standard), Somfic/EliteAPI (no declared license). Their *ideas* are
free to apply; their *code* is not.

**Attribution:** every borrowed file gets a header comment naming the source
project, file path, commit hash, and license; `THIRD_PARTY_NOTICES.md` at the
repo root collates the same.

---

## 4. Inputs — what we read

### 4.1 File locations

`%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous\`
(confirmed present on target machine).

| File | Read pattern | Notes |
|---|---|---|
| `Journal.YYYY-MM-DDTHHMMSS.NN.log` | tail latest, follow rotation | UTF-8, one JSON event per line |
| `Status.json` | poll on FS change, ~2 Hz | rewritten in place, may briefly be empty during write |
| `NavRoute.json` | poll on FS change | written on `NavRoute` event, cleared on `NavRouteClear` |
| `Cargo.json`, `ShipLocker.json`, `Backpack.json`, `ModulesInfo.json`, `Outfitting.json`, `Shipyard.json` | read on demand | not in v1 hot path |
| `Market.json` | read on demand | docking phase only |
| `edmc-journal-lock.txt` | inspect at startup | non-blocking; warn if EDMC is running concurrently |

**Journal rotation behaviour:** A new file appears whenever the game restarts
*or* roughly every four hours of continuous play. We watch the directory
(not a specific file) and switch to the newest `Journal.*.log` when it appears.
On first attach we replay the current journal from the top so we know our
starting `Loadout`, current system, ship state, etc. **Never** start from
EOF — too much state is in earlier events.

### 4.2 Journal events the bot consumes

Real samples below are from the developer's Cutter journal of 2026-01-10.

#### 4.2.1 Startup / loadout

`Loadout` — fires at game start, ship swap, and outfitting changes.

```json
{ "event":"Loadout", "Ship":"cutter", "ShipID":14, "ShipName":"[Mine] Laser",
  "MaxJumpRange":31.288385,
  "FuelCapacity":{ "Main":64.000000, "Reserve":1.160000 },
  "UnladenMass":1705.699951,
  "Modules":[ /* including FrameShiftDrive item id */ ] }
```

**Bot extracts:**

- `Ship` (class identifier — `cutter`, `anaconda`, `dolphin`…)
- `MaxJumpRange` (LY, computed by game with current loadout + full fuel)
- `FuelCapacity.Main` (tonnes), `FuelCapacity.Reserve` (tonnes)
- `UnladenMass` (tonnes, for our own range calc)
- Scans `Modules` for:
  - `int_hyperdrive_*` — note `_overcharge_` variant = SCO-capable. Reference
    sample shows `int_hyperdrive_overcharge_size7_class5`.
  - `int_fuelscoop_size*_class*` — **bot hard-aborts startup if absent**.
  - `int_detailedsurfacescanner_tiny` — required for req 6.
  - `int_dockingcomputer_advanced` (v2 — required for docking).
  - `int_supercruiseassist` (optional — but we don't use it for body approach).

> **VERIFY:** the user's reference Loadout is a mining Cutter and the truncated
> module list in our sample didn't show a fuel scoop. The bot must scan the
> *entire* module list and fail-fast with a readable error if no scoop is
> fitted. The pre-flight check runs at every `Loadout` event, not just startup.

#### 4.2.2 Routing

`FSDTarget` — fires when the next jump target locks, **before** the jump
starts. The single most important event for safety:

```json
{ "event":"FSDTarget", "Name":"V886 Centauri", "SystemAddress":2931071912299,
  "StarClass":"DA", "RemainingJumpsInRoute":1 }
```

`StarClass` arrives before we commit to the jump. The bot inspects it and
**refuses** to engage if the next star is on the danger list (§8.4).

`NavRoute` — fires when a route is plotted; payload is in `NavRoute.json`:

```json
{ "Route":[ {"StarSystem":"...","SystemAddress":N,"StarPos":[x,y,z],"StarClass":"K"}, ... ] }
```

`NavRouteClear` — fires when the route is completed or cancelled.

#### 4.2.3 Jump cycle

`StartJump` — countdown start (FSD charge → witch space).

```json
{ "event":"StartJump", "JumpType":"Hyperspace", "StarSystem":"...",
  "SystemAddress":N, "StarClass":"K" }    // hyperspace variant carries StarClass
{ "event":"StartJump", "JumpType":"Supercruise", "Taxi":false }   // SC variant — no class
```

The 5-second hyperspace countdown is when the bot **must** zero its throttle —
post-exit is too late (see §9.2).

`FSDJump` — exit from witch space; we're now in the new system.

```json
{ "event":"FSDJump", "StarSystem":"Core Sys Sector QT-R b4-6",
  "SystemAddress":13864825595321, "StarPos":[-14.78125,8.59375,35.03125],
  "Body":"Core Sys Sector QT-R b4-6 A", "BodyID":1, "BodyType":"Star",
  "JumpDist":72.436, "FuelUsed":4.981084, "FuelLevel":27.018915 }
```

`BodyType:"Star"` confirms we arrived at a star (the normal case).
`FuelLevel` is remaining fuel after the jump — feed to the fuel guard.
`StarClass` is **not** in `FSDJump` — only in the prior `FSDTarget` and
`StartJump`. The bot caches the upcoming `StarClass` and pairs it with the
`FSDJump` arrival event.

`SupercruiseEntry` — entering supercruise from normal space.
`SupercruiseExit` — dropping out at a body. `Body` and `BodyType` tell us what
we're near. Observed values: `"Star"`, `"Planet"`, `"Station"`.

#### 4.2.4 Fueling

`FuelScoop` — fires repeatedly while scooping. `Scooped` is delta this tick;
`Total` is total fuel currently in the tank.

```json
{ "event":"FuelScoop", "Scooped":4.981064, "Total":32.000000 }
```

The bot considers scooping complete when `Total >= FuelCapacity.Main * 0.98`.
We don't trust `>= Main` exactly due to float rounding.

`ReservoirReplenished` — passive top-up of the reservoir from the main tank.
Informational, not a trigger.

#### 4.2.5 Exploration

`FSSDiscoveryScan` — the **honk** completed. `Progress:1.0` = fully resolved.

```json
{ "event":"FSSDiscoveryScan", "Progress":1.000000, "BodyCount":28,
  "NonBodyCount":52, "SystemName":"Kokoller", "SystemAddress":9704579273450 }
```

`FSSAllBodiesFound` — all bodies in the system have been FSS-resolved.

```json
{ "event":"FSSAllBodiesFound", "SystemName":"...", "SystemAddress":N, "Count":2 }
```

`FSSSignalDiscovered` — non-body signals (nav beacon, station, USS, installation).

```json
{ "event":"FSSSignalDiscovered", "SystemAddress":N, "SignalName":"Daley Vision",
  "SignalType":"Outpost" }
```

`FSSBodySignals` — per-body bio/geo signals revealed by FSS or DSS.

`Scan` — body data. `ScanType` is `"AutoScan"` (free on arrival),
`"Detailed"` (full FSS), or `"NavBeacon"`.

```json
{ "event":"Scan", "ScanType":"AutoScan", "BodyName":"... A", "BodyID":1,
  "StarType":"M", "Subclass":3, /* ... */
  "WasDiscovered":true, "WasMapped":false }
```

The bot maintains a per-system table keyed by `BodyID`:
`{type, distance_ls, mass, was_discovered, was_mapped, fss_done, dss_done}`.

`SAAScanComplete` — DSS finished mapping a body.

```json
{ "event":"SAAScanComplete", "BodyName":"... 1 a", "BodyID":2,
  "ProbesUsed":6, "EfficiencyTarget":9 }
```

`SAASignalsFound` — bio/geo signals revealed by DSS.

#### 4.2.6 Hazards

| Event | Trigger / action |
|---|---|
| `HullDamage` | Health < panic_threshold → PANIC |
| `Music: Combat_Dogfight` | heuristic interdiction → PANIC |
| `Interdicted` | submit + boost run (configurable) |
| `JetConeBoost` | unexpected for our route (we filter dangerous classes) → PANIC |
| `JetConeDamage` | retract everything, flee |
| `UnderAttack` | log and check threat |
| `ReceiveText` w/ `Channel:"npc"` and aggressive content | informational |
| `ShutDown` | clean exit |

#### 4.2.7 Docking events (v2)

Sequence and field set documented in §10.2. The relevant events are
`DockingRequested`, `DockingGranted`, `DockingDenied`, `DockingTimeout`,
`DockingCancelled`, `Docked`, `Undocked`, plus `ApproachSettlement` for
Odyssey surface bases.

### 4.3 Status.json — flags & cadence

Update cadence: ~1–2 Hz baseline, more often on flag/position change. Single
JSON object, rewritten in place. May briefly be empty mid-write — parse with
retry-on-error.

| Field | Type | Used for |
|---|---|---|
| `Flags` | uint32 bitfield | the primary fast-path state probe |
| `Flags2` | uint32 bitfield | on-foot states (we should not be) |
| `Fuel.FuelMain` | float | live fuel for guard checks |
| `Fuel.FuelReservoir` | float | reservoir for emergency calc |
| `GuiFocus` | uint | which UI panel is open |
| `Pips` | [int,int,int] | weapons/engines/systems half-pips |
| `FireGroup` | uint | active firegroup index |
| `Heat` | float (where present) | cockpit temp, 0..1+ |
| `LegalState` | string | "Clean" / "Wanted" / "Hostile" / etc. — gates docking |
| `BodyName` | string | what body we're near |
| `Latitude/Longitude/Altitude/Heading` | float | landed/glide only |
| `Destination` | object | targeted destination details |

#### 4.3.1 Flags bits we care about

| Bit | Hex | Name | Meaning |
|---|---|---|---|
| 0 | 0x00000001 | Docked | landed at a station |
| 1 | 0x00000002 | Landed | landed on a body |
| 2 | 0x00000004 | LandingGearDown | — |
| 3 | 0x00000008 | ShieldsUp | informational |
| 4 | 0x00000010 | Supercruise | gates state transitions |
| 8 | 0x00000100 | CargoScoopDeployed | — |
| 16 | 0x00010000 | FsdMassLocked | wait before jump |
| 17 | 0x00020000 | FsdCharging | jump committed |
| 18 | 0x00040000 | FsdCooldown | wait for cooldown end |
| 19 | 0x00080000 | LowFuel | redundant safety |
| 20 | 0x00100000 | Overheating | PANIC, deploy heat sink |
| 22 | 0x00400000 | IsInDanger | hostile contacts present |
| 23 | 0x00800000 | InWing | user joined wing → pause |
| 24 | 0x01000000 | InMainShip | required true |
| 25 | 0x02000000 | InFighter | abort |
| 26 | 0x04000000 | InSRV | abort |
| 27 | 0x08000000 | InAnalysisMode | FSS / DSS HUD active |
| 28 | 0x10000000 | NightVision | — |
| (varies) | — | ScoopingFuel | true while scooping |

> **VERIFY:** the exact bit position of `ScoopingFuel` and a few minor flags
> against [elite-journal.readthedocs.io](https://elite-journal.readthedocs.io/en/latest/Status%20File.html).
> Several community lists disagree on minor bits. EDAPGui's `StatusParser.py`
> is the most-tested implementation and serves as our reference.

#### 4.3.2 GuiFocus values

`0` = none, `1` = internal panel, `2` = external, `3` = comms, `4` = role,
`5` = stationServices, `6` = galaxyMap, `7` = systemMap, `8` = orrery,
`9` = FSS, `10` = SAA, `11` = codex. The bot uses these to confirm UI state
before sending UI keys.

#### 4.3.3 LegalState values (docking-relevant)

`"Clean"`, `"IllegalCargo"`, `"Speeding"`, `"Wanted"`, `"Hostile"`,
`"PassengerWanted"`, `"Warrant"`, `"Allied"`, `"Thargoid"`.

`"Wanted"` and `"Warrant"` cause `DockingDenied:Reason:Offences` at non-anarchy
stations. `"Hostile"` causes `DockingDenied:Reason:Hostile`. We refuse to
request docking unless `LegalState in {"Clean", "Allied"}` (configurable).

### 4.4 NavRoute.json

When the player (or bot) plots a multi-jump route, the file is rewritten with:

```json
{ "Route":[
  {"StarSystem":"Sol", "SystemAddress":N, "StarPos":[x,y,z], "StarClass":"G"},
  ...
]}
```

When the route is cleared (completed or cancelled), the file becomes:

```json
{ "timestamp":"...", "event":"NavRouteClear", "Route":[ ] }
```

(Yes, the cleared file misleadingly contains `event:"NavRouteClear"` — we
detect "no route" by checking `len(Route) == 0` rather than parsing the event
field.) The bot watches this file as a secondary truth source — if the player
manually plots a route, we follow it (after running it through the safety
filter).

### 4.5 Real-time tailing gotchas

- **First-attach replay:** read journal from the top, not the tail.
- **Rotation:** watch the *directory* via `watchdog`, not a specific file.
  When a new `Journal.*.log` appears, swap to it.
- **Status.json zero-length window:** during the game's rewrite, the file may
  be 0 bytes for a few milliseconds. Parse with retry on `JSONDecodeError`.
- **EDMC journal lock:** `edmc-journal-lock.txt` contains EDMC's PID. We don't
  lock; we just warn the user that another consumer is reading the same files.
  Multiple readers don't conflict (read-only on our side).
- **File encoding:** UTF-8 throughout. One JSON event per line in `.log` files.

---

## 5. Outputs — how we drive the game

### 5.1 Keybind strategy — dual-profile

The developer plays with a controller (gamepad confirmed in their
`Custom.4.2.binds`: `GamePad_RStickX`, `GamePad_LStickX`, `GamePad_LStickY`).
The bot needs a **separate keyboard-only preset** that doesn't disturb the
player's setup.

#### 5.1.1 Bind file format and locations

`%LOCALAPPDATA%\Frontier Developments\Elite Dangerous\Options\Bindings\`

| File | Purpose |
|---|---|
| `Custom.4.2.binds` (or `.4.0`, `.4.1`, etc.) | Player-saved preset |
| `ED-AFK.4.2.binds` | **Our bot's preset (we ship this)** |
| `StartPreset.4.start` | **4 lines: General / Ship / SRV / On Foot** — names which preset is active for each control category |
| `Custom.4.2.binds.<hash>.backup` | game's automatic backup |

> The minor version (`.4.0` / `.4.1` / `.4.2`) increments when Frontier adds
> binding slots. The developer's machine currently uses `.4.2`. The bot ships
> the latest minor version available at release; the game forward-migrates
> older minor versions.

#### 5.1.2 StartPreset.4.start is four lines, not one

Read from the developer's machine:

```
ConsoleX360
Custom
ConsoleX360
ConsoleX360
```

Line 1 = General, line 2 = Ship cockpit, line 3 = SRV, line 4 = On Foot. The
developer has a Custom Ship profile but ConsoleX360 elsewhere. To activate the
bot's profile, **all four lines** must be set to `ED-AFK` (or the bot needs
to also ship SRV and On-Foot variants — but we don't, since the bot only flies
the main ship).

For the four non-flight categories, we either:
- (a) Leave them alone (player's preference applies on-foot and in SRV); or
- (b) Write `ED-AFK` to all four, accepting that on-foot keys won't work for
  the player while the bot is active.

Option (a) is the right default: write `ED-AFK` to line 2 (Ship) only.

#### 5.1.3 Swap-on-launch script

```text
1. backup = read("StartPreset.4.start")
2. write("StartPreset.4.start.player-backup", backup)
3. lines = backup.split("\n")
4. lines[1] = "ED-AFK"   # ship-cockpit slot
5. write("StartPreset.4.start", "\n".join(lines))
6. launch ED (or have the user launch it)
7. on bot exit: copy player-backup back over the start file
```

The bot exposes a `--swap-binds` / `--restore-binds` CLI so the user can
trigger this without launching the bot. The restore is also wired to the
panic-hotkey path (§12.2).

#### 5.1.4 The .binds XML schema (essentials)

```xml
<?xml version="1.0" encoding="UTF-8" ?>
<Root PresetName="ED-AFK" MajorVersion="4" MinorVersion="2">
  <KeyboardLayout>en-US</KeyboardLayout>
  <!-- Mouse/joystick global setup ... -->

  <!-- Button binding -->
  <UseBoostJuice>
    <Primary Device="Keyboard" Key="Key_Tab" />
    <Secondary Device="{NoDevice}" Key="" />
  </UseBoostJuice>

  <!-- Binding with modifier -->
  <SomeAction>
    <Primary Device="Keyboard" Key="Key_J">
      <Modifier Device="Keyboard" Key="Key_LeftShift" />
    </Primary>
    <Secondary Device="{NoDevice}" Key="" />
    <ToggleOn Value="1" />
  </SomeAction>

  <!-- Analog axis -->
  <ThrottleAxis>
    <Binding Device="{NoDevice}" Key="" />
    <Inverted Value="0" />
    <Deadzone Value="0.00000000" />
  </ThrottleAxis>
</Root>
```

Device names: `Keyboard`, `Mouse`, named HID devices (`DualShock4`, `VPCThrottle`,
or raw VID/PID hex like `3537103E`), or `{NoDevice}` for explicitly unbound.
Keyboard keys use `Key_` prefix (`Key_W`, `Key_Tab`, `Key_LeftShift`,
`Key_Numpad_0`, `Key_UpArrow`, `Key_F1`, `Key_Apostrophe`, `Key_SemiColon`…).

**Critical:** the bot's `.binds` file must contain **every** schema element a
vanilla Custom.4.2.binds contains. Omitting elements causes ED to inject
inherited defaults from the vendor presets — surprising. The shipped file is
generated from a vanilla template with our specific bindings substituted in.

#### 5.1.5 Logical action catalogue

Confirmed XML binding tag names (cross-referenced against the developer's
`Custom.4.2.binds` and community sources):

| Function | XML tag | Bot use |
|---|---|---|
| FSD jump (super+hyper combo) | `HyperSuperCombination` | req 1 |
| Supercruise engage | `Supercruise` | post-arrival |
| Hyperspace jump | `Hyperspace` | (combo binding usually) |
| Target next system in route | `TargetNextRouteSystem` | route follow |
| Throttle forward | `ForwardKey` | scoop entry |
| Throttle reverse | `BackwardKey` | rare |
| Set speed 0% | `SetSpeedZero` | **pre-jump (req 7)** |
| Set speed 25 / 50 / 75 / 100% | `SetSpeed25/50/75/100` | scoop technique |
| Pitch up button | `PitchUpButton` | post-exit escape |
| Pitch down button | `PitchDownButton` | scoop |
| Yaw L/R | `YawLeftButton`/`YawRightButton` | scoop |
| Roll L/R | `RollLeftButton`/`RollRightButton` | mailslot align (v2) |
| Lateral thrust L/R | `LeftThrustButton`/`RightThrustButton` | docking (v2) |
| Vertical thrust U/D | `UpThrustButton`/`DownThrustButton` | docking (v2) |
| Boost | `UseBoostJuice` | emergency |
| Deploy/retract hardpoints | `DeployHardpointToggle` | scoop requires retracted |
| Deploy/retract cargo scoop | `ToggleCargoScoop` | (auto with hardpoints retracted near star) |
| Landing gear | `LandingGearToggle` | docking (v2) |
| Deploy heat sink | `DeployHeatSink` | panic |
| Chaff | `FireChaffLauncher` | combat (rare) |
| Charge ECM | `ChargeECM` | combat (rare) |
| Primary fire | `PrimaryFire` | DSS probe fire |
| Secondary fire | `SecondaryFire` | (rare) |
| Cycle fire group next/prev | `CycleFireGroupNext` / `…Previous` | DSS swap |
| **Honk** (Discovery Scanner) | `ExplorationFSSDiscoveryScan` | **req 4 — confirmed in dev's binds** |
| Enter FSS | `ExplorationFSSEnter` | req 5 |
| Exit FSS | `ExplorationFSSQuit` | req 5 |
| FSS tune increase / decrease | `ExplorationFSSRadioTuningX_Increase` / `…Decrease` | req 5 |
| FSS zoom in / out | `ExplorationFSSZoomIn` / `…Out` | req 5 |
| FSS camera pitch up / down | `ExplorationFSSCameraPitchIncreaseButton` / `…DecreaseButton` | req 5 |
| FSS camera yaw L / R | `ExplorationFSSCameraYawDecreaseButton` / `…IncreaseButton` | req 5 |
| FSS target signal | `ExplorationFSSTarget` | req 5 |
| **DSS (SAA): no dedicated Enter** — use `PlayerHUDModeToggle` (Analysis Mode) + fire group with DSS | — | req 6 — confirmed |
| SAA exit third person | `ExplorationSAAExitThirdPerson` | req 6 |
| SAA probe view toggle | `ExplorationSAAChangeScannedAreaViewToggle` | req 6 |
| SAA probe aim L / R / U / D | `SAAThirdPersonYawLeft/RightButton`, `…PitchUp/DownButton` | req 6 |
| UI focus (hold to open panels) | `UIFocus` | inventory / nav panel |
| Left/Right panels | `FocusLeftPanel` / `FocusRightPanel` | docking request (v2) |
| Cycle next panel / page | `CycleNextPanel` / `CycleNextPage` | nav |
| UI movement | `UI_Up` / `UI_Down` / `UI_Left` / `UI_Right` | menu nav |
| UI select / back | `UI_Select` / `UI_Back` | menu nav |
| Galaxy / System map open | `GalaxyMapOpen` / `SystemMapOpen` | route plot |
| Analysis HUD toggle | `PlayerHUDModeToggle` | FSS/SAA entry |
| Pause menu | `Pause` | emergency stop |

Notable absences (no dedicated binding tag):
- **RequestDocking** — done via Contacts panel in Left Panel.
- **SelectFireGroup1/2/N** — only Next/Previous cycle exist; bot counts cycles.

#### 5.1.6 Sample bot binds (fragment)

`ED-AFK.4.2.binds`, abbreviated:

```xml
<?xml version="1.0" encoding="UTF-8" ?>
<Root PresetName="ED-AFK" MajorVersion="4" MinorVersion="2">
  <KeyboardLayout>en-US</KeyboardLayout>

  <HyperSuperCombination>
    <Primary Device="Keyboard" Key="Key_J" />
    <Secondary Device="{NoDevice}" Key="" />
  </HyperSuperCombination>

  <TargetNextRouteSystem>
    <Primary Device="Keyboard" Key="Key_H" />
    <Secondary Device="{NoDevice}" Key="" />
  </TargetNextRouteSystem>

  <SetSpeedZero>
    <Primary Device="Keyboard" Key="Key_Numpad_0" />
    <Secondary Device="{NoDevice}" Key="" />
  </SetSpeedZero>

  <SetSpeed75>
    <Primary Device="Keyboard" Key="Key_Numpad_7" />
    <Secondary Device="{NoDevice}" Key="" />
  </SetSpeed75>

  <PitchUpButton>
    <Primary Device="Keyboard" Key="Key_S" />
    <Secondary Device="{NoDevice}" Key="" />
  </PitchUpButton>

  <UseBoostJuice>
    <Primary Device="Keyboard" Key="Key_Tab" />
    <Secondary Device="{NoDevice}" Key="" />
  </UseBoostJuice>

  <DeployHeatSink>
    <Primary Device="Keyboard" Key="Key_V" />
    <Secondary Device="{NoDevice}" Key="" />
  </DeployHeatSink>

  <ExplorationFSSDiscoveryScan>
    <Primary Device="Keyboard" Key="Key_F" />
    <Secondary Device="{NoDevice}" Key="" />
  </ExplorationFSSDiscoveryScan>

  <ExplorationFSSEnter>
    <Primary Device="Keyboard" Key="Key_Apostrophe" />
    <Secondary Device="{NoDevice}" Key="" />
  </ExplorationFSSEnter>

  <!-- ...all other schema elements present with {NoDevice}... -->
</Root>
```

The full file (~70 KB; same size as the dev's `Custom.4.2.binds`) ships at
`projects/ed-autojump/binds/ED-AFK.4.2.binds`.

### 5.2 Input injection

#### 5.2.1 Library and protocol

- `pydirectinput` (or `pydirectinput-rgx` fork). The library wraps Win32
  `SendInput` with `KEYEVENTF_SCANCODE` set, which is what ED listens to.
  Vanilla `SendInput` virtual-key codes are ignored.
- Scancode table comes from EDAPGui's `directinput.py` (MIT, attributable).

#### 5.2.2 Timing constants (community-derived; calibrate per-ship)

| Action | Hold / wait | Source |
|---|---|---|
| `ExplorationFSSDiscoveryScan` (honk) | **6000 ms** hold | `mwmike/edhud` AHK script |
| Inter-action gap (min) | **50 ms** | EDAPGui `key_mod_delay=10ms` + safety margin |
| Inter-action gap (between firegroup cycle and trigger) | **150–250 ms** | EDAPGui `key_def_hold_time=200ms` |
| FSD charge (waiting for `StartJump`) | **15–20 s** after engage | community AHK scripts |
| FSD cooldown (after `FSDJump`) | **~5 s** (normal exit) | wiki, Mass Lock article |
| Forced-exit FSD cooldown | **~45 s** | ED wiki |
| SC disengage window (from "TO DISENGAGE" prompt) | **~7 s** | AHK scripts |
| Throttle settle after `SetSpeedX` | **~100 ms** | EDAPGui timing |

#### 5.2.3 Sender pattern

```python
class Sender:
    def press(self, action: str, *, hold: float = 0.05):
        scancode = self.binds.resolve(action)   # logical → scancode
        pydirectinput.keyDown(scancode=scancode)
        time.sleep(hold)
        pydirectinput.keyUp(scancode=scancode)

    def chord(self, *actions, hold: float = 0.1):
        # press all, hold, release all
        ...
```

Every send is logged with `(timestamp, action, scancode, hold)` for
post-hoc debugging of failed sequences.

---

## 6. CV-friendly HUD via EDHM

### 6.1 What EDHM is, technically

EDHM is a **3Dmigoto** mod — a DirectX 11 hooking framework. Installing EDHM
drops a modified `d3d11.dll` into the ED executable directory (typically
`%ProgramFiles(x86)%\Steam\steamapps\common\Elite Dangerous\Products\elite-dangerous-64\`).
The game loads this DLL instead of the system one; 3Dmigoto intercepts shader
calls and substitutes modified pixel shaders that recolor HUD elements
individually. No Frontier files are modified. The community consensus (per
the Frontier-forums EDHM thread) is that Frontier tolerates the mod because
it satisfies four criteria: no original-file modification, no ARX item
modification, no cheating advantage, game remains recognisable.

### 6.2 Detection on the developer's machine

Confirmed install path (user-supplied):
`C:\Users\Quadstronaut\AppData\Local\EDHM-UI-V3\EDHM-UI-V3.exe`

EDHM-UI is the cross-platform GUI front-end for EDHM
([BlueMystical/EDHM_UI](https://github.com/BlueMystical/EDHM_UI), GPL-3.0).
It manages theme installs, palette browsing, in-game preview, and stores
themes under `My Documents` (so themes survive Frontier game-folder wipes).

### 6.3 Bot strategy: detect, recommend, fall back

At startup, the bot checks:

1. **Is EDHM-UI installed?** Look for `%LOCALAPPDATA%\EDHM-UI-V3\EDHM-UI-V3.exe`
   or registry uninstall key. If yes:
   - Inform the user that CV reliability is improved by an EDHM theme with
     specific colour anchors. Offer to import `ED-AFK-CV.json` (a shipped
     preset stored in `projects/ed-autojump/edhm-presets/`).
   - Never auto-install without consent.
2. **Is EDHM (the DLL) installed?** Check for `d3d11.dll` in the ED executable
   directory. (ED does not ship its own `d3d11.dll`; presence indicates 3Dmigoto.)
3. **If neither is installed**, fall back to `GraphicsConfigurationOverride.xml`
   (§6.4) — a vanilla, zero-dependency HUD recolour.

### 6.4 GraphicsConfigurationOverride.xml — the zero-dependency fallback

File path:
`%LOCALAPPDATA%\Frontier Developments\Elite Dangerous\Options\Graphics\GraphicsConfigurationOverride.xml`

Minimal HUD-cyan recolour:

```xml
<?xml version="1.0" encoding="UTF-8" ?>
<GraphicsConfig>
  <GUIColour>
    <Default>
      <LocalisationName>Standard</LocalisationName>
      <MatrixRed>   0, 1, 0 </MatrixRed>
      <MatrixGreen> 0, 0, 1 </MatrixGreen>
      <MatrixBlue>  1, 0, 0 </MatrixBlue>
    </Default>
  </GUIColour>
</GraphicsConfig>
```

Each matrix row is `(R_in, G_in, B_in)` for that channel's output. The above
swaps to cyan HUD. The bot writes its own file under a backup name and only
applies it with user consent. The XML is unaffected by game updates (unlike
the game-folder `GraphicsConfiguration.xml` which gets wiped).

### 6.5 Recommended CV palette

A saturated cyan or magenta HUD on near-black background gives the CV pipeline
the cleanest possible HSV anchor:

```
H = 180° (cyan) or 300° (magenta), S = 1.0, V = 0.95
```

No natural game environment produces fully saturated cyan/magenta at high
value — making the HUD-vs-background segmentation a trivial threshold
operation. Bonus: this is also a recommended palette for partial colourblindness.

### 6.6 Update survival

Frontier game updates that change shader hashes break EDHM (HUD reverts to
default; game still runs normally). EDHM maintainers typically patch within
days. Themes stored in `My Documents` survive — only the shader interception
breaks. The bot tolerates the broken state by falling back to relaxed colour
thresholds and warning the user.

---

## 7. Vision pipeline (tier C only)

CV is used only for:

- FSS tuning + signal-blob resolve (req 5)
- DSS probe targeting + coverage check (req 6)
- (v2) Station mailslot detect and cockpit nav-bobble read for docking align

The bot's tier-A code path (req 1, 2, 3, 4, 7) does not touch CV.

### 7.1 Capture: dxcam-cpp

**Choice:** [`dxcam-cpp`](https://github.com/Fidelxyz/DXCam-CPP) — DXGI
Desktop Duplication, ~240 fps ceiling on modern hardware, C++ core with Python
bindings, API-compatible drop-in for the older `dxcam`. Last release Dec 2025,
sustainable maintenance.

**Fallback:** [`windows-capture`](https://github.com/NiiightmareXD/windows-capture)
— Windows.Graphics.Capture (WGC), better compatibility with exclusive
fullscreen on Win11.

The bot wraps both behind a `CaptureBackend` abstract class so swap is ~5
lines of change.

### 7.2 HDR considerations

On HDR systems, DXGI Duplication returns `DXGI_FORMAT_R16G16B16A16_FLOAT`
(scRGB FP16). Naive cast to uint8 produces blown highlights and crushed
shadows. Three options:

- **Require SDR + borderless windowed** (v1 policy).
- Detect HDR via `IDXGIOutput6::GetDesc1().ColorSpace` (callable from
  ctypes) and warn the user at startup with reduced-reliability mode.
- Implement tone-mapping (Reinhard or OBS-style) on captured frames. Not in
  v1 — calibration mode (§7.3) partially compensates.

### 7.3 Calibration mode

Template matching at fixed RGB / HSV breaks across HDR, EDHM palettes,
resolution scaling, and Windows DPI. **Solution:** per-install calibration.

On first run (or via `--calibrate`):

1. Bot prompts the user to open a known menu (e.g. main menu → Controls).
2. Bot captures one frame.
3. User clicks 3–5 HUD anchor points (e.g. menu border, button background,
   selected-item highlight).
4. Bot records the actual HSV values at those pixels and stores them in
   `calibration/profile.json`:
   ```json
   { "hud_primary_hsv": [180, 250, 220],
     "hud_secondary_hsv": [205, 200, 200],
     "background_value_max": 30,
     "screen_w": 2560, "screen_h": 1440,
     "hdr_active": false,
     "rois_relative": { ... } }
   ```
5. All subsequent CV operations use profile-adjusted thresholds.

This beats template matching because:
- Works for any EDHM palette
- Works at any resolution (ROIs are stored as fractions)
- Works with HDR-on-but-degraded (captured hue is consistently shifted; offset compensates)

### 7.4 Per-element CV approach

#### 7.4.1 FSS signal blobs

Signals appear as faint blue-white points that resolve to crisp rings when
tuned. Two-pass:

1. **Coarse:** HSV mask using profile thresholds → `cv2.SimpleBlobDetector` with
   `filterByArea(min=5px, max=200px)` → list of blob centroids. Mask out the
   stellar bloom zone (top 30% of FSS view when star is centred).
2. **Fine:** when tuned to a candidate, `cv2.HoughCircles` on the
   grayscale-thresholded frame to confirm ring formation at the expected
   radius (5–40 px depending on zoom).

#### 7.4.2 DSS coverage heatmap

The probe-coverage overlay is a distinct colour wash (green / yellow) on a
darker background. HSV-threshold the covered zone → `connectedComponentsWithStats`
→ `covered_pixels / body_pixels`. If `< 0.90`, find the largest uncovered
centroid → that's the next probe aim point in normalised 2-D projection
coordinates.

#### 7.4.3 Cockpit nav compass (v2 — docking)

EDAPGui implements this with `match_template_in_region_x3('compass')` plus a
nested template match for the navpoint dot. The dot's offset from compass
centre maps directly to target azimuth/elevation in ship frame. The dot is
filled (front hemisphere) or hollow (behind) — disambiguates "turn 30° left"
from "turn 150° right." Direct sunlight on the cockpit can defeat template
matching; the fallback is HSV thresholding using calibrated colour anchors.

#### 7.4.4 Station mailslot (v2)

Bright cyan letterbox glow on station face. HSV mask → `findContours` →
filter aspect ratio > 3.5 → `minAreaRect`. Rotation tells us how to roll;
size tells us approximate distance. We hand off to the Advanced Docking
Computer once aligned — we don't try to fly through the slot ourselves.

### 7.5 OCR

- **Tesseract 5** with `--psm 7` (single line) for short numeric fields
  (distance LS, ammo count). 30 ms per call CPU. Pre-crop + upscale ROI.
- **PaddleOCR** (PP-OCRv3, `use_angle_cls=False`) for multi-word fields
  (station names, service lists). 200 ms per call after a 4-second warm-up.
  Loaded once at startup if `dss == "high_value_only"` is on, otherwise lazy.

### 7.6 Performance budget

| Task | Capture Hz | CV ms/frame | Notes |
|---|---|---|---|
| FSS sweep | 10 | ~15 | blob detect + HoughCircles |
| DSS probe | 30 | ~8 | HSV ratio + components |
| Docking align (v2) | 15 | ~12 | mailslot + compass |
| OCR (station panel, v2) | 2–3 | 30–200 | infrequent |
| Idle (jump/scoop legs) | 0 | 0 | FSM-gated |

Target average < 10% of one core; peak < 30%. Workers are gated by FSM state,
so most of the time CV threads are sleeping on `threading.Event`.

### 7.7 Pipeline architecture

```
CaptureThread (dxcam-cpp) ──► FrameRingBuffer (size 3)
                                     │
                                     ▼
                              DispatchThread
                              ├─► FSSWorker  (active in SCANNING)
                              ├─► DSSWorker  (active in PROBING)
                              └─► DockWorker (active in DOCKING, v2)
                                     │
                                     ▼ on failure
                              debug/frame_<ts>.png dump
```

Workers write `(timestamp, result_dict)` to a lock-protected slot; main FSM
reads the slot each tick. No queues from worker → FSM — latest result only.

---

## 8. FSD math + routing

### 8.1 The fuel formula

```
fuel_cost = (LC * 0.001) * (ship_mass * dist / opt_mass) ^ PC
```

where:
- `LC` = LinearConstant per FSD class+rating (8, 10, 11, or 12)
- `PC` = PowerConstant per FSD class (2.00 → 2.90, +0.15 per class)
- `ship_mass` = `UnladenMass + FuelLevel + Cargo`
- `dist` = jump distance in LY (no `+8`; older community posts confuse this)
- `opt_mass` = FSD's optimal mass (modified by engineering)

Solved for `dist` at `fuel = maxfuel`:

```
max_range = (min(maxfuel, fuel) / (LC * 0.001)) ^ (1/PC) * opt_mass / ship_mass
```

`MaxJumpRange` in `Loadout` is the game's own calculation with `fuel = maxfuel`
and `ship_mass = UnladenMass + FuelCapacity.Main` (no cargo, full tank). The
bot recomputes with **live** fuel and mass on every leg.

### 8.2 Per-FSD constants

Sourced from [EDCD/coriolis-data](https://github.com/EDCD/coriolis-data)
`modules/standard/frame_shift_drive.json`. We embed this file at
`projects/ed-autojump/data/fsd_modules.json`.

| Class | Rating | LC | PC | OptMass (t) | MaxFuel (t) |
|---|---|---:|---:|---:|---:|
| 2 | E | 11 | 2.00 | 48 | 0.60 |
| 2 | D | 10 | 2.00 | 54 | 0.60 |
| 2 | C | 8 | 2.00 | 60 | 0.60 |
| 2 | B | 10 | 2.00 | 75 | 0.80 |
| 2 | A | 12 | 2.00 | 90 | 0.90 |
| 3 | (E/D/C/B/A) | (11/10/8/10/12) | 2.15 | (80/90/100/125/150) | (1.2/1.2/1.2/1.5/1.8) |
| 4 | (E/D/C/B/A) | (11/10/8/10/12) | 2.30 | (280/315/350/437.5/525) | (2.0/2.0/2.0/2.5/3.0) |
| 5 | (E/D/C/B/A) | (11/10/8/10/12) | 2.45 | (560/630/700/875/1050) | (3.3/3.3/3.3/4.1/5.0) |
| 6 | (E/D/C/B/A) | (11/10/8/10/12) | 2.60 | (960/1080/1200/1500/1800) | (5.3/5.3/5.3/6.6/8.0) |
| 7 | (E/D/C/B/A) | (11/10/8/10/12) | 2.75 | (1440/1620/1800/2250/2700) | (8.5/8.5/8.5/10.6/12.8) |
| 8 | (special) | (varies) | (~2.90) | (varies) | (varies) |

Pattern: LC = `{A:12, B:10, C:8, D:10, E:11}`. PC = `2.00 + 0.15 * (class - 2)`.

#### SCO (Supercruise Overcharge) FSDs

Use the **same hyperspace fuel formula** as standard FSDs. The differences are
in per-module stats (typically higher `opt_mass`) and in supercruise behaviour
(massive fuel burn during sustained boost). The bot does not invoke SCO during
exploration legs.

### 8.3 Boost factors

| Boost type | Multiplier | Trigger |
|---|---|---|
| Basic FSD Inject (synthesis) | ×1.25 | C + V + Ge |
| Standard FSD Inject | ×1.50 | + Cd + Nb |
| Premium FSD Inject | ×2.00 | + As + Y + Po |
| White Dwarf jet cone | ×1.50 | fuel scoop equipped, fly into cone |
| Neutron star jet cone | ×4.00 | fuel scoop equipped, fly into cone |

V1 does **not** use jet cone supercharging (we filter out N/D* stars from
routes — §8.4). Synthesis injects are usable as an emergency tool (§9.5).

### 8.4 Star classes — scoopable vs dangerous

**Scoopable (KGBFOAM)** — main sequence, fuel scoop works:

`O`, `B`, `A`, `F`, `G`, `K`, `M`.

**Dangerous (route filter rejects these by default)**:

| Class | Type | Hazard |
|---|---|---|
| `D`, `DA`, `DAB`, `DAO`, `DAZ`, `DAV`, `DB`, `DBZ`, `DBV`, `DO`, `DOV`, `DQ`, `DC`, `DCV`, `DX` | White Dwarf | exclusion zone, jet cone +50% supercharge, **NON-SCOOPABLE** |
| `N` | Neutron Star | exclusion zone, jet cone +400% supercharge, **NON-SCOOPABLE** |
| `H` | Black Hole | exclusion zone, no supercharge, **NON-SCOOPABLE**, often invisible until close |
| `W`, `WC`, `WN`, `WNC`, `WO` | Wolf-Rayet | exclusion zone, radiation, **NON-SCOOPABLE** |

**Non-scoopable but low-hazard** (allowed as transit only — must not be the
sole star in a route segment):

`L`, `T`, `Y` (brown dwarfs), `S`, `MS`, `C` (carbon / S-type), `AeBe` (Herbig
Ae/Be), `TTS` (T-Tauri).

The route filter rejects any leg whose destination `StarClass` is in the
**dangerous** set. The user's reference journal shows the in-game plotter
happily routed them through `V886 Centauri` (`StarClass:"DA"`) — proof we
must filter at our planner layer.

### 8.5 Planet value tiers (req 6 priority)

From the [Frontier-forums exploration value formula thread](https://forums.frontier.co.uk/threads/exploration-value-formulae.232000/):

```
value = k + (3 * k * earth_masses^0.199977 / 5.3)
```

| Planet class | k (base) | k (terraformable) |
|---|---:|---:|
| Earth-like world (ELW) | 155,581 | 279,088 |
| Water world (WW) | 155,581 | 279,088 |
| Ammonia world (AW) | 232,619 | — |
| High Metal Content (HMC) | 23,168 | 241,607 |
| Metal-rich | 52,292 | — |
| Rocky (terraformable) | 720 | 223,971 |
| Other | 720 | — |

Multipliers:
- First-discovered (`WasDiscovered: false`) — adds bonus
- First-mapped (`WasMapped: false` and we successfully DSS) — multiplies

DSS priority (configurable):
- Tier 1 (always map): ELW, WW, AW
- Tier 2 (map if route allows): terraformable HMC, terraformable rocky
- Tier 3 (skip): non-terraformable HMC, metal-rich, gas giants Class II
- Skip: ice worlds, gas giants Class I/III/IV/V, asteroid belts, stars

### 8.6 Routing modes

- **External plotter (default for v1)**: bot calls Spansh
  `POST /api/route?efficiency=60&range=R&from=A&to=B` with
  `R = Loadout.MaxJumpRange × 0.97`. Polls job UUID via `GET /api/results/<uuid>`.
  Route is then walked one waypoint at a time using the in-game galmap target
  function — we don't try to plot the entire route in the galaxy map UI.
- **In-game plotter**: user manually plots a route; bot detects populated
  `NavRoute.json` and follows it. We still run the route through our safety
  filter (§8.4) and refuse to engage on rejected legs.
- **Spansh dump (offline)**: for "skip systems already 100% mapped" we keep
  an optional local SQLite built from the [Spansh galaxy dump](https://www.spansh.co.uk/dumps).
  Not in v1; planned for v2.

#### 8.6.1 Spansh API endpoints we use

```
POST https://www.spansh.co.uk/api/route          → {"job": uuid}
GET  https://www.spansh.co.uk/api/results/<uuid> → {result: {system_jumps: [...]}}
GET  https://www.spansh.co.uk/api/system/<id64>  → bodies + values
POST https://www.spansh.co.uk/api/systems/search → free-form filters
```

Rate limit: undocumented. Community practice: < 1 req/sec on search;
fire-and-poll for route jobs.

#### 8.6.2 EDSM as supplement

[EDSM API v1](https://www.edsm.net/en/api-v1): 360 req/hour cap.
`GET /api-system-v1/bodies?systemName=...` returns `WasDiscovered`/`WasMapped`
per body — useful for "is this system already explored." Slower than the
Spansh dump at scale but no setup cost.

---

## 9. Per-requirement implementation

### 9.1 Req 1 — Jump to destination

Per-jump sequence (state diagram in §11):

```
ARRIVED ──► AWAIT_FUEL_DECISION ──► [SCOOPING or skip]
   │                                   │
   ▼                                   ▼
HONK_AND_SCAN ◄────────────── SCOOP_DONE
   │                                   ▲
   ▼                                   │
[optional: FSS / DSS]                  │
   │                                   │
   ▼                                   │
ROUTE_NEXT ──► TARGET_NEXT ──► CHARGE ─┘
                                  │
                                  ▼
                              JUMPING ──► ARRIVED
```

Per-leg algorithm:

1. On `FSDJump` event (we've arrived).
2. Wait for `FsdMassLocked` flag to clear (~2 s).
3. Run §9.2 escape sequence.
4. Decide scoop (§9.3) — if `FuelLevel < FuelCapacity * refuel_threshold` and
   `StarClass in KGBFOAM`, transition to `SCOOPING`. Else skip.
5. After scoop (or skip), run §9.4 honk.
6. If FSS or DSS enabled, run them (§9.6 / §9.7).
7. Target next system: open galmap, search for next route waypoint, target it.
   Confirm via `FSDTarget` event with matching `Name`.
8. Run §8.4 danger-class check on the *new* `FSDTarget.StarClass`. If
   dangerous, log and clean-stop. Otherwise verify fuel (§9.5).
9. Engage FSD (`HyperSuperCombination` key). Within 1 s, send `SetSpeedZero`.
10. Wait for `StartJump:Hyperspace` event. Watch for `FSDJump` arrival.

### 9.2 Req 7 — Don't collide with the star (the hard one — corrected)

#### 9.2.1 What v0.1 had wrong

v0.1 said "pitch up for 2 seconds after `FSDJump` and boost." This is wrong:

- You exit witch space **pointed directly at the primary star** with
  **carry-over throttle**. If you arrived from a leg where the throttle was
  high (cruise), the ship is moving toward the star at exit.
- The escape window is **before** witch space, not after. By the time
  `FSDJump` fires, you may already be heating up.
- Pitch-up is the right escape direction only when the star is below you.
  In binary/trinary systems you may exit near a secondary; the primary can
  be off-axis and pitch-up can be wrong.
- Neutron stars and white dwarfs have **asymmetric exclusion zones** and
  narrow jet cones. Pitch-up into a cone is catastrophic.

#### 9.2.2 Correct procedure

**T-pre-charge (`StartJump:Hyperspace` event seen, ~5 s before arrival):**

1. Send `SetSpeedZero`. Confirm `Status.json` next update shows the engine
   throttle ≈ 0 (we approximate by waiting one Status poll cycle).
2. Inspect cached `StartJump.StarClass`. If it's in §8.4's **danger list**,
   the route filter should have prevented this — abort the jump by toggling
   the FSD-engage key again. The charge cancels; `Flags.FsdCharging` clears
   within 1 s. Log and clean-stop.

**T+0 (witch space → normal space, `FSDJump` event observed):**

3. Immediately `PitchUpButton` hold for `t_pitch` (class-dependent, table
   below). During pitch, also send `SetSpeed75` so we start moving once we're
   off the star vector.
4. After `t_pitch`, release pitch and watch `Status.Flags` — wait for
   `FsdMassLocked` (bit 16) to clear before any further FSD action.

**T+~3 s:**

5. Read `Heat` from `Status.json` if present. If `Heat > 0.6`, send
   `DeployHeatSink`. If `> 0.9`, send `UseBoostJuice` (boost) and re-pitch
   90° from estimated star vector for 1 s.
6. If `FuelLevel < FuelCapacity * refuel_threshold` and `StarClass in KGBFOAM`,
   transition to `SCOOPING`. Otherwise transition to `HONK_AND_SCAN`.

#### 9.2.3 Class-conditional pitch parameters (initial — **VERIFY in flight**)

| Star class | t_pitch (s) | Post-pitch throttle | Notes |
|---|---:|---:|---|
| K, G, F | 2.0 | 75% | safe yellow/orange, baseline |
| B, A | 3.0 | 50% | bigger exclusion zone, more heat |
| O | 4.0 | 50% | rare, biggest mainline class |
| M | 1.5 | 75% | smallest main-sequence, low heat |
| L, T, Y | 1.5 | 100% | brown dwarfs, safe to leave fast |
| **D\*** (white dwarf) | 4.5 | 50% | route filter prevents this; macro must survive |
| **N** (neutron) | 4.5 | 50% | route filter prevents this; macro must survive |
| **H** (black hole) | 4.0 | 50% | route filter prevents this; macro must survive |
| **W\*** (Wolf-Rayet) | 4.0 | 50% | route filter prevents this; macro must survive |

The post-exit escape macro must survive arrival at any class even if the
route filter is bypassed (e.g. user manually plotted into a danger system).

#### 9.2.4 Heat as ground-truth signal

We can't see the star; we *can* read `Status.Heat`. The bot's escape
correctness check is `Heat` trend over 5 s after `FSDJump`. If `Heat` ever
exceeds 1.0 we deploy heatsink immediately and trigger a wider escape
(boost + re-pitch). Every escape's `Heat_max` is logged so we can refine
the per-class pitch table from real data.

### 9.3 Req 2 — Refuel on KGBFOAM

#### 9.3.1 Trigger

After §9.2 escape completes:
- `FSDJump.StarClass in {K,G,B,F,O,A,M}` **and**
- `FuelLevel < FuelCapacity * refuel_threshold` (default 0.70)
→ enter `SCOOPING`.

#### 9.3.2 Scoop technique

Community-standard technique is "graze the blue zone" at ~30 km/s. We can't
read the throttle blue zone or distance-to-star directly from Status, so we
approximate:

1. `SetSpeed75` for 2 s (close distance after the pitch-up).
2. `SetSpeed25` (default `Key_Numpad_2`) to settle into approximate scoop speed.
3. Watch for first `FuelScoop` event — confirms we're in scoop range with
   hardpoints retracted (scoop deploys automatically with hardpoints in).
4. If no `FuelScoop` event after 5 s and `Heat < 0.5`, throttle up 25% and
   wait 3 s more. Up to 2 cycles.
5. If `Heat > 0.85`, `SetSpeedZero` + `PitchUpButton` 1 s to back off.
6. Continue until `FuelScoop.Total >= FuelCapacity.Main * 0.98`.
7. `SetSpeed75`, `PitchUpButton` 2 s to escape the corona, then resume.

#### 9.3.3 Failure modes

| Failure | Detection | Response |
|---|---|---|
| Hardpoints accidentally deployed | `Flags.HardpointsDeployed` true | `DeployHardpointToggle`, retry |
| Drop into exclusion zone | sudden `Music:MainMenu` pattern + `HullDamage` | log, abort scoop, continue route |
| Heat runaway | `Heat > 1.0` | `DeployHeatSink` + emergency pitch + boost |
| Stuck (not approaching) | no `FuelScoop` event in 10 s | throttle adjust + log |
| Scoop module damaged | `HullDamage` with module ref | abort cycle (no repair in v1) |

### 9.4 Req 4 — Honk every system

After §9.2 escape (and optionally §9.3 scoop) while still in supercruise near
the arrival star:

1. Confirm `Flags.Supercruise` true and `GuiFocus = 0`.
2. Send `ExplorationFSSDiscoveryScan` (the honk key).
3. **Hold for 6000 ms** (community-confirmed timing; the discovery scanner is
   charge-and-release).
4. Wait for `FSSDiscoveryScan` event with `Progress == 1.0`.
5. Record `BodyCount` and `NonBodyCount` for the body-table init.

Timeout: if no `FSSDiscoveryScan` event within 8 s, retry once. If still
nothing, log and continue.

### 9.5 Req 3 — Fuel-safe routing

Pre-route + per-leg checks.

#### 9.5.1 Pre-route check

For each leg `i` in the planned route:

1. **Distance check:** `leg[i].dist <= MaxJumpRange × 0.97`. Margin covers
   mass-lock deviation and module damage.
2. **Fuel-after-jump:** `predicted_fuel_after = current_fuel − fsd_cost(dist, mass)`
   using §8.1 formula. We track predicted fuel across the route.
3. **Scoop-window check:** find the next leg whose destination is in KGBFOAM.
   If `predicted_fuel_after_n_legs < FuelCapacity × 0.20` before reaching a
   scoopable star, the route is unsafe — re-request from Spansh with
   `efficiency=80` (more scoopables, fewer LY per jump).
4. **Danger-class filter:** reject any leg whose `StarClass` is in the
   dangerous list (§8.4).

#### 9.5.2 Per-leg check (just before engaging FSD)

1. Recompute fuel cost using **live** `FuelLevel` (not the predicted value).
2. If `predicted_fuel_after < 0.10 × FuelCapacity` and the destination is
   non-scoopable, refuse the jump — synthesise a Basic FSD Inject if materials
   permit, or stop.
3. If `FSDTarget.StarClass` is in the dangerous list (defence-in-depth — route
   filter should have prevented this), refuse and clean-stop.

### 9.6 Req 5 — FSS every body (tier C)

Journal alone tells us *when* FSS is complete (`FSSAllBodiesFound`), not how
to make it progress. We need to enter FSS mode, drag the frequency band
across all visible signal sources, and confirm-resolve each.

#### 9.6.1 Two paths

- **Path A — keyboard-only sweep (default):** enter FSS (`ExplorationFSSEnter`),
  hold `ExplorationFSSRadioTuningX_Decrease` from max to min over `t_sweep`
  seconds (default 30 s), then back up. Each pass should resolve any signal
  in that band. We watch for `Scan` events to confirm. Limitation: signals
  sitting at one specific frequency may require centring + zoom — the sweep
  may miss them.
- **Path B — CV-assisted (feature flag):** capture FSS HUD region. Detect the
  "broken circle" indicators near the centre reticle. Tune toward each.
  Trigger `ExplorationFSSTarget` (the resolve key) once the ring goes solid.

Decision for v0.2: ship **Path A** as default. Implement Path B once Path A's
pass rate is measured.

#### 9.6.2 Termination

- `FSSAllBodiesFound` fires → leave FSS mode (`ExplorationFSSQuit`), proceed.
- 90-second timeout per system → leave FSS, log incomplete, proceed.
- The number of `Scan` events received during the FSS session is recorded
  for per-system completion-rate telemetry.

### 9.7 Req 6 — DSS every body (tier C, hardest)

No public ED automation has solved DSS reliably. We accept v1 will have
known failure modes.

#### 9.7.1 DSS entry sequence

DSS has **no dedicated "enter" binding**. The procedure is:

1. `PlayerHUDModeToggle` — switch to Analysis Mode HUD.
2. `CycleFireGroupNext` until the DSS-equipped fire group is selected.
   (Bot counts cycles; user must position DSS in a known group via outfitting.)
3. Confirm `Flags.InAnalysisMode` (bit 27) is set.
4. `PrimaryFire` — deploys probes when in DSS mode.

#### 9.7.2 Per-body workflow

For each `Scan` event where `BodyType` is mappable
(planet/moon — **not** star/asteroid belt) and `was_mapped == false` and
the body's value tier (§8.5) meets the configured threshold:

1. Open Nav Panel (`FocusLeftPanel` → cycle to body) and target body.
2. Supercruise toward it. Drop when close (Status `Altitude` or `BodyName`
   transitions to the target body and speed < cruise threshold).
3. Execute §9.7.1 to enter DSS.
4. Body rotation is **ignored** in v1 — probes are aimed in a 6-direction
   star pattern (forward, up, down, left, right, back-around) using
   `SAAThirdPersonPitch/Yaw` keys + `PrimaryFire`.
5. Wait for `SAAScanComplete`. Log `ProbesUsed` vs `EfficiencyTarget`.

#### 9.7.3 Known failure modes accepted

- Probes overshoot small bodies → wasted probes.
- High-rotation bodies → probes land on backside, coverage incomplete →
  no `SAAScanComplete`, hit timeout (120 s) → mark "DSS failed", continue.
- Gas giants too large → bot keeps firing until probe ammo depletes (~3 per
  reload) or timeout.

#### 9.7.4 Per-system DSS budget (configurable)

Defaults:
- Map only Tier-1 bodies (§8.5): ELW, WW, AW.
- Skip bodies > `DSS_MAX_LS_DISTANCE` (default 50 000 Ls).
- Hard cap: 4 bodies per system.

This is **not** "DSS every body" in the literal sense. Literal "every body"
is out of scope for v1; spec to be revised once v1 reliability is measured.

---

## 10. Docking — designed for v2

Out of scope for v1. Spec'd here so v1 doesn't paint itself into a corner.

### 10.1 Why docking is its own beast

- The journal has nothing about ship orientation relative to the station.
  We need CV for the nav bobble (§7.4.3).
- Permission may be denied for half a dozen reasons (§10.2) — pre-checking
  saves a wasted approach.
- Mailslot entry requires roll-to-match-rotation; the Advanced Docking
  Computer handles this if fitted (recommended).
- Surface ports, outposts, fleet carriers, and capital ships all have
  different docking flows.

### 10.2 Permission flow & denial reasons

| Event | Fields |
|---|---|
| `DockingRequested` | StationName, StationType, MarketID, LandingPads {Small, Medium, Large} |
| `DockingGranted` | StationName, StationType, MarketID, LandingPad (pad number) |
| `DockingDenied` | StationName, StationType, MarketID, **Reason** |
| `DockingTimeout` | (~5 min after grant without landing) |
| `DockingCancelled` | (player cancel, or moved > 7.5 km after grant) |
| `Docked` | full station info — services, faction, allegiance, government, state, pads |
| `Undocked` | StationName, MarketID |

`DockingDenied:Reason` values: `NoSpace`, `TooLarge`, `Hostile`, `Offences`,
`Distance`, `ActiveFighter`, `NoReason`.

`RestrictedAccess` / `PermitRequired` do **not** appear here — they're
gated at FSD jump (you can't reach the system without the permit).

### 10.3 Pre-flight check (avoid blind requesting)

The user explicitly called this out: "blind-firing requesting of docking is
not good practice." Before sending the request:

1. `Status.LegalState in {"Clean", "Allied"}` — else expect `Offences` /
   `Hostile`. Skip the station.
2. `Ship class fits station's largest pad` — large ships can't dock at
   outposts. Cross-reference `LandingPads.Large > 0` from cached Spansh data
   or from a prior `DockingRequested` response. Outposts always = medium max.
3. `Distance < 7.5 km` — else expect `Distance`.
4. `Flags.InSRV` / `InFighter` false — else expect `ActiveFighter`.
5. No active fines for the station's controlling faction — observable from
   journal `CommitCrime` history in this session, or `Status.LegalState`.

If any pre-check fails, log and skip — don't request.

### 10.4 Mailslot — via the Advanced Docking Computer

v2 **requires** `int_dockingcomputer_advanced` fitted with `On: true` (verified
from `Loadout`). The bot:

1. Approaches station at full throttle until `< 10 km`.
2. Drops to `< 200 m/s` (well under no-fire-zone 100 m/s limit by the time
   we're inside).
3. Confirms `DockingGranted`.
4. Hands off — the DC flies through the mailslot, navigates to the assigned
   pad, lands.
5. Waits for `Docked` event.
6. If the DC fails (collision, get-stuck), abort to PANIC and let user recover.

### 10.5 Repair workflow

Post-`Docked`:

1. Open Station Services (`UI_Select` on the panel).
2. Navigate to Repair → Repair All. Send the confirm key.
3. Wait for `Repair` / `RepairAll` event.
4. Refuel: navigate to Refuel, confirm. Wait for `Refuel` event.
5. Restock: optional, configured per ship.
6. Launch: navigate to Launch, confirm.
7. Wait for `Undocked` event.
8. Re-engage route (re-target next system).

Engineering modifications survive repairs — no special handling.

### 10.6 Nav bobble CV (for non-DC fallback)

If the user's ship lacks an Advanced DC, we'd need to fly the mailslot
ourselves. Out of scope for v2 — initial v2 requires DC fitted. The compass
CV (§7.4.3) is useful for orientation toward the station from supercruise but
not for the mailslot itself.

---

## 11. Master state machine

```
                ┌───────────────────────────────────────────────────────┐
                │                                                       │
   IDLE ──user start──► BOOTING ──Loadout OK──► PLANNING ──route OK──► READY
    ▲                       │                       │                    │
    │                       ▼                       ▼                    │
    │                    ABORT                   ABORT (no fuel-safe     │
    │                                            route → exit)            │
    │                                                                    │
    │  ┌──────────────────────────────────────────────────────────────┐  │
    │  │                                                              │  │
    │  ▼                                                              ▼  ▼
PANIC ◄─hull<thr / heat>1 / overheat / interdicted / wing────  EXECUTING_LEG
                                                                    │
                  ┌──── ARRIVED ◄──── JUMPING ◄────── CHARGING ◄────┤
                  │                                       ▲          │
                  ▼                                       │      TARGETING
            ESCAPE_STAR  ──ok──►  SCOOPING  ──full──►  HONK_AND_SCAN
                                                              │
                                                              ▼
                                                          [FSS opt] ──► [DSS opt]
                                                              │
                                                              ▼
                                                         CONTINUE_OR_FINISH
                                                              │
                                                              ▼
                                                          ROUTE_NEXT
```

State invariants:

- `EXECUTING_LEG` requires `Flags.InMainShip & ~Docked & ~Landed`.
- `JUMPING` requires `Flags.FsdCharging` (we set it, game confirms).
- `SCOOPING` requires `FSDJump.StarClass in KGBFOAM` and `FuelLevel < threshold`.
- `PANIC` is sticky — only resolves on user intervention or after sustained
  safe state (`InMainShip & ~Overheating & Heat < 0.5` for 5 s).

---

## 12. Safety & abort

### 12.1 Auto-abort triggers

| Trigger | Source | Action |
|---|---|---|
| `HullDamage.Health < 0.7` | journal | `PANIC` — `SetSpeedZero`, `UseBoostJuice`, retract everything |
| `Status.Flags.Overheating` | status bit 20 | `DeployHeatSink`, `UseBoostJuice`, abort scoop |
| `Status.Flags.IsInDanger` | status bit 22 | log; if scoop in progress, abort scoop |
| `Status.Flags.LowFuel` | status bit 19 | confirm against our calc; route to scoop |
| `Music: Combat_Dogfight` | journal | `PANIC` |
| `Interdicted` event | journal | submit + boost (configurable) |
| `JetConeBoost` | journal | unexpected — route filter should have prevented; `PANIC` |
| `JetConeDamage` | journal | `PANIC` |
| Unexpected `Docked` / `Landed` | status | user intervened — pause bot |
| `Flags.InWing` true | status bit 23 | user joined wing — pause bot |
| Watchdog: no journal event in 90 s while game alive | timer | `PANIC` |
| User panic hotkey | hotkey listener | clean stop |

### 12.2 Clean stop

Configurable panic hotkey (default `Ctrl+Alt+P`) triggers:

1. Release all held keys.
2. `SetSpeedZero`.
3. Retract scoop / hardpoints (no-op if already retracted).
4. Restore the player's `StartPreset.4.start` if we swapped it (§5.1.3).
5. Drop to `IDLE`. Log full transcript of the last leg.

### 12.3 What we cannot recover from

- Destruction. Rebuy is the user's problem.
- Mid-supercruise interdiction during high-speed exploration legs — limited
  options; we submit and try to outrun, then re-plot.
- Stuck in a station after manual repair — bot exits, user takes over.

---

## 13. Configuration

`projects/ed-autojump/config.toml`:

```toml
[ship]
# Auto-extracted from Loadout, but pinnable for testing
expected_ship = "cutter"
expected_max_jump_range_ly = 31.288
expected_fuel_capacity_t = 64.0
required_modules = ["int_fuelscoop_*", "int_detailedsurfacescanner_tiny"]
required_modules_v2 = ["int_dockingcomputer_advanced"]    # docking phase

[routing]
mode = "external_spansh"            # or "in_game_plotter"
destination = "Beagle Point"
efficiency = 60                     # Spansh 0..100, lower = shorter legs
range_margin = 0.97
fuel_safety_threshold = 0.20        # min fuel ratio at next scoopable star
refuel_threshold = 0.70             # scoop trigger
danger_classes = ["D","DA","DAB","DAO","DAZ","DAV","DB","DBZ","DBV","DO","DOV","DQ","DC","DCV","DX","N","H","W","WC","WN","WNC","WO","AeBe","TTS"]

[exploration]
honk = true
fss = "off"                         # "off" | "keyboard_sweep" | "cv_assisted"
dss = "off"                         # "off" | "high_value_only" | "all"
dss_max_distance_ls = 50000
dss_per_system_cap = 4
dss_tier_threshold = 1              # 1 = tier 1 only; 2 = tier 1 + 2

[safety]
hull_panic_threshold = 0.70
heat_panic_threshold = 1.00
heatsink_threshold = 0.80
no_journal_timeout_s = 90
panic_hotkey = "ctrl+alt+p"
legal_state_allowed = ["Clean", "Allied"]

[input]
backend = "pydirectinput"
key_delay_ms = 75
pitch_up_default_s = 2.0
class_pitch_overrides = { K = 2.0, G = 2.0, F = 2.0, B = 3.0, A = 3.0, O = 4.0, M = 1.5 }

[binds]
preset_name = "ED-AFK"
auto_swap_start_preset = true       # write our preset to StartPreset.4.start line 2 only
restore_on_exit = true

[hud]
edhm_detect = true
edhm_preset_to_offer = "ED-AFK-CV.json"
graphics_override_fallback = true   # write GraphicsConfigurationOverride.xml if no EDHM

[cv]
capture_backend = "dxcam-cpp"       # or "windows-capture"
require_sdr = true                  # warn if HDR detected
require_borderless_windowed = true
target_resolution = [2560, 1440]    # warn if mismatch
ocr_engine = "tesseract"            # or "paddleocr"

[eddn]
publish = true                      # contribute scan data back to EDDN
software_name = "ED-AFK / ed-autojump"
software_version = "0.2.0"
uploader_id = ""                    # auto-filled from Loadout commander name

[paths]
journal_dir = '%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous'
binds_dir = '%LOCALAPPDATA%\Frontier Developments\Elite Dangerous\Options\Bindings'
log_dir = './logs'
calibration_dir = './calibration'
```

---

## 14. Telemetry & community data

### 14.1 Local logs

Per-jump JSONL record: timestamp, system in/out, distance, fuel before/after,
star class, heat max, time-in-scoop, scoop tons added, FSS/DSS results,
any aborts.

Per-session summary on exit: jumps completed, bodies honked, FSS rate, DSS
rate, total panics.

Every key send is logged with timestamp, action, scancode, hold — required
for diagnosing input failures and tuning timings.

### 14.2 EDDN contribution (opt-in by default)

The bot can publish our scan / honk / FSS data back to EDDN
([eddn.edcd.io](https://github.com/EDCD/EDDN)). This contributes to Spansh,
EDSM, INARA, and other community tools. Schemas we publish:

- `journal-v1.0` — `Scan`, `SAAScanComplete`, `SAASignalsFound`
- `fssdiscoveryscan-v1.0` — the honk
- `fssallbodiesfound-v1.0`
- `fssbodysignals-v1.0`
- `navroute-v1.0` (optional — our route)

Upload endpoint: `POST https://eddn.edcd.io:4430/upload/`.

Required envelope fields: `uploaderID` (commander name), `softwareName`,
`softwareVersion`. Game journal events are already in the schema's expected
format — strip forbidden fields per schema README and POST.

User can disable with `eddn.publish = false`.

---

## 15. Testing strategy

### 15.1 Offline tests (no game)

- Replay tests: feed recorded `.log` lines into JournalTail, assert state
  transitions. Bundle a corpus from real journals (developer can anonymise
  system names if desired).
- Route planner unit tests: golden fixtures of `(loadout, start, end)` →
  expected `(route, fuel-after-each-leg)`.
- Status parser tests against checked-in `Status.json` snapshots.
- FSD math tests against Coriolis golden values.

### 15.2 In-game calibration (supervised)

1. **Single-jump test** (req 1, 7): plot one jump to a known K-class system.
   Verify escape sequence, no heat overrun, scoop trigger.
2. **Multi-jump fuel test** (req 2, 3): 5-jump route mixing K and M stars
   with one M-only stretch. Verify scoop fires only on K; fuel never drops
   below threshold.
3. **Honk test** (req 4): 10 systems in a row. Verify `FSSDiscoveryScan`
   fires within 8 s every time.
4. **Danger-class refusal test**: manually plot a route through `V886 Centauri`
   (`DA`). Verify bot refuses to jump and clean-stops.

### 15.3 Unsupervised endurance (req tier A/B verified)

- 50-jump run, M+K mix, no exploration. Pass criteria: all jumps complete,
  no hull damage, no fuel-out, no panic state.
- 200-jump run. Pass: as above plus zero unhandled exceptions, clean shutdown.

### 15.4 Tier C calibration (FSS / DSS)

- Manual recording of 10 FSS sessions to calibrate sweep duration.
- Manual recording of 10 DSS sessions to calibrate probe-pattern timings.
- Per-body coverage success rate becomes a tracked metric, not a hard
  pass/fail.

---

## 16. Risks & open questions

| Risk | Severity | Mitigation |
|---|---|---|
| Frontier patches change `Music` / `Flags` / event shapes | M | Pin to journal manual v32, regression-test on patch via replay corpus |
| User's keybinds preset migration (4.0 → 4.2) breaks our preset | M | Ship `.4.0`, `.4.1`, `.4.2` variants; detect version at startup |
| HDR breaks CV (tier C) | H | Require SDR for tier C; auto-detect with `ColorSpace` check |
| Class-pitch table wrong for user's specific ship mass | H | Calibrate per-ship from supervised runs; bias toward longer pitch |
| Mass-locked at scoopable secondary star | M | If primary is M with mass-locking secondary nearby, SC away first |
| Spansh API rate-limit / outage | L | Cache last computed route; fall back to in-game plotter |
| EDHM update lag (broken between Frontier patch and EDHM fix) | L | Relaxed CV thresholds during broken window |
| Bot detected by Frontier as input cheating | L (per user) | We don't conceal; if banned, user accepts consequence |
| StartPreset.4.start swap interferes with EDMC | L | EDMC reads journals only; doesn't touch binds |
| `LegalState` stale during legal-recovery window | L | Conservatively pessimistic; bot waits N seconds after legal-change event |

### 16.1 Things we must learn from supervised testing

- Actual heat trajectory by class for the developer's specific ship.
- Whether step-targeting (re-target on each `FSDJump`) is fast enough to be
  worth its robustness over multi-jump route plotting.
- Real `FSSAllBodiesFound` rate from keyboard-only FSS sweep.
- Whether Spansh's `efficiency=60` is the right default for fuel safety.
- DSS coverage rate from naive 6-direction probe pattern (likely poor).

### 16.2 Decisions deferred to implementation

- Whether to subclass EDMC's journal parser (GPL — would force relicense) or
  port EDAPGui's `EDJournal.py` (MIT — preferred).
- Whether to use `asyncio` or threads for tail watchers.
- Whether to use the `transitions` library for the FSM or hand-roll.
- Mouse input as a fallback if keyboard binds aren't sufficient for FSS
  precision tuning.

---

## 17. Implementation phases

| Phase | Scope | Exit criteria |
|---|---|---|
| 0 | Project skeleton, config, journal/status tailers, replay-test harness, EDAPGui borrow (directinput, EDKeys, StatusParser, NavRouteParser) | Replay sample journal end-to-end without crash |
| 1 | Binds preset generation + swap-on-launch script | User can launch ED with bot preset active, restore on exit |
| 2 | Req 4 (honk) only — minimum viable bot | 10-system manual route → 10 honks logged |
| 3 | Req 1 + 3 + 7 — jump + escape + route safety + danger-class refusal | 50-jump unsupervised run, no fuel-out, no hull damage |
| 4 | Req 2 — scoop integration | 200-jump unsupervised run |
| 5 | EDDN sidecar publisher | Verify scans appear on EDSM within 1 hr |
| 6 | EDHM detection + calibration mode skeleton | First-run calibration completes; profile.json written |
| 7 | Req 5 — FSS keyboard sweep | per-system FSS completion measured |
| 8 | Req 5 — FSS CV-assisted (optional) | improvement over keyboard sweep measured |
| 9 | Req 6 — DSS naive 6-direction pattern | per-body success rate measured |
| 10 (v2) | Docking — repair workflow | dock at known station, repair, undock, resume route |
| 11 (v2) | Headless launcher integration (`min-ed-launcher`) | autorun/autoquit cycle works for unsupervised multi-hour runs |

---

## 18. References

### 18.1 Frontier-official

- [Frontier Player Journal Manual v32 (PDF)](https://hosting.zaonce.net/community/journal/v32/Journal_Manual-v32.pdf) — canonical event reference
- [Frontier Knowledge Base — Player Journal location](https://customersupport.frontier.co.uk/hc/en-us/articles/4404788337938)
- [Frontier Forums — Tools & APIs subforum](https://forums.frontier.co.uk/forums/tools-and-apis.59/)
- [Exploration value formulae (Frontier-forums thread)](https://forums.frontier.co.uk/threads/exploration-value-formulae.232000/)

### 18.2 Community-maintained schemas & docs

- [elite-journal.readthedocs.io](https://elite-journal.readthedocs.io/) — event schema, Status file, Travel events, Exploration events
- [EDCD/EDDN schemas](https://github.com/EDCD/EDDN/tree/master/schemas) — canonical field names for `fssdiscoveryscan-v1.0`, `fssbodysignals-v1.0`, `fssallbodiesfound-v1.0`, `navroute-v1.0`, `journal-v1.0`
- [EDCD/coriolis-data](https://github.com/EDCD/coriolis-data) — module data including FSD constants (`modules/standard/frame_shift_drive.json`)
- [EDCD/FDevIDs](https://github.com/EDCD/FDevIDs) — commodity, outfitting, shipyard CSV references
- [edcodex.info](https://edcodex.info/?m=doc) — community tool index (~210 ED tools)
- [doc.elitedangereuse.fr](https://doc.elitedangereuse.fr/) — French-maintained journal docs

### 18.3 ED wiki (Fandom + Lave + ED-DSN)

- [Hyperspace](https://elite-dangerous.fandom.com/wiki/Hyperspace), [Exclusion Zone](https://elite-dangerous.fandom.com/wiki/Exclusion_Zone), [Mass Lock](https://elite-dangerous.fandom.com/wiki/Mass_Lock)
- [Fuel Scoop](https://elite-dangerous.fandom.com/wiki/Fuel_Scoop), [Neutron Highway](https://elite-dangerous.fandom.com/wiki/Neutron_Highway), [FSD Supercharging](https://elite-dangerous.fandom.com/wiki/FSD_Supercharging)
- [Frame Shift Drive](https://elite-dangerous.fandom.com/wiki/Frame_Shift_Drive), [Frame Shift Drive (SCO)](https://elite-dangerous.fandom.com/wiki/Frame_Shift_Drive_(SCO))
- [Full Spectrum System Scanner](https://elite-dangerous.fandom.com/wiki/Full_Spectrum_System_Scanner), [Detailed Surface Scanner](https://elite-dangerous.fandom.com/wiki/Detailed_Surface_Scanner)
- [Docking](https://elite-dangerous.fandom.com/wiki/Docking), [Speeding](https://elite-dangerous.fandom.com/wiki/Speeding), [Supercruise Assist](https://elite-dangerous.fandom.com/wiki/Supercruise_Assist)
- [Lave Wiki — Exploration](https://lavewiki.com/exploration)
- [ED-DSN (Deep Space Network)](https://ed-dsn.net/) — Dangers of Exploration, Neutron Travel, key bindings

### 18.4 Community data APIs

- [Spansh](https://www.spansh.co.uk/), [dumps](https://www.spansh.co.uk/dumps), [neutron systems dump](https://downloads.spansh.co.uk/systems_neutron.json)
- [EDSM API v1](https://www.edsm.net/en/api-v1), [API systems v1](https://www.edsm.net/en/api-system-v1)
- [INARA](https://inara.cz), [INARA API docs](https://inara.cz/elite/inara-api-docs/)
- [EDDN](https://github.com/EDCD/EDDN), upload `https://eddn.edcd.io:4430/upload/`, relay `tcp://eddn.edcd.io:9500/`
- [Athanasius/fd-api](https://github.com/Athanasius/fd-api) — Frontier cAPI OAuth flow (archived but reference quality)
- [iaincollins/ardent-api](https://github.com/iaincollins/ardent-api) — 150M-system community dataset + REST API

### 18.5 Prior art — what we borrow from

| Project | License | URL |
|---|---|---|
| **SumZer0-git/EDAPGui** ← primary borrow source | MIT | https://github.com/SumZer0-git/EDAPGui |
| skai2/EDAutopilot ← original autopilot, CV reference | MIT | https://github.com/skai2/EDAutopilot |
| Matrixchung/EDAutopilot-v2 ← cleaner module split | MIT | https://github.com/Matrixchung/EDAutopilot-v2 |
| EDCD/EDMarketConnector ← architecture reference, GPL-2 (study only) | GPL-2 | https://github.com/EDCD/EDMarketConnector |
| EDCD/EDDI ← event docs reference | mixed | https://github.com/EDCD/EDDI |
| EDDiscovery/EDDiscovery ← Apache-2 journal patterns | Apache-2 | https://github.com/EDDiscovery/EDDiscovery |
| Somfic/EliteAPI ← C# event schema reference | no-license | https://github.com/Somfic/EliteAPI |
| Numerlor/Auto_Neutron ← Spansh integration pattern | GPL-3 (study only) | https://github.com/Numerlor/Auto_Neutron |
| RatherRude/Elite-Dangerous-AI-Integration (COVAS:NEXT) | MIT | https://github.com/RatherRude/Elite-Dangerous-AI-Integration |
| rster2002/ed-journals (Rust event-type reference) | MIT | https://github.com/rster2002/ed-journals |
| kayahr/ed-journal (TS schemas) | MIT | https://github.com/kayahr/ed-journal |
| Xjph/ObservatoryCore | MIT | https://github.com/Xjph/ObservatoryCore |
| fredjk-gh/ObservatoryPlugins (Helm, Stat Scanner) | MIT | https://github.com/fredjk-gh/ObservatoryPlugins |
| Silarn/EDMC-BioScan ← species deduction (study only, GPL-2) | GPL-2 | https://github.com/Silarn/EDMC-BioScan |
| Silarn/EDMC-Pioneer ← scan-value formulas (study only, GPL-2) | GPL-2 | https://github.com/Silarn/EDMC-Pioneer |
| Silarn/EDMC-ExploData | GPL-2 | https://github.com/Silarn/EDMC-ExploData |
| dwomble/EDMC-NeutronDancer ← EDMC plugin Spansh pattern | MIT | https://github.com/dwomble/EDMC-NeutronDancer |
| canonn-science/EDMC-Canonn ← codex/POI dataset | GPL-3 | https://github.com/canonn-science/EDMC-Canonn |
| Mirooz/EliteDangerousWarboard ← bioforge, Spansh routing | MIT | https://github.com/Mirooz/EliteDangerousWarboard |
| Ma77h3hac83r/GalNetOps ← species estimator, FSS/DSS dashboard | MIT | https://github.com/Ma77h3hac83r/GalNetOps |
| iaincollins/icarus ← live journal → browser, ISC | ISC | https://github.com/iaincollins/icarus |
| NinurtaKalhu/Elite-Dangerous-Multi-Route-Optimizer ← TSP+LK + neutron | AGPL-3 | https://github.com/NinurtaKalhu/Elite-Dangerous-Multi-Route-Optimizer |
| Kepas-Beleglorn/EDXD ← explorer dashboard | CC BY-NC | https://github.com/Kepas-Beleglorn/EDXD |
| GWLlosa/elite-dangerous-local-ai-tie-in-mcp ← 213-event MCP taxonomy | MIT | https://github.com/GWLlosa/elite-dangerous-local-ai-tie-in-mcp |
| EliteG19s/MagicMau ← display backends | unknown | https://github.com/MagicMau/EliteG19s |
| rfvgyhn/min-ed-launcher ← headless launcher (autorun/autoquit) | MIT | https://github.com/rfvgyhn/min-ed-launcher |
| Viper-Dude/EliteMining ← mining automation (sister project reference) | GPL-3 | https://github.com/Viper-Dude/EliteMining |
| EpicStuff/Elite-Dangerous-Auto-Pilot ← Robigo + AFK combat escape | MIT (archived) | https://github.com/EpicStuff/Elite-Dangerous-Auto-Pilot |
| spansh/a-star-router ← Spansh's own routing reference | GPL-3 | https://github.com/spansh/a-star-router |

### 18.6 Keybind / binds tooling

- [ngollan/Elite-Binds](https://github.com/ngollan/Elite-Binds) — XSD schema for `.binds` files
- [ljvasey/elite-bind-values](https://github.com/ljvasey/elite-bind-values) — `Key_*` name list
- [cmdrdahkron/elite-binds](https://github.com/cmdrdahkron/elite-binds) — real `.binds` examples
- [alterNERDtive/bindED](https://github.com/alterNERDtive/bindED) — C# .binds parser (VoiceAttack plugin)
- [brammmers/edrefcard](https://github.com/brammmers/edrefcard) — active fork of EDRefCard for binds → reference cards
- [SpaceJock — Elite Keybinds guide](https://spacejock.com.au/EliteKeybinds.html) — dual-profile swap pattern reference

### 18.7 HUD modding (EDHM ecosystem)

- [BlueMystical/EDHM_UI](https://github.com/BlueMystical/EDHM_UI) — GPL-3, current EDHM-UI maintainer
- [psychicEgg/EDHM (archived original)](https://github.com/psychicEgg/EDHM)
- [BlueMystical EDHM-UI API docs](https://bluemystical.github.io/edhm-api/)
- [Frontier Forums — EDHM thread](https://forums.frontier.co.uk/threads/elite-dangerous-hud-mod-edhm.557033/)
- [ED wiki — HUD Color Editor (vanilla XML matrix)](https://elite-dangerous.fandom.com/wiki/HUD_Color_Editor)

### 18.8 Vision / CV / capture libraries

- [Fidelxyz/DXCam-CPP](https://github.com/Fidelxyz/DXCam-CPP) — fast DXGI Duplication, drop-in `dxcam` replacement
- [NiiightmareXD/windows-capture](https://github.com/NiiightmareXD/windows-capture) — WGC capture, fallback option
- [ra1nty/DXcam](https://github.com/ra1nty/DXcam) — original pyDXcam (less maintained)
- [bvibber/hdrfix](https://github.com/bvibber/hdrfix) — HDR→SDR tone-map reference
- [MagestiUA/HDR_Screenshot_tool_for_windows](https://github.com/MagestiUA/HDR_Screenshot_tool_for_windows) — HDR capture reference
- [PaddleOCR vs Tesseract benchmark](https://www.codesota.com/ocr/paddleocr-vs-tesseract) — OCR comparison

### 18.9 Community technique references (YouTube, written guides)

- **Down to Earth Astronomy** (YouTube) — exobiology, DSS technique, exploration deep dives
- **ObsidianAnt** (YouTube) — narrative exploration, lore
- **Yamiks** (YouTube) — efficiency guides, route optimisation
- **CMDR Mechan** (YouTube) — fleet carrier logistics
- **The Pilot** (YouTube) — beginner-friendly exploration
- **Nick The Gamer** (YouTube) — neutron highway runs
- **Crewman6 ED Scanning Guide (2025)** — https://crewman6elitedangerous.blogspot.com/2025/04/elite-dangerous-scanning-guide-by-grok.html
- **ED Autopilot OpenCV writeup — networkgeekstuff** — https://networkgeekstuff.com/projects/autopilot-for-elite-dangerous-using-opencv-and-thoughts-on-cv-enabled-bots-in-visual-to-keyboard-loop/

### 18.10 Discord communities

- **Elite Dangerous Community Devs** (EDCD) — invite via https://edcd.github.io
- **COVAS:NEXT** — https://discord.gg/Aj6KFJFkKs (AI integration)
- **BioScan / Pioneer / ExploData** — https://discord.gg/RhY7KPzTht (Silarn's exobiology tools)
- **Observatory** — invite at observatory.xjph.net
- **Canonn Science** — https://canonn.science/discord
- **The Fuel Rats** — https://github.com/fuelrats/api.fuelrats.com (BSD-3, rescue API)

### 18.11 Reddit & forums

- r/EliteDangerous (general), r/EliteExplorers (exploration-specific), r/EliteOne (console)

### 18.12 Local data sources

- Real journal samples from this user's machine at
  `%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous\`.
  Reference sample: `Journal.2026-01-09T194605.01.log` (Cutter, MaxJumpRange
  31.29 LY, 64 t fuel, FSD `int_hyperdrive_overcharge_size7_class5`, route
  including `V886 Centauri:DA` as evidence of in-game plotter's danger-class
  blind spot).
- User's binds file: `Custom.4.2.binds` (gamepad-primary, keyboard-fallback;
  confirms `ExplorationFSSDiscoveryScan` and full XML element naming).
- User's EDHM install: `C:\Users\Quadstronaut\AppData\Local\EDHM-UI-V3\EDHM-UI-V3.exe`.
