"""Measures a track's tempo (BPM) from its audio, for sources that don't provide one.

ffmpeg pulls a short excerpt straight from the stream URL (no full download), and
librosa estimates the tempo. Long mixes are sampled at several points and the fastest
excerpt counts, so a mix with one fast song in it still gets rejected.
"""
import logging
import subprocess

import numpy as np

log = logging.getLogger("radio.tempo")
SR = 22050

try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:  # fall back to a system ffmpeg
    FFMPEG = "ffmpeg"


def _excerpt(url, headers, start, length):
    cmd = [FFMPEG, "-nostdin", "-loglevel", "error"]
    hdr = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items())
    if hdr:
        cmd += ["-headers", hdr]
    cmd += ["-ss", str(int(start)), "-t", str(int(length)), "-i", url,
            "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    out = subprocess.run(cmd, capture_output=True, timeout=90)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.decode(errors="replace").strip()[:200] or "ffmpeg failed")
    return np.frombuffer(out.stdout, dtype=np.float32)


def _bpm(y):
    import librosa
    if len(y) < SR * 10 or float(np.abs(y).max(initial=0)) < 1e-3:
        return None  # too short or silent
    onset = librosa.onset.onset_strength(y=y, sr=SR)
    return float(librosa.feature.tempo(onset_envelope=onset, sr=SR, start_bpm=90)[0])


def measure(url, headers=None, duration=0, is_mix=False):
    """Return the estimated BPM, or None if it couldn't be measured."""
    duration = duration or 0
    if is_mix and duration > 20 * 60:
        points, length = [0.15, 0.5, 0.85], 30
    else:
        points, length = [0.3], 40
    results = []
    for p in points:
        start = max(0, min(duration * p, duration - length - 5)) if duration > length + 10 else 0
        bpm = _bpm(_excerpt(url, headers, start, length))
        if bpm:
            results.append(bpm)
    if not results:
        return None
    return round(max(results), 1)
