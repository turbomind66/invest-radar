"""drawdown/tests/test_engine.py — 回撤引擎离线单测（不联网）。"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, BASE):
    if p not in sys.path:
        sys.path.insert(0, p)

from drawdown import engine, db, api  # noqa: E402


def series(*pairs):
    return list(pairs)


class TestComputeMetrics(unittest.TestCase):
    def test_monotonic_up_no_drawdown(self):
        rows = series(("2020-01-01", 100), ("2020-01-02", 110), ("2020-01-03", 120))
        m, ev = engine.compute_metrics(rows)
        self.assertEqual(len(m), 3)
        self.assertTrue(all(x[4] == 0 for x in m))  # dd_pct 全为 0
        self.assertEqual(len(ev), 0)

    def test_drawdown_and_recovery(self):
        rows = series(
            ("2020-01-01", 100), ("2020-01-02", 120),  # 峰值 120
            ("2020-01-03", 90),                        # 回撤 -25%
            ("2020-01-04", 100), ("2020-01-05", 130),  # 修复并创新高
        )
        m, ev = engine.compute_metrics(rows)
        self.assertEqual(m[2][4], round((90.0 / 120 - 1) * 100, 4))  # dd_pct
        # 一段已修复事件 + 结尾 ongoing 段（创新高后重置）
        closed = [e for e in ev if not e[7]]
        self.assertTrue(any(e[0] == "2020-01-02" and e[1] == "2020-01-03" for e in closed))
        r = [e for e in closed if e[0] == "2020-01-02"][0]
        self.assertEqual(r[2], "2020-01-05")  # 修复日
        self.assertAlmostEqual(r[5], -25.0, places=2)  # drop_pct

    def test_ongoing_segment_marked(self):
        rows = series(("2021-01-01", 100), ("2021-01-02", 80), ("2021-01-03", 70))
        m, ev = engine.compute_metrics(rows)
        self.assertTrue(all(e[7] == 1 for e in ev))
        self.assertEqual(ev[-1][1], "2021-01-03")  # 谷值随新低滚动

    def test_empty(self):
        self.assertEqual(engine.compute_metrics([]), ([], []))


class TestAggregate(unittest.TestCase):
    def test_granularities(self):
        metrics = [
            dict(date="2020-01-02", close=100, peak=100, peak_date="2020-01-02",
                 dd_pct=0, days_since_peak=0, state="new_high"),
            dict(date="2020-02-03", close=80, peak=100, peak_date="2020-01-02",
                 dd_pct=-20, days_since_peak=32, state="drawdown"),
            dict(date="2021-03-04", close=110, peak=110, peak_date="2021-03-04",
                 dd_pct=0, days_since_peak=0, state="new_high"),
        ]
        for g in ("day", "week", "month", "quarter", "year"):
            out = engine.aggregate(metrics, g)
            self.assertTrue(out)
            self.assertIn("period", out[0])
            self.assertIn("deepest_pct", out[0])
        year = engine.aggregate(metrics, "year")
        self.assertEqual(len(year), 2)
        self.assertEqual(year[1]["deepest_pct"], 0)


class TestSignal(unittest.TestCase):
    def test_rank_and_hint(self):
        metrics = []
        for i, c in enumerate([100, 90, 80, 70, 60, 50, 40, 30, 20, 10, 100]):
            d = "2020-01-%02d" % (i + 1)
            peak = 100
            metrics.append(dict(date=d, close=c, peak=peak, peak_date="2020-01-01",
                                dd_pct=round((c / peak - 1) * 100, 4),
                                days_since_peak=i, state="x"))
        s = engine.signal(metrics, [])
        self.assertIsNotNone(s)
        self.assertIn("dd_rank_pct", s)
        self.assertIn("hint", s)
        self.assertIsInstance(s["hint"], str)


class TestDBAndAPI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        db.init_db(self.tmp)

    def tearDown(self):
        try:
            os.unlink(self.tmp)
        except OSError:
            pass

    def test_roundtrip(self):
        rows = [("2022-01-01", 100.0), ("2022-01-02", 90.0), ("2022-01-03", 95.0)]
        n = db.upsert_daily("test_asset", rows, self.tmp)
        self.assertEqual(n, 3)
        got = db.get_daily("test_asset", self.tmp)
        self.assertEqual(got, rows)
        engine.rebuild_asset("test_asset", self.tmp)
        metrics = db.get_metrics("test_asset", path=self.tmp)
        self.assertEqual(len(metrics), 3)
        self.assertAlmostEqual(metrics[1]["dd_pct"], -10.0, places=2)
        ev = db.get_events("test_asset", self.tmp)
        self.assertTrue(ev)
        self.assertEqual(ev[0]["ongoing"], 1)

    def test_catalog_and_payload(self):
        db.upsert_daily("sh000001", [("2020-01-01", 100.0), ("2020-01-02", 110.0)], self.tmp)
        engine.rebuild_asset("sh000001", self.tmp)
        cat = api.catalog(self.tmp)
        sh = [a for a in cat["assets"] if a["id"] == "sh000001"][0]
        self.assertTrue(sh["ready"])
        self.assertEqual(sh["points"], 2)
        p = api.build_payload(ids=["sh000001"], path=self.tmp)
        self.assertIn("series", p)
        self.assertIn("signals", p)
        self.assertIn("table", p)
        self.assertEqual(p["series"]["series"][0]["points"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
