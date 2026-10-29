# VPS 部署 + Nginx Proxy Manager 域名反代指南

> 适用：Debian / Ubuntu VPS，用 Docker Compose 部署 **invest-radar**（国家队看板 + 海外 QDII 定投决策面板），
> 前面放 Nginx Proxy Manager（NPM）做域名反代 + HTTPS，域名已在 Cloudflare 登记。
>
> 端口关系（**对外只有一个**）：
>
> | 服务 | 容器内端口 | 宿主机端口 | 用途 |
> |---|---|---|---|
> | `gateway`（单端口入口） | **38881** | **48881** | 对外唯一入口，`/` → 国家队、`/qdii/` → QDII |
> | `web`（国家队看板） | 5000 | 不映射 | 页面 + `/api/data`，仅容器网络内可见 |
> | `qdii-web`（QDII 面板） | 5001 | 不映射 | 页面 + `/api/qdii/*`，仅容器网络内可见 |
> | `cron` / `qdii-cron` | — | 不暴露 | 定时抓取 |
>
> 直连测试：`http://<服务器IP>:48881/`（国家队）、`http://<服务器IP>:48881/qdii/`（QDII）。
> 两个面板页头都有选项卡，点一下即在本域内切换。

---

## 一、服务器准备

```bash
# 1) Docker + Compose 插件
curl -fsSL https://get.docker.com | sh
sudo apt-get update -y && sudo apt-get install -y docker-compose-plugin
sudo usermod -aG docker $USER && newgrp docker      # 重新登录后生效

# 2) 防火墙：只开 22 / 80 / 443
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
sudo ufw enable
```

⚠️ **对外只需考虑 48881 一个端口**（容器内 38881）。如果前面挂 NPM，公网通常只走 80/443，
48881 甚至可以不对外放行、只让 NPM 在内网访问它（见第五节）。

---

## 二、拉代码

```bash
sudo mkdir -p /opt/invest-radar && sudo chown $USER:$USER /opt/invest-radar
cd /opt/invest-radar
git clone https://github.com/turbomind66/invest-radar.git
cd invest-radar          # ← compose 文件在仓库根目录
```

---

## 三、配置 .env（关键）

```bash
cp .env.example .env
vi .env
```

必改项（其实只有时区和域名相关，端口已固定）：

```dotenv
# 时区：调度与交易日判定都依赖它，务必保持北京时间
TZ=Asia/Shanghai

# 对外唯一端口：宿主机 48881 -> 容器 38881
# 48881 被占用时改这里（或 ./deploy.sh --port 50080），只影响这一个数字
GATEWAY_HOST_PORT=48881
GATEWAY_PORT=38881

# 页头「面板选项卡」互跳地址（走网关时用相对路径，换域名不用改）
PEER_URL=/qdii/
QDII_PEER_URL=/nt/
```

> **为什么端口只有一个**：两个看板是两个独立进程，本来就各占一个内部端口（5000 / 5001）。
> 镜像里加了一层 `gateway.py`（零依赖，按路径分发），对外只暴露 **38881**：
> `/` 与 `/nt/` 给国家队、`/qdii/` 给 QDII。所以无论是直连还是 NPM 反代，**都只需要认一个端口**。
>
> **为什么要填 PEER_URL**：页头选项卡原本按「本端口 ±1」推断对端地址，走网关后这个推断失效。
> 走网关时两个面板在同一个域名下（`/` 与 `/qdii/`），写相对路径最省事；独立子域名才写完整 URL。
> ```dotenv
> PEER_URL=https://qdii.你的域名
> QDII_PEER_URL=https://radar.你的域名
> ```

---

## 四、启动与验证

```bash
chmod +x deploy.sh
./deploy.sh            # 等价于 docker compose up -d --build
```

五个服务都会起：`ntt-gateway` / `ntt-web` / `ntt-cron` / `qdii-web` / `qdii-cron`。

```bash
docker compose ps                                   # 看状态（gateway 应为 healthy）
docker compose logs -f qdii-cron                    # 跟抓取日志
curl -s http://127.0.0.1:48881/api/status      | head -c 200   # 国家队（经网关）
curl -s http://127.0.0.1:48881/qdii/api/qdii/status | head -c 200   # QDII（经网关）
```

- **首次启动**会用镜像内置的 `docs/data.json` 种子恢复最近 90 天历史，稍等几分钟；
- QDII 的分时/逐笔类数据**从部署当天起才累积**（上游只给近几日，历史不可回补）；
- 数据全部在命名卷 `invest-radar_ntt-data`（`data.db` 与 `qdii.db` 同卷分离），重建容器不丢。

---

## 五、Nginx Proxy Manager 反代

### 5.1 拓扑：一个域名一个端口就够了

镜像里带了一层 `gateway.py`，已经按路径把两个面板分好流，所以 **NPM 只需要配一个 Proxy Host、指向一个端口**：

| | **方案一：一个域名（推荐）** | 方案二：两个子域名 |
|---|---|---|
| 国家队 | `radar.你的域名/` | `radar.你的域名` |
| QDII | `radar.你的域名/qdii/` | `qdii.你的域名` |
| 页头选项卡 | 两个面板顶部都有，**点一下即在本域内切换** | 同样有选项卡，点击跳到另一个子域名 |
| NPM 配置 | **1 个 Proxy Host → `gateway:38881`**，不需要 Custom Location | 2 个 Proxy Host |
| Cloudflare DNS | 1 条 A 记录 | 2 条 A 记录 |
| `.env` 里的 PEER_URL | 相对路径 `/qdii/`、`/nt/` | 完整 URL |

方案一少一条 DNS、少一个证书、换域名不用改配置，**默认就用它**。

### 5.2 前置：让 NPM 能找到 gateway 容器

先确认 NPM 是**容器**还是**宿主机程序**（`docker ps | grep -i proxy`）。

**NPM 是容器（最常见）** → 只需把 `gateway` 这一个容器接进 NPM 的网络（两个看板留在内网，不接也一样能访问）：

```bash
docker network create proxy          # 已存在会报错，忽略即可
```

在仓库根目录（`/opt/invest-radar/invest-radar/`）新建 `docker-compose.override.yml`（不改仓库代码）：

```yaml
services:
  gateway:
    networks: [default, proxy]

networks:
  proxy:
    external: true
    name: proxy
```

```bash
docker compose up -d                        # compose 自动合并 override
docker network connect proxy <NPM容器名>     # 例如 npm-app
```

**NPM 装在宿主机上（apt / 二进制）** → 跳过上面的网络步骤，转发到 `127.0.0.1:48881` 即可。

### 5.3 方案一：一个 Proxy Host 搞定（推荐）

NPM 面板地址通常是 `http://VPS_IP:81`（首次登录 `admin@example.com` / `changeme`，**登录后立刻改**）。

| 字段 | 填什么 |
|---|---|
| Domain Names | `radar.你的域名` |
| Scheme | `http` |
| Forward Hostname / IP | **`gateway`**（NPM 在宿主机则填 `127.0.0.1`） |
| Forward Port | **`38881`**（NPM 在宿主机则填 `48881`） |
| Block Common Exploits | ✅ |
| Websockets Support | ✅（ECharts 不需要，开着无害） |
| SSL | 见第六节 |

**不要配 Custom Location** —— 路径分发已经由网关做掉了：
`/` 与 `/nt/` → 国家队（容器 5000），`/qdii/` → QDII（容器 5001）。

保存后：

- `https://radar.你的域名/` → 国家队看板
- `https://radar.你的域名/qdii/` → QDII 面板
- 两边页头都有选项卡，点一下直接切

`.env` 保持默认的相对路径即可：

```dotenv
PEER_URL=/qdii/      # 国家队页的「全球 QDII 定投」选项卡 → /qdii/
QDII_PEER_URL=/nt/   # QDII 页的「国家队 A股」选项卡 → /nt/
```

```bash
docker compose up -d     # 让环境变量生效
```

> **为什么这样最省事**：网关负责剥前缀，页面里的接口请求又会按「当前访问路径」自动拼前缀
> （在 `/qdii/` 下请求 `/qdii/api/...`），两边一夹，后端不需要任何额外配置。
> 因此也不存在「NPM 的 Forward Path 填 `/` 还是空」这类坑。

### 5.4 方案二：两个子域名

仍指向同一个网关，只是路径不同、域名不同：

| | Proxy Host #1（国家队） | Proxy Host #2（QDII） |
|---|---|---|
| Domain Names | `radar.你的域名` | `qdii.你的域名` |
| Forward Hostname / IP | `gateway` | `gateway` |
| Forward Port | `38881` | `38881` |
| Forward Path（自定义路径） | `/` | **`/qdii`** |

> 这样 `https://qdii.你的域名/` 实际打到网关的 `/qdii/`。
> 若 NPM 版本不支持 Forward Path，也可改成直接代理到后端容器：
> `qdii-web` + `5001`（此时 `.env` 里设 `QDII_BASE_PATH=`，保持空）。
>
> `.env` 里则要写完整 URL：`PEER_URL=https://qdii.你的域名`、`QDII_PEER_URL=https://radar.你的域名`。

### 5.5 NPM 不在同一网络时的填法

| 场景 | Forward Hostname / IP | Forward Port |
|---|---|---|
| NPM 装在宿主机（apt / 二进制） | `127.0.0.1` | `48881` |
| NPM 是容器，但**不在** `proxy` 网络 | **宿主机内网 IP** | `48881` |

宿主机内网 IP 用 `hostname -I | awk '{print $1}'` 查（通常 `172.17.0.1` 或 `10.x.x.x`）。

> ⚠️ 这时**不能填 `127.0.0.1`** —— 在 NPM 容器里，`127.0.0.1` 指的是 NPM 容器自己，必然 502。

---

## 六、Cloudflare 设置

1. **DNS**：加 A 记录，指向 VPS 公网 IP
   - **方案一（单域名）**：只需 `radar` 一条
   - **方案二（双子域名）**：`radar` 与 `qdii` 各一条
   - 代理状态：橙云（已代理）或灰云（仅 DNS）都行；见下方证书说明。
2. **SSL/TLS → Overview**：模式选 **Full (strict)**
   - ❌ **不要用 Flexible** —— 会造成「NPM 强制跳 HTTPS ↔ Cloudflare 回源 HTTP」的重定向循环。
3. **证书怎么签**（二选一）：
   - **A. Cloudflare Origin Certificate**（推荐，15 年有效、最省事）
     Cloudflare → SSL/TLS → Origin Server → Create Certificate → 选 "Cloudflare Origin Certificate"，生成 **证书 + 私钥**；
     NPM 的 Proxy Host → SSL → Custom SSL Certificate，粘贴这两段内容 → Save。
   - **B. NPM 内置 Let's Encrypt**
     Proxy Host → SSL → Request a new SSL Certificate → 勾选 *Force SSL* / *HTTP/2* / *HSTS* → 填域名 → Request。
     需要 **80 端口对外可达**且 DNS 已指向本机。Cloudflare 橙云一般也能签（会转发 80）；
     签不下来就把该条记录临时切成 **DNS only（灰云）**，签完再切回橙云。
4. **NPM 管理面板（81 端口）不要暴露公网**：改端口、加 IP 白名单，或用 Cloudflare Access 保护。

---

## 七、日常运维

```bash
cd /opt/invest-radar/invest-radar

# 升级（拉代码后重建镜像，数据卷保留）
git pull && docker compose up -d --build

# 看日志
docker compose logs -f web
docker compose logs -f qdii-cron

# 手动立刻抓一次
docker compose run --rm -T --entrypoint python qdii-cron qdii/qdii_scheduler.py --once

# 数据卷位置 / 备份
docker volume inspect invest-radar_ntt-data
docker run --rm -v invest-radar_ntt-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/ntt-data-$(date +%F).tar.gz -C /data .
```

---

## 八、故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 访问域名 502 | 容器没起 / NPM 填错端口 | `docker compose ps` 看 `ntt-gateway` 是否 healthy；NPM 的 Forward Port 应为 **38881**（同网络）或 **48881**（宿主机/跨网络） |
| 502 且页面提示「无法连接上游」 | 网关起来了但后端没起 | 网关会把目标地址和原因写在响应里，照着查 `ntt-web` / `qdii-web` 是否 healthy |
| 页头选项卡不出现 | 没配 `PEER_URL` / `QDII_PEER_URL`，端口推断又失效 | `.env` 写 `/qdii/`、`/nt/` 后 `docker compose up -d` |
| `/qdii/` 打开是 404 | `qdii-web` 没起来，或改过 `QDII_PREFIX` | `docker compose ps`；默认前缀就是 `/qdii` |
| `/qdii/api/...` 打到国家队的数据 | 网关的 `QDII_PREFIX` 与 `PEER_URL` 不一致 | 两者保持一致（默认都 `/qdii`） |
| 直连 `IP:48881` 打不开 | 防火墙/安全组没放行，或 `GATEWAY_HOST_PORT` 被占 | 放行 48881；`./deploy.sh --port 50080` 换端口 |
| 证书签不下来 | 80 端口被占 / DNS 未生效 | 临时灰云再签，或改用 Cloudflare Origin Certificate |
| 一直重定向 / 502 | Cloudflare SSL 模式是 Flexible | 改成 **Full (strict)** |
| 页面有数据但很旧 | cron 容器没跑 | `docker compose logs qdii-cron`；确认 `TZ=Asia/Shanghai` |
| 数据是空的 | 首次启动还在恢复种子 | 等 2~5 分钟再看 `docker compose logs cron` |
| 抓取一直失败 | VPS 出网被墙 | 容器内需能直连新浪/东财；必要时给容器配代理环境变量 |

---

## 九、附：与 GitHub Pages 静态站的关系

- `https://turbomind66.github.io/invest-radar/` 是**静态快照**（每天定时导出），只读、不实时；
- 本指南部署的是**动态服务**（本地/容器实时抓取 + 实时接口），两者可并存，互不影响。
