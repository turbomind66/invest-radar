# -*- coding: utf-8 -*-
"""common/market_session —— 全球市场交易时段（北京时间）判定。

丁总 2026-10-30 反馈：QDII 页「日经225」被标成「昨收·非实时」，但日经
东京 09:00–15:30 = 北京 08:00–14:30，晚上看到的其实是**当天收盘价**。
本文件把这些时段事实钉死，防止 is_realtime 再退回硬编码。
"""
import os
import sys
import unittest
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(BASE)
for p in (ROOT, BASE):
    if p not in sys.path:
        sys.path.insert(0, p)
if sys.path and sys.path[0] != BASE:
    sys.path.remove(BASE)
    sys.path.insert(0, BASE)

from common.market_session import market_status, known_symbols  # noqa: E402


def _at(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M")


class MarketSessionTest(unittest.TestCase):
    def test_nikkei_closes_1430_beijing(self):
        """日经225：北京 08:00–14:30 交易，晚间应为「已收盘 14:30」而非昨收。"""
        self.assertEqual(market_status("NK", _at("2026-10-30 19:00"))["market_status"],
                         "已收盘 14:30")
        self.assertEqual(market_status("NK", _at("2026-10-30 10:00"))["is_realtime"], 1)
        self.assertEqual(market_status("NK", _at("2026-10-30 14:00"))["is_realtime"], 1)
        self.assertEqual(market_status("NK", _at("2026-10-30 15:00"))["is_realtime"], 0)

    def test_europe_is_live_in_beijing_evening(self):
        """欧洲三大：北京约 15/16:00–次日 00:30，晚间是实时（旧代码错标成昨收）。"""
        for sym in ("DAX", "CAC40", "FTSE"):
            self.assertEqual(market_status(sym, _at("2026-10-30 19:00"))["is_realtime"], 1,
                             "%s 晚间应为实时" % sym)
        # 欧洲已入冬令时（10 月最后一个周日），开盘应为北京 16:00
        self.assertEqual(market_status("DAX", _at("2026-10-30 10:00"))["market_status"],
                         "已收盘 00:30 · 16:00 开盘")

    def test_us_cash_is_prev_close_before_2130(self):
        """美股现货/VIX：北京 21:30 前显示上一交易日收盘，21:30 后转实时。"""
        for sym in ("IXIC", "SPX", "VIX"):
            st = market_status(sym, _at("2026-10-30 19:00"))
            self.assertEqual(st["is_realtime"], 0, "%s 19:00 应为非实时" % sym)
            self.assertTrue(st["market_status"].startswith("已收盘 04:00"))
            self.assertEqual(market_status(sym, _at("2026-10-30 23:00"))["is_realtime"], 1)

    def test_futures_and_ust_are_realtime(self):
        for sym in ("NDX_FUT", "SPX_FUT", "UST10Y"):
            self.assertEqual(market_status(sym, _at("2026-10-30 19:00"))["is_realtime"], 1,
                             "%s 应为实时" % sym)

    def test_weekend_market_shows_closed_not_fake_close(self):
        """周末不能标成「已收盘 HH:MM」（看着像当天还在交易）。"""
        self.assertEqual(market_status("NK", _at("2026-10-31 10:00"))["market_status"],
                         "休市 · 上一交易日收盘")
        self.assertEqual(market_status("IXIC", _at("2026-11-01 10:00"))["market_status"],
                         "休市 · 上一交易日收盘")

    def test_us_holiday_not_shown_as_closed_today(self):
        """圣诞：美股休市，不应显示「已收盘 05:00」。"""
        self.assertEqual(market_status("IXIC", _at("2026-12-25 20:00"))["market_status"],
                         "休市 · 上一交易日收盘")

    def test_dst_switch_moves_us_open_one_hour(self):
        """美东夏令时 21:30 开盘、冬令时 22:30 开盘。"""
        # 2026-11-01 美东结束夏令时 → 11-02（周一）起北京 22:30 开盘
        self.assertEqual(market_status("SPX", _at("2026-11-02 21:00"))["next_open"], "22:30")
        self.assertEqual(market_status("SPX", _at("2026-10-30 19:00"))["next_open"], "21:30")

    def test_unknown_symbol_defaults_to_realtime(self):
        st = market_status("SOMETHING_NEW", _at("2026-10-30 19:00"))
        self.assertEqual(st["is_realtime"], 1)
        self.assertIn("NK", known_symbols())
        self.assertIn("UST10Y", known_symbols())


if __name__ == "__main__":
    unittest.main()
