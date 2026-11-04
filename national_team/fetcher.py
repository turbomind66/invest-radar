"""抓取：汪汪队宽基ETF主力净流入（代理"汪汪队买卖A股"）+ 相关新闻。"""
import urllib.request
import urllib.parse
import json
import ssl
import time
import datetime
import re
import os

import db

# ---- fuyao 增强源（可选，2026-11-04）------------------------------------
# 懒加载：模块加载期绝不因为 common/fuyao 不可用而报错（三种原部署零影响）。
# 无 key 时 _fuyao_mod().enabled() 返回 False，所有接入点直接短路、零额外请求。
_FUYAO = None
_FUYAO_TRIED = False


def _fuyao_mod():
    """返回 common.fuyao 模块，或 None（不可用 = 增强关闭）。"""
    global _FUYAO, _FUYAO_TRIED
    if _FUYAO_TRIED:
        return _FUYAO
    _FUYAO_TRIED = True
    try:
        from common import fuyao as _m
    except ImportError:
        try:  # 兼容被直接 exec / sys.path 里没有项目根的入口
            import sys as _sys
            _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if _root not in _sys.path:
                _sys.path.insert(0, _root)
            from common import fuyao as _m
        except ImportError:
            _m = None
    _FUYAO = _m
    return _FUYAO


CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

# 东方财富多个镜像主机，遇限流/断连自动轮换
HOSTS = [
    "push2.eastmoney.com",
    "push2delay.eastmoney.com",
    "82.push2.eastmoney.com",
    "11.push2.eastmoney.com",
]

# 汪汪队常用护盘宽基ETF池（代码, 名称, 类别）
# 2026-11-04 校准（依据：基金公司官网 / 天天基金代码页 / 汇金 2025 中报增持名单）：
#   - 560010 实为广发中证1000ETF（易方达是 159633）；
#   - 588800 实为华夏上证科创板100ETF（博时是 588030）；
#   - 563300 实为华泰柏瑞中证2000ETF，此前误标成「中证A500ETF / 中证A500」，
#     导致全池没有任何一只真正的中证A500 ETF，且中证2000 的数据被算进 A500 类别。
#   - 新增 588080（易方达科创50ETF）、159629（富国中证1000ETF）：两只均在
#     中央汇金 2025 上半年增持的 12 只宽基 ETF 名单内；
#     新增 563360（华泰柏瑞中证A500ETF）：中证A500 龙头，2026-10 起纳入常规买入清单。
# ⚠️ 改这里只影响今后抓到的数据；已归档行/cats 清单由导出流程刷新，
#    docs/data.json 中 563300 等旧标签已同步做纯文本纠正（数值一字未动）。
WATCHLIST = [
    ("510300", "华泰柏瑞沪深300ETF", "沪深300"),
    ("510310", "易方达沪深300ETF", "沪深300"),
    ("159919", "嘉实沪深300ETF", "沪深300"),
    ("510330", "华夏沪深300ETF", "沪深300"),
    ("510500", "南方中证500ETF", "中证500"),
    ("510510", "广发中证500ETF", "中证500"),
    ("512100", "南方中证1000ETF", "中证1000"),
    ("560010", "广发中证1000ETF", "中证1000"),
    ("159845", "华夏中证1000ETF", "中证1000"),
    ("159629", "富国中证1000ETF", "中证1000"),
    ("510050", "华夏上证50ETF", "上证50"),
    ("159915", "易方达创业板ETF", "创业板"),
    ("588000", "华夏科创50ETF", "科创50"),
    ("588080", "易方达科创50ETF", "科创50"),
    ("588800", "华夏科创100ETF", "科创100"),
    ("563360", "华泰柏瑞中证A500ETF", "中证A500"),
    ("563300", "华泰柏瑞中证2000ETF", "中证2000"),
]

FIELDS = "f12,f14,f2,f3,f4,f6,f62"

# ---- 新闻抓取配置（2026-10-30 重整）----
# 旧的两个源已被上游改版废弃（实测）：
#   新浪 roll pageid=515             → {"status":{"code":11,"msg":"param lid illegal"}}
#   东财 search ?search_text=&type=12 → 400 Bad Request
# 现用三个源，各自独立 try/except，任一挂掉不影响其它：
#   1) 东财搜索 param= JSON 格式  —— keyword 精确（主源，命中即相关）
#   2) 同花顺 7×24 快讯           —— 实时全量流，靠标题关键词过滤
#   3) 新浪财经滚动 pageid=153     —— 全量流，靠标题关键词过滤
NEWS_EM_KEYWORDS = ["国家队", "中央汇金", "证金公司", "汇金公司"]

# 新闻兜底源（2026-11-01 新增）：公开站 data.json（GitHub Pages）。
# 当本机三源（东财/同花顺/新浪）全部抓空时，从公开快照借一份相同的新闻。
# 典型场景：VPS 机房 IP 被东方财富新闻搜索接口风控 → 东财搜索返回非 JSON
# （JSONDecodeError）→ 本地新闻恒为 0；而公开快照由能正常访问东财的 runner
# 抓取，等于借它的结果，让 GitHub Pages / VPS / WorkBuddy 应用新闻一致。
# 可用环境变量 NT_NEWS_FALLBACK_URL 覆盖（如自托管镜像站）。
NT_NEWS_FALLBACK_URL = os.environ.get(
    "NT_NEWS_FALLBACK_URL",
    "https://turbomind66.github.io/invest-radar/data.json",
)

# 资金流兜底源（2026-11-01 新增）：与新闻兜底同源（同一份 data.json）。
# 当本机资金流覆盖率不足（境外 IP 被东财 push2/push2his 限流，部分 ETF 拿不到）
# 时，从公开快照 days[date] 借**同一天**的四档净额补齐缺失的 code。
# 典型场景：VPS 机房 IP 被东方财富资金流接口限流 → 14 只里只拿到 1 只（中证A500）
# → 净买入合计被严重低估（VPS 0.24 亿 vs 真实 −41.47 亿）。
# 公开快照由能正常访问东财的 runner 抓取，借它的结果让 GitHub Pages / VPS /
# WorkBuddy 应用三端资金流一致。默认与新闻兜底同源（NT_NEWS_FALLBACK_URL 若被
# 改成自托管镜像也一并生效）；可用环境变量 NT_FUND_FALLBACK_URL 单独覆盖。
NT_FUND_FALLBACK_URL = os.environ.get(
    "NT_FUND_FALLBACK_URL",
    NT_NEWS_FALLBACK_URL,
)

# 逐笔大单兜底源（2026-11-02 新增）：与新闻/资金流兜底同源（同一份 data.json）。
# 当本机 archive_tick 抓空（VPS/云端 IP 被东财 push2 限流、或盘后滚动窗口冻结）
# 时，从公开快照 tick_rows[date] 借同日的逐笔大单，使 GitHub Pages / VPS / WorkBuddy
# 三端逐笔大单一致。仅当本地该日期完全无大单才借，绝不覆盖真实抓取值；
# 网络异常静默降级。默认与新闻兜底同源；可用 NT_TICK_FALLBACK_URL 单独覆盖。
NT_TICK_FALLBACK_URL = os.environ.get(
    "NT_TICK_FALLBACK_URL",
    NT_NEWS_FALLBACK_URL,
)

# 相关性分层：score 越大越靠前（同分按时间倒序）
SCORE_STRONG = 2    # 标题含主体词
SCORE_WEAK = 1      # 标题含弱词 + 语境

# 主体词：唯一指向证券国家队（中央汇金/证金/汇金公司），标题命中即相关
NEWS_STRONG = ["中央汇金", "证金公司", "汇金公司"]
# 「国家队」一词在中文里还指科技/高校/航天/数据等领域的国家队，
# 故需搭配金融语境才算命中本面板（A股资金动向）
NEWS_GUOJIA = "国家队"
# 金融语境词：必须是「资金/持仓」类实义词。
# 注意排除「科创」「创业板」「机构」等泛词 —— 它们是板块名/泛称，
# 会让「半导体国家队科创板IPO辅导」这类非金融条目误命中。
NEWS_FIN_CTX = ["A股", "沪深", "上证", "深证", "宽基", "ETF",
                "基金", "持仓", "重仓", "增持", "买入", "加仓", "建仓", "入市", "注资",
                "举牌", "配置", "仓位", "资金", "净流入", "净买入", "主力",
                "二级市场", "一二级市场", "股市", "资本市场", "上市公司", "两融", "份额"]

# 弱主体词：单独出现易误伤（"汇金科技"等同名公司），需搭配语境
NEWS_WEAK_SUBJECT = ["汇金", "证金"]
# 动作词：需搭配 A 股/资金语境
NEWS_ACTION = ["增持", "护盘", "主力资金", "宽基", "重仓"]
# A 股语境
NEWS_A_CTX = ["A股", "沪深", "上证", "深证", "创业板", "科创", "北交所", "宽基",
              "场内", "基金", "份额", "净流入", "两市", "成交额", "主力", "板块", "市场"]
# 买入语境：裸 ETF 标题（ETF午评/两市成交额/央行抛售ETF）不相关，
# 只有出现买入/增持类语境才算国家队动向
NEWS_BUY_CTX = ["增持", "买入", "加仓", "国家队", "汇金", "证金", "护盘", "净流入", "建仓"]
# 误伤黑名单：「保证金」含「证金」子串；其余为公司/基金同名噪音
NEWS_BLOCK = ["保证金", "汇金科技", "浙商汇金", "汇金通", "汇金蓝", "汇金诺", "汇金宝"]
# 「国家队」的非金融义噪音（科技/高校/航天/数据等领域）
NEWS_NOISE = ["数据国家队", "数据集团", "数据要素", "教育部", "高校", "职业院校",
               "火箭", "商火", "商业航天", "科技国家队"]
# 同题异写去重：同一条新闻的不同标题写法（如「中央汇金打假」「中央汇金公司打假」
# 「中央汇金郑重提醒」）会刷屏挤掉时效新闻，故按事件特征词二次去重
NEWS_EVENT_GROUPS = [
    ("打假", ["打假", "仿冒", "李鬼", "声明", "郑重提醒", "严正声明"]),
]




# 东财 push2his 日K 路径可用性（None=未探测 / True=可用 / False=本次运行不可用）
_EM_KLINE_OK = None


def _num(v):
    try:
        if v in (None, "-", ""):
            return 0.0
        return float(v)
    except Exception:
        return 0.0


def _get(url: str, ref: str = None, timeout: int = 20, max_try: int = 3):
    """带主机轮换 + 指数退避的GET。url 中写主域名即可，内部轮换镜像。"""
    hdr = dict(HDR)
    if ref:
        hdr["Referer"] = ref
    last = None
    for attempt in range(max_try):
        host = HOSTS[attempt % len(HOSTS)]
        u = url.replace("push2.eastmoney.com", host, 1) if "push2.eastmoney.com" in url else url
        try:
            req = urllib.request.Request(u, headers=hdr)
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            last = e
            time.sleep(min(2 ** attempt * 0.4, 3))
    raise last


def _sina_prefix(code: str) -> str:
    return "sh" + code if code[0] in "56" else "sz" + code


def fetch_sina_etfs() -> dict:
    """从新浪行情接口取价格/涨跌幅/成交额（稳定，作为基础字段）。"""
    wl_map = {c: (nm, cat) for c, nm, cat in WATCHLIST}
    codes = ",".join(_sina_prefix(c) for c, _, _ in WATCHLIST)
    url = "https://hq.sinajs.cn/list=" + codes
    out = {}
    try:
        txt = _get_sina(url)
    except Exception:
        txt = ""  # 新浪挂了不直接返回，后面还有 fuyao 增强源可补缺
    for line in txt.replace("\n", ";").split(";"):
        if "hq_str_" not in line:
            continue
        try:
            key, body = line.split("=", 1)
            code = key.replace("var hq_str_", "").lstrip("shsz")
            parts = body.strip('"').split(",")
            if len(parts) < 10:
                continue
            prev = _num(parts[2])
            cur = _num(parts[3])
            chg = round((cur - prev) / prev * 100, 2) if prev else 0.0
            out[code] = {
                "price": cur,
                "chg": chg,
                "amount": _num(parts[9]),
            }
        except Exception:
            continue

    # fuyao 增强（可选，2026-11-04）：**仅补缺** —— 新浪限流/断连导致部分或全部
    # 观察池没拿到行情时，用同花顺官方快照补上缺失的 code。有 key 才启用；
    # 正常环境 14/14 全绿 ⇒ 零额外请求。任何异常静默降级，绝不阻断主流程。
    fy = _fuyao_mod()
    if fy is not None and fy.enabled() and len(out) < len(WATCHLIST):
        missing = [c for c, _, _ in WATCHLIST if c not in out]
        if missing:
            try:
                extra = fy.etf_quotes(missing)
                out.update(extra)
                fy.STATE["enhanced_quotes"] = len(extra)
                if extra:
                    print("[fuyao] ETF行情补齐 %d/%d 只（新浪缺失 %d 只）"
                          % (len(extra), len(WATCHLIST), len(missing)))
            except Exception as e:  # noqa: BLE001
                print("[fuyao] ETF行情增强跳过：%s" % type(e).__name__)
    return out


def _get_sina(url: str, timeout: int = 15, max_try: int = 3):
    hdr = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
    last = None
    for i in range(max_try):
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return r.read().decode("gbk", "ignore")
        except Exception as e:
            last = e
            time.sleep(0.5 * (i + 1))
    raise last


def _em_ulist_main_net() -> dict:
    """东财 ulist：按 secid **定点**取观察池 14 只 ETF 的资金流四档 —— **主源**。

    ⚠️ 2026-10-30 修：旧主源是 clist 在 ETF 板块（`fs=b:MK0021`，2500+ 只）里
    翻 25 页捞这 14 只，任一页被断链就累计失败、满 3 页直接 break —— 实测 push2
    出口不稳时覆盖率掉到 **0/14**，main_net 全 NULL，页面判定「当日尚未生成」
    回退到前一交易日（丁总反馈「晚上 6 点多还是昨天的数据」）。
    ulist 支持一次传全部 secid，**1 个请求拿全 14 只**，与翻页数无关，稳得多。
    """
    secids = ",".join(_secid_of(c) for c, _, _ in WATCHLIST)
    url = ("https://push2.eastmoney.com/api/qt/ulist/get?pn=1&pz=%d&po=1&np=1"
           "&fltt=2&invt=2&ut=b2884a393a59ad64002292a3e90d46a5"
           "&fields=f12,f62,f66,f72,f78,f84&secids=%s" % (len(WATCHLIST), secids))
    d = json.loads(_get(url))
    out = {}
    for it in ((d.get("data") or {}).get("diff") or []):
        c = it.get("f12")
        if not c:
            continue
        out[c] = {
            "main_net": _num(it.get("f62")),
            "huge_net": _num(it.get("f66")),
            "big_net": _num(it.get("f72")),
            "mid_net": _num(it.get("f78")),
            "small_net": _num(it.get("f84")),
        }
    if not out:
        raise ValueError("东财 ulist 未返回任何资金流")
    return out


def _em_fflow_daykline_main_net(date: str) -> dict:
    """东财 push2his 资金流日线 —— **备源**。

    与 `backfill_date` 走的是同一条出口（push2his），实测在 push2 整段断链的
    网络里依然可达；且它按 secid 单只查询，不受板块翻页影响。
    fflow daykline 字段序：f51日期 f52主力净 f53小单净 f54中单净 f55大单净 f56超大单净。
    """
    out = {}
    for c, _nm, _cat in WATCHLIST:
        url = (
            "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?lmt=5"
            "&klt=101&secid=%s&fields1=f1,f2,f3,f7"
            "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
            "&ut=b2884a393a59ad64002292a3e90d46a5" % _secid_of(c)
        )
        try:
            d = json.loads(_get(url, max_try=2))
        except Exception:  # noqa: BLE001
            continue
        for line in ((d.get("data") or {}).get("klines") or []):
            p = line.split(",")
            if p[0] != date:
                continue
            out[c] = {"main_net": _num(p[1]), "small_net": _num(p[2]),
                      "mid_net": _num(p[3]), "big_net": _num(p[4]),
                      "huge_net": _num(p[5])}
            break
    return out


def _em_clist_main_net() -> dict:
    """东财 clist 板块翻页 —— **最后补漏**（前两源都没凑齐时才走）。

    返回 {code: {main_net, huge_net, big_net, mid_net, small_net}}，单位元。
    东财口径：主力净 = 超大单净 + 大单净（f62 = f66 + f72，已勾稽验证）。

    ⚠️ 2026-10-30 修：旧实现在某一页抛异常时 `break` 整个循环，
    单页网络抽风就会把当天/前几天的资金流静默截断（尤其是 page 1 失败时
    返回空 dict → em_ok=False → fetch_and_save 用 save_etf_daily 先 DELETE
    再 INSERT，把当天已归档的四档数据全部洗成 NULL，页面「主力净 = —」）。
    `_get` 已经带 3 次主机轮换重试；到这里还失败说明这一页确实拿不到，
    但**其它页可能正常**，所以跳过这一页继续下一页，最后由调用方按
    「拿到多少只」决定要不要覆盖旧值。
    """
    wl_map = {c: (nm, cat) for c, nm, cat in WATCHLIST}
    found = {}
    failed_pages = []
    for pn in range(1, 25):
        url = (
            "https://push2.eastmoney.com/api/qt/clist/get?pn=%d&pz=100&po=1&np=1"
            "&fltt=2&invt=2&fid=f3&fs=b:MK0021&fields=f12,f62,f66,f72,f78,f84" % (pn,)
        )
        try:
            d = json.loads(_get(url))
        except Exception as e:  # noqa: BLE001
            failed_pages.append(pn)
            # 连续失败过多 → 主机族整体不可达，早点退出别拖
            if len(failed_pages) >= 3:
                print("[em] 资金流多页失败(%s)：%s" % (failed_pages, e))
                break
            continue
        diff = (d.get("data") or {}).get("diff", [])
        if not diff:
            break
        for it in diff:
            c = it.get("f12")
            if c in wl_map and c not in found:
                found[c] = {
                    "main_net": _num(it.get("f62")),
                    "huge_net": _num(it.get("f66")),
                    "big_net": _num(it.get("f72")),
                    "mid_net": _num(it.get("f78")),
                    "small_net": _num(it.get("f84")),
                }
        if len(found) == len(WATCHLIST):
            break
        time.sleep(0.25)
    return found


def fetch_em_main_net() -> dict:
    """取观察池各 ETF 的资金流四档净额 —— **三源按优先级合并**。

    顺序：① ulist 定点（1 请求拿全 14 只，主源）
          → ② push2his fflow daykline（备源，push2 断链时仍可达）
          → ③ clist 板块翻页（最后补漏）

    ⚠️ 2026-10-30 修：此前只有 clist 翻页一条路，push2 出口不稳时覆盖率
    直接归零 → main_net 全 NULL → 页面回退到前一交易日。现在任一源可用即可。

    返回 {code: {main_net, huge_net, big_net, mid_net, small_net}}，单位元。
    `last_coverage` 记录最终覆盖只数，供 fetch_and_save 选择写入策略。
    """
    found = {}
    try:
        found = _em_ulist_main_net()
    except Exception as e:  # noqa: BLE001
        print("[em] ulist 资金流失败：%s" % e)
    if len(found) < len(WATCHLIST):
        today = datetime.date.today().strftime("%Y-%m-%d")
        for c, v in _em_fflow_daykline_main_net(today).items():
            found.setdefault(c, v)
        if len(found) < len(WATCHLIST):
            try:
                for c, v in _em_clist_main_net().items():
                    found.setdefault(c, v)
            except Exception as e:  # noqa: BLE001
                print("[em] clist 资金流补漏失败：%s" % e)
        if len(found) < len(WATCHLIST):
            miss = [c for c, _n, _t in WATCHLIST if c not in found]
            print("[em] 资金流仅覆盖 %d/%d，缺失 %s" % (len(found), len(WATCHLIST), miss))
    # 让调用方能感知「本次只拿到部分」，从而选择安全的写入策略（COALESCE 而非 DELETE）
    fetch_em_main_net.last_coverage = len(found)
    return found


def fetch_etfs() -> (list, bool):
    """返回 (ETF列表, 主力净流入是否可用)。价格等用新浪，主力净流入优先东方财富。"""
    sina = fetch_sina_etfs()
    try:
        em = fetch_em_main_net()
        em_ok = bool(em)
    except Exception:
        em = {}
        em_ok = False
    result = []
    for c, nm, cat in WATCHLIST:
        s = sina.get(c, {})
        em_row = em.get(c) or {}
        result.append({
            "code": c,
            "name": nm,
            "cat": cat,
            "price": s.get("price"),
            "chg": s.get("chg"),
            "amount": s.get("amount", 0.0),
            "main_net": em_row.get("main_net") if em_ok else None,
            "huge_net": em_row.get("huge_net") if em_ok else None,
            "big_net": em_row.get("big_net") if em_ok else None,
            "mid_net": em_row.get("mid_net") if em_ok else None,
            "small_net": em_row.get("small_net") if em_ok else None,
        })
    return result, em_ok


# 东方财富 datacenter（ETF 份额历史报表）
EM_DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def _em_market(code: str) -> str:
    """东财证券代码市场前缀：15 开头为深市，其余为沪市。"""
    return "SZ" if code.startswith("15") else "SH"


def fetch_em_share_scale(code: str, page_size: int = 400) -> dict:
    """取某只 ETF 的每日份额与单位净值历史。

    数据源：东方财富 datacenter 报表 RPT_ETF_SCALE_CHANGE
    （已与上交所 TOT_VOL、深交所 dqgm 逐日交叉校验一致）。
    返回 {date: {shares, nav, scale, name}}，份额单位亿份、净值单位元。
    """
    mk = _em_market(code)
    url = (
        EM_DC + "?reportName=RPT_ETF_SCALE_CHANGE"
        "&columns=SECUCODE,SECURITY_NAME_ABBR,SHAREEND,PERNAV,NAVNEW,TRADE_DATE,DAY_STR,IS_DAY"
        "&pageNumber=1&pageSize=%d&source=WEB&client=WEB" % page_size
        + "&filter=(SECUCODE%%3D%%22%s.%s%%22)&sortColumns=TRADE_DATE&sortTypes=-1" % (code, mk)
    )
    try:
        d = json.loads(_get(url, ref="https://data.eastmoney.com/"))
    except Exception:
        return {}
    rows = ((d or {}).get("result") or {}).get("data") or []
    out = {}
    for r in rows:
        dt = (r.get("DAY_STR") or (r.get("TRADE_DATE") or "")[:10] or "").strip()
        if not dt or dt in out:
            continue
        out[dt] = {
            "shares": _num(r.get("SHAREEND")),
            "nav": _num(r.get("PERNAV")),
            "scale": _num(r.get("NAVNEW")),
            "name": r.get("SECURITY_NAME_ABBR") or "",
        }
    return out


def sync_shares(days: int = 400, path: str = db.DB_PATH):
    """抓全池 ETF 每日份额，算份额日变动与一级市场净申购金额，落库。

    净申购(亿元) ≈ 份额日变动(亿份) × 单位净值(元/份)；正=净申购(一级买入)，负=净赎回。
    返回 (覆盖日期数, 覆盖基金数, 写入行数)。
    """
    per_date = {}
    hit = 0
    for code, nm, cat in WATCHLIST:
        hist = fetch_em_share_scale(code)
        if not hist:
            continue
        hit += 1
        ordered = sorted(hist.keys())
        for i, dt in enumerate(ordered):
            h = hist[dt]
            chg = None
            if i > 0:
                chg = h["shares"] - hist[ordered[i - 1]]["shares"]
            per_date.setdefault(dt, []).append({
                "code": code, "name": nm, "cat": cat,
                "shares": h["shares"], "nav": h["nav"],
                "chg_shares": chg,
                "net_sub": (chg * h["nav"]) if chg is not None else None,
            })
        time.sleep(0.2)
    n = 0
    for dt, rows in per_date.items():
        db.save_share_rows(dt, rows, path)
        n += len(rows)
    return (len(per_date), hit, n)


def _norm_news_title(t: str) -> str:
    """去 HTML 标签 + 压空白。东财搜索结果标题含 <em> 高亮标签，需剥掉。"""
    t = re.sub(r"<[^>]+>", "", (t or ""))
    return re.sub(r"\s+", " ", t).strip()


def mask_news_title(t: str) -> str:
    """展示层去敏：抓取层保留真实'国家队'以匹配新闻源关键词（NEWS_EM_KEYWORDS），
    但对外展示（/api/data 接口、静态站 data.json）将敏感词替换为代号'汪汪队'，
    避免敏感信息露出。仅作用于展示，不改写 DB。"""
    if not t:
        return t
    return t.replace("国家队", "汪汪队")


def mask_news_items(items: list) -> list:
    """对新闻列表逐条做标题去敏，返回新列表（不改动入参）。"""
    if not items:
        return items
    out = []
    for it in items:
        if isinstance(it, dict) and "title" in it:
            it = dict(it)
            it["title"] = mask_news_title(it.get("title") or "")
        out.append(it)
    return out


def _norm_news_time(t) -> str:
    """统一时间格式为 'YYYY-MM-DD HH:MM'（可直接字符串排序）。

    同花顺/新浪给 Unix 时间戳（如 1793335013），东财给 'YYYY-MM-DD HH:MM:SS'，
    混用会导致排序错乱，故统一转换。
    """
    s = str(t or "").strip()
    if not s:
        return ""
    if s.isdigit():
        try:
            return datetime.datetime.fromtimestamp(int(s)).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""
    m = re.match(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})", s)
    if m:
        return m.group(1) + " " + m.group(2)
    return s


def _extract_news_from_json(obj, title_keys=("title", "t", "subject"), url_keys=("url", "u", "link"), time_keys=("ctime", "time", "date", "dt")):
    """在任意JSON结构里找新闻列表，提取标题/链接/时间。"""
    def find_list(o):
        if isinstance(o, list):
            return o
        if isinstance(o, dict):
            for v in o.values():
                r = find_list(v)
                if r:
                    return r
        return None

    lst = find_list(obj)
    if not lst:
        return []
    out = []
    for item in lst:
        if not isinstance(item, dict):
            continue
        title = next((str(item.get(k, "")) for k in title_keys if item.get(k)), "")
        url = next((str(item.get(k, "")) for k in url_keys if item.get(k)), "")
        t = next((str(item.get(k, "")) for k in time_keys if item.get(k)), "")
        if title:
            out.append({"title": _norm_news_title(title), "url": url, "time": _norm_news_time(t)})
    return out


def _em_search_url(keyword: str, page: int = 1, page_size: int = 20) -> str:
    """东财搜索 API（param= JSON 格式）。

    旧版 ?search_text=...&type=12 已于上游废弃（400 Bad Request），
    现版必须把检索条件放进 param 的 JSON 里，keyword 为检索词。
    sort=default 为综合排序（相关度优先），比 sort=time 更贴合「汪汪队」主题。
    """
    param = {
        "uid": "",
        "keyword": keyword,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientVersion": "curr",
        "clientType": "web",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "default",
                "pageIndex": page,
                "pageSize": page_size,
            }
        },
    }
    qs = urllib.parse.quote(json.dumps(param, ensure_ascii=False))
    return "https://search-api-web.eastmoney.com/search/jsonp?cb=cb&param=" + qs


def _news_score(title: str) -> int:
    """标题相关性打分（0=不相关）。

    检索源只作「候选池」，是否入榜一律以**标题**判定，避免正文提及即入榜
    （东财搜「中央汇金」会带出大量「机构风向标 | 某公司持仓」类噪音）。

    噪音治理：
      - 「保证金」含「证金」子串 → 期货保证金调整通知被误收；
      - 「汇金科技」「浙商汇金」等公司/基金名 → 三季报公告被误收；
      - 裸 ETF 标题（ETF午评 / 两市成交额 / 央行抛售ETF）→ 非国家队动向；
      - 「国家队」在中文里也指科技/高校/航天/数据等领域 → 须搭配金融语境。
    """
    if not title:
        return 0
    if any(b in title for b in NEWS_BLOCK):
        return 0
    if any(n in title for n in NEWS_NOISE):
        return 0
    # 中央汇金/证金公司/汇金公司：唯一指向证券国家队，命中即相关
    if any(k in title for k in NEWS_STRONG):
        return SCORE_STRONG
    # 国家队：须搭配金融语境才属本面板
    if NEWS_GUOJIA in title:
        return SCORE_STRONG if any(c in title for c in NEWS_FIN_CTX) else 0
    if any(k in title for k in NEWS_WEAK_SUBJECT):
        return SCORE_WEAK if any(c in title for c in NEWS_A_CTX) else 0
    if any(k in title for k in NEWS_ACTION):
        return SCORE_WEAK if any(c in title for c in NEWS_A_CTX) else 0
    if "ETF" in title:
        return SCORE_WEAK if any(c in title for c in NEWS_BUY_CTX) else 0
    return 0


def _finalize_news(scored: list) -> list:
    """去重（同一标题多源命中只留一条，保留分高者）+ 同题异写合并 + 截断到 12。

    供 fetch_news 在「本地优先」与「借快照差量」两种口径下复用，避免逻辑分叉。
    注意：本函数**不**弹出 _score——fetch_news 可能在借差量后二次调用，
    若此处弹出会导致本地 items 在第二次排序时分数归零、被快照条目挤掉。
    _score 由 fetch_news 在最终返回前统一弹出。
    """
    best = {}
    for it in scored:
        key = it["title"][:40]
        sc = it.get("_score", 0)
        if key not in best or sc > best[key].get("_score", 0):
            best[key] = it
    cand = sorted(best.values(),
                  key=lambda x: (x.get("_score", 0), str(x.get("time") or "")),
                  reverse=True)
    # 同题异写去重：同一事件（打假/仿冒/声明类）只保留最新的一条，
    # 否则「中央汇金打假」的 7 种写法会刷屏挤掉时效新闻
    seen_events = set()
    out = []
    for it in cand:
        ev = next((g for g, toks in NEWS_EVENT_GROUPS
                   if any(t in it["title"] for t in toks)), None)
        if ev:
            if ev in seen_events:
                continue
            seen_events.add(ev)
        out.append(it)
        if len(out) >= 12:
            break
    return out


def fetch_news() -> list:
    """多源尽力抓取与国家队相关的财经新闻，失败则降级返回空。

    每个源独立 try/except：单源故障不影响其它源产出。
    """
    raw = []
    # 主源：东财搜索（keyword 精确，作候选池；入榜仍以标题判定）
    # 取 2 页：单页仅 20 条，含大量旧文，取 2 页可覆盖到「ETF数据专题-国家队今天买了啥」这类高价值条目
    for kw in NEWS_EM_KEYWORDS:
        for pg in (1, 2):
            raw.append(("东方财富", _em_search_url(kw, page=pg), "https://search.eastmoney.com/"))
    # 备源：同花顺 7×24 快讯（实时全量流，靠标题关键词过滤）
    raw.append(("同花顺", "https://news.10jqka.com.cn/tapp/news/push/stock/?page=1&tag=&track=website&pagesize=30",
                "https://news.10jqka.com.cn/"))
    # 备源：新浪财经滚动（pageid=153 + lid=2516 全量流，靠标题关键词过滤）
    raw.append(("新浪财经", "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&k=&num=40&page=1",
                "https://finance.sina.com.cn/"))

    scored = []
    # 逐源诊断报告：[(源名, 原始条数, 入榜条数, 错误类型或 None)]
    # 只有总数（"新闻 0 条"）时无法判断「上游挂了」还是「当天确实没有相关新闻」，
    # VPS 上排查过一次，加上这个后日志一眼可见是哪个源在掉链子。
    report = []
    for src, url, ref in raw:
        try:
            txt = _get(url, ref)
            txt = re.sub(r"^cb\(|\)$", "", txt.strip()) if txt.strip().startswith("cb(") else txt
            d = json.loads(txt)
            items = _extract_news_from_json(d)
            hit = 0
            for it in items:
                sc = _news_score(it["title"])
                if sc <= 0:
                    continue
                it["src"] = src
                it["_score"] = sc
                scored.append(it)
                hit += 1
            report.append((src, len(items), hit, None))
        except Exception as e:
            report.append((src, 0, 0, type(e).__name__))
            continue

    # fuyao 增强源（可选，2026-11-04）：作为**第 4 个候选源**并入同一套打分/去重，
    # 有 key 才启用。入榜仍一律走 _news_score()，口径与原三源完全一致，不会因
    # 新增源放宽标准；key 无效 / 2004 / 限流 / 网络异常一律静默降级。
    fy = _fuyao_mod()
    if fy is not None and fy.enabled():
        try:
            items = fy.news_items()
            hit = 0
            for it in items:
                sc = _news_score(it["title"])
                if sc <= 0:
                    continue
                it["_score"] = sc
                scored.append(it)
                hit += 1
            report.append(("同花顺API", len(items), hit, None))
            fy.STATE["enhanced_news"] = hit
            if hit:
                print("[fuyao] 新闻候选 %d 条，相关入榜 %d 条" % (len(items), hit))
        except Exception as e:  # noqa: BLE001
            report.append(("同花顺API", 0, 0, type(e).__name__))
            print("[fuyao] 新闻增强跳过：%s" % type(e).__name__)
    fetch_news.last_report = report

    # 兜底（v2.7.1）：本地最终条数 < 快照条数时，借差量补齐，让所有部署形态新闻一致。
    # 旧口径「三源全空才借」会让 VPS 因新浪恒命中 1 条而永远借不到东财被拦掉的其余条数；
    # 新口径改为「本地条数 < 快照条数」即借差量（按标题去重并入），正常环境本地≥快照不额外请求。
    # 微优化：本地已填满 12（快照上限）则不可能缺，直接跳过兜底请求。
    local_out = _finalize_news(scored)
    snapshot = []
    if len(local_out) < 12:
        snapshot = _fetch_remote_news()
        if snapshot and len(local_out) < len(snapshot):
            local_titles = {it["title"] for it in scored}
            for it in snapshot:
                if it["title"] not in local_titles:
                    scored.append(it)
            report.append(("公开快照", len(snapshot), len(snapshot), None))
            fetch_news.last_report = report
            local_out = _finalize_news(scored)
    for it in local_out:
        it.pop("_score", None)
    return local_out


def _fetch_remote_news() -> list:
    """从公开快照（默认 GitHub Pages data.json）兜底取新闻。

    仅当本机三源全部抓空时由 fetch_news 调用。返回与本地新闻同构、带 _score
    的列表，可直接并入 scored（后续走同一套去重/同题合并/截断逻辑）。
    任意网络异常都静默返回空，绝不阻断主流程。
    """
    url = NT_NEWS_FALLBACK_URL
    if not url:
        return []
    try:
        txt = _get(url)
        d = json.loads(txt)
    except Exception as e:
        print("[news-fallback] 拉取公开快照失败: %s" % type(e).__name__)
        return []
    items = []

    def _add(title, t, u):
        title = (title or "").strip()
        if not title:
            return
        items.append({"title": title, "time": t or "", "url": u or "",
                      "src": "公开快照", "_score": SCORE_STRONG})

    for date, lst in (d.get("news") or {}).items():
        for it in lst or []:
            _add(it.get("title"), it.get("time") or (date + " 00:00"), it.get("url"))
    fb = d.get("news_fallback") or {}
    for it in fb.get("items") or []:
        _add(it.get("title"), it.get("time") or (fb.get("date", "") + " 00:00"),
             it.get("url"))
    # 按标题去重（跨日期同题）
    seen = set()
    uniq = []
    for it in items:
        if it["title"] in seen:
            continue
        seen.add(it["title"])
        uniq.append(it)
    return uniq


# 公开快照 data.json 单次运行内缓存：121 天回补若每天各拉一次 1MB 快照太浪费，
# 同一 url 只解析一次，复用 days 全文（资金流/新闻/逐笔三类兜底共用）。
_FUND_FLOW_CACHE = {}


def _get_snapshot(url: str) -> dict:
    """拉取并缓存公开快照 data.json 全文（单次运行内复用，避免多日期重复下载）。

    资金流 / 新闻 / 逐笔三类兜底共用同一份 data.json，走同一缓存。
    任意网络异常静默返回 {}，绝不阻断主流程。
    """
    if not url:
        return {}
    d = _FUND_FLOW_CACHE.get(url)
    if d is None:
        try:
            txt = _get(url)
            d = json.loads(txt)
            _FUND_FLOW_CACHE[url] = d
        except Exception as e:
            print("[fallback] 拉取公开快照失败: %s" % type(e).__name__)
            return {}
    return d


def _fetch_remote_fund_flow(date: str) -> dict:
    """从公开快照（默认 GitHub Pages data.json）取 days[date] 的四档净额。

    仅当本地资金流覆盖率不足（如 VPS 境外 IP 被东财 push2 限流，部分 ETF 拿不到）
    时由 _apply_fund_flow_fallback 调用。返回 {code: {main_net,huge_net,big_net,
    mid_net,small_net}}（单位元，字段可能为 None）。任意网络异常静默返回 {}，
    绝不阻断主流程。
    """
    d = _get_snapshot(NT_FUND_FALLBACK_URL)
    out = {}
    for row in (d.get("days") or {}).get(date) or []:
        code = row.get("code")
        if not code:
            continue
        out[code] = {
            "main_net": row.get("main_net"),
            "huge_net": row.get("huge_net"),
            "big_net": row.get("big_net"),
            "mid_net": row.get("mid_net"),
            "small_net": row.get("small_net"),
        }
    return out


def _fetch_remote_tick_rows(date: str) -> list:
    """从公开快照（默认 GitHub Pages data.json）取 tick_rows[date] 的紧凑大单行。

    与 _fetch_remote_fund_flow 同源（同一份 data.json，走同一缓存）。
    返回 [[code,ttime,price,volume,direction,amount], ...]；snapshot 该日期不存在
    → 返回 []。任意网络异常已由 _get_snapshot 静默吞掉。
    """
    d = _get_snapshot(NT_TICK_FALLBACK_URL)
    rows = (d.get("tick_rows") or {}).get(date) or []
    # 只保留形如 6 元组的紧凑行，过滤脏数据，避免把坏行写进库
    return [r for r in rows if isinstance(r, (list, tuple)) and len(r) >= 6]


def _apply_tick_fallback(date: str, path: str = db.DB_PATH) -> int:
    """本地某日无逐笔大单时，从公开快照借同日的 tick_rows 补齐并落库。

    原则（与资金流兜底同源）：
      - 仅补缺：本地该日期**已经有**大单（哪怕部分）则直接跳过，绝不覆盖/混入
        真实抓取值（避免用陈旧/同源快照盖掉当天真实滚动窗口）；
      - 仅同日期：只借 tick_rows[date]，绝不借别的日期；
      - 网络异常静默降级，返回 0，绝不阻断主流程。
    返回实际借入并落库的笔数；借到时把 meta tick_fallback 置 "1"（供 /api/status 可见）。
    """
    # 延迟导入，避免 fetcher ↔ intraday 在模块加载期形成循环依赖
    from intraday import restore_tick_rows
    # 本地该日期已有一行及以上大单 → 不借（保护真实抓取值）
    if db.get_tick_by_date(date, path=path):
        return 0
    rows = _fetch_remote_tick_rows(date)
    if not rows:
        return 0
    n = restore_tick_rows(date, rows, path=path)
    if n:
        # 一律用关键字传 path：测试里的 db shim 会 setdefault("path")，
        # 位置传参会与之冲突成 "multiple values for argument 'path'"。
        db.set_meta("tick_fallback", "1", path=path)
    return n


def _apply_fund_flow_fallback(date: str, etfs: list) -> int:
    """把公开快照 days[date] 的四档净额借来补齐 etfs 里 main_net 为 None 的 code。

    原则（与新闻兜底同源，但语义略不同）：
      - 仅补缺：本地已取到真实值（main_net 非 None）的 code **绝不复写**，
        本地真实数据是权威，快照只补缺失项；
      - 仅同日期：只借 days[date]（与正在归档的日期严格一致），**绝不借别的日期**
        （避免把昨日净买入错标成今日）；
      - 快照里也是空（main_net 为 None）的 code 不借，避免用 NULL 覆盖本地 NULL。
    返回实际补齐的只数；任意异常已由 _fetch_remote_fund_flow 静默吞掉。
    """
    snap = _fetch_remote_fund_flow(date)
    if not snap:
        return 0
    by_code = {e["code"]: e for e in etfs}
    filled = 0
    for code, row in snap.items():
        e = by_code.get(code)
        if not e or e.get("main_net") is not None:
            continue  # 本地已有真实值，跳过
        if row.get("main_net") is None:
            continue  # 快照里也是空，没的借
        e["main_net"] = row["main_net"]
        e["huge_net"] = row.get("huge_net")
        e["big_net"] = row.get("big_net")
        e["mid_net"] = row.get("mid_net")
        e["small_net"] = row.get("small_net")
        filled += 1
    return filled


def split_news_by_date(news: list) -> dict:
    """按新闻自身发布日期分组：{'2026-10-30': [row, ...], ...}。

    为什么按发布日期而不是「抓取当天」归档：
    06:00 补抓时 target_date() 指向的是**上一交易日**，若把当日新闻全记到
    上一交易日名下，日期与新闻内容就对不上了。每条新闻带 time（YYYY-MM-DD HH:MM），
    取其日期部分归档最贴合事实。
    无法解析日期的条目归到 today。
    """
    groups = {}
    today = datetime.date.today().isoformat()
    for it in news or []:
        t = str(it.get("time") or "")
        d = t[:10] if len(t) >= 10 and t[4] == "-" and t[7] == "-" else today
        groups.setdefault(d, []).append(it)
    return groups


def is_trading_day(d=None) -> bool:
    """A 股交易日判定：走 common.market_calendar 的节假日表 + 周末。

    ⚠️ 2026-10-30 修：旧实现只 `d.weekday() < 5`，法定节假日（元旦/春节/
    清明/劳动/端午/中秋/国庆）里落在周一到周五的日子被当成交易日，
    ensure_today / ensure_history / staticsite.sync_missing / scheduler_loop
    都会在这些天跑一次抓取 → 上游给的是**节前最后一个交易日**的收盘快照，
    被错标到节假日当天入库、写进 data.json、进而在趋势图上出现幽灵交易日；
    06:00 那次尤其明显（会连着几天写同一天数据）。现在统一走日历。
    """
    try:
        from common.market_calendar import is_a_share_trading_day
    except ImportError:  # 兼容被直接 exec 时的 sys.path 状态
        import sys as _sys, os as _os
        _root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        if _root not in _sys.path:
            _sys.path.insert(0, _root)
        from common.market_calendar import is_a_share_trading_day
    return is_a_share_trading_day(d)


def fetch_and_save(force: bool = False) -> dict:
    """抓取当日数据并入库。返回状态摘要。

    写入策略（2026-10-30 加固）：
    - 只有当「主力接口全绿 + 观察池 14 只全部拿到」时才走覆盖式
      `save_etf_daily`（先 DELETE 当日旧行再 INSERT）；
    - 部分覆盖 / 主力接口挂了 → 改走 `upsert_etf_rows`（COALESCE，
      空值不覆盖旧值），避免抓 1 页失败就把已归档的四档数据洗成 NULL。

    新闻解耦（2026-11-01）：新闻不受「交易日」门槛限制——周末/节假日照样抓
    （政策、公司公告常在非交易日发布）。只有 ETF 行情/主力净额在非交易日才跳过；
    新闻按自身发布日期归档（见 split_news_by_date），增量合并不覆盖。
    """
    today = datetime.date.today().strftime("%Y-%m-%d")
    trading = is_trading_day()

    # 本次抓取的增强计数重置（供 /api/status 展示；模块不可用则跳过）
    fy = _fuyao_mod()
    if fy is not None:
        fy.reset_counters()

    # 新闻：无论是否交易日都抓（周末也有国家队/汇金相关新闻），先入库
    news = fetch_news()
    for d, items in split_news_by_date(news).items():
        db.merge_news(d, items)
    db.set_meta("news_ok", "1" if news else "0")

    if not trading:
        # 非交易日：无行情可抓，仅保留新闻抓取结果，并刷新 last_fetch 让时间戳前进
        db.set_meta("last_fetch", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        db.set_meta("last_date", today)
        return {
            "ok": False,
            "reason": "非交易日（周末或法定节假日），仅抓取新闻，跳过行情",
            "date": today,
            "news_count": len(news),
        }

    etfs, em_ok = fetch_etfs()

    # 资金流兜底（2026-11-01）：本地覆盖率不足时（境外 IP 被东财 push2 限流，
    # 部分 ETF 拿不到 → 净买入合计失真），从公开快照 data.json 借同一天的四档净额
    # 补齐。仅补缺、绝不复写本地真实值；正常环境（14/14）不触发、无额外请求。
    missing_codes = [e["code"] for e in etfs if e.get("main_net") is None]
    fund_fallback = 0
    if missing_codes:
        fund_fallback = _apply_fund_flow_fallback(today, etfs)
        if fund_fallback:
            print("[fund-fallback] 本地资金流覆盖 %d/%d，从公开快照补齐 %d 只"
                  % (len(WATCHLIST) - len(missing_codes), len(WATCHLIST), fund_fallback))

    total_main_net = sum((e["main_net"] or 0) for e in etfs)
    total_amount = sum(e["amount"] for e in etfs)

    coverage = getattr(fetch_em_main_net, "last_coverage", 0)
    # 有效覆盖率：本地 + 兜底后实际有 main_net 的只数，决定写入策略与状态展示。
    # 用有效覆盖率替代本地覆盖率，使「本地被限流 + 兜底补齐」后能走覆盖式写入、
    # 让净买入合计回归真实（否则 13 只仍为 NULL、合计被低估）。
    effective_coverage = sum(1 for e in etfs if e.get("main_net") is not None)
    full_snapshot = em_ok and effective_coverage >= len(WATCHLIST)
    if full_snapshot:
        db.save_etf_daily(today, etfs)
    else:
        n = db.upsert_etf_rows(today, etfs)
        fb_note = ("，公开快照补齐 %d 只" % fund_fallback) if fund_fallback else ""
        if em_ok:
            print("[fetch] 东财资金流仅覆盖 %d/%d%s，改用 COALESCE 保留旧值 (%d 行)"
                  % (coverage, len(WATCHLIST), fb_note, n))
        else:
            print("[fetch] 东财资金流不可达%s，价格入库走 upsert 以保留 main_net 旧值 (%d 行)"
                  % (fb_note, n))
    db.set_meta("last_fetch", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    db.set_meta("last_date", today)
    db.set_meta("mainnet_ok", "1" if em_ok else "0")
    db.set_meta("mainnet_fallback", "1" if fund_fallback else "0")

    return {
        "ok": True,
        "date": today,
        "etf_count": len(etfs),
        "mainnet_ok": em_ok,
        "coverage": effective_coverage,
        "fund_fallback": fund_fallback,
        "total_main_net_yi": round(total_main_net / 1e8, 2),
        "total_amount_yi": round(total_amount / 1e8, 2),
        "news_count": len(news),
    }


# ---------------- 历史回补：还原已收盘交易日的真实快照 ----------------
# 公开行情接口默认只给实时数据；本组函数走 push2his 历史接口，
# 准确还原历史交易日的 收盘价/成交额/涨跌幅/主力净流入，用于补回缺失的历史日期。
def _hist_get(url: str, retries: int = 3):
    hdr = dict(HDR)
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=20, context=CTX) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            last = e
            time.sleep(0.4 * (i + 1))
    raise last


def _secid_of(code: str) -> str:
    return ("1." if code[0] in "56" else "0.") + code


def backfill_date(date: str) -> dict:
    """还原指定已收盘交易日(YYYY-MM-DD)的真实快照并入库。返回统计。"""
    rows, ok_main, missing = [], 0, []
    d = datetime.date.fromisoformat(date)
    beg = (d - datetime.timedelta(days=12)).strftime("%Y%m%d")
    end = d.strftime("%Y%m%d")
    for code, name, cat in WATCHLIST:
        sid = _secid_of(code)
        # 1) 历史K线：收盘价/成交额(元)/涨跌幅%
        kn = None
        try:
            u_k = (
                "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s"
                "&fields1=f1,f2,f3,f4,f5,f6"
                "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
                "&klt=101&fqt=1&beg=%s&end=%s" % (sid, beg, end)
            )
            dk = json.loads(_hist_get(u_k))
            for ln in (dk.get("data") or {}).get("klines", []):
                p = ln.split(",")
                if p[0] == date and len(p) >= 9:
                    kn = (float(p[2]), float(p[6]), float(p[8]))
                    break
        except Exception:
            kn = None
        # 2) 资金流历史：主力净流入 + 四档净额(元)
        #    fflow daykline 字段序：f51日期 f52主力净 f53小单净 f54中单净 f55大单净 f56超大单净
        mn = huge = big = mid = small = None
        try:
            u_f = (
                "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?lmt=40"
                "&klt=101&secid=%s"
                "&fields1=f1,f2,f3,f4"
                "&fields2=f51,f52,f53,f54,f55,f56" % sid
            )
            df = json.loads(_hist_get(u_f))
            for ln in (df.get("data") or {}).get("klines", []):
                p = ln.split(",")
                if p[0] == date and len(p) >= 6:
                    mn, small, mid, big, huge = (float(p[i]) for i in range(1, 6))
                    break
        except Exception:
            mn = huge = big = mid = small = None
        if kn is None:
            missing.append((code, name))
            continue
        price, amount, chg = kn
        rows.append({
            "code": code, "name": name, "cat": cat,
            "price": price, "chg": chg, "amount": amount, "main_net": mn,
            "huge_net": huge, "big_net": big, "mid_net": mid, "small_net": small,
        })
        if mn is not None:
            ok_main += 1
        time.sleep(0.08)
    # 资金流兜底（2026-11-01）：回补路径同样会因 VPS 境外 IP 被东财 push2his 限流
    # 而拿不到部分 ETF 的四档净额（mn=None）→ 借公开快照 days[date] 补齐，使历史行
    # 也能对齐（否则重跑回补仍留 13 只 NULL、净买入合计失真）。仅补缺、绝不复写本地
    # 真实值；正常环境（14/14）不触发、无额外请求（快照已缓存）。
    fund_fallback = _apply_fund_flow_fallback(date, rows)
    if fund_fallback:
        print("[fund-fallback][backfill] %s 从公开快照补齐 %d 只" % (date, fund_fallback))
    db.save_etf_daily(date, rows)
    # 历史新闻无法精确还原，但**不能清空**该日期已有的新闻
    # （回补行情时会把同日期已抓到的新闻抹掉）。
    if not db.get_news_by_date(date):
        db.save_news(date, [])  # 该日期确实没有新闻记录时留空
    return {"date": date, "etf_count": len(rows), "mainnet_ok": ok_main,
            "fund_fallback": fund_fallback, "missing": missing}


def backfill_range(dates, path: str = db.DB_PATH) -> dict:
    """批量精确还原一批已收盘交易日（价格/成交额/涨跌幅 + 四档资金流）并入库。

    与 backfill_date 的区别：每只基金只发 2 次请求（一次日K、一次资金流 daykline），
    一次拿全区间，因此上百个交易日也只需 28 次请求。

    数据源与降级：
      - 资金流四档：东财 fflow daykline（**上游只保留最近约 121 个交易日**，更早取不到）
      - 价格/成交额：优先东财 push2his 日K（成交额精确）；该路径不可用时降级新浪日K
        （成交额按 收盘价×成交量 估算，口径说明中有注明）
    返回 {"days": n, "rows": n, "no_kline": [...], "no_flow": [...]}。
    """
    dset = sorted({d for d in (dates or []) if d})
    if not dset:
        return {"days": 0, "rows": 0, "no_kline": [], "no_flow": []}
    dset = set(dset)
    d0 = datetime.date.fromisoformat(sorted(dset)[0])
    d1 = datetime.date.fromisoformat(sorted(dset)[-1])
    beg = (d0 - datetime.timedelta(days=15)).strftime("%Y%m%d")
    end = d1.strftime("%Y%m%d")
    per_day, no_kline, no_flow = {}, set(), set()
    for code, name, cat in WATCHLIST:
        sid = _secid_of(code)
        kmap = _fetch_kline_map(sid, beg, end, dset)
        fmap = _fetch_flow_map(sid, dset)
        for ds in dset:
            kn = kmap.get(ds)
            fl = fmap.get(ds)
            if not kn:
                no_kline.add(ds)
                continue
            if not fl:
                no_flow.add(ds)
            price, amount, chg = kn
            mn = huge = big = mid = small = None
            if fl:
                mn, small, mid, big, huge = fl
            per_day.setdefault(ds, []).append({
                "code": code, "name": name, "cat": cat,
                "price": price, "chg": chg, "amount": amount,
                "main_net": mn, "huge_net": huge, "big_net": big,
                "mid_net": mid, "small_net": small,
            })
        time.sleep(0.1)
    for ds, rows in per_day.items():
        # 资金流兜底（2026-11-01）：与 backfill_date 同源——VPS 境外 IP 被 push2his
        # 限流时 mn=None，借公开快照 days[ds] 补齐后再 upsert（COALESCE 让非 NULL
        # 快照值覆盖本地 NULL），使重跑回补也能修复历史行的资金流。
        fb = _apply_fund_flow_fallback(ds, rows)
        if fb:
            print("[fund-fallback][backfill] %s 从公开快照补齐 %d 只" % (ds, fb))
        db.upsert_etf_rows(ds, rows, path)
    return {"days": len(per_day), "rows": sum(len(v) for v in per_day.values()),
            "no_kline": sorted(no_kline), "no_flow": sorted(no_flow)}


def _fetch_kline_map(sid: str, beg: str, end: str, dset) -> dict:
    """日K：{date: (收盘价, 成交额, 涨跌幅%)}。东财优先，失败降级新浪。

    东财 push2his 的 kline 路径有时整段不可用（连接被断），此时降级新浪日K，
    成交额按 收盘价×成交量 估算；涨跌幅用新浪完整序列相邻收盘价自算。
    """
    global _EM_KLINE_OK
    # 1) 东财 push2his（成交额精确）；一旦失败本次运行不再重试该路径
    if _EM_KLINE_OK is not False:
        try:
            u = (
                "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s"
                "&fields1=f1,f2,f3,f4,f5,f6"
                "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
                "&klt=101&fqt=1&beg=%s&end=%s" % (sid, beg, end)
            )
            dk = json.loads(_hist_get(u))
            out = {}
            for ln in (dk.get("data") or {}).get("klines", []):
                p = ln.split(",")
                if len(p) >= 9 and p[0] in dset:
                    out[p[0]] = (float(p[2]), float(p[6]), float(p[8]))
            if out:
                _EM_KLINE_OK = True
                return out
            _EM_KLINE_OK = False
        except Exception:
            _EM_KLINE_OK = False
    # 2) 新浪日K（成交额按 收盘价×成交量 估算）
    try:
        code = sid.split(".")[-1]
        sym = ("sh" if code[0] in "56" else "sz") + code
        u = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketData.getKLineData"
             "?symbol=%s&scale=240&ma=no&datalen=600" % sym)
        arr = json.loads(_hist_get(u))
        full = {}          # 完整序列（用于算涨跌幅）
        for it in arr:
            ds = (it.get("day") or "")[:10]
            try:
                full[ds] = (float(it.get("close") or 0), float(it.get("volume") or 0))
            except Exception:
                continue
        ordered = sorted(full.keys())
        out = {}
        for i, ds in enumerate(ordered):
            if ds not in dset:
                continue
            close, vol = full[ds]
            chg = 0.0
            if i > 0 and full[ordered[i - 1]][0]:
                prev = full[ordered[i - 1]][0]
                chg = round((close - prev) / prev * 100, 2)
            out[ds] = (close, close * vol, chg)
        return out
    except Exception:
        return {}


def _fetch_flow_map(sid: str, dset) -> dict:
    """资金流四档：{date: (主力净, 小单净, 中单净, 大单净, 超大单净)}，单位元。"""
    try:
        u = (
            "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?lmt=200"
            "&klt=101&secid=%s"
            "&fields1=f1,f2,f3,f4"
            "&fields2=f51,f52,f53,f54,f55,f56" % sid
        )
        df = json.loads(_hist_get(u))
        out = {}
        for ln in (df.get("data") or {}).get("klines", []):
            p = ln.split(",")
            if len(p) >= 6 and p[0] in dset:
                out[p[0]] = tuple(float(p[i]) for i in range(1, 6))
        return out
    except Exception:
        return {}


def refresh_flow_only(date: str, path: str = db.DB_PATH) -> dict:
    """只刷新某日已归档数据的资金流四档（不动价格/成交额/新闻）。

    用于每日"强制重刷"：某天可能已抓到但四档不全（或上游当时没出数），
    这里用历史资金流接口精确还原该日的四档与主力净，只更新资金流列。
    """
    dset = {date}
    updates = []
    for code, name, cat in WATCHLIST:
        fl = _fetch_flow_map(_secid_of(code), dset).get(date)
        if not fl:
            continue
        mn, small, mid, big, huge = fl
        updates.append({"code": code, "main_net": mn, "huge_net": huge,
                        "big_net": big, "mid_net": mid, "small_net": small})
    n = db.update_flow_rows(date, updates, path) if updates else 0
    return {"date": date, "updated": n, "found": len(updates)}


if __name__ == "__main__":
    print(json.dumps(fetch_and_save(), ensure_ascii=False, indent=2))
