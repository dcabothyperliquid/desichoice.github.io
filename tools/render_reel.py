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
VOICE = os.environ.get("REEL_VOICE", "en-US-AvaMultilingualNeural")
RATE = os.environ.get("REEL_RATE", "+10%")
PITCH = os.environ.get("REEL_PITCH", "+2Hz")
FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"]
ACCENT = "#e53935"
MAX_VIDEO_S = 4.5


def font(size):
    for p in FONTS:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def short(title, n=60):
    t = (title or "").strip()
    t = re.split(r"\s[|\-–—]\s|,\s|\s\(", t)[0].strip() or (title or "").strip()
    return (t[:n].rstrip() + "…") if len(t) > n else t


def spoken_name(title):
    return " ".join(short(title, 60).split()[:3]).strip(" .,&")


def inr(v):
    return f"₹{float(v):,.0f}"


def text_outline(d, xy, text, fnt, fill="white", stroke="#000000", sw=6, anchor="mm"):
    d.text(xy, text, font=fnt, fill=fill, anchor=anchor, stroke_width=sw, stroke_fill=stroke)


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


def photo_frame(photo, it, caption=None, counter=None, top=250, height=980):
    """Blurred full-bleed bg + white product card + caption + price strip. Returns RGB image."""
    im = blurred_bg(photo).convert("RGBA")
    d = ImageDraw.Draw(im, "RGBA")
    d.rounded_rectangle((50, top, W - 50, top + height), 48, fill=(255, 255, 255, 255))
    ph = photo.copy()
    ph.thumbnail((940, height - 40))
    im.paste(ph, ((W - ph.width) // 2, top + (height - ph.height) // 2))
    if counter:
        d.rounded_rectangle((W - 230, 190, W - 60, 270), 30, fill=(0, 0, 0, 170))
        d.text((W - 145, 230), counter, font=font(44), fill="white", anchor="mm")
    if caption:
        f = fit(d, caption, 64, W - 160, 40)
        for k, ln in enumerate(wrap(d, caption, f, W - 160)):
            text_outline(d, (W // 2, 1290 + k * 70), ln, f)
    price_strip(d, it)
    return im.convert("RGB")


def hook_overlay(head, sub, it):
    """Transparent overlay for the hook shot (drawn over blurred video / photo)."""
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov, "RGBA")
    d.rounded_rectangle((50, 90, W - 50, 380), 46, fill=(0, 0, 0, 160))
    text_outline(d, (W // 2, 200), head, fit(d, head, 120, W - 160, 60), fill="#ffeb3b", sw=7)
    text_outline(d, (W // 2, 320), sub, fit(d, sub, 58, W - 160, 34), sw=5)
    price_strip(d, it)
    return ov


def outro_frame(photo, it):
    im = blurred_bg(photo).convert("RGBA")
    d = ImageDraw.Draw(im, "RGBA")
    d.rounded_rectangle((70, 560, W - 70, 1260), 54, fill=(255, 255, 255, 240))
    d.text((W // 2, 700), "LINK", font=font(120), fill="#f97316", anchor="mm")
    d.text((W // 2, 850), "COMMENTS me hai", font=font(74), fill="#111111", anchor="mm")
    d.text((W // 2, 1010), "Follow @DesiChoice", font=font(58), fill="#555555", anchor="mm")
    d.text((W // 2, 1100), "roz aise sasti deals", font=font(52), fill="#555555", anchor="mm")
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
async def _tts(text, path):
    import edge_tts
    await edge_tts.Communicate(text, VOICE, rate=RATE, pitch=PITCH).save(path)


def tts_one(text, path):
    """One sentence -> mp3 with leading/trailing silence removed (edge-tts pads ~0.3s each side = the 'ruk ruk ke' feel)."""
    raw = path + ".raw.mp3"
    try:
        asyncio.run(_tts(text, raw))
        if os.path.getsize(raw) < 500:
            return None
        trim = ("silenceremove=start_periods=1:start_threshold=-33dB:start_silence=0.01,areverse,"
                "silenceremove=start_periods=1:start_threshold=-33dB:start_silence=0.01,areverse,"
                "silenceremove=stop_periods=-1:stop_duration=0.14:stop_threshold=-34dB:stop_silence=0.1")
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


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    if r.returncode:
        print("ffmpeg error:", r.stderr[-600:], file=sys.stderr)
        raise RuntimeError("ffmpeg failed")


def still_clip(png, secs, audio, out, k):
    # alternate zoom in / out so consecutive photos do not feel identical
    z = "min(zoom+0.0006,1.08)" if k % 2 == 0 else "if(eq(on,1),1.08,max(zoom-0.0006,1.0))"
    vf = f"scale=2160:3840,zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps=25,format=yuv420p"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "25", "-t", f"{secs:.2f}", "-i", png]
    cmd += ["-i", audio] if audio else ["-f", "lavfi", "-t", f"{secs:.2f}", "-i", "anullsrc=r=44100:cl=mono"]
    cmd += ["-vf", vf, "-r", "25", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-c:a", "aac", "-b:a", "128k",
            "-ar", "44100", "-ac", "1", "-af", "apad", "-t", f"{secs:.2f}", out]
    run(cmd)


def video_clip(mp4, overlay_png, secs, audio, out):
    """Product video: blurred cover bg + video fit-to-width in the middle + overlay (hook text, price). Video muted, voice on top."""
    fc = (f"[0:v]split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30:6,eq=brightness=-0.18[bg];"
          f"[b]scale={W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2[m];[m][1:v]overlay=0:0,format=yuv420p[v]")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", mp4, "-loop", "1", "-i", overlay_png]
    cmd += ["-i", audio] if audio else ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono"]
    cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "2:a", "-r", "25", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "22", "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "1", "-af", "apad", "-t", f"{secs:.2f}", out]
    run(cmd)


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


def item_script(it, k, n):
    """Per-item narration for multi-product reels (fallback when Groq script missing)."""
    price, lp, pct = float(it["price"]), float(it.get("list_price") or 0), int(it.get("pct") or 0)
    name = spoken_name(it.get("title"))
    sc = dict(it.get("script") or {})
    sc.setdefault("intro", f"{ORD[k]} है {name}, सिर्फ़ {rs_voice(price)} में, पूरे {hindi_num(pct)} परसेंट की छूट के साथ।")
    sc.setdefault("lines", [{"text": clean_bullet(b), "voice": clean_bullet(b)} for b in (it.get("benefits") or [])[:2] if clean_bullet(b)])
    sc.setdefault("tag", f"{k + 1}. {short(it.get('title'), 26)}")
    return sc


def build_multi(payload, items, shots, meta_items):
    n = len(items)
    top = max(items, key=lambda i: int(i.get("pct") or 0))
    hk = payload.get("hook") or {}
    head = hk.get("head") or f"ABHI LO! {int(top.get('pct') or 0)}% tak OFF"
    sub = hk.get("sub") or payload.get("label") or "Top Deals"
    low_p = min(float(i["price"]) for i in items)
    hvoice = hk.get("voice") or f"रुको! {hindi_num(n)} ऐसी डील्स, एक तो सिर्फ़ {rs_voice(low_p)} की। ये दाम कब तक रहेगा, पता नहीं, इसलिए पूरा देखो।"
    first_media = None
    for k, it in enumerate(items):
        media = item_media(it)
        if not it.get("benefits") and media.get("bullets"):
            it["benefits"] = [clean_bullet(b) for b in media["bullets"][:2]]
        photos = load_photos(it, media)
        if not photos:
            continue
        if os.environ.get("DEBUG_MONTAGE"):
            sheet = Image.new("RGB", (5 * 300, 300), "white")
            for q, ph in enumerate(photos[:5]):
                t = ph.copy(); t.thumbnail((300, 300)); sheet.paste(t, (q * 300, 0))
            sheet.save(f"{OUT}/gallery_{it.get('asin')}.jpg")
        vid = None
        if media.get("videos"):
            os.makedirs(f"{TMP}/v{k}", exist_ok=True)
            vid = load_video(media, f"{TMP}/pv{k}.mp4", secs=5)
        sc = item_script(it, k, n)
        meta_items.append({"asin": it.get("asin"), "photos": len(photos), "video": bool(vid)})
        gal = photos[1:] if len(photos) > 1 else photos
        # product shot A: video (5s max) or hero photo, name tag + intro voice
        cap = sc["tag"]
        voice_a = (hvoice + " " + sc["intro"]) if k == 0 else sc["intro"]
        if vid:
            if k == 0:
                ov = hook_overlay(head, sub, it)
            else:
                ov = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(ov, "RGBA")
                d.rounded_rectangle((50, 110, W - 50, 250), 40, fill=(0, 0, 0, 165))
                text_outline(d, (W // 2, 180), cap, fit(d, cap, 56, W - 160, 34), fill="#ffeb3b", sw=5)
                price_strip(d, it)
            ovp = f"{TMP}/ov{k}.png"; ov.save(ovp)
            shots.append({"kind": "video", "mp4": vid, "png": ovp, "voice": voice_a, "min": 3.0})
        else:
            if k == 0:
                f = photo_frame(photos[0], it, None, None, top=410, height=820).convert("RGBA"); f.alpha_composite(hook_overlay(head, sub, it))
                p = f"{TMP}/a{k}.png"; f.convert("RGB").save(p)
            else:
                p = f"{TMP}/a{k}.png"; photo_frame(photos[0], it, cap, None).save(p)
            shots.append({"kind": "still", "png": p, "voice": voice_a, "min": 2.6})
        # product shot B..: gallery photos with feature captions (voice: the line itself)
        for j, ph in enumerate(gal[:(1 if n >= 3 else 2)]):
            ln = sc["lines"][j] if j < len(sc["lines"]) else None
            p = f"{TMP}/b{k}_{j}.png"
            photo_frame(ph, it, ln["text"] if ln else None, None).save(p)
            shots.append({"kind": "still", "png": p, "voice": ln["voice"] if ln else None, "min": 2.0})
        if first_media is None:
            first_media = photos[0]
    outro = payload.get("outro") or "सारे लिंक कमेंट्स में हैं। रोज़ ऐसी डील्स के लिए फ़ॉलो करो।"
    p = f"{TMP}/outro.png"
    outro_frame(first_media or Image.new("RGB", (W, H), "#fff7ed"), items[0]).save(p)
    shots.append({"kind": "still", "png": p, "voice": outro, "min": 2.8})


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
    ov = hook_overlay(sc["head"], sc["sub"], it)
    if vid:
        ovp = f"{TMP}/ov0.png"; ov.save(ovp)
        shots.append({"kind": "video", "mp4": vid, "png": ovp, "voice": sc["hook"], "min": 3.6})
    else:
        f = photo_frame(photos[0], it, None, None, top=410, height=820).convert("RGBA"); f.alpha_composite(ov)
        p = f"{TMP}/h0.png"; f.convert("RGB").save(p)
        shots.append({"kind": "still", "png": p, "voice": sc["hook"], "min": 3.0})
    gal = photos[1:] if len(photos) > 1 else photos
    for j, ph in enumerate(gal[:3]):
        ln = lines[j] if j < len(lines) else None
        p = f"{TMP}/p0_{j}.png"
        photo_frame(ph, it, ln["text"] if ln else None, f"{j + 1}/{min(len(gal), 3)}").save(p)
        shots.append({"kind": "still", "png": p, "voice": ln["voice"] if ln else None, "min": 2.0})
    p = f"{TMP}/pr0.png"
    photo_frame(photos[0], it, "LINK: COMMENTS me hai", None).save(p)
    shots.append({"kind": "still", "png": p, "voice": sc["price_voice"] + " " + sc["outro"], "min": 3.0})


def _watchdog(signum, frame):
    print("WATCHDOG: render 12 min se zyada chala (media download / ffmpeg atka) -- abort", file=sys.stderr)
    sys.exit(3)


def main():
    try:
        import signal
        signal.signal(signal.SIGALRM, _watchdog)
        signal.alarm(12 * 60)
    except Exception:
        pass
    payload = json.loads(os.environ["PAYLOAD"])
    label = payload.get("label") or "Top Deals"
    items = [i for i in payload.get("items", []) if i.get("price")][:3]
    if not items:
        sys.exit("need >= 1 item")
    try:
        payload["hook"] = {k: fill(v, items) for k, v in (payload.get("hook") or {}).items()}
        if payload.get("outro"):
            payload["outro"] = fill(payload["outro"], items)
        for it_ in items:
            sc_ = it_.get("script") or {}
            for k_ in ("intro", "tag"):
                if sc_.get(k_):
                    sc_[k_] = fill(sc_[k_], items)
            for ln_ in sc_.get("lines") or []:
                ln_["text"], ln_["voice"] = fill(ln_.get("text"), items), fill(ln_.get("voice"), items)
    except Exception as e:
        print("script fill failed:", e, file=sys.stderr)
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(TMP, exist_ok=True)
    shots, meta_items = [], []
    (build_multi if len(items) > 1 else build_single)(payload, items, shots, meta_items)
    if not shots:
        sys.exit("no media at all")
    clips, voiced = [], 0
    for k, s in enumerate(shots):
        a = tts_one(s["voice"], f"{TMP}/v{k}.mp3") if s.get("voice") else None
        voiced += 1 if a else 0
        secs = (dur(a) + 0.15) if a else s["min"]
        if a and s["kind"] == "video":
            secs = max(secs, 2.5)
        if s["kind"] == "video" and not a:      # awaaz ho to poori bolne do (clip loop hota hai); cap sirf bina awaaz ke
            secs = min(secs, 7.0)
        c = f"{TMP}/c{k}.mp4"
        if s["kind"] == "video":
            video_clip(s["mp4"], s["png"], secs, a, c)
        else:
            still_clip(s["png"], secs, a, c, k)
        clips.append(c)
    with open(f"{TMP}/list.txt", "w") as f:
        for c in clips:
            f.write(f"file '{os.path.abspath(c)}'\n")
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", f"{TMP}/list.txt", "-c", "copy",
         "-movflags", "+faststart", f"{OUT}/reel.mp4"])
    total = dur(f"{OUT}/reel.mp4")
    json.dump({"label": label, "items": len(meta_items), "media": meta_items, "seconds": round(total, 1), "voice": voiced > 0,
               "voice_name": VOICE, "shots": len(shots)}, open(f"{OUT}/meta.json", "w"))
    print("done", round(total, 1), "s, shots", len(shots), "voice shots", voiced, meta_items, VOICE)


if __name__ == "__main__":
    main()
