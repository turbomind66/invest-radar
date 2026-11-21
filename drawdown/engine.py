"""drawdown/engine.py — 回撤计算引擎（纯函数 + 预计算入库）。

口径（2026-11-20 定）：
- peak_t = max(close[0..t])（历史最高**收盘**价，不用盘中最高，避免毛刺）
- dd_pct = (close_t / peak_t - 1) * 100  （<=0）
- 修复 = 收盘重新站上前期峰值
- 事件段 = 一次「创新高 → 回撤 → 见谷 → 重回前高」的完整过程；
  未修复的记为 ongoing，谷值随新低滚动更新。
所有结果预计算入库，请求时只读不重算（1核2G 关键）。
"""
import datetime

from . import constants as C
from . import db


def compute_metrics(rows):
    """rows=[(date, close)] 升序 → metrics 行列表 + events 列表。

    metrics 行：(date, close, peak, peak_date, dd_pct, days_since_peak, state)
    state: new_high / drawdown / recovering / recovered
    """
    metrics = []
    events = []
    if not rows:
        return metrics, events
    peak = -1.0
    peak_date = None
    peak_idx = -1
    # 当前未完成的回撤段
    open_seg = None  # dict(peak_date, peak, trough_date, trough, start_idx)

    for i, (date, close) in enumerate(rows):
        if close <= 0:
            continue
        if close > peak:
            # 创新高：之前有未修复段 → 价格已重新站上峰值，用当天封口（已修复）
            if open_seg is not None:
                events.append(_close_segment(open_seg, date, rows))
                open_seg = None
            peak = close
            peak_date = date
            peak_idx = i
            state = "new_high"
            dd_pct = 0.0
        else:
            dd_pct = round((close / peak - 1.0) * 100.0, 4)
            if open_seg is None:
                open_seg = dict(peak_date=peak_date, peak=peak,
                                trough_date=date, trough=close, start_idx=peak_idx)
            elif close < open_seg["trough"]:
                open_seg["trough_date"] = date
                open_seg["trough"] = close
            state = "drawdown"

        days_since_peak = (datetime.date.fromisoformat(date)
                           - datetime.date.fromisoformat(peak_date)).days
        metrics.append((date, close, peak, peak_date, dd_pct, days_since_peak, state))

    if open_seg is not None:
        open_seg["end_date"] = rows[-1][0]
        events.append(_close_segment(open_seg, None, rows))
    return metrics, events


def _close_segment(seg, recover_date, rows):
    """把一段峰谷转为事件行（供 dd_events 表）。"""
    drop = round((seg["trough"] / seg["peak"] - 1.0) * 100.0, 4) if seg["peak"] else None
    recover_days = None
    ongoing = 1
    if recover_date:
        ongoing = 0
        try:
            recover_days = (datetime.date.fromisoformat(recover_date)
                            - datetime.date.fromisoformat(seg["peak_date"])).days
        except ValueError:
            recover_days = None
    # 未修复段的「修复天数」按数据末端算（供展示参考，ongoing=1 区分）
    if ongoing and seg.get("end_date"):
        try:
            recover_days = (datetime.date.fromisoformat(seg["end_date"])
                            - datetime.date.fromisoformat(seg["peak_date"])).days
        except (ValueError, TypeError):
            pass
    return (seg["peak_date"], seg["trough_date"], recover_date,
            seg["peak"], seg["trough"], drop, recover_days, ongoing)


def compute_episodes(rows, min_drop=10.0):
    """按阈值识别「峰 → 谷 → 收复前峰」的独立波段（zigzag）。

    为什么不复用 compute_metrics 的段：
    metrics/dd_pct 始终相对**历史最高收盘**，这是页面的核心口径（免责声明也这么写），
    但它会把「高点一直没被收复」期间的若干次大跌并成一段 —— 例如沪深300 从 2007-10
    的 5891 点到 2021 年才重回前高，中间 2015(-43%)、2018(-32%)、2021(-40%) 全部
    被吞进同一段，事件列表看起来只有寥寥几条。

    episodes 反过来只关心「这一波跌了多少、后来回升了多少」：
    - 跌破前高 min_drop% 即确认一个峰，从谷底回升 min_drop% 即确认一个谷；
    - 因此每个大波段都是独立事件，涨跌两侧都能标注。
    该结果只用于「事件标注 / 回撤事件列表」，不改动 dd_pct 与今日信号的既有口径。

    返回 [{peak_date, peak, trough_date, trough, drop_pct, days_down,
           recover_date, recover_days, ongoing, rebound_pct, rebound_date}]
    """
    eps = []
    n = len(rows)
    if n < 2:
        return eps
    peak_c, peak_d, peak_i = rows[0][1], rows[0][0], 0
    if not peak_c or peak_c <= 0:
        return eps
    phase = "up"
    trough_c = trough_d = None
    trough_i = -1
    for i in range(1, n):
        d, c = rows[i]
        if c is None or c <= 0:
            continue
        if phase == "up":
            if c > peak_c:
                peak_c, peak_d, peak_i = c, d, i
            elif c <= peak_c * (1.0 - min_drop / 100.0):
                phase = "down"
                trough_c, trough_d, trough_i = c, d, i
        else:
            if c < trough_c:
                trough_c, trough_d, trough_i = c, d, i
            elif c >= trough_c * (1.0 + min_drop / 100.0):
                eps.append(dict(peak_date=peak_d, peak=peak_c,
                                trough_date=trough_d, trough=trough_c,
                                peak_i=peak_i, trough_i=trough_i))
                phase = "up"
                peak_c, peak_d, peak_i = c, d, i
    if phase == "down":
        eps.append(dict(peak_date=peak_d, peak=peak_c,
                        trough_date=trough_d, trough=trough_c,
                        peak_i=peak_i, trough_i=trough_i))

    # 收复前峰日期 + 反弹幅度（下一浪的峰，或末尾运行中的峰）
    for k, e in enumerate(eps):
        rec_d = rec_i = None
        for j in range(e["trough_i"] + 1, n):
            if rows[j][1] is not None and rows[j][1] >= e["peak"]:
                rec_d, rec_i = rows[j][0], j
                break
        e["drop_pct"] = round((e["trough"] / e["peak"] - 1.0) * 100.0, 2)
        e["days_down"] = e["trough_i"] - e["peak_i"]
        e["recover_date"] = rec_d
        e["ongoing"] = 0 if rec_d else 1
        e["recover_days"] = (rec_i - e["peak_i"]) if rec_i is not None else None
        nxt = eps[k + 1] if k + 1 < len(eps) else None
        rb_d = nxt["peak_date"] if nxt else (peak_d if phase == "up" else None)
        rb_c = nxt["peak"] if nxt else (peak_c if phase == "up" else None)
        if rb_c and e["trough"]:
            e["rebound_pct"] = round((rb_c / e["trough"] - 1.0) * 100.0, 2)
            e["rebound_date"] = rb_d
        else:
            e["rebound_pct"] = None
            e["rebound_date"] = None
    return eps


def _period_key(date, gran):
    d = datetime.date.fromisoformat(date)
    if gran == "day":
        return date
    if gran == "week":
        y, w, _ = d.isocalendar()
        return "%d-W%02d" % (y, w)
    if gran == "month":
        return date[:7]
    if gran == "quarter":
        return "%d-Q%d" % (d.year, (d.month - 1) // 3 + 1)
    if gran == "year":
        return date[:4]
    return date


def aggregate(metrics, gran="day"):
    """按周期聚合：每个周期取期末收盘/期末回撤/区间最深回撤/区间峰值。

    返回明细行列表（dict），供三视图表格展示。
    """
    buckets = {}
    order = []
    for m in metrics:
        key = _period_key(m["date"], gran)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(m)
    out = []
    for key in order:
        arr = buckets[key]
        last = arr[-1]
        deepest = min(arr, key=lambda x: x["dd_pct"] if x["dd_pct"] is not None else 0)
        out.append({
            "period": key,
            "start": arr[0]["date"],
            "end": last["date"],
            "close": last["close"],
            "dd_pct": last["dd_pct"],
            "deepest_pct": deepest["dd_pct"],
            "deepest_date": deepest["date"],
            "peak": last["peak"],
            "peak_date": last["peak_date"],
            "days_since_peak": last["days_since_peak"],
        })
    return out


def signal(metrics, events, asof=None):
    """当日客观信号：当前回撤、历史分位、距高点天数、同类回撤修复统计。

    不含任何「买入/卖出」指令；页面底部统一标注「参考信号，非投资建议」。
    """
    if not metrics:
        return None
    last = metrics[-1]
    dds = sorted([m["dd_pct"] for m in metrics if m["dd_pct"] is not None])
    cur = last["dd_pct"]
    # 分位：当前回撤在历史中有多深（0=最浅，100=最深）
    pct_rank = None
    if dds:
        below = sum(1 for x in dds if x >= cur)  # 回撤更深或相等
        pct_rank = round(below / len(dds) * 100.0, 1)
    # 修复统计：已完成事件的 drop_pct 分档，找跌幅相近者的修复天数中位数
    def _num(e, key):
        v = e.get(key) if isinstance(e, dict) else None
        return v
    past = [e for e in events
            if not _num(e, "ongoing") and _num(e, "recover_days") is not None
            and _num(e, "drop_pct") is not None]
    similar = [abs(_num(e, "drop_pct")) for e in past
               if abs(abs(_num(e, "drop_pct")) - abs(cur or 0)) <= 5]
    recover_median = None
    recover_samples = len(similar)
    if similar:
        past_days = sorted(_num(e, "recover_days") for e in past
                           if _num(e, "drop_pct") is not None
                           and abs(abs(_num(e, "drop_pct")) - abs(cur or 0)) <= 5)
        if past_days:
            recover_median = past_days[len(past_days) // 2]
    return {
        "asof": asof or last["date"],
        "close": last["close"],
        "peak": last["peak"],
        "peak_date": last["peak_date"],
        "dd_pct": cur,
        "dd_rank_pct": pct_rank,
        "days_since_peak": last["days_since_peak"],
        "state": last["state"],
        "recover_days_median": recover_median,
        "recover_samples": recover_samples,
        "total_events": len(events),
        "hint": _hint(cur, pct_rank, last["days_since_peak"]),
    }


def _hint(cur, rank, days):
    """生成客观文字提示（非买卖建议）。"""
    if cur is None:
        return "数据不足，无法评估"
    if rank is not None and rank >= 90:
        return "当前回撤处于历史极值区间（分位 %s%%），距高点 %s 天" % (rank, days)
    if rank is not None and rank >= 60:
        return "当前回撤深于历史多数时期（分位 %s%%），距高点 %s 天" % (rank, days)
    if cur == 0:
        return "处于历史高位附近，距高点 %s 天" % days
    return "当前回撤分位 %s%%，距高点 %s 天" % (rank, days)


def rebuild_asset(asset_id, path=db.DB_PATH, log=print):
    """从 dd_daily 重算某标的 metrics + events 并入库。"""
    rows = db.get_daily(asset_id, path)
    if not rows:
        log("[dd-engine] %s 无日线，跳过" % asset_id)
        return None
    metrics, events = compute_metrics(rows)
    db.save_metrics(asset_id, metrics, path)
    db.save_events(asset_id, events, path)
    log("[dd-engine] %s %d 指标 / %d 事件" % (asset_id, len(metrics), len(events)))
    return {"asset": asset_id, "metrics": len(metrics), "events": len(events)}


def rebuild_all(path=db.DB_PATH, log=print):
    avail = db.available_assets(path)
    out = {}
    for aid in avail:
        r = rebuild_asset(aid, path, log)
        if r:
            out[aid] = r
    db.set_meta("last_rebuild", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), path)
    return out


if __name__ == "__main__":
    import sys
    only = [a for a in sys.argv[1:] if not a.startswith("--")] or None
    if only:
        for a in only:
            rebuild_asset(a)
    else:
        rebuild_all()
