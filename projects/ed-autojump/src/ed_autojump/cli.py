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
    sub_run.add_argument(
        "--status", dest="status", action="store_true", default=True,
        help="enable Status.json polling (default: on; HeatSupplier feeds scoop guard)",
    )
    sub_run.add_argument(
        "--no-status", dest="status", action="store_false",
        help="disable Status.json polling",
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
                         help="commander to calibrate (e.g. Duvrazh)")
    sub_cal.add_argument("--is-owner", action="store_true", default=False,
                         help="commander owns the private group (skips select-group step)")

    # ed-autojump calibrate-compass — auto-locate the nav compass on screen.
    sub.add_parser(
        "calibrate-compass",
        help="auto-locate the nav compass and print a [vision] region block",
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
    """Phase-12 main loop.

    Wires JournalTail → Orchestrator → executor dispatch with Recorder
    snooping every event + outcome. By default uses NullSender (does not
    send keys); --engage-keys swaps in DirectInputSender driven by the
    parsed ED-AFK.4.2.binds preset.
    """
    from datetime import datetime, timezone

    from .config import load_config
    from .eddn.publisher import EddnPublisher
    from .journal.tail import JournalTail
    from .keys import NullSender, parse_binds
    from .orchestrator import Orchestrator
    from .panic import PanicSwitch
    from .panic_listener import HotkeyListener, _NullBackend, resolve_backend
    from .planner.spansh import SpanshClient
    from .recorder import Recorder, default_session_path
    from .state import GameState
    from .status.navroute import NavRouteReader
    from .status.status import StatusReader

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

    # Sender selection. Real key dispatch requires the binds preset.
    if args.engage_keys:
        from .keys import DirectInputSender
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
        from .keys import LoggingSender
        sender = LoggingSender(sender, recorder)

    # Status + NavRoute readers (default on when journal dir exists).
    status_reader = None
    navroute_reader = None
    if args.status:
        status_path = journal_dir / "Status.json"
        navroute_path = journal_dir / "NavRoute.json"
        status_reader = StatusReader(status_path)
        navroute_reader = NavRouteReader(navroute_path)

    # EDDN publisher.
    eddn_publisher = None
    if cfg.eddn.publish:
        eddn_publisher = EddnPublisher(
            uploader_id=cfg.eddn.uploader_id,
            software_name=cfg.eddn.software_name,
            software_version=cfg.eddn.software_version,
            enabled=True,
        )

    # Route planner adapter.
    route_planner = None
    if args.route_plot:
        client = SpanshClient()

        def _planner(source: str, dest: str, range_ly: float):
            try:
                return client.plot_route(
                    source=source,
                    destination=dest,
                    range_ly=range_ly,
                    efficiency=cfg.routing.efficiency,
                )
            except Exception as exc:  # noqa: BLE001
                # Orchestrator catches + records; we don't need to here.
                raise
        route_planner = _planner

    # --launch: invoke MEL → wait main menu → optional menu nav → wait LoadGame
    # BEFORE the AFK loop begins. If the launch fails, abort with a clear
    # message rather than start the loop on a non-launched game.
    if getattr(args, "launch", False):
        from .launcher import LauncherError
        from .launcher.flow import FlowStatus, launch_and_enter_game
        from .launcher.menu_nav import MenuNavigator

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
            print(f"--launch failed: {result.status.value} — {result.detail}")
            return 1

    state = GameState()
    # Wire nav-compass alignment when engaging keys. build_vision returns
    # (None, None) unless [vision] is enabled AND a region is calibrated, so
    # this is a no-op for everyone who hasn't run calibrate-compass.
    compass_reader = frame_grabber = sun_grab = None
    if args.engage_keys:
        from .vision.capture import build_sun_grabber, build_vision
        compass_reader, frame_grabber = build_vision(cfg)
        if compass_reader is not None:
            print(f"vision: alignment ON (backend={cfg.vision.backend}, "
                  f"region={tuple(cfg.vision.region)})")
        else:
            # Loud, so a blind run is never mistaken for a steering one.
            reason = ("[vision].enabled = false" if not cfg.vision.enabled
                      else "no compass region calibrated")
            print(f"vision: alignment OFF ({reason}) — the ship will NOT be "
                  "steered. Run `ed-autojump calibrate-compass` and set "
                  "[vision].enabled = true to enable orientation.")
        # The "get off the star" maneuver's dependency depends on the mode:
        #   compass (DEFAULT) -> uses the NAV-COMPASS (compass_reader); NO sun grab.
        #   brightness/sc_assist/refuel -> use the sun-brightness grabber (top 2/3).
        #   blind -> fixed-timer pitch; needs neither.
        escape_mode = cfg.escape.escape_mode
        if escape_mode == "compass":
            if compass_reader is None:
                # Loud: compass mode without vision can't get off the star at all.
                print("escape: mode='compass' but vision/compass is OFF — the ship "
                      "will NOT get off the star (arrivals will stall at it). Run "
                      "`ed-autojump calibrate-compass` and set [vision].enabled = true.")
            else:
                print("escape: mode='compass' — target star -> nav-compass pitch-under "
                      "-> fly clear -> target next -> orient (no brightness, no scoop)")
        elif escape_mode != "blind":
            sun_grab = build_sun_grabber(cfg)
            if sun_grab is None:
                print(f"escape: mode={escape_mode!r} but sun grabber unavailable; "
                      "startup get-off-star pitch disabled (jumps may stall at the star)")
            elif escape_mode == "refuel":
                print("escape: mode='refuel' (DEPRECATED — SC Assist rams the star); "
                      f"compass align {'ON' if compass_reader is not None else 'OFF'}")
            else:
                print(f"escape: sensed mode={escape_mode!r} (sun region probe active)")
    orch = Orchestrator(
        sender=sender,
        recorder=recorder,
        state=state,
        config=cfg,
        panic_switch=panic,
        status_reader=status_reader,
        navroute_reader=navroute_reader,
        eddn_publisher=eddn_publisher,
        route_planner=route_planner,
        compass_reader=compass_reader,
        frame_grabber=frame_grabber,
        sun_grab=sun_grab,
    )

    # Startup route check. The jump loop only engages when a route is plotted;
    # with none the bot sits idle and the ship never moves (this surprised a
    # user). Read NavRoute.json directly so we don't disturb the orchestrator's
    # NavRouteReader dedup state.
    _nav_path = journal_dir / "NavRoute.json"
    _plotted = []
    try:
        from .status.navroute import parse_navroute
        _raw = _nav_path.read_text(encoding="utf-8").strip()
        if _raw:
            _plotted = parse_navroute(_raw).route
    except (FileNotFoundError, OSError, ValueError):
        _plotted = []
    if _plotted:
        print(f"route: {len(_plotted)} systems plotted (next hop "
              f"{_plotted[0].star_system!r}).")
    elif args.route_plot:
        print(f"route: NONE plotted — auto-plot is ON, will plot to "
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
        tail = JournalTail(journal_dir)
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
        orch.run_live(tail, duration_s=args.duration)
        return 0
    except KeyboardInterrupt:
        print("\ninterrupted — tripping panic switch")
        panic.trip()
        orch.request_stop()
        return 130
    finally:
        if listener is not None:
            listener.stop()
        orch.shutdown()


def _build_launch_spec(cfg, *, commander=None, auth=None, product=None):
    """Build a LaunchSpec from config + CLI overrides. Raises LauncherError
    if the commander isn't in config.launcher.profiles."""
    from .launcher import LaunchSpec, resolve_profile

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
    from .launcher import MinEdLauncher, detect_min_ed_launcher

    path = explicit_path
    if path is None and cfg.launcher.mel_path:
        path = Path(cfg.launcher.mel_path)
    det = detect_min_ed_launcher(explicit_path=path)
    if not det.found:
        from .launcher import LauncherError
        raise LauncherError(
            "MinEdLauncher.exe not found — pass --mel-path, set "
            "[launcher].mel_path in config.toml, or install it on PATH"
        )
    return MinEdLauncher(exe_path=det.path)


def cmd_launch(args) -> int:
    """Standalone launch — start ED, wait for main menu, optionally nav to PG."""
    from .journal.tail import JournalTail
    from .launcher import LauncherError
    from .launcher.flow import FlowStatus, launch_and_enter_game
    from .launcher.menu_nav import MenuNavigator

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
        from .keys import DirectInputSender
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
        print(f"[launch] OK — entered as {result.load_game_event.commander}")
        return 0
    if result.status == FlowStatus.MAIN_MENU_READY:
        print(f"[launch] main menu ready — operator handoff. {result.detail}")
        return 0
    print(f"[launch] FAILED: {result.status.value} — {result.detail}")
    return 1


def cmd_setup_creds(args) -> int:
    """Interactive .cred onboarding wizard."""
    from .launcher import LauncherError
    from .launcher.wizard import setup_frontier_creds

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
    from .launcher.wizard import calibrate_menu

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
        print("blocks in your config.toml — TOML treats them as nested tables and")
        print("requires the parent table's own keys to be defined first.")
    return 0


def cmd_calibrate_compass(args) -> int:
    """Grab the screen, find the nav compass ring, print a [vision] region block."""
    import os
    import time

    cfg = load_config(args.config if args.config.is_file() else None)
    try:
        from .vision.capture import ScreenGrabber, locate_compass_ring, ring_to_region
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
        print(f"(saved annotated capture to {outpath} — check the green ring + box)")
    except Exception:  # noqa: BLE001
        pass

    print("")
    print("=== Compass located — add this to your config.toml ===")
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


def cmd_doctor(args) -> int:
    from .doctor import format_results, overall_status, run_all_checks

    cfg = load_config(args.config if args.config.is_file() else None)
    print(f"ed-autojump {__version__}")
    print(f"  config:        {args.config}")
    results = run_all_checks(cfg)
    print(format_results(results))
    rc = overall_status(results)
    print()
    print("FAIL — fix the issues above before running the bot." if rc else "All critical checks passed.")
    return rc


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
        "launch": cmd_launch,
        "setup-frontier-creds": cmd_setup_creds,
        "calibrate-menu": cmd_calibrate_menu,
        "calibrate-compass": cmd_calibrate_compass,
    }
    return dispatch[cmd](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
