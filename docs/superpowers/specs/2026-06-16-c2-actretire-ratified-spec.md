REVIEWED-SPEC â€” Retire the 11 inert C-series act() bodies (Stage-0, design review)

PROBLEM
scenes.py (projects/ed-core/src/ed_core/boot/scenes.py) carries an `act: Callable[..., None]` field on `SceneTemplate` plus a module helper `_act_pending` that every one of the 11 templates points at; `_act_pending` raises `NotImplementedError("[Phase-2 CV/action pending]")`. Determination is now LIVE via a SEPARATE map: ed_autojump/flow/boot_routes.py `classify_startup` -> `scene_for(ctx)` -> reads `tmpl.state` -> `_STATE_TO_PROC[tmpl.state]`. The `act` callables are therefore (a) dead code â€” no caller invokes them â€” and (b) a fork-flight-logic trap inviting a second, competing action path that diverges from `_STATE_TO_PROC`. Council C (docs/superpowers/specs/2026-06-16-forward-councils-outcomes.md) ratified retiring them.

GROUNDED CONSUMER FACTS (verified by grep over the whole repo)
- The `act` symbol is consumed in exactly TWO files: scenes.py (definition + 11 template kwargs + docstring) and projects/ed-core/scripts/verify_cseries_boot.py:114-117 (the `C_SERIES_SCENES[0].act()` raises-check). NO domain package, test, or other script reads `.act`, `action_sketch`, or `_act_pending`.
- Live wiring boot_routes.py touches the template ONLY via `tmpl.state` (lines 272, 276, 279, 282). `scene_for`/`scene_by_state` return the template; neither calls `.act`. So removing `act` is byte-for-byte invisible to classify_startup.
- primitives.py:12 docstring says "(see scenes.act())" â€” a dangling reference once `act` is gone. Cosmetic, in-scope to update to "(see scenes.SceneTemplate.proc / the live _STATE_TO_PROC map)". NOT load-bearing; flag if reviewer disputes scope.
- The DAG gate (docs/superpowers/specs/reorg_artifacts/whole_tree_import_check.py) is a STATIC AST import-edge walk over projects/*/src â€” it never imports/executes scenes.py. INV2 (stdlib + ed_core.boot.primitives only) is preserved as long as no NEW cross-package import line is added. The retire REMOVES the need for `Callable` in the field type but `Callable` is still imported for the `determine` field; the `typing` import line is unchanged either way.

THE NEW SceneTemplate SHAPE (scenes.py)
Replace the `act` field with an inert data field `proc: Optional[str]` naming the live procedure each scene maps to (or None for idle/fallback/no-standalone-proc). `proc` is PURE DATA â€” a str or None â€” never a callable, never invoked by scenes.py.

  @dataclass(frozen=True)
  class SceneTemplate:
      state: CSeriesState
      determine: Callable[[DetermineContext], Optional[bool]]
      proc: Optional[str]          # live procedure name (mirrors _STATE_TO_PROC); None = idle/fallback/no-standalone-proc
      fail_closed: str
      gate: str = ""
      # action_sketch REMOVED (it was the Phase-2 marker string; obsolete)

Per-template `proc` values (mirror boot_routes._STATE_TO_PROC EXACTLY â€” this is the binding contract; any divergence is a spec-conformance fail):
  STARTUP      -> proc="startup"          (_STATE_TO_PROC: ("run","startup"))
  STARSMACK    -> proc="smack_recovery"   (("run","smack_recovery"); live path AND-gates fsd_cooldown â€” that guard stays in boot_routes, NOT here)
  ARRIVAL      -> proc="arrival"          (("run","arrival"); live path gates on ArrivalLatch.consume â€” stays in boot_routes)
  DOCKED       -> proc=None               (("idle",None)) â€” comment: idle, no live proc
  PARKED       -> proc=None               (("idle","ParkedIdleNormalSpace")) â€” comment: idle; side-effect label lives in boot_routes._idle_side_effect, NOT mirrored here
  NO_ROUTE     -> proc=None               (("idle","NoRouteOnStartup")) â€” comment: idle; side-effect label lives in boot_routes
  TRAVERSAL    -> proc=None               (("fallback",None)) â€” comment: routes to legacy classifier; no standalone proc
  REFUEL       -> proc=None               (("fallback",None)) â€” comment: routes to legacy classifier; no standalone proc
  EXPLORATION  -> proc=None               (("fallback",None)) â€” comment: OPEN follow-on â€” honk/FSS/body-tour has NO standalone live procedure yet (Council C bucket B/C); proc=None until built
  PAUSE        -> proc=None               (("fallback",None)) â€” comment: routes to legacy classifier; no standalone proc
  RESUME       -> proc=None               (("fallback",None)) â€” comment: OPEN follow-on â€” re-derive has NO standalone live procedure; proc=None until built

DESIGN NOTE on what proc DOES and does NOT encode:
  proc carries the "run" procedure NAME for the three unambiguous run states (STARTUP/STARSMACK/ARRIVAL) and None for everything else. It deliberately does NOT distinguish idle-vs-fallback, and does NOT carry the idle side-effect label or the per-state run guards â€” those are POLICY that lives in boot_routes (_STATE_TO_PROC tuple kind, _idle_side_effect, ARRIVAL latch gate, STARSMACK cooldown gate). scenes.py stays determination + inert metadata ONLY; replicating the kind/guard/label here would re-create the fork-logic trap the retire is meant to kill. proc is a documentation/diagnostic mirror of the RUN target, not a second dispatch table. (If a reviewer argues proc should encode idle-vs-fallback-vs-run, that is OPEN QUESTION 1 â€” do not guess; the task says "naming the live procedure each scene maps to", which the run-name-or-None reading satisfies.)

EXACT EDITS (file:symbol)

A) scenes.py
  A1. SceneTemplate (dataclass, ~line 101-110): remove field `act: Callable[..., None]`; add field `proc: Optional[str]` (positioned after `determine`, before `fail_closed`); remove field `action_sketch: str = ""`. Keep `state`, `determine`, `fail_closed`, `gate`.
  A2. Module helper `_act_pending` (~line 113-115): DELETE the function entirely.
  A3. Constant `_PHASE2` (~line 32): DELETE (its only uses were `_act_pending` and the `action_sketch` f-strings, both removed). Confirm no other reference (grep: only scenes.py uses it).
  A4. C_SERIES_SCENES tuple (~line 241-330): in each of the 11 SceneTemplate(...) calls, REMOVE the `act=_act_pending,` kwarg and the `action_sketch=...` kwarg; ADD `proc=<value per table above>,`. Preserve `state`, `determine`, `gate`, `fail_closed` byte-for-byte. EXPLORATION and RESUME templates get an inline comment naming the OPEN follow-on (no standalone live proc).
  A5. Imports (~line 24): `Callable` is STILL needed (the `determine` field type). Keep the `typing` import line as-is. Do NOT add or remove any import that would change a cross-package edge (INV2/DAG).
  A6. Module docstring (~line 5-11): update the "act(...)  : ALWAYS raises NotImplementedError" bullet to describe the new inert `proc: str | None` field instead. Update the assertion comment region if it referenced act (it does not â€” the two import-time asserts are count==11 and one-per-state; both stay, byte-for-byte).
  A7. scene_for docstring (~line 352): the line "NOTE: this is determination ONLY. The caller does not act() here â€” act() is Phase-2 and raises NotImplementedError." â€” update to "determination ONLY; the caller maps tmpl.state via the live _STATE_TO_PROC. No action body exists here." (cosmetic; remove the dangling act() reference).
  A8. __all__ (~line 369): unchanged â€” it exports `SceneTemplate` (the class, now with proc), never `act`/`_act_pending`/`_PHASE2`. Verify no removed symbol is listed (none is).

B) primitives.py (cosmetic, in-scope to avoid a dangling reference)
  B1. Docstring line 12 "(see scenes.act())" -> "(see scenes.SceneTemplate.proc / the live _STATE_TO_PROC map)". No code change. If reviewer scopes this out, it is a harmless stale comment â€” flag, do not block.

C) verify_cseries_boot.py
  C1. Lines 112-117 (the `_raised`/`C_SERIES_SCENES[0].act()` NotImplementedError block): DELETE the try/except and its `check(...)`. REPLACE with a proc-field-present check asserting the new shape:
     - every template has a `proc` attribute that is `str | None` (no callable);
     - NO template exposes an `act` attribute (hasattr(tmpl,"act") is False) â€” proves the trap is gone;
     - the three run-states carry the exact proc strings: STARTUP->"startup", STARSMACK->"smack_recovery", ARRIVAL->"arrival" (cross-checked against the live _STATE_TO_PROC values, read INDEPENDENTLY or asserted as literals â€” see acceptance tests);
     - DOCKED/PARKED/NO_ROUTE/TRAVERSAL/REFUEL/EXPLORATION/PAUSE/RESUME carry proc is None.
  C2. Module docstring (~line 4-7): update the "11 scene templates" line if it described the act()-raises check; reflect the proc-field check instead.
  C3. Everything else in verify_cseries_boot.py (PINs 1-7 primitive checks, FIX1/FIX2 scene routing checks lines 124-140) stays UNTOUCHED â€” those exercise determination, which this change does not alter.

OUT OF SCOPE (do NOT touch â€” INV3): boot_routes.py (_STATE_TO_PROC stays the authoritative live map; this retire does NOT move policy into scenes.py), dispatcher.py, any procedure .toml, any CV module. If any reviewer asks to wire proc into classify_startup, REFUSE â€” that is the fork-logic trap, and it would change live behavior (violating "live wiring keeps working byte-for-byte").


## INTERFACE

PUBLIC SURFACE AFTER RETIRE (scenes.py)

@dataclass(frozen=True)
class SceneTemplate:
    state: CSeriesState
    determine: Callable[[DetermineContext], Optional[bool]]
    proc: Optional[str]          # NEW: live procedure name or None; inert data, never invoked
    fail_closed: str
    gate: str = ""
    # REMOVED: act: Callable[..., None]
    # REMOVED: action_sketch: str = ""

# REMOVED module-level: def _act_pending(*_args, **_kwargs) -> None  (raised NotImplementedError)
# REMOVED module-level: _PHASE2 = "[Phase-2 CV/action pending]"

C_SERIES_SCENES: tuple[SceneTemplate, ...]   # 11 entries, priority-ordered, each now proc=<str|None>
def scene_for(ctx: DetermineContext) -> Optional[SceneTemplate]   # UNCHANGED behavior
def scene_by_state(state: CSeriesState) -> Optional[SceneTemplate] # UNCHANGED behavior

__all__ unchanged: ["CSeriesState","DetermineContext","SceneTemplate","C_SERIES_SCENES","scene_for","scene_by_state"]

proc -> _STATE_TO_PROC correspondence (the binding mirror):
  scenes.SceneTemplate.proc == "startup"        WHERE _STATE_TO_PROC[STARTUP]   == ("run","startup")
  scenes.SceneTemplate.proc == "smack_recovery" WHERE _STATE_TO_PROC[STARSMACK] == ("run","smack_recovery")
  scenes.SceneTemplate.proc == "arrival"        WHERE _STATE_TO_PROC[ARRIVAL]   == ("run","arrival")
  scenes.SceneTemplate.proc is None             WHERE _STATE_TO_PROC[state] kind in {"idle","fallback"}
        (DOCKED, PARKED, NO_ROUTE, TRAVERSAL, REFUEL, EXPLORATION, PAUSE, RESUME)

LIVE WIRING CONTRACT (unchanged, byte-for-byte): boot_routes.classify_startup reads tmpl.state and indexes _STATE_TO_PROC; it does NOT read tmpl.proc or tmpl.act. The retire does not add a proc read to the live path.



## INVARIANTS

### [1]
INV1 (no-ship): scenes.py touches no ship â€” no key press, no pydirectinput, no procedure run, no Status/journal write. After the retire the module is determination + inert metadata only. (Strengthened: the NotImplementedError trap that could have been mis-wired into a ship action is gone.)

### [2]
INV2 (layering/DAG): scenes.py imports ONLY stdlib (__future__, enum, dataclasses, typing) + ed_core.boot.primitives. No new import added; no cross-package edge introduced. The whole_tree_import_check.py AST walk must stay PASS (zero violations).

### [3]
INV3 (no register_*, no live-path change): scenes.py makes no register_classifier_rule / register_event_route call and imports no domain. The retire does NOT move _STATE_TO_PROC policy, the ARRIVAL latch gate, the STARSMACK cooldown gate, or idle side-effect labels into scenes.py â€” those stay in boot_routes.py.

### [4]
INV4 (live wiring byte-for-byte): classify_startup -> scene_for -> tmpl.state -> _STATE_TO_PROC produces identical routing for every input before and after the retire, because it never reads .act or .proc. No live behavior changes.

### [5]
INV5 (determination untouched): all 11 determine predicates, scene_for priority order, scene_by_state, the two import-time asserts (count==11, one-per-state), CSeriesState, and DetermineContext are byte-for-byte unchanged.

### [6]
INV6 (no callable proc): SceneTemplate.proc is typed Optional[str] and every value is a str or None â€” never a callable, never invoked. No template retains an `act` attribute (hasattr -> False).

### [7]
INV7 (honest abstention preserved): a None determine() verdict (CV-pending) still means 'not routed here'; scene_for still treats only `is True` as a match. The retire does not alter the None semantics.

### [8]
INV8 (proc mirrors the live map): for the three run-states proc equals the live _STATE_TO_PROC run-target string; for all idle/fallback states proc is None. No proc value contradicts the live map (a spec-conformance fail if it does).

### [9]
INV9 (no dangling references): after the retire, NO file references _act_pending, _PHASE2, action_sketch, or .act on a SceneTemplate (verify_cseries_boot updated; primitives.py docstring updated).



## ACCEPTANCE_CRITERIA

### [1]
AC1: SceneTemplate has a `proc: Optional[str]` field and has NO `act` field and NO `action_sketch` field. The `_act_pending` function and `_PHASE2` constant are deleted from scenes.py.

### [2]
AC2: Each of the 11 templates carries the exact proc value from the mapping table: STARTUP='startup', STARSMACK='smack_recovery', ARRIVAL='arrival', and DOCKED/PARKED/NO_ROUTE/TRAVERSAL/REFUEL/EXPLORATION/PAUSE/RESUME=None. proc matches boot_routes._STATE_TO_PROC for all 11 (run-name for run states, None otherwise).

### [3]
AC3: No `act` callable remains anywhere â€” scenes.py defines none, the 11 templates pass none, and grep finds no `.act(` / `_act_pending` / `action_sketch` / `_PHASE2` outside an unrelated context (the diag_widget_frame.py local var `act` and the reader.py 'don't act' prose are NOT C-series and are untouched).

### [4]
AC4 (INV2/DAG): scenes.py import set is unchanged (stdlib + ed_core.boot.primitives); whole_tree_import_check.py over projects/*/src returns RESULT: PASS with zero violations.

### [5]
AC5 (INV4 live wiring): classify_startup routing is byte-for-byte identical â€” for STARTUP/STARSMACK/ARRIVAL the run target is unchanged, for idle states the side-effect+None is unchanged, for fallback states the legacy classifier is still reached. boot_routes.py is NOT edited.

### [6]
AC6 (INV5 determination): the two import-time asserts still hold (exactly 11 templates, one per CSeriesState); scene_for/scene_by_state behavior is unchanged; the determination predicates are untouched.

### [7]
AC7: verify_cseries_boot.py replaces the act()-raises-NotImplementedError check with a proc-field-present check (proc is str|None; no `act` attr; the three run-state proc strings correct; the eight non-run states proc is None) and its other checks (PINs 1-7, FIX1/FIX2) are untouched and still pass.

### [8]
AC8: EXPLORATION (proc=None) and RESUME (proc=None) each carry an inline comment marking the OPEN follow-on (no standalone live procedure â€” honk/FSS/body-tour for EXPLORATION, re-derive for RESUME), so the absence of a proc is documented as intentional, not an omission.

### [9]
AC9 (INV9): primitives.py docstring no longer references scenes.act(); scenes.py docstrings (module + scene_for) no longer describe an act() that raises. No dangling reference to the removed symbols remains in any docstring or comment.

### [10]
AC10: The frozen-dataclass property holds â€” SceneTemplate stays @dataclass(frozen=True); proc is a plain immutable field; no mutability is introduced.



## ACCEPTANCE_TESTS

DESCRIPTIONS ONLY â€” what a verification WOULD assert. These extend/replace the standalone checks in verify_cseries_boot.py (NO pytest; the existing script's style). Do NOT write or run runnable tests at this stage.

T1 (proc field present, correct type) â€” for every t in C_SERIES_SCENES, assert hasattr(t,'proc') and (t.proc is None or isinstance(t.proc,str)). Asserts AC1/INV6.

T2 (no act attribute survives) â€” for every t in C_SERIES_SCENES, assert not hasattr(t,'act'); and assert the scenes module has no attribute '_act_pending' and no '_PHASE2'. Asserts AC1/AC3/INV6/INV9. (This is the DIRECT replacement for the deleted lines 112-117.)

T3 (run-state proc strings exact) â€” assert scene_by_state(STARTUP).proc=='startup', scene_by_state(STARSMACK).proc=='smack_recovery', scene_by_state(ARRIVAL).proc=='arrival'. Asserts AC2.

T4 (idle/fallback proc is None) â€” for each of DOCKED, PARKED, NO_ROUTE, TRAVERSAL, REFUEL, EXPLORATION, PAUSE, RESUME assert scene_by_state(state).proc is None. Asserts AC2/AC8.

T5 (proc mirrors the live map â€” cross-module conformance) â€” import _STATE_TO_PROC from ed_autojump.flow.boot_routes; for each state, if _STATE_TO_PROC[state][0]=='run' assert scene_by_state(state).proc == _STATE_TO_PROC[state][1], else assert scene_by_state(state).proc is None. This is the binding INV8 check â€” proves scenes.proc cannot silently drift from the live dispatch map. NOTE: this introduces a TEST-ONLY import of a domain package into the verify script; that is fine (the verify script is tooling, not ed_core source â€” the DAG rule governs package source, not scripts). If the reviewer wants the verify script to stay ed_core-only, the fallback is T3+T4 with HARDCODED literals plus a static-grep cross-check of _STATE_TO_PROC â€” see OPEN QUESTION 2.

T6 (determination unchanged regression) â€” re-run the EXISTING FIX1/FIX2/PIN5 scene-routing checks (verify_cseries_boot.py lines 124-140) verbatim and assert identical verdicts: smacked+cooldown-cleared+empty-route -> STARSMACK; smacked+route-present -> STARSMACK; non-smacked empty-route -> NO_ROUTE; SC+empty-route+exploration_mode -> EXPLORATION. Asserts AC6/INV5.

T7 (import-time invariants intact) â€” assert len(C_SERIES_SCENES)==11 and {t.state for t in C_SERIES_SCENES}==set(CSeriesState). (The module's own asserts already enforce this at import; T7 makes it an explicit check.) Asserts AC6.

T8 (DAG / layering) â€” run whole_tree_import_check.py against projects/ and assert RESULT: PASS, zero violations. Asserts AC4/INV2. (Static AST walk; does not execute scenes.py.)

T9 (frozen dataclass preserved) â€” attempt to set t.proc on a template and assert it raises FrozenInstanceError (or equivalent); assert SceneTemplate is still frozen. Asserts AC10.

T10 (no dangling docstring references) â€” static grep over projects/ed-core/src/ed_core/boot/ asserts zero matches for 'scenes.act(' , '_act_pending', '_PHASE2', 'action_sketch' (excluding the diag_widget_frame.py / reader.py false positives which are outside boot/). Asserts AC9/INV9.



## OPEN_QUESTIONS

### [1]
OQ1 (proc semantics â€” run-name-only vs full kind): the task says proc names 'the live procedure each scene maps to' and gives explicit values where idle/fallback = None. This spec reads proc as the RUN-target string or None, deliberately NOT encoding idle-vs-fallback-vs-run kind (that policy stays in _STATE_TO_PROC to avoid the fork-logic trap). If the operator/Council actually wants proc to carry the full tri-state kind, the field type and the 8 None values change. DO NOT guess â€” the provided mapping (idle/fallback=None) supports the run-name-or-None reading; flag if a reviewer disputes. STUB+TODO if escalated.

### [2]
OQ2 (verify script cross-module import): T5 (the strongest conformance test â€” proc mirrors _STATE_TO_PROC) requires verify_cseries_boot.py to import ed_autojump.flow.boot_routes, adding a domain dependency to an ed_core tooling script. Acceptable for a script (DAG governs package SOURCE, not scripts), but if the operator wants the verify script to stay ed_core-pure, fall back to hardcoded-literal T3/T4 + a static grep of _STATE_TO_PROC. Operator preference needed; default = T5 with the domain import unless told otherwise.

### [3]
OQ3 (EXPLORATION/RESUME follow-on â€” explicitly OPEN, not for this task): EXPLORATION (honk/FSS/body-tour) and RESUME (re-derive) have NO standalone live procedure. proc=None is the correct retire-time value, but the eventual procs are unbuilt (Council C bucket B/C: detect_orbiting/detect_align CV-blocked; exploration action design is a separate council). This is a noted follow-on, NOT to be invented here. Do NOT fabricate a proc name for either.

### [4]
OQ4 (primitives.py docstring scope): updating primitives.py:12 'see scenes.act()' is cosmetic-but-correct (avoids a dangling reference once act is gone). If the reviewer scopes the retire to scenes.py + verify script ONLY, the stale comment is harmless and can be deferred. Default = fix it in this change (one-line docstring edit, no code).

### [5]
OQ5 (STARSMACK proc vs the held-for-sign-off cooldown relaxation): proc='smack_recovery' mirrors the live ('run','smack_recovery') map. The live path still AND-gates fsd_cooldown (the relaxation to smacked-alone is held for operator sign-off per Council A / the outcomes doc). proc does NOT encode that guard (guard stays in boot_routes). Confirm the operator does not expect proc to reflect the pending relaxation â€” it should not; proc is the procedure name, not the gate.

