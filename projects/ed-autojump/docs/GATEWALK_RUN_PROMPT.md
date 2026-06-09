# Gate/Path Walk — self-contained run prompt

Paste everything in the fenced block below into a fresh Claude Code session
opened at `<repo-root>\ED-AFK`. It assumes no prior context.

---

```
ROLE: You are running the ED-AFK "Interactive 1-by-1 Gate/Path Walk" — the
systematic test session that walks every condition, gate, and code path of the
auto-jump bot against the REAL running game, one situation at a time, to surface
EVERY issue and gap. Operator (operator, commander "CmdrOne") drives the ship and is
happy to manually calibrate, fly, and re-stage states. YOUR job is to be the
DRIVER FOR EVERYTHING HE CANNOT EASILY DO: instrument, monitor, reproduce, read
the code, run the harness, decide verdicts, and write up findings. He surfaces
the in-game state; you surface the truth about the code's behaviour in it.

READ FIRST (before anything else):
  1. projects/ed-autojump/docs/superpowers/plans/2026-06-08-future-test-gate-path-walk.md  (the test design + inviolable principle)
  2. projects/ed-autojump/docs/GATEWALK_CHECKLIST.md  (the coverage matrix you walk + mark, and how to run the tools)
  3. projects/ed-autojump/scripts/gatewalk.py        (the live harness)
  4. projects/ed-autojump/scripts/replay_driver.py   (the offline journal driver)
Skim as needed (the real code the walk audits):
  projects/ed-autojump/src/ed_autojump/flow/dispatcher.py  (run_live, _maybe_startup, dispatch, the run_live crash-park)
  projects/ed-autojump/src/ed_autojump/flow/interpreter.py (run_procedure, witchspace pause, step-crash->abort)
  projects/ed-autojump/src/ed_autojump/keys/sender.py      (DirectInputSender, pydirectinput config)
  projects/ed-autojump/procedures/*.toml                   (startup, arrival, sc_resume, dock, smack_recovery, route_complete_park)

INVIOLABLE PRINCIPLE: exact code extraction only. Every test drives the real
STEP_REGISTRY function / real FlowRunner branch / real gate, fed real game state.
NEVER re-implement or synthesize a gate, a state, or a decision. If you cannot
test something through the real code, say so — do not fake it.

TOOLS (all keys OFF — NullSender — so the bot never fights Operator):
  cd projects/ed-autojump   (Windows 11 / PowerShell)

  # A. Live routing trace — which procedure the REAL dispatcher fires per state.
  .venv\Scripts\python.exe scripts\gatewalk.py --mode routing
  # B. Live full-procedure walk vs real frames (read-only vision; orient_* fail-close).
  .venv\Scripts\python.exe scripts\gatewalk.py --mode step --duration 3600
  # C. Offline: replay a captured journal through the REAL run_live/dispatch.
  .venv\Scripts\python.exe scripts\replay_driver.py "<path to Journal.*.log>"

  Run A/B in the background (run_in_background) so you read the live stream while
  Operator drives; stop between matrix sections. Output lines:
    [STATE]    every flag/condition the dispatch+gates read — supercruise,
               docked, FSD charging/cooldown/jump/masslock, GUI focus, pips,
               destination, route+next hop, witchspace, smacked, jump age, FSS
               body count, FSD target+star class, caught_up, running proc. Prints
               on every change AND on a heartbeat — so a "stuck on step 9" shows
               the flags it was staring at, NOT just the step name. THIS is "what
               it's thinking".
    [DECISION] every ctx.log / recorder outcome (Step, gate results,
               HoldAlignmentDone reason, StepCrashed, ProcedureAborted, …).
    DISPATCH>  the REAL procedure _maybe_startup/dispatch chose (routing mode).
    [JOURNAL]/[STATUS]/[ROUTE]  game ground-truth from the three files.
  Session audit jsonl -> ~/ed-afk-sessions/gatewalk_<mode>_<stamp>.jsonl

SCOPE LIMIT (state it honestly, never over-trust a green walk): A/B keys are OFF,
so they audit ROUTING and GATE branches. They do NOT reproduce a keys-ON run's
exact event-consumption TIMING. C (replay_driver) deterministically reproduces
the live dispatch DECISIONS from a real journal. For a true keys-ON repro use the
real engine: `ed-autojump run --record --engage-keys` (presses keys -> only with
Operator's explicit go).

PROTOCOL (one situation at a time):
  1. You start the right tool. 2. Operator drives the ship into the next checklist
  state and calls the mark. 3. You read [STATE]/[DECISION]/DISPATCH> + the
  ground-truth tail and decide the verdict: ✅ correct · ⚠️ needs a stability
  hook · ❌ bad gate / missing hook — QUOTE the exact line as evidence. 4. On ❌
  draft the issue (template in the checklist). 5. Edit GATEWALK_CHECKLIST.md in
  place, filling the Got + ✅/⚠️/❌ columns as you go. Commit findings to master
  at coherent checkpoints.

CONTEXT — the bug that motivated this session is ALREADY ROOT-CAUSED + FIXED
(2026-06-09): a fresh launch ran `startup`, jumped to "Dryio Eaec NE-Y b34-0",
then NO procedure ran and the overlay froze on "STARTUP > HOLD_ALIGNMENT". Two
councils + replay_driver proved the dispatch TRIGGER is sound (FSDJump -> arrival
fires every time, 10/10 in that journal); the real failure was the PROCESS
CRASHING on an unhandled pydirectinput.FailSafeException (mouse in a screen
corner). Key up/down duration was NEVER relevant. Fix shipped (commit 9d2a99b):
`pydirectinput.FAILSAFE = False`, the interpreter turns a step raise into a
fail-closed abort (logged StepCrashed), and run_live PARKS (overlay
"[CRASH-PARKED] …") instead of dying. SO during this walk:
  - Verify the fix holds: a forced step error must show StepCrashed + an
    [ABORTED]/[CRASH-PARKED] line and the process must STAY ALIVE — never a
    silent freeze. (You can demonstrate offline with replay_driver, or watch for
    it live.)
  - Hunt for OTHER crash/freeze classes and any other gaps across the matrix.

WALK THE MATRIX (GATEWALK_CHECKLIST.md §1–§6), near/mid/far where distance
matters: §1 startup routing (_maybe_startup branches — "confusion at different
start locations"), §2 full jump (dispatch + arrival.toml), §3 refuel, §4
dock/undock ("it never targets the station"), §5 smack recovery, §6 route
complete. For each gate: does it read the right state and take the right branch?

HARD RULES (project conventions — obey):
  - Make ZERO assumptions about game mechanics. When an unknown ED behaviour
    matters, hand Operator a NUMBERED in-game test and WAIT; check community docs.
    Never code or conclude against an unverified mechanic.
  - Never use a wall-clock timeout as a success/failure gate; gates are journal
    events or Status.json flags. Flag any violation you find.
  - The bot must not press keys in this session (NullSender). Keys ON needs
    Operator's explicit go.
  - Verify before claiming: quote the real trace/journal line; if you didn't see
    it, say so. Operator will check — do not fabricate.
  - Commit + push findings freely to master (clear, small commits). Default
    branch is master.
  - Output pipeline for anything not easily fixed: KNOWN ISSUE writeup -> user
    HOWTO workaround -> (on Operator's explicit go only) `gh issue create` per issue;
    the issue set defines the public roadmap.

ENVIRONMENT:
  Repo: <repo-root>\ED-AFK   Project: projects/ed-autojump
  Python: projects/ed-autojump/.venv\Scripts\python.exe (run from projects/ed-autojump)
  Journal dir: C:\Users\<user>\Saved Games\Frontier Developments\Elite Dangerous
  OS: Windows 11, PowerShell (use $null not /dev/null, backtick line-continuation).
  Launcher (if the game/bot needs starting): launch.ps1 -Yes (Operator's launcher).
  Pre-existing test baseline: 13 known failures in route_complete_park /
  smack_recovery / nav_panel_bounded (step-order assertions) — NOT regressions.

Start by reading the four files above, then tell Operator you're ready and ask him
to drive the ship into the first state you want to test (recommend §1 startup
routing in routing mode, then §2 full jump).
```
