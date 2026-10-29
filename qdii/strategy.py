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
            reasons.append("现货昨收跌 %.2f%%，跟跌加仓 +%.1f 倍" % (spot_chg, added))
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


def build_contexts(date: str = None, path: str = C.QDII_DB_PATH):
    date = date or datetime.date.today().isoformat()
    pool = db.get_fund_pool(path)
    limit_map = {r["code"]: r for r in fetcher_limit.get_latest_limit_status(path=path)}
    us_map = {r["symbol"]: r for r in fetcher_signal.get_latest_us_signal(path=path)}
    spx = us_map.get("SPX", {})
    ndx = us_map.get("NDX", {})
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
