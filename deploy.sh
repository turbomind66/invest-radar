#!/usr/bin/env bash
# 国家队买卖A股日报 —— Debian / Ubuntu VPS 一键部署脚本
#
# 用法：
#   ./deploy.sh                    # Docker Compose 部署（默认，推荐）
#   ./deploy.sh --run              # 纯 docker run 单容器部署（web + 调度合一）
#   ./deploy.sh --port 18080       # 指定宿主机端口
#   ./deploy.sh --install-docker   # 未装 Docker 时自动安装
#   ./deploy.sh --help
#
# 说明：脚本可重复执行；更新代码后再次运行即完成升级（compose 模式会自动重新构建镜像）。
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${REPO_DIR}"

IMAGE="ashare-national-team-tracker:latest"
VOLUME="ntt-data"
MODE="compose"
INSTALL_DOCKER=0
HOST_PORT=""

info() { printf '\033[1;36m[deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --run) MODE="run"; shift ;;
        --port) HOST_PORT="${2:-}"; shift 2 ;;
        --install-docker) INSTALL_DOCKER=1; shift ;;
        -h|--help) usage ;;
        *) die "未知参数：$1（用 --help 查看用法）" ;;
    esac
done

port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1; }

# ---- 1. 环境检查 ----
if ! command -v docker >/dev/null 2>&1; then
    if [ "${INSTALL_DOCKER}" = "1" ]; then
        info "未检测到 Docker，开始安装（get.docker.com）"
        curl -fsSL https://get.docker.com | sh
        systemctl enable --now docker || true
    else
        die "未检测到 Docker。请先安装：curl -fsSL https://get.docker.com | sh
             ，或改用本脚本加 --install-docker 自动安装。"
    fi
fi

if ! docker info >/dev/null 2>&1; then
    die "当前用户无权访问 Docker。请用 sudo 运行本脚本，或执行：sudo usermod -aG docker \$USER（需重新登录生效）。"
fi

if [ "${MODE}" = "compose" ] && ! docker compose version >/dev/null 2>&1; then
    if [ "${INSTALL_DOCKER}" = "1" ] && command -v apt-get >/dev/null 2>&1; then
        info "安装 docker compose 插件"
        apt-get update -y && apt-get install -y docker-compose-plugin
    else
        die "未检测到 docker compose 插件。可安装：sudo apt-get install -y docker-compose-plugin
             ，或改用单容器模式：./deploy.sh --run"
    fi
fi

# ---- 2. 准备 .env ----
if [ ! -f .env ]; then
    [ -f .env.example ] || die "缺少 .env.example，无法生成 .env"
    cp .env.example .env
    info "已根据 .env.example 生成 .env"
fi

if [ -n "${HOST_PORT}" ]; then
    if grep -q '^HOST_PORT=' .env; then
        sed -i "s/^HOST_PORT=.*/HOST_PORT=${HOST_PORT}/" .env
    else
        echo "HOST_PORT=${HOST_PORT}" >> .env
    fi
    info "宿主机端口设为 ${HOST_PORT}"
fi

HOST_PORT="$(grep -m1 '^HOST_PORT=' .env | cut -d= -f2 | tr -d '[:space:]')"
[ -n "${HOST_PORT}" ] || HOST_PORT=18080

if port_in_use "${HOST_PORT}"; then
    warn "宿主机端口 ${HOST_PORT} 已被占用，可能与服务器上已有服务冲突。"
    warn "如需更换，请修改 .env 中的 HOST_PORT 后重新运行本脚本。"
fi

# ---- 3. 构建并启动 ----
if [ "${MODE}" = "compose" ]; then
    info "使用 Docker Compose 部署（web + cron 两个服务）"
    docker compose up -d --build
    WEB_NAME="ntt-web"
else
    info "使用单容器部署（web + 调度在同一容器）"
    docker build -t "${IMAGE}" .
    if docker ps -a --format '{{.Names}}' | grep -qx ntt; then
        info "发现已存在的容器 ntt，先移除再重建（数据卷保留）"
        docker rm -f ntt >/dev/null
    fi
    docker run -d --name ntt --restart unless-stopped --init \
        -e APP_ROLE=all \
        -e TZ=Asia/Shanghai \
        -e PORT=5000 \
        -e NT_DB_PATH=/app/data/data.db \
        -p "${HOST_PORT}:5000" \
        -v "${VOLUME}:/app/data" \
        --memory=320m --cpus=0.75 \
        "${IMAGE}"
    WEB_NAME="ntt"
fi

# ---- 4. 等待服务就绪 ----
info "等待服务就绪（最多 90 秒）"
READY=0
for i in $(seq 1 30); do
    if docker exec "${WEB_NAME}" python -c \
        "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','5000')+'/api/status',timeout=5)" \
        >/dev/null 2>&1; then
        READY=1
        break
    fi
    sleep 3
done

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "${IP}" ] || IP="<服务器IP>"

echo ""
if [ "${READY}" = "1" ]; then
    info "部署完成 ✅  访问：http://${IP}:${HOST_PORT}"
else
    warn "服务已启动但 90 秒内未通过自检（首次启动会先抓取数据，可能较慢）。"
    warn "请稍后查看日志：docker logs ${WEB_NAME}"
fi

cat <<EOF

常用命令：
  查看状态    docker ps --filter name=ntt
  看板日志    docker logs -f ${WEB_NAME}
  抓取日志    $( [ "${MODE}" = "compose" ] && echo "docker compose logs -f cron" || echo "docker logs -f ntt" )
  立即抓一次  $( [ "${MODE}" = "compose" ] && echo "docker compose run --rm -T --entrypoint python cron staticsite.py" || echo "docker exec ntt python staticsite.py" )
  停止/启动   $( [ "${MODE}" = "compose" ] && echo "docker compose down / docker compose up -d" || echo "docker stop ntt / docker start ntt" )
  数据卷位置  docker volume inspect ${VOLUME}

提示：
  · 首次启动会自动从内置种子数据恢复最近 90 天历史，随后定时任务持续累积。
  · 服务器若有防火墙/安全组，需放行 ${HOST_PORT} 端口。
  · 建议用 Nginx 反代并配 HTTPS（见 README「Docker 部署」章节）。
EOF
