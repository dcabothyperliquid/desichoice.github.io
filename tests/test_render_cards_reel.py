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
