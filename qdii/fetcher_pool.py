"""qdii/fetcher_pool.py — 基金池初始化（模块A）。

把 constants.FUND_POOL 种子写入 qdii_fund 表，建库并初始化全部表结构。
"""
import os
import sys
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
for p in (BASE, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

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
