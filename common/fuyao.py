"""common/fuyao.py — 同花顺金融数据 API（fuyao.aicubes.cn）**可选增强源**客户端。

定位（丁总 2026-11-04 拍板）：**增强源，而非唯一源**。
    - 没配置 API Key → 本模块所有能力直接短路，零额外请求、零行为变化；
    - 有 Key → 只在「原源缺数据」的缝隙里补：新闻作为第 4 个候选源、
      ETF 行情仅补新浪抓不到的 code；
    - 任何异常（网络 / 2003 key 无效 / 2004 AI 客户端专用 / 429 限流 / 超时）
      一律静默降级，绝不抛到主流程。

实测可用端点（2026-11-04 真实 key 验证，详见仓库根 FUYAO_INTEGRATION.md）：
    GET /api/a-share/prices/snapshot?thscodes=     A股快照（本面板观察池全是 ETF，
                                                    传 ETF 代码返回 3001，故仅备用）
    GET /api/fund/market/snapshot?thscode=         ETF/LOF 快照（一次只能 1 只）
    GET /api/fund/market/historical                 ETF 前复权日线（interval=1d）
    GET /api/news/flash/list                       快讯列表（tag_id + seq）
    GET /api/news/stock/timeline-list              个股分时新闻（thscode + offset）

实测不可用（code=2004「该数据为同花顺AI客户端专用」，REST key 无权）：
    /api/a-share/capital-flow/*    主力资金（本面板头号痛点）
    /api/a-share/high-frequency/*  高频/逐笔
    /api/fund/etf-flow/*           宽基 ETF 申购净流入
    海外行情（IXIC/SPX/DAX/NKY/VIX/美债）全文无数据源 ⇒ QDII 层零改动

⚠️ 环境变量名陷阱：丁总配的是 `invest-radar-fuyaokey`（含连字符）。
   Python 的 os.environ.get("invest-radar-fuyaokey") **合法且正常**（就是个 dict key）；
   只有 bash 的 ${invest-radar-fuyaokey} 会被当成「变量 invest + 默认值 -fuyaokey」
   展开成假值。排查一律用 printenv 或 Python，别用 ${}。
"""
import datetime
import json
import os
import ssl
import sys
import threading
import urllib.parse
import urllib.request

# 可被自托管镜像覆盖（测试 / 内网代理场景）
BASE_URL = (os.environ.get("FUYAO_BASE_URL") or "https://fuyao.aicubes.cn").rstrip("/")

# Key 解析优先级见 resolve_key()。第一个是丁总已配置的系统环境变量名。
DEFAULT_ENV_NAMES = ("invest-radar-fuyaokey", "FUYAO_API_KEY")
# A股快讯栏目（实测 21103 = 「A股」）
FLASH_TAG_ID = 21103

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# 每次请求可覆盖的 key / 环境变量名（thread-local，避免多线程串味）。
# 由 app.py 从请求头 X-Fuyao-Key / X-Fuyao-Env 注入，仅本次请求生效。
_CTX = threading.local()

# 最近一次调用状态：只给 /api/status 展示，**绝不含 key 内容**。
STATE = {"last_error": None, "enhanced_news": 0, "enhanced_quotes": 0}


class FuyaoUnavailable(Exception):
    """fuyao 不可用（无 key / 鉴权失败 / 限流 / 网络异常）。调用方一律吞掉。"""


def set_request_override(key=None, env_name=None):
    """设置本次请求（线程）的 key 覆盖；传 None 表示清除。由 app.py 调用。"""
    _CTX.key = (key or "").strip() or None
    _CTX.env_name = (env_name or "").strip() or None


def clear_request_override():
    _CTX.key = None
    _CTX.env_name = None


def resolve_key():
    """按优先级解析 API Key，取不到返回 None（= 增强关闭）。

    优先级：
      1. 页面显式传入的 key        请求头 X-Fuyao-Key（thread-local）
      2. 页面传入的环境变量名      请求头 X-Fuyao-Env → os.environ[name]
      3. NT_FUYAO_ENV_NAME         部署者指定的「key 在哪个变量里」
      4. invest-radar-fuyaokey     丁总已配置的系统环境变量名（默认首选）
      5. FUYAO_API_KEY             合法标识符别名（shell / GitHub Actions 用）
    """
    # 离线测试保护：跑 unittest 时默认关闭增强，避免开发机上真实存在的
    # invest-radar-fuyaokey 把测试变成联网测试（且会拖慢 / 污染断言）。
    # 测试自己要验证增强逻辑时设 NT_FUYAO_ALLOW_IN_TESTS=1 并用 mock 隔离网络。
    if ("unittest" in sys.modules
            and (os.environ.get("NT_FUYAO_ALLOW_IN_TESTS") or "").strip()
            not in ("1", "true", "yes", "on")):
        return None

    explicit = getattr(_CTX, "key", None)
    if explicit:
        return explicit

    names = []
    page_env = getattr(_CTX, "env_name", None)
    if page_env:
        names.append(page_env)
    cfg_env = (os.environ.get("NT_FUYAO_ENV_NAME") or "").strip()
    if cfg_env:
        names.append(cfg_env)
    names.extend(DEFAULT_ENV_NAMES)

    for name in names:
        try:
            v = (os.environ.get(name) or "").strip()
        except Exception:  # noqa: BLE001 - 极端平台 environ 异常
            continue
        if v:
            return v
    return None


def enabled() -> bool:
    """是否启用增强源。无 key 时返回 False，所有调用方据此直接短路。"""
    return bool(resolve_key())


def status() -> dict:
    """给 /api/status 的可见性字段；**不返回 key**，只返回布尔与来源。"""
    key = resolve_key()
    if not key:
        source = "off"
    elif getattr(_CTX, "key", None):
        source = "page"
    else:
        source = "env"
    return {
        "enabled": bool(key),
        "source": source,
        "last_error": STATE.get("last_error"),
        "enhanced_news": STATE.get("enhanced_news", 0),
        "enhanced_quotes": STATE.get("enhanced_quotes", 0),
    }


def _note_error(what):
    STATE["last_error"] = what


def get(path, params, timeout=12, retries=1):
    """调用一个 fuyao REST 端点，返回 data（dict/list）；失败抛 FuyaoUnavailable。

    业务错误都是 HTTP 200 + JSON code，统一按「不可用」处理，绝不阻断主流程。
    """
    key = resolve_key()
    if not key:
        raise FuyaoUnavailable("no-key")
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    last = "request-failed"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=dict(HDR, **{"X-api-key": key}))
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                d = json.loads(r.read().decode("utf-8", "ignore"))
            code = d.get("code")
            if code == 0:
                STATE["last_error"] = None
                return d.get("data") or {}
            last = "code=%s" % code
            # 2001/2003 鉴权、2004 AI 客户端专用、429/4001 限流：重试无意义
            if code in (2001, 2003, 2004, 4001):
                break
        except Exception as e:  # noqa: BLE001 - 网络/JSON/HTTPError 一律降级
            last = type(e).__name__
            if attempt < retries:
                continue
    _note_error(last)
    raise FuyaoUnavailable(last)


# ---------------- 能力封装（实测可用） ----------------

def fund_snapshot(thscode: str) -> dict:
    """场内基金行情快照；一次只能 1 只。返回 item[0] 或 {}。"""
    d = get("/api/fund/market/snapshot", {"thscode": thscode})
    items = d.get("item") or []
    return items[0] if items and isinstance(items[0], dict) else {}


def fund_historical(thscode: str, start_ms: int, end_ms: int) -> list:
    """ETF 前复权历史日线，返回 [{date_ms, open/high/low/close_price, turnover}, ...]。"""
    d = get("/api/fund/market/historical",
            {"thscode": thscode, "interval": "1d", "start": start_ms, "end": end_ms})
    return [x for x in (d.get("item") or []) if isinstance(x, dict)]


def flash_list(tag_id=FLASH_TAG_ID, seq="0") -> list:
    """快讯列表。"""
    d = get("/api/news/flash/list", {"tag_id": tag_id, "seq": seq})
    return [x for x in (d.get("items") or []) if isinstance(x, dict)]


def stock_timeline(thscode: str, offset="first", size=None) -> list:
    """个股分时新闻（个股/ETF 相关资讯）。"""
    p = {"thscode": thscode, "offset": offset}
    if size:
        p["size"] = size
    d = get("/api/news/stock/timeline-list", p)
    return [x for x in (d.get("items") or []) if isinstance(x, dict)]


def stock_snapshot(thscodes) -> list:
    """A股行情快照（备用；观察池 ETF 代码传进去会 3001，故本面板默认不用）。"""
    if isinstance(thscodes, (list, tuple)):
        thscodes = ",".join(thscodes)
    d = get("/api/a-share/prices/snapshot", {"thscodes": thscodes})
    return [x for x in (d.get("item") or []) if isinstance(x, dict)]


# ---------------- 字段适配：fuyao → invest-radar 内部口径 ----------------

def thscode_of(code: str) -> str:
    """6 位基金代码 → 带市场后缀的 thscode。

    沪市 ETF：5 开头（51/56/58 等）→ .SH；深市 ETF：1 开头（15x）→ .SZ。
    与 fetcher._sina_prefix 的判定一致（code[0] in "56"）。
    """
    return code + (".SH" if code[:1] in "56" else ".SZ")


def _ms_to_hm(ms) -> str:
    """13 位毫秒时间戳 → 'YYYY-MM-DD HH:MM'。

    ⚠️ 关键适配：fetcher._norm_news_time() 对纯数字串走 fromtimestamp(int(s))，
    喂 13 位毫秒会 OverflowError → 返回空串，新闻时间全空、排序错乱。
    必须在这里先转成字符串格式再交给它。
    """
    try:
        v = int(ms)
        if v <= 0:
            return ""
        if v > 1e12:  # 毫秒
            v /= 1000.0
        return datetime.datetime.fromtimestamp(v).strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return ""


def news_items() -> list:
    """把 fuyao 快讯（+ 可选个股新闻）转成 fetcher.fetch_news 的同构条目。

    返回 [{"title","url","time","src"}]，不带 _score —— 由 fetch_news 用统一的
    _news_score() 打分，保证入榜口径与原有三源完全一致。

    ⚠️ 两源**各自独立** try/except（与 fetch_news 原有多源设计一致）：
    实测 `/api/news/stock/timeline-list` 只支持个股，传 ETF 代码返回
    `1002 Unknown thscode`。若把两个源串在一个 try 里，个股源一失败会把
    已到手的 20 条快讯一起丢掉。个股新闻对本面板价值有限且易引入噪音，
    故默认关闭；需要时用 NT_FUYAO_NEWS_STOCK 指定个股 thscode 开启。
    """
    raw = []
    try:
        for it in flash_list():
            raw.append((it.get("title"), it.get("encoded_url"),
                        it.get("create_time_ms"), "同花顺API"))
    except FuyaoUnavailable as e:
        _note_error(str(e))
    stock = (os.environ.get("NT_FUYAO_NEWS_STOCK") or "").strip()
    if stock:
        try:
            for it in stock_timeline(stock):
                raw.append((it.get("title"), it.get("jump_url"),
                            it.get("publish_time_ms"), "同花顺API"))
        except FuyaoUnavailable:
            pass  # 个股源失败不影响快讯源
    out, seen = [], set()
    for title, url, t, src in raw:
        title = (title or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        out.append({"title": title, "url": url or "",
                    "time": _ms_to_hm(t), "src": src})
    return out


def etf_quotes(missing_codes) -> dict:
    """把 fuyao 基金快照转成 fetcher.fetch_sina_etfs 的同构 dict。

    返回 {code: {"price": 元, "chg": %, "amount": 元}}。
    ⚠️ turnover 已是**元**（与新浪 parts[9] 同单位），不要再 /1e8。
    ⚠️ 一次只能查 1 只，故只对「原源缺失」的 code 发请求，避免 14 次调用。
    """
    out = {}
    for code in missing_codes or []:
        try:
            row = fund_snapshot(thscode_of(code))
        except FuyaoUnavailable:
            break  # 一个失败大概率整条链路不通，别把 14 次都耗完
        if not row:
            continue
        try:
            price = float(row.get("last_price") or 0)
            chg = float(row.get("price_change_ratio_pct") or 0)
            amount = float(row.get("turnover") or 0)
        except (TypeError, ValueError):
            continue
        if price > 0:
            out[code] = {"price": price, "chg": round(chg, 2), "amount": amount}
    return out


def reset_counters():
    STATE["enhanced_news"] = 0
    STATE["enhanced_quotes"] = 0
