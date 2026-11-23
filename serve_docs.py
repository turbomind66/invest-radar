"""serve_docs.py — 本地静态站服务（替代 `python -m http.server --directory docs`）。

为什么需要它
------------
GitHub Pages 只能托管静态文件，`docs/` 里的三个页面（汪汪队 / 回撤全景 /
全球QDII）点「更新数据」只能引导用户去 Actions 手动 dispatch。但数据其实就在
本机——把静态站起在自己的电脑上时，完全可以让页面直接触发一次抓取 + 重新导出。

本服务 = 静态文件服务器 + 一个很小的同源刷新接口：

    GET  /api/local/status                 各页面任务状态（前端据此探测「有无后端」）
    GET  /api/local/refresh?page=nt|dd|qdii|all   启动抓取（后台线程跑子进程）

三个页面（`index.html`、`dd/index.html`、`qdii/index.html`）在
`window.__STATIC__` 模式下会先 probe 这个接口：
    有 → 按钮变「🔄 更新数据」，点击原地抓取，完成后自动重载；
    无 → 保持旧行为（打开 Actions 页），因此 GitHub Pages 上的行为完全不变。

工作目录
--------
**不写 `docs/`**。`docs/` 是 GitHub Pages 的发布目录、也是 Actions 提交的快照，
本地库覆盖它会造成「本地旧数据冲掉线上校准结果」（历史事故）。本服务把产物写到
`.local_docs/`（已 gitignore）：

    首次启动  docs/ → .local_docs/ 整份复制（页面立刻有数据可看）
    之后每次  .local_docs/ 只刷新 *.html/*.js 骨架，json 由抓取任务写入

用法
----
    python serve_docs.py                      # http://127.0.0.1:5056/
    python serve_docs.py --port 8000
    python serve_docs.py --work .local_docs   # 换工作目录
    python serve_docs.py --bind 0.0.0.0       # 局域网共享（谨慎）

注意
----
- 只读静态文件 + 触发既有 CLI；不新增任何抓取口径，抓取逻辑仍是
  national_team/staticsite.py、drawdown.updater、qdii_scheduler + qdii/staticsite.py。
- 子进程串行执行「同页面」，不同页面可并行（各写各的库：data.db / dd.db / qdii.db）。
- 抓取受东财限流影响，连点会被拒绝（同页面 running 时直接复用，不重复起进程）。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

PY = sys.executable or "python"

# 骨架文件：模板/脚本更新后要跟着走，json 属于「数据」不能覆盖。
SKELETON_EXT = (".html", ".htm", ".js", ".css", ".svg", ".png", ".ico")
# 每个页面 = 一串命令（顺序执行，任一失败即判定失败）。
# 命令都复用仓库自带的 CLI，口径与定时任务/服务端完全一致；--out 指向工作目录。
PAGES = {
    "nt": {"name": "汪汪队（国家队）", "sub": ""},
    "dd": {"name": "回撤全景", "sub": "dd"},
    "qdii": {"name": "全球QDII", "sub": "qdii"},
}
PAGE_ALIAS = {"all": list(PAGES.keys())}
# 导出根目录（由 main() 按 --work 设置）；全局是因为 Job 线程里要用。
WORK = os.path.join(BASE, ".local_docs")


def page_steps(page, work):
    """按页面生成命令行（--out 全部落在工作目录，绝不碰 docs/）。"""
    if page == "nt":
        # staticsite.py 内含同步（回补缺失交易日、份额、分时、逐笔）后导出
        return [[os.path.join("national_team", "staticsite.py"), "--out", work]]
    if page == "dd":
        # 必须用 -m 走包内相对导入（直接跑文件会 ImportError）
        return [["-m", "drawdown.updater", "--export",
                 "--out", os.path.join(work, "dd")]]
    if page == "qdii":
        return [[os.path.join("qdii", "qdii_scheduler.py"), "--once"],
                [os.path.join("qdii", "staticsite.py"),
                 "--out", os.path.join(work, "qdii")]]
    return []


def sync_skeleton(src, dest):
    """docs/ 的页面骨架同步到工作目录；数据 json 只在首次整份复制时带过去。

    这样改模板后重启服务就能看到新页面，而本地抓取的历史数据不会被 Actions
    快照反复冲掉（反之亦然）。
    """
    if not os.path.isdir(dest):
        shutil.copytree(src, dest)
        return "copytree"
    n = 0
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        out = dest if rel == "." else os.path.join(dest, rel)
        for f in files:
            if not (f.endswith(SKELETON_EXT) or f == ".nojekyll"):
                continue
            os.makedirs(out, exist_ok=True)
            try:
                shutil.copy2(os.path.join(root, f), os.path.join(out, f))
                n += 1
            except Exception as e:  # noqa: BLE001
                print("[docs] 骨架同步失败 %s：%s" % (f, e), flush=True)
    return n


class Job:
    """一个页面的刷新任务；状态由前端轮询读取。"""

    def __init__(self, page, steps_fn=None):
        self.page = page
        # 步骤可注入：测试用 python -c 假命令，避免真联网抓取
        self.steps_fn = steps_fn or page_steps
        self.lock = threading.Lock()
        self.state = "idle"          # idle | running | ok | error
        self.started_at = ""
        self.ended_at = ""
        self.started_ts = 0.0
        self.ended_ts = 0.0
        self.ok = None
        self.error = ""
        self.log = deque(maxlen=80)
        self.thread = None

    def _now(self):
        return time.strftime("%Y-%m-%d %H:%M:%S")

    def running(self):
        return self.state == "running"

    def _emit(self, line):
        line = (line or "").rstrip()
        if not line:
            return
        # 只留最后一行有效信息给前端，完整输出留在服务端控制台
        self.log.append(line[:300])
        try:
            print("[%s] %s" % (self.page, line), flush=True)
        except Exception:  # noqa: BLE001
            pass

    def _run_step(self, argv):
        cmd = [PY] + argv
        self._emit("$ " + " ".join(argv))
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env["PYTHONUNBUFFERED"] = "1"
        # 本机起服务时允许 updater 自己判断增量；不改变任何抓取口径。
        proc = subprocess.Popen(
            cmd, cwd=BASE, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", bufsize=1,
        )
        last = ""
        for line in proc.stdout:      # 逐行读，前端能看到"正在抓哪一步"
            last = line.rstrip()
            self._emit(last)
        code = proc.wait()
        if code != 0:
            raise RuntimeError("步骤失败（exit=%s）：%s" % (code, last[:160] or "无输出"))

    def _work(self):
        try:
            for argv in self.steps_fn(self.page, WORK):
                self._run_step(argv)
            self.state = "ok"
            self.ok = True
            self._emit("✓ 完成，静态页已重新导出")
        except Exception as e:  # noqa: BLE001
            self.state = "error"
            self.ok = False
            self.error = "%s: %s" % (type(e).__name__, e)
            self._emit("✗ " + self.error)
        finally:
            self.ended_ts = time.time()
            self.ended_at = self._now()

    def start(self):
        with self.lock:
            if self.running():
                return False
            self.state = "running"
            self.ok = None
            self.error = ""
            self.log.clear()
            self.started_ts = time.time()
            self.started_at = self._now()
            self.ended_at = ""
            self.ended_ts = 0.0
            self.thread = threading.Thread(target=self._work, name="job-" + self.page, daemon=True)
            self.thread.start()
            return True

    def snapshot(self):
        elapsed = 0
        if self.started_ts:
            end = self.ended_ts or time.time()
            elapsed = int(end - self.started_ts)
        return {
            "state": self.state,
            "ok": self.ok,
            "error": self.error,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "elapsed": elapsed,
            "log": list(self.log)[-6:],
        }


JOBS = {k: Job(k) for k in PAGES}


class Handler(SimpleHTTPRequestHandler):
    server_version = "InvestRadarDocs/1.0"

    # ---------- 响应工具 ----------
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # 同源即可，无需 CORS；显式禁掉预检以外的跨源读取
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _api(self, path, query):
        if path == "/api/local/status":
            self._json({
                "ok": True,
                "pages": {k: v["name"] for k, v in PAGES.items()},
                "jobs": {k: j.snapshot() for k, j in JOBS.items()},
                "server_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            return True
        if path == "/api/local/refresh":
            want = (query.get("page") or ["nt"])[0].strip().lower()
            targets = PAGE_ALIAS.get(want) or ([want] if want in PAGES else [])
            if not targets:
                self._json({"ok": False, "error": "未知页面：%s" % want}, 400)
                return True
            started = []
            for t in targets:
                if JOBS[t].start():
                    started.append(t)
            self._json({
                "ok": True,
                "page": want,
                "targets": targets,
                "started": started,
                "running": [t for t in targets if JOBS[t].running()],
            })
            return True
        return False

    # ---------- 路由 ----------
    def _handle(self, with_body):
        url = urlparse(self.path)
        path = url.path
        # 站点根 /dd → /dd/，否则 /dd 下的相对资源（echarts.min.js、recon/*.json）会 404
        if path in ("/dd", "/qdii"):
            self.send_response(301)
            self.send_header("Location", path + "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/api/local" or path.startswith("/api/local/"):
            query = parse_qs(url.query)
            if self._api(path, query):
                return
            self._json({"ok": False, "error": "unknown endpoint"}, 404)
            return
        if with_body:
            super().do_GET()
        else:
            super().do_HEAD()

    def do_GET(self):  # noqa: N802
        self._handle(True)

    def do_HEAD(self):  # noqa: N802
        self._handle(False)

    def do_POST(self):  # noqa: N802
        # 刷新接口允许 POST（前端也可以直接 window.open 一个 URL 触发）
        self._handle(True)

    def end_headers(self):
        # 静态资源（data.json / 页面）不要被浏览器长时间缓存，
        # 否则「更新完成 → reload」还会看到旧数字。
        if not self.path.startswith("/api/local"):
            self.send_header("Cache-Control", "no-cache, must-revalidate")
        super().end_headers()

    def log_message(self, fmt, *args):
        # 只打 API 与错误，静态文件请求刷屏没意义
        if self.path.startswith("/api/local") or (args and str(args[1]) not in ("200", "304")):
            sys.stderr.write("[docs] %s - %s\n" % (self.address_string(), fmt % args))


def main():
    global WORK
    ap = argparse.ArgumentParser(description="本地静态站 + 一键刷新服务")
    ap.add_argument("--port", type=int, default=int(os.environ.get("DOCS_PORT", 5056)))
    ap.add_argument("--bind", default=os.environ.get("DOCS_BIND", "127.0.0.1"))
    ap.add_argument("--docs", default=os.environ.get("NT_DOCS_DIR") or os.path.join(BASE, "docs"),
                    help="快照源目录（GitHub Pages 发布目录，只读）")
    ap.add_argument("--work", default=os.environ.get("DOCS_WORK_DIR") or WORK,
                    help="工作目录（抓取产物写这里，默认 .local_docs，已 gitignore）")
    args = ap.parse_args()

    src = os.path.abspath(args.docs)
    if not os.path.isdir(src):
        print("[docs] 快照目录不存在：%s（先跑一次导出）" % src, file=sys.stderr)
        return 2
    WORK = os.path.abspath(args.work)
    r = sync_skeleton(src, WORK)
    print("[docs] 骨架同步：%s" % ("首次整份复制" if r == "copytree" else "%d 个文件" % r), flush=True)

    handler = lambda *a, **kw: Handler(*a, directory=WORK, **kw)  # noqa: E731
    httpd = ThreadingHTTPServer((args.bind, args.port), handler)
    url = "http://%s:%d/" % ("127.0.0.1" if args.bind in ("0.0.0.0", "") else args.bind, args.port)
    print("[docs] 静态站：%s" % url, flush=True)
    print("[docs] 服务目录：%s（写这里，不动 docs/）" % WORK, flush=True)
    print("[docs] 刷新接口：/api/local/status · /api/local/refresh?page=nt|dd|qdii|all", flush=True)
    print("[docs] 三个页面点「更新数据」即本地抓取，无需 GitHub Actions。Ctrl+C 退出。", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[docs] 已退出", flush=True)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
