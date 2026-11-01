"""新闻兜底源回归测试（v2.6.12 / v2.7.1，完全离线）。

验证 _fetch_remote_news（兜底取数）与 fetch_news（兜底触发口径）：
- v2.6.12：三源全空时从公开快照 data.json 借到新闻（见 TestFetchRemoteNews）；
- v2.7.1：触发口径改为「本地条数 < 快照条数就借差量」，根治 VPS 因新浪恒命中 1 条
  而永远借不到东财被拦掉其余条数的问题（见 TestFetchNewsBorrowDiff）。
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


class TestFetchNewsBorrowDiff(unittest.TestCase):
    """v2.7.1 触发口径：本地条数 < 快照条数就借差量（按标题去重并入）。"""

    def _snapshot(self, n=12):
        items = []
        for i in range(n):
            items.append({"title": "快照新闻%d" % i,
                          "time": "2026-10-30 %02d:%02d" % (i // 60, i % 60),
                          "url": "https://em.com/s%d" % i})
        return {"news": {"2026-10-30": items},
                "news_fallback": {"date": "2026-10-30", "items": []}}

    def _patch(self, mock_get, mock_extract, mock_score, local_items, snapshot=None,
               block_local=True):
        fb = fetcher.NT_NEWS_FALLBACK_URL

        def _get(url, ref=None):
            if fb and fb in url:
                if snapshot is None:
                    raise AssertionError("不应访问兜底 URL")
                return json.dumps(snapshot)
            if block_local:
                raise Exception("blocked")  # 模拟东财/同花顺被拦
            return "{}"

        mock_get.side_effect = _get
        mock_extract.return_value = local_items
        mock_score.return_value = fetcher.SCORE_STRONG

    @patch.object(fetcher, "_news_score", return_value=0)
    @patch.object(fetcher, "_extract_news_from_json")
    @patch.object(fetcher, "_get")
    def test_local_short_borrows_diff(self, mock_get, mock_extract, mock_score):
        # VPS 真实场景：三源被拦，仅新浪命中 1 条；快照有 12 条 → 借差量补齐到 12
        local = [{"title": "新浪独有的新闻", "time": "2026-10-30 23:59",
                  "url": "https://sina.com/x"}]
        snap = self._snapshot(12)
        # block_local=False：本地源返回空壳、由 mock_extract 产出这 1 条，
        # 模拟「三源被拦/无相关，仅新浪命中 1 条」的真实 VPS 场景
        self._patch(mock_get, mock_extract, mock_score, local, snapshot=snap,
                    block_local=False)
        out = fetcher.fetch_news()
        self.assertEqual(len(out), 12)
        self.assertIn("新浪独有的新闻", [o["title"] for o in out])
        # 借差量：本地 1 条 + 快照 12 条 = 13，截断到 12，故至少 11 条快照被借入
        snap_kept = sum(1 for o in out if o["title"].startswith("快照新闻"))
        self.assertGreaterEqual(snap_kept, 11)

    @patch.object(fetcher, "_news_score", return_value=0)
    @patch.object(fetcher, "_extract_news_from_json")
    @patch.object(fetcher, "_get")
    def test_local_full_no_borrow(self, mock_get, mock_extract, mock_score):
        # 健康环境：本地已集满 12 条 → 不访问兜底 URL、不借
        local = [{"title": "本地新闻%d" % i, "time": "2026-10-30 12:%02d" % i,
                  "url": "https://em.com/l%d" % i} for i in range(12)]
        self._patch(mock_get, mock_extract, mock_score, local, snapshot=None,
                    block_local=False)
        out = fetcher.fetch_news()
        self.assertEqual(len(out), 12)
        self.assertIn("本地新闻0", [o["title"] for o in out])
        # 兜底 URL 未被访问（_get 里 snapshot=None 会 raise AssertionError）

    @patch.object(fetcher, "_news_score", return_value=0)
    @patch.object(fetcher, "_extract_news_from_json")
    @patch.object(fetcher, "_get")
    def test_local_empty_borrows_all(self, mock_get, mock_extract, mock_score):
        # 旧口径也覆盖：三源全空 → 借快照全部 12 条
        snap = self._snapshot(12)
        self._patch(mock_get, mock_extract, mock_score, [], snapshot=snap,
                    block_local=True)
        out = fetcher.fetch_news()
        self.assertEqual(len(out), 12)

    @patch.object(fetcher, "_news_score", return_value=0)
    @patch.object(fetcher, "_extract_news_from_json")
    @patch.object(fetcher, "_get")
    def test_snapshot_fail_local_unaffected(self, mock_get, mock_extract, mock_score):
        # 快照不可达且本地也空 → 静默返回空，绝不抛错
        self._patch(mock_get, mock_extract, mock_score, [], snapshot=None,
                    block_local=True)
        out = fetcher.fetch_news()
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
