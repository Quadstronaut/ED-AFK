"""WinRT ``Windows.Media.Ocr`` engine for the nav-panel READ layer (parser-v2
rebuild, G1/G5/G6 root).

The ratified nav-panel design is OCR-first (memory ed-navpanel-ocr-first-parser):
whole-region crop -> upscale -> white-pad -> WinRT OCR -> rows.  WinRT OCR is
built into Win11 (zero-install at the OS level), reads the dim slanted HUD font
where pytesseract is "hit-or-miss", and returns word bounding boxes in REAL
screen coords -> the icon box is just-left of each name's first word (no
homography needed).  The old monolithic ``winsdk`` package has no cp314 wheel;
the modern split ``winrt-Windows.*`` packages do (validated live on scoop
Python 3.14 reading the Sol frame: NAV BEACON / MERCURY / VENUS / EARTH / MOON /
TITAN COCIJO / MARS / JUPITER / GANYMEDE / IO).

This module is the ENGINE only.  ``navpanel_reader.read_nav_panel_lines`` calls
``ocr_lines`` (preferring WinRT, falling back to pytesseract).  Everything is
fail-soft: if the winrt packages are absent or a frame can't be decoded, the
caller falls back -- the nav panel is never driven blind off a crashed reader.

Required packages (declared in the ed-vision [navocr] extra):
    winrt-Windows.Media.Ocr  winrt-Windows.Graphics.Imaging
    winrt-Windows.Storage.Streams  winrt-Windows.Foundation.Collections
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

# WinRT OCR returns 0 lines without a white margin around the crop (empirically
# confirmed, memory ed-navpanel-ocr-first-parser); 30px is comfortably >=20.
_PAD = 30
_UPSCALE = 2.5

# Leading icon-glyph tokens OCR emits before a body name (the row's depth/kind
# icon read as 'O'/'@'/'©' etc.).  A single char or a known glyph is dropped;
# real first tokens (proc-gen system names "Sifi"/"Tyriedgoea", "Sol", "NAV")
# are multi-char and never in this set, so stripping is safe.
_ICON_GLYPHS = {"O", "0", "@", "©", "o", "*", "[O]", "(O)", "[0]"}


@dataclass(frozen=True)
class OcrWord:
    text: str
    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class OcrLine:
    text: str          # icon-glyph-stripped line text, top-to-bottom order
    y: float           # min word top (screen coords of the UNPADDED upscaled crop)
    words: Tuple[OcrWord, ...]


def available() -> bool:
    """True if the winrt OCR projection imports.  Cheap; never raises."""
    try:
        import winrt.windows.media.ocr  # noqa: F401
        import winrt.windows.graphics.imaging  # noqa: F401
        import winrt.windows.storage.streams  # noqa: F401
        return True
    except Exception:
        return False


def _strip_icon_glyph(text: str) -> str:
    toks = text.split()
    if toks and (len(toks[0]) == 1 or toks[0] in _ICON_GLYPHS):
        toks = toks[1:]
    return " ".join(toks)


def _to_png_bytes(frame: Any, *, upscale: float, pad: int) -> bytes:
    """A BGR/BGRA/gray ndarray (or anything cv2 accepts) -> padded, upscaled PNG
    bytes.  WinRT decodes PNG bytes from an in-memory stream -- this is the
    live-frame path (the flight loop hands us a numpy frame, not a file)."""
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    arr = np.asarray(frame)
    if arr.ndim == 2:
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    elif arr.ndim == 3 and arr.shape[2] == 4:
        arr = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
    if upscale and upscale != 1.0:
        arr = cv2.resize(arr, None, fx=upscale, fy=upscale,
                         interpolation=cv2.INTER_CUBIC)
    if pad:
        arr = cv2.copyMakeBorder(arr, pad, pad, pad, pad,
                                 cv2.BORDER_CONSTANT, value=(255, 255, 255))
    ok, buf = cv2.imencode(".png", arr)
    if not ok:
        raise RuntimeError("cv2.imencode failed on nav-panel crop")
    return bytes(buf.tobytes())


async def _recognize_async(png_bytes: bytes) -> List[OcrLine]:
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    stream = InMemoryRandomAccessStream()
    try:
        writer = DataWriter(stream)
        writer.write_bytes(png_bytes)
        await writer.store_async()
        writer.detach_stream()          # release the stream so we can read it back
        stream.seek(0)

        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise RuntimeError("WinRT: no OCR engine for the user-profile languages")
        result = await engine.recognize_async(bitmap)

        lines: List[OcrLine] = []
        for line in result.lines:
            words = tuple(
                OcrWord(w.text, w.bounding_rect.x, w.bounding_rect.y,
                        w.bounding_rect.width, w.bounding_rect.height)
                for w in line.words
            )
            y = min((w.y for w in words), default=0.0)
            stripped = _strip_icon_glyph(line.text)
            if stripped:
                lines.append(OcrLine(stripped, y, words))
        lines.sort(key=lambda ln: ln.y)
        return lines
    finally:
        # Release the WinRT COM buffer promptly (IClosable) — this runs ~1 Hz
        # across an overnight flight; don't lean on GC to reclaim it.
        try:
            stream.close()
        except Exception:
            pass


def _run(coro):
    """Run an async WinRT coroutine from sync flight code.  asyncio.run normally,
    a fresh loop if one is already running on this thread."""
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def ocr_detailed(frame: Any, *, upscale: float = _UPSCALE,
                 pad: int = _PAD) -> List[OcrLine]:
    """OCR a nav-panel crop -> OcrLine[] (text + word bboxes, top-to-bottom).

    Word bboxes are in the coords of the UPSCALED crop (before padding offset is
    removed): subtract `pad` and divide by `upscale` to map back to the original
    crop, then add the crop origin for screen coords (used by the G5 icon-anchor
    box placement).  Raises on a hard WinRT failure -- callers catch and fall
    back to pytesseract."""
    return _run(_recognize_async(_to_png_bytes(frame, upscale=upscale, pad=pad)))


def ocr_lines(frame: Any, *, upscale: float = _UPSCALE,
              pad: int = _PAD) -> List[str]:
    """OCR a nav-panel crop -> text lines, top-to-bottom, icon-glyph stripped.
    The drop-in replacement for the pytesseract `read_nav_panel_lines` body."""
    return [ln.text for ln in ocr_detailed(frame, upscale=upscale, pad=pad)]
