"""
DFPWM1a audio codec.

DFPWM (Dynamic Filter Pulse Width Modulation) is the 1-bit-per-sample audio
codec used by ComputerCraft/CC: Tweaked speakers. This module implements the
DFPWM1a encoder (and, for self-testing purposes only, a matching decoder) so
that we can turn ordinary PCM audio into a byte stream that CC: Tweaked's
built-in `cc.audio.dfpwm` decoder can play back correctly.

The algorithm implemented here is the DFPWM1a reference algorithm originally
written by Ben "GreaseMonkey" Russell (released to the public domain, see
https://github.com/ChenThread/dfpwm/blob/master/1a/), the same reference used
by CC: Tweaked and by FFmpeg's own DFPWM1a codec. It is re-implemented here
from scratch in Python, following the public-domain specification.

Input PCM format expected by the encoder: mono, 8-bit **signed** samples in
the range [-128, 127] -- exactly what CC: Tweaked's speaker.playAudio expects
on the way out, and what `ffmpeg -f s8` produces on the way in.
"""

from __future__ import annotations

CONST_PREC = 10
_PREC_ROUND = 1 << (CONST_PREC - 1)          # rounding term for the fixed point IIR
_PREC_MAX = (1 << CONST_PREC) - 1            # max "strength" value
_PREC_MIN = 1 + (1 << (CONST_PREC - 8))      # min "strength" value (== 5 for CONST_PREC=10)


class DFPWMEncoder:
    """
    Streaming DFPWM1a encoder.

    Create one instance per audio stream (it carries state between calls,
    exactly like CC: Tweaked's `dfpwm.make_encoder()`), and feed it PCM data
    in any chunk size via `feed()`. Each call returns the encoded bytes for
    that chunk. If the total number of samples fed is not a multiple of 8,
    the final partial byte is zero-padded (silence) on `flush()`.
    """

    def __init__(self) -> None:
        self.charge = 0        # q
        self.strength = 0      # s
        self.last_target = -128  # lt
        self._pending: list[int] = []  # leftover samples (< 8) between feed() calls

    def feed(self, samples) -> bytes:
        """Encode an iterable of signed 8-bit PCM samples (-128..127)."""
        buf = self._pending
        buf.extend(samples)
        out = bytearray()
        n_complete = (len(buf) // 8) * 8
        for i in range(0, n_complete, 8):
            out.append(self._encode_byte(buf[i:i + 8]))
        # keep the remainder for the next call
        self._pending = buf[n_complete:]
        return bytes(out)

    def flush(self) -> bytes:
        """Pad any remaining <8 samples with silence and encode the final byte."""
        if not self._pending:
            return b""
        padded = self._pending + [0] * (8 - len(self._pending))
        self._pending = []
        return bytes([self._encode_byte(padded)])

    def _encode_byte(self, eight_samples) -> int:
        q = self.charge
        s = self.strength
        lt = self.last_target
        d = 0
        for j, v in enumerate(eight_samples):
            # clamp defensively -- callers should already be passing -128..127
            if v < -128:
                v = -128
            elif v > 127:
                v = 127

            t = -128 if (v < q or v == -128) else 127

            bit = 1 if t > 0 else 0
            d |= (bit << j)  # bit 0 = first sample of the byte (LSB first)

            # adjust charge (predictor) towards the target
            nq = q + (((s * (t - q)) + _PREC_ROUND) >> CONST_PREC)
            if nq == q and nq != t:
                nq += 1 if t == 127 else -1
            q = nq

            # adjust strength (adaptive step size)
            st = 0 if t != lt else _PREC_MAX
            ns = s
            if ns != st:
                ns += 1 if st != 0 else -1
            if ns < _PREC_MIN:
                ns = _PREC_MIN
            s = ns

            lt = t

        self.charge, self.strength, self.last_target = q, s, lt
        return d


class DFPWMDecoder:
    """
    Streaming DFPWM1a decoder, used only to self-test the encoder above by
    round-tripping audio and checking the reconstruction is sane. This is
    *not* used by the CC: Tweaked side -- CC provides its own decoder via
    `cc.audio.dfpwm`. Included here purely for offline verification.
    """

    def __init__(self, lpf_strength: int = 140) -> None:
        self.fq = 0
        self.charge = 0
        self.strength = 0
        self.last_target = -128
        self.lpf_strength = lpf_strength

    def decode(self, data: bytes):
        out = []
        q = self.charge
        s = self.strength
        lt = self.last_target
        fq = self.fq
        fs = self.lpf_strength
        for byte in data:
            for j in range(8):
                bit = (byte >> j) & 1
                t = 127 if bit else -128

                nq = q + (((s * (t - q)) + _PREC_ROUND) >> CONST_PREC)
                if nq == q and nq != t:
                    q += 1 if t == 127 else -1
                lq = q
                q = nq

                st = 0 if t != lt else _PREC_MAX
                ns = s
                if ns != st:
                    ns += 1 if st != 0 else -1
                if ns < _PREC_MIN:
                    ns = _PREC_MIN
                s = ns

                ov = (nq + lq) >> 1 if t != lt else nq
                fq += ((fs * (ov - fq)) + 0x80) >> 8
                out.append(fq)

                lt = t
        self.charge, self.strength, self.last_target, self.fq = q, s, lt, fq
        return out


def encode_pcm_s8(samples) -> bytes:
    """Convenience: encode a full buffer of signed 8-bit PCM in one call."""
    enc = DFPWMEncoder()
    return enc.feed(samples) + enc.flush()
