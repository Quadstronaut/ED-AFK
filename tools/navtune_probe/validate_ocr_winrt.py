"""Validate ed_vision.ocr_winrt against the pinned Sol frame via the LIVE-FRAME
path (ndarray crop -> in-memory PNG stream -> WinRT), the path the flight loop
uses. Also renders an overlay so the read is visible, not blind.
Run: python tools/navtune_probe/validate_ocr_winrt.py
"""
from __future__ import annotations

from pathlib import Path

import cv2  # type: ignore

from ed_vision.ocr_winrt import available, ocr_detailed

SOL = Path(r"C:\Users\<user>\ed-afk-sessions\navcap_sol_ED_120055\f000.png")
CROP = (485, 425, 480, 400)   # (x, y, w, h) generous NAVIGATION-list region @1080p
UPSCALE = 2.5
PAD = 30
EXPECTED = ["MERCURY", "VENUS", "EARTH", "MOON", "MARS", "JUPITER",
            "GANYMEDE", "NAV BEACON"]


def main() -> int:
    print("WINRT_AVAILABLE", available())
    img = cv2.imread(str(SOL))
    assert img is not None, f"missing {SOL}"
    x, y, w, h = CROP
    crop = img[y:y + h, x:x + w]              # ndarray, exactly what grabber() returns

    lines = ocr_detailed(crop, upscale=UPSCALE, pad=PAD)
    print(f"LINES {len(lines)} (live ndarray path)")
    for ln in lines:
        print(f"  y={ln.y:7.1f}  {ln.text!r}")

    text = " ".join(ln.text for ln in lines).upper()
    hits = [e for e in EXPECTED if e in text]
    print(f"SOL_HITS {len(hits)}/{len(EXPECTED)}: {hits}")

    # Visible overlay: map each line's first-word bbox back to screen coords and
    # box it. bbox is in upscaled+padded crop coords -> /upscale, -pad, +origin.
    vis = img.copy()
    for ln in lines:
        if not ln.words:
            continue
        wd = ln.words[0]
        bx = int(x + (wd.x - PAD) / UPSCALE)
        by = int(y + (wd.y - PAD) / UPSCALE)
        bw = int(wd.w / UPSCALE)
        bh = int(wd.h / UPSCALE)
        # icon box = just-left of the name's first word
        cv2.rectangle(vis, (bx - 34, by), (bx - 6, by + bh), (0, 255, 0), 2)
        cv2.rectangle(vis, (bx, by), (bx + bw, by + bh), (0, 180, 255), 1)
        cv2.putText(vis, ln.text, (bx + bw + 8, by + bh - 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)
    out = Path(__file__).with_name("sol_ocr_anchored.png")
    cv2.imwrite(str(out), vis)
    print("OVERLAY", out)
    return 0 if len(hits) >= 6 else 1


if __name__ == "__main__":
    raise SystemExit(main())
