import os, sys, unittest
from datetime import datetime, timezone
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import scan_in as S


def card(asin="B0ABCDEFGH", title="Tata Tea Gold 1 kg", price="₹399", was="₹520", rating="4.4 out of 5 stars", reviews="12,345",
         img="https://m.media-amazon.com/images/I/71abc._AC_UY218_.jpg", text="Tata Tea\n-23%\nLimited time deal"):
    return {"asin": asin, "title": title, "price": price, "was": was, "rating": rating, "reviews": reviews, "img": img, "text": text}


class T(unittest.TestCase):
    def test_item(self):
        it = S.card_to_item(card(), now=1000)
        self.assertEqual(it["pct"], 23); self.assertEqual(it["now"], "399"); self.assertEqual(it["was"], "520")
        self.assertEqual(it["reviews"], 12345); self.assertEqual(it["rating"], 4.4); self.assertEqual(it["deal"], "Limited time deal")
        self.assertEqual(it["img"], "https://m.media-amazon.com/images/I/71abc.jpg")
        self.assertTrue(it["link"].startswith("https://www.amazon.in/dp/B0ABCDEFGH"))

    def test_sponsored_and_bad(self):
        self.assertIsNone(S.card_to_item(card(text="Sponsored\nTata")))
        self.assertIsNone(S.card_to_item(card(asin="")))
        self.assertIsNone(S.card_to_item(card(price="")))
        self.assertIsNone(S.card_to_item(card(was="", text="no discount here")))

    def test_pct_from_text_when_no_was(self):
        it = S.card_to_item(card(was="", text="Boat Airdopes (40% off)"))
        self.assertEqual(it["pct"], 40); self.assertGreater(float(it["was"]), float(it["now"]))

    def test_gates(self):
        ok = S.card_to_item(card())
        self.assertTrue(S.credible(ok, 10, 49))
        self.assertFalse(S.credible(ok, 30, 49))                       # discount too small
        self.assertFalse(S.credible(S.card_to_item(card(rating="3.5 out of 5 stars")), 10, 49))
        self.assertFalse(S.credible(S.card_to_item(card(reviews="12")), 10, 49))
        self.assertFalse(S.credible(S.card_to_item(card(price="₹100", was="₹900", reviews="100")), 10, 49))   # 89% / suspicious
        self.assertFalse(S.credible(ok, 10, 5000))                     # too cheap for this list

    def test_parsers(self):
        self.assertEqual(S.parse_reviews("1.2K"), 1200); self.assertEqual(S.parse_reviews("(3,456)"), 3456); self.assertEqual(S.parse_reviews(""), 0)
        self.assertEqual(S.parse_rating("4.3 out of 5 stars"), 4.3); self.assertEqual(S.parse_rating(""), 0.0)
        self.assertLessEqual(len(S.short_name("word " * 40)), S.NAME_MAX)
        self.assertEqual(S.tier(500), "budget"); self.assertEqual(S.tier(5000), "mid"); self.assertEqual(S.tier(50000), "premium")

    def test_themes(self):
        n = datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc)
        keys = [k for k, *_ in S.active_themes(n)]
        self.assertIn("diwali", keys); self.assertIn("winter", keys); self.assertNotIn("summer", keys)
        self.assertEqual(keys[0], "diwali")                           # soonest-ending first
        jan = [k for k, *_ in S.active_themes(datetime(2027, 1, 10, tzinfo=timezone.utc))]
        self.assertIn("winter", jan); self.assertIn("republic", jan)  # winter wraps the new year
        chh = [k for k, *_ in S.active_themes(datetime(2026, 11, 10, tzinfo=timezone.utc))]
        self.assertIn("chhath", chh); self.assertNotIn("diwali", chh)
        xm = [k for k, *_ in S.active_themes(datetime(2026, 12, 22, tzinfo=timezone.utc))]
        self.assertIn("christmas", xm); self.assertIn("newyear", xm)
        self.assertIn("newyear", [k for k, *_ in S.active_themes(datetime(2027, 1, 1, tzinfo=timezone.utc))])  # wraps the year

    def test_ladies_needs_product_title(self):
        mk = lambda a, n: {"asin": a, "name": n, "pct": 60, "now": 499, "was": 1299, "reviews": 900, "rating": 4.3, "t": 1}
        out = S.regroup({"ladies": [mk("A1", "Ratnavali Jewels"), mk("A2", "Biba Women Cotton Kurti Set with Dupatta")]})
        self.assertEqual([i["asin"] for i in out["ladies"]], ["A2"])           # brand-only title dropped, real product kept

    def test_ladies_rule(self):
        cl = lambda n: S.classify({"name": n, "now": "999"})
        self.assertEqual(cl("Biba Women Cotton Kurti Set with Dupatta"), "ladies")
        self.assertEqual(cl("Women's Gold Plated Jhumka Earrings"), "ladies")
        self.assertEqual(cl("Fabindia Women Printed Maxi Dress"), "ladies_fashion")      # dresses / shoes / sandals live in Ladies Fashion now
        self.assertEqual(cl("Women Heels Block Sandals for Party"), "ladies_fashion")
        self.assertEqual(cl("Women Dress Material Unstitched Cotton Kurti"), "ladies")      # dress material is not a dress
        self.assertEqual(cl("Baby Girl Frock Dress 2-3 Years"), "kids_mom")
        self.assertEqual(cl("Kids Building Blocks Toy Set"), "toys_kids")
        self.assertEqual(cl("Pink Hair Clips Set for Women"), "ladies")
        self.assertNotEqual(cl("Garnier Vitamin C Face Serum"), "ladies")          # beauty stays beauty
        self.assertNotEqual(cl("Men Running Shoes Sports"), "ladies")

    def test_round_robin(self):
        mk = lambda a, p: {"asin": a, "pct": p}
        out = S.round_robin({"x": [mk("1", 50), mk("2", 40)], "y": [mk("3", 60)], "z": []}, 3)
        self.assertEqual([i["asin"] for i in out], ["3", "1", "2"])

    def test_clean_and_scopes(self):
        it = S.clean(S.card_to_item(card(text="x -23%")), cat="tea")
        self.assertNotIn("_price", it); self.assertEqual(it["cat"], "tea"); self.assertIn("tier", it)
        self.assertEqual(len(S.CATEGORIES), 20)
        self.assertEqual(set(S.SCOPES), {k for k, _, _ in S.CATEGORIES})
        self.assertEqual(set(S.FALLBACK_KW), set(S.SCOPES))

    def test_accessory_filter(self):
        self.assertTrue(S.ACCESSORY.search("Dog bowl stand"))
        self.assertFalse(S.ACCESSORY.search("Tata Tea Gold 1 kg"))



class TitleClassify(unittest.TestCase):
    def cl(self, name, now="999"):
        return S.classify({"name": name, "now": now})

    def test_laptop_accessories_are_not_laptops(self):
        self.assertEqual(self.cl("Tygot Aluminium Laptop Stand Adjustable 6 Angles", "299"), "accessories")
        self.assertEqual(self.cl("Dyazo 13.3 Inch Laptop Sleeve Case with Handle", "299"), "accessories")
        self.assertEqual(self.cl("Safari Omega Pro Laptop Backpack With Raincover", "729"), "fashion")
        self.assertEqual(self.cl("HAMMONDS FLYCATCHER Genuine Leather Office Bag for Men - 15.6 Inch Laptop", "2559"), "fashion")

    def test_real_laptop(self):
        self.assertEqual(self.cl("HP Pavilion 15 Laptop, Intel Core i5, 16GB RAM", "55990"), "laptops")
        self.assertNotEqual(self.cl("Some Laptop Table", "55990"), "laptops")

    def test_misc(self):
        self.assertEqual(self.cl("Safari Genius Alley Set of 3 Trolley Bags Hard Case"), "fashion")
        self.assertEqual(self.cl("Spigen Floating Waterproof Phone Pouch (2 Pack)"), "accessories")
        self.assertEqual(self.cl("Zebronics Juke Bar 9330 500W Soundbar"), "electronics")
        self.assertEqual(self.cl("WALI Dual Monitor Wall Mount, Single to Double"), "accessories")
        self.assertEqual(self.cl("Noise Pulse Go Buzz Smart Watch with Bluetooth Calling"), "mobiles")
        self.assertIsNone(self.cl("Dell SE2225HM 21.5in FHD, 100Hz Refresh, VA Panel"))

    def test_regroup_moves_and_dedupes(self):
        a = {"asin": "A1", "name": "Tygot Laptop Stand", "now": "299", "pct": 85, "reviews": 10, "cat": "laptops"}
        b = {"asin": "A1", "name": "Tygot Laptop Stand", "now": "299", "pct": 80, "reviews": 10, "cat": "electronics"}
        out = S.regroup({"laptops": [a], "electronics": [b]})
        self.assertEqual([i["asin"] for i in out["accessories"]], ["A1"])
        self.assertEqual(out["accessories"][0]["label"], "Accessories")
        self.assertEqual(out["laptops"], [])


if __name__ == "__main__":
    unittest.main()
