"""drawdown/daily.py — 回撤全景每日增量入口（离线脚本，跑完即退出）。

供两种调度调用：
- VPS：national_team/scheduler.py 的 dd 定时任务（cron 容器有网络）；
- 手动：python -m drawdown.daily。

流程：种子恢复（空库时）→ 已有标的新增日线 → 重算指标 → QDII 净值增量。
与 WorkBuddy 的 auto_refresh._dd_incremental() 同源（同一个 updater），
区别仅在本脚本独立进程、自带退出码（供 scheduler 判定成功/失败）。
"""
from . import seed
from . import updater


def main():
    seed.ensure()
    res = updater.incremental()
    changed = res.get("_changed") or []
    print("[dd-daily] 日线增量：%d 标的变更%s" % (
        len(changed), ("（" + ", ".join(changed[:8]) + "）") if changed else "（无新数据）"))
    nav = updater.fetch_nav()
    nav_n = sum(1 for v in nav.values() if v)
    print("[dd-daily] 净值增量：%d 只更新" % nav_n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
