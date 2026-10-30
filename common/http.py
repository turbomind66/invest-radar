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
import fnmatch
import urllib.request
import urllib.error
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


def fetch_text(url, referer=None, encoding="utf-8", timeout=15,
               use_proxy=None, retries=2, headers=None):
    """抓取 URL 返回文本。本地地址自动直连；公网默认走环境代理。"""
    host = urlparse(url).hostname or ""
    if use_proxy is None:
        use_proxy = _auto_proxy(host)
    req_headers = {"User-Agent": _DEFAULT_UA}
    if referer:
        req_headers["Referer"] = referer
    if headers:
        req_headers.update(headers)
    last = None
    for attempt in range(retries + 1):
        try:
            opener = urllib.request.build_opener(_proxy_handler(use_proxy))
            req = urllib.request.Request(url, headers=req_headers)
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read()
                try:
                    return raw.decode(encoding)
                except UnicodeDecodeError:
                    return raw.decode("gbk", errors="replace")
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries:
                time.sleep(1 + attempt)
    raise last


def fetch_json(url, referer=None, timeout=15, use_proxy=None, retries=2):
    """抓取并解析 JSON。"""
    return json.loads(
        fetch_text(url, referer=referer, encoding="utf-8",
                   timeout=timeout, use_proxy=use_proxy, retries=retries)
    )
