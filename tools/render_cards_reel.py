"""Card Reel (1080x1920): the same deal cards as the Facebook photo post, as a swipe-style carousel video.
Env PAYLOAD = {"label": "Daily Essentials", "items": [{asin,title,price,list_price,pct,rating,reviews,image}]}.
Hook slide (price/theme hook in the first second) -> one slide per product (photo, % OFF, short title, price, struck list price,
stars + ratings, "links COMMENT me" pill) -> outro (comment CTA + affiliate disclosure). Fast slide-left transitions + music bed.
Output: out/reel.mp4 + out/meta.json ({"asins": [...]}: products that really made it into the video)."""
import io, json, math, os, re, subprocess, sys, urllib.request
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 1080, 1920
OUT, TMP = os.environ.get("REEL_OUT", "out"), os.environ.get("REEL_TMP", "tmp")
HIND = os.path.join(HERE, "fonts", "Hind-Bold.ttf")
FONTS = [HIND, "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"]
MUSIC = os.environ.get("REEL_MUSIC", os.path.join(HERE, "music", "bed.m4a"))
MUSIC_VOL = float(os.environ.get("REEL_MUSIC_VOL", "0.8"))          # final loudness is set by loudnorm (-14 LUFS), this just balances bed vs sfx
ACCENT = "#e53935"
T_INTRO, T_ITEM, T_OUTRO, T_X = 2.0, 4.2, 3.0, 0.25          # 4.2s per product = time to actually read it; 5 products ~26s, 3 products ~18s


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
        return "₹" + f"{float(v):,.0f}"
    except Exception:
        return "₹-"


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


def hires(url):
    """Amazon image urls carry a size token (._SX300_.jpg); ask for a big one so the photo stays sharp when enlarged."""
    return re.sub(r"\._[A-Za-z0-9,_\-]+_\.(jpe?g|png|webp)$", r"._SL1200_.\1", str(url or ""), flags=re.I)


def autocrop(im, thr=244, pad=0.03):
    """Trim the white margins of a product photo so the product fills the card instead of floating in empty space."""
    g = im.convert("L").point(lambda v: 255 if v < thr else 0)
    bb = g.getbbox()
    if not bb:
        return im
    x0, y0, x1, y1 = bb
    w, h = im.size
    if (x1 - x0) * (y1 - y0) < 0.04 * w * h:          # almost nothing found -> do not risk cropping a real photo
        return im
    px, py = int((x1 - x0) * pad) + 4, int((y1 - y0) * pad) + 4
    return im.crop((max(0, x0 - px), max(0, y0 - py), min(w, x1 + px), min(h, y1 + py)))


_STOP = {"for", "with", "and", "&", "of", "in", "to", "the", "a", "by", "-", "|"}


def short_title(title, max_words=5, max_chars=32):
    """Amazon titles are 150 chars of keyword soup. Keep the punchy front: cut at | , - ( and trailing filler words."""
    t = re.sub(r"\s+", " ", str(title or "")).strip()
    t = re.split(r"\s[|\-–—]\s|[|,(]", t)[0].strip() or t
    t = re.split(r"\s(?:for|with|suitable|compatible)\s", t, maxsplit=1, flags=re.I)[0].strip() or t      # 'X for Women ...' -> 'X'
    words = t.split()[:max_words]
    while len(words) > 2 and " ".join(words).__len__() > max_chars:
        words.pop()
    while len(words) > 1 and words[-1].lower().strip(",.") in _STOP:
        words.pop()
    return " ".join(words)


def nice_ceil(v):
    step = 50 if v < 1000 else 100 if v < 5000 else 500
    return int(math.ceil(v / step) * step)


def hook_text(items):
    """First-second hook, from the real data: 'Teeno ₹650 ke andar' when prices sit close together, else '₹649 se shuru'."""
    pr = []
    for i in items:
        try:
            pr.append(float(i.get("price")))
        except Exception:
            pass
    if not pr:
        return "Aaj ki best deals"
    mn, mx = min(pr), max(pr)
    n = len(pr)
    if n >= 2 and mx <= 1.6 * mn:
        word = {2: "Dono", 3: "Teeno", 4: "Chaaron", 5: "Paanchon"}.get(n, f"Sab {n}")
        return f"{word} {inr(nice_ceil(mx))} ke andar"
    return f"{inr(mn)} se shuru!"


def blurred_bg(photo):
    """Vivid purple->magenta gradient with a tinted, saturated blur of the product photo (the old dark grey looked dull)."""
    mask = Image.linear_gradient("L").resize((W, H), Image.BILINEAR)
    grad = Image.composite(Image.new("RGB", (W, H), (150, 28, 120)), Image.new("RGB", (W, H), (30, 10, 70)), mask)
    blur = photo.copy().resize((W, H), Image.BILINEAR).filter(ImageFilter.GaussianBlur(60))
    blur = ImageEnhance.Color(blur).enhance(1.8)
    return Image.blend(grad, blur, 0.22)


def cta_pill(d, y=205):
    """Always-on 'links in comments' pill (the viewer should know from second 1 where the links are)."""
    d.rounded_rectangle((250, y, W - 250, y + 62), 31, fill="#ffeb3b")
    d.text((W // 2, y + 31), "Links COMMENT me hain", font=font(38), fill="#111111", anchor="mm")


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
    d.rounded_rectangle((50, 90, W - 50, 190), 50, fill=(255, 255, 255, 235))
    d.text((100, 140), "DESI CHOICE", font=font(46), fill="#f97316", anchor="lm")
    d.text((W - 100, 140), label if idx is None else f"{idx}/{total}", font=font(44), fill="#111111", anchor="rm")


# ── motion helpers (everything is rendered per frame; t=None means "final resting state") ──────────────────────
FPS = 30
LEAD = 0.15          # product/outro slides start animating just after the slide-left transition has begun


def clamp(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def prog(t, a, d):
    return 1.0 if t is None else clamp((t - a) / d)


def e_out(x):
    return 1 - (1 - x) ** 3


def e_back(x, s=1.70158):
    x -= 1
    return 1 + (s + 1) * x ** 3 + s * x ** 2          # small overshoot = the "pop"


def layer(size):
    return Image.new("RGBA", size, (0, 0, 0, 0))


def put(img, lay, cx, cy, scale=1.0, alpha=1.0):
    if alpha <= 0.003 or scale <= 0.003:
        return
    if abs(scale - 1) > 1e-3:
        lay = lay.resize((max(1, int(lay.width * scale)), max(1, int(lay.height * scale))), Image.BILINEAR)
    if alpha < 0.999:
        lay = lay.copy()
        lay.putalpha(lay.getchannel("A").point(lambda v: int(v * alpha)))
    img.paste(lay, (int(round(cx - lay.width / 2)), int(round(cy - lay.height / 2))), lay)


def text_layer(text, f, fill, pad=8):
    m = ImageDraw.Draw(layer((4, 4)))
    w = int(m.textlength(text, font=f)) + 2 * pad
    lay = layer((w, int(f.size * 1.5) + 2 * pad))
    ImageDraw.Draw(lay).text((lay.width // 2, lay.height // 2), text, font=f, fill=fill, anchor="mm")
    return lay


class ProductSlide:
    def __init__(self, it, photo, label, idx, total, dur):
        self.it, self.dur = it, dur
        self.bg = blurred_bg(photo)
        self.static = layer((W, H))
        sd = ImageDraw.Draw(self.static, "RGBA")
        header(sd, label, idx, total)
        cta_pill(sd)
        self.card = layer((W - 100, 860))
        ImageDraw.Draw(self.card).rounded_rectangle((0, 0, W - 101, 859), 56, fill=(255, 255, 255, 255))
        p = autocrop(photo)
        sc = min(880 / p.width, 740 / p.height, 2.5)
        self.photo = p.resize((max(1, int(p.width * sc)), max(1, int(p.height * sc))), Image.LANCZOS)
        pct = int(it.get("pct") or 0)
        self.badge = None
        if pct:
            self.badge = layer((340, 100))
            bd = ImageDraw.Draw(self.badge)
            bd.rounded_rectangle((0, 0, 339, 99), 28, fill=ACCENT)
            bd.text((170, 50), f"{pct}% OFF", font=font(58), fill="white", anchor="mm")
        f = font(58)
        lines = wrap(ImageDraw.Draw(layer((4, 4))), short_title(it.get("title")), f, W - 160, 2)
        self.title = layer((W - 100, 160))
        td = ImageDraw.Draw(self.title)
        td.rounded_rectangle((0, 0, W - 101, 159), 40, fill=(0, 0, 0, 200))
        for ln, y in zip(lines, [80] if len(lines) == 1 else [55, 115]):
            td.text(((W - 100) // 2, y), ln, font=f, fill="#ffeb3b", anchor="mm")
        self.price_base = layer((W - 100, 280))
        ImageDraw.Draw(self.price_base).rounded_rectangle((0, 0, W - 101, 279), 44, fill=(255, 255, 255, 240))
        try:
            self.price = float(it.get("price"))
        except Exception:
            self.price = None
        try:
            self.lp = float(it.get("list_price") or 0)
        except Exception:
            self.lp = 0.0
        try:
            self.rating, self.n = float(it.get("rating") or 0), int(float(it.get("reviews") or 0))
        except Exception:
            self.rating, self.n = 0.0, 0

    def price_strip(self, tt):
        lay = self.price_base.copy()
        d = ImageDraw.Draw(lay)
        big = font(124)
        final = inr(self.it.get("price"))
        shown = final
        if self.price is not None and self.lp > self.price:         # price DROP: counts down from the MRP to the deal price
            k = e_out(prog(tt, 0.35, 0.7))
            shown = inr(round(self.lp + (self.price - self.lp) * k))
        d.text((50, 100), shown, font=big, fill="#111111", anchor="lm")
        if self.lp:
            x0 = 50 + d.textlength(final, font=big) + 30
            t = inr(self.lp)
            d.text((x0, 112), t, font=font(56), fill="#888888", anchor="lm")
            sl = prog(tt, 1.0, 0.18)                                # the slash through the MRP
            if sl > 0:
                d.line((x0, 107, x0 + d.textlength(t, font=font(56)) * sl, 107), fill="#888888", width=5)
        if self.rating >= 1:
            fade = prog(tt, 0.8, 0.3)
            if fade > 0:
                ov = layer(lay.size)
                od = ImageDraw.Draw(ov)
                for k in range(5):
                    star(od, 80 + k * 66, 215, 28, "#f59e0b" if self.rating >= k + 0.75 else ("#fbcf72" if self.rating >= k + 0.25 else "#d4d4d4"))
                lab = f"{self.rating:.1f}" + (f"  ({fmt_n(self.n)} ratings)" if self.n else "")
                od.text((80 + 5 * 66 + 8, 216), lab, font=font(46), fill="#333333", anchor="lm")
                ov.putalpha(ov.getchannel("A").point(lambda v: int(v * fade)))
                lay.alpha_composite(ov)
        return lay

    def frame(self, t=None):
        tt = None if t is None else max(0.0, t - LEAD)
        img = self.bg.copy()
        img.paste(self.static, (0, 0), self.static)
        # card: pops in, then the photo drifts in slowly (Ken Burns)
        pc = prog(tt, 0, 0.3)
        zoom = 1.0 if tt is None else 1.0 + 0.07 * clamp(tt / self.dur)
        ph = self.photo if zoom == 1.0 else self.photo.resize((int(self.photo.width * zoom), int(self.photo.height * zoom)), Image.BICUBIC)
        card = self.card.copy()
        card.paste(ph, ((card.width - ph.width) // 2, (card.height - ph.height) // 2))
        put(img, card, W / 2, 720, 0.92 + 0.08 * e_back(pc), clamp(pc * 3))
        if self.badge is not None:
            pb = prog(tt, 0.25, 0.3)
            pulse = 1.0 if (tt is None or tt < 1.0) else 1 + 0.04 * math.sin((tt - 1.0) * 2 * math.pi * 1.6)
            put(img, self.badge, 250, 370, (0.3 + 0.7 * e_back(pb)) * pulse, clamp(pb * 3))
        pt = e_out(prog(tt, 0.2, 0.3))
        put(img, self.title, W / 2 - (1 - pt) * W, 1260)
        pp = e_out(prog(tt, 0.35, 0.3))
        put(img, self.price_strip(tt), W / 2, 1510 + (1 - pp) * 70, 1.0, pp)
        return img


class HookSlide:
    """Opening slide. Already fully readable at t=0 (it becomes the thumbnail); elements pop on top of that."""
    def __init__(self, items, photos, label):
        self.bg = blurred_bg(photos[0])
        self.static = layer((W, H))
        sd = ImageDraw.Draw(self.static, "RGBA")
        header(sd, label)
        cta_pill(sd)
        best = max([int(i.get("pct") or 0) for i in items] + [0])
        self.label = text_layer(label.upper(), font(60), "white")
        hook = hook_text(items)
        hf = font(150)
        while ImageDraw.Draw(layer((4, 4))).textlength(hook, font=hf) > 930 and hf.size > 80:      # 930 * 1.1 pop scale still fits 1080
            hf = font(hf.size - 8)
        self.hook = text_layer(hook, hf, "#ffeb3b", pad=20)
        self.pill = None
        if best:
            self.pill = layer((560, 130))
            pd = ImageDraw.Draw(self.pill)
            pd.rounded_rectangle((0, 0, 559, 129), 40, fill=ACCENT)
            pd.text((280, 65), f"upto {best}% OFF", font=font(72), fill="white", anchor="mm")
        self.thumbs = []
        n = len(photos)
        self.cw = min(290, (W - 100 - (n - 1) * 20) // max(1, n))         # 3 thumbs = 290px, 5 thumbs = 176px: always inside the screen
        cw = self.cw
        for p in photos:
            c = layer((cw, cw))
            ImageDraw.Draw(c).rounded_rectangle((0, 0, cw - 1, cw - 1), 36 if cw > 220 else 24, fill=(255, 255, 255, 255))
            q = autocrop(p)
            q.thumbnail((cw - 40, cw - 40))
            c.paste(q, ((cw - q.width) // 2, (cw - q.height) // 2))
            self.thumbs.append(c)
        self.last = text_layer("Aakhri deal sabse best!", font(60), "white") if len(photos) >= 2 else None

    def frame(self, t=None):
        img = self.bg.copy()
        img.paste(self.static, (0, 0), self.static)
        put(img, self.label, W / 2, 470)
        put(img, self.hook, W / 2, 640, 1 + 0.10 * (1 - e_out(prog(t, 0, 0.28))))
        if self.pill is not None:
            put(img, self.pill, W / 2, 835, 0.6 + 0.4 * e_back(prog(t, 0.12, 0.3)))
        n, cw = len(self.thumbs), self.cw
        gap = 30 if cw >= 290 else 20
        x = (W - (n * cw + (n - 1) * gap)) // 2
        for k, c in enumerate(self.thumbs):
            pk = prog(t, 0.3 + 0.12 * k, 0.3)
            put(img, c, x + cw / 2, 1145, 0.5 + 0.5 * e_back(pk), clamp(pk * 3))
            x += cw + gap
        if self.last is not None:
            put(img, self.last, W / 2, 1450, 1.0, prog(t, 0.9, 0.4))
        return img


class OutroSlide:
    def __init__(self, photo, label, n):
        self.bg = blurred_bg(photo)
        self.static = layer((W, H))
        header(ImageDraw.Draw(self.static, "RGBA"), label)
        pick = {2: "1 ya 2", 3: "1, 2 ya 3"}.get(n, f"1 se {n} tak number")
        self.l1 = text_layer("Saare links", font(110), "#ffeb3b")
        self.l2 = text_layer("COMMENT me hain", font(110), "#ffeb3b")
        self.arrow = layer((200, 140))
        ImageDraw.Draw(self.arrow).polygon([(10, 5), (190, 5), (100, 135)], fill="white")
        self.ask = text_layer(f"Kaunsi leni hai? {pick} likho", font(60), "white")
        self.follow = text_layer("Follow karo Desi Choice", font(58), "white")
        self.sub = text_layer("roz ki best Amazon deals", font(46), (255, 255, 255, 200))
        self.disc = text_layer("Affiliate links  •  As an Amazon Associate I earn from qualifying purchases", font(30), (255, 255, 255, 170))

    def frame(self, t=None):
        tt = None if t is None else max(0.0, t - LEAD)
        img = self.bg.copy()
        img.paste(self.static, (0, 0), self.static)
        put(img, self.l1, W / 2, 600, 0.85 + 0.15 * e_back(prog(tt, 0, 0.3)), clamp(prog(tt, 0, 0.2) * 2))
        put(img, self.l2, W / 2, 740, 0.85 + 0.15 * e_back(prog(tt, 0.1, 0.3)), clamp(prog(tt, 0.1, 0.2) * 2))
        bounce = 0 if tt is None else 16 * abs(math.sin(tt * math.pi * 1.8))     # arrow keeps pointing down at the comments
        put(img, self.arrow, W / 2, 945 + bounce, 1.0, prog(tt, 0.25, 0.2))
        put(img, self.ask, W / 2, 1150, 1.0, prog(tt, 0.45, 0.3))
        put(img, self.follow, W / 2, 1290, 1.0, prog(tt, 0.6, 0.3))
        put(img, self.sub, W / 2, 1375, 1.0, prog(tt, 0.7, 0.3))
        put(img, self.disc, W / 2, 1520, 1.0, prog(tt, 0.8, 0.3))
        return img


def render_clip(slide, dur, path):
    """Stream the slide's frames (dur + the transition tail) straight into ffmpeg as one h264 clip."""
    n = int(round((dur + T_X) * FPS))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "20", "-r", str(FPS), path]
    pr = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for k in range(n):
            pr.stdin.write(slide.frame(k / FPS).convert("RGB").tobytes())
    finally:
        pr.stdin.close()
    if pr.wait():
        raise SystemExit("ffmpeg clip failed")


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-1500:], file=sys.stderr)
        raise SystemExit("ffmpeg failed")


def sfx_events(durs):
    """(kind, seconds) for the little sound effects: whoosh on every slide-left, pop when the % badge lands, ding when the price lands."""
    ev, start = [], 0.0
    for i, d in enumerate(durs):
        if i > 0:
            ev.append(("whoosh", max(0.0, start - 0.05)))
        if 0 < i < len(durs) - 1:                                  # product slides
            ev.append(("pop", start + LEAD + 0.25))
            ev.append(("ding", start + LEAD + 1.0))
        start += d
    return ev


def audio_graph(durs, total, music_idx=None):
    """Music bed (or silence) + sfx, mixed, then loudness-normalised so the reel is not quiet next to other reels. -> (filter parts, label)."""
    st = "aformat=sample_rates=44100:channel_layouts=stereo"
    if music_idx is not None:
        base = f"[{music_idx}:a]volume={MUSIC_VOL},{st},atrim=0:{total:.2f}[bed]"
    else:
        base = f"anullsrc=r=44100:cl=stereo,atrim=0:{total:.2f}[bed]"
    parts, labels = [base], ["[bed]"]
    for k, (kind, at) in enumerate(sfx_events(durs)):
        ms = int(at * 1000)
        if kind == "whoosh":
            g = "anoisesrc=color=pink:duration=0.40:sample_rate=44100:amplitude=0.6,highpass=f=400,lowpass=f=5000,afade=t=in:d=0.18,afade=t=out:st=0.18:d=0.22,volume=0.45"
        elif kind == "pop":
            g = "sine=frequency=880:duration=0.12:sample_rate=44100,afade=t=out:st=0.03:d=0.09,volume=0.35"
        else:
            g = "sine=frequency=1318:duration=0.22:sample_rate=44100,afade=t=out:st=0.05:d=0.17,volume=0.28"
        parts.append(f"{g},{st},adelay={ms}:all=1[e{k}]")
        labels.append(f"[e{k}]")
    parts.append("".join(labels) + f"amix=inputs={len(labels)}:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=7,"
                 f"afade=t=out:st={max(0, total - 1.0):.2f}:d=1.0,atrim=0:{total:.2f}[a]")
    return parts, "[a]"


def assemble(slides, durs, out_path):
    n = len(slides)
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for s in slides:
        cmd += ["-i", s]
    parts, last, off = [], "[0:v]", 0.0
    for i in range(1, n):
        off += durs[i - 1]
        parts.append(f"{last}[{i}:v]xfade=transition=slideleft:duration={T_X}:offset={off:.2f}[x{i}]")
        last = f"[x{i}]"
    total = sum(durs) + T_X
    fg = parts[:] if parts else ["[0:v]null[x0]"]
    last = last if parts else "[x0]"
    have_music = os.path.exists(MUSIC)
    if have_music:
        cmd += ["-stream_loop", "-1", "-i", MUSIC]
    ap, alabel = audio_graph(durs, total, n if have_music else None)
    fg += ap
    cmd += ["-filter_complex", ";".join(fg), "-map", last, "-map", alabel, "-c:a", "aac", "-b:a", "128k"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "22", "-r", "30", "-movflags", "+faststart", "-t", f"{total:.2f}", out_path]
    run(cmd)
    print(f"assembled {n} slides, {total:.1f}s, music={have_music}, sfx={len(sfx_events(durs))}")


def main():
    global MUSIC
    pl = json.loads(os.environ["PAYLOAD"])
    label = str(pl.get("label") or "Daily Essentials")
    os.makedirs(OUT, exist_ok=True); os.makedirs(TMP, exist_ok=True)
    credit = None
    if not os.environ.get("REEL_MUSIC"):                       # explicit REEL_MUSIC override wins; otherwise a fresh Jamendo track per reel
        try:
            import jamendo_music
            jm = jamendo_music.pick(TMP, os.environ.get("JOB_ID"))
            if jm:
                MUSIC, credit = jm["path"], jm["credit"]
        except Exception as e:
            print("jamendo music skipped:", e, file=sys.stderr)
    good, photos = [], []
    for it in pl.get("items") or []:
        try:
            try:
                im = Image.open(io.BytesIO(fetch(hires(it["image"])))).convert("RGB")
                if im.width < 300:
                    raise ValueError("tiny")
            except Exception:
                im = Image.open(io.BytesIO(fetch(it["image"]))).convert("RGB")
            good.append(it); photos.append(im)
        except Exception as e:
            print("photo failed for", it.get("asin"), e, file=sys.stderr)
    if not good:
        raise SystemExit("no usable product photo")
    slides, durs = [], []
    jobs = [(HookSlide(good, photos, label), T_INTRO)]
    jobs += [(ProductSlide(it, ph, label, i, len(good), T_ITEM + T_X), T_ITEM) for i, (it, ph) in enumerate(zip(good, photos), 1)]
    jobs += [(OutroSlide(photos[-1], label, len(good)), T_OUTRO)]
    for k, (sl, dur) in enumerate(jobs):
        p = os.path.join(TMP, f"clip_{k}.mp4")
        render_clip(sl, dur, p)
        slides.append(p); durs.append(dur)
        if os.environ.get("REEL_KEEP_PNG"):
            sl.frame(None).save(os.path.join(TMP, f"clip_{k}.png"))
    assemble(slides, durs, os.path.join(OUT, "reel.mp4"))
    json.dump({"asins": [i.get("asin") for i in good], "kind": "cards", "music_credit": credit}, open(os.path.join(OUT, "meta.json"), "w"))


if __name__ == "__main__":
    main()
