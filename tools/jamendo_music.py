"""Jamendo background music for reels: a different upbeat instrumental track per reel, fresh from Jamendo's weekly-popular list.

pick(dest_dir, seed) -> {"path", "credit", "id"} or None.  None (no key / API down / no usable track) = the renderer keeps
its old tools/music/bed.m4a, so a Jamendo outage never breaks a reel.
Env: JAMENDO_CLIENT_ID (GitHub Actions secret).
Licence filter: only CC BY and CC0 / public domain (no NC = no non-commercial, no ND, no SA). CC BY needs credit, so `credit`
is returned and written to meta.json (Xbot adds it to the post caption).
"""
import hashlib, json, os, re, sys, time
from datetime import datetime, timedelta, timezone
import requests

API = "https://api.jamendo.com/v3.0/tracks/"
MIN_SEC, MAX_SEC = 60, 400          # long enough to cover a 30 s reel
ORDERS = ("popularity_week", "popularity_month", "popularity_total")
TAGS = ("upbeat", "happy", "energetic", "dance", "pop", "groove", "funk", "electronic", "positive", "fun", "summer", "party")
HERE = os.path.dirname(os.path.abspath(__file__))
USED_PATH = os.path.join(HERE, "music", "used.json")          # {track_id: iso time last used}, committed back to the repo by the workflow
NOREPEAT_DAYS = int(os.environ.get("REEL_MUSIC_NOREPEAT_DAYS") or 35)   # a track is not used again for at least this long
FRESH_TARGET = 25                                              # stop querying once this many not-recently-used tracks are known   # one tag per query, results merged (avoids AND/OR ambiguity of multi-tag search)


def license_ok(url):
    u = (url or "").lower()
    if not u:
        return False
    if "publicdomain/zero" in u or "/zero/" in u:
        return True
    m = re.search(r"/licenses/([a-z\-]+)/", u)
    return bool(m) and m.group(1) == "by"


def _query(cid, tags, order, offset=0):
    r = requests.get(API, params={
        "client_id": cid, "format": "json", "limit": 200, "offset": offset, "order": order, "fuzzytags": tags,
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


def load_used(path=None):
    try:
        d = json.load(open(path or USED_PATH))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def recent_ids(used, days=None, now=None):
    now = now or datetime.now(timezone.utc)
    cut = now - timedelta(days=NOREPEAT_DAYS if days is None else days)
    out = set()
    for tid, ts in used.items():
        try:
            if datetime.fromisoformat(str(ts).replace("Z", "+00:00")) >= cut:
                out.add(str(tid))
        except ValueError:
            out.add(str(tid))                      # unreadable time -> treat as recent (safe side)
    return out


def record(meta_path, used_path=None, now=None):
    """After a successful render: remember meta.json's music_id (and drop entries older than 120 days). -> id or None."""
    used_path = used_path or USED_PATH
    try:
        mid = json.load(open(meta_path)).get("music_id")
    except Exception:
        mid = None
    used = load_used(used_path)
    now = now or datetime.now(timezone.utc)
    if mid:
        used[str(mid)] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    used = {k: v for k, v in used.items() if k in recent_ids(used, 120, now)}
    os.makedirs(os.path.dirname(used_path), exist_ok=True)
    json.dump(used, open(used_path, "w"), indent=0, sort_keys=True)
    return mid


def choose(pool, used, seed, now=None):
    """Pick one track from pool: not used within NOREPEAT_DAYS (hash of seed picks which); if every known track is recent,
    the least recently used one (and a loud warning), so a reel never ends up without music because of the no-repeat rule."""
    recent = recent_ids(used, None, now)
    fresh = [t for t in pool if str(t.get("id")) not in recent]
    cand = fresh
    if not cand:
        print(f"jamendo: WARNING pool exhausted ({len(pool)} tracks all used in the last {NOREPEAT_DAYS} days) -> least recently used", file=sys.stderr)
        cand = sorted(pool, key=lambda t: str(used.get(str(t.get("id")), "")))[:max(1, len(pool) // 3)]
    return sorted(cand, key=lambda t: hashlib.sha1(f"{seed}:{t.get('id')}".encode()).hexdigest())


def pick(dest_dir, seed=None):
    cid = (os.environ.get("JAMENDO_CLIENT_ID") or "").strip()
    if not cid:
        print("jamendo: JAMENDO_CLIENT_ID not set -> old bed music", file=sys.stderr)
        return None
    seed = str(seed or time.time())
    used = load_used()
    recent = recent_ids(used)
    try:
        seen, fresh = {}, 0
        for tag in TAGS:
            for order in ORDERS:
                for offset in (0, 200):
                    try:
                        res = _query(cid, tag, order, offset)
                    except Exception as e:
                        print(f"jamendo query {tag}/{order}/{offset} failed: {e}", file=sys.stderr)
                        break
                    for t in usable(res):
                        tid = str(t.get("id"))
                        if tid not in seen:
                            seen[tid] = t
                            fresh += tid not in recent
                    if len(res) < 200:
                        break
                if fresh >= FRESH_TARGET:
                    break
            if fresh >= FRESH_TARGET:
                break
        pool = list(seen.values())
        if not pool:
            print("jamendo: no usable track -> old bed music", file=sys.stderr)
            return None
        print(f"jamendo: pool {len(pool)} tracks, {fresh} not used in the last {NOREPEAT_DAYS} days")
        os.makedirs(dest_dir, exist_ok=True)
        for t in choose(pool, used, seed)[:4]:                 # a few tries if one download fails
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


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--record":
        print("recorded music id:", record(sys.argv[2]))
