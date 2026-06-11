"""Ship model -> pad size mapping and dock-pitch helpers.

SHIP_SIZE keys are the exact lowercase strings the ED journal emits in the
"Ship" field of LoadGame and Loadout events.  Sizes:
  'S' = small pad   -> 3 s pitch
  'M' = medium pad  -> 4 s pitch
  'L' = large pad   -> 7 s pitch

ANCHORS (operator-confirmed, do not change):
  mandalay=M  type9=L   type10=L  anaconda=L  federation_corvette=L
  cutter=L    beluga=L  sidewinder=S  eagle=S   hauler=S  adder=S
  viper=S     cobramkiii=S  python=M  krait_mkii=M  asp=M
  type6=M     type7=M
"""

from __future__ import annotations

from typing import Literal, Optional

# ---------------------------------------------------------------------------
# Model -> size table
# ---------------------------------------------------------------------------
# Mark any entry whose exact journal token is uncertain with # VERIFY so the
# operator can check against a live LoadGame line.
# ---------------------------------------------------------------------------
SHIP_SIZE: dict[str, Literal['S', 'M', 'L']] = {
    # ---- SMALL (S) -- operator-confirmed anchors first ----
    'sidewinder':           'S',   # anchor
    'eagle':                'S',   # anchor
    'hauler':               'S',   # anchor
    'adder':                'S',   # anchor
    'viper':                'S',   # anchor (Viper Mk III)
    'cobramkiii':           'S',   # anchor

    # ---- SMALL (S) -- high-confidence from EDCD/community docs ----
    'viper_mkiv':           'S',
    'cobramkiv':            'S',
    'cobramkv':             'S',
    'imperial_eagle':       'S',
    'imperial_courier':     'S',
    'dolphin':              'S',
    'diamondback':          'S',   # Diamondback Scout (journal: "Diamondback")  # VERIFY exact token vs 'diamondback_scout'
    'diamondbackxl':        'S',   # Diamondback Explorer (journal: "DiamondbackXL" → lowercase)  # VERIFY
    'vulture':              'S',
    'kestrel_mkii':         'S',   # VERIFY (new 2024 ship; journal token unconfirmed)

    # ---- MEDIUM (M) -- operator-confirmed anchors first ----
    'mandalay':             'M',   # anchor
    'python':               'M',   # anchor
    'krait_mkii':           'M',   # anchor
    'asp':                  'M',   # anchor (Asp Explorer, journal: "Asp")
    'type6':                'M',   # anchor (Type-6 Transporter)
    'type7':                'M',   # anchor — NOTE: ships.json has Type-7 as L; OPERATOR RULE takes precedence; VERIFY live journal

    # ---- MEDIUM (M) -- high-confidence ----
    'python_nx':            'M',   # Python Mk II (journal internal name)  # VERIFY exact token ('python_nx' vs 'python_mkii')
    'asp_scout':            'M',   # Asp Scout
    'krait_light':          'M',   # Krait Phantom (journal: "Krait_Light")
    'keelback':             'M',
    'ferdelance':           'M',   # Fer-de-Lance
    'alliance_chieftain':   'M',   # VERIFY exact token
    'alliance_challenger':  'M',   # VERIFY exact token
    'alliance_crusader':    'M',   # VERIFY exact token
    'federal_assault_ship': 'M',   # VERIFY exact token
    'federal_dropship':     'M',   # VERIFY exact token
    'federal_gunship':      'M',   # VERIFY exact token
    'mamba':                'M',
    'corsair':              'M',   # VERIFY (newer ship; token may differ)
    'type8':                'M',   # Type-8 Transporter  # VERIFY exact token ('type8' vs 'type8_transport')
    'type11':               'M',   # Type-11 Prospector  # VERIFY exact token
    'lynx_highliner':       'M',   # VERIFY exact token (newer ship)

    # ---- LARGE (L) -- operator-confirmed anchors first ----
    'type9':                'L',   # anchor (Type-9 Heavy)
    'type9_military':       'L',   # Type-10 Defender (journal internal name) -- anchor (type10=L)
    'anaconda':             'L',   # anchor
    'federation_corvette':  'L',   # anchor
    'cutter':               'L',   # anchor (Imperial Cutter)
    'beluga':               'L',   # anchor (Beluga Liner)

    # ---- LARGE (L) -- high-confidence ----
    'imperial_clipper':     'L',   # VERIFY exact token
    'orca':                 'L',
    'panther_clipper_mkii': 'L',   # VERIFY exact token (very new ship)
    'caspian_explorer':     'L',   # VERIFY exact token (newer ship)
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
