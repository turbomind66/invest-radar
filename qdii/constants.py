"""qdii/constants.py — QDII 定投决策面板：基金池种子 + 策略参数 + 运行配置。

基金池代码均来自天天基金 fundcode_search.js 全量表的真实代码（2026-10-29 核实）：
- LOF（16 开头）：场外申购与场内交易同代码 → online_code = 自身。
- ETF 联接基金：对应跟踪的场内 ETF → online_code = 该 ETF 代码（用于溢价率比对）。
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.dirname(os.path.abspath(__file__))

# ---- 数据库路径（与 national_team 的 data.db 分离） ----
QDII_DB_PATH = os.environ.get("NT_QDII_DB_PATH") or os.path.join(BASE, "qdii.db")

# ---- 调度时间（北京时间；容器已设 TZ=Asia/Shanghai） ----
# 限额/状态/费率/跟踪误差：按 A 股交易日
QDII_SCHED_LIMITTIMES = os.environ.get("QDII_SCHED_LIMITTIMES", "09:00")
# 美股信号：按美股交易日（14:30 北京 = 美东约 01:30-02:30，现货/美债为上一交易日收盘）
QDII_SCHED_SIGNALTIME = os.environ.get("QDII_SCHED_SIGNALTIME", "14:30")
# 场内溢价：按 A 股交易日（14:30 A股交易时段内，场内价为实时）
QDII_SCHED_PREMIUMTIME = os.environ.get("QDII_SCHED_PREMIUMTIME", "14:30")

# ---- 初始基金池（手工维护，覆盖 4 类指数；代码已核实真实存在） ----
# 字段：code/name/index_name/category/company/fund_type/online_code/is_offline/note
FUND_POOL = [
    # ===== 标普500 =====
    dict(code="161125", name="易方达标普500指数人民币A", index_name="标普500",
         category="标普500", company="易方达", fund_type="LOF",
         online_code="161125", is_offline=1, note="LOF：场内场外同代码"),
    dict(code="050025", name="博时标普500ETF联接A", index_name="标普500",
         category="标普500", company="博时", fund_type="ETF联接",
         online_code="513500", is_offline=1, note="对应博时标普500ETF"),
    dict(code="007721", name="天弘标普500发起(QDII-FOF)A", index_name="标普500",
         category="标普500", company="天弘", fund_type="FOF联接",
         online_code="", is_offline=1, note="FOF（无跟踪标的），无场内对应ETF"),
    dict(code="096001", name="大成标普500等权重指数(QDII)A", index_name="标普500等权",
         category="标普500", company="大成", fund_type="指数",
         online_code="", is_offline=1, note="等权重指数，无对应场内ETF"),
    # ===== 纳斯达克100 =====
    dict(code="161130", name="易方达纳斯达克100ETF联接(QDII-LOF)A", index_name="纳斯达克100",
         category="纳斯达克100", company="易方达", fund_type="LOF",
         online_code="161130", is_offline=1, note="LOF：场内场外同代码"),
    dict(code="160213", name="国泰纳斯达克100指数", index_name="纳斯达克100",
         category="纳斯达克100", company="国泰", fund_type="LOF",
         online_code="160213", is_offline=1, note="LOF：场内场外同代码"),
    dict(code="270042", name="广发纳斯达克100ETF联接人民币(QDII)A", index_name="纳斯达克100",
         category="纳斯达克100", company="广发", fund_type="ETF联接",
         online_code="159941", is_offline=1, note="对应广发纳指ETF 159941"),
    dict(code="000834", name="大成纳斯达克100ETF联接(QDII)A", index_name="纳斯达克100",
         category="纳斯达克100", company="大成", fund_type="ETF联接",
         online_code="", is_offline=1, note="大成无自家场内纳指ETF，溢价看清单同类ETF"),
    dict(code="040046", name="华安纳斯达克100ETF联接(QDII)A", index_name="纳斯达克100",
         category="纳斯达克100", company="华安", fund_type="ETF联接",
         online_code="159632", is_offline=1, note="对应华安纳指ETF 159632"),
    # ===== 日经225 =====
    dict(code="020712", name="华安三菱日联日经225ETF发起式联接(QDII)A", index_name="日经225",
         category="日经225", company="华安", fund_type="ETF联接",
         online_code="513880", is_offline=1, note="对应华安日经225ETF"),
    # ===== 德国DAX =====
    dict(code="000614", name="华安德国(DAX)联接(QDII)A", index_name="德国DAX",
         category="德国DAX", company="华安", fund_type="ETF联接",
         online_code="513030", is_offline=1, note="对应华安德国30(DAX)ETF"),
]

# ---- 场内溢价监控清单（模块G） ----
# ⚠️ 溢价率必须按「场内 ETF 代码」计算，不能按场外基金遍历 online_code：
#    旧实现把 5 只纳指场外基金全指向 513100，导致表里 5 行溢价率一模一样，
#    而广发 159941（溢价常年在 10% 以上、最需要看的品种）从未出现过。
# 这里显式维护场内可交易的主流 QDII ETF，一只一行；场外基金与 ETF 的关系
# 用「同一指数」表达即可（同一指数可由多家 ETF 跟踪，不存在唯一映射）。
# 名称/代码已用新浪行情（2026-10-29）逐一核对。
PREMIUM_ETFS = [
    dict(code="513100", name="纳指ETF", company="国泰", index_name="纳斯达克100",
         category="纳斯达克100"),
    dict(code="159941", name="纳指ETF", company="广发", index_name="纳斯达克100",
         category="纳斯达克100"),
    dict(code="159632", name="纳斯达克", company="华安", index_name="纳斯达克100",
         category="纳斯达克100"),
    dict(code="513300", name="纳斯达克ETF", company="华夏", index_name="纳斯达克100",
         category="纳斯达克100"),
    dict(code="513110", name="纳指ETF", company="华泰柏瑞", index_name="纳斯达克100",
         category="纳斯达克100"),
    dict(code="513500", name="标普500ETF", company="博时", index_name="标普500",
         category="标普500"),
    dict(code="513650", name="标普500ETF", company="南方", index_name="标普500",
         category="标普500"),
    dict(code="159612", name="标普500ETF", company="国泰", index_name="标普500",
         category="标普500"),
    dict(code="513880", name="日经225ETF", company="华安", index_name="日经225",
         category="日经225"),
    dict(code="513030", name="德国ETF", company="华安", index_name="德国DAX",
         category="德国DAX"),
]

# ---- 定投决策：大类 → 参考信号 ----
# 按「大类」给建议，不落到具体基金（同一大类下各家基金同涨同跌，差别只在费率和限额）。
# 日经225 / 德国DAX 没有对应的美股信号源 → 用美股整体（纳指优先）作宏观风向参考。
# CATEGORY_OWN_SIGNAL 标记「哪些大类有自己的指数信号」，其余为借用，页面理由会标注。
CATEGORY_SIGNAL = {
    "标普500": ["SPX"],
    "纳斯达克100": ["IXIC", "NDX"],
    "日经225": ["IXIC", "NDX", "SPX"],
    "德国DAX": ["IXIC", "NDX", "SPX"],
}
CATEGORY_OWN_SIGNAL = {"标普500": "SPX", "纳斯达克100": "IXIC"}
# 估值百分位（大类级）：只有标普500 / 纳斯达克100 有维护值，其余按中性处理
CATEGORY_ORDER = ["纳斯达克100", "标普500", "日经225", "德国DAX"]

# ---- 策略参数（全部可配置，不硬编码到逻辑里） ----
STRATEGY_PARAMS = {
    "base_amount": 1000.0,        # 基准定投额（元）
    # 跟跌加仓档位：(现货昨收跌幅阈值, 增加的倍数)
    # 单位必须与 signal.change_pct 一致 —— **百分点**（不是小数！）。
    # 踩坑记录：曾写成 -0.02/-0.04/-0.06（小数形式），而跌幅值是百分单位
    # （如 -2.5 表示跌 2.5%），导致 -2.5 <= -0.06 恒成立，
    # 只要跌 2.5% 就直接命中「跌 6%」的最高档，加仓倍数严重失真。
    "drop_tiers": [(-2.0, 0.5), (-4.0, 1.0), (-6.0, 1.5)],
    "valuation_low": 30.0,        # PE 百分位 < 此值：低估，+0.5
    "valuation_high": 70.0,       # PE 百分位 > 此值：高估，-0.5
    "risk_off_vix": 30.0,         # VIX 超过此值：避险防御，倍数 ×0.5
    "risk_off_ust_up": 0.30,      # 美债10Y 单日上行超过此(百分点)：减额，倍数 ×0.5
    "multiple_cap": 3.0,          # 倍数上限
    "multiple_floor": 0.0,        # 倍数下限
    # 模式：signal=固定周期+信号增强（骨架） / auto=全自动 / manual=仅提示人工
    "mode": "signal",
}

# ---- 静态估值百分位（零外部依赖；标普500/纳指100 近5年 PE 分位快照） ----
# 来源：周期性维护的年度快照；实现时可用东方财富/新浪动态刷新后覆盖。
# 值含义：当前 PE 在历史中的分位（0-100，越低越便宜）。
STATIC_VALUATION_PCT = {
    "标普500": 78.0,      # 近年偏高
    "纳斯达克100": 65.0,
}

# ---- 跟踪误差基准映射（模块E） ----
# 统一以「真实指数」日线作基准，绝不用场内 ETF 代替（ETF 自身折溢价噪声可达 15%+，
# 会让跟踪误差被高估到 30%+ 的荒谬水平）。
#
# sources 是**有序候选源**，依次尝试、取第一个可用的：
#   ("sina_us", ".INX")    新浪美股指数日K（全历史，实测稳定，优先）
#   ("em_us",   "100.SPX") 东财 push2his 指数日K（数据全，但部分网络出口被断开）
# 两源同一指数，互为备份：
#   .INX    <-> 100.SPX      标普500
#   .NDX    <-> 100.NDX100   纳斯达克100（东财是 100.NDX100，100.NDX 无效）
#   日经225 / 德国DAX 暂无新浪日K（symbol 返回 null），只能走东财。
#
# lag = 净值日期与指数日期的错位交易日数。**实测结论：统一取 0**。
# 东财 lsjz 的 FSRQ（净值日期）本身已对齐到海外估值日，同交易日直接配对即可：
#   2026-10 实测 161125/161130/040046 在 lag=0 时 TE=1.23%/1.55%/1.06%（符合指数基金
#   1~3% 的常识区间）；改成 lag=1 立刻虚高到 18.6%/30.5%/31.1%（错位一天导致的假偏离）。
# 结论：QDII 的 T+1 体现在**披露时间**，不是净值日期的标注，不要再按日期前推。
# 保留 lag 字段是为了个别基金后续微调（如某只确认按海外 D-1 标注）。
TRACK_BENCHMARK = {
    # 标普500
    "161125": {"lag": 0, "sources": [("sina_us", ".INX"), ("em_us", "100.SPX")]},
    "050025": {"lag": 0, "sources": [("sina_us", ".INX"), ("em_us", "100.SPX")]},
    "007721": {"lag": 0, "sources": [("sina_us", ".INX"), ("em_us", "100.SPX")]},
    "096001": {"lag": 0, "sources": [("sina_us", ".INX"), ("em_us", "100.SPX")]},
    # 纳斯达克100
    "161130": {"lag": 0, "sources": [("sina_us", ".NDX"), ("em_us", "100.NDX100")]},
    "160213": {"lag": 0, "sources": [("sina_us", ".NDX"), ("em_us", "100.NDX100")]},
    "270042": {"lag": 0, "sources": [("sina_us", ".NDX"), ("em_us", "100.NDX100")]},
    "000834": {"lag": 0, "sources": [("sina_us", ".NDX"), ("em_us", "100.NDX100")]},
    "040046": {"lag": 0, "sources": [("sina_us", ".NDX"), ("em_us", "100.NDX100")]},
    # 日经225（无新浪日K，仅东财；出口被封时该项暂缺，换网络环境即恢复）
    "020712": {"lag": 0, "sources": [("em_us", "100.N225")]},
    # 德国DAX（同上）
    "000614": {"lag": 0, "sources": [("em_us", "100.GDAXI")]},
}

# ---- 数据源 URL 模板 ----
URL_FUND_POOL_SEARCH = "https://fund.eastmoney.com/js/fundcode_search.js"
URL_LIMIT_ALL = ("https://fund.eastmoney.com/Data/Fund_JJJZ_Data.aspx?t=8&"
                 "page=1,50000&js=reData&sort=fcode,asc")
URL_RATE_INFO = ("https://fundmobapi.eastmoney.com/FundMApi/FundRateInfo.ashx?"
                 "FCODE={code}&deviceid=1&plat=Android&product=EFund&version=6.5.5")
URL_ALIPAY = "https://www.fund123.cn/matiaria?fundCode={code}"
URL_NAV_HISTORY = "https://fundf10.eastmoney.com/f10/jjjz_{code}.html"
URL_NAV_LATEST = ("https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}&"
                  "pageIndex=1&pageSize=1")
URL_SINA_FUTURE = "https://hq.sinajs.cn/list=hf_ES,hf_NQ"
URL_SINA_A_SHARE = "https://hq.sinajs.cn/list={codes}"  # codes: sh513500,sz161125...
URL_YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
