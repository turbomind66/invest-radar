"""qdii/fetcher_signal.py — 模块D：每日美股信号（14:30，美股交易日）。

⚠️ 时区事实：14:30 北京时间 = 美东约 01:30–02:30，美股已收盘，抓到的是
「上一交易日收盘（非实时）」；美股期货（CME Globex 近24h）为实时。

数据源（国内友好、实测可达，替代被封的雅虎）：
- 期货实时：新浪 `hf_ES`(标普500指数期货) / `hf_NQ`(纳指期货，标的为 CME 纳指100期货)
- 现货昨收（非实时）：新浪 `gb_inx`(标普500) / 腾讯 `usIXIC`(纳斯达克综合指数＝口语「纳指」)
- **美债10年期收益率（实时）**：CNBC 报价服务（腾讯 `usTNX` 已失效，2026-10-29 实测不返回）
- **VIX（日频收盘，参与避险判断 + 面板展示）**：CBOE 官方日收盘 CSV
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

from common.http import fetch_text
import db
import constants as C

# 新浪期货代码 -> (symbol, name)
# hf_NQ 的官方名称是「纳斯达克指数期货」，标的即 CME 纳指100期货（全球唯一的纳指期货品种），
# 页面按用户口径统一叫「纳指期货」。
FUTURES_MAP = {
    "hf_ES": ("SPX_FUT", "标普500期货"),
    "hf_NQ": ("NDX_FUT", "纳指期货"),
    # 日经225 期货（东京）：欧洲/亚太类别的实时信号源之一
    "hf_NK": ("NK", "日经225期货"),
}
# 新浪美股指数（标普500 现货，上一交易日收盘）
SINA_INDEX_URL = "https://hq.sinajs.cn/list=gb_inx,gb_ixic"
# 腾讯财经美股代码 -> (symbol, name, is_realtime)
# usIXIC = 纳斯达克综合指数（口语「纳指」）；usNDX = 纳斯达克100，两者不同：
# 综合指数含全部纳斯达克上市股票（约 3300 只），纳指100 只含 100 只非金融大盘股。
# ⚠️ 腾讯 `usTNX`（美债10Y）已不再返回数据（2026-10-29 实测请求 3 个代码只回 2 条），
#    美债改用 CNBC 实时报价，见 fetch_cnbc_ust10y()。
# usEWU = iShares MSCI 英国 ETF —— 富时100 无公开行情源，用它做**代理**（页面必须标注）。
TENCENT_URL = "https://qt.gtimg.cn/q=usIXIC,usEWU"
TENCENT_SYMBOLS = {
    "usIXIC": ("IXIC", "纳指现货", 0),
    "usEWU": ("FTSE_UK", "英国富时100（代理）", 1),
}
# 境内场内 ETF（实时）：欧洲/亚太里**取不到海外指数现货**的类别，用对应场内 ETF 实时价代理。
# 新浪 A 股行情字段：parts[0]=名称 parts[2]=昨收 parts[3]=现价 parts[30]=日期 parts[31]=时间
SINA_A_ETF = {
    "sh513030": ("DAX_ETF", "德国DAX（513030）"),
    "sh513080": ("CAC40_ETF", "法国CAC40（513080）"),
}
SINA_A_ETF_URL = "https://hq.sinajs.cn/list=" + ",".join(SINA_A_ETF)
# symbol → 展示名（写库用）
SINA_A_ETF_NAME = {v[0]: v[1] for v in SINA_A_ETF.values()}
# VIX：腾讯 usVIX 的数值自 2026-09-15 起就不再更新（实测长期停在 21.67），
# 拿它当避险开关等于开关永远不动。改用 CBOE 官方日收盘 CSV（权威、日频、T+1）。
CBOE_VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
# 美债10年期收益率（实时）：CNBC 报价服务，实测 last_time 就是当前时刻、curmktstatus=REG_MKT。
# 备选（日频 T+1、权威）：美国财政部 daily_treasury_yield_curve XML。
CNBC_US10Y_URL = ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
                  "?symbols=US10Y&requestMethod=itv&noform=1&partnerId=2&fund=1"
                  "&exthrs=1&output=json&events=1")


def fetch_sina_futures():
    """新浪环球期货（hf_*）实时行情。

    ⚠️ 字段位置（2026-10-29 实测 15 段，勿凭印象改）：
        [0] 现价  [2] 买价  [3] 卖价  [4] 最高  [5] 最低
        [6] 时间  [7] **昨收**  [8] 开盘  [12] 日期  [13] **名称**  [14] 占位 0
    历史 bug：曾把 parts[5] 当价格（实际是当日最低价）、把 parts[-1] 当名称
    （实际是占位 "0"），导致页面出现「名称 0、价格恒为最低价、涨跌 0.00%」。

    返回 {hf_code: (名称, 现价, 昨收, 日期时间)}
    """
    txt = fetch_text(C.URL_SINA_FUTURE,
                     referer="https://finance.sina.com.cn/",
                     encoding="gbk", use_proxy=None, retries=2)
    res = {}
    for m in re.finditer(r'var hq_str_(hf_\w+)="([^"]*)"', txt):
        sym, body = m.group(1), m.group(2)
        parts = body.split(",")
        if len(parts) < 14:
            continue

        def f(i):
            try:
                return float(parts[i])
            except (ValueError, TypeError, IndexError):
                return None

        price, prev = f(0), f(7)
        if not price:
            continue
        name = (parts[13] or "").strip()
        ts = "%s %s" % ((parts[12] or "").strip(), (parts[6] or "").strip())
        res[sym] = (name, price, prev, ts.strip())
    return res


def fetch_tencent_us(codes=None):
    """腾讯财经美股行情（~ 分隔）。codes 形如 'usVIX'，默认取 TENCENT_SYMBOLS 那批。"""
    url = TENCENT_URL if not codes else ("https://qt.gtimg.cn/q=" + codes)
    txt = fetch_text(url, use_proxy=None, retries=2)
    res = {}
    for m in re.finditer(r'v_(\w+)="([^"]*)"', txt):
        sym, body = m.group(1), m.group(2)
        parts = body.split("~")
        if len(parts) < 5:
            continue
        try:
            price = float(parts[3])
            prev = float(parts[4])
        except (ValueError, TypeError):
            continue
        chg = (price / prev - 1) * 100 if prev else 0.0
        res[sym] = (price, chg)
    return res


def fetch_sina_us_index():
    """新浪美股指数（标普500 gb_inx / 纳指综合 gb_ixic），上一交易日收盘。"""
    txt = fetch_text(SINA_INDEX_URL, referer="https://finance.sina.com.cn/",
                     encoding="gbk", use_proxy=None, retries=2)
    res = {}
    for m in re.finditer(r'var hq_str_(gb_\w+)="([^"]*)"', txt):
        sym, body = m.group(1), m.group(2)
        parts = body.split(",")
        if len(parts) < 6:
            continue
        try:
            price = float(parts[1])
            prev = float(parts[5])
        except (ValueError, TypeError):
            continue
        chg = (price / prev - 1) * 100 if prev else 0.0
        res[sym] = (price, chg)
    return res


def fetch_cboe_vix():
    """CBOE 官方 VIX 日收盘 CSV → (收盘价, 涨跌幅%, 日期ISO)。

    取最后一条完整记录；CSV 首行是表头 `DATE,OPEN,HIGH,LOW,CLOSE`，日期格式 MM/DD/YYYY。
    """
    txt = fetch_text(CBOE_VIX_URL, timeout=40, retries=2)
    lines = [ln.strip() for ln in txt.strip().split("\n") if ln.strip()]
    rows = [ln.split(",") for ln in lines[1:] if len(ln.split(",")) >= 5]
    if len(rows) < 2:
        raise ValueError("CBOE VIX csv 行数不足")
    last, prev = rows[-1], rows[-2]
    close = float(last[4])
    prev_close = float(prev[4])
    chg = (close / prev_close - 1) * 100 if prev_close else 0.0
    m, d, y = last[0].split("/")
    return close, round(chg, 4), "%s-%s-%s" % (y, m, d)


def fetch_cnbc_ust10y():
    """CNBC 美债 10 年期收益率（**实时**），返回 (收益率%, 变动bp, 相对变动%, 美东时间)。

    字段（2026-10-29 实测）：
        last="4.995%"  previous_day_closing="5.045%"  change="-0.05"  change_pct="-0.9911%"
        last_time="2026-10-29T03:51:40.000-0400"（美东）  curmktstatus="REG_MKT"
    ⚠️ 收益率要用 **bp** 表达涨跌：change 本身就是「收益率百分点」差，×100 即 bp
       （-0.05 → -5bp，正好 5.045→4.995）。change_pct 是相对变化率，对收益率没有
       经济含义（4%→4.04% 只算 +1%，实际是 +4bp），仅作参考保留。
    """
    txt = fetch_text(CNBC_US10Y_URL, timeout=30, retries=2)
    obj = json.loads(txt)
    quotes = ((obj.get("FormattedQuoteResult") or {}).get("FormattedQuote") or [])
    if not quotes:
        raise ValueError("CNBC US10Y 无报价")
    q = quotes[0]
    last = float(str(q["last"]).rstrip("%").replace(",", ""))
    prev = float(str(q.get("previous_day_closing") or "0").rstrip("%").replace(",", ""))
    bp = round((last - prev) * 100, 1) if prev else None
    chg_pct = round((last / prev - 1) * 100, 4) if prev else None
    as_of = (q.get("last_time") or "").replace("T", " ")[:19]
    return last, bp, chg_pct, ("美东 " + as_of if as_of else "")


def fetch_sina_a_etf():
    """新浪 A 股场内 ETF 实时价 → {symbol: (名称, 现价, 昨收, 时间)}。

    用途：DAX / 法国CAC40 / 纳斯达克科技 这几类**取不到海外指数现货**
    （东财 push2 在本网络恒断、腾讯/新浪也没有对应指数代码），
    用境内对应 ETF 的实时价代理 —— 反映的是国内投资者实际能买到的价格。
    代理口径会在页面类型标签里写明（场内ETF · 实时），不冒充指数。
    """
    txt = fetch_text(SINA_A_ETF_URL, referer="https://finance.sina.com.cn",
                     encoding="gbk", use_proxy=None, retries=2)
    res = {}
    for m in re.finditer(r'var hq_str_(\w+)="([^"]*)"', txt):
        code, body = m.group(1), m.group(2)
        if code not in SINA_A_ETF or not body:
            continue
        parts = body.split(",")
        if len(parts) < 4:
            continue
        try:
            price = float(parts[3])
            prev = float(parts[2])
        except (ValueError, TypeError):
            continue
        ts = ""
        if len(parts) > 31:
            ts = "%s %s" % (parts[30], parts[31])
        res[SINA_A_ETF[code][0]] = (SINA_A_ETF[code][1], price, prev, ts.strip())
    return res


def fetch_us_signal(date: str = None, path: str = C.QDII_DB_PATH):
    date = date or datetime.date.today().isoformat()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = []
    # 期货（实时）
    try:
        futures = fetch_sina_futures()
        for hf_code, (symbol, name) in FUTURES_MAP.items():
            got = futures.get(hf_code)
            if not got:
                continue
            _n, price, prev, ts = got
            # 涨跌幅按「现价 vs 昨收」真实计算（此前写死 0.00%，等于没有信息）
            chg = round((price / prev - 1) * 100, 4) if prev else None
            rows.append((date, symbol, name, price, chg, 1, ts or now, now, None))
    except Exception as e:  # noqa: BLE001
        print("[signal] futures failed:", e)
    # 腾讯（非实时：纳指100现货/VIX/美债）
    try:
        tencent = fetch_tencent_us()
        for tcode, (symbol, name, is_rt) in TENCENT_SYMBOLS.items():
            if tcode in tencent:
                price, chg = tencent[tcode]
                rows.append((date, symbol, name, price, chg, is_rt, now, now, None))
    except Exception as e:  # noqa: BLE001
        print("[signal] tencent failed:", e)
    # 美债10年期收益率（实时，CNBC）
    try:
        ust, ust_bp, ust_pct, ust_ts = fetch_cnbc_ust10y()
        rows.append((date, "UST10Y", "美债10Y收益率", ust, ust_pct, 1,
                     ust_ts or now, now, ust_bp))
    except Exception as e:  # noqa: BLE001
        print("[signal] cnbc ust10y failed:", e)
    # VIX（避险开关 + 面板展示）：CBOE 官方 CSV 优先，失败再退回腾讯（可能停在旧值）。
    # 美债10Y 是「原因」（贴现率上行压制估值），VIX 是「结果」（恐慌程度已到多少），
    # 两者维度不同、并列参与避险判断（见 strategy.risk_off_assess），缺一个都不完整。
    try:
        vix_close, vix_chg, vix_date = fetch_cboe_vix()
        rows.append((date, "VIX", "VIX波动率", vix_close, vix_chg, 0, vix_date, now, None))
    except Exception as e:  # noqa: BLE001
        print("[signal] cboe vix failed, fallback tencent:", e)
        try:
            t2 = fetch_tencent_us("usVIX")
            if "usVIX" in t2:
                price, chg = t2["usVIX"]
                rows.append((date, "VIX", "VIX波动率", price, chg, 0, now, now, None))
        except Exception as e2:  # noqa: BLE001
            print("[signal] vix unavailable:", e2)
    # 新浪（非实时：标普500 现货）
    try:
        sina_idx = fetch_sina_us_index()
        if "gb_inx" in sina_idx:
            price, chg = sina_idx["gb_inx"]
            rows.append((date, "SPX", "标普500现货", price, chg, 0, now, now, None))
    except Exception as e:  # noqa: BLE001
        print("[signal] sina index failed:", e)
    # 境内场内 ETF（实时）：DAX / 法国CAC40 / 纳斯达克科技的代理信号
    try:
        a_etf = fetch_sina_a_etf()
        for symbol, (_n, price, prev, ts) in a_etf.items():
            chg = round((price / prev - 1) * 100, 4) if prev else None
            rows.append((date, symbol, SINA_A_ETF_NAME[symbol], price, chg, 1,
                         ts or now, now, None))
    except Exception as e:  # noqa: BLE001
        print("[signal] sina a-etf failed:", e)
    conn = db.get_conn(path)
    # 同日重跑先清当日旧行：symbol 口径变过（NDX → IXIC、usTNX → CNBC UST10Y），
    # 不清理会让面板同时出现新旧两条。
    conn.execute("DELETE FROM qdii_us_signal WHERE date=?", (date,))
    for r in rows:
        db.upsert_row(conn, "qdii_us_signal",
                      ["date", "symbol", "name", "last_price", "change_pct",
                       "is_realtime", "as_of", "fetched_at", "change_bp"],
                      list(r), "date,symbol")
    conn.commit()
    conn.close()
    db.set_meta("last_signal_fetch", now, path)
    return len(rows)


def get_latest_us_signal(path: str = C.QDII_DB_PATH):
    conn = db.get_conn(path)
    drow = conn.execute(
        "SELECT date FROM qdii_us_signal ORDER BY date DESC LIMIT 1").fetchone()
    if not drow:
        conn.close()
        return []
    # 显式排序：按 constants.MARKET_SIGNALS 的顺序（美股 → 科技 → 亚太 → 欧洲 → 利率）。
    # 别依赖插入顺序——重跑/补数时顺序会变。
    order = ["IXIC", "NDX_FUT", "SPX", "SPX_FUT", "NK",
             "DAX_ETF", "CAC40_ETF", "FTSE_UK", "UST10Y", "VIX"]
    cases = " ".join("WHEN '%s' THEN %d" % (s, i + 1) for i, s in enumerate(order))
    rows = conn.execute(
        "SELECT * FROM qdii_us_signal WHERE date=? ORDER BY CASE symbol %s ELSE 99 END"
        % cases, (drow["date"],)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    db.init_db()
    n = fetch_us_signal()
    print("fetched %d us signals" % n)
    for r in get_latest_us_signal():
        bp = r.get("change_bp")
        print("  %-8s %-14s price=%.3f chg=%.2f%%%s rt=%s as_of=%s" % (
            r["symbol"], r["name"], r["last_price"], r["change_pct"],
            (" (%+.1fbp)" % bp) if bp is not None else "",
            r["is_realtime"], r["as_of"]))
