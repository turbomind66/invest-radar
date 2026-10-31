"""基准源（跟踪误差/溢价）与东财镜像轮换的回归测试。

覆盖 2026-10-30 的两处改动：
1. `TRACK_BENCHMARK_BY_CATEGORY` 补全备源：日经225 加 100.N225；
   法国CAC40 / 英国富时100 从「无源」改为东财 100.FCHI / 100.FTSE。
2. `common.http.fetch_text(rotate_hosts=True)`：东财 push2 / push2his 被按主机
   限流时，每次重试换一台镜像，而不是死磕同一台。

另含一条**防呆断言**：新浪 sina_us 端点上裸 "DAX"/"CAC" 返回的是同名美国股票
（约 45 美元）而非欧亚指数，绝不能配进 sources（会让跟踪误差算成天文数字）。
"""
import os
import sys
import unittest
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (ROOT, os.path.join(ROOT, "qdii")):
    if p not in sys.path:
        sys.path.insert(0, p)

import constants as C  # noqa: E402
from common import http  # noqa: E402

# 基金池里出现过的全部大类，缺一个都会让该大类基金跟踪误差恒为空
ALL_CATEGORIES = ["纳斯达克100", "标普500", "日经225", "德国DAX", "法国CAC40", "英国富时100"]


class TestBenchSourceConfig(unittest.TestCase):
    def test_all_categories_have_at_least_one_source(self):
        """每个大类至少要有 1 个可用源，否则该类基金跟踪误差恒为空。"""
        for cat in ALL_CATEGORIES:
            self.assertIn(cat, C.TRACK_BENCHMARK_BY_CATEGORY, "缺少大类配置：%s" % cat)
            srcs = C.TRACK_BENCHMARK_BY_CATEGORY[cat]["sources"]
            self.assertTrue(srcs, "大类 %s 没有配置任何基准源，跟踪误差会恒为空" % cat)

    def test_cac40_and_ftse_enabled(self):
        """CAC40 / 富时100 此前误标为「无公开日K」，实测东财有，误判已修。"""
        self.assertEqual(C.TRACK_BENCHMARK_BY_CATEGORY["法国CAC40"]["sources"],
                         [("em_us", "100.FCHI")])
        self.assertEqual(C.TRACK_BENCHMARK_BY_CATEGORY["英国富时100"]["sources"],
                         [("em_us", "100.FTSE")])

    def test_nikkei_has_backup(self):
        """日经225 原来只有 sina_fut NK 单源，现补真实指数 100.N225 作备源。"""
        srcs = C.TRACK_BENCHMARK_BY_CATEGORY["日经225"]["sources"]
        self.assertEqual(len(srcs), 2, "日经225 应有主源 + 备源")
        self.assertEqual(srcs[0], ("sina_fut", "NK"), "主源保持不变，避免改动既有数值")
        self.assertEqual(srcs[1], ("em_us", "100.N225"))

    def test_us_index_keeps_dual_source(self):
        for cat, sina, em in [("纳斯达克100", ".NDX", "100.NDX100"),
                              ("标普500", ".INX", "100.SPX")]:
            self.assertEqual(C.TRACK_BENCHMARK_BY_CATEGORY[cat]["sources"],
                             [("sina_us", sina), ("em_us", em)])

    def test_no_bare_dax_or_cac_in_sina_us(self):
        """防呆：sina_us 端点上裸 DAX/CAC 是同名美股（约45美元），不是欧亚指数。

        这条断言保护的是 2026-10-30 实测踩到的坑：不带点的 DAX/CAC 在该端点
        **确实返回数据**，很容易被误当成指数配进 sources。
        """
        for cat, cfg in C.TRACK_BENCHMARK_BY_CATEGORY.items():
            for btype, bcode in cfg["sources"]:
                if btype == "sina_us":
                    self.assertTrue(bcode.startswith("."),
                                    "%s 的 sina_us 符号 %r 未以点开头，"
                                    "新浪该端点上不带点的是同名美股而非指数" % (cat, bcode))


class TestMirrorPool(unittest.TestCase):
    def test_push2_hosts(self):
        self.assertEqual(http._mirror_pool("push2.eastmoney.com"), http.PUSH2_HOSTS)

    def test_push2his_hosts(self):
        self.assertEqual(http._mirror_pool("push2his.eastmoney.com"), http.PUSH2HIS_HOSTS)
        # 已轮换到镜像后也要能继续识别，否则第二次重试会退化成死磕同一台
        self.assertEqual(http._mirror_pool("82.push2his.eastmoney.com"), http.PUSH2HIS_HOSTS)

    def test_non_em_host_not_rotated(self):
        self.assertIsNone(http._mirror_pool("finance.sina.com.cn"))
        self.assertIsNone(http._mirror_pool("quote.eastmoney.com"))

    def test_pool_has_no_duplicates_and_no_dead_host(self):
        for name, pool in (("PUSH2", http.PUSH2_HOSTS), ("PUSH2HIS", http.PUSH2HIS_HOSTS)):
            self.assertEqual(len(pool), len(set(pool)), "%s 镜像池有重复项" % name)
            self.assertNotIn("push2hisdelay.eastmoney.com", pool,
                             "push2hisdelay 不存在（实测解析失败），勿加进池子")

    def test_fetch_text_rotates_host_on_retry(self):
        """每次重试都要换一台镜像主机，而不是重复打同一台。"""
        attempted = []

        class _FakeResp(object):
            def read(self):
                return b"ok"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def _boom(*_a, **_kw):
            raise OSError("simulated RemoteDisconnected")

        orig = urllib.request.build_opener
        try:
            def _spy(*handlers, **kw):
                opener = orig(*handlers, **kw)
                orig_open = opener.open

                def _open(req, *a, **k):
                    attempted.append(
                        req.full_url.split("/")[2] if hasattr(req, "full_url") else str(req))
                    raise OSError("simulated RemoteDisconnected")

                opener.open = _open
                return opener

            urllib.request.build_opener = _spy
            with self.assertRaises(Exception):
                http.fetch_text("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=100.GDAXI",
                                retries=3, rotate_hosts=True)
        finally:
            urllib.request.build_opener = orig

        self.assertEqual(len(attempted), 4, "retries=3 应共发起 4 次请求")
        self.assertEqual(len(set(attempted)), 4,
                         "每次重试都必须换一台镜像，实际打到：%s" % attempted)

    def test_fetch_text_default_does_not_rotate(self):
        """默认保持既有行为：不轮换，避免影响其它调用方。"""
        attempted = []
        orig = urllib.request.build_opener
        try:
            def _spy(*handlers, **kw):
                opener = orig(*handlers, **kw)

                def _open(req, *a, **k):
                    attempted.append(
                        req.full_url.split("/")[2] if hasattr(req, "full_url") else str(req))
                    raise OSError("simulated RemoteDisconnected")

                opener.open = _open
                return opener

            urllib.request.build_opener = _spy
            with self.assertRaises(Exception):
                http.fetch_text("https://push2his.eastmoney.com/api/qt/x",
                                retries=2)
        finally:
            urllib.request.build_opener = orig

        self.assertEqual(set(attempted), {"push2his.eastmoney.com"},
                         "未开启 rotate_hosts 时不应换主机")

    def test_rotation_starts_from_caller_host(self):
        """调用方显式指定某台镜像时，第 0 次尝试必须仍打这台，不得被改回主域名。

        主域名（pool[0]）是最常被限流的一台；若首次就把它换回去，等于先白失败一次。
        """
        attempted = []
        orig = urllib.request.build_opener
        try:
            def _spy(*handlers, **kw):
                opener = orig(*handlers, **kw)

                def _open(req, *a, **k):
                    attempted.append(req.full_url.split("/")[2])
                    raise OSError("simulated RemoteDisconnected")

                opener.open = _open
                return opener

            urllib.request.build_opener = _spy
            with self.assertRaises(Exception):
                http.fetch_text("https://23.push2his.eastmoney.com/api/qt/x",
                                retries=2, rotate_hosts=True)
        finally:
            urllib.request.build_opener = orig

        self.assertEqual(attempted[0], "23.push2his.eastmoney.com",
                         "首次应保留调用方指定的主机，实际：%s" % attempted)
        self.assertEqual(len(set(attempted)), 3, "后续仍须逐次换机：%s" % attempted)


if __name__ == "__main__":
    unittest.main()
