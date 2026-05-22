"""
CLI entry point.

Currently supports the subset of operations that are safe to invoke without
the game running. Game-controlling commands (`run`, `--start`) live in the
later-phase executors.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import load_config


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ed-autojump", description="ED-AFK autojump bot")
    p.add_argument("--version", action="version", version=f"ed-autojump {__version__}")
    p.add_argument(
        "--config",
        type=Path,
        default=Path("config.toml"),
        help="path to config.toml",
    )
    sub = p.add_subparsers(dest="command", required=False)

    sub_replay = sub.add_parser(
        "replay",
        help="replay a journal file through the parser and print event counts",
    )
    sub_replay.add_argument("journal", type=Path, help="path to Journal.*.log")

    sub.add_parser("doctor", help="check environment + config + binds + EDHM")

    sub_binds = sub.add_parser("install-binds", help="install ED-AFK binds preset")
    sub_binds.add_argument(
        "--swap", action="store_true", help="also swap StartPreset line 2"
    )

    sub.add_parser("restore-binds", help="restore the player's StartPreset")

    return p


def cmd_replay(args) -> int:
    from .journal.tail import JournalTail
    from collections import Counter

    tail = JournalTail(args.journal.parent)
    counts: Counter[str] = Counter()
    for ev in tail.replay_file(args.journal):
        counts[ev.event] += 1
    for name, n in counts.most_common():
        print(f"{name:32} {n}")
    return 0


def cmd_doctor(args) -> int:
    from .hud.detect import detect_edhm, detect_graphics_override

    cfg = load_config(args.config if args.config.is_file() else None)
    print(f"ed-autojump {__version__}")
    print(f"  config:        {args.config}")
    print(f"  journal dir:   {cfg.paths.journal_dir_expanded()}")
    print(f"  binds dir:     {cfg.paths.binds_dir_expanded()}")
    edhm = detect_edhm()
    print(f"  EDHM-UI:       {'found' if edhm.ui_installed else 'not found'}")
    print(f"  EDHM DLL:      {'found' if edhm.dll_installed else 'not found'}")
    go = detect_graphics_override()
    print(f"  GraphicsOverride.xml: {'present' if go else 'absent'}")
    return 0


def cmd_install_binds(args) -> int:
    from .binds_tool import install_binds_preset, swap_start_preset

    cfg = load_config(args.config if args.config.is_file() else None)
    install_binds_preset(cfg)
    if args.swap:
        swap_start_preset(cfg)
    return 0


def cmd_restore_binds(args) -> int:
    from .binds_tool import restore_start_preset

    cfg = load_config(args.config if args.config.is_file() else None)
    restore_start_preset(cfg)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    cmd = args.command
    if cmd is None:
        _parser().print_help()
        return 0
    dispatch = {
        "replay": cmd_replay,
        "doctor": cmd_doctor,
        "install-binds": cmd_install_binds,
        "restore-binds": cmd_restore_binds,
    }
    return dispatch[cmd](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
