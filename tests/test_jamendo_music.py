import os, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import jamendo_music as jm


def T(i, lic, dur=120):
    return {"id": str(i), "name": f"T{i}", "artist_name": "A", "duration": dur, "license_ccurl": lic,
            "audiodownload": f"http://x/{i}.mp3"}


BY = "https://creativecommons.org/licenses/by/3.0/"


class Resp:
    def __init__(self, js=None, content=b"x" * 60000):
        self.js, self.content = js, content
    def raise_for_status(self): pass
    def json(self): return self.js
    def iter_content(self, n): yield self.content


class JamendoTests(unittest.TestCase):
    def test_license_filter(self):
        self.assertTrue(jm.license_ok(BY))
        self.assertTrue(jm.license_ok("https://creativecommons.org/publicdomain/zero/1.0/"))
        for bad in ("https://creativecommons.org/licenses/by-nc/3.0/", "https://creativecommons.org/licenses/by-sa/3.0/",
                    "https://creativecommons.org/licenses/by-nd/3.0/", "https://creativecommons.org/licenses/by-nc-sa/3.0/", "", None):
            self.assertFalse(jm.license_ok(bad), bad)

    def test_usable_filters_duration_and_license(self):
        r = jm.usable([T(1, BY), T(2, BY, 20), T(3, "https://creativecommons.org/licenses/by-nc/3.0/")])
        self.assertEqual([t["id"] for t in r], ["1"])

    def test_pick_no_key_returns_none(self):
        with mock.patch.dict(os.environ, {"JAMENDO_CLIENT_ID": ""}):
            self.assertIsNone(jm.pick(tempfile.mkdtemp(), "s"))

    def test_pick_downloads_and_varies_per_seed(self):
        tracks = [T(i, BY) for i in range(1, 30)]
        def fake_get(url, params=None, **kw):
            return Resp({"headers": {"status": "success"}, "results": tracks}) if params else Resp()
        with mock.patch.dict(os.environ, {"JAMENDO_CLIENT_ID": "k"}), mock.patch.object(jm.requests, "get", fake_get):
            ids = {jm.pick(tempfile.mkdtemp(), f"job{n}")["id"] for n in range(12)}
            r = jm.pick(tempfile.mkdtemp(), "job0")
        self.assertGreater(len(ids), 3)
        self.assertTrue(os.path.exists(r["path"]))
        self.assertIn("CC BY", r["credit"])

    def test_api_error_returns_none(self):
        def boom(*a, **k): raise RuntimeError("down")
        with mock.patch.dict(os.environ, {"JAMENDO_CLIENT_ID": "k"}), mock.patch.object(jm.requests, "get", boom):
            self.assertIsNone(jm.pick(tempfile.mkdtemp(), "s"))


if __name__ == "__main__":
    unittest.main()
