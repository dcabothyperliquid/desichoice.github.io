"""probe2.py - which FREE no-signup routes can fetch amazon.in from a GitHub runner? Writes guides/probe2.json."""
import json, os, re, time
import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
H = {"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9", "Accept": "text/html,application/xhtml+xml,*/*"}
S = "https://www.amazon.in/s?k=power+bank"
Q = "https%3A%2F%2Fwww.amazon.in%2Fs%3Fk%3Dpower%2Bbank"
TESTS = [
    ("direct_search", S, {}),
    ("direct_dp", "https://www.amazon.in/dp/B0BDHWDR12", {}),
    ("direct_bestsellers", "https://www.amazon.in/gp/bestsellers/electronics", {}),
    ("direct_goldbox", "https://www.amazon.in/gp/goldbox", {}),
    ("direct_mobile_site", "https://www.amazon.in/gp/aw/s?k=power+bank", {}),
    ("jina_reader", "https://r.jina.ai/" + S, {"X-Return-Format": "html"}),
    ("jina_reader_text", "https://r.jina.ai/" + S, {}),
    ("google_translate_proxy", "https://www-amazon-in.translate.goog/s?k=power+bank&_x_tr_sl=en&_x_tr_tl=hi&_x_tr_hl=en", {}),
    ("allorigins", "https://api.allorigins.win/raw?url=" + Q, {}),
    ("corsproxy", "https://corsproxy.io/?" + S, {}),
    ("codetabs", "https://api.codetabs.com/v1/proxy?quest=" + S, {}),
    ("wayback_latest", "https://web.archive.org/web/2/" + S, {}),
]


def main():
    out = []
    for name, url, extra in TESTS:
        r = {"test": name}
        try:
            resp = requests.get(url, headers={**H, **extra}, timeout=40)
            t = resp.text
            r.update(http=resp.status_code, len=len(t),
                     products=len(set(re.findall(r"/dp/([A-Z0-9]{10})", t))),
                     rupee="\u20b9" in t,
                     blocked=("automated access" in t or "validateCaptcha" in t or "Robot Check" in t),
                     sample=re.sub(r"\s+", " ", t[:140]))
        except Exception as e:
            r["error"] = str(e)[:120]
        print(r, flush=True)
        out.append(r)
        time.sleep(2)
    os.makedirs("guides", exist_ok=True)
    json.dump({"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "results": out},
              open("guides/probe2.json", "w"), indent=1)


if __name__ == "__main__":
    main()
