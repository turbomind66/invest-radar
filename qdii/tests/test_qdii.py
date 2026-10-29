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
        for e in C.PREMIUM_ETFS:
            self.assertTrue(e.get("bench"), "%s 缺 bench，折算列会永远为空" % e["code"])
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
                [("161125", "天天基金"), ("161125", "支付宝"), ("161125", "理财通"),
                 ("161130", "天天基金"), ("161130", "支付宝"), ("161130", "理财通")])
            # 标普500（161125）在池里排在纳指100（161130）之前
            self.assertLess(
                [f["code"] for f in C.FUND_POOL].index("161125"),
                [f["code"] for f in C.FUND_POOL].index("161130"))
        finally:
            if os.path.exists(p):
                os.remove(p)


if __name__ == "__main__":
    unittest.main()
