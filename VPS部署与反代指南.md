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

# 页头「面板选项卡」互跳地址（域名反代后必填，写法见第五节）
PEER_URL=/qdii/
QDII_PEER_URL=/nt/
```

> **为什么要填 PEER_URL**：两个面板页头的选项卡互跳原本按「本端口 ±1」推断（5000↔5001）。
> 用域名反代后两边端口都是 443，端口推断会指向**不存在的 444/442 端口 → 死链**。
> 填了这两个变量，服务端会把真实地址注入页面。
> **同域不同路径时直接写相对路径**（`/qdii/`、`/nt/`）最省事，换域名都不用改；跨域名才写完整 URL。
> 留空则回退到端口推断（本机不同端口直访仍正常）。
>
> 如果你不用路径前缀、而是两个独立子域名，就写完整 URL：
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

### 5.1 先选拓扑：一个域名还是两个域名

| | **方案一：一个域名 + 路径前缀**（推荐） | 方案二：两个子域名 |
|---|---|---|
| 国家队 | `radar.你的域名` | `radar.你的域名` |
| QDII | `radar.你的域名/qdii/` | `qdii.你的域名` |
| 页头选项卡 | 两个面板顶部都有「国家队 A股 / 全球 QDII 定投」，**点一下即在本域内切换** | 同样有选项卡，点击跳到另一个子域名 |
| Cloudflare DNS | 1 条 A 记录 | 2 条 A 记录 |
| NPM Proxy Host | 1 个 + 2 个 Custom Location | 2 个 |
| `.env` 里的 PEER_URL | 相对路径 `/qdii/`、`/nt/` | 完整 URL |

两者页面体验几乎一样（都是页头选项卡），**方案一少一条 DNS、少一个证书、换域名不用改配置**，所以默认推荐。

### 5.2 前置：让 NPM 能找到两个 web 容器

先确认 NPM 是**容器**还是**宿主机程序**（`docker ps | grep -i proxy`）。

**NPM 是容器（最常见）** → 让三者共用同一 docker 网络，之后 NPM 直接用「容器名 + 容器内端口」转发，
**完全不需要暴露 18080/18081**：

```bash
docker network create proxy          # 已存在会报错，忽略即可
```

在仓库根目录（`/opt/invest-radar/invest-radar/`）新建 `docker-compose.override.yml`（不改仓库代码）：

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
docker compose up -d                        # compose 自动合并 override
docker network connect proxy <NPM容器名>     # 例如 npm-app
```

**NPM 装在宿主机上（apt / 二进制）** → 跳过上面的网络步骤，直接把请求转发到 `127.0.0.1:18080 / 18081`。

### 5.3 方案一：一个域名 + 两个选项卡（推荐）

NPM 面板地址通常是 `http://VPS_IP:81`（首次登录 `admin@example.com` / `changeme`，**登录后立刻改**）。

**第一步：建 1 个 Proxy Host**

| 字段 | 填什么 |
|---|---|
| Domain Names | `radar.你的域名` |
| Scheme | `http` |
| Forward Hostname / IP | `web`（NPM 在宿主机则填 `127.0.0.1`） |
| Forward Port | `5000`（NPM 在宿主机则填 `18080`） |
| Block Common Exploits | ✅ |
| Websockets Support | ✅（ECharts 不需要，开着无害） |

保存后访问 `https://radar.你的域名` 应该直接是国家队看板。

**第二步：给这个 Proxy Host 加 2 个 Custom Location**

打开该 Proxy Host → 上方 **Custom Locations** 标签 → Add location：

| | Location #1（QDII 面板） | Location #2（国家队别名，可选） |
|---|---|---|
| Define Location | `/qdii` | `/nt` |
| Scheme | `http` | `http` |
| Forward Hostname / IP | `qdii-web` | `web` |
| Forward Port | `5001` | `5000` |
| **Forward Path** | **`/`** ← 关键 | **`/`** |
| 齿轮里的 rewrite / 其它 | 不勾 | 不勾 |

> **Forward Path 填一个斜杠 `/` 就是「把 `/qdii` 前缀剥掉再转给后端」**：
> `/qdii/api/qdii/status` → `qdii-web:5001/api/qdii/status`。
> 别写成 `/qdii` —— 那是「把前缀换成 /qdii」，会变成 `/qdii/api/...` 再打一次，反而多套一层。
>
> **如果你的 NPM 版本没有 Forward Path 字段**：留空即可（前缀会原样透传给后端），
> 然后在 `.env` 里补上 `QDII_BASE_PATH=/qdii`（`/nt` 那条补 `BASE_PATH=/nt`），
> 服务端会自己把前缀剥掉。页面前端已按当前路径自动拼接口地址，两种方式都能跑。

**第三步：`.env` 里配对端地址**

```dotenv
PEER_URL=/qdii/      # 国家队页的「全球 QDII 定投」选项卡 → /qdii/
QDII_PEER_URL=/nt/   # QDII 页的「国家队 A股」选项卡 → /nt/（走 Location #2，也可写 /）
```

```bash
docker compose up -d     # 让新的环境变量生效
```

刷新页面，两个面板顶部都会出现选项卡，当前所在面板高亮，点另一个直接切换。

> **为什么页头选项卡能跨路径**：页面里所有接口请求（`/api/data`、`/api/qdii/*`）都会自动带上
> 「当前页面所在路径」作为前缀 —— 在 `/` 下请求 `/api/...`，在 `/qdii/` 下请求 `/qdii/api/...`。
> 所以**不需要**给两个应用配额外的 URL 前缀参数，本地 `localhost:5000/5001` 直连行为完全不变。

### 5.4 方案二：两个子域名

建 2 个 Proxy Host，不配 Custom Location：

| | Proxy Host #1（国家队） | Proxy Host #2（QDII） |
|---|---|---|
| Domain Names | `radar.你的域名` | `qdii.你的域名` |
| Scheme | `http` | `http` |
| Forward Hostname / IP | **`web`** | **`qdii-web`** |
| Forward Port | **`5000`** | **`5001`** |
| Block Common Exploits | ✅ | ✅ |
| Websockets Support | ✅ | ✅ |
| SSL | 见第六节 | 见第六节 |

> 这里填的是**容器内端口 5000 / 5001**，不是 18080/18081 —— NPM 与 web 容器在同一网络里时按容器名寻址。
> `.env` 里则写完整 URL：`PEER_URL=https://qdii.你的域名`、`QDII_PEER_URL=https://radar.你的域名`。

### 5.5 NPM 不在同一网络时的填法

| 场景 | Forward Hostname / IP | Forward Port |
|---|---|---|
| NPM 装在宿主机（apt / 二进制） | `127.0.0.1` | `18080` / `18081` |
| NPM 是容器，但**不在** `proxy` 网络 | **宿主机内网 IP** | `18080` / `18081` |

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
| 访问域名 502 | 容器没起 / NPM 填错端口 | `docker compose ps`；NPM 的 Forward Port 填**容器内端口**（同网络）或宿主映射端口（宿主机/跨网络） |
| 页头选项卡不出现 | 没配 `PEER_URL` / `QDII_PEER_URL`，且端口推断失效 | `.env` 写 `/qdii/`、`/nt/` 后 `docker compose up -d` |
| 选项卡能点但 404 | Custom Location 的 Location 名与 `PEER_URL` 不一致 | 例如 Location 建的是 `/qdii`，`PEER_URL` 却写 `/qdii-panel/` |
| `/qdii/` 打开是空白/接口全 404 | Forward Path 没填，且没设 `QDII_BASE_PATH` | Forward Path 填 `/`；或 `.env` 设 `QDII_BASE_PATH=/qdii` |
| `/qdii/api/...` 返回另一个面板的数据 | Forward Path 留空、后端也不剥前缀 → 命中根 location | 同上 |
| 证书签不下来 | 80 端口被占 / DNS 未生效 | 临时灰云再签，或改用 Cloudflare Origin Certificate |
| 一直重定向 / 502 | Cloudflare SSL 模式是 Flexible | 改成 **Full (strict)** |
| 页面有数据但很旧 | cron 容器没跑 | `docker compose logs qdii-cron`；确认 `TZ=Asia/Shanghai` |
| 数据是空的 | 首次启动还在恢复种子 | 等 2~5 分钟再看 `docker compose logs cron` |
| 抓取一直失败 | VPS 出网被墙 | 容器内需能直连新浪/东财；必要时给容器配代理环境变量 |

---

## 九、附：与 GitHub Pages 静态站的关系

- `https://turbomind66.github.io/invest-radar/` 是**静态快照**（每天定时导出），只读、不实时；
- 本指南部署的是**动态服务**（本地/容器实时抓取 + 实时接口），两者可并存，互不影响。
