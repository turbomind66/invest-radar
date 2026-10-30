"""combo_app.py — 单端口托管「国家队 + 海外QDII」两个面板（WorkBuddy 单应用双页）。

为什么需要它：
- 发布平台对一个项目只给一个在线应用 / 一个对外端口。
- 国家队(national_team/app.py) 与 QDII(qdii/qdii_app.py) 都 import 顶层同名模块 `db`，
  同进程加载会冲突（sys.modules['db'] 只能存一个）。
- 故用子进程隔离两个子系统，主进程监听 $PORT，按路径前缀反向代理：
    /qdii 与 /qdii/* 与 /api/qdii/*  -> QDII 子进程（内部端口，已设 BASE_PATH=/qdii）
    其余                               -> 国家队子进程（内部端口）
两边用 PEER_URL 互跳：国家队跳 /qdii、QDII 跳 /。

仅依赖 Python 标准库。

诊断：/__diag 返回两个子进程的存活状态、退出码与最近 stderr 尾部，便于排查。

2026-10-30 加固（WorkBuddy 部署形态踩过的四类静默坏）：
- 子进程 spawn 从模块顶层挪进 main()，import 不再有两个副作用（起服务 + 抢端口）。
- 内部端口强制 127.0.0.1 绑定：通过给子进程注入 NT_BIND/QDII_BIND 环境变量，
  避免「内部端口」跟父进程一起在宿主 LAN 上开（Windows 每次弹防火墙、
  也绕过 combo_app 的 /qdii 路径路由）。
- 清理从父进程继承下来的 BASE_PATH：QDII 子进程需要 BASE_PATH=/qdii，
  但国家队子进程如果继承了同一个变量会把 /api/data 剥成 /data 而 404。
- free_port() 竞态保护：NT_PORT == QDII_PORT 时重分配；两个端口都 try-bind
  确认可用再交给子进程。
- _pick_backend 的 `/qdii` 前缀严格匹配：`startswith("/qdii")` 会连带命中
  `/qdii-x`；改成 `path == "/qdii"` 或 `startswith("/qdii/")`。
- 代理异常不再一律吞成 200「预热中」：区分「子进程还没起」与「子进程崩了」，
  后者返回 503 + Retry-After + 诊断信息，让平台健康检查真能感知到失败。
- 加 atexit / SIGTERM 处理器，父进程退出时把子进程一并 terminate，
  避免留下孤儿子进程占着 SQLite 锁 / 端口 / 内存。
"""
import os
import sys
import atexit
import signal
import socket
import subprocess
import threading
import http.server
import socketserver
import http.client
import json

BASE = os.path.dirname(os.path.abspath(__file__))
# 对外端口（发布平台注入 $PORT）；WorkBuddy 偶发注入空值也要兜底
PORT = int(os.environ.get("PORT") or "5000")
# 内部子进程端口：每次启动动态申请空闲端口，避免与历史部署遗留的孤儿子进程
# 冲突；spawn 已挪进 main()，import 本模块不再抢端口。
COMBO_VERSION = "v5-safe-spawn"

PROC_DIAG = {
    "nt": {"proc": None, "alive": False, "returncode": None,
           "stderr_tail": "", "last_proxy_err": "", "port": None},
    "qdii": {"proc": None, "alive": False, "returncode": None,
             "stderr_tail": "", "last_proxy_err": "", "port": None},
}
_CHILDREN_LOCK = threading.Lock()


def _try_bind(host, port):
    """确认一个端口真的能 bind 到（比 free_port 的 TOCTOU 更严一点）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        return s.getsockname()[1]
    except OSError:
        return None
    finally:
        try:
            s.close()
        except OSError:
            pass


def free_port(exclude=(), host="127.0.0.1"):
    """向 OS 申请一个当前空闲的临时端口；避开 exclude 里的端口，最多试 10 次。"""
    exclude = set(exclude)
    for _ in range(10):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((host, 0))
            p = s.getsockname()[1]
        finally:
            s.close()
        if p in exclude:
            continue
        # 二次确认还能再 bind 上（缩小 TOCTOU 窗口；不是绝对防御，但足以
        # 避开另一个子进程刚好在同一毫秒抢到端口的典型场景）
        if _try_bind(host, p) == p:
            return p
    raise RuntimeError("连续 10 次都拿不到空闲端口，请检查是否有孤儿进程占端口")


def _drain_stderr(label, proc):
    """后台线程：持续读子进程 stderr，保留最近 4KB。"""
    buf = []
    total = 0
    try:
        for raw in proc.stderr:
            text = raw.decode("utf-8", "replace")
            buf.append(text)
            total += len(text)
            while buf and total - len(buf[0]) > 4096:
                total -= len(buf.pop(0))
    except Exception:
        pass
    PROC_DIAG[label]["stderr_tail"] = "".join(buf)[-4096:]


def spawn(label, script_rel, env_extra, port_key, port_val):
    """启动一个子系统子进程，注入所需环境变量与内部端口，并捕获 stderr。"""
    env = dict(os.environ)
    # **关键**：清掉可能干扰 import 解析的环境变量。
    #
    # 两个子系统都 import 顶层同名模块（db/constants/...）。
    # 沙箱里 sys.path 会被平台插入额外路径（实测子进程 sys.path 为
    # ['/', '/workspace', '/workspace/qdii', ...]）—— '/workspace' 排在
    # '/workspace/qdii' 之前，而部署根目录下可能存在另一份（旧的）同名副本，
    # 于是 import 命中旧副本，表现为「部署了新代码、线上却跑着旧行为」。
    #
    # 真正的修正在各子系统模块内部（qdii/*.py 把 BASE 排在 ROOT 之前），
    # 这里只负责清理环境变量，避免平台注入的 PYTHONSAFEPATH（会移除脚本目录）
    # 与用户 site-packages 干扰 import。
    sub_dir = os.path.dirname(os.path.join(BASE, script_rel))
    env.pop("PYTHONSAFEPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONNOUSERSITE"] = "1"
    # 追加而不是覆盖：平台可能已经把关键路径（比如它自己的 site-packages）
    # 注入到 PYTHONPATH，直接 overwrite 会让子进程找不到依赖。
    existing = env.get("PYTHONPATH") or ""
    env["PYTHONPATH"] = sub_dir + (os.pathsep + existing if existing else "")

    # ⚠️ 2026-10-30 修：清掉父进程继承下来的 BASE_PATH。
    # combo_app 只在 QDII 分支显式注入 BASE_PATH=/qdii（见 env_extra），
    # 如果宿主环境里 BASE_PATH 已经有值（比如平台保留上一次部署 / 用户 shell
    # 里带进来），NT 子进程就会把 /nt/api/data 剥成 /api/data —— 而请求
    # 路径本来就是 /api/data，剥不剥没差别；更糟的是 NT_BASE_PATH=/nt 之类
    # 会让 /api/refresh 被剥成空串、路由匹配不到任何 if 分支 → 404。
    env.pop("BASE_PATH", None)
    # 强制内部端口只绑回环：combo 顶层监听 0.0.0.0 由父进程负责对外，
    # 内部子进程不应该在宿主 LAN 上再开一份同端口服务（Windows 每次弹防火墙、
    # 也让 /qdii 前缀路由失去唯一入口的意义）。
    env.setdefault("NT_BIND", "127.0.0.1")
    env.setdefault("QDII_BIND", "127.0.0.1")

    env.update(env_extra)
    env[port_key] = str(port_val)
    script = os.path.join(BASE, script_rel)

    proc = subprocess.Popen(
        [sys.executable, script],
        env=env,
        stderr=subprocess.PIPE,
    )
    with _CHILDREN_LOCK:
        PROC_DIAG[label]["proc"] = proc
        PROC_DIAG[label]["alive"] = True
        PROC_DIAG[label]["returncode"] = None
        PROC_DIAG[label]["port"] = port_val
    t = threading.Thread(target=_drain_stderr, args=(label, proc), daemon=True)
    t.start()
    return proc


def shutdown_children(signum=None, frame=None):
    """atexit / SIGTERM / SIGINT 共用：礼貌 terminate，5s 后再 kill。"""
    with _CHILDREN_LOCK:
        procs = [(k, v["proc"]) for k, v in PROC_DIAG.items() if v["proc"]]
    for label, p in procs:
        try:
            if p.poll() is None:
                p.terminate()
        except Exception:
            pass
    import time as _t
    deadline = _t.time() + 5
    for label, p in procs:
        while _t.time() < deadline and p.poll() is None:
            _t.sleep(0.1)
        try:
            if p.poll() is None:
                p.kill()
        except Exception:
            pass
    if signum is not None:
        # 让默认行为继续把 combo 自己终止（返回 None 表示不吞信号）
        raise SystemExit(128 + signum)


def pick_backend(path):
    """路由决策：/qdii 精确、/qdii/*、/api/qdii、/api/qdii/* 走 QDII，其余走 NT。

    纯函数，方便单测覆盖边界（旧实现 startswith('/qdii') 会连带命中 /qdii-x）。
    /api/qdii 也精确匹配：/api/qdii2/foo 应落到 NT 的 404，而不是错误地
    被送进 QDII 面板；两个子面板的 API 前缀完全等价处理。
    """
    p = path.split("?", 1)[0]
    if p == "/qdii" or p.startswith("/qdii/"):
        return "qdii"
    if p == "/api/qdii" or p.startswith("/api/qdii/"):
        return "qdii"
    return "nt"


# ---- 主进程 handler：等 spawn() 在 main() 里被调用后再用 ----
class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _backend_port(self, label):
        return PROC_DIAG[label]["port"]

    def _warming(self):
        """子进程还在启动窗口：返回 200 + meta refresh 让浏览器稍等。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(WARMING)))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(WARMING)
        except Exception:
            pass

    def _unavailable(self, label, err):
        """子进程已崩：返回 503 + Retry-After，健康检查能真正感知到失败。"""
        body = json.dumps({
            "error": "backend_unavailable",
            "backend": label,
            "returncode": PROC_DIAG[label]["returncode"],
            "detail": str(err)[:200],
            "stderr_tail": PROC_DIAG[label]["stderr_tail"][-800:],
        }, ensure_ascii=False).encode("utf-8")
        self.send_response(503)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Retry-After", "5")
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except Exception:
                pass

    def _proxy(self):
        label = pick_backend(self.path)
        port = self._backend_port(label)
        if port is None:
            # 还没 spawn（combo_app 被 import 后未调用 main）→ 视为预热
            self._warming()
            return
        # 判断子进程是否已经崩：poll() 非 None 就是退出过
        proc = PROC_DIAG[label]["proc"]
        exited = proc is not None and proc.poll() is not None
        try:
            body_req = None
            clen = self.headers.get("Content-Length")
            if clen:
                try:
                    body_req = self.rfile.read(int(clen))
                except Exception:
                    body_req = None
            # /api/refresh 会联网抓新浪+东财，最坏耗时 >60s，超时对齐 gateway
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=180)
            hdrs = {}
            for k, v in self.headers.items():
                if k.lower() in ("host", "connection", "transfer-encoding",
                                 "proxy-connection", "content-length"):
                    continue
                hdrs[k] = v
            conn.request(self.command, self.path, body=body_req, headers=hdrs)
            resp = conn.getresponse()
            body = resp.read()
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                lk = k.lower()
                if lk in ("transfer-encoding", "connection", "proxy-connection",
                          "content-length"):
                    continue
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)
            conn.close()
        except Exception as e:
            PROC_DIAG[label]["last_proxy_err"] = "%s: %s" % (type(e).__name__, e)
            if exited:
                # 子进程明确死了 → 503，让平台健康检查能感知
                self._unavailable(label, e)
            else:
                # 子进程还在但暂时没就绪：保留 200 预热页，避免部署首分钟
                # 就报红、触发平台重启循环
                self._warming()

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/__diag":
            self._diag()
            return
        self._proxy()

    def do_HEAD(self):
        self._proxy()

    def do_POST(self):
        self._proxy()

    def _diag(self):
        for label in ("nt", "qdii"):
            p = PROC_DIAG[label]["proc"]
            if p is not None:
                PROC_DIAG[label]["alive"] = p.poll() is None
                PROC_DIAG[label]["returncode"] = p.returncode
        payload = {
            "version": COMBO_VERSION,
            "port": PORT,
            "nt_port": PROC_DIAG["nt"]["port"],
            "qdii_port": PROC_DIAG["qdii"]["port"],
            "nt": {k: PROC_DIAG["nt"][k] for k in
                   ("alive", "returncode", "last_proxy_err", "port")},
            "qdii": {k: PROC_DIAG["qdii"][k] for k in
                     ("alive", "returncode", "last_proxy_err", "port")},
            "nt_stderr": PROC_DIAG["nt"]["stderr_tail"][-1500:],
            "qdii_stderr": PROC_DIAG["qdii"]["stderr_tail"][-1500:],
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, *args):
        pass


WARMING = (
    b"<html><head><meta charset='utf-8'><title>\xe9\xa2\x84\xe7\x83\xad\xe4\xb8\xad</title>"
    b"<meta http-equiv='refresh' content='2'></head><body style='font-family:sans-serif;"
    b"text-align:center;margin-top:20%'><h2>\xf0\x9f\x94\x84 \xe6\x9c\x8d\xe5\x8a\xa1\xe9\xa2\x84"
    b"\xe7\x83\xad\xe4\xb8\xad\xe2\x80\xa6</h2><p>\xe4\xb8\xa4\xe4\xb8\xaa\xe9\x9d\xa2\xe6\x9d\xbf"
    b"\xe6\xad\xa3\xe5\x9c\xa8\xe5\x90\xaf\xe5\x8a\xa8\xef\xbc\x8c\xe7\xba\xa62\xe7\xa7\x92\xe5\x90"
    b"\x8e\xe8\x87\xaa\xe5\x8a\xa8\xe5\x88\xb7\xe6\x96\xb0\xe3\x80\x82</p></body></html>"
)


def main():
    # 分配两个不冲突的内部端口（NT 与 QDII 各一），避开彼此 + 避开主 PORT
    nt_port = free_port(exclude={PORT})
    qdii_port = free_port(exclude={PORT, nt_port})

    # 主进程立即监听对外端口，不等子进程就绪（避免发布平台 60s 端口检查超时）。
    # 子进程在后台预热，代理在连接失败时返回「预热中」200 页，健康检查即视为就绪；
    # 但如果子进程明确崩了（proc.poll() 非 None），会返回 503 让平台真感知失败。
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    socketserver.ThreadingTCPServer.daemon_threads = True
    httpd = socketserver.ThreadingTCPServer(("0.0.0.0", PORT), Handler)

    # 子进程在 httpd 就绪后再 spawn：这样 /__diag 一被访问就能拿到状态，
    # 也不会出现「import 时就起两个服务、单测想 verify 语法就抢端口」的副作用。
    spawn("nt", "national_team/app.py",
          {"NT_AUTO_ENSURE": "0", "PEER_URL": "/qdii"},
          "PORT", nt_port)
    spawn("qdii", "qdii/qdii_app.py",
          {"BASE_PATH": "/qdii", "PEER_URL": "/"},
          "QDII_PORT", qdii_port)

    # 收尾：atexit + SIGTERM/SIGINT 都把子进程带走，避免留下孤儿占 SQLite 锁 / 端口
    atexit.register(shutdown_children)
    for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
        if sig is not None:
            try:
                signal.signal(sig, shutdown_children)
            except (ValueError, OSError):
                # 非主线程或平台不支持时忽略，atexit 仍会兜底
                pass

    print("combo %s listening on 0.0.0.0:%d (nt:%d qdii:%d)"
          % (COMBO_VERSION, PORT, nt_port, qdii_port), flush=True)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
        shutdown_children()


if __name__ == "__main__":
    main()
