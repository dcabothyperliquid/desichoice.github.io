"""Product gallery + video for one ASIN (amazon.in product page via the same translate.goog proxy the scanner uses).

media(asin) -> {"images": [url, ...up to 6 hi-res], "videos": [mp4 url, ...], "bullets": [feature line, ...]}
Never raises: on any failure returns what it has (possibly empty) so the reel falls back to the single feed photo.
"""
import json, re, sys, time

TRANSLATE_HOST = "https://www-amazon-in.translate.goog"
_TR = "_x_tr_sl=auto&_x_tr_tl=en&_x_tr_hl=en"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0.0.0 Safari/537.36")


def fetch_dp(asin, session=None):
    import requests
    s = session or requests.Session()
    s.headers.update({"User-Agent": _UA, "Accept-Language": "en-IN,en;q=0.9"})
    url = f"{TRANSLATE_HOST}/dp/{asin}?{_TR}"
    for attempt in (1, 2):
        try:
            r = s.get(url, timeout=45)
            if r.status_code == 200 and len(r.text) > 50000:
                return r.text
            print(f"  dp {asin}: http {r.status_code} len {len(r.text)}", file=sys.stderr)
        except Exception as e:
            print(f"  dp {asin}: {type(e).__name__} {str(e)[:80]}", file=sys.stderr)
        time.sleep(8)
    return ""


def _big(u):
    """.../I/abc._AC_UL320_.jpg -> .../I/abc.jpg (full size)."""
    return re.sub(r"\._[A-Z0-9_,.\-]+_\.(jpg|jpeg|png|webp)$", r".\1", u or "")


def parse(html):
    imgs, vids, bullets = [], [], []
    # 1) colorImages initial data: "hiRes":"https://m.media-amazon.com/images/I/xxx.jpg"
    for m in re.finditer(r'"hiRes"\s*:\s*"(https:[^"]+)"', html):
        imgs.append(m.group(1))
    if not imgs:
        for m in re.finditer(r'"large"\s*:\s*"(https:[^"]+)"', html):
            imgs.append(m.group(1))
    if not imgs:   # landing image dynamic-image JSON
        m = re.search(r'data-a-dynamic-image="([^"]+)"', html)
        if m:
            try:
                d = json.loads(m.group(1).replace("&quot;", '"'))
                imgs += sorted(d, key=lambda k: -(d[k][0] if d[k] else 0))[:1]
            except Exception:
                pass
    # 2) videos: "videos":[{"url":"https://...mp4", ...}]  or any m.media-amazon vse mp4
    for m in re.finditer(r'https:\\?/\\?/m\.media-amazon\.com/images/S/vse-vms-transcoding-artifact-[A-Za-z0-9\-_/\\.]+?\.mp4', html):
        vids.append(m.group(0).replace("\\/", "/"))
    # 3) feature bullets
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for li in soup.select("#feature-bullets li span.a-list-item")[:6]:
            t = li.get_text(" ", strip=True)
            if 8 <= len(t) <= 160:
                bullets.append(t)
    except Exception:
        pass
    seen, out_i = set(), []
    for u in imgs:
        u = _big(u.replace("\\/", "/"))
        if u not in seen and "sprite" not in u and "play-button" not in u:
            seen.add(u)
            out_i.append(u)
    seen, out_v = set(), []
    for u in vids:
        if u not in seen:
            seen.add(u)
            out_v.append(u)
    return {"images": out_i[:6], "videos": out_v[:3], "bullets": bullets}


def media(asin, session=None):
    try:
        html = fetch_dp(asin, session)
        return parse(html) if html else {"images": [], "videos": [], "bullets": []}
    except Exception as e:
        print("media failed:", e, file=sys.stderr)
        return {"images": [], "videos": [], "bullets": []}


if __name__ == "__main__":
    for a in sys.argv[1:]:
        r = media(a)
        print(a, json.dumps({k: (v if k != "bullets" else v[:3]) for k, v in r.items()}, indent=1))
