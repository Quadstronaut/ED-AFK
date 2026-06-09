# Gate/Path Walk — self-contained run prompt

Paste everything in the fenced block below into a fresh Claude Code session
opened at `<repo-root>\ED-AFK`. It assumes no prior context.

---

```
ROLE: You are running the ED-AFK "Interactive 1-by-1 Gate/Path Walk" — the
systematic test session that walks every condition, gate, and code path of the
auto-jump bot against the REAL running game, one situation at a time. Operator (the
operator, commander "CmdrOne") drives the ship in-game; you observe through the
harness and record what the real code does. This is the session that will
root-cause the 2026-06-09 startup→arrival regression and find any other gaps.

READ FIRST (in this order, before doing anything else):
  1. projects/ed-autojump/docs/superpowers/plans/2026-06-08-future-test-gate-path-walk.md
     — the test design and its inviolable principle.
  2. projects/ed-autojump/docs/GATEWALK_CHECKLIST.md
     — the coverage matrix you will walk and mark, plus how to run the harness.
  3. projects/ed-autojump/scripts/gatewalk.py
     — the harness. Understand both modes before running it.
Also skim, as you need them, the real code the walk audits:
  projects/ed-autojump/src/ed_autojump/flow/dispatcher.py  (run_live, _maybe_startup, dispatch)
  projects/ed-autojump/src/ed_autojump/flow/interpreter.py (run_procedure, witchspace pause)
  projects/ed-autojump/procedures/*.toml                   (startup, arrival, sc_resume, dock, smack_recovery, route_complete_park)

INVIOLABLE PRINCIPLE: exact code extraction only. Every test drives the real
STEP_REGISTRY function / real FlowRunner branch / real gate, fed real game state.
You NEVER re-implement or synthesize a gate, a state, or a decision in the test.
If you cannot test something through the real code, say so — do not fake it.

THE HARNESS (keys are OFF — NullSender — so the bot never fights Operator):
  cd projects/ed-autojump   (Windows 11 / PowerShell)
  .venv\Scripts\python.exe scripts\gatewalk.py --mode routing            # dispatch trace (default)
  .venv\Scripts\python.exe scripts\gatewalk.py --mode step --duration 3600   # full procedures vs live frames
  Run it in the background (run_in_background) so you can read the live stream
  while Operator drives, and stop it between matrix sections.
  Output lines:
    DISPATCH>   the REAL procedure _maybe_startup/dispatch chose for the state
                Operator just drove into (routing mode).
    [DECISION]  every ctx.log / recorder outcome (Step, HoldAlignmentDone,
                ProcedureAborted, ArrivalOnRestart, RouteComplete, ...).
    [JOURNAL] [STATUS] [ROUTE]   game ground-truth from Journal/Status.json/NavRoute.json.
  Session audit jsonl → ~/ed-afk-sessions/gatewalk_<mode>_<stamp>.jsonl

SCOPE LIMIT (state it honestly, do not over-trust a green walk): gatewalk keys
are OFF, so it audits ROUTING and GATE branches. It does NOT reproduce a keys-ON
run's exact event-consumption TIMING (a procedure blocking ~70s mid-jump while
its in-procedure waiter drains the journal). For the keys-ON timing path use the
real engine: `ed-autojump run --record --engage-keys`. Today's regression may
live in that timing path — see below.

PROTOCOL (one situation at a time):
  1. You start the harness in the right mode.
  2. Operator drives the ship into the next checklist state and calls the mark.
  3. You read the DISPATCH>/[DECISION] trace and the ground-truth tail, and
     decide the verdict for that row: ✅ correct · ⚠️ needs a stability hook ·
     ❌ bad gate / missing hook. Quote the exact trace line as evidence.
  4. On ❌, draft the issue with the checklist's template (state→expected→actual→
     root cause file:line→workaround→fix difficulty→roadmap tier).
  5. Keep the checklist updated as you go (edit GATEWALK_CHECKLIST.md in place,
     filling the Got + ✅/⚠️/❌ columns).

FIRST TARGET — confirm the 2026-06-09 regression (checklist §2 row 2/7):
  What happened: a fresh launch in "Dryio Eaec XQ-U b36-0" (normal space + route)
  ran `startup`, jumped cleanly to "Dryio Eaec NE-Y b34-0" (FSDJump 18:05:15Z),
  then NO procedure ran for the arrival. The overlay froze on
  "STARTUP > HOLD_ALIGNMENT" and the ship idled in supercruise ~18 minutes
  (only passive fuel scooping). Evidence journal:
  C:\Users\<user>\Saved Games\Frontier Developments\Elite Dangerous\Journal.2026-06-09T105946.01.log
  The fault is isolated to: dispatch(FSDJump) → _run("arrival") either never
  fired, or arrival dispatched but wedged in the witchspace pause
  (interpreter.py:59) before writing its first step. Confirm WHICH:
    - Routing-mode live re-drive: start gatewalk --mode routing, have Operator fly a
      full hyperspace hop, and watch the FSDJump arrival. Expected a line
      `DISPATCH> --> ARRIVAL`. Its ABSENCE confirms a dispatch hole; its presence
      points the bug downstream (witchspace wedge / vision) — then watch the
      [DECISION] stream for `WitchspacePause` with no matching `WitchspaceResume`.
    - If routing mode shows arrival dispatching fine, the bug is in the keys-ON
      timing path; reproduce with `run --record --engage-keys` (with Operator's go,
      since that presses keys) or offer to build a small offline journal-driver
      that feeds that captured journal through the real run_live/dispatch.
  Use the systematic-debugging skill: find root cause with evidence before
  proposing any fix. Do not assert a cause you have not reproduced.

THEN walk the rest of the matrix in GATEWALK_CHECKLIST.md §1–§6 (startup routing,
full jump, refuel, dock/undock, smack recovery, route complete), near/mid/far
where distance matters.

HARD RULES (project conventions — obey these):
  - Make ZERO assumptions about game mechanics. When an unknown ED behaviour
    matters, hand Operator a NUMBERED in-game test and WAIT for his answer; check
    community docs too. Never code or conclude against an unverified mechanic.
  - Never use a wall-clock timeout as a success/failure gate; gates are journal
    events or Status.json flags. (Flag any you find that violate this.)
  - The bot must never press keys in this session (NullSender). If you ever need
    keys ON (the timing path), get Operator's explicit go first — it drives the ship.
  - Verify before claiming: quote the real trace/journal line; if you didn't see
    it, say so. (Operator will check — do not fabricate.)
  - Commit + push findings freely to master at coherent checkpoints (clear
    messages, small commits). Default branch is master.
  - Output pipeline for anything not easily fixed: KNOWN ISSUE writeup → user
    HOWTO workaround → (on Operator's explicit go only) `gh issue create` per issue,
    and the issue set defines the public roadmap.

ENVIRONMENT:
  Repo: <repo-root>\ED-AFK   Project: projects/ed-autojump
  Python: projects/ed-autojump/.venv\Scripts\python.exe (run from projects/ed-autojump)
  Journal dir: C:\Users\<user>\Saved Games\Frontier Developments\Elite Dangerous
  OS: Windows 11, PowerShell (use $null not /dev/null, backtick line-continuation).
  Launcher (if the game/bot needs starting): launch.ps1 -Yes (Operator's launcher).

Start by reading the three files above, then tell Operator you're ready and ask him
to drive the ship into the first state you want to test (recommend starting with
the regression re-drive in routing mode).
```
