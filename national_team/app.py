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

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
# 把【本模块所在目录】放在 sys.path 最前，保证 `import db/fetcher/intraday`
# 命中的是本目录下的那一份，而不是部署根目录下的同名副本。
#
# 顺序很重要：ROOT（部署根）必须放在 BASE **之后**插入 —— 若 ROOT 先插到 index 0，
# 则 ROOT 排在 BASE 前面，部署根目录存在同名模块时（沙箱里确实有一份旧副本），
# import 会加载到那一份，表现为「部署了新代码、线上却跑着旧行为」。
for _p in (ROOT, BASE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
if sys.path and sys.path[0] != BASE:
    sys.path.remove(BASE)
    sys.path.insert(0, BASE)

import db  # noqa: E402
import fetcher  # noqa: E402
import intraday  # noqa: E402

INDEX = os.path.join(BASE, "templates", "index.html")
PORT = int(os.environ.get("PORT") or "5000")
# 版本指纹：与 qdii 侧的 sig/codes 同用途，部署后 /api/status 里能一眼辨新旧代码。
# 手动同步更新，或用 git rev-parse 生成一次写死。
APP_VERSION = "v2.6.2-nt"
# 站点路径前缀：域名反代把本面板挂在子路径（如 /nt）时，若 Nginx/NPM 保留前缀不剥，
# 就在这里声明（BASE_PATH=/nt），服务端匹配路由前先剥掉。默认空 = 行为与之前完全一致。
BASE_PATH = (os.environ.get("BASE_PATH") or "").strip().rstrip("/")


def strip_base(path):
    """剥掉 BASE_PATH 前缀（/nt/api/data -> /api/data）；未配置或不匹配时原样返回。"""
    if BASE_PATH and (path == BASE_PATH or path.startswith(BASE_PATH + "/")):
        return path[len(BASE_PATH):] or "/"
    return path


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
    # 兜底新闻：所选日期没有新闻时取最近有新闻的那天（避免面板整块空白）
    news_fallback = {"date": None, "items": []}
    if not news:
        fd = db.get_latest_news_date(path)
        if fd:
            news_fallback = {"date": fd, "items": db.get_news_by_date(fd, path)}
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
        # 疑似护盘/压盘模块（独立口径，分时来源与主流程不同）
        "intraday": intraday.build_view(sel_date, path) if sel_date else None,
        "news_ok": news_ok,
        "mainnet_ok": mainnet_ok,
        "partial_main": partial_main,
        "etf": etf,
        "news": news,
        "news_fallback": news_fallback,
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
_scheduler_last_run_date = None  # 记住最近一次自动抓的日期，防 16:00 之后每小时重抓


def scheduler_loop():
    """进程内定时器：北京时间 16:05 触发一次当日抓取。

    ⚠️ 2026-10-30 修：旧实现 wait 上限 3600s，然后按「datetime.now().hour >= 16」
    判定，等于 16:05 抓到之后、17:05/18:05/…/23:05 每次醒来都判定通过 → 一天里
    重复 24 页东财 crawl + save_etf_daily DELETE/INSERT 至多半晚，
    配合 fetcher 的部分覆盖 bug 会反复洗数据。现在按「日期」判重，
    同一天只跑一次；抓取窗口也收窄到 [16:05, 17:00)。
    """
    global _scheduler_last_run_date
    while True:
        now = datetime.datetime.now()
        target = now.replace(hour=16, minute=5, second=0, microsecond=0)
        if now >= target + datetime.timedelta(hours=1):
            target = target + datetime.timedelta(days=1)
        wait = (target - now).total_seconds()
        # 上限保护，避免极端等待；到点重算
        threading.Event().wait(max(60, min(wait, 3600)))
        now = datetime.datetime.now()
        today = now.strftime("%Y-%m-%d")
        # 只在窗口内跑，且当天没跑过才跑
        if not (16 <= now.hour < 17):
            continue
        if _scheduler_last_run_date == today:
            continue
        if not fetcher.is_trading_day():
            continue
        _scheduler_last_run_date = today
        try:
            do_fetch()
        except Exception as e:  # noqa: BLE001
            print("[scheduler_loop] 抓取失败：%s: %s" % (type(e).__name__, e))


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


def auto_enabled():
    """是否允许本实例联网抓取。

    容器化部署时 web 与 cron 是两个容器：若 web 也抓，会与定时任务重复请求、
    并争抢同一个 SQLite 写锁。此时给 web 设 NT_AUTO_ENSURE=0，抓取交给 cron。
    默认 1（本地 / 单容器部署行为不变）。
    """
    return (os.environ.get("NT_AUTO_ENSURE") or "1").strip().lower() not in (
        "0", "false", "no", "off")


def auto_ensure():
    """带节流的自动补数：60 秒内只跑一次，避免筛选请求反复触发抓取。"""
    if not auto_enabled():
        return
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
    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8",
              head_only=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def do_GET(self):
        # 兜底：任意未捕获异常都返回 500 JSON，而不是让 socketserver 直接
        # handle_error 断连。断连在 Docker gateway 里会被包成 502「无法连接上游」、
        # WorkBuddy combo_app 里会被吞成 200「服务预热中」→ 上层健康检查永远绿、
        # 真实错误被完全吞掉（v2.6.1 修 QDII 时踩过一模一样的坑）。
        try:
            self._do_GET(head_only=False)
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            try:
                self._send(500, json.dumps(
                    {"error": "internal", "type": type(e).__name__,
                     "msg": str(e)[:300]}, ensure_ascii=False).encode("utf-8"))
            except Exception:
                pass

    def do_HEAD(self):
        # HEAD 走同一路由，但只发头不发体，让 Docker healthcheck / 外部监控
        # 可以 HEAD /api/status 而不是必须 GET（旧实现直接 501 Unsupported method）。
        try:
            self._do_GET(head_only=True)
        except Exception:
            try:
                self.send_response(500)
                self.send_header("Content-Length", "0")
                self.end_headers()
            except Exception:
                pass

    def _do_GET(self, head_only=False):
        parsed = urllib.parse.urlparse(self.path)
        path = strip_base(parsed.path)
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
                self._send(200, render_index(), "text/html; charset=utf-8",
                           head_only=head_only)
            except Exception as e:
                self._send(500, str(e).encode("utf-8"), head_only=head_only)
            return
        if path == "/api/data":
            auto_ensure()
            payload = build_payload(f_date, f_cats, f_codes)
            payload["signal"] = signal_label(payload["summary"]["total_main_net_yi"])
            self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       head_only=head_only)
            return
        if path == "/api/refresh":
            # 手动刷新仍然允许，但要跟 cron / 上一次点击串行：拿不到锁说明
            # 已有同步在跑，直接返回 409 提示前端「稍后再试」，不再两个进程
            # 抢写同一个 SQLite、也不再把当天已归档的数据洗掉。
            from common.lock import FileLock
            lock_path = os.environ.get("NT_REFRESH_LOCK") or os.path.join(
                os.path.dirname(db.DB_PATH), ".nt-refresh.lock")
            lk = FileLock(lock_path)
            if not lk.acquire():
                body = json.dumps(
                    {"ok": False, "reason": "已有抓取在运行，请稍后重试"},
                    ensure_ascii=False).encode("utf-8")
                self._send(409, body, head_only=head_only)
                return
            try:
                res = do_fetch()
            finally:
                lk.release()
            payload = build_payload(f_date, f_cats, f_codes)
            payload["signal"] = signal_label(payload["summary"]["total_main_net_yi"])
            payload["fetch_result"] = res
            self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       head_only=head_only)
            return
        if path == "/api/options":
            self._send(200, json.dumps({
                "dates": db.get_dates(),
                "cats": db.get_cats(),
                "funds": db.get_funds(),
            }, ensure_ascii=False).encode("utf-8"), head_only=head_only)
            return
        if path == "/api/status":
            self._send(200, json.dumps({
                "last_fetch": db.get_meta("last_fetch", "—"),
                "last_date": db.get_meta("last_date", "—"),
                "news_ok": db.get_meta("news_ok", "1"),
                # 版本指纹：与 qdii 侧一致，部署后一眼可辨线上跑的是新/旧代码
                # （见 v2.6.1 修「部署后线上跑旧代码」根因）
                "app_version": APP_VERSION,
            }, ensure_ascii=False).encode("utf-8"), head_only=head_only)
            return
        self._send(404, b"not found", head_only=head_only)

    def log_message(self, *args):
        pass


def render_index():
    """读取首页模板并注入「对端面板地址」与共享刷新逻辑。

    PEER_URL 显式指定时用它（域名反代场景：两边端口都是 443，端口推断会跳成死链）；
    未配置则留空，前端回退到「本端口 ±1」的推断（同机不同端口部署时仍然正确）。
    common/static_refresh.js 内联注入，保证服务端模式下前端函数（如 refreshToday）也有定义。
    """
    with open(INDEX, "rb") as f:
        html = f.read().decode("utf-8", "replace")
    peer = (os.environ.get("PEER_URL") or "").strip()
    if peer:
        html = html.replace('<meta name="peer-url" content="">',
                            '<meta name="peer-url" content="%s">' % peer)
    common_js = ""
    try:
        with open(os.path.join(ROOT, "common", "static_refresh.js"), encoding="utf-8") as f:
            common_js = f.read()
    except Exception:
        common_js = ""
    if common_js:
        html = html.replace("</head>", "<script>%s</script>\n</head>" % common_js, 1)
    return html.encode("utf-8")


def main():
    db.init_db()
    if auto_enabled():
        ensure_today()
        ensure_history()
        t = threading.Thread(target=scheduler_loop, daemon=True)
        t.start()
    else:
        print("NT_AUTO_ENSURE=0：本实例只提供展示，抓取由定时任务容器负责")
    # 监听地址：默认 0.0.0.0（Docker 容器内网 / 裸机部署对外服务）；
    # combo_app 作为 WorkBuddy 单应用启动子进程时会注入 NT_BIND=127.0.0.1，
    # 让 5000 只在本机回环可达（父进程通过网关对外，内部端口不落到宿主 LAN）。
    bind = (os.environ.get("NT_BIND") or "0.0.0.0").strip() or "0.0.0.0"
    socketserver.TCPServer.allow_reuse_address = True
    # 工作线程随主进程退出，避免 SIGTERM 时被某个最长 150s 的 /api/refresh 卡住
    socketserver.ThreadingTCPServer.daemon_threads = True
    with socketserver.ThreadingTCPServer((bind, PORT), Handler) as httpd:
        print("国家队A股日报 running on http://%s:%d" % (bind, PORT))
        httpd.serve_forever()


if __name__ == "__main__":
    main()
