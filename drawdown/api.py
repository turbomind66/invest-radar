"""drawdown/api.py — 回撤全景页数据装配层（服务端与静态站同源）。

对外接口（供 national_team/app.py 挂路由）：
- catalog()                可选题材清单 + 数据可用性
- series(ids, dfrom, dto)  折线数据（收盘价 + 回撤%，含事件标注）
- table(id, gran, ...)     三视图表格：日明细 / 周期聚合 / 回撤事件
- signals(ids)             今日信号卡
- build_payload(...)       一次性返回首屏全部数据（静态快照也用这个函数）
"""
from . import constants as C
from . import db
from . import engine
from . import reconstruct as RC


def catalog(path=db.DB_PATH):
    avail = db.available_assets(path)
    assets = []
    for a in C.ALL:
        row = dict(a)
        info = avail.get(a["id"])
        row["ready"] = bool(info)
        row["actual_from"] = info["from"] if info else None
        row["actual_to"] = info["to"] if info else None
        row["points"] = info["n"] if info else 0
        assets.append(row)
    last_fetch = db.get_meta("last_fetch", None, path)
    last_rebuild = db.get_meta("last_rebuild", None, path)
    return {
        "assets": assets,
        "groups": [
            {"key": "index", "label": "A股指数", "ids": [a["id"] for a in C.INDEXES]},
            {"key": "etf", "label": "场内ETF", "ids": [a["id"] for a in C.ETFS]},
            {"key": "overseas", "label": "海外指数", "ids": [a["id"] for a in C.OVERSEAS]},
        ],
        "granularities": [
            {"key": g, "label": C.GRANULARITY_LABELS[g]} for g in C.GRANULARITIES],
        "default_ids": C.DEFAULT_IDS,
        "events": C.EVENTS,
        "reconstruct": C.RECONSTRUCT,
        "last_fetch": last_fetch,
        "last_rebuild": last_rebuild,
    }


def _downsample(points, max_points=C.MAX_POINTS):
    """简单等距降采样：1核2G 下控制传输体积，前端仍可缩放看局部。"""
    n = len(points)
    if n <= max_points:
        return points
    step = n / float(max_points)
    out = []
    i = 0
    while i < max_points:
        out.append(points[min(int(i * step), n - 1)])
        i += 1
    out.append(points[-1])
    return out


def _series_one(asset_id, dfrom=None, dto=None, path=db.DB_PATH):
    rows = db.get_daily_range(asset_id, dfrom, dto, path)
    if not rows:
        rows = db.get_daily(asset_id, path)
    if dfrom:
        rows = [r for r in rows if r[0] >= dfrom]
    if dto:
        rows = [r for r in rows if r[0] <= dto]
    metrics = {m["date"]: m for m in db.get_metrics(asset_id, dfrom, dto, path)}
    line = []
    ddline = []
    for d, c in rows:
        m = metrics.get(d)
        line.append([d, round(c, 4)])
        ddline.append([d, m["dd_pct"] if m else None])
    return {
        "id": asset_id,
        "name": C.BY_ID.get(asset_id, {}).get("name", asset_id),
        "line": _downsample(line),
        "dd": _downsample(ddline),
        "points": len(line),
    }


def series(ids, dfrom=None, dto=None, path=db.DB_PATH):
    ids = [i for i in (ids or []) if i in C.BY_ID]
    if not ids:
        ids = list(C.DEFAULT_IDS)
    out = {"series": [_series_one(i, dfrom, dto, path) for i in ids],
           "events": [e for e in C.EVENTS if not ids or e["target"] in ids]}
    return out


def table(asset_id, gran="day", dfrom=None, dto=None, path=db.DB_PATH):
    if asset_id not in C.BY_ID:
        return {"error": "unknown_asset"}
    if gran not in C.GRANULARITIES:
        gran = "day"
    metrics = db.get_metrics(asset_id, dfrom, dto, path)
    daily = [{"date": m["date"], "close": m["close"], "peak": m["peak"],
              "peak_date": m["peak_date"], "dd_pct": m["dd_pct"],
              "days_since_peak": m["days_since_peak"], "state": m["state"]}
             for m in metrics]
    return {
        "id": asset_id,
        "name": C.BY_ID[asset_id]["name"],
        "gran": gran,
        "periods": engine.aggregate(metrics, gran),
        "daily": daily,
        "events": db.get_events(asset_id, path),
        "total": len(metrics),
    }


def signals(ids, path=db.DB_PATH):
    ids = [i for i in (ids or []) if i in C.BY_ID] or list(C.DEFAULT_IDS)
    out = []
    for aid in ids:
        metrics = db.get_metrics(aid, path=path)
        events = db.get_events(aid, path)
        s = engine.signal(metrics, events)
        if s:
            s["id"] = aid
            s["name"] = C.BY_ID[aid]["name"]
            s["kind"] = C.BY_ID[aid]["kind"]
            out.append(s)
    return {"signals": out}


def build_payload(ids=None, gran="day", dfrom=None, dto=None, path=db.DB_PATH):
    """首屏一次性 payload（服务端首屏与静态 data.json 共用）。"""
    ids = [i for i in (ids or []) if i in C.BY_ID] or list(C.DEFAULT_IDS)
    return {
        "catalog": catalog(path),
        "series": series(ids, dfrom, dto, path),
        "signals": signals(ids, path)["signals"],
        "table": table(ids[0], gran, dfrom, dto, path),
        "reconstruct": RC.payload(path=path),
        "generated_at": engine.datetime.datetime.now().isoformat(timespec="seconds"),
    }


def export_static(out_dir, path=db.DB_PATH):
    """导出 docs/dd/（data.json + index.html），与服务端同源口径。

    out_dir 为目录（如 docs/dd）。只写 dd 自己的文件，绝不碰 docs/data.json。
    """
    import json
    import os
    payload = build_payload(path=path)
    os.makedirs(out_dir, exist_ok=True)
    # data.json（原子写）
    data_file = os.path.join(out_dir, "data.json")
    tmp = data_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, allow_nan=False, default=str,
                  separators=(",", ":"))
    os.replace(tmp, data_file)
    # index.html：模板注入静态标记（前端走 data.json）+ 版本号
    from . import constants as _C
    tpl = os.path.join(_C.BASE, "templates", "index.html")
    with open(tpl, encoding="utf-8") as f:
        html = f.read()
    version = os.environ.get("DD_STATIC_VERSION", "v2.9.1-nt")
    html = html.replace("__APP_VERSION__", version)
    flag = {"data_base": "", "static": True}
    inject = ("<script>window.__STATIC__=%s;</script>\n</head>"
              % json.dumps(flag, ensure_ascii=False))
    html = html.replace("</head>", inject, 1)
    html_file = os.path.join(out_dir, "index.html")
    tmp2 = html_file + ".tmp"
    with open(tmp2, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(tmp2, html_file)
    return {"bytes": os.path.getsize(data_file), "html_bytes": os.path.getsize(html_file),
            "generated_at": payload["generated_at"]}
