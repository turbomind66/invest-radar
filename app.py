"""国家队买卖A股日报 —— 零依赖 Web 应用（Python 标准库）。
- 每日收盘后(16:05)自动抓取宽基ETF主力净流入，作为国家队动向代理
- 提供仪表盘展示：当日净买入合计、历史趋势、各ETF明细、相关新闻
"""
import http.server
import socketserver
import threading
import json
import datetime
import os
import sys
import time
import urllib.parse

import db
import fetcher

BASE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(BASE, "templates", "index.html")
PORT = int(os.environ.get("PORT", "5000"))


def _summarize(rows):
    """对一批ETF行做汇总：净买入合计/成交额/主力数据完整度/分类小计/四档净额。"""
    total_main = sum((e["main_net"] or 0) for e in rows)
    total_amount = sum((e["amount"] or 0) for e in rows)
    has_main = sum(1 for e in rows if e["main_net"] is not None)
    by_cat = {}
    for e in rows:
        by_cat.setdefault(e["cat"], 0.0)
        by_cat[e["cat"]] += (e["main_net"] or 0)
    # 四档净额（超大/大/中/小单）：无任一有效值则整体为 None（前端不显示拆解行）
    tiers = {}
    has_tier = any(e.get(k) is not None for e in rows for k in db.TIER_FIELDS)
    for k in db.TIER_FIELDS:
        tiers[k] = sum((e.get(k) or 0) for e in rows) if has_tier else None
    # 一级市场申赎（ETF 份额口径）：净申购合计(亿元)与覆盖只数
    has_share = sum(1 for e in rows if e.get("net_sub") is not None)
    share_net_sub = sum((e.get("net_sub") or 0) for e in rows) if has_share else None
    return {
        "total_main_net": total_main,
        "total_amount": total_amount,
        "has_main": has_main,
        "missing_main": len(rows) - has_main,
        "by_cat": by_cat,
        "tiers": tiers,
        "share_net_sub": share_net_sub,
        "has_share": has_share,
    }


def build_payload(date=None, cats=None, codes=None, path=db.DB_PATH):
    """组装前端数据。支持按 日期 / 类别 / 基金代码 三个维度筛选。

    path 可注入（测试用临时库），默认与服务端共用同一个 SQLite。
    """
    dates = db.get_dates(path)
    latest = dates[-1] if dates else None
    sel_date = date if (date in dates) else latest
    fallback_from = None

    cats = [c for c in (cats or []) if c]
    codes = [c for c in (codes or []) if c]

    # 未指定日期时：若最新日期的数据尚未生成（盘前/未收盘），自动回退到最近有效日
    if not date and sel_date:
        probe = db.get_etf_by_date(sel_date, path=path)
        if not any(e["main_net"] is not None for e in probe):
            alt = db.latest_main_date(path)
            if alt and alt != sel_date:
                fallback_from, sel_date = sel_date, alt

    etf = db.get_etf_by_date(sel_date, cats, codes, path) if sel_date else []
    etf_all = db.get_etf_by_date(sel_date, path=path) if sel_date else []
    # 一级市场申赎口径：把当日份额变动并入明细行（chg_shares 亿份 / net_sub 亿元）
    share_rows = db.get_shares_by_date(sel_date, path=path) if sel_date else []
    smap = {r["code"]: r for r in share_rows}
    for e in etf:
        s = smap.get(e["code"]) or {}
        e["shares"] = s.get("shares")
        e["chg_shares"] = s.get("chg_shares")
        e["net_sub"] = s.get("net_sub")
    news = db.get_news_by_date(sel_date, path) if sel_date else []
    # 历史序列：未筛选用全量汇总（趋势图展示完整区间，日期=二级∪一级并集）；
    # 筛选时用窗口内聚合（与静态端浏览器自算同口径）
    if cats or codes:
        history = db.get_history(cats, codes, path, limit_days=db.EXPORT_WINDOW_DAYS)
    else:
        history = db.get_history_full(path)

    last_fetch = db.get_meta("last_fetch", "—", path)
    last_sync = db.get_meta("last_sync", "—", path)
    news_ok = db.get_meta("news_ok", "1", path) == "1"

    cur = _summarize(etf)
    full = _summarize(etf_all)
    # 主力数据是否可用：按所选日期/范围的真实数据判定（而非全局标记）
    mainnet_ok = cur["has_main"] > 0
    partial_main = mainnet_ok and cur["missing_main"] > 0

    return {
        "latest_date": sel_date,
        "dates": dates,
        "last_fetch": last_fetch,
        "last_sync": last_sync,
        "news_ok": news_ok,
        "mainnet_ok": mainnet_ok,
        "partial_main": partial_main,
        "etf": etf,
        "news": news,
        "history": history,
        "filters": {
            "date": sel_date,
            "cats": cats,
            "codes": codes,
            "filtered": bool(cats or codes),
            "auto": not bool(date),
            "fallback_from": fallback_from,
        },
        "options": {
            "dates": dates,
            "cats": db.get_cats(path),
            "funds": db.get_funds(path),
        },
        "summary": {
            "total_main_net_yi": round(cur["total_main_net"] / 1e8, 2) if mainnet_ok else None,
            "total_amount_yi": round(cur["total_amount"] / 1e8, 2),
            "etf_count": len(etf),
            "main_cnt": cur["has_main"],
            "by_cat_yi": {k: round(v / 1e8, 2) for k, v in cur["by_cat"].items()},
            "full_total_main_net_yi": round(full["total_main_net"] / 1e8, 2) if full["has_main"] else None,
            "full_etf_count": len(etf_all),
            "tier_yi": {k: (None if v is None else round(v / 1e8, 2))
                        for k, v in cur["tiers"].items()},
            # 一级市场申赎（ETF 份额口径，亿元）：二级资金流看不到的一级申购/赎回
            "share_net_sub_yi": (round(cur["share_net_sub"], 2)
                                 if cur["share_net_sub"] is not None else None),
            "share_cover": cur["has_share"],
        },
    }


def signal_label(yi):
    if yi is None:
        return ("主力净流入暂不可用", "flat")
    if yi > 5:
        return ("明显净买入（护盘）", "buy")
    if yi > 0:
        return ("净买入", "buy")
    if yi < -5:
        return ("明显净卖出", "sell")
    if yi < 0:
        return ("净卖出", "sell")
    return ("基本中性", "flat")


def do_fetch():
    res = fetcher.fetch_and_save()
    return res


# ---------------- 定时调度：收盘后自动抓取 ----------------
def scheduler_loop():
    while True:
        now = datetime.datetime.now()
        # 目标时间：今天 16:05；若已过则明天
        target = now.replace(hour=16, minute=5, second=0, microsecond=0)
        if now >= target:
            target = target + datetime.timedelta(days=1)
        wait = (target - now).total_seconds()
        # 上限保护，避免极端等待
        time_sleep = min(wait, 3600)
        threading.Event().wait(time_sleep)
        # 到点后再确认（防止被截断的等待）
        if datetime.datetime.now().hour >= 16 and fetcher.is_trading_day():
            try:
                do_fetch()
            except Exception:
                pass
        # 等待到下一个整点再循环判断
        threading.Event().wait(600)


def ensure_today():
    """启动或访问时：若今天无数据(交易日)，自动补抓一次最新可得行情。"""
    dates = db.get_dates()
    today = datetime.date.today().strftime("%Y-%m-%d")
    if today in dates:
        return
    if fetcher.is_trading_day():
        try:
            do_fetch()
        except Exception:
            pass


def ensure_history():
    """启动/访问时：自动补回最近几个已收盘交易日的真实快照（若缺失）。

    历史回补走 push2his 历史接口，不受“是否已收盘”限制，可随时还原历史。
    今天的数据留给实时抓取(ensure_today/定时任务)，此处只补过去的交易日。
    """
    today = datetime.date.today()
    for i in range(1, 4):  # 最多补回前 3 个自然日（覆盖周末/假期）
        d = today - datetime.timedelta(days=i)
        if not fetcher.is_trading_day(d):
            continue
        ds = d.strftime("%Y-%m-%d")
        if ds in db.get_dates():
            continue
        try:
            fetcher.backfill_date(ds)
        except Exception:
            pass


_ensure_lock = threading.Lock()
_last_ensure = 0.0


def auto_ensure():
    """带节流的自动补数：60 秒内只跑一次，避免筛选请求反复触发抓取。"""
    global _last_ensure
    now = time.time()
    with _ensure_lock:
        if now - _last_ensure < 60:
            return
        _last_ensure = now
    try:
        ensure_today()
        ensure_history()
    except Exception:
        pass


# ---------------- HTTP 处理 ----------------
class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        q = urllib.parse.parse_qs(parsed.query)

        def qone(k):
            v = q.get(k) or [""]
            return (v[0] or "").strip()

        # 筛选参数：?date=YYYY-MM-DD&cats=沪深300,中证500&codes=510300,159919
        f_date = qone("date") or None
        f_cats = [x.strip() for x in qone("cats").split(",") if x.strip()]
        f_codes = [x.strip() for x in qone("codes").split(",") if x.strip()]

        if path in ("/", "/index.html"):
            try:
                with open(INDEX, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except Exception as e:
                self._send(500, str(e).encode("utf-8"))
            return
        if path == "/api/data":
            auto_ensure()
            payload = build_payload(f_date, f_cats, f_codes)
            payload["signal"] = signal_label(payload["summary"]["total_main_net_yi"])
            self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            return
        if path == "/api/refresh":
            res = do_fetch()
            payload = build_payload(f_date, f_cats, f_codes)
            payload["signal"] = signal_label(payload["summary"]["total_main_net_yi"])
            payload["fetch_result"] = res
            self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            return
        if path == "/api/options":
            self._send(200, json.dumps({
                "dates": db.get_dates(),
                "cats": db.get_cats(),
                "funds": db.get_funds(),
            }, ensure_ascii=False).encode("utf-8"))
            return
        if path == "/api/status":
            self._send(200, json.dumps({
                "last_fetch": db.get_meta("last_fetch", "—"),
                "last_date": db.get_meta("last_date", "—"),
                "news_ok": db.get_meta("news_ok", "1"),
            }, ensure_ascii=False).encode("utf-8"))
            return
        self._send(404, b"not found")

    def log_message(self, *args):
        pass


def main():
    db.init_db()
    ensure_today()
    ensure_history()
    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("0.0.0.0", PORT), Handler) as httpd:
        print("国家队A股日报 running on http://0.0.0.0:%d" % PORT)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
