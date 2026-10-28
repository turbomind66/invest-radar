#!/bin/sh
# 容器入口：按 APP_ROLE 决定跑哪个进程
#   web  -> 只跑看板服务（app.py）
#   cron -> 只跑定时调度（scheduler.py）
#   all  -> 两个都跑，适合单容器一键部署（docker run）
set -eu

ROLE="${APP_ROLE:-all}"
echo "[entrypoint] role=${ROLE} tz=${TZ:-system} port=${PORT:-5000} db=${NT_DB_PATH:-<default>}"

# 数据目录必须可写，否则 SQLite 无法初始化（挂载卷权限不对时给出明确提示）
if [ -n "${NT_DB_PATH:-}" ]; then
    DATA_DIR="$(dirname "${NT_DB_PATH}")"
    mkdir -p "${DATA_DIR}" 2>/dev/null || true
    if [ ! -w "${DATA_DIR}" ]; then
        echo "[entrypoint] 警告：数据目录不可写 ${DATA_DIR}（请检查挂载卷权限）" >&2
    fi
fi

case "${ROLE}" in
    web|server|api)
        exec python -u app.py
        ;;
    cron|scheduler)
        exec python -u scheduler.py
        ;;
    all|"")
        echo "[entrypoint] 单容器模式：后台启动调度器 + 前台启动看板服务"
        python -u scheduler.py &
        exec python -u app.py
        ;;
    *)
        echo "[entrypoint] 未知 APP_ROLE=${ROLE}（可选：web / cron / all）" >&2
        exit 64
        ;;
esac
