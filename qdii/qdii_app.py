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

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
for p in (BASE, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

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
PORT = int(os.environ.get("QDII_PORT", "5001"))


def route_api(path, query=None):
    query = query or {}
    if path == "/api/qdii/status":
        return {"status": "ok", "db": C.QDII_DB_PATH,
                "pool_size": len(C.FUND_POOL), "now": _now()}
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
        meta = {s[0]: {"name": s[1], "group": s[2], "kind": s[3]}
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


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, body_bytes, content_type, code=200):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def do_GET(self):
        path = self.path.split("?")[0]
        # 解析查询串（表头排序用）：sort/psort=列键，dir/pdir=asc|desc
        query = {}
        if "?" in self.path:
            for kv in self.path.split("?", 1)[1].split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    query[k] = urllib.parse.unquote(v)
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
    """读取首页模板并注入「对端面板地址」（PEER_URL，见 national_team/app.py 同名函数）。"""
    with open(INDEX, "rb") as f:
        html = f.read().decode("utf-8", "replace")
    peer = (os.environ.get("PEER_URL") or "").strip()
    if peer:
        html = html.replace('<meta name="peer-url" content="">',
                            '<meta name="peer-url" content="%s">' % peer)
    return html.encode("utf-8")


def main():
    fetcher_pool.init_pool()
    httpd = socketserver.ThreadingTCPServer(("0.0.0.0", PORT), Handler)
    print("[qdii-app] listening on port %d (db=%s)" % (PORT, C.QDII_DB_PATH))
    httpd.serve_forever()


if __name__ == "__main__":
    main()
