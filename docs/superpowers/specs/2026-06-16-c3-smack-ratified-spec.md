REVIEWED-SPEC (Stage-0 scaffold) â€” Smack determination + recovery redesign
Arbiter: Opus 4.8 (Fable 5 substitute). Evidence class for all game-truth below: OPERATOR-WITNESSED LIVE (docs/superpowers/specs/2026-06-16-obstruction-and-smack-game-truth.md) + journal-blindness confirmed (memory smack-journal-blind-vision-discriminator). DO NOT fabricate ED mechanics beyond what is cited; unknowns are OPEN QUESTIONS, not guesses.

=== 0. PROBLEM (what is wrong today, grounded in the read) ===
Three live code sites conflate a DELIBERATE drop with a SMACK and entirely miss PLANET-smack:
- BUG A â€” projects/ed-autojump/src/ed_autojump/flow/boot_routes.py : _route_sc_exit (lines 515-521). Fires runner._run("smack_recovery") on ANY SupercruiseExit whose body_type == "Star". This is WRONG two ways: (1) a deliberate star-drop (e.g. SC-assist drop at a scoopable arrival star, or any operator/flow-intended drop) is IDENTICAL in the journal to a star-smack, so this auto-fires recovery on benign drops; (2) it never handles body_type == "Planet" at all, so a real planet-smack is silently dropped on the floor.
- BUG B â€” projects/ed-core/src/ed_core/boot/scenes.py : _det_starsmack (lines 181-199). Returns ctx.smacked (a plain bool) so the C-series STARSMACK scene is entered on ANY star-drop telemetry. Its own docstring acknowledges telemetry cannot distinguish smack from deliberate drop and defers the discriminator to a Phase-2 CV that does not yet exist â€” meaning today it WILL route a benign drop into STARSMACK.
- BUG C â€” projects/ed-core/src/ed_core/flow/dispatcher.py (lines 596-625). runner._smacked is latched purely from body_type == "Star" (line 616) and _PREEMPT_ON_SMACK preemption keys on body_type == "Star" (line 596,604). So (1) the smacked latch is True for every star-drop incl. deliberate, and (2) planet-smacks neither set _smacked nor preempt a live arrival/dock/startup/sc_resume scene.
GAME-TRUTH (BINDING): a 'smack' = a FORCED drop from getting too close to a massive body (HUD 'Dropping - too close'). It is JOURNAL-BLIND: smack vs deliberate drop are the SAME SupercruiseExit event (body_type Star|Planet), no damage/cooldown event distinguishes them. The ONLY discriminator is VISION: an ESCAPE VECTOR shown on the HUD/compass => we were SMACKED; NO escape vector => a deliberate drop. The escape-vector COLOR encodes the body: BLUE = star-smack, PURPLE = planet-smack. Same recovery mechanics for both; only the vector color differs.

=== 1. DESIGN OVERVIEW (the contract this spec freezes) ===
Introduce a single CV discriminator and route ALL smack determination through it, FAIL-CLOSED. Until the CV is calibrated/wired it returns 'none'/abstains, which means: NEVER auto-fire smack_recovery on a bare SupercruiseExit. The existing flow (deliberate-drop / arrival continuation) proceeds unchanged when the CV is unwired or returns 'none'.

1a. NEW CV detector (STUBBED â€” do NOT invent pixel logic; needs operator calibration frames):
  Module: projects/ed-vision/src/ed_vision/escape_vector.py  (NEW)
  Pure function over a full-frame BGR ndarray, mirroring station_menu.detect_menu_item:
    Result tokens (plain strings, dependency-free for callers):
      NONE   = "none"     # no escape vector visible  => NOT smacked (deliberate drop)
      BLUE   = "blue"     # blue escape vector visible => STAR-smack
      PURPLE = "purple"   # purple escape vector visible => PLANET-smack
    def detect_escape_vector(frame: Any) -> str:
        '''Return 'none' | 'blue' | 'purple'. Pure over a full-frame BGR ndarray
        (opencv channel order, as GdiGrabber.grab returns). STUB: returns NONE
        unconditionally and logs nothing until calibrated. The escape-vector
        glyph + its color->body mapping (BLUE=star, PURPLE=planet) is operator-
        game-truth; the PIXEL detection (region, color thresholds, glyph shape)
        is NOT YET KNOWN and MUST NOT be guessed.
        TODO(calibration frames, operator-provided):
          - blue star-smack escape-vector frame
          - purple planet-smack escape-vector frame
          - a deliberate-drop NO-vector frame (negative case)
        Until those land + thresholds are measured against them, this returns NONE
        (fail-closed: 'we do not see a smack').'''
  Rationale for the stub returning NONE (not raising / not guessing): a NONE return is the fail-closed identity for the whole determination â€” an un-calibrated detector must never manufacture a smack. This preserves ship-safety: the bot keeps doing what it does today (treat a bare drop as benign) until the operator calibrates the CV. NONE is also what a genuine deliberate drop returns, so the stub and the real negative case are indistinguishable to callers â€” correct by construction.
  Optional (parallel to station_menu.region_rect): region_rect(frame_height) for the CV debug overlay, also stubbed/TODO until the region is known.

1b. NEW determination predicate (pure, testable, lives where the discriminator can be reused by BOTH _route_sc_exit and the STARSMACK scene). Proposed location: projects/ed-core/src/ed_core/boot/primitives.py (rank-1, no domain import) OR co-located in escape_vector.py if a frame-grabber is injected at the call site. Signature:
    def classify_smack(vector_token: str) -> Optional[str]:
        '''Map an escape-vector CV token to a recovery route, or None to ABSTAIN.
        'blue'   -> 'star'      (blue escape vector => star-smack)
        'purple' -> 'planet'    (purple escape vector => planet-smack)
        'none'   -> None        (no vector => deliberate drop, NOT a smack)
        anything else -> None   (unknown token => fail-closed abstain)'''
  This is the ONLY place the color->body mapping is encoded. It is total over the three tokens and abstains on everything else.

1c. FIX BUG A â€” _route_sc_exit (boot_routes.py:515). New behavior:
  - Guard early-out unchanged in spirit but WIDEN the body filter: act on body_type in {"Star","Planet"} (a Station drop is never a smack â€” keep returning None for it).
  - Acquire a frame via an injected grabber and call detect_escape_vector. The grabber MUST be the same Optional[Callable[[], Any]] pattern already used by ctx.frame_grabber / station_menu_grabber: a runner-level escape-vector frame-grabber attribute (e.g. runner._escape_vector_grabber, default None / unwired).
  - If the grabber is None (CV UNWIRED) -> ABSTAIN: record an observability event (e.g. SmackDeterminationAbstained {reason:"cv_unwired", body_type}) and return None. DO NOT fire smack_recovery. (This is the central ship-safety guarantee and the acceptance gate.)
  - If the grabber yields a frame -> token = detect_escape_vector(frame); route = classify_smack(token).
      route is None ('none' token)  -> record SmackDeterminationNegative {body_type, token:"none"}; return None (deliberate drop, flow continues â€” NO recovery).
      route == "star"   -> record StarSmackConfirmed; runner._run("smack_recovery") with the body kind threaded through (see 1f); return "smack_recovery".
      route == "planet" -> record PlanetSmackConfirmed; runner._run("smack_recovery") with body kind=planet; return "smack_recovery".
  - Set runner._event_times["drop"] only on a CONFIRMED smack (parity-preserving), not on every drop. (OPEN QUESTION 4: confirm whether other consumers depend on the drop time being set for benign drops.)

1d. FIX BUG B â€” _det_starsmack (scenes.py:181). The C-series STARSMACK scene must NOT enter on bare ctx.smacked. Two acceptable shapes; pick ONE in Stage-1 and pin it:
  Shape (i) PURE-TELEMETRY ABSTAIN: _det_starsmack returns None (CV-PENDING) whenever it cannot confirm a smack from a CV-derived fact present in DetermineContext, and True only when a CV-confirmed smack token is present. This requires threading a CV-derived smack token into DetermineContext (a NEW optional field, e.g. smack_kind: Optional[str] = None, where None=unknown/abstain, "star"/"planet"=confirmed). _det_starsmack then returns (ctx.smack_kind in {"star","planet"}) or None when ctx.smack_kind is None AND ctx.smacked (i.e. a drop happened but CV has not confirmed â€” honest abstention, per INV7). When ctx.smack_kind is None and not ctx.smacked, return False.
  Shape (ii) keep _det_starsmack telemetry-gated but make the ACT fail-closed: leave determination as-is BUT the STARSMACK->smack_recovery dispatch path in boot_routes._STATE_TO_PROC / classify_startup gains a CV confirmation guard identical to 1c (grabber None or token 'none' -> fall back to the legacy non-smack path, NOT smack_recovery).
  REQUIRED INVARIANT regardless of shape: a bare SupercruiseExit (no CV escape-vector evidence) MUST NOT cause smack_recovery to run, on the boot/classify path OR the event-route path. The arbiter's pin: Shape (i) is preferred (keeps determination honest and abstaining per the module's stated INV7) UNLESS Stage-1 shows DetermineContext cannot be populated with a CV fact at classify time (no frame-grabber available in build_determine_context) â€” in which case Shape (ii) is the fallback and MUST be logged as a dissent-resolved-by-constraint ledger entry.
  Either shape: the scene's gate string + action_sketch + fail_closed must be updated to name the escape-vector CV and the blue/purple color truth, and the determination must NEVER return True on telemetry alone.

1e. FIX BUG C â€” dispatcher.py _record_event_time (596-625). The _smacked latch and the _PREEMPT_ON_SMACK preemption are the LIVE mid-scene smack interrupt. Today both ignore planets and treat every star-drop as a smack.
  - PREEMPTION (lines 596-609): must preempt a live arrival/startup/dock/sc_resume scene on a REAL smack of EITHER body. But the preempt fires from _record_event_time which has no frame at hand. ACCEPTABLE design (pin in Stage-1): keep the preempt CONSERVATIVE-WIDE (preempt the live scene on SupercruiseExit body_type in {"Star","Planet"}) BECAUSE preemption only ABORTS the current scene at its next poll; the subsequent re-dispatch goes through the CV-gated _route_sc_exit (1c) which is the fail-closed decision point. i.e. a benign drop may abort a fly-at-star arrival scene, but it will NOT trigger smack_recovery â€” re-dispatch will fall through to arrival/flow continuation. This is the SAME cost as today for stars and ADDS planet coverage. EXPLICITLY DOCUMENT this two-stage design (wide preempt + narrow CV-gated recovery) so a reviewer does not mistake the wide preempt for the old bug. (OPEN QUESTION 5: is aborting a live deliberate-drop arrival scene on a planet drop acceptable, or does it disrupt a planet-approach flow? Needs operator confirmation.)
  - _smacked LATCH (lines 615-625): rename/retain semantics so it means "last SC transition was a drop at a massive body" (Star OR Planet) for restart-routing, but DOWNSTREAM consumers (the STARSMACK scene + smack_recovery dispatch) MUST re-confirm via CV before recovering. Do NOT widen _smacked to auto-recover. Keep the existing FSDJump/SupercruiseEntry/Docked-Location clears (617-625) unchanged. (OPEN QUESTION 6: on a restart-while-smacked there may be NO live frame yet / the escape vector may have already cleared on screen â€” can the escape vector be re-read on a cold restart, or is restart-recovery fundamentally CV-blind and must abstain? This is a real ship-safety gap; flag, do not guess.)

1f. RECOVERY ROUTING (smack_recovery.toml + steps). Game-truth: SAME recovery mechanics for star and planet, ONLY the escape-vector color differs (blue vs purple). Therefore the recovery PROCEDURE body is shared; the body kind ("star"|"planet") is threaded only so any CV step inside recovery (orient-to-vector) knows WHICH color to look for.
  - smack_recovery.toml: the procedure's existing escape-vector dance (target_ahead -> engage_supercruise until_charging -> orient_compass to the spawned vector -> hold_alignment to SupercruiseEntry, steps 4.5-5.6) is the shared mechanic and is RETAINED. The only smack-kind-dependent point is the color the orient-to-vector CV expects. If orient_compass is the step that centers the escape vector, it needs a color parameter (default to today's behavior; pass "blue"/"purple" from the determined kind). If that color-aware CV does not yet exist, STUB the parameter + TODO and keep orient_compass color-agnostic (it must still pass for star, the only case exercised today). DO NOT block this spec on a color-aware orient CV â€” it is a downstream refinement.
  - steps.py: there is NO step_smack_recovery; "smack_recovery" is a PROCEDURE (procedures/smack_recovery.toml) run via runner._run. So the "fix to steps.py smack_recovery" in the task is interpreted as: (a) thread the body kind into the procedure run (runner._run signature / a runner attribute the procedure's StepContext can read), and (b) if any NEW step is needed (e.g. step_confirm_escape_vector that re-reads the vector and fails closed), add it here following the Optional-grabber-None-> no-op-pass convention used by every other CV step (e.g. step_nav_panel_target's compass_reader/frame_grabber None branch, step_auto_launch's station_menu_grabber None branch). The arbiter does NOT mandate a new step unless Stage-1 finds the determination cannot be fully made before dispatch.

=== 2. STATE / DECISION TABLE (the fail-closed truth table) ===
Inputs: body_type of the SupercruiseExit; escape-vector CV token; CV-grabber wired?
| body_type        | grabber  | CV token  | DECISION                        | smack_recovery? | recovery kind |
|------------------|----------|-----------|---------------------------------|-----------------|---------------|
| Station          | any      | n/a       | not a smack (early return)      | NO              | -             |
| Star or Planet   | None     | n/a       | ABSTAIN (cv_unwired)            | NO              | -             |
| Star or Planet   | wired    | none      | deliberate drop (negative)      | NO              | -             |
| Star             | wired    | blue      | STAR-smack confirmed            | YES             | star          |
| Planet           | wired    | purple    | PLANET-smack confirmed          | YES             | planet        |
| Star             | wired    | purple    | MISMATCH (color!=body)          | see OQ 2        | abstain*      |
| Planet           | wired    | blue      | MISMATCH (color!=body)          | see OQ 2        | abstain*      |
| Star or Planet   | wired    | <unknown> | fail-closed abstain             | NO              | -             |
*MISMATCH rows (color disagrees with journal body_type) are an OPEN QUESTION (OQ 2). Default until resolved: FAIL-CLOSED ABSTAIN (no recovery) and record SmackDeterminationMismatch {body_type, token} loudly â€” never guess which signal wins.

=== 3. OBSERVABILITY (records every path must emit; no silent fall-through) ===
SmackDeterminationAbstained {reason, body_type}  â€” grabber None / unknown token / mismatch
SmackDeterminationNegative  {body_type, token:"none"} â€” deliberate drop, no recovery
StarSmackConfirmed   {body_type:"Star", token:"blue"}
PlanetSmackConfirmed {body_type:"Planet", token:"purple"}
SmackDeterminationMismatch {body_type, token} â€” OQ 2 case
(Names indicative; Stage-1 may align to existing record conventions. The CONTRACT is: every branch of the decision table emits exactly one determination record.)

=== 4. DELIVERABLE EDIT LIST (file:symbol) the build must produce ===
- NEW projects/ed-vision/src/ed_vision/escape_vector.py : detect_escape_vector(frame)->str STUB + NONE/BLUE/PURPLE tokens + TODO calibration frames; optional region_rect stub.
- NEW classify_smack(token)->Optional[str] in ed_core.boot.primitives (or escape_vector.py).
- EDIT projects/ed-autojump/src/ed_autojump/flow/boot_routes.py : _route_sc_exit â€” widen body filter to {Star,Planet}, CV-gate via grabber + detect_escape_vector + classify_smack, abstain when unwired, route blue->star / purple->planet, NO fire on 'none'.
- EDIT projects/ed-core/src/ed_core/boot/scenes.py : _det_starsmack (+ its SceneTemplate gate/action_sketch/fail_closed text) â€” never True on bare telemetry; abstain (None) or add CV confirmation per chosen Shape (i)/(ii). If Shape (i): add DetermineContext.smack_kind field + populate it in boot_routes.build_determine_context.
- EDIT projects/ed-core/src/ed_core/flow/dispatcher.py : _record_event_time â€” document + (per pin) widen preempt to {Star,Planet} as a SCENE-ABORT only; keep _smacked semantics non-recovering; ensure downstream re-confirms via CV.
- EDIT projects/ed-autojump/procedures/smack_recovery.toml + projects/ed-autojump/src/ed_autojump/flow/steps.py : thread body-kind into the recovery run; stub a color param on the orient-to-vector step (default = today's behavior) with TODO; add step_confirm_escape_vector ONLY if Stage-1 deems determination must re-confirm inside recovery (Optional-grabber-None -> pass).
- Wire a runner-level escape-vector frame-grabber attribute (default None) following the frame_grabber/station_menu_grabber injection pattern; leave it UNWIRED by default so the determination abstains until the operator calibrates the CV.

=== 5. NON-GOALS / OUT OF SCOPE ===
- Implementing the CV pixel logic (blocked on operator calibration frames â€” TODO).
- A color-aware orient-to-vector CV (downstream refinement; star path must keep working color-agnostic).
- Any change to the smack_recovery mechanic itself (it is operator-dictated v7; only the determination GATE and body-kind threading change).
- Reconciling the pre-existing RED test_smack_recovery_flow.py::test_v7_step_order (it lists pips/reset_power_distribution steps removed from the toml; RED on purpose per its own NOTE â€” out of scope unless the build touches that ordering).


## INTERFACE

PUBLIC SURFACE introduced/changed (signatures only; bodies are Stage-1):

# NEW â€” projects/ed-vision/src/ed_vision/escape_vector.py
NONE   = "none"      # no escape vector  => NOT smacked (deliberate drop)
BLUE   = "blue"      # blue vector       => STAR-smack
PURPLE = "purple"    # purple vector     => PLANET-smack
def detect_escape_vector(frame: Any) -> str: ...   # STUB: returns NONE; PURE over BGR ndarray; TODO calibration frames
def region_rect(frame_height: int) -> tuple[int,int,int,int]: ...   # OPTIONAL, stub/TODO (debug-overlay parity with station_menu.region_rect)

# NEW â€” ed_core.boot.primitives (or escape_vector.py)
def classify_smack(vector_token: str) -> Optional[str]: ...   # 'blue'->'star','purple'->'planet','none'/other->None

# CHANGED â€” projects/ed-autojump/src/ed_autojump/flow/boot_routes.py
def _route_sc_exit(runner: Any, ev: Any) -> Optional[str]: ...
#   body_type in {Star,Planet}; CV-gate via runner._escape_vector_grabber (Optional[Callable[[],Any]], default None)
#   + detect_escape_vector + classify_smack; abstain->None when unwired/'none'/mismatch; blue->star, purple->planet smack_recovery.

# CHANGED â€” projects/ed-core/src/ed_core/boot/scenes.py
def _det_starsmack(ctx: DetermineContext) -> Optional[bool]: ...   # NEVER True on bare ctx.smacked; abstain (None) until CV-confirmed
# (Shape (i)) NEW optional field:
@dataclass class DetermineContext: ...; smack_kind: Optional[str] = None   # None=unknown/abstain, 'star'/'planet'=CV-confirmed

# CHANGED â€” projects/ed-core/src/ed_core/flow/dispatcher.py
#   _record_event_time: preempt {Star,Planet} (scene-abort only); _smacked stays non-recovering; downstream re-confirms via CV.

# CHANGED â€” projects/ed-autojump/procedures/smack_recovery.toml + steps.py
#   thread recovery body-kind ('star'|'planet') into the run; orient-to-vector step gains optional color param (default today's behavior, TODO color-aware CV); add step_confirm_escape_vector ONLY if Stage-1 requires (Optional-grabber-None -> no-op pass).

# Runner attribute (injection): runner._escape_vector_grabber: Optional[Callable[[], Any]] = None
#   wired exactly like frame_grabber / station_menu_grabber; UNWIRED by default so determination abstains until calibrated.



## INVARIANTS

### [1]
INV1 FAIL-CLOSED DEFAULT: a bare SupercruiseExit with NO escape-vector CV evidence is NOT a smack and MUST NOT cause smack_recovery to run â€” on the event-route path OR the C-series classify path. Absence of evidence => abstain, never recover.

### [2]
INV2 CV-UNWIRED == ABSTAIN: when the escape-vector grabber is None (CV not calibrated/wired), every determination abstains and the EXISTING flow (deliberate-drop / arrival continuation) proceeds unchanged. The redesign ships ship-safe with the CV stubbed.

### [3]
INV3 COLOR ENCODES BODY: blue escape vector <=> star-smack; purple escape vector <=> planet-smack. This mapping lives in exactly ONE place (classify_smack). Same recovery mechanics for both; only the vector color differs.

### [4]
INV4 NO INVENTED CV: detect_escape_vector contains NO guessed pixel thresholds; it is a stub returning 'none' with TODO calibration-frame markers until the operator provides the blue/purple/no-vector frames. No fabricated ED mechanic (there is no supercruise exclusion zone; the only smack signal is the HUD escape vector).

### [5]
INV5 PLANET PARITY: planet-smack is a first-class case everywhere a star-smack is handled (event route, scene, dispatcher preempt, recovery) â€” never silently dropped. The pre-existing body_type=='Star'-only filters are the bug, not the contract.

### [6]
INV6 STATION IS NEVER A SMACK: a SupercruiseExit body_type=='Station' short-circuits before any determination.

### [7]
INV7 HONEST ABSTENTION (carried from scenes.py): a determination that cannot decide returns None (CV-PENDING) â€” never a fabricated True. scene_for treats None as 'not routed here'.

### [8]
INV8 NO CLOCK GATES: no wall-clock timeout decides smack-vs-deliberate; gates are the CV token, journal body_type, and Status.json flags (e.g. the existing FsdCooldown-gated recovery steps).

### [9]
INV9 TOTAL + EXPLICIT MISMATCH: classify_smack is total; a CV-color vs journal-body MISMATCH is an explicit, recorded, FAIL-CLOSED abstain (default for OQ 2) â€” never a silent guess about which signal wins.

### [10]
INV10 OBSERVABILITY: every branch of the decision table emits exactly one determination record (abstain / negative / star-confirmed / planet-confirmed / mismatch) â€” no silent fall-through.



## ACCEPTANCE_CRITERIA

### [1]
AC1 (FAIL-CLOSED, the load-bearing one): with the escape-vector CV grabber UNWIRED (None), a SupercruiseExit with body_type=='Star' does NOT fire smack_recovery â€” _route_sc_exit returns None (or routes to the benign/arrival continuation), and emits a SmackDeterminationAbstained{reason:'cv_unwired'} record. This is the exact inverse of today's BUG A.

### [2]
AC2: with the CV stub returning 'none' (deliberate drop), neither a Star nor a Planet SupercruiseExit fires smack_recovery; a SmackDeterminationNegative record is emitted and the existing flow continues unchanged.

### [3]
AC3: with the CV returning 'blue' on a body_type=='Star' drop, _route_sc_exit routes to smack_recovery with recovery kind 'star' and emits StarSmackConfirmed.

### [4]
AC4: with the CV returning 'purple' on a body_type=='Planet' drop, _route_sc_exit routes to smack_recovery with recovery kind 'planet' and emits PlanetSmackConfirmed (planet-smack is no longer silently dropped â€” closes the second half of BUG A).

### [5]
AC5: a SupercruiseExit with body_type=='Station' never enters the smack determination (early return None), regardless of CV state.

### [6]
AC6 (BUG B): the C-series STARSMACK scene (_det_starsmack) NEVER returns True on bare ctx.smacked / bare telemetry; with no CV-confirmed smack it abstains (None) or the dispatch path falls back to the legacy non-smack route â€” verified by a scene-selection test where ctx has a star drop but no CV confirmation and the selected scene is NOT STARSMACK (and smack_recovery is not dispatched).

### [7]
AC7: classify_smack is total and fail-closed: 'blue'->'star', 'purple'->'planet', 'none'->None, and any other token->None.

### [8]
AC8: detect_escape_vector is a PURE function over a BGR ndarray returning one of 'none'|'blue'|'purple', imports without the [vision] extra (lazy cv2/numpy import, mirroring station_menu), and the stub body carries the three TODO calibration-frame markers (blue star, purple planet, deliberate no-vector) and does NOT contain invented pixel thresholds.

### [9]
AC9 (no new false-negative for stars): the legacy star-smack recovery still works end-to-end once CV returns 'blue' â€” the existing smack_recovery.toml mechanic (target_ahead -> engage_supercruise until_charging -> orient -> hold to SupercruiseEntry -> hop lock -> jump) runs unchanged; the only added gate is the CV determination upstream.

### [10]
AC10: the dispatcher preempt change preempts a live arrival/startup/dock/sc_resume scene on a Star OR Planet SupercruiseExit (planet coverage added) BUT the subsequent re-dispatch still passes through the CV-gated determination, so a benign drop that preempted a scene does NOT result in smack_recovery â€” verified by a dispatcher test (planet drop preempts arrival; with CV unwired/'none', no smack_recovery follows).

### [11]
AC11: no wall-clock timer is introduced as a success/failure gate anywhere in the determination path (house rule); the determination is gated on the CV token + journal body_type + Status flags only.

### [12]
AC12: the change is implement-ready with the CV stubbed â€” the full test suite for the determination logic passes against the STUB (which returns 'none'), proving the fail-closed default, with the real-token cases driven by injecting a fake detector that returns 'blue'/'purple'.



## ACCEPTANCE_TESTS

ACCEPTANCE-TEST DESCRIPTIONS (what each test WOULD assert; none are written/run here):

T1 (AC1, the central ship-safety gate) â€” _route_sc_exit, grabber unwired, star drop: build a fake runner with _escape_vector_grabber=None and a SupercruiseExit ev (body_type='Star'). Assert _route_sc_exit(runner, ev) returns None (NOT 'smack_recovery'), runner._run was NOT called with 'smack_recovery', and a SmackDeterminationAbstained record with reason 'cv_unwired' was emitted. This is the regression test that BUG A is fixed.

T2 (AC2) â€” deliberate drop, CV says 'none': fake runner with a grabber returning a frame and a fake detect_escape_vector returning 'none'. For body_type in {'Star','Planet'} assert no smack_recovery dispatch, a SmackDeterminationNegative record, and (parity) the existing benign-drop continuation path is taken.

T3 (AC3) â€” star-smack: grabber wired, detector returns 'blue', ev body_type='Star'. Assert _route_sc_exit returns 'smack_recovery', runner._run('smack_recovery', ...) called with recovery kind 'star', StarSmackConfirmed recorded.

T4 (AC4) â€” planet-smack: grabber wired, detector returns 'purple', ev body_type='Planet'. Assert 'smack_recovery' dispatched with kind 'planet', PlanetSmackConfirmed recorded. (Today this path produces NOTHING â€” the test would currently fail, proving the gap is closed.)

T5 (AC5) â€” station drop is never a smack: ev body_type='Station', any CV state. Assert _route_sc_exit returns None and no determination record about smack is emitted.

T6 (AC6, BUG B) â€” STARSMACK scene does not enter on bare telemetry: build a DetermineContext with smacked=True (a star drop) but NO CV confirmation (smack_kind None / detector unwired). Assert scene_for(ctx) is NOT the STARSMACK template (it abstains/None or routes elsewhere by priority), and that the classify_startup path does NOT dispatch smack_recovery. A companion test: with smack_kind='star' (CV-confirmed) STARSMACK IS selected.

T7 (AC7) â€” classify_smack truth table: parametrized over ('blue'->'star','purple'->'planet','none'->None,'garbage'->None,''->None) asserting exact returns.

T8 (AC8) â€” detect_escape_vector stub contract: the function imports without the vision extra; called on a synthetic BGR ndarray it returns 'none' (the fail-closed stub value); its source contains the three TODO calibration markers; it returns a value in the {'none','blue','purple'} set for any ndarray input. (No assertion on real pixel classification â€” that is post-calibration.)

T9 (AC9) â€” star recovery mechanic intact: load smack_recovery.toml and assert the escape-vector segment (target_ahead -> engage_supercruise until_charging=True -> orient_compass -> hold_alignment until_event='SupercruiseEntry' -> target_next_route retry_anchor -> ... -> engage_jump) is preserved (mirrors the existing test_escape_vector_segment_charge_orient_hold), plus that the body-kind is threaded into the run when dispatched as 'star'.

T10 (AC10) â€” dispatcher preempt covers planet but does not over-recover: feed the dispatcher a SupercruiseExit body_type='Planet' while a live arrival scene is running; assert _preempt is set (scene aborts) AND that with the CV unwired/'none' the follow-on _route_sc_exit does NOT dispatch smack_recovery. A second case: body_type='Star' same shape, parity with today plus the CV gate.

T11 (AC11) â€” no clock gate: static/behavioral check that the determination path consults only the CV token, ev.body_type, and (where relevant) Status flags / the existing FsdCooldown-gated recovery steps â€” no new wall-clock deadline decides smack-vs-not.

T12 (decision-table MISMATCH, OQ 2 default) â€” body_type='Star' but detector returns 'purple' (or Planet+blue): assert FAIL-CLOSED â€” no smack_recovery, SmackDeterminationMismatch recorded. (This test pins the chosen default for the open question; if the operator later rules the CV color authoritative over journal body_type, this test changes.)

T13 (AC12, stub-driven suite) â€” the whole determination suite runs green against the real stub for the negative/abstain cases (T1,T2,T5) and against an injected fake detector for the positive cases (T3,T4) â€” proving the design is implement-ready with the CV stubbed and the fail-closed default is the shipped default.



## OPEN_QUESTIONS

### [1]
OQ1 RESTART-WHILE-SMACKED IS CV-BLIND (ship-safety gap): on a cold bot restart that lands the ship already sitting smacked in normal space, is the escape vector STILL on the HUD/compass to be read, or has it cleared? If it has cleared, the CV discriminator is unavailable on restart and the determination can ONLY abstain â€” meaning a genuine restart-while-smacked would NOT auto-recover. Operator/game-truth needed: does the escape vector persist after the drop completes, and for how long / under what condition does it clear? (Memory smack-escape-vector-recovery notes the compass reader currently returns found=False on the real escape vector â€” this compounds the gap.) Until answered, restart-while-smacked abstains (no false recovery) and the existing flow continues.

### [2]
OQ2 COLOR vs BODY_TYPE MISMATCH AUTHORITY: if the CV returns 'purple' on a journal body_type=='Star' drop (or 'blue' on 'Planet'), which signal is authoritative? Default pinned by this spec = FAIL-CLOSED abstain + SmackDeterminationMismatch record. Operator ruling needed if the CV color should override the journal body_type (e.g. journal body_type can be wrong/absent), or vice-versa.

### [3]
OQ3 CALIBRATION FRAMES (blocks the real detector, NOT this spec): operator must provide three frames to implement detect_escape_vector â€” (a) a BLUE star-smack escape vector, (b) a PURPLE planet-smack escape vector, (c) a deliberate-drop NO-vector negative frame. Drop them into tests/fixtures/ (path TBD, mirror hud_sc_indicators fixture plan). Until then the detector is a NONE-returning stub.

### [4]
OQ4 DROP-TIME SIDE EFFECT: _route_sc_exit currently sets runner._event_times['drop'] on every star drop (boot_routes.py:519). Should this be set on EVERY drop (current) or only on a CONFIRMED smack? Are there consumers (staleness/diagnostics) that rely on drop-time for benign drops? Confirm before narrowing.

### [5]
OQ5 PLANET PREEMPT DISRUPTION: widening the dispatcher preempt to body_type=='Planet' will ABORT a live arrival/dock/sc_resume scene on ANY planet drop (incl. deliberate). Is aborting a live scene on a deliberate planet drop acceptable (re-dispatch then continues benign), or does a legitimate planet-approach flow get disrupted? Operator confirmation of the two-stage (wide-preempt + narrow-CV-recovery) design for planets.

### [6]
OQ6 RECOVERY MECHANIC IDENTICAL FOR PLANET? Game-truth states 'same align-and-burn-out mechanics' for planet-smack. Confirm the smack_recovery.toml v7 dance (nav_panel_target row-0 star lock, pitch-180-star-astern, FsdCooldown gate, escape-vector charge, 13s clear-of-star) works UNCHANGED when the body is a PLANET â€” specifically: is nav_panel_target locking the correct body, is the pitch-astern reference correct, and is the post-charge clear distance the same for a planet? If any step is star-specific, planet recovery needs its own variant (out of scope for this spec; flag for a follow-up). Until confirmed, planet recovery reuses the star procedure and this assumption is logged as a risk.

### [7]
OQ7 SCENE FIX SHAPE: Shape (i) (CV fact threaded into DetermineContext.smack_kind) vs Shape (ii) (CV guard at the dispatch site). Shape (i) requires build_determine_context (boot_routes.py:61) to have access to a frame at classify time â€” does the runner expose an escape-vector frame at classify_startup time, or only at event-route time? If no frame is available during boot classification, Shape (i) is infeasible and Shape (ii) is forced. Resolve in Stage-1; the choice is a ledger entry.

