# COUNCIL C4 — SMACK RECOVERY SCENE — STAGE-0 SPEC (BUILD AUTHORIZED, 2026-06-18)

> ✅ **RECONCILED (operator 2026-06-18).** Two status inversions from the original arbiter draft, applied
> per the locked decisions:
> - **L1 — step 6 is RESOLVED/LOCKED: `engage_supercruise` (Key_J).** The original "STEP-6 CONFLICT /
>   BLOCKED-ON-KYLE #1 (headline)" framing is **struck** — step 6 is settled (re-enter SC, spawn the
>   escape vector, ALIGN-AND-HOLD to `SupercruiseEntry`); it is **NOT** `engage_jump_clearance` (Key_K).
>   The "DEVIATION-BLOCKED / target-nothing / verbal-vs-written contradiction" framing was a Claude error.
> - **L5 — DESIGN-ONLY is LIFTED.** Building is authorized for ratified scenes (the master-spec standing
>   rule that held the live flight path untouched is superseded). **NO GUESSING + fail-closed remain.**
>
> The §4 "STEP-6 CONFLICT", the §5 item-1 headline blocker, AC-3, and the T-2 `STEP6_BLOCKER` assertion
> below are rewritten to reflect this resolution. The ship-safety content (smack-glare guards
> `behind_confirm_reads` / `behind_fill_max`, the escape-vector ALIGN-AND-HOLD ladder) is RETAINED — the
> resolution settles a status, it does not delete safety.

Arbiter-authored Stage-0 spec, reconciled. Remaining unknowns are still flagged `BLOCKED-ON-KYLE:` /
`BLOCKED-ON-CONTRACT:` — those are real contract gaps (C1/C2), not the struck framing. Downstream stages
are judged against this doc.

Scope authority: `C4-smack-recovery.md` (self-contained brief) + the SMACK RECOVERY section of
`2026-06-17-flow-redesign-MASTER-SPEC.md`. C1/C2 own the actions/transitions this scene consumes.

---

## 0. What this scene IS (and what it is NOT)

The SMACK RECOVERY scene is the **procedure body that runs AFTER the bot is already in the
`STARSMACK` state** (`projects/ed-core/src/ed_core/boot/scenes.py`, `proc="smack_recovery"`). It
recovers a ship that emergency-dropped inside a body's exclusion zone and gets it back to a clean
hyperspace-jump posture, then chains to **Traversal**.

It is NOT the smack *detector*. The smack trigger/discrimination is the escape-vector CV
(`ed_vision/escape_vector.py`, currently a fail-closed STUB returning `NONE`), threaded into the
boot router via `_route_sc_exit` → `DetermineContext.smack_kind`. That detector is **track G2** and is
out of scope here. This scene DEPENDS on it only for entry (the scene never runs unless
`smack_kind ∈ {star, planet}` already latched).

DELIVERABLE of C4 = a redesigned `smack_recovery.toml` **sketch inside this doc** that realises the
operator's authored 8-step flow, plus the consolidated `BLOCKED-ON-KYLE` / `BLOCKED-ON-CONTRACT` list.
Step 6 is now LOCKED to `engage_supercruise` (Key_J) — no longer the headline blocker.

---

## 1. INTERFACE

### 1.1 Scene contract (what the dispatcher calls)
- **Name:** `smack_recovery` (existing procedure name; `scenes.py` already routes `STARSMACK → proc="smack_recovery"`). Redesign REPLACES the body of `projects/ed-autojump/procedures/smack_recovery.toml`; it does NOT rename the proc or rewire the scene template.
- **Entry precondition (owned by C-series boot, NOT re-checked here):** ship in NORMAL space, just emergency-dropped at a body, `smack_kind ∈ {star, planet}` CV-confirmed. `_det_starsmack` ABSTAINS (None) on bare telemetry, so the scene never runs on an unconfirmed drop.
- **Exit / success:** ship is back in a state where the **Traversal** scene can take over — i.e. supercruise re-entered and oriented for the route. The authored flow ends `→ Traversal` (step 8). The mechanism of that chaining is **C2's section-transition contract** (RESOLVED: Python orchestrator `transition_to`); C4 emits the transition marker, it does not implement the dispatcher hop.
- **Failure:** any `required` step exhausts retries → procedure `on_required_fail` fires → park / abort (fail closed: never throttle-into-star, never jump on an unread frame).

### 1.2 Authored step sequence (operator verbatim — the WHAT) — **LOCKED 2026-06-18**
1. `set_throttle 100`
2. `nav_target_star`           ← **C1 contract action** (does not exist yet)
3. `pitch_compass`             ← exists (`steps_shared.step_pitch_compass`); carry the smack-glare guards
4. `target_ahead`              ← exists (`steps_shared.step_target_ahead`)
5. `wait_cooldown_clear`       ← exists (`steps_shared.step_wait_cooldown_clear`)
6. `engage_supercruise` (Key_J) ← exists (`steps_shared`). **SETTLED/LOCKED.** Re-enter SC from normal
   space; `until_charging` spawns the escape vector the ship ALIGN-AND-HOLDs to `SupercruiseEntry`. This
   is the SC-entry mechanic — **NOT** `engage_jump_clearance` (Key_K). The operator's "(enter supercruise)"
   note was the intent; the `engage_jump_clearance` token was a copy error and is rejected.
7. `nav_supercruise_star`      ← **C1 contract action** (does not exist yet)
8. → **Traversal**

### 1.3 Actions consumed and their source-of-truth
| Action | Status | Owner | Notes |
|---|---|---|---|
| `set_throttle` | EXISTS | ed-core shared | `SetSpeed100` bind. |
| `nav_target_star` | **DOES NOT EXIST** | **C1** | Idempotent single lock of main star; verify `Status.Destination == system`. C4 designs AGAINST C1's contract; flags every assumption. |
| `pitch_compass` | EXISTS | ed-core shared | `until="behind"`; smack-glare false-pass guards (`behind_confirm_reads`, `behind_fill_max`) are LOAD-BEARING and MUST be carried (memories `smack-compass-glare`). |
| `target_ahead` | EXISTS | ed-core shared | `SelectTarget`; star astern → nothing ahead → DESELECTS. |
| `wait_cooldown_clear` | EXISTS | ed-core shared | Blocks on `Status.FsdCooldown` bit; fails closed without status. |
| `engage_supercruise` | EXISTS | ed-core shared | **THE step-6 action (LOCKED).** SC entry (`Supercruise`/J); `until_charging` spawns the escape vector. |
| `engage_jump_clearance` | EXISTS | ed-autojump | Hyperspace clearance LOOP (`Hyperspace`/K). **NOT used by smack recovery** — named only to mark what was rejected at step 6. See §4. |
| `nav_supercruise_star` | **DOES NOT EXIST** | **C1** | Opens star-row detail page, presses SUPERCRUISE ASSIST once. Replaces the blind `sc_assist_orbit`. |

### 1.4 Binds (confirmed in `ed_core/binds_validate.py REQUIRED_ACTIONS`)
- `Supercruise` = Key_J (SC entry). `Hyperspace` = Key_K (hyperspace jump). **DISTINCT binds.** Step 6
  uses `Supercruise` (Key_J) — the SC-entry path — NOT `Hyperspace` (Key_K).
- `SetSpeed100`, `SelectTarget`, `PitchUp/DownButton`, `YawLeft/RightButton`, `FocusLeftPanel`, `UI_Select`, `UI_Right`, `UI_Up`, `UI_Down`, `CycleNextPanel` all confirmed bound.

---

## 2. INVARIANTS (must hold in any C4 design candidate)

- **INV-1 (fail-closed throttle).** No step throttles toward an un-pitched body. `pitch_compass(until="behind")` MUST certify the body is astern (with the smack-glare guards) before any forward burn. Carry `behind_confirm_reads ≥ 3` and `behind_fill_max ≤ 0.30` — the 2026-06-08 false-pass refix; dropping them regresses the ship-into-star bug.
- **INV-2 (no blind jump/SC).** No `engage_*` step proceeds on an unread frame. Vision/status-unwired → step returns False (fail closed), never fail-open.
- **INV-3 (no arbitrary timed waits as gates).** Every gate is a journal event or `Status.json` flag. The current proc's `wait s=13.0` star-clearance is TRAJECTORY PACING, not a success gate; any retained `wait Ns` MUST be flagged "pacing, not a gate" per master-spec standing rule.
- **INV-4 (cooldown precedes any FSD press).** The smack FSD cooldown (`Status` bit 18, binary, no duration) MUST be cleared (`wait_cooldown_clear`) BEFORE any `engage_*`. The cooldown is journal-blind; only the Status bit reports it.
- **INV-5 (escape-vector dependency is ENTRY-ONLY).** This scene does not call the escape-vector detector in-scene. It assumes STARSMACK was entered on a CV-confirmed smack. Any in-scene re-confirmation is OUT OF SCOPE (G2).
- **INV-6 (C1 contract fidelity).** `nav_target_star` / `nav_supercruise_star` are consumed exactly per C1's published contract. Any behaviour C4 needs that C1 does not yet guarantee → `BLOCKED-ON-CONTRACT(C1)` note, NOT a guess.
- **INV-7 (idempotent / restart-safe).** A bot restarted mid-recovery must not double-lock or throttle-into-star. `nav_target_star` is idempotent by C1 contract; `pitch_compass` re-certifies; `engage_supercruise` no-ops if already in SC. Preserve the current proc's state-aware retry split (real-space fail → restart at throttle-0/100; in-SC fail → resume at the hop lock).
- **INV-8 (escape-vector ALIGN-AND-HOLD is the SC-entry path, not a hyperspace jump).** Per memories `smack-escape-vector-recovery` / `smack-journal-blind-vision-discriminator`: a post-smack `engage_supercruise(until_charging)` spawns the BLUE/CYAN escape vector; the ship must ALIGN to it and HOLD to `SupercruiseEntry`. Re-pressing a live charge CANCELS it. Step 6 (`engage_supercruise`, LOCKED) honours this mechanic; a raw `Hyperspace`/K press into the exclusion zone would be wrong by game-truth and is rejected.

---

## 3. ACCEPTANCE CRITERIA

- **AC-1.** The C4 candidate is a `smack_recovery.toml` SKETCH embedded in the design doc. (Building is now
  authorized per L5; a Stage-1 build candidate MAY also produce the real `.toml` — but the design
  deliverable itself is the embedded sketch + blocker list.)
- **AC-2.** The sketch realises all 8 authored steps in order, mapping each to a named action (existing or C1-contract), with no invented action that lacks a C1 contract or a `BLOCKED-ON-KYLE`.
- **AC-3 (REVISED — step 6 is LOCKED).** Step 6 is documented as `engage_supercruise` (Key_J), settled —
  re-enter SC, align-and-hold the spawned escape vector to `SupercruiseEntry`. The doc states explicitly
  that `engage_jump_clearance` (Hyperspace/K) was the rejected token, NOT the chosen action. A candidate
  that RE-OPENS step 6 as an unresolved `BLOCKED-ON-KYLE`, or picks `engage_jump_clearance`, FAILS
  spec-conformance (routes to Stage 0). *(This inverts the original AC-3, which required step 6 stay
  unresolved — that requirement was struck per L1.)*
- **AC-4.** Every smack-glare guard from the current proc (`behind_confirm_reads`, `behind_fill_max`, the
  `_supercruise_lost_guard` semantics, the escape-vector ALIGN-AND-HOLD ladder) is preserved or its
  removal justified against the cited memories. Silent loss of INV-1/INV-8 guards FAILS.
- **AC-5.** Every gate is event/Status-flag (INV-3). Any retained `wait Ns` is labelled pacing-not-gate.
- **AC-6.** `nav_target_star` / `nav_supercruise_star` are cited as C1 deliverables with a contract-assumption list; the design does not assume capabilities C1 has not published. Unsettled C1 surface → `BLOCKED-ON-CONTRACT(C1)`.
- **AC-7.** The `→ Traversal` transition is delegated to C2's section-transition mechanism (Python
  orchestrator `transition_to`), not hand-rolled.
- **AC-8.** A consolidated `BLOCKED-ON-KYLE` / `BLOCKED-ON-CONTRACT` list is present (step 6 is NO LONGER
  on it) and includes every unknown surfaced in §5.
- **AC-9.** The design preserves the entry-only escape-vector dependency (INV-5): no in-scene call to the
  escape-vector detector; the scene assumes confirmed entry.
- **AC-10.** Diff hygiene: a DESIGN deliverable is docs-only; a Stage-1 BUILD deliverable (now authorized)
  may touch `smack_recovery.toml` + step code, fail-closed, with the build councils' gates.

---

## 4. STEP 6 — RESOLVED/LOCKED (was "THE STEP-6 CONFLICT")

**RESOLVED (operator 2026-06-18): step 6 = `engage_supercruise` (Key_J).** The original draft framed an
unresolved conflict between the authored token `engage_jump_clearance` and the "(enter supercruise)" note.
That conflict is closed in favour of `engage_supercruise`. For the record of why the two are different
mechanics (so a builder never substitutes one for the other):

- `engage_jump_clearance` (`ed_autojump/flow/steps.py`) is the **HYPERSPACE** clearance loop: it presses
  `SetSpeed100` + `Hyperspace` (Key_K), bounded-polls for a `StartJump` into witchspace, and on
  obstruction pitches away + burns + retries. It is the route-system jump path. **It is NOT step 6.**
- `engage_supercruise` is the **SUPERCRUISE** entry mechanic: press `Supercruise` (Key_J) →
  `SupercruiseEntry`. A post-smack SC charge spawns the **escape vector** the ship must align to
  (game-truth, memories above). **This IS step 6.** A hyperspace `Hyperspace`/K press inside the
  exclusion zone does NOT produce the escape vector and is wrong by game-truth.

The two use **different binds** (J vs K) and **different game mechanics**; they are not interchangeable.
The smack-recovery step 6 is the SC re-entry (J). This is no longer a `BLOCKED-ON-KYLE`.

> **(STRUCK — Claude error.)** ~~BLOCKED-ON-KYLE #1 (HEADLINE): Step 6 says `engage_jump_clearance` but is
> annotated "(enter supercruise)"…~~ — RESOLVED above; the routine is law, step 6 is `engage_supercruise`.

---

## 5. CONSOLIDATED BLOCKED-ON-KYLE / BLOCKED-ON-CONTRACT LIST

1. **(RESOLVED — struck.)** ~~BLOCKED-ON-KYLE #1 (headline): step-6 `engage_jump_clearance` vs "(enter
   supercruise)".~~ Step 6 = `engage_supercruise` (Key_J), LOCKED 2026-06-18 (§4). No longer a blocker.
2. **BLOCKED-ON-CONTRACT(C1).** `nav_target_star` exact contract: idempotent single-lock semantics, the `UNLOCK DESTINATION` already-locked detection, and the `Status.Destination == system` verification predicate must be PUBLISHED by C1 before C4 can pin step 2.
3. **BLOCKED-ON-CONTRACT(C1).** `nav_supercruise_star` exact contract: detail-page SUPERCRUISE-ASSIST single-press, success predicate (SC-assist active? `SupercruiseEntry`? a HUD CV?), and fail-closed behaviour — needed to pin step 7.
4. **BLOCKED-ON-CONTRACT(C2).** Section-transition mechanism for `→ Traversal` (step 8) — RESOLVED to the
   Python orchestrator `transition_to(runner, "traversal")`; the exact marker/return form C4 emits is
   pinned at design-merge with C2.
5. **BLOCKED-ON-KYLE.** Smack-recovery RETRY policy under the new actions: the current proc splits real-space-fail (→ throttle-0/100 restart) vs in-SC-fail (→ hop lock). Does the redesigned 8-step flow keep that split, and where is the in-SC anchor now that the explicit hop-lock step is gone from the authored sequence?
6. **BLOCKED-ON-KYLE.** Does step 7 `nav_supercruise_star` REPLACE the escape-vector ALIGN-AND-HOLD ladder, or run AFTER it? In the current proc, SC entry happens via the escape-vector dance (step 6, `engage_supercruise`), and `nav_supercruise_star` is a DIFFERENT thing (SC-assist toward the star, already in SC). Confirm the ordering: enter-SC (escape vector) THEN SC-assist-to-star.
7. **BLOCKED-ON-KYLE.** Planet-smack vs star-smack: `smack_kind` can be `planet` (purple vector). The
   master-spec LOCKED note says planet-smack is the same procedure parameterized, EXCEPT the compass is
   usable the whole time (no flip-about — a planet has no superbright glare). Confirm
   `nav_target_star`/`nav_supercruise_star` (star-specific) behaviour for a planet-smack.
8. **NOTE (no guess).** `escape_vector.py` is a fail-closed STUB; if the live flow ever needs in-scene CV here it is blocked on G2 calibration frames (out of C4 scope, recorded for traceability).

---

## 6. EXECUTABLE ACCEPTANCE TESTS

These check the spec/design artifact + the live entry-routing truth. Run from repo root
`<repo-root>\ED-AFK`.

### T-1 (AC-1/AC-10): the C4 design doc names all 8 steps with step 6 = engage_supercruise.
```bash
DOC="docs/superpowers/specs/council-briefs/C4-smack-recovery-CANDIDATE.md"   # the C4 deliverable
for s in set_throttle nav_target_star pitch_compass target_ahead wait_cooldown_clear engage_supercruise nav_supercruise_star Traversal; do
  grep -q "$s" "$DOC" || echo "MISSING_STEP:$s"
done
```

### T-2 (AC-3, REVISED): step 6 is LOCKED to engage_supercruise, NOT an open blocker.
```bash
DOC="docs/superpowers/specs/council-briefs/C4-smack-recovery-CANDIDATE.md"
# Step 6 must be engage_supercruise and must NOT be re-opened as a live BLOCKED-ON-KYLE.
grep -qi "engage_supercruise" "$DOC" \
  && ! ( grep -ni "BLOCKED-ON-KYLE" "$DOC" | grep -i "step.*6\|engage_jump_clearance" | grep -vi "resolved\|locked\|struck\|was\|rejected" ) \
  && echo "STEP6_LOCKED_OK" || echo "STEP6_REOPENED_FAIL"
```

### T-3 (AC-4/INV-1/INV-8): smack-glare + escape-vector guards survive.
```bash
DOC="docs/superpowers/specs/council-briefs/C4-smack-recovery-CANDIDATE.md"
for g in behind_confirm_reads behind_fill_max "escape vector" SupercruiseEntry; do
  grep -qi "$g" "$DOC" || echo "GUARD_DROPPED_OR_UNJUSTIFIED:$g"
done
```

### T-4 (AC-5/INV-3): no NEW wall-clock success gate; any wait is labelled pacing.
```bash
DOC="docs/superpowers/specs/council-briefs/C4-smack-recovery-CANDIDATE.md"
grep -nE 'wait.*s *= *[0-9]' "$DOC" | grep -vi 'pacing\|not a gate\|trajectory' \
  && echo "UNLABELLED_TIMED_WAIT" || echo "NO_UNLABELLED_WAIT_GATE_OK"
```

### T-5 (entry-precondition truth — binds are distinct, J≠K): step 6 uses J, not K.
```bash
# Proves Supercruise (J) and Hyperspace (K) are SEPARATE required binds — step 6 is the J path.
rg -n '"Supercruise"|"Hyperspace"' projects/ed-core/src/ed_core/binds_validate.py
```

### T-6 (INV-5/AC-9): the scene does NOT call the escape-vector detector in-scene.
```bash
DOC="docs/superpowers/specs/council-briefs/C4-smack-recovery-CANDIDATE.md"
grep -qi 'detect_escape_vector' "$DOC" && echo "IN_SCENE_DETECTOR_CALL_VIOLATES_INV5" \
  || echo "ESCAPE_VECTOR_ENTRY_ONLY_OK"
```

### T-7 (entry routing truth): STARSMACK abstains on bare telemetry (scene only runs CV-confirmed).
```bash
rg -n 'smack_kind in \{"star", "planet"\}|return None' projects/ed-core/src/ed_core/boot/scenes.py
```

PASS condition: T-5, T-7 pass against the live repo today; T-1…T-4, T-6 pass against the C4 CANDIDATE
design doc once it exists. Step 6 is LOCKED — a candidate that re-opens it FAILS spec-conformance.
