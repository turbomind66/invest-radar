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

_EVT_CACHE = {}   # (path, asset, 最新日期, 阈值, top) → 自动事件；日更后自然失效


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
    last_check = db.get_meta("last_check", None, path)
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
        "event_count": len(C.EVENTS),
        "reconstruct": C.RECONSTRUCT,
        "last_fetch": last_fetch,
        "last_rebuild": last_rebuild,
        # 最近一次「检查/更新」时间（0 新增也会写）——页面用它证明按钮真的跑过
        "last_check": last_check,
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


def _num(v):
    """价格显示：指数用 2 位、ETF 用 4 位，避免 3900.1234 这种噪声。"""
    if v is None:
        return "—"
    return ("%.2f" if abs(v) >= 100 else "%.4f") % v


def _auto_events(asset_id, path=db.DB_PATH, min_drop=20.0, top=6):
    """数据自动识别的「深度回撤 / 触底反弹」标注（每标的最多取最深的 top 次）。

    人工策展事件覆盖不到的长尾（尤其 2015/2018/2021 这类嵌套大跌）由这里补齐，
    标题直接来自真实峰谷，不会编造历史。按 (库, 标的, 最新日期) 缓存，日更后自动失效。
    """
    latest = db.latest_date(asset_id, path)
    if not latest:
        return []
    key = (path, asset_id, latest, min_drop, top)
    if key in _EVT_CACHE:
        return _EVT_CACHE[key]
    eps = engine.compute_episodes(db.get_daily(asset_id, path), min_drop)
    picked = sorted(eps, key=lambda e: e["drop_pct"])[:top]
    out = []
    for e in picked:
        if e.get("recover_date"):
            rec = "已在 %s 收复前峰（累计 %d 个交易日）" % (
                e["recover_date"], e["recover_days"] or 0)
        else:
            rec = "至今未收复前峰"
        out.append(dict(
            date=e["peak_date"], target=asset_id, side="crash",
            title="深度回撤 %.1f%%" % e["drop_pct"],
            desc="自 %s 高点 %s 跌至 %s 低点 %s，最大回撤 %.2f%%，历时 %d 个交易日；%s。" % (
                e["peak_date"], _num(e["peak"]), e["trough_date"],
                _num(e["trough"]), e["drop_pct"], e["days_down"], rec),
            auto=True))
        if e.get("rebound_pct") is not None:
            out.append(dict(
                date=e["trough_date"], target=asset_id, side="rally",
                title="触底反弹 +%.1f%%" % e["rebound_pct"],
                desc="%s 见底 %s 后回升，至 %s 反弹 %.2f%%。" % (
                    e["trough_date"], _num(e["trough"]),
                    e["rebound_date"] or "—", e["rebound_pct"]),
                auto=True))
    _EVT_CACHE[key] = out
    return out


def _days_between(d1, d2):
    import datetime
    try:
        return abs((datetime.date.fromisoformat(d1)
                    - datetime.date.fromisoformat(d2)).days)
    except (ValueError, TypeError):
        return 9999


def events_for(asset_id, path=db.DB_PATH):
    """单标的事件：策展事件 + 自动识别波段（同名/邻近的自动事件不重复标注）。

    场内 ETF 继承其跟踪指数的策展事件（target 改写成该 ETF），
    否则只选「纳指ETF国泰」时看不到指数层面的 2015/2020 这些大事件。
    """
    meta = C.BY_ID.get(asset_id, {})
    track = meta.get("track")
    targets = [asset_id]
    if track and track in C.BY_ID and track != asset_id:
        targets.append(track)
    curated, seen = [], set()
    for e in C.EVENTS:
        if e["target"] not in targets:
            continue
        key = (e["date"], e["title"])
        if key in seen:
            continue
        seen.add(key)
        row = dict(e)
        row["target"] = asset_id      # 前端按 target 找曲线，继承来的事件要落在本标的上
        curated.append(row)
    auto = []
    for e in _auto_events(asset_id, path):
        if any(a["side"] == e["side"] and _days_between(a["date"], e["date"]) <= 45
               for a in curated):
            continue
        auto.append(e)
    out = curated + auto
    out.sort(key=lambda e: (e["date"], e["side"]))
    return out


def series(ids, dfrom=None, dto=None, path=db.DB_PATH):
    ids = [i for i in (ids or []) if i in C.BY_ID]
    if not ids:
        ids = list(C.DEFAULT_IDS)
    out = {"series": [_series_one(i, dfrom, dto, path) for i in ids],
           "events": [e for aid in ids for e in events_for(aid, path)]}
    return out


def _rows_in_window(asset_id, dfrom=None, dto=None, path=db.DB_PATH):
    """按起止日裁剪的日线（两端都可缺省）。空窗口返回 []。"""
    rows = db.get_daily(asset_id, path)
    if dfrom:
        rows = [r for r in rows if r[0] >= dfrom]
    if dto:
        rows = [r for r in rows if r[0] <= dto]
    return rows


def table(asset_id, gran="day", dfrom=None, dto=None, path=db.DB_PATH):
    if asset_id not in C.BY_ID:
        return {"error": "unknown_asset"}
    if gran not in C.GRANULARITIES:
        gran = "day"
    metrics = db.get_metrics(asset_id, dfrom, dto, path)
    # 窗口口径：峰值从窗口第一根起算（用户选了区间，就该看到区间内的高点）。
    # 与上面 metrics 的「历史最高收盘」口径并存，两套回撤同时给前端。
    rows = _rows_in_window(asset_id, dfrom, dto, path)
    wmetrics, _ = engine.compute_metrics(rows)
    win_map = {m[0]: {"date": m[0], "peak": m[2], "peak_date": m[3], "dd": m[4]}
               for m in wmetrics}
    daily = [{"date": m["date"], "close": m["close"], "peak": m["peak"],
              "peak_date": m["peak_date"], "dd_pct": m["dd_pct"],
              "days_since_peak": m["days_since_peak"], "state": m["state"],
              "wpeak": win_map.get(m["date"], {}).get("peak"),
              "wpeak_date": win_map.get(m["date"], {}).get("peak_date"),
              "wdd_pct": win_map.get(m["date"], {}).get("dd")}
             for m in metrics]
    # 回撤事件用「波段识别」而非 dd_events：后者锚定历史最高收盘，
    # 高点长期未收复时会把 2015/2018/2021 的大跌并进同一段（事件只有寥寥几条）。
    episodes = [{"peak_date": e["peak_date"], "peak": e["peak"],
                 "trough_date": e["trough_date"], "trough": e["trough"],
                 "recover_date": e["recover_date"], "drop_pct": e["drop_pct"],
                 "rebound_pct": e.get("rebound_pct"),
                 "days_down": e.get("days_down"),
                 "recover_days": e["recover_days"], "ongoing": e["ongoing"]}
                for e in engine.compute_episodes(rows, 10.0)]
    episodes.sort(key=lambda x: x["peak_date"], reverse=True)
    window = engine.window_summary(rows)
    if window:
        # 与「历史最高收盘」对齐：metrics 的 peak 是**截至当日的滚动最高**，
        # 取窗口最后一根得到的就是「本轮行情走到这里为止的历史高点」。
        gpeak = metrics[-1]["peak"] if metrics else None
        gdate = metrics[-1]["peak_date"] if metrics else None
        window["history_peak"] = gpeak
        window["history_peak_date"] = gdate
        window["is_global_peak"] = 1 if (gpeak is not None
                                         and window["peak"] >= gpeak - 1e-9) else 0
    return {
        "id": asset_id,
        "name": C.BY_ID[asset_id]["name"],
        "gran": gran,
        "window": window,
        "periods": engine.aggregate(metrics, gran, win_map),
        "daily": daily,
        "events": db.get_events(asset_id, path),
        "episodes": episodes,
        "total": len(metrics),
    }


def signals(ids, dfrom=None, dto=None, path=db.DB_PATH):
    """今日信号。给了起止日则附「所选区间」区块（区间高点/区间回撤/区间修复）。"""
    ids = [i for i in (ids or []) if i in C.BY_ID] or list(C.DEFAULT_IDS)
    has_window = bool(dfrom or dto)
    out = []
    for aid in ids:
        metrics = db.get_metrics(aid, path=path)
        events = db.get_events(aid, path)
        s = engine.signal(metrics, events)
        if s:
            s["id"] = aid
            s["name"] = C.BY_ID[aid]["name"]
            s["kind"] = C.BY_ID[aid]["kind"]
            if has_window:
                w = engine.window_summary(_rows_in_window(aid, dfrom, dto, path))
                if w:
                    # 区间高点是否就是历史高点：前端据此决定要不要合并展示
                    w["is_global_peak"] = 1 if w["peak"] >= s["peak"] - 1e-9 else 0
                    w["history_peak"] = s["peak"]
                    w["history_peak_date"] = s["peak_date"]
                s["window"] = w
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


def render_index(payload=None):
    """把 DD 模板转成静态页 HTML（注入 __STATIC__ 标记 + 共享刷新脚本 + 版本号）。

    单独抽出来是为了「只重导页面、不动 data.json」——模板哪怕只改一行样式，
    也不必重算 4MB 快照（重跑 export_static 会把本地库数据灌进 Pages 快照）。
    """
    import json
    import os
    tpl = os.path.join(C.BASE, "templates", "index.html")
    with open(tpl, encoding="utf-8") as f:
        html = f.read()
    version = os.environ.get("DD_STATIC_VERSION", "v2.9.1-nt")
    html = html.replace("__APP_VERSION__", version)
    flag = {"data_base": "", "static": True}
    inject = ("<script>window.__STATIC__=%s;</script>\n"
              % json.dumps(flag, ensure_ascii=False))
    # 本地静态站（python serve_docs.py）用这套共享脚本触发 /api/local/refresh；
    # Pages 上 probe 失败 → 按钮保持隐藏，行为不变。
    js_path = os.path.join(C.ROOT, "common", "static_refresh.js")
    try:
        with open(js_path, encoding="utf-8") as f:
            js = f.read()
        if js:
            inject += "<script>%s</script>\n" % js
    except Exception:  # noqa: BLE001
        pass
    inject += "</head>"
    return html.replace("</head>", inject, 1)


def export_static(out_dir, path=db.DB_PATH):
    """导出 docs/dd/（data.json + index.html），与服务端同源口径。

    out_dir 为目录（如 docs/dd）。只写 dd 自己的文件，绝不碰 docs/data.json。
    """
    import json
    import os
    payload = build_payload(path=path)
    # 静态快照瘦身：区间口径的 w* 列在前端由 staticTable() 按所选起止日现算，
    # 快照里没有窗口概念，存 3 列 × 8595 根日线只会让 data.json 从 4.2MB 涨到 5.7MB。
    _t = payload.get("table") or {}
    _t.pop("window", None)
    for _k in ("daily", "periods"):
        if _t.get(_k):
            _t[_k] = [{k: v for k, v in r.items() if not k.startswith("w")}
                      for r in _t[_k]]
    # 静态站没有 /api/dd/reconstruct：把每只可还原标的单独写一个 recon/<etf>.json
    # （单只 140~300KB），勾选还原时按需取，首屏 data.json 不背这 1.3MB。
    # 只把 current 放 data.json 会让 GitHub Pages 永远只出首只（用户报「只显示纳指ETF」）。
    _rc = payload.get("reconstruct") or {}
    recon_dir = os.path.join(out_dir, "recon")
    os.makedirs(recon_dir, exist_ok=True)
    for _r in _rc.get("available") or []:
        _one = RC.reconstruct(_r["etf"], path)
        if _one.get("error"):
            continue
        _fp = os.path.join(recon_dir, _r["etf"] + ".json")
        _tmp3 = _fp + ".tmp"
        with open(_tmp3, "w", encoding="utf-8") as _f:
            json.dump(_one, _f, ensure_ascii=False, allow_nan=False, default=str,
                      separators=(",", ":"))
        os.replace(_tmp3, _fp)
    os.makedirs(out_dir, exist_ok=True)
    # data.json（原子写）
    data_file = os.path.join(out_dir, "data.json")
    tmp = data_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, allow_nan=False, default=str,
                  separators=(",", ":"))
    os.replace(tmp, data_file)
    # index.html：模板注入静态标记（前端走 data.json）+ 版本号
    html_file = os.path.join(out_dir, "index.html")
    tmp2 = html_file + ".tmp"
    with open(tmp2, "w", encoding="utf-8") as f:
        f.write(render_index(payload))
    os.replace(tmp2, html_file)
    return {"bytes": os.path.getsize(data_file), "html_bytes": os.path.getsize(html_file),
            "generated_at": payload["generated_at"]}
