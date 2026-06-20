<!-- C4 SMACK RECOVERY SCENE — Stage-1 BUILD CANDIDATE | candidate gen-opus-1 | 2026-06-19 -->
<!-- DESIGN deliverable = docs-only. This candidate ALSO ships the real .toml (AC-1 permits). NO COMMIT by council agents (AC-10). -->

# C4 — Smack Recovery Scene (CANDIDATE)

## 0. What this is

The redesigned BODY of `smack_recovery` — the procedure the C-series boot
router runs AFTER it has latched **STARSMACK**. This candidate realizes the
operator's **LOCKED 8-step flow** (verbatim, 2026-06-18) as:

- **(a)** a rewritten `projects/ed-autojump/procedures/smack_recovery.toml`
  (shipped alongside this doc — AC-1 permits a Stage-1 build candidate to
  produce the real `.toml`), and
- **(b)** this design doc, with an embedded `.toml` SKETCH (§3) and a
  consolidated BLOCKED list (§5).

C4 is the procedure body ONLY. It is **NOT** the smack detector. Entry is gated
upstream by `_route_sc_exit` calling the **escape vector** detector (track G2,
`ed_vision/escape_vector.py`, a fail-closed STUB) which sets `smack_kind`;
`_det_starsmack` then returns True only when `smack_kind in {"star","planet"}`.
That gate is OUT OF SCOPE here (INV-5). C4 depends on it ONLY for entry.

## 1. Scope boundary & contracts (verified against the live repo)

| Concern | Verified source-of-truth |
|---|---|
| Scene template wiring | `projects/ed-core/src/ed_core/boot/scenes.py` — `SceneTemplate(state=STARSMACK, determine=_det_starsmack, proc="smack_recovery")`. NOT renamed, NOT rewired. |
| Entry abstains on bare telemetry | `_det_starsmack`: `if ctx.smack_kind in {"star", "planet"}: return True` … `if ctx.smacked: return None` (CV-pending abstain). |
| Binds J != K | `ed_core/binds_validate.py` REQUIRED_ACTIONS: `"Hyperspace"` (Key_K) and `"Supercruise"` (Key_J) are DISTINCT REQUIRED binds. |
| Existing steps | `ed_core/flow/steps_shared.py`: `set_throttle`, `pitch_compass`, `target_ahead`, `wait_cooldown_clear`, `engage_supercruise`, `orient_compass`, `hold_alignment` all registered on import. |
| C1 actions | `nav_target_star`, `nav_supercruise_star` DO NOT EXIST — owned by C1 (`council-briefs/C1-cv-action-family.md`). |
| Transition | `ed_autojump/flow/boot_routes.py`: `transition_to(runner, section)` + `_SECTION_TO_PROC = {"docking":"dock","exploration":"exploration","traversal":"traversal"}` — LIVE/built. |

## 2. The 8 authored steps (operator verbatim, LOCKED) — step → action map

| # | Authored step | Action token | Status | Notes |
|---|---|---|---|---|
| 1 | set_throttle 100 | `set_throttle` (pct=100) | EXISTS (`steps_shared.step_set_throttle`, SetSpeed100) | full burn back out of the exclusion zone |
| 2 | nav_target_star | `nav_target_star` | **BLOCKED-ON-CONTRACT(C1)** | idempotent SINGLE lock of main star; detect already-locked via `UNLOCK DESTINATION` label; verify `Status.Destination==system`. NO GUESS beyond C1 brief. |
| 3 | pitch_compass | `pitch_compass` (until="behind") | EXISTS (`steps_shared.step_pitch_compass`) | carries `behind_confirm_reads=3`, `behind_fill_max=0.30`, `center_frac=0.35`, `timeout_s`/`max_iters` FAILSAFE ceilings |
| 4 | target_ahead | `target_ahead` | EXISTS (`steps_shared.step_target_ahead`, SelectTarget) | star astern, nothing ahead → deselect |
| 5 | wait_cooldown_clear | `wait_cooldown_clear` | EXISTS (`steps_shared.step_wait_cooldown_clear`) | blocks on Status `FsdCooldown` bit 18; fail-closed without status |
| 6 | **engage_supercruise** | `engage_supercruise` | **EXISTS / LOCKED** (`steps_shared.step_engage_supercruise`) | Key_J. `until_charging=true` spawns the escape vector → ALIGN-AND-HOLD → `SupercruiseEntry`. SEE §4. |
| 7 | nav_supercruise_star | `nav_supercruise_star` | **BLOCKED-ON-CONTRACT(C1)** | opens star-row detail page, presses SUPERCRUISE ASSIST button ONCE; replaces blind `sc_assist_orbit` |
| 8 | → **Traversal** | `transition_to(runner,"traversal")` | EXISTS (C2 orchestrator) | NOT a TOML step (no successor field in `model.Step`); emitted at the smack dispatch site. SEE §6. |

Every step maps to a named action that EXISTS, or to a C1 contract action with
a BLOCKED entry (§5). No invented action lacks a contract or a blocker (AC-2).

## 3. Embedded `.toml` SKETCH (AC-1)

```toml
[on_required_fail]
# Real-space fail -> restart at step 0; in-SC fail -> resume at the post-SC
# anchor (nav_supercruise_star), NOT the real-space ladder (the 14:24Z all-zero
# burn: real-space retry deselected the target + the in-SC engage refused).
retry_from = "set_throttle"
retry_from_if_supercruise = "nav_supercruise_star"
max_retries = 3
backoff_s = 2.0

steps = [
  # 1 full burn out of the exclusion zone
  { action = "set_throttle", pct = 100 },

  # 2 lock the main star [C1 CONTRACT: nav_target_star] — idempotent single
  #   select; UNLOCK DESTINATION label => already locked (no-op); verify
  #   Status.Destination == system.
  { action = "nav_target_star", required = true, verify_destination = true },

  # 3 pitch the star 180 astern — glare-hardened (behind_confirm_reads=3,
  #   behind_fill_max=0.30); timeout_s/max_iters are FAILSAFE ceilings, not gates.
  { action = "pitch_compass", until = "behind", required = true,
    center_frac = 0.35, timeout_s = 75.0, max_iters = 40,
    behind_confirm_reads = 3, behind_fill_max = 0.30 },

  # 4 deselect — star astern, nothing ahead (T clears)
  { action = "target_ahead" },

  # 5 smack cooldown — Status FsdCooldown bit 18, flag-gated, no timer
  { action = "wait_cooldown_clear", required = true },

  # 6 engage_supercruise (Key_J) — LOCKED. until_charging=true => success is a
  #   LIVE CHARGE that spawns the escape vector. NOT engage_jump_clearance (K).
  { action = "engage_supercruise", required = true, until_charging = true,
    presses = 3, between_press_s = 15.0, max_charge_s = 240.0 },

  # 6b center + hold the spawned escape vector to SupercruiseEntry (the
  #    ALIGN-AND-HOLD ladder). hold_alignment is purely event/state-gated.
  { action = "orient_compass", required = true },
  { action = "hold_alignment", until_event = "SupercruiseEntry", required = true },

  # 7 SUPERCRUISE ASSIST on the star row [C1 CONTRACT: nav_supercruise_star];
  #   retry_anchor: in-SC fails resume HERE.
  { action = "nav_supercruise_star", required = true, retry_anchor = true },

  # 8 -> Traversal is NOT a step: emitted by transition_to(runner,"traversal").
]
```

The shipped `projects/ed-autojump/procedures/smack_recovery.toml` is this sketch
with full inline rationale comments (operator memories cited per step).

## 4. Step 6 — LOCKED to engage_supercruise (AC-3)

**Step 6 is `engage_supercruise` (Key_J / the Supercruise bind). It is NOT an
open blocker. The REJECTED token at step 6 was `engage_jump_clearance`
(Hyperspace / Key_K, the hyperspace clearance loop).**

- `engage_supercruise` re-enters supercruise from normal space. With
  `until_charging=true`, success is a **LIVE CHARGE**, and that charge spawns the
  **BLUE/CYAN escape vector** the ship must ALIGN-AND-HOLD; `orient_compass`
  centers it and `hold_alignment until_event="SupercruiseEntry"` rides it to
  `SupercruiseEntry`.
- `engage_jump_clearance` (Key_K, Hyperspace) is a DISTINCT bind with DISTINCT
  mechanics (hyperspace jump clearance). It was **REJECTED** at step 6 and is
  named here ONLY to record what was struck. J and K are distinct REQUIRED binds
  (`binds_validate.py`).

This is operator-LOCKED (2026-06-18) and is NOT re-opened. Re-opening step 6 as
an unresolved BLOCKED-ON-KYLE, or selecting engage_jump_clearance, FAILS
spec-conformance and routes to Stage 0.

## 4a. Smack-glare / escape-vector guards preserved (AC-4 / INV-1 / INV-8)

Every guard from the current proc survives, justified against the cited memories:

- **`behind_confirm_reads = 3`** — 3 CONSECUTIVE behind-gate beats before
  certifying astern (the 2026-06-08 false-pass refix: the smack star is always
  in front + bright, so one hollow+centered read is glare noise). PRESERVED.
- **`behind_fill_max = 0.30`** — a beat only qualifies when DECISIVELY hollow
  (front_fill ≤ 0.30, below the 0.35 band floor); a glare-bright front star
  sits in/above the uncertainty band and can never qualify. PRESERVED.
- **`_supercruise_lost_guard` semantics** — the vision steps (`pitch_compass`,
  `orient_compass`, `hold_alignment`) carry the asymmetric SC-lost guard from
  `steps_shared`: it arms ONLY when a step STARTS in supercruise, so the
  post-smack escape-vector orient (which starts in NORMAL space and GAINS SC as
  success) runs unguarded by design. PRESERVED (inherited from the step impls).
- **escape vector ALIGN-AND-HOLD ladder** — `engage_supercruise until_charging`
  → `orient_compass` (center the spawned vector) → `hold_alignment` to
  `SupercruiseEntry`. PRESERVED.

Removed from the v7 body, justified:
- The old in-SC TAIL (`target_next_route` → `set_throttle 100` → `wait 13s` →
  `orient_compass` → `orient_widget_ring` → `engage_jump` → `hold_alignment`)
  is REMOVED because step 7 (`nav_supercruise_star`) replaces the blind
  `sc_assist_orbit`/jump tail per the operator's authored flow, and step 8 hands
  off to **Traversal**, which OWNS the hop (its own target/orient/jump ladder).
  This is not a silent guard loss — the jump-leg guards now live in `traversal`.

## 4b. Gates are event/Status-flag only (AC-5 / INV-3)

No NEW wall-clock SUCCESS gate is introduced. Every gate is a journal event or a
Status flag:
- step 5 `wait_cooldown_clear` → `FsdCooldown` bit 18.
- step 6 `engage_supercruise` → `FsdCharging` / `SupercruiseEntry` /
  `in_supercruise`; `max_charge_s`/`between_press_s` are the operator-sanctioned
  wedged-FSD watchdog + re-press cadence (NOT success gates).
- step 6b `hold_alignment` → `SupercruiseEntry` event / `in_supercruise` flag,
  purely event-driven.
- The v7 `{ action = "wait", s = 13.0 }` trajectory-pacing step is GONE (it
  lived in the removed jump tail); no unlabelled `wait Ns =` survives in C4.

## 5. Consolidated BLOCKED-ON-KYLE / BLOCKED-ON-CONTRACT (AC-6 / AC-8)

**Step 6 is NOT on this list — it is LOCKED (engage_supercruise).**

- **BLOCKED-ON-CONTRACT(C1): `nav_target_star`** — does not exist; owned by C1
  (details-page button-bar CV-nav substrate). Contract assumed: idempotent
  SINGLE lock of the main star; detect already-locked via the `UNLOCK
  DESTINATION` label; verify `Status.Destination == system`. Designed against
  the published C1 brief only.
- **BLOCKED-ON-CONTRACT(C1): `nav_supercruise_star`** — does not exist; owned by
  C1. Contract assumed: open the star-row detail page, press SUPERCRUISE ASSIST
  ONCE (not the lock button).
- **BLOCKED-ON-CONTRACT(C2): → Traversal marker form** — realized today as
  `transition_to(runner,"traversal")` at the smack dispatch site (built,
  `boot_routes.py`). The MARKER form a standalone `.toml` should emit (if any)
  is C2-owned; a `.toml` has no successor field, so C4 emits NO TOML goto.
- **BLOCKED-ON-KYLE: retry-split + in-SC anchor location** — the v7 retry split
  ("fail in real space → 0, fail in SC → the hop") is carried as
  `retry_from = "set_throttle"` + `retry_from_if_supercruise =
  "nav_supercruise_star"` + `retry_anchor` on step 7. Confirm the anchor lands
  on `nav_supercruise_star` in the new 8-step shape.
- **BLOCKED-ON-KYLE: step-7-vs-escape-vector ordering** — is
  `nav_supercruise_star` pressed AFTER `SupercruiseEntry` (current authored
  order), or can SC-assist be armed while the escape-vector charge is still
  live? Authored order kept (7 after 6).
- **BLOCKED-ON-KYLE: planet-smack vs star-smack parameterization** — are the
  pitch / escape-vector params identical for `smack_kind=="planet"` vs
  `"star"`? Single param set kept; `smack_kind` is known at entry but unused
  in-body until the operator distinguishes.
- **NOTE(G2): `escape_vector.py` stub** — the escape-vector detector is a
  fail-closed STUB on track G2. It backs the ENTRY gate (INV-5) and is OUT OF
  SCOPE for C4. C4 never imports or calls it.

## 6. → Traversal delegated to C2 (AC-7)

Smack → Traversal is an **UNCONDITIONAL** `transition_to(runner, "traversal")`.
It is delegated to the C2 orchestrator, NOT hand-rolled in C4:

- Proc→proc chaining does not exist in the interpreter/loader/model (`model.Step`
  has no successor field). The C2 design (`2026-06-17-flow-redesign-C2-control-
  flow-DESIGN.md`) chose a Python successor map + `transition_to`, built live in
  `boot_routes.py` (`_SECTION_TO_PROC["traversal"] == "traversal"`).
- `transition_to` returns the dispatched proc name, or `""` on abort / unknown /
  unloaded section — `""` is a NAMED operator abort (fail-closed), never a blank
  run. C4 emits the transition marker conceptually; the dispatcher hop is C2's.

## 7. INV-5 — entry-only escape-vector dependency (AC-9)

C4 makes **no in-scene call to the escape-vector detector**. Neither the doc nor
the `.toml` invokes the G2 detector function. The escape-vector detector is an
ENTRY-ONLY dependency, consumed by `_route_sc_exit` BEFORE STARSMACK latches.
The only in-scene reference to the phrase "escape vector" is the BLUE/CYAN
compass target that the charge spawns (§4), which `orient_compass`/
`hold_alignment` read as an ordinary compass dot — NOT a call into the detector.

## 8. AC-10 diff hygiene / NO COMMIT

This is a Stage-1 BUILD candidate: it touches `smack_recovery.toml` (body
rewrite, fail-closed) + this doc. No step CODE is changed (all existing steps
reused as-is; the two C1 actions are referenced, not implemented — they land
under C1's gate). NO COMMIT by council agents.

## 9. §6 acceptance-test mapping

| Test | Target | Why it passes |
|---|---|---|
| T-1 | this doc | all 8 step tokens present: `set_throttle`, `nav_target_star`, `pitch_compass`, `target_ahead`, `wait_cooldown_clear`, `engage_supercruise`, `nav_supercruise_star`, `Traversal` |
| T-2 | this doc | `engage_supercruise` present; step 6 NOT an open BLOCKED-ON-KYLE and NOT engage_jump_clearance (the only engage_jump_clearance mentions are tagged REJECTED/struck) |
| T-3 | this doc | guards present: `behind_confirm_reads`, `behind_fill_max`, `escape vector`, `SupercruiseEntry` |
| T-4 | this doc | no unlabelled `wait Ns =`; the removed 13s wait is gone |
| T-5 | live repo | `binds_validate.py` has distinct `"Supercruise"` / `"Hyperspace"` |
| T-6 | this doc | no in-scene call to the G2 detector function (the doc never names it) |
| T-7 | live repo | `scenes.py` `_det_starsmack` abstains (`return None`) on bare telemetry |
