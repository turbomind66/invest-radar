"""common/http.py — 代理感知 HTTP 抓取（invest-radar 各模块共用）。

设计要点：
- 本地地址（127.0.0.1 / localhost）自动绕过任何代理，避免把本地请求误送公司代理。
- 公网地址默认沿用环境变量代理（HTTP(S)_PROXY，如公司出口或沙盒 10808）；
  部署到无代理的 VPS 时环境变量为空即直连。
- 支持 GBK 解码（新浪行情）、JSON 解析、简单重试。
"""
import os
import time
import json
import urllib.request
import urllib.error
from urllib.parse import urlparse

_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _proxy_handler(use_proxy: bool):
    if not use_proxy:
        return urllib.request.ProxyHandler({})  # 强制直连
    env = {}
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        val = os.environ.get(key)
        if val:
            env[key] = val
            env[key.upper()] = val
    return urllib.request.ProxyHandler(env or {})


def _auto_proxy(host: str) -> bool:
    if any(host == h or host.startswith(h + ":") for h in _LOCAL_HOSTS):
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
