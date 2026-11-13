import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import render_cards_reel as C
from PIL import Image


class T(unittest.TestCase):
    def test_short_title_is_punchy_and_never_dangling(self):
        self.assertEqual(C.short_title("GRECIILOOKS Track Pants for Woman | Parachute Pant for Women Baggy"), "GRECIILOOKS Track Pants")
        self.assertEqual(C.short_title('Midwest Calming Donut Bed for Medium Dogs & Cats – 26"x26"x6"'), "Midwest Calming Donut Bed")
        t = C.short_title("AUTO SNAP Headlight Foglight Car LED Bulbs 220W/22000 Lumens Super")
        self.assertLessEqual(len(t), 32)
        self.assertNotIn(t.split()[-1].lower(), C._STOP)
        self.assertEqual(C.short_title(""), "")

    def test_hook_uses_real_prices(self):
        self.assertEqual(C.hook_text([{"price": 649}, {"price": 649}, {"price": 599}]), "Teeno ₹650 ke andar")
        self.assertEqual(C.hook_text([{"price": 649}, {"price": 3058}]), "₹649 se shuru!")       # far apart -> no false 'andar' claim
        self.assertEqual(C.hook_text([]), "Aaj ki best deals")

    def test_autocrop_removes_white_margin_but_keeps_plain_photos(self):
        im = Image.new("RGB", (400, 400), "white")
        im.paste(Image.new("RGB", (100, 100), "black"), (150, 150))
        out = C.autocrop(im)
        self.assertLess(out.width, 200)
        self.assertEqual(C.autocrop(Image.new("RGB", (50, 50), "white")).size, (50, 50))

    def test_hires_only_rewrites_amazon_size_token(self):
        self.assertEqual(C.hires("https://m.media-amazon.com/images/I/71abc._SX300_.jpg"), "https://m.media-amazon.com/images/I/71abc._SL1200_.jpg")
        self.assertEqual(C.hires("file:///tmp/a.png"), "file:///tmp/a.png")

    def test_no_swipe_cta_on_a_reel_and_pace_is_fast(self):
        src = open(C.__file__).read()
        self.assertNotIn("Swipe karke", src)                                  # reels cannot be swiped
        total = C.T_INTRO + 3 * C.T_ITEM + C.T_OUTRO + C.T_X
        self.assertTrue(10 <= total <= 14, total)


if __name__ == "__main__":
    unittest.main()


class Motion(unittest.TestCase):
    ITEM = {"asin": "A", "title": "Test Product for Home", "price": 649, "list_price": 1999, "pct": 68, "rating": 4.1, "reviews": 314}

    def photos(self):
        return [Image.new("RGB", (600, 500), "white") for _ in range(3)]

    def test_easing_bounds(self):
        self.assertEqual(C.prog(None, 1, 1), 1.0)
        self.assertEqual(C.prog(0.0, 1, 1), 0.0)
        self.assertEqual(C.prog(5.0, 1, 1), 1.0)
        self.assertGreater(C.e_back(0.8), 1.0)                      # overshoot = pop

    def test_all_slides_render_at_start_and_end(self):
        items = [dict(self.ITEM, asin=str(i)) for i in range(3)]
        slides = [C.HookSlide(items, self.photos(), "Daily Essentials"),
                  C.ProductSlide(items[0], self.photos()[0], "Daily Essentials", 1, 3, 2.95),
                  C.OutroSlide(self.photos()[0], "Daily Essentials", 3)]
        for sl in slides:
            for t in (0.0, 0.5, 2.0, None):
                self.assertEqual(sl.frame(t).size, (C.W, C.H))

    def test_hook_text_fits_screen_even_at_pop_scale(self):
        h = C.HookSlide([{"price": 649, "pct": 68}, {"price": 649, "pct": 68}, {"price": 599, "pct": 69}], self.photos(), "Daily Essentials")
        self.assertLessEqual(h.hook.width * 1.1, C.W)

    def test_price_counts_down_to_the_real_price(self):
        sl = C.ProductSlide(self.ITEM, self.photos()[0], "Daily Essentials", 1, 3, 2.95)
        early, late = sl.price_strip(0.35).tobytes(), sl.price_strip(None).tobytes()
        self.assertNotEqual(early, late)                            # early frame shows the MRP, final frame the deal price
