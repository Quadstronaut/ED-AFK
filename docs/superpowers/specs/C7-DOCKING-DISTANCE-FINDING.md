# C7 — station distance source for the docking proximity gate (2026-06-21, live)

Operator-captured at Jameson Memorial (Orbis starport), Mandalay, 1080.
Fixtures: `navpanel_nav_station_km_1080.png` (Navigation tab) +
`navpanel_contacts_request_docking_1080.png` (Contacts tab + REQUEST DOCKING).

## Two places show the station distance — they are NOT equivalent for the bot

1. **Nav-panel NAVIGATION tab** — the selected/highlighted station row's distance shows in the bright
   header bar, far right (`9.66km` here). Other rows list system bodies in Ls (316Ls, 339Ls, …). At close
   range the selected target reads **km**; confirms unit order Km < Mm < Ls < Ly.
2. **Right-side cockpit target panel** (top-right) — `JAMESON MEMORIAL / ORBIS STARPORT / 9.66km`.

## The operational catch (operator recommendation)

The bot presses **REQUEST DOCKING on the CONTACTS tab** (chair-decision #4). On the Contacts tab the
nav-list distance is **gone** — only the **right-side target panel** still shows the km distance. So the
`< 7.5 km` docking proximity gate must read the **right-side target panel**, which is visible across BOTH
the Navigation and Contacts tabs, NOT the nav-list distance (which vanishes exactly where docking happens).

## Per-ship caveat (gap #19)

The right-side panel's screen position is **ship-specific**. Operator: on some ships the viewing angle hides
the right-side distance the way it's visible on the Mandalay here. The right-side-distance crop must come
from the per-ship CV region resolver, fail-closed on an uncalibrated hull. Mandalay layout = the captured one.

## REQUEST DOCKING button (grounds chair-decision #4)

Contacts tab → station selected → header `JAMESON MEMORIAL / ORBIS STARPORT`, `FACTION: PILOTS' FEDERATION
LOCAL BRANCH`, then the orange **REQUEST DOCKING** button. The no-blind OCR-gated docking sequence reads
this button label before pressing.

## Parser implication

The distance parser currently handles Ls/Ly only; it must add a **km** unit (and Mm) for the close-range
station read. Calibrate the km parse to `navpanel_nav_station_km_1080.png` and the right-side-panel crop to
both frames.
