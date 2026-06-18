<!-- C-council C7-docking | task w0n0vgxtw | decision=route_back selected= | 2026-06-17 | DESIGN-ONLY (not built/wired) | ledger: .claude/council-ledger.jsonl -->
<!-- STATUS 2026-06-18 (L5): DESIGN-ONLY / no-build is LIFTED per the MASTER-SPEC standing rules — building is authorized for ratified scenes. The "(not built/wired)" marker above is the historical design-only record, not a current prohibition. NO-GUESSING + fail-closed still bind. -->

DESIGN-ONLY deliverable: <repo-root>\ED-AFK\.claude\worktrees\wf_bc37f748-3ce-2\docs\superpowers\specs\designs\C7-docking-DESIGN.md (new file; git status shows ONLY docs/superpowers/specs/designs/ added â€” no flight-code/procedure edit, no commit). The doc contains all required sections. Every fact below was re-verified in-repo this session.

== 1. AUTHORED-FLOW MECHANISM MAP (all 9 steps + both branches, nothing unmapped) ==
1 wait 1s -> operator literal (kept). 2 check Status.json -> read Status.Destination (C2 discriminator). 3 dest==system -> nav_supercruise_star (C1) + bot IDLE (C2 sink). 4.1 nav_supercruise_target station row (C1). 4.2 wait SupercruiseExit -> EVENT+STATE gate (mirror step_dock_sc_assist). 4.3 station name -> Status.Destination.Name proposed (BLOCKED). 4.4 boost -> NEW step_boost. 4.5 set_throttle 50 -> existing step_set_throttle. 4.6 nav-panel target -> existing step_nav_panel_target. 4.7 OCR <7.5km loop -> NEW step_dock_close_to_range + NEW km/Mm parser. 4.8 E/E/D/space + throttle0 -> NEW request_docking_tail wrapper. 4.9 autodock -> scene terminus.

== 2. BIND TABLE (verified ED-AFK.4.2.binds) ==
E=CycleNextPanel (585-587), E=CycleNextPanel, D=UI_Right (569-571), space=UI_Select SECONDARY bind (575; primary Key_Enter=574). So E,E,D,space == CycleNextPanel,CycleNextPanel,UI_Right,UI_Select. All four in REQUIRED_ACTIONS (ed_core/binds_validate.py:53,65,69) -> binds_validate PASSES. This is the EXACT tail of request_docking() (ed_core/executor/navpanel.py:243-306) MINUS leading FocusLeftPanel+pin and trailing close. DESIGN-CRITICAL: literal sequence has no panel-open/pin -> recommend wrapping full request_docking (reading B); flagged BLOCKED.

== 3. STATION NAME ==
SupercruiseExit (events.py:147-151) carries StarSystem/Body/BodyType ONLY, no StationName. Candidates: Status.Destination.Name (lowest latency, no event), SupercruiseDestinationDrop.Type (~5s before exit, events.py:164-172), Docked.StationName (too late, post-dock, events.py:213-218). Proposed default Status.Destination.Name. BLOCKED: which source + what the name is USED FOR (never consumed after 4.3 -> likely log-only).

== 4. boost ==
UseBoostJuice=Key_B bound (binds:285-288); NO register_step('boost') exists (registrations end steps.py:1499). NEW step_boost contract: tap UseBoostJuice, fail-closed if unbound, best-effort True on cooldown (no boost-ready Status flag), not input_exclusive. BLOCKED: semantics + heat guard.

== 5. <7.5km OCR ==
navpanel_reader._DISTANCE_RE matches LS|LY only (line 81); distance column explicitly DROPPED as redundant (40-43). No km/Mm parser, no <7.5km comparator in repo. NEW parse_station_distance_km(text)->float|None (km/Mm regex, Mm*1000, None on unread) + NEW step_dock_close_to_range (bounded backstop, fail-closed on unread, throttle-zero finally ram-guard via resolve_nav_region per-ship crop #19). BLOCKED: unit/tab + no calibration frame.

== 6. RECONCILIATION ==
> **L4 CORRECTION — RESOLVED/LOCKED (operator 2026-06-18): the NFZ gate and OCR loop are SEPARATE, distinct, both kept — NOT a replace.** The
> `$STATION_NoFireZone_entered` journal gate and the `< 7.5 km` OCR proximity loop are SEPARATE, distinct
> concerns and BOTH are kept: NFZ-entry is a fire-safety zone (LARGER than the docking zone); OCR < 7.5 km
> is the docking-readiness trigger. The "REPLACED … NFZ journal gate -> OCR loop" / "REGRESSION RISK"
> framing below was a CONFLATION (Claude hallucination) and is struck — there is no replacement and no
> regression; read the line through this correction.
REPLACED: dock_target_station+dock_sc_assist -> nav_supercruise_target (C1); dock_approach NFZ journal gate ReceiveText $STATION_NoFireZone_entered -> OCR loop (struck: NOT a replacement — NFZ and OCR are separate gates, both kept, per the L4 correction above); dock_request -> E/E/D/space tail. KEPT: ship-safety patterns + request_docking macro + the NFZ journal fire-safety gate (distinct from the proximity loop). OMITTED: dock_blind_maneuver, orient_compass, dock_await_docked, station_services_macro (BLOCKED: intentional?). NEW: boost+set_throttle 50 leg.

== 7. C1/C2 ==
C1: nav_supercruise_target/nav_supercruise_star don't exist; design against details-page button-bar CV; assumptions flagged (targets station row, subsumes blind-maneuver+orient). C2: discriminator = Status.Destination via _destination_is_local_star/_dest_is_named_station; IDLE sink (none exists today, dock.toml header); scene chaining. Every assumption flagged.

== 8. STANDING RULES ==
no-arbitrary-timed-waits: wait 1s + two wait 0.5s kept as operator literals; SupercruiseExit = event+state gate; <7.5km loop = state gate with bounded backstop + throttle-zero ram-guard. Ship-safety: all NEW steps fail closed, nothing drives on unread frame.

== 9. CONSOLIDATED BLOCKED-ON-KYLE (8 items) ==
station-name source+use; E/E/D/space panel-open+pin; station-distance unit+tab+calibration frame; boost semantics; await-docked/services omission; NFZ->OCR regression; C1 shape; C2 shape. PLUS cite-error: spec Â§6 claims binds_validate is in ed_autojump but it lives at projects/ed-core/src/ed_core/binds_validate.py (REQUIRED_ACTIONS at line 43); brief's Ground-in path is correct.

== 10. .toml SKETCH embedded in doc (NOT a real file). ==

## ASSUMPTIONS ()
- Reading (B) is the safe interpretation of E/E/D/space: the literal sequence is shorthand for the full request_docking macro (open+pin+walk+select), not a from-cold continuation. The current step_nav_panel_target closes the panel and works the Navigation tab, so reading (A) (continue from an open Contacts row) would mis-fire â€” but I flagged this as BLOCKED rather than committing to (B) in build.
- Status.Destination.Name is the right default station-name source (lowest latency, same field C2 uses) and the name is probably log-only since the authored flow never consumes it after step 4.3.
- C1's nav_supercruise_target targets+SC-assists the STATION row and subsumes dock_blind_maneuver + orient_compass â€” which is why the authored flow omits them rather than it being an oversight.
- The dock_await_docked + station_services omission is intentional: C7 is scoped to end at the docking request, and a later scene owns the Docked-wait + refuel/repair/rearm.
- Station distance at approach range renders in km/Mm (not Ls/Ly), justifying a brand-new parser rather than extending _DISTANCE_RE â€” but I have no real frame to confirm the unit string, so it stays BLOCKED.
- Deliverable location: I placed the design at docs/superpowers/specs/designs/C7-docking-DESIGN.md (the designs/ dir did not exist; I created it). The spec said 'inside the design doc' without pinning a path.
- boost is a single UseBoostJuice tap in normal space and no heat/cooldown guard is wanted (ED gives no boost-ready Status flag to gate on anyway).
- The .toml sketch's `when_dest` branch predicate and a `wait`/`read_station_name` step are illustrative design constructs; the real branching mechanism is owned by C2 and not pinned here.

## RISKS ()
- @{lens=spec-conformance; note=The headline E/E/D/space ambiguity is unresolved by design (BLOCKED), so a downstream builder who reads my 'recommend full request_docking' as settled would build reading (B) when the operator may have meant a true continuation. I flagged it but did not force a decision â€” that is correct for design-only but leaves the single most load-bearing question open.}
- @{lens=boundaries; note=The <7.5km gate is the riskiest boundary: the new km/Mm parser, the Mm->km*1000 conversion, and the gate comparator are all UNCALIBRATED against any real frame. A unit-string surprise (e.g. distance shows as 'Mm' with a space, or mixed 'km'/'Mm' at the 7.5km boundary, or a localized decimal comma) would mis-gate. Mitigated by fail-closed-on-unread + ram-guard, but the parser regex itself is a guess until a frame exists.}
- @{lens=failure-recovery; note=[RESOLVED/LOCKED operator 2026-06-18: this risk is moot — the OCR loop does NOT replace the NFZ gate. They are SEPARATE, distinct gates and BOTH are kept (NFZ = fire-safety zone, larger; OCR < 7.5 km = docking-readiness). The premise below ("replacing the NFZ journal gate") was a conflation and is struck.] (historical) Replacing the LIVE-VERIFIED NFZ journal gate with an unproven OCR loop is a recovery regression: the journal gate never spins and never mis-reads, whereas a mis-calibrated OCR that never trips would ride 50% throttle toward the station until the watchdog. I recommend keeping the journal gate as fallback but did not mandate it â€” if the council ratifies OCR-only, the bounded backstop + throttle-zero are the ONLY things standing between a bad read and a ram.}
- @{lens=security; note=Low relevance (single-operator local tool, no untrusted input). The nearest analogue is that station name/Type fields are read from the journal/Status and could in principle carry odd characters; since I propose the name is log-only and never gates control flow, blast radius is negligible. Flagging only for completeness.}
- @{lens=concurrency; note=The parallel honk track + the input_exclusive new steps (dock_close_to_range, request_docking_tail) share the input device; I assumed the existing FlowRunner input-exclusivity machinery serializes them as it does for current dock_* steps, but I did not re-derive the exclusivity scheduler here. If honk can fire mid-macro it could corrupt the panel-walk keystroke sequence â€” a known class the existing flow already handles, but unverified for the NEW steps specifically.}
