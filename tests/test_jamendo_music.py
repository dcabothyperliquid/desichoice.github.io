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

    def test_choose_never_picks_recently_used_track(self):
        from datetime import datetime, timezone, timedelta
        now = datetime(2026, 11, 13, tzinfo=timezone.utc)
        iso = lambda d: (now - timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pool = [T(i, BY) for i in range(1, 11)]
        used = {"1": iso(2), "2": iso(10), "3": iso(34), "4": iso(36), "5": iso(90)}      # 1,2,3 are inside the 35-day window
        for n in range(30):
            first = jm.choose(pool, used, f"seed{n}", now)[0]["id"]
            self.assertNotIn(first, {"1", "2", "3"})

    def test_choose_when_pool_exhausted_uses_least_recently_used(self):
        from datetime import datetime, timezone, timedelta
        now = datetime(2026, 11, 13, tzinfo=timezone.utc)
        pool = [T(i, BY) for i in range(1, 7)]
        used = {str(i): (now - timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%SZ") for i in range(1, 7)}     # all within 35 days
        got = {t["id"] for t in jm.choose(pool, used, "s", now)}
        self.assertTrue(got <= {"6", "5"})                                                 # oldest third only
        self.assertTrue(got)

    def test_record_writes_id_and_prunes_old(self):
        import json
        from datetime import datetime, timezone
        d = tempfile.mkdtemp(); meta = os.path.join(d, "meta.json"); up = os.path.join(d, "used.json")
        json.dump({"music_id": "77"}, open(meta, "w"))
        json.dump({"1": "2020-01-01T00:00:00Z", "2": "2026-11-01T00:00:00Z"}, open(up, "w"))
        self.assertEqual(jm.record(meta, up, datetime(2026, 11, 13, tzinfo=timezone.utc)), "77")
        u = json.load(open(up))
        self.assertEqual(set(u), {"2", "77"})                                              # "1" is older than 120 days -> dropped

    def test_record_without_music_keeps_file_valid(self):
        import json
        d = tempfile.mkdtemp(); meta = os.path.join(d, "meta.json"); up = os.path.join(d, "used.json")
        json.dump({"asins": []}, open(meta, "w"))
        self.assertIsNone(jm.record(meta, up))
        self.assertEqual(json.load(open(up)), {})

    def test_api_error_returns_none(self):
        def boom(*a, **k): raise RuntimeError("down")
        with mock.patch.dict(os.environ, {"JAMENDO_CLIENT_ID": "k"}), mock.patch.object(jm.requests, "get", boom):
            self.assertIsNone(jm.pick(tempfile.mkdtemp(), "s"))


if __name__ == "__main__":
    unittest.main()
