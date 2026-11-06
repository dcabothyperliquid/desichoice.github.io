"""Deal Reel v2 (1080x1920). Env PAYLOAD = {"label", "items":[{asin,title,price,list_price,pct,image,benefits,script?}]}.

What changed vs v1 (reels looked like a slideshow of one photo each + robotic voice):
  * every product gets its REAL Amazon gallery (3-4 photos) + the product video clip when Amazon has one
    (tools/product_media.py, fetched through the same translate.goog proxy as the scanner)
  * photos sit on a blurred full-bleed background (no empty beige card), price strip stays on screen
  * hook = real price shock ("Rs 2,899 -> Rs 599?!") instead of "ITNA SASTA?!"
  * voice = Hindi neural voice reading natural Hindi (Devanagari) lines, rate/pitch tuned, ONE short sentence per shot
  * if the payload has item["script"] (written by Groq in Xbot) it is used, else a template script from title + bullets
Output: out/reel.mp4 + out/meta.json. Voice failure -> silent reel, media failure -> falls back to the feed photo.
"""
import asyncio, io, json, os, re, subprocess, sys, urllib.request
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import product_media
except Exception as _e:
    print("product_media unavailable:", _e, file=sys.stderr)
    product_media = None
W, H = 1080, 1920
OUT, TMP = os.environ.get("REEL_OUT", "out"), os.environ.get("REEL_TMP", "tmp")
VOICE = os.environ.get("REEL_VOICE", "hi-IN-SwaraNeural")        # female Hindi neural voice
RATE = os.environ.get("REEL_RATE", "+6%")                           # edge-tts me zyada rate = bhaari/male + toot-toot awaaz; speed ffmpeg atempo se (SPEEDUP)
PITCH = os.environ.get("REEL_PITCH", "+4Hz")                         # halka upar = saaf female sur
SPEEDUP = float(os.environ.get("REEL_SPEEDUP", "1.16"))           # pitch-preserving tez bolna (atempo)
FALLBACK_VOICES = ["en-US-AvaMultilingualNeural", "en-US-EmmaMultilingualNeural"]   # sirf female fallbacks
VOICE_USED = [None]
PRODUCT_MIN = float(os.environ.get("REEL_PRODUCT_MIN", "8"))       # har product kam se kam itne sec
MIN_TOTAL = float(os.environ.get("REEL_MIN_TOTAL", "36"))          # reel ki lambai 36-44 sec ke beech rakho
MAX_TOTAL = float(os.environ.get("REEL_MAX_TOTAL", "43"))
MAX_EXTRA_SPEED = float(os.environ.get("REEL_MAX_EXTRA_SPEED", "1.14"))   # lambi script ho to awaaz max itni aur tez (SPEEDUP ke upar)
FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"]
HIND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "Hind-Bold.ttf")   # Devanagari + Latin
ACCENT = "#e53935"
MAX_VIDEO_S = 4.5


def font(size):
    for p in FONTS:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


# DejaVu me emoji/symbol glyph nahi -> '⬛⬛⬛' boxes. AI script ke head/caption se saare emoji hata do (arrows ₹ rehne do).
_NOGLYPH = re.compile("[\U0001F000-\U0001FFFF\u2300-\u23FF\u2600-\u27BF\u2B00-\u2BFF\uFE00-\uFE0F\u200d\u20e3]")


def san(t):
    return re.sub(r"\s{2,}", " ", _NOGLYPH.sub("", str(t or ""))).strip()


def deal_head(it):
    """Hook ka top text seedha deal: ('₹15,998 → ₹2,629', '84% OFF'). list_price na ho to sirf % OFF."""
    price, lp, pct = float(it["price"]), float(it.get("list_price") or 0), int(it.get("pct") or 0)
    if lp > price:
        return f"{inr(lp)} → {inr(price)}", f"{pct or round((1 - price / lp) * 100)}% OFF"
    return f"{pct}% OFF" if pct else inr(price), "Top Deal"


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def short(title, n=60):
    t = (title or "").strip()
    t = re.split(r"\s[|\-–—]\s|,\s|\s\(", t)[0].strip() or (title or "").strip()
    if len(t) <= n:
        return t
    cut = t[:n].rsplit(" ", 1)[0] if " " in t[:n] else t[:n]      # shabd ke beech me nahi ('C…' jaisa nahi), pure shabd par ruko
    return cut.rstrip(" ,.-&|") + "…"


def spoken_name(title):
    return " ".join(short(title, 60).split()[:3]).strip(" .,&")


def inr(v):
    return f"₹{float(v):,.0f}"


def text_outline(d, xy, text, fnt, fill="white", stroke="#000000", sw=6, anchor="mm"):
    t = san(text)
    if re.search("[\u0900-\u097F]", t) and os.path.exists(HIND):      # safety net: Devanagari kabhi tofu box na bane
        fnt = ImageFont.truetype(HIND, getattr(fnt, "size", 60))
    d.text(xy, t, font=fnt, fill=fill, anchor=anchor, stroke_width=sw, stroke_fill=stroke)


def fit(d, text, size, maxw, lo=34):
    while size > lo and d.textlength(text, font=font(size)) > maxw:
        size -= 4
    return font(size)


def wrap(d, text, fnt, maxw, maxlines=2):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=fnt) <= maxw:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines[:maxlines]


# ───────────────────────── frames ─────────────────────────
def blurred_bg(im):
    bg = im.copy()
    s = max(W / bg.width, H / bg.height)
    bg = bg.resize((int(bg.width * s) + 1, int(bg.height * s) + 1))
    l, t = (bg.width - W) // 2, (bg.height - H) // 2
    bg = bg.crop((l, t, l + W, t + H)).filter(ImageFilter.GaussianBlur(38))
    return ImageEnhance.Brightness(bg).enhance(0.62)


def price_strip(d, it, pct_badge=True):
    """Bottom strip kept clear of the Reels UI (bottom ~230px)."""
    d.rounded_rectangle((50, 1390, W - 50, 1660), 44, fill=(255, 255, 255, 235))
    price, lp = it.get("price"), it.get("list_price")
    d.text((100, 1500), inr(price), font=font(128), fill="#111111", anchor="lm")
    if lp:
        x0 = 100 + d.textlength(inr(price), font=font(128)) + 34
        t = inr(lp)
        d.text((x0, 1512), t, font=font(58), fill="#888888", anchor="lm")
        w = d.textlength(t, font=font(58))
        d.line((x0, 1507, x0 + w, 1507), fill="#888888", width=5)
    pct = int(it.get("pct") or 0)
    if pct_badge and pct:
        d.rounded_rectangle((100, 1570, 420, 1640), 22, fill=ACCENT)
        d.text((260, 1605), f"{pct}% OFF", font=font(44), fill="white", anchor="mm")
    d.text((W - 100, 1605), "DESI CHOICE", font=font(36), fill="#f97316", anchor="rm")


def title_chip(d, text, y0=110, y1=240):
    """Product naam ka chip: pehle 1 line (size 56), na aaye to 2 lines (size 46). Dark box (alpha 215) taaki bright photo par bhi saaf padhe."""
    d.rounded_rectangle((50, y0, W - 50, y1), 40, fill=(0, 0, 0, 215))
    text = san(text)
    f, lines = font(56), [text]
    if d.textlength(text, font=f) > W - 160:
        f = font(46)
        lines = wrap(d, text, f, W - 160, 2)
    cy = (y0 + y1) // 2
    ys = [cy] if len(lines) == 1 else [cy - 26, cy + 26]
    for ln, y in zip(lines, ys):
        text_outline(d, (W // 2, y), ln, f, fill="#ffeb3b", sw=5)


STRIP_BOX = (50, 1390, W - 50, 1660)      # price_strip() lives exactly here


def strip_png(it, path):
    """Price strip alone, cropped to its box (transparent PNG) so the renderer can 'pop' it (scale slam) without moving it."""
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    price_strip(ImageDraw.Draw(ov, "RGBA"), it)
    ov.crop(STRIP_BOX).save(path)
    return path


def photo_layers(photo, it, caption=None, counter=None, top=250, height=760, strip=True):
    """Two layers so the PHOTO can zoom hard while text/price stay fixed (old single baked PNG could only zoom 8% before the
    text got cut at the edges -> looked like a slideshow).
    -> (base RGB: blurred bg + white card + photo, overlay RGBA: counter + caption + price strip)."""
    base = blurred_bg(photo).convert("RGBA")
    d = ImageDraw.Draw(base, "RGBA")
    d.rounded_rectangle((50, top, W - 50, top + height), 48, fill=(255, 255, 255, 255))
    ph = photo.copy()
    ph.thumbnail((940, height - 40))
    base.paste(ph, ((W - ph.width) // 2, top + (height - ph.height) // 2))
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov, "RGBA")
    if counter:
        od.rounded_rectangle((W - 230, 190, W - 60, 270), 30, fill=(0, 0, 0, 170))
        od.text((W - 145, 230), counter, font=font(44), fill="white", anchor="mm")
    if caption:
        f = fit(od, caption, 64, W - 160, 40)
        for k, ln in enumerate(wrap(od, caption, f, W - 160)):
            text_outline(od, (W // 2, 1290 + k * 70), ln, f)
    if strip:                                  # strip=False -> caller adds the animated strip_png() instead
        price_strip(od, it)
    return base.convert("RGB"), ov


def photo_frame(photo, it, caption=None, counter=None, top=250, height=760):
    """Blurred full-bleed bg + white product card + caption + price strip, flattened. Returns RGB image."""
    base, ov = photo_layers(photo, it, caption, counter, top, height)
    im = base.convert("RGBA")
    im.alpha_composite(ov)
    return im.convert("RGB")


def save_layers(base, ov, stem):
    """Write base/overlay PNGs for a layered still shot -> (base_path, overlay_path)."""
    bp, op = f"{stem}.png", f"{stem}_ov.png"
    base.save(bp)
    ov.save(op)
    return bp, op


def hook_overlay(head, sub, it):
    """Transparent overlay for the hook shot (drawn over blurred video / photo)."""
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov, "RGBA")
    d.rounded_rectangle((50, 90, W - 50, 380), 46, fill=(0, 0, 0, 215))
    text_outline(d, (W // 2, 200), head, fit(d, head, 120, W - 160, 60), fill="#ffeb3b", sw=7)
    text_outline(d, (W // 2, 320), sub, fit(d, sub, 58, W - 160, 34), sw=5)
    price_strip(d, it)
    return ov


def outro_frame(photo, it):
    im = blurred_bg(photo).convert("RGBA")
    d = ImageDraw.Draw(im, "RGBA")
    d.rounded_rectangle((70, 330, W - 70, 960), 54, fill=(255, 255, 255, 240))      # card caption zone (y~1040+) se upar: text overlap nahi
    d.text((W // 2, 470), "LINK", font=font(120), fill="#f97316", anchor="mm")
    d.text((W // 2, 620), "COMMENTS me hai", font=font(74), fill="#111111", anchor="mm")
    d.text((W // 2, 780), "Follow @DesiChoice", font=font(58), fill="#555555", anchor="mm")
    d.text((W // 2, 870), "roz aise sasti deals", font=font(52), fill="#555555", anchor="mm")
    return im.convert("RGB")


# ───────────────────────── script (hook + lines) ─────────────────────────
def clean_bullet(b):
    b = re.sub(r"\s+", " ", b or "").strip(" .:-|")
    head = re.split(r"\s[|•]\s", b)[0]
    if ":" in head and len(head.split(":")[0]) >= 6:
        head = head.split(":")[0]
    if len(head) > 34:
        head = head[:34].rsplit(" ", 1)[0]
    return head.rstrip(" ,.-")


_H = ("शून्य एक दो तीन चार पाँच छह सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह उन्नीस बीस इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस "
      "सत्ताईस अट्ठाईस उनतीस तीस इकतीस बत्तीस तैंतीस चौंतीस पैंतीस छत्तीस सैंतीस अड़तीस उनतालीस चालीस इकतालीस बयालीस तैंतालीस चौवालीस पैंतालीस "
      "छियालीस सैंतालीस अड़तालीस उनचास पचास इक्यावन बावन तिरपन चौवन पचपन छप्पन सत्तावन अट्ठावन उनसठ साठ इकसठ बासठ तिरसठ चौंसठ पैंसठ छियासठ "
      "सड़सठ अड़सठ उनहत्तर सत्तर इकहत्तर बहत्तर तिहत्तर चौहत्तर पचहत्तर छिहत्तर सतहत्तर अठहत्तर उनासी अस्सी इक्यासी बयासी तिरासी चौरासी पचासी "
      "छियासी सत्तासी अट्ठासी नवासी नब्बे इक्यानवे बानवे तिरानवे चौरानवे पंचानवे छियानवे सत्तानवे अट्ठानवे निन्यानवे").split()


def hindi_num(n):
    n = int(round(float(n)))
    if n < 100:
        return _H[n]
    out = []
    if n >= 100000:
        out.append(hindi_num(n // 100000) + " लाख"); n %= 100000
    if n >= 1000:
        out.append(_H[n // 1000] + " हज़ार"); n %= 1000
    if n >= 100:
        out.append(_H[n // 100] + " सौ"); n %= 100
    if n:
        out.append(_H[n])
    return " ".join(out)


def rs_voice(v):
    return hindi_num(v) + " रुपये"


def rs_short(v):
    """Bolne ke liye gol amount: 15998 -> 'सोलह हज़ार रुपये', 2629 -> 'दो हज़ार छह सौ रुपये' (screen par exact price rehta hai)."""
    v = float(v)
    return rs_voice(int(round(v / 100.0) * 100) if v >= 1000 else int(round(v)))


def price_first(it, tail=""):
    """Pehli line me hi price: 'सोलह हज़ार वाला सूटकेस, सिर्फ़ दो हज़ार छह सौ रुपये में मिल रहा है!'"""
    price, lp = float(it["price"]), float(it.get("list_price") or 0)
    name = " ".join(spoken_name(it.get("title")).split()[:2])
    if lp > price:
        return f"{rs_short(lp).replace(' रुपये', '')} वाला {name}, सिर्फ़ {rs_short(price)} में मिल रहा है!{tail}"
    return f"{name}, सिर्फ़ {rs_short(price)} में मिल रहा है!{tail}"


def default_script(it, n_items):
    """Fallback when Groq script is not in the payload. Spoken text in Devanagari so the Hindi voice sounds natural."""
    price, lp, pct = float(it["price"]), float(it.get("list_price") or 0), int(it.get("pct") or 0)
    save = int(lp - price) if lp > price else 0
    name = spoken_name(it.get("title"))
    head = f"{inr(lp)} → {inr(price)}?!" if lp else f"{pct}% OFF!"
    sub = f"{pct}% OFF · {short(it.get('title'), 28)}"
    hook = f"रुको! {rs_voice(lp)} वाला {name} अभी सिर्फ़ {rs_voice(price)} में? पहले ये देख लो।" if lp else \
           f"रुको! {name} पर {hindi_num(pct)} परसेंट की छूट, सिर्फ़ {rs_voice(price)} में।"
    feats = [{"text": clean_bullet(b), "voice": clean_bullet(b) + "।"} for b in (it.get("benefits") or [])[:3] if clean_bullet(b)]
    return {"head": head, "sub": sub, "hook": hook, "lines": feats,
            "price_voice": f"और कीमत? सिर्फ़ {rs_voice(price)}। पूरे {rs_voice(save)} की बचत।" if save else f"कीमत सिर्फ़ {rs_voice(price)}।",
            "outro": "लिंक कमेंट्स में है। रोज़ ऐसी डील के लिए फ़ॉलो करो।"}


# ───────────────────────── tts / ffmpeg ─────────────────────────
def _style(text, lively=False):
    """Har line ki rate/pitch thodi alag (text-hash se, taaki same text par same awaaz) -> ek hi flat sur wali robotic feel nahi.
    REEL_RATE / REEL_PITCH env set ho to wahi chalega. lively=True (hook): thodi energetic."""
    if os.environ.get("REEL_RATE") or os.environ.get("REEL_PITCH"):
        return RATE, PITCH
    h = sum(ord(c) for c in text)
    rate = 8 if lively else (4, 6, 8, 5, 7)[h % 5]
    pitch = 7 if lively else (3, 4, 5, 4, 3)[(h // 5) % 5]
    return f"{rate:+d}%", f"{pitch:+d}Hz"


async def _tts(text, path, lively=False):
    import edge_tts
    last = None
    rate, pitch = _style(text, lively)
    for v in [VOICE] + [x for x in FALLBACK_VOICES if x != VOICE]:
        try:
            await edge_tts.Communicate(text, v, rate=rate if v == VOICE else RATE, pitch=pitch if v == VOICE else PITCH).save(path)
            if os.path.exists(path) and os.path.getsize(path) > 500:
                VOICE_USED[0] = v
                return
        except Exception as e:
            last = e
            print("TTS voice", v, "failed:", str(e)[:80], file=sys.stderr)
    raise last or RuntimeError("tts failed")


def flow(text):
    """TTS ko bhejne se pehle: vakya ke beech ke comma/hyphen/colon hatao (neural voice har comma par ruk jaati hai -> tuk-tuk bolna).
    Sirf vakya ke ant ka viram (। ? !) rehta hai. Content/shabd same rehte hain; screen text par asar nahi."""
    t = str(text or "")
    def _soft(sent):                           # har vakya me max 1 comma (saans), wo bhi tab jab dono taraf >=4 shabd hon; baaki jud jaate hain
        parts = [x.strip() for x in re.split(r"[,،、]+", sent) if x.strip()]
        buf, used = (parts[0] if parts else ""), False
        for x in parts[1:]:
            if not used and len(buf.split()) >= 4 and len(x.split()) >= 4:
                buf, used = buf + ", " + x, True
            else:
                buf += " " + x
        return buf
    t = "".join(_soft(x) if not re.fullmatch(r"[।?!.\s]*", x) else x for x in re.split(r"([।?!.]+\s*)", t))
    t = re.sub(r"[;:\u2013\u2014\u2012]+", " ", t)
    t = re.sub(r"(?<=\S)-(?=\S)", " ", t)
    t = re.sub(r"\s*\.\.\.+\s*", " ", t)
    t = re.sub(r"[!?](?=\s*\S)", "।", t)           # vakya ke beech ka ! ya ? -> chhota viram; aakhri wala intonation ke liye rehta hai
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t


def tts_one(text, path, lively=False):
    """One sentence -> mp3 with leading/trailing silence removed (edge-tts pads ~0.3s each side = the 'ruk ruk ke' feel)."""
    raw = path + ".raw.mp3"
    try:
        asyncio.run(_tts(flow(text), raw, lively))
        if os.path.getsize(raw) < 500:
            return None
        # sirf shuru/ant ki khamoshi hatao; beech ke natural pauses (comma/purn viram) rehne do, bahut lambe (>0.5s) hi chhote karo
        trim = ("silenceremove=start_periods=1:start_threshold=-35dB:start_silence=0.03,areverse,"
                "silenceremove=start_periods=1:start_threshold=-35dB:start_silence=0.12,areverse,"
                "silenceremove=stop_periods=-1:stop_duration=0.5:stop_threshold=-38dB:stop_silence=0.3,"
                f"atempo={SPEEDUP},highpass=f=80,acompressor=threshold=0.12:ratio=2.2:attack=8:release=160,loudnorm=I=-16:TP=-1.5:LRA=9")
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw, "-af", trim, "-ar", "44100", "-ac", "1", path],
                           capture_output=True, text=True, timeout=60)
        return path if r.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 500 else raw
    except Exception as e:
        print("TTS failed:", str(e)[:120], file=sys.stderr)
        return None


def dur(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True, timeout=30)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def run(cmd, timeout=240):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        print("ffmpeg error:", r.stderr[-600:], file=sys.stderr)
        raise RuntimeError("ffmpeg failed")


ZOOM_AMT = float(os.environ.get("REEL_ZOOM", "0.20"))        # visible punch-in (20%); old Ken-Burns was 8% and looked like a slideshow
PUNCH_AMT = float(os.environ.get("REEL_PUNCH", "0.05"))      # extra hard punch-in beat in the middle of longer shots
PUNCH_MIN_S = 3.2                                             # shots shorter than this get no mid-shot punch
CARD_Y = "(ih-ih/zoom)*0.526"      # zoom pivot = card BOTTOM edge (y=1010/1920): the card grows upward only, so it never runs into
                                   # the word captions (they sit at y~1020-1185) or the benefit line (y~1290)


HOOK_SNAP = float(os.environ.get("REEL_HOOK_SNAP", "0.20"))   # hook shot: sharp snap-zoom in the first ~0.5s (frame-1 visual interrupt)
POP_AMT, POP_S = float(os.environ.get("REEL_POP", "0.10")), 0.45   # price strip slams in from +10% to 100% over 0.45s


def pop_chain(src, strip_idx, out):
    """ffmpeg filter text: overlay the price strip (input strip_idx) on [src] with a scale 'pop' at the start of the shot,
    pivoting on the strip's own centre so it lands exactly where the static strip would be."""
    s = "(1+%.3f*pow(max(0,1-t/%.2f),2))" % (POP_AMT, POP_S)
    cx, cy = (STRIP_BOX[0] + STRIP_BOX[2]) // 2, (STRIP_BOX[1] + STRIP_BOX[3]) // 2
    return (f"[{strip_idx}:v]scale=w='trunc(iw*{s}/2)*2':h='trunc(ih*{s}/2)*2':eval=frame[sp];"
            f"[{src}][sp]overlay=x='{cx}-w/2':y='{cy}-h/2'[{out}]")


def zoom_exprs(secs, k, layered, hook=False):
    """(z, x, y) zoompan expressions for shot k.
    layered=True  -> only the photo layer moves (text/price are a static overlay), so we can zoom hard: ease-out cubic
                     (fast punch at the start, settles) + alternating in / out / pan-left / pan-right + a mid-shot punch-in beat.
    layered=False -> text is baked into the frame (outro), keep the old gentle <=8% Ken-Burns so nothing gets cut.
    hook=True     -> first shot: snap-zoom ~HOOK_SNAP within 0.5s, then a slow creep (the 'sharp zoom on frame 1' pattern interrupt)."""
    N = max(2, int(secs * 25))
    if layered and hook:
        snap = "(1-pow(1-min(1,on/12),3))"
        return ("1.0+%.3f*%s+0.05*min(1,on/%d)" % (HOOK_SNAP, snap, N), "(iw-iw/zoom)/2", CARD_Y)
    if not layered:
        c = "(iw-iw/zoom)/2"
        modes = [("1.0+0.08*on/%d" % N, c, "(ih-ih/zoom)/2"),
                 ("1.08-0.08*on/%d" % N, c, "(ih-ih/zoom)/2"),
                 ("1.05+0.03*on/%d" % N, "(iw-iw/zoom)*(0.38+0.24*on/%d)" % N, "(ih-ih/zoom)*0.45"),
                 ("1.08-0.03*on/%d" % N, "(iw-iw/zoom)*(0.62-0.24*on/%d)" % N, "(ih-ih/zoom)*0.55")]
        return modes[k % 4]
    e = "(1-pow(1-min(1,on/%d),3))" % N                     # ease-out cubic 0..1
    p = "min(1,on/%d)" % N                                  # linear 0..1 (for pans)
    punch = ("+%.3f*gte(on,%d)" % (PUNCH_AMT, int(N * 0.55))) if secs >= PUNCH_MIN_S else ""
    a = ZOOM_AMT
    y = CARD_Y
    modes = [("1.0+%.3f*%s%s" % (a, e, punch), "(iw-iw/zoom)/2", y),                                 # punch in
             ("%.3f-%.3f*%s" % (1.0 + a + 0.02, a, e), "(iw-iw/zoom)/2", y),                           # pull back (reveal)
             ("1.12+%.3f*%s%s" % (a * 0.5, e, punch), "(iw-iw/zoom)*(0.30+0.40*%s)" % p, y),           # zoom + pan left -> right
             ("1.12+%.3f*%s%s" % (a * 0.5, e, punch), "(iw-iw/zoom)*(0.70-0.40*%s)" % p, y)]           # zoom + pan right -> left
    return modes[k % 4]


def still_clip(png, secs, audio, out, k, overlay=None, strip=None, hook=False):
    """One still shot. overlay=None: png is the full frame (text baked in) -> gentle zoom. overlay=<RGBA png>: png is just the
    photo layer, it zooms hard and the overlay (text/price) is laid on top, static.
    strip=<RGBA png from strip_png()>: price strip is added on top with a 'pop' instead of being part of the static overlay."""
    z, x, y = zoom_exprs(secs, k, bool(overlay), hook)
    zp = f"scale=2160:3840,zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps=25"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "25", "-t", f"{secs:.2f}", "-i", png]
    if overlay:
        cmd += ["-loop", "1", "-framerate", "25", "-t", f"{secs:.2f}", "-i", overlay]
    if overlay and strip:
        cmd += ["-loop", "1", "-framerate", "25", "-t", f"{secs:.2f}", "-i", strip]
    cmd += ["-i", audio] if audio else ["-f", "lavfi", "-t", f"{secs:.2f}", "-i", "anullsrc=r=44100:cl=mono"]
    if overlay and strip:
        cmd += ["-filter_complex", f"[0:v]{zp}[z];[z][1:v]overlay=0:0[m];{pop_chain('m', 2, 'm2')};[m2]format=yuv420p[v]",
                "-map", "[v]", "-map", "3:a"]
    elif overlay:
        cmd += ["-filter_complex", f"[0:v]{zp}[z];[z][1:v]overlay=0:0,format=yuv420p[v]", "-map", "[v]", "-map", "2:a"]
    else:
        cmd += ["-vf", f"{zp},format=yuv420p"]
    cmd += ["-r", "25", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-c:a", "aac", "-b:a", "128k",
            "-ar", "44100", "-ac", "1", "-af", "apad", "-t", f"{secs:.2f}", out]
    run(cmd)


def video_clip(mp4, overlay_png, secs, audio, out, start=0.0, strip=None, hook=False):
    """Product video: blurred cover bg + video fit-to-width in the middle + overlay (hook text, price). Video muted, voice on top.
    strip=<png from strip_png()>: price strip pops in at the start instead of being baked into overlay_png."""
    # landscape clips (16:9 = most Amazon videos) used to be fitted to width -> a thin ~550px band, product looked tiny.
    # Fill the whole 980x760 box instead (centre crop, ~25% of the width trimmed). Square/portrait clips keep the old fit.
    top, bh = (385, 630) if hook else (260, 760)      # hook shot: hook text box sits at y 90-380, so the clip box starts below it
    fill = False
    try:
        pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", mp4],
                            capture_output=True, text=True, timeout=30).stdout.strip().split(",")
        fill = int(pr[0]) / max(1, int(pr[1])) >= 1.2
    except Exception:
        pass
    fg = (f"[b]scale={W - 100}:{bh}:force_original_aspect_ratio=increase,crop={W - 100}:{bh}[fg];" if fill else
          f"[b]scale={W - 100}:{bh}:force_original_aspect_ratio=decrease:force_divisible_by=2[fg];")
    fc = (f"[0:v]fps=25,loop=loop=-1:size=250:start=0,setpts=N/(25*TB),trim=start={start:.2f}:end={start + secs + 0.5:.2f},setpts=PTS-STARTPTS,split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30:6,eq=brightness=-0.18[bg];"
          f"{fg}[bg][fg]overlay=(W-w)/2:{top}+({bh}-h)/2[m];")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", mp4, "-loop", "1", "-i", overlay_png]
    if strip:
        cmd += ["-loop", "1", "-i", strip]
        fc += f"[m][1:v]overlay=0:0[m1];{pop_chain('m1', 2, 'm2')};[m2]format=yuv420p[v]"
    else:
        fc += "[m][1:v]overlay=0:0,format=yuv420p[v]"
    a_idx = 3 if strip else 2
    cmd += ["-i", audio] if audio else ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono"]
    cmd += ["-filter_complex", fc, "-map", "[v]", "-map", f"{a_idx}:a", "-r", "25", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "22", "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "1", "-af", "apad", "-t", f"{secs:.2f}", out]
    run(cmd)


# ───────────────────────── final assembly: transitions + word captions + music ─────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR = os.path.join(HERE, "fonts")                     # Hind-Bold.ttf (Devanagari + Latin) for captions
MUSIC = os.environ.get("REEL_MUSIC", os.path.join(HERE, "music", "bed.m4a"))
MUSIC_VOL = float(os.environ.get("REEL_MUSIC_VOL", "0.42"))   # relative level; ducks further under the voice
CAPTION_Y = int(os.environ.get("REEL_CAPTION_Y", "1185"))     # baseline (px from top) of the word captions
XF_D, XF_CUT = 0.25, 0.08          # XF_CUT=0.04 (sirf 1 frame) par ffmpeg 6.1 xfade baaki video kaat deta tha (reel beech me ruk jaati) -> 2 frame


def _trans(shots, i):
    """(xfade name, seconds) for the join into shot i."""
    cur, prev = shots[i], shots[i - 1]
    if cur.get("vcont"):
        return "fade", XF_CUT                  # hook -> intro continue the same clip: practically a cut
    if cur.get("pk") is None:
        return "fade", XF_D                    # outro
    if cur.get("pk") != prev.get("pk"):
        return "slideup", XF_D                 # next product
    return ("fade", "slideleft")[i % 2], XF_D


def _ass_time(t):
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def make_ass(shots, starts, path):
    """Static white captions: whole phrase (<=6 words) at once, no highlight. Word timing = spread over the audio by word length."""
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,"
            "StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            "Style: Cap,Hind,70,&H00FFFFFF,&H00FFFFFF,&H00202020,&H78000000,-1,0,0,0,100,100,0,0,1,4,2,2,70,70,%d,1\n\n"
            "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n") % (W, H, H - CAPTION_Y)
    ev = []
    for k, s_ in enumerate(shots):
        a, txt = s_.get("audio"), str(s_.get("voice") or "")
        if not a or not txt.strip():
            continue
        words = [w.strip(" ।,.!?;:\"'()") for w in txt.split()]
        words = [w for w in words if w]
        if not words:
            continue
        t0, span = starts[k] + 0.08, max(0.6, dur(a) - 0.12)
        wts = [max(2, len(w)) for w in words]
        tot, acc, times = float(sum(wts)), 0.0, []
        for w_ in wts:
            times.append((t0 + span * acc / tot, t0 + span * (acc + w_) / tot)); acc += w_
        # static white captions: <=6 words per chunk shown together (2 lines of <=3 words), no per-word highlight
        n_ch = -(-len(words) // 6)                              # even split: no tiny tail chunk, every line <=3 words
        size = -(-len(words) // n_ch)
        chunks = [list(range(i, min(i + size, len(words)))) for i in range(0, len(words), size)]
        for ci, ch in enumerate(chunks):
            st = times[ch[0]][0]
            en = times[chunks[ci + 1][0]][0] if ci + 1 < len(chunks) else times[ch[-1]][1] + 0.12
            half = (len(ch) + 1) // 2 if len(ch) > 3 else len(ch)
            txt_ = " ".join(words[q] for q in ch[:half]) + ("\\N" + " ".join(words[q] for q in ch[half:]) if half < len(ch) else "")
            ev.append(f"Dialogue: 0,{_ass_time(st)},{_ass_time(en)},Cap,,0,0,0,,{txt_}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(head + "\n".join(ev) + "\n")
    return len(ev)


def assemble(clips, shots, out_path):
    n = len(clips)
    durs = [dur(c) for c in clips]
    v_prev, a_prev, L, starts = "[0:v]", "[0:a]", durs[0], [0.0]
    vf, af = [], []
    for i in range(1, n):
        tr, dd = _trans(shots, i)
        off = L - dd
        vf.append(f"{v_prev}[{i}:v]xfade=transition={tr}:duration={dd}:offset={off:.3f}[v{i}]")
        af.append(f"{a_prev}[{i}:a]acrossfade=d={dd}[a{i}]")
        v_prev, a_prev = f"[v{i}]", f"[a{i}]"
        starts.append(off)
        L += durs[i] - dd
    ass = os.path.abspath(f"{TMP}/caps.ass")
    nev = make_ass(shots, starts, ass)
    vf.append(f"{v_prev}subtitles={ass}:fontsdir={FONTS_DIR},format=yuv420p[vout]")
    have_music = os.path.exists(MUSIC)
    if have_music:
        af.append(f"{a_prev}asplit=2[vo][sc]")
        af.append(f"[{n}:a]volume={MUSIC_VOL},aformat=sample_rates=44100:channel_layouts=mono[mu]")
        af.append("[mu][sc]sidechaincompress=threshold=0.03:ratio=9:attack=15:release=400[du]")
        af.append(f"[vo][du]amix=inputs=2:duration=first:normalize=0,afade=t=out:st={max(0, L - 1.3):.2f}:d=1.2,alimiter=limit=0.95[aout]")
    else:
        af.append(f"{a_prev}anull[aout]")
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for c in clips:
        cmd += ["-i", c]
    if have_music:
        cmd += ["-stream_loop", "-1", "-i", MUSIC]
    cmd += ["-filter_complex", ";".join(vf + af), "-map", "[vout]", "-map", "[aout]", "-r", "25", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "22", "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "1", "-movflags", "+faststart", "-t", f"{L:.2f}", out_path]
    run(cmd, timeout=420)
    print(f"assembled: {n} clips, xfade transitions, {nev} caption events, music={have_music}, {L:.1f}s")


# ───────────────────────── main ─────────────────────────
BAD_WORDS = ("support", "helpline", "toll free", "whatsapp", "contact us", "scan &", "scan and", "qr", "app store", "google play", "customer care")


def is_junk(im):
    """OCR the gallery photo; support / QR / app-download pages make bad reel shots. No tesseract -> keep everything."""
    try:
        import pytesseract
        small = im.copy(); small.thumbnail((900, 900))
        txt = pytesseract.image_to_string(small).lower()
        return any(w in txt for w in BAD_WORDS)
    except Exception:
        return False


_XBOT_DEADLINE = [None]     # total time budget for ALL Xbot media downloads (server atak jaye to poora job 15 min na khaye)
XBOT_BUDGET = 240


def _xget(url, tries=3):
    """Xbot (Render free) so raha ho to pehli request jagati hai: retry -- par total XBOT_BUDGET sec se zyada kabhi nahi."""
    import time as _t
    if _XBOT_DEADLINE[0] is None:
        _XBOT_DEADLINE[0] = _t.time() + XBOT_BUDGET
    for i in range(tries):
        left = _XBOT_DEADLINE[0] - _t.time()
        if left <= 3:
            print("xbot media budget khatam, skip:", url[-40:], file=sys.stderr)
            return None
        try:
            return fetch(url, timeout=max(5, min(45, left)))
        except Exception as e:
            print("xbot media retry", i + 1, str(e)[:60], file=sys.stderr)
            _t.sleep(min(6, max(0, _XBOT_DEADLINE[0] - _t.time())))
    return None


def item_media(it):
    """Xbot ne Turso me pehle se saved photos + <=7s clip payload me bheje -> Amazon scrape nahi. Na mile to purana tareeka."""
    m = it.get("media") or {}
    if m.get("photos"):
        os.makedirs(TMP, exist_ok=True)
        imgs, vids = [], []
        for j, u in enumerate(m["photos"]):
            b = _xget(u)
            if not b and j == 0:
                break               # pehli hi photo nahi mili -> Xbot down/atka hai, baaki par waqt kharab mat karo
            if b:
                p = os.path.abspath(f"{TMP}/xm_{it.get('asin')}_{j}.jpg")
                open(p, "wb").write(b)
                imgs.append("file://" + p)
        if m.get("clip"):
            b = _xget(m["clip"])
            if b:
                p = os.path.abspath(f"{TMP}/xm_{it.get('asin')}.mp4")
                open(p, "wb").write(b)
                vids.append(p)
        if imgs:
            print("xbot media ok:", it.get("asin"), len(imgs), "photos", len(vids), "clip")
            return {"images": imgs, "videos": vids, "bullets": m.get("bullets") or []}
        print("xbot media failed, Amazon fallback:", it.get("asin"), file=sys.stderr)
    if product_media and it.get("asin"):
        return product_media.media(it["asin"])
    return {"images": [], "videos": [], "bullets": []}


def inr_screen(v):
    return inr(v)


def fill(text, items):
    """Groq script ke placeholders -> asli numbers. Capital = bola hua (Hindi words), small = screen ({P1} {p1} {L1} {S1} {D1} {PMIN} {DMAX})."""
    if not text or "{" not in text:
        return text

    def val(m):
        key, idx = m.group(1), m.group(2)
        try:
            if key.upper() == "PMIN":
                it, ch = min(items, key=lambda i: float(i["price"])), key[0]
            elif key.upper() == "DMAX":
                it, ch = max(items, key=lambda i: int(i.get("pct") or 0)), key[0]
            else:
                it, ch = items[int(idx) - 1], key
            price, lp, pct = float(it["price"]), float(it.get("list_price") or 0), int(it.get("pct") or 0)
            save = int(lp - price) if lp > price else 0
            return {"P": rs_voice(price), "p": inr(price), "L": rs_voice(lp or price), "l": inr(lp or price),
                    "S": rs_voice(save or price), "s": inr(save or price), "D": hindi_num(pct) + " परसेंट", "d": f"{pct}%"}[ch]
        except Exception:
            return ""
    out = re.sub(r"\{(PMIN|pmin|DMAX|dmax|[PLSDplsd])(\d?)\}", val, text)
    return re.sub(r"\{[^}]*\}", "", out).strip()


_DEVA = re.compile("[\u0900-\u097F]")


def fill_screen(text, items):
    """Hook ka SCREEN text (head/sub): capital placeholder ({DMAX}/{PMIN}/{P1}) bhi screen form ({dmax}/{pmin}/{p1}) me bharo.
    Capital = Hindi shabd (sirf awaaz ke liye); DejaVu font me Devanagari glyph nahi -> boxes. Bacha Devanagari bhi hata do."""
    if not text:
        return text
    t = re.sub(r"\{(PMIN|DMAX|[PLSD])(\d?)\}", lambda m: "{" + m.group(1).lower() + m.group(2) + "}", text)
    return re.sub(r"\s{2,}", " ", _DEVA.sub("", fill(t, items))).strip()


def load_photos(it, media):
    urls = list(dict.fromkeys(([it.get("image")] if it.get("image") else []) + [u for u in media.get("images", [])]))
    # gallery first (hi-res, different angles); feed photo is a low-res search thumbnail
    urls = [u for u in media.get("images", [])] + ([it["image"]] if it.get("image") else [])
    out = []
    for u in dict.fromkeys(urls):
        try:
            im = Image.open(io.BytesIO(fetch(u))).convert("RGB")
            if min(im.size) >= 300 and not (out and is_junk(im)):
                out.append(im)
        except Exception as e:
            print("photo failed:", u[-30:], str(e)[:60], file=sys.stderr)
        if len(out) >= 5:
            break
    return out


def load_video(media, path=None, secs=7):
    """HLS (.m3u8) from Amazon -> first `secs` seconds as local mp4 (ffmpeg pulls the playlist itself)."""
    path = path or f"{TMP}/pv.mp4"
    for u in media.get("videos", []):
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error"] + (["-user_agent", "Mozilla/5.0"] if u.startswith("http") else []) + ["-i", u, "-t", str(secs), "-an",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", path],
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and os.path.exists(path) and dur(path) >= 1.5:
            print("video ok:", u[-60:], round(dur(path), 1), "s")
            return path
        print("video failed:", u[-50:], r.stderr[-160:], file=sys.stderr)
    return None


ORD = ["पहला", "दूसरा", "तीसरा"]
CONNECT = ["अच्छा, अब ये देखो।", "ये तो कुछ नहीं, अब ये देखो।", "चलो अब ये वाला।"]   # fallback jod (AI intro na aaye tab)
CONNECT_LAST = "और आख़िर में, ये मत छोड़ना।"


def item_script(it, k, n):
    """Per-item narration for multi-product reels (fallback when Groq script missing)."""
    price, lp, pct = float(it["price"]), float(it.get("list_price") or 0), int(it.get("pct") or 0)
    name = spoken_name(it.get("title"))
    sc = dict(it.get("script") or {})
    sc.setdefault("intro", f"ये देखो {name}, सिर्फ़ {rs_voice(price)} में, और पूरे {hindi_num(pct)} परसेंट डिस्काउंट के साथ।")
    sc.setdefault("lines", [{"text": clean_bullet(b), "voice": clean_bullet(b)} for b in (it.get("benefits") or [])[:2] if clean_bullet(b)])
    sc.setdefault("tag", f"{k + 1}. {short(it.get('title'), 44)}")
    return sc


def item_benefits(it, sc):
    """Product ki benefit lines -> [(screen_text, voice), ...] max 3. Pehle Groq script lines, phir payload benefits / Amazon bullets."""
    out = []
    for ln in sc.get("lines") or []:
        if isinstance(ln, dict) and san(ln.get("text")):
            t = san(ln.get("text"))[:30]
            out.append((t, str(ln.get("voice") or "").strip() or t))
    if not out:
        for b in (it.get("benefits") or []):
            t = san(clean_bullet(b))[:30]
            if t:
                out.append((t, t))
    return out[:3]


def with_stop(v):
    v = (v or "").strip()
    return v if not v or v[-1] in "।.!?" else v + "।"


def build_multi(payload, items, shots, meta_items):
    """Har product ~15+ sec: (A) video clip / hero photo + price intro, (B) 2-3 gallery photos, har ek par ek benefit line (screen + awaaz)."""
    n = len(items)
    head, sub = deal_head(items[0])          # fallback: Rs old -> Rs new / % OFF
    hk = payload.get("hook") or {}
    head, sub = (str(hk.get("head") or "").strip() or head), (str(hk.get("sub") or "").strip() or sub)   # AI hook text screen pe
    first_media = None
    for k, it in enumerate(items):
        media = item_media(it)
        if not it.get("benefits") and media.get("bullets"):
            it["benefits"] = [clean_bullet(b) for b in media["bullets"][:3]]
        photos = load_photos(it, media)
        if not photos:
            continue
        vid = None
        if media.get("videos"):
            os.makedirs(f"{TMP}/v{k}", exist_ok=True)
            vid = load_video(media, f"{TMP}/pv{k}.mp4", secs=7)
        given = it.get("script") or {}
        sc = item_script(it, k, n)
        meta_items.append({"asin": it.get("asin"), "photos": len(photos), "video": bool(vid)})
        gal = photos[1:] if len(photos) > 1 else photos
        bens = item_benefits(it, sc)
        cap = sc["tag"]
        hook_v = ""
        if k == 0:
            hook_v = with_stop(str((payload.get("hook") or {}).get("voice") or "").strip())
            intro = (given.get("intro") or "").strip() or price_first(it)    # AI intro (bol-chal); na ho to template
        else:
            intro = (given.get("intro") or "").strip()                        # AI intro me jod (transition) pehle se hota hai
            if not intro:                                                     # fallback: har baar alag jod, phir product
                intro = f"{CONNECT[(k - 1) % len(CONNECT)] if k < n - 1 else CONNECT_LAST} {spoken_name(it.get('title'))}, सिर्फ़ {rs_short(it['price'])} में।"
        # ---- shot H (sirf pehle product par): hook line = AI head/sub screen pe + hook voice, clip/photo ke upar
        hook_shot = bool(k == 0 and hook_v)
        if hook_shot:
            if vid:
                hp = f"{TMP}/hov.png"; hook_overlay(head, sub, it).save(hp)
                shots.append({"kind": "video", "mp4": vid, "png": hp, "hook": True, "voice": hook_v, "min": 2.5, "pk": k, "lively": True})
            else:
                b_, o_ = photo_layers(photos[0], it, None, None, top=410, height=600); o_.alpha_composite(hook_overlay(head, sub, it))
                hp, hop = save_layers(b_, o_, f"{TMP}/hov")
                shots.append({"kind": "still", "png": hp, "ov": hop, "hook": True, "voice": hook_v, "min": 2.5, "pk": k, "lively": True})
        # ---- shot A: video clip (ya hero photo) + product intro (hook ke baad wahi clip aage chalti hai)
        if vid:
            ov = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(ov, "RGBA")
            title_chip(d, cap)                                   # price strip comes separately (animated pop)
            ovp = f"{TMP}/ov{k}.png"; ov.save(ovp)
            sp = strip_png(it, f"{TMP}/ov{k}_strip.png")
            shots.append({"kind": "video", "mp4": vid, "png": ovp, "strip": sp, "voice": intro, "min": 3.0, "pk": k, "vcont": hook_shot})
        else:
            b_, o_ = photo_layers(photos[0], it, None, None, strip=False)
            title_chip(ImageDraw.Draw(o_, "RGBA"), cap)
            p, op = save_layers(b_, o_, f"{TMP}/a{k}")
            sp = strip_png(it, f"{TMP}/a{k}_strip.png")
            shots.append({"kind": "still", "png": p, "ov": op, "strip": sp, "voice": intro, "min": 3.0, "pk": k})
        # ---- shots B: 2-3 gallery photos, har photo par ek benefit (screen caption + awaaz)
        cnt = max(2, min(3, len(bens))) if bens else 2
        for j in range(cnt):
            ph = gal[j % len(gal)]
            ln = bens[j] if j < len(bens) else None
            b_, o_ = photo_layers(ph, it, ln[0] if ln else None, f"{j + 1}/{cnt}")
            p, op = save_layers(b_, o_, f"{TMP}/b{k}_{j}")
            shots.append({"kind": "still", "png": p, "ov": op, "voice": with_stop(ln[1]) if ln else None, "min": 2.2, "pk": k})
        shots[-1]["tail"] = 0.45                  # product khatam: agle product se pehle thoda gap
        if first_media is None:
            first_media = photos[0]
    outro = (str(payload.get("outro") or "").strip()) or "लिंक कमेंट्स में है। रील सेव कर लो, और ऐसी डील्स के लिए फ़ॉलो ज़रूर करना।"
    p = f"{TMP}/outro.png"
    outro_frame(first_media or Image.new("RGB", (W, H), "#fff7ed"), items[0]).save(p)
    shots.append({"kind": "still", "png": p, "voice": outro, "min": 2.6})


def build_single(payload, items, shots, meta_items):
    idx, it = 0, items[0]
    media = item_media(it)
    if not it.get("benefits") and media.get("bullets"):
        it["benefits"] = [clean_bullet(b) for b in media["bullets"][:3]]
    photos = load_photos(it, media)
    if not photos:
        return
    vid = load_video(media, f"{TMP}/pv0.mp4", secs=7) if media.get("videos") else None
    sc = it.get("script") or default_script(it, 1)
    base = default_script(it, 1)
    for k_ in ("head", "sub", "hook", "price_voice", "outro"):
        sc.setdefault(k_, base[k_])
    lines = sc.get("lines") or base["lines"]
    meta_items.append({"asin": it.get("asin"), "photos": len(photos), "video": bool(vid)})
    sc["head"], sc["sub"] = deal_head(it)
    sc["hook"] = price_first(it, " पहले ये देख लो।")
    ov = hook_overlay(sc["head"], sc["sub"], it)
    if vid:
        ovp = f"{TMP}/ov0.png"; ov.save(ovp)
        shots.append({"kind": "video", "mp4": vid, "png": ovp, "voice": sc["hook"], "min": 3.6})
    else:
        b_, o_ = photo_layers(photos[0], it, None, None, top=410, height=600); o_.alpha_composite(ov)
        p, op = save_layers(b_, o_, f"{TMP}/h0")
        shots.append({"kind": "still", "png": p, "ov": op, "hook": True, "voice": sc["hook"], "min": 3.0})
    gal = photos[1:] if len(photos) > 1 else photos
    for j, ph in enumerate(gal[:3]):
        ln = lines[j] if j < len(lines) else None
        b_, o_ = photo_layers(ph, it, ln["text"] if ln else None, f"{j + 1}/{min(len(gal), 3)}")
        p, op = save_layers(b_, o_, f"{TMP}/p0_{j}")
        shots.append({"kind": "still", "png": p, "ov": op, "voice": ln["voice"] if ln else None, "min": 2.0})
    b_, o_ = photo_layers(photos[0], it, "LINK: COMMENTS me hai", None)
    p, op = save_layers(b_, o_, f"{TMP}/pr0")
    shots.append({"kind": "still", "png": p, "ov": op, "voice": sc["price_voice"] + " " + sc["outro"], "min": 3.0})


def _watchdog(signum, frame):
    print("WATCHDOG: render 12 min se zyada chala (media download / ffmpeg atka) -- abort", file=sys.stderr)
    sys.exit(3)


def voice_test(text):
    """Ek hi script alag-alag voices me: reel.mp4 = har voice ka naam + awaaz (A/B sunne ke liye)."""
    import edge_tts
    os.makedirs(OUT, exist_ok=True); os.makedirs(TMP, exist_ok=True)
    text = flow(text if isinstance(text, str) and len(text) > 5 else "रुको, आगे मत बढ़ना। इतने कम दाम में ये साउंडबार मिल रहा है। पाँच सौ वॉट की आवाज़, और दाम सिर्फ़ नौ हज़ार नौ सौ निन्यानवे रुपये। लिंक कमेंट्स में है।")
    voices = [("1 Swara hi-IN (abhi wali)", "hi-IN-SwaraNeural", "+4%", "+0Hz"), ("2 Swara dheemi", "hi-IN-SwaraNeural", "-4%", "+0Hz"),
              ("3 Neerja en-IN", "en-IN-NeerjaNeural", "+0%", "+0Hz"), ("4 Ava Multilingual", "en-US-AvaMultilingualNeural", "+0%", "+0Hz"),
              ("5 Emma Multilingual", "en-US-EmmaMultilingualNeural", "+0%", "+0Hz")]
    clips, ok = [], []
    for i, (lab, v, rate, pitch) in enumerate(voices):
        mp3 = f"{TMP}/t{i}.mp3"
        try:
            asyncio.run(edge_tts.Communicate(text, v, rate=rate, pitch=pitch).save(mp3))
        except Exception as e:
            print("voice test failed", v, str(e)[:100], file=sys.stderr); continue
        im = Image.new("RGB", (W, H), "#111111"); d = ImageDraw.Draw(im)
        text_outline(d, (W // 2, H // 2), lab, fit(d, lab, 80, W - 120, 40), fill="#ffeb3b", sw=4)
        png = f"{TMP}/t{i}.png"; im.save(png)
        c = f"{TMP}/tc{i}.mp4"; still_clip(png, dur(mp3) + 1.0, mp3, c, i); clips.append(c); ok.append(lab)
    with open(f"{TMP}/list.txt", "w") as f:
        for c in clips:
            f.write(f"file '{os.path.abspath(c)}'\n")
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", f"{TMP}/list.txt", "-c", "copy", f"{OUT}/reel.mp4"])
    json.dump({"voice_test": ok}, open(f"{OUT}/meta.json", "w"))
    print("voice test done", ok)


def _set_secs(shots):
    """Shot ki lambai = poori awaaz + chhota tail (awaaz kabhi kaati nahi jaati); har product kam se kam PRODUCT_MIN sec."""
    for s_ in shots:
        a = s_.get("audio")
        s_["secs"] = max(dur(a) + s_.get("tail", 0.25), s_["min"]) if a else s_["min"]
    by = {}
    for s_ in shots:
        if s_.get("pk") is not None:
            by.setdefault(s_["pk"], []).append(s_)
    for pk, grp in by.items():
        tot = sum(x["secs"] for x in grp)
        if tot < PRODUCT_MIN:
            add = (PRODUCT_MIN - tot) / len(grp)
            for x in grp:
                x["secs"] += add


def _est_total(shots):
    return sum(s_["secs"] for s_ in shots) - sum(_trans(shots, i)[1] for i in range(1, len(shots)))


def _retempo(path, f):
    out = path[:-4] + f".t{int(f * 100)}.mp3"
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-af", f"atempo={f:.3f}", "-ar", "44100", "-ac", "1", out],
                       capture_output=True, text=True, timeout=60)
    return out if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 500 else path


def fit_length(shots):
    """Reel 36-44 sec: lambi ho to awaaz thodi tez (max MAX_EXTRA_SPEED), chhoti ho to shots thoda zyada rukte hain."""
    _set_secs(shots)
    est = _est_total(shots)
    if est > MAX_TOTAL and any(s_.get("audio") for s_ in shots):
        f = min(MAX_EXTRA_SPEED, est / MAX_TOTAL)
        if f > 1.02:
            for s_ in shots:
                if s_.get("audio"):
                    s_["audio"] = _retempo(s_["audio"], f)
            _set_secs(shots)
            print(f"length {est:.1f}s > {MAX_TOTAL:.0f}s -> voice x{f:.2f} -> {_est_total(shots):.1f}s")
    est = _est_total(shots)
    if est < MIN_TOTAL:
        add = (MIN_TOTAL - est) / len(shots)
        for s_ in shots:
            s_["secs"] += add
        print(f"length {est:.1f}s < {MIN_TOTAL:.0f}s -> +{add:.2f}s per shot")
    print(f"estimated reel length {_est_total(shots):.1f}s")


def main():
    try:
        import signal
        signal.signal(signal.SIGALRM, _watchdog)
        signal.alarm(12 * 60)
    except Exception:
        pass
    payload = json.loads(os.environ["PAYLOAD"])
    if "voice_test" in payload:
        return voice_test(payload["voice_test"])
    label = payload.get("label") or "Top Deals"
    items = [i for i in payload.get("items", []) if i.get("price")][:3]
    if not items:
        sys.exit("need >= 1 item")
    try:
        payload["hook"] = {k: (fill_screen(v, items) if k in ("head", "sub") else fill(v, items)) for k, v in (payload.get("hook") or {}).items()}
        if payload.get("outro"):
            payload["outro"] = fill(payload["outro"], items)
        for it_ in items:
            sc_ = it_.get("script") or {}
            for k_ in ("intro", "tag"):
                if sc_.get(k_):
                    sc_[k_] = fill_screen(sc_[k_], items) if k_ == "tag" else fill(sc_[k_], items)
            for ln_ in sc_.get("lines") or []:
                ln_["text"], ln_["voice"] = fill_screen(ln_.get("text"), items), fill(ln_.get("voice"), items)      # screen text: Hindi shabd nahi (tofu), sirf Rs/%
    except Exception as e:
        print("script fill failed:", e, file=sys.stderr)
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(TMP, exist_ok=True)
    shots, meta_items = [], []
    (build_multi if len(items) > 1 else build_single)(payload, items, shots, meta_items)
    if not shots:
        sys.exit("no media at all")
    voiced = 0
    for k, s_ in enumerate(shots):
        a = tts_one(s_["voice"], f"{TMP}/v{k}.mp3", bool(s_.get("lively"))) if s_.get("voice") else None
        voiced += 1 if a else 0
        s_["audio"] = a
    fit_length(shots)
    clips, prev_secs = [], 0.0
    for k, s_ in enumerate(shots):
        c, a, secs = f"{TMP}/c{k}.mp4", s_["audio"], s_["secs"]
        if s_["kind"] == "video":
            st = 0.0
            if s_.get("vcont"):                  # pichla shot isi clip ka tha: wahin se aage chalao
                L = max(1.0, dur(s_["mp4"]))
                st = prev_secs % L
            video_clip(s_["mp4"], s_["png"], secs, a, c, st, s_.get("strip"), bool(s_.get("hook")))
        else:
            still_clip(s_["png"], secs, a, c, k, s_.get("ov"), s_.get("strip"), bool(s_.get("hook")))
        clips.append(c)
        prev_secs = secs
    try:
        assemble(clips, shots, f"{OUT}/reel.mp4")
    except Exception as e:           # kuch bhi toota to purana plain concat (reel phir bhi banegi)
        print("fancy assemble failed -> plain concat:", str(e)[:200], file=sys.stderr)
        with open(f"{TMP}/list.txt", "w") as f:
            for c in clips:
                f.write(f"file '{os.path.abspath(c)}'\n")
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", f"{TMP}/list.txt", "-c", "copy",
             "-movflags", "+faststart", f"{OUT}/reel.mp4"])
    total = dur(f"{OUT}/reel.mp4")
    json.dump({"label": label, "items": len(meta_items), "media": meta_items, "seconds": round(total, 1), "voice": voiced > 0,
               "voice_name": VOICE, "voice_used": VOICE_USED[0], "shots": len(shots)}, open(f"{OUT}/meta.json", "w"))
    print("done", round(total, 1), "s, shots", len(shots), "voice shots", voiced, meta_items, VOICE)


if __name__ == "__main__":
    main()
