"""
ED audio-session diagnostic (multi-endpoint).

Read-only. The bot only ever inspects the DEFAULT render endpoint via
pycaw.GetAllSessions(). On machines with a virtual-audio router (SteelSeries
Sonar, VoiceMeeter, DTS, Nahimic, ...) the game often renders into a VIRTUAL
endpoint that isn't the default — so the bot's meter watches a device the
game never touches and reports eternal silence.

This probe enumerates EVERY active render endpoint, finds which one(s)
EliteDangerous64.exe actually renders into, and samples that session's peak
meter at high frequency (default 20 ms, 12x finer than the bot) while logging
session State, mute/volume, and the default endpoint.

Run it, THEN launch ED (any way you like). Paste the SUMMARY back.

    .venv\\Scripts\\python.exe scripts\\audio_probe.py --seconds 120
"""

from __future__ import annotations

import argparse
import time
import warnings

warnings.simplefilter("ignore")  # pycaw spams COMError warnings reading device props

ED = "EliteDangerous64.exe"


def _imports():
    import comtypes  # noqa: F401
    from pycaw.api.audioclient import ISimpleAudioVolume
    from pycaw.api.audiopolicy import IAudioSessionControl2, IAudioSessionManager2
    from pycaw.api.endpointvolume import IAudioMeterInformation
    from pycaw.constants import AudioDeviceState, EDataFlow, ERole, DEVICE_STATE
    from pycaw.pycaw import AudioUtilities
    return (comtypes, ISimpleAudioVolume, IAudioSessionControl2,
            IAudioSessionManager2, IAudioMeterInformation, AudioDeviceState,
            EDataFlow, ERole, DEVICE_STATE, AudioUtilities)


def _active_render_devices(ignore=()):
    """[(name, AudioDevice)] for every ACTIVE render endpoint, minus any whose
    name contains an `ignore` substring (e.g. 'Media' — the user idle-listens
    to ASMR on Sonar - Media and we must never count that as game audio)."""
    (_c, _sav, _c2, _mgr, _meter, AudioDeviceState,
     EDataFlow, _role, DEVICE_STATE, AudioUtilities) = _imports()
    out = []
    try:
        devs = AudioUtilities.GetAllDevices(
            data_flow=EDataFlow.eRender.value,
            device_state=DEVICE_STATE.MASK_ALL.value,
        )
    except Exception:  # noqa: BLE001
        return out
    for d in devs:
        try:
            if d.state != AudioDeviceState.Active:
                continue
            name = d.FriendlyName or str(d)
            if any(s.lower() in name.lower() for s in ignore):
                continue
            out.append((name, d))
        except Exception:  # noqa: BLE001
            continue
    return out


def _default_render_id_and_name():
    (comtypes, _sav, _c2, _mgr, _meter, _ads,
     EDataFlow, ERole, _ds, AudioUtilities) = _imports()
    try:
        enum = AudioUtilities.GetDeviceEnumerator()
        dev = enum.GetDefaultAudioEndpoint(EDataFlow.eRender.value, ERole.eConsole.value)
        did = dev.GetId()
        ad = AudioUtilities.CreateDevice(dev)
        return did, (ad.FriendlyName or "?")
    except Exception as exc:  # noqa: BLE001
        return None, f"(unknown: {exc})"


def _session_manager(comtypes, IAudioSessionManager2, audio_device):
    iface = audio_device._dev.Activate(
        IAudioSessionManager2._iid_, comtypes.CLSCTX_ALL, None)
    return iface.QueryInterface(IAudioSessionManager2)


def _proc_name(pid: int) -> str:
    try:
        import psutil
        return psutil.Process(pid).name()
    except Exception:  # noqa: BLE001
        return ""


def _discover_ed_meters(default_id, ignore=()):
    """Scan ALL active render endpoints (minus ignored) for ED sessions.

    Returns [(device_name, is_default, meter, ctl2)]. Rebound each cycle so a
    recreated session (device/format switch) doesn't leave us on a dead meter.
    """
    (comtypes, ISimpleAudioVolume, IAudioSessionControl2,
     IAudioSessionManager2, IAudioMeterInformation, _ads,
     _df, _role, _ds, _au) = _imports()

    found = []
    for name, dev in _active_render_devices(ignore):
        try:
            mgr = _session_manager(comtypes, IAudioSessionManager2, dev)
            enum = mgr.GetSessionEnumerator()
            for i in range(enum.GetCount()):
                ctl = enum.GetSession(i)
                if ctl is None:
                    continue
                ctl2 = ctl.QueryInterface(IAudioSessionControl2)
                pid = ctl2.GetProcessId()
                if pid and _proc_name(pid).lower() == ED.lower():
                    meter = ctl2.QueryInterface(IAudioMeterInformation)
                    found.append((name, dev.id == default_id, meter, ctl2))
        except Exception:  # noqa: BLE001
            continue
    return found


def _state_name(ctl2) -> str:
    try:
        return {0: "Inactive", 1: "Active", 2: "Expired"}.get(ctl2.GetState(), "?")
    except Exception:  # noqa: BLE001
        return "?"


def _mute_vol(ctl2) -> str:
    try:
        from pycaw.api.audioclient import ISimpleAudioVolume
        sav = ctl2.QueryInterface(ISimpleAudioVolume)
        return f"mute={sav.GetMute()} vol={sav.GetMasterVolume():.2f}"
    except Exception:  # noqa: BLE001
        return "mute=? vol=?"


def _ed_alive() -> bool:
    try:
        import psutil
        return any((p.info.get("name") or "").lower() == ED.lower()
                   for p in psutil.process_iter(["name"]))
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="ED audio-session diagnostic (multi-endpoint)")
    ap.add_argument("--seconds", type=float, default=120.0)
    ap.add_argument("--interval", type=float, default=0.02, help="sample period (s)")
    ap.add_argument("--rediscover", type=float, default=1.0, help="re-enumerate period (s)")
    ap.add_argument("--threshold", type=float, default=0.001)
    ap.add_argument("--ignore", default="Media",
                    help="comma-separated endpoint-name substrings to exclude "
                         "(default 'Media' so idle ASMR is never counted)")
    args = ap.parse_args()

    ignore = [s.strip() for s in args.ignore.split(",") if s.strip()]

    default_id, default_name = _default_render_id_and_name()
    print(f"default render endpoint : {default_name}")
    print(f"ignoring endpoints matching: {ignore or '(none)'}")
    print("active render endpoints  :")
    for name, _d in _active_render_devices(ignore):
        print(f"  - {name}")
    print(f"\nwatching {ED} for {args.seconds:.0f}s "
          f"(sample {args.interval*1000:.0f}ms, threshold {args.threshold})\n")

    clock = time.monotonic
    t0 = clock()
    deadline = t0 + args.seconds
    last_rediscover = -1e9
    meters: list = []

    samples = above = 0
    max_peak = 0.0
    first_above_t = last_above_t = None
    ever_session = False
    ed_endpoints: set[str] = set()
    saw_on_default = saw_off_default = False
    alive_no_session = False
    last_key = None
    last_sec = -1.0
    sec_peak = 0.0

    while clock() < deadline:
        now = clock()

        if now - last_rediscover >= args.rediscover:
            last_rediscover = now
            meters = _discover_ed_meters(default_id, ignore)
            if meters:
                ever_session = True
                for name, is_def, _m, ctl2 in meters:
                    ed_endpoints.add(name)
                    saw_on_default |= is_def
                    saw_off_default |= (not is_def)
                name, is_def, _m, ctl2 = meters[0]
                key = (len(meters), name, is_def, _state_name(ctl2), _mute_vol(ctl2))
                if key != last_key:
                    tag = "DEFAULT" if is_def else "NON-default"
                    print(f"[{now-t0:6.2f}s] ED session on '{name}' ({tag}) "
                          f"state={key[3]} {key[4]} | sessions={len(meters)}")
                    last_key = key
            else:
                if _ed_alive():
                    alive_no_session = True
                    if last_key != "NOSESS":
                        print(f"[{now-t0:6.2f}s] ED RUNNING but no audio session on ANY endpoint")
                        last_key = "NOSESS"
                elif last_key != "NOPROC":
                    print(f"[{now-t0:6.2f}s] ED not running yet...")
                    last_key = "NOPROC"

        peak = 0.0
        for _n, _d, meter, _c in meters:
            try:
                peak = max(peak, float(meter.GetPeakValue()))
            except Exception:  # noqa: BLE001
                pass

        if meters:
            samples += 1
            sec_peak = max(sec_peak, peak)
            max_peak = max(max_peak, peak)
            if peak > args.threshold:
                above += 1
                last_above_t = now - t0
                if first_above_t is None:
                    first_above_t = now - t0
                    print(f"[{now-t0:6.2f}s] FIRST audio over threshold: peak={peak:.4f}")

        if now - t0 - last_sec >= 1.0:
            last_sec = now - t0
            if meters:
                print(f"[{now-t0:6.2f}s] peak(1s max)={sec_peak:.4f}")
            sec_peak = 0.0

        time.sleep(args.interval)

    print("\n================ SUMMARY ================")
    print(f"default render endpoint           : {default_name}")
    print(f"ED session ever seen              : {ever_session}")
    print(f"ED endpoints used                 : {sorted(ed_endpoints) or '(none)'}")
    print(f"  on default endpoint             : {saw_on_default}")
    print(f"  on a NON-default endpoint       : {saw_off_default}")
    print(f"ED alive but session-less         : {alive_no_session}")
    print(f"samples (session present)         : {samples}")
    if samples:
        print(f"samples over threshold            : {above} ({100.0*above/samples:.1f}%)")
    print(f"max peak observed                 : {max_peak:.4f}")
    print(f"first / last audio over thresh (s): {first_above_t} / {last_above_t}")

    print("\ninterpretation:")
    if saw_off_default and not saw_on_default:
        print("  -> ENDPOINT MISMATCH CONFIRMED. ED renders into a NON-default endpoint")
        print("     (likely a virtual-audio device). The bot only watches the default,")
        print("     so it sees silence. Fix: probe the endpoint ED actually uses, or")
        print("     point ED / Windows-default at the same physical device.")
    elif ever_session and samples and above == 0:
        print("  -> METER QUIRK or SILENT. Session exists but peak never moved. If you")
        print("     HEARD sound, the virtual device's per-session meter isn't reporting.")
    elif first_above_t is not None and last_above_t is not None \
            and (last_above_t - first_above_t) < 1.0:
        print("  -> DROPOUT. Sound appeared then stopped within ~1s (format/device")
        print("     renegotiation or focus-mute). Check the state line near the blip.")
    elif samples and above and (100.0*above/samples) < 10.0:
        print("  -> SPARSE audio: real but rare; the bot's 250ms poll aliases past it.")
    elif not ever_session and alive_no_session:
        print("  -> ED ran with NO session on any endpoint we can see. Exclusive-mode")
        print("     capture or a routing layer hiding the session.")
    else:
        print("  -> Audio looks sustained and detectable on the default endpoint.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
