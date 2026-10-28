# 国家队买卖A股日报 · National Team A-Share Tracker

> 每天收盘后自动抓取 A 股「国家队」动向，以宽基指数 ETF 的主力资金净流入为代理指标，用 ECharts 仪表盘展示，支持按日期 / 类别 / 基金三维筛选。
>
> **Zero-dependency** Python dashboard tracking China's "national team" (Central Huijin / CSF) A-share buying via broad-based ETF main-capital net inflow. No `pip install` required — standard library only.

[![CI](https://github.com/turbomind66/ashare-national-team-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/turbomind66/ashare-national-team-tracker/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/turbomind66/ashare-national-team-tracker?color=orange&label=release)](https://github.com/turbomind66/ashare-national-team-tracker/releases)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Zero Dependency](https://img.shields.io/badge/dependencies-0-brightgreen)](#快速开始)

---

## 这个项目解决什么问题

市场常说的「国家队出手了」——中央汇金、证金公司等通过买入宽基 ETF 托底——**没有任何官方逐日披露**。散户只能从新闻里猜，或者在十几个 ETF 的行情页里来回翻。

本项目把这件事固化成一条自动化流水线：

```
每个交易日 16:05  →  抓取宽基ETF行情与主力资金  →  存入 SQLite  →  仪表盘展示 + 三维筛选
```

打开页面即可知道：**今天国家队是净买入还是净卖出、买了多少、买的是哪一类宽基、哪只 ETF 被买得最多。**

## 为什么用「宽基 ETF 主力净流入」作为代理

- 国家队不直接买个股，**宽基指数 ETF 是其进场标准通道**（沪深300 / 中证500 / 中证1000 / 上证50 / 创业板 / 科创50 / 科创100 / 中证A500）。
- 这些 ETF 体量巨大、日内波动小，**主力资金（大单）方向的异常变化**基本只能来自机构级资金，与「国家队护盘」高度相关。
- 单一接口即可获得，无需历史对比推算，是最贴近真实的实时量化信号。

> ⚠️ 这是**代理指标**，不等于官方披露的持仓变动。它回答的是「今天大资金在宽基 ETF 上是买还是卖」，而非「汇金今天买了多少股」。

## 功能

- **自动抓取**：交易日收盘后（16:05）自动抓取归档；启动或访问时若当天缺数据会自动补抓。
- **历史回补**：走东方财富历史接口还原已收盘交易日的真实快照（收盘价 / 成交额 / 涨跌幅 / 主力净流入），**不用今天的实时值冒充历史**。
- **三维筛选**（可组合）：
  - 按**日期**：任选已归档交易日；
  - 按**类别**：8 类宽基，多选；
  - 按**基金**：观察池内单只 ETF，多选；选类别后基金列表自动收敛为该类别下标的；
  - 类别与基金叠加取交集，卡片 / 趋势 / 条形 / 饼图 / 明细表全站联动。
- **容错降级**：主力资金接口不可用时，价格 / 涨跌 / 成交额仍显示真实数据，并明确标注缺失，**不会静默显示成「中性」**。
- **零依赖**：只用 Python 标准库（`http.server` + `sqlite3` + `urllib`），单命令启动。

## 快速开始

```bash
git clone https://github.com/turbomind66/ashare-national-team-tracker.git
cd ashare-national-team-tracker

python app.py
# 打开 http://127.0.0.1:5000
```

- 默认端口 `5000`，可用环境变量 `PORT` 覆盖：`PORT=8080 python app.py`
- 首次启动会自动抓取当天（若已收盘）并回补最近几个交易日，历史曲线从此刻起逐日累积。
- 需要 Python 3.8+，无第三方依赖。

手动补某一天的数据：

```bash
python backfill.py 2026-10-27
```

## 数据源与口径

| 数据 | 来源 | 说明 |
|---|---|---|
| 现价 / 涨跌幅 / 成交额 | 新浪行情 `hq.sinajs.cn` | 稳定，作为基础字段 |
| **主力资金净流入** | 东方财富 `push2.eastmoney.com` | 核心指标，多镜像主机轮换容错 |
| 历史 K 线（收盘/成交额/涨跌） | 东方财富 `push2his.eastmoney.com` | 用于历史回补 |
| 历史主力资金 | 东方财富 `fflow/daykline` | 用于历史回补 |
| 相关新闻 | 东方财富 / 新浪（尽力抓取） | 上游不通则提示，不影响核心数据 |

均为公开接口，无需 API Key。生产环境每天只抓一次，不会触发上游限流；开发期高频探测可能被临时限制。

## HTTP 接口

| 接口 | 说明 |
|---|---|
| `GET /` | 仪表盘页面 |
| `GET /api/data` | 数据 + 历史（JSON），支持筛选参数 |
| `GET /api/options` | 筛选项清单（可选日期 / 类别 / 基金） |
| `GET /api/refresh` | 立即抓取并刷新 |
| `GET /api/status` | 最近抓取状态 |

筛选参数（可组合）：

```
/api/data?date=2026-10-27
/api/data?cats=沪深300,中证500
/api/data?codes=510300,159919
/api/data?date=2026-10-27&cats=沪深300&codes=510300
```

响应包含 `filters`（生效的筛选条件）、`options`（可选项）、`summary`（当前范围汇总 + 当日全量对比）、`etf`（明细）、`history`（按筛选重算的历史）。

## 目录结构

```
.
├── app.py                  # HTTP 服务 + 路由 + 定时调度 + 数据组装
├── db.py                   # SQLite 存储层（快照 / 筛选查询 / 历史聚合）
├── fetcher.py              # 抓取层（实时行情、主力资金、历史回补、新闻）
├── backfill.py             # 手动回补指定交易日
├── templates/index.html    # 仪表盘（原生 JS + ECharts）
├── tests/
│   ├── test_db.py          # 数据层单元测试（离线）
│   └── test_frontend.js    # 前端渲染逻辑测试（DOM 桩件，离线）
├── requirements.txt
├── LICENSE
└── .github/workflows/ci.yml
```

## 测试

```bash
# 数据层（离线，无需网络）
python -m unittest discover -s tests -p "test_*.py" -v

# 前端渲染逻辑（用 DOM / ECharts 桩件跑真实页面脚本，无需浏览器）
node tests/test_frontend.js
```

## 实现中踩过的坑

记录在此，便于后来者少走弯路：

1. **东方财富 `clist` 接口在 `np=1` 时强制只返回 100 条**，需翻页抓全；且 `f20` / `f21`（规模字段）在 ETF 板块会令返回为空，不要一起带。
2. **历史 K 线接口 `beg == end`（同一天）返回空数组**，必须传「目标日往前 12 天 → 目标日」的范围，再挑出目标日那一行。
3. **历史接口的 `beg` / `end` 日期格式必须统一无横杠**（`%Y%m%d`），混用带横杠格式会被静默忽略并返回空。
4. **本地 `127.0.0.1` 请求可能被系统代理劫持**（表现为 404 / Bad request syntax），写本地 HTTP 脚本时要绕过代理（`NO_PROXY` 或 `ProxyHandler({})`）。
5. **盘前打开页面**：当天数据尚未生成，默认自动回退到最近一个有效数据日并在页面标注原因，避免首屏一片 `—`。

## 更新日志

完整版本说明见 [Releases](https://github.com/turbomind66/ashare-national-team-tracker/releases)。

- **v1.0.0** — 首个可用版本：自动抓取归档、历史回补、日期/类别/基金三维筛选、容错降级。

## 免责声明

本项目仅对**公开行情数据**做整理与可视化，不构成任何投资建议。数据准确性依赖第三方公开接口，可能存在延迟、缺失或变动，请勿据此做出投资决策。

## License

[MIT](LICENSE) © 2026 turbomind66
