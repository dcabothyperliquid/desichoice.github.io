"""Probe 2: more direct routes + a real browser (Playwright) on the search page. Writes guides/probe4.json"""
import json, re, time
import requests
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
R = {
 "deals": "https://www.amazon.in/deals",
 "goldbox": "https://www.amazon.in/gp/goldbox",
 "dp": "https://www.amazon.in/dp/B0BDJ6X2Q5",
 "mobile_search": "https://www.amazon.in/s?k=kitchen+tools&rh=p_8%3A25-&ref=nb_sb_noss",
 "movers": "https://www.amazon.in/gp/movers-and-shakers/kitchen",
 "bs_women_clothing": "https://www.amazon.in/gp/bestsellers/apparel",
 "newrel": "https://www.amazon.in/gp/new-releases/kitchen",
}
out = {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "routes": {}}
def info(t):
    title = re.search(r"<title[^>]*>(.*?)</title>", t, re.I | re.S)
    return dict(len=len(t), title=(title.group(1).strip()[:100] if title else ""), search_cards=t.count("s-search-result"),
                asins=len(set(re.findall(r'data-asin="([A-Z0-9]{10})"', t))) or len(set(re.findall(r"/dp/([A-Z0-9]{10})", t))),
                pct_off=len(re.findall(r"\d{1,2}\s?% off", t)), challenge=("triggerInterstitialChallenge" in t), captcha=("Type the characters" in t or "validateCaptcha" in t))
for name, url in R.items():
    rec = {}
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"}, timeout=40)
        rec = dict(http=r.status_code, **info(r.text))
    except Exception as e:
        rec["error"] = str(e)[:150]
    out["routes"][name] = rec; print(name, rec, flush=True); time.sleep(2)
# real browser on the search page
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
        ctx = b.new_context(user_agent=UA, locale="en-IN", viewport={"width": 1366, "height": 900})
        pg = ctx.new_page()
        for name, url in (("pw_search", "https://www.amazon.in/s?k=kitchen+tools&rh=p_8%3A25-"), ("pw_deals", "https://www.amazon.in/deals")):
            rec = {}
            try:
                resp = pg.goto(url, wait_until="domcontentloaded", timeout=45000)
                pg.wait_for_timeout(7000)
                t = pg.content()
                rec = dict(http=resp.status if resp else None, final=pg.url[:100], **info(t))
            except Exception as e:
                rec["error"] = str(e)[:200]
            out["routes"][name] = rec; print(name, rec, flush=True)
        b.close()
except Exception as e:
    out["routes"]["playwright"] = {"error": str(e)[:300]}
json.dump(out, open("guides/probe4.json", "w"), indent=1)
