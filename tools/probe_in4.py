"""Probe 4: real browser on amazon.in/deals - does scrolling / the Department filter load more deal cards? What XHR does it use? -> guides/probe6.json"""
import json, os, re, time
from playwright.sync_api import sync_playwright

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
out = {"steps": [], "xhr": []}

def asins(html):
    return set(re.findall(r"/dp/([A-Z0-9]{10})", html))

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(user_agent=UA, locale="en-IN", viewport={"width": 1280, "height": 900})
    page = ctx.new_page()

    def on_resp(r):
        try:
            rt = r.request.resource_type
            if rt in ("xhr", "fetch"):
                ct = r.headers.get("content-type", "")
                body = ""
                try:
                    body = r.text()
                except Exception:
                    pass
                out["xhr"].append({"t": round(time.time() - t0, 1), "url": r.url[:260], "status": r.status, "ct": ct[:40],
                                   "len": len(body), "asin_count": len(re.findall(r'"asin"', body)),
                                   "method": r.request.method, "post": (r.request.post_data or "")[:300]})
        except Exception:
            pass
    page.on("response", on_resp)
    t0 = time.time()
    page.goto("https://www.amazon.in/deals", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(6000)
    html = page.content()
    out["steps"].append({"step": "loaded", "title": page.title(), "len": len(html), "asins": len(asins(html))})
    for k in range(5):
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(2500)
        html = page.content()
        out["steps"].append({"step": f"scroll{k+1}", "asins": len(asins(html)), "len": len(html)})
    # department filter
    for label in ("Grocery & Gourmet Foods", "Grocery", "Health & Personal Care", "Beauty"):
        try:
            el = page.get_by_text(label, exact=False).first
            el.scroll_into_view_if_needed(timeout=4000)
            el.click(timeout=4000)
            page.wait_for_timeout(5000)
            html = page.content()
            out["steps"].append({"step": "click " + label, "asins": len(asins(html)), "len": len(html), "url": page.url[:200]})
            for k in range(3):
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(2000)
            html = page.content()
            out["steps"].append({"step": "after-scroll " + label, "asins": len(asins(html)), "url": page.url[:200]})
            # first card titles
            out["steps"].append({"step": "titles " + label, "titles": re.findall(r'alt="([^"]{12,70})"', html)[:8]})
            break
        except Exception as e:
            out["steps"].append({"step": "click-fail " + label, "err": str(e)[:150]})
    b.close()

os.makedirs("guides", exist_ok=True)
json.dump(out, open("guides/probe6.json", "w"), indent=1)
print(json.dumps(out["steps"], indent=1)[:2500]); print(len(out["xhr"]), "xhr")
