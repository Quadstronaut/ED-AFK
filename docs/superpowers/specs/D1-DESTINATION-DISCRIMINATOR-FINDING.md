# D1 — destination discriminator: the LOCKED chair rule was wrong (2026-06-21, live)

> ⚠️ **UPDATE 2026-06-21 — the NAME-disambiguation below is REFUTED** (`ARRIVAL-STAR-NAMING-FINDING.md`).
> An arrival star can be named ANYTHING, not the system name (LAWD 26 system arrives at `GLIESE 293 B`, a
> Wolf-Rayet). So `Name==system ⇒ star` / `Name!=system ⇒ station` does NOT hold — Name cannot separate star
> from station. Use the **column-0 ✦ star ICON** + the **detail-pane STAR CLASS row**, not the name. The
> `Body==0` (system/next-hop) vs `Body!=0` (specific body) split below still holds.

**Corrects chair-decision D1 / item #6** in `resume-state-2026-06-18-flow-redesign`, which said
"`Destination.Body != 0` ⇒ station, `Body == 0` ⇒ system/star." **That binary is refuted.**
C2's `_dest_is_station` must NOT key on `Body != 0`.

## Observed live (operator-witnessed, Shinrarta Dezhra, Mandalay)

| Locked / route state | `Status.json` `Destination` |
|---|---|
| In-system **star** locked | `{System, Body: 1, Name: "Shinrarta Dezhra"}` (Name = system name) |
| In-system **station** locked (Jameson Memorial) | `{System, Body: 69, Name: "Jameson Memorial"}` |
| **Mid-route**, 5 hops to a far station (Ray Gateway, Diaguandri), docked | `{System, Body: 0, Name: "LHS 1935"}` — the **next-hop system**, not the station |

`NavRoute.json` lists **systems only** (last hop = `Diaguandri`, the *system* — never `Ray Gateway`).
So the route file never encodes the destination station.

## The correct model

- `Body == 0` → a **system** is the current target (an intermediate next-hop, or a system-only
  destination) → park-at-star case.
- `Body != 0` → a **specific body** is locked (star OR station — a star is `Body != 0` too).
  Disambiguate by **Name**:
  - `Name == current-system-name` → it's the arrival **star** → park.
  - `Name != current-system-name` → **station / specific body** → dock / approach.
- The SC-assist button label corroborates: plain **`SUPERCRUISE ASSIST`** = station (drops you there);
  **`SUPERCRUISE ASSIST AND ORBIT`** = orbitable body.

## Arrival behaviour (operator-confirmed game-truth, not freshly re-tested)

When a route is plotted **to a station**, `Status.Destination` shows the next-hop **system** (`Body 0`)
on every intermediate jump, and **flips to the station** (`Body != 0`, Name = station) **once you enter
the final destination system.** So C2 reads the discriminator at **route-complete / final-system arrival**,
not mid-route.

## Implication for C2 `_dest_is_station`

Read `Status.Destination` at route-complete. Station iff `Body != 0` **AND** `Name != currentSystemName`
(current system from the journal `FSDJump`/`Location` `StarSystem`). `Body != 0` alone is NOT sufficient —
it also matches a locked arrival star. Keep it fail-closed (ambiguous → park, never blind-dock).
