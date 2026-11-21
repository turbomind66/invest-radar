"""drawdown/constants.py — 回撤全景页标的清单、数据源、事件种子、运行配置。

设计原则：
- 所有标的带 kind(index/etf/overseas)、源类型、源代码，抓取与展示共用同一份。
- 全历史起点以实测为准（2026-11-20 探测），写入 since 字段，页面标注。
- 海外指数 100.NDX 在东财返回名「纳斯达克」实为纳斯达克综合指数：
  纳指100 用新浪 .NDX，纳指综合用新浪 .IXIC，两者严格分开。
- 事件种子为人工策展，历史大事件无法自动回补，新增请追加到 EVENTS。
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.dirname(os.path.abspath(__file__))

DD_DB_PATH = os.environ.get("NT_DD_DB_PATH") or os.path.join(ROOT, "dd.db")

# ---- 标的清单 ----
# id: 前端与API唯一键；kind: index(A股指数)/etf(场内)/overseas(海外指数)/fx(汇率)
# src: tencent(腾讯日K全历史) / csindex(中证官网) / tigzig(yfinance包装) / sina_us / sina_fx / em(东财,备源)
INDEXES = [
    dict(id="sh000001", kind="index", src="tencent", code="sh000001", name="上证指数", since="1990-12-19", alt_src="sina_a", alt_code="sh000001"),
    dict(id="sz399001", kind="index", src="tencent", code="sz399001", name="深证成指", since="1993-01-03", alt_src="sina_a", alt_code="sz399001"),
    dict(id="sh000300", kind="index", src="tencent", code="sh000300", name="沪深300", since="2005-04-08", alt_src="sina_a", alt_code="sh000300"),
    dict(id="sh000905", kind="index", src="tencent", code="sh000905", name="中证500", since="2007-01-15", alt_src="sina_a", alt_code="sh000905"),
    dict(id="sh000016", kind="index", src="tencent", code="sh000016", name="上证50", since="2004-01-02", alt_src="sina_a", alt_code="sh000016"),
    dict(id="sz399006", kind="index", src="tencent", code="sz399006", name="创业板指", since="2010-06-02", alt_src="sina_a", alt_code="sz399006"),
    dict(id="h30269", kind="index", src="csindex", code="H30269", name="红利低波", since="2006-01-04"),
    dict(id="h30269_100", kind="index", src="csindex", code="930955", name="红利低波100", since="2006-01-04"),
    dict(id="000922", kind="index", src="csindex", code="000922", name="中证红利", since="2004-12-31"),
]

# 场内 ETF：现有观察池（WATCHLIST）+ 主流 QDII 场内，全历史
ETFS = [
    dict(id="510300", kind="etf", src="tencent", code="sh510300", name="沪深300ETF", since="2012-05-28", track="sh000300", alt_src="sina_a", alt_code="sh510300"),
    dict(id="510310", kind="etf", src="tencent", code="sh510310", name="易方达沪深300ETF", since="2013-03-25", track="sh000300", alt_src="sina_a", alt_code="sh510310"),
    dict(id="159919", kind="etf", src="tencent", code="sz159919", name="嘉实沪深300ETF", since="2012-05-28", track="sh000300", alt_src="sina_a", alt_code="sz159919"),
    dict(id="510500", kind="etf", src="tencent", code="sh510500", name="中证500ETF", since="2013-03-15", track="sh000905", alt_src="sina_a", alt_code="sh510500"),
    dict(id="512100", kind="etf", src="tencent", code="sh512100", name="中证1000ETF", since="2016-11-04", track=None, alt_src="sina_a", alt_code="sh512100"),
    dict(id="510050", kind="etf", src="tencent", code="sh510050", name="上证50ETF", since="2005-02-23", track="sh000016", alt_src="sina_a", alt_code="sh510050"),
    dict(id="159915", kind="etf", src="tencent", code="sz159915", name="创业板ETF", since="2011-12-09", track="sz399006", alt_src="sina_a", alt_code="sz159915"),
    dict(id="588000", kind="etf", src="tencent", code="sh588000", name="科创50ETF", since="2020-11-16", track=None, alt_src="sina_a", alt_code="sh588000"),
    dict(id="563360", kind="etf", src="tencent", code="sh563360", name="A500ETF", since="2024-10-15", track=None, alt_src="sina_a", alt_code="sh563360"),
    dict(id="513100", kind="etf", src="tencent", code="sh513100", name="纳指ETF国泰", since="2013-05-15", track="ndx100", alt_src="sina_a", alt_code="sh513100"),
    dict(id="159941", kind="etf", src="tencent", code="sz159941", name="纳指ETF广发", since="2015-07-13", track="ndx100", alt_src="sina_a", alt_code="sz159941"),
    dict(id="513500", kind="etf", src="tencent", code="sh513500", name="标普500ETF", since="2014-01-15", track="spx", alt_src="sina_a", alt_code="sh513500"),
    dict(id="513880", kind="etf", src="tencent", code="sh513880", name="日经225ETF", since="2019-06-25", track="n225", alt_src="sina_a", alt_code="sh513880"),
    dict(id="513030", kind="etf", src="tencent", code="sh513030", name="德国ETF", since="2014-09-05", track="dax", alt_src="sina_a", alt_code="sh513030"),
    dict(id="513080", kind="etf", src="tencent", code="sh513080", name="法国ETF", since="2020-06-12", track="cac40", alt_src="sina_a", alt_code="sh513080"),
]

# 海外指数：优先东财全历史（1990起）；纳指两条线用新浪严格区分
OVERSEAS = [
    # 海外指数：优先 TigZig(yfinance 包装，1990起全历史、无 key)；东财作备源
    dict(id="ndx100", kind="overseas", src="tigzig", code="^NDX", name="纳斯达克100", since="1990-01-02", fx="usd"),
    dict(id="ixic", kind="overseas", src="tigzig", code="^IXIC", name="纳斯达克综合", since="1990-01-02", fx="usd"),
    dict(id="spx", kind="overseas", src="tigzig", code="^GSPC", name="标普500", since="1990-01-02", fx="usd",
         alt_src="sina_us", alt_code=".INX", alt_since="2004-01-02"),
    dict(id="dji", kind="overseas", src="tigzig", code="^DJI", name="道琼斯", since="1992-01-02", fx="usd",
         alt_src="sina_us", alt_code=".DJI", alt_since="2004-01-02"),
    dict(id="n225", kind="overseas", src="tigzig", code="^N225", name="日经225", since="1990-01-04", fx="jpy"),
    dict(id="dax", kind="overseas", src="tigzig", code="^GDAXI", name="德国DAX", since="1990-01-02", fx="eur"),
    dict(id="cac40", kind="overseas", src="tigzig", code="^FCHI", name="法国CAC40", since="1990-03-01", fx="eur"),
    dict(id="ftse", kind="overseas", src="tigzig", code="^FTSE", name="英国富时100", since="1990-01-02", fx="gbp"),
    dict(id="hsi", kind="overseas", src="tigzig", code="^HSI", name="恒生指数", since="1990-01-02", fx="hkd"),
    dict(id="hscei", kind="overseas", src="tigzig", code="^HSCE", name="恒生国企", since="1993-07-15", fx="hkd"),
]

# 汇率：新浪外汇（管道分隔字符串），用于「剔除汇率」还原
FX = [
    dict(id="usd", kind="fx", src="sina_fx", code="fx_susdcny", name="美元人民币", since="1994-08-30"),
    dict(id="jpy", kind="fx", src="sina_fx", code="fx_sjpycny", name="日元人民币", since="2008-09-04"),
    dict(id="eur", kind="fx", src="sina_fx", code="fx_seurcny", name="欧元人民币", since="2008-09-04"),
    dict(id="gbp", kind="fx", src="sina_fx", code="fx_sgbpcny", name="英镑人民币", since="2014-11-07"),
    dict(id="hkd", kind="fx", src="sina_fx", code="fx_shkdcny", name="港元人民币", since="2014-11-07"),
]

ALL = INDEXES + ETFS + OVERSEAS + FX
BY_ID = {a["id"]: a for a in ALL}


def reconstruct_targets():
    """可做「剔除溢价+汇率还原」的场内 ETF 映射（动态推导，避免硬编码重复）。

    仅跟踪海外指数的 QDII 场内 ETF 有意义：A股 ETF 无汇率、无 QDII 净值披露。
    返回 [{etf, nav_code, bench, fx, name, bench_name}, ...]。
    """
    out = []
    for e in ETFS:
        track = e.get("track")
        if not track or track not in BY_ID:
            continue
        b = BY_ID[track]
        if b.get("kind") != "overseas" or not b.get("fx"):
            continue
        out.append({
            "etf": e["id"], "nav_code": e["id"], "bench": track,
            "fx": b["fx"], "name": e["name"], "bench_name": b["name"],
        })
    return out


RECONSTRUCT = reconstruct_targets()

# 前端默认展示标的（最新选中态）
DEFAULT_IDS = ["sh000001", "sh000300", "sz399006", "510300"]

# ---- 事件种子（人工策展，date 对应标的的历史关键时刻）----
# side: crash(回撤/下跌) 或 rally(上涨/修复)；影响哪条曲线由 target id 指定
EVENTS = [
    dict(date="2007-10-16", target="sh000001", side="crash", title="上证6124点历史高点",
         desc="股权分置改革后牛市见顶，随后进入长达一年的单边下跌。"),
    dict(date="2008-10-28", target="sh000001", side="rally", title="四万亿刺激出台",
         desc="全球金融危机叠加国内宽松政策，A股见底1664点后反弹。"),
    dict(date="2015-06-12", target="sh000001", side="crash", title="杠杆牛见顶5178点",
         desc="场外配资清理引发去杠杆，随后数周出现连续千股跌停。"),
    dict(date="2016-01-27", target="sh000001", side="rally", title="熔断机制废止后企稳",
         desc="2016年初熔断四次触发后暂停，市场在2638点附近见底。"),
    dict(date="2018-01-26", target="sh000300", side="crash", title="沪深300触顶后贸易摩擦",
         desc="中美贸易摩擦升级叠加去杠杆，全年震荡下行。"),
    dict(date="2019-01-04", target="sh000300", side="rally", title="全面降准",
         desc="央行宣布全面降准1个百分点，A股开启春季行情。"),
    dict(date="2020-03-23", target="spx", side="crash", title="美股疫情熔断",
         desc="新冠疫情全球扩散，美股10天内三次熔断，标普500跌超30%。"),
    dict(date="2020-11-09", target="spx", side="rally", title="疫苗利好",
         desc="辉瑞疫苗有效性超90%公布，市场预期经济重启。"),
    dict(date="2021-02-18", target="ndx100", side="crash", title="美债收益率快速上行",
         desc="通胀预期升温，10年期美债收益率飙升，成长股估值承压。"),
    dict(date="2022-10-31", target="hsi", side="crash", title="港股年内低点",
         desc="美联储激进加息叠加地缘因素，恒指跌至14597点。"),
    dict(date="2024-09-24", target="sh000300", side="rally", title="924行情启动",
         desc="央行、证监会、金融监管总局联合发布一揽子政策，A股放量反弹。"),
]

# ---- 口径参数 ----
GRANULARITIES = ["day", "week", "month", "quarter", "year"]
GRANULARITY_LABELS = {"day": "日", "week": "周", "month": "月",
                      "quarter": "季", "year": "年"}

# 后端单次返回曲线点上限（前端 lttb 降采样用，避免 1 核 2G 传输压力）
MAX_POINTS = 6000
