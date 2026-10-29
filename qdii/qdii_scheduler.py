"""qdii/qdii_scheduler.py — QDII 模块定时调度器（零第三方依赖，纯标准库）。

双日历调度：
- LIMIT 任务（A 股交易日 09:00）：抓取 申购状态/限额 + 多平台费率 + 跟踪误差。
- 14:30 槽（北京时间）：
    · 美股交易日 → 抓取美股信号（期货实时/现货昨收/美债/VIX）+ 决策。
    · A 股交易日 → 抓取场内溢价 + 决策。
  （多数交易日两者重合，由文件锁串行，互不抢写）

并发保护：所有任务写同一 SQLite，用 common/lock 跨进程文件锁互斥。

手动触发（容器内排查/补抓）：
    python qdii/qdii_scheduler.py --once            # 限额+费率+跟踪误差、信号、溢价全跑一遍
    python qdii/qdii_scheduler.py --once=limit      # 只跑限额/费率/跟踪误差
    python qdii/qdii_scheduler.py --once=signal     # 只跑美股信号+决策
    python qdii/qdii_scheduler.py --once=premium    # 只跑场内溢价+决策
"""
import os
import sys
import time
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
for p in (BASE, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

import common.lock as L
import constants as C
import common.market_calendar as cal
import fetcher_pool
import fetcher_limit
import fetcher_fee
import fetcher_signal
import fetcher_premium
import tracking_error
import strategy

LOCK_PATH = os.path.join(os.path.dirname(C.QDII_DB_PATH), ".qdii.lock")


def _env_bool(key, default="0"):
    return (os.environ.get(key) or default).strip().lower() in ("1", "true", "yes", "on")


def parse_times(raw):
    out = set()
    for seg in (raw or "").split(","):
        seg = seg.strip()
        if not seg:
            continue
        parts = seg.split(":")
        if len(parts) != 2:
            continue
        try:
            h, m = int(parts[0]), int(parts[1])
        except (TypeError, ValueError):
            continue
        if 0 <= h <= 23 and 0 <= m <= 59:
            out.add((h, m))
    return sorted(out)


def due(now, times):
    return (now.hour, now.minute) in set(times or [])


def run_limit():
    with L.FileLock(LOCK_PATH):
        n1 = fetcher_limit.fetch_all_limit_status()
        n2 = fetcher_fee.fetch_platform_fee()
        n3 = tracking_error.fetch_tracking_error()
        print("[sched] limit+fee+te done: %d/%d/%d" % (n1, n2, n3))


def run_signal():
    with L.FileLock(LOCK_PATH):
        n = fetcher_signal.fetch_us_signal()
        strategy.run_daily_decision()          # 逐基金（追溯用）
        nc = strategy.run_category_decision()  # 大类（页面主口径）
        print("[sched] signal+decision done: %d, categories=%d" % (n, nc))


def run_premium():
    with L.FileLock(LOCK_PATH):
        n = fetcher_premium.fetch_premium()
        strategy.run_daily_decision()
        strategy.run_category_decision()
        print("[sched] premium+decision done: %d" % n)


def run_on_start():
    print("[sched] run_on_start: limit + signal + premium")
    run_limit()
    run_signal()
    run_premium()


def main():
    fetcher_pool.init_pool()
    tasks = {
        "limit": run_limit,
        "signal": run_signal,
        "premium": run_premium,
    }
    once = [a for a in sys.argv[1:] if a.startswith("--once")]
    if once:
        # 手动一次性触发：不判断交易日历（人工调用就是要立刻跑）
        which = once[0].split("=", 1)[1] if "=" in once[0] else "all"
        picked = list(tasks.values()) if which == "all" else [tasks[which]] if which in tasks else None
        if picked is None:
            print("[sched] 未知任务 %r（可选：limit / signal / premium）" % which)
            return 2
        for fn in picked:
            fn()
        return 0

    times_limit = parse_times(C.QDII_SCHED_LIMITTIMES)
    times_signal = parse_times(C.QDII_SCHED_SIGNALTIME)     # 美股日历
    times_premium = parse_times(C.QDII_SCHED_PREMIUMTIME)   # A 股日历
    last_minute = set()
    if _env_bool("SCHED_RUN_ON_START"):
        try:
            run_on_start()
        except Exception as e:  # noqa: BLE001
            print("[sched] run_on_start error:", e)
    print("[sched] started. limit=%s signal=%s premium=%s"
          % (times_limit, times_signal, times_premium))
    while True:
        now = datetime.datetime.now()
        key = (now.hour, now.minute)
        if key not in last_minute:
            last_minute.add(key)
            # 各任务按自己的日历与时点触发，均经文件锁串行，互不抢写 SQLite
            if due(now, times_limit) and cal.is_a_share_trading_day(now):
                try:
                    run_limit()
                except Exception as e:  # noqa: BLE001
                    print("[sched] limit error:", e)
            if due(now, times_signal) and cal.is_us_trading_day(now):
                try:
                    run_signal()
                except Exception as e:  # noqa: BLE001
                    print("[sched] signal error:", e)
            if due(now, times_premium) and cal.is_a_share_trading_day(now):
                try:
                    run_premium()
                except Exception as e:  # noqa: BLE001
                    print("[sched] premium error:", e)
        if len(last_minute) > 3000:
            last_minute.clear()
        time.sleep(20)


if __name__ == "__main__":
    sys.exit(main() or 0)
