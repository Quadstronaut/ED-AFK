# ED-AutoJump — Specification (v0.1 draft)

Autonomous exploration bot for Elite Dangerous: Odyssey. Designed to be left running
unattended for long routes while honking, scooping, scanning, and mapping.

> **Status:** design draft. Not implemented. Numbers cited from real journal samples
> at `Journal.2026-01-09T194605.01.log` and from cited references in the
> `RESEARCH.md` notes. Items marked **`VERIFY`** must be re-confirmed against a
> calibration session before the related code is trusted.

---

## 1. Goals & non-goals

### 1.1 Requirements (verbatim from user)

| # | Requirement | Bot tier* | Source of truth |
|---|---|---|---|
| 1 | Jump to any destination | A | Spansh route + journal `FSDTarget` / `FSDJump` |
| 2 | Refuel on every KGBFOAM star | A | journal `FSDJump.StarClass` + `Status.Flags.ScoopingFuel` |
| 3 | Never plot a route that runs out of fuel | A | pre-route filter + per-leg check |
| 4 | Honk every system | A | journal `FSSDiscoveryScan` |
| 5 | FSS every body in every system | C | journal `FSSAllBodiesFound` + screen CV |
| 6 | DSS-map every body | C | journal `SAAScanComplete` + screen CV |
| 7 | Never collide with the star on exit | A | pre-jump throttle + post-exit pitch macro |

*Tiers: **A** = journal-driven, deterministic. **B** = journal + input simulation,
deterministic. **C** = screen capture + computer vision; non-deterministic,
requires iterative tuning.

### 1.2 Non-goals (v1)

- Combat / interdiction handling beyond a "panic combat-log" abort.
- Docking, repairs, refit, cartographic data sale (planned for v2).
- Surface landings, SRV operations, exobiology footfall scans.
- DSS mapping of bodies that fail efficiency target (no retry logic; one shot).
- Anti-detection / evasion. The bot uses public APIs and the journal; we accept
  that Frontier *could* detect input cadence patterns. User stated Frontier does
  not ban for unattended exploration; we proceed on that assumption.

### 1.3 Anti-goals (do not do)

- Do **not** use `SendInput` / `keybd_event` directly. ED ignores synthetic input
  unless it comes via DirectInput scancodes (see §4.2).
- Do **not** trust Supercruise Assist for body approach. It rams planets
  ([source: ED wiki — Supercruise Assist collision bugs](https://elite-dangerous.fandom.com/wiki/Supercruise_Assist)).
- Do **not** treat the in-game route plotter as fuel-safe. The reference journal
  shows it routing through `StarClass:"DA"` (white dwarf, non-scoopable) without
  warning. We must filter at the route-planning layer.

---

## 2. Architecture

### 2.1 Process layout

```
┌─────────────────────────────────────────────────────────────┐
│                     ed-autojump (main)                      │
│                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐   │
│  │ JournalTail  │───►│  GameState   │◄───│ StatusTail   │   │
│  │ (file watch) │    │  (in-mem FSM)│    │ (file watch) │   │
│  └──────────────┘    └──────┬───────┘    └──────────────┘   │
│         ▲                   │                   ▲           │
│         │                   ▼                   │           │
│  ┌──────┴───────┐    ┌──────────────┐    ┌──────┴───────┐   │
│  │ NavRoute     │    │  Planner     │    │ Screen       │   │
│  │ watcher      │    │  (Spansh +   │    │ Reader (CV)  │   │
│  │              │    │   filters)   │    │ [tier C only]│   │
│  └──────────────┘    └──────┬───────┘    └──────────────┘   │
│                             │                   ▲           │
│                             ▼                   │           │
│                      ┌──────────────┐           │           │
│                      │   Executor   │───────────┘           │
│                      │ (state-driven│                       │
│                      │   key macros)│                       │
│                      └──────┬───────┘                       │
│                             │                               │
│                             ▼ pydirectinput scancodes       │
│                      ┌──────────────┐                       │
│                      │  Elite       │                       │
│                      │  Dangerous   │                       │
│                      └──────────────┘                       │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Why this shape (versus alternatives)

- **Journal-tailing over screen-reading**: deterministic, lower CPU, robust to HDR
  and resolution changes. Existing skai2/EDAutopilot uses CV for everything and is
  reported "lighting-dependent" with "alpha-quality runs" — we route around that
  pattern except where journal is silent (FSS, DSS).
- **Single process with internal state machine**: Auto_Neutron's clipboard+AHK
  delegation is robust for "one shot" jump sequences but composes poorly for an
  always-on bot that needs to react to mid-flight events (fuel low, hull damage,
  combat music). We keep the FSM in-process.
- **Planner separated from Executor**: route planning is a pure function of
  ship/system inputs; the Executor only steps through approved waypoints. This
  lets us unit-test routing without simulating the game.
- **Screen reader is opt-in**: tier C requirements (FSS, DSS) can be disabled.
  The bot remains useful as a tier-A honker even with CV off.

### 2.3 Language / runtime

- **Python 3.11+**, Windows-only (DirectInput).
- Key dependencies (versions to pin at first install):
  - `watchdog` — file system events for journal/status tailing
  - `pydirectinput` (or `pydirectinput-rgx` fork) — scancode key injection
  - `requests` — Spansh API
  - `pydantic` v2 — event parsing with strong types
  - `numpy`, `opencv-python`, `mss` — tier C only
  - `pytest` + `pytest-asyncio` for tests
- Distribution: PyInstaller single-exe + sidecar config.

---

## 3. Inputs — what we read

### 3.1 File locations

`%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous\`
(confirmed present on target machine).

| File | Read pattern | Notes |
|---|---|---|
| `Journal.YYYY-MM-DDTHHMMSS.NN.log` | tail latest, follow rotation | UTF-8, one JSON event per line |
| `Status.json` | poll on FS change, ~1–10 Hz | rewritten in place, may briefly be empty during write |
| `NavRoute.json` | poll on FS change | written when route plotted, cleared on completion |
| `Cargo.json`, `ShipLocker.json`, `Backpack.json`, `ModulesInfo.json`, `Outfitting.json`, `Shipyard.json` | read on demand | not in v1 hot path |
| `edmc-journal-lock.txt` | check at startup | warn if EDMC running; we don't lock |

**Journal rotation:** new file when game restarts. We watch the directory, not a
specific file, and switch to the newest `Journal.*.log` when it appears. We must
NOT seek to end-of-file on first attach — we replay the current journal from the
top so we know our starting `Loadout`, current system, etc.

### 3.2 Journal events the bot consumes

Real samples below are taken from the user's Cutter journal of 2026-01-10.

#### 3.2.1 Startup / loadout

`Loadout` — fires at game start, ship swap, outfitting changes.

```json
{ "event":"Loadout", "Ship":"cutter", "ShipID":14, "ShipName":"[Mine] Laser",
  "MaxJumpRange":31.288385,
  "FuelCapacity":{ "Main":64.000000, "Reserve":1.160000 },
  "Modules":[ /* including FrameShiftDrive item id */ ] }
```

**Bot extracts:** `MaxJumpRange`, `FuelCapacity.Main`, `FuelCapacity.Reserve`,
`Ship` (class id), and scans `Modules` for:
- `int_hyperdrive_*` — note `_overcharge_` variant = SCO-capable
- `int_fuelscoop_size*_class*` — **must be present or bot refuses to start** for reqs 2 & 3
- `int_detailedsurfacescanner_tiny` — required for req 6

> **VERIFY:** the loadout sample shown is the user's mining Cutter without a
> visible fuel scoop in the truncated module list. The bot must hard-abort on
> startup if no scoop is fitted, with a clear error.

#### 3.2.2 Routing

`FSDTarget` — fires when next jump target locks, **before** jump start.

```json
{ "event":"FSDTarget", "Name":"V886 Centauri", "SystemAddress":2931071912299,
  "StarClass":"DA", "RemainingJumpsInRoute":1 }
```

This is the **single most important event for safety**: `StarClass` arrives
before we commit to the jump. The bot inspects it and can refuse to proceed if
the next star is in the danger list (see §5.1.3).

`NavRoute` — fires when route is plotted; payload is in `NavRoute.json`:

```json
{ "Route":[ {"StarSystem":"...","SystemAddress":N,"StarPos":[x,y,z],"StarClass":"K"}, ... ] }
```

`NavRouteClear` — fires when route is completed or cancelled.

#### 3.2.3 Jump cycle

`StartJump` — countdown start (charge → witch space).

```json
{ "event":"StartJump", "JumpType":"Hyperspace", "StarSystem":"...",
  "SystemAddress":N, "StarClass":"K" }    // hyperspace variant carries StarClass
{ "event":"StartJump", "JumpType":"Supercruise", "Taxi":false }   // SC variant — no class
```

`FSDJump` — exit from witch space; we're now in the new system.

```json
{ "event":"FSDJump", "StarSystem":"Core Sys Sector QT-R b4-6",
  "SystemAddress":13864825595321, "StarPos":[-14.78125,8.59375,35.03125],
  "Body":"Core Sys Sector QT-R b4-6 A", "BodyID":1, "BodyType":"Star",
  "JumpDist":72.436, "FuelUsed":4.981084, "FuelLevel":27.018915 }
```

**Critical:** `BodyType:"Star"` confirms we exited at a star (the normal case).
`FuelLevel` is our remaining fuel after the jump — feed to the fuel guard.
`StarClass` is **not** in `FSDJump` — only in the prior `FSDTarget` /
`StartJump`. The bot caches the upcoming `StarClass` and pairs it with the
`FSDJump` event.

`SupercruiseEntry` — entering supercruise (e.g., after dropping into normal space).
`SupercruiseExit` — dropping out at a body. `Body` and `BodyType` tell us what
we're near. Seen values: `"Star"`, `"Planet"`, `"Station"`.

#### 3.2.4 Fueling

`FuelScoop` — fires repeatedly while scooping. `Scooped` is delta this tick,
`Total` is total fuel currently in the tank.

```json
{ "event":"FuelScoop", "Scooped":4.981064, "Total":32.000000 }
```

The bot uses `Total >= FuelCapacity.Main * 0.98` to decide "stop scooping". We
don't trust `>= Main` exactly due to float rounding (Cutter sample shows
`Total:32.000000` mid-scoop with `Main:64`).

`ReservoirReplenished` — passive top-up of the reservoir from main tank. Not
used as a trigger; informational.

#### 3.2.5 Exploration

`FSSDiscoveryScan` — the **honk** completed. `Progress:1.0` = fully resolved.

```json
{ "event":"FSSDiscoveryScan", "Progress":1.000000, "BodyCount":28,
  "NonBodyCount":52, "SystemName":"Kokoller", "SystemAddress":9704579273450 }
```

`FSSAllBodiesFound` — all bodies in the system have been FSS-resolved.

`FSSSignalDiscovered` — non-body signal revealed (nav beacon, station, USS).

`Scan` — body data. `ScanType` is `"AutoScan"` (the freebie from arrival),
`"Detailed"` (full FSS), or `"NavBeacon"`.

```json
{ "event":"Scan", "ScanType":"AutoScan", "BodyName":"... A", "BodyID":1,
  "StarType":"M", "Subclass":3, /* ... */ "WasDiscovered":true, "WasMapped":false }
```

The bot maintains a per-system table keyed by `BodyID` of:
`{type, distance_ls, mass, was_discovered, was_mapped, fss_done, dss_done}`.

`SAAScanComplete` — DSS finished mapping a body.

```json
{ "event":"SAAScanComplete", "BodyName":"... 1 a", "BodyID":2,
  "ProbesUsed":6, "EfficiencyTarget":9 }
```

`SAASignalsFound` — bio/geo signals revealed by DSS.

#### 3.2.6 Hazards

`HullDamage` — payload includes `Health` (0..1). Trigger panic abort if
< configurable threshold (default 0.7).
`JetConeBoost` — neutron / WD supercharge. Ban from bot route in v1; treat as
abort if it fires unexpectedly.
`JetConeDamage` — bot panics, retracts everything, attempts to flee.
`Music` — `MusicTrack:"Combat_Dogfight"` etc. is a heuristic interdiction signal.

### 3.3 Status.json fields

Update cadence: ~1 Hz baseline, more often on flag/position change. Single JSON
object, rewritten in place.

| Field | Type | Used for |
|---|---|---|
| `Flags` | uint32 bitfield | the primary fast-path state probe |
| `Flags2` | uint32 bitfield | on-foot states (we should not be) |
| `Fuel.FuelMain` | float | live fuel for guard checks |
| `Fuel.FuelReservoir` | float | reservoir for emergency calc |
| `GuiFocus` | uint | which UI panel is open (galmap, sysmap, FSS, SAA) |
| `Pips` | [int,int,int] | weapons/engines/systems half-pips |
| `FireGroup` | uint | active firegroup index |
| `Heat` | float | (where present) cockpit temp, 0..1+ |
| `LegalState` | string | "Clean" / "Wanted" — abort if not Clean |
| `BodyName` | string | what body we're near |
| `Latitude/Longitude/Altitude/Heading` | float | landed/glide only |

#### 3.3.1 Flags we read

| Bit | Name | Bot meaning |
|---|---|---|
| 0 | Docked | abort cycle, we're not flying |
| 1 | Landed | abort cycle |
| 3 | ShieldsUp | informational |
| 4 | Supercruise | gates state transitions |
| 16 | FsdMassLocked | wait before issuing jump |
| 17 | FsdCharging | jump committed |
| 18 | FsdCooldown | wait for cooldown end |
| 19 | LowFuel | redundant with our own calc but a safety check |
| 20 | Overheating | panic — pitch away, deploy heat sink |
| 23 | InWing | bot stops; user joined a wing |
| 24 | InMainShip | required true |
| 25 | InFighter | abort |
| 26 | InSRV | abort |
| ... | ScoopingFuel | should be true while we expect to scoop |

> **VERIFY:** bit positions for `ScoopingFuel`, `FsdCharging`, `FsdMassLocked`
> against [elite-journal.readthedocs.io Status File](https://elite-journal.readthedocs.io/en/latest/Status%20File.html).
> Several community lists disagree on minor bits.

#### 3.3.2 GuiFocus values

`0` = no focus, `1` = internal panel, `2` = external, `3` = comms, `4` = role,
`5` = stationServices, `6` = galaxyMap, `7` = systemMap, `9` = FSS, `10` = SAA,
`11` = codex. The bot uses these to confirm UI state before sending UI keys.

---

## 4. Outputs — how we drive the game

### 4.1 Keybind layer

The user's existing ED keybinds may not match the bot's expectations. We can't
introspect ED bindings reliably (the XML lives at `%LOCALAPPDATA%\Frontier
Developments\Elite Dangerous\Options\Bindings\Custom.4.0.binds` and uses
human-readable but version-volatile names). Therefore:

- The bot ships a **reference binds preset** (`ed-autojump.binds`) the user
  imports in ED options before first use.
- The Executor only knows logical actions (`HONK`, `FSD_ENGAGE`, `DEPLOY_SCOOP`,
  `PITCH_UP`, `THROTTLE_ZERO`, `FIRE_GROUP_NEXT`, `OPEN_FSS`, `LEAVE_FSS`,
  `OPEN_SAA`, `FIRE_PROBE`, `TARGET_NEXT_SYSTEM_IN_ROUTE`, `JUMP`, ...).
- Each logical action maps to a configured scancode in `config.toml`. The shipped
  preset uses a documented set the user can review and override.

### 4.2 Input injection

- **Library**: `pydirectinput` with explicit scancodes. Reason: ED ignores
  `SendInput` virtual-key codes unless prefixed with `KEYEVENTF_SCANCODE`. Prior
  art (skai2/EDAutopilot, EDAPGui) confirms this.
- **Hold semantics**: many ED actions require hold (e.g., `PITCH_UP` for 2.5s).
  We use `key_down` + `time.sleep(d)` + `key_up`, NOT repeated press, because ED
  ignores rapid-fire press/release for analog-like inputs.
- **Spacing**: minimum 50 ms between distinct actions. Some sequences need
  150–250 ms for the game to register the previous action (e.g., between
  fire-group cycle and trigger press).
- **No mouse**: v1 avoids mouse input entirely. FSS frequency tuning will use
  keyboard-only bindings (community-documented `FSS Tune Up/Down`).

### 4.3 Action catalog (v1)

| Logical action | Default binding | Hold? | Used by |
|---|---|---|---|
| `THROTTLE_ZERO` | `END` (Backspace below 25%) | — | jump prep |
| `THROTTLE_75` | `7` (numpad) | — | scoop entry |
| `THROTTLE_100` | `HOME` | — | post-clear acceleration |
| `PITCH_UP` | `S` (HOTAS users vary) | hold variable | post-exit star avoidance |
| `PITCH_DOWN` | `W` | hold variable | scoop technique |
| `YAW_LEFT/RIGHT` | `A`/`D` | hold variable | scoop alignment |
| `BOOST` | `Tab` | — | emergency escape |
| `FSD_ENGAGE` | `J` | — | hyperjump |
| `SUPERCRUISE` | `J` (context) | — | enter SC |
| `TARGET_NEXT_SYSTEM` | `H` | — | route follow |
| `HONK` | `Shift+\` (Discovery Scanner fire group secondary) | — | req 4 |
| `OPEN_GALMAP` | `;` | — | manual route plot |
| `OPEN_SYSMAP` | `'` | — | body inventory |
| `OPEN_FSS` | `Space+1` mode switch combo | — | req 5 |
| `LEAVE_FSS` | `Space+1` again | — | req 5 |
| `FSS_TUNE_UP/DOWN` | `Mouse Wheel` → rebind to keys | hold | req 5 |
| `FSS_ZOOM_IN/OUT` | bound keys | — | req 5 |
| `OPEN_SAA` | mode switch | — | req 6 |
| `FIRE_PROBE` | trigger (post-firegroup-switch) | — | req 6 |
| `DEPLOY_SCOOP` | (auto with hardpoints retracted near star) | — | req 2 |
| `HEATSINK` | configured key | — | panic |
| `PAUSE_MENU` | `ESC` | — | emergency stop |

> **VERIFY:** the FSS / SAA mode switch is currently `1` while holding Mode
> Switch modifier; community guides list slight variations across patches.
> A binds preset will normalize this.

---

## 5. Per-requirement implementation

### 5.1 Requirement 1 — Jump to any destination

#### 5.1.1 Two routing modes

- **In-game plotter mode**: user opens the galaxy map, plots a route, closes
  the map. Bot detects `NavRoute.json` populated and follows it. Fuel safety
  guard runs over the *existing* route and aborts if a leg is unsafe.
- **External plotter mode** (recommended): user provides
  destination system in config or via CLI; bot calls Spansh
  `POST /api/route?efficiency=60&range=R&from=A&to=B` with `R` =
  `Loadout.MaxJumpRange × 0.97` and asks for fuel-aware routing. Bot then plots
  this route via the galmap UI by entering each waypoint sequentially —
  OR (lighter implementation) by accepting that ED only takes one destination
  and re-targeting on each `FSDJump` arrival.

> **Decision deferred:** v0.1 implements **external plotter, step-targeting**:
> rather than plotting a full multi-jump route in-game (which requires UI
> navigation we'd rather avoid), the bot targets the *next system* on each
> arrival using galmap → "find system" → enter waypoint name → engage. Slower
> per jump (~8 sec UI overhead) but radically more robust than walking the route
> tree.

#### 5.1.2 Per-jump sequence (state diagram)

```
ARRIVED ──► AWAIT_FUEL_DECISION ──► [scoop or skip]
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

#### 5.1.3 Route safety filter (req 3 implemented here)

Before accepting any waypoint sequence the planner computes, for each leg `i`:

1. **Distance check:** `leg.dist <= MaxJumpRange × 0.97`. Margin covers
   mass-locked deviation and SCO mis-fire.
2. **Fuel-after-jump:** `predicted_fuel_after = current_fuel − fsd_fuel_cost(dist)`.
   FSD fuel cost is non-linear in distance; we use the well-known formula
   `cost = LinearConstant * MaxFuelPerJump * (dist / MaxJumpRange)^PowerConstant`
   where `LinearConstant` and `PowerConstant` are FSD-rating specific. We bundle
   a static table per FSD `(class, rating, overcharge_yes/no)`.
3. **Scoop-window check:** find the next leg whose destination is in KGBFOAM
   (allowable refuel point). If `predicted_fuel_after_n_legs <
   FuelCapacity × 0.20` before reaching a scoopable star, the route is unsafe.
   Abort planning, ask Spansh for a fuel-prioritised re-plan, or stop.
4. **Danger-star filter:** reject any leg where `StarClass` is in
   `{D*, N, H, AeBe, W*, C*, MS, S}` — i.e., white dwarf families, neutron,
   black hole, Herbig Ae/Be, Wolf-Rayet, carbon stars, S-type, MS-subclass.
   These either can't be scooped, have dangerous jets, or both. Reason given in
   §5.2.

> **VERIFY:** `AeBe` is rare and Spansh treats it as scoopable in some
> contexts. Confirm with sample data before finalising.

### 5.2 Requirement 7 — Don't collide with the star (the hard one)

#### 5.2.1 What I had wrong

The naïve plan was "after `FSDJump`, hold pitch-up for 2 seconds and boost."
This is **wrong**:

- You exit witch space **already pointed at the primary**, at
  **carry-over throttle**. The reference samples have the player flying with
  cruise throttle and arriving in motion. By the time `FSDJump` fires in the
  journal you have ~1 second before heat starts climbing for a too-close exit.
- Pitch-up is the right escape direction **only if the star is below you**. In
  binary/trinary systems you may exit near a secondary; the primary is then
  off-axis and pitch-up can be wrong.
- Some star classes have asymmetric exclusion zones (neutron jet cones).
  Pitch-up into a cone is catastrophic — well-documented community pitfall.

#### 5.2.2 Correct procedure

The bot enforces a multi-stage sequence that begins **before** witch space:

**T-pre-charge (during `StartJump` countdown):**
1. Send `THROTTLE_ZERO`. Verify next `Status.json` shows engine pips engaged and
   throttle ≈ 0 (we can't read throttle directly from Status; we infer from a
   ~600 ms pause where `StartJump` countdown continues).
2. If `StartJump.StarClass` is in the **danger list** (see 5.1.3), **abort the
   jump**. The window to abort is the ~5 second charge: release `J` (or send
   the FSD-engage toggle key again) and the charge cancels. Status `Flags` bit
   17 (`FsdCharging`) clearing within 1 s confirms abort.

**T+0 (witch space → normal space, `FSDJump` event observed):**
3. Immediately `PITCH_UP` for `t_pitch` seconds, where `t_pitch` is
   class-dependent (table below). During pitch, also send `THROTTLE_75` to
   start moving once we're off the star vector. Boost is **only** used if heat
   exceeds 60% (see step 5).
4. After `t_pitch`, release pitch and check `Status.Flags` for `Overheating`
   (bit 20) and `FsdMassLocked` clearing.

**T+~3 s:**
5. If `Heat > 0.6` (Status, when available), deploy `HEATSINK`. If still
   climbing past 0.9, send `BOOST` to widen distance and re-pitch perpendicular
   to the (estimated) star vector. The bot does **not** know exact star
   position; we rely on the heat trend.
6. If `FuelLevel < FuelCapacity * configured_refuel_threshold` (default 0.7)
   **and** `FSDJump.StarClass in KGBFOAM`, transition to `SCOOPING`. Otherwise
   transition to `HONK_AND_SCAN`.

##### Class-conditional pitch parameters (initial table — **VERIFY in flight test**)

| Star class | Notes | `t_pitch` (s) | Throttle after pitch | Notes |
|---|---|---|---|---|
| K, G, F | "safe" yellow/orange | 2.0 | 75% | normal scoop class |
| B, A | hot blue | 3.0 | 50% | bigger exclusion zone, more heat |
| O | very hot blue | 4.0 | 50% | rare, big zone |
| M | cool red | 1.5 | 75% | smallest mainline class |
| L, T, Y | brown dwarfs (non-scoop) | 1.5 | 100% | safe to leave fast |
| **D\*** (white dwarf) | **DANGER** | 4.5 | 50% | route filter should prevent us being here |
| **N** (neutron) | **DANGER** | 4.5 | 50% | route filter should prevent us being here |
| **H** (black hole) | **DANGER** | 4.0 | 50% | route filter should prevent us being here |
| **W\*** (Wolf-Rayet) | **DANGER** | 4.0 | 50% | route filter should prevent us being here |

> Even with the route filter, we leave the dangerous-class entries in the
> table because the user *could* plot manually into one. The post-exit macro
> must survive arrival at any class.

#### 5.2.3 Heat as the ground-truth signal

We can't see the star. We *can* see heat. The bot's escape correctness check
is `Heat` trend over the 5 seconds following `FSDJump`. If `Heat` ever exceeds
1.0 we deploy heatsink immediately and trigger a wider escape (full boost +
re-pitch). The bot logs every escape's `Heat_max` so we can tune the
class-conditional table from real data.

### 5.3 Requirement 2 — Refuel on every KGBFOAM star

#### 5.3.1 Trigger condition

After §5.2.2 escape completes and `FSDJump.StarClass` is in `{K,G,B,F,O,A,M}`,
and `FuelLevel < FuelCapacity * refuel_threshold` (default 0.7), enter
`SCOOPING`.

#### 5.3.2 Scoop technique

The community-standard technique is **graze the blue zone** at ~30 km/s after
escape. The bot does not know its exact distance to the star and cannot read
the throttle blue-zone position from Status. Approximation:

1. `THROTTLE_75` for 2 seconds (closes distance after the pitch-up).
2. `THROTTLE_25` (default `2` numpad) to settle into approximate scoop speed.
3. Watch for first `FuelScoop` event — confirms we're in scoop range with
   hardpoints retracted (scoop deploys automatically with hardpoints in).
4. If no `FuelScoop` event after 5 s and `Heat < 0.5`, throttle up 25% and
   wait 3 s more. Repeat up to 2 cycles.
5. If `Heat > 0.85`, throttle to 0 and `PITCH_UP` 1 s to back off.
6. Continue until `FuelScoop.Total >= FuelCapacity.Main * 0.98` or we go
   `RemainingJumpsInRoute == 0` (no more route — stop).
7. `THROTTLE_75`, `PITCH_UP` 2 s to escape the corona, then resume the cycle.

#### 5.3.3 What can go wrong

| Failure | Detection | Response |
|---|---|---|
| Hardpoints accidentally deployed | `Flags.HardpointsDeployed` true | retract via key, retry |
| Drop into exclusion zone | `HullDamage` or sudden `Music:GalaxyMap`-pattern | abort scoop, log, continue route at next system |
| Heat runaway | `Heat > 1.0` | heatsink + emergency pitch + boost |
| Stuck in scoop (not approaching) | no `FuelScoop` event in 10 s | throttle adjust + log |
| Scoop module damaged | hull damage events with module reference | route to nearest station for repair (not v1; just stop) |

### 5.4 Requirement 4 — Honk every system

Easy. After §5.2.2 escape and (optionally) §5.3 scoop completes, while still in
supercruise near the arrival star:

1. Confirm `Flags.Supercruise` true and `GuiFocus = 0`.
2. Send `HONK` (Discovery Scanner secondary fire).
3. Wait for `FSSDiscoveryScan` event with `Progress == 1.0`.
4. Note `BodyCount` and `NonBodyCount` for the body-table init.

Timeout: if no `FSSDiscoveryScan` within 8 seconds, retry once. If still no
event, log and continue (the system may have already been honked in a previous
session, but `FSSDiscoveryScan` should still fire — investigate).

### 5.5 Requirement 5 — FSS every body (tier C)

This is the first requirement that **cannot** be done from journal alone.
`FSSAllBodiesFound` tells us when FSS is *complete*, not how to make it
progress. We need to enter FSS mode, drag the frequency band across all
visible signal sources, and confirm-resolve each.

#### 5.5.1 Two paths

- **Path A — keyboard-only FSS sweep:** enter FSS, `FSS_TUNE_DOWN` from max to
  min over `t_sweep` seconds, then back up. Each pass should resolve any signal
  in that band. Without CV we have no way to confirm a resolve happened; we
  rely on `Scan` events firing for each body. This is brittle for systems
  where a signal sits at one specific frequency and requires zoom + center.
- **Path B — CV-assisted FSS:** capture the FSS HUD region, detect the "broken
  circle" signal indicators near the centre reticle, tune in their direction
  until solid, then trigger `Scan` (left mouse / configured key) to resolve.

Decision for v0.1: ship **Path A** as default, **Path B** behind a feature flag.
Path A's pass-rate is unknown but ≥1 body per system is the floor (the primary
star auto-scans on arrival from the AutoScan event).

#### 5.5.2 Termination

- `FSSAllBodiesFound` fires → done, leave FSS mode.
- 90-second timeout per system → leave FSS, log incomplete, continue route.
- Number of `Scan` events received during the FSS session is logged for
  per-system completion-rate telemetry.

### 5.6 Requirement 6 — DSS every body (tier C, hardest)

This is *the* unsolved problem in public ED automation. No reliable open-source
DSS bot exists. We accept v0.1 will have known failure modes.

#### 5.6.1 Per-body workflow

For each `Scan` event where `BodyType` is mappable
(planet/moon — **not** star/asteroid belt) and `was_mapped == false`:

1. From route node "scanned", select body in nav panel — keyboard cycle.
2. `TARGET_BODY`, supercruise toward it.
3. Approach at high throttle until `Status.BodyName == target_body` and the
   speed-blue-zone appears. We approximate "right speed" via heuristic: drop
   throttle to 0 when `Status.Altitude` (which only appears near planets at
   <1 megameter) is populated and < threshold.
4. `OPEN_SAA` (mode switch to surface scanner). Confirm `GuiFocus == 10`.
5. Body rotation is ignored in v0.1 — we aim probes in a 6-direction star pattern
   (forward, up, down, left, right, back) and rely on body size to make this
   sufficient. Confirmed efficient bonuses (`ProbesUsed <= EfficiencyTarget`)
   are *not* a goal; coverage >= 90% is.
6. Fire probes one at a time, repointing between shots.
7. Wait for `SAAScanComplete`. Log probe count vs target.

#### 5.6.2 Known failure modes accepted

- Probes overshooting on small bodies → wasted probes.
- Bodies with high rotation → probes land on backside, coverage incomplete →
  no `SAAScanComplete`, hit timeout (120 s) → mark body "DSS failed", continue.
- Gas giants too large → may need >6 probes; the bot keeps firing until
  probe ammo depletes or timeout.

#### 5.6.3 Per-system DSS budget

User-configurable. Defaults:
- Map only "high-value" bodies: terraformable, water world, ELW, ammonia world,
  earth-likes. These are identifiable from the `Scan` event's `PlanetClass`.
- Skip bodies more than `DSS_MAX_LS_DISTANCE` (default 50 000 Ls) from arrival
  to avoid 30-minute supercruise legs.
- Hard cap: 4 bodies per system.

This is **not** "DSS every body" in the literal sense. The full literal goal is
out of scope for v0.1. Spec to be revised once v0.1 DSS reliability is measured.

---

## 6. Master state machine

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
- `JUMPING` requires `Flags.FsdCharging` true (we set it, game confirms).
- `SCOOPING` requires `FSDJump.StarClass in KGBFOAM` *and* `FuelLevel < threshold`.
- `PANIC` is sticky — only resolves on user intervention or after a safe-state
  timeout (`Status.Flags.InMainShip & ~Overheating & Heat < 0.5` for 5 s).

---

## 7. Safety & abort

### 7.1 Auto-abort triggers

| Trigger | Source | Action |
|---|---|---|
| `HullDamage.Health < 0.7` | journal | `PANIC` — `THROTTLE_ZERO`, `BOOST`, retract everything |
| `Status.Flags.Overheating` | status | `HEATSINK` if available, `BOOST`, abort scoop |
| `Status.Flags.LowFuel` | status | confirm against our calc; if real, route to scoop |
| `Music.MusicTrack == "Combat_Dogfight"` | journal | `PANIC` |
| `Interdicted` event | journal | `PANIC` — submit + boost run? user-configurable |
| `JetConeDamage` | journal | `PANIC` |
| Unexpected `Docked` / `Landed` | status | user intervened — pause bot |
| `Flags.InWing` true | status | user joined wing — pause bot |
| Watchdog: no journal event in 90 s while game responsive | timer | `PANIC` |
| User pressed configured panic key | hotkey listener | clean stop |

### 7.2 Clean stop

User-configurable panic hotkey (default `Ctrl+Alt+P`) triggers:
1. Release all held keys.
2. `THROTTLE_ZERO`.
3. Retract scoop / hardpoints.
4. Drop to `IDLE`. Log full transcript of the last leg.

### 7.3 What we cannot recover from

- Repair / refit / rebuy. Once destroyed, bot exits and a human deals with it.
- Mid-supercruise interdiction during high-speed exploration legs — limited
  options; we submit and try to outrun, then re-plot.

---

## 8. Configuration

`config.toml` (UTF-8, in working directory). Example:

```toml
[ship]
# Auto-extracted from Loadout but pinnable for testing
expected_ship = "cutter"
expected_max_jump_range_ly = 31.288
expected_fuel_capacity_t = 64.0

[routing]
mode = "external_spansh"            # or "in_game_plotter"
destination = "Beagle Point"
efficiency = 60                     # Spansh 0..100, lower = shorter legs
range_margin = 0.97
fuel_safety_threshold = 0.20        # min fuel ratio at next scoopable star
refuel_threshold = 0.70             # scoop trigger
danger_classes = ["D","DA","DAB","DC","N","H","W","WC","WN","WO","AeBe","C","S","MS"]

[exploration]
honk = true
fss = "off"                         # "off" | "keyboard_sweep" | "cv_assisted"
dss = "off"                         # "off" | "high_value_only" | "all"
dss_max_distance_ls = 50000
dss_per_system_cap = 4

[safety]
hull_panic_threshold = 0.70
heat_panic_threshold = 1.00
heatsink_threshold = 0.80
no_journal_timeout_s = 90
panic_hotkey = "ctrl+alt+p"

[input]
backend = "pydirectinput"
key_delay_ms = 75
pitch_up_default_s = 2.0
# class-specific overrides
class_pitch_overrides = { K = 2.0, G = 2.0, B = 3.0, A = 3.0, O = 4.0, M = 1.5 }

[paths]
journal_dir = '%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous'
log_dir = './logs'
```

---

## 9. Telemetry & logging

- Per-jump record (JSONL): timestamp, system in/out, distance, fuel before/after,
  star class, heat max, time-in-scoop, scoop tons added, FSS/DSS results,
  any aborts.
- Per-session summary on exit: jumps completed, bodies honked, FSS rate, DSS
  rate, total panics.
- All sent keys logged with timestamp — required for diagnosing input failures
  and tuning pitch / throttle timings.

---

## 10. Testing strategy

### 10.1 Offline tests (no game running)

- Replay tests: feed recorded `.log` lines into JournalTail, assert state
  transitions. We bundle a corpus from real journals (with system names
  anonymised? — user choice).
- Route planner unit tests: golden fixtures of (loadout, start, end) →
  expected (route, fuel-after-each-leg).
- Status parser tests against checked-in `Status.json` snapshots.

### 10.2 In-game calibration (supervised)

- **Single-jump test** (req 1, 7): plot one short jump to a known K-class
  system. Verify escape sequence, no heat overrun, scoop triggers.
- **Multi-jump fuel test** (req 2, 3): plot 5-jump route through mixed K/M
  stars with one M-only stretch. Verify scoop triggers only on K, fuel never
  drops below threshold.
- **Honk test** (req 4): verify `FSSDiscoveryScan` fires within 8 s in every
  system over a 10-system run.
- **Danger-class refusal test**: manually plot a route through a known DA
  (the user's journal shows `V886 Centauri` is DA). Verify bot refuses to jump
  and clean-stops.

### 10.3 Unsupervised endurance (req fully satisfied for tier A/B)

- 50-jump run, M+K mix, no exploration. Pass criteria: all jumps complete, no
  hull damage, no fuel-out, no panic state.
- 200-jump run. Pass criteria: as above plus zero unhandled exceptions, log
  shows clean shutdown.

### 10.4 Tier C calibration (FSS / DSS)

- Manual recording of 10 FSS sessions to calibrate sweep duration.
- Manual recording of 10 DSS sessions to calibrate probe-pattern timings.
- Per-body coverage success rate becomes a tracked metric, not a hard pass/fail.

---

## 11. Risks & open questions

| Risk | Severity | Mitigation |
|---|---|---|
| Frontier patches change `Music` / `Flags` / event shapes | M | Pin to journal manual v32, regression-test on each patch via replay corpus |
| User's keybinds differ from preset | H | Ship binds file, refuse to start if heuristic mismatch detected |
| HDR breaks CV (tier C) | H | Document SDR requirement for FSS/DSS modes; auto-detect with brightness probe |
| Class-pitch table is wrong for user's ship mass | H | Calibrate per-ship from supervised runs; bias toward longer pitch |
| Mass-locked at scoopable secondary star | M | If primary is e.g. M with mass-locking secondary nearby, can't FSD; bot must SC away first |
| Spansh API rate-limit / outage | L | Cache last computed route; fall back to in-game plotter |
| Bot detected by Frontier as "input cheating" | L (user assertion) | We don't conceal; if banned, user accepts consequence |

### 11.1 Things we don't yet know and must learn from supervised testing

- Actual heat trajectory by class for the user's specific ship.
- Whether step-targeting (re-target on each `FSDJump`) is fast enough to be
  worth the robustness over multi-jump route plotting.
- Real `FSSAllBodiesFound` rate from keyboard-only FSS sweep.
- Whether Spansh fuel-aware routing is sufficient or we need our own
  per-leg recomputation.
- DSS coverage rate from naive 6-direction probe pattern (likely poor; we'll
  measure and decide whether to invest in rotation-aware aiming).

### 11.2 Decisions deferred to implementation

- Whether to subclass EDMC's journal parser or write our own (EDMC's is well
  tested; ours is dependency-free).
- Whether to use `asyncio` or threads for the tail watchers.
- Mouse input as a fallback if keyboard binds aren't sufficient for FSS
  precision tuning.

---

## 12. Implementation phases

| Phase | Scope | Exit criteria |
|---|---|---|
| 0 | Project skeleton, config, journal/status tailers, replay-test harness | Replay sample journal end-to-end without crash |
| 1 | Req 4 (honk) only — minimum viable bot | 10-system manual route → 10 honks logged |
| 2 | Req 1+3+7 — jump+escape+route safety | 50-jump unsupervised run, no fuel-out, no hull damage |
| 3 | Req 2 — scoop integration | 200-jump unsupervised run |
| 4 | Req 5 — FSS keyboard sweep | per-system FSS completion measured |
| 5 | Req 5 — FSS CV-assisted (optional) | improvement over keyboard sweep measured |
| 6 | Req 6 — DSS naive pattern | per-body success rate measured |
| 7 | Req 6 — DSS rotation-aware (optional) | improvement measured |

---

## 13. References

- [Frontier Player Journal Manual v32 (PDF)](https://hosting.zaonce.net/community/journal/v32/Journal_Manual-v32.pdf) — canonical event reference
- [elite-journal.readthedocs.io](https://elite-journal.readthedocs.io/) — community-maintained journal schema
- [ED Wiki: Hyperspace](https://elite-dangerous.fandom.com/wiki/Hyperspace), [Exclusion Zone](https://elite-dangerous.fandom.com/wiki/Exclusion_Zone), [Fuel Scoop](https://elite-dangerous.fandom.com/wiki/Fuel_Scoop), [Mass Lock](https://elite-dangerous.fandom.com/wiki/Mass_Lock)
- [Deep Space Network: Dangers of Exploration](https://ed-dsn.net/en/dangers-exploration-2/), [Neutrons & White Dwarfs](https://ed-dsn.net/en/neutrons-2/)
- [Spansh route plotter](https://spansh.co.uk/) and API
- [skai2/EDAutopilot](https://github.com/skai2/EDAutopilot), [Numerlor/Auto_Neutron](https://github.com/Numerlor/Auto_Neutron), [Somfic/EliteAPI](https://github.com/Somfic/EliteAPI), [EDCD/EDMarketConnector](https://github.com/EDCD/EDMarketConnector) — prior art
- Real journal samples from this user's machine (Cutter, MaxJumpRange 31.29 LY, 64 t fuel) at `Journal.2026-01-09T194605.01.log`.
