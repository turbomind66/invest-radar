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
    # ===== 纳斯达克100 =====
    dict(code="000834", name="大成纳斯达克100ETF联接(QDII)A", index_name="纳斯达克100",
         category="纳斯达克100", company="大成", fund_type="ETF联接",
         online_code="159513", is_offline=1, note="对应场内 ETF 159513"),
    dict(code="006479", name="广发纳斯达克100ETF联接人民币(QDII)C", index_name="纳斯达克100",
         category="纳斯达克100", company="广发", fund_type="ETF联接",
         online_code="159941", is_offline=1, note="对应场内 ETF 159941"),
    dict(code="008971", name="大成纳斯达克100ETF联接(QDII)C", index_name="纳斯达克100",
         category="纳斯达克100", company="大成", fund_type="ETF联接",
         online_code="159513", is_offline=1, note="对应场内 ETF 159513"),
    dict(code="012752", name="建信纳斯达克100指数(QDII)C人民币", index_name="纳斯达克100",
         category="纳斯达克100", company="建信", fund_type="指数",
         online_code="", is_offline=1, note="无自家场内 ETF"),
    dict(code="012870", name="易方达纳斯达克100ETF联接(QDII-LOF)C(人民币)", index_name="纳斯达克100",
         category="纳斯达克100", company="易方达", fund_type="LOF",
         online_code="", is_offline=1, note="LOF：场内场外同代码"),
    dict(code="014978", name="华安纳斯达克100ETF联接(QDII)C", index_name="纳斯达克100",
         category="纳斯达克100", company="华安", fund_type="ETF联接",
         online_code="159632", is_offline=1, note="对应场内 ETF 159632"),
    dict(code="015299", name="华夏纳斯达克100ETF发起式联接(QDII)A", index_name="纳斯达克100",
         category="纳斯达克100", company="华夏", fund_type="ETF联接",
         online_code="513300", is_offline=1, note="对应场内 ETF 513300"),
    dict(code="015300", name="华夏纳斯达克100ETF发起式联接(QDII)C", index_name="纳斯达克100",
         category="纳斯达克100", company="华夏", fund_type="ETF联接",
         online_code="513300", is_offline=1, note="对应场内 ETF 513300"),
    dict(code="016055", name="博时纳斯达克100ETF发起式联接(QDII)A人民币", index_name="纳斯达克100",
         category="纳斯达克100", company="博时", fund_type="ETF联接",
         online_code="513390", is_offline=1, note="对应场内 ETF 513390"),
    dict(code="016057", name="博时纳斯达克100ETF发起式联接(QDII)C人民币", index_name="纳斯达克100",
         category="纳斯达克100", company="博时", fund_type="ETF联接",
         online_code="513390", is_offline=1, note="对应场内 ETF 513390"),
    dict(code="016452", name="南方纳斯达克100指数发起(QDII)A", index_name="纳斯达克100",
         category="纳斯达克100", company="南方", fund_type="指数",
         online_code="", is_offline=1, note="无自家场内 ETF"),
    dict(code="016453", name="南方纳斯达克100指数发起(QDII)C", index_name="纳斯达克100",
         category="纳斯达克100", company="南方", fund_type="指数",
         online_code="", is_offline=1, note="无自家场内 ETF"),
    dict(code="016532", name="嘉实纳斯达克100ETF发起联接(QDII)A人民币", index_name="纳斯达克100",
         category="纳斯达克100", company="嘉实", fund_type="ETF联接",
         online_code="159501", is_offline=1, note="对应场内 ETF 159501"),
    dict(code="016533", name="嘉实纳斯达克100ETF发起联接(QDII)C人民币", index_name="纳斯达克100",
         category="纳斯达克100", company="嘉实", fund_type="ETF联接",
         online_code="159501", is_offline=1, note="对应场内 ETF 159501"),
    dict(code="018043", name="天弘纳斯达克100指数发起(QDII)A", index_name="纳斯达克100",
         category="纳斯达克100", company="天弘", fund_type="指数",
         online_code="", is_offline=1, note="无自家场内 ETF"),
    dict(code="018044", name="天弘纳斯达克100指数发起(QDII)C", index_name="纳斯达克100",
         category="纳斯达克100", company="天弘", fund_type="指数",
         online_code="", is_offline=1, note="无自家场内 ETF"),
    dict(code="018966", name="汇添富纳斯达克100ETF发起式联接(QDII)人民币A", index_name="纳斯达克100",
         category="纳斯达克100", company="汇添富", fund_type="ETF联接",
         online_code="159660", is_offline=1, note="对应场内 ETF 159660"),
    dict(code="018967", name="汇添富纳斯达克100ETF发起式联接(QDII)人民币C", index_name="纳斯达克100",
         category="纳斯达克100", company="汇添富", fund_type="ETF联接",
         online_code="159660", is_offline=1, note="对应场内 ETF 159660"),
    dict(code="019172", name="摩根纳斯达克100指数(QDII)人民币A", index_name="纳斯达克100",
         category="纳斯达克100", company="摩根", fund_type="指数",
         online_code="", is_offline=1, note="无自家场内 ETF"),
    dict(code="019173", name="摩根纳斯达克100指数(QDII)人民币C", index_name="纳斯达克100",
         category="纳斯达克100", company="摩根", fund_type="指数",
         online_code="", is_offline=1, note="无自家场内 ETF"),
    dict(code="019441", name="万家纳斯达克100指数发起式(QDII)A", index_name="纳斯达克100",
         category="纳斯达克100", company="万家", fund_type="指数",
         online_code="", is_offline=1, note="无自家场内 ETF"),
    dict(code="019442", name="万家纳斯达克100指数发起式(QDII)C", index_name="纳斯达克100",
         category="纳斯达克100", company="万家", fund_type="指数",
         online_code="", is_offline=1, note="无自家场内 ETF"),
    dict(code="019524", name="华泰柏瑞纳斯达克100ETF发起式联接(QDII)A", index_name="纳斯达克100",
         category="纳斯达克100", company="华泰柏瑞", fund_type="ETF联接",
         online_code="513110", is_offline=1, note="对应场内 ETF 513110"),
    dict(code="019525", name="华泰柏瑞纳斯达克100ETF发起式联接(QDII)C", index_name="纳斯达克100",
         category="纳斯达克100", company="华泰柏瑞", fund_type="ETF联接",
         online_code="513110", is_offline=1, note="对应场内 ETF 513110"),
    dict(code="019547", name="招商纳斯达克100ETF发起式联接(QDII)A", index_name="纳斯达克100",
         category="纳斯达克100", company="招商", fund_type="ETF联接",
         online_code="159659", is_offline=1, note="对应场内 ETF 159659"),
    dict(code="019548", name="招商纳斯达克100ETF发起式联接(QDII)C", index_name="纳斯达克100",
         category="纳斯达克100", company="招商", fund_type="ETF联接",
         online_code="159659", is_offline=1, note="对应场内 ETF 159659"),
    dict(code="019736", name="宝盈纳斯达克100指数发起(QDII)A人民币", index_name="纳斯达克100",
         category="纳斯达克100", company="宝盈", fund_type="指数",
         online_code="", is_offline=1, note="无自家场内 ETF"),
    dict(code="019737", name="宝盈纳斯达克100指数发起(QDII)C人民币", index_name="纳斯达克100",
         category="纳斯达克100", company="宝盈", fund_type="指数",
         online_code="", is_offline=1, note="无自家场内 ETF"),
    dict(code="021773", name="汇添富纳斯达克100ETF发起式联接(QDII)人民币E", index_name="纳斯达克100",
         category="纳斯达克100", company="汇添富", fund_type="ETF联接",
         online_code="159660", is_offline=1, note="对应场内 ETF 159660"),
    dict(code="022525", name="天弘纳斯达克100指数发起(QDII)D", index_name="纳斯达克100",
         category="纳斯达克100", company="天弘", fund_type="指数",
         online_code="", is_offline=1, note="无自家场内 ETF"),
    dict(code="022664", name="华泰柏瑞纳斯达克100ETF发起式联接(QDII)I", index_name="纳斯达克100",
         category="纳斯达克100", company="华泰柏瑞", fund_type="ETF联接",
         online_code="513110", is_offline=1, note="对应场内 ETF 513110"),
    dict(code="023422", name="建信纳斯达克100指数(QDII)D人民币", index_name="纳斯达克100",
         category="纳斯达克100", company="建信", fund_type="指数",
         online_code="", is_offline=1, note="无自家场内 ETF"),
    dict(code="040046", name="华安纳斯达克100ETF联接(QDII)A", index_name="纳斯达克100",
         category="纳斯达克100", company="华安", fund_type="ETF联接",
         online_code="159632", is_offline=1, note="对应场内 ETF 159632"),
    dict(code="160213", name="国泰纳斯达克100指数", index_name="纳斯达克100",
         category="纳斯达克100", company="国泰", fund_type="LOF",
         online_code="160213", is_offline=1, note="LOF：场内场外同代码"),
    dict(code="161130", name="易方达纳斯达克100ETF联接(QDII-LOF)A(人民币)", index_name="纳斯达克100",
         category="纳斯达克100", company="易方达", fund_type="LOF",
         online_code="161130", is_offline=1, note="LOF：场内场外同代码"),
    dict(code="270042", name="广发纳斯达克100ETF联接人民币(QDII)A", index_name="纳斯达克100",
         category="纳斯达克100", company="广发", fund_type="ETF联接",
         online_code="159941", is_offline=1, note="对应场内 ETF 159941"),
    dict(code="539001", name="建信纳斯达克100指数(QDII)A人民币", index_name="纳斯达克100",
         category="纳斯达克100", company="建信", fund_type="指数",
         online_code="", is_offline=1, note="无自家场内 ETF"),
    # ===== 标普500 =====
    dict(code="006075", name="博时标普500ETF联接C", index_name="标普500",
         category="标普500", company="博时", fund_type="ETF联接",
         online_code="513500", is_offline=1, note="对应场内 ETF 513500"),
    dict(code="007721", name="天弘标普500发起(QDII-FOF)A", index_name="标普500",
         category="标普500", company="天弘", fund_type="FOF联接",
         online_code="", is_offline=1, note="无自家场内 ETF"),
    dict(code="007722", name="天弘标普500发起(QDII-FOF)C", index_name="标普500",
         category="标普500", company="天弘", fund_type="FOF联接",
         online_code="", is_offline=1, note="无自家场内 ETF"),
    dict(code="012860", name="易方达标普500指数人民币C", index_name="标普500",
         category="标普500", company="易方达", fund_type="LOF",
         online_code="", is_offline=1, note="LOF：场内场外同代码"),
    dict(code="017028", name="国泰标普500ETF发起联接(QDII)A人民币", index_name="标普500",
         category="标普500", company="国泰", fund_type="ETF联接",
         online_code="159612", is_offline=1, note="对应场内 ETF 159612"),
    dict(code="017030", name="国泰标普500ETF发起联接(QDII)C人民币", index_name="标普500",
         category="标普500", company="国泰", fund_type="ETF联接",
         online_code="159612", is_offline=1, note="对应场内 ETF 159612"),
    dict(code="017641", name="摩根标普500指数(QDII)人民币A", index_name="标普500",
         category="标普500", company="摩根", fund_type="指数",
         online_code="", is_offline=1, note="无自家场内 ETF"),
    dict(code="018064", name="华夏标普500ETF发起式联接(QDII)A(人民币)", index_name="标普500",
         category="标普500", company="华夏", fund_type="ETF联接",
         online_code="159655", is_offline=1, note="对应场内 ETF 159655"),
    dict(code="018065", name="华夏标普500ETF发起式联接(QDII)C", index_name="标普500",
         category="标普500", company="华夏", fund_type="ETF联接",
         online_code="159655", is_offline=1, note="对应场内 ETF 159655"),
    dict(code="019305", name="摩根标普500指数(QDII)人民币C", index_name="标普500",
         category="标普500", company="摩根", fund_type="指数",
         online_code="", is_offline=1, note="无自家场内 ETF"),
    dict(code="022523", name="天弘标普500发起(QDII-FOF)D", index_name="标普500",
         category="标普500", company="天弘", fund_type="FOF联接",
         online_code="", is_offline=1, note="无自家场内 ETF"),
    dict(code="050025", name="博时标普500ETF联接A", index_name="标普500",
         category="标普500", company="博时", fund_type="ETF联接",
         online_code="513500", is_offline=1, note="对应场内 ETF 513500"),
    dict(code="161125", name="易方达标普500指数人民币A", index_name="标普500",
         category="标普500", company="易方达", fund_type="LOF",
         online_code="161125", is_offline=1, note="LOF：场内场外同代码"),
    # ===== 标普500等权 =====
    dict(code="096001", name="大成标普500等权重指数(QDII)A", index_name="标普500等权",
         category="标普500等权", company="大成", fund_type="指数",
         online_code="", is_offline=1, note="等权重指数，无对应场内ETF；跟踪标的与标普500不同，单列一类"),
    # ===== 纳斯达克生物科技 =====
    dict(code="001092", name="广发生物科技指数人民币(QDII)A", index_name="纳斯达克生物科技",
         category="纳斯达克生物科技", company="广发", fund_type="指数",
         online_code="", is_offline=1, note="无自家场内 ETF"),
    dict(code="016470", name="广发生物科技指数人民币(QDII)C", index_name="纳斯达克生物科技",
         category="纳斯达克生物科技", company="广发", fund_type="指数",
         online_code="", is_offline=1, note="无自家场内 ETF"),
    dict(code="017894", name="汇添富纳斯达克生物科技ETF发起式联接(QDII)人民币A", index_name="纳斯达克生物科技",
         category="纳斯达克生物科技", company="汇添富", fund_type="ETF联接",
         online_code="513290", is_offline=1, note="对应场内 ETF 513290"),
    dict(code="017895", name="汇添富纳斯达克生物科技ETF发起式联接(QDII)人民币C", index_name="纳斯达克生物科技",
         category="纳斯达克生物科技", company="汇添富", fund_type="ETF联接",
         online_code="513290", is_offline=1, note="对应场内 ETF 513290"),
    # ===== 纳斯达克科技 =====
    dict(code="017091", name="景顺长城纳斯达克科技ETF联接(QDII)A人民币", index_name="纳斯达克科技",
         category="纳斯达克科技", company="景顺长城", fund_type="ETF联接",
         online_code="159509", is_offline=1, note="对应场内 ETF 159509"),
    dict(code="017093", name="景顺长城纳斯达克科技ETF联接(QDII)C人民币", index_name="纳斯达克科技",
         category="纳斯达克科技", company="景顺长城", fund_type="ETF联接",
         online_code="159509", is_offline=1, note="对应场内 ETF 159509"),
    dict(code="019118", name="景顺长城纳斯达克科技ETF联接(QDII)E人民币", index_name="纳斯达克科技",
         category="纳斯达克科技", company="景顺长城", fund_type="ETF联接",
         online_code="159509", is_offline=1, note="对应场内 ETF 159509"),
    # ===== 日经225 =====
    dict(code="020712", name="华安三菱日联日经225ETF发起式联接(QDII)A", index_name="日经225",
         category="日经225", company="华安", fund_type="ETF联接",
         online_code="513880", is_offline=1, note="对应场内 ETF 513880"),
    dict(code="020713", name="华安三菱日联日经225ETF发起式联接(QDII)C", index_name="日经225",
         category="日经225", company="华安", fund_type="ETF联接",
         online_code="513880", is_offline=1, note="对应场内 ETF 513880"),
    # ===== 德国DAX =====
    dict(code="000614", name="华安德国(DAX)联接(QDII)A", index_name="德国DAX",
         category="德国DAX", company="华安", fund_type="指数",
         online_code="", is_offline=1, note="无自家场内 ETF"),
    dict(code="015016", name="华安德国(DAX)联接(QDII)C", index_name="德国DAX",
         category="德国DAX", company="华安", fund_type="指数",
         online_code="", is_offline=1, note="无自家场内 ETF"),
    # ===== 法国CAC40 =====
    dict(code="021539", name="华安法国CAC40ETF发起式联接(QDII)A", index_name="法国CAC40",
         category="法国CAC40", company="华安", fund_type="ETF联接",
         online_code="513080", is_offline=1, note="对应场内 ETF 513080"),
    dict(code="021540", name="华安法国CAC40ETF发起式联接(QDII)C", index_name="法国CAC40",
         category="法国CAC40", company="华安", fund_type="ETF联接",
         online_code="513080", is_offline=1, note="对应场内 ETF 513080"),
    # ===== 英国富时100 =====
    dict(code="008706", name="建信富时100指数(QDII)C人民币", index_name="英国富时100",
         category="英国富时100", company="建信", fund_type="指数",
         online_code="", is_offline=1, note="无自家场内 ETF"),
    dict(code="023373", name="建信富时100指数(QDII)D人民币", index_name="英国富时100",
         category="英国富时100", company="建信", fund_type="指数",
         online_code="", is_offline=1, note="无自家场内 ETF"),
    dict(code="539003", name="建信富时100指数(QDII)A人民币", index_name="英国富时100",
         category="英国富时100", company="建信", fund_type="指数",
         online_code="", is_offline=1, note="无自家场内 ETF"),
]


# ---- 场内溢价监控清单（模块G） ----
# ⚠️ 溢价率必须按「场内 ETF 代码」计算，不能按场外基金遍历 online_code：
#    旧实现把 5 只纳指场外基金全指向 513100，导致表里 5 行溢价率一模一样，
#    而广发 159941（溢价常年在 10% 以上、最需要看的品种）从未出现过。
# 这里显式维护场内可交易的主流 QDII ETF，一只一行；场外基金与 ETF 的关系
# 用「同一指数」表达即可（同一指数可由多家 ETF 跟踪，不存在唯一映射）。
# 名称/代码已用新浪行情（2026-10-29）逐一核对。
# bench = (源类型, 代码)，用于「折算溢价」：QDII 净值 T+1 披露，净值日期常比场内价晚
#   1~2 个交易日，直接相减会把溢价系统性高估；用该指数的日线把净值折算到最新收盘日，
#   才是「今天的真实溢价」。取不到日线的品种（日经/DAX 东财接口常被断）留空不折算。
PREMIUM_ETFS = [
    dict(code="513100", name="纳指ETF", company="国泰", index_name="纳斯达克100",
         category="纳斯达克100", bench=("sina_us", ".NDX")),
    dict(code="159941", name="纳指ETF", company="广发", index_name="纳斯达克100",
         category="纳斯达克100", bench=("sina_us", ".NDX")),
    dict(code="159632", name="纳斯达克ETF", company="华安", index_name="纳斯达克100",
         category="纳斯达克100", bench=("sina_us", ".NDX")),
    dict(code="513300", name="纳斯达克ETF", company="华夏", index_name="纳斯达克100",
         category="纳斯达克100", bench=("sina_us", ".NDX")),
    dict(code="513110", name="纳指ETF", company="华泰柏瑞", index_name="纳斯达克100",
         category="纳斯达克100", bench=("sina_us", ".NDX")),
    dict(code="159501", name="纳指ETF嘉实", company="嘉实", index_name="纳斯达克100",
         category="纳斯达克100", bench=("sina_us", ".NDX")),
    dict(code="159513", name="纳斯达克100ETF大成", company="大成", index_name="纳斯达克100",
         category="纳斯达克100", bench=("sina_us", ".NDX")),
    dict(code="159659", name="纳斯达克100ETF招商", company="招商", index_name="纳斯达克100",
         category="纳斯达克100", bench=("sina_us", ".NDX")),
    dict(code="159660", name="纳指ETF汇添富", company="汇添富", index_name="纳斯达克100",
         category="纳斯达克100", bench=("sina_us", ".NDX")),
    dict(code="159696", name="纳指ETF易方达", company="易方达", index_name="纳斯达克100",
         category="纳斯达克100", bench=("sina_us", ".NDX")),
    dict(code="513390", name="纳指100ETF博时", company="博时", index_name="纳斯达克100",
         category="纳斯达克100", bench=("sina_us", ".NDX")),
    dict(code="513870", name="纳指ETF富国", company="富国", index_name="纳斯达克100",
         category="纳斯达克100", bench=("sina_us", ".NDX")),
    dict(code="513500", name="标普500ETF博时", company="博时", index_name="标普500",
         category="标普500", bench=("sina_us", ".INX")),
    dict(code="513650", name="标普500ETF南方", company="南方", index_name="标普500",
         category="标普500", bench=("sina_us", ".INX")),
    dict(code="159612", name="标普500ETF国泰", company="国泰", index_name="标普500",
         category="标普500", bench=("sina_us", ".INX")),
    dict(code="159655", name="标普500ETF华夏", company="华夏", index_name="标普500",
         category="标普500", bench=("sina_us", ".INX")),
    dict(code="513290", name="纳指生物科技ETF汇添富", company="汇添富", index_name="纳斯达克生物科技",
         category="纳斯达克生物科技", bench=("sina_us", ".NBI")),
    dict(code="159509", name="纳指科技ETF景顺", company="景顺长城", index_name="纳斯达克科技",
         category="纳斯达克科技", bench=None),
    dict(code="513880", name="日经225ETF华安", company="华安", index_name="日经225",
         category="日经225", bench=("sina_fut", "NK")),
    dict(code="513000", name="日经225ETF易方达", company="易方达", index_name="日经225",
         category="日经225", bench=("sina_fut", "NK")),
    dict(code="513520", name="日经ETF华夏", company="华夏", index_name="日经225",
         category="日经225", bench=("sina_fut", "NK")),
    dict(code="513030", name="德国ETF华安", company="华安", index_name="德国DAX",
         category="德国DAX", bench=("em_us", "100.GDAXI")),
    dict(code="159561", name="德国ETF嘉实", company="嘉实", index_name="德国DAX",
         category="德国DAX", bench=("em_us", "100.GDAXI")),
    dict(code="513080", name="法国ETF华安", company="华安", index_name="法国CAC40",
         category="法国CAC40", bench=None),
]

# ---- 全球市场信号（模块D）：分市场分组，供面板展示 ----
# 每类一个「主信号」：能取到海外指数现货就用现货；取不到的（DAX/CAC40/XNDT）
# 用**境内对应场内 ETF** 实时价代理；富时100 连境内 ETF 都没有，用美股
# `usEWU`（iShares MSCI 英国 ETF）代理 —— 代理口径必须在页面写明，不能装作是指数。
# 类型标签会直接显示在面板上（指数/期货/场内ETF/美股ETF·代理）。
MARKET_SIGNALS = [
    ("IXIC", "纳指现货", "美股", "指数 · 上一交易日收盘"),
    ("NDX_FUT", "纳指期货", "美股", "期货 · 24h 连续"),
    ("SPX", "标普500现货", "美股", "指数 · 上一交易日收盘"),
    ("SPX_FUT", "标普500期货", "美股", "期货 · 24h 连续"),
    ("NBI", "纳指生技现货", "科技", "指数 · 上一交易日收盘"),
    ("NXT_ETF", "纳指科技（159509）", "科技", "场内ETF · 实时"),
    ("NK", "日经225期货", "亚太", "期货 · 24h 连续"),
    ("DAX_ETF", "德国DAX（513030）", "欧洲", "场内ETF · 实时"),
    ("CAC40_ETF", "法国CAC40（513080）", "欧洲", "场内ETF · 实时"),
    ("FTSE_UK", "英国富时100（代理）", "欧洲", "美股ETF · 实时 · 代理"),
    ("UST10Y", "美债10Y收益率", "利率", "实时 · 收益率"),
    ("VIX", "VIX波动率", "利率", "日频 · 上一交易日收盘"),
]
# 面板分组顺序（同一组内按上表顺序）
MARKET_GROUPS = ["美股", "科技", "亚太", "欧洲", "利率"]

# ---- 定投决策：大类 → 参考信号 ----
# 按「大类」给建议，不落到具体基金（同一大类下各家基金同涨同跌，差别只在费率和限额）。
# 2026-10-29 起**每个大类都有自有信号**（此前日经225/德国DAX 只能借美股风向）：
#   纳斯达克100 → 纳指综合指数(IXIC)      标普500 → 标普500指数(SPX)
#   纳斯达克生物科技 → 纳指生技指数(NBI)   纳斯达克科技 → 境内 ETF 159509
#   日经225 → 日经225期货(NK)              德国DAX → 境内 ETF 513030
#   法国CAC40 → 境内 ETF 513080            英国富时100 → 美股 ETF 代理(usEWU)
CATEGORY_SIGNAL = {
    "纳斯达克100": ["IXIC", "NDX_FUT"],
    "标普500": ["SPX", "SPX_FUT"],
    "标普500等权": ["SPX"],
    "纳斯达克生物科技": ["NBI"],
    "纳斯达克科技": ["NXT_ETF"],
    "日经225": ["NK"],
    "德国DAX": ["DAX_ETF"],
    "法国CAC40": ["CAC40_ETF"],
    "英国富时100": ["FTSE_UK"],
}
# 各大类的自有信号（第一个即主信号）；不在表里的大类才会去借美股风向
CATEGORY_OWN_SIGNAL = {k: v[0] for k, v in CATEGORY_SIGNAL.items()}
# 大类顺序（柱状图与表格按此排）
CATEGORY_ORDER = ["纳斯达克100", "标普500", "标普500等权", "纳斯达克生物科技",
                  "纳斯达克科技", "日经225", "德国DAX", "法国CAC40", "英国富时100"]

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
    # --- 避险防御三判据（维度不同，并列触发，谁触发写谁；见 strategy.risk_off_assess）---
    # ① VIX 绝对水平：市场「已经」恐慌到什么程度（日频，CBOE 收盘）
    "risk_off_vix": 30.0,         # > 30：防御，倍数 ×0.5
    "risk_off_vix_warn": 20.0,    # 20~30：预警区，只提示不减额（原来一刀切看不见逼近过程）
    # ② VIX 单日跳升幅度：情绪急变速度（如 15 → 19 一天到位），只提示
    "risk_off_vix_jump": 20.0,    # 单日涨 ≥ 20%：提示「情绪急变」
    # ③ 美债10Y 单日变动：无风险利率跳升 → 压制估值，是「原因」而非「结果」。
    #    ⚠️ 必须用 bp：收益率的「相对涨跌%」没有经济含义（4.00%→4.04% 只算 +1%，实际 +4bp），
    #    旧代码拿 change_pct(-0.99%) 去比 0.30 阈值，等于开关基本不会触发。
    "risk_off_ust_bp": 30.0,      # > +30bp：防御，倍数 ×0.5
    "risk_off_ust_warn_bp": 15.0,  # +15~+30bp：逼近阈值，只提示
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
    # 其余大类（纳斯达克生物科技/科技、日经225、德国DAX、法国CAC40、英国富时100）
    # 没有维护的 PE 分位值 → 一律按中性处理，**不凭空编造**（见 strategy.recommend_category）。
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
# 按**大类**给基准（同一大类的基金跟踪同一个指数 → 基准相同）。
# 66 只基金逐个硬编码不现实且易错；按代码的 TRACK_BENCHMARK 优先（个别基金有特殊基准，
# 如 096001 是标普500等权），查不到再回落这里。
# sources 为空 = 该指数没有可用的公开日线源 → 跟踪误差留空，页面显示「待计算」，
# **不要用场内 ETF 当基准**（折溢价噪声会让 TE 虚高到 30%+）。
TRACK_BENCHMARK_BY_CATEGORY = {
    "纳斯达克100": {"lag": 0, "sources": [("sina_us", ".NDX"), ("em_us", "100.NDX100")]},
    "标普500": {"lag": 0, "sources": [("sina_us", ".INX"), ("em_us", "100.SPX")]},
    # 等权指数没有公开日K → 只能用市值加权 .INX 近似，TE 会偏大（页面需注明口径）
    "标普500等权": {"lag": 0, "sources": [("sina_us", ".INX")]},
    "纳斯达克生物科技": {"lag": 0, "sources": [("sina_us", ".NBI")]},
    "纳斯达克科技": {"lag": 0, "sources": []},       # 纳斯达克科技市值加权指数无公开日K
    "日经225": {"lag": 0, "sources": [("sina_fut", "NK")]},
    "德国DAX": {"lag": 0, "sources": [("em_us", "100.GDAXI")]},
    "法国CAC40": {"lag": 0, "sources": []},          # 无公开日K
    "英国富时100": {"lag": 0, "sources": []},        # 无公开日K
}

# 按代码的特殊基准表已废弃（2026-10-29）：基金池扩到 66 只后逐只硬编码不现实，
# 且旧表里日经写的是 em_us 100.N225（本网络恒断）。统一走 TRACK_BENCHMARK_BY_CATEGORY。
TRACK_BENCHMARK = {}

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
URL_SINA_FUTURE = "https://hq.sinajs.cn/list=hf_ES,hf_NQ,hf_NK"
URL_SINA_A_SHARE = "https://hq.sinajs.cn/list={codes}"  # codes: sh513500,sz161125...
URL_YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
