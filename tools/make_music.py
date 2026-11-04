"""Generates tools/music/bed.m4a: a soft, upbeat 32 s loop (118 BPM, Am-F-C-G) for the reel background.
Own synthesis = no licensing issue. Replace tools/music/bed.m4a with any track you like; the renderer just loops whatever is there.
Run: python3 tools/make_music.py   (needs numpy + ffmpeg)"""
import subprocess, numpy as np, os
SR, BPM = 44100, 118
beat = 60 / BPM
bars, per = 16, 4
N = int(SR * beat * bars * per)
t_all = np.arange(N) / SR
out = np.zeros(N)

def env(n, a=0.005, d=0.2, s=0.0):
    e = np.ones(n)
    na, nd = max(1, int(a * SR)), max(1, int(d * SR))
    e[:na] = np.linspace(0, 1, na)
    tail = np.exp(-np.arange(n - na) / (nd)) * (1 - s) + s
    e[na:] = tail[: n - na]
    return e

def add(buf, start_s, sig):
    i = int(start_s * SR)
    j = min(len(buf), i + len(sig))
    if i < len(buf):
        buf[i:j] += sig[: j - i]

def note(f, dur, kind="pluck", amp=1.0):
    n = int(dur * SR); t = np.arange(n) / SR
    if kind == "pluck":
        s = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * 2 * f * t) + 0.15 * np.sin(2 * np.pi * 3 * f * t)
        return s * env(n, 0.003, 0.12) * amp
    if kind == "pad":
        s = sum(np.sin(2 * np.pi * f * (1 + d) * t) for d in (-0.003, 0, 0.003)) / 3
        s += 0.25 * np.sin(2 * np.pi * 2 * f * t)
        e = np.minimum(1, t / 0.25) * np.minimum(1, (dur - t) / 0.3)
        return s * e * amp
    if kind == "bass":
        s = np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * 2 * f * t)
        return s * env(n, 0.004, 0.22, 0.15) * amp

def hz(m): return 440 * 2 ** ((m - 69) / 12)

chords = [(57, [57, 60, 64]), (53, [53, 57, 60]), (48, [48, 52, 55]), (55, [55, 59, 62])]   # Am F C G (root, triad)
for b in range(bars):
    root, tri = chords[b % 4]
    t0 = b * per * beat
    for m in tri:                                   # pad chord
        add(out, t0, note(hz(m), per * beat, "pad", 0.16))
    for k in range(8):                              # eighth-note bass
        add(out, t0 + k * beat / 2, note(hz(root - 24 if root > 50 else root - 12), beat * 0.45, "bass", 0.5 if k % 2 == 0 else 0.32))
    arp = [tri[0] + 12, tri[1] + 12, tri[2] + 12, tri[1] + 12, tri[2] + 12, tri[1] + 12, tri[0] + 12, tri[1] + 12]
    for k, m in enumerate(arp):                     # light pluck arpeggio
        add(out, t0 + k * beat / 2, note(hz(m + (12 if b % 8 >= 4 else 0)), beat * 0.5, "pluck", 0.20))
    for k in range(4):                              # soft kick on every beat
        n = int(0.18 * SR); tt = np.arange(n) / SR
        f = 110 * np.exp(-tt * 18) + 45
        kick = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt * 14)
        add(out, t0 + k * beat, kick * 0.55)
    for k in range(4):                              # off-beat hat
        n = int(0.05 * SR)
        rng = np.random.default_rng(b * 8 + k)
        hat = rng.standard_normal(n) * np.exp(-np.arange(n) / SR * 90)
        hat = np.diff(hat, prepend=0)
        add(out, t0 + k * beat + beat / 2, hat * 0.10)
# loop-safe fade on the seam, normalise (peak), stereo-ish -> mono is fine
out /= np.max(np.abs(out)) + 1e-9
out *= 0.8
fade = int(0.01 * SR); out[:fade] *= np.linspace(0, 1, fade); out[-fade:] *= np.linspace(1, 0, fade)
os.makedirs(os.path.join(os.path.dirname(__file__), "music"), exist_ok=True)
dst = os.path.join(os.path.dirname(__file__), "music", "bed.m4a")
raw = dst + ".wav"
import wave
with wave.open(raw, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((out * 32767).astype(np.int16).tobytes())
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw, "-c:a", "aac", "-b:a", "96k", dst], check=True)
os.remove(raw)
print("wrote", dst, os.path.getsize(dst) // 1024, "KB,", round(N / SR, 1), "s")
