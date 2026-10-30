"""容器内定时调度器（零第三方依赖）。

为什么不用 cron：cron 不继承容器环境变量、需要额外装包、日志也要额外落盘；
这里是纯标准库实现，日志直接进 stdout（`docker compose logs -f cron` 即可看）。

职责：
  - full：跑一次完整同步导出（staticsite.py），等价于 GitHub Actions 的 17:00 / 06:00
  - tick：盘中轻量采样（staticsite.py --mode=tick），只抓当日分时 + 逐笔大单

并发保护：full 与 tick 都会写同一个 SQLite，统一走 common.lock.FileLock
（Linux/macOS fcntl、Windows msvcrt、其余 O_EXCL+PID 兜底）互斥；
2026-10-30 之前这里是本地简化实现，Windows 上「没 fcntl ⇒ 不加锁」，
两个进程可以同时进入任务并抢写 SQLite，现在改回跨平台强互斥。
"""
import datetime
import os
import subprocess
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

STATICSITE = os.path.join(BASE, "staticsite.py")

# 默认时间点（Asia/Shanghai）。可用环境变量覆盖，见 .env.example。
DEFAULT_FULL = "06:00,17:10"                 # 完整同步（收盘后 + 次日清晨重刷）
DEFAULT_TICK = "09:30,10:00,10:30,11:00,11:30,13:00,13:30,14:00,14:30,14:50"  # 盘中逐笔大单采样（交易时段，约每30分钟一次，保留尾盘14:50）

from common.lock import FileLock  # noqa: E402


def _env_bool(key, default="0"):
    return (os.environ.get(key) or default).strip().lower() in ("1", "true", "yes", "on")


def parse_times(raw):
    """把 "06:00,17:10" 解析为 [(6,0),(17,10)]，去重排序；非法片段丢弃。"""
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
    """当前时刻是否命中某个时间点（精确到分钟）。"""
    return (now.hour, now.minute) in set(times or [])


def _log(msg):
    print("[%s] %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg),
          flush=True)


def run_task(mode, lock_path=None, timeout=None):
    """执行一次任务。mode: full | tick。"""
    args = [sys.executable, "-u", STATICSITE]
    if mode == "tick":
        args += ["--mode=tick"]
    lock = FileLock(lock_path or os.path.join(BASE, ".sync.lock"))
    if not lock.acquire():
        _log("跳过 %s：已有同步任务在运行（文件锁占用）" % mode)
        return False
    _log("开始 %s：%s" % (mode, " ".join(args)))
    t0 = time.time()
    try:
        # 2026-10-30 修：旧实现丢弃 subprocess.run 的 returncode，
        # staticsite 崩溃/非零退出仍会被记成「完成」，日志看不出失败。
        proc = subprocess.run(args, cwd=BASE, timeout=timeout)
        cost = time.time() - t0
        if proc.returncode == 0:
            _log("完成 %s，耗时 %.1fs" % (mode, cost))
            return True
        _log("失败 %s：staticsite 退出码 %s，耗时 %.1fs" % (mode, proc.returncode, cost))
        return False
    except subprocess.TimeoutExpired:
        _log("超时 %s：超过 %ss，已终止本次任务" % (mode, timeout))
        return False
    except Exception as e:  # 抓取失败不应让调度器退出
        _log("失败 %s：%s: %s" % (mode, type(e).__name__, e))
        return False
    finally:
        lock.release()


def main():
    import db  # 延迟导入：确保 NT_DB_PATH 已生效后再建库

    full_times = parse_times(os.environ.get("SCHED_FULL_TIMES") or DEFAULT_FULL)
    tick_times = parse_times(os.environ.get("SCHED_TICK_TIMES") or DEFAULT_TICK)
    timeout = int(os.environ.get("SCHED_TIMEOUT") or 1800)
    lock_path = os.environ.get("SCHED_LOCK_PATH") or os.path.join(BASE, ".sync.lock")
    weekday_only = _env_bool("SCHED_FULL_WEEKDAY_ONLY", "1")
    run_on_start = _env_bool("SCHED_RUN_ON_START", "1")

    db.init_db()
    _log("调度器启动 | 时区 %s(%s) | full=%s | tick=%s | 超时 %ss"
         % (os.environ.get("TZ") or "(系统默认)", time.strftime("%Z"),
            ["%02d:%02d" % t for t in full_times],
            ["%02d:%02d" % t for t in tick_times], timeout))

    if run_on_start:
        def _first():
            _log("启动即跑一次完整同步（首次部署可立即填充数据）")
            run_task("full", lock_path, timeout)
            # 盘中（约 09:00–15:10）部署时补跑一次逐笔大单采样（tick），
            # 避免傻等下一个 tick 整点才有首笔数据；盘后东财滚动窗口冻结采不到大单，故不跑。
            now = datetime.datetime.now()
            if (9, 0) <= (now.hour, now.minute) <= (15, 10):
                _log("启动即补跑一次盘中逐笔大单采样（tick）")
                run_task("tick", lock_path, timeout)
        threading.Thread(target=_first, daemon=True).start()

    done = set()  # 防重：同一天同一分钟同一任务只跑一次
    while True:
        now = datetime.datetime.now()
        today = now.strftime("%Y-%m-%d")
        # 清理过期的防重记录
        for k in [k for k in done if not k.startswith(today)]:
            done.discard(k)

        jobs = [(t, "full") for t in full_times]
        jobs += [(t, "tick") for t in tick_times]
        for (h, m), mode in jobs:
            if not due(now, [(h, m)]):
                continue
            if mode == "full" and weekday_only and now.weekday() >= 5:
                continue  # 周末不跑全量（tick 内部自判交易日）
            key = "%s %02d:%02d %s" % (today, h, m, mode)
            if key in done:
                continue
            done.add(key)
            run_task(mode, lock_path, timeout)

        time.sleep(20)  # 20 秒轮询：既不会错过分钟窗口，也不会空转烧 CPU


if __name__ == "__main__":
    main()
