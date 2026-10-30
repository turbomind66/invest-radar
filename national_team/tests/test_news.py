"""新闻抓取逻辑单元测试（完全离线，不访问任何网络接口）。

覆盖 2026-10-30 新闻源换源后的关键口径：
  - 标题相关性打分（噪音过滤）
  - 标题/时间归一（东财 <em> 标签、Unix 时间戳）
  - 东财搜索 URL 构造（param= JSON 格式，禁用已废弃的 search_text=）
  - 单源失败不影响其它源

运行：
    python -m unittest discover -s tests -p "test_*.py" -v
"""
import json
import os
import sys
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fetcher  # noqa: E402


class TestNewsTitleNormalization(unittest.TestCase):
    """标题归一：东财搜索结果标题带 <em> 高亮标签。"""

    def test_strip_em_highlight_tags(self):
        raw = "“<em>国家队</em>”三季度持仓浮现 社保基金新进103<em>家</em>A股公司"
        out = fetcher._norm_news_title(raw)
        self.assertNotIn("<em>", out)
        self.assertNotIn("</em>", out)
        self.assertIn("国家队", out)
        self.assertIn("103家A股公司", out)

    def test_collapse_whitespace(self):
        self.assertEqual(fetcher._norm_news_title("  国家队   增持  \n 宽基ETF "),
                         "国家队 增持 宽基ETF")

    def test_empty_title_safe(self):
        self.assertEqual(fetcher._norm_news_title(None), "")
        self.assertEqual(fetcher._norm_news_title(""), "")


class TestNewsTimeNormalization(unittest.TestCase):
    """时间归一：同花顺/新浪给 Unix 时间戳，东财给 'YYYY-MM-DD HH:MM:SS'。"""

    def test_unix_timestamp_converted(self):
        out = fetcher._norm_news_time("1793335013")
        self.assertRegex(out, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_iso_string_trimmed_to_minute(self):
        self.assertEqual(fetcher._norm_news_time("2026-10-30 00:27:28"), "2026-10-30 00:27")

    def test_empty_safe(self):
        self.assertEqual(fetcher._norm_news_time(None), "")
        self.assertEqual(fetcher._norm_news_time(""), "")

    def test_all_formats_sortable_as_string(self):
        """归一后各源时间可按字符串排序（混用时间戳会错乱）。"""
        a = fetcher._norm_news_time("2026-10-30 00:27:28")
        b = fetcher._norm_news_time("1793335013")
        self.assertEqual(len(a), len(b))
        self.assertTrue(a > b or a < b)  # 同格式可比较


class TestNewsScore(unittest.TestCase):
    """相关性打分：过滤上游噪音，只保留国家队资金动向相关条目。"""

    def test_strong_subject_hits(self):
        for t in ("中央汇金发布打假声明：公布21家仿冒公司名单",
                  "证金公司重仓中国神华",
                  "汇金公司一季度持仓曝光"):
            self.assertGreater(fetcher._news_score(t), 0, t)

    def test_guojia_requires_finance_context(self):
        """「国家队」在中文里也指科技/高校/航天等领域的国家队 → 需金融语境。"""
        self.assertGreater(fetcher._news_score("“国家队”三季度持仓浮现 社保基金新进103家A股公司"), 0)
        self.assertEqual(fetcher._news_score("教育部遴选出220所职业高校 打造技能型高校“国家队”"), 0)
        self.assertEqual(fetcher._news_score("半导体“国家队”科创板IPO辅导完成"), 0)

    def test_margin_word_not_matched_as_zhengjin(self):
        """「保证金」含「证金」子串 → 期货保证金通知必须被排除。"""
        self.assertEqual(fetcher._news_score("关于调整生猪期货2703和2705合约公司保证金比例的通知"), 0)

    def test_same_name_company_excluded(self):
        self.assertEqual(fetcher._news_score("汇金科技：2026年第三季度营业收入同比增长12.65%"), 0)
        self.assertEqual(fetcher._news_score("浙商汇金量化臻选股票A：2026年第三季度利润206.42万元"), 0)

    def test_bare_etf_headlines_excluded(self):
        """裸 ETF 行情标题（ETF午评/两市成交额/央行抛售ETF）非国家队动向。"""
        self.assertEqual(fetcher._news_score("ETF午评：酒ETF领涨3.65%，创业板人工智能ETF领跌3.92%"), 0)
        self.assertEqual(fetcher._news_score("ETF两市成交额报3232.05亿元，较昨日此时缩量119.62亿元"), 0)
        self.assertEqual(fetcher._news_score("日本央行：从明年1月起 将每年出售ETF的规模由3300亿日元上调至约8600亿日元"), 0)

    def test_etf_with_buy_context_kept(self):
        self.assertGreater(fetcher._news_score("国家队今日增持宽基ETF"), 0)

    def test_empty_title_zero(self):
        self.assertEqual(fetcher._news_score(""), 0)
        self.assertEqual(fetcher._news_score(None), 0)


class TestEmSearchUrl(unittest.TestCase):
    """东财搜索 URL 构造。"""

    def test_uses_param_json_not_search_text(self):
        """旧版 ?search_text=...&type=12 已被上游废弃（400），必须用 param= JSON。"""
        url = fetcher._em_search_url("国家队")
        self.assertIn("param=", url)
        self.assertNotIn("search_text", url)
        self.assertNotIn("type=12", url)

    def test_param_decodes_to_expected_structure(self):
        url = fetcher._em_search_url("国家队", page=2, page_size=20)
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        p = json.loads(qs["param"][0])
        self.assertEqual(p["keyword"], "国家队")
        self.assertEqual(p["client"], "web")
        self.assertEqual(p["param"]["cmsArticleWebOld"]["pageIndex"], 2)
        self.assertEqual(p["param"]["cmsArticleWebOld"]["pageSize"], 20)
        self.assertEqual(p["param"]["cmsArticleWebOld"]["sort"], "default")

    def test_keyword_is_url_encoded(self):
        url = fetcher._em_search_url("中央汇金")
        # 关键词在 param JSON 内部被编码，不应裸出现在 query 中
        self.assertNotIn("中央汇金", url)


class TestSplitNewsByDate(unittest.TestCase):
    """新闻按自身发布日期归档（不能一律记到「抓取当天」）。"""

    def test_group_by_publication_date(self):
        news = [
            {"title": "A", "time": "2026-10-30 00:27"},
            {"title": "B", "time": "2026-10-28 17:12"},
            {"title": "C", "time": "2026-10-30 09:15"},
        ]
        g = fetcher.split_news_by_date(news)
        self.assertEqual(sorted(g), ["2026-10-28", "2026-10-30"])
        self.assertEqual(len(g["2026-10-30"]), 2)
        self.assertEqual(len(g["2026-10-28"]), 1)

    def test_unparsable_date_falls_back_to_today(self):
        import datetime
        today = datetime.date.today().isoformat()
        g = fetcher.split_news_by_date([{"title": "X", "time": ""}])
        self.assertEqual(list(g), [today])

    def test_empty_input(self):
        self.assertEqual(fetcher.split_news_by_date([]), {})
        self.assertEqual(fetcher.split_news_by_date(None), {})


class TestFetchNewsResilience(unittest.TestCase):
    """抓取容错：单源故障不影响其它源产出。"""

    def test_one_source_failure_does_not_break_others(self):
        orig_get = fetcher._get
        calls = {"n": 0}

        def fake_get(url, ref=None, timeout=20, max_try=3):
            calls["n"] += 1
            if "eastmoney" in url:            # 主源挂掉
                raise OSError("simulated upstream failure")
            if "10jqka" in url:
                return json.dumps({"data": {"list": [
                    {"title": "国家队今日增持宽基ETF", "url": "https://x/1", "ctime": "1793335013"},
                ]}})
            return json.dumps({"result": {"status": {"code": 0}, "data": []}})

        fetcher._get = fake_get
        try:
            out = fetcher.fetch_news()
        finally:
            fetcher._get = orig_get
        self.assertGreaterEqual(calls["n"], 3, "三个源都应被尝试")
        self.assertTrue(any("国家队" in (o["title"] or "") for o in out))

    def test_all_sources_failure_returns_empty(self):
        orig_get = fetcher._get

        def fake_get(url, ref=None, timeout=20, max_try=3):
            raise OSError("all down")

        fetcher._get = fake_get
        try:
            out = fetcher.fetch_news()
        finally:
            fetcher._get = orig_get
        self.assertEqual(out, [])

    def test_result_shape_has_required_fields(self):
        orig_get = fetcher._get

        def fake_get(url, ref=None, timeout=20, max_try=3):
            if "eastmoney" in url:
                return 'cb({"code":0,"result":{"cmsArticleWebOld":[{"title":"<em>国家队</em>加仓宽基ETF","url":"http://a/1","date":"2026-10-30 10:00:00"}]}})'
            return json.dumps({"result": {"status": {"code": 0}, "data": []}})

        fetcher._get = fake_get
        try:
            out = fetcher.fetch_news()
        finally:
            fetcher._get = orig_get
        self.assertTrue(out)
        for it in out:
            self.assertIn("title", it)
            self.assertIn("url", it)
            self.assertIn("time", it)
            self.assertIn("src", it)
            self.assertNotIn("_score", it, "内部打分字段不应泄漏到入库数据")


if __name__ == "__main__":
    unittest.main()
