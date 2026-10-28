"""静态站导出测试（完全离线，不访问网络）。

验证 GitHub Pages 用的 docs/data.json 结构与模板注入正确。
运行：
    python -m unittest discover -s tests -p "test_*.py" -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db          # noqa: E402
import staticsite  # noqa: E402

D1 = "2026-10-26"
D2 = "2026-10-27"
D3 = "2026-10-28"   # 盘前当日：价格有、主力净流入缺失

ROWS = [
    {"code": "510300", "name": "华泰柏瑞沪深300ETF", "cat": "沪深300",
     "price": 4.10, "chg": -1.34, "amount": 5.8e9, "main_net": -8.61e8},
    {"code": "510310", "name": "易方达沪深300ETF", "cat": "沪深300",
     "price": 1.90, "chg": -1.20, "amount": 1.2e9, "main_net": -1.10e8},
    {"code": "510050", "name": "华夏上证50ETF", "cat": "上证50",
     "price": 3.10, "chg": 0.40, "amount": 2.1e9, "main_net": 1.66e8},
]
ROWS_NO_MAIN = [dict(r, main_net=None) for r in ROWS]

# 一级市场申赎（ETF 份额口径）样例
SHARES_D1 = [
    {"code": "510300", "name": "华泰柏瑞沪深300ETF", "cat": "沪深300",
     "shares": 354.469877, "nav": 4.1797, "chg_shares": 2.043, "net_sub": 8.54},
    {"code": "510310", "name": "易方达沪深300ETF", "cat": "沪深300",
     "shares": 216.0154, "nav": 4.0618, "chg_shares": 32.418, "net_sub": 131.68},
    {"code": "510050", "name": "华夏上证50ETF", "cat": "上证50",
     "shares": 126.5737, "nav": 2.8878, "chg_shares": -3.357, "net_sub": -9.69},
]


class TestStaticExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ntt_static_")
        self.db_path = os.path.join(self.tmp, "test.db")
        self.out = os.path.join(self.tmp, "docs")
        db.init_db(self.db_path)
        db.save_etf_daily(D1, ROWS, path=self.db_path)
        db.save_etf_daily(D2, ROWS, path=self.db_path)
        db.save_etf_daily(D3, ROWS_NO_MAIN, path=self.db_path)
        db.save_share_rows(D1, SHARES_D1, path=self.db_path)
        db.save_news(D2, [{"time": "09:30", "title": "中央汇金增持宽基ETF",
                           "url": "https://example.com/a", "src": "示例"}],
                     path=self.db_path)
        db.set_meta("last_fetch", "2026-10-27 16:20:00", path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_payload_shape(self):
        p = staticsite.build_static_payload(path=self.db_path)
        self.assertEqual(p["dates"], [D1, D2, D3])
        self.assertEqual(p["latest_date"], D3)
        self.assertEqual(sorted(p["days"]), [D1, D2, D3])
        for d in (D1, D2, D3):
            self.assertEqual(len(p["days"][d]), 3)
        self.assertEqual(p["last_fetch"], "2026-10-27 16:20:00")
        # 类别与基金清单
        cats = {c["cat"]: c["count"] for c in p["cats"]}
        self.assertEqual(cats, {"沪深300": 2, "上证50": 1})
        self.assertEqual(len(p["funds"]), 3)
        self.assertIn("actions_url", p)
        # 一级市场申赎（份额）必须一并打包，供前端浏览器端合并
        self.assertIn("shares", p)
        self.assertEqual(len(p["shares"][D1]), 3)
        self.assertAlmostEqual(p["shares"][D1][0]["net_sub"], -9.69)   # 按 code 升序，首行 510050
        self.assertNotIn(D2, p["shares"])

    def test_payload_json_roundtrip(self):
        p = staticsite.build_static_payload(path=self.db_path)
        again = json.loads(json.dumps(p, ensure_ascii=False))
        self.assertEqual(again["days"][D1][0]["code"], p["days"][D1][0]["code"])
        # 主力缺失日必须保持 null（不能变成 0，否则前端会误判为"中性"）
        self.assertIsNone(again["days"][D3][0]["main_net"])

    def test_import_from_days_file(self):
        """Actions 每次干净环境：应从上一版 data.json 恢复历史。"""
        os.makedirs(self.out, exist_ok=True)
        payload = staticsite.build_static_payload(path=self.db_path)
        days_file = os.path.join(self.out, "data.json")
        with open(days_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

        fresh = os.path.join(self.tmp, "fresh.db")
        db.init_db(fresh)
        self.assertEqual(db.get_dates(fresh), [])
        n = staticsite.import_from_days_file(days_file=days_file, path=fresh)
        self.assertEqual(n, 3)
        self.assertEqual(db.get_dates(fresh), [D1, D2, D3])
        self.assertEqual(db.get_meta("last_fetch", "", fresh), "2026-10-27 16:20:00")
        # 份额历史也要跟着恢复（否则 Actions 干净环境会丢一级申赎口径）
        self.assertEqual(len(db.get_shares_by_date(D1, path=fresh)), 3)
        # 二次调用不应重复导入（已有数据直接返回 0）
        self.assertEqual(staticsite.import_from_days_file(days_file=days_file, path=fresh), 0)

    def test_render_index_injects_static_flag(self):
        payload = staticsite.build_static_payload(path=self.db_path)
        html = staticsite.render_index(payload)
        self.assertIn("window.__STATIC__=", html)
        self.assertIn("actions_url", html)
        # 注入后仍只有一个 </head>，且标记在 head 内
        self.assertEqual(html.count("</head>"), 1)
        self.assertLess(html.index("window.__STATIC__"), html.index("</head>"))
        # 模板自身的关键函数必须保留
        for fn in ("computeStaticPayload", "staticPayload", "signalLabel", "render("):
            self.assertIn(fn, html)

    def test_export_writes_files(self):
        res = staticsite.export(out_dir=self.out, path=self.db_path)
        self.assertEqual(res["days"], 3)
        self.assertEqual(res["latest"], D3)
        for name in ("data.json", "index.html", ".nojekyll"):
            self.assertTrue(os.path.exists(os.path.join(self.out, name)), name)
        with open(os.path.join(self.out, "data.json"), encoding="utf-8") as f:
            p = json.load(f)
        self.assertEqual(p["dates"], [D1, D2, D3])
        # 体积应保持很小（Pages 直接加载，不能随天数膨胀失控）
        size = os.path.getsize(os.path.join(self.out, "data.json"))
        self.assertLess(size, 200 * 1024)

    def test_target_date_rules(self):
        """收盘后(>=15:30)算当天；盘前/盘中/周末算上一交易日。"""
        import datetime
        tz = staticsite.TZ_BEIJING
        wed_after = datetime.datetime(2026, 10, 28, 16, 20, tzinfo=tz)   # 周三收盘后
        wed_before = datetime.datetime(2026, 10, 28, 10, 0, tzinfo=tz)   # 周三盘前
        sat = datetime.datetime(2026, 10, 31, 16, 20, tzinfo=tz)         # 周六
        self.assertEqual(staticsite.target_date(wed_after).isoformat(), "2026-10-28")
        self.assertEqual(staticsite.target_date(wed_before).isoformat(), "2026-10-27")
        self.assertEqual(staticsite.target_date(sat).isoformat(), "2026-10-30")


if __name__ == "__main__":
    unittest.main(verbosity=2)
