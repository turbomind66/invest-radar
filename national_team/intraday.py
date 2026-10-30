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


# ---------------- 方案C：逐笔大额买单识别（Tick-level large buy detection） ----------------
# 数据源：东财 stock/details/get 滚动明细（每笔含 时间/价格/手数/方向/金额）。
# 这是滚动窗口——盘后冻结只留尾盘少量、盘中才有多笔；多次采样可累积全天大单分布。
# 与方案A（分时放量）的关系：方案A 看「量放大了」，方案C 钻到逐笔层验证「是不是主动性大买单在托盘」。
TICK_BIG = 30e4        # 大单阈值（单笔成交额，元）：宽基ETF 单笔 ≥ 30 万元算大单
TICK_HUGE = 100e4      # 超大单阈值（元）：≥ 100 万元
BUY_RATIO = 0.60       # 窗口内大买单额占比 ≥ 60% 视为「主动买入主导」
MIN_WIN_AMT = 200e4    # 窗口大单净额绝对值 ≥ 200 万元才记为一个像样的「集中」事件
UP_MIN = 0.0           # 价格上行门槛（末价 ≥ 首价 × (1+UP_MIN)）
TICK_WIN = 5           # 聚合窗口（分钟）
# 持久化：Actions 每次都是干净环境、本地 SQLite 不随仓库走，
# 大单必须随 data.json 往返，否则盘中多次采样的数据会在下次运行时全部丢失。
TICK_EXPORT_DAYS = 3       # data.json 里保留最近几个交易日的大单原始行
TICK_CAP_PER_FUND = 200    # 每只基金每日最多保留多少笔（按金额降序），控制体积


def _secid_of(code: str) -> str:
    """东财 secid：沪市 1.xxxxxx，深市 0.xxxxxx（与腾讯 sh/sz 前缀规则一致）。"""
    return ("1." if code[0] in "56" else "0.") + code


def fetch_details(code: str, lmt: int = 2000):
    """抓东财逐笔成交明细（滚动窗口），返回 [(ttime, price, volume手, direction, amount元)]。

    东财 details 接口（fields2=f51,f52,f53,f54,f55）的真实列序为：
        f51=时间, f52=价格, f53=成交量(手), **f54=笔数**, **f55=成交性质**

    ⚠️ 2026-10-30 修正：此前误把 **f54（笔数，取值 3~97 的成交笔数）** 当成买卖方向，
    结果所有大单都被判成「卖单」（面板上表现为「大买单合计 0.00 亿、大卖单合计 2.12 亿」，
    且因买单占比恒为 0，永远识别不出「集中扫货」）。
    正确的方向列是 **f55 成交性质**：1=主动买、2=主动卖、其它=中性(0)。

    金额不依赖接口的金额列（单位有歧义），自算 amount = 手数 × 价格 × 100（ETF 一手=100 股）。
    盘后接口冻结（只返回当日最后一笔左右），盘中会滚动返回最近若干笔。
    """
    sid = _secid_of(code)
    url = ("https://push2.eastmoney.com/api/qt/stock/details/get?secid=%s"
           "&fields1=f1,f2,f3,f4,f5&fields2=f51,f52,f53,f54,f55"
           "&pos=-1&lmt=%d"
           "&ut=b2884a393a59ad64002292a3e90d46a5" % (sid, lmt))
    txt = _get(url, timeout=12, retries=2, proxies=(None, "http://127.0.0.1:10808"))
    d = json.loads(txt)
    det = ((d.get("data") or {}).get("details") or [])
    out = []
    for row in det:
        p = row.split(",")
        if len(p) < 5:
            continue
        try:
            t, price, vol = p[0], float(p[1]), int(p[2])
        except ValueError:
            continue
        # f55 = 成交性质：1=主动买 / 2=主动卖 / 其它=中性(0)
        # （f54 是笔数，不能当方向用 —— 见上面说明）
        try:
            raw_dir = int(p[4])
        except ValueError:
            raw_dir = 0
        direction = raw_dir if raw_dir in (1, 2) else 0
        amount = vol * price * 100
        out.append((t, price, vol, direction, amount))
    return out


def archive_tick(date: str, path: str = db.DB_PATH, lmt: int = 2000):
    """抓逐笔明细并归档大单到 intraday_tick。返回 (入库行数, 覆盖基金数)。

    只存金额 ≥ TICK_BIG 的大单，控制体积；主键去重，盘中多次运行可累积全天。
    """
    n, funds = 0, set()
    for code, name, cat in WATCHLIST:
        try:
            rows = fetch_details(code, lmt)
        except Exception:
            rows = []
        big = [r for r in rows if r[4] >= TICK_BIG]
        if not big:
            continue
        store = [{"code": code, "name": name, "cat": cat, "ttime": r[0],
                  "price": r[1], "volume": r[2], "direction": r[3], "amount": r[4]}
                 for r in big]
        db.save_tick_rows(date, store, path)
        n += len(store)
        funds.add(code)
    return n, len(funds)


def tick_rows_for_export(date: str, path: str = db.DB_PATH, cap: int = None):
    """导出用：某日大单的紧凑表示 [[code,ttime,price,volume,direction,amount],...]。

    Actions 干净环境下大单必须写进 data.json 才能跨次累积（盘中多次采样不丢）。
    体积控制：每只基金每日只保留金额最大的 cap 笔；name/cat 不落盘，导入时反查补回。
    """
    cap = cap or TICK_CAP_PER_FUND
    rows = db.get_tick_by_date(date, path=path)
    by = {}
    for r in rows:
        by.setdefault(r["code"], []).append(r)
    out = []
    for code, lst in by.items():
        lst.sort(key=lambda r: -(r.get("amount") or 0))
        for r in lst[:cap]:
            out.append([code, r["ttime"], r["price"], r["volume"],
                        r["direction"], r["amount"]])
    return out


def restore_tick_rows(date: str, compact: list, path: str = db.DB_PATH) -> int:
    """导入用：把紧凑行还原写回 intraday_tick（name/cat 从 WATCHLIST 反查补回）。

    与 save_tick_rows 的主键去重一致，重复导入不会产生重复行。
    """
    if not compact:
        return 0
    meta = {c: (n, cat) for c, n, cat in WATCHLIST}
    store = []
    for it in compact:
        if len(it) < 6:
            continue
        code, ttime, price, vol, direction, amount = it[0], it[1], it[2], it[3], it[4], it[5]
        name, cat = meta.get(code, (code, ""))
        store.append({"code": code, "name": name, "cat": cat, "ttime": ttime,
                      "price": price, "volume": vol, "direction": direction,
                      "amount": amount})
    if store:
        db.save_tick_rows(date, store, path)
    return len(store)


def _win_of(ttime: str) -> int:
    """把时间（"HHMM" 或 "HH:MM:SS"）归到 5 分钟窗口起点（分钟数）。

    方案A 信号存的是 HHMM（如 "0931"），方案C 明细是 HH:MM:SS（如 "09:31:00"），
    这里统一去掉冒号后按 HHMM 解析，保证两种格式落到同一窗口。
    """
    t = ttime.replace(":", "")
    h, m = int(t[:2]), int(t[2:4])
    return ((h * 60 + m) // TICK_WIN) * TICK_WIN


def compute_tick_windows(date: str, path: str = db.DB_PATH):
    """纯读：把当日大单按 (code, 5分钟窗口) 聚合，返回**所有**含大单的窗口。

    每个窗口带 `kind` 标记：
      tick_buy  ：窗口内大单净买为正、大买单占比 ≥ BUY_RATIO、价格上行 → 疑似主动托盘
      tick_sell ：窗口内大单净卖为负、大卖单占比高、价格下行 → 疑似集中抛压
      neutral   ：有大单但未达集中阈值（盘后快照的常态）
    ⚠️ 2026-10-30：此前只有「达阈值」的窗口才进 events，导致面板表格在
    无集中事件时整表空白（丁总反馈「表格没显示出来」）。而表格的列设计
    （时段/基金/方向/大买单额/大卖单额/净买/买单占比/价格涨跌）本就是
    **窗口聚合口径** —— 应展示所有含大单的窗口，用「方向」列区分是否达阈值。
    """
    rows = db.get_tick_by_date(date, path=path)
    if not rows:
        return []
    buckets = {}
    for r in rows:
        wb = _win_of(r["ttime"])
        key = (r["code"], wb)
        b = buckets.setdefault(key, {"code": r["code"], "name": r["name"], "cat": r["cat"],
                                     "win": wb, "buy": 0.0, "sell": 0.0, "neutral": 0.0,
                                     "buy_n": 0, "sell_n": 0, "neutral_n": 0,
                                     "prices": []})
        # direction: 1=主动买 / 2=主动卖 / 0=中性或方向未知。
        # 中性单既不计买也不计卖（此前 else 分支把非 1 全当卖，导致买单占比恒为 0）。
        if r["direction"] == 1:
            b["buy"] += r["amount"]; b["buy_n"] += 1
        elif r["direction"] == 2:
            b["sell"] += r["amount"]; b["sell_n"] += 1
        else:
            b["neutral"] += r["amount"]; b["neutral_n"] += 1
        b["prices"].append(r["price"])
    out = []
    for (code, wb), b in buckets.items():
        net = b["buy"] - b["sell"]
        tot = b["buy"] + b["sell"]
        buy_ratio = (b["buy"] / tot) if tot else 0.0
        first_p, last_p = b["prices"][0], b["prices"][-1]
        up = (last_p / first_p - 1.0) if first_p else 0.0
        if net >= MIN_WIN_AMT and buy_ratio >= BUY_RATIO and up >= UP_MIN:
            kind = "tick_buy"
        elif net <= -MIN_WIN_AMT and (1 - buy_ratio) >= BUY_RATIO and up < UP_MIN:
            kind = "tick_sell"
        else:
            kind = "neutral"
        out.append({
            "code": code, "name": b["name"], "cat": b["cat"],
            "window": "%02d%02d" % (wb // 60, wb % 60),
            "buy": round(b["buy"]), "sell": round(b["sell"]), "net": round(net),
            # 方向未知（历史归档口径修正前抓的）大单金额：买卖列会是 0，
            # 前端单列展示，避免「有行但数字全是 0」的误读。
            "neutral": round(b["neutral"]),
            "buy_n": b["buy_n"], "sell_n": b["sell_n"], "neutral_n": b["neutral_n"],
            "buy_ratio": round(buy_ratio, 2), "up_pct": round(up * 100, 3),
            "kind": kind,
            "amount": round(abs(net) if net else (b["buy"] + b["sell"] + b["neutral"])),
        })
    # 排序：先按是否达阈值（集中事件优先），再按金额绝对值降序
    out.sort(key=lambda e: (0 if e["kind"] != "neutral" else 1, -e["amount"]))
    return out


def compute_tick_events(date: str, path: str = db.DB_PATH):
    """纯读：识别「集中扫货/抛压」事件（仅含达阈值的窗口）。

    不写库。信号方向：
      tick_buy  ：窗口内大单净买为正、大买单占比 ≥ BUY_RATIO、价格上行 → 疑似主动托盘
      tick_sell ：窗口内大单净卖为负、大卖单占比高、价格下行 → 疑似集中抛压
    """
    return [e for e in compute_tick_windows(date, path=path)
            if e["kind"] in ("tick_buy", "tick_sell")]


def detect_tick(date: str, path: str = db.DB_PATH):
    """计算逐笔事件，并与方案A（分时放量）联动升级信号。

    联动：方案A 的护盘(support)信号所在 5 分钟窗口，若方案C 同窗口有 tick_buy 印证
    → 把该信号 level 升级为 confirmed_by_tick（融合「放量+逐笔大买」双重证据），note 追加提示。
    返回当日逐笔事件列表。
    """
    events = compute_tick_events(date, path=path)
    if not events:
        return events
    buy_windows = {e["window"] for e in events if e["kind"] == "tick_buy"}
    sell_windows = {e["window"] for e in events if e["kind"] == "tick_sell"}
    sigs = db.get_intraday_signals(date, path=path)
    if not sigs:
        return events
    changed = False
    for s in sigs:
        wb = _win_of(s["minute"])
        w = "%02d%02d" % (wb // 60, wb % 60)
        if s["kind"] == "support" and w in buy_windows:
            if s["level"] != "confirmed_by_tick":
                s["level"] = "confirmed_by_tick"
                s["note"] = (s["note"] or "") + "，逐笔大买单印证"
                changed = True
        elif s["kind"] == "pressure" and w in sell_windows:
            s["note"] = (s["note"] or "").rstrip("；") + "；逐笔大卖单印证"
            changed = True
    if changed:
        db.save_intraday_signals(date, sigs, path)
    return events


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
    conf = [s for s in sigs if s["level"] == "confirmed" or s["level"] == "confirmed_by_tick"]
    # 方案C：逐笔大额买单识别（基于东财滚动明细，纯读聚合，不写库）
    tick_windows = compute_tick_windows(date, path=path)
    tick_events = [e for e in tick_windows if e["kind"] in ("tick_buy", "tick_sell")]
    tick_buy = [e for e in tick_events if e["kind"] == "tick_buy"]
    tick_sell = [e for e in tick_events if e["kind"] == "tick_sell"]
    tick_rows = db.get_tick_by_date(date, path=path)
    # 合计口径必须统计【全部】大单，而非仅「集中事件」金额：
    # 盘后快照里大单散落各窗口、形不成集中事件，若只汇总事件金额会恒为 0，
    # 造成"有 12 笔大单却显示 0.00 亿"的假象。
    # 方向口径与 compute_tick_events 保持一致：1=买 / 2=卖 / 0=中性（方向未知，不计入买卖）。
    tick_buy_amt = sum(r.get("amount") or 0 for r in tick_rows if r.get("direction") == 1)
    tick_sell_amt = sum(r.get("amount") or 0 for r in tick_rows if r.get("direction") == 2)
    tick_neutral_amt = sum(r.get("amount") or 0 for r in tick_rows
                            if r.get("direction") not in (1, 2))
    tick_summary = {
        "has_data": bool(tick_rows),
        "big_count": len(tick_rows),
        "events": tick_events,
        # 所有含大单的窗口（含未达集中阈值的），供表格逐行展示 ——
        # 否则无集中事件时表格整表空白，看不到已抓到的大单分布。
        "windows": tick_windows,
        "buy_events": len(tick_buy),
        "sell_events": len(tick_sell),
        "buy_amount": tick_buy_amt,
        "sell_amount": tick_sell_amt,
        "neutral_amount": tick_neutral_amt,
        "thresholds": {"big": TICK_BIG, "huge": TICK_HUGE, "buy_ratio": BUY_RATIO,
                       "min_win_amt": MIN_WIN_AMT, "window": TICK_WIN},
    }
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
        "tick": tick_summary,
    }
