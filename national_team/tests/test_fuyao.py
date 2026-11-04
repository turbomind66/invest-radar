"""fuyao 可选增强源回归测试（2026-11-04，完全离线、零网络）。

覆盖三条铁律：
  1) 无 key ⇒ 增强完全关闭、零额外请求、原抓取链行为不变；
  2) 有 key ⇒ 只在「原源缺数据」的缝隙补，口径与原源一致；
  3) 任何异常（2003/2004/429/网络）静默降级，绝不阻断主流程。

另锁定两个实测踩过的坑：
  - 环境变量名 `invest-radar-fuyaokey` 含连字符，Python os.environ 可读；
  - fuyao 新闻时间是 13 位毫秒，必须先格式化再交给 _norm_news_time。
"""
import json
import os
import sys
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
NT = os.path.dirname(HERE)                 # national_team/
ROOT = os.path.dirname(NT)                 # 仓库根（common/ 所在）
for _p in (ROOT, NT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from common import fuyao  # noqa: E402
import fetcher  # noqa: E402


_ENV_KEYS = ("invest-radar-fuyaokey", "FUYAO_API_KEY",
             "NT_FUYAO_ENV_NAME", "FUYAO_BASE_URL", "NT_FUYAO_NEWS_STOCK",
             "NT_FUYAO_ALLOW_IN_TESTS")


class _CleanEnv(unittest.TestCase):
    """每个用例前后清干净 fuyao 相关环境变量与 thread-local，避免相互污染。"""

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _ENV_KEYS}
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        # 本文件要验证增强逻辑本身，显式允许；所有网络访问都用 mock 隔离。
        os.environ["NT_FUYAO_ALLOW_IN_TESTS"] = "1"
        fuyao.clear_request_override()
        fuyao.reset_counters()

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        fuyao.clear_request_override()


class TestResolveKey(_CleanEnv):
    def test_no_key_disabled(self):
        self.assertIsNone(fuyao.resolve_key())
        self.assertFalse(fuyao.enabled())
        self.assertEqual(fuyao.status()["source"], "off")

    def test_hyphenated_env_name_readable_in_python(self):
        # bash 的 ${invest-radar-fuyaokey} 会读错，Python os.environ 完全正常
        os.environ["invest-radar-fuyaokey"] = "sk-test-hyphen"
        self.assertEqual(fuyao.resolve_key(), "sk-test-hyphen")
        self.assertTrue(fuyao.enabled())
        self.assertEqual(fuyao.status()["source"], "env")

    def test_alias_and_priority(self):
        os.environ["invest-radar-fuyaokey"] = "sk-default"
        os.environ["FUYAO_API_KEY"] = "sk-alias"
        self.assertEqual(fuyao.resolve_key(), "sk-default")
        os.environ.pop("invest-radar-fuyaokey")
        self.assertEqual(fuyao.resolve_key(), "sk-alias")

    def test_page_key_overrides_env(self):
        os.environ["invest-radar-fuyaokey"] = "sk-env"
        fuyao.set_request_override(key="sk-page")
        self.assertEqual(fuyao.resolve_key(), "sk-page")
        self.assertEqual(fuyao.status()["source"], "page")
        fuyao.clear_request_override()
        self.assertEqual(fuyao.resolve_key(), "sk-env")

    def test_page_env_name(self):
        os.environ["MY_CUSTOM_KEY"] = "sk-custom"
        fuyao.set_request_override(env_name="MY_CUSTOM_KEY")
        self.assertEqual(fuyao.resolve_key(), "sk-custom")

    def test_nt_fuyao_env_name_indirection(self):
        os.environ["MY_CUSTOM_KEY"] = "sk-indirect"
        os.environ["NT_FUYAO_ENV_NAME"] = "MY_CUSTOM_KEY"
        self.assertEqual(fuyao.resolve_key(), "sk-indirect")

    def test_blank_values_ignored(self):
        os.environ["invest-radar-fuyaokey"] = "   "
        os.environ["FUYAO_API_KEY"] = "sk-real"
        self.assertEqual(fuyao.resolve_key(), "sk-real")

    def test_status_never_leaks_key(self):
        os.environ["invest-radar-fuyaokey"] = "sk-super-secret"
        blob = json.dumps(fuyao.status(), ensure_ascii=False)
        self.assertNotIn("sk-super-secret", blob)
        self.assertTrue(fuyao.status()["enabled"])


class TestClientDegradation(_CleanEnv):
    def test_get_without_key_never_hits_network(self):
        with patch.object(fuyao.urllib.request, "urlopen") as m:
            with self.assertRaises(fuyao.FuyaoUnavailable):
                fuyao.get("/api/fund/market/snapshot", {"thscode": "510300.SH"})
            m.assert_not_called()

    def test_success(self):
        os.environ["FUYAO_API_KEY"] = "sk-ok"
        body = json.dumps({"code": 0, "data": {"item": [{"last_price": 4.065}]}}).encode()

        class _R:
            def read(self):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with patch.object(fuyao.urllib.request, "urlopen", return_value=_R()):
            self.assertEqual(fuyao.fund_snapshot("510300.SH")["last_price"], 4.065)

    def test_code_2004_ai_client_only_is_silent(self):
        """主力资金/高频被锁 AI 客户端：静默降级，不重试、不抛到主流程之外。"""
        os.environ["FUYAO_API_KEY"] = "sk-ok"
        body = json.dumps({"code": 2004, "message": "AI客户端专用", "data": None}).encode()

        class _R:
            def read(self):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with patch.object(fuyao.urllib.request, "urlopen", return_value=_R()) as m:
            with self.assertRaises(fuyao.FuyaoUnavailable):
                fuyao.get("/api/a-share/capital-flow/snapshot", {})
            self.assertEqual(m.call_count, 1, "2004 应立即放弃，不做无意义重试")
        self.assertEqual(fuyao.STATE["last_error"], "code=2004")

    def test_network_error_is_silent(self):
        os.environ["FUYAO_API_KEY"] = "sk-ok"
        with patch.object(fuyao.urllib.request, "urlopen",
                          side_effect=OSError("boom")):
            with self.assertRaises(fuyao.FuyaoUnavailable):
                fuyao.get("/api/news/flash/list", {"tag_id": 21103, "seq": "0"})


class TestFieldMapping(_CleanEnv):
    def test_thscode_suffix(self):
        self.assertEqual(fuyao.thscode_of("510300"), "510300.SH")
        self.assertEqual(fuyao.thscode_of("563300"), "563300.SH")
        self.assertEqual(fuyao.thscode_of("159919"), "159919.SZ")
        self.assertEqual(fuyao.thscode_of("588000"), "588000.SH")

    def test_ms_time_conversion(self):
        # 13 位毫秒必须转成 'YYYY-MM-DD HH:MM'，直接喂 _norm_news_time 会返回空串
        s = fuyao._ms_to_hm(1793773102000)
        self.assertRegex(s, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertEqual(s, fetcher._norm_news_time(s))
        self.assertEqual(fuyao._ms_to_hm(0), "")
        self.assertEqual(fuyao._ms_to_hm("bad"), "")

    def test_news_items_shape_and_time(self):
        with patch.object(fuyao, "flash_list", return_value=[
                {"title": "中央汇金增持ETF", "encoded_url": "https://x/1",
                 "create_time_ms": 1793773102000},
                {"title": "", "encoded_url": "", "create_time_ms": 0},
        ]), patch.object(fuyao, "stock_timeline", return_value=[]):
            out = fuyao.news_items()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "中央汇金增持ETF")
        self.assertEqual(out[0]["src"], "同花顺API")
        self.assertRegex(out[0]["time"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_stock_source_failure_does_not_drop_flash_news(self):
        """实测坑：个股分时新闻只认个股，传 ETF 代码返回 1002。

        若两源共用一个 try，个股源一挂会把已到手的快讯全丢掉。
        """
        os.environ["NT_FUYAO_NEWS_STOCK"] = "601398.SH"
        flash = [{"title": "中央汇金增持宽基ETF", "encoded_url": "https://x/1",
                  "create_time_ms": 1793773102000}]
        with patch.object(fuyao, "flash_list", return_value=flash), \
             patch.object(fuyao, "stock_timeline",
                          side_effect=fuyao.FuyaoUnavailable("code=1002")):
            out = fuyao.news_items()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "中央汇金增持宽基ETF")

    def test_flash_failure_does_not_raise(self):
        with patch.object(fuyao, "flash_list",
                          side_effect=fuyao.FuyaoUnavailable("code=429")):
            self.assertEqual(fuyao.news_items(), [])

    def test_etf_quotes_units_and_rounding(self):
        with patch.object(fuyao, "fund_snapshot", return_value={
                "last_price": 4.065, "price_change_ratio_pct": -0.659824,
                "turnover": 5075684400}):
            out = fuyao.etf_quotes(["510300"])
        self.assertAlmostEqual(out["510300"]["price"], 4.065)
        self.assertEqual(out["510300"]["chg"], -0.66)
        # turnover 已是「元」，与新浪 parts[9] 同单位，不能再 /1e8
        self.assertEqual(out["510300"]["amount"], 5075684400)

    def test_etf_quotes_stops_on_first_failure(self):
        """链路不通时立刻放弃，别把 14 次请求都耗完。"""
        with patch.object(fuyao, "fund_snapshot",
                          side_effect=fuyao.FuyaoUnavailable("429")) as m:
            out = fuyao.etf_quotes(["510300", "510500", "159919"])
        self.assertEqual(out, {})
        self.assertEqual(m.call_count, 1)

    def test_etf_quotes_skips_zero_price(self):
        with patch.object(fuyao, "fund_snapshot", return_value={"last_price": 0}):
            self.assertEqual(fuyao.etf_quotes(["510300"]), {})


class TestFetcherIntegration(_CleanEnv):
    """无 key 时原抓取链零额外请求；有 key 时仅补缺。"""

    def test_no_key_news_has_no_extra_source(self):
        with patch.object(fetcher, "_get", return_value="{}"), \
             patch.object(fetcher, "_fetch_remote_news", return_value=[]):
            fetcher.fetch_news()
        report = fetcher.fetch_news.last_report
        self.assertFalse(any(r[0] == "同花顺API" for r in report))

    def test_with_key_news_joins_same_scoring(self):
        os.environ["FUYAO_API_KEY"] = "sk-ok"
        items = [
            {"title": "中央汇金增持宽基ETF", "url": "https://x/1",
             "time": "2026-11-04 10:00", "src": "同花顺API"},
            # 无关条目必须被 _news_score 挡掉，不能因新源放宽入榜标准
            {"title": "比特币跌破76000美元", "url": "https://x/2",
             "time": "2026-11-04 10:01", "src": "同花顺API"},
        ]
        with patch.object(fetcher, "_get", return_value="{}"), \
             patch.object(fetcher, "_fetch_remote_news", return_value=[]), \
             patch.object(fuyao, "news_items", return_value=items):
            out = fetcher.fetch_news()
        titles = [x["title"] for x in out]
        self.assertIn("中央汇金增持宽基ETF", titles)
        self.assertNotIn("比特币跌破76000美元", titles)
        self.assertIn("同花顺API", [r[0] for r in fetcher.fetch_news.last_report])

    def test_broken_fuyao_news_does_not_break_fetch_news(self):
        os.environ["FUYAO_API_KEY"] = "sk-ok"
        with patch.object(fetcher, "_get", return_value="{}"), \
             patch.object(fetcher, "_fetch_remote_news", return_value=[]), \
             patch.object(fuyao, "news_items", side_effect=RuntimeError("boom")):
            out = fetcher.fetch_news()
        self.assertIsInstance(out, list)

    def test_sina_all_green_no_fuyao_calls(self):
        os.environ["FUYAO_API_KEY"] = "sk-ok"
        # 造一份 14 只全绿的新浪响应
        lines = []
        for c, _n, _t in fetcher.WATCHLIST:
            pre = "sh" if c[0] in "56" else "sz"
            lines.append('var hq_str_%s%s="ETF,3.0,3.0,3.1,3.2,2.9,3.1,3.1,1000,5000000,'
                         '1,3.1,1,3.0,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,2026-11-04,15:00:00,00";'
                         % (pre, c))
        with patch.object(fetcher, "_get_sina", return_value="\n".join(lines)), \
             patch.object(fuyao, "etf_quotes") as m:
            out = fetcher.fetch_sina_etfs()
        self.assertEqual(len(out), len(fetcher.WATCHLIST))
        m.assert_not_called()

    def test_sina_partial_fuyao_fills_only_missing(self):
        os.environ["FUYAO_API_KEY"] = "sk-ok"
        with patch.object(fetcher, "_get_sina", return_value=""), \
             patch.object(fuyao, "etf_quotes",
                          return_value={"510300": {"price": 4.065, "chg": -0.66,
                                                   "amount": 5075684400}}) as m:
            out = fetcher.fetch_sina_etfs()
        self.assertEqual(out["510300"]["price"], 4.065)
        self.assertEqual(len(m.call_args[0][0]), len(fetcher.WATCHLIST))
        self.assertEqual(fuyao.STATE["enhanced_quotes"], 1)

    def test_sina_down_with_fuyao_still_returns_data(self):
        """新浪整体不可达时，增强源能顶上（这正是接入的价值）。"""
        os.environ["FUYAO_API_KEY"] = "sk-ok"
        with patch.object(fetcher, "_get_sina", side_effect=OSError("blocked")), \
             patch.object(fuyao, "etf_quotes",
                          return_value={"510300": {"price": 4.065, "chg": 0.0,
                                                   "amount": 1}}):
            out = fetcher.fetch_sina_etfs()
        self.assertEqual(out["510300"]["price"], 4.065)

    def test_no_key_sina_down_returns_empty_as_before(self):
        with patch.object(fetcher, "_get_sina", side_effect=OSError("blocked")), \
             patch.object(fuyao, "etf_quotes") as m:
            out = fetcher.fetch_sina_etfs()
        self.assertEqual(out, {})
        m.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
