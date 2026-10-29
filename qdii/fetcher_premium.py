"""qdii/fetcher_premium.py — 模块G：场内溢价率监控（14:30，A股交易日）。

场外按净值申购、场内按价格交易，同一标的两条渠道的价差（溢价/折价）直接决定
「从哪个口子买更划算」。高溢价往往意味着该基金限购抢手（需求>供给），是定投旁证。

数据源（均已实测可达）：
- 场内实时价：新浪 `hq.sinajs.cn/list=<市场><代码>`（GBK 解码）
- 最新单位净值：东财 `api.fund.eastmoney.com/f10/lsjz`（代理）

溢价率 = (场内价 − 最新单位净值) / 最新单位净值 × 100%
折算溢价 = (场内价 − 折算净值) / 折算净值 × 100%
           折算净值 = 净值 × 该指数从「净值日期」到「最新收盘日」的累计涨幅
场内价 = 实时价(字段[3]) 若 >0 否则 昨收(字段[2])（盘前/非交易时段为 0）

为什么需要折算：QDII 净值 T+1 披露，净值日期常比场内价晚 1~2 个交易日，
直接相减会把这几天的指数涨跌算成溢价（纳指类实测虚高十几个点）。折算溢价才是
「净值更新到今天后还剩多少溢价」。
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
import tracking_error


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


_FUT_BENCH_CACHE = {}
URL_SINA_FUT_KLINE = ("https://stock.finance.sina.com.cn/futures/api/jsonp.php/"
                      "var%20_=/GlobalFuturesService.getGlobalFuturesDailyKLine?symbol=")


def _fetch_sina_fut_daily(symbol):
    """新浪外盘期货日线（NK=日经225期货、NQ=纳指100期货…），升序 [(日期, 收盘)]。

    用途：日经225 / 德国DAX 的现货日线（东财 `100.N225` / `100.GDAXI`）在本网络
    恒被断（RemoteDisconnected），但新浪外盘期货日线可达。日经用 NK 期货近似现货
    （期货 24h 连续、含次日盘前，折算误差 <0.5%，对 5% 量级的溢价够用）。
    """
    if symbol in _FUT_BENCH_CACHE:
        return _FUT_BENCH_CACHE[symbol]
    txt = fetch_text(URL_SINA_FUT_KLINE + symbol, encoding="utf-8",
                     use_proxy=None, retries=2,
                     referer="https://finance.sina.com.cn/")
    out = []
    for r in tracking_error._parse_sina_jsonp(txt):
        d, c = r.get("date"), r.get("close")
        if not d or not c:
            continue
        try:
            out.append((d, float(c)))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x[0])
    _FUT_BENCH_CACHE[symbol] = out
    return out


def index_cum_ratio(bench, nav_date):
    """指数从 nav_date 收盘 → 最新收盘的累计涨幅比例，用于把滞后净值折算到今天。

    返回 (ratio, asof)；拿不到指数日线时返回 (None, None)，调用方按「不折算」处理。
    ratio == 1.0 表示净值日期已等于指数最新收盘日（无需折算）。

    口径说明：QDII 净值是 T 日收盘估值、T+1 才披露，所以净值日期常比场内价晚 1~2 个
    交易日。直接 (价 − 净值) 会把这几天的指数涨跌记成溢价，实测能把纳指类溢价虚高十几
    个点。折算 = 净值 × 指数区间涨幅，得到「若净值已更新到今天」的估算值。
    ⚠️ 未计入汇率日内波动（美元资产对人民币，纳指/标普类同向同幅，影响通常 <0.2%）。
    """
    if not bench or not nav_date:
        return None, None
    try:
        if bench[0] == "sina_fut":
            series = _fetch_sina_fut_daily(bench[1])
        else:
            series = tracking_error.fetch_benchmark_daily(*bench)
    except Exception as ex:  # noqa: BLE001
        print("[premium] bench failed for %s:" % (bench,), ex)
        return None, None
    if not series:
        return None, None
    win = [(d, c) for d, c in series if d >= nav_date and c]
    if not win:
        return None, None
    base = win[0][1]
    asof, last = win[-1]
    if not base or base <= 0:
        return None, None
    return last / base, asof


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
        # 折算溢价：把滞后净值按指数涨幅折算到最新收盘日，剔除 T+1 滞后造成的虚高
        ratio, bench_asof = index_cum_ratio(e.get("bench"), nav_date)
        est_nav = est_premium_pct = None
        if ratio is not None:
            est_nav = round(nav * ratio, 4)
            if est_nav > 0:
                est_premium_pct = round(
                    (market_price - est_nav) / est_nav * 100.0, 2)
        bench_label = "%s:%s" % e["bench"] if e.get("bench") else None
        db.upsert_row(conn, "qdii_premium",
                      ["date", "code", "online_code", "market_price", "nav",
                       "nav_date", "premium_pct", "source", "fetched_at",
                       "est_nav", "est_premium_pct", "bench_label", "bench_asof"],
                      [date, code, code, market_price, nav, nav_date,
                       round(premium_pct, 2), "sina_eastmoney", now,
                       est_nav, est_premium_pct, bench_label, bench_asof],
                      "date,code")
        got.append(code)
        cnt += 1
    # 清理当日残留：旧版本按场外基金代码写入的行（513100 之外的 161125 等）会与新口径混在一起
    if got:
        marks = ",".join("?" for _ in got)
        conn.execute("DELETE FROM qdii_premium WHERE date=? AND code NOT IN (%s)"
                     % marks, [date] + got)
    # 清理**当日不在溢价清单里**的历史行：清单会随大类增删变化
    # （2026-10-29 移除纳斯达克生物科技/科技后，513290、159509 就不该再出现），
    # 只 upsert 不删除的话，页面会多出两只已不在清单的 ETF。
    keep = [e["code"] for e in etfs]
    if keep:
        marks = ",".join("?" for _ in keep)
        conn.execute("DELETE FROM qdii_premium WHERE date=? AND code NOT IN (%s)" % marks,
                     [date] + keep)
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
        print("  %s online=%s price=%.3f nav=%.3f(%s) premium=%.2f%% "
              "est_nav=%s est_premium=%s" % (
                  r["code"], r["online_code"], r["market_price"],
                  r["nav"], r["nav_date"], r["premium_pct"],
                  "%.4f" % r["est_nav"] if r.get("est_nav") else "—",
                  "%.2f%%" % r["est_premium_pct"] if r.get("est_premium_pct") is not None else "—"))
