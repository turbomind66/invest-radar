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

# ---- 事件种子（人工策展，date 对应标的的历史关键峰/谷）----
# side: crash(见顶/下跌) 用**峰值日**，rally(见底/启动) 用**谷值日**，便于钉在拐点上。
# 一条宏观事件挂到整组标的（targets），前端对「同日同名」事件只画一个图钉，避免多条线重复堆叠。
_A_INDEX = ["sh000001", "sz399001", "sh000300", "sh000905", "sh000016", "sz399006",
            "h30269", "h30269_100", "000922"]
_A_ETF = ["510300", "510310", "159919", "510500", "512100", "510050", "159915",
          "588000", "563360"]
_A_SHARE = _A_INDEX + _A_ETF
_HK = ["hsi", "hscei"]
_US = ["spx", "ndx100", "ixic", "dji"]
_US_GROWTH = ["ndx100", "ixic"]

# (date, side, title, desc, targets)
_EVENT_SEED = [
    # ===== A 股：政策/流动性驱动的大起大落 =====
    ("1994-07-29", "rally", "三大政策救市（325点）",
     "暂停新股发行、控制配股、扩大入市资金三大利好出台，指数两个月内翻倍。", _A_SHARE),
    ("1996-12-16", "crash", "涨跌停板制度实施",
     "监管连发「十二道金牌」降温，12月16日起实施10%涨跌停，指数连续跌停。", _A_SHARE),
    ("1999-05-19", "rally", "519行情启动",
     "证监会出台搞活市场的六项政策，网络科技股领涨，两年内上证翻倍。", _A_SHARE),
    ("2001-06-14", "crash", "2245点见顶，国有股减持",
     "国有股减持办法出台叠加清查违规资金，开启长达四年的熊市。", _A_SHARE),
    ("2005-06-06", "rally", "998点见底，股改启动",
     "上证跌破千点，股权分置改革试点推出，催生两年大牛市。", _A_SHARE),
    ("2007-10-16", "crash", "6124点历史高点",
     "股权分置改革牛市见顶，随后一年单边下跌至1664点。", _A_SHARE),
    ("2008-10-28", "rally", "1664点，四万亿刺激",
     "全球金融危机叠加国内宽松政策，A股见底后反弹。", _A_SHARE),
    ("2009-08-04", "crash", "3478点反弹见顶",
     "四万亿行情透支，货币政策转向，随后三年阴跌。", _A_SHARE),
    ("2014-07-22", "rally", "沪港通与改革牛启动",
     "沪港通试点获批、国企改革预期升温，杠杆资金推动指数一年翻倍。", _A_SHARE),
    ("2015-06-12", "crash", "5178点杠杆牛见顶",
     "场外配资清理引发去杠杆，随后数周出现连续千股跌停。", _A_SHARE),
    ("2016-01-27", "rally", "2638点，熔断废止",
     "2016年初熔断四次触发后暂停，市场在2638点附近见底。", _A_SHARE),
    ("2018-01-26", "crash", "3587点，贸易摩擦与去杠杆",
     "中美贸易摩擦升级叠加资管新规去杠杆，全年单边下行。", _A_SHARE),
    ("2019-01-04", "rally", "2440点，全面降准",
     "央行宣布全面降准1个百分点，A股开启春季行情。", _A_SHARE),
    ("2020-01-13", "crash", "疫情前高点3127点",
     "春节前最后一个高点，随后疫情冲击下推迟开市、单日大跌。", _A_SHARE),
    ("2020-03-19", "rally", "全球流动性危机见底2646点",
     "美联储无限量QE，北向资金回流，A股率先修复。", _A_SHARE),
    ("2021-02-18", "crash", "核心资产抱团瓦解（3731点）",
     "美债收益率快速上行刺破高估值，茅指数与成长股大幅回撤。", _A_SHARE),
    ("2021-12-13", "crash", "3708点，美联储转鹰",
     "地产信用风险与美联储收紧预期压制估值，全年震荡下行。", _A_SHARE),
    ("2022-04-26", "rally", "2863点，复工预期",
     "疫情封控与美联储加息共振后见底，随后两个月反弹近20%。", _A_SHARE),
    ("2022-10-31", "rally", "2885点，防疫优化预期",
     "市场预期防控政策调整，外资回流，A股与港股同步反弹。", _A_SHARE),
    ("2024-02-05", "rally", "2635点，流动性危机缓解",
     "小微盘流动性危机与雪球敲入冲击后，汇金增持ETF稳定预期。", _A_SHARE),
    ("2024-09-24", "rally", "924行情启动",
     "央行、证监会、金融监管总局联合发布一揽子政策，A股放量急涨。", _A_SHARE),
    ("2025-04-07", "rally", "关税冲击后见底（3040点）",
     "对等关税落地引发单日重挫，汇金增持ETF、政策托底，此后一路修复。", _A_SHARE),
    # 成长板块专属
    ("2015-06-05", "crash", "创业板4037点见顶",
     "杠杆牛中创业板率先见顶，随后三年最大回撤超七成。", ["sz399006", "159915"]),
    ("2018-10-19", "rally", "1184点，纾困基金入场",
     "股权质押风险暴露，地方政府纾困基金与民企座谈会稳定预期。", ["sz399006", "159915"]),
    ("2021-07-22", "crash", "创业板3576点见顶",
     "新能源与医药抱团见顶，随后三年回调超五成。", ["sz399006", "159915"]),
    ("2020-07-13", "crash", "科创50高位回落",
     "科创板开板一周年后估值高位，随后进入两年调整。", ["588000"]),

    # ===== 港股 =====
    ("1997-08-07", "crash", "恒指16820点，亚洲金融危机前高点",
     "泰铢脱钩引发区域货币危机，恒指四个月内腰斩。", _HK),
    ("1998-08-14", "rally", "政府入市击退炒家（6544点）",
     "港府动用外汇基金买入蓝筹，配合联系汇率保卫战稳定市场。", _HK),
    ("2000-03-28", "crash", "科网泡沫18397点见顶",
     "全球科网股见顶，恒指随后三年跌至8331点。", _HK),
    ("2003-04-25", "rally", "SARS见底8331点",
     "非典疫情受控，CEPA与自由行政策预期升温。", _HK),
    ("2007-10-30", "crash", "港股直通车31958点",
     "直通车预期推动恒指见顶，随后一年最大回撤逾六成。", _HK),
    ("2008-10-27", "rally", "10676点见底",
     "雷曼冲击后全球救市，港股在10676点止跌。", _HK),
    ("2015-04-28", "crash", "港股大时代28588点",
     "公募基金可投港股预期点燃行情，随后A股去杠杆波及。", _HK),
    ("2016-02-12", "rally", "18278点见底",
     "人民币贬值预期缓和，南向资金开始持续流入。", _HK),
    ("2018-01-29", "crash", "33484点历史高点",
     "南向资金与全球风险偏好共振见顶，随后贸易摩擦与去杠杆双杀。", _HK),
    ("2020-03-23", "rally", "21139点，全球流动性危机",
     "美元流动性紧张缓解，港股跟随全球风险资产修复。", _HK),
    ("2021-02-17", "crash", "31183点，平台监管与南向退潮",
     "反垄断与教培监管落地，恒生科技此后最大回撤超七成。", _HK),
    ("2022-10-31", "rally", "14597点年内低点",
     "美联储激进加息与地缘因素见底，随后两个月反弹近五成。", _HK),
    ("2023-01-27", "rally", "通关重启行情",
     "内地与香港恢复全面通关，恒指一度冲上22700点。", _HK),
    ("2024-01-22", "rally", "14794点低点",
     "雪球与流动性冲击见底，此后政策组合拳推动港股大幅反弹。", _HK),

    # ===== 美股 =====
    ("1990-07-16", "crash", "海湾战争前高点",
     "油价飙升与储贷危机冲击，标普500三个月回落近两成。", _US),
    ("1990-10-11", "rally", "295点见底",
     "海湾战争预期明朗，美股开启十年长牛。", _US),
    ("1998-07-17", "crash", "亚洲金融危机蔓延",
     "俄罗斯违约与LTCM危机前的高点，标普500两个月回落近两成。", _US),
    ("1998-08-31", "rally", "LTCM救助后见底957点",
     "美联储三次降息并组织救助长期资本，市场快速修复。", _US),
    ("2000-03-24", "crash", "科网泡沫见顶",
     "纳指与标普同月见顶，纳指此后两年半最大回撤近八成。", _US),
    ("2002-10-09", "rally", "科网泡沫底部",
     "世通造假与安然事件出清，标普500在776点见底。", _US),
    ("2007-10-09", "crash", "次贷危机前高点",
     "标普500见顶1565点，随后爆发全球金融危机。", _US),
    ("2009-03-09", "rally", "金融危机底部（标普666点）",
     "量化宽松与银行压力测试稳定预期，美股开启十一年长牛。", _US),
    ("2011-04-29", "crash", "美债降级与欧债危机前高点",
     "标普下调美国评级、欧债危机发酵，指数半年回落近两成。", _US),
    ("2011-10-03", "rally", "欧债危机底部1099点",
     "欧央行无限量购债预期，风险资产同步修复。", _US),
    ("2015-08-25", "rally", "人民币汇改冲击后闪崩见底",
     "人民币一次性贬值引发全球risk-off，标普盘中一度熔断式下挫。", _US),
    ("2018-09-20", "crash", "加息与贸易摩擦前高点",
     "美联储坚持加息叠加中美贸易摩擦，四季度快速回撤近两成。", _US),
    ("2018-12-24", "rally", "平安夜低点2351点",
     "美联储转鸽，美股在圣诞节前夜见底。", _US),
    ("2020-02-19", "crash", "疫情前高点",
     "新冠疫情全球扩散，美股10天内三次熔断。", _US),
    ("2020-03-23", "rally", "无限QE，美股见底",
     "美联储宣布无上限量化宽松，风险资产V型反转。", _US),
    ("2022-01-03", "crash", "美联储加息周期启动",
     "通胀升温迫使美联储转鹰，成长股估值大幅压缩。", _US),
    ("2022-10-12", "rally", "通胀见顶，标普见底3577点",
     "CPI回落预期升温，美股开启反弹（纳指底部在12月）。", ["spx", "dji"]),
    ("2022-12-28", "rally", "纳指10679点见底",
     "加息预期见顶，AI叙事开始接棒。", _US_GROWTH),
    ("2023-01-06", "rally", "AI行情启动",
     "ChatGPT带动的算力与软件行情启动，纳指全年涨超四成。", _US_GROWTH),
    ("2024-08-05", "crash", "日元套息交易平仓",
     "日央行加息叠加美国就业走弱，全球风险资产单日急跌。", _US),
    ("2025-04-09", "rally", "对等关税暂缓90天",
     "关税暂停推动美股V型反弹，纳指收复大半跌幅。", _US),

    # ===== 日经225 =====
    ("1990-10-01", "crash", "泡沫破裂，转入长期熊市",
     "日经从38957点泡沫高点回落，此后二十年未能收复。", ["n225"]),
    ("1992-08-18", "rally", "14309点，泡沫后首轮探底",
     "股价维稳政策出台，指数出现阶段性反弹。", ["n225"]),
    ("2003-04-28", "rally", "7607点，坏账危机出清",
     "银行不良债权处理推进，小泉改革预期升温。", ["n225"]),
    ("2008-10-27", "crash", "雷曼冲击，跌至7162点",
     "全球金融危机叠加日元升值，出口股重挫。", ["n225"]),
    ("2009-03-10", "rally", "7054点见底",
     "全球同步宽松，日经开启修复行情。", ["n225"]),
    ("2012-11-14", "rally", "安倍经济学启动",
     "解散众议院、无限量QQE预期推动日元贬值与股市重估。", ["n225"]),
    ("2015-06-24", "crash", "20952点阶段高点",
     "希腊危机与中国股灾引发避险，日元升值压制出口股。", ["n225"]),
    ("2018-10-02", "crash", "24448点阶段高点",
     "中美贸易摩擦与半导体周期见顶，四季度快速回撤。", ["n225"]),
    ("2020-03-19", "rally", "疫情低点16358点",
     "奥运延期与紧急事态落地后，日央行加码ETF购买。", ["n225"]),
    ("2024-03-22", "rally", "收复1989年泡沫高点",
     "公司治理改革与弱日元推动日经突破41000点，创历史新高。", ["n225"]),
    ("2024-08-05", "crash", "单日暴跌4451点",
     "日央行加息叠加美国衰退交易，日经单日跌12.4%，创历史最大点数跌幅。", ["n225"]),
    ("2025-04-07", "crash", "关税冲击单日重挫",
     "美国对等关税落地，日经单日跌逾7%，随后逐步修复。", ["n225"]),

    # ===== 德国DAX =====
    ("2000-03-07", "crash", "科网高点8065点",
     "新市场（Neuer Markt）泡沫见顶，DAX三年跌去七成。", ["dax"]),
    ("2003-03-12", "rally", "2203点见底",
     "伊拉克战争预期明朗与税改推进，德股见底。", ["dax"]),
    ("2007-07-16", "crash", "危机前高点8106点",
     "次贷危机蔓延，DAX一年半内最大回撤超五成。", ["dax"]),
    ("2009-03-06", "rally", "3666点见底",
     "全球银行救助与宽松政策见效。", ["dax"]),
    ("2011-09-12", "rally", "欧债危机底部5072点",
     "欧央行购债与希腊救助推进，风险偏好修复。", ["dax"]),
    ("2015-04-10", "crash", "12375点阶段高点",
     "希腊退出风险与中国需求走弱，DAX回撤逾两成。", ["dax"]),
    ("2018-12-27", "rally", "10381点，贸易摩擦与加息",
     "全球同步回调后见底，2019年重启上涨。", ["dax"]),
    ("2020-02-19", "crash", "疫情前高点13789点",
     "新冠疫情冲击，DAX一个月内跌近四成。", ["dax"]),
    ("2020-03-18", "rally", "8442点见底",
     "欧央行紧急购债，德股V型反转。", ["dax"]),
    ("2022-01-05", "crash", "16272点，俄乌与能源危机",
     "能源价格飙升与加息预期，DAX回撤逾两成。", ["dax"]),
    ("2022-09-29", "rally", "11975点见底",
     "天然气储备改善与通胀见顶预期。", ["dax"]),
    ("2025-03-04", "rally", "财政转向（国防+基建）",
     "德国放松债务刹车、大幅增加国防与基建支出，DAX单日涨3.4%。", ["dax"]),

    # ===== 法国CAC40 =====
    ("2000-09-04", "crash", "科网高点6922点",
     "电信与科技股泡沫见顶，CAC40三年跌去六成。", ["cac40"]),
    ("2003-03-12", "rally", "2403点见底",
     "战争预期明朗，法股见底回升。", ["cac40"]),
    ("2007-06-01", "crash", "6168点危机前高点",
     "次贷危机前高点，随后回撤近六成。", ["cac40"]),
    ("2009-03-09", "rally", "2519点见底",
     "全球银行救助与宽松政策见效。", ["cac40"]),
    ("2015-04-27", "crash", "5269点阶段高点",
     "希腊风险与中国需求走弱，法股回撤两成。", ["cac40"]),
    ("2020-02-19", "crash", "疫情前高点6111点",
     "疫情冲击下CAC40一个月跌近四成。", ["cac40"]),
    ("2020-03-18", "rally", "3755点见底",
     "欧央行紧急购债，法股V型反转。", ["cac40"]),
    ("2022-01-05", "crash", "7376点，加息与能源危机",
     "通胀与地缘风险压制估值，法股回撤两成。", ["cac40"]),
    ("2022-09-29", "rally", "5677点见底",
     "通胀见顶预期与能源价格回落。", ["cac40"]),

    # ===== 英国富时100 =====
    ("1999-12-30", "crash", "科网高点6930点",
     "电信与科技权重见顶，富时100三年跌近五成。", ["ftse"]),
    ("2003-03-12", "rally", "3287点见底",
     "战争预期明朗，英股见底。", ["ftse"]),
    ("2007-06-15", "crash", "6732点危机前高点",
     "次贷危机冲击金融权重股，指数回撤近五成。", ["ftse"]),
    ("2009-03-03", "rally", "3512点见底",
     "银行国有化落地与宽松政策见效。", ["ftse"]),
    ("2015-04-27", "crash", "7104点阶段高点",
     "大宗商品走弱与中国需求下滑，指数回撤两成。", ["ftse"]),
    ("2020-02-19", "crash", "疫情前高点7457点",
     "疫情冲击下富时100一个月跌逾三成。", ["ftse"]),
    ("2020-03-23", "rally", "4994点见底",
     "英国央行紧急降息与购债，指数V型反转。", ["ftse"]),
    ("2022-02-10", "crash", "7672点，通胀与加息",
     "能源价格飙升与加息周期，指数回撤一成半。", ["ftse"]),
    ("2022-10-12", "rally", "6826点，迷你预算风波后企稳",
     "减税方案撤回、英债市场稳定，指数修复。", ["ftse"]),
]

EVENTS = [dict(date=d, side=s, title=t, desc=desc, target=aid)
          for (d, s, t, desc, targets) in _EVENT_SEED
          for aid in targets if aid in BY_ID]
EVENTS.sort(key=lambda e: (e["date"], e["target"]))

# ---- 口径参数 ----
GRANULARITIES = ["day", "week", "month", "quarter", "year"]
GRANULARITY_LABELS = {"day": "日", "week": "周", "month": "月",
                      "quarter": "季", "year": "年"}

# 后端单次返回曲线点上限（前端 lttb 降采样用，避免 1 核 2G 传输压力）
MAX_POINTS = 6000
