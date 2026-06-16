# C-series boot determination — Stage-0 authoritative spec (re-run)

**Date:** 2026-06-15
**Supersedes:** the under-specified contract that route_back'd `wf_28d4c266-0cc`.
**Scope:** DETERMINATION ONLY. No ACTION bodies. No CV. No live wiring.
**Layering:** `ed_core.boot.*` imports `ed_vision` + stdlib only (DAG rank 1).

This document is the primitive contract. Stage-1 builders implement against it
verbatim. The 7 PINS below are the gaps that sank the prior run; they are now
binding, not advisory.

---

## §0 — Why this spec exists (route_back root cause)

The prior run produced design-correct templates and primitives, the live-path
diff was empty (ship-safe), but the gate caught two boundary defects the
candidates' own verifiers missed:

1. `bounded_poll` was clock-deadline-only. Under a frozen clock
   (`clock=lambda: 0.0`) with any positive ceiling, the deadline is never
   reached → **infinite loop** once wired into the engine. 3 of 4 candidates hung.
2. `reconstruct_arrival_from_journal` had a `SupercruiseExit → True` inversion in
   one candidate (fires the orbit get-around on every routine normal-space drop),
   and a `type(ev).__name__` class-name path that a class literally named
   `FSDJump` in `events.py` spoofs into a ghost arrival.

Everything else was basically right. This is an amend + regenerate, not a redesign.

---

## §1 — The 7 PINS (binding primitive contract — AC2)

**PIN 1 — `bounded_poll` is READ-COUNT-BOUNDED.** The loop bound is `max_reads`
(a read-count cap), NOT a clock deadline. The clock/ceiling are an *advisory
early-exit* only. Under `clock=lambda: 0.0` and a never-matching predicate the
call MUST return with `matched=False` after exactly `max_reads` reads. A
clock-deadline-only loop is non-conformant.

**PIN 2 — Arrival event semantics are fixed.** `FSDJump → True`;
`SupercruiseExit → False`; `SupercruiseEntry → False`. Most-recent qualifying
event decides. Rationale: `SupercruiseExit → True` would fire the arrival orbit
get-around on every routine normal-space drop on cold start.

**PIN 3 — `reconstruct` input contract is dual + name-from-data.** Accept BOTH a
typed event model (read `.event` attribute) AND a raw dict (read `['event']`
key). Resolve the event name from BOTH sources. NEVER `type(ev).__name__`: a
class named `FSDJump` exists in `events.py`; a name-from-type path lets an
instance of it with `event=None` spoof a ghost arrival. The ghost
(`class FSDJump` with `event=None`) MUST NOT count.

**PIN 4 — `PollResult` carries `aborted`.** Abort-before-first-read returns
`reads=0, aborted=True`, distinct from `hit_ceiling`. The LP3 abort path must be
observable.

**PIN 5 — `EXPLORATION.determine()` is telemetry-wired, returns a bool.** Not
`None`. True iff `in_supercruise AND route empty AND NOT arrival_latch.armed AND
exploration_mode`. The arrival latch is UNSET in this state. The FSS *action* is
the Phase-2 part; the *scene detection* is telemetry-sufficient now.

**PIN 6 — `ArrivalLatch` single-threaded precondition is a DOCUMENTED class
invariant.** Exactly-once consume; idempotent arm. The engine loop is
single-threaded — this is stated as a class invariant. NO `threading.Lock`.

**PIN 7 — `fsd_cooldown_blocked(None) → False`, bit 18 only.** Consumer is
block-detection: "don't assert a block without evidence." None status → no
evidence → False. Bit 18 (`FsdCooldown`) only; bits 16/17/30 without 18 → False.

---

## §2 — Interface signatures

### `ed_core.boot.primitives`

```python
@dataclass(frozen=True)
class PollResult:
    matched: bool        # predicate fired on some read
    reads: int           # reads performed (0 if aborted before first read)
    hit_ceiling: bool    # advisory clock ceiling tripped (early exit)
    aborted: bool        # abort callback returned True before a match
    value: Any | None    # the read value that matched, else None

class ArrivalLatch:
    # INVARIANT: single-threaded. The engine loop owns this. No lock.
    def arm(self) -> None: ...        # idempotent
    def consume(self) -> bool: ...    # exactly-once; True the first time after arm
    @property
    def armed(self) -> bool: ...

def reconstruct_arrival_from_journal(events: Iterable[Any]) -> bool: ...
def fsd_cooldown_blocked(status: Any | None) -> bool: ...
def bounded_poll(read, predicate, *, max_reads, clock=..., ceiling_s=None,
                 sleeper=None, should_abort=None) -> PollResult: ...
```

### `ed_core.boot.scenes`

```python
class CSeriesState(enum.Enum):
    DOCKED, STARTUP, ARRIVAL, REFUEL, TRAVERSAL, EXPLORATION,
    STARSMACK, NO_ROUTE, PAUSE, RESUME, PARKED

@dataclass
class DetermineContext:
    status: Any | None          # parsed Status (or None)
    events: Iterable[Any]       # recent journal events, newest-last
    route_empty: bool           # NavRoute.json Route == []
    arrival_latch: ArrivalLatch
    exploration_mode: bool
    fsd_cooldown: bool
    smacked: bool
    paused: bool
    diverged: bool              # log/state divergence (LP4 resume trigger)

@dataclass
class SceneTemplate:
    state: CSeriesState
    determine: Callable[[DetermineContext], bool | None]  # None = CV-pending
    act: Callable[..., None]                              # raises NotImplemented
    fail_closed: str                                      # named branch

C_SERIES_SCENES: tuple[SceneTemplate, ...]  # 11, priority-ordered
def scene_for(ctx: DetermineContext) -> SceneTemplate | None: ...
```

---

## §3 — 11-state determination table (AC1)

Every state: a determination predicate (telemetry where sufficient, else a
`None` CV-pending sentinel), a named gate, an action sketch marked
`[Phase-2 CV/action pending]`, and a named fail-closed branch.

| # | State | Determine (telemetry) | Gate | Action sketch | Fail-closed |
|---|-------|-----------------------|------|---------------|-------------|
| 1 | DOCKED | `status.docked` | Docked flag (bit 0) | `[Phase-2]` idle / await pit-stop resume | → STARTUP (no status → don't assume docked) |
| 2 | STARTUP | not docked, not SC, route present, not smacked | first status seen, route non-empty | `[Phase-2]` align + throttle to first hop | → NO_ROUTE if route absent |
| 3 | ARRIVAL | `reconstruct_arrival_from_journal(events)` True AND in_supercruise | FSDJump latched (LP1) | `[Phase-2]` orbit get-around + target next | → TRAVERSAL (no arrival evidence) |
| 4 | REFUEL | `status.scooping_fuel` OR (low_fuel AND in_supercruise) | ScoopingFuel flag / LowFuel | `[Phase-2]` hold scoop until full | → ARRIVAL (scoop window is part of arrival) |
| 5 | TRAVERSAL | in_supercruise AND route non-empty AND NOT arrival_latch.armed | SC flag + route present | `[Phase-2]` hold SC-assist toward next hop | → STARTUP (lost SC) |
| 6 | EXPLORATION | in_supercruise AND route empty AND NOT latch AND exploration_mode (**PIN 5**) | SC + empty route + mode | `[Phase-2]` honk / FSS / body tour | → PARKED (mode off) |
| 7 | STARSMACK | `smacked` AND `fsd_cooldown` (CV confirm = None) | last SC drop = star + cooldown | `[Phase-2]` align escape-vector dot, burn out | → ARRIVAL (cooldown cleared, no smack CV) |
| 8 | NO_ROUTE | route empty AND NOT docked AND NOT SC AND NOT exploration_mode | empty route, normal space | `[Phase-2]` idle + overlay "plot a route" | → PARKED (terminal idle) |
| 9 | PAUSE | `paused` (cooperative loop-flag) | LP4 pause flag set | `[Phase-2]` cease dispatch, hold keys off | → PARKED (can't resume) |
| 10 | RESUME | `paused` cleared AND `diverged` (LP4) | unpause + log/state divergence | `[Phase-2]` re-derive scene, re-enter | → STARTUP (re-derive from scratch) |
| 11 | PARKED | terminal: route empty AND NOT SC AND NOT docked AND NOT diverged | nothing to do | `[Phase-2]` idle, hold | (terminal; no further branch) |

`STARSMACK` CV-confirm (was the drop actually at a star, glare-robust) is
Phase-2 → its `determine` returns `None` (CV-pending) when the telemetry gate
(`smacked AND fsd_cooldown`) does not by itself decide. Per §0/route-back, the
telemetry gate alone is sufficient to *enter* STARSMACK; the `None` sentinel is
reserved for the ambiguous case so a builder cannot fabricate a CV verdict.

`scene_for` walks `C_SERIES_SCENES` in priority order and returns the first
template whose `determine` returns truthy. A `None` (CV-pending) is treated as
"not this scene" for routing (honest abstention), and is distinguishable by the
caller inspecting the template directly.

Priority order (highest first, most-specific / safety-first):
PAUSE, RESUME, STARSMACK, ARRIVAL, REFUEL, DOCKED, TRAVERSAL, EXPLORATION,
STARTUP, NO_ROUTE, PARKED.

---

## §4 — The 4 LOCKED PATTERNS (carried verbatim — AC2)

**LP1 — FSDJump arrival latch.** Arrival is detected by an FSDJump (the
hyperspace arrival), latched exactly once, consumed by the arrival handler. The
latch dedups: a second FSDJump into the same system without a fresh plot is not
a re-arrival. (`ArrivalLatch`, PIN 6.)

**LP2 — Cooldown bit-18 pause.** FSD cooldown is bit 18 (`FsdCooldown`) of
Status `Flags`, distinct from mass-lock (16) and charging (17) and the jump bit
(30). Block-detection asserts a block only on evidence. (`fsd_cooldown_blocked`,
PIN 7.)

**LP3 — Bounded poll with ceiling.** Any "wait for an event/state" is a bounded
poll. The bound is a read-count cap (`max_reads`); a wall-clock ceiling is an
*advisory* early exit only. Abort is first-class and observable
(`PollResult.aborted`). (`bounded_poll`, PINs 1 & 4.)

**LP4 — Cooperative pause via loop-flag + RESUME re-derive.** Pause is a
cooperative loop-flag the engine checks between steps (never a thread kill).
RESUME re-derives the scene from current telemetry on the next tick, triggered
by log/state divergence. (PAUSE/RESUME states.)

---

## §5 — Invariants (AC-mapped)

- **INV1 (SHIP-SAFETY):** `classify_startup`/`boot_routes.py`, steps, dispatcher,
  registry, status, events, predicates, procedure TOMLs, and test dirs are
  BYTE-UNTOUCHED. Non-empty diff = Stage-2 BLOCKER. (AC14)
- **INV2:** `ed_core/boot/*` makes NO real `register_*` CALL and imports NO
  domain package. AST-verified. (AC15)
- **INV3:** `whole_tree_import_check.py projects` PASSes with `boot/*` present. (AC13)
- **INV4:** `ed_core/__init__.py` stays non-eager; `boot` is dead-until-imported.
  `boot/__init__.py` re-exports primitives ONLY (no scene side-effect import).
- **INV5 (determinism):** `bounded_poll(clock=lambda: 0.0, never-match)` returns
  `matched=False`. (AC12)
- **INV6:** every `act()` raises `NotImplementedError("[Phase-2 CV/action pending]")`.
- **INV7:** CV-pending determinations return `None` (honest sentinel), never a guess.
- **INV8:** `reconstruct` never uses `type(ev).__name__`. (PIN 3)
- **INV9:** `ArrivalLatch` has no `threading.Lock`; precondition documented. (PIN 6)
- **INV10:** all verification is standalone scripts (no pytest), worktree-isolated,
  never auto-committed. (AC16)

---

## §6 — Acceptance criteria

AC1–AC16 as enumerated in the Stage-0 brief; each maps to a §3/§4/§5 clause and
to a T1–T8 test below.

---

## §7 — Standalone acceptance tests (NO pytest) — T1–T8

Executable scripts under `projects/ed-core/tests_standalone/boot/`. Each prints
`PASS`/`FAIL` lines and exits non-zero on any failure. Run directly with
`python`. They prepend `projects/ed-core/src` (and `ed-vision/src`) to
`sys.path` so they need no install.

- **T1** primitives import + `PollResult.aborted` present (AC3, AC8 shape).
- **T2** `bounded_poll` read-count bound: first-match-wins; cap-no-match →
  `matched=False, reads==max_reads`. (AC5)
- **T3** `bounded_poll` DETERMINISM: `clock=lambda:0.0` + never-match RETURNS
  `matched=False`. Wrapped in a hard wall-clock watchdog so a regression that
  reintroduces the infinite loop FAILS instead of hanging the suite. (AC12)
- **T4** `bounded_poll` abort: abort-before-first-read → `reads==0, aborted=True`,
  `hit_ceiling==False`. (AC8)
- **T5** `reconstruct`: FSDJump(dict)→True; FSDJump(typed)→True;
  SupercruiseExit→False; SupercruiseEntry→False; most-recent-decides;
  ghost `class FSDJump(event=None)`→does NOT count. (AC6, AC7)
- **T6** `fsd_cooldown_blocked`: bit18→True; None→False; bits 16/17/30 w/o 18→False;
  16|18→True. (AC11)
- **T7** scenes: 11 templates; `EXPLORATION.determine()` returns a bool per PIN 5;
  CV-pending returns None; every `act()` raises `NotImplementedError([Phase-2…])`;
  `ArrivalLatch` exactly-once + no `threading.Lock`. (AC4, AC9, AC10)
- **T8** STATIC SHIP-SAFETY: AST scan of `ed_core/boot/*` finds no real
  `register_*` call and no domain import; assert no `type(ev).__name__` literal.
  (AC15, AC2)

Out-of-band (run by the verifier, scripted in §8): AC13 import check, AC14 git
diff empty.

---

## §8 — Stage-2 adversarial checklist (per-lens artifacts)

- **concurrency:** grep `ed_core/boot/*` for `threading` → expect ZERO. Confirm
  `ArrivalLatch` invariant doc-string. Artifact: T7.
- **boundaries:** T2/T3/T4 (frozen clock, abort-before-read, cap-no-match,
  ceiling≤0). Plus: `max_reads=0` → `reads=0, matched=False`.
- **security:** T8 AST scan (no domain import, no register call). No file/network
  I/O in boot/*.
- **spec-conformance:** T5/T6 (event semantics table, bit-18-only), §3 table coverage.
- **failure-recovery:** T4 abort observability; PARKED terminal; fail-closed
  branch per state in §3.

Out-of-band: AC13 (`whole_tree_import_check.py projects`), AC14 (git diff empty).

---

## §9 — OPEN QUESTIONS (unverified game behavior — flagged, NOT coded against)

1. STARSMACK CV-confirm under star glare — glare blinds the compass CV (memory:
   smack-compass-glare). Determination stays telemetry-only; CV is Phase-2.
2. EXPLORATION `exploration_mode` source — is it a launcher flag, a journal
   `Music==Exploration`, or operator config? Treated as an injected bool.
3. REFUEL entry: is `low_fuel AND in_supercruise` a real scoop-imminent signal,
   or does scoop only ever begin via `ScoopingFuel`? Conservative OR for now.
4. PAUSE/RESUME `diverged` definition — what counts as "log/state divergence"?
   Injected bool; LP4 semantics to be pinned in Phase-2.
5. PARKED vs NO_ROUTE overlap — both are normal-space empty-route idles;
   PARKED is the post-completion terminal, NO_ROUTE the never-plotted case. The
   discriminator (was a route ever completed) is an injected/derived flag, here
   approximated by priority order (NO_ROUTE before PARKED).
6. TRAVERSAL vs ARRIVAL boundary within the FRESH_ARRIVAL_WINDOW — the live
   classifier uses a 30s window; this determination layer uses the arrival
   latch instead and does NOT reintroduce a timed window.
7. DOCKED-with-no-status — a fresh boot with no Status.json yet: we fail-closed
   to STARTUP rather than assume docked. Verify against a real cold-boot frame.
