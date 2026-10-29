"""combo_app.py — 单端口托管「国家队 + 海外QDII」两个面板（WorkBuddy 单应用双页）。

为什么需要它：
- 发布平台对一个项目只给一个在线应用 / 一个对外端口。
- 国家队(national_team/app.py) 与 QDII(qdii/qdii_app.py) 都 import 顶层同名模块 `db`，
  同进程加载会冲突（sys.modules['db'] 只能存一个）。
- 故用子进程隔离两个子系统，主进程监听 $PORT，按路径前缀反向代理：
    /qdii* 与 /api/qdii*  -> QDII 子进程（内部端口，已设 BASE_PATH=/qdii）
    其余                   -> 国家队子进程（内部端口）
两边用 PEER_URL 互跳：国家队跳 /qdii、QDII 跳 /。

仅依赖 Python 标准库。

诊断：/__diag 返回两个子进程的存活状态、退出码与最近 stderr 尾部，便于排查。
"""
import os
import sys
import time
import socket
import subprocess
import threading
import http.server
import socketserver
import http.client
import json

BASE = os.path.dirname(os.path.abspath(__file__))
# 对外端口（发布平台注入 $PORT）
PORT = int(os.environ.get("PORT", "5000"))
# 内部子进程端口：每次启动动态申请空闲端口，避免与历史部署遗留的孤儿子进程
# 占用的固定端口(5101/5102)冲突导致 QDII 子进程 bind 失败、长期「预热中」。
COMBO_VERSION = "v4-freeport"


def free_port():
    """向 OS 申请一个当前空闲的临时端口。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


NT_PORT = free_port()
QDII_PORT = free_port()

# 捕获子进程 stderr 的环形缓冲（diagnostic 用）
PROC_DIAG = {
    "nt": {"proc": None, "alive": False, "returncode": None, "stderr_tail": "", "last_proxy_err": ""},
    "qdii": {"proc": None, "alive": False, "returncode": None, "stderr_tail": "", "last_proxy_err": ""},
}


def _drain_stderr(label, proc):
    """后台线程：持续读子进程 stderr，保留最近 4KB。"""
    buf = []
    total = 0
    try:
        for raw in proc.stderr:
            text = raw.decode("utf-8", "replace")
            buf.append(text)
            total += len(text)
            # 仅保留最近约 4KB
            while buf and total - len(buf[0]) > 4096:
                total -= len(buf.pop(0))
    except Exception:
        pass
    PROC_DIAG[label]["stderr_tail"] = "".join(buf)[-4096:]


def spawn(label, script_rel, env_extra, port_key, port_val):
    """启动一个子系统子进程，注入所需环境变量与内部端口，并捕获 stderr。"""
    env = dict(os.environ)
    env.update(env_extra)
    env[port_key] = str(port_val)
    script = os.path.join(BASE, script_rel)
    proc = subprocess.Popen(
        [sys.executable, script],
        env=env,
        stderr=subprocess.PIPE,
    )
    PROC_DIAG[label]["proc"] = proc
    PROC_DIAG[label]["alive"] = True
    PROC_DIAG[label]["returncode"] = None
    t = threading.Thread(target=_drain_stderr, args=(label, proc), daemon=True)
    t.start()
    return proc


# 国家队子进程：根路径 / 。NT_AUTO_ENSURE=0 避免冷启动联网补数拖慢；
# 手动刷新(/api/refresh)仍会真抓，数据新鲜度由用户触发保证。
nt_proc = spawn(
    "nt", "national_team/app.py",
    {"NT_AUTO_ENSURE": "0", "PEER_URL": "/qdii"},
    "PORT", NT_PORT,
)
# QDII 子进程：挂到 /qdii 子路径（BASE_PATH=/qdii 让它正确处理 /qdii 与 /api/qdii）
qdii_proc = spawn(
    "qdii", "qdii/qdii_app.py",
    {"BASE_PATH": "/qdii", "PEER_URL": "/", "QDII_PORT": str(QDII_PORT)},
    "QDII_PORT", QDII_PORT,
)


def _refresh_diag(label):
    d = PROC_DIAG[label]
    p = d["proc"]
    if p is not None:
        d["alive"] = p.poll() is None
        d["returncode"] = p.returncode


WARMING = (
    b"<html><head><meta charset='utf-8'><title>\xe9\xa2\x84\xe7\x83\xad\xe4\xb8\xad</title>"
    b"<meta http-equiv='refresh' content='2'></head><body style='font-family:sans-serif;"
    b"text-align:center;margin-top:20%'><h2>\xf0\x9f\x94\x84 \xe6\x9c\x8d\xe5\x8a\xa1\xe9\xa2\x84"
    b"\xe7\x83\xad\xe4\xb8\xad\xe2\x80\xa6</h2><p>\xe4\xb8\xa4\xe4\xb8\xaa\xe9\x9d\xa2\xe6\x9d\xbf"
    b"\xe6\xad\xa3\xe5\x9c\xa8\xe5\x90\xaf\xe5\x8a\xa8\xef\xbc\x8c\xe7\xba\xa62\xe7\xa7\x92\xe5\x90"
    b"\x8e\xe8\x87\xaa\xe5\x8a\xa8\xe5\x88\xb7\xe6\x96\xb0\xe3\x80\x82</p></body></html>"
)


class Handler(http.server.BaseHTTPRequestHandler):
    def _pick_backend(self):
        p = self.path.split("?")[0]
        if p.startswith("/qdii") or p.startswith("/api/qdii"):
            return ("127.0.0.1", QDII_PORT), "qdii"
        return ("127.0.0.1", NT_PORT), "nt"

    def _warming(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(WARMING)))
        self.end_headers()
        try:
            self.wfile.write(WARMING)
        except Exception:
            pass

    def _proxy(self):
        (host, port), label = self._pick_backend()
        try:
            # 读取请求体（POST 触发刷新等场景需要转发给后端）
            body_req = None
            clen = self.headers.get("Content-Length")
            if clen:
                try:
                    body_req = self.rfile.read(int(clen))
                except Exception:
                    body_req = None
            # /api/refresh 会联网抓新浪+东财，耗时可能 >40s，超时放宽到 150s
            conn = http.client.HTTPConnection(host, port, timeout=150)
            hdrs = {}
            for k, v in self.headers.items():
                # 不转发 Host/Connection/Transfer-Encoding/Content-Length，让 http.client 自动补正确值
                if k.lower() in ("host", "connection", "transfer-encoding", "proxy-connection", "content-length"):
                    continue
                hdrs[k] = v
            conn.request(self.command, self.path, body=body_req, headers=hdrs)
            resp = conn.getresponse()
            body = resp.read()
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                lk = k.lower()
                if lk in ("transfer-encoding", "connection", "proxy-connection", "content-length"):
                    continue
                self.send_header(k, v)
            # 用真实长度兜底，避免分块/压缩导致的截断（不转发后端的 Content-Length，避免重复）
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)
            conn.close()
        except Exception as e:
            # 记录最近一次代理异常，便于 /__diag 定位
            PROC_DIAG[label]["last_proxy_err"] = "%s: %s" % (type(e).__name__, e)
            # 子进程尚未就绪或崩溃：返回 200 预热页，避免健康检查误判 + 端口始终可响应
            self._warming()

    def do_GET(self):
        if self.path.split("?")[0] == "/__diag":
            self._diag()
            return
        self._proxy()

    def do_POST(self):
        self._proxy()

    def _diag(self):
        _refresh_diag("nt")
        _refresh_diag("qdii")
        payload = {
            "version": COMBO_VERSION,
            "port": PORT,
            "nt_port": NT_PORT,
            "qdii_port": QDII_PORT,
            "nt": {k: PROC_DIAG["nt"][k] for k in ("alive", "returncode", "last_proxy_err")},
            "qdii": {k: PROC_DIAG["qdii"][k] for k in ("alive", "returncode", "last_proxy_err")},
            "nt_stderr": PROC_DIAG["nt"]["stderr_tail"][-1500:],
            "qdii_stderr": PROC_DIAG["qdii"]["stderr_tail"][-1500:],
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    # 主进程立即监听对外端口，不等子进程就绪（避免发布平台 60s 端口检查超时）。
    # 子进程在后台预热，代理在连接失败时返回「预热中」200 页，健康检查即视为就绪。
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("0.0.0.0", PORT), Handler) as httpd:
        print("combo listening on %d (nt:%d qdii:%d)" % (PORT, NT_PORT, QDII_PORT))
        httpd.serve_forever()


if __name__ == "__main__":
    main()
