"""drawdown/fetcher.py — 全历史日线抓取（东财 push2his / 新浪美股指数 / 新浪外汇）。

关键纪律（2026-11-20 实测后定）：
- 东财会按主机限流（Remote end closed connection），必须 6 台镜像轮换 + 串行 + 间隔；
  一次失败不留假数据，断点续传由 db.has_asset 与 dd_meta 水位保证。
- 全历史一次请求拿全（beg=19900101），单标的通常 0.2~0.5s；30 标的串行 < 30s。
- 新浪外汇是管道分隔字符串 `date,o,c,h,l,|date,...`，不是 JSON。
- 抓取结果写独立 dd.db，绝不碰 data.db / qdii.db。
"""
import json
import re
import ssl
import time
import urllib.request

from . import constants as C
from . import db

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

EM_MIRRORS = [
    "push2his.eastmoney.com", "1.push2his.eastmoney.com",
    "2.push2his.eastmoney.com", "7.push2his.eastmoney.com",
    "23.push2his.eastmoney.com", "82.push2his.eastmoney.com",
]


def _get(url, ref=None, enc="utf-8", timeout=20):
    h = dict(HDR)
    if ref:
        h["Referer"] = ref
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.read().decode(enc, "ignore")


def fetch_em_kline(secid, retries=2):
    """东财 push2his 日K，镜像轮换。返回升序 [(date, close)]。"""
    url_tpl = ("https://%s/api/qt/stock/kline/get?secid=%s"
               "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
               "&klt=101&fqt=1&beg=19900101&end=20500101")
    for _ in range(retries):
        for host in EM_MIRRORS:
            try:
                txt = _get(url_tpl % (host, secid), ref="https://quote.eastmoney.com/", timeout=15)
                obj = json.loads(txt)
                klines = (obj.get("data") or {}).get("klines") or []
                out = []
                for line in klines:
                    p = line.split(",")
                    if len(p) >= 3:
                        try:
                            out.append((p[0], float(p[2])))
                        except (ValueError, TypeError):
                            pass
                if out:
                    return out
            except Exception:
                continue
        time.sleep(0.6)
    return []


def fetch_sina_us(symbol, retries=2):
    """新浪美股指数日K（JSONP）。返回升序 [(date, close)]。"""
    url = ("https://stock.finance.sina.com.cn/usstock/api/jsonp_v2.php/"
           "var%%20_=/US_MinKService.getDailyK?symbol=%s" % symbol)
    for _ in range(retries):
        try:
            txt = _get(url, ref="https://finance.sina.com.cn/")
            i, j = txt.find("("), txt.rfind(")")
            rows = json.loads(txt[i + 1:j]) if i >= 0 and j > i else []
            out = []
            for r in rows:
                d, c = r.get("d"), r.get("c")
                if d and c:
                    try:
                        out.append((d, float(c)))
                    except (ValueError, TypeError):
                        pass
            out.sort(key=lambda x: x[0])
            if out:
                return out
        except Exception:
            pass
        time.sleep(0.4)
    return []


def fetch_sina_fx(symbol, retries=2):
    """新浪外汇日线（管道分隔字符串）。返回升序 [(date, close)]。

    结构：var _=("1994-08-30,8.5616,8.5616,8.5616,8.5616,|1994-08-31,...")
    每块 5 列：日期, 开, 收, 高, 低 —— 取收盘（index 2）。
    """
    url = ("https://vip.stock.finance.sina.com.cn/forex/api/jsonp.php/"
           "var%%20_=/NewForexService.getDayKLine?symbol=%s" % symbol)
    for _ in range(retries):
        try:
            txt = _get(url, ref="https://finance.sina.com.cn/")
            m = re.search(r'\("(.*)"\)', txt, re.S)
            body = m.group(1) if m else ""
            out = []
            for chunk in body.split("|"):
                p = chunk.split(",")
                if len(p) >= 3 and re.match(r"^\d{4}-\d{2}-\d{2}$", p[0]):
                    try:
                        out.append((p[0], float(p[2])))
                    except (ValueError, TypeError):
                        pass
            out.sort(key=lambda x: x[0])
            if out:
                return out
        except Exception:
            pass
        time.sleep(0.4)
    return []


def fetch_sina_a(symbol, retries=2):
    """新浪 A股/ETF 日K（JSON 数组，datalen 上限约 1023 根，约近 4 年）。

    作为东财限流期的降级备源：先让标的有数据，东财恢复后 --force 覆盖为全历史。
    symbol 例：sh000001 / sh000300 / sz399006 / sh510300。
    """
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           "CN_MarketData.getKLineData?symbol=%s&scale=240&ma=no&datalen=1023" % symbol)
    for _ in range(retries):
        try:
            txt = _get(url, ref="https://finance.sina.com.cn/")
            arr = json.loads(txt)
            out = []
            for r in arr:
                d, c = r.get("day"), r.get("close")
                if d and c:
                    try:
                        out.append((d[:10], float(c)))
                    except (ValueError, TypeError):
                        pass
            out.sort(key=lambda x: x[0])
            if out:
                return out
        except Exception:
            pass
        time.sleep(0.3)
    return []


def fetch_asset(asset):
    src, code = asset["src"], asset["code"]
    rows = []
    if src == "em":
        rows = fetch_em_kline(code)
    elif src == "sina_us":
        rows = fetch_sina_us(code)
    elif src == "sina_fx":
        rows = fetch_sina_fx(code)
    # 备源回退：东财限流时用新浪（A股/ETF 近4年，美股 2004 起）
    if not rows:
        if asset.get("alt_src") == "sina_us":
            rows = fetch_sina_us(asset["alt_code"])
        elif asset.get("alt_src") == "sina_a":
            rows = fetch_sina_a(asset["alt_code"] or asset["id"])
    return rows


def fetch_all(force=False, only=None, sleep=0.35, path=db.DB_PATH, log=print):
    """串行抓全部标的（或 only 指定 id）。返回 {id: (n, from, to)}。

    force=False 时跳过库里已有数据的标的（断点续传/增量友好）。
    """
    db.init_db(path)
    result = {}
    targets = [a for a in C.ALL if (not only or a["id"] in only)]
    for i, a in enumerate(targets, 1):
        aid = a["id"]
        if not force and db.has_asset(aid, path):
            log("[dd-fetch] %2d/%d %s(%s) 已有数据，跳过" % (i, len(targets), aid, a["name"]))
            continue
        rows = fetch_asset(a)
        if not rows:
            log("[dd-fetch] %2d/%d %s(%s) 抓取失败，留空待重试" % (i, len(targets), aid, a["name"]))
            result[aid] = (0, None, None)
            continue
        db.upsert_daily(aid, rows, path)
        result[aid] = (len(rows), rows[0][0], rows[-1][0])
        log("[dd-fetch] %2d/%d %s(%s) %d 根 %s~%s" % (
            i, len(targets), aid, a["name"], len(rows), rows[0][0], rows[-1][0]))
        time.sleep(sleep)
    db.set_meta("last_fetch", time.strftime("%Y-%m-%d %H:%M:%S"), path)
    return result


if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("--")] or None
    r = fetch_all(force=force, only=only)
    print("done:", len(r), "assets")
