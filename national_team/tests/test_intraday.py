"""疑似护盘/压盘模块单元测试（完全离线，不访问网络）。

覆盖：
  - 分时归档与信号的读写
  - 四条件判定：护盘（放量不跌）/ 压盘（放量跌破均价）
  - 收盘后大单净额印证 → 升级为 confirmed
  - 事件聚合（5 分钟窗口）与分时点聚合（倍数口径不变）

运行：
    python -m unittest discover -s tests -p "test_*.py" -v
"""
import os
import json
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db          # noqa: E402
import intraday    # noqa: E402

D1 = "2026-10-28"

# 6 只基金 × 30 个分钟点的构造数据
CODES = ["510300", "510310", "159919", "510050", "510500", "512100"]


def _rows(code, name, cat, hot_minutes=(), hot_price=1.0, hot2_minutes=(), hot2_price=1.0):
    """构造一天的分时。

    基准：每分钟 100 元 @1.0；
    hot_minutes 内的分钟放量到 1000 元 @hot_price；
    hot2_minutes 内的分钟放量到 1000 元 @hot2_price。
    """
    out = []
    for i in range(30):
        minute = "%02d%02d" % (9, 30 + i)
        price, amount = 1.0, 100.0
        if minute in hot_minutes:
            amount, price = 1000.0, hot_price
        if minute in hot2_minutes:
            amount, price = 1000.0, hot2_price
        vol = amount / price / 100.0
        out.append({"code": code, "name": name, "cat": cat, "minute": minute,
                    "price": price, "vol": vol, "amount": amount})
    return out


class TestIntradayDetect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ntt_intraday_")
        self.path = os.path.join(self.tmp, "test.db")
        db.init_db(self.path)
        rows = []
        for i, code in enumerate(CODES):
            # 0935：全部放量且价格不跌 → 护盘
            # 0940：全部放量，前 3 只价格跌破均价 → 压盘，后 3 只不跌 → 护盘
            rows += _rows(code, "测试ETF%d" % i, "测试类",
                          hot_minutes={"0935"}, hot_price=1.0,
                          hot2_minutes={"0940"}, hot2_price=(0.9 if i < 3 else 1.0))
        db.save_intraday_rows(D1, rows, path=self.path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------- 读写 ----------
    def test_intraday_rows_roundtrip(self):
        data = db.get_intraday_by_date(D1, path=self.path)
        self.assertEqual(len(data), len(CODES))
        one = data[CODES[0]]
        self.assertEqual(len(one), 30)
        self.assertEqual(one[0]["minute"], "0930")

    def test_intraday_dates(self):
        self.assertEqual(db.get_intraday_dates(path=self.path), [D1])

    # ---------- 判定 ----------
    def test_detect_finds_support_and_pressure(self):
        sigs = intraday.detect(D1, path=self.path)
        kinds = {s["kind"] for s in sigs}
        self.assertIn("support", kinds)
        self.assertIn("pressure", kinds)
        sup = [s for s in sigs if s["kind"] == "support"]
        pre = [s for s in sigs if s["kind"] == "pressure"]
        # 压盘只可能出现在 0940（其中 3 只价格跌破均价）
        self.assertTrue(all(s["minute"] == "0940" for s in pre))
        self.assertEqual(len(pre), 3)
        # 护盘：0935 全部 6 只放量不跌 + 0940 里价格未跌的 3 只
        self.assertTrue(all(s["minute"] in ("0935", "0940") for s in sup))
        at35 = [s for s in sup if s["minute"] == "0935"]
        self.assertEqual(len(at35), len(CODES))
        # 放量倍数 = 该分钟额 ÷ 该基金当日分钟均值（均值含另一个放量点，故非 10）
        amounts = [r["amount"] for r in db.get_intraday_by_date(D1, path=self.path)[CODES[0]]]
        avg = sum(amounts) / len(amounts)
        self.assertAlmostEqual(sup[0]["ratio"], 1000.0 / avg, places=1)
        self.assertGreater(sup[0]["ratio"], 5)

    def test_detect_requires_resonance(self):
        """只有 1 只放量（全池共振不足）时不得判为信号。"""
        tmp = tempfile.mkdtemp(prefix="ntt_intra2_")
        try:
            p2 = os.path.join(tmp, "s.db")
            db.init_db(p2)
            rows = _rows("510300", "独苗", "测试类", {"0935"}, 1.0)
            for i in range(1, 6):
                rows += _rows(CODES[i], "平静%d" % i, "测试类", set(), 1.0)
            db.save_intraday_rows(D1, rows, path=p2)
            sigs = intraday.detect(D1, path=p2)
            # 共振不足（同时放量的只数 < BREADTH_MIN）→ 不产生信号
            self.assertEqual(sigs, [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_level_upgrade_with_flow(self):
        """收盘后大单净额方向一致 → 疑似升级为已印证。"""
        # 先测无资金流时：全部是 suspect
        sigs = intraday.detect(D1, path=self.path)
        self.assertTrue(all(s["level"] == "suspect" for s in sigs))
        # 补一条大单净流入为正的数据 → 护盘信号升级为 confirmed
        db.save_etf_daily(D1, [
            {"code": c, "name": "测试ETF", "cat": "测试类", "price": 1.0, "chg": 0,
             "amount": 1e8, "main_net": 5e7, "huge_net": 3e7, "big_net": 2e7,
             "mid_net": 0, "small_net": 0} for c in CODES
        ], path=self.path)
        sigs2 = intraday.detect(D1, path=self.path)
        sup = [s for s in sigs2 if s["kind"] == "support"]
        self.assertTrue(all(s["level"] == "confirmed" for s in sup))

    # ---------- 聚合 ----------
    def test_events_aggregate_by_window(self):
        intraday.detect(D1, path=self.path)
        view = intraday.build_view(D1, path=self.path)
        ev = view["events"]
        self.assertTrue(ev)
        # 事件按金额倒序
        amounts = [e["amount"] for e in ev]
        self.assertEqual(amounts, sorted(amounts, reverse=True))
        for e in ev:
            self.assertGreaterEqual(e["funds"], intraday.BREADTH_MIN)
            self.assertIn(e["kind"], ("support", "pressure"))
            self.assertIn(e["segment"], ("开盘", "盘中", "尾盘"))

    def test_agg_points_keeps_ratio(self):
        """5 分钟聚合后倍数口径不变（5 分钟额 ÷ 5×分钟均 = 原倍数）。"""
        data = db.get_intraday_by_date(D1, path=self.path)
        rows = data[CODES[0]]
        core = [r for r in rows if r["minute"] not in ("1500", "1530")]
        avg = sum(r["amount"] or 0 for r in core) / len(core)
        pts = intraday._agg_points(rows, avg)
        self.assertLess(len(pts), len(rows))
        for pt in pts:
            m = int(pt["m"][:2]) * 60 + int(pt["m"][2:])
            bucket = [r for r in rows
                      if (int(r["minute"][:2]) * 60 + int(r["minute"][2:])) // 5 * 5 == m]
            self.assertAlmostEqual(pt["a"], sum(r["amount"] for r in bucket), places=2)
            self.assertAlmostEqual(pt["r"],
                                   (sum(r["amount"] for r in bucket) / avg) / len(bucket),
                                   places=1)

    def test_build_view_shape(self):
        intraday.detect(D1, path=self.path)
        v = intraday.build_view(D1, path=self.path)
        self.assertTrue(v["has_data"])
        self.assertEqual(v["fund_count"], len(CODES))
        self.assertIn("events", v)
        self.assertIn("signals", v)
        self.assertIn("thresholds", v)
        self.assertGreater(v["support_count"], 0)


# ---------------- 方案C：逐笔大额买单识别 ----------------
def _tick(path, date, code, name, cat, rows):
    """注入若干逐笔大单（rows: [(ttime,price,vol手,direction,amount元)]）。"""
    store = [{"code": code, "name": name, "cat": cat, "ttime": r[0], "price": r[1],
              "volume": r[2], "direction": r[3], "amount": r[4]} for r in rows]
    db.save_tick_rows(date, store, path=path)


class TestTickDetect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ntt_tick_")
        self.path = os.path.join(self.tmp, "test.db")
        db.init_db(self.path)
        # 510300：窗口 0930 集中扫货（买入主导 + 价格上行）
        _tick(self.path, D1, "510300", "沪深300ETF", "沪深300", [
            ("09:31:00", 1.00, 10000, 1, 100e4),
            ("09:32:00", 1.02, 10000, 1, 102e4),
            ("09:33:00", 1.05, 10000, 1, 105e4),
            ("09:34:00", 1.05, 5000, 2, 52.5e4),
        ])
        # 510310：窗口 1000 集中抛压（卖出主导 + 价格下行）
        _tick(self.path, D1, "510310", "沪深300ETF2", "沪深300", [
            ("10:01:00", 2.00, 10000, 2, 200e4),
            ("10:02:00", 1.95, 10000, 2, 195e4),
            ("10:03:00", 1.90, 10000, 2, 190e4),
            ("10:04:00", 1.90, 3000, 1, 57e4),
        ])
        # 510500：窗口 1100 净额不足（<200万）→ 不应产生事件
        _tick(self.path, D1, "510500", "中证500ETF", "中证500", [
            ("11:01:00", 5.00, 3000, 1, 150e4),
            ("11:02:00", 5.00, 2000, 2, 100e4),
        ])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------- 解析 ----------
    def test_fetch_details_parses_and_self_computes_amount(self):
        """fetch_details 应正确解析每行并自算金额=手数×价×100（不依赖金额列）。"""
        fake = json.dumps({"data": {"details": [
            "09:31:00,1.00,10000,1,100",
            "09:32:00,1.02,5000,2,51",
        ]}})
        orig = intraday._get
        intraday._get = lambda url, **k: fake
        try:
            rows = intraday.fetch_details("510300", lmt=10)
        finally:
            intraday._get = orig
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], ("09:31:00", 1.0, 10000, 1, 10000 * 1.0 * 100))
        self.assertEqual(rows[1], ("09:32:00", 1.02, 5000, 2, 5000 * 1.02 * 100))

    # ---------- 聚合判定 ----------
    def test_tick_buy_and_sell_detected(self):
        ev = intraday.compute_tick_events(D1, path=self.path)
        kinds = {e["kind"] for e in ev}
        self.assertIn("tick_buy", kinds)
        self.assertIn("tick_sell", kinds)
        buy = [e for e in ev if e["kind"] == "tick_buy"]
        self.assertEqual(len(buy), 1)
        self.assertEqual(buy[0]["code"], "510300")
        self.assertEqual(buy[0]["window"], "0930")
        self.assertGreater(buy[0]["net"], intraday.MIN_WIN_AMT)
        self.assertGreaterEqual(buy[0]["buy_ratio"], intraday.BUY_RATIO)
        self.assertGreater(buy[0]["up_pct"], 0)
        sell = [e for e in ev if e["kind"] == "tick_sell"]
        self.assertEqual(sell[0]["code"], "510310")
        self.assertEqual(sell[0]["window"], "1000")
        # 净额不足的 510500 不应出现
        self.assertNotIn("510500", {e["code"] for e in ev})

    def test_tick_none_when_no_data(self):
        tmp = tempfile.mkdtemp()
        try:
            p = os.path.join(tmp, "s.db")
            db.init_db(p)
            self.assertEqual(intraday.compute_tick_events(D1, path=p), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_tick_empty_when_net_too_small(self):
        tmp = tempfile.mkdtemp()
        try:
            p = os.path.join(tmp, "s.db")
            db.init_db(p)
            # 两笔各 50 万，方向相反，净买 0 → 不触发阈值
            _tick(p, D1, "510300", "T", "C", [
                ("09:31:00", 1.0, 5000, 1, 50e4),
                ("09:32:00", 1.0, 5000, 2, 50e4),
            ])
            self.assertEqual(intraday.compute_tick_events(D1, path=p), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ---------- 与方案A 联动 ----------
    def test_tick_linkage_upgrades_support_signal(self):
        """方案A 的护盘信号所在窗口若有方案C 大买单印证 → 升级 confirmed_by_tick。"""
        db.save_intraday_signals(D1, [{
            "code": "510300", "name": "沪深300ETF", "cat": "沪深300", "minute": "0931",
            "kind": "support", "amount": 100e4, "ratio": 5, "price": 1.0,
            "price_chg": 0, "cover": 3, "level": "suspect", "note": "大额买入承接",
        }], path=self.path)
        intraday.detect_tick(D1, path=self.path)
        sigs = db.get_intraday_signals(D1, path=self.path)
        sup = [s for s in sigs if s["kind"] == "support"]
        self.assertEqual(len(sup), 1)
        self.assertEqual(sup[0]["level"], "confirmed_by_tick")
        self.assertIn("逐笔大买单印证", sup[0]["note"])

    # ---------- 视图包含 tick ----------
    def test_build_view_has_tick_block(self):
        v = intraday.build_view(D1, path=self.path)
        self.assertIn("tick", v)
        self.assertTrue(v["tick"]["has_data"])
        self.assertGreaterEqual(v["tick"]["buy_events"], 1)
        self.assertGreaterEqual(v["tick"]["sell_events"], 1)
        self.assertIn("thresholds", v["tick"])
        self.assertEqual(v["tick"]["thresholds"]["big"], intraday.TICK_BIG)

    def test_tick_totals_include_all_rows_not_just_events(self):
        """回归：盘后快照里大单散落各窗口、形不成集中事件时，
        「大买单合计/大卖单合计」仍须汇总【全部】大单金额，不能为 0。"""
        tmp = tempfile.mkdtemp()
        try:
            p = os.path.join(tmp, "s.db")
            db.init_db(p)
            # 5 笔大单：3 买 2 卖，分散在不同时段，净额均不足 → 不会触发集中事件
            _tick(p, D1, "510300", "沪深300ETF", "沪深300", [
                ("11:30:02", 4.105, 3088, 1, 3088 * 4.105 * 100),
                ("14:55:10", 4.110, 5000, 2, 5000 * 4.110 * 100),
                ("14:56:01", 2.950, 2000, 1, 2000 * 2.950 * 100),
                ("14:57:22", 2.960, 1500, 2, 1500 * 2.960 * 100),
                ("14:58:00", 1.850, 3000, 1, 3000 * 1.850 * 100),
            ])
            v = intraday.build_view(D1, path=p)["tick"]
            self.assertEqual(v["big_count"], 5)
            self.assertEqual(v["buy_events"], 0)   # 无集中事件
            self.assertEqual(v["sell_events"], 0)
            # 合计必须覆盖全部大单（3 买 + 2 卖），不能为零
            expect_buy = 3088 * 4.105 * 100 + 2000 * 2.950 * 100 + 3000 * 1.850 * 100
            expect_sell = 5000 * 4.110 * 100 + 1500 * 2.960 * 100
            self.assertAlmostEqual(v["buy_amount"], expect_buy, delta=1)
            self.assertAlmostEqual(v["sell_amount"], expect_sell, delta=1)
            self.assertGreater(v["buy_amount"], 0)
            self.assertGreater(v["sell_amount"], 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
