#!/usr/bin/env python3
"""容器健康检查：按当前角色探测对应服务是否存活。

镜像内同时存在国家队（PORT，默认 5000）与 QDII（QDII_PORT，默认 5001）
两套服务，不同容器只跑其中之一。逐个探测、任一成功即视为健康，
这样同一个 HEALTHCHECK 指令可以同时用于两个 web 容器，不必按角色分支。

cron 容器不提供 HTTP 端口，compose 里已把 test 覆盖为 `true`。
"""
import os
import sys
import urllib.request

# 容器内访问本机，显式禁用代理（宿主机环境变量可能带 HTTP_PROXY）
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

TARGETS = [
    "http://127.0.0.1:%s/api/status" % os.environ.get("PORT", "5000"),
    "http://127.0.0.1:%s/api/qdii/status" % os.environ.get("QDII_PORT", "5001"),
]

for url in TARGETS:
    try:
        with opener.open(url, timeout=8) as resp:
            if resp.status == 200:
                print("[healthcheck] ok %s" % url)
                sys.exit(0)
    except Exception as exc:  # 连不上就试下一个
        print("[healthcheck] miss %s (%s)" % (url, exc), file=sys.stderr)

sys.exit(1)
