"""Jamendo background music for reels: a different upbeat instrumental track per reel, fresh from Jamendo's weekly-popular list.

pick(dest_dir, seed) -> {"path", "credit", "id"} or None.  None (no key / API down / no usable track) = the renderer keeps
its old tools/music/bed.m4a, so a Jamendo outage never breaks a reel.
Env: JAMENDO_CLIENT_ID (GitHub Actions secret).
Licence filter: only CC BY and CC0 / public domain (no NC = no non-commercial, no ND, no SA). CC BY needs credit, so `credit`
is returned and written to meta.json (Xbot adds it to the post caption).
"""
import hashlib, os, re, sys, time
import requests

API = "https://api.jamendo.com/v3.0/tracks/"
MIN_SEC, MAX_SEC = 60, 400          # long enough to cover a 30 s reel
ORDERS = ("popularity_week", "popularity_month")
TAGS = ("upbeat", "happy", "energetic", "dance", "pop", "groove")   # one tag per query, results merged (avoids AND/OR ambiguity of multi-tag search)


def license_ok(url):
    u = (url or "").lower()
    if not u:
        return False
    if "publicdomain/zero" in u or "/zero/" in u:
        return True
    m = re.search(r"/licenses/([a-z\-]+)/", u)
    return bool(m) and m.group(1) == "by"


def _query(cid, tags, order):
    r = requests.get(API, params={
        "client_id": cid, "format": "json", "limit": 200, "order": order, "fuzzytags": tags,
        "vocalinstrumental": "instrumental",
    }, timeout=25)
    r.raise_for_status()
    j = r.json()
    if (j.get("headers") or {}).get("status") not in (None, "success"):
        raise RuntimeError((j.get("headers") or {}).get("error_message") or "jamendo api error")
    return j.get("results") or []


def usable(tracks):
    out = []
    for t in tracks:
        try:
            ok = (MIN_SEC <= int(t.get("duration") or 0) <= MAX_SEC and license_ok(t.get("license_ccurl"))
                  and t.get("audiodownload_allowed") is not False
                  and (t.get("audiodownload") or t.get("audio")))
        except (TypeError, ValueError):
            ok = False
        if ok:
            out.append(t)
    return out


def credit_line(t):
    return f"🎵 Music: {t.get('name', '').strip()} - {t.get('artist_name', '').strip()} (Jamendo, CC BY)"


def pick(dest_dir, seed=None):
    cid = (os.environ.get("JAMENDO_CLIENT_ID") or "").strip()
    if not cid:
        print("jamendo: JAMENDO_CLIENT_ID not set -> old bed music", file=sys.stderr)
        return None
    seed = str(seed or time.time())
    pool = []
    try:
        seen = {}
        for tag in TAGS:
            for order in ORDERS:
                try:
                    for t in usable(_query(cid, tag, order)):
                        seen.setdefault(str(t.get("id")), t)
                except Exception as e:
                    print(f"jamendo query {tag}/{order} failed: {e}", file=sys.stderr)
            if len(seen) >= 40:
                break
        pool = list(seen.values())
        if not pool:
            print("jamendo: no usable track -> old bed music", file=sys.stderr)
            return None
        pool.sort(key=lambda t: str(t.get("id")))
        order = sorted(range(len(pool)), key=lambda i: hashlib.sha1(f"{seed}:{pool[i].get('id')}".encode()).hexdigest())
        os.makedirs(dest_dir, exist_ok=True)
        for i in order[:4]:                                   # a few tries if one download fails
            t = pool[i]
            url = t.get("audiodownload") or t.get("audio")
            path = os.path.join(dest_dir, f"jamendo_{t.get('id')}.mp3")
            try:
                r = requests.get(url, timeout=60, stream=True)
                r.raise_for_status()
                with open(path, "wb") as f:
                    for chunk in r.iter_content(65536):
                        f.write(chunk)
                if os.path.getsize(path) < 50_000:
                    raise ValueError("file too small")
                print(f"jamendo: using {t.get('name')} - {t.get('artist_name')} ({t.get('license_ccurl')})")
                return {"path": path, "credit": credit_line(t), "id": str(t.get("id"))}
            except Exception as e:
                print(f"jamendo download {t.get('id')} failed: {e}", file=sys.stderr)
    except Exception as e:
        print(f"jamendo failed: {e}", file=sys.stderr)
    return None
