"""种子恢复（按日期增量）回归测试（v2.8.2，完全离线）。

背景（2026-11-02 修「WorkBuddy 版很多内容不显示」）：
    GitHub Pages / VPS 两种形态启动时都会跑 staticsite.import_from_days_file()，
    从上一版 docs/data.json 恢复 90 天历史；WorkBuddy 单应用形态**从来没调用过**
    这个钩子，库里只剩「用户手动刷新那一天」的 1 天数据 →
    趋势图只有 1 个点、份额列全是「—」、新闻整块空白、逐笔面板空。

    本次新增 restore_missing_from_days_file()：**按日期粒度补缺**，
    只补库里完全没有的日期，已有日期绝不覆盖（免得用旧快照盖掉当天真实值）。

覆盖：
  1. 缺失的 ETF 明细日期被补齐；
  2. 库里已有的日期**绝不被覆盖**（值保持本地真实值）；
  3. 一级份额按同样口径补缺；
  4. 新闻走 merge_news 增量合并（标题去重：已有条目不重复、新条目补进来）；
  5. 逐笔大单原始行按日期恢复；
  6. 重复调用幂等（第二次全部补 0）；
  7. 种子文件缺失 / 坏 JSON 时静默返回 0，绝不抛错阻断启动；
  8. **import_from_days_file 的原语义保持不变**（库非空且有新闻 → 仍返回 0），
     保证 Actions / Docker cron 路径零回归。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # national_team/
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import db  # noqa: E402
import intraday  # noqa: E402
import staticsite  # noqa: E402

D1 = "2026-10-28"   # 种子有、库里没有 → 应被补
D2 = "2026-10-29"   # 种子有、库里没有 → 应被补
D3 = "2026-10-30"   # 种子有、库里已有 → 绝不被覆盖


def _etf_rows(code, price, main_net):
    return [{
        "code": code, "name": "ETF-" + code, "cat": "沪深300",
        "price": price, "chg": 0.5, "amount": 1e8,
        "main_net": main_net, "huge_net": 1e8, "big_net": 1e8,
        "mid_net": 1e8, "small_net": 1e8,
    }]


def _share_rows(code, shares, net_sub):
    return [{
        "code": code, "name": "ETF-" + code, "cat": "沪深300",
        "shares": shares, "nav": 1.0, "chg_shares": 1e7, "net_sub": net_sub,
    }]


def _seed_payload():
    return {
        "days": {
            D1: _etf_rows("510300", 1.0, 1e8),
            D2: _etf_rows("510300", 1.1, 2e8),
            D3: _etf_rows("510300", 9.9, 9e9),   # 旧快照值，库里已有真实值
        },
        "shares": {
            D1: _share_rows("510300", 1e9, 1e7),
            D2: _share_rows("510300", 1.1e9, 2e7),
            D3: _share_rows("510300", 9.9e9, 9e9),
        },
        "news": {
            D1: [{"title": "旧闻A", "time": "09:00", "url": "u1", "src": "s1"}],
            D2: [{"title": "旧闻B", "time": "09:00", "url": "u2", "src": "s1"}],
            D3: [{"title": "旧闻C", "time": "09:00", "url": "u3", "src": "s1"}],
        },
        "tick_rows": {
            D2: [["510300", "10:00:00", 1.1, 100, 1, 500000.0]],
            D3: [["510300", "10:00:00", 9.9, 999, 1, 999999.0]],
        },
        "last_fetch": "2026-10-30 17:00:00",
    }


class TestSeedRestore(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="seed_restore_")
        self.dbp = os.path.join(self.tmp, "data.db")
        self.seed = os.path.join(self.tmp, "data.json")
        db.init_db(self.dbp)
        with open(self.seed, "w", encoding="utf-8") as f:
            json.dump(_seed_payload(), f, ensure_ascii=False)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------- 1. 缺失日期被补齐 ----------
    def test_missing_days_are_restored(self):
        res = staticsite.restore_missing_from_days_file(self.seed, self.dbp)
        self.assertEqual(res["days"], 3)
        self.assertIn(D1, db.get_dates(self.dbp))
        self.assertIn(D2, db.get_dates(self.dbp))
        self.assertEqual(len(db.get_etf_by_date(D1, path=self.dbp)), 1)
        self.assertEqual(len(db.get_etf_by_date(D2, path=self.dbp)), 1)

    def test_missing_shares_are_restored(self):
        res = staticsite.restore_missing_from_days_file(self.seed, self.dbp)
        self.assertEqual(res["shares"], 3)
        rows = db.get_shares_by_date(D1, path=self.dbp)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["shares"], 1e9)

    def test_news_merged_with_dedup(self):
        # 本地已有 D3 的一条新闻（标题与种子相同 → 不应重复）
        db.merge_news(D3, [{"title": "旧闻C", "time": "08:00",
                            "url": "local", "src": "local"}], self.dbp)
        res = staticsite.restore_missing_from_days_file(self.seed, self.dbp)
        # D1/D2 各新增 1 条；D3 的「旧闻C」本地已有 → 按标题去重，新增 0 条
        self.assertEqual(res["news"], 2)
        titles = [r["title"] for r in db.get_news_by_date(D3, self.dbp)]
        self.assertEqual(titles.count("旧闻C"), 1)  # 去重后仍只有一条
        # 本地那条时间/来源不被种子覆盖
        row = db.get_news_by_date(D3, self.dbp)[0]
        self.assertEqual(row["src"], "local")

    def test_tick_rows_restored(self):
        res = staticsite.restore_missing_from_days_file(self.seed, self.dbp)
        self.assertEqual(res["ticks"], 2)          # D2 缺 → 补；D3 库里有 → 不补
        rows = db.get_tick_by_date(D2, path=self.dbp)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "510300")
        self.assertEqual(rows[0]["amount"], 500000.0)

    # ---------- 2. 已有日期绝不被覆盖 ----------
    def test_existing_day_is_never_overwritten(self):
        # 库里放 D3 的**真实值**（与种子里的旧值不同）
        db.save_etf_daily(D3, _etf_rows("510300", 3.3, 4.4e8), self.dbp)
        db.save_share_rows(D3, _share_rows("510300", 2.2e9, 3.3e7), self.dbp)
        db.merge_news(D3, [{"title": "本地独有", "time": "07:00",
                            "url": "L", "src": "local"}], self.dbp)
        db.save_tick_rows(D3, [{"code": "510300", "name": "ETF-510300",
                                "cat": "沪深300", "ttime": "10:00:00",
                                "price": 1.5, "volume": 7, "direction": 1,
                                "amount": 777.0}], self.dbp)

        staticsite.restore_missing_from_days_file(self.seed, self.dbp)

        rows = db.get_etf_by_date(D3, path=self.dbp)
        self.assertAlmostEqual(rows[0]["price"], 3.3)      # 不是种子的 9.9
        self.assertAlmostEqual(rows[0]["main_net"], 4.4e8)  # 不是种子的 9e9
        sh = db.get_shares_by_date(D3, path=self.dbp)
        self.assertAlmostEqual(sh[0]["shares"], 2.2e9)      # 不是种子的 9.9e9
        nt = db.get_news_by_date(D3, self.dbp)
        self.assertEqual(len(nt), 2)                        # 本地独有 + 种子补入
        tk = db.get_tick_by_date(D3, path=self.dbp)
        self.assertEqual(len(tk), 1)
        self.assertAlmostEqual(tk[0]["amount"], 777.0)     # 不是种子的 999999

    # ---------- 3. 幂等 ----------
    def test_second_call_is_idempotent(self):
        first = staticsite.restore_missing_from_days_file(self.seed, self.dbp)
        self.assertTrue(any(first.values()))
        second = staticsite.restore_missing_from_days_file(self.seed, self.dbp)
        self.assertEqual(second["days"], 0)
        self.assertEqual(second["shares"], 0)
        self.assertEqual(second["news"], 0)      # 新闻按标题去重 → 0 新增
        self.assertEqual(second["ticks"], 0)
        # 数据量不膨胀
        self.assertEqual(len(db.get_dates(self.dbp)), 3)
        self.assertEqual(len(db.get_news_by_date(D1, self.dbp)), 1)

    # ---------- 4. 异常输入静默降级 ----------
    def test_missing_seed_file_returns_empty(self):
        res = staticsite.restore_missing_from_days_file(
            os.path.join(self.tmp, "nope.json"), self.dbp)
        self.assertEqual(res, {"days": 0, "shares": 0, "news": 0, "ticks": 0})

    def test_bad_json_returns_empty(self):
        bad = os.path.join(self.tmp, "bad.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("{not json")
        res = staticsite.restore_missing_from_days_file(bad, self.dbp)
        self.assertEqual(res["days"], 0)
        self.assertEqual(res["news"], 0)

    def test_empty_rows_are_skipped(self):
        payload = _seed_payload()
        payload["days"][D1] = []
        payload["shares"][D1] = []
        with open(self.seed, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        res = staticsite.restore_missing_from_days_file(self.seed, self.dbp)
        self.assertNotIn(D1, db.get_dates(self.dbp))
        self.assertEqual(res["shares"], 2)   # D2/D3 照常补

    # ---------- 5. GitHub/VPS 路径语义不变 ----------
    def test_import_from_days_file_semantics_unchanged(self):
        """库非空且有新闻 → import_from_days_file 仍返回 0（Actions/Docker 不变）。"""
        db.save_etf_daily(D3, _etf_rows("510300", 1.0, 1e8), self.dbp)
        db.merge_news(D3, [{"title": "x", "time": "", "url": "", "src": ""}], self.dbp)
        self.assertEqual(staticsite.import_from_days_file(self.seed, self.dbp), 0)

    def test_import_from_days_file_restores_into_empty_db(self):
        """库全空 → import_from_days_file 仍整批恢复（Actions/Docker 首启路径）。"""
        n = staticsite.import_from_days_file(self.seed, self.dbp)
        self.assertEqual(n, 3)
        self.assertIn(D1, db.get_dates(self.dbp))
        self.assertIn(D2, db.get_dates(self.dbp))


if __name__ == "__main__":
    unittest.main()
