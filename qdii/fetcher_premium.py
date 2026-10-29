"""qdii/fetcher_premium.py — 模块G：场内溢价率监控（14:30，A股交易日）。

场外按净值申购、场内按价格交易，同一标的两条渠道的价差（溢价/折价）直接决定
「从哪个口子买更划算」。高溢价往往意味着该基金限购抢手（需求>供给），是定投旁证。

数据源（均已实测可达）：
- 场内实时价：新浪 `hq.sinajs.cn/list=<市场><代码>`（GBK 解码）
- 最新单位净值：东财 `api.fund.eastmoney.com/f10/lsjz`（代理）

溢价率 = (场内价 − 最新单位净值) / 最新单位净值 × 100%
场内价 = 实时价(字段[3]) 若 >0 否则 昨收(字段[2])（盘前/非交易时段为 0）
"""
import os
import sys
import re
import json
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
for p in (BASE, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from common.http import fetch_text, fetch_json
import db
import constants as C


def market_prefix(code):
    """场内代码市场前缀：沪市(5/6/9 开头)->sh，深市(0/1/2/3 开头)->sz。"""
    return "sh" if code[0] in "569" else "sz"


def fetch_a_share_prices(codes):
    """codes: 列表，元素为 'sh513500' / 'sz161125' 形式。"""
    url = C.URL_SINA_A_SHARE.format(codes=",".join(codes))
    txt = fetch_text(url, referer="https://finance.sina.com.cn/",
                     encoding="gbk", use_proxy=None, retries=2)
    res = {}
    for m in re.finditer(r'var hq_str_(\w+)="([^"]*)"', txt):
        sym, body = m.group(1), m.group(2)
        parts = body.split(",")
        if len(parts) < 4:
            continue
        try:
            real = float(parts[3])
        except (ValueError, TypeError):
            real = 0.0
        try:
            prev = float(parts[2])
        except (ValueError, TypeError):
            prev = 0.0
        # 实时价为 0（盘前/停牌/非交易时段）则退用昨收
        res[sym] = real if real > 0 else prev
    return res


def fetch_nav(code):
    url = C.URL_NAV_LATEST.format(code=code)
    obj = fetch_json(url, referer="https://fundf10.eastmoney.com/",
                     use_proxy=None, retries=2)
    data = obj["Data"]["LSJZList"][0]
    return float(data["DWJZ"]), data.get("FSRQ")


def fetch_premium(date: str = None, path: str = C.QDII_DB_PATH):
    """按**场内 ETF 清单**抓取溢价（一只 ETF 一行）。

    ⚠️ 不要改回「遍历场外基金 + online_code」：场外联接基金与其 ETF 不存在唯一映射，
    旧写法让纳指 5 只场外基金全部指向 513100，159941 永远不出现、5 行溢价率完全相同。
    """
    date = date or datetime.date.today().isoformat()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    etfs = C.PREMIUM_ETFS
    codes = [e["code"] for e in etfs]
    market_codes = [market_prefix(c) + c for c in codes]
    prices = fetch_a_share_prices(market_codes)
    conn = db.get_conn(path)
    cnt = 0
    got = []
    for e in etfs:
        code = e["code"]
        sym = market_prefix(code) + code
        market_price = prices.get(sym)
        if market_price is None:
            continue
        try:
            nav, nav_date = fetch_nav(code)
        except Exception as ex:  # noqa: BLE001
            print("[premium] nav failed for %s:" % code, ex)
            continue
        if not nav:
            continue
        premium_pct = (market_price - nav) / nav * 100.0
        db.upsert_row(conn, "qdii_premium",
                      ["date", "code", "online_code", "market_price", "nav",
                       "nav_date", "premium_pct", "source", "fetched_at"],
                      [date, code, code, market_price, nav, nav_date,
                       round(premium_pct, 2), "sina_eastmoney", now],
                      "date,code")
        got.append(code)
        cnt += 1
    # 清理当日残留：旧版本按场外基金代码写入的行（513100 之外的 161125 等）会与新口径混在一起
    if got:
        marks = ",".join("?" for _ in got)
        conn.execute("DELETE FROM qdii_premium WHERE date=? AND code NOT IN (%s)"
                     % marks, [date] + got)
    conn.commit()
    conn.close()
    db.set_meta("last_premium_fetch", now, path)
    return cnt


_PREMIUM_META = {e["code"]: e for e in C.PREMIUM_ETFS}


def get_latest_premium(path: str = C.QDII_DB_PATH):
    conn = db.get_conn(path)
    drow = conn.execute(
        "SELECT date FROM qdii_premium ORDER BY date DESC LIMIT 1").fetchone()
    if not drow:
        conn.close()
        return []
    rows = conn.execute(
        "SELECT * FROM qdii_premium WHERE date=?", (drow["date"],)).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        meta = _PREMIUM_META.get(d["code"], {})
        d["etf_name"] = meta.get("name") or ""
        d["company"] = meta.get("company") or ""
        d["index_name"] = meta.get("index_name") or ""
        d["category"] = meta.get("category") or ""
        out.append(d)
    out.sort(key=lambda x: -(x.get("premium_pct") or 0))
    return out


if __name__ == "__main__":
    db.init_db()
    n = fetch_premium()
    print("fetched premium for %d funds" % n)
    for r in get_latest_premium():
        print("  %s online=%s price=%.3f nav=%.3f premium=%.2f%%" % (
            r["code"], r["online_code"], r["market_price"],
            r["nav"], r["premium_pct"]))
