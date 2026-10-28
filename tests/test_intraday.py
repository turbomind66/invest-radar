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


if __name__ == "__main__":
    unittest.main(verbosity=2)
