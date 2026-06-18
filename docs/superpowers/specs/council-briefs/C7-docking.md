# COUNCIL C7 — DOCKING SCENE (ED-AFK flow redesign, 2026-06-17)

You are a council-v2 instance. **This brief is SELF-CONTAINED and AUTHORITATIVE.** Tier: arch.

> 📌 **ROUND-2 PINNED (operator 2026-06-18) — apply, do not re-litigate:**
> - **Request-docking sequence is OCR-GATED, NOT blind.** `1` (open nav panel) → `E` → `E` (page right to
>   the Contacts tab) → `D` (cursor right onto the REQUEST DOCKING button) → `space` (select) →
>   `set_throttle 0`. **OCR-confirm the REQUEST DOCKING button label before pressing** (no-blind spec). It
>   is a full open-from-scratch sequence — do NOT assume the panel is already open or a row pinned.
> - **NFZ ≠ docking-ready.** Entering the no-fire zone does NOT mean close enough to dock. The docking
>   trigger is the **proximity gate** (OCR distance-to-station < 7.5 km). The `ReceiveText
>   $STATION_NoFireZone_entered` journal event is a SEPARATE fire-safety concern — keep both, distinct; do
>   NOT collapse the proximity loop into the NFZ event.
> - **Station-name source = `Status.Destination.Name`** (logging-only; flow never consumes it downstream).
> - **STILL OWED:** a contacts-tab station-distance frame at approach range + a km/Mm distance parser
>   calibrated to it (READ layer only parses Ls/Ly today). nav_target_star / nav_supercruise_target come
>   from C1 (blocked on the detail-page frame).

## Binding standing rules
- **DESIGN-ONLY.** Ratified design + Operator-blocker list. Do NOT build, edit flight code, or commit.
- **NO GUESSING.** Unknown not settled in the cited sources → `BLOCKED-ON-KYLE: <question>`. Read repo +
  community ED docs (Status.json + journal schema, request-docking flow) before asserting.
- Honor `no-arbitrary-timed-waits` except where the operator explicitly wrote `wait Ns`.

## Shared context — read FIRST
- `docs/superpowers/specs/2026-06-17-flow-redesign-MASTER-SPEC.md` (operator intent + settled truths).

## YOUR SCOPE — design the DOCKING scene exactly as authored
1. `wait 1s`
2. Check `Status.json`.
3. **if** destination == system → `nav_supercruise_star`; **bot goes IDLE (no further execution).**
4. **elif** destination == station →
   1. `nav_supercruise_target` (station row)
   2. wait for the journal entry that drops out of supercruise (`SupercruiseExit`)
   3. get station name from `Status.json` or journal — **BLOCKED-ON-KYLE / READ-DOCS:** determine the
      AUTHORITATIVE source + exact field (do NOT guess).
   4. `boost`
   5. `set_throttle 50`
   6. nav-panel target the station
   7. nav-panel OCR distance to station, LOOP until **< 7.5 km** (use the highlight-to-read technique +
      the live READ layer)
   8. send `E` bind → `wait 0.5s` → send `E` bind → `wait 0.5s` → send `D` bind → send `spacebar` bind →
      `set_throttle 0`  — operator-specified EXACT sequence; design it as written. **Confirm each bind
      exists via `binds_validate`; flag any missing as BLOCKED-ON-KYLE.** Identify what E/E/D/space map to
      in the ED-AFK preset (UI navigation to request-docking?) from the binds, do not assume.
   9. done — autodock takes over.

`nav_supercruise_target` + the nav-panel station target come from council **C1** — design against their
contract and flag assumptions. The `destination==system|station` discriminator + entry from Arrival's
branch are council **C2**.

## Ground in
- `projects/ed-autojump/src/ed_autojump/flow/steps.py` (existing dock_* steps: dock_target_station,
  dock_sc_assist, dock_approach, dock_request, dock_await_docked, dock_blind_maneuver — RECONCILE the
  operator's flow against these; state what's replaced/kept).
- `projects/ed-core/src/ed_core/executor/navpanel.py` (CycleNextPanel → Contacts tab for the station).
- `projects/ed-core/src/ed_core/binds_validate.py` + bundled binds (the E/E/D/space sequence).
- `ed_vision/ocr_winrt.py` + `navpanel_reader.py` (distance OCR for the <7.5km gate).

## Deliverable
The DOCKING scene DESIGN as a proposed `.toml` sketch inside the doc (no real file) + the bind-mapping
for E/E/D/space + a `BLOCKED-ON-KYLE:` list (station-name source + any missing bind are the headline ones).
Do NOT modify flight code.
