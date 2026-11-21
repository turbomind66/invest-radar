"""drawdown/reconstruct.py — QDII 场内 ETF「剔除溢价 + 剔除汇率」还原曲线。

口径（2026-11-21 定稿，区间起点归一化为 0%）：
- 场内价收益% = price_t / price_t0 − 1
    场内 ETF 收盘价，含溢价、含汇率（人民币计价），是用户实际买卖的价格。
- 净值收益%   = nav_t / nav_t0 − 1
    基金单位净值，已剔除场内溢价，但净值本身是人民币计价、仍含汇率折算。
- 还原收益%   = (nav_t / fx_t) / (nav_t0 / fx_t0) − 1
    把净值按当日汇率换回海外本币口径，剔除汇率后的真实涨跌。
    fx = 人民币兑外币中间价（如 usd=USDCNY，1美元=X人民币，数值↑=人民币贬值）。
- 基准指数%   = bench_t / bench_t0 − 1
    跟踪的海外指数本币点位（如纳斯达克100），用于对照还原收益%的跟踪效果。

四条线以「共同交易日」为 t0 各自归一化。差额即「溢价 + 费率 + 跟踪误差」综合贡献：
  场内价% − 净值%   ≈ 溢价变动
  净值%   − 还原%   ≈ 汇率贡献
  还原%   − 基准%   ≈ 跟踪误差 + 费率

数据不全时如实标注：任一曲线缺数据则整组降级，不造假、不补零。
"""
from . import constants as C
from . import db


def _norm(rows, t0):
    """把 [(date, value)] 归一化为 [[date, pct]]，起点 t0 的值当作基准。"""
    base = None
    out = []
    for d, v in rows:
        if d < t0 or v is None:
            continue
        if base is None:
            base = v
        if base:
            out.append([d, round((v / base - 1) * 100, 4)])
    return out


def _common_start(*date_sets):
    """多组日期的交集里最早的共同交易日；交集为空返回 None。"""
    if not date_sets or any(not s for s in date_sets):
        return None
    common = set(date_sets[0])
    for s in date_sets[1:]:
        common &= set(s)
    return min(common) if common else None


def reconstruct(etf_id, path=db.DB_PATH):
    """返回一只可还原 ETF 的四曲线对照；数据不全返回 {"error": ...}。"""
    cfg = next((r for r in C.RECONSTRUCT if r["etf"] == etf_id), None)
    if not cfg:
        return {"error": "not_reconstructible", "etf": etf_id}

    price = db.get_daily(cfg["etf"], path)          # 场内价（人民币，含溢价+汇率）
    nav = db.get_nav(cfg["nav_code"], path)         # 基金净值（人民币，含汇率）
    fx = db.get_daily(cfg["fx"], path)              # 汇率（USDCNY 等）
    bench = db.get_daily(cfg["bench"], path)        # 跟踪指数（本币）

    if not (price and nav and fx and bench):
        missing = [n for n, rows in (("场内价", price), ("净值", nav),
                                     ("汇率", fx), ("基准指数", bench)) if not rows]
        return {"error": "missing_data", "etf": etf_id,
                "missing": missing,
                "msg": "缺数据：" + "、".join(missing) + "（净值需先跑 backfill --nav）"}

    # 净值换算回本币：nav/fx（如净值人民币 / USDCNY = 美元口径资产价值）
    nav_by_d = dict(nav)
    fx_by_d = dict(fx)
    common_dates = sorted(set(d for d, _ in price) & set(nav_by_d) &
                          set(fx_by_d) & set(d for d, _ in bench))
    t0 = _common_start(common_dates, common_dates, common_dates, common_dates)
    if not t0:
        return {"error": "no_common_date", "etf": etf_id,
                "msg": "四条曲线无共同交易日，无法对齐起点"}

    nav_fx = [(d, nav_by_d[d] / fx_by_d[d]) for d in common_dates
              if d in nav_by_d and d in fx_by_d]
    bench_rows = [(d, dict(bench)[d]) for d in common_dates]

    return {
        "etf": etf_id,
        "name": cfg["name"],
        "nav_code": cfg["nav_code"],
        "bench": cfg["bench"],
        "bench_name": cfg["bench_name"],
        "fx": cfg["fx"],
        "t0": t0,
        "points": len(common_dates),
        "curves": {
            "price": _norm(price, t0),      # 场内价%（含溢价+汇率）
            "nav": _norm(nav, t0),          # 净值%（剔溢价，含汇率）
            "restored": _norm(nav_fx, t0),  # 还原%（剔溢价+汇率）
            "bench": _norm(bench_rows, t0), # 基准指数%（本币）
        },
        "note": ("起点 %s = 0%%；场内价含溢价+汇率，净值剔溢价仍含汇率，"
                 "还原=净值/汇率剔汇率，基准为跟踪指数本币。差额=溢价+汇率+跟踪误差+费率。"
                 % t0),
    }


def payload(etf_id=None, path=db.DB_PATH):
    """给 API 用：默认第一只可还原标的；列出全部可还原标的。"""
    ids = [r["etf"] for r in C.RECONSTRUCT]
    target = etf_id if etf_id in ids else (ids[0] if ids else None)
    return {
        "available": C.RECONSTRUCT,
        "current": reconstruct(target, path) if target else None,
    }
