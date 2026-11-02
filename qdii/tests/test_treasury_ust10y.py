"""fetch_treasury_ust10y 解析测试（mock fetch_text，隔离网络）。

真实样本来自 home.treasury.gov 日频收益率曲线 CSV（2026-11-02 实测）：
表头含 "10 Yr"，日期 MM/DD/YYYY；最近两个交易日 10/30=4.94、10/29=4.98。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
QDIR = os.path.join(ROOT, "qdii")
for p in (ROOT, QDIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import fetcher_signal as fs  # noqa: E402

_SAMPLE = (
    'Date,"1 Mo","2 Mo","3 Mo","6 Mo","1 Yr","2 Yr","3 Yr","5 Yr",'
    '"7 Yr","10 Yr","20 Yr","30 Yr"\n'
    "10/30/2026,4.06,4.16,4.16,4.25,4.36,4.56,4.65,4.71,4.80,4.94,5.34,5.31\n"
    "10/29/2026,4.06,4.17,4.18,4.28,4.42,4.61,4.73,4.78,4.87,4.98,5.35,5.32\n"
    "10/28/2026,4.05,4.16,4.17,4.27,4.41,4.60,4.72,4.77,4.86,4.97,5.34,5.30\n"
)


class TreasuryUst10yTest(unittest.TestCase):
    def setUp(self):
        self._old = fs.fetch_text
        fs.fetch_text = lambda *a, **k: _SAMPLE

    def tearDown(self):
        fs.fetch_text = self._old

    def test_parse_latest_and_bp(self):
        yield_v, bp, chg_pct, as_of = fs.fetch_treasury_ust10y()
        self.assertAlmostEqual(yield_v, 4.94)
        # (4.94 - 4.98) * 100 = -4.0 bp
        self.assertAlmostEqual(bp, -4.0)
        self.assertIn("美财政部 T+1", as_of)
        self.assertIn("2026-10-30", as_of)

    def test_parse_skips_na_and_falls_back(self):
        sample = _SAMPLE.replace("4.94,5.34,5.31", "N/A,5.34,5.31")
        fs.fetch_text = lambda *a, **k: sample
        yield_v, bp, chg_pct, as_of = fs.fetch_treasury_ust10y()
        # 最新一行 10 Yr = N/A 被跳过，退回 10/29 的 4.98 vs 10/28 的 4.97
        self.assertAlmostEqual(yield_v, 4.98)
        self.assertAlmostEqual(bp, 1.0)
        self.assertIn("2026-10-29", as_of)

    def test_parser_handles_title_row_and_quotes(self):
        # 前置一行无关标题，验证定位表头逻辑（CSV 为最新在前，故 vals[0] 最新）
        titled = "Daily Treasury Yield Curve Rates\n" + _SAMPLE
        fs.fetch_text = lambda *a, **k: titled
        vals = fs._parse_treasury_10y(titled)
        self.assertEqual(vals[0][0], "2026-10-30")
        self.assertAlmostEqual(vals[0][1], 4.94)


if __name__ == "__main__":
    unittest.main()
