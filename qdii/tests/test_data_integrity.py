"""qdii/tests/test_data_integrity.py — v2.6.2 数据完整性回归测试（离线）。

覆盖三个静默坏掉的坑：
1. fetch_us_signal 的 VIX 腾讯兜底：旧代码 `price, chg = t2["usVIX"]`
   从 3 元组解 2 个变量 → ValueError；即便解出来，append 的 tuple 是 9 项
   但 upsert 需要 10 项 → ProgrammingError。CBOE 挂掉时 VIX 直接消失，
   risk_off_assess 拿到 vix=None，避险 ×0.5 开关永远不动，页面毫无提示。
2. fetch_platform_fee 抓不到天天基金/理财通时不得覆盖既有费率与在售状态。
3. /api/qdii/status 的 `codes` 字段在 MARKET_SIGNALS 混用 4/5 元组时
   不得抛 IndexError（这正是 sig/codes 指纹要检测的场景，不能自己先崩）。
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
QDIR = os.path.join(ROOT, "qdii")
for p in (ROOT, QDIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import db  # noqa: E402
import constants as C  # noqa: E402


class VixTencentFallbackShapeTest(unittest.TestCase):
    """fetch_tencent_us 返回 (price, chg, prev)，VIX 兜底必须按 3 元组解、
    且 append 到 upsert 的 tuple 必须是 10 列（与主源 CBOE 同形状）。"""

    def test_signal_writes_10_tuple_when_only_tencent_available(self):
        """桩掉所有主源，只让腾讯 usVIX 有值；检查最终入库行有 change_value。"""
        import fetcher_signal as fs

        tmp_path = os.path.join(tempfile.gettempdir(),
                                "qdii_vix_fallback_%d.db" % os.getpid())
        # 干净起库
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        db.init_db(tmp_path)

        # 让所有主源抛异常；腾讯 usVIX 返回 (price, chg, prev) 3 元组
        def _boom(*a, **k):
            raise RuntimeError("simulated outage")

        old = {
            "fetch_cnbc_ust10y": fs.fetch_cnbc_ust10y,
            "fetch_cboe_vix": fs.fetch_cboe_vix,
            "fetch_sina_us_index": fs.fetch_sina_us_index,
            "fetch_sina_futures": fs.fetch_sina_futures,
            "fetch_tencent_us": fs.fetch_tencent_us,
            "fetch_cnbc_global_index": getattr(fs, "fetch_cnbc_global_index", None),
            "fetch_eastmoney_index": getattr(fs, "fetch_eastmoney_index", None),
            "fetch_sina_a_etf": getattr(fs, "fetch_sina_a_etf", None),
        }
        fs.fetch_cnbc_ust10y = _boom
        fs.fetch_cboe_vix = _boom
        fs.fetch_sina_us_index = _boom
        fs.fetch_sina_futures = _boom
        fs.fetch_tencent_us = lambda codes=None, *a, **k: (
            {"usVIX": (21.5, 0.85, 21.3)} if codes == "usVIX" else {}
        )
        try:
            fs.fetch_us_signal(date="2026-10-30", path=tmp_path)
        finally:
            for k, v in old.items():
                if v is not None:
                    setattr(fs, k, v)

        rows = db.get_conn(tmp_path).execute(
            "SELECT symbol, last_price, change_pct, change_value FROM qdii_us_signal "
            "WHERE date='2026-10-30' AND symbol='VIX'"
        ).fetchall()
        try:
            tmp_path and os.remove(tmp_path)
        except OSError:
            pass
        self.assertEqual(len(rows), 1, "CBOE 挂掉时腾讯兜底必须落 VIX 一行")
        sym, price, chg, chg_val = rows[0]
        self.assertAlmostEqual(price, 21.5)
        self.assertAlmostEqual(chg, 0.85)
        # 修复前这里会是 None（因为整个 except 吞掉，压根没落库）
        self.assertIsNotNone(chg_val, "change_value 列不能因 3/9/10 元组错位而丢")


class FeePreservesGoodHistory(unittest.TestCase):
    """抓失败的一只基金不得把库里已有的费率/在售状态洗成 NULL/0。"""

    def test_tt_fee_preserves_row_when_info_empty(self):
        import fetcher_fee as ff

        tmp_path = os.path.join(tempfile.gettempdir(),
                                "qdii_fee_preserve_%d.db" % os.getpid())
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        db.init_db(tmp_path)
        conn = db.get_conn(tmp_path)
        # 预置：161125 天天基金在售 + 管理费 1.0 + 托管费 0.2
        conn.execute(
            "INSERT INTO qdii_platform_fee(code,platform,on_sale,mgmt_fee,"
            "custodian_fee,sub_fee_rate,source,fetched_at) VALUES(?,?,?,?,?,?,?,?)",
            ("161125", "天天基金", 1, 1.0, 0.2, 0.12, "eastmoney_rateinfo",
             "2026-10-29T10:00:00"),
        )
        conn.commit()
        conn.close()

        # 桩掉两个上游：_fetch_all_raw 返回空 info（本次一只都没抓到）
        def _all_raw(codes, workers=8):
            return {c: ({}, None) for c in codes}

        old = ff._fetch_all_raw
        ff._fetch_all_raw = _all_raw
        try:
            ff.fetch_platform_fee(date="2026-10-30", path=tmp_path)
        finally:
            ff._fetch_all_raw = old

        conn = db.get_conn(tmp_path)
        row = conn.execute(
            "SELECT on_sale, mgmt_fee, custodian_fee, sub_fee_rate "
            "FROM qdii_platform_fee WHERE code='161125' AND platform='天天基金'"
        ).fetchone()
        conn.close()
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        self.assertEqual(row["on_sale"], 1, "抓失败不能把在售翻成不可售")
        self.assertAlmostEqual(row["mgmt_fee"], 1.0,
                               "抓失败不能把已归档的管理费洗成 NULL")


class StatusSigFingerprintGuardsMixedTuples(unittest.TestCase):
    """/api/qdii/status 的 sig/codes 不能因为混用 4/5 元组常量就自己抛 IndexError。

    v2.6.1 只修了 /api/qdii/signal 的第 5 字段守卫；status 接口里
    `[s[4] for s in MARKET_SIGNALS]` 依然只判 `MARKET_SIGNALS[0]` 的长度，
    一旦新旧常量在同一进程里混跑（部署时的典型状态），IndexError 会把
    status 也打挂 —— 而 status 恰好是 gateway/combo 的健康检查在轮询的接口。
    """

    def test_status_survives_mixed_4_and_5_tuples(self):
        import qdii_app

        original = C.MARKET_SIGNALS
        C.MARKET_SIGNALS = [
            ("IXIC", "纳指现货", "美股", "指数", "IXIC"),   # 新 5 元组
            ("NQ", "纳指期货", "美股", "期货"),             # 旧 4 元组（模拟漂移）
            ("SPX", "标普500现货", "美股", "指数", "SPX"),
        ]
        try:
            data = qdii_app.route_api("/api/qdii/status")
            # 至少要有 sig + codes 指纹字段
            self.assertIn("sig", data)
            self.assertIn("codes", data)
            # codes 里短元组以空串占位，整体不能崩
            self.assertIsInstance(data["codes"], list)
            self.assertEqual(len(data["codes"]), 3)
            self.assertEqual(data["codes"][1], "")
        finally:
            C.MARKET_SIGNALS = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
