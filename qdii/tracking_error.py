"""qdii/tracking_error.py — 模块E：跟踪误差（年化标准差）。

自建 NAV 历史法（零第三方依赖）：
- 基金历史净值：东财 lsjz（api.fund.eastmoney.com/f10/lsjz，实测每页固定 20 条，需翻页）。
- 跟踪基准日线：按 constants.TRACK_BENCHMARK 的**有序候选源**依次尝试
    · sina_us：新浪美股指数日K（全历史，实测稳定，优先）
    · em_us  ：东财 push2his 指数日K（数据全，但部分网络出口被整段断开）
  取**真实指数**而非场内 ETF，均为主流指数权威代码。
- 对齐：东财 lsjz 的净值日期已对齐海外估值日，按 lag=0（同交易日）直接配对；
    个别基金如需错位，用 TRACK_BENCHMARK 的 lag 字段微调。
    TE = std(基金日收益 − 指数日收益) × √252（年化百分点）。

关键教训（勿回退）：
- 不能用场内 ETF 代替指数作基准：QDII ETF 自身折溢价噪声极大（实测溢价可达 15%+），
  会使 TE 被高估到 30%+ 的荒谬水平。
- 雅虎日线在此环境被 403 封禁，已彻底弃用；披露值（F10 跟踪误差页）经实测 404，
  降级为可选项，此处不依赖。
- 东财 push2* 主机族在部分出口（公司网络直连）整段 RemoteDisconnected，
  这不是代码问题；新浪源可用即不受影响，日经/DAX 换到服务器出口即可恢复。
"""
import os
import sys
import json
import bisect
import datetime
from concurrent.futures import ThreadPoolExecutor
import math
import re

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
for p in (BASE, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from common.http import fetch_json, fetch_text
import db
import constants as C
import time

# 基准日线按 (type, code) 缓存：11 只基金仅 5 个不重复基准，避免重复抓取被限流。
_BENCH_CACHE = {}

# 新浪美股指数日K（JSONP，全历史）。symbol 例：.INX=标普500 / .NDX=纳斯达克100
# 注意：URL 里含 `%20`（=空格），不能用 % 格式化，故用占位符替换。
URL_SINA_US_KLINE = ("https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/"
                     "var%20_=/US_MinKService.getDailyK?symbol=__SYM__")


def fetch_nav_history(code, pages=8, page_size=20):
    """基金历史单位净值（东财 lsjz）。返回升序 [(日期, 净值)]。

    重要：lsjz 接口实测**忽略 pageSize 参数**，每页固定返回 20 条，
    因此必须翻页累积才能得到足够样本（否则仅 20 个点，无法算跟踪误差）。
    """
    rows = []
    for pi in range(1, pages + 1):
        url = ("https://api.fund.eastmoney.com/f10/lsjz?fundCode=%s&"
               "pageIndex=%d&pageSize=%d" % (code, pi, page_size))
        try:
            obj = fetch_json(url, referer="https://fundf10.eastmoney.com/",
                             use_proxy=None, retries=3)
        except Exception as e:  # noqa: BLE001
            print("[te] nav page %d failed (%s): %s" % (pi, code, e))
            break
        lst = (obj.get("Data") or {}).get("LSJZList", []) or []
        if not lst:
            break
        for r in lst:
            dwjz = r.get("DWJZ")
            fsrq = r.get("FSRQ")
            if dwjz and fsrq:
                try:
                    rows.append((fsrq, float(dwjz)))
                except (ValueError, TypeError):
                    pass
        if len(lst) < page_size:
            break
        if pi < pages:
            time.sleep(0.4)  # 限速，避免触发代理/上游限流
    rows.reverse()  # 升序
    return rows


def _parse_sina_jsonp(text):
    """新浪 JSONP：`/*...*/ var _=([{...}]);` → list。"""
    i = text.find("(")
    j = text.rfind(")")
    if i < 0 or j <= i:
        return []
    try:
        data = json.loads(text[i + 1:j])
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def fetch_benchmark_sina_us(symbol):
    """新浪美股指数日K，返回升序 [(日期, 收盘)]。symbol 例：.INX / .NDX。

    .N225 / .GDAXI 实测返回 null（新浪无此日K），由调用方回退到东财。
    """
    url = URL_SINA_US_KLINE.replace("__SYM__", symbol)
    txt = fetch_text(url, encoding="utf-8", use_proxy=None, retries=2,
                     referer="https://finance.sina.com.cn/")
    out = []
    for r in _parse_sina_jsonp(txt):
        d, c = r.get("d"), r.get("c")
        if not d or not c:
            continue
        try:
            out.append((d, float(c)))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x[0])
    return out


def fetch_benchmark_em(btype, bcode):
    """跟踪基准日线（东财 K 线）。返回升序 [(日期, 收盘)]。

    btype=em_a -> A 股 ETF（secid 1.bcode）；btype=em_us -> 海外指数（secid bcode）。
    klines 每行：日期,开,收,高,低,...（逗号分隔，收盘在第 3 列 / index 2）。
    """
    secid = bcode if btype == "em_us" else "1." + bcode
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?"
           "secid=%s&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57,f58&"
           "klt=101&fqt=1&beg=20260101&end=20500101" % secid)
    t = fetch_text(url, encoding="utf-8", use_proxy=None, retries=3)
    o = json.loads(t)
    k = o.get("data") or {}
    klines = k.get("klines") or []
    out = []
    for line in klines:
        parts = line.split(",")
        if len(parts) < 3:
            continue
        try:
            out.append((parts[0], float(parts[2])))
        except (ValueError, TypeError):
            pass
    return out


def fetch_benchmark_sina_fut(symbol):
    """新浪外盘期货日K（全球期货），返回升序 [(日期, 收盘)]。symbol 例：NK（日经225）。

    新浪没有日经225/DAX 的**指数**日K，但有对应的**期货**日K，方向一致，
    可作为跟踪误差基准（此前日经类别无基准、TE 长期为空）。
    接口：GlobalFuturesService.getGlobalFuturesDailyKLine，字段 date/close。
    """
    # ⚠️ URL 里的空格必须编码成 %20，否则 fetch_text 直接抛
    #    "URL can't contain control characters"（之前写成 var _=/ 就踩了这个坑）。
    url = ("https://stock.finance.sina.com.cn/futures/api/jsonp.php/"
           "var%20_=/GlobalFuturesService.getGlobalFuturesDailyKLine?symbol=" + symbol)
    txt = fetch_text(url, encoding="utf-8", use_proxy=None, retries=2,
                     referer="https://finance.sina.com.cn/")
    out = []
    for r in _parse_sina_jsonp(txt):
        d, c = r.get("date"), r.get("close")
        if not d or not c:
            continue
        try:
            out.append((str(d)[:10], float(c)))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x[0])
    return out


def fetch_benchmark_daily(btype, bcode):
    """按 (类型, 代码) 取基准日线，带缓存。返回升序 [(日期, 收盘)]。"""
    key = (btype, bcode)
    if key in _BENCH_CACHE:
        return _BENCH_CACHE[key]
    if btype == "sina_us":
        out = fetch_benchmark_sina_us(bcode)
    elif btype == "sina_fut":
        out = fetch_benchmark_sina_fut(bcode)
    else:
        out = fetch_benchmark_em(btype, bcode)
    _BENCH_CACHE[key] = out
    return out


def bench_cfg(code):
    """取一只基金的基准配置：先按代码（特例），再按大类（通用）。"""
    cfg = C.TRACK_BENCHMARK.get(code)
    if cfg:
        return cfg
    for f in C.FUND_POOL:
        if f["code"] == code:
            return C.TRACK_BENCHMARK_BY_CATEGORY.get(f.get("category")) or {}
    return {}


def resolve_benchmark(code):
    """按候选源顺序取基准，返回 (基准序列, 源标签) 或 (None, None)。

    源标签形如 "sina_us:.INX" / "em_us:100.SPX"，落库到 benchmark 字段便于溯源。
    """
    cfg = bench_cfg(code)
    if not cfg or not cfg.get("sources"):
        return None, None
    for btype, bcode in cfg.get("sources") or []:
        try:
            rows = fetch_benchmark_daily(btype, bcode)
        except Exception as e:  # noqa: BLE001
            print("[te] benchmark %s/%s failed: %s" % (btype, bcode, e))
            continue
        if len(rows) >= 12:
            return rows, "%s:%s" % (btype, bcode)
    return None, None


def compute_te(code, lag_override=None):
    """计算单只基金的跟踪误差（年化百分点）。失败返回 None。

    方法（自建 NAV 历史法，针对 QDII 特性修正）：
    1. 基金历史净值 vs **真实指数**日线，而非场内 ETF——ETF 自身折溢价噪声
       会让跟踪误差被严重高估（实测退化到 30%+）。
    2. 日期对齐：对每个净值日期 D，取「不晚于 D 的最近指数交易日」，再按 lag 前推
       （实测 lag=0 最贴合：lag=1 会把 TE 从 1.2% 抬到 18%+ 的假偏离）。
       TE = std(日收益差) × √252（年化百分点）。
    3. 返回 (TE, 净值最新日期, 基准源标签)。
    """
    cfg = bench_cfg(code)
    if not cfg or not cfg.get("sources"):
        return None
    lag = cfg.get("lag", 0)
    if lag_override is not None:
        lag = lag_override
    nav = fetch_nav_history(code)        # 升序 [(日期, 净值)]
    if len(nav) < 12:
        return None
    bmk, src = resolve_benchmark(code)
    if not bmk:
        print("[te] %s 无可用基准源（候选：%s）" % (code, cfg.get("sources")))
        return None
    if len(bmk) < 12:
        return None
    idx_dates = [d for d, _ in bmk]
    idx_close = [c for _, c in bmk]
    # 净值日期 -> 指数点位（不晚于该日的最近交易日，再前推 lag 个交易日）
    pairs = []
    for d, n in nav:
        p = bisect.bisect_right(idx_dates, d) - 1
        if p < 0:
            continue
        q = p - lag
        if q < 0:
            continue
        pairs.append((n, idx_close[q]))
    if len(pairs) < 11:
        return None
    diffs = []
    for i in range(1, len(pairs)):
        n0, i0 = pairs[i - 1]
        n1, i1 = pairs[i]
        diffs.append((n1 / n0 - 1) - (i1 / i0 - 1))
    if len(diffs) < 10:
        return None
    mean = sum(diffs) / len(diffs)
    var = sum((x - mean) ** 2 for x in diffs) / len(diffs)
    te = math.sqrt(var) * math.sqrt(252) * 100.0  # 年化百分点
    return round(te, 2), nav[-1][0], src


def _safe_compute_te(code):
    """算一只基金的跟踪误差，异常吞掉返回 None（并发用）。"""
    try:
        return compute_te(code)
    except Exception as e:  # noqa: BLE001
        print("[te] %s failed:" % code, e)
        return None


def _prefetch_benchmarks():
    """预取各基准指数日线进缓存（同大类共用一份，避免并发时重复下载）。"""
    seen = set()
    for f in C.FUND_POOL:
        cfg = bench_cfg(f["code"])
        for st, sc in cfg.get("sources") or []:
            key = (st, sc)
            if key in seen:
                continue
            seen.add(key)
            try:
                fetch_benchmark_daily(st, sc)
            except Exception as e:  # noqa: BLE001
                print("[te] benchmark prefetch failed:", key, e)


def fetch_tracking_error(date: str = None, path: str = C.QDII_DB_PATH):
    date = date or datetime.date.today().isoformat()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    conn = db.get_conn(path)
    # ⚠️ 规模变化：基金池 11 → 66 只，串行（含 0.6s 限速）要 40 分钟以上。
    # 改为：① 先单线程**预取基准日线**（同大类共用一份，缓存住）；
    #       ② 再并发算各只基金的 TE（纯网络）；③ 主线程单连接写库。
    _prefetch_benchmarks()
    results = {}
    codes = [f["code"] for f in C.FUND_POOL]
    try:
        with ThreadPoolExecutor(max_workers=6) as ex:
            for code, te in zip(codes, ex.map(_safe_compute_te, codes)):
                results[code] = te
    except Exception as e:  # noqa: BLE001
        print("[te] concurrent compute failed:", e)
    cnt = 0
    for f in C.FUND_POOL:
        code = f["code"]
        te = results.get(code)
        if te is None:
            continue
        te_val, nav_date, src = te
        # source 记实际命中的数据源家（eastmoney / sina），benchmark 记具体代码，便于溯源
        src_house = "sina" if src.startswith("sina_us") else "eastmoney"
        conn.execute(
            "INSERT INTO qdii_tracking_error(code, te_computed, method, "
            "as_of_date, source, benchmark) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(code) DO UPDATE SET te_computed=excluded.te_computed,"
            "method=excluded.method,as_of_date=excluded.as_of_date,"
            "source=excluded.source,benchmark=excluded.benchmark",
            (code, te_val, "computed_nav", nav_date, src_house, src))
        cnt += 1
    # 清理不在基金池内的历史行（与费率表同理）
    keep = [f["code"] for f in C.FUND_POOL]
    if keep:
        marks = ",".join("?" for _ in keep)
        conn.execute("DELETE FROM qdii_tracking_error WHERE code NOT IN (%s)" % marks, keep)
    conn.commit()
    conn.close()
    db.set_meta("last_te_fetch", now, path)
    return cnt


def get_tracking_error(path: str = C.QDII_DB_PATH):
    conn = db.get_conn(path)
    rows = conn.execute("SELECT * FROM qdii_tracking_error").fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    db.init_db()
    n = fetch_tracking_error()
    print("computed TE for %d funds" % n)
    for r in get_tracking_error():
        print("  %s te=%.2f%% (%s) bench=%s" % (
            r["code"], r["te_computed"], r["method"], r.get("benchmark")))
