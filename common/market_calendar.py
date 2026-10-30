"""common/market_calendar.py — 零依赖市场交易日历。

QDII 调度需要两套日历：
- A 股交易日：基金申购/赎回/场内交易按此（模块 B/C/G 的 09:00、14:30 任务）。
- 美股交易日：美股现货/期货信号按此（模块 D 的 14:30 任务）。

实现：内置 2026-2027 年主要节假日集合 + 周末排除。零外部依赖。
如需更精确，后续可替换为交易所官方接口；当前集合已覆盖绝大多数实际交易日。

⚠️ 2026-10-30 修（三处静默坏）：
1. is_a_share_trading_day / is_us_trading_day 允许传 datetime.datetime
   （qdii_scheduler 一直这么传），但 `datetime.isoformat()` 返回
   '2026-10-30T14:30:00'，跟集合里的 '2026-10-30' 永远不命中 →
   「是不是休市」判定恒为 True，节假日照跑、周末仍被 weekday 挡住。
   现在自动把 datetime 归一到 date。
2. 表只覆盖到 2027。2028-01-01 起所有非周末都会被判定成交易日，
   调度器会在元旦/春节/国庆空跑请求 + 把上一个交易日的收盘快照错标到
   节假日当天，页面里出现「幽灵交易日」。现在加年覆盖上限检测：
   一旦目标日超过 _HOLIDAY_LAST_YEAR，用 stderr 打警告（不阻断），
   维护者年度更新时把这张表补一年即可。
3. US 日历按美股实际日（美东日期）判定，但调度时点在「北京时间」。
   北京时间 14:30 = 美东 01:30-02:30，属于**前一日美股收盘后** →
   抓的是「昨日美股数据」。旧实现把北京 date 直接丢给 is_us_trading_day，
   遇到美东周五盘后 / 周一开盘前的调度会误判。提供一个可选 helper
   `is_us_trading_session_for_beijing_time` 明确这一层语义。
"""
from datetime import date, datetime

# ---- A 股休市日（2026-2027，含调休；周末已自动排除，这里只列非周末休市） ----
A_SHARE_HOLIDAYS = {
    # 2026
    "2026-01-01",            # 元旦
    "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
    "2026-02-23", "2026-02-24",  # 春节
    "2026-04-04", "2026-04-05", "2026-04-06",  # 清明
    "2026-05-01", "2026-05-04", "2026-05-05",  # 劳动节
    "2026-06-19", "2026-06-20", "2026-06-21",  # 端午
    "2026-09-25", "2026-09-26", "2026-09-27",  # 中秋
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06",
    "2026-10-07",            # 国庆
    # 2027（初步，国庆/春节等以官方为准）
    "2027-01-01",
    "2027-02-16", "2027-02-17", "2027-02-18", "2027-02-19",
    "2027-04-05",
    "2027-05-03", "2027-05-04", "2027-05-05",
    "2027-06-09",            # 端午（暂估）
    "2027-09-15",            # 中秋（暂估）
    "2027-10-01", "2027-10-04", "2027-10-05", "2027-10-06", "2027-10-07",
}

# ---- 美股休市日（2026-2027，周一至周五；均为纽交所/NASDAQ 官方） ----
US_HOLIDAYS = {
    # 2026
    "2026-01-01",            # 元旦
    "2026-01-19",            # 马丁路德金
    "2026-02-16",            # 总统日
    "2026-04-03",            # 耶稣受难日
    "2026-05-25",            # 阵亡将士纪念日
    "2026-07-03",            # 独立日（7/4 周六，提前休市）
    "2026-09-07",            # 劳动节
    "2026-11-26",            # 感恩节
    "2026-12-25",            # 圣诞
    # 2027
    "2027-01-01",
    "2027-01-18",
    "2027-02-15",
    "2027-04-02",
    "2027-05-31",
    "2027-07-05",            # 独立日（7/4 周日，顺延）
    "2027-09-06",
    "2027-11-25",
    "2027-12-24",            # 圣诞（12/25 周六，提前）
}

# 表覆盖到的最后一年。运行到 2028+ 时会给 stderr 打警告、
# 但仍按「非周末即交易日」判定（不误杀调度、只是空跑请求）。
_HOLIDAY_LAST_YEAR = 2027
_warned_years = set()


def _to_date(d):
    """把 datetime 归一到 date（表里键都是 YYYY-MM-DD 字符串）。"""
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    # None → 今天
    return date.today()


def _warn_if_out_of_coverage(d: date, label: str):
    if d.year <= _HOLIDAY_LAST_YEAR:
        return
    if d.year in _warned_years:
        return
    _warned_years.add(d.year)
    print("[market_calendar] ⚠ %s 表尚未覆盖 %d 年，非周末一律视作交易日；"
          "维护者请把 %s_HOLIDAYS 补到新一年。"
          % (label, d.year, label), file=__import__("sys").stderr)


def is_a_share_trading_day(d=None) -> bool:
    d = _to_date(d)
    if d.weekday() >= 5:  # 周六=5 周日=6
        return False
    _warn_if_out_of_coverage(d, "A_SHARE")
    return d.isoformat() not in A_SHARE_HOLIDAYS


def is_us_trading_day(d=None) -> bool:
    """按**美东日历**判定，参数应是美东日期（date/datetime 皆可，会自动归一到 date）。

    如果调用方手上只有北京时间，用 is_us_trading_session_for_beijing_time。
    """
    d = _to_date(d)
    if d.weekday() >= 5:
        return False
    _warn_if_out_of_coverage(d, "US")
    return d.isoformat() not in US_HOLIDAYS


def is_us_trading_session_for_beijing_time(dt=None) -> bool:
    """给 QDII 美股信号调度用的正确语义：

    北京时间 14:30 = 美东 01:30-02:30（夏令时/冬令时都可能），
    此时**美股已经收盘，抓的是上一交易日的数据**。所以判定要看
    「美东昨天是不是交易日」—— 而不是把北京 date 丢给 is_us_trading_day。

    北京日期前一天近似等于美东日期前一天；跨国际日期变更线场景罕见，
    此处按 date-1 判定即可覆盖 MLK / 总统日等周五盘后 / 周一开盘前的调度。
    """
    dt = _to_date(dt)
    from datetime import timedelta
    return is_us_trading_day(dt - timedelta(days=1))


if __name__ == "__main__":
    print("2026-10-29 A股交易日?", is_a_share_trading_day(date(2026, 10, 29)))
    print("2026-10-29 美股交易日?", is_us_trading_day(date(2026, 10, 29)))
    print("2026-12-25 美股交易日?", is_us_trading_day(date(2026, 12, 25)))
    # datetime 现在也能正确判定（旧实现把 datetime.isoformat() 与 'YYYY-MM-DD'
    # 集合比对，永远 False，节假日照跑）
    print("2026-10-01 14:30 A股交易日(datetime)?",
          is_a_share_trading_day(datetime(2026, 10, 1, 14, 30)))
    print("2026-10-05 14:30 A股交易日(datetime)?",
          is_a_share_trading_day(datetime(2026, 10, 5, 14, 30)))
