"""
.ccvid container format -- binary layout shared between the Python converter
and the CC: Tweaked Lua player (see computercraft/ccvid.lua).

File layout (all multi-byte integers little-endian):

    Header (19 bytes):
        4s   magic          b"CCV1"
        B    version        1
        B    width           columns,  1..70
        B    height          rows,     1..40
        H    fps_x100        target frame rate * 100 (e.g. 2000 = 20.00 fps)
        B    color_mode      0 = 1-bit b/w, 1 = 2-bit (4 level) grayscale,
                              2 = 4-bit (16 color) CC palette
        B    has_audio       0 or 1 -- this video has an audio track *somewhere*
        I    frame_count     number of video frames
        I    audio_length    bytes of raw DFPWM1a audio embedded right here

    Audio blob (audio_length bytes):
        Raw DFPWM1a data, playable through cc.audio.dfpwm's decoder.

        has_audio=1 and audio_length>0: audio is embedded in this file
        (single-computer playback -- simple, but decoding DFPWM and
        rendering video on the same computer can cause dropped frames on
        anything but a short/simple clip).

        has_audio=1 and audio_length==0: this video HAS audio, but it
        ships as a separate same-named .dfpwm file instead, meant to be
        played by a second, dedicated "audio computer" over a wired/
        wireless network -- see computercraft/audio_player.lua. This is
        what the converter produces by default whenever the source has
        audio, since it keeps the (heavier) DFPWM decoding off the
        computer that's also trying to hit a steady frame rate.

        has_audio=0: no audio at all.

    Frames (frame_count records), each:
        H    record_len      length in bytes of [flag byte + payload]
        B    flag            0 = raw packed pixels, 1 = row-wise RLE
        ...  payload         record_len - 1 bytes

    Frame payload, flag = 0 (raw packed pixels):
        Pixels in row-major order, packed MSB-first, `bits_per_pixel` bits
        each (1/2/4 bits for color_mode 0/1/2).

    Frame payload, flag = 1 (row-wise RLE):
        For each row (top to bottom), a sequence of run bytes whose run
        lengths sum to exactly `width`. Each run byte packs the pixel colour
        in the top `bits_per_pixel` bits and (run_length - 1) in the
        remaining bits, e.g. for color_mode 0 (1 bit/pixel):
            bit 7       = colour (0 or 1)
            bits 6..0   = run_length - 1   (run_length in 1..128)
        for color_mode 1 (2 bits/pixel):
            bits 7..6   = colour (0..3)
            bits 5..0   = run_length - 1   (run_length in 1..64)
        for color_mode 2 (4 bits/pixel):
            bits 7..4   = colour (0..15)
            bits 3..0   = run_length - 1   (run_length in 1..16)

Palettes (index -> CC `colors` constant -> blit hex digit):
    color_mode 0 (1-bit), brightness ascending:
        0 = black ("f"),  1 = white ("0")
    color_mode 1 (2-bit), brightness ascending:
        0 = black ("f"),  1 = gray ("7"), 2 = lightGray ("8"), 3 = white ("0")
    color_mode 2 (4-bit): index N maps directly to blit digit N (0-9a-f),
        i.e. the full default CC 16-color palette in its natural blit
        order -- see CC_PALETTE_16 below for the RGB values used for
        nearest-color quantization. Note this only displays real color on
        an *Advanced* monitor/computer; standard ones automatically
        render it as grayscale (CC does this itself).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

MAGIC = b"CCV1"
VERSION = 1

MAX_WIDTH = 154
MAX_HEIGHT = 88

COLOR_MODE_1BIT = 0
COLOR_MODE_2BIT = 1
COLOR_MODE_16 = 2

BITS_PER_PIXEL = {COLOR_MODE_1BIT: 1, COLOR_MODE_2BIT: 2, COLOR_MODE_16: 4}
NUM_COLORS = {COLOR_MODE_1BIT: 2, COLOR_MODE_2BIT: 4, COLOR_MODE_16: 16}

# blit hex digits, brightness ascending (index 0 = darkest)
BLIT_CHARS = {
    COLOR_MODE_1BIT: ["f", "0"],
    COLOR_MODE_2BIT: ["f", "7", "8", "0"],
    # color_mode 2 doesn't have a brightness ordering -- index N *is* blit digit N,
    # matching CC's own colors.toBlit()/paint-file convention directly.
    COLOR_MODE_16: list("0123456789abcdef"),
}

# CC: Tweaked's default 16-color palette, in blit-digit order (0-9a-f),
# as (R, G, B) 0-255 tuples. Verified against the official reference table
# at https://tweaked.cc/module/colors.html. Used to nearest-color-quantize
# source video frames for color_mode 2.
#
# Note: this only renders as real color on an *Advanced* monitor/computer.
# Standard (non-Advanced) monitors automatically render every color as the
# nearest shade of gray -- CC does this conversion itself, no extra work
# needed on our end, but it does mean color_mode 2 has no visual benefit
# over color_mode 1 on a standard monitor.
CC_PALETTE_16 = [
    (240, 240, 240),  # 0 white
    (242, 178, 51),   # 1 orange
    (229, 127, 216),  # 2 magenta
    (153, 178, 242),  # 3 lightBlue
    (222, 222, 108),  # 4 yellow
    (127, 204, 25),   # 5 lime
    (242, 178, 204),  # 6 pink
    (76, 76, 76),     # 7 gray
    (153, 153, 153),  # 8 lightGray
    (76, 153, 178),   # 9 cyan
    (178, 102, 229),  # a purple
    (51, 102, 204),   # b blue
    (127, 102, 76),   # c brown
    (87, 166, 78),    # d green
    (204, 76, 76),    # e red
    (17, 17, 17),     # f black
]

_HEADER_FMT = "<4sBBBHBBII"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)
assert _HEADER_SIZE == 19


@dataclass
class Header:
    width: int
    height: int
    fps: float
    color_mode: int
    has_audio: bool
    frame_count: int
    audio_length: int

    def pack(self) -> bytes:
        return struct.pack(
            _HEADER_FMT,
            MAGIC,
            VERSION,
            self.width,
            self.height,
            int(round(self.fps * 100)),
            self.color_mode,
            1 if self.has_audio else 0,
            self.frame_count,
            self.audio_length,
        )

    @staticmethod
    def unpack(data: bytes) -> "Header":
        magic, version, width, height, fps_x100, color_mode, has_audio, frame_count, audio_length = (
            struct.unpack(_HEADER_FMT, data)
        )
        if magic != MAGIC:
            raise ValueError(f"Not a .ccvid file (bad magic {magic!r})")
        if version != VERSION:
            raise ValueError(f"Unsupported .ccvid version {version}")
        return Header(
            width=width,
            height=height,
            fps=fps_x100 / 100.0,
            color_mode=color_mode,
            has_audio=bool(has_audio),
            frame_count=frame_count,
            audio_length=audio_length,
        )


def pack_frame_raw(pixels, width: int, height: int, color_mode: int) -> bytes:
    """
    Pack a row-major list/array of palette indices (0..num_colors-1) into the
    fixed-size raw bit-packed representation (MSB first).
    """
    bpp = BITS_PER_PIXEL[color_mode]
    out = bytearray()
    acc = 0
    nbits = 0
    for v in pixels:
        acc = (acc << bpp) | (v & ((1 << bpp) - 1))
        nbits += bpp
        while nbits >= 8:
            nbits -= 8
            out.append((acc >> nbits) & 0xFF)
    if nbits > 0:
        out.append((acc << (8 - nbits)) & 0xFF)
    return bytes(out)


def pack_frame_rle(pixels, width: int, height: int, color_mode: int) -> bytes:
    """
    Row-wise run-length encode a row-major list/array of palette indices.
    """
    bpp = BITS_PER_PIXEL[color_mode]
    max_run = 1 << (8 - bpp)
    out = bytearray()
    for y in range(height):
        row = pixels[y * width:(y + 1) * width]
        x = 0
        n = len(row)
        while x < n:
            color = row[x]
            run = 1
            while x + run < n and row[x + run] == color and run < max_run:
                run += 1
            out.append((color << (8 - bpp)) | (run - 1))
            x += run
    return bytes(out)


def encode_frame(pixels, width: int, height: int, color_mode: int) -> bytes:
    """
    Encode one frame, choosing whichever of raw/RLE is smaller. Returns the
    full on-disk record (length prefix + flag + payload).
    """
    raw = pack_frame_raw(pixels, width, height, color_mode)
    rle = pack_frame_rle(pixels, width, height, color_mode)
    if len(rle) < len(raw):
        flag, payload = 1, rle
    else:
        flag, payload = 0, raw
    body = bytes([flag]) + payload
    if len(body) > 0xFFFF:
        raise ValueError("Encoded frame too large (>65535 bytes) -- this should not happen at 70x40")
    return struct.pack("<H", len(body)) + body


def decode_frame_rle(payload: bytes, width: int, height: int, color_mode: int):
    """Inverse of pack_frame_rle -- returns a flat row-major list of palette indices."""
    bpp = BITS_PER_PIXEL[color_mode]
    mask = (1 << bpp) - 1
    pixels = []
    idx = 0
    for _y in range(height):
        produced = 0
        while produced < width:
            b = payload[idx]
            idx += 1
            color = (b >> (8 - bpp)) & mask
            run = (b & ((1 << (8 - bpp)) - 1)) + 1
            pixels.extend([color] * run)
            produced += run
        if produced != width:
            raise ValueError("RLE row did not sum to width -- corrupt frame")
    if idx != len(payload):
        raise ValueError("RLE payload had trailing bytes")
    return pixels


def decode_frame_raw(payload: bytes, width: int, height: int, color_mode: int):
    bpp = BITS_PER_PIXEL[color_mode]
    mask = (1 << bpp) - 1
    total = width * height
    pixels = []
    acc = 0
    nbits = 0
    bi = 0
    for _ in range(total):
        while nbits < bpp:
            acc = (acc << 8) | payload[bi]
            bi += 1
            nbits += 8
        nbits -= bpp
        pixels.append((acc >> nbits) & mask)
    return pixels


def decode_frame(record_body: bytes, width: int, height: int, color_mode: int):
    """record_body = flag byte + payload (i.e. the record without its length prefix)."""
    flag = record_body[0]
    payload = record_body[1:]
    if flag == 0:
        return decode_frame_raw(payload, width, height, color_mode)
    elif flag == 1:
        return decode_frame_rle(payload, width, height, color_mode)
    raise ValueError(f"Unknown frame flag {flag}")


@dataclass
class CCVidWriter:
    """Buffers audio + encoded frames in memory, then writes the final file."""
    width: int
    height: int
    fps: float
    color_mode: int
    _frames: list = field(default_factory=list)
    _audio: bytes = b""
    _external_audio: bool = False

    def add_frame(self, pixels) -> None:
        self._frames.append(encode_frame(pixels, self.width, self.height, self.color_mode))

    def set_audio(self, dfpwm_bytes: bytes) -> None:
        """Embed DFPWM audio directly in this file (single-computer playback)."""
        self._audio = dfpwm_bytes

    def mark_external_audio(self) -> None:
        """
        Flag that this video has audio, but it's shipped as a separate
        same-named .dfpwm file for a networked audio computer to play,
        rather than embedded here. See audio_player.lua.
        """
        self._external_audio = True

    @property
    def frame_count(self) -> int:
        return len(self._frames)

    @property
    def raw_video_bytes(self) -> int:
        return sum(len(f) for f in self._frames)

    def write(self, path: str) -> None:
        header = Header(
            width=self.width,
            height=self.height,
            fps=self.fps,
            color_mode=self.color_mode,
            has_audio=len(self._audio) > 0 or self._external_audio,
            frame_count=len(self._frames),
            audio_length=len(self._audio),
        )
        with open(path, "wb") as f:
            f.write(header.pack())
            f.write(self._audio)
            for rec in self._frames:
                f.write(rec)


class CCVidReader:
    """Minimal reader for the .ccvid format, used for validation/inspection."""

    def __init__(self, path: str):
        self._f = open(path, "rb")
        self.header = Header.unpack(self._f.read(_HEADER_SIZE))
        self.audio = self._f.read(self.header.audio_length) if self.header.has_audio else b""

    def frames(self):
        for _ in range(self.header.frame_count):
            (length,) = struct.unpack("<H", self._f.read(2))
            body = self._f.read(length)
            yield decode_frame(body, self.header.width, self.header.height, self.header.color_mode)

    def close(self):
        self._f.close()
