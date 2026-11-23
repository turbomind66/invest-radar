"""drawdown/updater.py — 每日增量更新（挂 NT 子进程的 auto_refresh，零新增进程）。

职责：
- 对库内**已有数据**的标的，从 latest_date 起抓新增日线（当天收盘会被覆盖为最新值）；
- 只有真正出现 d > latest_date 的新行才 rebuild 指标，没新数据不空转；
- 串行 + 间隔，单标的失败只留空 + 记日志，绝不写假数据、绝不阻断其他标的；
- 更新完写 dd_meta.last_fetch（与全历史回补共用水位标记）。

设计约束（2026-11-21）：
- 三种原部署（Windows/Linux/VPS）的 auto_refresh 默认关闭，本模块不会被触发；
  只有 WorkBuddy 单应用（combo_app 注入 NT_AUTO_REFRESH=1）才跑，入口/端口/
  配置/定时职责一行不改。
- 只抓已有标的：从未回补过的标的留给离线 `python -m drawdown.backfill`，
  避免自动流程误触发 39 标的全历史拉取。
"""
import os
import time

from . import constants as C
from . import db
from . import engine
from . import fetcher


def _ok(msg, log):
    log("[dd-update] %s" % msg)


def fetch_nav(force=False, only=None, log=print, pause=0.12):
    """抓 6 只可还原 QDII 场内 ETF 的历史净值入库。

    force=False 从库内最新日期增量抓（日常）；force=True 全历史（首次回补）。
    串行翻页，单只失败留空不造假。返回 {code: 行数}。
    """
    db.init_db()
    result = {}
    for r in C.RECONSTRUCT:
        code = r["nav_code"]
        if only and code not in only:
            continue
        since = None if force else db.nav_latest_date(code)
        try:
            rows = fetcher.fetch_fund_nav_history(code, pause=pause, since=since)
        except Exception as e:  # noqa: BLE001
            _ok("净值 %s(%s) 抓取异常：%s: %s" % (code, r["name"], type(e).__name__, e), log)
            result[code] = 0
            continue
        if not rows:
            _ok("净值 %s(%s) 无数据，留空待重试" % (code, r["name"]), log)
            result[code] = 0
            continue
        db.save_nav(code, rows)
        result[code] = len(rows)
        _ok("净值 %s(%s) %d 根 %s~%s" % (code, r["name"], len(rows),
                                         rows[0][0], rows[-1][0]), log)
    return result


def refresh(nav=True, log=print):
    """一站式更新：种子恢复（空库）→ 已有标的日线增量 → QDII 净值增量。

    供服务端「🔄 更新数据」按钮与本地自动增量共用；容器里的 daily.py、
    WorkBuddy 的 auto_refresh._dd_incremental 仍走各自原路径（本函数只是把
    同一批 updater 串起来，口径完全一致，不新增抓取源）。

    单路失败只记日志、绝不写假数据，也不阻断其余步骤。
    返回摘要：{"changed": [...], "changed_n": n, "rows": n, "nav": n, "cost": 秒}
    """
    t0 = time.time()
    try:                          # 空库（首次本地起服务 / 新容器）先恢复全历史
        from . import seed as _seed
        _seed.ensure(log=log)
    except Exception as e:  # noqa: BLE001
        _ok("种子恢复跳过：%s: %s" % (type(e).__name__, e), log)
    res = incremental(log=log)
    changed = res.get("_changed") or []
    rows = sum(v for k, v in res.items()
               if k != "_changed" and isinstance(v, int))
    nav_n = 0
    if nav:
        try:
            nav_res = fetch_nav(log=log)
            nav_n = sum(1 for v in nav_res.values() if v)
        except Exception as e:  # noqa: BLE001
            _ok("净值增量失败：%s: %s" % (type(e).__name__, e), log)
    out = {"changed": changed, "changed_n": len(changed), "rows": rows,
           "nav": nav_n, "cost": round(time.time() - t0, 1)}
    # 与 last_fetch 分开：last_fetch 只在真有新增日线时更新（水位标记，
    # 供 backfill/断点续传判断），0 新增时前端会以为「没点成功」。
    # last_check 每次调用都写，页面据此显示「最近检查时间」。
    try:
        db.set_meta("last_check", time.strftime("%Y-%m-%d %H:%M:%S"))
    except Exception:  # noqa: BLE001
        pass
    _ok("本次更新完成：%d 标的 / %d 根日线 / 净值 %d 只，耗时 %.1fs"
        % (out["changed_n"], rows, nav_n, out["cost"]), log)
    return out


def incremental(path=db.DB_PATH, sleep=0.4, only=None, log=print):
    """增量抓取 + 重算。返回 {id: 新增行数}（含 0 行的失败标的）。

    非交易日/源未更新时新行为 0，指标不重算，幂等安全。
    """
    db.init_db(path)
    avail = db.available_assets(path)
    targets = [a for a in C.ALL
               if a["id"] in avail and (not only or a["id"] in only)]
    result = {}
    changed = []
    for a in targets:
        aid = a["id"]
        since = db.latest_date(aid, path)
        if not since:
            continue
        try:
            rows = fetcher.fetch_asset(a, since=since)
        except Exception as e:  # noqa: BLE001
            _ok("%s(%s) 抓取异常：%s: %s" % (aid, a["name"], type(e).__name__, e), log)
            result[aid] = 0
            continue
        fresh = [(d, c) for d, c in rows if d > since] if rows else []
        if not fresh:
            result[aid] = 0
            continue
        try:
            db.upsert_daily(aid, fresh, path, source=a.get("src", "auto"))
            engine.rebuild_asset(aid, path)
            result[aid] = len(fresh)
            changed.append(aid)
            _ok("%s(%s) +%d 根 %s~%s" % (aid, a["name"], len(fresh),
                                         fresh[0][0], fresh[-1][0]), log)
        except Exception as e:  # noqa: BLE001
            _ok("%s(%s) 入库/重算失败：%s: %s" % (aid, a["name"], type(e).__name__, e), log)
            result[aid] = 0
        if sleep:
            time.sleep(sleep)
    result["_changed"] = changed
    if changed:
        db.set_meta("last_fetch", time.strftime("%Y-%m-%d %H:%M:%S"), path)
    return result


if __name__ == "__main__":
    import sys
    only = [a for a in sys.argv[1:] if not a.startswith("--")] or None
    res = incremental(only=only)
    changed = res.pop("_changed", [])
    print("[dd-update] 更新 %d 标的，变更 %d：%s"
          % (len(res), len(changed), ", ".join(changed) or "无新数据"))
