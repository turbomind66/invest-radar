"""为跨语言一致性测试生成"期望值"（用服务端 Python 逻辑算）。

由 tests/test_static.js 调用：javaScript 端的 computeStaticPayload 必须
与这里产出的服务端 build_payload 结果逐字段一致，防止两套逻辑漂移。

用法：
    python tests/gen_static_expected.py      # JSON 打到 stdout
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app          # noqa: E402
import db           # noqa: E402
import staticsite   # noqa: E402

D1, D2, D3 = "2026-10-26", "2026-10-27", "2026-10-28"

ROWS = [
    {"code": "510300", "name": "华泰柏瑞沪深300ETF", "cat": "沪深300",
     "price": 4.10, "chg": -1.34, "amount": 5.8e9, "main_net": -8.61e8,
     "huge_net": -12.50e8, "big_net": 3.89e8, "mid_net": 2.10e8, "small_net": 1.20e8},
    {"code": "510310", "name": "易方达沪深300ETF", "cat": "沪深300",
     "price": 1.90, "chg": -1.20, "amount": 1.2e9, "main_net": -1.10e8,
     "huge_net": -2.20e8, "big_net": 1.10e8, "mid_net": 0.60e8, "small_net": 0.50e8},
    {"code": "510050", "name": "华夏上证50ETF", "cat": "上证50",
     "price": 3.10, "chg": 0.40, "amount": 2.1e9, "main_net": 1.66e8,
     "huge_net": 2.20e8, "big_net": -0.54e8, "mid_net": -0.80e8, "small_net": -0.40e8},
    {"code": "588000", "name": "华夏科创50ETF", "cat": "科创50",
     "price": 1.10, "chg": -3.10, "amount": 3.0e9, "main_net": -14.73e8,
     "huge_net": -23.06e8, "big_net": 8.33e8, "mid_net": 11.57e8, "small_net": 2.08e8},
]
ROWS_D2 = [
    dict(ROWS[0], main_net=-4.20e8, huge_net=-6.10e8, big_net=1.90e8),
    dict(ROWS[1], main_net=None, huge_net=None, big_net=None, mid_net=None, small_net=None),
    dict(ROWS[2], main_net=2.05e8, huge_net=2.60e8, big_net=-0.55e8),
    dict(ROWS[3], main_net=-6.00e8, huge_net=-9.10e8, big_net=3.10e8),
]
ROWS_D3 = [dict(r, main_net=None) for r in ROWS]   # 盘前日：主力全缺

CASES = [
    ("默认自动最新（回退到最近有效日）", {}),
    ("指定最新日(主力全缺)", {"date": D3}),
    ("指定历史日", {"date": D1}),
    ("单类别", {"cats": ["沪深300"]}),
    ("多类别", {"cats": ["沪深300", "上证50"]}),
    ("单基金", {"codes": ["510300"]}),
    ("类别+基金交集", {"cats": ["沪深300"], "codes": ["510310"]}),
    ("空交集", {"cats": ["上证50"], "codes": ["510300"]}),
    ("历史日+筛选", {"date": D2, "cats": ["沪深300"]}),
]


def main():
    tmp = tempfile.mkdtemp(prefix="ntt_expected_")
    try:
        path = os.path.join(tmp, "test.db")
        db.init_db(path)
        db.save_etf_daily(D1, ROWS, path=path)
        db.save_etf_daily(D2, ROWS_D2, path=path)
        db.save_etf_daily(D3, ROWS_D3, path=path)
        db.set_meta("last_fetch", "2026-10-27 16:20:00", path=path)

        raw = staticsite.build_static_payload(path=path)
        cases = []
        for name, kw in CASES:
            p = app.build_payload(path=path, **kw)
            p["signal"] = app.signal_label(p["summary"]["total_main_net_yi"])
            cases.append({"name": name, "params": kw, "payload": p})

        json.dump({"raw": raw, "cases": cases}, sys.stdout, ensure_ascii=False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
