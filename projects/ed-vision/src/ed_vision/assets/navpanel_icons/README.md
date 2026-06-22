# Nav-panel type-icon registry

This directory is the **loaded home** for the nav-panel type-icon templates and
the manifest (`registry.toml`) that maps each glyph to a body **kind** and an
**action**. The route-complete destination router reads the locked (highlighted)
nav-panel row's type-icon, correlates it against every template here, and uses the
winning row's `action` to decide **dock vs park**.

## The action vocabulary (only two)

| action | meaning |
| --- | --- |
| `park` | hold in orbit under SC-assist. Stars, planets, surface points — anything that is **not** an orbital dock. |
| `dock` | run the dock flow. Stations (Coriolis / Orbis / Ocellus / Outpost / Asteroid base), megaships, fleet carriers. |

## Fail-closed: unknown → park

The matcher **abstains-as-park** whenever there is no confident template match
(blank cell, unreadable glyph, score below the floor, or a glyph with no
registry row). The **only** path to a `dock` is a positive, confident match
against a row whose `action = "dock"`. A new/unseen station variant that you have
not added a template for will **park**, never blind-drive — safe by construction.

## Extending it (two steps, no code)

1. **Drop a template PNG** next to this file. Crop it tight to the type-icon glyph
   from a real frame (orange-on-dark; the same crop style as the existing
   `station-*.png`). Real frames beat synthetic shapes.
2. **Add one `[[icon]]` row** to `registry.toml`:

   ```toml
   [[icon]]
   template = "station-newkind.png"   # the file you just dropped (must exist here)
   kind     = "station-newkind"       # any human label
   action   = "dock"                  # "park" | "dock" — nothing else
   notes    = "what this glyph is"
   ```

## Validation (loud, at load)

`load_registry()` **fails loud** (raises `ValueError`) on a bad row:

- an `action` that is not exactly `park` or `dock`,
- a `template` whose PNG file is missing from this directory,
- a missing `template` / `kind` / `action` field.

A malformed registry is a **build error**, never a silent park — so a typo
surfaces in the import-time registry test, not as a mystery on the live ship.

## Files

- `registry.toml` — the manifest (this is the surface you edit).
- `star-unselected.png` / `system-star.png` — production star/system glyphs
  (also used by `navpanel_icons`' incumbent STAR/NON_STAR oracle; one source of
  truth for "what a star is").
- `unexplored-box.png` — used by `navpanel_column0`'s loop-terminator classifier
  (not a registry row).
- `station-*.png`, `planet.png`, `settlement*.png` — reconciled in from
  `projects/ed-autojump/tests/fixtures/navpanel/icons/` (which stays the
  test-only source). The manifest references **only** filenames in this dir.
