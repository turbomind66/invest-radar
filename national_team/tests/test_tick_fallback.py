"""逐笔大单兜底源回归测试（v2.8.3，完全离线）。

验证 _fetch_remote_tick_rows / _apply_tick_fallback 在「本机逐笔抓空」时能从
公开快照 data.json 的 tick_rows[date] 借到同日大单：
1. 正常解析 tick_rows[date] 返回紧凑行；脏行被过滤；别的日期不混入；
2. 本地该日期完全无大单 → 借入并落库（restore_tick_rows），db.get_tick_by_date 可见；
3. 本地该日期已有大单 → 不借（保护真实抓取值）；
4. 快照不含目标日期 → 借不到，落库 0 行；
5. 网络异常 / 坏 JSON 静默返回 0，绝不抛错阻断主流程；
6. NT_TICK_FALLBACK_URL 为空时直接返回 0（自托管关闭兜底场景）。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # national_team/
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import fetcher  # noqa: E402
import db  # noqa: E402

TODAY = "2026-11-02"
OTHER = "2026-10-30"


def _clear_cache():
    # _FUND_FLOW_CACHE 是模块级缓存，单次运行内复用快照；测试间需清空避免串味
    fetcher._FUND_FLOW_CACHE.clear()


def _payload():
    """公开快照 data.json 的最小子集（只放与兜底相关的字段）。"""
    codes = [c for c, _n, _t in fetcher.WATCHLIST]
    c0, c1 = codes[0], codes[1]
    # TODAY 给 3 笔真实大单（含买/卖方向）+ 1 个脏行（非 6 元组，应被过滤）
    # OTHER 给 1 笔——验证「只借同日」：请求 TODAY 时绝不混入 OTHER
    tick_rows = {
        TODAY: [
            [c0, "09:35:11", 1.0, 500, 1, 50000.0],
            [c0, "10:12:03", 1.01, 800, 2, 80800.0],
            [c1, "11:01:55", 2.0, 300, 1, 60000.0],
            "这是脏行不是6元组",  # 应被过滤
        ],
        OTHER: [[c1, "14:00:00", 2.0, 300, 1, 60000.0]],
    }
    return {"tick_rows": tick_rows}


def _make_apply_payload():
    """_apply_tick_fallback 借入时用的快照（只含 TODAY，3 笔干净行）。"""
    codes = [c for c, _n, _t in fetcher.WATCHLIST]
    c0, c1 = codes[0], codes[1]
    return {
        "tick_rows": {
            TODAY: [
                [c0, "09:35:11", 1.0, 500, 1, 50000.0],
                [c0, "10:12:03", 1.01, 800, 2, 80800.0],
                [c1, "11:01:55", 2.0, 300, 1, 60000.0],
            ],
        }
    }


class TestFetchRemoteTickRows(unittest.TestCase):
    def setUp(self):
        _clear_cache()

    def test_parse_today_filters_dirty(self):
        with patch.object(fetcher, "_get", return_value=json.dumps(_payload())):
            rows = fetcher._fetch_remote_tick_rows(TODAY)
        # 3 笔真实 + 1 脏行被过滤 = 3 行
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(len(r) >= 6 for r in rows))

    def test_per_date_isolation(self):
        with patch.object(fetcher, "_get", return_value=json.dumps(_payload())):
            other_rows = fetcher._fetch_remote_tick_rows(OTHER)
            today_rows = fetcher._fetch_remote_tick_rows(TODAY)
        self.assertEqual(len(other_rows), 1)  # OTHER 只有 1 笔
        self.assertEqual(len(today_rows), 3)  # TODAY 有 3 笔，且不混入 OTHER

    def test_absent_date_returns_empty(self):
        with patch.object(fetcher, "_get", return_value=json.dumps(_payload())):
            rows = fetcher._fetch_remote_tick_rows("2099-01-01")
        self.assertEqual(rows, [])


class TestApplyTickFallback(unittest.TestCase):
    def setUp(self):
        _clear_cache()
        self.tmp = tempfile.mkdtemp(prefix="ntt_tickfb_")
        self.path = os.path.join(self.tmp, "test.db")
        db.init_db(self.path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fills_when_local_empty(self):
        # 本地该日期无大单 → 借入并落库
        self.assertFalse(db.get_tick_by_date(TODAY, path=self.path))
        with patch.object(fetcher, "_get", return_value=json.dumps(_make_apply_payload())):
            n = fetcher._apply_tick_fallback(TODAY, path=self.path)
        self.assertEqual(n, 3)
        rows = db.get_tick_by_date(TODAY, path=self.path)
        self.assertEqual(len(rows), 3)
        # name/cat 从 WATCHLIST 反查补回
        self.assertTrue(all(r["name"] for r in rows))
        self.assertEqual(db.get_meta("tick_fallback", "0", path=self.path), "1")

    def test_local_present_not_overwritten(self):
        # 本地已有该日期大单 → 不借（保护真实抓取值）
        codes = [c for c, _n, _t in fetcher.WATCHLIST]
        db.save_tick_rows(TODAY, [{
            "code": codes[0], "name": "X", "cat": "沪深300",
            "ttime": "09:30:00", "price": 1.0, "volume": 100,
            "direction": 1, "amount": 10000.0,
        }], path=self.path)
        with patch.object(fetcher, "_get", return_value=json.dumps(_make_apply_payload())):
            n = fetcher._apply_tick_fallback(TODAY, path=self.path)
        self.assertEqual(n, 0)  # 本地已有 → 不借
        rows = db.get_tick_by_date(TODAY, path=self.path)
        self.assertEqual(len(rows), 1)  # 仍是本地那 1 笔，未被快照覆盖
        self.assertEqual(db.get_meta("tick_fallback", "0", path=self.path), "0")

    def test_absent_date_in_snapshot(self):
        with patch.object(fetcher, "_get", return_value=json.dumps(_make_apply_payload())):
            n = fetcher._apply_tick_fallback("2099-01-01", path=self.path)
        self.assertEqual(n, 0)
        self.assertFalse(db.get_tick_by_date("2099-01-01", path=self.path))

    def test_network_error_silent(self):
        with patch.object(fetcher, "_get", side_effect=Exception("boom")):
            n = fetcher._apply_tick_fallback(TODAY, path=self.path)
        self.assertEqual(n, 0)
        self.assertFalse(db.get_tick_by_date(TODAY, path=self.path))

    def test_bad_json_silent(self):
        with patch.object(fetcher, "_get", return_value="<html>blocked</html>"):
            n = fetcher._apply_tick_fallback(TODAY, path=self.path)
        self.assertEqual(n, 0)

    def test_empty_url_env_returns_zero(self):
        with patch.object(fetcher, "NT_TICK_FALLBACK_URL", ""):
            n = fetcher._apply_tick_fallback(TODAY, path=self.path)
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
