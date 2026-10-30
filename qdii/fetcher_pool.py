"""qdii/fetcher_pool.py — 基金池初始化（模块A）。

把 constants.FUND_POOL 种子写入 qdii_fund 表，建库并初始化全部表结构。
"""
import os
import sys
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
# BASE（本模块目录）必须排在 ROOT（部署根）之前 —— 否则部署根下存在同名
# 模块时（沙箱里确有旧副本），`import constants/db` 会加载到那一份，
# 表现为「部署了新代码、线上却跑着旧行为」。
for p in (ROOT, BASE):
    if p not in sys.path:
        sys.path.insert(0, p)
if sys.path and sys.path[0] != BASE:
    sys.path.remove(BASE)
    sys.path.insert(0, BASE)

import db
import constants as C


def init_pool(path: str = C.QDII_DB_PATH):
    db.init_db(path)
    db.seed_fund_pool(C.FUND_POOL, path)
    return db.get_fund_pool(path)


if __name__ == "__main__":
    rows = init_pool()
    print("seeded %d funds" % len(rows))
    for r in rows:
        print("  %-7s %s | %s | online=%s" % (r["code"], r["name"],
              r["category"], r["online_code"]))
