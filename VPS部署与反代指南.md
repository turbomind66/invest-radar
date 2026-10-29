# VPS 部署 + Nginx Proxy Manager 域名反代指南

> 适用：Debian / Ubuntu VPS，用 Docker Compose 部署 **invest-radar**（国家队看板 + 海外 QDII 定投决策面板），
> 前面放 Nginx Proxy Manager（NPM）做域名反代 + HTTPS，域名已在 Cloudflare 登记。
>
> 端口关系（务必记住这三个）：
>
> | 服务 | 容器内端口 | 宿主机端口 | 用途 |
> |---|---|---|---|
> | `web`（国家队看板） | **5000** | 18080 | 页面 + `/api/data` |
> | `qdii-web`（QDII 面板） | **5001** | 18081 | 页面 + `/api/qdii/*` |
> | `cron` / `qdii-cron` | — | 不暴露 | 定时抓取 |

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

⚠️ **不要放行 18080 / 18081**。它们只作为 NPM 的转发目标，公网只走 80/443。
（如果你的 NPM 与本项目在同一台机器上，见第五节「情况 A」时其实连这两个端口都不需要对外暴露。）

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

必改项：

```dotenv
# 时区：调度与交易日判定都依赖它，务必保持北京时间
TZ=Asia/Shanghai

# 宿主机端口：改成 VPS 上没被占用的
HOST_PORT=18080
QDII_HOST_PORT=18081

# 页头「面板互跳」地址（域名反代后必填，见下方说明）
PEER_URL=https://qdii.你的域名
QDII_PEER_URL=https://radar.你的域名
```

> **为什么要填 PEER_URL**：两个面板页头的互跳入口原本按「本端口 ±1」推断（5000↔5001）。
> 用域名反代后两边端口都是 443，端口推断会指向**不存在的 444/442 端口 → 死链**。
> 填了这两个变量，服务端会把真实地址注入页面；留空则回退到端口推断（本机不同端口直访仍正常）。

---

## 四、启动与验证

```bash
chmod +x deploy.sh
./deploy.sh            # 等价于 docker compose up -d --build
```

四个服务都会起：`ntt-web` / `ntt-cron` / `qdii-web` / `qdii-cron`。

```bash
docker compose ps                      # 看状态
docker compose logs -f qdii-cron       # 跟抓取日志
curl -s http://127.0.0.1:18080/api/status   | head -c 200
curl -s http://127.0.0.1:18081/api/qdii/status | head -c 200
```

- **首次启动**会用镜像内置的 `docs/data.json` 种子恢复最近 90 天历史，稍等几分钟；
- QDII 的分时/逐笔类数据**从部署当天起才累积**（上游只给近几日，历史不可回补）；
- 数据全部在命名卷 `invest-radar_ntt-data`（`data.db` 与 `qdii.db` 同卷分离），重建容器不丢。

---

## 五、Nginx Proxy Manager 反代

先确认你的 NPM 是**容器**还是**宿主机程序**（`docker ps | grep -i proxy`）。

### 情况 A（推荐）：NPM 也是 Docker 容器 → 共用同一 docker 网络

这样**完全不需要暴露 18080/18081**，NPM 直接用「容器名 + 容器内端口」转发，最干净。

**1) 建网络**

```bash
docker network create proxy
```

**2) 让两个 web 容器加入该网络**（用 override 文件，不改仓库代码）

在 `/opt/invest-radar/invest-radar/` 下新建 `docker-compose.override.yml`：

```yaml
services:
  web:
    networks: [default, proxy]
  qdii-web:
    networks: [default, proxy]

networks:
  proxy:
    external: true
    name: proxy
```

```bash
docker compose up -d          # compose 会自动合并 override
```

**3) 让 NPM 容器也进同一网络**

```bash
docker network connect proxy <NPM容器名>      # 例如 npm-app
# 或在你自己的 NPM compose 里加 networks: [proxy]
```

**4) 在 NPM 面板里建两个 Proxy Host**

面板地址通常是 `http://VPS_IP:81`（首次登录账号 `admin@example.com` / 密码 `changeme`，**登录后立刻改**）。

| | Proxy Host #1 | Proxy Host #2 |
|---|---|---|
| Domain Names | `radar.你的域名` | `qdii.你的域名` |
| Scheme | `http` | `http` |
| Forward Hostname / IP | **`web`** | **`qdii-web`** |
| Forward Port | **`5000`** | **`5001`** |
| Block Common Exploits | ✅ | ✅ |
| Websockets Support | ✅（ECharts 不需要，但开着无害） | ✅ |
| SSL | 见第六节 | 见第六节 |

> 注意这里填的是**容器内端口 5000 / 5001**，不是 18080/18081 —— 因为 NPM 与 web 容器在同一网络里，直接按容器名寻址。

### 情况 B：NPM 装在宿主机上（apt / 二进制）

| | Proxy Host #1 | Proxy Host #2 |
|---|---|---|
| Forward Hostname / IP | `127.0.0.1` | `127.0.0.1` |
| Forward Port | `18080` | `18081` |

### 情况 C：NPM 是容器，但两者**不在同一网络**

| | Proxy Host #1 | Proxy Host #2 |
|---|---|---|
| Forward Hostname / IP | **宿主机的内网 IP** | 同左 |
| Forward Port | `18080` | `18081` |

宿主机的内网 IP 用 `hostname -I | awk '{print $1}'` 查（通常 `172.17.0.1` 或 `10.x.x.x`）。

> ⚠️ 这里**不能填 `127.0.0.1`** —— 在 NPM 容器里，`127.0.0.1` 指的是 NPM 容器自己，必然 502。

---

## 六、Cloudflare 设置

1. **DNS**：加两条 A 记录，指向 VPS 公网 IP
   - `radar` → VPS IP
   - `qdii` → VPS IP
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
| 访问域名 502 | 容器没起 / NPM 填错端口 | `docker compose ps`；NPM 的 Forward Port 填**容器内端口**（情况 A）或宿主映射端口（情况 B/C） |
| 页头「QDII 面板」是死链 | 没配 `PEER_URL` / `QDII_PEER_URL` | 改 `.env` 后 `docker compose up -d` |
| 证书签不下来 | 80 端口被占 / DNS 未生效 | 临时灰云再签，或改用 Cloudflare Origin Certificate |
| 一直重定向 / 502 | Cloudflare SSL 模式是 Flexible | 改成 **Full (strict)** |
| 页面有数据但很旧 | cron 容器没跑 | `docker compose logs qdii-cron`；确认 `TZ=Asia/Shanghai` |
| 数据是空的 | 首次启动还在恢复种子 | 等 2~5 分钟再看 `docker compose logs cron` |
| 抓取一直失败 | VPS 出网被墙 | 容器内需能直连新浪/东财；必要时给容器配代理环境变量 |

---

## 九、附：与 GitHub Pages 静态站的关系

- `https://turbomind66.github.io/invest-radar/` 是**静态快照**（每天定时导出），只读、不实时；
- 本指南部署的是**动态服务**（本地/容器实时抓取 + 实时接口），两者可并存，互不影响。
