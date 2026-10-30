"""qdii/fetcher_limit.py — 模块B：每日申购状态 + 限额追踪。

数据源：天天基金 Fund_JJJZ_Data.aspx?t=8（稳定 JSON，全量）。
字段顺序（实测）：[代码, 简称, 类型, 最新净值, 净值日期, 申购状态,
赎回状态, 下一开放日, 购买起点, 日累计限定金额, 手续费, ...]。
"""
import os
import sys
import re
import json
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

from common.http import fetch_text
import db
import constants as C

# 日累计限定金额 >= 此阈值视为「不限」（接口用 1e11 量级表示不限）
LIMIT_UNLIMITED_THRESHOLD = 1e10


def extract_datas(txt):
    """该接口返回 JS 对象字面量（键无引号，非标准 JSON）。
    直接定位 datas 数组并用括号平衡提取其二维数组（元素均为字符串，合法 JSON）。"""
    i = txt.find("datas:")
    if i < 0:
        return []
    j = txt.find("[", i)
    depth = 0
    k = j
    n = len(txt)
    while k < n:
        ch = txt[k]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                break
        k += 1
    arr = txt[j:k + 1]
    return json.loads(arr)


def normalize_sub_status(s):
    s = (s or "")
    if "暂停" in s:
        return "SUSPEND"
    if "限" in s:  # 限大额 / 限额
        return "LIMIT"
    if "开放" in s:
        return "OPEN"
    return "OPEN"


def fetch_all_limit_status(date: str = None, path: str = C.QDII_DB_PATH):
    date = date or datetime.date.today().isoformat()
    # 实测：该接口返回 UTF-8 字节，若按 GBK 解码会静默产生乱码（GBK 几乎不抛
    # UnicodeDecodeError，不会触发降级），导致「暂停申购」被误判成 OPEN。务必用 utf-8。
    txt = fetch_text(C.URL_LIMIT_ALL, referer="https://fund.eastmoney.com/",
                     encoding="utf-8", use_proxy=None, retries=2)
    datas = extract_datas(txt)
    pool_codes = {f["code"] for f in C.FUND_POOL}
    now = datetime.datetime.now().isoformat(timespec="seconds")
    conn = db.get_conn(path)
    cnt = 0
    for row in datas:
        code = str(row[0])
        if code not in pool_codes:
            continue
        sub_raw = row[5] if len(row) > 5 else ""
        sub_status = normalize_sub_status(sub_raw)
        limit_amount = 0.0
        if len(row) > 9 and row[9] not in (None, ""):
            try:
                limit_amount = float(row[9])
            except (ValueError, TypeError):
                limit_amount = 0.0
        is_limited = 0 if limit_amount >= LIMIT_UNLIMITED_THRESHOLD else 1
        next_open = row[7] if len(row) > 7 else ""
        buy_start = 0.0
        if len(row) > 8 and row[8] not in (None, ""):
            try:
                buy_start = float(row[8])
            except (ValueError, TypeError):
                buy_start = 0.0
        redeem = row[6] if len(row) > 6 else ""
        db.upsert_row(conn, "qdii_limit_status",
                      ["code", "date", "sub_status", "limit_amount", "is_limited",
                       "next_open_date", "buy_start", "redeem_status",
                       "source", "fetched_at"],
                      [code, date, sub_status, limit_amount, is_limited,
                       next_open, buy_start, redeem, "eastmoney_JJJZ", now],
                      "code,date")
        cnt += 1
    conn.commit()
    conn.close()
    db.set_meta("last_limit_fetch", now, path)
    return cnt


def get_latest_limit_status(codes=None, path: str = C.QDII_DB_PATH):
    conn = db.get_conn(path)
    date_row = conn.execute(
        "SELECT date FROM qdii_limit_status ORDER BY date DESC LIMIT 1").fetchone()
    if not date_row:
        conn.close()
        return []
    date = date_row["date"]
    if codes:
        placeholders = ",".join("?" for _ in codes)
        rows = conn.execute(
            "SELECT * FROM qdii_limit_status WHERE date=? AND code IN (%s)"
            % placeholders, [date] + list(codes)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM qdii_limit_status WHERE date=?", (date,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    db.init_db()
    n = fetch_all_limit_status()
    print("fetched limit status for %d funds" % n)
    for r in get_latest_limit_status():
        print("  %s %s limit=%s is_limited=%s next_open=%s" % (
            r["code"], r["sub_status"], r["limit_amount"],
            r["is_limited"], r["next_open_date"]))
