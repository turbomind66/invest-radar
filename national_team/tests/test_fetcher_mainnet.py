# -*- coding: utf-8 -*-
"""national_team/fetcher.fetch_em_main_net —— 主力净额三源取数。

丁总 2026-10-30 反馈「晚上 6 点多了还是昨天的数据」：页面判定当日 main_net
全 NULL → 回退到前一交易日。根因是 clist 在 ETF 板块（2500+ 只）里翻 25 页
捞这 14 只，push2 出口不稳时覆盖率直接归零。
这里把「任一源可用即可凑齐 14 只」这条不变量钉死。
"""
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(BASE)
for p in (ROOT, BASE):
    if p not in sys.path:
        sys.path.insert(0, p)
if sys.path and sys.path[0] != BASE:
    sys.path.remove(BASE)
    sys.path.insert(0, BASE)

import fetcher  # noqa: E402


def _row():
    return {"main_net": 1.0, "huge_net": 2.0, "big_net": 3.0,
            "mid_net": 4.0, "small_net": 5.0}


class FetchMainNetTest(unittest.TestCase):
    def _patch(self, ulist=None, fflow=None, clist=None):
        """把三源替换成给定返回值（None 表示「该源抛异常」）。"""
        def _mk(val):
            def fn(*a, **k):
                if val is None:
                    raise IOError("simulated outage")
                return dict(val)
            return fn
        self._orig = (fetcher._em_ulist_main_net,
                      fetcher._em_fflow_daykline_main_net,
                      fetcher._em_clist_main_net)
        fetcher._em_ulist_main_net = _mk(ulist)
        fetcher._em_fflow_daykline_main_net = lambda d: dict(fflow) if fflow else {}
        fetcher._em_clist_main_net = _mk(clist)

    def tearDown(self):
        if getattr(self, "_orig", None):
            (fetcher._em_ulist_main_net,
             fetcher._em_fflow_daykline_main_net,
             fetcher._em_clist_main_net) = self._orig

    def test_ulist_alone_is_enough(self):
        """主源 ulist 一次请求拿全 14 只 —— 不再依赖板块翻页。"""
        full = {c: _row() for c, _n, _t in fetcher.WATCHLIST}
        self._patch(ulist=full)
        got = fetcher.fetch_em_main_net()
        self.assertEqual(len(got), len(fetcher.WATCHLIST))
        self.assertEqual(fetcher.fetch_em_main_net.last_coverage, len(fetcher.WATCHLIST))

    def test_fflow_backup_fills_the_gap(self):
        """ulist 挂了 → push2his fflow daykline 顶上（push2 整段断链时的救命路径）。"""
        head = {c: _row() for c, _n, _t in fetcher.WATCHLIST[:8]}
        tail = {c: _row() for c, _n, _t in fetcher.WATCHLIST[8:]}
        # ulist 挂了 → 只剩 fflow 的那部分（6 只），覆盖率如实上报
        self._patch(ulist=None, fflow=tail, clist=None)
        got = fetcher.fetch_em_main_net()
        self.assertEqual(len(got), len(tail))
        # ulist 给前 8 只 + fflow 补后 6 只 = 凑齐 14 只（setdefault：先到先得）
        self._patch(ulist=head, fflow=tail, clist=None)
        got = fetcher.fetch_em_main_net()
        self.assertEqual(len(got), len(fetcher.WATCHLIST))
        self.assertEqual(fetcher.fetch_em_main_net.last_coverage, len(fetcher.WATCHLIST))

    def test_all_sources_down_yields_empty(self):
        self._patch(ulist=None, fflow=None, clist=None)
        got = fetcher.fetch_em_main_net()
        self.assertEqual(got, {})
        self.assertEqual(fetcher.fetch_em_main_net.last_coverage, 0)


if __name__ == "__main__":
    unittest.main()
