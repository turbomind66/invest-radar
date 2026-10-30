"""qdii/qdii_app.py — QDII 定投决策面板 Web 服务（零依赖，标准库 http.server）。

与 national_team/app.py 同源风格：只展示，不抓取（抓取交给 qdii-cron）。
启动即初始化基金池种子（建库 + 写入 FUND_POOL）。
"""
import os
import sys
import json
import http.server
import socketserver
import datetime
import urllib.parse
import threading

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
# 把【本模块所在目录】放在 sys.path 最前，保证 `import constants/db/...`
# 命中的是本目录下的那一份，而不是部署根目录下的同名副本。
#
# 顺序很重要：ROOT（部署根）必须放在 BASE **之后**插入 ——
# 若 ROOT 先插到 index 0，则 ROOT 会排在 BASE 前面，
# 于是在部署根目录存在同名模块时（沙箱里确实有一份旧副本），
# `import constants` 会加载到那一份，表现为「部署了新代码、线上却跑着旧行为」。
for p in (ROOT, BASE):
    if p not in sys.path:
        sys.path.insert(0, p)
# 再次确保 BASE 在最前（上面两次 insert 后 BASE 应已是 index 0，此处兜底）
if sys.path and sys.path[0] != BASE:
    sys.path.remove(BASE)
    sys.path.insert(0, BASE)

import db
import constants as C
import fetcher_pool
import fetcher_limit
import fetcher_fee
import fetcher_signal
import fetcher_premium
import tracking_error
import board
import strategy

TEMPLATES = os.path.join(BASE, "templates")
INDEX = os.path.join(TEMPLATES, "index.html")
# 空字符串也退回默认：WorkBuddy / NPM 有时会注入 PORT= 空值，int("") 会直接
# ValueError 让子进程起不来（combo_app 那边就表现为「预热中」永不结束）。
PORT = int(os.environ.get("QDII_PORT") or "5001")
# 版本指纹：/api/qdii/status 里带回，配合 sig/codes 让部署后一眼辨新/旧代码。
APP_VERSION = "v2.6.7-qdii"
# 站点路径前缀：域名反代把本面板挂在子路径（如 /qdii）时，若 Nginx/NPM 保留前缀不剥，
# 就在这里声明（BASE_PATH=/qdii），服务端匹配路由前先剥掉。默认空 = 行为与之前完全一致。
BASE_PATH = (os.environ.get("BASE_PATH") or "").strip().rstrip("/")


def strip_base(path):
    """剥掉 BASE_PATH 前缀（/qdii/api/qdii/status -> /api/qdii/status）。"""
    if BASE_PATH and (path == BASE_PATH or path.startswith(BASE_PATH + "/")):
        return path[len(BASE_PATH):] or "/"
    return path


def route_api(path, query=None):
    query = query or {}
    if path == "/api/qdii/status":
        return {"status": "ok", "db": C.QDII_DB_PATH,
                "pool_size": len(C.FUND_POOL), "now": _now(),
                "app_version": APP_VERSION,
                # 代码版本指纹：排查「部署后线上跑的还是旧代码」时一眼可辨。
                # sig = MARKET_SIGNALS 首行长度（加了交易所代码字段后为 5）。
                "sig": len(C.MARKET_SIGNALS[0]) if C.MARKET_SIGNALS else 0,
                # ⚠️ 2026-10-30 修：旧写法 `[s[4] for s in MARKET_SIGNALS] if ...
                # and len(MARKET_SIGNALS[0]) > 4` 只看第 0 行长度，混用 4/5 元组时
                # 后面的 s[4] 直接 IndexError → status 500。而 status 恰好是
                # gateway/combo 健康检查轮询的接口，本身挂掉时页面就会一直
                # 停在「预热中」、真实错误被吞掉。改成按每行长度取值。
                "codes": [(s[4] if len(s) > 4 else "") for s in (C.MARKET_SIGNALS or [])]}
    if path == "/api/qdii/funds":
        return {"funds": db.get_fund_pool()}
    if path == "/api/qdii/limit":
        return {"rows": fetcher_limit.get_latest_limit_status()}
    if path == "/api/qdii/fee":
        return {"rows": fetcher_fee.get_platform_fee()}
    if path == "/api/qdii/board":
        # 页面主口径：把「申购状态+限额」与「多平台费率」合并成一只基金一个块
        # （上面 limit/fee 两个接口保留为原始数据，便于排障与追溯）
        # 排序参数：sort/psort = 排序列键，dir/pdir = desc
        #   sort  基金级 → 改变基金块先后
        #   psort 平台级 → 只改变同一只基金内部各平台行的先后，基金先后不变
        rows = board.get_fund_board(
            sort=query.get("sort") or None,
            desc=(query.get("dir") == "desc"),
            psort=query.get("psort") or None,
            pdesc=(query.get("pdir") == "desc"))
        return {"rows": rows, "summary": board.board_summary(rows),
                "sort": {"fund": query.get("sort") or None,
                         "fund_desc": query.get("dir") == "desc",
                         "plat": query.get("psort") or None,
                         "plat_desc": query.get("pdir") == "desc"}}
    if path == "/api/qdii/signal":
        # meta/groups 来自 constants.MARKET_SIGNALS：分组（美股/科技/亚太/欧洲/利率）
        # 与类型标签（指数/期货/场内ETF/美股ETF·代理…）由后端统一下发，
        # 前端不硬编码口径，将来加新市场只改常量。
        # 第 5 字段是交易所代码（可选）：用 len 守卫兼容 4 元组的旧版常量，
        # 避免常量与本模块版本不一致时 s[4] 抛 IndexError 把整个连接打断
        # （表现是接口无响应、页面一直「预热中」，而非 500）。
        meta = {s[0]: {"name": s[1], "group": s[2], "kind": s[3],
                       "code": (s[4] if len(s) > 4 else "")}
                for s in C.MARKET_SIGNALS}
        return {"rows": fetcher_signal.get_latest_us_signal(),
                "meta": meta, "groups": C.MARKET_GROUPS}
    if path == "/api/qdii/premium":
        return {"rows": fetcher_premium.get_latest_premium()}
    if path == "/api/qdii/tracking":
        return {"rows": tracking_error.get_tracking_error()}
    if path == "/api/qdii/decision":
        # 主口径：按大类（标普500/纳斯达克100/日经225/德国DAX）
        return {"rows": strategy.get_latest_category_decisions()}
    if path == "/api/qdii/decision_fund":
        # 逐基金明细（追溯用，页面不展示）
        return {"rows": strategy.get_latest_decision()}
    return None


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


# ---- 服务端手动刷新（后台线程 + 状态查询）----
# 单应用/在线部署没有独立 cron，QDII 只有打包时的静态快照；
# 让用户点「立即刷新 / 刷新当日行情」时能真触发 signal(美股信号+决策) +
# premium(场内溢价+决策) 抓取。境外源(CBOE VIX/CNBC 美债)在此环境可能超时，
# 故用后台线程异步执行、前端轮询状态，单个请求不会长连接等待。
# 各抓取源彼此独立 try/except（见 fetcher_signal），单源失败不影响其余，
# 且写库仅针对「当日」，不会删除历史快照。
_REFRESH_LOCK = threading.Lock()
_REFRESH = {"running": False, "start": None, "done": None, "result": None}


def _run_refresh_bg():
    res = {"signal": None, "premium": None}
    try:
        import qdii_scheduler as sched
        try:
            sched.run_signal()
            res["signal"] = "ok"
        except Exception as e:
            res["signal"] = "error: %s" % e
        try:
            sched.run_premium()
            res["premium"] = "ok"
        except Exception as e:
            res["premium"] = "error: %s" % e
    except Exception as e:
        res["fatal"] = str(e)
    finally:
        with _REFRESH_LOCK:
            _REFRESH["running"] = False
            _REFRESH["done"] = _now()
            _REFRESH["result"] = res


def trigger_refresh():
    with _REFRESH_LOCK:
        if _REFRESH["running"]:
            return {"status": "running", "start": _REFRESH["start"]}
        _REFRESH["running"] = True
        _REFRESH["start"] = _now()
        _REFRESH["done"] = None
        _REFRESH["result"] = None
        threading.Thread(target=_run_refresh_bg, daemon=True).start()
        return {"status": "started", "start": _REFRESH["start"]}


def refresh_status():
    with _REFRESH_LOCK:
        return {"running": _REFRESH["running"], "start": _REFRESH["start"],
                "done": _REFRESH["done"], "result": _REFRESH["result"]}


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, body_bytes, content_type, code=200):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def do_GET(self):
        # 兜底：任何未捕获异常都返回 500 JSON，而不是让连接无响应断开
        # （无响应会让上层反向代理判定「子进程未就绪」，页面一直停在「预热中」，
        #  真实报错被吞掉、极难排查）。
        try:
            self._do_GET()
        except Exception as e:  # noqa: BLE001
            try:
                self._send(json.dumps({"error": "internal", "type": type(e).__name__,
                                        "msg": str(e)[:300]}, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8", 500)
            except Exception:
                pass

    def _do_GET(self):
        path = strip_base(self.path.split("?")[0])
        # 解析查询串（表头排序用）：sort/psort=列键，dir/pdir=asc|desc
        query = {}
        if "?" in self.path:
            for kv in self.path.split("?", 1)[1].split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    query[k] = urllib.parse.unquote(v)
        if path == "/api/qdii/refresh":
            self._send(json.dumps(trigger_refresh(), ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
            return
        if path == "/api/qdii/refresh_status":
            self._send(json.dumps(refresh_status(), ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
            return
        if path.startswith("/api/"):
            data = route_api(path, query)
            if data is None:
                self._send(json.dumps({"error": "not found"},
                          ensure_ascii=False).encode("utf-8"),
                          "application/json; charset=utf-8", 404)
            else:
                self._send(json.dumps(data, ensure_ascii=False).encode("utf-8"),
                          "application/json; charset=utf-8")
        elif path in ("/", "/index.html"):
            try:
                self._send(render_index(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(json.dumps({"error": "frontend not built"},
                          ensure_ascii=False).encode("utf-8"),
                          "application/json; charset=utf-8", 404)
        else:
            self._send(json.dumps({"error": "not found"},
                      ensure_ascii=False).encode("utf-8"),
                      "application/json; charset=utf-8", 404)

    def log_message(self, *args):  # 静默常规日志
        pass


def render_index():
    """读取首页模板并注入「对端面板地址」（PEER_URL）与共享刷新逻辑。

    PEER_URL 显式指定时用它（域名反代场景：两边端口都是 443，端口推断会跳成死链）；
    未配置则留空，前端回退到「本端口 -1」的推断（同机不同端口部署时仍然正确）。
    common/static_refresh.js 内联注入，保证服务端模式下前端函数（如 refreshToday）也有定义。
    """
    with open(INDEX, "rb") as f:
        html = f.read().decode("utf-8", "replace")
    peer = (os.environ.get("PEER_URL") or "").strip()
    if peer:
        html = html.replace('<meta name="peer-url" content="">',
                            '<meta name="peer-url" content="%s">' % peer)
    try:
        with open(os.path.join(ROOT, "common", "static_refresh.js"), encoding="utf-8") as f:
            common_js = f.read()
    except Exception:
        common_js = ""
    if common_js:
        html = html.replace("</head>", "<script>%s</script>\n</head>" % common_js, 1)
    return html.encode("utf-8")


def main():
    fetcher_pool.init_pool()
    # 监听地址：默认 0.0.0.0（Docker 容器内网 / 裸机部署对外服务）；
    # combo_app 作为 WorkBuddy 单应用启动子进程时应注入 QDII_BIND=127.0.0.1，
    # 让内部端口只在本机回环可达（否则「内部端口」会跟着父进程一起在宿主 LAN 上
    # 打开、绕过网关的路径路由，Windows 上还会触发防火墙弹窗）。
    bind = (os.environ.get("QDII_BIND") or "0.0.0.0").strip() or "0.0.0.0"
    # 与 gateway.py / national_team/app.py 保持一致：允许快速重启复用 TIME_WAIT
    # 端口，工作线程随主进程退出（否则 SIGTERM 会被 handler 里最长的 150s 上游读拖住）。
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    socketserver.ThreadingTCPServer.daemon_threads = True
    with socketserver.ThreadingTCPServer((bind, PORT), Handler) as httpd:
        print("[qdii-app] listening on %s:%d (db=%s)" % (bind, PORT, C.QDII_DB_PATH))
        httpd.serve_forever()


if __name__ == "__main__":
    main()
