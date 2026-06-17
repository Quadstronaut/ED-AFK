"""Run the WinRT READ layer on the two operator-supplied 1080p Mandalay frames
(populated Shinrarta Dezhra + unexplored LHS 2509) to calibrate the region and
see exactly what OCR yields on each real case. Renders overlays.
Run: python tools/navtune_probe/validate_real_frames.py
"""
from __future__ import annotations

from pathlib import Path

import cv2  # type: ignore

from ed_vision.ocr_winrt import ocr_detailed

FIX = Path(r"<repo-root>\ED-AFK\projects\ed-autojump\tests\fixtures\navpanel")
# (x, y, w, h) @1920x1080 — name column + the far-right distance column, one
# wide crop (the dark gap between is just whitespace to OCR).
CROP = (520, 455, 800, 400)
UPSCALE = 2.5
PAD = 30

FRAMES = [
    ("shinrarta_populated_1080.png", "Shinrarta Dezhra"),
    ("lhs2509_unexplored_1080.png", "LHS 2509"),
]


def main() -> int:
    x, y, w, h = CROP
    for fname, system in FRAMES:
        img = cv2.imread(str(FIX / fname))
        assert img is not None, fname
        crop = img[y:y + h, x:x + w]
        lines = ocr_detailed(crop, upscale=UPSCALE, pad=PAD)
        print(f"\n=== {system}  ({fname}) — {len(lines)} OCR lines ===")
        for ln in lines:
            print(f"  y={ln.y:7.1f}  {ln.text!r}")
        vis = img.copy()
        for ln in lines:
            if not ln.words:
                continue
            wd = ln.words[0]
            bx = int(x + (wd.x - PAD) / UPSCALE)
            by = int(y + (wd.y - PAD) / UPSCALE)
            bh = int(wd.h / UPSCALE)
            cv2.rectangle(vis, (bx - 34, by), (bx - 6, by + bh), (0, 255, 0), 2)
            cv2.putText(vis, ln.text, (bx, by - 3),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)
        out = FIX.parent.parent.parent.parent.parent / "tools" / "navtune_probe" / f"_real_{system.replace(' ', '_')}.png"
        cv2.imwrite(str(out), vis)
        print(f"  OVERLAY {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
