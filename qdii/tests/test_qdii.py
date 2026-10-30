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

        # 基准配置已改为「按大类」（TRACK_BENCHMARK 按代码表已废弃）
        sources = C.TRACK_BENCHMARK_BY_CATEGORY["纳斯达克100"]["sources"]
        self.assertIn(("em_us", "100.NDX100"), sources)
        self.assertEqual(C.TRACK_BENCHMARK_BY_CATEGORY["纳斯达克100"]["lag"], 0)  # 实测 lag=0 才对齐

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
        # 2026-10-29 起每个大类都有自有信号（日经→日经期货、DAX→境内ETF…），
        # 这里用一个**没有配置信号**的假类别，验证「借用美股风向时必须写明」，
        # 避免误读成本地数据。
        import constants as C
        C.CATEGORY_SIGNAL["测试类"] = ["IXIC"]      # 临时给一个「借来的」信号
        try:
            r = strategy.recommend_category("测试类", self._stats(), self._us(), None)
        finally:
            C.CATEGORY_SIGNAL.pop("测试类", None)
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
        # 只有**境内有对应 ETF 的大类**才要求进溢价清单；
        # 英国富时100 / 法国CAC40 / 纳斯达克科技市值加权指数在境内没有可交易 ETF，
        # 硬凑一只别的指数来充数只会给出错误溢价。
        cats = {e["category"] for e in C.PREMIUM_ETFS}
        for c in C.CATEGORY_ORDER:
            if c in ("英国富时100", "法国CAC40"):
                continue
            self.assertIn(c, cats, "大类 %s 至少要有一只场内 ETF" % c)

    def test_premium_math(self):
        # (场内价 - 净值) / 净值
        price, nav = 1.691, 1.4837
        self.assertAlmostEqual((price - nav) / nav * 100, 13.97, places=1)


class PremiumAdjustedTest(unittest.TestCase):
    """折算溢价：把滞后净值按指数涨幅折到最新收盘日。

    场景：净值日 10-27、指数最新 10-28 涨 0.55%，则折算净值 = 净值 × 1.0055，
    折算溢价应低于原溢价 0.3~0.9 个点——用来确认「高溢价是真实的，不是滞后造成的」。
    """

    def setUp(self):
        import fetcher_premium as fp
        self.fp = fp
        self._orig = fp.tracking_error.fetch_benchmark_daily
        self.series = [("2026-10-24", 100.0), ("2026-10-27", 200.0),
                       ("2026-10-28", 202.0)]
        fp.tracking_error.fetch_benchmark_daily = lambda *a, **k: self.series

    def tearDown(self):
        self.fp.tracking_error.fetch_benchmark_daily = self._orig
        self.fp._FUT_BENCH_CACHE.clear()

    def test_ratio_from_nav_date_to_latest(self):
        ratio, asof = self.fp.index_cum_ratio(("sina_us", ".NDX"), "2026-10-27")
        self.assertAlmostEqual(ratio, 202.0 / 200.0, places=6)
        self.assertEqual(asof, "2026-10-28")

    def test_ratio_is_one_when_nav_is_latest(self):
        ratio, asof = self.fp.index_cum_ratio(("sina_us", ".NDX"), "2026-10-28")
        self.assertAlmostEqual(ratio, 1.0, places=9)
        self.assertEqual(asof, "2026-10-28")

    def test_missing_bench_returns_none(self):
        self.assertEqual(self.fp.index_cum_ratio(None, "2026-10-27"), (None, None))
        self.assertEqual(self.fp.index_cum_ratio(("sina_us", ".NDX"), None), (None, None))

    def test_failed_source_returns_none_not_raise(self):
        def boom(*a, **k):
            raise RuntimeError("blocked")
        self.fp.tracking_error.fetch_benchmark_daily = boom
        self.assertEqual(self.fp.index_cum_ratio(("em_us", "100.GDAXI"), "2026-10-27"),
                         (None, None))

    def test_adjusted_premium_lower_than_raw(self):
        """折算后溢价应低于原口径，且差额接近净值区间内的指数涨幅。"""
        nav, price = 1.9836, 2.29
        ratio, _ = self.fp.index_cum_ratio(("sina_us", ".NDX"), "2026-10-27")
        raw = (price - nav) / nav * 100
        est_nav = nav * ratio
        adj = (price - est_nav) / est_nav * 100
        self.assertAlmostEqual(adj, (price / est_nav - 1) * 100, places=6)
        self.assertLess(adj, raw)
        # 指数涨 1% → 溢价被削掉的点数 ≈ 1% × 场内价/净值（此处约 1.15 个点）
        self.assertAlmostEqual(raw - adj, 100 * (price / nav) * (1 - 1 / ratio), places=6)

    def test_every_etf_has_bench(self):
        # 有基准源的必须写清源类型；确实没有公开日K 的（纳斯达克科技市值加权指数、
        # 法国CAC40）允许 bench=None —— 折算列留空比用错的基准更诚实。
        for e in C.PREMIUM_ETFS:
            if not e.get("bench"):
                self.assertIn(e["category"], ("纳斯达克科技", "法国CAC40"),
                              "%s 缺 bench 且该类别不该留空" % e["code"])
                continue
            self.assertIn(e["bench"][0], ("sina_us", "em_us", "sina_fut"),
                          "未知的基准源类型")


class Ust10yTest(unittest.TestCase):
    """美债10Y：涨跌必须用 **bp**，不能用相对涨跌%。

    旧实现拿 change_pct(-0.99%) 去比 risk_off_ust_up(0.30)，阈值等于形同虚设：
    收益率 4.00%→4.04% 只算 +1%（相对），实际是 +4bp，用 % 会严重低估利率冲击。
    """

    SAMPLE = json.dumps({"FormattedQuoteResult": {"FormattedQuote": [{
        "symbol": "US10Y", "name": "U.S. 10 Year Treasury",
        "last": "4.995%", "previous_day_closing": "5.045%", "change": "-0.05",
        "change_pct": "-0.9911%", "last_time": "2026-10-29T03:51:40.000-0400",
        "curmktstatus": "REG_MKT"}]}})

    def setUp(self):
        import fetcher_signal as fs
        self.fs = fs
        self._orig = fs.fetch_text
        fs.fetch_text = lambda *a, **k: self.SAMPLE

    def tearDown(self):
        self.fs.fetch_text = self._orig

    def test_parse_yield_and_bp(self):
        last, bp, chg_pct, as_of = self.fs.fetch_cnbc_ust10y()
        self.assertAlmostEqual(last, 4.995, places=6)
        self.assertAlmostEqual(bp, -5.0, places=1)
        self.assertAlmostEqual(chg_pct, -0.99, places=2)
        self.assertIn("美东", as_of)

    def test_bp_differs_from_relative_pct(self):
        """4.00%→4.04% 应为 +4bp，而相对变化是 +1% —— 两者不可混用。"""
        self.fs.fetch_text = lambda *a, **k: json.dumps(
            {"FormattedQuoteResult": {"FormattedQuote": [{
                "last": "4.04%", "previous_day_closing": "4.00%",
                "last_time": "2026-10-29T03:51:40.000-0400"}]}})
        _last, bp, chg_pct, _ = self.fs.fetch_cnbc_ust10y()
        self.assertAlmostEqual(bp, 4.0, places=1)
        self.assertGreater(chg_pct, 0.9)

    def test_empty_quote_raises(self):
        self.fs.fetch_text = lambda *a, **k: json.dumps(
            {"FormattedQuoteResult": {"FormattedQuote": []}})
        with self.assertRaises(ValueError):
            self.fs.fetch_cnbc_ust10y()

    def test_threshold_is_bp_and_old_key_gone(self):
        self.assertEqual(C.STRATEGY_PARAMS["risk_off_ust_bp"], 30.0)
        self.assertNotIn("risk_off_ust_up", C.STRATEGY_PARAMS)

    def test_tentcent_tnx_removed(self):
        """腾讯 usTNX 已不返回数据（2026-10-29 实测），不得再挂在配置里。"""
        self.assertNotIn("usTNX", self.fs.TENCENT_SYMBOLS)

    def test_signal_order_prefers_ust_over_vix(self):
        """面板顺序固定：纳指现货 → 纳指期货 → 标普现货 → 标普期货 → 美债 → VIX。

        用临时库乱序写入再读回，验证 SQL 里的 CASE 排序真的生效
        （别依赖插入顺序——重跑补数时顺序会变）。
        """
        import tempfile
        import datetime as _dt
        import db as qdb
        p = tempfile.mktemp(suffix=".db")
        try:
            init_conn = qdb.init_db(p)      # 必须显式关闭：Windows 上不关就删不掉文件
            conn = qdb.get_conn(p)
            d = _dt.date.today().isoformat()
            for sym, name in [("VIX", "VIX波动率"), ("UST10Y", "美债10Y收益率"),
                              ("SPX", "标普500现货"), ("IXIC", "纳指现货"),
                              ("NDX_FUT", "纳指期货"), ("SPX_FUT", "标普500期货")]:
                qdb.upsert_row(
                    conn, "qdii_us_signal",
                    ["date", "symbol", "name", "last_price", "change_pct",
                     "is_realtime", "as_of", "fetched_at", "change_bp"],
                    [d, sym, name, 1.0, 0.0, 0, None, None, None], "date,symbol")
            conn.commit()
            conn.close()
            init_conn.close()
            rows = self.fs.get_latest_us_signal(p)
            self.assertEqual([r["symbol"] for r in rows],
                             ["IXIC", "NDX_FUT", "SPX", "SPX_FUT", "UST10Y", "VIX"])
        finally:
            if os.path.exists(p):
                os.remove(p)


class RiskOffAssessTest(unittest.TestCase):
    """避险防御：VIX（绝对水平/跳升）与美债10Y（单日 bp）并列、各自归因。

    踩坑回归：旧实现把两者写进同一句且只输出 VIX 数值，于是美债触发时理由会写
    「避险升温（VIX 15.0）→ ×0.5」——一个低于 30 阈值的数字却减了半，看着就是算错。
    """

    def test_nothing_triggers(self):
        f, hits, warns = strategy.risk_off_assess(vix=15.0, ust_bp=-2.0)
        self.assertEqual(f, 1.0)
        self.assertEqual(hits, [])
        self.assertEqual(warns, [])

    def test_vix_warn_zone_only_warns(self):
        f, hits, warns = strategy.risk_off_assess(vix=25.0)
        self.assertEqual(f, 1.0)            # 预警区不减额
        self.assertEqual(hits, [])
        self.assertTrue(any("预警区" in w for w in warns))

    def test_vix_hard_triggers(self):
        f, hits, _ = strategy.risk_off_assess(vix=32.0)
        self.assertEqual(f, 0.5)
        self.assertTrue(any("VIX" in h for h in hits))

    def test_vix_jump_warns(self):
        _f, hits, warns = strategy.risk_off_assess(vix=18.0, vix_chg=25.0)
        self.assertEqual(hits, [])
        self.assertTrue(any("跳升" in w for w in warns))

    def test_ust_hard_triggers_and_attribution(self):
        f, hits, _ = strategy.risk_off_assess(vix=15.0, ust_bp=35.0)
        self.assertEqual(f, 0.5)
        self.assertTrue(any("美债10Y" in h for h in hits))
        self.assertFalse(any("VIX" in h for h in hits))   # 不能把美债的锅甩给 VIX

    def test_ust_warn_zone_only_warns(self):
        f, hits, warns = strategy.risk_off_assess(vix=15.0, ust_bp=20.0)
        self.assertEqual(f, 1.0)
        self.assertEqual(hits, [])
        self.assertTrue(any("逼近" in w for w in warns))

    def test_both_trigger_listed(self):
        f, hits, _ = strategy.risk_off_assess(vix=40.0, ust_bp=50.0)
        self.assertEqual(f, 0.5)                              # 只乘一次 0.5，不叠加
        self.assertTrue(any("VIX" in h for h in hits))
        self.assertTrue(any("美债10Y" in h for h in hits))

    def test_missing_values_are_safe(self):
        f, hits, warns = strategy.risk_off_assess(None, None, None)
        self.assertEqual((f, hits, warns), (1.0, [], []))

    def test_category_reason_attributes_ust_when_vix_norm(self):
        stats = dict(total=2, open=2, limit=0, suspend=0, min_limit=None, companies=["x"])
        us = {"IXIC": {"symbol": "IXIC", "name": "纳指现货", "change_pct": 0.4},
              "VIX": {"last_price": 15.0, "change_pct": 3.0},
              "UST10Y": {"last_price": 5.0, "change_bp": 40.0}}
        r = strategy.recommend_category("纳斯达克100", stats, us, None)
        self.assertAlmostEqual(r["multiple"], 0.5)
        line = [x for x in r["reasons"] if "避险防御" in x]
        self.assertTrue(line)
        self.assertIn("美债10Y", line[0])
        self.assertNotIn("VIX", line[0])


class PlatformFeeOrderTest(unittest.TestCase):
    """费率矩阵：同基金的多平台行必须相邻 + 平台顺序固定（页面靠它做 rowspan 合并）。

    踩坑背景：原 get_platform_fee 没有 ORDER BY，行序是 SQLite 物理存储序，
    同一只基金的三个平台可能被别的基金隔开，合并单元格会错行；
    若改按 `ORDER BY code` 又会把 FUND_POOL 的大类顺序（标普500→纳指→日经→DAX）打乱。
    """

    def test_rows_grouped_by_pool_order_then_platform(self):
        import tempfile
        import db as qdb
        import constants as C
        import fetcher_fee
        p = tempfile.mktemp(suffix=".db")
        init_conn = qdb.init_db(p)
        conn = qdb.get_conn(p)
        # 故意乱序写入：先纳指（池里靠后）、且平台倒着来
        for code in ("161130", "161125"):
            for plat in ("理财通", "支付宝", "天天基金"):
                qdb.upsert_row(
                    conn, "qdii_platform_fee",
                    ["code", "platform", "on_sale", "mgmt_fee", "fetched_at"],
                    [code, plat, 1, 0.5, "2026-10-29T16:00:00"], "code,platform")
        conn.commit()
        conn.close()
        init_conn.close()
        try:
            rows = fetcher_fee.get_platform_fee(path=p)
            self.assertEqual(
                [(r["code"], r["platform"]) for r in rows],
                [("161130", "天天基金"), ("161130", "支付宝"), ("161130", "理财通"),
                 ("161125", "天天基金"), ("161125", "支付宝"), ("161125", "理财通")])
            # 纳指100（161130）在池里排在标普500（161125）之前
            self.assertLess(
                [f["code"] for f in C.FUND_POOL].index("161130"),
                [f["code"] for f in C.FUND_POOL].index("161125"))
        finally:
            if os.path.exists(p):
                os.remove(p)


class BoardMergeTest(unittest.TestCase):
    """④ 基金总览：把「申购状态+限额」与「多平台费率」合并成一只基金一个块。

    为什么要合并：两张表各自都有「代码/名称」，同一只基金要来回对代码才能判断
    「这只还能不能买、在哪个平台买便宜」。合并后基金级字段 rowspan 合并、
    平台级字段逐行展开，一屏读完。
    """

    POOL = [
        dict(code="161125", name="易方达标普500指数人民币A", category="标普500"),
        dict(code="161130", name="易方达纳斯达克100ETF联接A", category="纳斯达克100"),
        dict(code="000614", name="华安德国(DAX)联接A", category="德国DAX"),
    ]

    def _fee(self, code, plat, mgmt=0.8, cust=0.2, sales=None,
             rate=1.2, disc=0.12, on_sale=1, source="eastmoney_rate"):
        return dict(code=code, platform=plat, on_sale=on_sale,
                    sub_fee_rate=rate, sub_fee_discount=disc,
                    mgmt_fee=mgmt, custodian_fee=cust,
                    sales_service_fee=sales, source=source)

    def _limit(self, code, status="LIMIT", amount=1000.0, limited=1,
               next_open="", buy_start=10.0):
        return dict(code=code, sub_status=status, limit_amount=amount,
                    is_limited=limited, next_open_date=next_open,
                    buy_start=buy_start)

    def test_one_block_per_fund_with_platforms(self):
        import board
        fees = [self._fee("161125", "天天基金"), self._fee("161125", "支付宝", rate=None, disc=None),
                self._fee("161125", "理财通", source="rule:licai"),
                self._fee("161130", "天天基金", mgmt=0.6)]
        limits = [self._limit("161125"), self._limit("161130", "SUSPEND", 10.0)]
        blocks = board.merge_board(limits, fees, self.POOL)

        self.assertEqual([b["code"] for b in blocks], ["161125", "161130"])
        self.assertEqual(len(blocks[0]["platforms"]), 3)
        self.assertEqual(len(blocks[1]["platforms"]), 1)
        # 基金级字段来自限额表，平台无法提供
        self.assertEqual(blocks[0]["status_text"], "限大额")
        self.assertEqual(blocks[1]["status_text"], "暂停")
        self.assertEqual(blocks[1]["limit_text"], "10")
        # 名称/类别来自基金池（而不是前端再查一次 window.__pool）
        self.assertEqual(blocks[0]["name"], "易方达标普500指数人民币A")
        self.assertEqual(blocks[0]["category"], "标普500")

    def test_fixed_fees_are_fund_level_not_per_platform(self):
        """管理费/托管费/销售服务费是合同条款，各平台一致 → 提到基金级只列一次。"""
        import board
        # 支付宝缺管理费、销售服务费只有天天基金有值，仍要能取出正确值
        fees = [self._fee("161125", "天天基金", sales=0.0),
                self._fee("161125", "支付宝", mgmt=None, cust=None),
                self._fee("161125", "理财通", mgmt=0.8, source="rule:licai")]
        blocks = board.merge_board([self._limit("161125")], fees, self.POOL)
        b = blocks[0]
        self.assertEqual(b["mgmt_fee"], 0.8)
        self.assertEqual(b["custodian_fee"], 0.2)
        self.assertEqual(b["sales_service_fee"], 0.0)
        self.assertEqual(b["annual_fee"], 1.0)     # 0.8 + 0.2 + 0.0
        # 平台级只保留真正按平台变的字段
        for p in b["platforms"]:
            self.assertNotIn("mgmt_fee", p)

    def test_limit_only_fund_not_dropped(self):
        """只有申购状态、没有任何费率行的基金必须仍出现在表里（否则暂停的基金会消失）。

        追加部分的顺序按 FUND_POOL 秩（标普500 → 纳指100 → DAX），不能按接口返回序。
        """
        import board
        fees = [self._fee("161130", "天天基金")]
        # 故意把 DAX（池里最后一个）放在标普500 前面传进来，验证排序不是「照抄入参顺序」
        limits = [self._limit("000614", "SUSPEND", 100.0),
                  self._limit("161125", "SUSPEND", 10.0),
                  self._limit("161130", "OPEN", 0.0, limited=0)]
        blocks = board.merge_board(limits, fees, self.POOL)
        self.assertEqual([b["code"] for b in blocks],
                         ["161130", "161125", "000614"])
        self.assertEqual(blocks[1]["platforms"], [])
        self.assertEqual(blocks[1]["status_text"], "暂停")

    def test_platform_order_and_approx_flag(self):
        import board
        fees = [self._fee("161125", "理财通", source="rule:licai"),
                self._fee("161125", "券商", source="legacy"),
                self._fee("161125", "天天基金"),
                self._fee("161125", "支付宝")]
        blocks = board.merge_board([self._limit("161125")], fees, self.POOL)
        plats = [p["platform"] for p in blocks[0]["platforms"]]
        self.assertEqual(plats, ["天天基金", "支付宝", "理财通"])   # 券商行被跳过
        approx = {p["platform"]: p["approx"] for p in blocks[0]["platforms"]}
        self.assertTrue(approx["理财通"])
        self.assertFalse(approx["天天基金"])

    def test_summary_uses_same_100_yuan_threshold_as_strategy(self):
        """「限大额但日限 <100 元」= 等同变相暂停，必须与 strategy 的阈值一致。"""
        import board
        fees = [self._fee(c, "天天基金") for c in ("161125", "161130", "000614")]
        limits = [self._limit("161125", "SUSPEND", 10.0),      # 暂停
                  self._limit("161130", "LIMIT", 10.0),        # 变相暂停
                  self._limit("000614", "LIMIT", 1000.0)]      # 真能买
        blocks = board.merge_board(limits, fees, self.POOL)
        s = board.board_summary(blocks)
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["suspend"], 1)
        self.assertEqual(s["limited"], 2)
        self.assertEqual(s["nominal_only"], 1)
        self.assertEqual(s["min_limit"], 10.0)
        self.assertEqual(s["usable"], 1)

    def test_limit_amount_unlimited_when_not_limited(self):
        import board
        fees = [self._fee("161125", "天天基金")]
        blocks = board.merge_board(
            [self._limit("161125", "OPEN", 1000000000.0, limited=0)], fees, self.POOL)
        self.assertEqual(blocks[0]["limit_text"], "不限")


class BoardSortTest(unittest.TestCase):
    """④ 基金总览的表头排序。

    丁总口径：点**基金级**列 → 改变「哪只基金排在前面」；点**平台级**列 →
    只改变同一只基金内部各平台行的先后，**各基金之间的顺序不变**。
    排序统一在后端做（前端只传列键），所以口径与页面天然一致、可单测。
    """

    def _blocks(self):
        return [
            dict(code="161125", category="标普500", sub_status="SUSPEND",
                 limit_amount=10.0, next_open_date="", buy_start=10.0,
                 mgmt_fee=0.8, custodian_fee=0.2, sales_service_fee=0.0,
                 annual_fee=1.0,
                 platforms=[dict(platform="天天基金", on_sale=0, sub_fee_rate=1.2,
                                 sub_fee_discount=0.12),
                            dict(platform="支付宝", on_sale=0, sub_fee_rate=None,
                                 sub_fee_discount=None),
                            dict(platform="理财通", on_sale=0, sub_fee_rate=1.2,
                                 sub_fee_discount=0.12)]),
            dict(code="096001", category="标普500", sub_status="LIMIT",
                 limit_amount=50.0, next_open_date="", buy_start=10.0,
                 mgmt_fee=1.0, custodian_fee=0.2, sales_service_fee=0.0,
                 annual_fee=1.2,
                 platforms=[dict(platform="天天基金", on_sale=1, sub_fee_rate=1.5,
                                 sub_fee_discount=0.15),
                            dict(platform="支付宝", on_sale=1, sub_fee_rate=None,
                                 sub_fee_discount=None),
                            dict(platform="理财通", on_sale=1, sub_fee_rate=1.5,
                                 sub_fee_discount=0.15)]),
            dict(code="161130", category="纳斯达克100", sub_status="LIMIT",
                 limit_amount=10.0, next_open_date="2026-11-02", buy_start=10.0,
                 mgmt_fee=0.5, custodian_fee=0.1, sales_service_fee=0.0,
                 annual_fee=0.6,
                 platforms=[dict(platform="天天基金", on_sale=1, sub_fee_rate=1.2,
                                 sub_fee_discount=0.12)]),
        ]

    def test_fund_sort_by_annual_fee(self):
        import board
        out = board.sort_blocks(self._blocks(), "annual_fee")
        self.assertEqual([b["code"] for b in out], ["161130", "161125", "096001"])
        out_desc = board.sort_blocks(self._blocks(), "annual_fee", True)
        self.assertEqual([b["code"] for b in out_desc], ["096001", "161125", "161130"])

    def test_fund_sort_by_limit_amount(self):
        import board
        out = board.sort_blocks(self._blocks(), "limit_amount")
        # 10, 10, 50 → 同值保持原顺序（稳定排序）
        self.assertEqual([b["code"] for b in out], ["161125", "161130", "096001"])

    def test_fund_sort_by_status_puts_openable_first(self):
        import board
        out = board.sort_blocks(self._blocks(), "sub_status")
        # 限大额(LIMIT=1) 在 暂停(SUSPEND=2) 之前
        self.assertEqual([b["code"] for b in out], ["096001", "161130", "161125"])

    def test_fund_sort_by_category_follows_pool_order(self):
        """类别排序按 FUND_POOL 的类别序，不按字符串码点（否则日经会排到标普前面）。"""
        import board
        import constants as C
        out = board.sort_blocks(self._blocks(), "category")
        cats = [b["category"] for b in out]
        pool_cats = []
        for f in C.FUND_POOL:
            if f["category"] not in pool_cats:
                pool_cats.append(f["category"])
        # 排序结果里的类别相对顺序必须与 FUND_POOL 的类别序一致
        pool_order = {c: i for i, c in enumerate(pool_cats)}
        idxs = [pool_order[c] for c in cats]
        self.assertEqual(idxs, sorted(idxs))
        # 类别序以 FUND_POOL 为准（纳斯达克100 在前、标普500 次之）
        self.assertLess(pool_cats.index("纳斯达克100"), pool_cats.index("标普500"))

    def test_platform_sort_keeps_fund_order(self):
        """平台级排序只动块内行序，基金的先后必须完全不变。"""
        import board
        blocks = self._blocks()
        before = [b["code"] for b in blocks]
        out = board.sort_platforms(blocks, "platform")
        self.assertEqual([b["code"] for b in out], before)
        # 块内平台顺序按固定秩：天天基金 → 支付宝 → 理财通
        self.assertEqual([p["platform"] for p in out[0]["platforms"]],
                         ["天天基金", "支付宝", "理财通"])
        # 降序则反过来
        out_desc = board.sort_platforms(blocks, "platform", True)
        self.assertEqual([p["platform"] for p in out_desc[0]["platforms"]],
                         ["理财通", "支付宝", "天天基金"])
        self.assertEqual([b["code"] for b in out_desc], before)

    def test_platform_sort_by_fee_and_onsale(self):
        import board
        blocks = self._blocks()
        out = board.sort_platforms(blocks, "sub_fee")
        # 支付宝没有折后价 → 排最后；天天基金/理财通同价，保持原顺序
        self.assertEqual([p["platform"] for p in out[0]["platforms"]],
                         ["天天基金", "理财通", "支付宝"])
        out2 = board.sort_platforms(blocks, "on_sale")
        self.assertEqual([p["platform"] for p in out2[0]["platforms"]],
                         ["天天基金", "支付宝", "理财通"])

    def test_null_values_always_last(self):
        """空值恒定排最后，且不随升降序翻转（否则降序时空值会跑到最前面）。"""
        import board
        blocks = self._blocks()
        # 161125 / 096001 的下一开放日为空，161130 有值（2026-11-02）
        asc = board.sort_blocks(blocks, "next_open_date")
        self.assertEqual(asc[0]["code"], "161130")
        self.assertEqual([b["code"] for b in asc[1:]], ["161125", "096001"])
        desc = board.sort_blocks(blocks, "next_open_date", True)
        self.assertEqual(desc[0]["code"], "161130")
        self.assertEqual([b["code"] for b in desc[1:]], ["161125", "096001"])

    def test_unknown_key_is_noop(self):
        import board
        blocks = self._blocks()
        self.assertEqual([b["code"] for b in board.sort_blocks(blocks, "nope")],
                         [b["code"] for b in blocks])
        self.assertEqual([b["code"] for b in board.sort_platforms(blocks, "nope")],
                         [b["code"] for b in blocks])



class PoolExpansionTest(unittest.TestCase):
    """基金池扩充到 67 只 / 9 个大类后的口径守护（2026-10-29）。"""

    def test_pool_size_and_uniqueness(self):
        codes = [f["code"] for f in C.FUND_POOL]
        # 59 只（66 只截图清单去掉丁总说「多粘贴了」的 8 只后）
        self.assertGreaterEqual(len(codes), 55, "基金池应覆盖理财通清单上的主流场外 QDII")
        self.assertEqual(len(codes), 59, "基金池数量变了，请同步核对是否误删/误加")
        self.assertEqual(len(codes), len(set(codes)), "基金代码重复")

    def test_every_fund_has_required_fields(self):
        for f in C.FUND_POOL:
            for k in ("code", "name", "index_name", "category", "company", "fund_type"):
                self.assertTrue(f.get(k), "%s 缺少字段 %s" % (f.get("code"), k))

    def test_every_category_in_order(self):
        cats = {f["category"] for f in C.FUND_POOL}
        for c in cats:
            self.assertIn(c, C.CATEGORY_ORDER, "类别 %s 不在 CATEGORY_ORDER 里" % c)

    def test_new_categories_present(self):
        cats = {f["category"] for f in C.FUND_POOL}
        for c in ("纳斯达克100", "标普500", "日经225", "德国DAX",
                  "法国CAC40", "英国富时100"):
            self.assertIn(c, cats, "缺少大类 %s" % c)

    def test_removed_categories_gone(self):
        """丁总 2026-10-29 明确：纳斯达克生物科技 / 纳斯达克科技 / 标普500等权 不要。"""
        cats = {f["category"] for f in C.FUND_POOL}
        for c in ("纳斯达克生物科技", "纳斯达克科技", "标普500等权"):
            self.assertNotIn(c, cats, "大类 %s 已按要求移除，却又出现了" % c)
        for sym in ("NBI", "NXT_ETF"):
            self.assertNotIn(sym, C.CATEGORY_SIGNAL.get("标普500", []))
        for e in C.PREMIUM_ETFS:
            self.assertNotIn(e["code"], ("513290", "159509"),
                             "已移除大类的场内 ETF 不该留在溢价清单")

    def test_online_code_points_to_real_etf(self):
        """场外基金的「场内对应」必须是真的场内 ETF 代码（6 位、纯数字）。"""
        for f in C.FUND_POOL:
            oc = f.get("online_code") or ""
            if oc:
                self.assertEqual(len(oc), 6, "%s 的 online_code 不合法" % f["code"])
                self.assertTrue(oc.isdigit(), "%s 的 online_code 非数字" % f["code"])

    def test_lof_c_share_has_no_online_code(self):
        """LOF 的 C 类份额只能场外申购，场内代码是 A 类的 → 不能填自己的代码。"""
        for f in C.FUND_POOL:
            if f["code"] in ("012870", "012860"):
                self.assertEqual(f.get("online_code"), "",
                                 "%s 是 LOF 的 C 类份额，不应有场内代码" % f["code"])


class SignalCoverageTest(unittest.TestCase):
    """全球市场信号：每个大类都要有自有信号，信号表要覆盖所有被引用的 symbol。"""

    def test_every_category_has_own_signal(self):
        for c in C.CATEGORY_ORDER:
            self.assertIn(c, C.CATEGORY_SIGNAL, "大类 %s 没有配置信号" % c)
            self.assertTrue(C.CATEGORY_SIGNAL[c], "大类 %s 的信号列表为空" % c)
            self.assertEqual(C.CATEGORY_OWN_SIGNAL.get(c), C.CATEGORY_SIGNAL[c][0])

    def test_all_signal_symbols_declared(self):
        """决策里引用的每个 symbol 都要在 MARKET_SIGNALS 里登记（否则面板没有它的标签）。"""
        declared = {s[0] for s in C.MARKET_SIGNALS}
        for c, syms in C.CATEGORY_SIGNAL.items():
            for sym in syms:
                self.assertIn(sym, declared,
                              "类别 %s 引用的 %s 未在 MARKET_SIGNALS 登记" % (c, sym))

    def test_market_groups_cover_all_signals(self):
        declared = {s[0] for s in C.MARKET_SIGNALS}
        groups = set(C.MARKET_GROUPS)
        for sym, _name, grp, _kind, _code in C.MARKET_SIGNALS:
            self.assertIn(grp, groups, "%s 的分组 %s 不在 MARKET_GROUPS 里" % (sym, grp))
        self.assertEqual(declared, set(s[0] for s in C.MARKET_SIGNALS))

    def test_european_signals_are_real_indices(self):
        """DAX/CAC40/FTSE 现在走东财真实指数，类型标签必须是「指数」且不得再标「代理」。"""
        real = {"DAX": "德国DAX指数", "CAC40": "法国CAC40指数", "FTSE": "英国富时100指数"}
        for sym, name, _grp, kind, _code in C.MARKET_SIGNALS:
            if sym in real:
                self.assertEqual(name, real[sym], "%s 名称应为真实指数名" % sym)
                self.assertIn("指数", kind, "%s 类型应为指数" % sym)
                self.assertNotIn("代理", kind, "%s 已是真实指数，不应再标代理" % sym)

    def test_market_signals_have_exchange_code(self):
        """每个信号都登记了交易所代码（第 5 字段），供面板展示对照行情软件。"""
        # 期望代码：现货指数 / 期货 / 其他
        expect = {"IXIC": "IXIC", "NDX_FUT": "NQ", "SPX": "SPX", "SPX_FUT": "ES",
                  "NK": "NKY", "DAX": "DAX", "CAC40": "CAC", "FTSE": "UKX",
                  "UST10Y": "US10Y", "VIX": "VIX"}
        for row in C.MARKET_SIGNALS:
            self.assertIn(row[0], expect, "%s 应在 expect 表中" % row[0])
            self.assertEqual(len(row), 5, "%s 应为 5 元组(symbol,name,group,kind,code)" % row[0])
            self.assertEqual(row[4], expect[row[0]],
                             "%s 的交易所代码应为 %s，实际 %s" % (row[0], expect[row[0]], row[4]))


class EmGlobalIndexTest(unittest.TestCase):
    """欧洲三大指数改走东财真实指数（100.GDAXI/FCHI/FTSE）。

    沙箱到东财 push2 被墙，故用 mock 验证解析逻辑；生产 VPS 直连。
    """

    SAMPLE = json.dumps({"rc": 0, "data": {
        "f43": "19234.56", "f57": "GDAXI", "f58": "德国DAX",
        "f169": "-0.30", "f170": "-58.23"}})

    def setUp(self):
        import fetcher_signal as fs
        self.fs = fs
        self._orig = fs.fetch_text
        fs.fetch_text = lambda *a, **k: self.SAMPLE

    def tearDown(self):
        self.fs.fetch_text = self._orig

    def test_parse_dax(self):
        res = self.fs.fetch_em_global_index()
        self.assertIn("DAX", res)
        name, price, chg, chg_val = res["DAX"]
        self.assertEqual(name, "德国DAX指数")
        self.assertAlmostEqual(price, 19234.56, places=2)
        self.assertAlmostEqual(chg, -0.30, places=2)
        # f170 = 涨跌额（绝对变动），直接作为「涨跌值」
        self.assertAlmostEqual(chg_val, -58.23, places=2)

    def test_all_three_present(self):
        # 三指数各自独立 try/except，逐个验证 secid 映射（mock 对三个 secid 返回同一份样本）
        res = self.fs.fetch_em_global_index()
        self.assertEqual(set(res), {"DAX", "CAC40", "FTSE"})

    def test_empty_raises(self):
        self.fs.fetch_text = lambda *a, **k: json.dumps({"rc": 0, "data": {}})
        with self.assertRaises(ValueError):
            self.fs.fetch_em_global_index()


class CnbcGlobalIndexTest(unittest.TestCase):
    """全球市场信号欧洲/日本指数改走 CNBC 主源（.DAX/.CAC/.FTSE/.N225）。

    沙箱到 CNBC 走代理可通，但单测用 mock 隔离，验证解析（含千分位逗号、带符号、
    百分号清洗）与 symbol 映射（.N225→NK、展示名「日经225指数」）。
    """
    SAMPLE = json.dumps({"FormattedQuoteResult": {"FormattedQuote": [
        {"symbol": ".DAX", "name": "Germany DAX", "last": "26,039.68",
         "change": "+335.66", "change_pct": "+1.31%", "previous_day_closing": "25,704.02"},
        {"symbol": ".CAC", "name": "CAC 40 Index", "last": "7,970.00",
         "change": "0.00", "change_pct": "0.00%", "previous_day_closing": "7,970.00"},
        {"symbol": ".FTSE", "name": "FTSE 100", "last": "10,722.07",
         "change": "+27.57", "change_pct": "+0.26%", "previous_day_closing": "10,694.50"},
        {"symbol": ".N225", "name": "Nikkei 225 Index", "last": "63,019.78",
         "change": "-1,639.69", "change_pct": "-2.54%", "previous_day_closing": "64,659.47"},
    ]}})

    def setUp(self):
        import fetcher_signal as fs
        self.fs = fs
        self._orig = fs.fetch_text
        fs.fetch_text = lambda *a, **k: self.SAMPLE

    def tearDown(self):
        self.fs.fetch_text = self._orig

    def test_symbols_present(self):
        res = self.fs.fetch_cnbc_global_index()
        self.assertEqual(set(res), {"DAX", "CAC40", "FTSE", "NK"})

    def test_parse_dax(self):
        res = self.fs.fetch_cnbc_global_index()
        name, price, pct, chg, prev = res["DAX"]
        self.assertEqual(name, "德国DAX指数")
        self.assertAlmostEqual(price, 26039.68, places=2)
        self.assertAlmostEqual(pct, 1.31, places=2)
        self.assertAlmostEqual(chg, 335.66, places=2)
        self.assertAlmostEqual(prev, 25704.02, places=2)

    def test_parse_nikkei(self):
        res = self.fs.fetch_cnbc_global_index()
        name, price, pct, chg, prev = res["NK"]
        self.assertEqual(name, "日经225指数")
        self.assertAlmostEqual(price, 63019.78, places=2)
        self.assertAlmostEqual(pct, -2.54, places=2)
        self.assertAlmostEqual(chg, -1639.69, places=2)

    def test_cac_zero_change(self):
        res = self.fs.fetch_cnbc_global_index()
        _n, price, pct, chg, _p = res["CAC40"]
        self.assertAlmostEqual(price, 7970.00, places=2)
        self.assertAlmostEqual(pct, 0.0, places=2)
        self.assertAlmostEqual(chg, 0.0, places=2)

    def test_empty_raises(self):
        self.fs.fetch_text = lambda *a, **k: json.dumps({"FormattedQuoteResult": {}})
        with self.assertRaises(ValueError):
            self.fs.fetch_cnbc_global_index()


class ChangeValueTest(unittest.TestCase):
    """change_value（涨跌值/绝对变动）全面铺开：美债=bp、指数/期货/VIX=点数差。"""

    def test_sina_us_index_change_value(self):
        import fetcher_signal as fs
        # gb_inx 实测：最新价 7720.39 / [2]涨跌幅 -0.30 / [4]涨跌额 -23.59
        sample = ('var hq_str_gb_inx="标普500指数,7720.3901,-0.30,2026-10-30 04:33:03,'
                  '-23.5900,7699.6099,7732.2100,7690.9399,7844.5200,6316.9102,'
                  '3300311487,2995724322,0,0.00,--,0.00,0.00,0.00,0.00,0,0,0.0000,'
                  '0.00,0.0000,,Oct 29 04:32PM EDT,7743.9800,0,1,2026";')
        orig = fs.fetch_text
        fs.fetch_text = lambda *a, **k: sample
        try:
            price, chg, val = fs.fetch_sina_us_index()["gb_inx"]
            self.assertAlmostEqual(price, 7720.39, places=2)
            self.assertAlmostEqual(chg, -0.30, places=2)
            self.assertAlmostEqual(val, -23.59, places=2)  # [4] 涨跌额
        finally:
            fs.fetch_text = orig

    def test_cboe_vix_change_value(self):
        import fetcher_signal as fs
        # 末行=当日，倒数第二行=前一日；close 差即涨跌值
        csv = ("DATE,OPEN,HIGH,LOW,CLOSE\n"
               "10/29/2026,18.0,19.0,17.0,18.50\n"
               "10/30/2026,18.5,19.5,18.0,19.20\n")
        orig = fs.fetch_text
        fs.fetch_text = lambda *a, **k: csv
        try:
            close, chg, date, val = fs.fetch_cboe_vix()
            self.assertAlmostEqual(close, 19.20, places=2)
            self.assertAlmostEqual(val, 19.20 - 18.50, places=2)
        finally:
            fs.fetch_text = orig

    def test_change_value_persist(self):
        import db, fetcher_signal as fs
        # 用内存库验证 upsert 的 10 列对齐（避免 Windows 临时文件锁）
        conn = db.get_conn(":memory:")
        conn.execute(
            "CREATE TABLE qdii_us_signal ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, symbol TEXT, name TEXT, "
            "last_price REAL, change_pct REAL, is_realtime INTEGER, as_of TEXT, "
            "fetched_at TEXT, change_bp REAL, change_value REAL, UNIQUE(date, symbol))")
        db.upsert_row(conn, "qdii_us_signal",
                      ["date", "symbol", "name", "last_price", "change_pct",
                       "is_realtime", "as_of", "fetched_at", "change_bp", "change_value"],
                      ["2026-10-30", "SPX", "标普500现货", 7720.39, -0.30,
                       0, "", "x", "", -23.59], "date,symbol")
        conn.commit()
        row = conn.execute(
            "SELECT change_value FROM qdii_us_signal WHERE symbol='SPX'").fetchone()
        self.assertAlmostEqual(row["change_value"], -23.59, places=2)
        conn.close()


class BoardTrackingErrorTest(unittest.TestCase):
    """跟踪误差并入 ④ 基金总览表（原来是独立的一张表）。"""

    def test_block_carries_te(self):
        import board
        blocks = board.merge_board(
            [dict(code="161130", sub_status="OPEN", limit_amount=0.0,
                  is_limited=0, next_open_date="", buy_start=10.0)],
            [dict(code="161130", platform="天天基金", on_sale=1, sub_fee_rate=1.2,
                  sub_fee_discount=0.12, mgmt_fee=0.5, custodian_fee=0.1,
                  sales_service_fee=0.0, source="eastmoney_rateinfo")],
            [dict(code="161130", name="易方达纳斯达克100ETF联接(QDII-LOF)A",
                  category="纳斯达克100")],
            [dict(code="161130", te_computed=1.55)])
        b = blocks[0]
        self.assertAlmostEqual(b["te"], 1.55, places=3)
        self.assertEqual(b["te_text"], "1.55%")

    def test_te_sort_key_exists(self):
        import board
        self.assertIn("te", board.FUND_SORT_KEYS)


if __name__ == "__main__":
    unittest.main()
