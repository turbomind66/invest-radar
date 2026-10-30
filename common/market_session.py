"""common/market_session.py — 全球主要市场的交易时段（**北京时间**）判定。

为什么需要它（2026-10-30 丁总反馈）：
    QDII 页「全球市场信号」里 `is_realtime` 是**硬编码**的（期货/美债写 1、
    现货写 0），于是「日经225」和「欧洲三大」被统一标成「昨收·非实时」。
    但实际上：
      - 日经225 东京 09:00–15:30 JST = **北京 08:00–14:30**，北京时间 14:30 就已收盘，
        晚上看到的确实是**今天**的收盘价，不是昨收；
      - DAX / CAC40 / FTSE 在北京时间 15:00–次日 00:30 区间是**正在交易**的，
        晚上 19:00 看到的是实时价，标成「昨收·非实时」是错的；
      - 真正「昨收」的是美股现货（IXIC/SPX/VIX）：美东 09:30–16:00 =
        北京 21:30–次日 04:00(夏)/05:00(冬)，北京时间白天到晚上 21:30 之前
        显示的都是**上一交易日**收盘。

所以 `is_realtime` 必须由「抓取时刻 + 各市场真实交易时段」算出来，
并额外给出一段人话状态文案 `market_status`（实时 / 已收盘 HH:MM / 未开盘）。

实现要点：
  - 零依赖（标准库 datetime），与 common/market_calendar.py 同一层级；
  - 夏令时按规则计算（EU：3 月最后一个周日 ~ 10 月最后一个周日；
    US：3 月第 2 个周日 ~ 11 月第 1 个周日），不查网络；
  - 休市日只判周末 + 美股节假日表（EU/JP 节假日未建表，按周末处理 ——
    节假日对状态文案的影响只是「上一交易日」的前移一天，不影响实时/收盘判定）。
"""
from datetime import date, datetime, time, timedelta

# 各市场当地连续交易时段（当地时钟）与所属时区规则
#   tz: "JP"(+9 无夏令时) / "EU"(+1/+2) / "UK"(+0/+1) / "US"(-5/-4)
#   close < open 表示跨日（如 CME 期货 06:00 → 次日 05:00）
#   None 表示近 24 小时连续（如美债 10Y 现券收益率）
_SESSIONS = {
    "NK": ("JP", "09:00", "15:30", "东京证券交易所"),
    "DAX": ("EU", "09:00", "17:30", "法兰克福证券交易所"),
    "CAC40": ("EU", "09:00", "17:30", "泛欧交易所(巴黎)"),
    "FTSE": ("UK", "08:00", "16:30", "伦敦证券交易所"),
    "IXIC": ("US", "09:30", "16:00", "纳斯达克"),
    "SPX": ("US", "09:30", "16:00", "纽交所"),
    "VIX": ("US", "09:30", "16:00", "CBOE"),
    "NDX_FUT": ("US", "06:00", "05:00", "CME Globex"),
    "SPX_FUT": ("US", "06:00", "05:00", "CME Globex"),
    "UST10Y": None,
}

# 美股节假日（与 market_calendar.US_HOLIDAYS 同源，避免跨模块循环 import 时重复维护，
# 这里只用到「是不是交易日」这一层，故直接复用 market_calendar 的函数）
_SUNDAY = 6


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """该月第 n 个 weekday（周一=0 … 周日=6）。"""
    d = date(year, month, 1)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d + timedelta(days=7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """该月最后一个 weekday。"""
    if month == 12:
        last = date(year, 12, 31)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    while last.weekday() != weekday:
        last -= timedelta(days=1)
    return last


def _tz_offset(tz: str, local_day: date) -> int:
    """该市场在 local_day（当地日历日）的 UTC 偏移小时数。"""
    if tz == "JP":
        return 9
    if tz == "EU":
        s, e = _last_weekday(local_day.year, 3, _SUNDAY), _last_weekday(local_day.year, 10, _SUNDAY)
        return 2 if s <= local_day < e else 1
    if tz == "UK":
        s, e = _last_weekday(local_day.year, 3, _SUNDAY), _last_weekday(local_day.year, 10, _SUNDAY)
        return 1 if s <= local_day < e else 0
    if tz == "US":
        s = _nth_weekday(local_day.year, 3, _SUNDAY, 2)
        e = _nth_weekday(local_day.year, 11, _SUNDAY, 1)
        return -4 if s <= local_day < e else -5
    return 0


def _us_holidays():
    try:
        from common.market_calendar import US_HOLIDAYS
        return US_HOLIDAYS
    except Exception:  # noqa: BLE001  # 被单文件直跑时 common 可能不在 path 上
        try:
            from market_calendar import US_HOLIDAYS
            return US_HOLIDAYS
        except Exception:  # noqa: BLE001
            return set()


def _is_open_day(tz: str, local_day: date) -> bool:
    """当地日历日是不是开市日（周末 + 美股节假日排除；EU/JP 只排除周末）。"""
    if local_day.weekday() >= 5:
        return False
    if tz == "US" and local_day.isoformat() in _us_holidays():
        return False
    return True


def _session_window(tz: str, local_day: date, open_hm: str, close_hm: str):
    """某一当地日历日的交易时段，换算成**北京时间**的 [起, 止)。"""
    off = _tz_offset(tz, local_day)
    shift = timedelta(hours=8 - off)  # 当地 → 北京
    oh, om = (int(x) for x in open_hm.split(":"))
    ch, cm = (int(x) for x in close_hm.split(":"))
    start = datetime.combine(local_day, time(oh, om)) + shift
    end = datetime.combine(local_day, time(ch, cm)) + shift
    if end <= start:  # 跨日（如期货 06:00 → 次日 05:00）
        end += timedelta(days=1)
    return start, end


def known_symbols():
    """本模块认识哪些 symbol（不认识的别拿它算时段，沿用调用方的硬编码值）。"""
    return set(_SESSIONS)


def market_status(symbol: str, now: datetime = None) -> dict:
    """返回某标的在 `now`（**北京时间**，naive datetime）的市场状态。

    {
      "is_realtime": 1/0,        # 1 = 此刻正在交易
      "market_status": "实时"/"已收盘 14:30"/"已收盘 04:00 · 21:30 开盘"/"休市",
      "session_close": "HH:MM" 或 None,   # 最近一次收盘的北京时刻
      "next_open": "HH:MM" 或 None,       # 下一次开盘的北京时刻（当天内才有）
      "venue": "东京证券交易所" 或 ""
    }
    """
    now = now or datetime.now()
    cfg = _SESSIONS.get(symbol)
    if cfg is None:
        return {"is_realtime": 1, "market_status": "实时", "session_close": None,
                "next_open": None, "venue": ""}
    tz, open_hm, close_hm, venue = cfg

    # 只考察「当地日历日」在 now 前后 3 天内的时段，足够覆盖所有跨日情形
    bj_day = now.date()
    cands = []
    for delta in (-2, -1, 0, 1, 2):
        local_day = bj_day + timedelta(days=delta)
        if not _is_open_day(tz, local_day):
            continue
        s, e = _session_window(tz, local_day, open_hm, close_hm)
        cands.append((s, e))
    cands.sort()

    for s, e in cands:
        if s <= now < e:
            return {"is_realtime": 1, "market_status": "实时",
                    "session_close": e.strftime("%H:%M"),
                    "next_open": None, "venue": venue}

    prev_close = max((e for _s, e in cands if e <= now), default=None)
    next_open = min((s for s, _e in cands if s > now), default=None)

    if prev_close is None:
        return {"is_realtime": 0, "market_status": "休市", "session_close": None,
                "next_open": next_open.strftime("%H:%M") if next_open else None,
                "venue": venue}

    # 「此刻」换算到当地日历日：非开市日（周末 / 美股节假日）统一写休市，
    # 否则圣诞这类假日会被标成「已收盘 05:00」，看着像当天还在交易。
    off_now = _tz_offset(tz, bj_day)
    local_now = now + timedelta(hours=off_now - 8)
    if not _is_open_day(tz, local_now.date()) or (now - prev_close) > timedelta(hours=24):
        return {"is_realtime": 0, "market_status": "休市 · 上一交易日收盘",
                "session_close": prev_close.strftime("%H:%M"),
                "next_open": next_open.strftime("%H:%M") if next_open else None,
                "venue": venue}

    close_txt = prev_close.strftime("%H:%M")
    label = "已收盘 %s" % close_txt
    # 下一次开盘仍在同一北京日历日内 → 顺带给出开盘时间，省得用户猜
    if next_open is not None and next_open.date() == now.date():
        label += " · %s 开盘" % next_open.strftime("%H:%M")
    return {"is_realtime": 0, "market_status": label, "session_close": close_txt,
            "next_open": next_open.strftime("%H:%M") if next_open else None,
            "venue": venue}


if __name__ == "__main__":
    for hhmm, syms in (("10-30 19:00", ("NK", "DAX", "CAC40", "FTSE", "IXIC",
                                        "SPX", "VIX", "NDX_FUT", "UST10Y")),):
        for s in syms:
            d = datetime.strptime("2026-" + hhmm, "%Y-%m-%d %H:%M")
            st = market_status(s, d)
            print("%-8s %s | %s" % (s, hhmm, st))
