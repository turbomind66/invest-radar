"""新闻栏日期段：db.get_news_range / app.build_news_payload 离线单测。

覆盖两个此前没有的能力：
  1) 按发布日期闭区间取新闻（含 date 字段、跨天按时间倒序）；
  2) /api/news 的数据组装（条数、日期回显、标题去敏、news_ok、空区间）。

运行：python -m unittest discover -s tests -p "test_*.py" -v
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402
import db  # noqa: E402


def _n(title, time, src="测试源"):
    return {"time": time, "title": title,
            "url": "https://example.com/" + title, "src": src}


class _Fixture:
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nt_news_range_")
        self.path = os.path.join(self.tmp, "test.db")
        db.init_db(self.path)
        db.merge_news("2026-10-27", [_n("甲", "2026-10-27 09:00")], path=self.path)
        db.merge_news("2026-10-28", [_n("乙", "2026-10-28 10:00"),
                                     _n("丙", "2026-10-28 08:00")], path=self.path)
        db.merge_news("2026-10-30", [_n("国家队增持宽基ETF", "2026-10-30 18:41")],
                      path=self.path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestGetNewsRange(_Fixture, unittest.TestCase):
    def test_closed_interval_and_desc_order(self):
        """闭区间、跨天按 time 倒序；date 字段供前端分组。"""
        rows = db.get_news_range("2026-10-27", "2026-10-30", path=self.path)
        self.assertEqual(len(rows), 4)
        self.assertEqual([r["title"] for r in rows],
                         ["国家队增持宽基ETF", "乙", "丙", "甲"])
        self.assertEqual(rows[0]["date"], "2026-10-30")
        self.assertEqual(rows[-1]["date"], "2026-10-27")

    def test_same_day_is_single_day_query(self):
        rows = db.get_news_range("2026-10-28", "2026-10-28", path=self.path)
        self.assertEqual({r["title"] for r in rows}, {"乙", "丙"})
        self.assertEqual([r["title"] for r in rows], ["乙", "丙"])

    def test_row_has_all_fields(self):
        row = db.get_news_range("2026-10-27", "2026-10-27", path=self.path)[0]
        self.assertEqual(set(row), {"date", "time", "title", "url", "src"})

    def test_empty_interval(self):
        self.assertEqual(db.get_news_range("2026-11-01", "2026-11-30", path=self.path), [])

    def test_reversed_interval_returns_empty(self):
        """路由层会先挡掉 from>to；数据层也不能反向吐数据。"""
        self.assertEqual(db.get_news_range("2026-10-30", "2026-10-27", path=self.path), [])


class TestBuildNewsPayload(_Fixture, unittest.TestCase):
    def test_payload_shape_and_mask(self):
        p = app.build_news_payload("2026-10-27", "2026-10-30", path=self.path)
        self.assertEqual((p["from"], p["to"]), ("2026-10-27", "2026-10-30"))
        self.assertEqual(p["count"], 4)
        self.assertEqual(len(p["items"]), p["count"])
        self.assertTrue(p["news_ok"])
        titles = [i["title"] for i in p["items"]]
        self.assertIn("汪汪队增持宽基ETF", titles)       # 与其它出口同口径去敏
        self.assertNotIn("国家队增持宽基ETF", titles)
        self.assertTrue(all("date" in i for i in p["items"]))

    def test_news_ok_flag_follows_meta(self):
        db.set_meta("news_ok", "0", path=self.path)
        p = app.build_news_payload("2026-10-27", "2026-10-30", path=self.path)
        self.assertFalse(p["news_ok"])

    def test_empty_range(self):
        p = app.build_news_payload("2026-11-01", "2026-11-30", path=self.path)
        self.assertEqual(p["count"], 0)
        self.assertEqual(p["items"], [])

    def test_single_day_matches_existing_api(self):
        """起止同一天时，结果必须与 /api/data 的当日 news 完全一致。"""
        p = app.build_news_payload("2026-10-28", "2026-10-28", path=self.path)
        self.assertEqual({i["title"] for i in p["items"]},
                         {i["title"] for i in db.get_news_by_date("2026-10-28", path=self.path)})


class TestDateParse(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(app._parse_iso_date("2026-11-04").isoformat(), "2026-11-04")
        self.assertEqual(app._parse_iso_date("2026-02-29"), None)  # 非闰年

    def test_invalid(self):
        for s in ("", None, "20261104", "2026-13-01", "2026-2-3", "abcd-ef-gh",
                  "2026-11-04 ", "2026/11/04"):
            self.assertIsNone(app._parse_iso_date(s), s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
