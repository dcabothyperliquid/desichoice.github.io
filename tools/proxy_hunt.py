"""proxy_hunt.py - finds FREE Indian proxies that amazon.in actually answers, from the GitHub runner. Writes guides/proxy_probe.json."""
import json, os, re, time, concurrent.futures as cf
import requests

SOURCES = [
    ("proxifly_IN", "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/countries/IN/data.txt"),
    ("proxyscrape_IN", "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=IN&ssl=all&anonymity=all"),
    ("geonode_IN", "https://proxylist.geonode.com/api/proxy-list?limit=200&page=1&sort_by=lastChecked&sort_type=desc&country=IN&protocols=http,https"),
    ("proxylistdownload_IN", "https://www.proxy-list.download/api/v1/get?type=http&country=IN"),
]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
HDR = {"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9", "Accept": "text/html,application/xhtml+xml"}
URL = "https://www.amazon.in/s?k=power+bank"
IP_RX = re.compile(r"(?:(?:https?|socks[45])://)?(\d{1,3}(?:\.\d{1,3}){3}):(\d{2,5})")


def gather():
    cands, report = {}, {}
    for name, url in SOURCES:
        try:
            t = requests.get(url, timeout=20).text
            got = set()
            if name.startswith("geonode"):
                for it in json.loads(t).get("data", []):
                    proto = (it.get("protocols") or ["http"])[0]
                    got.add(("socks5" if "socks5" in proto else "socks4" if "socks4" in proto else "http", it["ip"], str(it["port"])))
            else:
                for line in t.splitlines():
                    m = IP_RX.search(line)
                    if m:
                        proto = "socks5" if "socks5" in line else "socks4" if "socks4" in line else "http"
                        got.add((proto, m.group(1), m.group(2)))
            report[name] = len(got)
            for g in got:
                cands[g] = name
        except Exception as e:
            report[name] = "err: " + str(e)[:60]
    return cands, report


def test(c):
    proto, ip, port = c
    if proto != "http":
        return None                      # requests needs PySocks for socks; keep http only
    p = f"http://{ip}:{port}"
    try:
        r = requests.get(URL, headers=HDR, proxies={"http": p, "https": p}, timeout=12)
        return {"proxy": p, "http": r.status_code, "len": len(r.text),
                "ok": r.status_code == 200 and "s-search-result" in r.text}
    except Exception:
        return None


def main():
    cands, report = gather()
    items = [c for c in cands if c[0] == "http"]
    items = items[:400]
    t0 = time.time()
    results = []
    with cf.ThreadPoolExecutor(60) as ex:
        for r in ex.map(test, items):
            if r:
                results.append(r)
    good = [r for r in results if r["ok"]]
    out = {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "sources": report,
           "tested": len(items), "reachable": len(results), "amazon_200_with_results": len(good),
           "status_counts": {}, "good": good[:15], "secs": round(time.time() - t0)}
    for r in results:
        out["status_counts"][str(r["http"])] = out["status_counts"].get(str(r["http"]), 0) + 1
    os.makedirs("guides", exist_ok=True)
    json.dump(out, open("guides/proxy_probe.json", "w"), indent=1)
    print(json.dumps(out, indent=1)[:1500])


if __name__ == "__main__":
    main()
