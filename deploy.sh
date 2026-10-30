#!/usr/bin/env bash
# invest-radar（国家队看板 + QDII 决策面板） —— Debian / Ubuntu VPS 一键部署脚本
#
# 用法：
#   ./deploy.sh                    # 一键：拉取最新代码 + Docker Compose 部署（默认，推荐）
#   ./deploy.sh --no-pull          # 跳过 git 拉取，用当前本地代码直接部署
#   ./deploy.sh --run              # 纯 docker run 单容器部署（web + 调度合一）
#   ./deploy.sh --port 48881       # 指定对外端口（默认 48881 -> 容器 38881）
#   ./deploy.sh --install-docker   # 未装 Docker 时自动安装
#   ./deploy.sh --help
#
# 说明：脚本可重复执行。默认先 `git pull --ff-only` 拉最新代码，再重建镜像并启动，
#       所以「升级 = 再跑一次 ./deploy.sh」。
#       本地若有未提交改动会自动跳过拉取（避免冲突），仍用当前代码完成部署。
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${REPO_DIR}"

IMAGE="invest-radar:latest"
VOLUME="ntt-data"
MODE="compose"
INSTALL_DOCKER=0
GATEWAY_HOST_PORT=""
DO_PULL=1

info() { printf '\033[1;36m[deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'USAGE'
用法：
  ./deploy.sh                    # 一键：拉取最新代码 + Docker Compose 部署（默认，推荐）
  ./deploy.sh --no-pull          # 跳过 git 拉取，用当前本地代码直接部署
  ./deploy.sh --run              # 纯 docker run 单容器部署（web + 调度合一）
  ./deploy.sh --port 48881       # 指定对外端口（默认 48881 -> 容器 38881）
  ./deploy.sh --install-docker   # 未装 Docker 时自动安装
  ./deploy.sh --help             # 显示本帮助

说明：
  脚本可重复执行。默认先 git pull --ff-only 拉最新代码，再重建镜像并启动，
  所以「升级 = 再跑一次 ./deploy.sh」。
  本地若有未提交改动会自动跳过拉取（避免冲突），仍用当前代码完成部署。
USAGE
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --run) MODE="run"; shift ;;
        --port) GATEWAY_HOST_PORT="${2:-}"; shift 2 ;;
        --install-docker) INSTALL_DOCKER=1; shift ;;
        --no-pull) DO_PULL=0; shift ;;
        -h|--help) usage ;;
        *) die "未知参数：$1（用 --help 查看用法）" ;;
    esac
done

port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1; }

# ---- 0. 拉取最新代码（一键升级的关键一步）----
# 只在 git 仓库里执行；非 git 目录（手动拷贝的代码）自动跳过，不报错。
pull_latest() {
    if [ "${DO_PULL}" = "0" ]; then
        info "已指定 --no-pull，跳过代码拉取"
        return 0
    fi
    if ! command -v git >/dev/null 2>&1; then
        warn "未检测到 git，跳过代码拉取"
        return 0
    fi
    if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        warn "当前目录不是 git 仓库，跳过代码拉取"
        return 0
    fi

    local branch
    branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
    local before after
    before="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"

    # 有未提交改动时，pull 可能冲突 —— 直接跳过，保证「脚本一定能跑完」
    if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
        warn "检测到本地有未提交改动，跳过 git pull（如需强制更新请先 git stash）"
        return 0
    fi

    info "拉取最新代码（git pull --ff-only，分支 ${branch}）"
    if git pull --ff-only; then
        after="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
        if [ "${before}" != "${after}" ]; then
            info "代码已更新：${before} → ${after}"
        else
            info "代码已是最新（${after}）"
        fi
    else
        warn "git pull 失败，继续用本地现有代码部署（如需重试请手动 git pull）"
    fi
}

pull_latest

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

if [ -n "${GATEWAY_HOST_PORT}" ]; then
    if grep -q '^GATEWAY_HOST_PORT=' .env; then
        sed -i "s/^GATEWAY_HOST_PORT=.*/GATEWAY_HOST_PORT=${GATEWAY_HOST_PORT}/" .env
    else
        echo "GATEWAY_HOST_PORT=${GATEWAY_HOST_PORT}" >> .env
    fi
    info "对外端口设为 ${GATEWAY_HOST_PORT}"
fi

# 对外只有这一个端口：宿主机 GATEWAY_HOST_PORT -> 容器 GATEWAY_PORT(38881)
GATEWAY_HOST_PORT="$(grep -m1 '^GATEWAY_HOST_PORT=' .env | cut -d= -f2 | tr -d '[:space:]')"
[ -n "${GATEWAY_HOST_PORT}" ] || GATEWAY_HOST_PORT=48881

if port_in_use "${GATEWAY_HOST_PORT}"; then
    warn "端口 ${GATEWAY_HOST_PORT} 已被占用，可能与服务器上已有服务冲突。"
    warn "如需更换：./deploy.sh --port <其它端口>，或改 .env 里的 GATEWAY_HOST_PORT。"
fi

# ---- 3. 构建并启动 ----
if [ "${MODE}" = "compose" ]; then
    info "使用 Docker Compose 部署（ntt-gateway + ntt-web / ntt-cron / qdii-web / qdii-cron 五个服务）"
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
        -e GATEWAY_PORT=38881 \
        -e PORT=5000 \
        -e NT_DB_PATH=/app/data/data.db \
        -e QDII_PORT=5001 \
        -e NT_QDII_DB_PATH=/app/data/qdii.db \
        -p "${GATEWAY_HOST_PORT}:38881" \
        -v "${VOLUME}:/app/data" \
        --memory=384m --cpus=0.75 \
        "${IMAGE}"
    WEB_NAME="ntt"
fi

# ---- 4. 等待服务就绪 ----
info "等待服务就绪（最多 90 秒）"
READY=0
for i in $(seq 1 30); do
    if docker exec "${WEB_NAME}" python /app/docker/healthcheck.py >/dev/null 2>&1; then
        READY=1
        break
    fi
    sleep 3
done

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "${IP}" ] || IP="<服务器IP>"

echo ""
if [ "${READY}" = "1" ]; then
    info "部署完成 ✅"
    info "  国家队看板：http://${IP}:${GATEWAY_HOST_PORT}"
    info "  QDII 面板 ：http://${IP}:${GATEWAY_HOST_PORT}/qdii/"
else
    warn "服务已启动但 90 秒内未通过自检（首次启动会先抓取数据，可能较慢）。"
    warn "请稍后查看日志：docker logs ${WEB_NAME}"
fi

cat <<EOF

常用命令：
  查看状态    docker ps --filter name=ntt ; docker ps --filter name=qdii
  看板日志    docker logs -f ${WEB_NAME}
  抓取日志    $( [ "${MODE}" = "compose" ] && echo "docker compose logs -f cron qdii-cron" || echo "docker logs -f ntt" )
  立即抓一次  $( [ "${MODE}" = "compose" ] && echo "docker compose run --rm -T --entrypoint python cron national_team/staticsite.py" || echo "docker exec ntt python national_team/staticsite.py" )
  QDII 抓一次 $( [ "${MODE}" = "compose" ] && echo "docker compose run --rm -T --entrypoint python qdii-cron qdii/qdii_scheduler.py --once" || echo "docker exec ntt python qdii/qdii_scheduler.py --once" )
  停止/启动   $( [ "${MODE}" = "compose" ] && echo "docker compose down / docker compose up -d" || echo "docker stop ntt / docker start ntt" )
  数据卷位置  docker volume inspect ${VOLUME}

提示：
  · 首次启动会自动从内置种子数据恢复最近 90 天历史，随后定时任务持续累积。
  · QDII 的场内溢价/分时类数据从部署当天起才累积，历史无法回补（上游仅给近几日）。
  · 网关监听容器内 38881，宿主机映射 0.0.0.0:${GATEWAY_HOST_PORT}（NPM 等反代可经网桥访问）。
  · 公网访问走 NPM → 宿主机 ${GATEWAY_HOST_PORT} → 443，防火墙/安全组只放行 22/80/443 即可。
  · 若用 NPM（容器部署）反代：Forward Hostname 填宿主机内网 IP（如 172.17.0.1），Forward Port 填 ${GATEWAY_HOST_PORT}。
  · 后续升级只需再跑一次本脚本（自动 git pull + 重建 + 重启）。
  · 建议用 Nginx 反代并配 HTTPS（见 README「Docker 部署」章节）。
EOF
