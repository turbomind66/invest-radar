"""qdii/fetcher_signal.py — 模块D：每日美股信号（14:30，美股交易日）。

⚠️ 时区事实：14:30 北京时间 = 美东约 01:30–02:30，美股已收盘，抓到的是
「上一交易日收盘（非实时）」；美股期货（CME Globex 近24h）为实时。

数据源（国内友好、实测可达，替代被封的雅虎）：
- 期货实时：新浪 `hf_ES`(标普500指数期货) / `hf_NQ`(纳指期货，标的为 CME 纳指100期货)
- 现货昨收（非实时）：新浪 `gb_inx`(标普500) / 腾讯 `usIXIC`(纳斯达克综合指数＝口语「纳指」)
- **美债10年期收益率（实时）**：CNBC 报价服务（腾讯 `usTNX` 已失效，2026-10-29 实测不返回）
- **VIX（日频收盘，参与避险判断 + 面板展示）**：CBOE 官方日收盘 CSV
"""
import os
import sys
import re
import json
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
# BASE（本模块目录）必须排在 ROOT（部署根）之前 —— 否则部署根下存在同名
# 模块时（沙箱里确有旧副本），`import constants/db` 会加载到那一份，
# 表现为「部署了新代码、线上却跑着旧行为」。
for p in (ROOT, BASE):
    if p not in sys.path:
        sys.path.insert(0, p)
if sys.path and sys.path[0] != BASE:
    sys.path.remove(BASE)
    sys.path.insert(0, BASE)

from common.http import fetch_text
import db
import constants as C

# 新浪期货代码 -> (symbol, name)
# hf_NQ 的官方名称是「纳斯达克指数期货」，标的即 CME 纳指100期货（全球唯一的纳指期货品种），
# 页面按用户口径统一叫「纳指期货」。
FUTURES_MAP = {
    "hf_ES": ("SPX_FUT", "标普500期货"),
    "hf_NQ": ("NDX_FUT", "纳指期货"),
    # 日经225 期货（东京）：欧洲/亚太类别的实时信号源之一
    "hf_NK": ("NK", "日经225期货"),
}
# 新浪美股指数（标普500 现货，上一交易日收盘）
SINA_INDEX_URL = "https://hq.sinajs.cn/list=gb_inx,gb_ixic"
# 腾讯财经美股代码 -> (symbol, name, is_realtime)
# usIXIC = 纳斯达克综合指数（口语「纳指」）；usNDX = 纳斯达克100，两者不同：
# 综合指数含全部纳斯达克上市股票（约 3300 只），纳指100 只含 100 只非金融大盘股。
# ⚠️ 腾讯 `usTNX`（美债10Y）已不再返回数据（2026-10-29 实测请求 3 个代码只回 2 条），
#    美债改用 CNBC 实时报价，见 fetch_cnbc_ust10y()。
# usEWU = iShares MSCI 英国 ETF —— 仅作富时100 的**降级代理**：
# 富时100 现已优先走东财真实指数 100.FTSE（见 fetch_em_global_index），
# 东财不可达才在 fetch_us_signal 里降级回 usEWU。故不进主 TENCENT_SYMBOLS（避免重复写 FTSE）。
TENCENT_URL = "https://qt.gtimg.cn/q=usIXIC"
TENCENT_SYMBOLS = {
    "usIXIC": ("IXIC", "纳指现货", 0),
}
# 境内场内 ETF（实时）：DAX / 法国CAC40 的**降级代理**——东财真实指数（100.GDAXI/FCHI）
# 不可达时才退回这里。symbol 与东财真实指数保持一致（DAX/CAC40），仅名称加「（代理）」区分。
# 新浪 A 股行情字段：parts[0]=名称 parts[2]=昨收 parts[3]=现价 parts[30]=日期 parts[31]=时间
SINA_A_ETF = {
    "sh513030": ("DAX", "德国DAX指数"),
    "sh513080": ("CAC40", "法国CAC40指数"),
}
SINA_A_ETF_URL = "https://hq.sinajs.cn/list=" + ",".join(SINA_A_ETF)
# symbol → 展示名（写库用）
SINA_A_ETF_NAME = {v[0]: v[1] for v in SINA_A_ETF.values()}
# 欧洲三大指数：东财 push2 真实指数 —— **备选源**（CNBC 主源，见 fetch_cnbc_global_index）。
# 在 CNBC 单指数缺失时补全；本网络/部分 VPS 环境东财被墙时整体走 CNBC，不再依赖东财。
# secid 沿用 tracking_error 模块已验证的东财全球指数代码（100.GDAXI 等）。
EM_GLOBAL_INDEX = {
    "DAX": ("德国DAX指数", "100.GDAXI"),
    "CAC40": ("法国CAC40指数", "100.FCHI"),
    "FTSE": ("英国富时100指数", "100.FTSE"),
}
EM_GLOBAL_URL = ("https://push2.eastmoney.com/api/qt/stock/get"
                 "?secid={secid}&fields=f43,f57,f58,f60,f169,f170&invt=2&fltt=2")
# 新浪全球指数（znb_ 前缀）：欧洲三大指数 + 日经225 —— **主源**（2026-10-30 新增）。
# 为什么不用 CNBC 当主源：实测 CNBC 的 .DAX/.CAC 快照不完整（change/change_pct='UNCH'、
# previous_day_closing 被填成等于 last、open/high/low 全 0），只有这两个坏了；
# 而新浪 hq.sinajs.cn 是本项目既有的价格主源（gb_inx 标普500 就走它），VPS 必然可达。
# znb_ 代码 → 信号 symbol 映射（新浪用的代码与面板 symbol 不同名，别混）。
SINA_GLOBAL_INDEX = {
    "znb_DAX": "DAX",
    "znb_CAC": "CAC40",
    "znb_UKX": "FTSE",
    "znb_NKY": "NK",
}
SINA_GLOBAL_INDEX_NAME = {
    "DAX": "德国DAX指数",
    "CAC40": "法国CAC40指数",
    "FTSE": "英国富时100指数",
    "NK": "日经225指数",
}
SINA_GLOBAL_INDEX_URL = ("https://hq.sinajs.cn/list="
                         + ",".join(SINA_GLOBAL_INDEX.keys()))
# VIX：腾讯 usVIX 的数值自 2026-09-15 起就不再更新（实测长期停在 21.67），
# 拿它当避险开关等于开关永远不动。改用 CBOE 官方日收盘 CSV（权威、日频、T+1）。
CBOE_VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
# 美债10年期收益率（实时）：CNBC 报价服务，实测 last_time 就是当前时刻、curmktstatus=REG_MKT。
# 备选（日频 T+1、权威）：美国财政部 daily_treasury_yield_curve XML。
CNBC_US10Y_URL = ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
                  "?symbols=US10Y&requestMethod=itv&noform=1&partnerId=2&fund=1"
                  "&exthrs=1&output=json&events=1")


def fetch_sina_futures():
    """新浪环球期货（hf_*）实时行情。

    ⚠️ 字段位置（2026-10-29 实测 15 段，勿凭印象改）：
        [0] 现价  [2] 买价  [3] 卖价  [4] 最高  [5] 最低
        [6] 时间  [7] **昨收**  [8] 开盘  [12] 日期  [13] **名称**  [14] 占位 0
    历史 bug：曾把 parts[5] 当价格（实际是当日最低价）、把 parts[-1] 当名称
    （实际是占位 "0"），导致页面出现「名称 0、价格恒为最低价、涨跌 0.00%」。

    返回 {hf_code: (名称, 现价, 昨收, 日期时间)}
    """
    txt = fetch_text(C.URL_SINA_FUTURE,
                     referer="https://finance.sina.com.cn/",
                     encoding="gbk", use_proxy=None, retries=2)
    res = {}
    for m in re.finditer(r'var hq_str_(hf_\w+)="([^"]*)"', txt):
        sym, body = m.group(1), m.group(2)
        parts = body.split(",")
        if len(parts) < 14:
            continue

        def f(i):
            try:
                return float(parts[i])
            except (ValueError, TypeError, IndexError):
                return None

        price, prev = f(0), f(7)
        if not price:
            continue
        name = (parts[13] or "").strip()
        ts = "%s %s" % ((parts[12] or "").strip(), (parts[6] or "").strip())
        res[sym] = (name, price, prev, ts.strip())
    return res


def fetch_tencent_us(codes=None):
    """腾讯财经美股行情（~ 分隔）。codes 形如 'usVIX'，默认取 TENCENT_SYMBOLS 那批。"""
    url = TENCENT_URL if not codes else ("https://qt.gtimg.cn/q=" + codes)
    txt = fetch_text(url, use_proxy=None, retries=2)
    res = {}
    for m in re.finditer(r'v_(\w+)="([^"]*)"', txt):
        sym, body = m.group(1), m.group(2)
        parts = body.split("~")
        if len(parts) < 5:
            continue
        try:
            price = float(parts[3])
            prev = float(parts[4])
        except (ValueError, TypeError):
            continue
        chg = (price / prev - 1) * 100 if prev else 0.0
        res[sym] = (price, chg, prev)
    return res


def fetch_sina_us_index():
    """新浪美股指数（标普500 gb_inx / 纳指综合 gb_ixic），上一交易日收盘。

    字段（2026-10-30 实测 `var hq_str_gb_inx`）：
        [0]名称 [1]最新价 [2]**涨跌幅%** [3]时间 [4]涨跌额
        [5]最高 [6]最低 ... [26]昨收
    ⚠️ 涨跌幅必须用 sina **直接给出**的 [2]，不要拿 [1]/[5] 重算——
    [5] 是「最高价」不是昨收，用错会得到反向涨跌：
    实测标普500 最新价 7720.39、[2]=-0.30（正确），错用 [5]=7699.61 算成 +0.27%。
    [2] 是 sina 官方算好的每日涨跌幅（含分红/拆细校正），最权威。
    [4] = 涨跌额（绝对变动，单位点），直接作为 change_value（涨跌值）。
    """
    txt = fetch_text(SINA_INDEX_URL, referer="https://finance.sina.com.cn/",
                     encoding="gbk", use_proxy=None, retries=2)
    res = {}
    for m in re.finditer(r'var hq_str_(gb_\w+)="([^"]*)"', txt):
        sym, body = m.group(1), m.group(2)
        parts = body.split(",")
        if len(parts) < 3:
            continue
        try:
            price = float(parts[1])
        except (ValueError, TypeError):
            continue
        raw = (parts[2] or "").strip()
        try:
            chg = float(raw) if raw not in ("", "--") else None
        except ValueError:
            chg = None
        # [4] = 涨跌额（绝对变动，单位点）
        change_value = None
        if len(parts) > 4:
            r4 = (parts[4] or "").strip()
            try:
                change_value = float(r4) if r4 not in ("", "--") else None
            except (ValueError, TypeError):
                change_value = None
        res[sym] = (price, chg, change_value)
    return res


def fetch_cboe_vix():
    """CBOE 官方 VIX 日收盘 CSV → (收盘价, 涨跌幅%, 日期ISO)。

    取最后一条完整记录；CSV 首行是表头 `DATE,OPEN,HIGH,LOW,CLOSE`，日期格式 MM/DD/YYYY。
    """
    txt = fetch_text(CBOE_VIX_URL, timeout=40, retries=2)
    lines = [ln.strip() for ln in txt.strip().split("\n") if ln.strip()]
    rows = [ln.split(",") for ln in lines[1:] if len(ln.split(",")) >= 5]
    if len(rows) < 2:
        raise ValueError("CBOE VIX csv 行数不足")
    last, prev = rows[-1], rows[-2]
    close = float(last[4])
    prev_close = float(prev[4])
    chg = (close / prev_close - 1) * 100 if prev_close else 0.0
    m, d, y = last[0].split("/")
    return close, round(chg, 4), "%s-%s-%s" % (y, m, d), round(close - prev_close, 2)


def fetch_cnbc_ust10y():
    """CNBC 美债 10 年期收益率（**实时**），返回 (收益率%, 变动bp, 相对变动%, 美东时间)。

    字段（2026-10-29 实测）：
        last="4.995%"  previous_day_closing="5.045%"  change="-0.05"  change_pct="-0.9911%"
        last_time="2026-10-29T03:51:40.000-0400"（美东）  curmktstatus="REG_MKT"
    ⚠️ 收益率要用 **bp** 表达涨跌：change 本身就是「收益率百分点」差，×100 即 bp
       （-0.05 → -5bp，正好 5.045→4.995）。change_pct 是相对变化率，对收益率没有
       经济含义（4%→4.04% 只算 +1%，实际是 +4bp），仅作参考保留。
    """
    txt = fetch_text(CNBC_US10Y_URL, timeout=30, retries=2)
    obj = json.loads(txt)
    quotes = ((obj.get("FormattedQuoteResult") or {}).get("FormattedQuote") or [])
    if not quotes:
        raise ValueError("CNBC US10Y 无报价")
    q = quotes[0]
    last = float(str(q["last"]).rstrip("%").replace(",", ""))
    prev = float(str(q.get("previous_day_closing") or "0").rstrip("%").replace(",", ""))
    bp = round((last - prev) * 100, 1) if prev else None
    chg_pct = round((last / prev - 1) * 100, 4) if prev else None
    as_of = (q.get("last_time") or "").replace("T", " ")[:19]
    return last, bp, chg_pct, ("美东 " + as_of if as_of else "")


def fetch_sina_global_index():
    """新浪全球指数（znb_ 前缀）：欧洲三大指数 + 日经225 —— **主源**。

    2026-10-30 实测新增。原因：CNBC 对 .DAX / .CAC 这两个 symbol 的行情快照
    不完整 —— change/change_pct 返回 'UNCH'、previous_day_closing 被错误填成
    等于 last（26,039.68 == 26,039.68），open/high/low 全为 0.00，
    而 FTSE / N225 正常 ⇒ 只有这两个坏了。CNBC 之外又实测过：
    Yahoo（403）、stooq（JS 挑战页）、东财 push2（部分出口整段断链）均不可用。
    新浪 hq.sinajs.cn 是本项目**既有的价格主源**（gb_inx 标普500 就走它），
    VPS 上必然可达，且 znb_ 这四个指数的涨跌值完整 —— 三方交叉验证一致：
        DAX   : sina 335.66 / 1.31%  ==  东财 f169/f170 335.66 / 1.31
        CAC40 : sina  83.04 / 1.05%  ==  东财 f169/f170  83.04 / 1.05
        FTSE  : sina  27.57 / 0.26%  ==  CNBC +27.57 / +0.26%

    字段（2026-10-30 实测 `var hq_str_znb_DAX`，共 13 段）：
        [0]名称 [1]现价 [2]**涨跌额** [3]**涨跌幅%** [4]起始日期 [5]时间戳
        [6]日期 [7]时间 [8]开盘 [9]昨收 [10]最高 [11]最低 [12]成交量
    ⚠️ 涨跌值/涨跌幅必须直接用 [2]/[3]（新浪官方算好的，含分红/拆细校正），
    不要拿 [1]/[9] 重算 —— 与 gb_inx 同理（错用会得到反向涨跌）。

    返回 {signal_symbol: (name, price, chg_pct, chg_value)}。
    """
    txt = fetch_text(SINA_GLOBAL_INDEX_URL, referer="https://finance.sina.com.cn/",
                     encoding="gbk", use_proxy=None, retries=2)
    res = {}
    for m in re.finditer(r'var hq_str_(znb_\w+)="([^"]*)"', txt):
        sym, body = m.group(1), m.group(2)
        parts = body.split(",")
        if len(parts) < 10:
            continue
        try:
            price = float(parts[1])
        except (ValueError, TypeError):
            continue

        def _num(idx):
            v = (parts[idx] or "").strip()
            if v in ("", "--", "-"):
                return None
            try:
                return float(v)
            except ValueError:
                return None

        chg_value = _num(2)      # 涨跌额（点）
        chg_pct = _num(3)        # 涨跌幅（%）
        # 缺值时用「现价 − 昨收([9])」自算兜底
        prev = _num(9)
        if chg_value is None and prev:
            chg_value = round(price - prev, 4)
        if chg_pct is None and prev:
            chg_pct = round((price / prev - 1) * 100, 4)
        target = SINA_GLOBAL_INDEX.get(sym)
        if not target:
            continue
        res[target] = (SINA_GLOBAL_INDEX_NAME[target], price, chg_pct, chg_value)
    if not res:
        raise ValueError("新浪全球指数全部失败")
    return res


def fetch_cnbc_global_index():
    """CNBC 真实欧洲/日本指数（.DAX/.CAC/.FTSE/.N225）—— 备选源。

    用户部署环境 CNBC 稳定可达（美债10Y 已用同出口 quote.cnbc.com）。
    返回 {signal_symbol: (name, price, chg_pct, chg_value, prev_close)}。
    全失败抛 ValueError，交 fetch_us_signal 降级到东财/ETF。

    CNBC 的 change 是带符号字符串（"+335.66"）、change_pct 带百分号（"+1.31%"），
    需清洗后转 float；last / previous_day_closing 含千分位逗号也要去。

    ⚠️ 2026-10-30 修正：'UNCH' **不能当 0**。
    实测 .DAX / .CAC 返回 change='UNCH'、change_pct='UNCH'，且
    previous_day_closing == last（26,039.68 == 26,039.68）——说明这两个 symbol
    的行情快照本身没带涨跌字段，是**数据缺失**而非「涨跌为零」。
    若当 0 处理，面板会显示假的「0.00 / 0.00%」，而 DAX 当日实际是 +335.66（+1.31%）。
    现在 UNCH 一律解析为 None（无数据），由上层降级到东财 push2 取真实涨跌。
    """
    url = ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
           "?symbols=.DAX%7C.CAC%7C.FTSE%7C.N225&requestMethod=itv&noform=1"
           "&partnerId=2&fund=1&exthrs=1&output=json")
    txt = fetch_text(url, use_proxy=None, retries=2, timeout=20)
    obj = json.loads(txt)
    quotes = (obj.get("FormattedQuoteResult") or {}).get("FormattedQuote") or []
    sym_map = {".DAX": "DAX", ".CAC": "CAC40", ".FTSE": "FTSE", ".N225": "NK"}
    name_map = {"DAX": "德国DAX指数", "CAC40": "法国CAC40指数",
                "FTSE": "英国富时100指数", "NK": "日经225指数"}

    def _num(s):
        if s is None:
            return None
        s = str(s).replace(",", "").replace("%", "").strip()
        if not s:
            return None
        # 'UNCH' = 该字段无数据（不是「涨跌为零」）；'N/A'/'--' 同理。
        # 返回 None 让上层降级到东财取真实涨跌，勿当 0（否则显示假的平盘）。
        if s.upper() in ("UNCH", "UNCHANGED", "N/A", "NA", "--"):
            return None
        try:
            return float(s)
        except ValueError:
            return None

    res = {}
    for q in quotes:
        sym = sym_map.get(q.get("symbol"))
        if not sym:
            continue
        price = _num(q.get("last"))
        if price is None:
            continue
        chg = _num(q.get("change"))          # 涨跌值（点数差）
        pct = _num(q.get("change_pct"))      # 涨跌幅%
        prev = _num(q.get("previous_day_closing"))
        res[sym] = (name_map[sym], price, pct, chg, prev)
    if not res:
        raise ValueError("CNBC 全球指数全部失败")
    return res


def fetch_em_global_index():
    """东财 push2 真实欧洲三大指数（备选源；CNBC 主源，见 fetch_cnbc_global_index）。

    与 constants.MARKET_SIGNALS 的 DAX/CAC40/FTSE 一一对应，symbol 即 DAX/CAC40/FTSE。

    ⚠️ 字段语义（2026-10-30 实测校正，此前写反了）：
        f43 = 最新价      f60 = 昨收
        **f169 = 涨跌额（点数差）**  例：DAX 335.66
        **f170 = 涨跌幅（%）**      例：DAX 1.31
    原实现把 f169 当涨跌幅、f170 当涨跌额，导致面板上「涨跌幅」显示成点数、
    「涨跌值」显示成百分数。实测对照：f43 - f60 == f169（DAX 26039.68-25704.02=335.66）。
    当 f169/f170 缺失时，用 f43 - f60 自算涨跌额，再用涨跌额/昨收自算涨跌幅。

    逐个 secid 独立 try/except：某指数失败只丢该条，不拖垮其余；全失败抛 ValueError 交上层降级。
    """
    res = {}
    for symbol, (_name, secid) in EM_GLOBAL_INDEX.items():
        try:
            url = EM_GLOBAL_URL.format(secid=secid)
            txt = fetch_text(url, use_proxy=None, retries=1, timeout=15)
            obj = json.loads(txt)
            d = obj.get("data") or {}
            if not d or d.get("f43") in (None, "", "-"):
                continue
            price = float(d["f43"])

            def _f(key):
                v = d.get(key)
                if v in (None, "", "-", "--"):
                    return None
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None

            prev = _f("f60")
            # f169 = 涨跌额（点），f170 = 涨跌幅（%）
            change_value = _f("f169")
            change_pct = _f("f170")
            # 缺失时用昨收自算
            if change_value is None and prev:
                change_value = round(price - prev, 4)
            if change_pct is None and prev and change_value is not None:
                change_pct = round((price / prev - 1) * 100, 4)
            res[symbol] = (EM_GLOBAL_INDEX[symbol][0], price, change_pct, change_value)
        except Exception as e:  # noqa: BLE001
            print("[signal] 东财 %s 失败: %s" % (secid, e))
            continue
    if not res:
        raise ValueError("东财欧洲指数全部失败")
    return res


def fetch_sina_a_etf():
    """新浪 A 股场内 ETF 实时价 → {symbol: (名称, 现价, 昨收, 时间)}。

    用途：DAX / 法国CAC40 / 纳斯达克科技 这几类**取不到海外指数现货**
    （东财 push2 在本网络恒断、腾讯/新浪也没有对应指数代码），
    用境内对应 ETF 的实时价代理 —— 反映的是国内投资者实际能买到的价格。
    代理口径会在页面类型标签里写明（场内ETF · 实时），不冒充指数。
    """
    txt = fetch_text(SINA_A_ETF_URL, referer="https://finance.sina.com.cn",
                     encoding="gbk", use_proxy=None, retries=2)
    res = {}
    for m in re.finditer(r'var hq_str_(\w+)="([^"]*)"', txt):
        code, body = m.group(1), m.group(2)
        if code not in SINA_A_ETF or not body:
            continue
        parts = body.split(",")
        if len(parts) < 4:
            continue
        try:
            price = float(parts[3])
            prev = float(parts[2])
        except (ValueError, TypeError):
            continue
        ts = ""
        if len(parts) > 31:
            ts = "%s %s" % (parts[30], parts[31])
        res[SINA_A_ETF[code][0]] = (SINA_A_ETF[code][1], price, prev, ts.strip())
    return res


def fetch_us_signal(date: str = None, path: str = C.QDII_DB_PATH):
    date = date or datetime.date.today().isoformat()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    # 确保旧库补齐新增列（change_value / change_bp）：本函数是唯一不依赖
    # app/scheduler 启动时 init_db 的入口，缺列会直接 INSERT 失败。
    try:
        db.init_db(path).close()
    except Exception:
        pass
    rows = []
    # 期货（实时）
    try:
        futures = fetch_sina_futures()
        for hf_code, (symbol, name) in FUTURES_MAP.items():
            if symbol == "NK":
                continue  # 日经225 改由 CNBC 真实指数提供（见 cnbc_idx），不重复写
            got = futures.get(hf_code)
            if not got:
                continue
            _n, price, prev, ts = got
            # 涨跌幅按「现价 vs 昨收」真实计算（此前写死 0.00%，等于没有信息）
            chg = round((price / prev - 1) * 100, 4) if prev else None
            chg_val = round(price - prev, 2) if prev else None  # 涨跌值（点数差）
            rows.append((date, symbol, name, price, chg, 1, ts or now, now, None, chg_val))
    except Exception as e:  # noqa: BLE001
        print("[signal] futures failed:", e)
    # 腾讯（非实时：纳指100现货/VIX/美债）
    try:
        tencent = fetch_tencent_us()
        for tcode, (symbol, name, is_rt) in TENCENT_SYMBOLS.items():
            if tcode in tencent:
                price, chg, prev = tencent[tcode]
                chg_val = round(price - prev, 2) if prev else None
                rows.append((date, symbol, name, price, chg, is_rt, now, now, None, chg_val))
    except Exception as e:  # noqa: BLE001
        print("[signal] tencent failed:", e)
    # 美债10年期收益率（实时，CNBC）
    try:
        ust, ust_bp, ust_pct, ust_ts = fetch_cnbc_ust10y()
        rows.append((date, "UST10Y", "美债10Y收益率", ust, ust_pct, 1,
                     ust_ts or now, now, ust_bp, ust_bp))
    except Exception as e:  # noqa: BLE001
        print("[signal] cnbc ust10y failed:", e)
    # VIX（避险开关 + 面板展示）：CBOE 官方 CSV 优先，失败再退回腾讯（可能停在旧值）。
    # 美债10Y 是「原因」（贴现率上行压制估值），VIX 是「结果」（恐慌程度已到多少），
    # 两者维度不同、并列参与避险判断（见 strategy.risk_off_assess），缺一个都不完整。
    try:
        vix_close, vix_chg, vix_date, vix_val = fetch_cboe_vix()
        rows.append((date, "VIX", "VIX波动率", vix_close, vix_chg, 0, vix_date, now, None, vix_val))
    except Exception as e:  # noqa: BLE001
        print("[signal] cboe vix failed, fallback tencent:", e)
        try:
            t2 = fetch_tencent_us("usVIX")
            if "usVIX" in t2:
                price, chg = t2["usVIX"]
                rows.append((date, "VIX", "VIX波动率", price, chg, 0, now, now, None))
        except Exception as e2:  # noqa: BLE001
            print("[signal] vix unavailable:", e2)
    # 新浪（非实时：标普500 现货）
    try:
        sina_idx = fetch_sina_us_index()
        if "gb_inx" in sina_idx:
            price, chg, chg_val = sina_idx["gb_inx"]
            rows.append((date, "SPX", "标普500现货", price, chg, 0, now, now, None, chg_val))
    except Exception as e:  # noqa: BLE001
        print("[signal] sina index failed:", e)
    # 欧洲三大指数 + 日经225：**三源合并**（新浪 znb_ 主源 + 东财 push2 + CNBC）。
    #
    # 为什么这样设计（2026-10-30 逐源实测）：
    #   - 新浪 znb_（hq.sinajs.cn）：四个指数的现价/昨收/涨跌额/涨跌幅全有，
    #     且是本项目既有价格主源 → VPS 必可达，**主源**；
    #   - 东财 push2：f43/f60/f169/f170 齐全，但部分出口整段断链 → **备源**；
    #   - CNBC：FTSE/N225 正常，但 .DAX/.CAC 快照坏（change='UNCH'、昨收=现价）
    #     → **备源**（对 FTSE/N225 仍可作交叉校验）。
    # 合取规则：价格取任一可用值；涨跌取「真正给得出」的那个（新浪→东财→CNBC）。
    # 三源都没有的指数才落到 sina/tencent ETF 代理（fail-safe，不回归）。
    idx_all = {}
    for label, fn in (("sina", fetch_sina_global_index),
                      ("em", fetch_em_global_index),
                      ("cnbc", fetch_cnbc_global_index)):
        try:
            got = fn()
        except Exception as e:  # noqa: BLE001
            print("[signal] %s 全球指数失败: %s" % (label, e))
            continue
        for sym, val in got.items():
            # 新浪/东财: (name, price, pct, chg_value)；CNBC: (name, price, pct, chg_value, prev)
            name, price, pct, chg_val = val[0], val[1], val[2], val[3]
            cur = idx_all.get(sym)
            if cur is None:
                idx_all[sym] = [name, price, pct, chg_val, label]
            else:
                # 价格：优先新浪（主源，最权威的官方口径）
                if cur[4] != "sina" and label == "sina":
                    cur[1] = price
                # 涨跌：谁先给出真实值用谁（sina > em > cnbc 的优先级由遍历顺序保证）
                if cur[2] is None and pct is not None:
                    cur[2], cur[3] = pct, chg_val

    for symbol in ("DAX", "CAC40", "FTSE", "NK"):
        v = idx_all.get(symbol)
        if v:
            _n, price, chg, chg_val, _src = v
            rows.append((date, symbol, _n, price, chg, 0, now, now, None, chg_val))

    # 三源都没覆盖的 → 兜底：sina A股ETF(DAX/CAC40) + tencent usEWU(FTSE)；NK 无 ETF 代理则留空
    missing = [s for s in ("DAX", "CAC40", "FTSE", "NK") if s not in idx_all]
    if missing:
        if "DAX" in missing or "CAC40" in missing:
            try:
                a_etf = fetch_sina_a_etf()
                for symbol in ("DAX", "CAC40"):
                    if symbol in a_etf and symbol in missing:
                        _n, price, prev, ts = a_etf[symbol]
                        chg = round((price / prev - 1) * 100, 4) if prev else None
                        chg_val = round(price - prev, 2) if prev else None
                        rows.append((date, symbol, SINA_A_ETF_NAME[symbol] + "（代理）",
                                     price, chg, 1, ts or now, now, None, chg_val))
                        missing = [s for s in missing if s != symbol]
            except Exception as e2:  # noqa: BLE001
                print("[signal] sina a-etf fallback failed:", e2)
        if "FTSE" in missing:
            try:
                t = fetch_tencent_us("usEWU")
                if "usEWU" in t:
                    price, chg, prev = t["usEWU"]
                    chg_val = round(price - prev, 2) if prev else None
                    rows.append((date, "FTSE", "英国富时100指数（代理）",
                                 price, chg, 1, now, now, None, chg_val))
            except Exception as e3:  # noqa: BLE001
                print("[signal] tencent usEWU fallback failed:", e3)
    conn = db.get_conn(path)
    # 同日重跑先清当日旧行：symbol 口径变过（NDX → IXIC、usTNX → CNBC UST10Y），
    # 不清理会让面板同时出现新旧两条。
    conn.execute("DELETE FROM qdii_us_signal WHERE date=?", (date,))
    for r in rows:
        db.upsert_row(conn, "qdii_us_signal",
                      ["date", "symbol", "name", "last_price", "change_pct",
                       "is_realtime", "as_of", "fetched_at", "change_bp", "change_value"],
                      list(r), "date,symbol")
    conn.commit()
    conn.close()
    db.set_meta("last_signal_fetch", now, path)
    return len(rows)


def get_latest_us_signal(path: str = C.QDII_DB_PATH):
    conn = db.get_conn(path)
    drow = conn.execute(
        "SELECT date FROM qdii_us_signal ORDER BY date DESC LIMIT 1").fetchone()
    if not drow:
        conn.close()
        return []
    # 显式排序：按 constants.MARKET_SIGNALS 的顺序（美股 → 科技 → 亚太 → 欧洲 → 利率）。
    # 别依赖插入顺序——重跑/补数时顺序会变。
    order = ["IXIC", "NDX_FUT", "SPX", "SPX_FUT", "NK",
             "DAX", "CAC40", "FTSE", "UST10Y", "VIX"]
    cases = " ".join("WHEN '%s' THEN %d" % (s, i + 1) for i, s in enumerate(order))
    rows = conn.execute(
        "SELECT * FROM qdii_us_signal WHERE date=? ORDER BY CASE symbol %s ELSE 99 END"
        % cases, (drow["date"],)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    db.init_db()
    n = fetch_us_signal()
    print("fetched %d us signals" % n)
    for r in get_latest_us_signal():
        bp = r.get("change_bp")
        val = r.get("change_value")
        print("  %-8s %-14s price=%.3f chg=%.2f%%  value=%s%s rt=%s as_of=%s" % (
            r["symbol"], r["name"], r["last_price"], r["change_pct"],
            ("%+.2f" % val) if val is not None else "—",
            (" (%+.1fbp)" % bp) if bp is not None else "",
            r["is_realtime"], r["as_of"]))
