"""probe.py - tries several browser setups against amazon.in from the runner and writes guides/probe.json (no Actions log needed)."""
import json, os, time
from playwright.sync_api import sync_playwright

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
FLAG = ["--disable-blink-features=AutomationControlled"]
SEARCH = "https://www.amazon.in/s?k=power+bank"


def look(page, url, wait=2500):
    r = {"url": url}
    try:
        resp = page.goto(url, timeout=45000, wait_until="domcontentloaded")
        r["http"] = resp.status if resp else None
        try:
            page.wait_for_selector('div[data-component-type="s-search-result"]', timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(wait)
        html = page.content()
        r["title"] = (page.title() or "")[:80]
        r["html_len"] = len(html)
        r["cards"] = html.count('data-component-type="s-search-result"')
        r["blocked_mark"] = "automated access" in html or "validateCaptcha" in html
    except Exception as e:
        r["error"] = str(e)[:140]
    return r


def run(name, fn):
    t = time.time()
    try:
        out = fn()
    except Exception as e:
        out = {"error": str(e)[:160]}
    out["variant"] = name
    out["secs"] = round(time.time() - t, 1)
    print(name, out, flush=True)
    return out


def main():
    res = []
    with sync_playwright() as pw:
        def v_plain():
            b = pw.chromium.launch(headless=True)
            c = b.new_context(locale="en-IN", user_agent=UA); r = look(c.new_page(), SEARCH); b.close(); return r

        def v_flag():
            b = pw.chromium.launch(headless=True, args=FLAG)
            c = b.new_context(locale="en-IN", user_agent=UA, viewport={"width": 1366, "height": 900})
            r = look(c.new_page(), SEARCH); b.close(); return r

        def v_home_first():
            b = pw.chromium.launch(headless=True, args=FLAG)
            c = b.new_context(locale="en-IN", user_agent=UA, viewport={"width": 1366, "height": 900})
            p = c.new_page(); h = look(p, "https://www.amazon.in/", 3000)
            r = look(p, SEARCH); r["home"] = {k: h.get(k) for k in ("http", "title", "html_len")}; b.close(); return r

        def v_mobile():
            d = pw.devices["Pixel 7"]
            b = pw.chromium.launch(headless=True, args=FLAG)
            c = b.new_context(**d, locale="en-IN"); r = look(c.new_page(), SEARCH); b.close(); return r

        def v_new_headless():
            b = pw.chromium.launch(channel="chromium", headless=True, args=FLAG)
            c = b.new_context(locale="en-IN", user_agent=UA, viewport={"width": 1366, "height": 900})
            r = look(c.new_page(), SEARCH); b.close(); return r

        def v_com_control():
            b = pw.chromium.launch(headless=True, args=FLAG)
            c = b.new_context(locale="en-US", user_agent=UA)
            r = look(c.new_page(), "https://www.amazon.com/s?k=power+bank"); b.close(); return r

        for name, fn in [("in_plain", v_plain), ("in_flag", v_flag), ("in_home_first", v_home_first),
                         ("in_mobile_pixel7", v_mobile), ("in_new_headless", v_new_headless), ("com_control", v_com_control)]:
            res.append(run(name, fn))
            time.sleep(4)
    os.makedirs("guides", exist_ok=True)
    json.dump({"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "results": res},
              open("guides/probe.json", "w"), indent=1)


if __name__ == "__main__":
    main()
