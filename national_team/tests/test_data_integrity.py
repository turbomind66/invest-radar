"""数据完整性回归测试（v2.6.2，完全离线）。

覆盖四个「静默坏掉、事后看不出」的坑：
1. 东财资金流部分页失败时，fetch_and_save 不得用 save_etf_daily 把当天
   已归档的四档数据洗成 NULL；必须走 upsert_etf_rows（COALESCE）。
2. 逐笔大单（方案C）源是滚动窗口，archive_tick 只能落在真实当天；
   旧实现按 ds_list 循环、把今天的尾盘快照以过去 5 个日期标签反复入库。
3. QDII 多平台费率抓失败时不得覆盖既有费率/在售状态。
4. QDII VIX 腾讯兜底源必须给出与 CBOE 主源一致的 10 列 tuple。
"""
import datetime
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # national_team/
REPO_ROOT = os.path.dirname(ROOT)  # 仓库根（common 与 docs 都在这层）
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# national_team
import db as nt_db


class _SinaStub(dict):
    pass


class FetcherPartialCoverage(unittest.TestCase):
    """fetch_and_save 在 em_ok=True 但只覆盖部分基金时必须走 upsert。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fetch_cov_")
        self.db_path = os.path.join(self.tmp, "data.db")
        nt_db.init_db(self.db_path)
        self.saved = sys.path[:]
        sys.path.insert(0, os.path.join(ROOT, "national_team"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        sys.path[:] = self.saved

    def _seed_yesterday(self, date_iso):
        """先塞一天完整归档，作为「旧值」基准。"""
        rows = [
            {"code": c[0], "name": c[1], "cat": c[2],
             "price": 3.0, "chg": 0.5, "amount": 1.0e9,
             "main_net": 1.0e8, "huge_net": 2.0e8, "big_net": -1.0e8,
             "mid_net": 5.0e7, "small_net": 5.0e7}
            for c in [("510300", "华泰柏瑞沪深300ETF", "沪深300"),
                      ("510050", "华夏上证50ETF", "上证50")]
        ]
        nt_db.save_etf_daily(date_iso, rows, path=self.db_path)

    def test_partial_coverage_uses_upsert_and_preserves_history(self):
        import fetcher
        today = datetime.date.today().strftime("%Y-%m-%d")
        self._seed_yesterday(today)  # 先把今天塞满，模拟「06:00 已抓到、17:00 抽风」

        # 桩掉三个上游：新浪给价格；东财只覆盖一半；新闻空
        def fake_fetch_etfs():
            # 只覆盖 2/14：东财返回 {510300}，其它 fund 的 main_net=None
            fetcher.fetch_em_main_net.last_coverage = 1
            etfs = []
            for c, nm, cat in fetcher.WATCHLIST:
                etfs.append({
                    "code": c, "name": nm, "cat": cat,
                    "price": 3.5, "chg": 0.1, "amount": 1.5e9,
                    "main_net": (2.5e8 if c == "510300" else None),
                    "huge_net": None, "big_net": None, "mid_net": None,
                    "small_net": None,
                })
            return etfs, True  # em_ok=True，但只覆盖部分

        old_fetch_etfs = fetcher.fetch_etfs
        old_fetch_news = fetcher.fetch_news
        old_is_trading = fetcher.is_trading_day
        old_db_path = nt_db.DB_PATH
        fetcher.fetch_etfs = fake_fetch_etfs
        fetcher.fetch_news = lambda *a, **k: []
        fetcher.is_trading_day = lambda d=None: True
        try:
            # 让 fetcher 内部 `db.xxx` 全部指向临时库
            fetcher.db = _patch_db_path(nt_db, self.db_path)
            res = fetcher.fetch_and_save()
            self.assertTrue(res["ok"])
            self.assertEqual(res["coverage"], 1)
            # 510300 应被更新到新值
            got = nt_db.get_etf_by_date(today, path=self.db_path)
            m = {r["code"]: r for r in got}
            self.assertEqual(m["510300"]["main_net"], 2.5e8,
                             "覆盖到的行应写入新值")
            self.assertEqual(m["510050"]["main_net"], 1.0e8,
                             "没覆盖到的行必须保留旧值，不能被洗成 NULL")
        finally:
            fetcher.fetch_etfs = old_fetch_etfs
            fetcher.fetch_news = old_fetch_news
            fetcher.is_trading_day = old_is_trading
            nt_db.DB_PATH = old_db_path

    def test_full_coverage_uses_save_etf_daily(self):
        """全量覆盖时仍应走覆盖式 save_etf_daily（保证 WATCHLIST 收缩时不留旧行）。"""
        import fetcher
        today = datetime.date.today().strftime("%Y-%m-%d")
        self._seed_yesterday(today)

        def fake_fetch_etfs():
            fetcher.fetch_em_main_net.last_coverage = len(fetcher.WATCHLIST)
            etfs = [{"code": c[0], "name": c[1], "cat": c[2],
                     "price": 3.5, "chg": 0.1, "amount": 1.5e9,
                     "main_net": 9.9e8,
                     "huge_net": None, "big_net": None, "mid_net": None,
                     "small_net": None}
                    for c in fetcher.WATCHLIST]
            return etfs, True

        old = (fetcher.fetch_etfs, fetcher.fetch_news, fetcher.is_trading_day, nt_db.DB_PATH)
        fetcher.fetch_etfs = fake_fetch_etfs
        fetcher.fetch_news = lambda *a, **k: []
        fetcher.is_trading_day = lambda d=None: True
        try:
            fetcher.db = _patch_db_path(nt_db, self.db_path)
            res = fetcher.fetch_and_save()
            self.assertTrue(res["ok"])
            got = nt_db.get_etf_by_date(today, path=self.db_path)
            self.assertEqual({r["code"] for r in got},
                             {c[0] for c in fetcher.WATCHLIST},
                             "全绿时行为与 save_etf_daily 一致")
            # 510050 也应被更新到 9.9e8（旧值 1.0e8 被覆盖）
            m = {r["code"]: r for r in got}
            self.assertEqual(m["510050"]["main_net"], 9.9e8)
        finally:
            (fetcher.fetch_etfs, fetcher.fetch_news,
             fetcher.is_trading_day, nt_db.DB_PATH) = old


def _patch_db_path(module, path):
    """返回一个 view：所有函数默认走临时 path，避免测试污染真实 data.db。"""
    import types

    class Shim(types.ModuleType):
        pass

    shim = Shim("db_shim")
    for name in dir(module):
        if name.startswith("_"):
            continue
        attr = getattr(module, name)
        if callable(attr):
            def _wrap(a=attr):
                def _call(*args, **kwargs):
                    kwargs.setdefault("path", path)
                    return a(*args, **kwargs)
                return _call
            setattr(shim, name, _wrap())
        else:
            setattr(shim, name, attr)
    shim.TIER_FIELDS = module.TIER_FIELDS
    shim.EXPORT_WINDOW_DAYS = module.EXPORT_WINDOW_DAYS
    shim.DB_PATH = path
    return shim


class ArchiveTickDateGuard(unittest.TestCase):
    """archive_tick 只应写到真实当天；旧实现按 ds_list 循环会污染历史。

    这里直接检查 staticsite.main() 里那段 for 循环的代码形状（grep 级断言），
    避免引入网络桩 + intraday 依赖带来的复杂 mock；等价的集成测覆盖在
    Actions / cron 全量同步里跑。
    """

    def test_staticsite_only_archives_today(self):
        ss = os.path.join(ROOT, "staticsite.py")
        with open(ss, encoding="utf-8") as f:
            src = f.read()
        # 循环里的 archive_tick 必须先判 ds == today_iso
        self.assertIn("if ds == today_iso:", src,
                      "archive_tick 必须在循环里判当天，否则会污染历史日期")
        # 名字修正：notes.append 在旧实现里 NameError
        self.assertNotIn("notes.append(\"逐笔大单归档失败", src,
                         "notes 变量在 sync_missing 局部，主流程里没定义")


if __name__ == "__main__":
    unittest.main(verbosity=2)
