"""数据层单元测试（完全离线，不访问任何网络接口）。

运行：
    python -m unittest discover -s tests -p "test_*.py" -v
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402

D1 = "2026-10-26"
D2 = "2026-10-27"
D3 = "2026-10-28"   # 模拟“盘前”当日：价格有、主力净流入缺失

ROWS_D1 = [
    {"code": "510300", "name": "华泰柏瑞沪深300ETF", "cat": "沪深300",
     "price": 4.10, "chg": -1.34, "amount": 5.8e9, "main_net": -8.61e8,
     "huge_net": -12.50e8, "big_net": 3.89e8, "mid_net": 2.10e8, "small_net": 1.20e8},
    {"code": "510310", "name": "易方达沪深300ETF", "cat": "沪深300",
     "price": 1.90, "chg": -1.20, "amount": 1.2e9, "main_net": -1.10e8,
     "huge_net": -2.20e8, "big_net": 1.10e8, "mid_net": 0.60e8, "small_net": 0.50e8},
    {"code": "510050", "name": "华夏上证50ETF", "cat": "上证50",
     "price": 3.10, "chg": 0.40, "amount": 2.1e9, "main_net": 1.66e8,
     "huge_net": 2.20e8, "big_net": -0.54e8, "mid_net": -0.80e8, "small_net": -0.40e8},
    {"code": "588000", "name": "华夏科创50ETF", "cat": "科创50",
     "price": 1.10, "chg": -3.10, "amount": 3.0e9, "main_net": -14.73e8,
     "huge_net": -23.06e8, "big_net": 8.33e8, "mid_net": 11.57e8, "small_net": 2.08e8},
]
def _no_tier(row):
    """模拟升级前的旧行：无四档字段（资金流缺失日四档同样缺失）。"""
    return {k: v for k, v in row.items() if k not in db.TIER_FIELDS}


ROWS_D2 = [_no_tier(dict(r, main_net=None)) for r in ROWS_D1]   # 主力数据缺失日（旧数据无四档）
ROWS_D3 = [_no_tier(dict(r, main_net=None)) for r in ROWS_D1]   # 盘前当日：价格有、资金流未出

# 一级市场申赎（ETF 份额口径）样例
SHARES_D1 = [
    {"code": "510300", "name": "华泰柏瑞沪深300ETF", "cat": "沪深300",
     "shares": 354.469877, "nav": 4.1797, "chg_shares": 2.043, "net_sub": 8.54},
    {"code": "510310", "name": "易方达沪深300ETF", "cat": "沪深300",
     "shares": 216.0154, "nav": 4.0618, "chg_shares": 32.418, "net_sub": 131.68},
    {"code": "510050", "name": "华夏上证50ETF", "cat": "上证50",
     "shares": 126.5737, "nav": 2.8878, "chg_shares": -3.357, "net_sub": -9.69},
]


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ntt_test_")
        self.path = os.path.join(self.tmp, "test.db")
        db.init_db(self.path)
        db.save_etf_daily(D1, ROWS_D1, path=self.path)
        db.save_etf_daily(D2, ROWS_D2, path=self.path)
        db.save_etf_daily(D3, ROWS_D3, path=self.path)
        db.save_share_rows(D1, SHARES_D1, path=self.path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------- 基础读写 ----------
    def test_save_and_get_by_date(self):
        rows = db.get_etf_by_date(D1, path=self.path)
        self.assertEqual(len(rows), 4)
        codes = {r["code"] for r in rows}
        self.assertEqual(codes, {"510300", "510310", "510050", "588000"})

    def test_dates_ascending(self):
        self.assertEqual(db.get_dates(path=self.path), [D1, D2, D3])

    def test_save_is_idempotent(self):
        """同一天重复写入应覆盖而非追加。"""
        db.save_etf_daily(D1, ROWS_D1, path=self.path)
        self.assertEqual(len(db.get_etf_by_date(D1, path=self.path)), 4)

    def test_news_roundtrip(self):
        db.save_news(D1, [{"time": "09:30", "title": "汇金增持宽基ETF",
                           "url": "https://example.com/a", "src": "测试源"}], path=self.path)
        news = db.get_news_by_date(D1, path=self.path)
        self.assertEqual(len(news), 1)
        self.assertEqual(news[0]["title"], "汇金增持宽基ETF")
        self.assertEqual(news[0]["src"], "测试源")

    def test_meta_roundtrip(self):
        db.set_meta("last_fetch", "2026-10-28 16:05:00", path=self.path)
        self.assertEqual(db.get_meta("last_fetch", path=self.path), "2026-10-28 16:05:00")
        self.assertEqual(db.get_meta("not_exist", "fallback", path=self.path), "fallback")

    # ---------- 筛选 ----------
    def test_filter_by_category(self):
        rows = db.get_etf_by_date(D1, cats=["沪深300"], path=self.path)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["cat"] == "沪深300" for r in rows))

    def test_filter_by_multiple_categories(self):
        rows = db.get_etf_by_date(D1, cats=["沪深300", "上证50"], path=self.path)
        self.assertEqual(len(rows), 3)

    def test_filter_by_codes(self):
        rows = db.get_etf_by_date(D1, codes=["510300", "588000"], path=self.path)
        self.assertEqual({r["code"] for r in rows}, {"510300", "588000"})

    def test_filter_category_and_code_intersection(self):
        """类别与基金叠加取交集。"""
        rows = db.get_etf_by_date(D1, cats=["沪深300"], codes=["510300"], path=self.path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "510300")

    def test_filter_empty_intersection(self):
        """无交集时返回空列表，不报错。"""
        rows = db.get_etf_by_date(D1, cats=["上证50"], codes=["510300"], path=self.path)
        self.assertEqual(rows, [])

    def test_filter_unknown_category(self):
        rows = db.get_etf_by_date(D1, cats=["不存在的类别"], path=self.path)
        self.assertEqual(rows, [])

    def test_rows_with_null_main_net_sorted_last(self):
        """主力净流入为空的行应排在末尾，避免抢占首位。"""
        rows = db.get_etf_by_date(D2, path=self.path)
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(r["main_net"] is None for r in rows))

    # ---------- 四档资金流 ----------
    def test_tier_fields_roundtrip(self):
        """超大/大/中/小单四档净额读写不丢（分时大额买入的可见口径）。"""
        rows = {r["code"]: r for r in db.get_etf_by_date(D1, path=self.path)}
        r = rows["588000"]
        self.assertAlmostEqual(r["huge_net"], -23.06e8)
        self.assertAlmostEqual(r["big_net"], 8.33e8)
        self.assertAlmostEqual(r["mid_net"], 11.57e8)
        self.assertAlmostEqual(r["small_net"], 2.08e8)

    def test_tier_identity_main_is_big_plus_huge(self):
        """东财口径勾稽：主力净 = 大单净 + 超大单净。"""
        for r in db.get_etf_by_date(D1, path=self.path):
            self.assertAlmostEqual(r["main_net"], r["big_net"] + r["huge_net"],
                                   msg=r["code"])

    def test_tier_nulls_when_absent(self):
        """旧数据无四档字段时返回 None（前端显示 —，不得变成 0 误导）。"""
        rows = db.get_etf_by_date(D2, path=self.path)
        self.assertTrue(all(r["huge_net"] is None and r["small_net"] is None for r in rows))

    def test_legacy_db_migrates_tier_columns(self):
        """旧库（无四档列）初始化后自动补列并可读写。"""
        tmp = tempfile.mkdtemp(prefix="ntt_mig_")
        try:
            path = os.path.join(tmp, "legacy.db")
            conn = __import__("sqlite3").connect(path)
            conn.execute(
                "CREATE TABLE etf_daily(date TEXT, code TEXT, name TEXT, cat TEXT,"
                " price REAL, chg REAL, amount REAL, main_net REAL, PRIMARY KEY(date, code))"
            )
            conn.execute(
                "INSERT INTO etf_daily VALUES('2026-10-26','510300','华泰柏瑞沪深300ETF',"
                "'沪深300',4.10,-1.34,5.8e9,-8.61e8)"
            )
            conn.commit()
            conn.close()
            db.init_db(path)   # 触发 ALTER 迁移
            rows = db.get_etf_by_date("2026-10-26", path=path)
            self.assertEqual(rows[0]["main_net"], -8.61e8)
            self.assertIsNone(rows[0]["huge_net"])
            db.save_etf_daily("2026-10-27", [
                {"code": "510300", "name": "华泰柏瑞沪深300ETF", "cat": "沪深300",
                 "price": 4.1, "chg": 0.1, "amount": 1e9, "main_net": 2e8,
                 "huge_net": 1e8, "big_net": 1e8, "mid_net": -1e8, "small_net": -1e8},
            ], path=path)
            r = db.get_etf_by_date("2026-10-27", path=path)[0]
            self.assertAlmostEqual(r["huge_net"], 1e8)
            self.assertAlmostEqual(r["small_net"], -1e8)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ---------- ETF 份额（一级市场申赎口径） ----------
    def test_share_rows_roundtrip(self):
        """份额/净值/日变动/净申购读写不丢。"""
        rows = {r["code"]: r for r in db.get_shares_by_date(D1, path=self.path)}
        self.assertEqual(len(rows), 3)
        r = rows["510310"]
        self.assertAlmostEqual(r["shares"], 216.0154)
        self.assertAlmostEqual(r["nav"], 4.0618)
        self.assertAlmostEqual(r["chg_shares"], 32.418)
        self.assertAlmostEqual(r["net_sub"], 131.68)

    def test_share_net_sub_identity(self):
        """一级净申购 = 份额日变动(亿份) × 单位净值(元)。"""
        for r in db.get_shares_by_date(D1, path=self.path):
            self.assertAlmostEqual(r["net_sub"], r["chg_shares"] * r["nav"], places=2,
                                   msg=r["code"])

    def test_share_filter_by_category(self):
        rows = db.get_shares_by_date(D1, cats=["沪深300"], path=self.path)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["cat"] == "沪深300" for r in rows))

    def test_share_dates_only_covered(self):
        """只有写入过份额的日期才出现（前端据此显示 — 而不是 0）。"""
        self.assertEqual(db.get_share_dates(path=self.path), [D1])
        self.assertEqual(db.get_shares_by_date(D2, path=self.path), [])

    # ---------- 选项 ----------
    def test_get_cats(self):
        cats = {c["cat"]: c["count"] for c in db.get_cats(path=self.path)}
        self.assertEqual(cats, {"沪深300": 2, "上证50": 1, "科创50": 1})

    def test_get_funds(self):
        funds = db.get_funds(path=self.path)
        self.assertEqual(len(funds), 4)
        for f in funds:
            self.assertIn("code", f)
            self.assertIn("name", f)
            self.assertIn("cat", f)

    def test_latest_main_date(self):
        """最近一个存在有效主力数据的日期（跳过主力缺失日）。"""
        self.assertEqual(db.latest_main_date(path=self.path), D1)

    # ---------- 历史聚合 ----------
    def test_history_full(self):
        hist = db.get_history(path=self.path)
        self.assertEqual([h["date"] for h in hist], [D1, D2, D3])
        total_d1 = sum(r["main_net"] for r in ROWS_D1)
        self.assertAlmostEqual(hist[0]["total_main_net"], total_d1)
        self.assertEqual(hist[0]["main_cnt"], 4)
        # 主力缺失日：合计为 0，但 main_cnt 为 0（前端据此断线）
        self.assertEqual(hist[1]["main_cnt"], 0)
        self.assertEqual(hist[1]["total_main_net"], 0.0)
        self.assertEqual(hist[1]["etf_count"], 4)

    def test_history_filtered_by_category(self):
        hist = db.get_history(cats=["沪深300"], path=self.path)
        self.assertEqual(len(hist), 3)
        self.assertEqual(hist[0]["etf_count"], 2)
        expected = sum(r["main_net"] for r in ROWS_D1 if r["cat"] == "沪深300")
        self.assertAlmostEqual(hist[0]["total_main_net"], expected)
        self.assertEqual(set(hist[0]["by_cat"].keys()), {"沪深300"})

    def test_history_filtered_by_code(self):
        hist = db.get_history(codes=["588000"], path=self.path)
        self.assertEqual(hist[0]["etf_count"], 1)
        self.assertAlmostEqual(hist[0]["total_main_net"], -14.73e8)

    def test_history_empty_filter(self):
        self.assertEqual(db.get_history(cats=["不存在的类别"], path=self.path), [])


class TestSchema(unittest.TestCase):
    def test_init_creates_tables(self):
        tmp = tempfile.mkdtemp(prefix="ntt_schema_")
        try:
            path = os.path.join(tmp, "s.db")
            db.init_db(path)
            self.assertEqual(db.get_dates(path=path), [])
            self.assertEqual(db.get_cats(path=path), [])
            self.assertEqual(db.get_funds(path=path), [])
            self.assertIsNone(db.latest_main_date(path=path))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_init_is_idempotent(self):
        tmp = tempfile.mkdtemp(prefix="ntt_schema2_")
        try:
            path = os.path.join(tmp, "s.db")
            db.init_db(path)
            db.init_db(path)   # 重复初始化不应报错
            self.assertEqual(db.get_dates(path=path), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
