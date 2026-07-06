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
from ed_core.config import load_config


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

    sub_pull = sub.add_parser(
        "pull-binds",
        help="show diff (or apply) the active in-game preset back to the repo",
    )
    sub_pull.add_argument(
        "--apply", action="store_true",
        help="overwrite the bundled repo preset with the live in-game preset",
    )
    sub_pull.add_argument(
        "--preset", metavar="NAME", default=None,
        help="pull from this preset name instead of the active StartPreset",
    )

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
        # OPERATOR ORDER 2026-07-06: recording (and with it CV frame capture)
        # is DEFAULT ON ALWAYS until further notice — testing blind is over.
        "--record", dest="record", action="store_true", default=True,
        help="record session events to JSONL (default: ON, operator order 2026-07-06)",
    )
    sub_run.add_argument(
        "--no-record", dest="record", action="store_false",
        help="explicitly disable recording",
    )
    sub_run.add_argument(
        "--engage-keys", dest="engage_keys", action="store_true", default=False,
        help="actually send DirectInput keys (default: off — NullSender, for safe dev runs)",
    )
    sub_run.add_argument(
        "--no-engage-keys", dest="engage_keys", action="store_false",
        help="explicitly disable key sending (default behaviour)",
    )
    sub_run.add_argument(
        "--status", dest="status", action="store_true", default=True,
        help="enable Status.json polling (default: on; HeatSupplier feeds scoop guard)",
    )
    sub_run.add_argument(
        "--no-status", dest="status", action="store_false",
        help="disable Status.json polling",
    )
    sub_run.add_argument(
        "--console-status", dest="console_status", action="store_true",
        default=True,
        help="mirror live execution info to this terminal's stdout (default: on)",
    )
    sub_run.add_argument(
        "--no-console-status", dest="console_status", action="store_false",
        help="suppress the stdout status mirror",
    )
    sub_run.add_argument(
        "--eddn", dest="eddn", action="store_true", default=None,
        help="publish to EDDN on FSS scans (default: from config.eddn.publish)",
    )
    sub_run.add_argument(
        "--no-eddn", dest="eddn", action="store_false",
        help="explicitly disable EDDN publishing",
    )
    sub_run.add_argument(
        "--route-plot", dest="route_plot", action="store_true", default=False,
        help="enable Spansh route auto-plotting when NavRoute is empty",
    )
    sub_run.add_argument(
        "--visited-log", dest="visited_log", action="store_true", default=True,
        help="append each visited system to ~/Documents/ed-afk-systems-visited.log (default: on)",
    )
    sub_run.add_argument(
        "--no-visited-log", dest="visited_log", action="store_false",
        help="disable the visited-systems log",
    )
    sub_run.add_argument(
        "--destination", dest="destination", default=None,
        help="override config.routing.destination (e.g. 'Beagle Point')",
    )
    sub_run.add_argument(
        "--launch", dest="launch", action="store_true", default=False,
        help="also launch the game first (Phase 13: MEL -> main menu -> PG -> LoadGame)",
    )
    sub_run.add_argument(
        "--commander", dest="commander", default=None,
        help="commander to launch as (default: config.launcher.default_commander)",
    )
    sub_run.add_argument(
        "--auth", dest="auth", choices=["frontier", "steam"], default=None,
        help="auth method for launch (default: config.launcher.default_auth)",
    )
    sub_run.add_argument(
        "--group", dest="group", default=None,
        help="private group to join (default: config.launcher.default_group)",
    )
    sub_run.add_argument(
        "--dryrun", dest="dryrun_pre_flight", action="store_true", default=False,
        help="run MEL /dryrun first to catch stale .cred hang (slow; off by default)",
    )
    sub_run.add_argument(
        "--no-dryrun", dest="dryrun_pre_flight", action="store_false",
        help="(default) skip MEL /dryrun pre-flight",
    )

    # ed-autojump launch — standalone launch (no AFK loop after).
    sub_launch = sub.add_parser(
        "launch",
        help="launch ED via min-ed-launcher -> main menu -> optionally PG -> LoadGame",
    )
    sub_launch.add_argument("--commander", default=None,
                            help="commander name (default: config.launcher.default_commander)")
    sub_launch.add_argument("--auth", choices=["frontier", "steam"], default=None)
    sub_launch.add_argument("--product", choices=["edo", "edh4"], default=None)
    sub_launch.add_argument("--group", default=None,
                            help="private group to verify after LoadGame")
    sub_launch.add_argument("--mel-path", type=Path, default=None,
                            help="explicit MinEdLauncher.exe path (default: auto-detect)")
    sub_launch.add_argument("--journal-dir", type=Path, default=None)
    sub_launch.add_argument("--dryrun", dest="dryrun_pre_flight",
                            action="store_true", default=False,
                            help="run MEL /dryrun first to catch stale .cred hang (slow; off by default)")
    sub_launch.add_argument("--no-dryrun", dest="dryrun_pre_flight",
                            action="store_false",
                            help="(default) skip MEL /dryrun pre-flight")
    sub_launch.add_argument("--no-nav", dest="force_no_nav", action="store_true", default=False,
                            help="force menu_nav off for this launch (operator handoff at main menu)")

    # ed-autojump setup-frontier-creds — interactive cred onboarding.
    sub_creds = sub.add_parser(
        "setup-frontier-creds",
        help="interactively log in to MEL for each commander to write .cred files",
    )
    sub_creds.add_argument(
        "--commanders", nargs="*", default=None,
        help="space-separated commander names (default: all in config.launcher.profiles)",
    )
    sub_creds.add_argument("--mel-path", type=Path, default=None)

    # ed-autojump calibrate-menu — interactive press-count capture.
    sub_cal = sub.add_parser(
        "calibrate-menu",
        help="walk-through to determine main-menu press counts for a commander",
    )
    sub_cal.add_argument("--commander", required=True,
                         help="commander to calibrate (e.g. CmdrOne)")
    sub_cal.add_argument("--is-owner", action="store_true", default=False,
                         help="commander owns the private group (skips select-group step)")

    # ed-autojump calibrate-compass — auto-locate the nav compass on screen.
    sub.add_parser(
        "calibrate-compass",
        help="auto-locate the nav compass and print a [vision] region block",
    )

    # ed-autojump calibrate-overlay — tune the screen->overlay box transform.
    sub.add_parser(
        "calibrate-overlay",
        help="interactively align the CV debug boxes (EDMCOverlay) with the screen",
    )

    # ed-autojump navpanel-overlay — LIVE per-row nav-icon vision diagnostic.
    sub_navov = sub.add_parser(
        "navpanel-overlay",
        help="live: draw the nav-panel icon detector's per-row box "
             "(green star / red non-star) + confidence on the EDMCOverlay",
    )
    sub_navov.add_argument("--rows", type=int, default=12,
                           help="nav-list rows to scan/label (default 12)")

    # ed-autojump cv-debug — LIVE context-aware CV overlay (reacts to the UI).
    sub.add_parser(
        "cv-debug",
        help="live context-aware CV overlay: nav-panel boxes+labels when the "
             "panel is open, compass/sun/widget when forward; logs UI changes",
    )

    # ed-autojump cleanup — kill a stale survivor named by the PID file
    # (Layer 4). PID-targeted; never a command-line scan.
    sub.add_parser(
        "cleanup",
        help="kill a leftover bot child from a crashed prior session "
             "(targets the recorded PID file only; never your shell)",
    )

    # ed-autojump panic — release held DirectInput keys NOW (Layer 4).
    sub.add_parser(
        "panic",
        help="release any held DirectInput keys immediately (clears a stuck "
             "key after a hard-kill that left the game holding one)",
    )

    return p


def cmd_replay(args) -> int:
    from ed_core.journal.tail import JournalTail
    from ed_core.recorder import Recorder
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
    """Procedure-engine main loop.

    Wires JournalTail → FlowRunner → procedure dispatch with Recorder
    snooping every event + outcome. By default uses NullSender (does not
    send keys); --engage-keys swaps in DirectInputSender driven by the
    parsed ED-AFK.4.2.binds preset.
    """
    from datetime import datetime, timezone

    from ed_core.config import load_config
    from ed_core.journal.tail import JournalTail
    from ed_core.keys import NullSender, parse_binds
    from ed_core.lifecycle import install_signal_cleanup
    from ed_core.panic import PanicSwitch
    from ed_core.panic_listener import HotkeyListener, _NullBackend, resolve_backend
    from ed_core.recorder import Recorder, default_session_path
    from ed_core.status.navroute import NavRouteReader
    from ed_core.status.status import StatusReader

    cfg = load_config(args.config if args.config.is_file() else None)
    journal_dir = args.journal_dir or cfg.paths.journal_dir_expanded()
    panic = PanicSwitch()
    # Hotkey backend is only resolved when we actually run (duration > 0).
    # The `keyboard` library installs Win32 hooks that can crash the
    # interpreter on a fast-exit subprocess; defer until we're going to use it.
    listener: HotkeyListener | None = None

    # Apply CLI overrides into config.
    if args.destination:
        cfg.routing.destination = args.destination
    if args.eddn is not None:
        cfg.eddn.publish = args.eddn

    # Recorder setup.
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

    # CV frame dumps — every CV read's frame as PNG next to the session jsonl,
    # so any failed read is replayable offline against its reader (2026-06-06:
    # the 12:37 oscillation was only root-causable because ED happened to
    # still be running with the ship parked).
    #
    # OPERATOR ORDER 2026-07-06: frame capture DEFAULT ON ALWAYS until further
    # notice. The previous sink swallowed EVERY failure silently ("except:
    # pass") — zero _frames dirs since 06-16 with no way to know why; the
    # operator tested blind for three weeks. A diagnostic write must never
    # touch the flight (still fail-soft) but it must NEVER fail silently:
    # cv2 resolves ONCE up front (loud console warning if missing, .npy
    # fallback via numpy so frames land regardless), and per-call failures
    # record a FrameSinkError outcome once per failure type.
    frame_sink = None
    if recorder is not None:
        _frames_dir = session_path.with_name(session_path.stem + "_frames")
        try:
            import cv2 as _cv2_sink
        except Exception as _cv2_exc:  # noqa: BLE001 — degraded, NOT silent
            _cv2_sink = None
            print(f"WARNING frame-dump: cv2 unavailable ({type(_cv2_exc).__name__}: "
                  f"{_cv2_exc}) -- falling back to raw .npy frame dumps")
        _sink_errs: set[str] = set()

        def frame_sink(name: str, frame, _d=_frames_dir, _cv2=_cv2_sink,
                       _errs=_sink_errs) -> None:
            try:
                _d.mkdir(parents=True, exist_ok=True)
                if _cv2 is not None:
                    if not _cv2.imwrite(str(_d / f"{name}.png"), frame):
                        raise RuntimeError("cv2.imwrite returned False")
                else:
                    import numpy as _np
                    _np.save(str(_d / f"{name}.npy"), _np.asarray(frame))
            except Exception as exc:  # noqa: BLE001 — fail-soft but LOUD, once per type
                key = f"{type(exc).__name__}: {exc}"
                if key not in _errs:
                    _errs.add(key)
                    print(f"WARNING frame-dump failed ({key}) -- frame {name!r}")
                    try:
                        recorder.record_outcome("FrameSinkError",
                                                {"err": key, "frame": name})
                    except Exception:  # noqa: BLE001 — recorder must not kill flight
                        pass

    # Sender selection. Real key dispatch requires the binds preset.
    if args.engage_keys:
        from ed_core.keys import DirectInputSender
        binds_path = Path(__file__).parent / "binds" / "ED-AFK.4.2.binds"
        binds = parse_binds(binds_path)
        sender = DirectInputSender(binds)
        print(f"engaging keys via {binds_path.name}")
    else:
        sender = NullSender()

    # Log EVERY keypress to the session so the recording shows exactly what the
    # bot sent (and when) — not just journal events + outcomes. This is the only
    # way to tell "the escape never pitched" from "it pitched but the ship didn't
    # respond". Wrap only when recording.
    if recorder is not None:
        from ed_core.keys import LoggingSender
        sender = LoggingSender(sender, recorder)

    # Status + NavRoute readers (default on when journal dir exists).
    status_reader = None
    navroute_reader = None
    if args.status:
        status_path = journal_dir / "Status.json"
        navroute_path = journal_dir / "NavRoute.json"
        status_reader = StatusReader(status_path)
        navroute_reader = NavRouteReader(navroute_path)

    # --launch: invoke MEL → wait main menu → optional menu nav → wait LoadGame
    # BEFORE the AFK loop begins. If the launch fails, abort with a clear
    # message rather than start the loop on a non-launched game.
    if getattr(args, "launch", False):
        from ed_core.launcher import LauncherError
        from ed_core.launcher.flow import FlowStatus, launch_and_enter_game
        from ed_core.launcher.menu_nav import MenuNavigator

        try:
            launch_spec = _build_launch_spec(
                cfg, commander=args.commander, auth=args.auth,
            )
            mel = _resolve_mel(cfg)
        except LauncherError as exc:
            print(f"--launch failed: {exc}")
            return 2

        # Reuse the same sender for menu navigation (DirectInputSender works
        # without binds via press_raw; NullSender doesn't press anything).
        launch_tail = JournalTail(journal_dir)
        nav = None
        if cfg.menu_nav.enabled and args.engage_keys:
            nav = MenuNavigator(sender=sender, config=cfg.menu_nav,
                                sleep=__import__("time").sleep)

        result = launch_and_enter_game(
            spec=launch_spec, mel=mel, tail=launch_tail,
            menu_navigator=nav,
            menu_nav_cfg=cfg.menu_nav, launcher_cfg=cfg.launcher,
            expected_group=args.group or cfg.launcher.default_group,
            expected_commander=launch_spec.commander,
            pre_flight_dryrun=args.dryrun_pre_flight,
        )
        if result.status not in (FlowStatus.OK, FlowStatus.MAIN_MENU_READY):
            print(f"--launch failed: {result.status.value} -- {result.detail}")
            return 1

    # Wire nav-compass alignment when engaging keys. build_vision returns
    # (None, None) unless [vision] is enabled AND a region is calibrated, so
    # this is a no-op for everyone who hasn't run calibrate-compass.
    compass_reader = frame_grabber = None
    # Outer scope (before the engage-keys guard) so the FlowRunner call below —
    # which is OUTSIDE this block — always sees defined names, even on a
    # no-engage run. [council plan-gate fix]
    widget_ring_reader = widget_frame_grabber = None
    # Nav-panel IDENTITY targeting (task #45). (None, None) unless
    # [exploration].nav_panel_ocr_enabled AND [cv]+tesseract present; body_tour
    # falls back to the blind row walk otherwise. Outer scope like the widget
    # names so the FlowRunner call always sees them.
    nav_panel_reader = nav_panel_grabber = None
    station_menu_grabber = None
    # Route-complete dock-vs-park icon classifier frame source. None -> the
    # route-complete router falls back to the name heuristics (today's dock, no
    # regression). Set below on a keyed run when capture is available.
    navpanel_icon_grabber = None
    # CV-action family (#3/#4/#5/#6) full-frame grab — feeds the detail-page #8
    # label confirm AND nav_supercruise_unexplored's nav-list read. Outer scope so
    # the FlowRunner call (below the engage-keys block) always sees a defined name.
    navpanel_cv_grabber = None
    # Council B docking <7.5km gate: right-side target-panel km supplier. None ->
    # dock_close_to_range fails closed (an unread distance is never "in range").
    dock_distance_km_supplier = None
    # SC-assist HUD prompt reads (#17): confirm_orbiting / confirm_sc_assist_active
    # / wait_sc_assist_orbiting. None -> each step's own fail-soft fallback.
    hud_grabber = None
    if args.engage_keys:
        from ed_vision.capture import build_vision
        compass_reader, frame_grabber = build_vision(cfg)
        from ed_vision.capture import build_navpanel_vision
        nav_panel_reader, nav_panel_grabber = build_navpanel_vision(cfg)
        # Docked-menu CV (station_menu detector): feeds the auto_launch
        # undock safety gate + the services-macro menu-up entry gate.
        from ed_vision.capture import build_station_menu_grabber
        station_menu_grabber = build_station_menu_grabber(cfg)
        if station_menu_grabber is not None:
            print("vision: docked-menu detector ON (full-frame grab)")
        # Route-complete icon classifier: bare full-frame grab composed with the
        # open-panel/close keystroke wrapper (sender is in scope here). The router
        # (boot_routes.dispatch_route_complete) reads the selected destination
        # row's icon to decide DOCK (station/carrier) vs PARK (star/planet) and to
        # VETO an off-pattern arrival star a name heuristic mis-flagged a station.
        # GATED on WinRT OCR: the router OCR-name-matches the destination row,
        # WALKS the cursor onto it, and reads its icon (council ruling C) via
        # ocr_winrt.ocr_detailed bboxes. Without WinRT the destination row can't be
        # located, so we leave the grabber UNWIRED -> name fallback (today's dock,
        # no regression). The grabber is the BARE full-frame grab; the open/walk/
        # close orchestration lives in boot_routes._destination_dock_kind (it needs
        # the sender + reader + Destination.Name). WinRT is built into Win11.
        from ed_vision.capture import build_navpanel_icon_grabber
        from ed_vision import ocr_winrt as _ocr
        _navpanel_icon_full_frame = build_navpanel_icon_grabber(cfg)
        if _navpanel_icon_full_frame is not None and _ocr.available():
            navpanel_icon_grabber = _navpanel_icon_full_frame
            print("vision: route-complete icon classifier ON "
                  "(name-match + cursor-walk to destination; star->park veto active)")
        elif _navpanel_icon_full_frame is not None:
            print("vision: route-complete icon classifier OFF "
                  "(WinRT OCR unavailable -> name fallback, no veto)")
        # CV-action family (#3/#4/#5/#6): the SAME bare full-frame grab feeds the
        # detail-page #8 label confirm (nav_target_star / nav_supercruise_star/
        # _target/_unexplored) AND nav_supercruise_unexplored's find_first_unexplored
        # (each crops its own region). GATED on WinRT OCR (both layers OCR); without
        # it the actions fall back to blind press / "unreadable", no regression.
        if _navpanel_icon_full_frame is not None and _ocr.available():
            navpanel_cv_grabber = _navpanel_icon_full_frame
            print("vision: CV-action family ON "
                  "(detail-page #8 label confirm + unexplored finder)")
        # Council B docking: the SAME bare full-frame grab feeds the right-side
        # target-panel km read (the reader crops the Mandalay region itself,
        # fail-closed per-ship). GATED on WinRT like every other OCR consumer;
        # unwired -> dock_close_to_range fails closed, no silent "in range".
        if _navpanel_icon_full_frame is not None and _ocr.available():
            from ed_vision.target_panel_distance import read_target_panel_km
            _tp_grab = _navpanel_icon_full_frame

            def dock_distance_km_supplier(_grab=_tp_grab):
                return read_target_panel_km(_grab())
            print("vision: docking distance gate ON "
                  "(right-side target panel, <7.5km)")
        # SC-assist HUD prompt reader (#17): the SAME bare full-frame grab —
        # read_sc_hud/detect_orbiting crop HUD_REGION_FRAC themselves. Feeds
        # the park orbit-confirm, exploration's ScHudState telemetry, and the
        # startup/sc_resume get-around ORBITING wait (operator 2026-07-05
        # rewire). WinRT-gated; unwired -> the steps' own fail-soft fallbacks.
        if _navpanel_icon_full_frame is not None and _ocr.available():
            hud_grabber = _navpanel_icon_full_frame
            print("vision: SC-assist HUD reader ON "
                  "(ORBITING/ACTIVE/ALIGN center prompts)")
        if nav_panel_reader is not None:
            print(f"exploration: nav-panel identity targeting ON "
                  f"(region={tuple(cfg.exploration.nav_panel_region)}) "
                  f"[CALIBRATION-PENDING]")
        if compass_reader is not None:
            print(f"vision: alignment ON (backend={cfg.vision.backend}, "
                  f"region={tuple(cfg.vision.region)})")
        else:
            # Loud, so a blind run is never mistaken for a steering one.
            reason = ("[vision].enabled = false" if not cfg.vision.enabled
                      else "no compass region calibrated")
            print(f"vision: alignment OFF ({reason}) -- the ship will NOT be "
                  "steered. Run `ed-autojump calibrate-compass` and set "
                  "[vision].enabled = true to enable orientation.")

        # Widget-ring FINE pass (additive after orient_compass). ON by default.
        # A preflight MISS follows [vision].widget_ring_on_miss (operator
        # decision 2026-06-06, GitHub issue #1): "degrade" (default) disables
        # the fine pass FOR THIS RUN so jumps proceed compass-only;
        # "fail_closed" warns and the required fine step gates every jump
        # until the widget is detectable. Issue #1 was the degrade-less
        # version of this: the preflight warned, the pass stayed enabled, and
        # the bot flew-but-never-jumped.
        if cfg.vision.widget_ring_alignment:
            from ed_vision.capture import build_widget_vision
            from ed_vision.widget_ring import verify_widget_rendered
            widget_ring_reader, widget_frame_grabber = build_widget_vision(cfg)
            degrade = cfg.vision.widget_ring_on_miss != "fail_closed"
            missing = (widget_ring_reader is None
                       or widget_frame_grabber is None)
            undetected = (not missing and not verify_widget_rendered(
                widget_ring_reader, widget_frame_grabber))
            if missing or undetected:
                why = ("vision unavailable (install the [vision] extra)"
                       if missing else
                       "mouse widget not detected — enable the HUD mouse "
                       "widget in 'point' mode (see ED-AFK preset)")
                if degrade:
                    print(f"WARNING: {why}. Widget-ring fine pass DISABLED "
                          f"for this run — jumps proceed compass-only. "
                          f"([vision].widget_ring_on_miss='fail_closed' to "
                          f"gate jumps on it instead.)", file=sys.stderr)
                    cfg.vision.widget_ring_alignment = False
                    widget_ring_reader = None
                    widget_frame_grabber = None
                else:
                    print(f"WARNING: {why}. widget_ring_on_miss='fail_closed' "
                          f"— the required fine step gates every jump until "
                          f"the widget is detectable.", file=sys.stderr)
            else:
                print(f"vision: widget-ring FINE pass ON "
                      f"(crop={tuple(cfg.vision.widget_crop)})")

    from .flow import FlowRunner, load_procedures
    from ed_core.flow.loader import validate_procedure
    from .flow.steps import STEP_REGISTRY

    # Activate autojump's own boot classifier, event routes, step impls, and
    # procedure directory into the core registry before FlowRunner is built.
    from ed_autojump import activate as _activate_autojump
    _activate_autojump()

    # Activate domain plugins (e.g. ed_explore registers body_tour).
    # Uses Python entry_points so ed_autojump never imports a sibling domain
    # directly — DAG-clean by construction.
    import importlib.metadata as _meta
    for _ep in _meta.entry_points(group="ed_autojump.plugins"):
        _ep.load()()

    proc_dir = Path(__file__).resolve().parents[2] / "procedures"
    procedures = load_procedures(proc_dir)
    # Fail fast on an invalid procedure file rather than improvising in flight.
    problems = []
    for proc in procedures.values():
        problems += validate_procedure(proc, known_actions=STEP_REGISTRY.keys())
    if problems:
        for p in problems:
            print(f"procedure error: {p}", file=sys.stderr)
        return 2

    align_kwargs = dict(
        align_tol=cfg.vision.align_tol,
        deadzone=cfg.vision.deadzone,
        gain=cfg.vision.gain,
        min_press=cfg.vision.min_press_s,
        max_press=cfg.vision.max_press_s,
        search_press=cfg.vision.search_press_s,
        settle_s=cfg.vision.settle_s,
        max_iters=cfg.vision.max_iters,
        timeout_s=cfg.vision.timeout_s,
    )

    # Cosmetic sinks share the single overlay slot via a fail-soft Tee:
    #   - EDMCOverlay in-game writer (None when [overlay].enabled=false)
    #   - console mirror → launch terminal (the stream's console). ON by default;
    #     [overlay].console=false or --no-console-status suppresses it.
    from ed_core.overlay import build_overlay
    from ed_core.console_status import ConsoleStatusWriter, OverlayTee

    edmc = build_overlay(cfg)
    console = (ConsoleStatusWriter()
               if cfg.overlay.console and args.console_status else None)
    # overlay is always an OverlayTee; an empty Tee is a harmless no-op sink.
    overlay = OverlayTee(edmc, console)
    overlay.start()                     # fans start() to whichever sinks exist
    if edmc is not None:
        print("overlay: EDMCOverlay status ON (connecting in background)")
    if console is not None:
        print("console: live status mirror ON (stdout)")

    # CV debug boxes (DEFAULT OFF, opt-in — operator directive 2026-06-14,
    # supersedes the 2026-06-13 default-on rule). Opt in via the launcher VISION
    # toggle, which persists ED_AUTOJUMP_OVERLAY_CV_DEBUG=1 to projects/
    # ed-autojump/.env (local, gitignored); or cv_debug=true in config.local.toml.
    # When on, draws what every named grabber looks at, green hit / red miss.
    # Registered globally so vision call sites find it. Fail-soft: needs EDMC.
    if edmc is not None and cfg.overlay.cv_debug:
        import os as _os
        from ed_vision.debug_overlay import (CvDebugSink, ScreenToOverlay,
                                           set_debug_sink)
        _w, _h = tuple(cfg.cv.target_resolution)
        _transform = ScreenToOverlay.load(
            Path(_os.path.expandvars(cfg.paths.calibration_dir)), _w, _h)
        set_debug_sink(CvDebugSink(edmc, _transform,
                                   ttl_s=cfg.overlay.cv_debug_ttl_s))
        print("overlay: CV debug boxes ON (tune with `calibrate-overlay`)")

    # Visited-systems log (default on; --no-visited-log disables). Passive
    # observer: appends each live FSDJump arrival to ~/Documents, never deleted.
    from ed_core.visited import VisitedSystemsLogger
    visited_logger = VisitedSystemsLogger() if args.visited_log else None
    if visited_logger is not None:
        print(f"visited-log: ON -> {visited_logger.path}")

    # Layer-3/4 best-effort cleanup: install win32 console-ctrl + signal +
    # atexit handlers all converging on ONE idempotent release-keys + panic +
    # close. Writes a PID file (Layer 4) the launcher / `cleanup` can target.
    # Fail-soft on any platform missing a piece (AC12). This is the ONLY layer
    # that releases HELD KEYS on a graceful-ish termination; it does nothing on
    # a raw TerminateProcess of this child (documented residual).
    cleanup_guard = install_signal_cleanup(
        panic=panic, sender=sender, recorder=recorder, on_log=print,
    )

    runner = FlowRunner(
        procedures=procedures,
        sender=sender,
        status_reader=status_reader,
        navroute_reader=navroute_reader,
        compass_reader=compass_reader,
        frame_grabber=frame_grabber,
        align_kwargs=align_kwargs,
        compass_samples=cfg.vision.align_samples,
        widget_ring_enabled=cfg.vision.widget_ring_alignment,
        widget_ring_reader=widget_ring_reader,
        widget_frame_grabber=widget_frame_grabber,
        widget_ring_on_miss=cfg.vision.widget_ring_on_miss,
        body_tour_enabled=cfg.exploration.body_tour_enabled,
        body_tour_dwell_s=cfg.exploration.body_tour_dwell_s,
        body_tour_max_bodies=cfg.exploration.body_tour_max_bodies,
        body_tour_max_rows=cfg.exploration.body_tour_max_rows,
        body_tour_orbit_timeout_s=cfg.exploration.body_tour_orbit_timeout_s,
        body_tour_min_bodies=cfg.exploration.body_tour_min_bodies,
        nav_panel_reader=nav_panel_reader,
        nav_panel_grabber=nav_panel_grabber,
        station_menu_grabber=station_menu_grabber,
        navpanel_icon_grabber=navpanel_icon_grabber,
        navpanel_detail_grabber=navpanel_cv_grabber,
        navpanel_frame_grabber=navpanel_cv_grabber,
        hud_grabber=hud_grabber,
        dock_distance_km_supplier=dock_distance_km_supplier,
        visited_logger=visited_logger,
        overlay=overlay,
        record=(recorder.record_outcome if recorder is not None else None),
        frame_sink=frame_sink,
        tail=JournalTail(journal_dir),
        panic_switch=panic,
    )

    # Startup route check. The jump loop only engages when a route is plotted;
    # with none the bot sits idle and the ship never moves (this surprised a
    # user). Read NavRoute.json directly so we don't disturb the orchestrator's
    # NavRouteReader dedup state.
    _nav_path = journal_dir / "NavRoute.json"
    _plotted = []
    try:
        from ed_core.status.navroute import parse_navroute
        _raw = _nav_path.read_text(encoding="utf-8").strip()
        if _raw:
            _plotted = parse_navroute(_raw).route
    except (FileNotFoundError, OSError, ValueError):
        _plotted = []
    if _plotted:
        print(f"route: {len(_plotted)} systems plotted (next hop "
              f"{_plotted[0].star_system!r}).")
    elif args.route_plot:
        print(f"route: NONE plotted -- auto-plot is ON, will plot to "
              f"{cfg.routing.destination!r} when able.")
    else:
        print("=" * 64)
        print("  WARNING: NO ROUTE PLOTTED.")
        print("  The bot jumps along your in-game route. With none plotted it")
        print("  will sit idle and the ship will NOT move.")
        print("  Fix: plot a route in the Galaxy Map (or relaunch with")
        print("  --route-plot / the launcher's 'Auto-plot route' option).")
        print("=" * 64)

    try:
        if args.duration <= 0:
            return 0
        # Resolve + start hotkey listener now that we know we'll be running.
        backend = resolve_backend()
        listener = HotkeyListener(
            panic_switch=panic,
            backend=backend,
            hotkey=cfg.safety.panic_hotkey,
        )
        if isinstance(backend, _NullBackend):
            print(
                "WARNING: panic-hotkey backend unavailable (install `keyboard` to enable "
                f"{cfg.safety.panic_hotkey}); Ctrl+C in this terminal still trips panic."
            )
        listener.start()
        # Foreground ED before the first keypress. SendInput delivers to
        # whatever window has focus — without this, launching `run` from a
        # terminal sends the whole flight into the terminal. WIRED 2026-06-06:
        # the helper existed but only the --launch path called it.
        if args.engage_keys:
            from ed_core.launcher.focus import focus_ed_window
            print("[run] focusing ED window before key dispatch...")
            if not focus_ed_window():
                print("[run] WARN: could not focus EliteDangerous64.exe -- "
                      "keys may go to the wrong window")
        runner.run_live(duration_s=args.duration)
        return 0
    except KeyboardInterrupt:
        print("\ninterrupted -- tripping panic switch")
        # Route through the idempotent guard so the same release-keys + panic
        # + close path runs whether we got here via Ctrl+C, a signal handler,
        # or the win32 console handler — exactly once regardless.
        cleanup_guard.run("KeyboardInterrupt")
        runner.request_stop()
        return 130
    finally:
        if listener is not None:
            listener.stop()
        if overlay is not None:
            overlay.close()
        # Idempotent: if a handler already ran cleanup this is a no-op; if we
        # exited normally this is the one that releases keys + closes recorder.
        cleanup_guard.run("cmd_run finally")


def _build_launch_spec(cfg, *, commander=None, auth=None, product=None):
    """Build a LaunchSpec from config + CLI overrides. Raises LauncherError
    if the commander isn't in config.launcher.profiles."""
    from ed_core.launcher import LaunchSpec, resolve_profile

    cmdr = commander or cfg.launcher.default_commander
    profile = resolve_profile(cmdr, cfg.launcher)
    return LaunchSpec(
        commander=cmdr,
        profile_slug=profile.profile_slug,
        auth=auth or cfg.launcher.default_auth,
        product=product or cfg.launcher.default_product,
        autorun=cfg.launcher.autorun,
        autoquit=cfg.launcher.autoquit,
        skip_install_prompt=cfg.launcher.skip_install_prompt,
    )


def _resolve_mel(cfg, *, explicit_path=None):
    """Locate MinEdLauncher.exe via explicit path → config → auto-detect.
    Returns a MinEdLauncher instance ready to spawn."""
    from ed_core.launcher import MinEdLauncher, detect_min_ed_launcher

    path = explicit_path
    if path is None and cfg.launcher.mel_path:
        path = Path(cfg.launcher.mel_path)
    det = detect_min_ed_launcher(explicit_path=path)
    if not det.found:
        from ed_core.launcher import LauncherError
        raise LauncherError(
            "MinEdLauncher.exe not found — pass --mel-path, set "
            "[launcher].mel_path in config.toml, or install it on PATH"
        )
    return MinEdLauncher(exe_path=det.path)


def cmd_launch(args) -> int:
    """Standalone launch — start ED, wait for main menu, optionally nav to PG."""
    from ed_core.journal.tail import JournalTail
    from ed_core.launcher import LauncherError
    from ed_core.launcher.flow import FlowStatus, launch_and_enter_game
    from ed_core.launcher.menu_nav import MenuNavigator

    cfg = load_config(args.config if args.config.is_file() else None)
    journal_dir = args.journal_dir or cfg.paths.journal_dir_expanded()

    try:
        spec = _build_launch_spec(
            cfg, commander=args.commander, auth=args.auth, product=args.product,
        )
        mel = _resolve_mel(cfg, explicit_path=args.mel_path)
    except LauncherError as exc:
        print(f"error: {exc}")
        return 2

    tail = JournalTail(journal_dir)

    # Build the navigator only if menu_nav is enabled AND not forced off.
    navigator = None
    nav_cfg = cfg.menu_nav
    if args.force_no_nav:
        # Effectively disable nav for this run by giving a copy with enabled=False.
        from dataclasses import replace
        nav_cfg = replace(nav_cfg, enabled=False)
    if nav_cfg.enabled:
        from ed_core.keys import DirectInputSender
        sender = DirectInputSender(binds=None)
        navigator = MenuNavigator(sender=sender, config=nav_cfg, sleep=__import__("time").sleep)

    result = launch_and_enter_game(
        spec=spec, mel=mel, tail=tail,
        menu_navigator=navigator,
        menu_nav_cfg=nav_cfg, launcher_cfg=cfg.launcher,
        expected_group=args.group or cfg.launcher.default_group,
        expected_commander=spec.commander,
        pre_flight_dryrun=args.dryrun_pre_flight,
    )

    if result.status == FlowStatus.OK:
        print(f"[launch] OK -- entered as {result.load_game_event.commander}")
        return 0
    if result.status == FlowStatus.MAIN_MENU_READY:
        print(f"[launch] main menu ready -- operator handoff. {result.detail}")
        return 0
    print(f"[launch] FAILED: {result.status.value} -- {result.detail}")
    return 1


def cmd_setup_creds(args) -> int:
    """Interactive .cred onboarding wizard."""
    from ed_core.launcher import LauncherError
    from ed_core.launcher.wizard import setup_frontier_creds

    cfg = load_config(args.config if args.config.is_file() else None)
    commanders = args.commanders or list(cfg.launcher.profiles.keys())
    try:
        mel = _resolve_mel(cfg, explicit_path=args.mel_path)
    except LauncherError as exc:
        print(f"error: {exc}")
        return 2
    result = setup_frontier_creds(commanders, launcher_cfg=cfg.launcher, mel=mel)
    print("")
    print("=== Summary ===")
    print(f"  Succeeded: {result.succeeded}")
    print(f"  Skipped:   {result.skipped}")
    print(f"  Failed:    {result.failed}")
    return 0 if not result.failed else 1


def cmd_calibrate_menu(args) -> int:
    """Interactive menu calibration. Prints TOML snippet for user to paste."""
    from ed_core.launcher.wizard import calibrate_menu

    cfg = load_config(args.config if args.config.is_file() else None)
    is_owner = args.is_owner or (args.commander == cfg.menu_nav.group_owner_commander)
    calibration = calibrate_menu(commander=args.commander, is_owner=is_owner)

    # Detect whether [menu_nav] is already present + enabled in config.toml.
    # If not, prepend the header block so first-time users don't paste only
    # the calibration sub-section (which leaves enabled = false default →
    # navigator refuses to run, surprising the user mid-launch).
    menu_nav_header_needed = not cfg.menu_nav.enabled

    print("")
    print("=== Calibration captured ===")
    print("Add the block(s) below to your config.toml:")
    print("")

    if menu_nav_header_needed:
        print("# --- first-time setup: paste this ONCE (skip if [menu_nav] already exists) ---")
        print("[menu_nav]")
        print("enabled = true")
        print(f'group_owner_commander = "{cfg.menu_nav.group_owner_commander}"')
        print("")

    print("# --- per-commander calibration (one block per commander) ---")
    print(f"[menu_nav.calibration.{args.commander}]")
    for k, v in calibration.to_dict().items():
        if isinstance(v, str):
            print(f'{k} = "{v}"')
        else:
            print(f"{k} = {v}")
    print("")
    if menu_nav_header_needed:
        print("NOTE: place the [menu_nav] block BEFORE any [menu_nav.calibration.*]")
        print("blocks in your config.toml -- TOML treats them as nested tables and")
        print("requires the parent table's own keys to be defined first.")
    return 0


def cmd_calibrate_compass(args) -> int:
    """Grab the screen, find the nav compass ring, print a [vision] region block."""
    import os
    import time

    cfg = load_config(args.config if args.config.is_file() else None)
    try:
        from ed_vision.capture import ScreenGrabber, locate_compass_ring, ring_to_region
    except Exception as e:  # noqa: BLE001
        print(f"vision deps missing ({e}); install with:  pip install -e .[vision]")
        return 1

    print("Be in the cockpit with the nav-compass panel visible (the small disc")
    print("left of the radar). Capturing in 3 seconds...")
    time.sleep(3)

    try:
        grabber = ScreenGrabber((0, 0, 0, 0), backend=cfg.vision.capture_backend)  # full screen
    except Exception as e:  # noqa: BLE001
        print(f"could not start screen capture ({e})")
        return 1
    frame = None
    for _ in range(10):
        frame = grabber.grab()
        if frame is not None:
            break
        time.sleep(0.1)
    if frame is None:
        print("screen capture returned no frame")
        return 1

    result = locate_compass_ring(frame)
    if result is None:
        print("No compass ring found. Make sure the cockpit + nav compass are")
        print("visible and the HUD is bright enough.")
        return 1

    cx, cy, r = result
    region = ring_to_region(cx, cy, r, frame.shape[1], frame.shape[0])
    x, y, w, h = region

    # Save an annotated capture so the user can eyeball the detection.
    try:
        import cv2
        outdir = Path(os.path.expandvars(cfg.paths.calibration_dir))
        outdir.mkdir(parents=True, exist_ok=True)
        annotated = frame.copy()
        cv2.circle(annotated, (cx, cy), r, (0, 255, 0), 2)
        cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 255, 0), 2)
        outpath = outdir / "compass_calibration.png"
        cv2.imwrite(str(outpath), annotated)
        print(f"(saved annotated capture to {outpath} -- check the green ring + box)")
    except Exception:  # noqa: BLE001
        pass

    print("")
    print("=== Compass located -- add this to your config.toml ===")
    print("")
    print("[vision]")
    print("enabled = true")
    print('backend = "cyan"')
    print(f'capture_backend = "{cfg.vision.capture_backend}"')
    print(f"region = [{x}, {y}, {w}, {h}]")
    print(f"compass_radius = {r}")
    print(f"# ring detected at ({cx},{cy}) r={r}; ring detection re-centers live, so a few px of drift is fine.")
    print("")
    print("Then do a short run with --engage-keys; the bot will orient to each")
    print("target before it jumps.")
    return 0


def cmd_calibrate_overlay(args) -> int:
    """Interactive screen->overlay transform tuning for the CV debug boxes."""
    cfg = load_config(args.config if args.config.is_file() else None)
    from ed_core.cv_debug_cli import run_calibration
    return run_calibration(cfg)


def cmd_navpanel_overlay(args) -> int:
    """Live per-row nav-icon vision diagnostic on the EDMCOverlay."""
    cfg = load_config(args.config if args.config.is_file() else None)
    from ed_core.cv_debug_cli import run_navpanel_overlay
    return run_navpanel_overlay(cfg, n_rows=args.rows)


def cmd_cleanup(args) -> int:
    """Kill a leftover bot child from a crashed prior session (Layer 4).

    Targets the PID recorded in the PID file ONLY, and only if that PID is a
    live python interpreter that isn't us — so the operator's own shell and
    this very process are never touched (the command-line-match gotcha)."""
    from ed_core.lifecycle import default_pid_path, kill_pid_file_survivor

    pid = kill_pid_file_survivor()
    if pid:
        print(f"cleanup: killed leftover bot child PID {pid}")
    else:
        print(f"cleanup: nothing to do (no live survivor at "
              f"{default_pid_path()})")
    return 0


def cmd_panic(args) -> int:
    """Release any held DirectInput keys immediately (Layer 4)."""
    from ed_core.lifecycle import panic_release_keys

    panic_release_keys()
    print("panic: release_all() sent -- any held keys cleared")
    return 0


def cmd_cv_debug(args) -> int:
    """Live context-aware CV overlay that reacts to the current UI (GuiFocus)."""
    cfg = load_config(args.config if args.config.is_file() else None)
    from ed_core.cv_debug_cli import run_cv_debug
    return run_cv_debug(cfg)


def cmd_doctor(args) -> int:
    from ed_core.doctor import format_results, overall_status, run_all_checks

    cfg = load_config(args.config if args.config.is_file() else None)
    print(f"ed-autojump {__version__}")
    print(f"  config:        {args.config}")
    results = run_all_checks(cfg)
    print(format_results(results))
    rc = overall_status(results)
    print()
    print("FAIL -- fix the issues above before running the bot." if rc else "All critical checks passed.")
    return rc


def cmd_install_binds(args) -> int:
    from ed_core.binds_tool import install_binds_preset, swap_start_preset

    cfg = load_config(args.config if args.config.is_file() else None)
    install_binds_preset(cfg)
    if args.swap:
        swap_start_preset(cfg)
    return 0


def cmd_restore_binds(args) -> int:
    from ed_core.binds_tool import restore_start_preset

    cfg = load_config(args.config if args.config.is_file() else None)
    restore_start_preset(cfg)
    return 0


def cmd_pull_binds(args) -> int:
    from ed_core.pull_binds import format_diff, pull_binds

    cfg = load_config(args.config if args.config.is_file() else None)
    bindings_dir = cfg.paths.binds_dir_expanded()

    try:
        diff, live_path, repo_path = pull_binds(
            preset_name=args.preset or None,
            bindings_dir=bindings_dir,
            apply=args.apply,
        )
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"live:  {live_path}")
    print(f"repo:  {repo_path}")
    print()

    if diff.is_empty():
        print("No differences -- repo preset is already up to date.")
        return 0

    print(format_diff(diff))

    if args.apply:
        print()
        print(f"Applied: repo preset overwritten with live preset.")
    else:
        total = len(diff.added) + len(diff.removed) + len(diff.changed)
        print()
        print(f"{total} difference(s) found. Re-run with --apply to update the repo preset.")

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
        "pull-binds": cmd_pull_binds,
        "run": cmd_run,
        "launch": cmd_launch,
        "setup-frontier-creds": cmd_setup_creds,
        "calibrate-menu": cmd_calibrate_menu,
        "calibrate-compass": cmd_calibrate_compass,
        "calibrate-overlay": cmd_calibrate_overlay,
        "navpanel-overlay": cmd_navpanel_overlay,
        "cv-debug": cmd_cv_debug,
        "cleanup": cmd_cleanup,
        "panic": cmd_panic,
    }
    return dispatch[cmd](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
