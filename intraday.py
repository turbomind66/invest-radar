"""疑似国家队护盘 / 疑似国家队抛售压盘 —— 独立模块。

与主流程（二级资金流 / 四档 / 一级申赎）解耦：
  - 分时数据来自腾讯分时接口（分钟级价量），与主流程的数据源完全独立；
  - 每交易日归档一次，积累出自己的分时历史（上游只给近 5 日，久了就抓不到了）；
  - 判定采用"四条件联合"：放量 + 全池共振 + 价格方向 + 大单资金流印证，
    缺任何一条都只记为「疑似」，不直接判定为国家队行为。

信号方向：
  support  护盘：放量但价格不跌（有人接货托价），收盘后需大单净流入为正才升级为确认
  pressure 压盘：放量且价格跌破当日均价（抛压被放量放大），收盘后需大单净流出才升级为确认

告警通道（邮件 / 飞书等）在本模块留了配置位 notify_*，当前不启用推送。
"""
import datetime
import json
import ssl
import time
import urllib.request

import db
from fetcher import WATCHLIST

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# 判定阈值（先用保守值，等分时历史积累后再按真实分布校准）
RATIO_TH = 3.0        # 单只：分钟成交额 ≥ 自身当日分钟均值的 N 倍
COVER_TH = 2.0        # 全池：同一分钟各基金相对自身日均的倍数均值
BREADTH_MIN = 5       # 同时放大的基金只数下限（"共振"而非单只异动）
DROP_TH = -0.5        # 压盘：价格相对当日均价跌破该百分比
NEAR_VWAP = -0.2      # 护盘：价格相对当日均价不低于该百分比（没被砸下去）

# 告警通道预留（暂不启用；后续接邮件/飞书时只改这里）
# 东财 1 分钟 K 备选源可用性（None=未探测 / True=可用 / False=本次运行不可用）
_EM_MINUTE_OK = None

NOTIFY = {
    "enabled": False,
    "channel": "",        # "email" / "feishu" / "wecom"
    "targets": [],        # 收件人或 webhook
}


def _get(url, timeout=20, retries=2, proxies=(None, "http://127.0.0.1:10808")):
    """直连优先，失败再走 10808 代理。"""
    last = None
    for attempt in range(retries):
        for px in proxies:
            try:
                op = urllib.request.build_opener(
                    urllib.request.HTTPSHandler(context=CTX),
                    urllib.request.ProxyHandler({"http": px, "https": px} if px else {}))
                req = urllib.request.Request(url, headers=HDR)
                with op.open(req, timeout=timeout) as r:
                    return r.read().decode("utf-8", "ignore")
            except Exception as e:
                last = e
        time.sleep(0.4 * (attempt + 1))
    raise last


def _tencent_symbol(code: str) -> str:
    return ("sh" if code[0] in "56" else "sz") + code


def _norm_date(s):
    """把 20261028 / 2026-10-28 统一成 2026-10-28。"""
    s = str(s or "").strip()
    if len(s) == 8 and s.isdigit():
        return "%s-%s-%s" % (s[:4], s[4:6], s[6:8])
    return s


def fetch_minute(code: str):
    """取某只 ETF 最新分时：返回 (日期 'YYYY-MM-DD', [(HHMM, price, 分钟增量手, 分钟增量元)])。

    腾讯给的是**累计值**，必须差分才是每分钟增量（踩过的坑）。
    末尾 15:00 之后是集合竞价占位行（累计值不再变化），差分后自然为 0。
    数据自带 date 字段 → 归档日期精确归属，不会把上一交易日的数据错标成今天。
    """
    sym = _tencent_symbol(code)
    txt = _get("https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=%s" % sym)
    d = json.loads(txt)
    node = (d.get("data") or {}).get(sym) or {}
    dat = node.get("data") or {}
    keys = [k for k in dat if k != "date"]
    if not keys:
        return None, []
    raw = []
    for line in dat[keys[0]]:
        p = line.split()
        if len(p) >= 4:
            try:
                raw.append((p[0], float(p[1]), int(p[2]), float(p[3])))
            except ValueError:
                pass
    out, pv, pa = [], 0.0, 0.0
    for t, pr, cv, ca in raw:
        out.append((t, pr, max(cv - pv, 0), max(ca - pa, 0)))
        pv, pa = cv, ca
    return _norm_date(dat.get("date")), out


def fetch_em_minute_kline(code: str, days: int = 200):
    """备选源：东财 1 分钟 K 线 → {date: [(HHMM, price, vol手, amt元)]}。

    上游该路径当前常被断（连接被重置），一旦恢复即可回补**比 5 日窗口更早**的分时。
    失败返回空字典，不影响主流程。
    """
    sid = _secid_of(code)
    beg = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y%m%d")
    end = datetime.date.today().strftime("%Y%m%d")
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s"
           "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57"
           "&klt=1&fqt=1&beg=%s&end=%s" % (sid, beg, end))
    global _EM_MINUTE_OK
    if _EM_MINUTE_OK is False:
        return {}
    try:
        # 短超时、只试直连：这条路径常被断，别为每只基金白等
        txt = _get(url, timeout=8, retries=1, proxies=(None,))
        d = json.loads(txt)
        _EM_MINUTE_OK = True
    except Exception:
        _EM_MINUTE_OK = False
        return {}
    out = {}
    for ln in (d.get("data") or {}).get("klines") or []:
        p = ln.split(",")
        if len(p) < 7 or len(p[0]) < 16:
            continue
        try:
            out.setdefault(p[0][:10], []).append(
                (p[0][11:13] + p[0][14:16], float(p[2]), float(p[5]), float(p[6])))
        except ValueError:
            continue
    return out


def fetch_minute_days(code: str, days: int = 5):
    """取近 N 个交易日的分时，返回 {date(YYYY-MM-DD): [(HHMM, price, 累计手, 累计元)]}。

    腾讯 day 接口结构：data[code].data = [{date:"20261028", data:[...267 行...], prec}, ...]
    这里返回的是**累计值**，调用方需自行差分；历史分时仅此 5 天，
    因此每天必须归档，否则窗口一滑过就再也拿不回来。
    """
    sym = _tencent_symbol(code)
    txt = _get("https://web.ifzq.gtimg.cn/appstock/app/day/query?code=%s" % sym)
    d = json.loads(txt)
    items = ((d.get("data") or {}).get(sym) or {}).get("data") or []
    out = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        ds = str(item.get("date") or "")
        if len(ds) != 8:
            continue
        ds_iso = "%s-%s-%s" % (ds[:4], ds[4:6], ds[6:8])
        rows = []
        for line in item.get("data") or []:
            p = line.split()
            if len(p) >= 4:
                try:
                    rows.append((p[0], float(p[1]), int(p[2]), float(p[3])))
                except ValueError:
                    pass
        if rows:
            out[ds_iso] = rows
    return out


def _diff_rows(cum_rows):
    """累计值 → 每分钟增量。"""
    out, pv, pa = [], 0.0, 0.0
    for t, pr, cv, ca in cum_rows:
        out.append((t, pr, max(cv - pv, 0), max(ca - pa, 0)))
        pv, pa = cv, ca
    return out


def archive(date: str, path: str = db.DB_PATH, days: int = 5, backup: bool = False):
    """抓分时并归档到 intraday_minute。

    三个来源，日期归属一律以接口自带日期为准（绝不靠猜）：
      1) day 接口（近 N 个交易日，日期明确）→ 主力归档
      2) minute 接口（最新分时，自带 date 字段）→ 精确归属到它自己的日期
      3) 东财 1 分钟 K（备选源，上游恢复后可回补更早日期）
    返回 (归档分钟行数, 当日覆盖基金数, 覆盖日期集合)。
    """
    per_date = {}
    funds, dates_seen = 0, set()
    for code, name, cat in WATCHLIST:
        # 1) day 接口：日期明确
        try:
            dmap = fetch_minute_days(code, days)
        except Exception:
            dmap = {}
        for ds, cum in dmap.items():
            for t, price, vol, amt in _diff_rows(cum):
                per_date.setdefault(ds, []).append({
                    "code": code, "name": name, "cat": cat, "minute": t,
                    "price": price, "vol": vol, "amount": amt})
        # 2) minute 接口：自带日期，精确归属
        try:
            ds_m, mins = fetch_minute(code)
        except Exception:
            ds_m, mins = None, []
        if mins and ds_m:
            for t, price, vol, amt in mins:
                per_date.setdefault(ds_m, []).append({
                    "code": code, "name": name, "cat": cat, "minute": t,
                    "price": price, "vol": vol, "amount": amt})
        # 3) 备选源：东财 1 分钟 K（只补上面没覆盖到的日期）
        if backup:
            try:
                bmap = fetch_em_minute_kline(code)
                for ds, rows in bmap.items():
                    if ds in dmap or ds in per_date:
                        continue
                    for t, price, vol, amt in rows:
                        per_date.setdefault(ds, []).append({
                            "code": code, "name": name, "cat": cat, "minute": t,
                            "price": price, "vol": vol, "amount": amt})
            except Exception:
                pass
        time.sleep(0.05)
    n = 0
    for ds, rows in per_date.items():
        db.save_intraday_rows(ds, rows, path)
        n += len(rows)
        dates_seen.add(ds)
    funds = len({r["code"] for r in per_date.get(date, [])})
    return n, funds, sorted(dates_seen)


def gap_dates(path: str = db.DB_PATH):
    """分时归档的日期缺口（已无法回补的部分）。

    以**二级资金流的交易日历**为参照（那里的日期都是真实交易日，节假日不会被误报）。
    """
    have = set(db.get_intraday_dates(path))
    if not have:
        return []
    lo = min(have)
    cal = [d for d in db.get_dates(path) if d >= lo]
    return [d for d in cal if d not in have]


def detect(date: str, path: str = db.DB_PATH):
    """四条件联合判定，输出护盘/压盘信号并入库。

    条件：
      ① 单只放量：分钟成交额 ≥ 自身当日分钟均值 × RATIO_TH
      ② 全池共振：同一分钟至少 BREADTH_MIN 只同时放大，且全池倍数均值 ≥ COVER_TH
      ③ 价格方向：护盘=价格不跌破当日均价；压盘=价格明显跌破当日均价
      ④ 资金流印证（仅收盘后可得）：护盘需大单净流入为正，压盘需大单净流出为负
         —— 盘中只有前三条件，信号级别为 suspect
    """
    data = db.get_intraday_by_date(date, path=path)
    if not data:
        return []
    # 每只基金：当日分钟均量、当日 VWAP
    stats = {}
    for code, rows in data.items():
        core = [r for r in rows if r["minute"] not in ("1500", "1530")]
        amts = [r["amount"] or 0 for r in core]
        avg = sum(amts) / len(amts) if amts else 0
        tot_amt = sum(r["amount"] or 0 for r in rows)
        tot_vol = sum(r["vol"] or 0 for r in rows) * 100   # 腾讯 vol 单位为手
        vwap = (tot_amt / tot_vol) if tot_vol else None
        stats[code] = {"avg": avg, "vwap": vwap,
                       "name": rows[0].get("name"), "cat": rows[0].get("cat")}
    # 逐分钟：各基金倍数 + 价格相对均价偏离
    by_minute = {}
    for code, rows in data.items():
        for r in rows:
            st = stats[code]
            r["_ratio"] = ((r["amount"] or 0) / st["avg"]) if st["avg"] else 0
            if st["vwap"]:
                r["_dev"] = (r["price"] - st["vwap"]) / st["vwap"] * 100
            else:
                r["_dev"] = None
            by_minute.setdefault(r["minute"], []).append((code, r))
    # 当日四档（大单+超大单净额）：收盘后才有，用于信号升级
    flow = {}
    for e in db.get_etf_by_date(date, path=path):
        big = (e.get("big_net") or 0) + (e.get("huge_net") or 0)
        flow[e["code"]] = big
    signals = []
    for minute, items in by_minute.items():
        ratios = [r["_ratio"] for _, r in items]
        cover = sum(ratios) / len(ratios) if ratios else 0
        if cover < COVER_TH:
            continue
        breadth = sum(1 for x in ratios if x >= COVER_TH)
        if breadth < BREADTH_MIN:
            continue
        for code, r in items:
            if r["_ratio"] < RATIO_TH:
                continue
            dev = r["_dev"]
            if dev is None:
                continue
            if dev <= DROP_TH:
                kind = "pressure"
            elif dev >= NEAR_VWAP:
                kind = "support"
            else:
                continue
            big = flow.get(code)
            if big is None:
                level = "suspect"
            elif (kind == "support" and big > 0) or (kind == "pressure" and big < 0):
                level = "confirmed"
            else:
                level = "suspect"
            st = stats[code]
            signals.append({
                "code": code, "name": st["name"], "cat": st["cat"], "minute": minute,
                "kind": kind, "amount": r["amount"], "ratio": round(r["_ratio"], 2),
                "price": r["price"], "price_chg": round(dev, 3), "cover": round(cover, 2),
                "level": level,
                "note": ("大额买入承接" if kind == "support" else "放量跌破均价")
                        + ("，大单净额印证" if level == "confirmed" else "，待资金流印证"),
            })
    db.save_intraday_signals(date, signals, path)
    return signals


def _agg_points(rows, avg, window: int = 5):
    """分钟序列 → 5 分钟聚合点（与事件窗口同粒度，体积降 80%）。

    倍数口径不变：5 分钟额 ÷ (5 × 分钟均) = 原分钟额 ÷ 分钟均。
    """
    buckets = {}
    for r in rows:
        m = int(r["minute"][:2]) * 60 + int(r["minute"][2:])
        buckets.setdefault((m // window) * window, []).append(r)
    out = []
    for b in sorted(buckets):
        items = buckets[b]
        amt = sum(x["amount"] or 0 for x in items)
        out.append({
            "m": "%02d%02d" % (b // 60, b % 60),
            "p": items[-1]["price"],
            "a": amt,
            "r": round(amt / (avg * len(items)), 2) if avg else 0,
        })
    return out


def _events(signals, window: int = 5, min_funds: int = 3):
    """把逐条信号聚合成"事件"：同一 5 分钟窗口内多只基金的放量算一次事件。

    逐条信号上百条没法看，聚合后一眼能看出"几点几分、几只 ETF 一起放量、多少钱"。
    """
    buckets = {}
    for s in signals:
        m = int(s["minute"][:2]) * 60 + int(s["minute"][2:])
        buckets.setdefault((m // window) * window, []).append(s)
    out = []
    for b, items in sorted(buckets.items()):
        funds = {x["code"] for x in items}
        if len(funds) < min_funds:
            continue
        sup = [x for x in items if x["kind"] == "support"]
        pre = [x for x in items if x["kind"] == "pressure"]
        out.append({
            "window": "%02d%02d" % (b // 60, b % 60),
            "end": "%02d%02d" % ((b + window) // 60, (b + window) % 60),
            "segment": "开盘" if b <= 570 else ("尾盘" if b >= 840 else "盘中"),
            "funds": len(funds),
            "support": len(sup),
            "pressure": len(pre),
            "amount": sum(x["amount"] or 0 for x in items),
            "max_ratio": max((x["ratio"] or 0) for x in items),
            "max_cover": max((x["cover"] or 0) for x in items),
            "confirmed": sum(1 for x in items if x["level"] == "confirmed"),
            "kind": "support" if len(sup) >= len(pre) else "pressure",
            "codes": sorted(funds),
        })
    out.sort(key=lambda e: -e["amount"])
    return out


def notify_stub(signals, path: str = db.DB_PATH):
    """告警通道占位：当前不发送任何通知，只把"应告警的信号"整理出来备用。

    丁总要求通道先预留（邮件 / 飞书 / 企业微信都可），等具体方案定了再接。
    接入时只需在 NOTIFY 里填 channel 与 targets，并在这里实现投递函数。
    """
    if not NOTIFY.get("enabled"):
        return {"sent": 0, "pending": len(signals or []), "channel": NOTIFY.get("channel") or "(未启用)"}
    # TODO(预留): 按 NOTIFY["channel"] 分发到对应通道
    return {"sent": 0, "pending": len(signals or []), "channel": NOTIFY.get("channel")}


def build_view(date: str, path: str = db.DB_PATH):
    """组装前端视图数据：分时序列 + 逐条信号 + 事件聚合 + 摘要。"""
    data = db.get_intraday_by_date(date, path=path)
    sigs = db.get_intraday_signals(date, path=path)
    series = {}
    for code, rows in data.items():
        core = [r for r in rows if r["minute"] not in ("1500", "1530")]
        amts = [r["amount"] or 0 for r in core]
        avg = sum(amts) / len(amts) if amts else 0
        series[code] = {
            "name": rows[0].get("name") if rows else None,
            "cat": rows[0].get("cat") if rows else None,
            "avg": avg,
            "points": _agg_points(rows, avg),
        }
    events = _events(sigs)
    support = [s for s in sigs if s["kind"] == "support"]
    pressure = [s for s in sigs if s["kind"] == "pressure"]
    conf = [s for s in sigs if s["level"] == "confirmed"]
    return {
        "date": date,
        "has_data": bool(data),
        "fund_count": len(data),
        "series": series,
        "signals": sigs,
        "events": events,
        "support_count": len(support),
        "pressure_count": len(pressure),
        "confirmed_count": len(conf),
        "support_amount": sum(s["amount"] or 0 for s in support),
        "pressure_amount": sum(s["amount"] or 0 for s in pressure),
        "event_support": sum(1 for e in events if e["kind"] == "support"),
        "event_pressure": sum(1 for e in events if e["kind"] == "pressure"),
        "thresholds": {"ratio": RATIO_TH, "cover": COVER_TH,
                       "breadth": BREADTH_MIN, "drop": DROP_TH},
    }
