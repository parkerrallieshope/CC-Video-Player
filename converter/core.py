"""
Core video -> .ccvid conversion pipeline.

This module has no GUI dependency (no tkinter import) so it can be driven
from the command line or unit-tested directly. cc_video_converter.py wraps
this with a Tkinter front-end.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from dfpwm import DFPWMEncoder
from video_format import (
    CCVidWriter,
    COLOR_MODE_1BIT,
    COLOR_MODE_2BIT,
    COLOR_MODE_16,
    CC_PALETTE_16,
    NUM_COLORS,
    MAX_WIDTH,
    MAX_HEIGHT,
)

# CC: Tweaked's built-in monospace font glyphs are 6px wide x 9px tall, so a
# monitor "pixel" (one character cell) is NOT square. To make a 16:9 video
# look correctly proportioned on a 70x40-character monitor -- rather than
# vertically squashed -- we correct for this when choosing how many rows the
# image should occupy. See https://tweaked.cc/peripheral/monitor.html and
# the CraftOS-PC docs, which both confirm the 6x9 glyph cell size.
CHAR_CELL_ASPECT = 6.0 / 9.0  # (pixel width / pixel height) of one character cell

ProgressCB = Optional[Callable[[str, int, int, str], None]]


class ConversionError(RuntimeError):
    pass


def check_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise ConversionError(
            "ffmpeg/ffprobe were not found on your PATH. Install ffmpeg "
            "(https://ffmpeg.org/download.html) and make sure it's on your PATH."
        )


@dataclass
class ProbeResult:
    width: int
    height: int
    duration: float
    fps: float
    has_audio: bool


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_STREAM_RE = re.compile(
    r"Stream #\d+:\d+.*?Video:.*?(?<!\d)(\d{2,5})x(\d{2,5})(?:\s|\[|,)"
)
_FPS_RE = re.compile(r"([\d.]+)\s*fps")
_AUDIO_STREAM_RE = re.compile(r"Stream #\d+:\d+.*?Audio:")


def _probe_via_ffmpeg_stderr(input_path: str) -> Optional[ProbeResult]:
    """
    Fallback prober: `ffmpeg -i <file>` always prints a human-readable
    stream summary to stderr (even though it exits non-zero when no output
    is given), and is a much simpler/older code path than ffprobe's JSON
    writer. Used when the ffprobe JSON path comes back empty for some
    reason. Returns None if this also fails to find anything useful.
    """
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", input_path],
            capture_output=True, encoding="utf-8", errors="replace",
        )
    except (OSError, FileNotFoundError):
        return None

    text = proc.stderr or ""
    vm = _VIDEO_STREAM_RE.search(text)
    if not vm:
        return None
    width, height = int(vm.group(1)), int(vm.group(2))

    fps = 0.0
    fm = _FPS_RE.search(text[vm.end():vm.end() + 200])
    if fm:
        try:
            fps = float(fm.group(1))
        except ValueError:
            fps = 0.0
    if fps <= 0:
        fps = 25.0

    duration = 0.0
    dm = _DURATION_RE.search(text)
    if dm:
        h, m, s = dm.groups()
        duration = int(h) * 3600 + int(m) * 60 + float(s)

    has_audio = bool(_AUDIO_STREAM_RE.search(text))

    return ProbeResult(width=width, height=height, duration=duration, fps=fps, has_audio=has_audio)


def probe(input_path: str) -> ProbeResult:
    check_ffmpeg()

    if not os.path.isfile(input_path):
        raise ConversionError(f"Input file does not exist: {input_path}")
    file_size = os.path.getsize(input_path)
    if file_size == 0:
        raise ConversionError(
            f"Input file is 0 bytes: {input_path}\n"
            "If this file lives in OneDrive (or another cloud-sync folder) and shows a "
            "cloud icon in Explorer, it may not actually be downloaded to this PC yet -- "
            "right-click it and choose 'Always keep on this device', wait for it to finish "
            "downloading, then try again."
        )

    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_streams", "-show_format", "-i", input_path,
            ],
            capture_output=True, encoding="utf-8", errors="replace", check=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip() or "(ffprobe produced no error output)"
        raise ConversionError(f"ffprobe failed (exit code {e.returncode}): {stderr}") from e
    except FileNotFoundError as e:
        raise ConversionError(str(e)) from e

    stdout = out.stdout or ""
    data = None
    if stdout.strip():
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            data = None

    if data is not None and any(s.get("codec_type") == "video" for s in data.get("streams", [])):
        v_stream = next(s for s in data["streams"] if s.get("codec_type") == "video")
        a_stream = next((s for s in data["streams"] if s.get("codec_type") == "audio"), None)
        return _probe_result_from_ffprobe_json(data, v_stream, a_stream)

    # ffprobe's JSON path came back empty or unusable -- fall back to
    # parsing `ffmpeg -i`'s stderr summary instead, a much simpler and
    # more battle-tested code path that behaves the same across platforms.
    fallback = _probe_via_ffmpeg_stderr(input_path)
    if fallback is not None:
        return fallback

    stderr = (out.stderr or "").strip()
    raise ConversionError(
        f"Couldn't read this file's video info ({file_size:,} bytes on disk). ffprobe ran "
        "(exit code 0) but produced no usable output, and the ffmpeg fallback probe couldn't "
        "find a video stream either. To help debug this, please run this exact command in a "
        "terminal and share what it prints:\n\n"
        f'    ffmpeg -hide_banner -i "{input_path}"\n\n'
        + (f"(stderr from the ffprobe attempt: {stderr})" if stderr else "")
    )


def _probe_result_from_ffprobe_json(data: dict, v_stream: dict, a_stream: Optional[dict]) -> ProbeResult:

    width = int(v_stream["width"])
    height = int(v_stream["height"])

    fps = 0.0
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = v_stream.get(key)
        if raw and raw != "0/0":
            num, _, den = raw.partition("/")
            den = den or "1"
            if float(den) != 0:
                fps = float(num) / float(den)
                break
    if fps <= 0:
        fps = 25.0  # sane fallback

    duration = 0.0
    if "duration" in v_stream:
        duration = float(v_stream["duration"])
    elif "format" in data and "duration" in data["format"]:
        duration = float(data["format"]["duration"])

    return ProbeResult(width=width, height=height, duration=duration, fps=fps, has_audio=a_stream is not None)


def compute_fit(src_w: int, src_h: int, max_w: int, max_h: int, correct_cell_aspect: bool = True):
    """
    Compute the largest (cols, rows) that fit within (max_w, max_h) while
    preserving the source's visual aspect ratio. If correct_cell_aspect is
    True, accounts for CC monitor character cells not being square (see
    CHAR_CELL_ASPECT), so the *displayed* image keeps the right proportions
    rather than the *pixel grid*.
    """
    src_ar = src_w / src_h
    cell_ar = CHAR_CELL_ASPECT if correct_cell_aspect else 1.0

    # We want cols/rows * cell_ar == src_ar  =>  cols/rows == src_ar / cell_ar
    target_ratio = src_ar / cell_ar

    cols = max_w
    rows = max(1, round(cols / target_ratio))
    if rows > max_h:
        rows = max_h
        cols = max(1, round(rows * target_ratio))
        cols = min(cols, max_w)
    cols = max(1, min(cols, max_w))
    rows = max(1, min(rows, max_h))
    return cols, rows


# ---------------------------------------------------------------------------
# Dithering
# ---------------------------------------------------------------------------

_BAYER_8 = np.array([
    [0, 32, 8, 40, 2, 34, 10, 42],
    [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44, 4, 36, 14, 46, 6, 38],
    [60, 28, 52, 20, 62, 30, 54, 22],
    [3, 35, 11, 43, 1, 33, 9, 41],
    [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47, 7, 39, 13, 45, 5, 37],
    [63, 31, 55, 23, 61, 29, 53, 21],
], dtype=np.float64)
_BAYER_8_NORM = (_BAYER_8 / 64.0) - 0.5  # centred in [-0.5, 0.5)


def dither_none(gray: np.ndarray, levels: int) -> np.ndarray:
    """Plain nearest-level thresholding, fully vectorized."""
    scaled = gray.astype(np.float64) / 255.0 * (levels - 1)
    idx = np.round(scaled).astype(np.int32)
    return np.clip(idx, 0, levels - 1)


def dither_bayer(gray: np.ndarray, levels: int) -> np.ndarray:
    """Ordered (Bayer) dithering, fully vectorized."""
    h, w = gray.shape
    tile = np.tile(_BAYER_8_NORM, (h // 8 + 1, w // 8 + 1))[:h, :w]
    scaled = gray.astype(np.float64) / 255.0 * (levels - 1) + tile
    idx = np.round(scaled).astype(np.int32)
    return np.clip(idx, 0, levels - 1)


def dither_floyd_steinberg(gray: np.ndarray, levels: int) -> np.ndarray:
    """
    Classic Floyd-Steinberg error-diffusion dithering. Sequential by nature
    (each pixel's error depends on already-processed neighbours), so this
    is a plain Python loop -- at 70x40 = 2800 px/frame this is still fast
    enough for offline conversion.
    """
    h, w = gray.shape
    work = gray.astype(np.float64).copy()
    out = np.zeros((h, w), dtype=np.int32)
    step = 255.0 / (levels - 1)

    for y in range(h):
        row = work[y]
        next_row = work[y + 1] if y + 1 < h else None
        for x in range(w):
            old = row[x]
            level = int(round(old / step))
            if level < 0:
                level = 0
            elif level > levels - 1:
                level = levels - 1
            out[y, x] = level
            err = old - level * step
            if x + 1 < w:
                row[x + 1] += err * 7 / 16
            if next_row is not None:
                if x - 1 >= 0:
                    next_row[x - 1] += err * 3 / 16
                next_row[x] += err * 5 / 16
                if x + 1 < w:
                    next_row[x + 1] += err * 1 / 16
    return out


DITHER_FUNCS = {
    "none": dither_none,
    "bayer": dither_bayer,
    "floyd": dither_floyd_steinberg,
}


# ---------------------------------------------------------------------------
# Color (16-palette) dithering -- same three algorithms, generalized to
# nearest-neighbor search against CC's fixed 16-color palette instead of an
# evenly-spaced grayscale ramp.
# ---------------------------------------------------------------------------

_PALETTE_ARR = np.array(CC_PALETTE_16, dtype=np.float64)  # (16, 3)


def _nearest_palette_vectorized(rgb: np.ndarray) -> np.ndarray:
    """rgb: (H, W, 3) uint8 -> (H, W) int32 palette indices, via squared RGB distance."""
    h, w, _ = rgb.shape
    flat = rgb.reshape(-1, 3).astype(np.float64)  # (H*W, 3)
    # (H*W, 1, 3) - (1, 16, 3) -> (H*W, 16, 3) -> sum over channel -> (H*W, 16)
    dist2 = np.sum((flat[:, None, :] - _PALETTE_ARR[None, :, :]) ** 2, axis=2)
    idx = np.argmin(dist2, axis=1)
    return idx.reshape(h, w).astype(np.int32)


def dither_none_color(rgb: np.ndarray, levels: int = 16) -> np.ndarray:
    """Plain nearest-palette-color quantization, fully vectorized."""
    return _nearest_palette_vectorized(rgb)


def dither_bayer_color(rgb: np.ndarray, levels: int = 16) -> np.ndarray:
    """Ordered (Bayer) dithering: perturb RGB before nearest-palette search."""
    h, w, _ = rgb.shape
    tile = np.tile(_BAYER_8_NORM, (h // 8 + 1, w // 8 + 1))[:h, :w]
    # Spread tuned for CC's uneven ~50-70 unit palette spacing; centered noise
    # in [-32, 32) per channel is enough to break up banding without
    # introducing obviously wrong colors.
    perturbed = rgb.astype(np.float64) + (tile[:, :, None] * 64.0)
    perturbed = np.clip(perturbed, 0, 255)
    return _nearest_palette_vectorized(perturbed)


def _nearest_palette_single(r: float, g: float, b: float) -> int:
    best_i, best_d = 0, None
    for i, (pr, pg, pb) in enumerate(CC_PALETTE_16):
        d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
        if best_d is None or d < best_d:
            best_d, best_i = d, i
    return best_i


def dither_floyd_color(rgb: np.ndarray, levels: int = 16) -> np.ndarray:
    """Floyd-Steinberg error diffusion generalized to 3 channels + a fixed palette."""
    h, w, _ = rgb.shape
    work = rgb.astype(np.float64).copy()
    out = np.zeros((h, w), dtype=np.int32)

    for y in range(h):
        row = work[y]
        next_row = work[y + 1] if y + 1 < h else None
        for x in range(w):
            r, g, b = row[x]
            idx = _nearest_palette_single(r, g, b)
            out[y, x] = idx
            pr, pg, pb = CC_PALETTE_16[idx]
            er, eg, eb = r - pr, g - pg, b - pb
            if x + 1 < w:
                row[x + 1] += (er * 7 / 16, eg * 7 / 16, eb * 7 / 16)
            if next_row is not None:
                if x - 1 >= 0:
                    next_row[x - 1] += (er * 3 / 16, eg * 3 / 16, eb * 3 / 16)
                next_row[x] += (er * 5 / 16, eg * 5 / 16, eb * 5 / 16)
                if x + 1 < w:
                    next_row[x + 1] += (er * 1 / 16, eg * 1 / 16, eb * 1 / 16)
    return out


COLOR_DITHER_FUNCS = {
    "none": dither_none_color,
    "bayer": dither_bayer_color,
    "floyd": dither_floyd_color,
}


def quantize_frame(frame: np.ndarray, color_mode: int, dither: str) -> np.ndarray:
    """
    Dispatch to the right dithering implementation based on color_mode.
    `frame` is (rows, cols) uint8 grayscale for modes 0/1, or (rows, cols, 3)
    uint8 RGB for mode 2 (16-color). Returns (rows, cols) int32 palette indices.
    """
    if color_mode == COLOR_MODE_16:
        return COLOR_DITHER_FUNCS[dither](frame)
    return DITHER_FUNCS[dither](frame, NUM_COLORS[color_mode])


# ---------------------------------------------------------------------------
# Conversion options + main pipeline
# ---------------------------------------------------------------------------

@dataclass
class ConversionOptions:
    input_path: str
    output_path: str
    max_width: int = MAX_WIDTH
    max_height: int = MAX_HEIGHT
    max_fps: float = 20.0
    color_mode: int = COLOR_MODE_1BIT
    dither: str = "floyd"          # "floyd" | "bayer" | "none"
    correct_cell_aspect: bool = True
    include_audio: bool = True
    contrast: float = 1.0          # 1.0 = neutral
    brightness: float = 0.0        # 0.0 = neutral, +/- ~50 is a reasonable range
    progress_cb: ProgressCB = None


def _report(cb: ProgressCB, stage: str, current: int, total: int, message: str = "") -> None:
    if cb:
        cb(stage, current, total, message)


def _run_video_pipe(input_path: str, cols: int, rows: int, fps: float,
                     contrast: float, brightness: float, pixel_format: str = "gray"):
    """
    Launch ffmpeg to decode+scale+letterbox the video to exactly cols x rows
    frames at `fps`, and yield each frame as a numpy array: (rows, cols)
    uint8 for pixel_format="gray", or (rows, cols, 3) uint8 for "rgb24".
    ffmpeg does the heavy lifting (decode of MP4/MKV/AVI/etc, high quality
    Lanczos scaling, and centred letterbox padding).
    """
    vf = (
        f"fps={fps},"
        f"scale={cols}:{rows}:flags=lanczos,"
        f"pad={cols}:{rows}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"eq=contrast={contrast}:brightness={brightness / 255.0},"
        f"format={pixel_format}"
    )
    cmd = [
        "ffmpeg", "-v", "error", "-i", input_path,
        "-vf", vf,
        "-pix_fmt", pixel_format, "-f", "rawvideo", "-an", "pipe:1",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    channels = 1 if pixel_format == "gray" else 3
    frame_bytes = cols * rows * channels
    assert proc.stdout is not None
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            arr = np.frombuffer(buf, dtype=np.uint8)
            yield arr.reshape(rows, cols) if channels == 1 else arr.reshape(rows, cols, channels)
    finally:
        proc.stdout.close()
        stderr = proc.stderr.read() if proc.stderr else b""
        code = proc.wait()
        if code != 0:
            raise ConversionError(f"ffmpeg (video) exited with {code}: {stderr.decode(errors='replace')[-2000:]}")


def _extract_audio_dfpwm(input_path: str, progress_cb: ProgressCB) -> bytes:
    """Decode the input's audio track to mono 8-bit PCM @ 48kHz via ffmpeg, then DFPWM-encode it."""
    cmd = [
        "ffmpeg", "-v", "error", "-i", input_path,
        "-vn", "-ac", "1", "-ar", "48000",
        "-acodec", "pcm_s8", "-f", "s8", "pipe:1",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    encoder = DFPWMEncoder()
    out = bytearray()
    assert proc.stdout is not None
    chunk_size = 1 << 16
    total_read = 0
    try:
        while True:
            buf = proc.stdout.read(chunk_size)
            if not buf:
                break
            # signed 8-bit PCM bytes -> python ints in [-128, 127]
            samples = np.frombuffer(buf, dtype=np.int8).tolist()
            out.extend(encoder.feed(samples))
            total_read += len(buf)
            _report(progress_cb, "audio", total_read, total_read, f"{total_read/48000:.1f}s encoded")
    finally:
        proc.stdout.close()
        stderr = proc.stderr.read() if proc.stderr else b""
        code = proc.wait()
        if code != 0:
            raise ConversionError(f"ffmpeg (audio) exited with {code}: {stderr.decode(errors='replace')[-2000:]}")
    out.extend(encoder.flush())
    return bytes(out)


@dataclass
class ConversionResult:
    output_path: str          # the actual final deliverable: a .zip if audio was included, else the .ccvid
    width: int
    height: int
    fps: float
    frame_count: int
    duration_s: float
    has_audio: bool           # if True, output_path is a .zip containing a .ccvid + a same-named .dfpwm
    output_bytes: int
    video_bytes: int
    audio_bytes: int


def _derive_stem(output_path: str) -> str:
    """Strip a trailing .ccvid/.zip extension (if any) to get a base path to derive filenames from."""
    base, ext = os.path.splitext(output_path)
    if ext.lower() in (".ccvid", ".zip"):
        return base
    return output_path


def convert(opts: ConversionOptions) -> ConversionResult:
    check_ffmpeg()
    info = probe(opts.input_path)

    fps = min(opts.max_fps, info.fps) if info.fps > 0 else opts.max_fps
    fps = max(1.0, fps)

    cols, rows = compute_fit(info.width, info.height, opts.max_width, opts.max_height,
                              opts.correct_cell_aspect)

    _report(opts.progress_cb, "probe", 1, 1,
            f"Source {info.width}x{info.height} @ {info.fps:.2f}fps, "
            f"{info.duration:.1f}s -> target {cols}x{rows} @ {fps:.2f}fps")

    writer = CCVidWriter(width=cols, height=rows, fps=fps, color_mode=opts.color_mode)
    pixel_format = "rgb24" if opts.color_mode == COLOR_MODE_16 else "gray"

    est_total_frames = max(1, int(round(info.duration * fps))) if info.duration > 0 else 0

    frame_idx = 0
    for frame in _run_video_pipe(opts.input_path, cols, rows, fps, opts.contrast, opts.brightness,
                                  pixel_format=pixel_format):
        idx_frame = quantize_frame(frame, opts.color_mode, opts.dither)
        pixels = idx_frame.reshape(-1).tolist()
        writer.add_frame(pixels)
        frame_idx += 1
        if frame_idx % 5 == 0 or frame_idx == est_total_frames:
            _report(opts.progress_cb, "video", frame_idx, est_total_frames,
                     f"frame {frame_idx}" + (f"/{est_total_frames}" if est_total_frames else ""))

    will_have_audio = opts.include_audio and info.has_audio

    if will_have_audio:
        audio_bytes = _extract_audio_dfpwm(opts.input_path, opts.progress_cb)
        writer.mark_external_audio()

        stem = _derive_stem(opts.output_path)
        base_name = os.path.basename(stem)
        ccvid_path = stem + ".ccvid"
        dfpwm_path = stem + ".dfpwm"
        zip_path = stem + ".zip"

        _report(opts.progress_cb, "write", 1, 1, f"Writing {base_name}.ccvid + {base_name}.dfpwm")
        writer.write(ccvid_path)
        with open(dfpwm_path, "wb") as f:
            f.write(audio_bytes)

        _report(opts.progress_cb, "zip", 1, 1, f"Packaging {base_name}.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(ccvid_path, arcname=os.path.basename(ccvid_path))
            zf.write(dfpwm_path, arcname=os.path.basename(dfpwm_path))

        # the zip is the deliverable -- remove the loose copies so there's
        # only one output file to find
        os.remove(ccvid_path)
        os.remove(dfpwm_path)

        final_path = zip_path
        audio_len = len(audio_bytes)
    else:
        _report(opts.progress_cb, "write", 1, 1, f"Writing {os.path.basename(opts.output_path)}")
        writer.write(opts.output_path)
        final_path = opts.output_path
        audio_len = 0

    output_bytes = os.path.getsize(final_path)
    _report(opts.progress_cb, "done", 1, 1, "Done")

    return ConversionResult(
        output_path=final_path,
        width=cols, height=rows, fps=fps, frame_count=writer.frame_count,
        duration_s=writer.frame_count / fps if fps else 0.0,
        has_audio=will_have_audio,
        output_bytes=output_bytes,
        video_bytes=writer.raw_video_bytes,
        audio_bytes=audio_len,
    )
