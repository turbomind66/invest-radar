"""drawdown/tests/test_updater.py — 增量更新离线单测（不联网）。

用 mock 的 fetcher.fetch_asset 验证：
- 只抓库内已有标的（未回补的跳过）
- since 取 latest_date，无新数据不 rebuild、不误写
- 有新数据才 upsert + rebuild，失败标的留空不造假
"""
import os
import tempfile
import unittest
from unittest import mock

from drawdown import constants as C
from drawdown import db
from drawdown import updater


class TestIncremental(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.path = self.tmp.name
        db.init_db(self.path)
        db.upsert_daily("sh000001", [("2026-01-05", 100.0), ("2026-01-06", 101.0)],
                        self.path)

    def tearDown(self):
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def test_only_existing_assets(self):
        """库内没数据的标的（n225）不被抓；只处理 sh000001。"""
        with mock.patch.object(updater.fetcher, "fetch_asset",
                               return_value=[]) as m:
            res = updater.incremental(path=self.path, sleep=0, log=lambda x: None)
        self.assertEqual(list(k for k in res if k != "_changed"), ["sh000001"])
        self.assertEqual(m.call_count, 1)

    def test_no_new_rows_no_rebuild(self):
        """fetch 返回的都是 <=latest 的行 → 0 变更、不 rebuild。"""
        with mock.patch.object(updater.fetcher, "fetch_asset",
                               return_value=[("2026-01-05", 100.0),
                                             ("2026-01-06", 101.0)]), \
             mock.patch.object(updater.engine, "rebuild_asset") as rb:
            res = updater.incremental(path=self.path, sleep=0, log=lambda x: None)
        self.assertEqual(res["sh000001"], 0)
        self.assertEqual(res["_changed"], [])
        rb.assert_not_called()

    def test_new_rows_upsert_and_rebuild(self):
        """有新行 → upsert + rebuild，since 取 latest_date。"""
        with mock.patch.object(updater.fetcher, "fetch_asset",
                               return_value=[("2026-01-06", 101.0),
                                             ("2026-01-07", 102.5)]) as m, \
             mock.patch.object(updater.engine, "rebuild_asset") as rb:
            res = updater.incremental(path=self.path, sleep=0, log=lambda x: None)
        self.assertEqual(res["sh000001"], 1)
        self.assertEqual(res["_changed"], ["sh000001"])
        self.assertEqual(m.call_args.kwargs["since"], "2026-01-06")
        rb.assert_called_once()
        rows = db.get_daily("sh000001", self.path)
        self.assertEqual(rows[-1], ("2026-01-07", 102.5))

    def test_fetch_failure_leaves_empty(self):
        """抓取抛异常 → 留空、记 0，不写假数据。"""
        with mock.patch.object(updater.fetcher, "fetch_asset",
                               side_effect=RuntimeError("source down")), \
             mock.patch.object(updater.engine, "rebuild_asset") as rb:
            res = updater.incremental(path=self.path, sleep=0, log=lambda x: None)
        self.assertEqual(res["sh000001"], 0)
        rb.assert_not_called()
        self.assertEqual(len(db.get_daily("sh000001", self.path)), 2)


if __name__ == "__main__":
    unittest.main()
