"""Probe 4: real browser on amazon.in/deals - does scrolling / the Department filter load more deal cards? What XHR does it use? -> guides/probe6.json"""
import json, os, re, time
from playwright.sync_api import sync_playwright

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
out = {"steps": [], "xhr": [], "api_url": "", "api_body_keys": None, "dept": {}}

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
                if "d2b/api/v1/products/search" in r.url and not out["api_url"]:
                    out["api_url"] = r.url
                    try:
                        j = json.loads(body)
                        out["api_body_keys"] = {k: (type(v).__name__ + ":" + str(len(v) if hasattr(v, "__len__") else v)) for k, v in j.items()} if isinstance(j, dict) else type(j).__name__
                    except Exception as e:
                        out["api_body_keys"] = "nonjson " + str(e)[:80]
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
    # call the site's own deals API from inside the page with a department filter (same-origin, exactly what its JS does)
    if out["api_url"]:
        import urllib.parse as up
        u = up.urlparse(out["api_url"]); q = dict(up.parse_qsl(u.query))
        try:
            flt = json.loads(q.get("filters") or "{}")
        except Exception:
            flt = {}
        out["dept"]["filters_base"] = flt
        for name, dep in (("grocery", "2454179031"), ("health", "1350385031"), ("beauty", "1355017031"), ("home_kitchen", "976443031")):
            for start in (0, 30):
                f2 = dict(flt); f2["includedDepartments"] = [dep]
                q2 = dict(q); q2["filters"] = json.dumps(f2, separators=(",", ":")); q2["startIndex"] = str(start)
                url = u._replace(query=up.urlencode(q2)).geturl()
                try:
                    res = page.evaluate("""async (url) => { const r = await fetch(url, {credentials: 'include'}); const t = await r.text();
                        let titles = []; try { const j = JSON.parse(t); const ps = j.products || (j.productSearchResponse && j.productSearchResponse.products) || [];
                        titles = ps.slice(0, 4).map(p => (p.title || '').slice(0, 50)); return {status: r.status, len: t.length, n: ps.length, titles}; } catch (e) { return {status: r.status, len: t.length, err: String(e).slice(0, 80)}; } }""", url)
                except Exception as e:
                    res = {"err": str(e)[:150]}
                out["dept"][f"{name}@{start}"] = res
                page.wait_for_timeout(1500)
    b.close()

os.makedirs("guides", exist_ok=True)
json.dump(out, open("guides/probe6.json", "w"), indent=1)
print(json.dumps(out["steps"], indent=1)[:2500]); print(len(out["xhr"]), "xhr")
