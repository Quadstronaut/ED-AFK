# Overnight runbook

How to capture and validate unattended overnight ed-autojump sessions.

> **Phase 12 shipped.** Executor dispatch is now live: jump → escape →
> scoop → honk all run from the main loop on real journal events.
> Default mode is still NullSender (`--no-engage-keys`) for safe dev
> runs; drop that flag to drive keys. The recording loop continues to
> capture every event + outcome regardless of whether keys are engaged.

## TL;DR

```pwsh
cd G:\Documents\GIT\ED-AFK\projects\ed-autojump

# 0. Pre-flight (do this once per machine and after every binds change):
ed-autojump doctor

# 1. Run for 6 hours, record to ~\ed-afk-sessions\, RECORDING ONLY:
.\scripts\nightly-run.ps1 -DurationHours 6

# 2. Or run the actual autopilot for 6 hours, with key dispatch +
#    Spansh route auto-plot when NavRoute is empty:
ed-autojump run --record --engage-keys --route-plot \
    --destination "Beagle Point" --duration 21600

# Each session lands at %USERPROFILE%\ed-afk-sessions\session_<utc>.jsonl

# In the morning, run the regression suite:
.\.venv\Scripts\Activate.ps1
pytest tests/test_recorded_sessions.py -v
```

## `ed-autojump run` flags

| Flag | Default | Effect |
|---|---|---|
| `--engage-keys` | off | DirectInputSender (real key dispatch). Off uses NullSender. |
| `--record` | off | Write session JSONL to `--sessions-dir` (default `~/ed-afk-sessions`) |
| `--status` / `--no-status` | on | Poll Status.json for OverHeating + IsInDanger flags |
| `--eddn` / `--no-eddn` | from config | Publish FSS scans to EDDN |
| `--route-plot` | off | Auto-plot via Spansh when NavRoute is empty |
| `--destination` | from config | Override route destination (e.g. "Beagle Point") |
| `--duration` | 0 | Seconds to run. 0 = exit immediately (dry-run). |
| `--journal-dir` | from config | Override the FDev Saved-Games journal dir |
| `--sessions-dir` | env / `~/ed-afk-sessions` | Override session output dir |

If the recorded-session tests fail, the failure ID points to which night's
JSONL went bad. Open it and look for `HullDamage`, `outcome` rows with a
danger StarClass, or a long fuel-floor breach.

## Storage layout

```
%USERPROFILE%\ed-afk-sessions\
  session_2026-05-22T230015.jsonl         # the bot's recorded session
  nightly_default_2026-05-22T230015.log   # the runner's stdout
  nightly_default_2026-05-22T230015.err.log   # stderr (often empty)
```

Sessions stay local-only by user policy (not committed to the repo). The
regression suite reads from this directory (or `$ED_AFK_SESSIONS_DIR`).

## Running concurrently against multiple ED installs

You have 4 ED installs, 3 reachable through sandboxie-ng. Each instance
has its own `Saved Games\Frontier Developments\Elite Dangerous\` dir;
sandboxie remaps it. Drive each independently:

```pwsh
# instance A (sandboxie copy)
.\scripts\nightly-run.ps1 `
    -EdInstance "sandbox-A" `
    -JournalDir "C:\Sandbox\$env:USERNAME\sandbox-A\user\current\AppData\Local\Frontier Developments\Elite Dangerous"

# instance B (sandboxie copy)
.\scripts\nightly-run.ps1 `
    -EdInstance "sandbox-B" `
    -JournalDir "C:\Sandbox\$env:USERNAME\sandbox-B\user\current\AppData\Local\Frontier Developments\Elite Dangerous"

# bare (non-sandboxie) — your prime account
.\scripts\nightly-run.ps1 -EdInstance "prime"
```

The `-EdInstance` label is embedded in the runner log filename so concurrent
runs don't collide. The session JSONL filename has its own UTC stamp.

## Task Scheduler (manual import)

By design we don't auto-register a scheduled task. To set one up later:

1. Edit `scripts\ed-afk-nightly.xml` — fix the `<Arguments>` and
   `<WorkingDirectory>` paths to match your install. The XML ships with
   `G:\Documents\GIT\ED-AFK\...` hard-coded; adjust if your repo lives
   elsewhere.
2. Register:
   ```pwsh
   Register-ScheduledTask `
       -Xml (Get-Content -Raw .\scripts\ed-afk-nightly.xml) `
       -TaskName "ED-AFK Nightly" `
       -User $env:USERNAME
   ```
3. Or via GUI: `taskschd.msc` -> Action -> Import Task -> pick the XML.

The trigger fires daily at 23:00. The 8-hour `ExecutionTimeLimit` is a
hard kill — the bot's own `-DurationHours` is the soft limit.

## Morning check — what the regression suite asserts

Per session, the suite checks:

| Check | What "fail" means |
|---|---|
| zero `HullDamage` events | ship took damage — scoop got too hot or hit the corona |
| no `EscapeOutcome` on D*/N/H/W* | danger-class filter let one through |
| fuel below 8t for ≤3 consecutive events | scoop trigger or scoop logic isn't keeping up |
| FSDJump count ≥ route legs | bot abandoned the planned route mid-flight |

These run automatically when you do `pytest` from the project root — no
extra flag needed. The suite skips cleanly on a fresh checkout because
no sessions exist yet.

## What's NOT yet calibrated (live-game work)

Phases 7–11 ship as framework with `@pytest.mark.requires_game` stubs.
They need eyes-on validation:

- FSS keyboard sweep (`perform_fss_keyboard_sweep`) — is the 30s tune
  duration enough? Does `FSSAllBodiesFound` fire within the 90s timeout?
- DSS 6-direction probe pattern (`DSS_NAIVE_DIRECTIONS`) — what's the
  efficiency hit on a real low-value body?
- Docking pre-flight — verify all 5 predicates fire correctly on real
  outposts vs starports vs in-SRV scenarios.
- Headless launcher — does `min-ed-launcher /autorun /autoquit` actually
  produce a clean game exit in this version?

Each of these has a one-line `@requires_game` test that gets skipped by
default. Promote them to real assertions as you validate.

## Live-fix loop (when bot crashes overnight)

1. Open the most recent `session_*.jsonl`.
2. Tail it (`Get-Content -Tail 50 session_*.jsonl`) to see the last
   recorded events before the crash.
3. Cross-reference with the runner `.log` for the Python stack trace.
4. Reproduce offline with `python -m ed_autojump.cli replay
   <path-to-journal> --record /tmp/reproducer.jsonl`.
5. Anonymize for sharing: `python -m ed_autojump.anonymizer
   reproducer.jsonl reproducer_anon.jsonl`.
6. Commit `reproducer_anon.jsonl` as a fixture under
   `tests/fixtures/journals/` if the failure is reproducible.
