#!/usr/bin/env bash
# invest-radar（国家队看板 + QDII 决策面板） —— Debian / Ubuntu VPS 一键部署脚本
#
# 用法：
#   ./deploy.sh                    # 一键：拉取最新代码 + Docker Compose 部署（默认，推荐）
#   ./deploy.sh --no-pull          # 跳过 git 拉取，用当前本地代码直接部署
#   ./deploy.sh --run              # 纯 docker run 单容器部署（≤1GB 内存机器请用这个）
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
  ./deploy.sh --run              # 纯 docker run 单容器部署（≤1GB 内存机器请用这个）
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
        --port)
            # 2026-10-30 修：`--port` 后面没值时 shift 2 会失败、
            # 加上 set -e 直接崩掉整脚本，用户看不到任何提示。
            if [ "$#" -lt 2 ] || [ -z "${2:-}" ]; then
                die "--port 需要跟一个端口号（如 ./deploy.sh --port 50080）"
            fi
            case "$2" in
                *[!0-9]*) die "--port 参数必须是纯数字，收到：$2" ;;
            esac
            GATEWAY_HOST_PORT="$2"; shift 2 ;;
        --install-docker) INSTALL_DOCKER=1; shift ;;
        --no-pull) DO_PULL=0; shift ;;
        -h|--help) usage ;;
        *) die "未知参数：$1（用 --help 查看用法）" ;;
    esac
done

port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1; }

# ---- 0. 防 SSH 断连：交互式运行时自动转后台，前台继续实时跟随日志 ----
# 小内存 VPS 上构建镜像 / 容器启动抓取时可能把负载打满导致 SSH 掉线，
# 而 SSH 一断，会话前台进程会被 SIGHUP 杀掉 —— 部署跑到一半就停了，
# 表现为「git 已拉到最新，但镜像没重建、线上跑的还是旧代码」，极难排查。
#
# 这里在**交互式终端**下把自己转到后台（nohup 免疫 SIGHUP），再用 tail -f 跟随输出：
#   · 进度照旧实时可见（跟以前直接跑一样）
#   · SSH 断连也不会中断，重连后 `tail -f` 那个日志就能接着看
#   · Ctrl+C 只停止跟随，不会中断部署
# 非交互场景（cron / 脚本调用 / 输出已重定向）不做转换，行为与以前完全一致。
DEPLOY_LOG="${DEPLOY_LOG:-/tmp/invest-radar-deploy.log}"
if [ "${NT_DEPLOY_CHILD:-}" != "1" ] && [ -t 1 ]; then
    _self="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
    NT_DEPLOY_CHILD=1 nohup bash "${_self}" "$@" >"${DEPLOY_LOG}" 2>&1 &
    _child=$!
    info "已转入后台运行（PID ${_child}）—— SSH 断连也不会中断部署"
    info "日志：${DEPLOY_LOG}（断线重连后 tail -f 它就能接着看）"
    info "下面是实时进度，Ctrl+C 只停止跟随、不会中断部署"
    echo ""
    sleep 1
    tail -f --pid="${_child}" "${DEPLOY_LOG}" 2>/dev/null || true
    wait "${_child}"; _rc=$?
    echo ""
    if [ "${_rc}" = "0" ]; then
        info "部署脚本执行完毕（退出码 0）"
    else
        warn "部署脚本失败（退出码 ${_rc}），完整日志：${DEPLOY_LOG}"
    fi
    exit "${_rc}"
fi

# 端口占用检测：区分两种情况
#   a) 被**本项目自己的容器**占用 → 升级重建场景，compose 会自动接管，属正常，不是冲突
#   b) 被**其它服务**占用           → 真冲突，才需要提醒换端口
# 2026-10-30 修：旧实现只看 `docker ps` 里有没有 :PORT-> 的端口映射，
# 任意容器（比如服务器上跑的其他 sonarr/jellyfin 占 48881）都会被判成
# 「本项目容器占用 → 属正常升级流程」，把真冲突误报成正常，然后 compose up
# 立刻因端口占用失败、日志里看不到原因。现在按我们的 5 个 container_name
# 白名单筛占用者，命中才认「自己人」。
check_port_conflict() {
    local port="$1"
    local ours_pattern='^(ntt-gateway|ntt-web|ntt-cron|qdii-web|qdii-cron|ntt)$'
    local owners other_owners
    owners="$(docker ps --format '{{.Names}}|{{.Ports}}' 2>/dev/null \
             | awk -F'|' -v p=":${port}->" '$2 ~ p {print $1}' || true)"
    if [ -n "${owners}" ]; then
        # 把占用者分成「我们自己的」和「别人的」
        local our_list other_list
        # ⚠️ 两处 grep 都必须带 `|| true`：脚本开了 set -e，而 grep 无匹配时返回 1，
        # 命令替换的退出码会直接让整条赋值语句失败 —— 升级场景（端口已被本项目容器占用、
        # other_list 必然空）会当场静默退出，屏幕上只看到「代码已是最新」就没了下文。
        our_list="$(printf '%s\n' "${owners}" | grep -E "${ours_pattern}" | tr '\n' ' ' | sed 's/ $//' || true)"
        other_list="$(printf '%s\n' "${owners}" | grep -Ev "${ours_pattern}" | tr '\n' ' ' | sed 's/ $//' || true)"
        if [ -n "${our_list}" ]; then
            info "端口 ${port} 由本项目容器占用（${our_list}），重建时自动接管 —— 属正常升级流程"
        fi
        if [ -n "${other_list}" ]; then
            warn "端口 ${port} 同时被其它容器占用（${other_list}），compose up 会失败"
            warn "请停掉那些容器或换端口：./deploy.sh --port <其它端口>"
            die "端口冲突（详见上方 warn），部署中止"
        fi
        return 0
    fi
    if port_in_use "${port}"; then
        warn "端口 ${port} 已被非 Docker 的其它服务占用。"
        warn "如需更换：./deploy.sh --port <其它端口>，或改 .env 里的 GATEWAY_HOST_PORT。"
    fi
    return 0
}

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
# 同样要 `|| true`：.env 里没有这一行时 grep 返回 1，在 set -e 下会让脚本静默退出
# （下一行还有 48881 兜底，作者本意就是允许它缺失）。
GATEWAY_HOST_PORT="$(grep -m1 '^GATEWAY_HOST_PORT=' .env | cut -d= -f2 | tr -d '[:space:]' || true)"
[ -n "${GATEWAY_HOST_PORT}" ] || GATEWAY_HOST_PORT=48881

if port_in_use "${GATEWAY_HOST_PORT}"; then
    check_port_conflict "${GATEWAY_HOST_PORT}"
fi

# ---- 2.5 小内存机器提示（仅提示，不阻断）----
# Compose 五容器的 --memory 上限合计 832MB，但那只是天花板、不是预留：
# 实测常驻合计约 20MB，1GB 机器跑 Compose 完全没问题（v2.6.4 曾据此误判"必然 OOM"）。
# 真正可能让机器短暂无响应的是 docker build 瞬时峰值 + 抓取任务与其它吃 CPU 服务叠加，
# 而部署本身已自动转后台，SSH 断连也不会中断。这里只在极小内存时给一句可选建议。
if [ "${MODE}" = "compose" ] && [ -r /proc/meminfo ]; then
    _mem_kb="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || true)"
    if [ -n "${_mem_kb}" ] && [ "${_mem_kb}" -lt 900000 ] 2>/dev/null; then
        warn "本机物理内存约 $(( _mem_kb / 1024 ))MB（偏小）。Compose 五容器实测常驻仅约 20MB，可以正常跑；"
        warn "若希望进一步省内存，可选单容器模式：./deploy.sh --run"
    fi
fi

# ---- 3. 构建并启动 ----
# 两种形态互斥：单容器 ntt 与 Compose 五容器共用同一个对外端口和数据卷，
# 同时存在会互相抢端口（Compose 的 ntt-gateway 会一直卡在 Created 起不来），
# 且小内存机器上两套一起跑必然 OOM。故每次部署前先清掉「另一种形态」的残留。
# 只动容器，不碰数据卷 ntt-data，历史数据不受影响。
if [ "${MODE}" = "compose" ]; then
    if docker ps -a --format '{{.Names}}' | grep -qx ntt; then
        warn "检测到单容器 ntt 仍在（占用对外端口 ${GATEWAY_HOST_PORT}）→ 先移除，否则 ntt-gateway 起不来"
        docker rm -f ntt >/dev/null 2>&1 || true
    fi
    info "使用 Docker Compose 部署（ntt-gateway + ntt-web / ntt-cron / qdii-web / qdii-cron 五个服务）"
    docker compose up -d --build
    WEB_NAME="ntt-web"
else
    _leftover=""
    for _n in ntt-gateway ntt-web ntt-cron qdii-web qdii-cron; do
        if docker ps -a --format '{{.Names}}' | grep -qx "${_n}"; then
            _leftover="${_leftover} ${_n}"
        fi
    done
    if [ -n "${_leftover}" ]; then
        warn "检测到 Compose 形态残留（${_leftover}）→ 单容器模式下先移除，避免端口抢占与内存超限"
        for _n in ${_leftover}; do docker rm -f "${_n}" >/dev/null 2>&1 || true; done
    fi
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
    printf '\033[1;36m[deploy]\033[0m 等待服务就绪 %2ds / 90s ...\r' "$((i * 3))"
    sleep 3
done
printf '\033[1;36m[deploy]\033[0m %-46s\n' "自检完成"

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

# ---- 5. 容器状态 + 启动日志：一眼看到「容器到底起来没有」----
echo ""
info "容器状态："
if [ "${MODE}" = "compose" ]; then
    _shown="$(docker ps -a --format '  {{.Names}} : {{.Status}}' \
              | grep -E '^[[:space:]]*(ntt-gateway|ntt-web|ntt-cron|qdii-web|qdii-cron)[[:space:]]:' || true)"
    [ -n "${_shown}" ] && printf '%s\n' "${_shown}" || warn "  未找到本项目容器"
else
    docker ps -a --filter "name=^${WEB_NAME}$" --format '  {{.Names}} : {{.Status}}' || true
fi

echo ""
info "启动日志尾部（docker logs --tail 12 ${WEB_NAME}）："
docker logs --tail 12 "${WEB_NAME}" 2>&1 | sed 's/^/  /' || true

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
