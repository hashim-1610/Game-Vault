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


def _flutter(duration=0.7, volume=0.22, flutter_hz=16):
    """A soft, modulated whoosh — low-pass-smoothed noise (simple moving
    average, so it reads as fabric/paper rather than sharp static) with a
    slow amplitude wobble layered on top, like a card riffle heard up
    close rather than a sequence of electronic ticks."""
    n = int(SR * duration)
    fade_in = max(1, int(SR * 0.03))
    fade_out = max(1, int(SR * 0.22))
    rnd = random.Random(11)
    raw = [rnd.random() * 2 - 1 for _ in range(n)]
    window = 6
    smooth = []
    acc = 0.0
    for i in range(n):
        acc += raw[i]
        if i >= window:
            acc -= raw[i - window]
        smooth.append(acc / min(i + 1, window))
    samples = []
    for i in range(n):
        t = i / SR
        mod = 0.55 + 0.45 * math.sin(2 * math.pi * flutter_hz * t)
        val = smooth[i] * mod * volume * _envelope(i, n, fade_in, fade_out)
        samples.append(val)
    return samples


def _chord(freqs, duration, volume=0.25):
    """Several sine tones summed together — a real chime/bell instead of a
    single flat tone, used for the more emotionally-loaded moments (trump
    reveal, winning/losing a round or the game)."""
    n = int(SR * duration)
    fade_in = max(1, int(SR * 0.008))
    fade_out = max(1, int(SR * 0.35))
    samples = []
    for i in range(n):
        t = i / SR
        val = sum(math.sin(2 * math.pi * f * t) for f in freqs) / len(freqs)
        val *= volume * _envelope(i, n, fade_in, fade_out)
        samples.append(val)
    return samples


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
    "bid": _render_bytes(_tone(700, 720, 0.1, 0.26)),
    "pass": _render_bytes(_tone(320, 260, 0.14, 0.18)),
    "deal": _render_bytes(_noise(0.12, 0.16)),
    "card_play": _render_bytes(_noise(0.05, 0.16), _tone(220, 160, 0.06, 0.14)),
    "shuffle": _render_bytes(_flutter()),
    # A short bright arpeggio settling into a chord — "something important
    # just happened", paired with the on-screen trump-revealed toast.
    "trump_reveal": _render_bytes(
        _tone(523, 523, 0.08, 0.26), _tone(659, 659, 0.08, 0.26), _tone(784, 784, 0.08, 0.26),
        _chord([523, 659, 784], 0.5, 0.28),
    ),
    "cut": _render_bytes(_tone(300, 780, 0.16, 0.3)),
    "trick_win": _render_bytes(_tone(880, 880, 0.08, 0.24), _tone(1318, 1318, 0.14, 0.24)),
    "high_card": _render_bytes(_tone(523, 523, 0.07, 0.28), _tone(659, 659, 0.07, 0.28),
                                _tone(784, 784, 0.07, 0.28), _tone(1046, 1046, 0.18, 0.32)),
    "pair": _render_bytes(_tone(392, 392, 0.09, 0.28), _tone(494, 494, 0.09, 0.28),
                           _tone(587, 587, 0.09, 0.28), _tone(784, 784, 0.2, 0.32)),
    # Round result — distinct per-team via app.js, computed from
    # round_bidder_team / round_bid_made rather than baked into the event.
    "round_won": _render_bytes(_chord([523, 659, 784], 0.45, 0.3)),
    "round_lost": _render_bytes(_tone(392, 294, 0.4, 0.22)),
    # Game (match) result — bigger versions of the same idea.
    "game_won": _render_bytes(
        _tone(523, 523, 0.09, 0.3), _tone(659, 659, 0.09, 0.3), _tone(784, 784, 0.09, 0.3),
        _chord([523, 659, 784, 1046], 0.7, 0.32),
    ),
    "game_lost": _render_bytes(_tone(440, 349, 0.3, 0.24), _chord([349, 415], 0.6, 0.22)),
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
