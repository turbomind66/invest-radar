"""资金流兜底源回归测试（v2.7.0，完全离线）。

验证 _fetch_remote_fund_flow / _apply_fund_flow_fallback 在「本机资金流覆盖率
不足」时能从公开快照 data.json 的 days[date] 借到四档净额：
1. 正常解析 days[date] 返回 {code: 四档}，借来补齐 etfs 里 main_net 为 None 的 code；
2. 本地已有真实值（main_net 非 None）的 code 绝不被快照复写；
3. 快照某只 main_net 也是 None 时不借（不拿 NULL 覆盖本地 NULL）；
4. 快照不含目标日期 → 借不到，补齐 0 只，不崩；
5. 网络异常 / 坏 JSON 静默返回空，绝不抛错阻断主流程；
6. NT_FUND_FALLBACK_URL 为空时直接返回空（自托管关闭兜底场景）。
"""
import json
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # national_team/
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import fetcher  # noqa: E402

TODAY = "2026-10-30"


def _clear_cache():
    # _FUND_FLOW_CACHE 是模块级缓存，单次运行内复用快照；测试间需清空避免串味
    fetcher._FUND_FLOW_CACHE.clear()


def _payload():
    """公开快照 data.json 的最小子集（只放与兜底相关的字段）。"""
    # 观察池 14 只，这里给 14 只都带真实四档（模拟 runner 正常抓取的结果）
    codes = [c for c, _n, _t in fetcher.WATCHLIST]
    rows = []
    for i, code in enumerate(codes):
        rows.append({
            "code": code, "name": "ETF%d" % i, "cat": "沪深300",
            "price": 1.0 + i, "chg": 0.5, "amount": 1e8,
            # 让第 3 只 main_net 为 None（模拟快照里该只也缺），其余都有值
            "main_net": None if i == 2 else (1e9 - i * 1e7),
            "huge_net": (5e8 - i * 1e6) if i != 2 else None,
            "big_net": (3e8 - i * 1e6) if i != 2 else None,
            "mid_net": (1e8 - i * 1e5) if i != 2 else None,
            "small_net": (-1e8 + i * 1e5) if i != 2 else None,
        })
    return {
        "days": {
            TODAY: rows,
            "2026-10-29": [{"code": codes[0], "main_net": 9e8}],  # 别的日期，不应被借
        },
    }


def _make_etfs(missing_all_but=None):
    """构造 etfs 列表：missing_all_but 指定哪只本地有真实值（其余 main_net=None）。"""
    codes = [c for c, _n, _t in fetcher.WATCHLIST]
    etfs = []
    for i, code in enumerate(codes):
        has = (missing_all_but is not None and i == missing_all_but)
        etfs.append({
            "code": code, "name": "ETF%d" % i, "cat": "沪深300",
            "price": 1.0 + i, "chg": 0.5, "amount": 1e8,
            "main_net": (7.7e8 if has else None),  # 本地真实值，用于"不被复写"断言
            "huge_net": (4e8 if has else None),
            "big_net": (2e8 if has else None),
            "mid_net": (1e8 if has else None),
            "small_net": (-1e8 if has else None),
        })
    return etfs


class TestFetchRemoteFundFlow(unittest.TestCase):
    def setUp(self):
        _clear_cache()

    def test_parse_days(self):
        with patch.object(fetcher, "_get", return_value=json.dumps(_payload())):
            out = fetcher._fetch_remote_fund_flow(TODAY)
        self.assertEqual(len(out), len(fetcher.WATCHLIST))
        # 第 3 只快照里 main_net 为 None → 仍返回该 key，但字段为 None
        code2 = fetcher.WATCHLIST[2][0]
        self.assertIsNone(out[code2]["main_net"])
        # 其余有值
        code0 = fetcher.WATCHLIST[0][0]
        self.assertEqual(out[code0]["main_net"], 1e9)
        self.assertEqual(out[code0]["huge_net"], 5e8)

    def test_other_date_not_borrowed(self):
        with patch.object(fetcher, "_get", return_value=json.dumps(_payload())):
            out = fetcher._fetch_remote_fund_flow("2099-01-01")
        self.assertEqual(out, {})


class TestApplyFundFlowFallback(unittest.TestCase):
    def setUp(self):
        _clear_cache()

    def test_fills_missing_only(self):
        # 本地 0 只真实值（全 None），快照 13 只有值、1 只 None
        etfs = _make_etfs(missing_all_but=None)
        with patch.object(fetcher, "_get", return_value=json.dumps(_payload())):
            n = fetcher._apply_fund_flow_fallback(TODAY, etfs)
        # 13 只被补齐（第 3 只在快照里也是 None → 不借）
        self.assertEqual(n, len(fetcher.WATCHLIST) - 1)
        # 被补齐的 code 现在 main_net 非 None
        filled_ok = sum(1 for e in etfs if e["main_net"] is not None)
        self.assertEqual(filled_ok, len(fetcher.WATCHLIST) - 1)
        # 第 3 只（快照里 None）仍为 None
        code2 = fetcher.WATCHLIST[2][0]
        e2 = next(e for e in etfs if e["code"] == code2)
        self.assertIsNone(e2["main_net"])

    def test_local_real_value_not_overwritten(self):
        # 本地第 0 只有真实值 7.7e8；快照里第 0 只是 1e9 —— 必须保留本地 7.7e8
        etfs = _make_etfs(missing_all_but=0)
        with patch.object(fetcher, "_get", return_value=json.dumps(_payload())):
            n = fetcher._apply_fund_flow_fallback(TODAY, etfs)
        code0 = fetcher.WATCHLIST[0][0]
        e0 = next(e for e in etfs if e["code"] == code0)
        self.assertEqual(e0["main_net"], 7.7e8)  # 本地真实值保留
        self.assertEqual(e0["huge_net"], 4e8)
        # 其余缺失的被补齐（除快照里 None 的第 3 只）
        self.assertEqual(n, len(fetcher.WATCHLIST) - 2)  # 第0只本地有，第2只快照无

    def test_no_borrow_when_date_absent(self):
        etfs = _make_etfs(missing_all_but=None)
        with patch.object(fetcher, "_get", return_value=json.dumps(_payload())):
            n = fetcher._apply_fund_flow_fallback("2099-01-01", etfs)
        self.assertEqual(n, 0)
        self.assertTrue(all(e["main_net"] is None for e in etfs))

    def test_network_error_silent(self):
        etfs = _make_etfs(missing_all_but=None)
        with patch.object(fetcher, "_get", side_effect=Exception("boom")):
            n = fetcher._apply_fund_flow_fallback(TODAY, etfs)
        self.assertEqual(n, 0)
        self.assertTrue(all(e["main_net"] is None for e in etfs))

    def test_bad_json_silent(self):
        etfs = _make_etfs(missing_all_but=None)
        with patch.object(fetcher, "_get", return_value="<html>blocked</html>"):
            n = fetcher._apply_fund_flow_fallback(TODAY, etfs)
        self.assertEqual(n, 0)

    def test_empty_url_env_returns_empty(self):
        etfs = _make_etfs(missing_all_but=None)
        with patch.object(fetcher, "NT_FUND_FALLBACK_URL", ""):
            n = fetcher._apply_fund_flow_fallback(TODAY, etfs)
        self.assertEqual(n, 0)


class TestBackfillFundFlowFallback(unittest.TestCase):
    """回补路径（backfill_date）同样要借快照补齐历史行资金流（VPS 重跑回补用）。"""

    def setUp(self):
        _clear_cache()

    def test_backfill_date_borrows_missing_flow(self):
        codes = [c for c, _n, _t in fetcher.WATCHLIST]
        captured = {}

        def fake_hist(url, retries=3):
            # 注意：东财 klines 是「逗号分隔字符串」列表，不是嵌套 list。
            # fflow（资金流）URL 含 "fflow"：故意不带 TODAY → 本地 mn 全为 None
            #   （模拟 VPS 被 push2his 限流）；kline（行情）URL 给 TODAY 行，使 kn 非空。
            if "fflow" in url:
                return json.dumps({"data": {"klines": [",".join(
                    ["2026-10-29", "1", "2", "3", "4", "5"])]}})
            klines = [",".join([TODAY, "1.0", "2.50", "2.60", "2.40", "1e7",
                                "1e8", "x", "-3.0", "x", "x"])]
            return json.dumps({"data": {"klines": klines}})

        def fake_ff(date):
            # 公开快照：TODAY 14 只都带真实四档
            return {code: {"main_net": 1e9, "huge_net": 5e8, "big_net": 3e8,
                           "mid_net": 1e8, "small_net": -1e8} for code in codes}

        def fake_save(date, rows, path=None):
            captured["rows"] = rows

        with patch.object(fetcher, "_hist_get", side_effect=fake_hist), \
             patch.object(fetcher, "_fetch_remote_fund_flow", side_effect=fake_ff), \
             patch.object(fetcher.db, "get_news_by_date", return_value=[]), \
             patch.object(fetcher.db, "save_news", return_value=None), \
             patch.object(fetcher.db, "save_etf_daily", side_effect=fake_save):
            res = fetcher.backfill_date(TODAY)

        self.assertEqual(res["fund_fallback"], len(codes))
        self.assertEqual(len(captured["rows"]), len(codes))
        self.assertTrue(all(r["main_net"] is not None for r in captured["rows"]),
                        "历史回补后 main_net 不应再留 NULL")


if __name__ == "__main__":
    unittest.main()
