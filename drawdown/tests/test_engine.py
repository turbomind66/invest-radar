"""drawdown/tests/test_engine.py — 回撤引擎离线单测（不联网）。"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, BASE):
    if p not in sys.path:
        sys.path.insert(0, p)

from drawdown import engine, db, api, constants as C  # noqa: E402


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


class TestWindowSummary(unittest.TestCase):
    """区间口径：峰值从窗口第一根起算（用户选了区间就该看区间内的高点）。"""

    ROWS = [("2015-01-05", 100.0), ("2015-06-08", 150.0),
            ("2015-08-26", 85.0), ("2015-12-31", 110.0)]

    def test_peak_is_window_high_not_history_high(self):
        w = engine.window_summary(self.ROWS)
        self.assertEqual(w["peak"], 150.0)
        self.assertEqual(w["peak_date"], "2015-06-08")
        self.assertEqual(w["from"], "2015-01-05")
        self.assertEqual(w["to"], "2015-12-31")
        self.assertEqual(w["points"], 4)
        self.assertEqual(w["peak_is_start"], 0)

    def test_deepest_and_ongoing(self):
        w = engine.window_summary(self.ROWS)
        self.assertAlmostEqual(w["deepest_pct"], -43.3333, places=3)
        self.assertEqual(w["deepest_date"], "2015-08-26")
        self.assertEqual(w["trough"], 85.0)
        self.assertEqual(w["days_down"], 1)
        self.assertEqual(w["ongoing"], 1)
        self.assertIsNone(w["recover_date"])
        # 未收复时给「峰→窗口末」的交易日数（2 根）
        self.assertEqual(w["recover_days"], 2)
        self.assertAlmostEqual(w["dd_pct"], -26.6667, places=3)
        self.assertAlmostEqual(w["rebound_pct"], 29.4118, places=3)

    def test_recovered_window(self):
        # 收复 = 收盘重新站上峰值（== 也算，与 engine.compute_metrics 同口径）
        rows = [("2020-01-01", 100.0), ("2020-02-03", 120.0),
                ("2020-03-02", 90.0), ("2020-04-01", 120.0)]
        w = engine.window_summary(rows)
        self.assertEqual(w["peak"], 120.0)
        self.assertEqual(w["ongoing"], 0)
        self.assertEqual(w["recover_date"], "2020-04-01")
        self.assertEqual(w["recover_days"], 2)
        self.assertEqual(w["days_down"], 1)

    def test_peak_at_window_end_means_no_drawdown(self):
        """区间最高收盘就是最后一天时，区间回撤必然是 0（不是 bug）。"""
        rows = [("2020-01-01", 100.0), ("2020-03-02", 105.0)]
        w = engine.window_summary(rows)
        self.assertEqual(w["peak_date"], "2020-03-02")
        self.assertAlmostEqual(w["dd_pct"], 0.0, places=4)
        self.assertAlmostEqual(w["deepest_pct"], 0.0, places=4)

    def test_empty_and_invalid(self):
        self.assertIsNone(engine.window_summary([]))
        self.assertIsNone(engine.window_summary([("2020-01-01", 0)]))
        self.assertIsNone(engine.window_summary([("2020-01-01", None)]))

    def test_peak_at_window_start(self):
        rows = [("2020-01-01", 100.0), ("2020-02-03", 80.0)]
        w = engine.window_summary(rows)
        self.assertEqual(w["peak_is_start"], 1)
        self.assertEqual(w["days_down"], 1)


class TestWindowAggregate(unittest.TestCase):
    """aggregate 带窗口映射时必须额外给出区间列，且不改动全历史列。"""

    def test_window_columns(self):
        metrics = [
            dict(date="2015-01-05", close=3641, peak=5877.2, peak_date="2007-10-16",
                 dd_pct=-38.0, days_since_peak=1, state="drawdown"),
            dict(date="2015-01-30", close=3434.39, peak=5877.2, peak_date="2007-10-16",
                 dd_pct=-41.56, days_since_peak=2, state="drawdown"),
            dict(date="2015-02-27", close=3572.84, peak=5877.2, peak_date="2007-10-16",
                 dd_pct=-39.21, days_since_peak=3, state="drawdown"),
        ]
        win = {
            "2015-01-05": {"date": "2015-01-05", "peak": 3643.79,
                           "peak_date": "2015-01-05", "dd": -0.08},
            "2015-01-30": {"date": "2015-01-30", "peak": 3643.79,
                           "peak_date": "2015-01-05", "dd": -5.75},
            "2015-02-27": {"date": "2015-02-27", "peak": 3643.79,
                           "peak_date": "2015-01-05", "dd": -1.95},
        }
        out = engine.aggregate(metrics, "month", win)
        self.assertEqual(len(out), 2)
        jan = out[0]
        self.assertEqual(jan["period"], "2015-01")
        self.assertEqual(jan["start"], "2015-01-05")
        self.assertEqual(jan["end"], "2015-01-30")   # 起止不再同一天
        self.assertEqual(jan["wpeak"], 3643.79)
        self.assertAlmostEqual(jan["wdd_pct"], -5.75, places=2)
        self.assertAlmostEqual(jan["wdeepest_pct"], -5.75, places=2)
        # 全历史口径保持不变
        self.assertEqual(jan["peak"], 5877.2)
        self.assertAlmostEqual(jan["dd_pct"], -41.56, places=2)

    def test_without_window_map_has_no_window_columns(self):
        metrics = [dict(date="2015-01-05", close=1, peak=1, peak_date="2015-01-05",
                        dd_pct=0, days_since_peak=0, state="new_high")]
        out = engine.aggregate(metrics, "month")
        self.assertNotIn("wpeak", out[0])


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


class TestEpisodes(unittest.TestCase):
    """波段识别：覆盖「历史最高点长期未收复期间」的嵌套大跌。"""

    def test_nested_drawdowns_are_separate_episodes(self):
        rows = series(("2020-01-01", 100.0), ("2020-02-01", 200.0),
                      ("2020-03-01", 120.0),   # 第一波 -40%
                      ("2020-04-01", 190.0),   # 反弹但未创新高
                      ("2020-05-01", 110.0),   # 第二波 -42%
                      ("2020-06-01", 230.0))
        eps = engine.compute_episodes(rows, 15.0)
        self.assertEqual(len(eps), 2)
        self.assertAlmostEqual(eps[0]["drop_pct"], -40.0, places=2)
        self.assertAlmostEqual(eps[1]["drop_pct"], -42.11, places=1)
        self.assertEqual(eps[0]["peak_date"], "2020-02-01")
        self.assertEqual(eps[1]["peak_date"], "2020-04-01")
        # 第一段在重回 200 时才算收复（即使中间有 190 的反弹）
        self.assertEqual(eps[0]["recover_date"], "2020-06-01")
        self.assertEqual(eps[0]["ongoing"], 0)
        self.assertAlmostEqual(eps[0]["rebound_pct"], (190 / 120 - 1) * 100, places=1)

    def test_ongoing_segment(self):
        rows = series(("2021-01-01", 100.0), ("2021-02-01", 140.0),
                      ("2021-03-01", 90.0), ("2021-04-01", 95.0))
        eps = engine.compute_episodes(rows, 15.0)
        self.assertEqual(len(eps), 1)
        self.assertEqual(eps[0]["ongoing"], 1)
        self.assertIsNone(eps[0]["recover_date"])

    def test_small_swings_ignored(self):
        rows = series(("2020-01-01", 100.0), ("2020-02-01", 105.0),
                      ("2020-03-01", 98.0), ("2020-04-01", 106.0))
        self.assertEqual(engine.compute_episodes(rows, 15.0), [])

    def test_empty_and_short(self):
        self.assertEqual(engine.compute_episodes([], 15.0), [])
        self.assertEqual(engine.compute_episodes(series(("2020-01-01", 100.0)), 15.0), [])


class TestEventsFor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        db.init_db(self.tmp)

    def tearDown(self):
        try:
            os.unlink(self.tmp)
        except OSError:
            pass

    def test_curated_and_auto_merged_without_duplicates(self):
        # 2018-01-26 附近造一段 -31% 的大跌：应与策展事件「3587点」同类事件去重
        rows = [("2017-12-01", 3900.0), ("2018-01-02", 4000.0),
                ("2018-01-26", 4400.0), ("2018-02-09", 3000.0),
                ("2018-06-01", 3900.0),   # 自谷底反弹 30%，波段确认
                ("2018-12-28", 3800.0)]
        db.upsert_daily("sh000300", rows, self.tmp)
        db.set_meta("last_rebuild", "2018-12-28", self.tmp)
        evs = api.events_for("sh000300", self.tmp)
        curated = [e for e in evs if not e.get("auto")]
        auto = [e for e in evs if e.get("auto")]
        self.assertTrue(curated, "策展事件应保留")
        self.assertTrue(auto, "识别出的波段应补进事件列表")
        near = [e for e in auto
                if e["side"] == "crash" and e["date"] == "2018-01-26"]
        self.assertEqual(near, [], "与策展事件同侧且 45 天内的自动事件应被去重")
        rally = [e for e in auto if e["side"] == "rally"]
        self.assertEqual([e["date"] for e in rally], ["2018-02-09"],
                         "谷底反弹应作为「行情启动」事件补进来")
        # 事件按日期升序，前端按顺序落 pin
        dates = [e["date"] for e in evs]
        self.assertEqual(dates, sorted(dates))
        self.assertTrue(all("title" in e and "desc" in e for e in evs))


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

    def test_export_static_splits_reconstruct_curves(self):
        """静态站的还原曲线按需分片（recon/<etf>.json），不塞进 data.json。

        只放 current 会让 GitHub Pages 勾选「剔除汇率/溢价」后永远只出首只
        （用户报「只显示纳指ETF国泰 vs 纳斯达克100」）；而 6 只全塞进 data.json
        会把首屏从 4.2MB 顶到 5.6MB。分片是按需取，首屏不膨胀。
        """
        r = C.RECONSTRUCT[0]
        days = ["2020-01-02", "2020-01-03", "2020-01-06"]
        db.upsert_daily(r["etf"], [(d, 1.0 + i) for i, d in enumerate(days)], self.tmp)
        db.upsert_daily(r["fx"], [(d, 7.0) for d in days], self.tmp)
        db.upsert_daily(r["bench"], [(d, 100.0 + i) for i, d in enumerate(days)], self.tmp)
        db.save_nav(r["nav_code"], [(d, 1.0 + 0.1 * i) for i, d in enumerate(days)], self.tmp)

        out = tempfile.mkdtemp()
        api.export_static(out, path=self.tmp)
        with open(os.path.join(out, "data.json"), encoding="utf-8") as f:
            p = json.load(f)
        rc = p["reconstruct"]
        self.assertIn("current", rc)                  # 首屏仍可直接画首只
        self.assertNotIn("by_etf", rc)                # 不许内联 6 只（首屏会 +1.3MB）
        one = json.load(open(os.path.join(out, "recon", r["etf"] + ".json"),
                             encoding="utf-8"))
        self.assertEqual(one["etf"], r["etf"])
        self.assertEqual(one["points"], 3)
        for k in ("price", "nav", "restored", "bench"):
            self.assertIn(k, one["curves"])
        html = open(os.path.join(out, "index.html"), encoding="utf-8").read()
        self.assertIn("__STATIC__", html)             # 前端据此走本地数据分支

    def _seed_window(self):
        """三段行情：先创新高、再落、再反弹但未收复 → 全历史与区间口径必然不同。"""
        rows = [("2020-01-02", 100.0), ("2020-02-03", 200.0),   # 历史高点 200
                ("2020-03-02", 120.0),                          # -40%
                ("2020-06-01", 160.0),                          # 反弹
                ("2020-09-01", 140.0)]
        db.upsert_daily("sh000001", rows, self.tmp)
        engine.rebuild_asset("sh000001", self.tmp)

    def test_table_reports_window_peak(self):
        self._seed_window()
        t = api.table("sh000001", "month", "2020-06-01", "2020-09-01", path=self.tmp)
        w = t["window"]
        self.assertEqual(w["peak"], 160.0)          # 区间高点，不是历史 200
        self.assertEqual(w["peak_date"], "2020-06-01")
        self.assertEqual(w["from"], "2020-06-01")
        self.assertTrue(w["ongoing"])
        self.assertEqual(len(t["periods"]), 2)      # 6 月 / 9 月
        first = t["periods"][0]
        self.assertEqual(first["start"], "2020-06-01")
        self.assertEqual(first["end"], "2020-06-01")
        self.assertEqual(first["wpeak"], 160.0)     # 区间峰值列
        self.assertEqual(first["peak"], 200.0)      # 历史峰值列仍为全历史
        self.assertIn("wdd_pct", first)

    def test_table_daily_carries_window_columns(self):
        self._seed_window()
        t = api.table("sh000001", "day", "2020-09-01", "2020-09-01", path=self.tmp)
        row = t["daily"][0]
        self.assertEqual(row["peak"], 200.0)
        self.assertEqual(row["wpeak"], 140.0)       # 单日窗口，峰值=当日收盘
        self.assertAlmostEqual(row["wdd_pct"], 0.0, places=4)

    def test_table_granularity_actually_changes_rows(self):
        """用户报的「周期汇总每行都是一天」：粒度必须真的生效。

        同一个月里放两根日线 —— 日粒度必须是 2 行一天，月粒度必须是 1 行
        「起 01-02 ~ 止 01-20」；这正是原先 <select id=gran> 没接监听时的症状。
        """
        rows = [("2020-01-02", 100.0), ("2020-01-20", 120.0),
                ("2020-02-03", 110.0), ("2020-03-02", 90.0)]
        db.upsert_daily("sh000300", rows, self.tmp)
        engine.rebuild_asset("sh000300", self.tmp)

        day = api.table("sh000300", "day", path=self.tmp)
        month = api.table("sh000300", "month", path=self.tmp)
        year = api.table("sh000300", "year", path=self.tmp)
        self.assertEqual(len(day["periods"]), 4)
        self.assertEqual(len(month["periods"]), 3)
        self.assertEqual(len(year["periods"]), 1)
        self.assertEqual(month["periods"][0]["start"], "2020-01-02")
        self.assertEqual(month["periods"][0]["end"], "2020-01-20")
        self.assertNotEqual(month["periods"][0]["start"], month["periods"][0]["end"])
        self.assertEqual(month["gran"], "month")
        self.assertEqual(day["periods"][0]["start"], day["periods"][0]["end"])

    def test_signals_window_block(self):
        self._seed_window()
        s = api.signals(["sh000001"], "2020-06-01", "2020-09-01", path=self.tmp)["signals"][0]
        self.assertIn("window", s)
        w = s["window"]
        self.assertEqual(w["peak"], 160.0)
        self.assertEqual(w["history_peak"], 200.0)
        self.assertEqual(w["is_global_peak"], 0)
        self.assertAlmostEqual(s["dd_pct"], (140 / 200 - 1) * 100, places=3)  # 全历史口径不变

    def test_signals_without_window_has_no_block(self):
        self._seed_window()
        s = api.signals(["sh000001"], path=self.tmp)["signals"][0]
        self.assertNotIn("window", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
