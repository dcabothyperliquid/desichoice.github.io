"""Render a vertical (1080x1920) Deal Reel from a JSON payload (env PAYLOAD). Output: out/reel.mp4 + out/meta.json.

payload = {"label": "Mobiles", "items": [{"title", "price", "list_price", "pct", "image"}, ...]}
Voice: edge-tts (free). If TTS is unreachable the reel is rendered silent instead of failing.
"""
import asyncio, io, json, os, subprocess, sys, urllib.request
from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
OUT = "out"
TMP = "tmp"
VOICE = os.environ.get("REEL_VOICE", "en-IN-NeerjaNeural")   # Indian English voice reads Hinglish naturally
FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"]
BG = "#fff7ed"
ACCENT = "#e53935"


def font(size):
    for p in FONTS:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()


def short(title, n=60):
    t = (title or "").strip()
    for sep in (" | ", " - ", " – ", ", "):
        if sep in t:
            t = t.split(sep)[0].strip()
    return (t[:n].rstrip() + "…") if len(t) > n else t


def wrap(draw, text, fnt, maxw):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=fnt) <= maxw:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines[:3]


def rs(v):
    return f"Rs {float(v):,.0f}"


def fit(draw, text, size, maxw):
    while size > 30 and draw.textlength(text, font=font(size)) > maxw:
        size -= 4
    return font(size)


def slide_title(label, n, best):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((90, 560, W - 90, 1360), 48, fill="white", outline="#fdba74", width=6)
    d.text((W // 2, 700), "DESI CHOICE", fill="#f97316", font=font(64), anchor="mm")
    d.text((W // 2, 860), f"{label}", fill="#111111", font=fit(d, label, 110, W - 260), anchor="mm")
    d.text((W // 2, 1020), f"{n} DEALS", fill="#111111", font=font(84), anchor="mm")
    if best:
        d.rounded_rectangle((250, 1130, W - 250, 1260), 36, fill=ACCENT)
        d.text((W // 2, 1195), f"{best}% tak OFF", fill="white", font=font(70), anchor="mm")
    return im


def slide_item(it, idx, total):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((60, 120, W - 60, 1560), 48, fill="white", outline="#fdba74", width=6)
    ok = False
    try:
        ph = Image.open(io.BytesIO(fetch(it["image"]))).convert("RGB")
        ph.thumbnail((860, 860))
        im.paste(ph, ((W - ph.width) // 2, 190 + (860 - ph.height) // 2))
        ok = True
    except Exception as e:
        print("image failed:", it.get("image"), e, file=sys.stderr)
    if not ok:
        d.text((W // 2, 620), "photo na mili", fill="#999999", font=font(48), anchor="mm")
    pct = int(it.get("pct") or 0)
    d.rounded_rectangle((100, 160, 400, 270), 26, fill=ACCENT)
    d.text((250, 215), f"{pct}% OFF", fill="white", font=font(58), anchor="mm")
    d.text((W - 110, 215), f"{idx}/{total}", fill="#999999", font=font(50), anchor="rm")
    f = font(46)
    y = 1085
    for ln in wrap(d, short(it.get("title"), 50), f, W - 220)[:2]:
        d.text((110, y), ln, fill="#222222", font=f, anchor="lm")
        y += 58
    y = 1225
    for b in (it.get("benefits") or [])[:2]:
        b = str(b).strip()
        if not b:
            continue
        d.text((110, y), "+", fill="#16a34a", font=font(44), anchor="lm")
        d.text((160, y), b, fill="#166534", font=fit(d, b, 40, W - 280), anchor="lm")
        y += 64
    price, lp = it.get("price"), it.get("list_price")
    d.text((110, 1430), rs(price), fill="#111111", font=font(100), anchor="lm")
    if lp:
        x0 = 110 + d.textlength(rs(price), font=font(100)) + 36
        t = rs(lp)
        d.text((x0, 1438), t, fill="#888888", font=font(52), anchor="lm")
        w = d.textlength(t, font=font(52))
        d.line((x0, 1433, x0 + w, 1433), fill="#888888", width=5)
    return im


def slide_outro():
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((90, 600, W - 90, 1320), 48, fill="white", outline="#fdba74", width=6)
    d.text((W // 2, 780), "LINKS", fill="#f97316", font=font(110), anchor="mm")
    d.text((W // 2, 930), "COMMENTS me hain", fill="#111111", font=font(76), anchor="mm")
    d.text((W // 2, 1100), "Follow @DesiChoice", fill="#555555", font=font(60), anchor="mm")
    d.text((W // 2, 1200), "roz naye deals", fill="#555555", font=font(60), anchor="mm")
    return im


def say(text):
    return text.replace("%", " percent").replace("Rs", "rupaye")


def voice_lines(label, items):
    n = len(items)
    best = max(int(i.get("pct") or 0) for i in items)
    out = [f"{label} ke {n} deals, {best} percent tak off. Dekhte hain."]
    for k, it in enumerate(items, 1):
        out.append(f"Number {k}. {short(it.get('title'), 40)}. Sirf {float(it['price']):,.0f} rupaye, {int(it.get('pct') or 0)} percent off.")
        for b in (it.get("benefits") or [])[:2]:
            out[-1] += f" {b}."
    out.append("Saare links comments me hain. Follow karo roz ke naye deals ke liye.")
    return [say(x) for x in out]


async def tts_one(text, path):
    import edge_tts
    await edge_tts.Communicate(text, VOICE).save(path)


def tts(texts):
    paths = []
    try:
        for i, t in enumerate(texts):
            p = f"{TMP}/v{i}.mp3"
            asyncio.run(tts_one(t, p))
            paths.append(p)
        return paths
    except Exception as e:
        print("TTS unavailable, rendering silent:", e, file=sys.stderr)
        return None


def dur(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def clip(png, seconds, audio, out):
    vf = f"scale={W}:{H},zoompan=z='min(zoom+0.0004,1.03)':d={int(seconds*25)}:s={W}x{H}:fps=25,format=yuv420p"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-t", f"{seconds:.2f}", "-i", png]
    if audio:
        cmd += ["-i", audio]
    else:
        cmd += ["-f", "lavfi", "-t", f"{seconds:.2f}", "-i", "anullsrc=r=44100:cl=mono"]
    cmd += ["-vf", vf, "-r", "25", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "128k",
            "-ar", "44100", "-ac", "1", "-af", "apad", "-t", f"{seconds:.2f}", "-shortest", out]
    subprocess.run(cmd, check=True)


def main():
    payload = json.loads(os.environ["PAYLOAD"])
    label = payload.get("label") or "Top Deals"
    items = [i for i in payload.get("items", []) if i.get("price")][:5]
    if len(items) < 2:
        sys.exit("need >= 2 items")
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(TMP, exist_ok=True)
    best = max(int(i.get("pct") or 0) for i in items)
    slides = [slide_title(label, len(items), best)] + [slide_item(it, k, len(items)) for k, it in enumerate(items, 1)] + [slide_outro()]
    pngs = []
    for k, s in enumerate(slides):
        p = f"{TMP}/s{k}.png"
        s.save(p)
        pngs.append(p)
    texts = voice_lines(label, items)
    audio = tts(texts)
    clips = []
    for k, png in enumerate(pngs):
        a = audio[k] if audio else None
        secs = max(3.2, (dur(a) + 0.5) if a else 3.6)
        c = f"{TMP}/c{k}.mp4"
        clip(png, secs, a, c)
        clips.append(c)
    with open(f"{TMP}/list.txt", "w") as f:
        for c in clips:
            f.write(f"file '{os.path.abspath(c)}'\n")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", f"{TMP}/list.txt",
                    "-c", "copy", "-movflags", "+faststart", f"{OUT}/reel.mp4"], check=True)
    total = dur(f"{OUT}/reel.mp4")
    json.dump({"label": label, "items": len(items), "seconds": round(total, 1), "voice": bool(audio)}, open(f"{OUT}/meta.json", "w"))
    print("done", total, "s, voice:", bool(audio))


if __name__ == "__main__":
    main()
