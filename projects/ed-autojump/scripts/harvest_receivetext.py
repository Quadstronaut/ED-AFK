r"""ReceiveText harvester — self-built catalog of ED NPC/comms `$token;` strings.

WHAT: scans every Elite Dangerous `Journal.*.log` for `ReceiveText` events and
maintains a growing JSON collection of the `$token;` message keys (the
`Message` field, e.g. `$Pirate_OnStartScanCargo07;`). No public catalog of
these exists, so we self-harvest from our own journals. The catalog later feeds
bot reaction triggers (piracy / aggression / police / station).

WHY token-keyed (not raw-message-keyed): many tokens carry a runtime parameter
payload after a `:#`, e.g. `$COMMS_entered:#name=Wregoe IE-G d11-10;`. The
SYSTEM name is variable; the token identity is `$COMMS_entered;`. We normalize
by stripping everything from the first `:#` onward (re-appending `;`), collapsing
~970 raw variants into ~46 real tokens. Without this the catalog would be 95%
near-duplicate per-system-name rows.

DESIGN (stdlib only, apart from an OPTIONAL ed_core config import):
  * Journal dir comes from the bot config (`cfg.paths.journal_dir_expanded()`),
    so the harvester and the bot always agree. Falls back to the default Saved
    Games path if the import fails (e.g. run outside the venv).
  * Incremental: a sidecar `data/.harvest_state.json` records, per journal
    filename, how many LINES were already consumed. Each run re-opens every
    journal but seeks past the consumed-line count and parses only NEW lines —
    so the live (still-growing) journal is picked up correctly across runs and
    finished journals cost one cheap line-skip.
  * Idempotent merge: load the existing catalog, update counts / last_seen /
    add new tokens, write back. Repeated runs are safe and strictly cumulative.
  * Output is pretty-printed and SORTED by token key for stable git diffs.

Run by the project venv:  .venv\Scripts\python.exe scripts\harvest_receivetext.py
Read-only on the game — it only reads journals and writes under data/.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── locations ───────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent                      # projects/ed-autojump
# Phase-1 reorg: the receivetext catalog is a perception reference and now lives
# with ed-vision (projects/ed-vision/src/ed_vision/data/). The harvest sidecar
# state file stays alongside it.
WORKSPACE_ROOT = PROJECT_ROOT.parent                  # projects/
DATA_DIR = WORKSPACE_ROOT / "ed-vision" / "src" / "ed_vision" / "data"
CATALOG_PATH = DATA_DIR / "receivetext_catalog.json"
STATE_PATH = DATA_DIR / ".harvest_state.json"

# Fallback journal dir if the ed_core config import is unavailable.
DEFAULT_JOURNAL_DIR = (
    Path(os.path.expandvars(r"%USERPROFILE%"))
    / "Saved Games" / "Frontier Developments" / "Elite Dangerous"
)

# ── category map (ordered prefix match — first hit wins) ─────────────────────
# Token text after the leading `$`, matched case-insensitively by prefix. Order
# matters only where one prefix is a substring of another (none currently are),
# but we keep it explicit and ordered for clarity and future additions.
CATEGORY_PREFIXES: tuple[tuple[str, str], ...] = (
    ("pirate", "piracy"),
    ("smuggler", "piracy"),
    ("cargohunter", "piracy"),
    ("police", "authority"),
    ("military", "authority"),
    ("powerssecurity", "authority"),
    ("station", "station"),
    ("docking", "station"),
    ("generic", "generic"),
    ("miner", "miner"),
)


def categorize(token: str) -> str:
    """Map a `$token;` to a coarse category via its prefix.

    The prefix is the token body after `$`, lower-cased. First matching entry
    in CATEGORY_PREFIXES wins; anything unmatched is "other".
    """
    body = token.lstrip("$").lower()
    for prefix, category in CATEGORY_PREFIXES:
        if body.startswith(prefix):
            return category
    return "other"


def normalize_token(message: str) -> str:
    r"""Reduce a raw `Message` to its stable token key.

    ED embeds runtime parameters after a `:#`, e.g.
        `$COMMS_entered:#name=Wregoe IE-G d11-10;`
    The token identity is the part before `:#`, with the trailing `;` kept:
        `$COMMS_entered;`
    Messages without a `:#` payload are returned unchanged.
    """
    head = message.split(":#", 1)[0]
    # Guarantee a single trailing `;` (raw tokens already end in `;`; the split
    # head of a parameterised token does not).
    return head if head.endswith(";") else head + ";"


def resolve_journal_dir() -> tuple[Path, str]:
    """Return (journal_dir, source-label). Prefer the bot config so the
    harvester and the bot agree; fall back to the default Saved Games path."""
    # Make the workspace packages importable when run by the venv python.
    # Post-reorg, load_config lives in ed-core (projects/ed-core/src); the bot's
    # config.toml still lives at the ed-autojump project root.
    for src in (WORKSPACE_ROOT / "ed-core" / "src", PROJECT_ROOT / "src"):
        if src.is_dir() and str(src) not in sys.path:
            sys.path.insert(0, str(src))
    try:
        from ed_core.config import load_config

        cfg_path = PROJECT_ROOT / "config.toml"
        cfg = load_config(cfg_path if cfg_path.is_file() else None)
        return cfg.paths.journal_dir_expanded(), "ed_core.config"
    except Exception as exc:  # noqa: BLE001 — fall back, never abort on import
        print(f"[harvest] config import failed ({exc!r}); using default journal dir")
        return DEFAULT_JOURNAL_DIR, "default-fallback"


def load_json(path: Path, default):
    """Load JSON, tolerating a missing or corrupt file (returns default)."""
    if not path.is_file():
        return default
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[harvest] could not read {path.name} ({exc!r}); starting fresh")
        return default


def write_json(path: Path, obj) -> None:
    """Pretty-print JSON sorted by key for stable diffs (atomic-ish replace)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def merge_event(catalog: dict, token: str, ev: dict) -> bool:
    """Fold one ReceiveText event into the catalog. Returns True if `token` is
    newly added this run (for the run summary)."""
    ts = ev.get("timestamp", "")
    entry = catalog.get(token)
    is_new = entry is None
    if is_new:
        entry = {
            "message_localised": ev.get("Message_Localised", ""),
            "channel": ev.get("Channel", ""),
            "category": categorize(token),
            "from_example": ev.get("From_Localised") or ev.get("From", ""),
            "first_seen": ts,
            "last_seen": ts,
            "count": 0,
        }
        catalog[token] = entry

    entry["count"] += 1
    # Keep the earliest first_seen / latest last_seen across all runs+journals.
    # Journal timestamps are ISO-8601 Z strings, so lexical compare == chrono.
    if ts:
        if not entry.get("first_seen") or ts < entry["first_seen"]:
            entry["first_seen"] = ts
        if not entry.get("last_seen") or ts > entry["last_seen"]:
            entry["last_seen"] = ts
    # Backfill a missing localised string / from example if a later event has one.
    if not entry.get("message_localised") and ev.get("Message_Localised"):
        entry["message_localised"] = ev["Message_Localised"]
    if not entry.get("from_example"):
        entry["from_example"] = ev.get("From_Localised") or ev.get("From", "")
    return is_new


def harvest() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    journal_dir, source = resolve_journal_dir()
    catalog: dict = load_json(CATALOG_PATH, {})
    state: dict = load_json(STATE_PATH, {})

    if not journal_dir.is_dir():
        print(f"[harvest] journal dir not found: {journal_dir} (source: {source})")
        # Still write back what we have so a missing dir is non-destructive.
        write_json(CATALOG_PATH, catalog)
        write_json(STATE_PATH, state)
        return 1

    journals = sorted(journal_dir.glob("Journal.*.log"))
    new_tokens = 0
    new_events = 0
    scanned = 0

    for jp in journals:
        scanned += 1
        already = int(state.get(jp.name, 0))
        line_no = 0
        try:
            # errors="replace": a half-written final line in the LIVE journal
            # must not abort the whole file.
            with open(jp, "r", encoding="utf-8", errors="replace") as fh:
                for line_no, line in enumerate(fh, start=1):
                    if line_no <= already:
                        continue  # already consumed in a prior run
                    line = line.strip()
                    if not line or '"ReceiveText"' not in line:
                        # Cheap reject: skip the json.loads for non-ReceiveText
                        # lines (the overwhelming majority).
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # tolerate a malformed / partial line
                    if ev.get("event") != "ReceiveText":
                        continue
                    msg = ev.get("Message", "")
                    if not msg.startswith("$"):
                        continue  # skip plain free-text chatter — tokens only
                    token = normalize_token(msg)
                    if merge_event(catalog, token, ev):
                        new_tokens += 1
                    new_events += 1
        except OSError as exc:
            print(f"[harvest] skipping {jp.name} ({exc!r})")
            continue
        # Record how far we got. line_no is the last line number read (the full
        # current length of the file); next run resumes after it.
        state[jp.name] = line_no

    write_json(CATALOG_PATH, catalog)
    write_json(STATE_PATH, state)

    print(
        f"[harvest] tokens={len(catalog)} (+{new_tokens} new) "
        f"events_merged={new_events} journals_scanned={scanned} "
        f"journal_dir_source={source}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(harvest())
