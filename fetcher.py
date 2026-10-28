"""抓取：国家队宽基ETF主力净流入（代理"国家队买卖A股"）+ 相关新闻。"""
import urllib.request
import json
import ssl
import time
import datetime
import re

import db

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

# 东方财富多个镜像主机，遇限流/断连自动轮换
HOSTS = [
    "push2.eastmoney.com",
    "push2delay.eastmoney.com",
    "82.push2.eastmoney.com",
    "11.push2.eastmoney.com",
]

# 国家队常用护盘宽基ETF池（代码, 名称, 类别）
WATCHLIST = [
    ("510300", "华泰柏瑞沪深300ETF", "沪深300"),
    ("510310", "易方达沪深300ETF", "沪深300"),
    ("159919", "嘉实沪深300ETF", "沪深300"),
    ("510330", "华夏沪深300ETF", "沪深300"),
    ("510500", "南方中证500ETF", "中证500"),
    ("510510", "广发中证500ETF", "中证500"),
    ("512100", "南方中证1000ETF", "中证1000"),
    ("560010", "易方达中证1000ETF", "中证1000"),
    ("159845", "华夏中证1000ETF", "中证1000"),
    ("510050", "华夏上证50ETF", "上证50"),
    ("159915", "易方达创业板ETF", "创业板"),
    ("588000", "华夏科创50ETF", "科创50"),
    ("588800", "博时科创100ETF", "科创100"),
    ("563300", "中证A500ETF", "中证A500"),
]

FIELDS = "f12,f14,f2,f3,f4,f6,f62"
NEWS_KEYWORDS = ["国家队", "汇金", "证金", "增持", "护盘", "ETF", "中央汇金", "主力资金", "宽基"]


def _num(v):
    try:
        if v in (None, "-", ""):
            return 0.0
        return float(v)
    except Exception:
        return 0.0


def _get(url: str, ref: str = None, timeout: int = 20, max_try: int = 3):
    """带主机轮换 + 指数退避的GET。url 中写主域名即可，内部轮换镜像。"""
    hdr = dict(HDR)
    if ref:
        hdr["Referer"] = ref
    last = None
    for attempt in range(max_try):
        host = HOSTS[attempt % len(HOSTS)]
        u = url.replace("push2.eastmoney.com", host, 1) if "push2.eastmoney.com" in url else url
        try:
            req = urllib.request.Request(u, headers=hdr)
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            last = e
            time.sleep(min(2 ** attempt * 0.4, 3))
    raise last


def _sina_prefix(code: str) -> str:
    return "sh" + code if code[0] in "56" else "sz" + code


def fetch_sina_etfs() -> dict:
    """从新浪行情接口取价格/涨跌幅/成交额（稳定，作为基础字段）。"""
    wl_map = {c: (nm, cat) for c, nm, cat in WATCHLIST}
    codes = ",".join(_sina_prefix(c) for c, _, _ in WATCHLIST)
    url = "https://hq.sinajs.cn/list=" + codes
    out = {}
    try:
        txt = _get_sina(url)
    except Exception:
        return out
    for line in txt.replace("\n", ";").split(";"):
        if "hq_str_" not in line:
            continue
        try:
            key, body = line.split("=", 1)
            code = key.replace("var hq_str_", "").lstrip("shsz")
            parts = body.strip('"').split(",")
            if len(parts) < 10:
                continue
            prev = _num(parts[2])
            cur = _num(parts[3])
            chg = round((cur - prev) / prev * 100, 2) if prev else 0.0
            out[code] = {
                "price": cur,
                "chg": chg,
                "amount": _num(parts[9]),
            }
        except Exception:
            continue
    return out


def _get_sina(url: str, timeout: int = 15, max_try: int = 3):
    hdr = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
    last = None
    for i in range(max_try):
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return r.read().decode("gbk", "ignore")
        except Exception as e:
            last = e
            time.sleep(0.5 * (i + 1))
    raise last


def fetch_em_main_net() -> dict:
    """从东方财富取观察池各ETF的资金流四档净额（国家队代理核心指标）。

    返回 {code: {main_net, huge_net, big_net, mid_net, small_net}}，单位元。
    东财口径：主力净 = 超大单净 + 大单净（f62 = f66 + f72，已勾稽验证）。
    """
    wl_map = {c: (nm, cat) for c, nm, cat in WATCHLIST}
    found = {}
    for pn in range(1, 25):
        url = (
            "https://push2.eastmoney.com/api/qt/clist/get?pn=%d&pz=100&po=1&np=1"
            "&fltt=2&invt=2&fid=f3&fs=b:MK0021&fields=f12,f62,f66,f72,f78,f84" % (pn,)
        )
        try:
            d = json.loads(_get(url))
        except Exception:
            break
        diff = (d.get("data") or {}).get("diff", [])
        if not diff:
            break
        for it in diff:
            c = it.get("f12")
            if c in wl_map and c not in found:
                found[c] = {
                    "main_net": _num(it.get("f62")),
                    "huge_net": _num(it.get("f66")),
                    "big_net": _num(it.get("f72")),
                    "mid_net": _num(it.get("f78")),
                    "small_net": _num(it.get("f84")),
                }
        if len(found) == len(WATCHLIST):
            break
        time.sleep(0.25)
    return found


def fetch_etfs() -> (list, bool):
    """返回 (ETF列表, 主力净流入是否可用)。价格等用新浪，主力净流入优先东方财富。"""
    sina = fetch_sina_etfs()
    try:
        em = fetch_em_main_net()
        em_ok = bool(em)
    except Exception:
        em = {}
        em_ok = False
    result = []
    for c, nm, cat in WATCHLIST:
        s = sina.get(c, {})
        em_row = em.get(c) or {}
        result.append({
            "code": c,
            "name": nm,
            "cat": cat,
            "price": s.get("price"),
            "chg": s.get("chg"),
            "amount": s.get("amount", 0.0),
            "main_net": em_row.get("main_net") if em_ok else None,
            "huge_net": em_row.get("huge_net") if em_ok else None,
            "big_net": em_row.get("big_net") if em_ok else None,
            "mid_net": em_row.get("mid_net") if em_ok else None,
            "small_net": em_row.get("small_net") if em_ok else None,
        })
    return result, em_ok


def _norm_news_title(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "")).strip()


def _extract_news_from_json(obj, title_keys=("title", "t", "subject"), url_keys=("url", "u", "link"), time_keys=("ctime", "time", "date", "dt")):
    """在任意JSON结构里找新闻列表，提取标题/链接/时间。"""
    def find_list(o):
        if isinstance(o, list):
            return o
        if isinstance(o, dict):
            for v in o.values():
                r = find_list(v)
                if r:
                    return r
        return None

    lst = find_list(obj)
    if not lst:
        return []
    out = []
    for item in lst:
        if not isinstance(item, dict):
            continue
        title = next((str(item.get(k, "")) for k in title_keys if item.get(k)), "")
        url = next((str(item.get(k, "")) for k in url_keys if item.get(k)), "")
        t = next((str(item.get(k, "")) for k in time_keys if item.get(k)), "")
        if title:
            out.append({"title": _norm_news_title(title), "url": url, "time": t})
    return out


def fetch_news() -> list:
    """多源尽力抓取与国家队相关的财经新闻，失败则降级返回空。"""
    out = []
    srcs = [
        # 新浪滚动财经 (pageid 515)
        ("新浪财经", "https://feed.mix.sina.com.cn/api/roll/get?pageid=515&ct=1&num=40&version=1",
         "https://finance.sina.com.cn/"),
        # 东方财富搜索
        ("东方财富", "https://search-api-web.eastmoney.com/search/jsonp?cb=cb&search_text=%E5%9B%BD%E5%AE%B6%E9%98%9F&pageindex=1&pagesize=20&type=12",
         "https://search.eastmoney.com/"),
    ]
    raw = []
    for src, url, ref in srcs:
        try:
            txt = _get(url, ref)
            txt = re.sub(r"^cb\(|\)$", "", txt.strip()) if txt.strip().startswith("cb(") else txt
            d = json.loads(txt)
            items = _extract_news_from_json(d)
            for it in items:
                it["src"] = src
                raw.append(it)
        except Exception:
            continue
    # 关键词过滤
    seen = set()
    for it in raw:
        title = it["title"]
        if not any(k in title for k in NEWS_KEYWORDS):
            continue
        key = title[:40]
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out[:12]


def is_trading_day(d=None) -> bool:
    d = d or datetime.date.today()
    return d.weekday() < 5  # 周一~周五


def fetch_and_save(force: bool = False) -> dict:
    """抓取当日数据并入库。返回状态摘要。"""
    today = datetime.date.today().strftime("%Y-%m-%d")
    if not is_trading_day():
        return {"ok": False, "reason": "非交易日（周末），跳过抓取", "date": today}

    etfs, em_ok = fetch_etfs()
    news = fetch_news()
    total_main_net = sum((e["main_net"] or 0) for e in etfs)
    total_amount = sum(e["amount"] for e in etfs)

    db.save_etf_daily(today, etfs)
    db.save_news(today, news)
    db.set_meta("last_fetch", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    db.set_meta("last_date", today)
    db.set_meta("news_ok", "1" if news else "0")
    db.set_meta("mainnet_ok", "1" if em_ok else "0")

    return {
        "ok": True,
        "date": today,
        "etf_count": len(etfs),
        "mainnet_ok": em_ok,
        "total_main_net_yi": round(total_main_net / 1e8, 2),
        "total_amount_yi": round(total_amount / 1e8, 2),
        "news_count": len(news),
    }


# ---------------- 历史回补：还原已收盘交易日的真实快照 ----------------
# 公开行情接口默认只给实时数据；本组函数走 push2his 历史接口，
# 准确还原历史交易日的 收盘价/成交额/涨跌幅/主力净流入，用于补回缺失的历史日期。
def _hist_get(url: str, retries: int = 3):
    hdr = dict(HDR)
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=20, context=CTX) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            last = e
            time.sleep(0.4 * (i + 1))
    raise last


def _secid_of(code: str) -> str:
    return ("1." if code[0] in "56" else "0.") + code


def backfill_date(date: str) -> dict:
    """还原指定已收盘交易日(YYYY-MM-DD)的真实快照并入库。返回统计。"""
    rows, ok_main, missing = [], 0, []
    d = datetime.date.fromisoformat(date)
    beg = (d - datetime.timedelta(days=12)).strftime("%Y%m%d")
    end = d.strftime("%Y%m%d")
    for code, name, cat in WATCHLIST:
        sid = _secid_of(code)
        # 1) 历史K线：收盘价/成交额(元)/涨跌幅%
        kn = None
        try:
            u_k = (
                "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s"
                "&fields1=f1,f2,f3,f4,f5,f6"
                "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
                "&klt=101&fqt=1&beg=%s&end=%s" % (sid, beg, end)
            )
            dk = json.loads(_hist_get(u_k))
            for ln in (dk.get("data") or {}).get("klines", []):
                p = ln.split(",")
                if p[0] == date and len(p) >= 9:
                    kn = (float(p[2]), float(p[6]), float(p[8]))
                    break
        except Exception:
            kn = None
        # 2) 资金流历史：主力净流入 + 四档净额(元)
        #    fflow daykline 字段序：f51日期 f52主力净 f53小单净 f54中单净 f55大单净 f56超大单净
        mn = huge = big = mid = small = None
        try:
            u_f = (
                "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?lmt=40"
                "&klt=101&secid=%s"
                "&fields1=f1,f2,f3,f4"
                "&fields2=f51,f52,f53,f54,f55,f56" % sid
            )
            df = json.loads(_hist_get(u_f))
            for ln in (df.get("data") or {}).get("klines", []):
                p = ln.split(",")
                if p[0] == date and len(p) >= 6:
                    mn, small, mid, big, huge = (float(p[i]) for i in range(1, 6))
                    break
        except Exception:
            mn = huge = big = mid = small = None
        if kn is None:
            missing.append((code, name))
            continue
        price, amount, chg = kn
        rows.append({
            "code": code, "name": name, "cat": cat,
            "price": price, "chg": chg, "amount": amount, "main_net": mn,
            "huge_net": huge, "big_net": big, "mid_net": mid, "small_net": small,
        })
        if mn is not None:
            ok_main += 1
        time.sleep(0.08)
    db.save_etf_daily(date, rows)
    db.save_news(date, [])  # 历史新闻无法精确还原，留空
    return {"date": date, "etf_count": len(rows), "mainnet_ok": ok_main, "missing": missing}


if __name__ == "__main__":
    print(json.dumps(fetch_and_save(), ensure_ascii=False, indent=2))
