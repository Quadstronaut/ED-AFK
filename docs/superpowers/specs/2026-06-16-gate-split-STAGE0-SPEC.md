# Stage-0 Spec — Gate Split (ARRIVAL / REFUEL / TRAVERSAL / EXPLORATION) + Live Overlay Gate

Council council-v2-spec. RE-RUN (prior wf_8ad0f339-4eb routed back to Stage 0 on contract
ambiguity). This spec PINS the contracts the prior run forked on. Stage 1 generates the
DESIGN doc; Stage 2 writes the executable proof; this spec is the rubric both are judged
against.

DESIGN-ONLY. The sole committable artifact is
`docs/superpowers/specs/2026-06-16-gate-split-DESIGN.md` plus an OPTIONAL throwaway proof
bundle under `docs/superpowers/specs/gate_split_artifacts/`. NO file under
`projects/**/src/`, NO `projects/ed-autojump/procedures/*.toml`, and NO `*.toml` config is
modified by this council. The reviewer's proof imports the REAL production modules and
asserts coherence WITHOUT editing them.

---

## 0. Ground truth (verified against live code, 2026-06-16)

Judge against these line-anchored facts, not prose. Paths are repo-relative.

- `projects/ed-autojump/procedures/arrival.toml`: 13 ordered steps; `parallel_tracks=["honk"]`;
  `[on_required_fail] retry_from="scoop_refuel" max_retries=3 backoff_s=2.0`. The 13 steps in
  order: `set_throttle pct=0` / `nav_panel_target` (early best-effort lock, no skip_to) /
  `scoop_refuel(...)` / `nav_panel_target required=false skip_to="target_next_route" max_rows=3`
  (bounded lock-speed gate) / `sc_assist_orbit` / `wait s=13.0` / `explore` /
  `station_strand_recovery` / `target_next_route required=true` / `set_throttle pct=100` /
  `orient_compass required=true` / `orient_widget_ring required=true` /
  `engage_jump_clearance required=true`.
- `projects/ed-autojump/procedures/honk.toml`: untouched parallel track; owned by whichever
  procedure declares `parallel_tracks=["honk"]`.
- `projects/ed-autojump/src/ed_autojump/flow/boot_routes.py:507-530` `_route_fsd_jump` — the
  SINGLE live entry: on a non-route-complete FSDJump it arms the latch and calls
  `runner._run("arrival")` (line 529). Route-complete branches to `route_complete_park`.
- `boot_routes.py:269-314` `classify_startup` — C-series front-end. One-shot guard
  `if runner._startup_done: return None` at :278; consumes `_startup_done=True` on EVERY path
  at :283. Maps `tmpl.state` via `_STATE_TO_PROC` at :290; `kind=="run"` -> `runner._run(payload)`.
- `boot_routes.py:125-139` `_STATE_TO_PROC` — TOTAL over all 11 `CSeriesState`, asserted at
  import (:138). CURRENT (pre-design) values: `REFUEL`, `TRAVERSAL`, `EXPLORATION` are all
  `("fallback", None)`. `ARRIVAL` is `("run","arrival")`.
- `projects/ed-core/src/ed_core/boot/scenes.py` — `CSeriesState` enum (11 states) and
  `C_SERIES_SCENES` priority tuple (asserted len==11, one per state). PRIORITY ORDER (positional):
  PAUSE, RESUME, STARSMACK, ARRIVAL, **REFUEL (idx 4)**, DOCKED, **TRAVERSAL (idx 6)**,
  EXPLORATION, STARTUP, NO_ROUTE, PARKED. Determination predicates:
  `_det_refuel = scooping_fuel OR (low_fuel AND in_supercruise)`;
  `_det_traversal = in_supercruise AND not route_empty AND not arrival_latch.armed`;
  `_det_exploration = in_supercruise AND route_empty AND not arrival_latch.armed AND exploration_mode`.
  REFUEL (idx 4) outranks TRAVERSAL (idx 6) — confirms the PIN-F loop hazard.
- `projects/ed-core/src/ed_core/flow/dispatcher.py:37` `_PREEMPT_ON_SMACK =
  frozenset({"arrival","startup","dock","sc_resume"})`.
- `dispatcher.py:621-633` — `SupercruiseExit` Body in (Star,Planet) sets `self._preempt="star_smack"`
  ONLY IF `self._running_proc in _PREEMPT_ON_SMACK`.
- `dispatcher.py:642-643` — `if name=="SupercruiseExit": self._smacked = body_type in ("Star","Planet")`
  set UNCONDITIONALLY (no `_running_proc` guard).
- `dispatcher.py:390-406` — `_should_abort()` is OPERATOR-ONLY (stop_requested / panic; does NOT
  read `_smacked`/`_preempt`). `_run_abort() = _should_abort() or _preempt is not None`. The
  per-run `StepContext.should_abort` is wired to `_run_abort` (dispatcher.py:484).
- `dispatcher.py:507-583` `_run(name)` — fresh run resets `self._preempt=None` (:514), sets
  `_running_proc=name` (:515), runs the proc + honk track, and in `finally` sets
  `_running_proc=None` (:571). BETWEEN two sequential `_run()` calls `_running_proc is None`,
  so a smack in that window does NOT set `_preempt` — only `_smacked` (:643) records it.
- `projects/ed-core/src/ed_core/flow/interpreter.py:79-86` — overlay label source:
  `ctx.overlay.step(proc.name, step.action, i+1, n)`. `overlay.step` (overlay.py:222-223) =
  `status(f"{procedure} > {action} ({idx}/{total})")`. The procedure NAME is the ONLY label.
  `run_procedure` is the fail-closed engine; witchspace pause at :60-77.
- `interpreter.py:138-178` — required-fail retry resolution: `retry_from` (and
  `retry_from_if_supercruise`) resolve via `proc.index_of_action(...)` — they MUST name a step
  WITHIN the SAME procedure. `validate_procedure` (loader.py:82-108) reports
  `"on_required_fail.retry_from {rf!r} matches no step"` for a dangling target. Therefore a
  TRAVERSAL `retry_from="scoop_refuel"` (scoop now living in REFUEL) is a LOAD-TIME error.
- Model/loader API the proof must use: `ed_core.flow.loader.load_procedure`,
  `validate_procedure`; `ed_core.flow.step_registry.STEP_REGISTRY`;
  `Procedure.parallel_tracks` (tuple), `Procedure.on_required_fail`
  (`.retry_from`/`.max_retries`/`.backoff_s`), `Procedure.index_of_action(action)`,
  `Procedure.steps[i].{action,params,required,skip_to}`.

---

## 1. Interface (the design's contract surface)

This is a re-partition + relabel + scene-transition. The interface is the set of named
artifacts the DESIGN proposes and the proof asserts coherent.

### 1.1 Proposed procedures (design files only; live under `gate_split_artifacts/`, NOT installed)

- **`arrival.toml`** — BRIEF handoff. Owns `parallel_tracks=["honk"]`. Steps: kick the honk
  track (non-blocking, sub-second, background) and EVALUATE FUEL only. NO required steps. NO
  `engage_jump_clearance`. Hands off immediately to REFUEL (fuel low) or TRAVERSAL (fuel ok).
  Owns NEITHER scoop NOR get-around NOR orient NOR jump. (PIN-A)
- **`refuel.toml`** — owns `scoop_refuel`. NO required steps (scoop is non-required so a
  backstop/skip never aborts). NO `engage_jump_clearance`. Hands off to TRAVERSAL on
  scoop-complete. (PIN-B)
- **`traversal.toml`** — the cross-system get-around + orient + jump-out: the monolith's
  mislabeled tail. Steps, in original order: the lock-speed get-around `nav_panel_target`
  (`required=false skip_to="target_next_route" max_rows=3`), `sc_assist_orbit`, `wait s=13.0`,
  `target_next_route required=true`, `set_throttle pct=100`, `orient_compass required=true`,
  `orient_widget_ring required=true`, `engage_jump_clearance required=true`. Ends in the
  required `engage_jump_clearance` with NO later step. Carries the retry anchor. The
  `skip_to="target_next_route"` target MUST live in this procedure (no dangling skip). (PIN-C)
- **`exploration.toml`** — DISTINCT procedure (distinct overlay label), NOT a traversal alias.
  Runs the in-system tour (`explore` + `station_strand_recovery`) then RE-DERIVES to TRAVERSAL
  once a route exists. Its onward path MUST reconcile the empty-route precondition: an
  EXPLORATION scene has `route_empty=True`, so a `required=true target_next_route` step would
  fail on the empty route. The design MUST NOT give exploration a required-`target_next_route`-
  on-empty. (PIN-D)

### 1.2 Proposed determination edits (`_STATE_TO_PROC`)

- `REFUEL -> ("run","refuel")` (was `("fallback",None)`)
- `TRAVERSAL -> ("run","traversal")` (was `("fallback",None)`)
- `EXPLORATION -> ("run","exploration")` (was `("fallback",None)`)
- `ARRIVAL -> ("run","arrival")` (unchanged). The map stays TOTAL over all 11 states.

### 1.3 Proposed dispatcher edits (specified in design, applied only in proof)

- `_PREEMPT_ON_SMACK` gains `"refuel"`, `"traversal"`, `"exploration"` (arrival already present),
  preserving the existing `{"arrival","startup","dock","sc_resume"}`.
- The inter-procedure GAP-smack guard (PIN-E): the transition mechanism's hop between
  sequential `_run()` calls is gated by `runner._smacked or runner._preempt`, NOT by
  `runner._should_abort()`.
- A bounded-refuel cap / "refuel-attempted-this-arrival" latch (PIN-F).

### 1.4 Overlay change

A minimal edit so the active GATE name reaches `overlay.status()`. The label source is already
`proc.name`; the change is that distinct procedures (REFUEL/TRAVERSAL/EXPLORATION) now fly the
legs that were all "arrival". Literal before/after the design must show:
`arrival > orient_compass (8/13)` -> `TRAVERSAL > orient_compass (8/10)`. The
`{label} > {action} (i/n)` shape is preserved.

---

## 2. Invariants (MUST hold; any violation = blocker)

- **INV-A (ARRIVAL is brief).** ARRIVAL owns only honk-kick + fuel-eval. It contains NO
  `scoop_refuel`, `sc_assist_orbit`, `wait`, `explore`, `station_strand_recovery`,
  `nav_panel_target`-get-around, `target_next_route`, `orient_compass`, `orient_widget_ring`,
  or `engage_jump_clearance`. It has NO required step. Rejects any partition where ARRIVAL owns
  steps 1-5c. (PIN-A)
- **INV-B (REFUEL isolation).** `scoop_refuel` lives in REFUEL and nowhere else.
  `_STATE_TO_PROC[REFUEL]==("run","refuel")`. (PIN-B)
- **INV-C (TRAVERSAL fail-closed chain).** TRAVERSAL preserves the monolith's tail in ORDER,
  ends in `engage_jump_clearance required=true` with NO step after it, and every required
  prerequisite (`target_next_route`, `orient_compass`, `orient_widget_ring`) is present,
  required, and precedes the jump gate. Every `skip_to` target resolves WITHIN TRAVERSAL (no
  dangling skip). (PIN-C)
- **INV-D (partition totality + order).** `arrival.steps ++ refuel.steps ++ traversal.steps`
  equals the installed `arrival.toml`'s 13 steps, position-by-position, action + params +
  required + skip_to identical. Each of the 13 maps to EXACTLY ONE new procedure. (PIN-A/B/C)
- **INV-E (EXPLORATION reconciles empty route).** EXPLORATION is its own procedure with its own
  label. It does NOT carry a `required=true target_next_route` that would fail on `route_empty`.
  Its onward-jump mechanism is one of: (a) re-derive to TRAVERSAL after a route appears, or
  (b) a non-required onward step. The proof asserts no required-`target_next_route`-on-empty.
  (PIN-D)
- **INV-F (smack preemption incl. the gap).** Every new live-SC transit procedure name
  (`refuel`, `traversal`, `exploration`) is in the proposed `_PREEMPT_ON_SMACK`, and the
  existing members are preserved. The inter-procedure gap guard reads `_smacked or _preempt`
  (NOT `_should_abort()`). (PIN-E)
- **INV-G (bounded refuel).** REFUEL cannot re-enter indefinitely. Given `scoop_refuel` returns
  `ok=False` (backstopped) while `_det_refuel` (low_fuel AND in_supercruise) stays True and
  REFUEL(idx4) > TRAVERSAL(idx6), a bounded cap / per-arrival latch breaks the loop so control
  reaches TRAVERSAL. The mechanism is specified and demonstrated. (PIN-F)
- **INV-H (one-shot classifier reconciled).** The mid-flight ARRIVAL->REFUEL/TRAVERSAL
  transition mechanism is reconciled with the one-shot `_startup_done` guard
  (boot_routes.py:278): the design states EITHER sequential chaining inside the live run path
  OR a justified lift of the one-shot at boot, and the proof exercises that the gate label
  changes mid-flight without re-tripping the classifier in a way that breaks routing.
- **INV-I (honk single-shot).** Exactly ONE procedure declares `parallel_tracks=["honk"]`
  (ARRIVAL). REFUEL/TRAVERSAL/EXPLORATION declare none, so honk fires once per system, not per
  gate.
- **INV-J (retry anchor resolves in-procedure).** The retry anchor for the jump-owning
  procedure(s) names a step WITHIN that procedure (interpreter resolves via
  `index_of_action`; `validate_procedure` errors on a dangling target). No TRAVERSAL
  `retry_from="scoop_refuel"`. `max_retries=3`, `backoff_s=2.0` preserved.
- **INV-K (totality preserved).** `_STATE_TO_PROC` stays total over all 11 `CSeriesState`
  (the import-time assert still holds for the proposed map).
- **INV-L (overlay shape).** The overlay edit keeps the `{label} > {action} (i/n)` shape; the
  only change is the label now varies per gate.
- **INV-M (no production mutation).** No file under `projects/**/src/` or
  `projects/ed-autojump/procedures/` and no installed `*.toml` is modified. The committable
  artifact set is exactly the DESIGN doc (+ optional throwaway bundle under
  `gate_split_artifacts/`).
- **INV-N (no new flight behavior).** Each step's params, the witchspace pause, the fail-closed
  jump gate, smack preemption, and the honk track are PRESERVED byte-for-byte. This is
  re-partition + relabel + scene-transition only.
- **INV-O (open question flagged, not guessed).** The retry re-anchor question (re-scoop vs
  re-lock-on-traversal) is carried as the ONE open question with a clearly-flagged DEFAULT
  (re-lock on a TRAVERSAL step, NOT re-scoop) and the alternative presented for operator sign-off.

---

## 3. Acceptance criteria

The DESIGN doc is acceptable iff ALL of:

- **AC1.** Contains the scene+handoff TABLE: for ARRIVAL/REFUEL/TRAVERSAL/EXPLORATION — job,
  journal/Status TRIGGER (no wall-clock), and hands-off-to. (Deliverable 1)
- **AC2.** Contains the STEP PARTITION mapping each of arrival.toml's 13 steps to exactly one
  new procedure, preserving order and the fail-closed chain, and NAMING the honk owner and the
  retry anchor. (Deliverable 2; satisfies INV-A..D, I, J)
- **AC3.** Contains the DETERMINATION changes: the three `_STATE_TO_PROC` edits; the exact
  mid-flight ARRIVAL->REFUEL/TRAVERSAL transition mechanism reconciled with the one-shot
  classifier; the `_PREEMPT_ON_SMACK` additions; the gap-smack guard (`_smacked or _preempt`);
  and the bounded-refuel cap. (Deliverable 3; satisfies INV-F, G, H, K)
- **AC4.** Contains the OVERLAY change: the minimal edit, with the literal before/after
  (`arrival > orient_compass (8/13)` -> `TRAVERSAL > orient_compass (8/10)`). (Deliverable 4;
  satisfies INV-L)
- **AC5.** Carries the ONE open question (re-scoop-on-retry) with its DEFAULT (re-lock on a
  TRAVERSAL step) and the alternative, flagged for operator sign-off — NOT silently resolved.
  (Deliverable 5; satisfies INV-O)
- **AC6.** Contains the RISK/SCOPE note confirming re-partition + relabel + scene-transition
  (NOT new flight behavior) and enumerating that the fail-closed jump gate, each step's params,
  the witchspace pause, smack preemption (incl. the gap), and the honk track are all preserved.
  (Deliverable 6; satisfies INV-N)
- **AC7.** Is concise and operator-skimmable (tables over prose; PINs not re-litigated).
- **AC8 (HARD CONSTRAINT — gate-blocking).** A reviewer pass REQUIRES an executable proof
  artifact bound to the committable design. The proof imports the REAL `ed_core`/`ed_autojump`
  modules, edits NO production file, and asserts every invariant below (see §4). A
  spec-conformance fail or any blocker-severity fail HALTS the gate (spec-conformance routes to
  Stage 0; other blockers route to Stage 2/Stage 1 per the arbiter rubric).
- **AC9.** No production file mutated (INV-M); committable set == DESIGN doc (+ optional
  throwaway bundle).

---

## 4. Concrete executable acceptance tests

The reviewer's proof (e.g. `docs/superpowers/specs/gate_split_artifacts/prove_gate_split.py`)
MUST import the REAL modules and assert all of the following. Each maps to an invariant; a
failure of any is a blocker. Run from repo root with the ed-autojump venv:

```
projects\ed-autojump\.venv\Scripts\python docs\superpowers\specs\gate_split_artifacts\prove_gate_split.py
```

Exit 0 = all green; non-zero on first failed assertion. Required assertions:

- **T1 (INV-K).** Build `proposed = dict(boot_routes._STATE_TO_PROC)`; apply the three edits;
  assert `set(proposed)==set(CSeriesState) and len(proposed)==11`.
- **T2 (INV-B, and run-target resolvability).** Each `("run", name)` gate-split target
  (`refuel`, `traversal`, `exploration`, `arrival`) loads via `load_procedure(<bundle>/name.toml)`
  and `proc.name==name`.
- **T3 (INV-C/J load validity).** Every proposed procedure passes
  `validate_procedure(proc, set(STEP_REGISTRY))` with `errors==[]` — proves NO unknown action
  and NO dangling `skip_to`/`retry_from` against the REAL merged step registry. (This is the
  assertion that catches a TRAVERSAL `retry_from="scoop_refuel"` and gen-sonnet-2's dangling
  `skip_to`.)
- **T4 (INV-D).** `arrival.steps ++ refuel.steps ++ traversal.steps` has length 13 and equals
  the installed `arrival.toml` steps position-by-position on `(action, params, required, skip_to)`.
- **T5 (INV-A).** `arrival` and `refuel` each contain NO required step and NO
  `engage_jump_clearance`. `arrival` contains none of {`scoop_refuel`, `sc_assist_orbit`,
  `wait`, `explore`, `station_strand_recovery`, `target_next_route`, `orient_compass`,
  `orient_widget_ring`}. `refuel` contains `scoop_refuel`.
- **T6 (INV-C).** For `traversal`: last step is `engage_jump_clearance` with `required is True`
  and NO step after it; `target_next_route`, `orient_compass`, `orient_widget_ring` each
  precede it and are `required`. Every `skip_to` in `traversal` resolves to an in-procedure
  index.
- **T7 (INV-E — the gen-sonnet-3 blocker, MUST be distinct from T4).** `exploration` is loadable
  and contains the in-system tour steps (`explore`, `station_strand_recovery`); it does NOT
  contain a `required=true` `target_next_route` (i.e. assert there is no step with
  `action=="target_next_route" and required is True`). Assert the design's stated onward
  mechanism: EITHER `exploration` has no `target_next_route` at all (re-derive-to-TRAVERSAL
  model) OR its `target_next_route` is `required is False`. EXPLORATION MUST NOT be asserted
  byte-identical to TRAVERSAL.
- **T8 (INV-I).** `arrival.parallel_tracks == ("honk",)`; each of `refuel`, `traversal`,
  `exploration` has `parallel_tracks == ()`.
- **T9 (INV-J).** The jump-owning procedure(s)' `on_required_fail.retry_from` is non-None and
  resolves via `index_of_action` WITHIN that procedure; `max_retries==3` and `backoff_s==2.0`.
- **T10 (INV-F static).** Construct `proposed_preempt = set(REAL _PREEMPT_ON_SMACK) |
  {"arrival","refuel","traversal","exploration"}` (importing the REAL frozenset from
  `ed_core.flow.dispatcher`); assert each of the four names is in it and every original member
  is preserved.
- **T11 (INV-F gap — the NEW assertion the prior run lacked).** Demonstrate the gap-smack guard
  reads the right signal. Using the REAL `FlowRunner` semantics: assert that `_smacked` is set
  by a `SupercruiseExit(Star|Planet)` REGARDLESS of `_running_proc` (i.e. it is set even when
  `_running_proc is None`, the inter-procedure gap), whereas `_preempt` requires
  `_running_proc in _PREEMPT_ON_SMACK`. Assert the chosen guard expression is `_smacked or
  _preempt` and that `_should_abort()` does NOT read `_smacked`/`_preempt` (so it would miss the
  gap). May be proven by driving `_on_tail_event` / `_record_event_time` with a fake
  SupercruiseExit event against a constructed runner, or by an explicit source-level assertion
  that the proof documents and checks against the imported functions' behavior. The point: the
  inter-`_run()` gap is provably covered.
- **T12 (INV-G — the NEW assertion the prior run lacked).** Demonstrate the bounded-refuel cap.
  Model the loop hazard: with `_det_refuel` True and REFUEL outranking TRAVERSAL, show that the
  design's cap/latch (a counter or per-arrival boolean) causes the (N+1)th classification after
  a backstopped scoop to NOT select REFUEL, so control reaches TRAVERSAL. Prove against the
  real priority order in `C_SERIES_SCENES` (REFUEL idx < TRAVERSAL idx) so the test fails if
  the design's cap does not actually break the loop.
- **T13 (INV-L overlay).** With a fake overlay matching the REAL `OverlayWriter.step`/`status`
  shape: `step("arrival","orient_compass",8,13)` yields status
  `"arrival > orient_compass (8/13)"`; `step("TRAVERSAL","orient_compass",8,10)` yields
  `"TRAVERSAL > orient_compass (8/10)"`; the after-string has exactly one `>` and ends in `)`.
- **T14 (INV-M).** The proof asserts (or the council manifest confirms) that the only
  git-tracked changes outside the throwaway bundle are the DESIGN doc — no `projects/**/src/`,
  no `projects/ed-autojump/procedures/*.toml`, no installed `*.toml`.

A proof that omits T7, T11, or T12 is INCOMPLETE — those are the three blockers from the prior
run (gen-sonnet-3 empty-route, gen-opus-1 wrong gap guard, PIN-F unbounded loop) and a `pass`
without them is treated as `abstain` (no evidence_artifact for the load-bearing invariants).

---

## 5. The one carried open question (operator resolves at review)

**OQ: retry re-anchor after scoop moved to REFUEL.** Because `scoop_refuel` now lives in REFUEL,
a TRAVERSAL-phase required-failure can no longer `retry_from="scoop_refuel"` (that target is not
a TRAVERSAL step; the interpreter resolves `retry_from` only within the running procedure, and
`validate_procedure` would error on the dangling name).

- **DEFAULT (design's provisional choice, flagged for sign-off):** a TRAVERSAL-phase retry
  RE-ESTABLISHES lock + pose by anchoring `retry_from` on a TRAVERSAL step
  (`nav_panel_target` / `target_next_route`). It does NOT re-scoop.
- **Alternative (operator's call):** a TRAVERSAL required-failure re-enters REFUEL / re-scoops
  (e.g. via a cross-procedure re-derive on failure). Present but do not adopt without sign-off.

The design states the DEFAULT as provisional and presents the alternative. It does NOT silently
guess.

---

## 6. Out of scope / non-goals

- No production code, no installed-procedure or config `.toml` edits. Implementation is a later
  council.
- No new flight behavior, timing, params, or step semantics.
- PAUSE / RESUME `_STATE_TO_PROC` entries stay `("fallback", None)` — untouched.
- The PINs (A-F) are settled; re-litigating them is out of scope and a Stage-0 fail.
