import os, re, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import render_reel as R

ITEMS = [{"price": 189, "list_price": 999, "pct": 81}, {"price": 329, "list_price": 1999, "pct": 84}]
DEVA = re.compile("[\u0900-\u097F]")


class T(unittest.TestCase):
    def test_screen_text_never_devanagari(self):
        # 'Save {S1}' (capital = bola hua Hindi) pehle tofu boxes banata tha; screen text me sirf Rs/% aana chahiye
        out = R.fill_screen("Save {S1}", ITEMS)
        self.assertEqual(out, "Save ₹810")
        self.assertFalse(DEVA.search(R.fill_screen("Save {S2} today", ITEMS)))

    def test_short_cuts_on_word_boundary(self):
        t = R.short("CraftVatika Wax Tealight Candles for Diwali Decoration Long Burning", 26)
        self.assertTrue(t.endswith("…"))
        self.assertNotIn("Tealight C…", t)          # shabd ke beech me nahi kaatna
        self.assertEqual(R.short("Short Title", 26), "Short Title")

    def test_length_governor_bounds(self):
        mk = lambda secs: {"audio": None, "min": secs, "pk": 0}
        long_shots = [mk(6.0) for _ in range(11)]        # ~ 60s+
        R.fit_length(long_shots)                          # audio nahi -> voice tez nahi ho sakti, par chhoti wali side kaam karni chahiye
        short_shots = [mk(2.0) for _ in range(11)]
        R.fit_length(short_shots)
        self.assertGreaterEqual(R._est_total(short_shots), R.MIN_TOTAL - 0.1)

    def test_xfade_cut_not_single_frame(self):
        self.assertGreaterEqual(R.XF_CUT, 0.08)           # 0.04 (1 frame) par ffmpeg 6.1 xfade video kaat deta tha


if __name__ == "__main__":
    unittest.main()
