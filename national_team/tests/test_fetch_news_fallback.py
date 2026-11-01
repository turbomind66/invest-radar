"""新闻兜底源回归测试（v2.6.12，完全离线）。

验证 _fetch_remote_news 在「本机三源全空」时能从公开快照 data.json 借到新闻：
1. 正常解析 news + news_fallback，返回同构条目（含 _score / src=公开快照），跨日期同题去重；
2. 网络异常 / 坏 JSON 静默返回空，绝不抛错阻断主流程；
3. 条目缺字段（无 url / 无 time）不崩，time 缺省回退到日期 00:00；
4. NT_NEWS_FALLBACK_URL 为空时直接返回空（自托管关闭兜底场景）。
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


def _payload():
    return {
        "news": {
            "2026-10-30": [
                {"title": "国家队三季度持仓浮现 社保基金新进103家A股公司",
                 "time": "2026-10-30 00:27", "url": "https://eastmoney.com/a1"},
                {"title": "日本国家队将加速抛售ETF 年内已卖2440亿日元",
                 "time": "2026-10-30 18:41", "url": "https://eastmoney.com/a2"},
            ],
        },
        "news_fallback": {
            "date": "2026-10-30",
            "items": [
                {"title": "中央汇金最新动向解读", "time": "2026-10-29 09:00",
                 "url": "https://eastmoney.com/b1"},
                # 与 news 里同题（标题一致）→ 跨日期去重应被去掉
                {"title": "日本国家队将加速抛售ETF 年内已卖2440亿日元",
                 "time": "", "url": ""},
            ],
        },
    }


class TestFetchRemoteNews(unittest.TestCase):
    def test_parse_news_and_fallback(self):
        with patch.object(fetcher, "_get", return_value=json.dumps(_payload())):
            out = fetcher._fetch_remote_news()
        titles = [it["title"] for it in out]
        self.assertIn("国家队三季度持仓浮现 社保基金新进103家A股公司", titles)
        self.assertIn("日本国家队将加速抛售ETF 年内已卖2440亿日元", titles)
        self.assertIn("中央汇金最新动向解读", titles)
        # 同题跨日期去重：fallback 里那条与 news 重复的应被去掉
        self.assertEqual(
            titles.count("日本国家队将加速抛售ETF 年内已卖2440亿日元"), 1)
        for it in out:
            self.assertEqual(it["src"], "公开快照")
            self.assertIn("_score", it)

    def test_missing_keys_no_crash(self):
        bad = {"news": {"2026-10-30": [{"title": "只有标题"}]}}
        with patch.object(fetcher, "_get", return_value=json.dumps(bad)):
            out = fetcher._fetch_remote_news()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "只有标题")
        # time 缺省回退到 日期 00:00
        self.assertTrue(out[0]["time"].startswith("2026-10-30"))

    def test_network_error_returns_empty(self):
        with patch.object(fetcher, "_get", side_effect=Exception("boom")):
            out = fetcher._fetch_remote_news()
        self.assertEqual(out, [])

    def test_bad_json_returns_empty(self):
        with patch.object(fetcher, "_get", return_value="<html>blocked</html>"):
            out = fetcher._fetch_remote_news()
        self.assertEqual(out, [])

    def test_empty_url_env_returns_empty(self):
        with patch.object(fetcher, "NT_NEWS_FALLBACK_URL", ""):
            out = fetcher._fetch_remote_news()
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
