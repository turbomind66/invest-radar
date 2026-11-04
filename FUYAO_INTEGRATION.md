# fuyao 同花顺金融数据 API —— 增强源接入方案

> 定稿：2026-11-04 ｜ 状态：**方案已落地，代码已按本方案实现** ｜ 定位：**增强源，非唯一源**
> 站点：<https://fuyao.aicubes.cn/> ｜ 文档：`llms.txt` / `llms-full.txt`
> 决策人：丁总 ——「填了 API KEY 就可以作为增强，不填就不用」。

---

## 一、结论先行

fuyao 是阿里云 AIcubes 托管的**同花顺金融数据 API**（REST + MCP 双形态），
鉴权用请求头 `X-api-key`，数据来自同花顺官方。**当前免费**，但只解决国家队层
「行情 / ETF / 新闻」三块，**解决不了本面板最痛的主力净额与逐笔大单**，也完全没有
海外数据（QDII 层用不上）。

因此按丁总定位接入：**有 key 才启用、只在原源缺数据的缝隙里补、任何异常静默回退、
无 key 时与原版行为完全一致（零额外请求）**。

### 1.1 能否免费调用

- 全站文档（含本地完整版 `llms-full.txt`）**无任何**「付费 / 会员 / 配额 / QPS 上限 /
  调用上限」字样；唯一限流声明是「本服务**当前**不限制累计调用次数，触发限流返回
  HTTP 429 / `code=4001`」。
- 实测：连续 12 次突发调用快照，全部 HTTP 200、无 429，周期性抓取级用量绰绰有余。
- ⚠️ **"当前"是临时表述，无永久免费 / SLA 承诺**，且部分接口标「敬请期待」，产品仍在演进。
  ⇒ 只能当增强源，绝不能成为唯一依赖。

### 1.2 真实覆盖率（**全部用真实 key 实测**，非文档推断）

| invest-radar 模块 | 现有源（不稳） | fuyao 端点 | 实测结果 | 本次是否接入 |
|---|---|---|---|---|
| 国家队·**新闻** | 东财 / 同花顺 / 新浪（VPS 被拦） | `/api/news/flash/list`、`/api/news/stock/timeline-list` | ✅ `code:0`，真实快讯 20 条 / 个股新闻 20 条 | **接入**（第 4 候选源） |
| 国家队·**ETF 行情** | 新浪 `hq.sinajs.cn`（限流） | `/api/fund/market/snapshot` | ✅ `code:0`，510300 / 159919 均真实返回 | **接入**（仅补缺） |
| 国家队·**ETF 历史日线** | 东财 push2his / 新浪 | `/api/fund/market/historical?interval=1d` | ✅ `code:0`，OHLCV 真实 | 封装为能力，**默认不接回补链**（见 §5.3） |
| 国家队·A股行情 | 新浪 | `/api/a-share/prices/snapshot` | ✅ `code:0`（个股）；⚠️ **传 ETF 代码返回 `3001`** | 封装备用，本面板不用 |
| 国家队·**主力净额/资金流** | 东财 push2（**头号痛点**） | `/api/a-share/capital-flow/*` | ❌ `code:2004 该数据为同花顺AI客户端专用` | **接不了，维持原方案** |
| 国家队·**逐笔大单** | 东财 details（不可回补） | `/api/a-share/high-frequency/*` | ❌ `code:2004`，且本就是分时非 tick | **接不了，维持原方案** |
| 宽基 ETF 申购净流入 | — | `/api/fund/etf-flow/*` | ❌ 文档标 AI 客户端专用 | 不接 |
| 资讯事件库 | — | `/api/news/events/search` | ❌ 文档标 AI 客户端专用 | 不接 |
| **QDII·全球信号**（IXIC/SPX/DAX/NKY/CAC/UKX/US10Y/VIX） | 新浪 / CNBC / CBOE | 无 | ❌ 全文零命中，`asset_type` 无海外 | **完全用不上，QDII 零改动** |

> **诚实的净效果**：把「新闻 + ETF 行情」从被风控的新浪 / 东财，部分搬到同花顺官方云端，
> 稳定性会提升；但主力净额、逐笔、QDII 三块（东财最易限流处）依旧靠原方案。
> fuyao 是**半场增强**，不是核心解药。

---

## 二、接入设计

### 2.1 三条铁律

1. **增强而非唯一**：原抓取链一条不改，**只在原源产出之后追加 / 补缺**，绝不替换。
2. **有 key 才启用**：解析不到 key → 模块所有能力直接短路，零网络请求、零行为变化。
3. **失败静默降级**：网络异常 / `2003`（key 无效或吊销）/ `2004`（AI 客户端专用）/
   `2001`（未认证）/ `429`（限流）/ 超时 / JSON 异常 → 一律吞掉，主流程照常跑。
   与项目既有的「公开快照兜底」同构（仅补缺、绝不阻断）。

### 2.2 Key 解析优先级（`common/fuyao.py::resolve_key`）

| 优先级 | 来源 | 说明 |
|---|---|---|
| 1 | 页面显式传入的 key | 请求头 `X-Fuyao-Key`，仅本次请求生效（thread-local） |
| 2 | 页面传入的「环境变量名」 | 请求头 `X-Fuyao-Env`，后端 `os.environ[name]` 取值 |
| 3 | `NT_FUYAO_ENV_NAME` | 部署者用合法变量名指定「key 在哪个变量里」 |
| 4 | **`invest-radar-fuyaokey`** | 丁总已配置的系统环境变量名（默认首选） |
| 5 | `FUYAO_API_KEY` | 合法标识符别名，供 shell / GitHub Actions 使用 |

> ⚠️ **含连字符环境变量名陷阱（本次踩过）**：
> `os.environ.get("invest-radar-fuyaokey")` 在 **Python 里完全合法且正常工作**（就是个
> dict key）；只有 **bash** 的 `${invest-radar-fuyaokey}` 会被解析成「变量 `invest` +
> 默认值 `-fuyaokey`」而读到假值 `radar-fuyaokey`。
> ⇒ 排查时一律用 `printenv "invest-radar-fuyaokey"` 或 Python，别用 `${}`。
> 代码中不作字符串拼接展开，直接 `os.environ.get(name)`。

### 2.3 四种启用方式（覆盖所有部署形态）

| 场景 | 做法 |
|---|---|
| **丁总现有环境** | 什么都不用做 —— `invest-radar-fuyaokey` 已被自动识别 |
| **Win / Linux 本地 / VPS** | 在 `.env` 或系统环境设 `invest-radar-fuyaokey=<key>` 或 `FUYAO_API_KEY=<key>` |
| **公开网页访客自己填** | 页头「⚙ 数据源增强」→ 填 API Key 或环境变量名 → 存 localStorage → 随请求头传给后端 |
| **GitHub Pages 静态站** | 由**站主**在仓库 Secrets 配 `FUYAO_API_KEY`，`daily.yml` 注入给 `staticsite.py`，抓取时增强后生成 `data.json` ⇒ 所有访客受益（见 §5.2） |

> 静态站本身是**纯展示**（数据来自已发布的 `data.json`），访客填 key 无法改写已发布快照；
> 故静态站模式下设置面板会提示「需由站点维护者在生成快照时配置」。浏览器直连 fuyao
> 改写展示层会破坏双端一致性，**刻意不做**。

### 2.4 可见性（不泄露 key）

- `/api/status` 新增 `fuyao` 段：`enabled`（bool）、`source`（`page` / `env` / `off`）、
  `last_error`（错误码名，无 key 内容）、`enhanced_news` / `enhanced_quotes`（本次增强条数）。
- 抓取日志打 `[fuyao]` 前缀，**只打条数，绝不打 key**。

---

## 三、字段映射对照表（实测字段名）

### 3.1 ETF 行情：`/api/fund/market/snapshot?thscode=510300.SH`

| fuyao 字段 | 面板字段 | 单位 / 换算 | 备注 |
|---|---|---|---|
| `ticker` | `code` | — | 6 位代码 |
| `last_price` | `price` | 元 | 与新浪 `parts[3]` 同义 |
| `price_change_ratio_pct` | `chg` | % | 与新浪自算 `(cur-prev)/prev*100` 同义 |
| `turnover` | `amount` | **元** | 与新浪 `parts[9]` 成交额同单位，**勿再 /1e8** |

**约束（实测）**：
- 参数名是 `thscode`（**单数**），传 `thscodes` 报 `1001 Missing required parameter`；
- **一次只能查 1 只**，逗号分隔报 `1002 only one code per request` ⇒ 14 只需 14 个请求，
  因此**仅在原源有缺失时**才调用（正常新浪全绿 ⇒ 0 额外请求）；
- 后缀：沪市 `.SH`（`5` 开头：51/56/58/560/563）、深市 `.SZ`（`1` 开头：15x）。
  复用现有 `_sina_prefix` 的 `code[0] in "56"` 判定即可；`.SH` 查深市代码报 `3001`。
- ⚠️ **不要用 A股快照接口查 ETF**：`/api/a-share/prices/snapshot?thscodes=510300.SH`
  返回 `3001 Target not found`。ETF 必须走 `fund/market/*`。

### 3.2 ETF 历史日线：`/api/fund/market/historical`

| fuyao 字段 | 面板字段 | 换算 |
|---|---|---|
| `date_ms` | 日期 | Unix **毫秒** → `YYYY-MM-DD` |
| `close_price` | `price` | 元 |
| `turnover` | `amount` | 元 |
| `open_price` / `high_price` / `low_price` | — | 备查 |
| — | `chg` | 接口不给涨跌幅，需由前一日收盘自算 |

区间参数 `start` / `end` 为 **13 位毫秒时间戳**；`interval=1d`；窗口上限 5 年。

### 3.3 新闻：`/api/news/flash/list`、`/api/news/stock/timeline-list`

| fuyao 字段 | 面板字段 | 换算 |
|---|---|---|
| `title` | `title` | — |
| `encoded_url`（快讯）/ `jump_url`（个股） | `url` | — |
| `create_time_ms`（快讯）/ `publish_time_ms`（个股） | `time` | ⚠️ **13 位毫秒** → 必须先格式化为 `YYYY-MM-DD HH:MM` 字符串 |
| `tags[].name` | `src` | 源标记统一写「同花顺API」 |

**必填参数**：快讯 `tag_id`(int，A股栏目实测 `21103`) + `seq`（首屏字符串 `"0"`）；
个股 `thscode` + `offset`（首屏字符串 `"first"`），可选 `size`(1–100)。

> ⚠️ **实测坑（开发中发现并修复）**：`stock/timeline-list` **只认个股**，
> 传 ETF 代码（`510300.SH` / `159919.SZ`）返回 `1002 Unknown thscode`；个股
> （`600519.SH` / `601398.SH`）正常返回 20 条。本面板观察池全是 ETF，故
> **个股新闻默认关闭**（`NT_FUYAO_NEWS_STOCK` 才开启），且两个源各自独立
> `try/except` —— 否则个股源一失败会把已到手的 20 条快讯一起丢掉。
> 默认新闻增强 = 快讯列表一路，实测 20 条候选、按原口径打分入榜 1 条。

> ⚠️ **毫秒时间坑（必须适配）**：现有 `_norm_news_time()` 对纯数字串走
> `datetime.fromtimestamp(int(s))`，喂 13 位毫秒会 `OverflowError` → 返回空串，
> 导致新闻时间全空、排序错乱。适配层负责把毫秒转成 `YYYY-MM-DD HH:MM` 再交给它。

新闻接入后仍走原有 `_news_score()` 相关性打分 + `_finalize_news()` 去重 / 同题合并 /
截断 12 条，**口径完全一致**，不会因新增源而放宽入榜标准。

### 3.4 已验证的错误码

| code | 含义 | 处理 |
|---|---|---|
| `0` | success | 正常返回 |
| `1001` / `1002` / `1003` / `1004` | 缺参 / 格式 / 越界 / 冲突 | 视为调用方 bug，静默降级并记日志 |
| `2001` / `2003` | 未认证 / key 无效或吊销 | 静默降级回原源 |
| `2004` | 同花顺 AI 客户端专用 | **能力不可用**，静默降级 |
| `3001` / `3002` / `3004` | 标的不存在 / 未就绪 / 类型不支持 | 跳过该条 |
| HTTP `429` / `code=4001` | 限流 | 静默降级，不做指数重试 |

---

## 四、代码落点

| 文件 | 改动 | 影响面 |
|---|---|---|
| `common/fuyao.py` | **新增**：key 解析、5 个端点封装、字段适配、统一降级 | 纯新增，不被 import 时零影响 |
| `national_team/fetcher.py` | `fetch_news()` 追加第 4 候选源；`fetch_sina_etfs()` 末尾仅补缺 | 无 key 时两段直接 `if` 短路 |
| `national_team/app.py` | 请求头 → thread-local override；`/api/status` 增加 `fuyao` 段 | 增量路由字段，不改默认值 |
| `national_team/templates/index.html` | 页头「⚙ 数据源增强」面板（key / 环境变量名，localStorage） | 不填 = 界面与之前一致 |
| `.env.example` | 新增 3 个可选变量说明 | 仅注释 |
| `.github/workflows/daily.yml` | 注入 `FUYAO_API_KEY: ${{ secrets.FUYAO_API_KEY }}` | 未配 Secret 时为空 ⇒ 零影响 |
| `national_team/tests/test_fuyao.py` | **新增**：key 解析优先级、无 key 零侵入、2004/429 降级、字段映射、毫秒时间 | 测试 |
| `CHANGELOG.md` / `app.py` / `qdii_app.py` / `README.md` | 版本同步 | 按发版序列 |

### 4.1 三种原部署不受牵连（丁总明确铁律）

- **入口、端口、配置默认值、定时抓取职责一律不改**：`PORT=5000/5001`、`BASE_PATH`、
  `NT_AUTO_ENSURE`、`SCHED_*` 全部保持原样。
- 新增环境变量**默认全空**，未配置时 `resolve_key()` 返回 `None` ⇒ 增强完全关闭。
- `combo_app` / `gateway` 反代已确认**透传全部请求头**，自定义 `X-Fuyao-*` 头可到达后端。
- QDII 层（`qdii/`）**一行不改**（fuyao 无海外数据）。

---

## 五、已知限制 / 后续

1. **主力净额、逐笔大单、QDII 全球信号依旧是原方案**，fuyao `code=2004` 明确锁在
   同花顺 AI 客户端。若哪天开放 REST 权限，只需在 `common/fuyao.py` 加两个端点封装 +
   在 `_apply_fund_flow_fallback` / `_apply_tick_fallback` 前插一层「fuyao 优先补缺」。
2. **ETF 历史日线暂不接回补链**：`backfill_date` / `backfill_range` 已有
   「东财 push2his → 新浪 → 公开快照」三级降级，再插一层会改变历史写入路径、
   风险大于收益（且日线缺失并非当前痛点）。能力已封装，按需一行接入。
3. **静态站访客填 key 不生效**：静态站是已发布快照，需站主在 Actions 生成时配置。
   若将来要让访客浏览器直连增强，须同步维护双端一致性测试，另行评估。
4. **免费政策可变**：无 SLA，若 fuyao 开始收费 / 停服，删掉环境变量即完整回退。
5. **单点风险**：新闻 / ETF 多了一层依赖，但坚持「原源优先 + 仅补缺 + 静默降级」，
   fuyao 挂掉时表现与接入前完全一致。

---

## 六、实测记录（2026-11-04，真实 key）

```
[1] /api/a-share/prices/snapshot?thscodes=600519.SH      code=0   茅台 1463.99
[2] /api/a-share/capital-flow/snapshot?thscode=600519.SH  code=2004 AI客户端专用
[3] /api/a-share/high-frequency/intraday?thscode=600519.SH code=2004 AI客户端专用
[4] /api/fund/market/snapshot?thscode=510300.SH            code=0   4.065
[5] /api/fund/market/snapshot?thscode=159919.SZ            code=0   4.355
[6] /api/fund/market/historical (interval=1d)              code=0   OHLCV 日线
[7] /api/news/flash/list?tag_id=21103&seq=0                code=0   20 条真实快讯
[8] /api/news/stock/timeline-list?thscode=600519.SH        code=0   20 条个股新闻
[9] 连续 12 次快照突发                                      全部 200，无 429
[A] CORS：带 Origin + OPTIONS 预检                          允许 x-api-key 头
```
