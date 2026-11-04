"""观察池「代码 ↔ 简称 ↔ 类别」校准守卫（2026-11-04）。

背景：563300 实为华泰柏瑞中证2000ETF，此前被误标成「中证A500ETF / 中证A500」，
导致全池没有任何一只真正的中证A500 ETF，且中证2000 的数据被算进 A500 类别；
560010（实为广发）与 588800（实为华夏）的公司名也标错。

本测试把校准结果钉死，并校验线上快照 docs/data.json 的归档标签已同步纠正
（只改标签、数值一字未动），防止后续改动把错配带回来。
"""
import json
import os
import re
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "national_team"))

import fetcher  # noqa: E402


class TestWatchlist(unittest.TestCase):
    def setUp(self):
        self.w = {c: (n, cat) for c, n, cat in fetcher.WATCHLIST}
        with open(os.path.join(ROOT, "docs", "data.json"), encoding="utf-8") as f:
            self.raw_json = f.read()
        self.snapshot = json.loads(self.raw_json)

    def test_codes_unique_and_well_formed(self):
        codes = [c for c, _, _ in fetcher.WATCHLIST]
        self.assertEqual(len(codes), len(set(codes)), "观察池出现重复代码")
        self.assertTrue(all(re.fullmatch(r"\d{6}", c) for c in codes),
                        "观察池代码必须是 6 位数字")

    def test_pool_size_and_categories(self):
        self.assertEqual(len(fetcher.WATCHLIST), 17, "校准后观察池应为 17 只")
        cats = {cat for _, _, cat in fetcher.WATCHLIST}
        self.assertIn("中证A500", cats)
        self.assertIn("中证2000", cats)

    def test_corrected_labels(self):
        self.assertEqual(self.w["560010"], ("广发中证1000ETF", "中证1000"))
        self.assertEqual(self.w["588800"], ("华夏科创100ETF", "科创100"))
        self.assertEqual(self.w["563300"], ("华泰柏瑞中证2000ETF", "中证2000"))

    def test_real_a500_and_added_holdings(self):
        # 中证A500 类别必须由真正的 A500 ETF 承担，不能再是中证2000ETF
        a500 = [c for c, (_n, cat) in self.w.items() if cat == "中证A500"]
        self.assertEqual(a500, ["563360"])
        # 汇金 2025 中报增持名单 / 2026-10 新增常规买入品种
        for code in ("588080", "159629", "563360"):
            self.assertIn(code, self.w, "缺少 %s" % code)
        self.assertEqual(self.w["588080"], ("易方达科创50ETF", "科创50"))
        self.assertEqual(self.w["159629"], ("富国中证1000ETF", "中证1000"))

    def test_snapshot_has_no_stale_labels(self):
        for stale in ('"name":"中证A500ETF"', '"cat":"中证A500"',
                      '"name":"易方达中证1000ETF"', '"name":"博时科创100ETF"'):
            self.assertNotIn(stale, self.raw_json, "线上快照仍有旧标签 %s" % stale)

    def test_snapshot_funds_match_watchlist(self):
        for f in self.snapshot["funds"]:
            if f["code"] in self.w:
                self.assertEqual((f["name"], f["cat"]), self.w[f["code"]], f["code"])
        pool_cats = {cat for _, _, cat in fetcher.WATCHLIST}
        self.assertTrue({c["cat"] for c in self.snapshot["cats"]} <= pool_cats,
                        "快照类别清单含有观察池之外的类别")

    def test_snapshot_rows_corrected_but_numeric_intact(self):
        # 归档行标签已纠正，但数值/条目数不得因标签纠正而丢失
        d = self.snapshot
        # 不绑定具体天数：主仓与部署副本的快照窗口长度不同（366 / 90 天）
        self.assertGreater(len(d["dates"]), 0)
        self.assertGreater(len(d["history_full"]), 0)
        seen = 0
        for _day, rows in (d.get("days") or {}).items():
            for r in rows:
                if r.get("code") == "563300":
                    self.assertEqual((r["name"], r["cat"]),
                                     ("华泰柏瑞中证2000ETF", "中证2000"))
                    self.assertIn("main_net", r)
                    seen += 1
        self.assertGreater(seen, 0, "快照里应仍保留 563300 的历史行情行")
        shares = 0
        for _day, rows in (d.get("shares") or {}).items():
            for r in rows:
                if r.get("code") == "563300":
                    self.assertEqual((r["name"], r["cat"]),
                                     ("华泰柏瑞中证2000ETF", "中证2000"))
                    self.assertIn("net_sub", r)
                    shares += 1
        self.assertGreater(shares, 0, "快照里应仍保留 563300 的一级份额行")

    def test_static_page_scope_text_updated(self):
        with open(os.path.join(ROOT, "national_team", "templates", "index.html"),
                  encoding="utf-8") as f:
            tpl = f.read()
        self.assertIn("中证A500/中证2000", tpl, "口径说明需列出新增的中证2000")
        with open(os.path.join(ROOT, "docs", "index.html"), encoding="utf-8") as f:
            page = f.read()
        self.assertIn("中证A500/中证2000", page, "静态页需与模板同步")


if __name__ == "__main__":
    unittest.main()
