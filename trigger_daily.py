#!/usr/bin/env python3
# trigger_daily.py — 从本机触发 GitHub Actions 的 workflow_dispatch 事件
#
# 适用：Windows 任务计划程序 / WSL crontab / 普通命令行
# 用法：python trigger_daily.py
#
# 设计要点：
#   1) PAT 不落明文——优先读环境变量 GH_PAT，其次读 Windows 用户环境变量 workbuddy-github（注册表）。
#   2) 代理容错——本机访问 GitHub 必须走代理。依次尝试：
#        10808（WorkBuddy 沙盒代理，对 GitHub 最稳）
#        -> 系统环境变量代理（HTTP(S)_PROXY，常驻）
#        -> 直连（公司网络通常不通，兜底）
#      每个代理重试 2 次，任一成功即退出。
#   3) 退出码：0=成功, 1=全部代理失败, 2=找不到 PAT。任务计划程序可据此判成败。

import os
import sys
import json
import urllib.request
import urllib.error

REPO = "turbomind66/ashare-national-team-tracker"
WORKFLOW = "daily.yml"   # 对应 .github/workflows/daily.yml
REF = "main"             # 触发的分支


def get_token():
    """返回 (token, 来源说明)。找不到返回 (None, 'none')。"""
    # 1) 环境变量（WSL crontab / CI 用这个）
    t = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN")
    if t:
        return t, "env:GH_PAT/GITHUB_TOKEN"
    # 2) Windows 用户环境变量（注册表，避免明文落盘）
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment")
        return winreg.QueryValueEx(k, "workbuddy-github")[0], "winreg:workbuddy-github"
    except Exception:
        pass
    return None, "none"


def do_post(opener_factory):
    """发一次 dispatch 请求。返回 (ok:bool, 信息:str)。"""
    url = f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches"
    req = urllib.request.Request(
        url,
        data=json.dumps({"ref": REF}).encode(),
        method="POST",
        headers={
            "Authorization": "Bearer " + TOKEN,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "wb-trigger",
        },
    )
    try:
        r = opener_factory().open(req, timeout=40)
        return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code} {e.read().decode()[:200]}"
    except Exception as e:  # 含 SSL 抖动等
        return False, f"{type(e).__name__}: {e}"


def build_strategies():
    """构造代理策略列表 [(标签, opener工厂), ...]。"""
    strategies = []
    # 1) 沙盒代理（对 GitHub 最稳，但仅 WorkBuddy 会话期间常驻）
    strategies.append(("10808", lambda: urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": "http://127.0.0.1:10808",
                                     "https": "http://127.0.0.1:10808"}))))
    # 2) 系统环境变量代理（公司常驻出口）
    envp = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if envp:
        strategies.append(("env-proxy", lambda p=envp: urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": p, "https": p}))))
    # 3) 直连兜底
    strategies.append(("direct", lambda: urllib.request.build_opener(
        urllib.request.ProxyHandler({}))))
    return strategies


def main():
    global TOKEN
    TOKEN, src = get_token()
    if not TOKEN:
        print("ERROR: 找不到 PAT。请设置环境变量 GH_PAT，"
              "或在 Windows 用户环境变量中配置 workbuddy-github。", file=sys.stderr)
        return 2
    print(f"[info] token 来源: {src}")

    strategies = build_strategies()
    last_err = None
    for label, fac in strategies:
        for attempt in range(2):
            ok, msg = do_post(fac)
            if ok:
                print(f"OK: 经 {label} 触发成功（{msg}）")
                return 0
            last_err = f"{label}: {msg}"
            print(f"  [{label}] 第{attempt + 1}次失败: {msg}")
    print(f"ERROR: 全部代理策略失败。最后错误: {last_err}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
