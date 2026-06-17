> ## ⚠️ STALE — FROZEN SNAPSHOT, DO NOT USE AS CURRENT TRUTH
>
> **Frozen at 2026-06-08 (master @ ee4c0f7).** This document predates all C1–C4
> council work (landed 2026-06-16). The Part D defect list, the per-step action
> tables, and the gate/failure-policy columns **no longer match master**.
>
> **Current truth:**
> - Per-step procedures: `projects/ed-autojump/procedures/*.toml` (live, editable)
> - C-series design specs (C1–C4): `docs/superpowers/specs/` (ratified council output)
> - Open defects: `docs/superpowers/specs/OPERATOR_TODO_2026-06-16.md`
> - Repo overview: `README.md`
>
> This file is kept for historical reference only.

---

# ED-AFK Bot Action Surface — Council-of-7 Megasheet
**Audit date 2026-06-08 · master @ ee4c0f7 · synthesis of 7 slice audits**

---

## Legend

| Token | Meaning |
|---|---|
| **Gate** | The condition that decides success/failure. Journal events or Status.json flags only; wall-clock allowed ONLY as a documented stuck-state backstop, never a success gate. |
| **Required** | `required` = a False triggers the procedure's `on_required_fail` policy. `best-effort` = a False is logged but the flow proceeds. `n/a` = primitive/registry-only or not invoked. |
| **On-failure** | `retry_from` (resume at a named step), `skip_to` (jump forward), `abort` (hand to policy/human), `preempt` (cooperative scene handoff), `pause` (interpreter halt), `tap` (blind press, no fail path). |
| **EXCLUSIVE** | Multi-key UI macro wrapped in `exclusive_guard`; heat watchdog cannot desync it. The 7 are: sc_assist_orbit, nav_panel_target, dock_target_station, dock_sc_assist, dock_request, station_services, auto_launch. |
| **Vision** | Closed-loop CV step (compass / widget-ring); fails closed if vision/frame-grabber unwired. |
| **FAIL-CLOSED** | Never advances toward a jump without a confirmed positive read. |
| **Key encoding** | `0xNN` = plain make code; `0xE0 0xNN` = EXTENDED (arrow/numpad-nav keys). PAUSE=0 in the sender; back-to-back presses rely on `time.sleep(hold)`, hold ≥0.05s. |
| Pitch axis note | Bundled ED-AFK preset is inverted-feel: **PitchUp = physical S (0x1F), PitchDown = physical W (0x11)**. |

---

## PART A — Master action table (every row, all slices)

Sorted: procedures (arrival → startup → sc_resume → smack_recovery → route_complete_park → dock_resume → dock → honk), then registry, dispatcher, key-layer, vision, dead binds.

| Context | Action | Keys | Effect | Gate | Required | On-failure | Known defect |
|---|---|---|---|---|---|---|---|
| **arrival** s1 | set_throttle pct=0 | SetSpeedZero (X 0x2D) | Zero throttle on arrival in case auto-dethrottle missed | none (blind tap) | best-effort | tap; proc anchor=scoop_refuel | none |
| **arrival** s2 | scoop_refuel approach_pct=25 standoff_frac=0.80 refuel_below=0.70 budget_s=300 | SetSpeed25/SetSpeedZero | Pit-stop: nose into scoopable arrival star, scoop to standoff rate, drink to full | Status: fuel<refuel_below AND star KGBFOAM-scoopable AND arrival<120s; ScoopingFuel+FuelMain drive it; else skip | best-effort | skip/DONE→True; FAIL→False w/ throttle zeroed; orbit runs either way | standoff_frac=0.80 unproven toward 1.0; flow NOT live-tested (SC-assist-from-inside-band / HOLD creep / heat) |
| **arrival** s3 | nav_panel_target required=false skip_to=target_next_route max_rows=3 | FocusLeftPanel,UI_Select×2,FocusLeftPanel; pin=UI_Down tap+hold UI_Up | EXCLUSIVE: lock arrival star, compass-dot + identity verified; fails closed on wrong body | layer1 compass dot found + layer2 Destination.Name==system; max_rows=3 = lock-speed signal | required=false | skip_to=target_next_route (far star vaults orbit get-around) | station-dock detection family; capture-at-plot pending operator plot-timing answer |
| **arrival** s4 | sc_assist_orbit | FocusLeftPanel,UI_Select,UI_Right,UI_Select,FocusLeftPanel | EXCLUSIVE: engage SC-assist to orbit/get-around the locked star | in_supercruise==true AND dest is local star; logs ScAssistOrbitSent; post-check only "still in SC" | best-effort | refused→degrades to direct-jump path; proc anchor=scoop_refuel | ED exposes NO assist-engaged flag; engagement unprovable in-code |
| **arrival** s5 | wait s=13.0 | n/a (passive) | Let SC-assist orbit acquire | none (pacing, NOT a gate) | best-effort | always True; no retry_anchor | fixed 13s wall-clock (pacing only → permitted) |
| **arrival** s6 | target_next_route required=true | TargetNextRouteSystem (H 0x23) | Cancel SC-assist AND lock next route system; StarClass danger-check (D*/N/H/W) fail-closed | new FSDTarget event w/ StarClass OR Status.Destination in NavRoute route[1:]; no-route fast-fail | required | retry_from=scoop_refuel, max_retries=3, backoff_s=2.0 | no-route → Wolf 359 fresh-login family (bounded by fast no-route check) |
| **arrival** s7 | set_throttle pct=100 | SetSpeed100 (V 0x2F) | Full burn to clear the star | none (blind tap) | best-effort | tap; anchor scoop_refuel | none |
| **arrival** s8 | wait s=13.0 | n/a (passive) | Clearance-burn duration before orienting | none (pacing, NOT a gate) | best-effort | always True; no retry_anchor | none |
| **arrival** s9 | orient_compass required=true | Pitch/Yaw closed-loop (S/W/A/D) | VISION coarse-align nose to hop via nav-compass CV | vision wired; GuiFocus==0; SC-lost guard arms; outcome.aligned | required | retry_from=scoop_refuel, max_retries=3 | fails closed w/o vision (OrientNoVision); SC-lost mid-step aborts |
| **arrival** s10 | orient_widget_ring required=true | Yaw/Pitch fine micro-press | VISION fine-align: ring onto mouse widget; no-op unless widget_ring_alignment enabled | widget_ring_enabled; reader+grabber wired; GuiFocus==0; aligned | required | miss=degrade(default,True)/fail_closed→retry scoop_refuel; SC-lost→fail-closed | phantom ring-lock over target-info text seen historically; degrade-on-miss is default |
| **arrival** s11 | engage_jump required=true | SetSpeed100 then Hyperspace (K 0x25) | Fire hyperspace jump; distance-based FSD-obstruction backstop | Status: NOT docked/charging/cooldown/mass_locked/overheating | required | retry_from=scoop_refuel, max_retries=3, backoff_s=2.0 | none (combined HyperSuperCombination retired; granular FSD) |
| **arrival** s12 | hold_alignment required=true | Pitch/Yaw maintenance micro-corrections | VISION: hold cone during FSD spool until StartJump | StartJump event/FsdJump flag=success; charge-drop/cooldown-before-charge/abort=fail; 60s watchdog | required | retry_from=scoop_refuel, max_retries=3 | none — this is the fix for the banned 12s wait_for_event that cancelled healthy jumps |
| **startup** direct-1 | set_throttle pct=100 | SetSpeed100 | Full throttle for first-try direct jump (NORMAL space at a star) | none (blind tap) | best-effort | pre-anchor retry=sc_assist_orbit; tap | none |
| **startup** direct-2 | target_next_route required=true | TargetNextRouteSystem | Lock next route system; danger-class verified | new FSDTarget w/ StarClass OR Status.Destination in NavRoute[1:]; no-route fast-fail | required | pre-anchor retry=sc_assist_orbit, max_retries=3, backoff_s=2.0 | no-route (Wolf 359); pitch-astern that worsened it REMOVED 2026-06-08 |
| **startup** direct-3 | orient_compass required=true | Pitch/Yaw closed-loop | VISION coarse-align nose to hop (first-try lane) | vision wired; GuiFocus==0; SC-lost guard (no-op, normal space); aligned | required | pre-anchor retry=sc_assist_orbit | fails closed w/o vision |
| **startup** direct-4 | orient_widget_ring required=true | Yaw/Pitch fine | VISION fine-align; no-op unless enabled | widget_ring_enabled+wired; GuiFocus==0; aligned | required | miss=degrade/fail_closed→retry sc_assist_orbit | shared phantom-lock note |
| **startup** direct-5 | engage_jump required=true | SetSpeed100 then Hyperspace | Fire jump (first-try); obstruction fail-closed | Status not docked/charging/cooldown/mass_locked/overheating | required | pre-anchor retry=sc_assist_orbit | none |
| **startup** direct-6 | hold_alignment required=true | Pitch/Yaw maintenance | VISION hold cone until StartJump (first-try) | StartJump/FsdJump=success; charge-drop/cooldown/abort/60s watchdog=fail | required | pre-anchor retry=sc_assist_orbit (recovery lane begins on retry) | none |
| **startup** rec-7 | target_ahead | SelectTarget (T 0x14) | RECOVERY entry: T with nothing ahead deselects current target | none (blind tap) | best-effort | retry_from landing point; tap | blind toggle — untargets if something IS ahead; no already-locked guard |
| **startup** rec-8 | set_throttle pct=100 | SetSpeed100 | Full throttle (recovery) | none (blind) | best-effort | pre-anchor retry=sc_assist_orbit | none |
| **startup** rec-9 | engage_supercruise required=true | Supercruise (J 0x24) | Enter SC (recovery); re-press logic via params (default presses=1) | SupercruiseEntry event OR in_supercruise=success; FsdCharging true→false=abort; 60s watchdog; already-in-SC short-circuits True | required | pre-anchor retry=sc_assist_orbit | none |
| **startup** rec-10 | nav_panel_target (best-effort) max_rows=10 | FocusLeftPanel,UI_Select×2,FocusLeftPanel | EXCLUSIVE: re-lock star (row 0) so assist has a target after SC entry | compass dot + identity verify (wide max_rows=10); best-effort | best-effort | pre-anchor retry=sc_assist_orbit; degrades on fail | Robigo wrong-lock mitigated by identity check |
| **startup** rec-11 | sc_assist_orbit | FocusLeftPanel,UI_Select,UI_Right,UI_Select,FocusLeftPanel | EXCLUSIVE: engage SC-assist to orbit star; this IS the retry_from anchor | in_supercruise + dest local star; logs ScAssistOrbitSent | best-effort | refusal→degrades to direct jump; the proc retry_from anchor | no assist-engaged flag (same as arrival) |
| **startup** rec-12 | wait s=13.0 **retry_anchor=true** | n/a (passive) | Let orbit acquire; FIRST anchor — post-anchor fails return HERE | none (pacing) | best-effort | retry_anchor: later required fails return to this wait | none |
| **startup** rec-13 | target_next_route required=true | TargetNextRouteSystem | Lock next route system (post-orbit); danger-verified | new FSDTarget w/ StarClass OR Status.Destination in NavRoute[1:]; no-route fast-fail | required | post-anchor→return to s=13 wait; max_retries=3 | no-route family |
| **startup** rec-14 | set_throttle pct=100 | SetSpeed100 | Full throttle clearance burn (recovery) | none (blind) | best-effort | post-anchor fails return to retry_anchor wait; tap | none |
| **startup** rec-15 | wait s=13.0 **retry_anchor=true** | n/a (passive) | Clearance burn; SECOND anchor — fails at/after here return HERE | none (pacing) | best-effort | retry_anchor: subsequent required fails return here | none |
| **startup** rec-16 | orient_compass **(best-effort — NOT required)** | Pitch/Yaw closed-loop | VISION coarse-align (recovery); the lone non-required orient_compass | vision wired; GuiFocus==0; SC-lost guard; aligned | **best-effort** | False does NOT gate; flow continues to widget-ring | **ASYMMETRY: this orient_compass is best-effort while adjacent orient_widget_ring is required** — coarse-align fail won't gate, fine stage will |
| **startup** rec-17 | orient_widget_ring required=true | Yaw/Pitch fine | VISION fine-align (recovery); no-op unless enabled | widget_ring_enabled+wired; GuiFocus==0; aligned | required | post-anchor→return to 2nd s=13 wait; miss=degrade/fail_closed | shared phantom-lock note |
| **startup** rec-18 | engage_jump required=true | SetSpeed100 then Hyperspace | Fire jump (recovery); obstruction fail-closed | Status not docked/charging/cooldown/mass_locked/overheating | required | post-anchor→return to 2nd retry_anchor wait; max_retries=3 | none |
| **startup** rec-19 | hold_alignment required=true | Pitch/Yaw maintenance | VISION hold cone until StartJump (recovery); event/state-gated | StartJump/FsdJump=success; charge-drop/cooldown/abort/60s watchdog=fail | required | post-anchor→return to 2nd s=13 wait | none |
| **sc_resume** s1 | target_next_route required=true | TargetNextRouteSystem | Lock next hop (also cancels SC-assist); ship already in SC clear of star, no orbit | new FSDTarget w/ StarClass OR Status.Destination in NavRoute[1:]; danger fail-closed; no-route fast-fail | required | retry_from=target_next_route, max_retries=3, backoff_s=2.0 | no-route family; Robigo wrong-lock avoided by SKIPPING nav_panel_target+sc_assist_orbit (the point of this proc) |
| **sc_resume** s2 | set_throttle pct=100 | SetSpeed100 | Full burn | none (blind tap) | best-effort | retry_from=target_next_route; tap | none |
| **sc_resume** s3 | orient_compass required=true | Pitch/Yaw closed-loop | VISION coarse-align nose to hop | vision wired; GuiFocus==0; SC-lost guard arms (in SC); aligned | required | retry_from=target_next_route, max_retries=3 | fails closed w/o vision |
| **sc_resume** s4 | orient_widget_ring required=true | Yaw/Pitch fine | VISION fine-align; no-op unless enabled | widget_ring_enabled+wired; GuiFocus==0; aligned | required | miss=degrade/fail_closed; retry=target_next_route | shared phantom-lock note |
| **sc_resume** s5 | engage_jump required=true | SetSpeed100 then Hyperspace | Fire jump; distance-based obstruction fail-closed backstop | Status not docked/charging/cooldown/mass_locked/overheating | required | retry_from=target_next_route, max_retries=3 | **residual: manually-targeted-station-while-nose-on-star edge case**; obstruction check bounds damage; NEAR path (arrival) handles confirmed nose-on |
| **sc_resume** s6 | hold_alignment required=true | Pitch/Yaw maintenance | VISION hold cone until StartJump; event/state-gated | StartJump/FsdJump=success; charge-drop/cooldown/abort/60s watchdog=fail | required | retry_from=target_next_route, max_retries=3 | none |
| **smack_recovery** s0 | set_throttle pct=0 | SetSpeedZero | Kill thrust the instant of the smack drop inside exclusion zone | none (blind) | best-effort | real-space restart anchor (retry_from); n/a as fail point | none |
| **smack_recovery** s1 | nav_panel_target required=true | nav-panel UI macro | EXCLUSIVE: lock star at row 0, compass-verified | GuiFocus/compass CV verify of locked row | required | required fail→retry set_throttle(real) / target_next_route(SC) | none |
| **smack_recovery** s2 | set_throttle pct=75 | throttle bind | Burn through the 180° flip (operator: throttle up before pitch) | none (blind) | best-effort | tap | **DIVERGENCE: test asserts throttles==[0,100,100]; TOML gives [0,75,100,100] → RED. Comment says "full burn" but value=75** |
| **smack_recovery** s3 | pitch_compass until=behind center_frac=0.35 | Pitch closed-loop (+ 2-axis behind centering) | Pitch 180° to put star astern (hollow dot, centered) via CV; EXCLUSIVE vision | compass CV anti-star hollow dot; timeout_s=75/max_iters=40 are FAILSAFE ceilings, not the gate | required | required fail→retry set_throttle(real)/target_next_route(SC) | **2026-06-08 LIVE DEFECT: pitch reported done WITHOUT flipping the ship — compass-read success fired while pose still nose-on; star never put astern. Corrupts target_ahead deselect + escape-vector spawn downstream. #31 redesign** |
| **smack_recovery** s4 | wait_cooldown_clear required=true | n/a | Block until smack FSD cooldown clears; no input | Status.json FsdCooldown==false (no timer) | required | required fail→retry set_throttle(real)/target_next_route(SC) | none (unbounded state wait, NO wall-clock backstop at all) |
| **smack_recovery** s4.5 | target_ahead | SelectTarget (T) | T with star astern + nothing ahead deselects/clears | none (blind tap; relies on prior pitch) | best-effort | tap | depends on s3 flip; if pitch falsely reports done, star still ahead → T RE-LOCKS it instead of clearing |
| **smack_recovery** s5 | engage_supercruise until_charging presses=3 between_press_s=15 max_charge_s=240 | Supercruise (J) | Press SC until a LIVE charge starts (charge spawns the escape-vector compass target); re-press only if no charge yet (re-pressing live charge CANCELS it) | live charge=success (NOT entry); max_charge_s=240=wedged-FSD watchdog | required | PRE-anchor fail→retry set_throttle(real)/target_next_route(SC) | none in TOML; downstream ordering diverges from test |
| **smack_recovery** post-SC | set_throttle pct=100 | throttle bind | Throttle back to 100 after the SC press | none (blind) | best-effort | tap | **DIVERGENCE: sits BEFORE orient/hold in TOML; test places the only post-SC throttle AFTER escape-vector orient+hold+hop lock** |
| **smack_recovery** anchor-A | target_next_route required=true **retry_anchor=true** | select-next-system | Lock next route system as anchor immediately after SC press | NavRoute next-hop present; gated lock | required | fail at/after→resume here | **DEFECT: TWO target_next_route both carry retry_anchor=true; tests assert len(anchors)==1 → RED. Also ordering: test wants this AFTER orient+hold** |
| **smack_recovery** s5.5 | orient_compass required=true | pitch/yaw closed-loop | Center the spawned escape-vector circular target; EXCLUSIVE vision | compass CV escape-vector dot | required | PRE-anchor fail (scene-test driving fail)→set_throttle(real)/target_next_route(SC) | ordering DIVERGENCE: TOML runs after inserted throttle+target_next_route; test runs it directly after engage_supercruise |
| **smack_recovery** s5.6 | hold_alignment until_event=SupercruiseEntry required=true | pitch/yaw closed-loop | Ride escape vector w/ continuous align until SupercruiseEntry; EXCLUSIVE vision | journal SupercruiseEntry event | required | PRE-anchor fail→set_throttle(real)/target_next_route(SC) | ordering DIVERGENCE vs test (test puts it immediately after orient_compass, before any target_next_route) |
| **smack_recovery** anchor-B (s6) | target_next_route required=true **retry_anchor=true** | select-next-system | Lock hop in SC; SC-segment retry anchor (SC fails return HERE) | NavRoute next-hop present; gated | required | fail at/after→resume here; retry_from_if_supercruise points here | duplicate-anchor defect (shares retry_anchor) breaking len(anchors)==1 tests |
| **smack_recovery** s7 | set_throttle pct=100 | throttle bind | Re-apply throttle 100; SC entry reset it lower | none (blind) | best-effort | tap | test_v7 expects reset_power_distribution at s7b; TOML has none (pip mgmt ripped 2026-06-08) |
| **smack_recovery** s7.5 | wait s=13.0 | n/a | Fly clear of star before jumping; pacing | none (TRAJECTORY PACING, NOT a gate) | n/a | passive, cannot fail | fixed 13s wall-clock (commented pacing → borderline-compliant) |
| **smack_recovery** s8 | orient_compass required=true | pitch/yaw closed-loop | Coarse-align to locked hop before jump; EXCLUSIVE vision | compass CV read | required | fail at/after hop anchor→resume target_next_route(s6) | none |
| **smack_recovery** s9 | orient_widget_ring required=true | pitch/yaw closed-loop | Fine align via orange widget ring; no-op unless enabled; EXCLUSIVE vision | widget-ring CV read | required | fail→resume hop anchor (s6) | none |
| **smack_recovery** s10 | engage_jump required=true | FSD jump bind | Fire FSD hyperspace jump | Status FsdMassLocked / FSD-ready bits (fails closed) | required | fail→resume hop anchor | none |
| **smack_recovery** s11 | hold_alignment required=true | pitch/yaw closed-loop | Hold cone until StartJump; EXCLUSIVE vision, event/state-gated | journal StartJump / alignment state | required | fail→resume hop anchor | none |
| **route_complete_park** s1 | set_throttle pct=0 | throttle bind | Cut throttle at the LAST route waypoint | none (blind) | best-effort | retry_from points HERE | **DIVERGENCE: test asserts retry_from=='nav_panel_target'; TOML=='set_throttle' → RED** |
| **route_complete_park** s2 | scoop_refuel approach_pct=25 standoff_frac=0.80 budget_s=300 refuel_below=0.99 | throttle + approach | Best-effort fuel pit-stop at parked star; skip/fail never blocks orbit | Status Fuel / scoop-rate window (fails closed, best-effort) | best-effort | skip_to / fall through to orbit | none (test treats scoop as scaffolding) |
| **route_complete_park** s3 **(COMMENTED OUT)** | nav_panel_target required=true | nav-panel UI macro | INTENDED: identity-verified star lock before orbit | would be GuiFocus + compass CV; currently NONE (line commented) | required (intended) — absent | step does not run | **MAJOR DIVERGENCE: TOML line 40 commented `# { action="nav_panel_target", required=true }`. 3 tests RED. Orbit now runs with NO verified star lock** |
| **route_complete_park** s4 | sc_assist_orbit (best-effort) | SC-assist toggle macro | EXCLUSIVE: guarded SC-assist orbit; terminal parked end-state | scene guard (fails closed), best-effort | best-effort | degrade-friendly; does not abort | runs WITHOUT the commented-out preceding star lock → a wrong lock can reach the orbit (exactly what the lock step prevents) |
| **route_complete_park** s5 **(MISSING)** | wait (settle) | n/a | INTENDED: settle then STOP | n/a | n/a | n/a | DIVERGENCE: test expects a trailing wait (5 steps); TOML has none |
| **dock_resume** s1 | auto_launch required=true | S,S,Space (UI_Down×2,UI_Select) | EXCLUSIVE: auto-launch off pad when NEW route plotted while Docked (pit-stop) | Undocked event OR Status.docked→false; already-undocked instant success | required | NOT in retry lane (retry_from=target_next_route); pre-launch fail still bounded-retries from target_next_route | none (no wiring test for dock_resume) |
| **dock_resume** s2 | wait_masslock_clear required=true | n/a | Block until station FsdMassLocked (bit 16) clears while ADC flies clear; no timer | Status FsdMassLocked==false | required | retry_from=target_next_route | none (300s wall-clock FAIL backstop only) |
| **dock_resume** s3 | target_next_route required=true | select-next-system | Lock NEXT SYSTEM in resumed route; retry_from anchor | NavRoute next-hop present; gated | required | retry_from=target_next_route | none (no retry_anchor flag; referenced by action name) |
| **dock_resume** s4 | set_throttle pct=100 | throttle bind | Burn out from station | none (blind) | best-effort | tap | none |
| **dock_resume** s5 | wait s=13.0 | n/a | Clear the station; pacing | none (TRAJECTORY PACING, NOT a gate) | n/a | passive | fixed 13s wall-clock (commented pacing) |
| **dock_resume** s6a | orient_compass required=true | pitch/yaw closed-loop | Coarse-align to hop; EXCLUSIVE vision | compass CV read | required | retry_from=target_next_route | none |
| **dock_resume** s6b | orient_widget_ring required=true | pitch/yaw closed-loop | Fine align; no-op unless enabled; EXCLUSIVE vision | widget-ring CV read | required | retry_from=target_next_route | none |
| **dock_resume** s7 | engage_jump required=true | FSD jump bind | Fire FSD jump on resumed leg | Status FsdMassLocked / FSD-ready bits (fails closed) | required | retry_from=target_next_route | none |
| **dock_resume** s8 | hold_alignment required=true | pitch/yaw closed-loop | Hold cone until StartJump; EXCLUSIVE vision, event/state-gated | journal StartJump / alignment state | required | retry_from=target_next_route | none |
| **dock** (terminus) | dock_target_station required | SelectTarget (T); fallback request_docking macro (FocusLeftPanel,CycleNextPanel×2,pin,UI_Select,UI_Right,UI_Select,FocusLeftPanel) | EXCLUSIVE: ensure STATION is active target before SC-assist | Status.Destination is named non-star body (_dest_is_named_station); already-locked guard skips T | required | False on KeyError/focus-fail/both-miss→caller policy | none (already-locked guard added 2026-06-08 fixed untarget-on-arrival) |
| **dock** (terminus) | dock_sc_assist required | nav-panel SC-assist macro | EXCLUSIVE: engage SC-assist toward station, wait for drop | SupercruiseExit OR in_supercruise→false; refuses if not in SC | required | False if not in SC/focus/KeyError/watchdog/abort | max_approach_s=600 FAIL backstop; dock_approach (step 3, merged) closes to <7.5km after this drop |
| **dock** (terminus) | dock_request required | request_docking macro (FocusLeftPanel,CycleNextPanel×2,pin,UI_Select,UI_Right,UI_Select,FocusLeftPanel) | EXCLUSIVE: request docking inside 7.5km NFZ, gate on grant | ReceiveText(NoFireZone) or (normal-space + station targeted) to arm; then DockingGranted OR Status.docked; DockingDenied reason via supplier | required | False on abort/no-range/KeyError/DockingDenied(Distance=retry, other=exhaust to human)/watchdog | max_wait_s=120 FAIL backstop; clears stale DockingDenied on arm (B1/D1 fix) |
| **dock** (terminus) | dock_await_docked required | n/a (pure wait) | Wait for ADC to land ship on pad | Docked event OR Status.docked (bit 0); already-docked instant success | required | False on abort/watchdog | max_wait_s=300 FAIL backstop |
| **dock** (terminus) | station_services (best-effort) | UI_Up, UI_Right×2, UI_Select per service | Auto-opened Starport Services: refuel/repair/rearm | per-service journal (RefuelAll/RepairAll/BuyAmmo, each carries Cost); missed=no-op not failure | best-effort | False only on KeyError mid-macro/abort; else True | services_settle_s=2.0 is a documented press-TIMING settle (UI grayed ~2s), NOT a gate |
| **honk** s0 (parallel track) | ensure_analysis_mode required=true | PlayerHUDModeToggle (M 0x32) | Switch HUD to ANALYSIS (honk only fires there; PrimaryFire outside = LIVE WEAPONS) — load-bearing, first | Status analysis_mode (bit 27); already set→no-op success | required | required fail aborts the parallel honk track; does NOT gate host proc's main lane | none |
| **honk** s1 (parallel track) | hold_until_event bind=PrimaryFire event=FSSDiscoveryScan | PrimaryFire held (Numpad_Subtract 0x4A) | Hold fire-group trigger (discovery scanner) until FSSDiscoveryScan logs (~5s), then release; release log-gated | journal FSSDiscoveryScan; max_hold_s≈30 = key-RELEASE safety backstop (operator-kept, documented) | best-effort | backstop releases key if event never arrives; no retry, track ends | none. Historical: old ExplorationFSSDiscoveryScan (Key_Equals) only worked in FSS screen; fixed 2026-06-06 via fire-group trigger |
| **registry** | press | any bind→scancode (e.g. PrimaryFire=0x4A) | Single atomic keypress, hold default 0.05s | none (blind) | n/a | False only on KeyError | **UNUSED — referenced by NO procedure; kept as building block / unit-test surface** |
| **registry** | wait | n/a | Passive sleep s sec via ctx.sleeper | none (blind, always True) | best-effort | never fails | none (sleep-only, not a gate) |
| **registry** | set_throttle | SetSpeed{Zero/25/50/75/100}=X/'/[ /C/V | Set throttle to pct bucket | none (blind) | required (procs open with it) | False on bad pct or KeyError | none |
| **registry** | pitch | PitchUpButton(S, dir=up)/PitchDownButton(W) | Hold pitch up/down for hold_s | none (blind) | n/a | False on KeyError | **UNUSED — no proc uses action="pitch"; orphaned blind timed-pitch superseded by vision-gated pitch_compass** |
| **registry** | target_ahead | SelectTarget (T) | Lock body ahead; nothing ahead → CLEARS target | none (blind) | best-effort | False on KeyError | blind toggle untargets with nothing ahead; NO already-locked guard (unlike dock_target_station) |
| **registry** | target_next_route | TargetNextRouteSystem (H) | Cancel SC-assist AND lock next route star; StarClass danger fail-closed | new FSDTarget event OR Status.Destination+NavRoute StarClass; empty/origin-only NavRoute fast-fail | required | False on dangerous class/no-route/abort/watchdog | watchdog_s=60 = operator-sanctioned stuck-state backstop, not a success gate |
| **registry** | engage_jump | SetSpeed100 (V) then Hyperspace (K) | Throttle 100% then fire hyperspace | pre-check Status (docked/charging/cooldown/mass_locked/overheating) blocks; press blind | required | False if blocking flag set or KeyError | **NO in_supercruise guard despite MEMORY note mass-lock won't save a real-space engage; leans on hold_alignment downstream gate** |
| **registry** | engage_supercruise | Supercruise (J), skippable | Press SC, optionally re-press in exclusion zone; until_charging treats live charge as success | SupercruiseEntry OR in_supercruise; fail=FsdCharging true→false | best-effort | False on charge_dropped/watchdog/presses_exhausted/abort | max_charge_s=60 + between_press_s=8 = operator-sanctioned watchdog/cadence |
| **registry** | ensure_analysis_mode | PlayerHUDModeToggle (M) | Ensure ANALYSIS HUD mode | Status analysis_mode (bit 27); already set→no-op | required (honk) | False when status unwired or max_toggles exhausted | settle_polls*poll_s = post-toggle Status-latency settle (not a gate) |
| **registry** | wait_cooldown_clear | n/a | Block until FsdCooldown clears (replaces fixed-sec sleep) | Status fsd_cooldown==false | required (smack) | False if status unwired or abort | **none — UNBOUNDED state wait, NO wall-clock backstop at all** |
| **registry** | hold_until_event | bind down→event→up (try/finally) | Key DOWN, wait for journal event, key UP (always released) | journal event param via ctx.event_waiter | best-effort (honk) | False if event not seen before max_hold_s; key always released | max_hold_s=30 IS the success/fail return — operator reviewed+kept as key-RELEASE safety (documented exception) |
| **registry** | sc_assist_orbit | macro: FocusLeftPanel(1),UI_Select(Enter),UI_Right(D),UI_Select,FocusLeftPanel (+UI_Back Grave) | Engage SC-Assist on locked star (orbit) | PRE: in_supercruise + dest local star; POST: still in_supercruise | required (arrival/route_complete_park/startup) | False if not SC/wrong target/focus stuck/KeyError/SC drop | POST-check limited to "still in SC" — actual engagement unproven |
| **registry** | nav_panel_target | macro: FocusLeftPanel, UI_Down(S)/UI_Up(W) pin, UI_Down walk, UI_Select×2, FocusLeftPanel (+UI_Back) | Lock arrival star via nav panel, scroll past beacon/station rows | vision compass dot (lock signal) + Status.Destination.Name==local star identity | required (arrival/route_complete_park/smack/startup) | False on KeyError/focus/rows+toggles exhausted; blind single-run fallback when vision unwired | populated-system mislock risk: identity-unknowable path (supplier=None) accepts any dot-showing lock; secondary-star name-match only handles single trailing letter (multi-char 'Foo AB' falls through). pin_hold_s=4 is a HELD duration not a gate |
| **registry** | orient_compass | Pitch/Yaw via align_to_target (S/W/A/D) | Closed-loop coarse-align compass dot onto target | vision align_to_target outcome.aligned; SC-lost abort guard if started in SC; 45s timeout / 40 max_iters failsafe | required (every jump lane) | False if vision unwired/focus/not aligned; FAIL-CLOSED | none — decisive-astern damp fixed 2026-06-07 (was: fill=0.161 damped, 21 blind iters) |
| **registry** | orient_compass — hysteresis | n/a | Median front_fill over 7 samples; fill∈[0.35,0.65] holds prior verdict | _FILL_BAND_LO=0.35, _FILL_BAND_HI=0.65; prev_in_front threaded via `last` | best-effort | damped beat presses nothing; loop continues | none |
| **registry** | pitch_compass | PitchUp(S)/PitchDown(W)/YawLeft(A)/YawRight(D) | Pitch target's compass dot to a gate (edge≈90° or behind+centered) | vision align._measure dot at gate (magnitude vs edge_frac/center_frac, in_front) | best-effort (smack only) | False on vision unwired/focus/timeout/max_iters; fails closed w/o vision | **WALL-CLOCK VIOLATION: timeout_s=30.0 returns False on a VISION loop (PitchCompassTimeout) — NOT documented as a sanctioned backstop; clearest rule-violation candidate. PLUS until='behind' false-positive: no `behinds` counter mirroring `fronts`; a single false-behind read at mag≤center_frac fires gate prematurely** |
| **registry** | hold_alignment | micro pitch/yaw via align._correct (gentler than orient) | Maintain alignment during FSD spool until jump commits | journal until_event (StartJump default) OR state (fsd_jump/in_supercruise); fail=FsdCharging dropped or FsdCooldown-before-charge | required (every jump lane) | False on vision/waiter/status unwired/focus/charge_dropped/refused_cooldown/watchdog/abort; ValueError on samples<1/poll_s<=0 | max_charge_s=60 = operator-sanctioned stuck-FSD watchdog (NOT the banned success-window); _HOLD_GRACE_POLLS=3 race-absorb |
| **registry** | orient_widget_ring | YawL/R, PitchU/D via _correct_widget_ring (delta sign, NO inversion) | FINE align after orient_compass — drive reticle ring onto mouse widget | vision widget_ring_reader.read().aligned; SC-lost guard fails closed regardless of on_miss | required (every jump lane) but degrades by default | miss→ctx.widget_ring_on_miss ('degrade'=True skips fine / 'fail_closed'=False); flag off→no-op True; SC-lost→always False | **WALL-CLOCK: timeout_s=18.0 → on timeout returns degrade policy (True default); angular_coverage gate 0.5 can false-not_found a partial-arc reticle → SILENT skip, no operator alert** |
| **registry** | scoop_refuel | SetSpeed25/SetSpeed0 taps | Pit-stop: fly into scoopable star, scoop to standoff rate, drink to full | Status scooping_fuel + FuelMain rate; skip gates on fuel/capacity/scoop-facts/star-class/jump-age | best-effort (arrival/route_complete_park; skip/DONE→True) | False on throttle-bind missing/abort/stalled/budget backstop (throttle zeroed) | budget_s=300 = operator-mandated FAIL backstop only, never a success gate |
| **registry** | dock_target_station | SelectTarget (T); fallback request_docking macro | Ensure station is active target before SC-assist | Status.Destination named non-star body; already-locked guard skips T | required (dock) | False on KeyError/focus/both-miss; press-only fallback w/o status | none (already-locked guard 2026-06-08 fixed untarget-on-arrival) |
| **registry** | dock_sc_assist | nav-panel SC-assist macro | Engage SC-assist toward station, wait for drop | SupercruiseExit OR in_supercruise→false; refuses if not in SC | required (dock) | False if not SC/focus/KeyError/watchdog/abort; engage-only w/o journal/status | max_approach_s=600 FAIL backstop only |
| **registry** | dock_request | request_docking macro | Request docking inside 7.5km NFZ, gate on grant | ReceiveText(NoFireZone) or state to arm; DockingGranted OR Status.docked; DockingDenied via supplier | required (dock) | False on abort/no-range/KeyError/DockingDenied/watchdog | max_wait_s=120 FAIL backstop; clears stale DockingDenied on arm (B1/D1) |
| **registry** | dock_await_docked | n/a (pure wait) | Wait for ADC to land on pad | Docked event OR Status.docked (bit 0); already-docked instant | required (dock) | False on abort/watchdog; pass-through w/o journal/status | max_wait_s=300 FAIL backstop |
| **registry** | station_services | UI_Up(W), UI_Right(D)×2, UI_Select per service | Run Starport Services refuel/repair/rearm | per-service journal (RefuelAll/RepairAll/BuyAmmo); missed=no-op | best-effort (dock) | False only on KeyError mid-macro/abort | services_settle_s=2.0 documented press-TIMING settle, NOT a gate; verify_s=8 bounded per-service wait |
| **registry** | auto_launch | UI_Down(S)×2, UI_Select(Enter) | Pit-stop AUTO LAUNCH off pad | Undocked event OR Status.docked→false; already-undocked instant | required (dock_resume) | False on KeyError/abort/watchdog; presses-only w/o journal/status | max_wait_s=300 FAIL backstop |
| **registry** | wait_masslock_clear | n/a | Block until FsdMassLocked clears after auto-launch | Status fsd_mass_locked==false | required (dock_resume) | False if status unwired/abort/watchdog | max_wait_s=300 FAIL backstop |
| **dispatcher** | catch-up latch → _maybe_startup | n/a | First empty poll sets _caught_up; calls _maybe_startup once; thereafter every event→dispatch() | hub.poll returns [] (backlog drained=LIVE); _caught_up; _startup_done one-shot | required | empty poll sleeps poll_interval_s, re-loops; pre-catch-up events update state only, never dispatch | none |
| **dispatcher** | docked-on-load short-circuit | n/a | Return immediately, no procedure — nothing to escape parked at station | Status.docked true | required | skip_to (return; _startup_done set, never re-fires) | none |
| **dispatcher** | _is_parked_terminal idle | n/a | Restart parked at completed route's dest idles (RouteCompleteIdleOnRestart) instead of re-running arrival | in_supercruise; NavRoute route==[] (affirmatively empty); Destination local primary/secondary star OR nothing locked | required | skip_to; fails closed to arrival on route=None/missing/non-empty | getattr-default trap (falsy None must not pass as empty) fixed 2026-06-07; relies on durable NavRoute.json reader |
| **dispatcher** P1 | INDETERMINATE → arrival | n/a | Fail-safe arrival (orbit get-around) when scene unjudgeable. ArrivalOnRestart reason=indeterminate | in_supercruise; _destination_is_local_star()==None OR Destination==None | required | retry_from=arrival; smack-preemptible | none |
| **dispatcher** P2 | dest IS local star → arrival | n/a | Genuine nose-on-star needs orbit get-around. reason=local_star | in_supercruise; _destination_is_local_star()==True | required | retry_from=arrival; smack-preemptible | none |
| **dispatcher** P3 | fresh-arrival smack guard → arrival | n/a | Jump just happened; ED pre-loaded NEXT hop into Destination so star-lock reads False though physically nose-on. reason=fresh_arrival | in_supercruise; near_star==False; jump_age None OR ≤ FRESH_ARRIVAL_WINDOW_S (30.0s) | required | retry_from=arrival; smack-preemptible | 30s window = DELIBERATE exception (classifier heuristic, not a success gate). **This guard was too-narrow in the 2026-06-08 defect: an 88s-stale star-parked ship sailed past (88>30) into P4** |
| **dispatcher** P4 | stale loiter, confident non-local-star → sc_resume | n/a | sc_resume fast path (throttle+orient+jump, no orbit, no nav_panel_target). Intended for Robigo loiter / named-station. reason=not_local_star | in_supercruise; near_star==False; jump_age > 30s | required | abort/smack — sc_resume in _PREEMPT_ON_SMACK, preempts to smack_recovery AFTER it has already smacked | **CRITICAL 2026-06-08 LIVE ROUTING DEFECT — see Part D #2. Star-parked ship (88s, next-hop pre-locked) → throttle-100 → star smack. No proximity/obstruction gate; trusts dest-class classifier alone** |
| **dispatcher** smacked branch | restart-while-smacked → smack_recovery | n/a | Booted into smacked normal-space; star-astern escape; avoids startup's throttle-100 glare-blind orient | NOT in_supercruise; _smacked (last SC transition=SupercruiseExit BodyType=Star); Status fsd_cooldown still true | required | abort — smack_recovery owns it; stale smack (no cooldown) falls to startup recovery lane | FsdCooldown is the ONLY live discriminator between real exclusion-zone smack (~40s) and clean manual drop (~5s); 2026-06-07 10:05 false positive drove this gate |
| **dispatcher** empty-route guard | no-route-on-startup clean abort | n/a | Return WITHOUT startup on normal-space login with no route; overlay [NO ROUTE]; heat watchdog stays alive | NOT in_supercruise; not smacked-with-cooldown; NavRoute route None/absent/[] (`not route`) | required | abort — clean idle; operator plots route + relaunches | Wolf 359 fresh-login defect (2026-06-08): empty-route login used to fall into startup→target_next_route spun full 60s watchdog with no hop |
| **dispatcher** fall-through | normal-space with route → startup | n/a | Run startup (escape/orient/jump from cold normal-space load with real onward route) | NOT in_supercruise; not docked; not smacked-with-cooldown; NavRoute route truthy (≥1 hop) | required | abort — startup smack-preemptible | none |
| **dispatcher** dispatch | arrival on live FSDJump | n/a | Increment _jumps, overlay 'Jump N', run arrival for mid-route hop arrival | LIVE FSDJump AND _is_route_complete is False; only when _caught_up | required | abort — arrival smack-preemptible; FSDJump during a proc dispatches right after it returns | none |
| **dispatcher** dispatch | route-complete terminal (SUCCESS not abort) | n/a | Consume _navroute_cleared latch; STATION dest→dock+stay; SYSTEM/star/unknown→route_complete_park+hold orbit | FSDJump; _navroute_cleared latched; clear-to-jump ts gap ≤_CLEAR_JOIN_WINDOW_S (60s); final waypoint resolvable; jump SystemAddress==final waypoint addr | required | skip_to — fails closed to normal arrival on any missing piece; dock smack-preemptible | Station leg depends on CAPTURE-AT-PLOT (_dock_target) — LIVE-TEST-GATED/UNCONFIRMED; if plot-sets-Destination.Body doesn't hold, is_station stays False → parks instead (fail-safe) |
| **dispatcher** dispatch | smack_recovery on live SupercruiseExit Body=Star | n/a | Record drop time, run smack_recovery (star-astern escape) for a live smack | LIVE SupercruiseExit AND body_type=='Star'; only when _caught_up | required | retry_from — smack_recovery deliberately NOT in _PREEMPT_ON_SMACK; re-smack is its own retry scene | none |
| **dispatcher** dispatch | pit-stop resume → dock_resume | n/a | New non-empty route plotted WHILE docked = pit stop; run dock_resume. DockPitStopResume | LIVE NavRoute; _docked True; NavRoute route non-empty (empty=clear not plot) | best-effort | skip_to — absent a new route, branch never fires, stays docked (terminus) | none |
| **dispatcher** _record_event_time | _PREEMPT_ON_SMACK preempt (mid-procedure) | n/a | Star smack during a run sets _preempt='star_smack'; current proc aborts at next poll; queued SupercruiseExit dispatches smack_recovery. Relabel [PREEMPTED] | SupercruiseExit body_type=='Star' AND _running_proc∈{arrival,startup,dock,sc_resume} | required | preempt — cooperative, key-release-safe, no thread kill; backlog replay can't trip it | Reactive only — fires AFTER the smack; for sc_resume it's the back-end of the P4 defect (recovery, not prevention) |
| **dispatcher** witchspace latch | witchspace pause (interpreter gate) | n/a | StartJump(Hyperspace) sets _in_witchspace; interpreter PAUSES every step while set (nav-panel/orient scene invalid) | set: StartJump jump_type=='Hyperspace'; clear: FSDJump/SupercruiseEntry/Docked. Event-gated, NO timer | required | abort/pause — belt-and-suspenders clears on SupercruiseEntry/Docked so a missed FSDJump can't wedge | none |
| **dispatcher** heat watchdog | reactive heatsink eject (daemon thread) | DeployHeatSink (Minus 0x0C) | Fire DeployHeatSink the moment OverHeating observed; debounced; skips while a UI macro holds exclusive input | Status overheating flag; input_exclusive()==False; clock debounce | best-effort | skip_to — bind missing logs once + still debounces; pauses (not aborts) under exclusive input | OverHeating means damage ALREADY started (≥1.0); Frontier only writes Heat above an internal cutoff → threshold trigger unreliable; flag-driven "good enough for alpha". Comment: DeployHeatSink tap can desync panel state mid-navpanel; interpreter guards with panel-closed check |
| **keys** | Hyperspace | Key_K → 0x25 | Charge+trigger FSD hyperspace jump | Status FSDJumping / journal FSDJump | required | skip_to | none |
| **keys** | Supercruise | Key_J → 0x24 | Engage/exit supercruise | Status Supercruise / journal SupercruiseEntry | required | skip_to (False on timeout) | none |
| **keys** | SelectTarget | Key_T → 0x14 | Select ahead/current target | none (blind) | required | skip_to | none |
| **keys** | TargetNextRouteSystem | Key_H → 0x23 | Advance nav-route target to next system | none (blind) | required | skip_to | none |
| **keys** | SetSpeedZero | Key_X → 0x2D | Throttle 0% | none (blind) | required | skip_to (False on KeyError) | none |
| **keys** | SetSpeed25 | Key_Apostrophe → 0x28 | Throttle 25% | none (blind) | required | skip_to | none |
| **keys** | SetSpeed50 | Key_LeftBracket → 0x1A | Throttle 50% | none (blind) | required | skip_to | none |
| **keys** | SetSpeed75 | Key_C → 0x2E | Throttle 75% | none (blind) | required | skip_to | none |
| **keys** | SetSpeed100 | Key_V → 0x2F | Throttle 100% | none (blind) | required | skip_to | none |
| **keys** | PitchUpButton | Key_S → 0x1F | Pitch nose UP (note inverted-feel preset) | CV compass read before each press | required | retry_from | held ~0.05-2s; PAUSE=0 safe while hold>0 (always >0 in practice) |
| **keys** | PitchDownButton | Key_W → 0x11 | Pitch nose DOWN | CV compass read | required | retry_from | same back-to-back hold caveat |
| **keys** | YawLeftButton | Key_A → 0x1E | Yaw nose left | CV compass read | required | retry_from | none |
| **keys** | YawRightButton | Key_D → 0x20 | Yaw nose right | CV compass read | required | retry_from | none |
| **keys** | PrimaryFire | Key_Numpad_Subtract → 0x4A | Fire-group trigger; honk in ANALYSIS w/ Discovery Scanner | journal FSSDiscoveryScan gates key_up; AnalysisMode flag first | required | abort (key_up always fires in finally) | PAUSE=0: down/up are separate SendInput; journal poll loop runs between — fine |
| **keys** | PlayerHUDModeToggle | Key_M → 0x32 | Toggle Combat/Analysis HUD | Status AnalysisMode (bit 27); only pressed if not set | required | skip_to (max_toggles guard) | none |
| **keys** | FocusLeftPanel | Key_1 → 0x02 | Open left (navigation) panel | none (blind; cockpit focus restored before call) | required | skip_to | none |
| **keys** | UI_Select | Key_Enter → 0x1C (secondary Key_Space 0x39) | Confirm/select highlighted nav-panel item | panel focus assumed from prior FocusLeftPanel | required | skip_to | none |
| **keys** | UI_Right | Key_D → 0x20 | Move right in nav panel / docking sub-option | panel focus assumed | required | skip_to | Key_D also YawRightButton — legal; ED context router + binds_validate.py partition into separate collision groups |
| **keys** | UI_Up | Key_W → 0x11 | HELD to pin cursor to row 0 (saturates); single-tap nav | panel focus; hold via pin_hold_s | required | skip_to | Key_W also PitchDownButton — same cross-context reuse, legal |
| **keys** | UI_Down | Key_S → 0x1F | Single tap to unwrap pin before holding UI_Up to row 0 | panel focus assumed | required | skip_to | Key_S also PitchUpButton — same cross-context reuse, legal |
| **keys** | CycleNextPanel | Key_E → 0x12 | Cycle left-panel tabs forward (Nav→Contacts for docking) | panel focus assumed | required | skip_to | none |
| **keys** | DeployHeatSink | Key_Minus → 0x0C | Eject heat sink; reactive watchdog | Heat threshold in Status.json (reactive) | required | skip_to (best-effort reactive) | DeployHeatSink tap can desync panel state mid-navpanel; interpreter guards with panel-closed check |
| **keys** | UI_Back | Key_Grave → 0x29 | Back out of open panel, restore cockpit GuiFocus=0 | Status GuiFocus≠0 (pressed until ==0, max_backs=4) | best-effort | skip_to (max_backs exhausted→BindMissing, False) | **NOT in REQUIRED_ACTIONS — a missing bind surfaces only at runtime via logged BindMissing, not at startup validation** |
| **keys** (launcher) | raw:0x48 UpArrow | 0xE0 0x48 EXTENDED | Menu nav up (pre-cockpit main-menu launcher only) | none (blind raw) | n/a | abort | none |
| **keys** (launcher) | raw:0x50 DownArrow | 0xE0 0x50 EXTENDED | Menu nav down (launcher only) | none (blind raw) | n/a | abort | none |
| **keys** (launcher) | raw:0x1C Enter/NumpadEnter | 0x1C OR 0xE0 0x1C per call-site extended flag | Menu confirm (launcher only) | none (blind raw) | n/a | abort | none |
| **dead bind** | IncreaseEnginesPower | Key_UpArrow → 0xE0 0x48 EXT | Would move pip→ENG (pre-pip-rip) | n/a — not called | n/a | n/a | **DEAD post-pip-rip (b687a12); bound + scancode entry, zero Python callers, not in REQUIRED_ACTIONS. Safe to unbind** |
| **dead bind** | ResetPowerDistribution | Key_DownArrow → 0xE0 0x50 EXT | Would reset pips to centre (pip-reset action) | n/a — not called | n/a | n/a | **DEAD post-pip-rip; zero callers. Safe to unbind** |
| **dead bind** | IncreaseWeaponsPower | Key_RightArrow → 0xE0 0x4D EXT | Move pip→WEP | n/a — not called | n/a | n/a | bound, never called, not in REQUIRED_ACTIONS |
| **dead bind** | IncreaseSystemsPower | Key_LeftArrow → 0xE0 0x4B EXT | Move pip→SYS | n/a — not called | n/a | n/a | bound, never called, not in REQUIRED_ACTIONS |
| **dead bind** | SystemMapOpen | Key_Numpad_Divide → 0xE0 0x35 EXT | Open system map | n/a | n/a | n/a | bound, not called, not in REQUIRED_ACTIONS |
| **dead bind** | GalaxyMapOpen | Key_Numpad_Multiply → 0x37 | Open galaxy map | n/a | n/a | n/a | bound, not called, not in REQUIRED_ACTIONS |
| **dead bind** | ExplorationFSSDiscoveryScan | Key_Equals → 0x0D | FSS-screen-only discovery scan | n/a | n/a | n/a | bound, not called; 2026-06-06 probe confirmed FSS-screen-only, never logs from cockpit; PrimaryFire is the honk path |
| **dead bind** | NightVisionToggle | Key_N → 0x31 | Toggle night vision (formerly SelectTarget on Key_N) | n/a | n/a | n/a | bound, not called; SelectTarget moved to Key_T, Key_N repurposed |
| **dead bind** | ToggleCargoScoop | Key_End → 0xE0 0x4F EXT | Toggle cargo scoop | n/a | n/a | n/a | bound, not called, not in REQUIRED_ACTIONS |

---

## PART B — Per-procedure ordered step lists

### arrival.toml — 12 steps
`on_required_fail: retry_from=scoop_refuel, max_retries=3, backoff_s=2.0` · `parallel_tracks=[honk]`
1. `set_throttle pct=0`
2. `scoop_refuel` (best-effort)
3. `nav_panel_target required=false skip_to=target_next_route max_rows=3` (EXCLUSIVE)
4. `sc_assist_orbit` (EXCLUSIVE, best-effort)
5. `wait s=13.0`
6. `target_next_route required=true`
7. `set_throttle pct=100`
8. `wait s=13.0`
9. `orient_compass required=true` (vision)
10. `orient_widget_ring required=true` (vision)
11. `engage_jump required=true`
12. `hold_alignment required=true` (vision)

> Old pitch-astern climb-out and step-3b orient-to-star both DROPPED 2026-06-07. Inline step-number comments in the file are stale/non-contiguous; the array above is authoritative.

### startup.toml — 19 steps (first-try direct lane → recovery lane)
`on_required_fail: retry_from=sc_assist_orbit, max_retries=3, backoff_s=2.0` · `parallel_tracks=[honk]`

**First-try direct lane:**
1. `set_throttle pct=100`
2. `target_next_route required=true`
3. `orient_compass required=true` (vision)
4. `orient_widget_ring required=true` (vision)
5. `engage_jump required=true`
6. `hold_alignment required=true` (vision)

**Recovery lane** (retry_from lands at `sc_assist_orbit`/step 11; pre-anchor fails restart there):
7. `target_ahead` (deselect)
8. `set_throttle pct=100`
9. `engage_supercruise required=true`
10. `nav_panel_target` (best-effort, EXCLUSIVE, max_rows=10)
11. `sc_assist_orbit` (EXCLUSIVE — the retry_from anchor)
12. `wait s=13.0 retry_anchor=true` ← first anchor
13. `target_next_route required=true`
14. `set_throttle pct=100`
15. `wait s=13.0 retry_anchor=true` ← second anchor
16. `orient_compass` **(best-effort — NOT required; the lone anomaly)**
17. `orient_widget_ring required=true` (vision)
18. `engage_jump required=true`
19. `hold_alignment required=true` (vision)

> `pitch_compass(until=behind)` REMOVED 2026-06-08 council (Wolf 359 "pitched 180 away for no reason" defect). Inline comments stale; array authoritative.

### sc_resume.toml — 6 steps
`on_required_fail: retry_from=target_next_route, max_retries=3, backoff_s=2.0` · `parallel_tracks=[honk]`
1. `target_next_route required=true`
2. `set_throttle pct=100`
3. `orient_compass required=true` (vision)
4. `orient_widget_ring required=true` (vision)
5. `engage_jump required=true` (distance-based obstruction fail-closed)
6. `hold_alignment required=true` (vision)

> Deliberately skips nav_panel_target + sc_assist_orbit — no orbit get-around. This is why P4 routing into it on a nose-on-star scene smacks.

### smack_recovery.toml
`on_required_fail: retry_from=set_throttle(step 0), retry_from_if_supercruise=target_next_route, max_retries=3, backoff_s=2.0` · `parallel_tracks=[honk]`
0. `set_throttle pct=0` (real-space restart anchor)
1. `nav_panel_target required=true` (EXCLUSIVE)
2. `set_throttle pct=75` ⚠ test expects 100
3. `pitch_compass until=behind required=true center_frac=0.35 timeout_s=75 max_iters=40` ⚠ **2026-06-08 live flip defect**
4. `wait_cooldown_clear required=true`
5. (4.5) `target_ahead` (deselect)
6. `engage_supercruise required=true until_charging presses=3 between_press_s=15 max_charge_s=240`
7. `set_throttle pct=100` ⚠ ordering divergence (before orient/hold)
8. `target_next_route required=true retry_anchor=true` ⚠ **duplicate anchor #1**
9. (5.5) `orient_compass required=true` (escape-vector center)
10. (5.6) `hold_alignment until_event=SupercruiseEntry required=true`
11. (6) `target_next_route required=true retry_anchor=true` ⚠ **duplicate anchor #2 — SC-segment anchor**
12. (7) `set_throttle pct=100`
13. (7.5) `wait s=13.0`
14. (8) `orient_compass required=true`
15. (9) `orient_widget_ring required=true`
16. (10) `engage_jump required=true`
17. (11) `hold_alignment required=true`

> Test `test_v7_step_order` expects `pips_engines` (≈step 0.6) and `reset_power_distribution` (≈step 7b) — NEITHER exists (pip mgmt scrapped). Escape-vector ordering and the duplicate retry_anchor both diverge from the v7 spec. RED on purpose; #31 reconciliation.

### route_complete_park.toml — 3 live steps (2 declared + 1 disabled + 1 missing)
`on_required_fail: retry_from=set_throttle, max_retries=3, backoff_s=2.0` · `parallel_tracks=[honk]`
1. `set_throttle pct=0` ⚠ test expects retry_from=='nav_panel_target'
2. `scoop_refuel approach_pct=25 standoff_frac=0.80 budget_s=300 refuel_below=0.99` (best-effort)
3. ~~`nav_panel_target required=true`~~ **COMMENTED OUT (TOML line 40) — orbit runs with NO verified star lock**
4. `sc_assist_orbit` (best-effort, EXCLUSIVE) — terminal parked end-state
5. ~~`wait` (settle)~~ **MISSING — test expects a trailing settle wait**

### dock.toml (terminus) — 6 steps (dock_approach merged on master)
`on_required_fail: retry_from=dock_approach, max_retries=3, backoff_s=2.0` · `parallel_tracks=[honk]`
1. `dock_target_station required` (EXCLUSIVE)
2. `dock_sc_assist required` (EXCLUSIVE)
3. `dock_approach required` (EXCLUSIVE) — close from SC-assist dropout to <7.5km, gated on `ReceiveText $STATION_NoFireZone_entered;`
4. `dock_request required` (EXCLUSIVE)
5. `dock_await_docked required`
6. `station_services` (best-effort, EXCLUSIVE)

> `dock_approach` is present on master (branch `dock-approach-fix` merged). Not yet live-tested end-to-end.

### dock_resume.toml — 9 steps
`on_required_fail: retry_from=target_next_route, max_retries=3, backoff_s=2.0` · `parallel_tracks=[honk]` · no wiring test exists
1. `auto_launch required` (EXCLUSIVE; S,S,Space)
2. `wait_masslock_clear required`
3. `target_next_route required` (retry_from anchor by name)
4. `set_throttle pct=100`
5. `wait s=13.0`
6a. `orient_compass required` (vision)
6b. `orient_widget_ring required` (vision)
7. `engage_jump required`
8. `hold_alignment required` (vision)

### honk.toml — parallel track (parallel=true), runs alongside every flight procedure
0. `ensure_analysis_mode required=true` (load-bearing first — PrimaryFire outside ANALYSIS = live weapons)
1. `hold_until_event bind=PrimaryFire event=FSSDiscoveryScan` (best-effort; ~5s hold, log-gated release, max_hold_s≈30 key-release safety)

---

## PART C — Dispatcher restart-routing decision tree

`_maybe_startup` fires ONCE (`_startup_done` one-shot) on the first empty poll (= backlog drained = LIVE). Strict highest-priority-first; the first matching branch returns. Pre-catch-up replay events update state only — they never dispatch.

```
_maybe_startup
│
├─ st is None?                    ──────────────────────────► return  (no Status.json yet)
├─ Status.docked?                 ──────────────────────────► return  (parked at station, nothing to escape)
│
├─ Status.in_supercruise?  ── YES ─►
│     │
│     ├─ _is_parked_terminal(st)?
│     │     (route==[] affirmatively empty AND dest = local star OR nothing locked)
│     │                            ──────────────────────────► IDLE          (RouteCompleteIdleOnRestart)
│     │
│     ├─ P1: near_star is None OR Destination is None
│     │                            ──────────────────────────► arrival       (indeterminate, fail-safe; smack-preemptible)
│     │
│     ├─ P2: near_star is True  (dest == local primary/secondary star)
│     │                            ──────────────────────────► arrival       (genuine nose-on, orbit get-around)
│     │
│     ├─ P3: near_star==False AND (jump_age None OR ≤ 30s)
│     │                            ──────────────────────────► arrival       (FRESH-ARRIVAL SMACK GUARD)
│     │
│     └─ P4: near_star==False AND jump_age > 30s
│                                  ──────────────────────────► sc_resume     ◄── ★ DEFECT LANE (throttle+orient+jump, NO orbit)
│
└─ NOT in_supercruise  ─►
      │
      ├─ _smacked AND Status.fsd_cooldown?
      │                              ─────────────────────────► smack_recovery  (star-astern escape)
      │     (FsdCooldown = the only live discriminator: real exclusion-zone smack ~40s vs clean manual drop ~5s)
      │
      ├─ route is None/absent/[]  ('not route')
      │                              ─────────────────────────► IDLE clean abort  (NoRouteOnStartup, overlay [NO ROUTE])
      │
      └─ route truthy (≥1 onward hop)
                                     ─────────────────────────► startup        (smack-preemptible)
```

**Live-event dispatch (after catch-up, every event → `dispatch()`):**
- `FSDJump` + `_is_route_complete==False` → **arrival** (mid-route hop; increments _jumps)
- `FSDJump` + `_navroute_cleared` latched + final-waypoint match → **route-complete terminal**: STATION dest → **dock** (then stay docked); SYSTEM/star/unknown → **route_complete_park** (hold orbit)
- `SupercruiseExit` Body=Star → **smack_recovery** (deliberately NOT in _PREEMPT_ON_SMACK)
- `NavRoute` non-empty while `_docked` → **dock_resume** (pit-stop resume)
- `SupercruiseExit` Body=Star DURING a run of {arrival,startup,dock,sc_resume} → **_PREEMPT_ON_SMACK**: set `_preempt='star_smack'`, current proc aborts at next poll, queued SupercruiseExit dispatches smack_recovery (cooperative, key-release-safe; reactive — fires AFTER impact)
- `StartJump`(Hyperspace) → **witchspace pause latch** (interpreter pauses all steps; clears on FSDJump/SupercruiseEntry/Docked)

**The 2026-06-08 defect trace (88s-stale star-parked ship, in SC, Destination=pre-loaded next hop):**
`_is_parked_terminal`=FALSE (route not affirmatively empty) → P1 NO (dest present, system known, near_star a real bool) → P2 NO (near_star==False, dest is the next hop not local star) → **P3 MISS (jump_age 88s > 30s)** → **P4 → sc_resume → throttle-100 → drives nose-on into the arrival star → smack**. _PREEMPT_ON_SMACK then hands to smack_recovery — recovery, never prevention.

---

## PART D — Prioritized open defects / regressions (worst first)

### 1. ✅ DOCK LOOP — dock_approach step merged on master  *(CLOSED)*
**Evidence:** `dock.toml` now contains `dock_approach` between `dock_sc_assist` and `dock_request` (step 3 of 6); `step_dock_approach` exists in `steps.py` and is registered. `retry_from=dock_approach` so a DockingDenied(Distance) re-closes from the current position, not from the star. Tests `test_dock_procedure_retry_from_is_dock_approach`, `test_dock_approach_*` cover the fix.
**Status:** **MERGED on master** (branch `dock-approach-fix`). The dock lane is **not yet live-tested end-to-end** — the undock, full-approach, and services sequence needs a live station run to confirm.

### 2. ⛔ SC_RESUME STAR-SMACK ROUTING — P4 sends a parked ship into throttle-100  *(fix-built, UNREVIEWED)*
**Evidence:** Dispatcher P4 (`near_star==False AND jump_age>30s → sc_resume`) classifies "non-local-star Destination + stale jump" as a safe Robigo-style loiter. But a ship parked nose-on/near the arrival star with the *next hop already pre-loaded into Destination* (ED does this immediately post-FSDJump) is indistinguishable under this gate. P3's 30s window is the only barrier; an observed 88s-stale park blew past it (88>30) into P4. `sc_resume` has no orbit get-around → `set_throttle 100` drove the ship straight into the star → `SupercruiseExit Body=Star` smack. `_PREEMPT_ON_SMACK` then handed off to smack_recovery — **recovery, never prevention.** No proximity / row-1-distance / in-supercruise star-obstruction read gates sc_resume; it trusts the dest-class classifier alone. Test `test_sc_proximity_startup` covers a priority-3 smack guard.
**Status:** **open on master.** The dock-approach-fix branch was merged but did NOT include the proximity/smack guard for P4; `FRESH_ARRIVAL_WINDOW_S` remains 30 s and sc_resume still has no orbit get-around. Candidate hardening: gate P4 on a positive proximity signal before the no-orbit fast path, or have sc_resume pitch-the-star-off-screen-first before throttling.

### 3. 🔴 SMACK_RECOVERY pitch_compass declares done without flipping the ship  *(open — #31 council redesign)*
**Evidence (live, 2026-06-08):** `pitch_compass until=behind` (step 3, center_frac=0.35) reported done while the ship was still nose-on — the compass-read success gate fired before the 180° flip completed, so the star was never put astern. Root cause in `steps.py`: `_at_gate` fires on the FIRST `not in_front` read once `magnitude ≤ center_frac`, but there is **no `behinds` consecutive-reads counter** mirroring the `fronts` counter. A single classifier flip to `not-in_front` (front_fill in the noise band) while the dot is near-centre fires the gate prematurely. `align.py` has behind-flicker damping (2 consecutive beats, fill-gated); `pitch_compass` only protects the front→behind transition, not behind→gate. This corrupts everything downstream that assumes star-astern: `target_ahead` deselect (4.5) **re-locks** the star instead of clearing it; the escape-vector spawn (step 5) is invalid.
**Step-level fix (proposed, not built):** add a `behinds` counter requiring 2 consecutive `not-in_front` reads before `_at_gate` returns True for `until='behind'`.
**Status:** **open**, flagged under the #31 smacked-startup recovery council redesign.

### 4. 🔴 pitch_compass timeout_s=30.0 is a wall-clock SUCCESS/FAILURE gate on a vision loop  *(open — rule violation)*
**Evidence:** `step_pitch_compass` returns `False` (PitchCompassTimeout) when its closed-loop CV alignment exceeds `timeout_s=30.0`. Unlike every other wall-clock in `steps.py` (target_next_route watchdog_s=60, engage_supercruise/hold_alignment max_charge_s=60, scoop_refuel budget_s=300, dock_* 600/120/300, auto_launch/wait_masslock_clear 300 — all *documented stuck-state FAIL backstops set far above any real duration*), this one is NOT documented as a sanctioned backstop; it reads as a genuine clock deciding success/failure on a CV step, in direct tension with the `no-arbitrary-timed-waits` rule. Only used by `smack_recovery` (best-effort lane), which bounds the blast radius.
**Status:** **open.** Companion concern: `step_orient_widget_ring timeout_s=18.0` also lets a clock decide "no convergence," but it *degrades* (returns `widget_ring_on_miss`, default True) rather than hard-failing the jump, and the compass coarse stage already gated alignment — softer, but still a clock making a convergence call with no operator alert that the fine pass was silently skipped.

### 5. 🟠 ROUTE_COMPLETE_PARK nav_panel_target commented out — orbit runs with no verified star lock  *(still open on master)*
**Evidence:** master `route_complete_park.toml` line 40 is commented: `# { action = "nav_panel_target", required = true }`. The identity-verified star lock that must precede the orbit never runs; `sc_assist_orbit` may orbit a random body or refuse, and a *wrong* lock can reach the orbit — exactly the case the lock step was meant to prevent. The settle `wait` step is also absent, so the procedure exits immediately. `retry_from=set_throttle` (test expects `nav_panel_target`) means a lock failure retries from throttle-zero, wasting steps. Three tests RED: `test_step_order_is_arrival_front_half_only`, `test_nav_panel_target_is_required_orbit_is_best_effort` (KeyError), `test_retry_anchor_is_the_lock_bounded`.
**Status:** **open on master.** The dock-approach-fix branch was merged (defect #1 closed) but the route_complete_park.toml fix was NOT included — the commented-out `nav_panel_target` line remains on master.

### 6. 🟠 STARTUP wiring divergence — wrong retry anchor, double anchor, missing pip step  *(open — #31 council redesign)*
**Evidence:** three RED tests on master `startup.toml`:
- (a) `test_first_lane_is_direct_jump`: direct lane expected `pips_engines, set_throttle`; TOML starts with `set_throttle` only (pip mgmt ripped).
- (b) `test_recovery_lane_clears_the_star_before_the_hop`: recovery lane expected `pips_engines` between `nav_panel_target` and `engage_supercruise` and **ONE** `wait(retry_anchor)`; TOML has no pips step and **TWO** `wait(retry_anchor)` steps (allowing more re-spins than intended).
- (c) `test_clearance_wait_is_the_only_retry_anchor` / retry_from expected `target_ahead`; TOML `retry_from=sc_assist_orbit`.
**Impact:** on a smacked-startup recovery, failures re-enter at `sc_assist_orbit` rather than the `target_ahead` deselect — **the deselect + throttle steps are skipped, which can leave a target locked while in SC.** Double anchor means the retry budget can be consumed twice.
**Status:** **open**, part of #31. RRT mismatch is a wiring-spec divergence, not a crash — but the live ship runs the wrong recovery sequence.

### 7. 🟠 SMACK_RECOVERY escape-vector segment order + duplicate retry_anchor  *(open — #31 council redesign)*
**Evidence:** master smack_recovery escape-vector segment diverges from the v7 spec:
- ordering: test order is `engage_supercruise → orient_compass → hold_alignment → target_next_route`; TOML is `engage_supercruise → set_throttle(100) → target_next_route(anchor) → orient_compass → hold_alignment → target_next_route(anchor)` — a `set_throttle` + a `target_next_route` moved *in front of* orient/hold, so **orient_compass runs before the SC hop lock is established** (orients to whatever vector is on the compass rather than the confirmed hop).
- **duplicate `retry_anchor=true`:** TWO `target_next_route` steps both carry it; `test_retry_split_real_space_vs_supercruise` and `test_toml_carries_supercruise_retry_key` both assert `len(anchors)==1` → RED.
- `test_v7_step_order` also expects `reset_power_distribution` after the second `target_next_route` — absent (pip rip).
**Status:** **open**, #31 reconciliation. Actively wrong in the live bot at the escape-vector orient/hold segment.

### 8. 🟡 SMACK_RECOVERY throttle pct=75 vs expected 100  *(open — minor, #31 scope)*
**Evidence:** step 2 sets `pct=75` (its own comment says "full burn"); `test_first_throttle_is_zero_then_full_burn_before_the_pitch` asserts `[0,100,100]`; TOML produces `[0,75,100,100]` → RED. During the smack cooldown the ship burns 75% instead of 100% — marginal impact on clearing the exclusion zone but spec-divergent; if 75% is insufficient to reach engage range within the 240s watchdog, the step aborts.
**Status:** **open**, low safety risk, bundled into #31.

### 9. 🟡 nav_panel_target populated-system mislock  *(open — partially mitigated)*
**Evidence:** Layer-1 compass-dot check only proves *a* target is locked, not *which* body. Layer-2 `_destination_is_local_star()` rejects beacons/non-star bodies, but: (a) when status/system is unwired (`ctx.current_system_supplier`→None), `identity_checked=None` and the lock is **accepted on the dot alone** (loudly logged, not blocked) — a beacon or secondary body can slip through; (b) the secondary-star name-match only handles a single trailing letter (`'Foo A'`); a binary pair with a multi-char suffix (`'Foo AB'`) falls through to `ident=False` and triggers a row advance, potentially exhausting `max_rows` on a *valid* secondary.
**Status:** **open** (mitigated by the identity check in the wired path). Related: station-dock detection family — capture-at-plot for the dock-target mechanic is pending Operator's plot-timing answer.

### 10. 🟡 orient_widget_ring silent degrade-on-miss  *(open — by design, no operator alert)*
**Evidence:** Default `ctx.widget_ring_on_miss='degrade'`: if the mouse widget point-mode isn't active, the ring is occluded / HUD colour changed, or the 18s timeout fires, the step returns True and the jump proceeds on compass accuracy alone. The `angular_coverage` gate (`_ANGULAR_COVERAGE_MIN=0.5`) rejects a ring arc < 12/24 sectors; the real open-arc reticle (live-measured 0.917 but known to degrade to partial arcs with the info-block gap + stem) can trip a false `not_found` → degrade=True. Design intent ("a found=False idle beat is safer than steering on a guess"), but it produces a **silent skip with no operator alert that the fine pass was bypassed.**
**Status:** **open** (intentional behaviour; flagged for visibility — no alerting).

### 11. 🟡 engage_jump has no in_supercruise guard  *(open — leans on downstream gate)*
**Evidence:** `step_engage_jump` checks Status block-flags (docked/charging/cooldown/mass_locked/overheating) but has **no `in_supercruise` guard**, despite the MEMORY note that mass-lock won't stop a real-space hyperspace press. It relies entirely on `hold_alignment`'s downstream gate to catch a bad real-space fire.
**Status:** **open** (mitigated downstream; no direct guard).

### 12. 🟡 UI_Back omitted from REQUIRED_ACTIONS  *(open — validation gap)*
**Evidence:** `_ensure_cockpit_focus` presses `UI_Back` (Key_Grave / 0x29), but it is absent from `binds_validate.py REQUIRED_ACTIONS` (23 actions). A missing/unbound UI_Back is **not caught at startup validation** and will only surface at runtime via a logged `BindMissing` — after a panel is already stuck open.
**Status:** **open** (validation gap). `target_ahead` blind-toggle has the same untarget-on-nothing-ahead hazard `dock_target_station` got an already-locked guard for, but `target_ahead` has no such guard (used only in best-effort smack/startup lanes).

---

### Closed / GREEN (confirmed, listed for completeness)
- ✅ **Wolf 359 startup pitch-astern** — `pitch_compass(until=behind)` removed from `startup.toml`; `test_startup_recovery_has_no_pitch_astern` passes on master and dock-approach-fix.
- ✅ **arrival retry_from** — `arrival.toml retry_from==scoop_refuel`, scoop_refuel not required, `budget_s==300.0`; `test_arrival_runs_the_pit_stop_before_the_climb_out` GREEN.
- ✅ **Pip-rip clean in flight procedures** — no `pips_engines` / `reset_power_distribution` references in any of the three flight TOMLs or `STEP_REGISTRY`; the four arrow-key pip binds (ResetPowerDistribution, IncreaseEngines/Weapons/SystemsPower) are dead but harmless (zero callers, not in REQUIRED_ACTIONS, safe to unbind in-game).
- ✅ **dock_target_station untarget-on-arrival** — already-locked guard added 2026-06-08.
- ✅ **DockingDenied stale-clear** — cleared on dock_request arm (B1/D1 fix).

> **Single highest-leverage action: review and merge `dock-approach-fix` (895d833).** It closes #1, #2, and #5 at once. The #31 smacked-startup council redesign is the reconciliation gate for #3, #6, #7, and #8.