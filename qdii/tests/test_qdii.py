"""qdii/tests/test_qdii.py — QDII 定投决策面板单元测试（不依赖网络）。

运行：
    python -m unittest discover -s qdii/tests -p "test_*.py" -q
    （或 python qdii/tests/test_qdii.py）
"""
import os
import sys
import json
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
QDIR = os.path.join(ROOT, "qdii")
for p in (ROOT, QDIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import fetcher_limit  # noqa: E402
import fetcher_fee  # noqa: E402
import constants as C  # noqa: E402
import strategy  # noqa: E402
import tracking_error  # noqa: E402


class LimitParserTest(unittest.TestCase):
    """模块B：申购状态解析（含曾踩坑的编码/限流要点）。"""

    def test_normalize_status(self):
        self.assertEqual(fetcher_limit.normalize_sub_status("暂停申购"), "SUSPEND")
        self.assertEqual(fetcher_limit.normalize_sub_status("限制大额申购"), "LIMIT")
        self.assertEqual(fetcher_limit.normalize_sub_status("开放申购"), "OPEN")
        # 支付宝口径措辞也能正确归位
        self.assertEqual(fetcher_limit.normalize_sub_status("暂停销售"), "SUSPEND")
        self.assertEqual(fetcher_limit.normalize_sub_status(""), "OPEN")

    def test_extract_datas_from_js_literal(self):
        # 接口返回 JS 对象字面量（键无引号），不是标准 JSON
        txt = 'var reData = {datas:[["161125","某某基金","指数型","3.14","10-27",' \
              '"暂停申购","开放赎回","","10.0","10.0"]],allRecords:2};'
        out = fetcher_limit.extract_datas(txt)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0], "161125")
        self.assertEqual(out[0][5], "暂停申购")
        self.assertEqual(out[0][9], "10.0")

    def test_unlimited_threshold(self):
        self.assertGreaterEqual(fetcher_limit.LIMIT_UNLIMITED_THRESHOLD, 1e10)
        # 接口用 1e11 量级表示「不限」
        self.assertEqual(0 if 1e11 >= fetcher_limit.LIMIT_UNLIMITED_THRESHOLD else 1, 0)
        self.assertEqual(1 if 10.0 < fetcher_limit.LIMIT_UNLIMITED_THRESHOLD else 0, 1)


class FeeParserTest(unittest.TestCase):
    """模块C：费率解析。"""

    def test_to_pct(self):
        self.assertAlmostEqual(fetcher_fee.to_pct("0.80%"), 0.8)
        self.assertAlmostEqual(fetcher_fee.to_pct("1%"), 1.0)
        self.assertAlmostEqual(fetcher_fee.to_pct(0.2), 0.2)
        self.assertIsNone(fetcher_fee.to_pct("--"))   # 支付宝缺失值
        self.assertIsNone(fetcher_fee.to_pct(None))

    def test_parse_rate_info(self):
        obj = {
            "sg": [{"rate": "0.12", "source": "1.2", "min": "0", "max": "100"}],
            "sh": [{"time": "7", "rate": "0.1"}],
            "MGREXP": "0.8%", "TRUSTEXP": "0.2%", "SALESEXP": "0.0%",
            "MAXSG": "10", "DTZT": "0",
        }
        out = fetcher_fee.parse_rate_info(obj)
        self.assertAlmostEqual(out["sub_fee_rate"], 1.2)      # 原价
        self.assertAlmostEqual(out["sub_fee_discount"], 0.12)  # 折后
        self.assertAlmostEqual(out["mgmt_fee"], 0.8)
        self.assertAlmostEqual(out["custodian_fee"], 0.2)
        self.assertAlmostEqual(out["large_sub_limit"], 10.0)
        self.assertEqual(out["dca_status"], "0")
        ladder = json.loads(out["redeem_fee_ladder"])
        self.assertEqual(ladder["7"], 0.1)

    def test_parse_rate_info_empty(self):
        out = fetcher_fee.parse_rate_info({})
        self.assertIsNone(out["mgmt_fee"])
        self.assertEqual(json.loads(out["redeem_fee_ladder"]), {})


class TrackingErrorTest(unittest.TestCase):
    """模块E：跟踪误差。用合成序列验证「T+1 滞后错位对齐」数学正确性（不联网）。"""

    N = 40

    def setUp(self):
        tracking_error._BENCH_CACHE.clear()
        self._nav_backup = tracking_error.fetch_nav_history
        self._bmk_backup = tracking_error.fetch_benchmark_daily

    def tearDown(self):
        tracking_error.fetch_nav_history = self._nav_backup
        tracking_error.fetch_benchmark_daily = self._bmk_backup
        tracking_error._BENCH_CACHE.clear()

    @staticmethod
    def _series(n, start=100.0):
        """生成日期序列与价格。

        日收益刻意做成**交替变化**(+1%, -0.5%)：若用恒定日收益，错位一天后的
        收益率完全相同，会让「是否做滞后修正」看不出差别，失去检验意义。
        """
        dates, vals = [], []
        v = start
        for i in range(n):
            d = "2026-%02d-%02d" % ((i // 6) + 1, (i % 6) * 5 + 1)
            dates.append(d)
            vals.append(round(v, 6))
            v *= (1.0 + (0.01 if i % 2 == 0 else -0.005))
        return list(zip(dates, vals))

    def test_te_is_zero_when_lag_matched(self):
        """净值恰好等于「滞后1个交易日」的指数 → lag=1 时 TE 应为 0。"""
        bmk = self._series(self.N)                       # 指数 (d_i, c_i)
        # 净值日期与指数日期同日历日，但取值 = 上一个交易日的指数水平（T+1 反映）
        nav = [(bmk[i][0], bmk[i - 1][1]) for i in range(1, len(bmk))]
        tracking_error.fetch_nav_history = lambda code, *a, **k: nav
        tracking_error.fetch_benchmark_daily = lambda *a, **k: bmk

        sources = C.TRACK_BENCHMARK["161130"]["sources"]
        self.assertIn(("em_us", "100.NDX100"), sources)
        self.assertEqual(C.TRACK_BENCHMARK["161130"]["lag"], 0)  # 实测 lag=0 才对齐

        te, nav_date, src = tracking_error.compute_te("161130", lag_override=1)
        self.assertIsNotNone(te)
        self.assertAlmostEqual(te, 0.0, places=2)
        self.assertEqual(nav_date, nav[-1][0])
        self.assertTrue(src.startswith(("sina_us:", "em_us:")), src)

    def test_te_inflated_when_lag_ignored(self):
        """同样数据用 lag=0（不做滞后修正）应显著变大——证明修正不是无效操作。"""
        bmk = self._series(self.N)                       # 指数 (d_i, c_i)，日收益交替
        nav = [(bmk[i][0], bmk[i - 1][1]) for i in range(1, len(bmk))]
        tracking_error.fetch_nav_history = lambda code, *a, **k: nav
        tracking_error.fetch_benchmark_daily = lambda *a, **k: bmk

        te_lag1, _, _ = tracking_error.compute_te("161130", lag_override=1)
        te_lag0, _, _ = tracking_error.compute_te("161130", lag_override=0)
        self.assertAlmostEqual(te_lag1, 0.0, places=2)
        self.assertGreater(te_lag0, 1.0)   # 错位对齐会把跟踪误差放大到荒谬水平

    def test_te_detects_real_deviation(self):
        """净值带已知幅度噪声时，TE 应落在合理量级而非 30%+ 的荒谬值。"""
        bmk = self._series(self.N)
        nav = []
        import math
        for i in range(1, len(bmk)):
            noise = 0.0002 * (1 if i % 2 else -1)   # ±0.02% 日偏离
            nav.append((bmk[i][0], bmk[i - 1][1] * (1.0 + noise)))
        tracking_error.fetch_nav_history = lambda code, *a, **k: nav
        tracking_error.fetch_benchmark_daily = lambda *a, **k: bmk
        te, _, _ = tracking_error.compute_te("161130", lag_override=1)
        self.assertIsNotNone(te)
        # 日偏离 0.02% 年化 ≈ 0.02% * sqrt(252) ≈ 0.3%
        self.assertGreater(te, 0.1)
        self.assertLess(te, 3.0)

    def test_benchmark_is_true_index_not_etf(self):
        """基准必须是真实指数，不能退回场内 ETF（会导致 TE 高估到 30%+）。

        允许两类源：sina_us（新浪美股指数，symbol 形如 .INX/.NDX）与
        em_us（东财指数 secid 形如 100.SPX）；绝不允许出现 A 股 ETF（em_a）。
        """
        for code, cfg in C.TRACK_BENCHMARK.items():
            self.assertIn(cfg["lag"], (0, 1))
            self.assertTrue(cfg["sources"], "%s 未配置基准源" % code)
            for btype, bcode in cfg["sources"]:
                self.assertIn(btype, ("sina_us", "em_us"),
                              "%s 基准源类型异常：%s" % (code, btype))
                if btype == "sina_us":
                    self.assertTrue(bcode.startswith("."),
                                    "%s 新浪指数代码异常：%s" % (code, bcode))
                else:
                    self.assertTrue(bcode.startswith("100."),
                                    "%s 东财 secid 异常：%s" % (code, bcode))


class StrategyTest(unittest.TestCase):
    """模块F：决策倍数。"""

    def test_suspend_zero(self):
        r = strategy.recommend({"code": "x", "sub_status": "SUSPEND"})
        self.assertEqual(r["multiple"], 0.0)
        self.assertTrue(any("暂停" in s for s in r["reasons"]))

    def test_base_multiple(self):
        r = strategy.recommend({"code": "x", "sub_status": "OPEN"})
        self.assertAlmostEqual(r["multiple"], 1.0)

    def test_drop_tiers_add(self):
        """跟跌分档：跌幅越大加仓越多，且不跨档误触发（阈值为百分点单位）。"""
        tiers = C.STRATEGY_PARAMS["drop_tiers"]
        self.assertEqual([round(t, 2) for t, _ in tiers], [-2.0, -4.0, -6.0])

        def mult(drop_pct):
            return strategy.recommend({"code": "x", "sub_status": "OPEN",
                                       "spx_chg": drop_pct,
                                       "ndx_chg": drop_pct})["multiple"]

        self.assertAlmostEqual(mult(-1.0), 1.0)    # 跌幅未达第一档：不加
        self.assertAlmostEqual(mult(-2.5), 1.5)    # 越过 -2% 档：+0.5
        self.assertAlmostEqual(mult(-4.5), 2.0)    # 越过 -4% 档：+1.0
        self.assertAlmostEqual(mult(-6.5), 2.5)    # 越过 -6% 档：+1.5
        # 关键：中等跌幅不能误触发最高档（正是此前 units 踩坑暴露的问题）
        self.assertNotAlmostEqual(mult(-2.5), 2.5)

    def test_drop_tiers_units_are_percent(self):
        """回归：阈值若误写成小数，跌 2.5% 就会命中 -6% 最高档。"""
        def mult(drop_pct):
            return strategy.recommend({"code": "x", "sub_status": "OPEN",
                                       "spx_chg": drop_pct,
                                       "ndx_chg": drop_pct})["multiple"]
        # -2.5%（百分单位）应只拿到第一档 +0.5
        self.assertAlmostEqual(mult(-2.5), 1.5)

    def test_valuation_adjust(self):
        lo = C.STRATEGY_PARAMS["valuation_low"]
        hi = C.STRATEGY_PARAMS["valuation_high"]
        r = strategy.recommend({"code": "x", "sub_status": "OPEN",
                                "valuation_pct": lo - 5})
        self.assertAlmostEqual(r["multiple"], 1.5)      # 低估 +0.5
        r = strategy.recommend({"code": "x", "sub_status": "OPEN",
                                "valuation_pct": hi + 5})
        self.assertAlmostEqual(r["multiple"], 0.5)      # 高估 -0.5

    def test_risk_off_halves(self):
        v = C.STRATEGY_PARAMS["risk_off_vix"]
        r = strategy.recommend({"code": "x", "sub_status": "OPEN", "vix": v + 1})
        self.assertAlmostEqual(r["multiple"], 0.5)

    def test_limit_cap(self):
        base = C.STRATEGY_PARAMS["base_amount"]
        # 日限额 10 元，基准额 1000 → 倍数被封到 0.01
        r = strategy.recommend({"code": "x", "sub_status": "LIMIT",
                                "limit_amount": 10.0})
        self.assertAlmostEqual(r["multiple"], 10.0 / base)

    def test_capped_and_clamped(self):
        cap = C.STRATEGY_PARAMS["multiple_cap"]
        r = strategy.recommend({"code": "x", "sub_status": "OPEN",
                                "spx_chg": -50.0, "ndx_chg": -50.0,
                                "valuation_pct": 0.0})
        self.assertLessEqual(r["multiple"], cap)


class CategoryDecisionTest(unittest.TestCase):
    """大类维度决策（2026-10-29 起为页面主口径）。"""

    def _stats(self, total=3, open_=1, limit=1, suspend=1, min_limit=1000.0):
        return dict(total=total, open=open_, limit=limit, suspend=suspend,
                    min_limit=min_limit, companies=["测试"])

    def _us(self, **kw):
        base = {"IXIC": {"symbol": "IXIC", "name": "纳指现货", "change_pct": 0.5},
                "SPX": {"symbol": "SPX", "name": "标普500现货", "change_pct": 0.3},
                "VIX": {"last_price": 15.0}}
        base.update(kw)
        return base

    def test_all_suspend_gives_zero(self):
        r = strategy.recommend_category("纳斯达克100", self._stats(suspend=3, open_=0, limit=0),
                                        self._us(), None)
        self.assertEqual(r["multiple"], 0.0)
        self.assertEqual(r["status_text"], "全部暂停")

    def test_category_drop_tier_uses_own_index(self):
        # 标普跌 4.2% → 命中 -4% 档 +1.0；纳指涨，不能串用
        us = self._us(SPX={"symbol": "SPX", "name": "标普500现货", "change_pct": -4.2})
        r = strategy.recommend_category("标普500", self._stats(), us, None)
        self.assertAlmostEqual(r["multiple"], 2.0)
        self.assertTrue(any("跌 4.20%" in x for x in r["reasons"]))

    def test_borrowed_signal_is_labelled(self):
        # 日经没有本地信号，借用纳指时理由必须写明，避免误读成本地数据
        r = strategy.recommend_category("日经225", self._stats(), self._us(), None)
        self.assertTrue(any("无本地指数信号" in x for x in r["reasons"]))

    def test_own_signal_not_labelled(self):
        r = strategy.recommend_category("纳斯达克100", self._stats(), self._us(), None)
        self.assertFalse(any("无本地指数信号" in x for x in r["reasons"]))
        self.assertTrue(any("纳指现货" in x for x in r["reasons"]))

    def test_no_amount_in_reasons(self):
        # 用户口径：决策不再落到金额
        r = strategy.recommend_category("纳斯达克100", self._stats(), self._us(), 65.0)
        self.assertFalse(any("建议投入" in x or "基准额" in x for x in r["reasons"]))

    def test_status_text_prefers_configurable_limit(self):
        r = strategy.recommend_category("纳斯达克100", self._stats(min_limit=10.0),
                                        self._us(), None)
        self.assertIn("限额 10 元", r["status_text"])


class PremiumEtfListTest(unittest.TestCase):
    """场内溢价：按 ETF 清单逐只计算（修复 159941 缺失）。"""

    def test_159941_included(self):
        codes = [e["code"] for e in C.PREMIUM_ETFS]
        self.assertIn("159941", codes, "159941（广发纳指ETF）必须在溢价清单里")
        self.assertIn("513100", codes)

    def test_codes_unique_and_wellformed(self):
        codes = [e["code"] for e in C.PREMIUM_ETFS]
        self.assertEqual(len(codes), len(set(codes)))
        for c in codes:
            self.assertEqual(len(c), 6)
            self.assertTrue(c.isdigit())

    def test_metadata_complete(self):
        for e in C.PREMIUM_ETFS:
            for k in ("code", "name", "company", "index_name", "category"):
                self.assertTrue(e.get(k), "%s 缺少字段 %s" % (e.get("code"), k))

    def test_every_category_has_etf(self):
        cats = {e["category"] for e in C.PREMIUM_ETFS}
        for c in C.CATEGORY_ORDER:
            self.assertIn(c, cats, "大类 %s 至少要有一只场内 ETF" % c)

    def test_premium_math(self):
        # (场内价 - 净值) / 净值
        price, nav = 1.691, 1.4837
        self.assertAlmostEqual((price - nav) / nav * 100, 13.97, places=1)


if __name__ == "__main__":
    unittest.main()
