"""qdii/strategy.py — 模块F：定投决策引擎。

采纳的策略组合（用户确认）：
- 骨架 = 固定周期 + 信号增强：固定每周投基准额，用信号微调当期额度。
- 加仓量依据 = 跟跌加仓 + 估值百分位：跌越多/估值越低 → 倍数越高。
- 防御开关 = 避险情绪：美债避险信号极端时 → 临时减额。

输入：基金申购状态/限额 + 美股信号(现货昨收/期货实时/美债/VIX)
      + 估值百分位 + 跟踪误差。
输出：每只基金「建议投入倍数 + 理由」。
"""
import os
import sys
import json
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
for p in (BASE, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

import db
import constants as C
import fetcher_limit
import fetcher_signal
import fetcher_premium
import tracking_error


def recommend(ctx):
    """ctx 含: code, sub_status, limit_amount, spx_chg, ndx_chg, vix, ust_chg, valuation_pct。
    返回 {'multiple': float, 'reasons': [str]}。
    """
    P = C.STRATEGY_PARAMS
    reasons = []
    sub = ctx.get("sub_status")
    limit_amount = ctx.get("limit_amount") or 0

    # 1. 能否买（暂停申购直接归零）
    if sub == "SUSPEND":
        return {"multiple": 0.0, "reasons": ["暂停申购，无法买入"]}

    multiple = 1.0

    # 2. 现货昨收方向（标普/纳指简单平均；即本次申购将成交的净值方向）
    spx_chg = ctx.get("spx_chg") or 0.0
    ndx_chg = ctx.get("ndx_chg") or 0.0
    spot_chg = (spx_chg + ndx_chg) / 2.0
    if spot_chg < 0:
        added = 0.0
        for thr, add in P["drop_tiers"]:
            if spot_chg <= thr:
                added = max(added, add)
        if added > 0:
            multiple += added
            reasons.append("现货昨收跌 %.2f%%，跟跌加仓 +%.1f 倍" % (abs(spot_chg), added))
    else:
        reasons.append("现货昨收涨 %.2f%%（非加仓区间）" % spot_chg)

    # 3. 估值百分位（低估加 / 高估减）
    vp = ctx.get("valuation_pct")
    if vp is not None:
        if vp < P["valuation_low"]:
            multiple += 0.5
            reasons.append("估值百分位 %.0f%% 偏低，+0.5" % vp)
        elif vp > P["valuation_high"]:
            multiple -= 0.5
            reasons.append("估值百分位 %.0f%% 偏高，-0.5" % vp)

    # 4. 避险情绪防御开关
    vix = ctx.get("vix") or 0.0
    ust_chg = ctx.get("ust_chg") or 0.0
    if vix > P["risk_off_vix"] or ust_chg > P["risk_off_ust_up"]:
        multiple *= 0.5
        reasons.append("避险情绪升温(VIX=%.1f / 美债上行 %.2f%%)→防御 ×0.5"
                       % (vix, ust_chg))

    # 5. 限额封顶（限大额时按限额/基准额封顶）
    if sub == "LIMIT" and limit_amount > 0:
        cap = limit_amount / P["base_amount"]
        if multiple > cap:
            reasons.append("限大额 %.0f 元 → 倍数封顶 %.1f" % (limit_amount, cap))
            multiple = cap

    # 6. 上下限裁剪
    multiple = max(P["multiple_floor"], min(P["multiple_cap"], multiple))
    reasons.append("基准额 %.0f 元 × 倍数 %.2f = 建议投入 %.0f 元"
                   % (P["base_amount"], multiple, P["base_amount"] * multiple))
    return {"multiple": round(multiple, 2), "reasons": reasons}


# ==================== 大类维度决策（2026-10-29 起为主口径） ====================
# 用户口径：不再逐只基金给建议（同一大类下各家基金同涨同跌，差别只在费率/限额），
# 直接按大类给「建议倍数 + 理由」，也不显示金额（金额随各人预算变，倍数才是结论）。


def us_signal_map(date: str = None, path: str = C.QDII_DB_PATH):
    return {r["symbol"]: r for r in fetcher_signal.get_latest_us_signal(path=path)}


def pick_signal(cat, us_map):
    """取该大类用于判断的指数信号 -> (symbol, 显示名, 涨跌幅) ，取不到则 (None, None, None)。"""
    for sym in C.CATEGORY_SIGNAL.get(cat, []):
        row = us_map.get(sym)
        if row and row.get("change_pct") is not None:
            return sym, (row.get("name") or sym), float(row["change_pct"])
    return None, None, None


def category_stats(pool, limit_map):
    """按大类统计申购状态。"""
    stats = {}
    for f in pool:
        cat = f["category"]
        s = stats.setdefault(cat, dict(total=0, open=0, limit=0, suspend=0,
                                       min_limit=None, companies=[]))
        s["total"] += 1
        if f.get("company") and f["company"] not in s["companies"]:
            s["companies"].append(f["company"])
        lm = limit_map.get(f["code"], {})
        st = lm.get("sub_status")
        if st == "SUSPEND":
            s["suspend"] += 1
        elif st == "LIMIT":
            s["limit"] += 1
            la = lm.get("limit_amount") or 0
            if la and la < 1e9:
                s["min_limit"] = la if s["min_limit"] is None else min(s["min_limit"], la)
        else:
            # 未抓到状态（接口缺失）按可买处理，理由里会标注
            s["open"] += 1
    return stats


def _status_text(s):
    """状态文案：直接回答「能不能买 / 能买几只 / 额度多少」。"""
    total = s["total"]
    if not total:
        return "—"
    if s["suspend"] == total:
        return "全部暂停"
    avail = s["open"] + s["limit"]
    parts = ["可买 %d/%d" % (avail, total)]
    if s["limit"] and s["min_limit"] is not None:
        parts.append("限额 %.0f 元" % s["min_limit"])
    if s["suspend"]:
        parts.append("暂停 %d" % s["suspend"])
    return " · ".join(parts)


def recommend_category(cat, stats, us_map, valuation_pct=None):
    """大类级建议：返回 {'multiple','status_text','reasons'}。"""
    P = C.STRATEGY_PARAMS
    st = _status_text(stats)
    if stats["total"] and stats["suspend"] == stats["total"]:
        return dict(multiple=0.0, status_text=st,
                    reasons=["该大类 %d 只全部暂停申购，本期无法买入" % stats["total"]])

    reasons = []
    multiple = 1.0

    # 1) 方向：跟跌加仓（用该大类自己的指数；日经/DAX 无本地信号则借鉴美股）
    sym, sname, chg = pick_signal(cat, us_map)
    if chg is None:
        reasons.append("暂无对应指数信号，按基准倍数")
    else:
        label = sname or cat
        # 日经 / 德国DAX 没有本地信号，借美股风向时写明，避免误以为有本地数据
        if C.CATEGORY_OWN_SIGNAL.get(cat) != sym:
            label += "（无本地指数信号，参考美股风向）"
        if chg < 0:
            added = 0.0
            hit = None
            for thr, add in P["drop_tiers"]:
                if chg <= thr:
                    added = max(added, add)
                    hit = thr
            if added > 0:
                multiple += added
                reasons.append("%s昨收跌 %.2f%%（触发跌%.0f%%档），跟跌加仓 +%.1f 倍"
                               % (label, abs(chg), -hit, added))
            else:
                reasons.append("%s昨收跌 %.2f%%，跌幅未到加仓档，维持基准" % (label, abs(chg)))
        else:
            reasons.append("%s昨收涨 %.2f%%，非加仓区间" % (label, chg))

    # 2) 估值：只有标普/纳指有维护值
    if valuation_pct is not None:
        if valuation_pct < P["valuation_low"]:
            multiple += 0.5
            reasons.append("PE 分位 %.0f%% 偏低（低估），+0.5 倍" % valuation_pct)
        elif valuation_pct > P["valuation_high"]:
            multiple -= 0.5
            reasons.append("PE 分位 %.0f%% 偏高（高估），-0.5 倍" % valuation_pct)
        else:
            reasons.append("PE 分位 %.0f%%，估值中性" % valuation_pct)

    # 3) 避险开关（VIX / 美债）
    vix_row = us_map.get("VIX") or {}
    ust_row = us_map.get("UST10Y") or {}
    vix = vix_row.get("last_price") or 0.0
    ust_chg = ust_row.get("change_pct") or 0.0
    if vix > P["risk_off_vix"] or ust_chg > P["risk_off_ust_up"]:
        multiple *= 0.5
        reasons.append("避险升温（VIX %.1f%s）→ 倍数 ×0.5"
                       % (vix, "，美债10Y 大幅上行" if ust_chg > P["risk_off_ust_up"] else ""))

    # 4) 可买性说明（不封顶倍数，只提示）
    if stats["limit"] and stats["min_limit"] is not None:
        amt = stats["min_limit"]
        if amt < 100:
            reasons.append("%d 只名义开放但单日只给 %.0f 元（等同变相暂停），"
                           "真正能正常买的只有 %d 只"
                           % (stats["limit"], amt, stats["open"]))
        else:
            reasons.append("%d 只限大额（单日 %.0f 元），下单注意额度"
                           % (stats["limit"], amt))
        if stats["suspend"]:
            reasons.append("另 %d 只暂停申购" % stats["suspend"])
    elif stats["suspend"]:
        reasons.append("%d 只开放、%d 只暂停，优先买开放的"
                       % (stats["open"], stats["suspend"]))

    multiple = max(P["multiple_floor"], min(P["multiple_cap"], multiple))
    return dict(multiple=round(multiple, 2), status_text=st, reasons=reasons)


def build_category_decisions(date: str = None, path: str = C.QDII_DB_PATH):
    pool = db.get_fund_pool(path)
    limit_map = {r["code"]: r for r in fetcher_limit.get_latest_limit_status(path=path)}
    us_map = us_signal_map(date, path)
    stats = category_stats(pool, limit_map)
    out = []
    cats = [c for c in C.CATEGORY_ORDER if c in stats]
    cats += [c for c in stats if c not in cats]
    for cat in cats:
        s = stats[cat]
        rec = recommend_category(cat, s, us_map, C.STATIC_VALUATION_PCT.get(cat))
        out.append(dict(
            category=cat,
            multiple=rec["multiple"],
            status_text=rec["status_text"],
            reasons=rec["reasons"],
            total=s["total"], open_cnt=s["open"],
            suspend_cnt=s["suspend"], limit_cnt=s["limit"],
            companies=s["companies"],
        ))
    return out


def run_category_decision(date: str = None, path: str = C.QDII_DB_PATH):
    date = date or datetime.date.today().isoformat()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    P = C.STRATEGY_PARAMS
    rows = build_category_decisions(date, path)
    conn = db.get_conn(path)
    for r in rows:
        db.upsert_row(conn, "qdii_decision_cat",
                      ["date", "category", "strategy", "suggested_multiple",
                       "status_text", "reason_json", "total", "open_cnt",
                       "suspend_cnt", "limit_cnt"],
                      [date, r["category"], P["mode"], r["multiple"],
                       r["status_text"], json.dumps(r["reasons"], ensure_ascii=False),
                       r["total"], r["open_cnt"], r["suspend_cnt"], r["limit_cnt"]],
                      "date,category")
    conn.commit()
    conn.close()
    db.set_meta("last_decision_cat", now, path)
    return len(rows)


def get_latest_category_decisions(path: str = C.QDII_DB_PATH):
    conn = db.get_conn(path)
    drow = conn.execute(
        "SELECT date FROM qdii_decision_cat ORDER BY date DESC LIMIT 1").fetchone()
    if not drow:
        conn.close()
        return []
    rows = conn.execute(
        "SELECT * FROM qdii_decision_cat WHERE date=? ORDER BY id", (drow["date"],)
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["reasons"] = json.loads(d["reason_json"])
        except Exception:  # noqa: BLE001
            d["reasons"] = []
        out.append(d)
    return out


def build_contexts(date: str = None, path: str = C.QDII_DB_PATH):
    date = date or datetime.date.today().isoformat()
    pool = db.get_fund_pool(path)
    limit_map = {r["code"]: r for r in fetcher_limit.get_latest_limit_status(path=path)}
    us_map = {r["symbol"]: r for r in fetcher_signal.get_latest_us_signal(path=path)}
    spx = us_map.get("SPX", {})
    # 纳指：优先综合指数 IXIC（2026-10-29 起口径），旧数据回退 NDX
    ndx = us_map.get("IXIC") or us_map.get("NDX") or {}
    vix_row = us_map.get("VIX", {})
    ust = us_map.get("UST10Y", {})
    ctxs = []
    for f in pool:
        lm = limit_map.get(f["code"], {})
        ctxs.append(dict(
            code=f["code"],
            sub_status=lm.get("sub_status"),
            limit_amount=lm.get("limit_amount"),
            spx_chg=spx.get("change_pct"),
            ndx_chg=ndx.get("change_pct"),
            vix=vix_row.get("last_price"),
            ust_chg=ust.get("change_pct"),
            valuation_pct=C.STATIC_VALUATION_PCT.get(f["category"]),
        ))
    return ctxs


def run_daily_decision(date: str = None, path: str = C.QDII_DB_PATH):
    date = date or datetime.date.today().isoformat()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    P = C.STRATEGY_PARAMS
    ctxs = build_contexts(date, path)
    conn = db.get_conn(path)
    cnt = 0
    for ctx in ctxs:
        rec = recommend(ctx)
        db.upsert_row(conn, "qdii_decision",
                      ["date", "code", "strategy", "suggested_multiple",
                       "reason_json"],
                      [date, ctx["code"], P["mode"], rec["multiple"],
                       json.dumps(rec["reasons"], ensure_ascii=False)],
                      "date,code")
        cnt += 1
    conn.commit()
    conn.close()
    db.set_meta("last_decision", now, path)
    return cnt


def get_latest_decision(path: str = C.QDII_DB_PATH):
    conn = db.get_conn(path)
    drow = conn.execute(
        "SELECT date FROM qdii_decision ORDER BY date DESC LIMIT 1").fetchone()
    if not drow:
        conn.close()
        return []
    rows = conn.execute(
        "SELECT * FROM qdii_decision WHERE date=?", (drow["date"],)).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["reasons"] = json.loads(d["reason_json"])
        except Exception:  # noqa: BLE001
            d["reasons"] = []
        out.append(d)
    return out


if __name__ == "__main__":
    db.init_db()
    n = run_daily_decision()
    print("decision for %d funds" % n)
    for r in get_latest_decision():
        print("  %s ×%.2f" % (r["code"], r["suggested_multiple"]))
