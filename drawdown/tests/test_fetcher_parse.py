"""drawdown/tests/test_fetcher_parse.py — 新数据源解析离线单测（mock _get，不联网）。

覆盖东财封禁期启用的三个替代源：
- fetch_tigzig: yfinance 包装器，嵌套 {date:{symbol:close}} 结构
- fetch_csindex: 中证官网 perf API，需剔除 19900101 基日占位行
- fetch_tencent_a: 腾讯 fqkline，end 参数翻页 + 去重
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, BASE):
    if p not in sys.path:
        sys.path.insert(0, p)

from drawdown import fetcher  # noqa: E402


class TestTigzigParse(unittest.TestCase):
    def test_nested_structure_sorted(self):
        payload = ('{"2020-01-02":{"^N225":100},'
                   '"2020-01-01":{"^N225":99},'
                   '"2020-01-03":{"^N225":null}}')
        with mock.patch.object(fetcher, "_get", return_value=payload):
            rows = fetcher.fetch_tigzig("^N225", retries=1)
        self.assertEqual(rows, [("2020-01-01", 99.0), ("2020-01-02", 100.0)])
        # null 值被跳过

    def test_empty_returns_empty(self):
        with mock.patch.object(fetcher, "_get", return_value="{}"):
            self.assertEqual(fetcher.fetch_tigzig("^BAD", retries=1), [])


class TestCsindexParse(unittest.TestCase):
    def test_drops_base_date_placeholder(self):
        # 19900101 是基日占位（与真实起点同值），须剔除
        payload = ('{"data":['
                   '{"tradeDate":"19900101","close":1000},'
                   '{"tradeDate":"20060104","close":1009.34},'
                   '{"tradeDate":"20060105","close":1032.57}]}')
        with mock.patch.object(fetcher, "_get", return_value=payload):
            rows = fetcher.fetch_csindex("H30269", retries=1)
        self.assertEqual(rows, [("2006-01-04", 1009.34), ("2006-01-05", 1032.57)])

    def test_formats_date_with_dashes(self):
        payload = '{"data":[{"tradeDate":"20041231","close":1000.0}]}'
        with mock.patch.object(fetcher, "_get", return_value=payload):
            rows = fetcher.fetch_csindex("000922", retries=1)
        self.assertEqual(rows, [("2004-12-31", 1000.0)])


class TestTencentParse(unittest.TestCase):
    def test_paging_and_dedup(self):
        # 第一页返回 3 根（含 qfqday key），第二页返回更早 1 根 + 1 根重复
        page1 = ('{"data":{"sh000001":{"qfqday":['
                 '["2020-01-03","o","100.0"],'
                 '["2020-01-02","o","99.0"],'
                 '["2020-01-01","o","98.0"]]}}}')
        page2 = ('{"data":{"sh000001":{"qfqday":['
                 '["2019-12-31","o","97.0"],'
                 '["2020-01-01","o","98.0"]]}}}')
        responses = [page1, page2]
        with mock.patch.object(fetcher, "_get", side_effect=lambda *a, **k: responses.pop(0)):
            rows = fetcher.fetch_tencent_a("sh000001", retries=1, pause=0)
        self.assertEqual(rows, [
            ("2019-12-31", 97.0), ("2020-01-01", 98.0),
            ("2020-01-02", 99.0), ("2020-01-03", 100.0),
        ])

    def test_day_key_fallback(self):
        # 无 qfqday 时用 day（如恒生/ETF）
        payload = '{"data":{"hkHSI":{"day":[["2020-01-02","o","28000.0"]]}}}'
        with mock.patch.object(fetcher, "_get", return_value=payload):
            rows = fetcher.fetch_tencent_a("hkHSI", retries=1, pause=0)
        self.assertEqual(rows, [("2020-01-02", 28000.0)])


if __name__ == "__main__":
    unittest.main()
