"""drawdown/tests/test_reconstruct.py — 溢价/汇率还原口径离线单测（不联网）。

验证四曲线归一化、共同起点、口径方向（汇率剔除）、缺数据降级。
"""
import os
import tempfile
import unittest

from drawdown import db
from drawdown import reconstruct as RC


class TestReconstruct(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.path = self.tmp.name
        db.init_db(self.path)
        # 造一组可还原标的：513100 → ndx100(usd)，三条数据从 2020-01-02 起
        db.upsert_daily("513100", [("2020-01-02", 1.0), ("2020-01-03", 1.1),
                                   ("2020-01-06", 1.2)], self.path)
        db.save_nav("513100", [("2020-01-02", 1.0), ("2020-01-03", 1.1),
                               ("2020-01-06", 1.2)], self.path)
        db.upsert_daily("usd", [("2020-01-02", 7.0), ("2020-01-03", 7.0),
                                ("2020-01-06", 7.0)], self.path)
        db.upsert_daily("ndx100", [("2020-01-02", 100.0), ("2020-01-03", 110.0),
                                   ("2020-01-06", 120.0)], self.path)

    def tearDown(self):
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def test_four_curves_normalized(self):
        r = RC.reconstruct("513100", self.path)
        self.assertIsNone(r.get("error"))
        self.assertEqual(r["t0"], "2020-01-02")
        self.assertEqual(r["points"], 3)
        # 汇率恒定时净值% == 还原%（都涨 20%）
        self.assertAlmostEqual(r["curves"]["nav"][-1][1], 20.0, places=2)
        self.assertAlmostEqual(r["curves"]["restored"][-1][1], 20.0, places=2)
        self.assertAlmostEqual(r["curves"]["bench"][-1][1], 20.0, places=2)

    def test_fx_removed(self):
        """人民币贬值（USDCNY↑）→ 净值%>还原%，还原剔掉汇率贡献。"""
        db.upsert_daily("usd", [("2020-01-02", 7.0), ("2020-01-03", 7.0),
                                ("2020-01-06", 7.7)], self.path)  # 贬值10%
        r = RC.reconstruct("513100", self.path)
        nav_pct = r["curves"]["nav"][-1][1]
        restored_pct = r["curves"]["restored"][-1][1]
        # 净值涨20%（含汇率），剔除人民币贬值10%后真实涨约9%
        self.assertAlmostEqual(restored_pct, 9.09, places=1)
        self.assertGreater(nav_pct, restored_pct)

    def test_missing_data_downgrade(self):
        """删掉汇率 → 缺数据降级，不造假。"""
        conn = db.get_conn(self.path)
        conn.execute("DELETE FROM dd_daily WHERE asset='usd'")
        conn.commit()
        conn.close()
        r = RC.reconstruct("513100", self.path)
        self.assertEqual(r.get("error"), "missing_data")
        self.assertIn("汇率", r["missing"])

    def test_not_reconstructible(self):
        r = RC.reconstruct("sh000001", self.path)  # A股指数不可还原
        self.assertEqual(r.get("error"), "not_reconstructible")


if __name__ == "__main__":
    unittest.main()
