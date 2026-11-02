"""Deal Reel media warm -- runs on GitHub Actions (NOT on Render's 512MB box).

Xbot (Render) dispatches warm-media.yml with:
  BASE     Xbot public URL
  TARGETS  JSON [{"asin","cat","k","e"}]  (k/e = short-lived signed upload key for that asin)
For every asin: Amazon page (same translate.goog proxy as product_media) -> up to 4 resized JPEG photos + first 7s clip
(ffmpeg) -> uploaded to Xbot /api/desi/media/<asin>/<kind>/<idx>, which stores them in Turso. Meta is uploaded LAST, so a
half-finished upload never looks 'ready'. All heavy work (HTML parse, PIL, ffmpeg) happens here; Render only stores bytes.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import product_media as pm  # noqa: E402

PHOTO_MAX = 4
PHOTO_PX = 900
CLIP_SECS = 7
CLIP_MAX_BYTES = 1_800_000
BASE = os.environ.get("BASE", "").strip().rstrip("/")
UA = pm._UA


def log(*a):
    print(*a, flush=True)


def photo_bytes(url):
    from PIL import Image
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    if r.status_code != 200 or len(r.content) < 2000:
        return None
    im = Image.open(io.BytesIO(r.content)).convert("RGB")
    if min(im.size) < 300:
        return None
    im.thumbnail((PHOTO_PX, PHOTO_PX))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=78, optimize=True)
    return buf.getvalue()


def clip_bytes(urls):
    exe = shutil.which("ffmpeg")
    if not exe:
        log("  ffmpeg nahi mila")
        return None
    for u in urls:
        out = os.path.join(tempfile.gettempdir(), f"dm_{int(time.time() * 1000)}.mp4")
        try:
            r = subprocess.run([exe, "-y", "-loglevel", "error", "-user_agent", UA, "-i", u, "-t", str(CLIP_SECS), "-an",
                                "-vf", "scale='min(720,iw)':-2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                                "-maxrate", "900k", "-bufsize", "1800k", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out],
                               capture_output=True, text=True, timeout=120)
            if r.returncode == 0 and os.path.exists(out):
                size = os.path.getsize(out)
                if 5000 < size <= CLIP_MAX_BYTES:
                    with open(out, "rb") as f:
                        return f.read()
            log("  clip fail:", (r.stderr or "")[-120:])
        except Exception as e:
            log("  clip", type(e).__name__, str(e)[:80])
        finally:
            try:
                os.remove(out)
            except Exception:
                pass
    return None


def upload(t, kind, idx, data, ctype):
    url = f"{BASE}/api/desi/media/{t['asin']}/{kind}/{idx}"
    for i in range(4):
        try:
            r = requests.post(url, params={"k": t["k"], "e": t["e"], "cat": t.get("cat", "")}, data=data,
                              headers={"Content-Type": ctype}, timeout=90)
            if r.status_code == 200:
                return True
            log(f"  upload {kind}/{idx}: http {r.status_code} {r.text[:100]}")
            if r.status_code in (400, 403, 413):
                return False
        except Exception as e:
            log(f"  upload {kind}/{idx}: {type(e).__name__} {str(e)[:80]}")
        time.sleep(5 * (i + 1))        # Render free box so raha ho to jaagne do
    return False


def do_one(t):
    asin = t["asin"]
    html = pm.fetch_dp(asin)
    m = pm.parse(html) if html else {"images": [], "videos": [], "bullets": []}
    saved = 0
    for u in m["images"]:
        if saved >= PHOTO_MAX:
            break
        try:
            b = photo_bytes(u)
        except Exception as e:
            log("  photo", type(e).__name__)
            b = None
        if b and upload(t, "photo", saved, b, "image/jpeg"):
            saved += 1
    clip = False
    if m["videos"]:
        c = clip_bytes(m["videos"])
        clip = bool(c and upload(t, "clip", 0, c, "video/mp4"))
    meta = {"photos": saved, "src_photos": len(m["images"]), "clip": clip, "bullets": m["bullets"][:3],
            "cat": t.get("cat", ""), "t": int(time.time())}
    upload(t, "meta", 0, json.dumps(meta).encode(), "application/json")
    log(f"{asin}: photos {saved}/{len(m['images'])} clip {clip}")
    return saved


def main():
    if not BASE:
        log("BASE missing")
        sys.exit(1)
    targets = json.loads(os.environ.get("TARGETS") or "[]")
    log(f"{len(targets)} product(s) -> {BASE}")
    for t in targets:
        try:
            do_one(t)
        except Exception as e:
            log(t.get("asin"), "failed:", type(e).__name__, str(e)[:120])
        time.sleep(2)
    log("done")


if __name__ == "__main__":
    main()
