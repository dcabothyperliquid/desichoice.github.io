"""One-off route probe: which way of fetching Amazon India search pages still returns products from a GitHub runner?
Writes guides/probe3.json (status, length, title, first text, #asin cards) for each route."""
import json, re, time, urllib.parse
import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
Q = "k=kitchen+tools&rh=p_8%3A25-"
AM = "https://www.amazon.in/s?" + Q
ROUTES = {
    "direct": AM,
    "translate_now": "https://www-amazon-in.translate.goog/s?" + Q + "&_x_tr_sl=auto&_x_tr_tl=en&_x_tr_hl=en",
    "translate_hi": "https://www-amazon-in.translate.goog/s?" + Q + "&_x_tr_sl=en&_x_tr_tl=hi&_x_tr_hl=hi&_x_tr_pto=wapp",
    "jina": "https://r.jina.ai/" + AM,
    "allorigins": "https://api.allorigins.win/raw?url=" + urllib.parse.quote(AM, safe=""),
    "corsproxy": "https://corsproxy.io/?url=" + urllib.parse.quote(AM, safe=""),
    "codetabs": "https://api.codetabs.com/v1/proxy/?quest=" + urllib.parse.quote(AM, safe=""),
    "bestsellers_direct": "https://www.amazon.in/gp/bestsellers/kitchen",
    "bestsellers_jina": "https://r.jina.ai/https://www.amazon.in/gp/bestsellers/kitchen",
}
out = {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "routes": {}}
for name, url in ROUTES.items():
    rec = {"url": url[:120]}
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"}, timeout=40)
        t = r.text
        title = re.search(r"<title[^>]*>(.*?)</title>", t, re.I | re.S)
        txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))
        rec.update(http=r.status_code, len=len(t), title=(title.group(1).strip()[:120] if title else ""),
                   has_results=("s-search-result" in t), asins=len(set(re.findall(r'data-asin="([A-Z0-9]{10})"', t))) or len(set(re.findall(r"/dp/([A-Z0-9]{10})", t))),
                   text=txt[:300])
    except Exception as e:
        rec["error"] = str(e)[:200]
    out["routes"][name] = rec
    print(name, {k: v for k, v in rec.items() if k not in ("url", "text")}, flush=True)
    time.sleep(2)
json.dump(out, open("guides/probe3.json", "w"), indent=1, ensure_ascii=False)
