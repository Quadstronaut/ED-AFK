# DOC RECONCILIATION (2026-06-18) — STAGE-0 SPEC

Arbiter-authored Stage-0 spec for the council that reconciles ALL current ED-AFK documentation
against the operator's now-LOCKED 2026-06-18 decisions, the live repo state, and the desired
end-state. **DOCS ONLY** — no `.py`, no `.toml`, no `.binds`, no flight-code. Downstream stages
(generation, adversarial review, arbitration) are judged against this doc.

Ground truth read for this spec: every in-scope doc was read in full and grepped; the live repo
state is taken from `docs/superpowers/specs/2026-06-18-AUDIT-INVENTORY.md` (point-in-time snapshot,
**MUST NOT be edited**) and the actual files. Repo root for all commands:
`<repo-root>\ED-AFK`.

---

## 0. WHAT THIS TASK IS (and what it is NOT)

**IS:** apply 6 locked operator decisions across the AUTHORITATIVE/CURRENT redesign docs so the
documentation stops contradicting the operator's settled intent. The deliverable is the **actual doc
edits in the worktree** + a list of every file changed + every doc flagged superseded.

**IS NOT:** a rewrite of history (HISTORICAL/SUPERSEDED docs are only *marked* superseded, never
re-narrated); a code/flow change (zero non-`.md` edits); an invention of any decision not in the
locked list (anything else → `BLOCKED-ON-KYLE`, never guessed).

The single hardest correction is **decision #1**: the C4 smack routine is now LAW, and the prior
"verbal-vs-written contradiction / DEVIATION-BLOCKED" framing was a **Claude error** that must be
STRUCK everywhere — not softened, not hedged, not re-questioned.

---

## 1. INTERFACE

### 1.1 The deliverable (what a candidate produces)
A set of `.md` edits confined to the in-scope file list (§1.2), plus two report sections **inside the
candidate's own design/answer doc** (NOT a new tracked repo file unless it is the spec/brief itself):
1. **FILES-CHANGED** — every doc touched, absolute path, one-line summary of the edit class applied.
2. **SUPERSEDED-FLAGGED** — every HISTORICAL doc that was confirmed-or-made to carry a superseded
   marker, absolute path, and whether the marker was already present or added.
3. **BLOCKED-ON-KYLE** — every needed change NOT covered by the locked list (§1.4), surfaced not
   invented.

### 1.2 In-scope files (AUTHORITATIVE / CURRENT — these get CONTENT edits)
The reconciliation target set. Every path is under
`docs/superpowers/specs/` unless noted; all absolute under the repo root.

- `2026-06-17-flow-redesign-MASTER-SPEC.md` — the operator's source-of-truth spec.
- `2026-06-17-flow-redesign-CONSOLIDATED-BLOCKERS.md` — the 7-council blocker ledger.
- `council-briefs/C1-cv-action-family.md`
- `council-briefs/C2-control-flow.md`
- `council-briefs/C3-arrival.md`
- `council-briefs/C4-smack-recovery.md`
- `council-briefs/C4-smack-recovery-STAGE0-SPEC.md` *(currently UNTRACKED per git status — this is
  the very doc that carries the to-be-struck contradiction framing; reconcile its content, it will be
  committed as part of this task)*
- `council-briefs/C5-traversal.md`
- `council-briefs/C6-exploration.md`
- `council-briefs/C7-docking.md`
- `2026-06-17-flow-redesign-C1-cv-action-family-DESIGN.md`
- `2026-06-17-flow-redesign-C2-control-flow-DESIGN.md`
- `2026-06-17-flow-redesign-C3-arrival-DESIGN.md`
- `2026-06-17-flow-redesign-C4-smack-recovery-DESIGN.md`
- `2026-06-17-flow-redesign-C5-traversal-DESIGN.md`
- `2026-06-17-flow-redesign-C6-exploration-DESIGN.md`
- `2026-06-17-flow-redesign-C7-docking-DESIGN.md`
- `README.md` (repo root) — only if a locked decision actually contradicts it (see §3; current read
  shows README is generic and likely needs NO edit — do not invent one).

### 1.3 Frozen file (MUST NOT be touched)
- `docs/superpowers/specs/2026-06-18-AUDIT-INVENTORY.md` — point-in-time snapshot. Read-only ground
  truth. Any edit to it FAILS the gate.

### 1.4 The 6 LOCKED operator decisions (the ONLY authorized content changes)
Verbatim source of authority. A candidate may make exactly the edits these license, plus mechanical
superseded-marking (§1.5). Anything else → `BLOCKED-ON-KYLE`.

- **L1 — SMACK ROUTINE IS LAW.** Resolve C4 `DEVIATION-BLOCKED` → `LOCKED/RESOLVED` everywhere. The
  confirmed law, verbatim and in order: (1) `set_throttle 100` (2) `nav_target_star` (3) `pitch_compass`
  [keep smack-glare guards] (4) `target_ahead` (5) `wait_cooldown_clear` (6) `engage_supercruise`
  (Key_J — re-enter SC; spawns the escape vector; align-and-hold to `SupercruiseEntry`; **NOT**
  `engage_jump_clearance`/Key_K) (7) `nav_supercruise_star` (8) → Traversal. **STRIKE** the
  "verbal-vs-written contradiction" / "DEVIATION-BLOCKED" / "target NOTHING" / "DO-NOT-RE-FIRE"
  framing as a CLAUDE ERROR — there is no contradiction; step 6 is settled; do not re-open or hedge.
- **L2 — EXPLORATION is permanent IN-SCOPE.** Treat the Exploration scene + `nav_supercruise_unexplored`
  as first-class scope, not a side-quest.
- **L3 — FULL-CV is the committed direction.** Drop any hedging that the community blind-keypresses the
  nav panel / that CV is too risky. State full CV as THE chosen approach.
- **L4 — NFZ ≠ docking distance.** The no-fire zone is a fire-safety zone LARGER than the docking zone;
  it is NOT the docking-readiness gate. Docking trigger = OCR proximity < 7.5 km; the
  `$STATION_NoFireZone_entered` journal event is a SEPARATE fire-safety concern. Keep both, distinct.
  CORRECT any doc that conflates them or frames the OCR loop as "replacing" the NFZ gate (Claude
  hallucination — strike it).
- **L5 — DESIGN-ONLY IS LIFTED.** The master-spec STANDING RULE "DESIGN-ONLY / live flight path
  untouched until sign-off" is superseded — building is authorized (C5 Traversal + C2 orchestrator
  code councils are in flight). Update standing-rules language: ratified scenes are now being BUILT.
  **KEEP** the no-guessing + fail-closed rules; ONLY the no-build clause is lifted.
- **L6 — PIP MANAGEMENT is being RESTORED.** It was ripped 2026-06-08 ("we're scrapping it"); the
  operator reversed that. `pips.md` (repo root) is the placement/wiring reference (Left=SYS, Up=ENG,
  Right=WEP, Down=reset). Docs claiming pips are permanently scrapped must note: restoration pending,
  placement TBD by operator.

### 1.5 Superseded-marking (mechanical, allowed on HISTORICAL docs)
For HISTORICAL/SUPERSEDED docs (everything NOT in §1.2 and NOT the frozen file), the ONLY allowed
edit is ensuring a clear superseded/historical marker exists at the top if one is missing. Do NOT
rewrite their bodies. If a historical doc already carries a marker, leave it. This is the
"don't rewrite history" rule.

---

## 2. INVARIANTS (must hold in any candidate)

- **INV-1 (docs-only).** `git status --porcelain` shows changes to `*.md` files ONLY. Zero `.py`,
  `.toml`, `.binds`, `.json`, or any non-`.md` path. [executable]
- **INV-2 (frozen file untouched).** `2026-06-18-AUDIT-INVENTORY.md` has zero diff. [executable]
- **INV-3 (no invention / ground every edit).** Every content change traces to exactly one of L1–L6
  (or §1.5 superseded-marking). A change not so traceable is a guess and is forbidden → must instead
  be a `BLOCKED-ON-KYLE` line. The candidate's FILES-CHANGED report cites the governing L# per edit.
- **INV-4 (L1 fully propagated, contradiction framing eradicated).** After the edit, NO in-scope doc
  retains the strings `DEVIATION-BLOCKED`, `DO NOT RE-FIRE` / `DO-NOT-RE-FIRE`, `target NOTHING` /
  `target nothing` (as the smack routine), or "verbal-vs-written contradiction" framing as a LIVE
  status. (Past-tense, struck-through, or "(corrected: was a Claude error)" annotations are
  acceptable — the test checks these tokens are not presented as a current blocker.) Step 6 reads
  `engage_supercruise` (Key_J) as settled, not as an open `BLOCKED-ON-KYLE`. [executable, see T-3]
- **INV-5 (L1 keeps the safety guards).** The smack-glare guards (`behind_confirm_reads`,
  `behind_fill_max`) and the escape-vector ALIGN-AND-HOLD-to-`SupercruiseEntry` mechanic remain
  documented in the C4 docs. L1 resolves a STATUS, it does not delete the ship-safety content.
  [executable, see T-7]
- **INV-6 (L5 lifts ONLY the no-build clause).** Wherever "DESIGN-ONLY" governed *building*, the doc
  now states building is authorized; but `NO GUESSING` and fail-closed survive verbatim. A candidate
  that strips no-guessing or fail-closed FAILS. [executable, see T-6]
- **INV-7 (historical docs not re-narrated).** No HISTORICAL doc (§1.5 set) has body content rewritten;
  only a top-of-file superseded marker may be added. The diff to any historical doc is marker-only.
  [reviewer-checked; T-2 bounds the touched set]
- **INV-8 (each locked decision actually lands).** Every one of L1–L6 produces at least one concrete
  edit in at least one in-scope doc where that decision currently has a live contradiction. A
  decision with a live contradiction that is left unedited is an omission failure. [executable per-L
  presence checks, T-3…T-8]
- **INV-9 (no contradiction injected).** The edits do not introduce a NEW conflict between two
  in-scope docs (e.g. master spec says `engage_supercruise` but a brief still says the step-6 verb is
  unknown). After the pass, the C4 step-6 statement is identical-in-substance across MASTER-SPEC,
  CONSOLIDATED-BLOCKERS, both C4 briefs, and the C4 DESIGN doc. [reviewer-checked + T-3]
- **INV-10 (citations present).** The FILES-CHANGED report lists every touched file with its governing
  L# (or "superseded-marker"). A touched file absent from the report, or a reported file not actually
  touched, FAILS. [executable cross-check, T-9]

---

## 3. PER-DECISION EDIT MAP (grounded; the WHAT, candidates own the exact wording)

This is the verified contradiction surface from reading every in-scope file. A candidate MUST address
each live contradiction below; it MAY find additional in-scope occurrences (grep is authoritative).
Line numbers are as-read 2026-06-18 and are guidance, not contracts.

**L1 (smack LAW) — the largest surface:**
- `council-briefs/C4-smack-recovery.md` lines 3–10: the `⛔ DO NOT RE-FIRE — DEVIATION-BLOCKED` banner
  + the "fresh verbal routine … CONFLICTS with the authored scope … AND with live memory" paragraph.
  STRIKE/replace with a LOCKED/RESOLVED status carrying the 8-step law; keep the scope body (which is
  already the authored 8 steps) but resolve step 6 from `engage_jump_clearance`/BLOCKED to
  `engage_supercruise` (Key_J) settled.
- `council-briefs/C4-smack-recovery-STAGE0-SPEC.md`: title line 1 "(DESIGN-ONLY)"; §1.2 step 6 line 44
  "`engage_jump_clearance` … **BLOCKED-ON-KYLE #1 (headline)**"; the whole of §4 "THE STEP-6 CONFLICT";
  §5 item 1 "BLOCKED-ON-KYLE #1"; AC-3 (line 83) which *requires* step 6 stay unresolved; T-2's
  `STEP6_BLOCKER_NOT_FLAGGED` assertion (lines 153–154). These now INVERT: step 6 is resolved to
  `engage_supercruise`; the design-only banner is lifted (L5). This file is the densest single edit.
- `2026-06-17-flow-redesign-MASTER-SPEC.md` §"Smack Recovery" step 6 (lines 75–77): the inline
  `engage_jump_clearance *(operator note: "enter supercruise")* — **BLOCKED-ON-KYLE**`. Resolve to
  `engage_supercruise` (Key_J), settled.
- `2026-06-17-flow-redesign-CONSOLIDATED-BLOCKERS.md`: the `### ⚠ C4 SMACK IS DEVIATION-BLOCKED`
  section (lines 51–57); the original §C item 6 (lines 61–63) framing step-6 as an open question; the
  `#6 smack step-6 … (Moot pending the C4 deviation below)` note (line 41). Resolve all to LOCKED.
- `2026-06-17-flow-redesign-C4-smack-recovery-DESIGN.md`: the route_back rationale rests on the
  unresolved step 6 / deviation. Add a header note that the headline blocker (#1) and the deviation
  are RESOLVED (operator 2026-06-18) → step 6 = `engage_supercruise`; the design's other
  BLOCKED-ON-CONTRACT(C1/C2) items may remain (they are real contract gaps, not the struck framing).
  Do NOT rewrite the historical candidate analysis — annotate the status.

**L2 (exploration first-class):** check `MASTER-SPEC` COUNCIL DECOMPOSITION + SCENE FLOWS, `C6-*`
brief + DESIGN, and CONSOLIDATED-BLOCKERS for any "side-quest"/optional/deferred framing of
exploration. Current read shows exploration is already authored as a scene (C6) and the flag is
LOCKED to `body_tour_enabled`; if no doc *demotes* it, L2 may produce only a confirming clause in the
master spec — that is acceptable. **Do NOT invent a contradiction that isn't there** (INV-3); if
exploration is already first-class everywhere, record that in FILES-CHANGED as "L2: verified
already-first-class, no demotion found."

**L3 (full CV committed):** scan all in-scope docs for hedging that the community blind-keypresses
the nav panel or that CV is too risky. Note: the *prior-art* finding that "nobody mapped ED nav-panel
coords" is a true historical research result and lives mainly in memory + possibly historical docs —
do NOT strike a factual prior-art statement; strike only language that frames CV as NOT-the-chosen
direction or recommends blind keypresses as the path forward. The C1 DESIGN keeps blind macros as a
*fail-closed fallback* — that is correct and must remain (it is the no-blind-degrade safety, not a
hedge against CV). Distinguish "CV is the direction, blind macro is the fallback" (KEEP) from "CV is
risky / community blind-keypresses so we should too" (STRIKE).

**L4 (NFZ ≠ docking):** `CONSOLIDATED-BLOCKERS.md` item 12 (lines 85–87) frames the `< 7.5 km` OCR
loop as "**replaces** your live-verified `$STATION_NoFireZone_entered` journal gate" and asks whether
to keep the journal gate — this is the conflation to CORRECT: per the LOCKED #12 (lines 46–47, which
is already correct), they are SEPARATE concerns, both kept. The `C7-docking-DESIGN.md` §6 + the
failure-recovery RISK (lines 21, 47) carry the same "REPLACED … NFZ gate → OCR loop (REGRESSION RISK)"
framing — annotate that the operator RESOLVED this: not a replacement, two distinct gates. The
`C7-docking.md` brief ROUND-2 PINNED block (lines 11–13) is ALREADY correct (keep both, distinct) —
verify it agrees, no edit likely needed there.

**L5 (design-only lifted):** the `MASTER-SPEC` STANDING RULES bullet "**DESIGN-ONLY.** … The live
flight path stays untouched until Operator signs each design off." (lines 13–14). Update to: building is
authorized for ratified scenes (C5/C2 in flight); keep NO-GUESSING + fail-closed. Then every council
brief's "**DESIGN-ONLY.** … Do NOT build, edit flight code, or commit." bullet (C1 line 6, C2 line 21,
C3 line 6, C4 brief, C4-STAGE0 lines 1–4, C5 line 6, C6 line 6, C7 line 20) and every DESIGN doc's
HTML-comment `DESIGN-ONLY (not built/wired)` header marker. Decision: these council briefs are
HISTORICAL records of a design-only round; the cleanest faithful edit is a top-level note in the
MASTER-SPEC standing rules (the live source of truth) that DESIGN-ONLY/no-build is LIFTED as of
2026-06-18, and a one-line status note on each brief/DESIGN pointing to that — NOT a deletion of the
historical "this council ran design-only" record. **BLOCKED-ON-KYLE candidate** if it is unclear
whether the operator wants the per-brief bullets rewritten vs annotated: surface it, default to
annotate-not-delete (preserves the audit trail; INV-7 spirit).

**L6 (pips restored):** `pips.md` (root) is already correct (status 2026-06-18 says ripped + this is
the wiring reference) — likely NO edit. The contradiction to fix is any doc asserting pips are
*permanently* scrapped. Current read: `2026-06-16-c3-smack-ratified-spec.md` line 105 + the
`DEBUG-MENU-STAGE0-SPEC.md` lines 510–522 reference pips as "the ripped-out code" — these are
HISTORICAL/accurate-at-the-time; per §1.5 do not rewrite, but if any AUTHORITATIVE doc states pips
are *permanently* gone, add the "restoration pending, placement TBD by operator" note. If no
authoritative doc makes the permanent claim, L6 may yield only a confirming line; record that.

---

## 4. ACCEPTANCE CRITERIA

- **AC-1.** The diff is docs-only: only `*.md` files changed, zero non-`.md` paths. [T-1]
- **AC-2.** `2026-06-18-AUDIT-INVENTORY.md` is byte-identical (untouched). [T-2]
- **AC-3 (L1).** No in-scope doc presents `DEVIATION-BLOCKED`, `DO NOT RE-FIRE`/`DO-NOT-RE-FIRE`, or
  `target nothing` (smack routine) as a LIVE status; and the C4 step-6 reads `engage_supercruise`
  (Key_J) as settled in MASTER-SPEC, CONSOLIDATED-BLOCKERS, both C4 briefs, and the C4 DESIGN. [T-3]
- **AC-4 (L1).** The full 8-step smack law (set_throttle 100 → nav_target_star → pitch_compass →
  target_ahead → wait_cooldown_clear → engage_supercruise → nav_supercruise_star → Traversal) appears
  in order in at least the MASTER-SPEC and the primary C4 brief, marked LOCKED/RESOLVED. [T-4]
- **AC-5 (L4).** No in-scope doc states the OCR `< 7.5 km` loop *replaces* the NFZ journal gate; the
  NFZ event and the proximity gate are documented as SEPARATE (fire-safety vs docking-readiness),
  both kept. [T-5]
- **AC-6 (L5).** The MASTER-SPEC standing rules state DESIGN-ONLY/no-build is LIFTED (building
  authorized for ratified scenes), while `NO GUESSING` and fail-closed remain present verbatim. [T-6]
- **AC-7 (L1 safety).** The C4 docs still document the smack-glare guards (`behind_confirm_reads`,
  `behind_fill_max`) and the escape-vector ALIGN-AND-HOLD-to-`SupercruiseEntry` mechanic. [T-7]
- **AC-8 (L6).** No AUTHORITATIVE in-scope doc asserts pips are *permanently* scrapped without the
  "restoration pending, placement TBD by operator" note; `pips.md` is not contradicted. [T-8]
- **AC-9 (L2 / L3).** Exploration is not framed as a side-quest/optional in any in-scope doc; no
  in-scope doc recommends blind-keypressing the nav panel as the chosen path or frames CV as too risky
  to pursue (fail-closed fallback language is explicitly allowed). [T-3b/T-8b reviewer + grep]
- **AC-10 (reporting).** The candidate emits FILES-CHANGED (every touched file + governing L#),
  SUPERSEDED-FLAGGED, and BLOCKED-ON-KYLE sections; every touched `.md` appears in FILES-CHANGED and
  every FILES-CHANGED entry was actually touched. [T-9]
- **AC-11 (no invention).** Every BLOCKED-ON-KYLE item is a real gap not covered by L1–L6; no content
  edit lacks an L# justification. A change that should have been BLOCKED-ON-KYLE but was guessed is a
  **spec-conformance fail → routes to Stage 0**. [reviewer-checked]
- **AC-12 (historical preserved).** Historical docs (§1.5 set) carry only top-of-file superseded
  markers, no body rewrite. [T-2-bounds + reviewer]

A FAIL on AC-3 (L1 not fully propagated / step-6 re-questioned), AC-5 (NFZ conflation left in), or
AC-11 (a guessed change) is a **spec-conformance fail → routes to Stage 0**, not Stage 1.

---

## 5. EXECUTABLE ACCEPTANCE TESTS

Run from repo root `<repo-root>\ED-AFK`. These run against the candidate's
worktree AFTER the edits. `IN_SCOPE` is the §1.2 set. PASS markers are printed literally; any other
output is a FAIL for that gate. (POSIX sh / Git Bash; `rg` = ripgrep.)

```bash
# Shared scope list (in-scope CONTENT-edit docs; AUDIT-INVENTORY excluded by construction).
IN_SCOPE="docs/superpowers/specs/2026-06-17-flow-redesign-MASTER-SPEC.md \
docs/superpowers/specs/2026-06-17-flow-redesign-CONSOLIDATED-BLOCKERS.md \
docs/superpowers/specs/2026-06-17-flow-redesign-C1-cv-action-family-DESIGN.md \
docs/superpowers/specs/2026-06-17-flow-redesign-C2-control-flow-DESIGN.md \
docs/superpowers/specs/2026-06-17-flow-redesign-C3-arrival-DESIGN.md \
docs/superpowers/specs/2026-06-17-flow-redesign-C4-smack-recovery-DESIGN.md \
docs/superpowers/specs/2026-06-17-flow-redesign-C5-traversal-DESIGN.md \
docs/superpowers/specs/2026-06-17-flow-redesign-C6-exploration-DESIGN.md \
docs/superpowers/specs/2026-06-17-flow-redesign-C7-docking-DESIGN.md \
docs/superpowers/specs/council-briefs/C1-cv-action-family.md \
docs/superpowers/specs/council-briefs/C2-control-flow.md \
docs/superpowers/specs/council-briefs/C3-arrival.md \
docs/superpowers/specs/council-briefs/C4-smack-recovery.md \
docs/superpowers/specs/council-briefs/C4-smack-recovery-STAGE0-SPEC.md \
docs/superpowers/specs/council-briefs/C5-traversal.md \
docs/superpowers/specs/council-briefs/C6-exploration.md \
docs/superpowers/specs/council-briefs/C7-docking.md"
```

### T-1 (AC-1): docs-only diff. PASS = `DOCS_ONLY_OK`.
```bash
# Every changed path (staged+unstaged+untracked) must end in .md.
CHANGED=$(git status --porcelain | awk '{print $2}')
NONMD=$(printf '%s\n' "$CHANGED" | grep -v '\.md$' || true)
[ -z "$NONMD" ] && echo "DOCS_ONLY_OK" || { echo "NON_MD_CHANGED:"; echo "$NONMD"; }
```

### T-2 (AC-2 / AC-12): frozen file untouched. PASS = `AUDIT_INVENTORY_UNTOUCHED`.
```bash
git diff --quiet -- docs/superpowers/specs/2026-06-18-AUDIT-INVENTORY.md \
  && git diff --cached --quiet -- docs/superpowers/specs/2026-06-18-AUDIT-INVENTORY.md \
  && echo "AUDIT_INVENTORY_UNTOUCHED" || echo "AUDIT_INVENTORY_MODIFIED_FAIL"
```

### T-3 (AC-3, L1): the struck framing is gone as a LIVE status. PASS = `L1_FRAMING_STRUCK`.
```bash
# These tokens must not survive as a current blocker. We allow them ONLY on lines that
# also mark them corrected/struck/past (grep -v the annotation lines, then expect empty).
HITS=$(rg -n -i 'DEVIATION-BLOCKED|DO[- ]NOT[- ]RE-FIRE|target nothing' $IN_SCOPE 2>/dev/null \
  | rg -v -i 'corrected|struck|was a claude error|no longer|RESOLVED|LOCKED|~~' || true)
[ -z "$HITS" ] && echo "L1_FRAMING_STRUCK" || { echo "L1_FRAMING_SURVIVES_FAIL:"; echo "$HITS"; }
```

### T-3b (AC-3, L1): step 6 is settled to engage_supercruise, not re-questioned. PASS = `STEP6_SETTLED`.
```bash
# In the four docs that state the smack flow, step 6 must read engage_supercruise and
# must NOT still present engage_jump_clearance as the LIVE/unresolved step-6 choice.
S6DOCS="docs/superpowers/specs/2026-06-17-flow-redesign-MASTER-SPEC.md \
docs/superpowers/specs/2026-06-17-flow-redesign-CONSOLIDATED-BLOCKERS.md \
docs/superpowers/specs/council-briefs/C4-smack-recovery.md \
docs/superpowers/specs/council-briefs/C4-smack-recovery-STAGE0-SPEC.md"
rg -q -i 'engage_supercruise' $S6DOCS \
  && ( rg -n -i 'BLOCKED-ON-KYLE.*step.?6|step.?6.*BLOCKED-ON-KYLE|step.?6.*\?' $S6DOCS \
       | rg -v -i 'RESOLVED|LOCKED|settled|was|corrected' >/dev/null && echo "STEP6_STILL_OPEN_FAIL" \
       || echo "STEP6_SETTLED" ) \
  || echo "STEP6_VERB_MISSING_FAIL"
```

### T-4 (AC-4, L1): the 8-step law is present in order in the master spec + C4 brief. PASS = `SMACK_LAW_PRESENT`.
```bash
for DOC in docs/superpowers/specs/2026-06-17-flow-redesign-MASTER-SPEC.md \
           docs/superpowers/specs/council-briefs/C4-smack-recovery.md; do
  ok=1
  for s in set_throttle nav_target_star pitch_compass target_ahead wait_cooldown_clear \
           engage_supercruise nav_supercruise_star Traversal; do
    rg -q "$s" "$DOC" || { echo "SMACK_STEP_MISSING:$DOC:$s"; ok=0; }
  done
  [ $ok -eq 1 ] && echo "SMACK_LAW_PRESENT:$DOC"
done
```

### T-5 (AC-5, L4): no doc says the OCR loop REPLACES the NFZ gate. PASS = `NFZ_DISTINCT_OK`.
```bash
# Catch the conflation framing. Allow only if the line is corrected/past-tense.
HITS=$(rg -n -i 'replaces?.{0,40}NoFireZone|NoFireZone.{0,40}replac|OCR loop.{0,30}replac.{0,30}NFZ|NFZ.{0,30}replac.{0,30}OCR' $IN_SCOPE 2>/dev/null \
  | rg -v -i 'corrected|NOT a replace|separate|distinct|both kept|was |~~' || true)
[ -z "$HITS" ] && echo "NFZ_DISTINCT_OK" || { echo "NFZ_CONFLATION_SURVIVES_FAIL:"; echo "$HITS"; }
```

### T-6 (AC-6, L5): design-only/no-build lifted in master spec, no-guessing kept. PASS = `DESIGNONLY_LIFTED_OK`.
```bash
MS="docs/superpowers/specs/2026-06-17-flow-redesign-MASTER-SPEC.md"
# (a) master spec asserts building is authorized / no-build lifted:
rg -q -i 'no-build.{0,20}lifted|building (is )?authoriz|DESIGN-ONLY.{0,20}(lifted|superseded)|now being BUILT' "$MS" \
  && BUILD_OK=1 || BUILD_OK=0
# (b) no-guessing + fail-closed survive:
rg -q -i 'NO GUESSING' "$MS" && rg -q -i 'fail[ -]?clos' "$MS" && SAFE_OK=1 || SAFE_OK=0
[ $BUILD_OK -eq 1 ] && [ $SAFE_OK -eq 1 ] && echo "DESIGNONLY_LIFTED_OK" \
  || echo "DESIGNONLY_LIFT_FAIL(build=$BUILD_OK safe=$SAFE_OK)"
```

### T-7 (AC-7, L1 safety): smack-glare + escape-vector guards still documented. PASS = `SMACK_GUARDS_KEPT`.
```bash
C4DOCS="docs/superpowers/specs/council-briefs/C4-smack-recovery.md \
docs/superpowers/specs/council-briefs/C4-smack-recovery-STAGE0-SPEC.md \
docs/superpowers/specs/2026-06-17-flow-redesign-C4-smack-recovery-DESIGN.md"
ok=1
for g in behind_confirm_reads behind_fill_max SupercruiseEntry; do
  rg -q "$g" $C4DOCS || { echo "GUARD_DROPPED:$g"; ok=0; }
done
rg -q -i 'escape vector' $C4DOCS || { echo "GUARD_DROPPED:escape_vector"; ok=0; }
[ $ok -eq 1 ] && echo "SMACK_GUARDS_KEPT"
```

### T-8 (AC-8, L6): no authoritative doc claims pips permanently scrapped without the restoration note.
PASS = `PIPS_RESTORATION_NOTED`.
```bash
# Find authoritative in-scope docs that call pips scrapped/permanently-gone; each such line/file
# must also carry a restoration-pending note. (pips.md itself is the reference, excluded.)
BAD=$(rg -l -i 'pips? (permanently |are )?(scrapp|ripped|gone|removed for good)' $IN_SCOPE 2>/dev/null || true)
fail=0
for f in $BAD; do
  rg -q -i 'restoration pending|being restored|placement TBD|operator reversed' "$f" || { echo "PIPS_NO_RESTORE_NOTE:$f"; fail=1; }
done
[ $fail -eq 0 ] && echo "PIPS_RESTORATION_NOTED"
```

### T-9 (AC-10): every touched .md is reported and every reported file was touched. PASS = `REPORT_RECONCILES`.
```bash
# The candidate must write its FILES-CHANGED list to docs/superpowers/specs/_RECON_FILES_CHANGED.txt
# (one absolute-or-repo-relative .md path per line) as the machine-checkable manifest.
MANIFEST="docs/superpowers/specs/_RECON_FILES_CHANGED.txt"
[ -f "$MANIFEST" ] || { echo "MANIFEST_MISSING_FAIL"; exit 0; }
TOUCHED=$(git status --porcelain | awk '{print $2}' | grep '\.md$' | grep -v '_RECON_FILES_CHANGED' | sort -u)
REPORTED=$(grep -v '^\s*$' "$MANIFEST" | sed 's#.*ED-AFK/##' | sort -u)
DIFF=$(comm -3 <(printf '%s\n' "$TOUCHED") <(printf '%s\n' "$REPORTED"))
[ -z "$DIFF" ] && echo "REPORT_RECONCILES" || { echo "REPORT_MISMATCH_FAIL:"; echo "$DIFF"; }
```

### T-10 (AC-3 cross-doc consistency): the C4 step-6 statement does not conflict across docs. PASS = `C4_CONSISTENT`.
```bash
# No in-scope doc may still assert step-6 is engage_jump_clearance as the CHOSEN smack action.
HITS=$(rg -n -i 'step.?6.{0,40}engage_jump_clearance|engage_jump_clearance.{0,40}\(enter supercruise\)' $IN_SCOPE 2>/dev/null \
  | rg -v -i 'was|NOT|instead of|corrected|RESOLVED|~~|literal token' || true)
[ -z "$HITS" ] && echo "C4_CONSISTENT" || { echo "C4_STEP6_CONFLICT_FAIL:"; echo "$HITS"; }
```

**Gate pass condition:** T-1, T-2, T-3, T-3b, T-4, T-5, T-6, T-7, T-9, T-10 all print their PASS
marker; T-8 prints PASS or is vacuously satisfied (no authoritative permanent-scrap claim exists).
A FAIL on T-1 (non-md edit) or T-2 (frozen file touched) is a **blocker** — halts the gate. A FAIL on
T-3 / T-3b / T-5 (the struck L1 framing or NFZ conflation survives) is a **spec-conformance fail →
routes to Stage 0**. AC-11 (a guessed edit) is reviewer-judged and is likewise a Stage-0 route.

---

## 6. NOTES / KNOWN AMBIGUITIES (surfaced, not guessed)

- **N-1 (L5 per-brief annotate vs rewrite).** Whether the operator wants every council brief's
  "DESIGN-ONLY … do NOT build" bullet *rewritten* vs *annotated* (pointer to the master-spec lift) is
  not pinned by the locked list. Default per INV-7: annotate-not-delete (preserve the audit trail of
  what each council was told). If a candidate believes the briefs must be rewritten, that is a
  `BLOCKED-ON-KYLE`, not a unilateral rewrite.
- **N-2 (historical pip docs).** `2026-06-16-c3-smack-ratified-spec.md` and `DEBUG-MENU-STAGE0-SPEC.md`
  describe pips as "ripped-out" — historically accurate. Per §1.5 these are NOT rewritten; they get a
  superseded/historical marker only if they are in the HISTORICAL set and lack one. They are NOT in
  the §1.2 authoritative set, so L6 does not force a content edit there.
- **N-3 (README).** Current README is a generic, decision-agnostic product description; the read found
  no clause that contradicts L1–L6. Expect NO README edit. Editing it to inject the redesign decisions
  would be invention (INV-3) — do not. If a candidate finds a genuine contradiction, cite the line.
- **N-4 (memory store).** `C:\Users\…\.claude\projects\…\memory\*.md` (the auto-memory) is OUT OF
  SCOPE — it is not repo documentation; the task scope is `docs/**/*.md` + README. Do not edit memory.
- **N-5 (the C4-STAGE0-SPEC is self-referential).** This very spec's sibling
  `C4-smack-recovery-STAGE0-SPEC.md` is BOTH in-scope-to-edit (it carries the struck framing) AND was
  arbiter-authored. Reconciling it means inverting its step-6 BLOCKED-ON-KYLE and its design-only
  banner — the candidate edits it like any other in-scope doc.
