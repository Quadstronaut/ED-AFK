# Heat watchdog — flight-only coverage during procedures

**Date:** 2026-06-06 · **Status:** approved by operator (option 2 of 3)

## Problem

`heat_guard` (reactive `DeployHeatSink` on the OverHeating status flag) runs
only in `run_live`'s outer loop — between procedures. During a procedure
(SC-assist orbit, fly-outs, alignment holds, the arrival star-escape — the
hottest moments) no heatsink can fire.

## Decision

A dedicated watchdog thread with an exclusive-input gate:

- **Watchdog thread** — daemon, started by `run_live`, stopped on
  exit/stop/panic. Ticks ~1 Hz; each tick polls status and calls the existing
  `heat_guard()` (OverHeating flag + 10s debounce unchanged).
- **Exclusive-input gate** — `steps.INPUT_EXCLUSIVE_ACTIONS =
  {"sc_assist_orbit", "nav_panel_target"}`. The interpreter wraps those steps
  in a FlowRunner-owned guard (counter + lock, exposed on `StepContext` as
  `exclusive_guard`); the watchdog skips its tick while the count is > 0.
  Counter, not a bool, so a parallel track can't clear the main track's hold.
- **Single owner** — the inline `heat_guard()` call in `run_live`'s loop is
  removed; the thread is the only eject path.
- **`_poll_status` gets a lock** — main loop, honk waiters, and the watchdog
  all read Status.json through it.
- Sender concurrency is the already-accepted pattern (honk holds a key while
  the main track presses); a heatsink tap adds nothing new.

## Coverage

Protected: alignment holds, star escapes, fly-outs, future scooping.
Blind by design: the two nav-panel macros (a few seconds each, cold
contexts) — a stray keypress there can desync panel UI state.

## Rejected

1. Always-on thread (panel desync risk for no real heat scenario).
2. In-loop `ctx.tick()` hooks (a future step that forgets the hook is
   silently unprotected; concurrency-free but coverage-fragile).

## Tests

- Guard held → tick ejects nothing; released + overheating → ejects.
- Tick respects panic/stop.
- Interpreter enters/exits the guard around exclusive steps (and releases on
  step exception).
