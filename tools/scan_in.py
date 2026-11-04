"""
Desi Picks - Amazon INDIA deal scan (GitHub Actions, Playwright).  Run:  python tools/scan_in.py

Same idea as the Zoe Picks (US) scan, but for amazon.in and WITHOUT guides: it only produces two public feed files.

  guides/hot.json     v3  {updated, hot[], ess[], trend[], themes[]}      -> Daily Essentials / Top Offers ticker / Trending
  guides/offers.json  v3  {updated, fresh_h, cats{<category>: [items]}}   -> Top Offers per CATEGORY (17 categories, same as Zoe)

Item shape (same as the US feed so the bot code does not change):
  {asin, name, pct, rating, t, img, link, now, was, deal, cat, label, icon, reviews, tier}     now/was = rupees as plain numbers in text

MODE (env SCAN_MODE or 1st argument):
  auto      (default) do whatever is due: refresh hot.json if older than FEED_EVERY_H, sweep categories not swept for SWEEP_EVERY_H
  feed      force-refresh hot.json (essentials + top offers + trending) now
  offers    force-sweep every category now (still limited by the time budget)
  selftest  checks every Amazon department/alias + discount filter, prints cards per scope, writes NOTHING
ENV: AMAZON_IN_TAG (affiliate tag, optional)  BUDGET_MIN (40)  PAGES (2)  MIN_PCT (25)  SWEEP_EVERY_H (11)  FEED_EVERY_H (11)
     FRESH_H (36)  SCAN_ONLY=<category>  (offers mode: only that category)
"""
import json
import os
import random
import re
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOT_PATH = os.path.join(ROOT, "guides", "hot.json")
OFFERS_PATH = os.path.join(ROOT, "guides", "offers.json")
IST = timezone(timedelta(hours=5, minutes=30))
BASE = "https://www.amazon.in"


def _int(name, default):
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


TAG = (os.environ.get("AMAZON_IN_TAG") or "desichoice06-21").strip()    # Amazon India Store ID (env var overrides)
BUDGET_MIN = _int("BUDGET_MIN", 40)
PAGES = _int("PAGES", 2)
MIN_PCT = _int("MIN_PCT", 25)                 # "xx% off or more" filter used for category sweeps / top offers
SWEEP_EVERY_H = _int("SWEEP_EVERY_H", 11)
FEED_EVERY_H = _int("FEED_EVERY_H", 11)
FRESH_H = _int("FRESH_H", 36)
PAUSE = (6, 14)                               # seconds between two Amazon pages
CAPTCHA_STOP = 2

# gates (same spirit as Zoe / Naami credibility gate)
MIN_RATING = 4.0
MIN_REVIEWS = 50
MAX_PCT = 85
SUSPICIOUS_PCT, SUSPICIOUS_REVIEWS = 70, 300
MIN_PRICE_ESS, MIN_PRICE_HOT, MIN_PRICE_OFFER = 49, 1500, 299     # rupees
LADIES_MIN_PRICE, LADIES_MIN_RATING = 149, 3.8                      # ladies category only
MAX_HOT, MAX_ESS, MAX_TREND, PER_THEME, PER_CAT_OFFERS = 30, 60, 36, 14, 40
NAME_MAX = 70

# ---- the 18 categories (17 from Zoe + Ladies) -------------------------------------------------------------
CATEGORIES = [
    ("home_kitchen", "🍳", "Home & Kitchen"), ("electronics", "🎧", "Electronics"), ("laptops", "💻", "Laptops"),
    ("mobiles", "📱", "Mobiles"), ("monitors", "🖥️", "Monitors & TV"), ("accessories", "🖱️", "Accessories"),
    ("beauty_health", "🧴", "Beauty & Health"), ("fitness_outdoor", "🏃", "Fitness & Outdoor"), ("fashion", "👟", "Fashion"), ("ladies", "👗", "Ladies"),
    ("toys_kids", "🧸", "Toys & Kids"), ("tools_auto", "🔧", "Tools & Auto"), ("pets", "🐶", "Pets"),
    ("office_books", "📚", "Office & Books"), ("gaming", "🎮", "Gaming"), ("smart_home", "🏠", "Smart Home"),
    ("furniture_decor", "🛋️", "Furniture & Decor"), ("hobby_music", "🎸", "Hobby & Music"),
]
CAT_LABEL = {k: (ic, lab) for k, ic, lab in CATEGORIES}

# category -> [(amazon.in search alias for &i=, keyword or None)].  Aliases are Amazon's public ones but can change:
# run mode=selftest once; a scope with 0 cards is wrong -> fix it HERE (or it falls back to FALLBACK_KW automatically).
SCOPES = {
    "home_kitchen":    [("kitchen", None), ("appliances", None)],
    "electronics":     [("electronics", None), ("electronics", "headphones")],
    "laptops":         [("computers", "laptop")],
    "mobiles":         [("electronics", "smartphone"), ("electronics", "smartwatch")],
    "monitors":        [("computers", "monitor"), ("electronics", "smart tv"), ("electronics", "projector")],
    "accessories":     [("computers", "keyboard mouse"), ("electronics", "power bank")],
    "beauty_health":   [("beauty", None), ("hpc", None)],
    "fitness_outdoor": [("sports", None)],
    "fashion":         [("apparel", None), ("shoes", None)],
    "ladies":          [("apparel", "women kurti set"), ("apparel", "women dress top"), ("jewelry", "women earrings necklace"),
                        ("apparel", "women saree"), ("apparel", "kurti"), ("apparel", "women tops"), ("jewelry", "women jewellery set"),
                        ("shoes", "women handbag"), ("shoes", "women sandals")],
    "toys_kids":       [("toys", None), ("baby", None)],
    "tools_auto":      [("home-improvement", None), ("automotive", None)],
    "pets":            [("pets", None)],
    "office_books":    [("office-products", None), ("stripbooks", None)],
    "gaming":          [("videogames", None)],
    "smart_home":      [("electronics", "smart home"), ("electronics", "security camera")],
    "furniture_decor": [("garden", "furniture"), ("kitchen", "home decor")],
    "hobby_music":     [("musical-instruments", None)],
}
FALLBACK_KW = {"home_kitchen": "kitchen appliances", "laptops": "laptop", "gaming": "gaming", "hobby_music": "guitar keyboard instrument",
               "office_books": "stationery books", "pets": "pet food", "tools_auto": "car accessories tools", "fashion": "men shoes clothing", "ladies": "women kurti dress",
               "fitness_outdoor": "fitness", "toys_kids": "toys", "beauty_health": "skin care", "electronics": "electronics",
               "furniture_decor": "furniture", "mobiles": "smartphone", "monitors": "monitor", "accessories": "computer accessories",
               "smart_home": "smart home"}

# ---- Daily Essentials: (key, label, icon, search keyword, must-match regex) -------------------------------------
ESS = [
    ("atta", "Atta & flour", "🌾", "wheat flour atta", r"atta|flour|besan|maida"),
    ("rice", "Rice", "🍚", "basmati rice", r"rice"),
    ("dal", "Dal & pulses", "🫘", "toor dal pulses", r"dal|chana|rajma|moong|lentil|pulses"),
    ("oil", "Cooking oil & ghee", "🫒", "cooking oil ghee", r"oil|ghee"),
    ("tea", "Tea & coffee", "☕", "tea coffee", r"tea|coffee"),
    ("snacks", "Biscuits & snacks", "🍪", "biscuits namkeen snacks", r"biscuit|cookies?|namkeen|chips|snack|wafer"),
    ("dryfruit", "Dry fruits & nuts", "🥜", "almonds cashews dry fruits", r"almond|cashew|raisin|pista|walnut|nuts|dry fruit|dates"),
    ("masala", "Spices & masala", "🧂", "masala spices", r"masala|spice|turmeric|chilli|jeera|haldi"),
    ("laundry", "Laundry detergent", "🧺", "laundry detergent", r"detergent|laundry|washing powder|fabric"),
    ("dish", "Dishwash", "🍽️", "dishwash liquid", r"dish ?wash|dish soap|vim"),
    ("floor", "Floor & toilet cleaner", "🧼", "floor cleaner toilet cleaner", r"floor|toilet|harpic|lizol|phenyl|cleaner"),
    ("handwash", "Handwash & sanitizer", "🧴", "handwash sanitizer", r"hand ?wash|sanitizer|hand soap"),
    ("shampoo", "Shampoo & hair care", "🧴", "shampoo conditioner", r"shampoo|conditioner|hair oil"),
    ("bodywash", "Soap & body wash", "🚿", "soap body wash", r"soap|body wash|shower gel"),
    ("toothpaste", "Toothpaste", "🪥", "toothpaste", r"toothpaste|tooth paste|toothbrush"),
    ("facewash", "Face wash & skincare", "🧴", "face wash moisturizer", r"face ?wash|moisturi|sunscreen|face cream|serum"),
    ("diapers", "Diapers & baby care", "👶", "baby diapers", r"diaper|baby wipes|pants"),
    ("pads", "Sanitary pads", "🌸", "sanitary pads", r"pads?|sanitary"),
    ("batteries", "Batteries", "🔋", "AA AAA batteries", r"batter"),
    ("protein", "Protein & supplements", "💪", "whey protein", r"protein|whey|creatine|bcaa"),
    ("vitamins", "Vitamins", "💊", "multivitamin tablets", r"vitamin|omega|multivit|zinc|calcium|supplement"),
    ("petfood", "Pet food", "🐶", "dog food", r"dog|cat|pet|puppy"),
    ("garbage", "Garbage bags", "🗑️", "garbage bags", r"garbage|trash|dustbin bags?|bin bags?"),
    ("bulbs", "LED bulbs", "💡", "led bulb", r"bulb|led"),
    ("bottles", "Bottles & lunch boxes", "🥤", "water bottle lunch box", r"bottle|flask|tiffin|lunch ?box"),
    ("socks", "Socks & innerwear", "🧦", "socks innerwear", r"socks?|vest|brief|boxer|innerwear"),
]
ACCESSORY = re.compile(r"\b(holder|dispenser|rack|stand|cover|case|bowl|feeder|tray|scoop|toy|machine|maker|grinder|mug|"
                       r"charger|power bank|mounts?|brush holder|storage|organi[sz]er|tester|empty)\b", re.I)

# ---- Trending: India seasons, picked by today's IST date.  (key, label, icon, (start m,d), (end m,d), [keywords]) ------
# Festival dates move every year (Diwali, Holi, Rakhi): adjust the windows here once a year.
THEMES = [
    ("festive", "Festive sale", "🪔", (9, 15), (10, 10), ["festive offers", "navratri decoration", "home decor lights"]),
    ("diwali", "Diwali gifts", "🪔", (10, 10), (11, 9), ["diwali gift hamper", "diwali decoration lights", "dry fruits gift box", "diya candles rangoli"]),
    ("wedding", "Wedding season", "💍", (11, 10), (2, 28), ["wedding gift", "ethnic wear", "jewellery set", "gift set"]),
    ("winter", "Winter ready", "🧣", (10, 20), (2, 15), ["room heater", "geyser water heater", "blanket quilt", "woolen jacket sweater"]),
    ("chhath", "Chhath Puja", "🌅", (11, 5), (11, 16), ["chhath puja samagri", "puja thali set", "copper kalash puja", "bamboo soop dala"]),
    ("christmas", "Christmas", "🎄", (12, 5), (12, 26), ["christmas decoration", "christmas tree", "santa claus gifts", "christmas gift set"]),
    ("newyear", "New Year party", "🎉", (12, 20), (1, 2), ["new year party decoration", "party lights", "photo frame gift", "gift set"]),
    ("valentine", "Valentine's Day", "❤️", (2, 1), (2, 14), ["valentine gift", "teddy bear", "chocolate gift box", "couple gift"]),
    ("republic", "Republic Day sale", "🇮🇳", (1, 1), (1, 27), ["smartphone", "laptop", "smart tv", "headphones"]),
    ("holi", "Holi", "🎨", (2, 20), (3, 10), ["holi colours", "water gun pichkari", "gulal"]),
    ("summer", "Summer cooling", "❄️", (3, 1), (6, 15), ["air cooler", "ceiling fan", "split ac", "water bottle cooler"]),
    ("monsoon", "Monsoon ready", "🌧️", (6, 15), (9, 14), ["umbrella raincoat", "monsoon shoes", "dehumidifier"]),
    ("rakhi", "Raksha Bandhan", "🧵", (7, 25), (8, 31), ["rakhi gift", "rakhi chocolates"]),
]

# ---- hot (top offers) searches: big-ticket things Indians shop on offer -----------------------------------------
HOT_QUERIES = ["smartphone", "laptop", "smart tv", "smartwatch", "wireless earbuds", "air conditioner", "refrigerator",
               "washing machine", "air fryer", "soundbar", "water purifier", "tablet", "headphones", "robot vacuum cleaner"]

_JS_CARDS = r"""
() => [...document.querySelectorAll('div[data-component-type="s-search-result"]')].map(c => {
  const asin = c.getAttribute('data-asin') || '';
  const h = c.querySelector('h2');
  const title = h ? (h.innerText || h.getAttribute('aria-label') || '').trim() : '';
  const im = c.querySelector('img.s-image');
  const pe = c.querySelector('.a-price:not(.a-text-price) .a-offscreen');
  const we = c.querySelector('.a-price.a-text-price .a-offscreen, [data-a-strike="true"] .a-offscreen');
  const re_ = c.querySelector('span.a-icon-alt');
  const rv = c.querySelector('span.s-underline-text, a[href*="customerReviews"] span, span[aria-label*="ratings"]');
  return {asin: asin, title: title, img: im ? im.src : '',
          price: pe ? pe.textContent : '', was: we ? we.textContent : '',
          rating: re_ ? re_.textContent : '', reviews: rv ? (rv.getAttribute('aria-label') || rv.textContent) : '',
          text: (c.innerText || '').slice(0, 900)};
})
"""
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_MARKS = ("Type the characters you see", "/errors/validateCaptcha", "Enter the characters you see below",
          "To discuss automated access", "Robot Check", "api-services-support@amazon.com")
_DEAL_RX = re.compile(r"(Limited time deal|Deal of the Day|Lightning Deal|Great Indian Festival|Prime Exclusive|Top Deal|Festive Deal)", re.I)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# =====================================================================================================================
# pure helpers (unit-tested in tests/test_logic.py - no browser needed)
# =====================================================================================================================
def num(v):
    s = re.sub(r"[^\d.]", "", str(v or "").replace(",", ""))
    try:
        return float(s) if s else None
    except ValueError:
        return None


def parse_reviews(s):
    s = str(s or "").strip().replace(",", "")
    m = re.search(r"([\d.]+)\s*([KkMm]?)", s)
    if not m:
        return 0
    try:
        n = float(m.group(1))
    except ValueError:
        return 0
    return int(n * {"k": 1e3, "m": 1e6}.get(m.group(2).lower(), 1))


def parse_rating(s):
    m = re.search(r"(\d(?:\.\d)?)\s*out of", str(s or ""))
    return float(m.group(1)) if m else 0.0


def full_img(url):
    return re.sub(r"\._[A-Z0-9_,.\-]+_\.", ".", str(url or ""))


def short_name(t):
    t = re.sub(r"\s+", " ", str(t or "")).strip(" -,|")
    if len(t) <= NAME_MAX:
        return t
    cut = t[:NAME_MAX].rsplit(" ", 1)[0]
    return cut.strip(" -,|(/")


def tier(price):
    return "budget" if price < 1000 else ("mid" if price < 10000 else "premium")


def card_to_item(c, now=None):
    """raw browser card -> item dict or None (sponsored / no price / no discount / bad data)."""
    asin = str(c.get("asin") or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{10}", asin):
        return None
    text = str(c.get("text") or "")
    if re.search(r"(^|\n|\s)Sponsored\b", text):
        return None
    price, was = num(c.get("price")), num(c.get("was"))
    if not price or price <= 0:
        return None
    pct = 0
    if was and was <= price * 1.01:
        was = 0                      # not a real MRP (e.g. a per-unit price) -> derive from the "xx% off" text below
    if was and was > price * 1.01:
        pct = int(round((was - price) / was * 100))
    else:
        m = re.search(r"\(?(\d{1,2})\s*%\s*off\)?", text, re.I) or re.search(r"(?<![\d.])-\s?(\d{1,2})\s?%", text)
        if m:
            pct = int(m.group(1))
            if pct and not was:
                was = round(price / (1 - pct / 100.0))
    if pct <= 0 or not was:
        return None
    img = full_img(c.get("img"))
    if not img.startswith("http"):
        return None
    m = _DEAL_RX.search(text)
    return {"asin": asin, "name": short_name(c.get("title")), "pct": pct, "rating": parse_rating(c.get("rating")),
            "t": int(now or time.time()), "img": img, "now": ("%g" % price), "was": ("%g" % was),
            "link": f"{BASE}/dp/{asin}" + (f"?tag={TAG}" if TAG else ""), "deal": (m.group(1) if m else ""),
            "reviews": parse_reviews(c.get("reviews")), "_price": price}


def credible(it, min_pct, min_price, min_rating=MIN_RATING):
    """same gates as Naami: real discount, rating >= 4.0 (unknown skipped), reviews >= 50 (unknown skipped), suspicious-discount gate."""
    if not it or not it.get("name"):
        return False
    pct, price = it["pct"], it["_price"]
    if pct < min_pct or pct > MAX_PCT or price < min_price:
        return False
    if 0 < it["rating"] < min_rating:
        return False
    if 0 < it["reviews"] < MIN_REVIEWS:
        return False
    if pct >= SUSPICIOUS_PCT and it["reviews"] < SUSPICIOUS_REVIEWS:
        return False
    return True


def clean(it, **extra):
    o = {k: v for k, v in it.items() if not k.startswith("_")}
    if not o.get("deal"):
        o.pop("deal", None)
    if not o.get("reviews"):
        o.pop("reviews", None)
    o["tier"] = extra.pop("tier", None) or tier(it["_price"])
    o.update(extra)
    return o


def active_themes(now=None):
    n = (now or datetime.now(timezone.utc)).astimezone(IST)
    cur = (n.month, n.day)
    out = []
    for key, label, icon, a, b, kws in THEMES:
        inside = (a <= cur <= b) if a <= b else (cur >= a or cur <= b)
        if not inside:
            continue
        end_y = n.year if b >= cur else n.year + 1
        left = (datetime(end_y, b[0], b[1]) - datetime(n.year, cur[0], cur[1])).days
        out.append((left, key, label, icon, kws))
    out.sort()
    return [(k, l, i, kws) for _, k, l, i, kws in out]


def round_robin(groups, limit):
    """groups = {key: [items best first]} -> best-discount-first interleave across keys, up to limit."""
    keys = sorted(groups, key=lambda k: -(groups[k][0]["pct"] if groups[k] else 0))
    out, i = [], 0
    while len(out) < limit and any(groups[k][i:i + 1] for k in keys):
        for k in keys:
            if len(out) >= limit:
                break
            if i < len(groups[k]):
                out.append(groups[k][i])
        i += 1
    return out


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def iso(ts=None):
    return datetime.fromtimestamp(ts or time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def age_h(s):
    try:
        return (time.time() - datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()) / 3600
    except Exception:
        return 1e9


# =====================================================================================================================
# browser
# =====================================================================================================================
# Amazon.in answers 503 ("automated access") to GitHub runner IPs, but the same page fetched through Google's website-translate
# proxy (<host with dots as dashes>.translate.goog) comes back complete (verified from the runner, see guides/probe2.json).
# So: plain HTTP fetch through translate.goog + HTML parsing - no browser needed.
TRANSLATE_HOST = "https://www-amazon-in.translate.goog"
_TR = "_x_tr_sl=auto&_x_tr_tl=en&_x_tr_hl=en"


def parse_cards(html):
    """Same fields as the old in-browser extractor: asin,title,img,price,was,rating,reviews,text."""
    from bs4 import BeautifulSoup
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    out = []
    for c in soup.select('div[data-component-type="s-search-result"]'):
        def txt(el):
            return el.get_text(" ", strip=True) if el else ""
        h = c.find("h2")
        title = (txt(h) or (h.get("aria-label") or "").strip()) if h else ""
        im = c.select_one("img.s-image")
        pe = c.select_one(".a-price:not(.a-text-price) .a-offscreen")
        we = c.select_one('[data-a-strike="true"] .a-offscreen')      # ONLY the struck-through MRP (unit prices like Rs36.9/count are .a-text-price too)
        ra = c.select_one("span.a-icon-alt")
        rv = c.select_one('span.s-underline-text, a[href*="customerReviews"] span, span[aria-label*="ratings"]')
        out.append({"asin": c.get("data-asin") or "", "title": title, "img": (im.get("src") if im else "") or "",
                    "price": txt(pe), "was": txt(we), "rating": txt(ra),
                    "reviews": ((rv.get("aria-label") if rv else "") or txt(rv)),
                    "text": txt(c)[:900]})
    return out


class Amazon:
    def __init__(self):
        import requests
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": _UA, "Accept-Language": "en-IN,en;q=0.9",
                               "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
        self.captcha_streak = 0
        self.trace = []          # per-page diagnostics -> guides/scan_status.json
        self.pages_loaded = 0
        self.t0 = time.time()

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass

    def out_of_time(self):
        return (time.time() - self.t0) > BUDGET_MIN * 60

    def blocked(self):
        return self.captcha_streak >= CAPTCHA_STOP

    def search(self, kw=None, alias=None, page_no=1, pct=MIN_PCT, use_filter=True):
        """-> list of raw cards (may be []), sets captcha streak. One polite pause before every page."""
        if self.pages_loaded:
            time.sleep(random.uniform(*PAUSE))
        q = []
        if kw:
            q.append("k=" + urllib.parse.quote_plus(kw))
        if alias:
            q.append("i=" + urllib.parse.quote_plus(alias))
        if use_filter and pct:
            q.append("rh=" + urllib.parse.quote(f"p_8:{pct}-"))
        if page_no > 1:
            q.append(f"page={page_no}")
        url = f"{TRANSLATE_HOST}/s?" + "&".join(q + [_TR])
        self.pages_loaded += 1
        html, status = "", None
        for attempt in (1, 2):
            try:
                r = self.s.get(url, timeout=45)
                html, status = r.text, r.status_code
            except Exception as e:
                log("  page error:", str(e)[:100])
                self.trace.append({"url": url, "error": str(e)[:160]})
                return []
            if status in (429, 500, 502, 503, 504) and attempt == 1:
                time.sleep(random.uniform(15, 25))
                continue
            break
        _rec = {"url": url, "http": status, "html_len": len(html),
                "has_results": "s-search-result" in html, "marks": [m for m in _MARKS if m in html]}
        self.trace.append(_rec)
        if 's-search-result' not in html and (status in (429, 503) or any(m in html for m in _MARKS)
                                              or "unusual traffic" in html):
            self.captcha_streak += 1
            log("  CAPTCHA / block (streak %d)" % self.captcha_streak)
            return []
        self.captcha_streak = 0
        try:
            _cards = parse_cards(html)
            _rec["cards"] = len(_cards)
            return _cards
        except Exception as e:
            log("  extract error:", str(e)[:100])
            _rec["extract_error"] = str(e)[:120]
            return []

    def items(self, kw=None, alias=None, pages=1, pct=MIN_PCT, min_price=MIN_PRICE_OFFER, min_rating=MIN_RATING):
        """-> (kept credible items, raw card count). Falls back to an unfiltered page when the discount filter returns 0 cards."""
        out, seen, raw_n = [], set(), 0
        for p in range(1, pages + 1):
            cards = self.search(kw, alias, p, pct, True)
            if not cards and not self.blocked() and p == 1:
                cards = self.search(kw, alias, p, pct, False)      # filter id might be wrong -> filter locally instead
            raw_n += len(cards)
            for c in cards:
                it = card_to_item(c)
                if it and it["asin"] not in seen and credible(it, pct, min_price, min_rating):
                    seen.add(it["asin"])
                    out.append(it)
            if self.blocked() or self.out_of_time() or not cards:
                break
        return out, raw_n


# =====================================================================================================================
# jobs
# =====================================================================================================================
def build_feed(am):
    """hot.json: ess + hot + trend. Each product in ONE list only (trend > ess > hot)."""
    old = read_json(HOT_PATH, {})
    used = set()

    # trend
    themes = active_themes()
    trend = []
    if themes:
        per = {}
        for key, label, icon, kws in themes:
            lst = []
            for kw in kws:
                if am.blocked() or am.out_of_time():
                    break
                items, _ = am.items(kw, None, 1, 20, MIN_PRICE_ESS)
                lst += [clean(i, theme=key, label=label, icon=icon) for i in items]
                log(f"  trend {key}/{kw}: {len(items)}")
            lst = sorted({i["asin"]: i for i in lst}.values(), key=lambda x: -x["pct"])[:PER_THEME]
            per[key] = lst
        trend = round_robin({k: v for k, v in per.items() if v}, MAX_TREND)
    used |= {i["asin"] for i in trend}

    # ess
    groups = {}
    for key, label, icon, kw, rx in ESS:
        if am.blocked() or am.out_of_time():
            break
        items, _ = am.items(kw, None, 1, 10, MIN_PRICE_ESS)
        good = [i for i in items if re.search(rx, i["name"], re.I) and not ACCESSORY.search(i["name"]) and i["asin"] not in used]
        good.sort(key=lambda x: -x["pct"])
        groups[key] = [clean(i, cat=key, label=label, icon=icon) for i in good[:4]]
        log(f"  ess {key}: {len(groups[key])}")
    ess = round_robin({k: v for k, v in groups.items() if v}, MAX_ESS)
    used |= {i["asin"] for i in ess}

    # hot (top offers): big-ticket, diversity cap per search
    hot = []
    for kw in HOT_QUERIES:
        if am.blocked() or am.out_of_time():
            break
        items, _ = am.items(kw, None, 1, MIN_PCT, MIN_PRICE_HOT)
        items = [i for i in items if i["asin"] not in used and i["rating"] >= MIN_RATING]
        items.sort(key=lambda x: -x["pct"])
        hot += [clean(i) for i in items[:4]]
        log(f"  hot {kw}: {min(len(items), 4)}")
    hot = sorted({i["asin"]: i for i in hot}.values(), key=lambda x: -x["pct"])[:MAX_HOT]

    # never overwrite a list with nothing: keep the previous list if it is still fresh
    def keep(new, key):
        if new:
            return new
        return [i for i in (old.get(key) or []) if (time.time() - float(i.get("t") or 0)) < FRESH_H * 3600]

    out = {"v": 3, "updated": iso(), "hot": keep(hot, "hot"), "ess": keep(ess, "ess"), "trend": keep(trend, "trend"),
           "themes": [{"key": k, "label": l, "icon": i} for k, l, i, _ in themes]}
    if not (out["hot"] or out["ess"] or out["trend"]):
        log("feed: nothing usable (blocked?) - hot.json left untouched")
        return False
    write_json(HOT_PATH, out)
    log(f"feed written: hot={len(out['hot'])} ess={len(out['ess'])} trend={len(out['trend'])}")
    return True


def sweep_category(am, cat):
    ic, lab = CAT_LABEL[cat]
    got = {}
    _mp, _mr = (LADIES_MIN_PRICE, LADIES_MIN_RATING) if cat == "ladies" else (MIN_PRICE_OFFER, MIN_RATING)      # ladies items (kurti, jewellery, tops) are cheap + rated a bit lower: relaxed gates only here
    for alias, kw in SCOPES[cat]:
        if am.blocked() or am.out_of_time():
            break
        items, raw = am.items(kw, alias, PAGES, MIN_PCT, _mp, _mr)
        if raw == 0 and not am.blocked():                       # wrong alias -> keyword search instead
            items, raw = am.items(kw or FALLBACK_KW.get(cat), None, PAGES, MIN_PCT, _mp, _mr)
        log(f"  {cat} [{alias}/{kw}]: {raw} cards -> {len(items)} offers")
        for i in items:
            got[i["asin"]] = clean(i, cat=cat, label=lab, icon=ic)
    if cat == "ladies":      # TEMP debug: why ladies ends empty -> guides/ladies_debug.json
        try:
            write_json(os.path.join(os.path.dirname(OFFERS_PATH), "ladies_debug.json"), {"time": iso(), "kept": len(got),
                "sample": [{"name": (v.get("name") or "")[:90], "cls": classify(v), "pct": v.get("pct"), "now": v.get("now"), "rating": v.get("rating"), "reviews": v.get("reviews")} for v in list(got.values())[:25]]})
        except Exception as e:
            log("ladies debug failed", e)
    return sorted(got.values(), key=lambda x: (-x["pct"], -(x.get("reviews") or 0)))[:PER_CAT_OFFERS]


# ---- title-based category check ----------------------------------------------------------------------------------
# Amazon's department search is loose ("laptop" also returns stands, sleeves, bags), so every offer is re-filed by what its
# TITLE says. First matching rule wins. No match = keep the category it was found in.
_LAPTOP_WORD = re.compile(r"\b(laptop|notebook|macbook|chromebook|thinkpad|ideapad|vivobook|zenbook|inspiron|pavilion|victus|thinkbook)\b")
_NOT_A_LAPTOP = re.compile(r"\b(stand|bag|bags|backpack|sleeve|case|cover|skin|charger|adapter|cooling|pad|mouse|keyboard|holder|riser|cable|dock|hub|"
                           r"protector|guard|sticker|organi[sz]er|lock|table|desk|tray|cleaner|lamp|brief|monitor|projector|tablet|portable)\b")
TITLE_RULES = [
    ("tools_auto",      r"\bcar\b|\bcar\d+|\bbike\b|motorcycle|\bhelmet\b|dash ?cam|\btyre|windshield|sun ?shade|automotive|\bcar[- ]"),
    ("pets",            r"\b(dogs?|cats?|puppy|kitten|pets?)\b"),
    ("hobby_music",     r"guitar|ukulele|piano|harmonica|\bflute\b|violin|\btabla\b|\bdrums?\b|karaoke|casio"),
    ("home_kitchen",    r"washing machine|refrigerator|\bfridge\b|mixer|grinder|cooker|kettle|vacuum|air ?fryer|induction|lunch ?(box|bag)|"
                        r"\bbottle|flask|coffee|descaler|gasket|cookware|\bmop\b|chimney|geyser|water purifier|toaster|\boven\b|blender|juicer|"
                        r"dishwasher|\bpan\b|\bpot\b|casserole|tiffin|dinner ?set|steel (glass|plate)"),
    ("gaming",          r"gaming|gamepad|joystick|playstation|\bps[45]\b|xbox|nintendo|\bcontroller\b"),
    ("furniture_decor", r"table lamp|study lamp|night lamp"),
    ("accessories",     r"keyboard|\bmouse\b|mouse ?pad|power ?bank|laptop (stand|sleeve|riser|cooling)|sleeve|webcam|usb hub|\bcable\b|charger|adapter|"
                        r"pen ?drive|hard ?disk|\bssd\b|micro ?sd|memory card|card reader|monitor (arm|mount|stand)|wall mount|bracket|cooling pad|"
                        r"magsafe|(phone|mobile)\s(pouch|holster|holder|case|cover)|(pouch|holster).{0,30}phone|screen ?guard|tempered"),
    ("monitors",        r"\bmonitors?\b|\btv\b|television|projector"),
    ("electronics",     r"earbuds?|headphones?|earphones?|neckband|\btws\b|speakers?|soundbar|dolby|home theat|\bcamera\b"),
    ("mobiles",         r"smartphones?|smart phone|\b5g\b|iphone|smart ?watch|mobile phone"),
    ("smart_home",      r"smart (plug|bulb|light|lock|switch|speaker)|alexa|echo dot|cctv|security camera|video doorbell|extension board|surge"),
    ("ladies",          r"\b(kurtis?|kurta sets?|sarees?|saris?|lehengas?|dupattas?|palazzos?|salwar|anarkali|gowns?|leggings?|jeggings?|tunics?|"
                        r"crop tops?|maxi|midi|skirts?|kaftan|co-?ord sets?|blouse|shrugs?|jumpsuits?|earrings?|necklaces?|bangles?|jhumkas?|"
                        r"mangalsutra|anklets?|chokers?|nose pins?|handbags?|clutch|sling bags?|hair clips?|hair bands?|scrunchies?|bindi)\b|"
                        r"\b(women'?s?|womens|ladies)\b.{0,40}\b(dress|tops?|shirts?|t-?shirts?|jeans|jackets?|sweaters?|cardigans?|shoes?|"
                        r"sandals?|heels|flats|bellies|watch|wallet|bags?|tote)\b|"
                        r"\b(dress|tops?|jeans|shoes|sandals|watch|bags?)\b.{0,25}\b(for )?(women|womens|ladies)\b"),
    ("beauty_health",   r"serum|shampoo|lotion|cream|\bsoap\b|moisturi[sz]|sunscreen|face ?wash|hair|\bskin\b|trimmer|razor|perfume|deodorant|"
                        r"lipstick|kajal|vitamin|supplement|toothpaste|face mask"),
    ("office_books",    r"stationery|\bpens?\b|diary|\bnovels?\b|\bbooks?\b|\bhow to\b|laminat|shredder|marker|stapler|writing tablet|bulletin board|notice board|paperback|hardcover"),
    ("toys_kids",       r"\btoys?\b|\bkids?\b|\bbaby\b|scooter|\bdoll\b|puzzle|stroller|diaper"),
    ("fitness_outdoor", r"\byoga\b|dumbbell|\bgym\b|treadmill|bicycle|fitness|resistance band|skipping|trekking|camping|\btent\b|cricket|badminton|football"),
    ("furniture_decor", r"\bshelf|\brack\b|\bvase|decor|\bstool|ottoman|\bsofa|\bchair|\btable\b|\bbed\b|mattress|curtain|\blamp|night light|wall art|"
                        r"photo frame|planter|\bplants?\b|storage|cabinet|wardrobe|organi[sz]er|\bmirror|cushion|bedsheet|moss stick"),
    ("fashion",         r"saree|kurta|kurti|shirt|jeans|\bdress\b|shoes?|sneaker|sandal|slipper|\bwatch\b|wallet|\bbelt\b|jacket|hoodie|trouser|"
                        r"leggings|\bbags?\b|backpack|handbag|trolley|luggage|duffel|analog|quartz|chronograph"),
]
_TITLE_RULES = [(c, re.compile(rx)) for c, rx in TITLE_RULES]


def classify(item):
    """Category key the TITLE points to, or None (= no opinion, keep the found category)."""
    t = (item.get("name") or "").lower().replace("cable world", "")      # brand name, not a cable
    if not t:
        return None
    try:
        price = float(item.get("now") or 0)
    except (TypeError, ValueError):
        price = 0
    if _LAPTOP_WORD.search(t) and not _NOT_A_LAPTOP.search(t) and price >= 12000:
        return "laptops"
    for cat, rx in _TITLE_RULES:
        if rx.search(t):
            return cat
    return None


def _usable_name(n):
    n = (n or "").strip()
    if not n or "terms and conditions" in n.lower():
        return False
    return " " in n or len(n) > 15                         # a bare brand token ("ATHCO") is a scrape miss, not a product title


def regroup(cats):
    """Re-file every offer by title; dedupe by asin; each category sorted best discount first and capped."""
    flat = {}
    for cat, lst in (cats or {}).items():
        for it in lst:
            if not _usable_name(it.get("name")):
                continue
            new = classify(it)
            if new is None and cat in ("laptops", "ladies"):
                continue                                   # "laptops"/"ladies" only hold titles that really say so (a bare brand title like "Ratnavali Jewels" gives the reel script nothing) - cannot verify, so drop
            new = new or cat
            if new in CAT_LABEL:
                ic, lab = CAT_LABEL[new]
                it = dict(it, cat=new, label=lab, icon=ic)
            old = flat.get(it["asin"])
            if not old or (it.get("pct") or 0) > (old.get("pct") or 0):
                flat[it["asin"]] = it
    out = {c: [] for c in cats or {}}
    for it in flat.values():
        out.setdefault(it["cat"], []).append(it)
    return {c: sorted(l, key=lambda x: (-(x.get("pct") or 0), -(x.get("reviews") or 0)))[:PER_CAT_OFFERS] for c, l in out.items()}



def _drop_stale(cats):
    cutoff = time.time() - FRESH_H * 3600
    return {c: [i for i in lst if float(i.get("t") or 0) >= cutoff] for c, lst in cats.items()}


def run_offers(am, force=False):
    data = read_json(OFFERS_PATH, {})
    cats = dict(data.get("cats") or {})
    swept = dict(data.get("swept") or {})
    only = (os.environ.get("SCAN_ONLY") or "").strip()
    due = [c for c, _, _ in CATEGORIES if (not only or c == only) and (force or age_h(swept.get(c)) >= SWEEP_EVERY_H)]
    due.sort(key=lambda c: age_h(swept.get(c)), reverse=True)    # oldest first
    if not due:
        log("offers: nothing due")
        return False
    log("offers due:", ", ".join(due))
    changed = False
    for cat in due:
        if am.blocked() or am.out_of_time():
            log("offers: stopping (blocked or out of time)")
            break
        items = sweep_category(am, cat)
        if items:
            cats[cat] = items
            swept[cat] = iso()
            changed = True
            cats = regroup(cats)
            data = {"v": 3, "updated": iso(), "fresh_h": FRESH_H, "cats": _drop_stale(cats), "swept": swept}
            write_json(OFFERS_PATH, data)               # write after every category: a cancelled run loses nothing
    return changed


def selftest(am):
    log("SELFTEST - nothing is written")
    bad = 0
    for cat, scopes in SCOPES.items():
        for alias, kw in scopes:
            if am.blocked():
                log("blocked - stop")
                return
            cards = am.search(kw, alias, 1, MIN_PCT, True)
            offers = sum(1 for c in cards if card_to_item(c))
            log(f"{cat:16s} i={alias:20s} kw={kw!s:18s} filtered cards={len(cards):3d} with-offer={offers:3d}")
            if not cards:
                bad += 1
    log("scopes with 0 cards:", bad, "(they auto-fall-back to FALLBACK_KW during the sweep)")


def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SCAN_MODE") or "auto").strip().lower()
    log("mode:", mode, "| tag:", TAG or "(none)", "| budget:", BUDGET_MIN, "min")
    feed_due = mode == "feed" or (mode == "auto" and age_h(read_json(HOT_PATH, {}).get("updated")) >= FEED_EVERY_H)
    offers_due = mode in ("offers", "auto")
    if mode == "auto" and not feed_due:
        d = read_json(OFFERS_PATH, {})
        if all(age_h((d.get("swept") or {}).get(c)) < SWEEP_EVERY_H for c, _, _ in CATEGORIES):
            log("nothing due - exit")
            return 0
    am = Amazon()
    try:
        if mode == "selftest":
            selftest(am)
            return 0
        if feed_due:
            build_feed(am)
        if offers_due and not am.blocked() and not am.out_of_time():
            run_offers(am, force=(mode == "offers"))
    finally:
        try:   # always leave a small status file so a run can be diagnosed without opening Actions logs
            write_json(os.path.join(os.path.dirname(HOT_PATH), "scan_status.json"),
                       {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "mode": mode,
                        "captcha_streak": am.captcha_streak, "pages_loaded": am.pages_loaded, "pages": am.trace[:40]})
        except Exception as e:
            log("status write failed:", str(e)[:80])
        am.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
