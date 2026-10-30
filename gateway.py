"""gateway.py —— 单端口入口（零依赖反向代理）。

两个面板是同一个镜像里跑起来的两个独立进程，各占一个内部端口；对外只需要一个端口：

    http://<host>:38881/         ->  web:5000        国家队 A 股看板
    http://<host>:38881/nt/...   ->  web:5000        同上（前缀剥掉），让页头选项卡地址对称
    http://<host>:38881/qdii/... ->  qdii-web:5001   QDII 面板（前缀剥掉）

这样无论直连还是用 Nginx Proxy Manager 反代，**都只需要认一个端口**，
不必再配 Custom Location，也不会出现「前缀没剥干净 → 请求打到另一个面板」的串台。

注意：页面里的接口请求会按「当前访问路径」自动拼前缀（在 /qdii/ 下就请求 /qdii/api/...），
网关把前缀剥掉再交给后端，所以后端不需要任何额外配置。

环境变量：
    GATEWAY_PORT   容器内监听端口，默认 38881
    NT_UPSTREAM    国家队上游，默认 http://web:5000（单容器模式由 entrypoint 改成 127.0.0.1）
    QDII_UPSTREAM  QDII 上游，默认 http://qdii-web:5001
    QDII_PREFIX    QDII 路径前缀，默认 /qdii
    NT_PREFIX      国家队路径前缀（别名），默认 /nt
    GATEWAY_TIMEOUT    上游读超时秒数，默认 180（覆盖 /api/refresh 最坏耗时）
    GATEWAY_SOCK_TIMEOUT  单 socket 空闲超时秒数，默认 60（防 keep-alive 泄漏线程）

命令行：
    python gateway.py --port 48881     # 裸机部署时对外只开这一个端口
"""
import http.server
import os
import socketserver
import sys
import urllib.error
import urllib.parse
import urllib.request


def _resolve_port(argv) -> int:
    """监听端口优先级：--port > GATEWAY_PORT > 38881。"""
    value = ""
    for i, arg in enumerate(argv):
        if arg == "--port" and i + 1 < len(argv):
            value = argv[i + 1]
        elif arg.startswith("--port="):
            value = arg.split("=", 1)[1]
    value = (value or os.environ.get("GATEWAY_PORT") or "38881").strip()
    return int(value)


PORT = _resolve_port(sys.argv[1:])
NT_UPSTREAM = (os.environ.get("NT_UPSTREAM") or "http://web:5000").rstrip("/")
QDII_UPSTREAM = (os.environ.get("QDII_UPSTREAM") or "http://qdii-web:5001").rstrip("/")
QDII_PREFIX = "/" + (os.environ.get("QDII_PREFIX") or "qdii").strip("/")
NT_PREFIX = "/" + (os.environ.get("NT_PREFIX") or "nt").strip("/")
# 上游超时：旧默认 30s 短于 /api/refresh 最坏耗时（24 页 × 3 重试 × 20s），
# 网关在浏览器还没收到响应前就 502，用户重试 → 多次并发抓取 + 抢 SQLite 写锁。
# 改成 180s 兜住 refresh，同时给空闲 keep-alive 加 60s socket timeout（见 Handler）。
TIMEOUT = float(os.environ.get("GATEWAY_TIMEOUT", "180"))
SOCKET_TIMEOUT = float(os.environ.get("GATEWAY_SOCK_TIMEOUT", "60"))

# 容器内访问上游，显式禁用代理（宿主机环境变量可能带 HTTP_PROXY）
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 逐跳头不能透传（RFC 7230）
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "accept-encoding",
}


def resolve(path: str) -> str:
    """按路径前缀挑选上游，并把前缀剥掉。/qdii/api/x -> <qdii 上游>/api/x"""
    if path == QDII_PREFIX or path.startswith(QDII_PREFIX + "/"):
        return QDII_UPSTREAM + (path[len(QDII_PREFIX):] or "/")
    if path == NT_PREFIX or path.startswith(NT_PREFIX + "/"):
        return NT_UPSTREAM + (path[len(NT_PREFIX):] or "/")
    return NT_UPSTREAM + path


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"   # 配合显式 Content-Length，浏览器可复用连接
    # socket 空闲超时：不设的话 HTTP/1.1 keep-alive 会把每个空闲浏览器连接
    # 长期挂在一个 handler 线程里，96M/0.25 核的 gateway 容器很容易线程/内存耗尽。
    timeout = SOCKET_TIMEOUT

    def _proxy(self, method: str):
        # _headers_sent：如果已经在往 wfile 写字节，异常时不能再调 _send 发 502
        # （否则 send_response 触发 RuntimeError，socket 半路断，浏览器看到烂响应）
        self._headers_sent = False
        parsed = urllib.parse.urlsplit(self.path)
        target = resolve(parsed.path)
        if parsed.query:
            target += "?" + parsed.query

        body = None
        length = self.headers.get("Content-Length")
        if length and method in ("POST", "PUT", "PATCH"):
            try:
                body = self.rfile.read(int(length))
            except ValueError:
                body = None

        req = urllib.request.Request(target, data=body, method=method)
        for key, value in self.headers.items():
            if key.lower() not in HOP_BY_HOP:
                req.add_header(key, value)

        try:
            with OPENER.open(req, timeout=TIMEOUT) as resp:
                self._send(resp.status, resp.read(), resp.headers)
        except urllib.error.HTTPError as exc:      # 后端的 404/500 原样回传
            self._send(exc.code, exc.read(), exc.headers)
        except Exception as exc:                    # 连不上上游
            if getattr(self, "_headers_sent", False):
                # 已经开始发响应了 → 只能断连，别二次 send_response 抛 RuntimeError
                return
            msg = ("[gateway] 无法连接上游 %s\n%s\n"
                   "→ 请确认对应容器已启动（docker compose ps）\n" % (target, exc))
            self._send(502, msg.encode("utf-8"), None)

    def _send(self, code: int, data: bytes, headers=None):
        """回传响应：状态码 + 除 hop-by-hop 外全部头 + 显式 Content-Length。

        旧实现只透传 Content-Type，会把上游的 Location / Set-Cookie / ETag /
        Last-Modified / Cache-Control 全部吞掉，任何后端重定向 / auth cookie /
        条件请求都过不了网关。现在按上游原样转发，再剥掉 hop-by-hop 与 Host 类。
        """
        self.send_response(code)
        self._headers_sent = True
        sent_ctype = False
        if headers is not None:
            try:
                items = headers.items()
            except AttributeError:
                items = []
            for key, value in items:
                lk = key.lower()
                if lk in HOP_BY_HOP or lk == "content-length":
                    continue
                if lk == "content-type":
                    sent_ctype = True
                self.send_header(key, value)
        if not sent_ctype:
            self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_GET(self):
        self._proxy("GET")

    def do_HEAD(self):
        self._proxy("HEAD")

    def do_POST(self):
        self._proxy("POST")

    def log_message(self, fmt, *args):
        # 只记录非 2xx/3xx，避免刷屏
        pass


def main():
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    socketserver.ThreadingTCPServer.daemon_threads = True
    with socketserver.ThreadingTCPServer(("0.0.0.0", PORT), Handler) as httpd:
        print("[gateway] listen 0.0.0.0:%d   /  -> %s   %s/ -> %s   %s/ -> %s"
              % (PORT, NT_UPSTREAM, QDII_PREFIX, QDII_UPSTREAM, NT_PREFIX, NT_UPSTREAM),
              flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
