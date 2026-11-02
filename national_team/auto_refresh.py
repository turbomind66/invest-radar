"""auto_refresh.py — **单应用形态**（WorkBuddy）专属的自动补数与周期刷新。

为什么需要它（2026-11-02 修「WorkBuddy 版很多内容不显示」）：
    三套部署形态里，只有 WorkBuddy 这一套**既没有 cron、SQLite 也不随部署走**：
      - GitHub Pages：GitHub Actions 定时跑 staticsite.py，每次都是干净环境，
        首启由 import_from_days_file() 从上一版 data.json 恢复 90 天历史；
      - VPS：docker 的 cron 容器跑 scheduler.py → staticsite.py，同样带首启恢复；
      - WorkBuddy：combo_app 只 spawn 两个只读面板（NT_AUTO_ENSURE=0），
        既没有 cron 也没有恢复钩子 → 线上库里只剩「用户手动刷新那一天」的
        1 天数据 → 趋势图只有 1 个点、份额列全是「—」、新闻整块空白、
        分时/逐笔面板也空。

本模块补上单应用形态缺失的两件事：
    1) bootstrap()：一次性补数——先从内置 docs/data.json **按日期增量**恢复
       90 天历史/份额/新闻/逐笔（本地文件、不联网、秒级），再补份额全量、
       二级历史回补、当日行情与分时/逐笔；
    2) loop()：周期刷新——盘中每 30 分钟采样分时 + 逐笔大单（多次采样才能
       累积出全天分布），收盘后当天跑一次全量同步（等价 Actions 17:00 那次）。

**只在 NT_AUTO_REFRESH=1 时启用**（由 combo_app 注入），
GitHub Pages / VPS / 本地直跑 app.py 的行为完全不变（默认 0 = 不启用）。
每一步都独立容错：任一步失败只记日志，绝不阻断其余步骤，更不会让面板起不来。
"""
import datetime
import os
import sys
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
# BASE 排在 ROOT 之前，避免部署根目录下的同名旧副本被优先 import（v2.6.1 踩过）
for _p in (ROOT, BASE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
if sys.path and sys.path[0] != BASE:
    sys.path.remove(BASE)
    sys.path.insert(0, BASE)

# ⚠️ 容器时区：WorkBuddy 沙箱可能是 UTC，而 fetcher.fetch_and_save 用的是
# `datetime.date.today()`（= 容器本地日期）——北京 06:00 时 UTC 还是前一天，
# 会把当天数据写成前一天并覆盖历史行（v2.8.0 在 QDII 侧修过同一类 bug）。
# 这里在**真正启用**时才把进程时区固定成 Asia/Shanghai，让「容器本地日期 == 北京日期」成立。
#
# ⚠️ 必须只在 enabled() 为真时才改：直接跑 national_team/app.py 的三种原部署
# （Windows 本地 / Linux 本地 / Linux VPS）默认 NT_AUTO_REFRESH=0，
# 它们的进程时区必须保持原样（铁律：三种原部署方式不受牵连）。
_TZ_FIXED = False


def _ensure_shanghai_tz():
    """把进程时区固定为 Asia/Shanghai（幂等）。"""
    global _TZ_FIXED
    if _TZ_FIXED:
        return
    os.environ.setdefault("TZ", "Asia/Shanghai")
    if hasattr(time, "tzset"):
        try:
            time.tzset()
        except Exception:  # noqa: BLE001
            pass
    _TZ_FIXED = True

import db  # noqa: E402
import fetcher  # noqa: E402
import intraday  # noqa: E402

try:
    from common.market_session import bj_now
except ImportError:  # 兼容被单独 exec 时的 sys.path 状态
    def bj_now():
        return datetime.datetime.now()

# 二次资金流上游只保留约 121 个交易日，回补到这个上限即可
BACKFILL_SPAN = 121
# 一级份额上游可回补约 368 个交易日
SHARE_SPAN = 400
# 触发「历史缺口回补」的门槛：库里已有这么多天就不再重压上游
MIN_SHARE_DAYS = 300
MIN_ETF_DAYS = 100

# 串行锁：bootstrap（首启补数）与 loop（周期刷新）是两个线程，
# SQLite 在**同进程内**多连接并发读写时可能偶发
# 「attempt to write a readonly database」（见 db.retry_write 的注释）。
# 这里再加一道串行锁，让两段抓取工作不同时压库；
# db 层的重试作为第二道保险（HTTP 读线程仍会与写线程并发）。
_WORK_LOCK = threading.RLock()


def _log(msg):
    # 写 **stderr** 而不是 stdout：combo_app 的 /__diag 只捕获子进程的 stderr，
    # 写 stdout 的话线上完全看不到自动补数/刷新的进度，出问题无从查起。
    print("[auto-refresh] %s" % msg, file=sys.stderr, flush=True)


# 运行时状态：供 /api/status 暴露「单应用自动补数是否在跑、跑到哪一步了」。
# 只在内存里，不落库（多进程/多容器部署时各算各的）。
STATE = {
    "enabled": False,
    "started_at": "",
    "phase": "",          # bootstrap / idle
    "last_seed": "",      # 最近一次种子恢复摘要
    "last_sync": "",      # 最近一次当日全量摘要
    "last_sample": "",    # 最近一次盘中采样摘要
    "last_error": "",     # 最近一次失败摘要（成功即清空）
}


def _state(**kw):
    STATE.update(kw)
    return STATE


def _log_exc(what, e):
    """失败时把完整栈打出来——线上只能看 stdout，缺栈很难定位。"""
    import traceback
    _log("%s失败（不影响其余步骤）：%s: %s" % (what, type(e).__name__, e))
    try:
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        for line in tb.strip().splitlines():
            _log("  | %s" % line)
    except Exception:  # noqa: BLE001
        pass
    try:
        _state(last_error="%s: %s" % (what, e))
    except Exception:  # noqa: BLE001
        pass


def enabled() -> bool:
    """是否启用单应用形态的自动补数（默认关闭）。"""
    return (os.environ.get("NT_AUTO_REFRESH") or "").strip().lower() in (
        "1", "true", "yes", "on")


def _recent_trading_days(n: int, end=None) -> list:
    """从 end 往回数 n 个交易日（升序）。"""
    end = end or bj_now().date()
    out, d = [], end
    floor = end - datetime.timedelta(days=400)
    while len(out) < n and d > floor:
        if fetcher.is_trading_day(d):
            out.append(d.strftime("%Y-%m-%d"))
        d -= datetime.timedelta(days=1)
    return sorted(out)


def _restore_seed():
    """从内置 docs/data.json 按日期增量恢复历史（本地文件，无需联网）。"""
    try:
        import staticsite
        res = staticsite.restore_missing_from_days_file()
        summary = "明细 %d 天 / 份额 %d 天 / 新闻 %d 天 / 逐笔 %d 天" % (
            res["days"], res["shares"], res["news"], res["ticks"])
        if any(res.values()):
            _log("种子恢复：%s" % summary)
        else:
            _log("种子恢复：库内已完整，无需补齐")
        _state(last_seed=summary, last_error="")
        return res
    except Exception as e:  # noqa: BLE001
        _log_exc("种子恢复", e)
        return None


def _sync_shares():
    """份额全量回补：趋势图的一级净申购线与日期下拉需要长区间份额。

    只有库里份额天数不足 MIN_SHARE_DAYS 时才跑（避免每次重启都重压上游）。
    """
    have = len(db.get_share_dates())
    if have >= MIN_SHARE_DAYS:
        _log("份额已有 %d 天，跳过全量回补" % have)
        return have
    try:
        sdays, hit, n = fetcher.sync_shares(days=SHARE_SPAN)
        _log("份额回补：%d 个交易日 / %d 只基金 / %d 行" % (sdays, hit, n))
        return sdays
    except Exception as e:  # noqa: BLE001
        _log_exc("份额回补", e)
        return have


def _backfill_secondary():
    """二级资金流历史回补（最近 BACKFILL_SPAN 个交易日）。

    库里已有足够天数则跳过——种子恢复已经补过 90 天，不必每次重启都重压上游。
    """
    have = len(db.get_dates())
    if have >= MIN_ETF_DAYS:
        _log("二级明细已有 %d 天，跳过历史回补" % have)
        return 0
    try:
        dates = _recent_trading_days(BACKFILL_SPAN)
        if not dates:
            return 0
        res = fetcher.backfill_range(dates)
        _log("二级回补：%d 天 / %d 行（无资金流 %d 天）"
             % (res.get("days", 0), res.get("rows", 0), len(res.get("no_flow") or [])))
        return res.get("rows", 0)
    except Exception as e:  # noqa: BLE001
        _log_exc("二级回补", e)
        return 0


def _fetch_today():
    """当日行情 + 新闻（口径与 Actions 17:00 那次完全一致）。"""
    try:
        res = fetcher.fetch_and_save()
        _log("当日同步：%s" % res)
        return res
    except Exception as e:  # noqa: BLE001
        _log_exc("当日同步", e)
        return None


def _archive_intraday(day: str):
    """归档分时并重算护盘/压盘信号（腾讯分时源，与主流程独立）。

    腾讯 day 接口一次给近 5 个交易日的分时，所以这一调会补齐最近几天，
    不只是 day 这一天；日期归属一律以接口自带日期为准。
    """
    try:
        n, funds, ds_list = intraday.archive(day, backup=False)
        sig = 0
        for ds in ds_list:
            sig += len(intraday.detect(ds))
        _log("分时归档：%d 行 / %d 只 / %d 天；信号 %d 条" % (n, funds, len(ds_list), sig))
        return n
    except Exception as e:  # noqa: BLE001
        _log_exc("分时归档", e)
        return 0


def _archive_tick(day: str):
    """归档逐笔大单并识别集中扫货/抛压（东财滚动明细）。

    ⚠️ 东财 details 接口是**滚动窗口**（始终返回当前滚动区间，没有日期参数），
    因此只能归档到「今天」，绝不能按历史日期标签反复写入——那会造成
    自我强化的污染（v2.6.8 修过同类 bug）。非交易日直接跳过。
    """
    if not fetcher.is_trading_day(day):
        _log("非交易日（%s），跳过逐笔归档" % day)
        return 0
    tn = 0
    try:
        tn, tf = intraday.archive_tick(day)
        ev = intraday.detect_tick(day)
        _log("逐笔归档：%d 行 / %d 只；集中事件 %d 个" % (tn, tf, len(ev or [])))
    except Exception as e:  # noqa: BLE001
        _log_exc("逐笔归档", e)
    # 兜底（2026-11-02）：本地抓不到大单（云端/机房 IP 被东财 push2 限流、或盘后
    # 滚动窗口冻结）→ 该日期库里 0 行 → 从公开快照（GitHub Pages data.json）借同日的
    # tick_rows，让 WorkBuddy 也能显示 GitHub 已验证的大单。仅本地为空才借，绝不
    # 覆盖真实抓取值；网络异常静默降级。
    if tn == 0:
        try:
            fb = fetcher._apply_tick_fallback(day)
            if fb:
                _log("逐笔兜底：从公开快照借入 %d 行（%s）" % (fb, day))
        except Exception as e:  # noqa: BLE001
            _log_exc("逐笔兜底", e)
    return tn


def bootstrap():
    """一次性补数：把线上库从「只有当天」补成「有完整历史 + 当日实时」。

    顺序按「本地文件优先、联网在后」排列：种子恢复不依赖网络，
    面板能立刻拿到趋势图/份额列/新闻；联网部分再逐步补上二级历史与当日分时。
    """
    _log("开始首启补数 …")
    _ensure_shanghai_tz()
    _state(phase="bootstrap", started_at=bj_now().strftime("%Y-%m-%d %H:%M:%S"))
    t0 = time.time()
    with _WORK_LOCK:
        _restore_seed()
        _sync_shares()
        _backfill_secondary()
        _fetch_today()
        today = bj_now().strftime("%Y-%m-%d")
        _archive_intraday(today)
        _archive_tick(today)
    _log("首启补数完成，耗时 %.1fs" % (time.time() - t0))
    _state(phase="idle")


def _full_sync(day: str):
    """收盘后的当日全量：行情 + 新闻 + 分时 + 信号 + 逐笔。"""
    with _WORK_LOCK:
        _fetch_today()
        _archive_intraday(day)
        _archive_tick(day)
    _state(last_sync="%s 当日全量" % day, last_error="")


def _intraday_sample(day: str):
    """盘中轻量采样：只补分时与逐笔大单（多次采样累积全天分布）。"""
    with _WORK_LOCK:
        _archive_intraday(day)
        _archive_tick(day)
    _state(last_sample="%s 盘中采样" % day, last_error="")


def loop():
    """周期刷新主循环。

    - 交易日 09:30–15:00：每 30 分钟采样一次分时 + 逐笔大单；
      （东财逐笔是**滚动窗口**，盘中多次采样才能累积出全天大单分布，
        盘后再采只剩尾盘快照）
    - 交易日 15:05 之后：当天跑一次全量同步（等价 Actions 17:00 那次）；
    - 其余时段：每 2 小时探一次，当天库里没数据才补一次。
    """
    last_full = None      # 最近一次「当日全量」的日期
    last_sample = None    # 最近一次「盘中采样」的 (日期, 半点桶)
    _ensure_shanghai_tz()
    while True:
        try:
            now = bj_now()
            today = now.strftime("%Y-%m-%d")
            hm = now.hour * 60 + now.minute
            trading = fetcher.is_trading_day(now.date())

            if trading and hm >= 15 * 60 + 5:
                if last_full != today:
                    _log("收盘后全量同步（%s）" % today)
                    _full_sync(today)
                    last_full = today
            elif trading and 9 * 60 + 30 <= hm <= 15 * 60:
                bucket = (today, hm // 30)
                if last_sample != bucket:
                    _intraday_sample(today)
                    last_sample = bucket
            else:
                # 非交易时段：若当天库里还没有数据（首次部署在开盘前就起来），
                # 补一次即可；其余时间纯待机。
                if trading and last_full != today and today not in set(db.get_dates()):
                    _log("非交易时段补当日数据（%s）" % today)
                    _full_sync(today)
                    last_full = today

            time.sleep(120)
        except Exception as e:  # noqa: BLE001
            _log_exc("刷新循环异常（已忽略，继续）", e)
            time.sleep(60)


def start():
    """启动首启补数 + 周期刷新（各一个后台线程）。"""
    import threading

    _state(enabled=True, started_at=bj_now().strftime("%Y-%m-%d %H:%M:%S"))

    def _boot():
        try:
            bootstrap()
        except Exception as e:  # noqa: BLE001
            _log_exc("首启补数异常（已忽略）", e)

    t = threading.Thread(target=_boot, daemon=True)
    t.start()
    t2 = threading.Thread(target=loop, daemon=True)
    t2.start()
    return t, t2


if __name__ == "__main__":
    print("enabled=%s" % enabled())
    bootstrap()
