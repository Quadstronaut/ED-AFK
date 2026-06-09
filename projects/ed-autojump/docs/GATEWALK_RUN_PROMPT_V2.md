# Gate/Path Walk — Run Prompt V2 (self-contained; supersedes V1)

ROLE: You run the ED-AFK "Interactive 1-by-1 Gate/Path Walk" — systematically walk every
condition, gate, and code path of the auto-jump bot against the REAL running game, one
situation at a time, to surface every issue and gap. Operator (operator, cmdr "CmdrOne") drives
the ship and re-stages states; he is happy to fly, calibrate, and report. YOU are the driver
for everything he can't easily do: instrument, monitor, reproduce, read code, run the harness,
decide verdicts, write findings. He surfaces in-game state; you surface the truth about the
code's behaviour in it.

────────────────────────────────────────────────────────────────────────
## INTERACTION CONTRACT (read this first — it is why V2 exists)

Operator is **intensely latency-sensitive**. Sitting 5–15 min between steps while the foreground
churns is "physically painful." Cost is NOT a constraint; responsiveness is. Obey ALL of:

1. **Main session stays responsive.** Never block it on a long read. Offload thorough log
   analysis to **subagents — one per reading if needed.** He explicitly wants *every line of
   log analyzed* — do it in a subagent, not the main thread.
   - **NEVER haiku** (they screw up). **Sonnet** for text/log/journal analysis. **Opus or a
     vision-capable ≥Sonnet model** for anything involving vision/OCR/screenshots or complex
     reasoning. Relay the subagent's findings back (Operator can't see subagent output).
2. **Visible streaming window.** Launch the live trace in its OWN visible console window
   (`Start-Process powershell -ArgumentList '-NoExit','-Command', <venv python> <gatewalk.py> …`)
   so Operator watches the [STATE]/DISPATCH/[JOURNAL]/[DECISION] stream in real time and visually
   confirms what the bot sees. Keep ONE long-lived trace (duration 3600); don't relaunch per step.
3. **Screenshot the EXACT region you read.** Any time you read a CV/vision region (compass, nav
   panel OCR, widget ring, station target, HUD), capture THOSE EXACT configured coordinates
   (from `cfg.vision`, the same region the code reads) and send it to Operator (SendUserFile).
   Reason: TWICE the radar was mistaken for the compass. Show him exactly what you're looking at
   BEFORE concluding anything from it — prevent downstream bad-assumption cascades.
4. **Fast verdicts.** Reply verdict + next action in ~2 lines; lead with the next action so he
   never waits on you to think. Grep the session jsonl / signal lines for quick checks; use a
   subagent when thoroughness is required. Don't read 500-line heartbeat dumps in the main thread.
5. **Batch bookkeeping at section ends** — checklist marks, commits, adversarial passes — not
   after every micro-step.

────────────────────────────────────────────────────────────────────────
## READ FIRST (before anything)
1. docs/superpowers/plans/2026-06-08-future-test-gate-path-walk.md   (design + inviolable principle)
2. docs/GATEWALK_CHECKLIST.md            (the coverage matrix you mark; §1,§4,§7 partly done)
3. docs/GATEWALK_REFERENCE_LOGIC.md      (Operator's play-by-play = the DESIRED logic to diff against)
4. scripts/gatewalk.py                   (live harness: routing + step modes, keys OFF)
5. scripts/replay_driver.py              (offline journal → real dispatch)
Skim as needed: src/ed_autojump/flow/dispatcher.py (run_live, _maybe_startup, dispatch,
_is_route_complete, dispatch_route_complete, crash-park), flow/interpreter.py (step-crash→abort),
keys/sender.py (FAILSAFE=False), procedures/*.toml (startup, arrival, sc_resume, dock,
dock_resume, smack_recovery, route_complete_park, honk).

## INVIOLABLE PRINCIPLE: exact code extraction only
Every test drives the REAL STEP_REGISTRY function / real FlowRunner branch / real gate, fed real
game state. NEVER re-implement or synthesize a gate, state, or decision. If you can't test
something through the real code, say so — don't fake it.

## HARD RULES (project conventions — obey)
- **Never fabricate a game mechanic.** Explain odd telemetry FROM the telemetry (FuelLevel↑ =
  refuel; BodyType; Status flags; journal events). If the data doesn't explain it, say so or hand
  Operator a NUMBERED in-game test and WAIT. (Proof case 2026-06-09: a refuel was mislabelled a
  "secondary-star drop" — never again.)
- **Zero assumptions.** Verify in logs/code or ask. Quote the real trace/journal line as evidence;
  if you didn't see it, say so. Operator will check — do not fabricate.
- **No wall-clock timeout as a success/failure GATE.** Gates are journal events or Status.json
  flags only. (Maneuver *durations* like "pitch 4 s" are fine; success *gates* are not.) Flag any
  violation you find.
- **Keys OFF by default (NullSender)** so the bot never fights Operator. Keys ON (the real motor path,
  `ed-autojump run --engage-keys`) ONLY on Operator's explicit go, each time.
- **Adversarial confirmation:** for refutable verdicts, have an independent agent (≥sonnet, prompted
  to REFUTE) confirm — but SKIP it for irrefutable/directly-readable reads. Always relay findings.
- **Commit + push to master** freely at section checkpoints (small, clear, revertable commits).
  Default branch is master. PowerShell here-strings: NO embedded double-quotes (they split the
  native arg — use plain text in commit bodies).
- Output pipeline for anything not easily fixed: KNOWN ISSUE writeup → user HOWTO → (on Operator's
  explicit go) `gh issue create`. The issue set is the public roadmap.

────────────────────────────────────────────────────────────────────────
## TOOLS (keys OFF unless Operator green-lights keys ON)
From projects/ed-autojump (Windows 11 / PowerShell):
- Routing trace (which procedure the REAL dispatcher fires per state):
  `.venv\Scripts\python.exe scripts\gatewalk.py --mode routing --duration 3600 --heartbeat 30`
- Step trace (full procedures vs real frames; read-only vision; orient_* fail-close):
  `.venv\Scripts\python.exe scripts\gatewalk.py --mode step --duration 3600`
- Offline replay of a captured journal through real run_live/dispatch:
  `.venv\Scripts\python.exe scripts\replay_driver.py "<Journal.*.log>"`
- Keys-ON real flight (Operator's explicit go only): `ed-autojump run --record --engage-keys`
Run the visible trace via Start-Process (contract §2). Read the session jsonl
(`~/ed-afk-sessions/gatewalk_*.jsonl`) for clean structured DECISION/DISPATCH records (no
heartbeat noise); dispatch a ≥sonnet subagent for thorough journal/Status line-by-line analysis.

Output lines: [STATE]=every flag/condition the gates read (changes + heartbeat); [DECISION]=every
ctx.log/recorder outcome; DISPATCH>=the procedure _maybe_startup/dispatch chose;
[JOURNAL]/[STATUS]/[ROUTE]=game ground-truth.

SCOPE LIMIT (state honestly): keys-OFF traces audit ROUTING + GATE branches, NOT keys-ON
event-consumption TIMING. For true timing use the keys-ON run. replay_driver deterministically
reproduces dispatch DECISIONS from a real journal.

────────────────────────────────────────────────────────────────────────
## TEST ORDER (Operator's spec, 2026-06-09)
1. **STATION FIRST** (he is ~20.3 km from **Tortooga**, drifting forward ~20 m/s — cause unknown;
   explain the drift FROM telemetry, don't guess). Demonstrate, observing flags at each step:
   approach to **≤7.49 km** → **request docking** → **dock** → observe flags incl. possible
   **docking-queue wait** → **undock (AUTO LAUNCH)** → done. (Diff against REFERENCE_LOGIC
   `# docking` / `# undocking`; the "it never targets the station" known issue lives here.)
2. **Within-system** next (SC-assist around star, refuel, exploration/body_tour, target-next,
   compass front/behind → orient).
3. **Jumping systems** (full hop; FSDJump→arrival regression row §2; witchspace resume).
4. **Then any order Operator requests.**
5. **LEAVE OFF planetary landing today** (Operator's call — no patience to teach it).

────────────────────────────────────────────────────────────────────────
## CONTEXT — the motivating bug is ALREADY ROOT-CAUSED + FIXED (commit 9d2a99b, 2026-06-09)
A fresh launch ran `startup`, jumped to "Dryio Eaec NE-Y b34-0", then NO procedure ran and the
overlay froze on "STARTUP > HOLD_ALIGNMENT". Two councils + replay_driver proved the dispatch
TRIGGER is sound (FSDJump→arrival fired 10/10). The real failure was a PROCESS CRASH on an
unhandled pydirectinput.FailSafeException (cursor in a screen corner). Fix shipped:
`FAILSAFE=False` (sender.py:261) + interpreter step-crash→`StepCrashed`/fail-closed abort
(interpreter.py:110) + run_live `[CRASH-PARKED]` park instead of dying (dispatcher.py:1213).
Walk task: verify the fix HOLDS live (force a step error → StepCrashed + abort/park, process STAYS
ALIVE) and hunt other crash/freeze classes.

## FINDINGS SO FAR THIS SESSION (already verified — see checklist)
- §1 r4 ✅: SC + dest=local-star + route → `arrival` via `_maybe_startup` Priority 2 (`local_star`);
  P2 outranks the stale-loiter `sc_resume` (P4) even when jump_age>30s.
- §1 r8 ✅(+⚠️): docked-on-load → returns at docked guard (no dispatch); `_smacked` correctly
  ignored (docked guard precedes smack guard). ⚠️ silent — no record/overlay (candidate issue).
- §4 r4 ⚠️: `dispatch` has NO `Undocked` branch — manual undock dispatches nothing; real resume =
  NavRoute-while-docked → `dock_resume` (row 3, still UNTESTED). Bot loaded-while-docked stays inert.
- §1 r1 / §2 r1 ✅: normal-space + route plotted → `startup`.

## ISSUE TEMPLATE
**Title:** [KNOWN ISSUE] <behaviour> · **State:** <system/dist/SC-normal/route/GuiFocus> ·
**Expected:** <gate should do> · **Actual:** <branch taken + journal/Status evidence> ·
**Root cause:** <file:line, gate> · **Workaround:** <HOWTO> ·
**Fix difficulty:** easy-code | hard-code | needs-mechanic-confirmation · **Tier:** blocker|next|someday

## ENVIRONMENT
Repo: <repo-root>\ED-AFK   Project: projects/ed-autojump
Python: projects/ed-autojump/.venv\Scripts\python.exe (run from projects/ed-autojump)
Journal dir: C:\Users\<user>\Saved Games\Frontier Developments\Elite Dangerous
OS: Windows 11, PowerShell ($null not /dev/null; backtick line-continuation). Launcher: launch.ps1 -Yes.
Pre-existing test baseline: 13 known failures (route_complete_park / smack_recovery /
nav_panel_bounded step-order asserts) — NOT regressions.

START: read the files above, launch the visible long-lived routing trace (contract §2), tell Operator
you're ready, and begin the STATION test at Tortooga (test order #1). Keep the main thread fast:
grep/subagent for reads, screenshot every CV region, ≤2-line replies.
