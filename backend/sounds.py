"""Tiny synthesized sound effects — no external audio files or network
access required, so nothing to license or host from a third party.

Unlike the Streamlit version (which embedded base64 data URIs inline),
this writes real .wav files to disk once at startup; the frontend just
references them as ordinary static URLs (e.g. /static/sounds/trick_win.wav).
"""

import io
import math
import os
import random
import struct
import wave

SR = 22050


def _envelope(i, n, fade_in, fade_out):
    if i < fade_in:
        return i / fade_in
    if i > n - fade_out:
        return max(0.0, (n - i) / fade_out)
    return 1.0


def _tone(freq_start, freq_end, duration, volume=0.3):
    n = int(SR * duration)
    fade_in = max(1, int(SR * 0.005))
    fade_out = max(1, int(SR * 0.03))
    samples = []
    for i in range(n):
        t = i / SR
        freq = freq_start + (freq_end - freq_start) * (i / n)
        val = math.sin(2 * math.pi * freq * t)
        val *= volume * _envelope(i, n, fade_in, fade_out)
        samples.append(val)
    return samples


def _noise(duration, volume=0.2):
    n = int(SR * duration)
    fade_in = max(1, int(SR * 0.01))
    fade_out = max(1, int(SR * 0.05))
    rnd = random.Random(42)
    return [volume * (rnd.random() * 2 - 1) * _envelope(i, n, fade_in, fade_out) for i in range(n)]


def _render_bytes(*segments):
    samples = []
    for seg in segments:
        samples.extend(seg)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        frames = b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in samples)
        wf.writeframes(frames)
    return buf.getvalue()


_SOUNDS = {
    "bid": _render_bytes(_tone(700, 700, 0.12, 0.28)),
    "pass": _render_bytes(_tone(300, 280, 0.12, 0.2)),
    "deal": _render_bytes(_noise(0.3, 0.18)),
    "trump_reveal": _render_bytes(_tone(440, 440, 0.09, 0.3), _tone(554, 554, 0.09, 0.3), _tone(659, 659, 0.2, 0.35)),
    "cut": _render_bytes(_tone(220, 900, 0.18, 0.35)),
    "trick_win": _render_bytes(_tone(880, 880, 0.09, 0.28), _tone(1318, 1318, 0.16, 0.28)),
    "high_card": _render_bytes(_tone(523, 523, 0.08, 0.3), _tone(659, 659, 0.08, 0.3),
                                _tone(784, 784, 0.08, 0.3), _tone(1046, 1046, 0.2, 0.35)),
    "pair": _render_bytes(_tone(392, 392, 0.1, 0.3), _tone(494, 494, 0.1, 0.3),
                           _tone(587, 587, 0.1, 0.3), _tone(784, 784, 0.22, 0.35)),
    "game_win": _render_bytes(_tone(523, 523, 0.1, 0.32), _tone(659, 659, 0.1, 0.32),
                               _tone(784, 784, 0.1, 0.32), _tone(1046, 1046, 0.1, 0.35),
                               _tone(1318, 1318, 0.3, 0.4)),
}

SOUND_NAMES = list(_SOUNDS.keys())


def write_all(directory):
    """Write every synthesized sound to <directory>/<name>.wav. Called once
    at app startup; idempotent (safe to call on every boot)."""
    os.makedirs(directory, exist_ok=True)
    for name, data in _SOUNDS.items():
        path = os.path.join(directory, f"{name}.wav")
        with open(path, "wb") as f:
            f.write(data)
