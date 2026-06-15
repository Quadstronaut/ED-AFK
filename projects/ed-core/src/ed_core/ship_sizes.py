"""Ship model -> pad size mapping and dock-pitch helpers.

SHIP_SIZE keys are the exact lowercase strings the ED journal emits in the
"Ship" field of LoadGame and Loadout events.  Sizes:
  'S' = small pad   -> 3 s pitch
  'M' = medium pad  -> 4 s pitch
  'L' = large pad   -> 7 s pitch

TOKENS source-verified 2026-06-11 against EDCD FDevIDs shipyard.csv (the
`symbol` column IS the journal Ship field):
https://raw.githubusercontent.com/EDCD/FDevIDs/master/shipyard.csv
The 2026-06-09 draft guessed display-style tokens for ~10 ships
(imperial_eagle, keelback, beluga, the Alliance/Federal trios, ...) — every
one of those would have missed the lookup and silently fallen to the MEDIUM
default. Corrected below; old wrong keys removed.

SIZE anchors (operator-confirmed 2026-06-09):
  mandalay=M  type9=L   type10=L  anaconda=L  federation_corvette=L
  cutter=L    beluga=L  sidewinder=S  eagle=S   hauler=S  adder=S
  viper=S     cobramkiii=S  python=M  krait_mkii=M  asp=M  type6=M
EXCEPTION — type7: the 2026-06-09 anchor list said M, flagged in-session as a
transcription error; community docs are unanimous the Type-7 needs a LARGE
pad (the classic can't-land-at-outposts freighter). Set L pending Operator's nod.
"""

from __future__ import annotations

from typing import Literal, Optional

# ---------------------------------------------------------------------------
# Model -> size table — keys = lowercase FDevIDs journal symbols.
# ---------------------------------------------------------------------------
SHIP_SIZE: dict[str, Literal['S', 'M', 'L']] = {
    # ---- SMALL (S) ----
    'sidewinder':               'S',   # anchor
    'eagle':                    'S',   # anchor
    'hauler':                   'S',   # anchor
    'adder':                    'S',   # anchor
    'viper':                    'S',   # anchor (Viper MkIII)
    'cobramkiii':               'S',   # anchor
    'viper_mkiv':               'S',
    'cobramkiv':                'S',
    'cobramkv':                 'S',
    'empire_eagle':             'S',   # Imperial Eagle
    'empire_courier':           'S',   # Imperial Courier
    'dolphin':                  'S',
    'diamondback':              'S',   # Diamondback Scout
    'diamondbackxl':            'S',   # Diamondback Explorer (small pad!)
    'vulture':                  'S',
    'smallcombat01_nx':         'S',   # Kestrel Mk II (2026)

    # ---- MEDIUM (M) ----
    'mandalay':                 'M',   # anchor (the bot's current ship)
    'python':                   'M',   # anchor
    'krait_mkii':               'M',   # anchor
    'asp':                      'M',   # anchor (Asp Explorer)
    'type6':                    'M',   # anchor (Type-6 Transporter)
    'python_nx':                'M',   # Python MkII
    'asp_scout':                'M',
    'krait_light':              'M',   # Krait Phantom
    'independant_trader':       'M',   # Keelback (Frontier's own misspelling)
    'ferdelance':               'M',   # Fer-de-Lance
    'typex':                    'M',   # Alliance Chieftain
    'typex_2':                  'M',   # Alliance Crusader
    'typex_3':                  'M',   # Alliance Challenger
    'federation_dropship':      'M',   # Federal Dropship
    'federation_dropship_mkii': 'M',   # Federal Assault Ship
    'federation_gunship':       'M',   # Federal Gunship
    'mamba':                    'M',
    'corsair':                  'M',   # Corsair (2025)
    'type8':                    'M',   # Type-8 Transporter
    'lakonminer':               'M',   # Type-11 Prospector (2026)
    'mediumtransport01':        'M',   # Lynx Highliner (2026)

    # ---- LARGE (L) ----
    'type7':                    'L',   # Type-7 Transporter — see docstring EXCEPTION
    'type9':                    'L',   # anchor (Type-9 Heavy)
    'type9_military':           'L',   # Type-10 Defender — anchor (type10=L)
    'anaconda':                 'L',   # anchor
    'federation_corvette':      'L',   # anchor
    'cutter':                   'L',   # anchor (Imperial Cutter)
    'belugaliner':              'L',   # Beluga Liner — anchor (beluga=L)
    'empire_trader':            'L',   # Imperial Clipper (large pad)
    'orca':                     'L',
    'panthermkii':              'L',   # Panther Clipper MkII (2025)
    'explorer_nx':              'L',   # Caspian Explorer (2025)
}

# ---------------------------------------------------------------------------
# Pitch durations by size
# ---------------------------------------------------------------------------
PITCH_S_BY_SIZE: dict[str, float] = {
    'S': 3.0,
    'M': 4.0,
    'L': 7.0,
}

_DEFAULT_PITCH_S = 4.0   # MEDIUM — used when model is None or unknown


def size_for_ship(model: Optional[str]) -> Optional[Literal['S', 'M', 'L']]:
    """Return the pad size for *model*, or None when unknown.

    Lowercases and strips the input; a None or blank model returns None.
    Callers that need a fallback should use pitch_s_for_ship instead."""
    if not model:
        return None
    return SHIP_SIZE.get(model.lower().strip())


def pitch_s_for_ship(model: Optional[str]) -> float:
    """Return the dock-blind-maneuver pitch duration in seconds for *model*.

    Unknown or None model -> _DEFAULT_PITCH_S (MEDIUM, 4.0 s).  Callers
    should log when the default fires so the operator can see the miss."""
    sz = size_for_ship(model)
    if sz is None:
        return _DEFAULT_PITCH_S
    return PITCH_S_BY_SIZE[sz]
