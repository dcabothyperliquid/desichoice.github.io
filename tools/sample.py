"""sample.py - saves raw HTML of the routes that work, to guides/samples/ (temporary, for writing the parser)."""
import os, requests
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
H = {"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9", "Accept": "text/html,application/xhtml+xml,*/*"}
T = {
 "bestsellers_electronics": "https://www.amazon.in/gp/bestsellers/electronics",
 "bestsellers_kitchen_p2": "https://www.amazon.in/gp/bestsellers/kitchen/ref=zg_bs_pg_2?ie=UTF8&pg=2",
 "movers_electronics": "https://www.amazon.in/gp/movers-and-shakers/electronics",
 "goldbox": "https://www.amazon.in/gp/goldbox",
 "deals": "https://www.amazon.in/deals",
 "translate_search_hi": "https://www-amazon-in.translate.goog/s?k=power+bank&_x_tr_sl=en&_x_tr_tl=hi&_x_tr_hl=en",
 "translate_search_off": "https://www-amazon-in.translate.goog/s?k=power+bank&rh=p_8%3A25-&_x_tr_sl=auto&_x_tr_tl=en&_x_tr_hl=en",
}
os.makedirs("guides/samples", exist_ok=True)
for n, u in T.items():
    try:
        r = requests.get(u, headers=H, timeout=40)
        open(f"guides/samples/{n}.html", "w", encoding="utf-8").write(r.text[:900000])
        print(n, r.status_code, len(r.text), flush=True)
    except Exception as e:
        print(n, "err", str(e)[:100], flush=True)
