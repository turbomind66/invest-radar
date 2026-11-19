"""common/http.py — 代理感知 HTTP 抓取（invest-radar 各模块共用）。

设计要点：
- 本地地址（127.0.0.1 / localhost / ::1 / host.docker.internal / 172.17.0.1）
  自动绕过任何代理，避免把本地/容器网桥请求误送公司代理。
- 公网地址默认沿用环境变量代理：HTTP_PROXY/HTTPS_PROXY（大写优先，兼容小写），
  NO_PROXY 里的域名/IP 一律直连（逗号或空格分隔，支持 `*` 与 `.` 前缀通配）。
  部署到无代理的 VPS 时环境变量为空即直连。
- 支持 GBK 解码（新浪行情）、JSON 解析、简单重试。

⚠️ 踩过的坑（2026-10-30 修复）：
- 旧实现直接把 `HTTP_PROXY` / `HTTPS_PROXY` 这样的**环境变量名**塞进
  `ProxyHandler` 的 dict，但 ProxyHandler 期望的是**协议方案名**（`http` / `https`），
  键不匹配 → 环境代理被静默忽略、请求实际全走直连，与文档承诺相反。
  现在按方案名映射，并显式实现 NO_PROXY。
"""
import os
import time
import json
import random
import fnmatch
import threading
import urllib.request
import urllib.error
from collections import deque
from urllib.parse import urlparse

_LOCAL_HOSTS = (
    "127.0.0.1", "localhost", "::1",
    # 常见容器/网关地址：反代与 healthcheck 不应误走公司代理
    "host.docker.internal", "172.17.0.1",
)

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


# ---- 限流防封三件套（默认全部关闭，保持既有行为不变）----
# 参考「东财接口限流防封实战」：按子域名独立限流、单次请求后抖动、
# 命中强制断开/403 立即熔断切源、同主机复用 opener。
# 本项目抓取频率极低（daily 两次 + tick 盘中十次），默认无需节流；
# 只有高并发批量补数场景才需显式开启，避免影响既有低频调用方。
#
# 环境变量：
#   EM_RATE_PER_MIN   int   按主机每分钟请求上限（滑动窗口）；<=0 关闭
#   EM_JITTER_SEC     float 每次请求后的随机抖动上限秒；<=0 关闭（默认建议 0.3~1.0）
#   EM_HOST_COOLDOWN  float 命中失败的主机冷却秒数（熔断后不立即重试）；<=0 关闭
#   EM_OPENER_REUSE   1     复用 opener（同 use_proxy 同一实例，减少握手开销）

_RATE_PER_MIN = 0
try:
    _RATE_PER_MIN = int(os.environ.get("EM_RATE_PER_MIN", "0"))
except (TypeError, ValueError):
    pass

_JITTER_SEC = 0.0
try:
    _JITTER_SEC = float(os.environ.get("EM_JITTER_SEC", "0"))
except (TypeError, ValueError):
    pass

_HOST_COOLDOWN = 0.0
try:
    _HOST_COOLDOWN = float(os.environ.get("EM_HOST_COOLDOWN", "0"))
except (TypeError, ValueError):
    pass

_OPENER_REUSE = os.environ.get("EM_OPENER_REUSE", "") == "1"


class _HostRateLimiter(object):
    """按主机滑动窗口限流器（线程安全）。

    每个 host 维护一个最近 window 秒内的请求时间戳队列；acquire() 会
    阻塞到有配额为止。仅当 limit>0 时生效，否则零开销直通。
    """

    def __init__(self, limit, window=60.0, sleep_fn=None, clock_fn=None):
        self.limit = limit
        self.window = window
        self._sleep = sleep_fn or time.sleep
        self._clock = clock_fn or time.monotonic
        self._hits = {}
        self._lock = threading.Lock()

    def acquire(self, host):
        if not self.limit or self.limit <= 0:
            return
        while True:
            now = self._clock()
            with self._lock:
                q = self._hits.setdefault(host, deque())
                cutoff = now - self.window
                while q and q[0] <= cutoff:
                    q.popleft()
                if len(q) < self.limit:
                    q.append(now)
                    return
                wait = self.window - (now - q[0]) + 0.001
            self._sleep(max(wait, 0.001))


_rate_limiter = _HostRateLimiter(_RATE_PER_MIN)


class _HostCooldown(object):
    """失败主机冷却表（线程安全）。

    mark(host) 后 cooldown 秒内 is_cold(host) 为真，轮换镜像时应跳过该主机。
    仅当 cooldown>0 时生效。
    """

    def __init__(self, cooldown, clock_fn=None):
        self.cooldown = cooldown
        self._clock = clock_fn or time.monotonic
        self._until = {}
        self._lock = threading.Lock()

    def mark(self, host):
        if not self.cooldown or self.cooldown <= 0 or not host:
            return
        with self._lock:
            self._until[host] = self._clock() + self.cooldown

    def is_cold(self, host):
        if not self.cooldown or self.cooldown <= 0:
            return False
        with self._lock:
            until = self._until.get(host)
            if until is None:
                return False
            if self._clock() >= until:
                self._until.pop(host, None)
                return False
            return True

    def clear(self, host):
        with self._lock:
            self._until.pop(host, None)


_host_cooldown = _HostCooldown(_HOST_COOLDOWN)


# 同 use_proxy 复用同一个 opener，减少重复 build_opener 的开销。
# 注意：urllib 每次 open() 仍是独立连接（非 HTTP keep-alive），
# 这里只是省掉重复构造 opener/handler 的 CPU，不改变连接语义。
_opener_cache = {}
_opener_cache_lock = threading.Lock()


def _get_opener(use_proxy):
    if not _OPENER_REUSE:
        return urllib.request.build_opener(_proxy_handler(use_proxy))
    key = bool(use_proxy)
    with _opener_cache_lock:
        op = _opener_cache.get(key)
        if op is None:
            op = urllib.request.build_opener(_proxy_handler(use_proxy))
            _opener_cache[key] = op
        return op


def _first_env(*keys):
    """按顺序取第一个非空环境变量（大写优先，兼容小写）。"""
    for k in keys:
        v = os.environ.get(k)
        if v:
            return v.strip()
    return ""


def _no_proxy_matches(host: str) -> bool:
    """NO_PROXY 语义（curl / requests 的通用写法）：

    - 逗号或空格分隔；
    - `*` 表示全部；
    - 前缀 `.` 或裸域名做**后缀匹配**（`.example.com` 也匹配 `a.example.com`）；
    - 支持 `10.*`、`*.corp.internal` 这样的通配符；
    - 主机名大小写不敏感，忽略端口。
    """
    raw = _first_env("NO_PROXY", "no_proxy")
    if not raw:
        return False
    h = (host or "").lower()
    # 去掉可能的端口（IPv6 字面量带 [] 时不动）
    if ":" in h and not h.startswith("["):
        h = h.rsplit(":", 1)[0]
    for tok in raw.replace(",", " ").split():
        tok = tok.strip().lower()
        if not tok:
            continue
        if tok == "*":
            return True
        # 带端口的写法（如 localhost:8080），只比对主机部分
        if ":" in tok and not tok.startswith("["):
            tok = tok.rsplit(":", 1)[0]
        # 前缀点写法：等价 glob `.tok`，同时接受不带点的裸域
        if tok.startswith("."):
            bare = tok.lstrip(".")
            if h == bare or h.endswith(tok) or fnmatch.fnmatchcase(h, "*" + tok):
                return True
            continue
        # 精确 或 glob（10.*、*.corp.internal 等）
        if h == tok or fnmatch.fnmatchcase(h, tok):
            return True
    return False


def _proxy_handler(use_proxy: bool):
    """返回 ProxyHandler。

    ProxyHandler 的 dict 键必须是**协议方案名**（`http` / `https`），
    不能用环境变量名。这里显式按方案映射，并让 NO_PROXY 生效。
    """
    if not use_proxy:
        return urllib.request.ProxyHandler({})  # 强制直连
    proxies = {}
    http_v = _first_env("HTTP_PROXY", "http_proxy")
    https_v = _first_env("HTTPS_PROXY", "https_proxy")
    if http_v:
        proxies["http"] = http_v
    if https_v:
        proxies["https"] = https_v
    return urllib.request.ProxyHandler(proxies)


def _auto_proxy(host: str) -> bool:
    """本地回环 / 容器网桥 / NO_PROXY 命中 → 直连；其它 → 沿用环境代理。"""
    if any(host == h or host.startswith(h + ":") for h in _LOCAL_HOSTS):
        return False
    if _no_proxy_matches(host):
        return False
    return True


# ---- 东财镜像主机池（rotate_hosts 用） ----
# 背景：东财 push2 / push2his 在部分出口会被**按主机限流**，表现为
#   http.client.RemoteDisconnected: Remote end closed connection without response
# 此时「对同一台主机重试 N 次」毫无意义（每次都一样），换一台镜像反而立刻成功。
# 实测（2026-10-30）：
#   push2    可用：push2 / push2delay / 82.push2 / 11.push2
#   push2his 可用：push2his / 1.push2his / 2.push2his / 82.push2his / 11.push2his
#                 / 7.push2his / 23.push2his     （push2hisdelay 不存在，勿加）
PUSH2_HOSTS = (
    "push2.eastmoney.com",
    "push2delay.eastmoney.com",
    "82.push2.eastmoney.com",
    "11.push2.eastmoney.com",
)
PUSH2HIS_HOSTS = (
    "push2his.eastmoney.com",
    "1.push2his.eastmoney.com",
    "2.push2his.eastmoney.com",
    "82.push2his.eastmoney.com",
    "11.push2his.eastmoney.com",
    "7.push2his.eastmoney.com",
    "23.push2his.eastmoney.com",
)


def _mirror_pool(host: str):
    """返回该主机可用的镜像池；非东财行情域名返回 None（不轮换）。"""
    if host in PUSH2_HOSTS or host in ("push2delay.eastmoney.com",):
        return PUSH2_HOSTS
    if host == "push2his.eastmoney.com" or host.endswith(".push2his.eastmoney.com"):
        return PUSH2HIS_HOSTS
    return None


def _pick_host(order, attempt, cooldown_sec):
    """从镜像池按序挑一台未冷却的主机。

    - 未启用冷却（默认 cooldown_sec<=0）：完全保持既有轮转 `order[attempt % n]`，
      行为零变化。
    - 启用冷却：从 attempt 位置起找第一台未冷却的；全冷则仍按轮转选，
      避免所有主机都在冷却时卡死。
    """
    n = len(order)
    if not cooldown_sec or cooldown_sec <= 0:
        return order[attempt % n]
    for i in range(n):
        cand = order[(attempt + i) % n]
        if not _host_cooldown.is_cold(cand):
            return cand
    return order[attempt % n]


def fetch_text(url, referer=None, encoding="utf-8", timeout=15,
               use_proxy=None, retries=2, headers=None, rotate_hosts=False):
    """抓取 URL 返回文本。本地地址自动直连；公网默认走环境代理。

    rotate_hosts=True 且 URL 命中东财行情域名（push2 / push2his）时，
    每次重试**换一台镜像主机**，而不是死磕同一台——用于对抗按主机限流。
    默认 False，保持既有行为不变。

    限流防封三件套均默认关闭（见文件顶部 EM_* 环境变量）：开启后按主机
    滑动窗口节流、单次请求失败即标记该主机冷却（熔断切源）、重试带随机抖动。
    """
    host = urlparse(url).hostname or ""
    if use_proxy is None:
        use_proxy = _auto_proxy(host)
    req_headers = {"User-Agent": _DEFAULT_UA}
    if referer:
        req_headers["Referer"] = referer
    if headers:
        req_headers.update(headers)
    pool = _mirror_pool(host) if rotate_hosts else None
    # 轮换顺序：原始主机已在池内时从它开始轮，而不是强行回到 pool[0]。
    # 否则调用方显式指定镜像时，第 0 次尝试会被改回主域名——而主域名恰恰是
    # 最容易被限流的那台，等于先白白失败一次。
    order = None
    if pool:
        start = pool.index(host) if host in pool else 0
        order = pool[start:] + pool[:start]
    last = None
    for attempt in range(retries + 1):
        target_host = _pick_host(order, attempt, _HOST_COOLDOWN) if order else host
        target = url.replace(host, target_host, 1)

        # 按主机滑动窗口节流（默认 limit=0，零开销直通）
        _rate_limiter.acquire(target_host)

        try:
            opener = _get_opener(use_proxy)
            req = urllib.request.Request(target, headers=req_headers)
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read()
                _host_cooldown.clear(target_host)  # 成功即解除该主机冷却
                try:
                    return raw.decode(encoding)
                except UnicodeDecodeError:
                    return raw.decode("gbk", errors="replace")
        except Exception as e:  # noqa: BLE001
            last = e
            # 命中失败（含强制断开 / 403）：标记主机冷却，后续轮换跳过，
            # 避免撞墙重试同一台——这正是文章强调的「熔断切源」。
            _host_cooldown.mark(target_host)
            if attempt < retries:
                wait = 1 + attempt
                if _JITTER_SEC > 0:
                    wait += random.uniform(0, _JITTER_SEC)
                time.sleep(wait)
    raise last


def fetch_json(url, referer=None, timeout=15, use_proxy=None, retries=2):
    """抓取并解析 JSON。"""
    return json.loads(
        fetch_text(url, referer=referer, encoding="utf-8",
                   timeout=timeout, use_proxy=use_proxy, retries=retries)
    )
