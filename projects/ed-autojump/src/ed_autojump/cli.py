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
    sub_replay.add_argument(
        "--record",
        type=Path,
        default=None,
        metavar="OUT",
        help="also write the replayed events as a session JSONL at OUT",
    )

    sub.add_parser("doctor", help="check environment + config + binds + EDHM")

    sub_binds = sub.add_parser("install-binds", help="install ED-AFK binds preset")
    sub_binds.add_argument(
        "--swap", action="store_true", help="also swap StartPreset line 2"
    )

    sub.add_parser("restore-binds", help="restore the player's StartPreset")

    sub_run = sub.add_parser(
        "run",
        help="run the bot main loop (Phase 12 — minimal: tail + record, no key sending yet)",
    )
    sub_run.add_argument(
        "--journal-dir", type=Path, default=None,
        help="override journal directory (default: from config.toml)",
    )
    sub_run.add_argument(
        "--sessions-dir", type=Path, default=None,
        help="override session output dir (default: $ED_AFK_SESSIONS_DIR or ~/ed-afk-sessions)",
    )
    sub_run.add_argument(
        "--duration", type=float, default=0.0,
        help="how many seconds to tail the journal before exiting (0 = exit immediately, useful for dry-run)",
    )
    sub_run.add_argument(
        "--record", dest="record", action="store_true", default=False,
        help="record session events to JSONL (default: off)",
    )
    sub_run.add_argument(
        "--no-record", dest="record", action="store_false",
        help="explicitly disable recording (default behaviour)",
    )
    sub_run.add_argument(
        "--engage-keys", dest="engage_keys", action="store_true", default=False,
        help="actually send DirectInput keys (default: off — NullSender, for safe dev runs)",
    )
    sub_run.add_argument(
        "--no-engage-keys", dest="engage_keys", action="store_false",
        help="explicitly disable key sending (default behaviour)",
    )

    return p


def cmd_replay(args) -> int:
    from .journal.tail import JournalTail
    from .recorder import Recorder
    from collections import Counter

    tail = JournalTail(args.journal.parent)
    counts: Counter[str] = Counter()
    recorder: Recorder | None = None
    if getattr(args, "record", None) is not None:
        recorder = Recorder(args.record)
    try:
        for ev in tail.replay_file(args.journal):
            counts[ev.event] += 1
            if recorder is not None:
                recorder.record_journal(ev)
    finally:
        if recorder is not None:
            recorder.close()
    for name, n in counts.most_common():
        print(f"{name:32} {n}")
    return 0


def cmd_run(args) -> int:
    """Phase-12 minimal main loop.

    Currently does: open journal tail at `--journal-dir`, optionally open a
    Recorder at `--sessions-dir`, loop calling `tail.step()` until
    `--duration` seconds have elapsed. Does NOT yet dispatch executors or
    send keys — `--engage-keys` is reserved for when the integration loop
    lands. Use this for capturing real overnight sessions where you drive
    the ship manually and want the bot to record your decisions.
    """
    import time
    from datetime import datetime, timezone

    from .journal.tail import JournalTail
    from .recorder import Recorder, default_session_path

    journal_dir = args.journal_dir
    if journal_dir is None:
        cfg = load_config(args.config if args.config.is_file() else None)
        journal_dir = cfg.paths.journal_dir_expanded()

    recorder: Recorder | None = None
    if args.record:
        if args.sessions_dir is not None:
            args.sessions_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
            session_path = args.sessions_dir / f"session_{stamp}.jsonl"
        else:
            session_path = default_session_path()
        recorder = Recorder(session_path)
        print(f"recording -> {session_path}")

    if args.engage_keys:
        print("WARNING: --engage-keys requested but executor dispatch not yet wired; ignoring")

    try:
        tail = JournalTail(journal_dir)
        if args.duration <= 0:
            return 0
        deadline = time.monotonic() + args.duration
        poll_interval = 0.5
        while time.monotonic() < deadline:
            try:
                events = tail.step()
            except FileNotFoundError:
                events = []
            for ev in events:
                if recorder is not None:
                    recorder.record_journal(ev)
            time.sleep(poll_interval)
        return 0
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130
    finally:
        if recorder is not None:
            recorder.close()


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
        "run": cmd_run,
    }
    return dispatch[cmd](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
