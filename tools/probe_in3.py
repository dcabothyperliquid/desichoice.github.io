"""Probe 3: does amazon.in/deals accept a department filter / pagination param? Writes guides/probe5.json"""
import json, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scan_in as S

DEPT = "2454179031"      # Grocery & Gourmet Foods
B = S.BASE
VARIANTS = {
    "base": B + "/deals",
    "departments": B + "/deals?departments=" + DEPT,
    "department": B + "/deals?department=" + DEPT,
    "rh_n": B + "/deals?rh=n%3A" + DEPT,
    "node": B + "/deals?node=" + DEPT,
    "goldbox_departments": B + "/gp/goldbox?departments=" + DEPT,
    "alldeals_departments": B + "/gp/goldbox/all-deals/?departments=" + DEPT,
    "startIndex30": B + "/deals?startIndex=30",
    "nextIndex30": B + "/deals?nextIndex=30",
    "page2": B + "/deals?page=2",
    "dealsPage2": B + "/deals?dealsPage=2",
}
am = S.Amazon()
out, base = {}, set()
for k, u in VARIANTS.items():
    try:
        r = am.s.get(u, timeout=45)
        cards = S.parse_deal_cards(r.text) if r.status_code == 200 else []
        asins = [c["asin"] for c in cards]
        if k == "base":
            base = set(asins)
        out[k] = {"url": u, "http": r.status_code, "len": len(r.text), "cards": len(asins),
                  "new_vs_base": len(set(asins) - base), "titles": [c["title"][:50] for c in cards[:4]]}
    except Exception as e:
        out[k] = {"url": u, "error": str(e)[:150]}
    time.sleep(2.5)
os.makedirs("guides", exist_ok=True)
json.dump({"time": S.iso(), "results": out}, open("guides/probe5.json", "w"), indent=1)
print(json.dumps(out, indent=1)[:3000])
