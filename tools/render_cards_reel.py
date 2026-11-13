"""Card Reel (1080x1920): the same deal cards as the Facebook photo post, as a swipe-style carousel video.
Env PAYLOAD = {"label": "Daily Essentials", "items": [{asin,title,price,list_price,pct,rating,reviews,image}]}.
Intro slide -> one slide per product (photo, % OFF, price, struck list price, stars + ratings) -> outro. Slide-left transitions + music bed.
Output: out/reel.mp4 + out/meta.json ({"asins": [...]}: products that really made it into the video)."""
import io, json, math, os, re, subprocess, sys, urllib.request
from PIL import Image, ImageDraw, ImageFont, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 1080, 1920
OUT, TMP = os.environ.get("REEL_OUT", "out"), os.environ.get("REEL_TMP", "tmp")
HIND = os.path.join(HERE, "fonts", "Hind-Bold.ttf")
FONTS = [HIND, "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"]
MUSIC = os.environ.get("REEL_MUSIC", os.path.join(HERE, "music", "bed.m4a"))
MUSIC_VOL = float(os.environ.get("REEL_MUSIC_VOL", "0.55"))
ACCENT = "#e53935"
T_INTRO, T_ITEM, T_OUTRO, T_X = 2.6, 4.2, 2.8, 0.45


def font(size):
    for p in FONTS:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def inr(v):
    try:
        return "Rs " + f"{float(v):,.0f}"
    except Exception:
        return "Rs -"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def wrap(d, text, f, maxw, maxlines):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=f) <= maxw:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
        if len(lines) == maxlines:
            break
    if cur and len(lines) < maxlines:
        lines.append(cur)
    if len(lines) == maxlines and " ".join(lines) != text.strip():
        lines[-1] = lines[-1].rstrip(" ,.-|") + "…"
    return lines


def blurred_bg(photo):
    bg = photo.copy().resize((W, H), Image.LANCZOS).filter(ImageFilter.GaussianBlur(40))
    ov = Image.new("RGB", (W, H), (20, 20, 30))
    return Image.blend(bg, ov, 0.62)


def star(d, cx, cy, r, fill):
    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.42
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    d.polygon(pts, fill=fill)


def fmt_n(n):
    return f"{n:,}"


def header(d, label, idx=None, total=None):
    d.rounded_rectangle((50, 110, W - 50, 220), 50, fill=(255, 255, 255, 235))
    d.text((100, 165), "DESI CHOICE", font=font(46), fill="#f97316", anchor="lm")
    d.text((W - 100, 165), label if idx is None else f"{idx}/{total}", font=font(44), fill="#111111", anchor="rm")


def product_slide(it, photo, label, idx, total, path):
    img = blurred_bg(photo).convert("RGBA")
    d = ImageDraw.Draw(img, "RGBA")
    header(d, label, idx, total)
    d.rounded_rectangle((50, 270, W - 50, 1090), 56, fill=(255, 255, 255, 255))
    p = photo.copy()
    p.thumbnail((W - 200, 720))
    img.paste(p, ((W - p.width) // 2, 270 + (820 - p.height) // 2))
    pct = int(it.get("pct") or 0)
    if pct:
        d.rounded_rectangle((80, 300, 420, 400), 28, fill=ACCENT)
        d.text((250, 350), f"{pct}% OFF", font=font(58), fill="white", anchor="mm")
    # title
    f = font(50)
    lines = wrap(d, re.sub(r"\s+", " ", str(it.get("title") or "")).strip(), f, W - 160, 2)
    d.rounded_rectangle((50, 1120, W - 50, 1290), 40, fill=(0, 0, 0, 200))
    ys = [1205] if len(lines) == 1 else [1178, 1236]
    for ln, y in zip(lines, ys):
        d.text((W // 2, y), ln, font=f, fill="#ffeb3b", anchor="mm")
    # price strip (kept clear of the Reels UI at the bottom)
    d.rounded_rectangle((50, 1330, W - 50, 1640), 44, fill=(255, 255, 255, 240))
    price, lp = it.get("price"), it.get("list_price")
    d.text((100, 1440), inr(price), font=font(124), fill="#111111", anchor="lm")
    if lp:
        x0 = 100 + d.textlength(inr(price), font=font(124)) + 30
        t = inr(lp)
        d.text((x0, 1452), t, font=font(56), fill="#888888", anchor="lm")
        d.line((x0, 1447, x0 + d.textlength(t, font=font(56)), 1447), fill="#888888", width=5)
    try:
        rating, n = float(it.get("rating") or 0), int(float(it.get("reviews") or 0))
    except Exception:
        rating, n = 0.0, 0
    if rating >= 1:
        gap = 66
        for k in range(5):
            star(d, 130 + k * gap, 1565, 28, "#f59e0b" if rating >= k + 0.75 else ("#fbcf72" if rating >= k + 0.25 else "#d4d4d4"))
        lab = f"{rating:.1f}" + (f"  ({fmt_n(n)} ratings)" if n else "")
        d.text((130 + 5 * gap + 8, 1566), lab, font=font(46), fill="#333333", anchor="lm")
    img.convert("RGB").save(path)


def intro_slide(items, photos, label, path):
    img = blurred_bg(photos[0]).convert("RGBA")
    d = ImageDraw.Draw(img, "RGBA")
    header(d, label)
    best = max([int(i.get("pct") or 0) for i in items] + [0])
    d.text((W // 2, 520), label.upper(), font=font(92), fill="#ffeb3b", anchor="mm")
    d.text((W // 2, 660), f"{len(items)} sasti deals aaj ki", font=font(64), fill="white", anchor="mm")
    if best:
        d.rounded_rectangle((W // 2 - 260, 740, W // 2 + 260, 870), 40, fill=ACCENT)
        d.text((W // 2, 805), f"upto {best}% OFF", font=font(66), fill="white", anchor="mm")
    n = len(photos)
    cw = 290
    x = (W - (n * cw + (n - 1) * 30)) // 2
    for p in photos:
        d.rounded_rectangle((x, 1000, x + cw, 1000 + cw), 36, fill=(255, 255, 255, 255))
        q = p.copy(); q.thumbnail((cw - 40, cw - 40))
        img.paste(q, (x + (cw - q.width) // 2, 1000 + (cw - q.height) // 2))
        x += cw + 30
    d.text((W // 2, 1480), "Swipe karke dekho  >>>", font=font(54), fill="white", anchor="mm")
    img.convert("RGB").save(path)


def outro_slide(photo, label, path):
    img = blurred_bg(photo).convert("RGBA")
    d = ImageDraw.Draw(img, "RGBA")
    header(d, label)
    d.text((W // 2, 700), "Saare links", font=font(110), fill="#ffeb3b", anchor="mm")
    d.text((W // 2, 840), "COMMENT me hain", font=font(110), fill="#ffeb3b", anchor="mm")
    d.polygon([(W // 2 - 90, 1000), (W // 2 + 90, 1000), (W // 2, 1130)], fill="white")
    d.text((W // 2, 1300), "Follow karo Desi Choice", font=font(58), fill="white", anchor="mm")
    d.text((W // 2, 1390), "roz ki best Amazon deals", font=font(46), fill=(255, 255, 255, 200), anchor="mm")
    img.convert("RGB").save(path)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-1500:], file=sys.stderr)
        raise SystemExit("ffmpeg failed")


def assemble(slides, durs, out_path):
    n = len(slides)
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for s, t in zip(slides, durs):
        cmd += ["-loop", "1", "-framerate", "30", "-t", f"{t + T_X:.2f}", "-i", s]
    parts, last, off = [], "[0:v]", 0.0
    for i in range(1, n):
        off += durs[i - 1]
        parts.append(f"{last}[{i}:v]xfade=transition=slideleft:duration={T_X}:offset={off:.2f}[x{i}]")
        last = f"[x{i}]"
    total = sum(durs) + T_X
    fg = ";".join(parts) if parts else "[0:v]null[x0]"
    last = last if parts else "[x0]"
    have_music = os.path.exists(MUSIC)
    if have_music:
        cmd += ["-stream_loop", "-1", "-i", MUSIC]
        fg += f";[{n}:a]volume={MUSIC_VOL},atrim=0:{total:.2f},afade=t=out:st={max(0, total - 1.2):.2f}:d=1.2[a]"
    cmd += ["-filter_complex", fg, "-map", last]
    if have_music:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "128k"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "22", "-r", "30", "-movflags", "+faststart", "-t", f"{total:.2f}", out_path]
    run(cmd)
    print(f"assembled {n} slides, {total:.1f}s, music={have_music}")


def main():
    pl = json.loads(os.environ["PAYLOAD"])
    label = str(pl.get("label") or "Daily Essentials")
    os.makedirs(OUT, exist_ok=True); os.makedirs(TMP, exist_ok=True)
    good, photos = [], []
    for it in pl.get("items") or []:
        try:
            im = Image.open(io.BytesIO(fetch(it["image"]))).convert("RGB")
            good.append(it); photos.append(im)
        except Exception as e:
            print("photo failed for", it.get("asin"), e, file=sys.stderr)
    if not good:
        raise SystemExit("no usable product photo")
    slides, durs = [], []
    p = os.path.join(TMP, "s_intro.png"); intro_slide(good, photos, label, p); slides.append(p); durs.append(T_INTRO)
    for i, (it, ph) in enumerate(zip(good, photos), 1):
        p = os.path.join(TMP, f"s_{i}.png"); product_slide(it, ph, label, i, len(good), p); slides.append(p); durs.append(T_ITEM)
    p = os.path.join(TMP, "s_outro.png"); outro_slide(photos[-1], label, p); slides.append(p); durs.append(T_OUTRO)
    assemble(slides, durs, os.path.join(OUT, "reel.mp4"))
    json.dump({"asins": [i.get("asin") for i in good], "kind": "cards"}, open(os.path.join(OUT, "meta.json"), "w"))


if __name__ == "__main__":
    main()
