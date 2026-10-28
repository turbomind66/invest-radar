"""静态站导出：把每日快照汇总成 docs/data.json + docs/index.html，供 GitHub Pages 托管。

GitHub Pages 只能托管静态文件、跑不了 Python 服务，所以这里把"抓取"交给
GitHub Actions（收盘后定时跑），"展示"交给静态页：

    Actions 定时  →  staticsite.py 抓取入库  →  生成 docs/data.json
                                              →  由 Pages 直接提供访问

前端复用 templates/index.html，仅注入 window.__STATIC__ 标记后切换到
"本地筛选"模式（不再请求 /api/data）。服务端模式（python app.py）完全不受影响。

用法：
    python staticsite.py                 # 同步缺失交易日 + 导出
    python staticsite.py --no-sync       # 只导出（不联网）
    python staticsite.py --date 2026-10-27   # 补指定交易日后再导出
"""
import argparse
import datetime
import time
import json
import os

import db
import intraday
import fetcher

BASE = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(BASE, "docs")
TEMPLATE = os.path.join(BASE, "templates", "index.html")
DAYS_FILE = os.path.join(DOCS_DIR, "data.json")

TZ_BEIJING = datetime.timezone(datetime.timedelta(hours=8))
ACTIONS_URL = ("https://github.com/turbomind66/ashare-national-team-tracker"
               "/actions/workflows/daily.yml")


def bj_now() -> datetime.datetime:
    """当前北京时间（Actions runner 是 UTC，必须显式换算）。"""
    return datetime.datetime.now(datetime.timezone.utc).astimezone(TZ_BEIJING)


def prev_trading_day(d: datetime.date) -> datetime.date:
    d = d - datetime.timedelta(days=1)
    while not fetcher.is_trading_day(d):
        d = d - datetime.timedelta(days=1)
    return d


def target_date(now=None) -> datetime.date:
    """应归档到哪一天：收盘后（15:30 之后）算今天，否则算上一个交易日。"""
    now = now or bj_now()
    d = now.date()
    if fetcher.is_trading_day(d) and (now.hour, now.minute) >= (15, 30):
        return d
    return prev_trading_day(d)


# ---------------- 从已发布的 data.json 恢复历史 ----------------
def import_from_days_file(days_file: str = DAYS_FILE, path: str = db.DB_PATH) -> int:
    """Actions 每次都是干净环境，本地 SQLite 不随仓库走。

    因此先读上一版 docs/data.json 把历史灌回库里，避免每天只剩当天数据。
    返回恢复的日期数。
    """
    if db.get_dates(path):
        return 0
    if not os.path.exists(days_file):
        return 0
    try:
        with open(days_file, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return 0
    n = 0
    for d, rows in (raw.get("days") or {}).items():
        if rows:
            db.save_etf_daily(d, rows, path)
            n += 1
    for d, rows in (raw.get("shares") or {}).items():
        if rows:
            db.save_share_rows(d, rows, path)
    for d, items in (raw.get("news") or {}).items():
        if items:
            db.save_news(d, items, path)
    # 方案C：逐笔大单原始行——盘中多次采样靠它跨次累积，干净环境才不会丢
    for d, rows in (raw.get("tick_rows") or {}).items():
        if rows:
            intraday.restore_tick_rows(d, rows, path)
    if raw.get("last_fetch"):
        db.set_meta("last_fetch", raw["last_fetch"], path)
    return n


# ---------------- 补齐缺失交易日 ----------------
def _recent_trading_days(tgt, n: int, max_lookback: int = 260) -> list:
    """从 tgt 往回数 n 个交易日（跳过周末），返回 YYYY-MM-DD 列表（升序）。"""
    out, d = [], tgt
    while len(out) < n and d > tgt - datetime.timedelta(days=max_lookback):
        if fetcher.is_trading_day(d):
            out.append(d.strftime("%Y-%m-%d"))
        d -= datetime.timedelta(days=1)
    return sorted(out)


def sync_missing(days: int = 20, span: int = 121, path: str = db.DB_PATH) -> list:
    """补齐缺失交易日，并**重刷/回补**最近 span 个交易日。

    为什么要"重刷"：某一天可能"已经抓到，但统计不全"——比如抓取时
    上游资金流还没出数（f62 缺失），这种日期不会被"补缺失"逻辑发现，
    就会一直残缺。每天 06:00 的那次运行会用历史接口精确还原最近交易日的
    收盘价与四档资金流，把前一天统计不全的数据补齐；17:00 的那次则把
    当天连同前两天一并重刷（收盘后上游陆续出数，越晚越全）。

    span 默认 121：资金流历史接口只保留最近约 121 个交易日，一次批量回补
    只需每只基金 2 次请求（121 天约 13 秒），顺带把二级历史补到接口上限。
    """
    tgt = target_date()
    existed = set(db.get_dates(path))
    # 自适应识别法定节假日：某日既无行情也无资金流 → 标记为非交易日，后续不再尝试抓取
    try:
        skip = set(json.loads(db.get_meta("non_trading", "[]", path) or "[]"))
    except Exception:
        skip = set()
    start = tgt - datetime.timedelta(days=days)
    todo = []
    d = start
    while d <= tgt:
        ds = d.strftime("%Y-%m-%d")
        if fetcher.is_trading_day(d) and ds not in existed and ds not in skip:
            todo.append(ds)
        d += datetime.timedelta(days=1)

    done, notes = [], []
    # 1) 完全缺失的交易日：批量精确还原
    if todo:
        try:
            res = fetcher.backfill_range(todo, path)
            if res.get("rows"):
                done.append("补齐 %d 天/%d 行" % (res["days"], res["rows"]))
        except Exception as e:
            notes.append("补齐失败(%s)" % type(e).__name__)
        # 补齐后仍无任何数据的日期 → 记为非交易日（法定节假日），下次不再白跑
        empty = [ds for ds in todo if not db.get_etf_by_date(ds, path=path)]
        if empty:
            skip |= set(empty)
            db.set_meta("non_trading", json.dumps(sorted(skip)), path)

    # 2) 强制重刷 + 扩展历史：一次批量回补到资金流接口可得上限（约 121 个交易日）
    #    覆盖"已抓到但统计不全"（当天及前两天），同时把二级历史补到上限
    refresh = _recent_trading_days(tgt, span)
    if refresh:
        try:
            res = fetcher.backfill_range(refresh, path)
            if res.get("rows"):
                done.append("回补/重刷 %d 天（%d 行）" % (res["days"], res["rows"]))
            else:
                notes.append("回补无数据")
            # 回补时既无行情也无资金流的日期 → 判定为非交易日（法定节假日）
            no_kline = [x for x in (res.get("no_kline") or []) if x in refresh]
            if no_kline:
                skip |= set(no_kline)
                db.set_meta("non_trading", json.dumps(sorted(skip)), path)
        except Exception as e:
            notes.append("回补失败(%s)" % type(e).__name__)

    # 3) 当天已收盘时用实时接口校准（精确成交额 + 实时四档）；
    #    收盘后上游资金流陆续出数，当晚不全的部分由次日 06:00 的重刷修正
    now = bj_now()
    if tgt == datetime.date(now.year, now.month, now.day) and now.hour >= 15:
        try:
            etfs, _ok = fetcher.fetch_etfs()
            if etfs:
                db.upsert_etf_rows(tgt.strftime("%Y-%m-%d"), etfs, path)
                done.append("实时校准 %s（%d 只）" % (tgt.strftime("%Y-%m-%d"), len(etfs)))
        except Exception as e:
            notes.append("实时校准失败(%s)" % type(e).__name__)

    if done:
        db.set_meta("last_fetch", bj_now().strftime("%Y-%m-%d %H:%M:%S"), path)
    # 同步状态写入 meta，页面"数据更新于"条会显示——失败不再静默
    status = "；".join(done) if done else "无变更"
    if notes:
        status += "　⚠ " + "；".join(notes)
    db.set_meta("last_sync", "%s %s" % (bj_now().strftime("%Y-%m-%d %H:%M"), status), path)
    return done


# ---------------- 组装静态数据 ----------------
def build_static_payload(path: str = db.DB_PATH, window_days: int = None) -> dict:
    """全量快照：所有日期的明细一次性打包，筛选交给前端算。

    结构刻意贴近 /api/data 的字段语义，前端同一套渲染代码可直接复用。

    明细只保留最近 window_days 个交易日（默认 db.EXPORT_WINDOW_DAYS），
    避免 data.json 随天数无限膨胀；趋势图另用 history_full（全量汇总）展示完整区间。
    """
    if window_days is None:
        window_days = db.EXPORT_WINDOW_DAYS
    dates = db.get_dates(path)
    if window_days and len(dates) > window_days:
        dates = dates[-window_days:]
    days = {d: db.get_etf_by_date(d, path=path) for d in dates}
    news = {d: db.get_news_by_date(d, path=path) for d in dates}
    # 一级市场申赎（ETF 份额）：按日期打包，前端与明细表合并展示
    # 只打包应用展示的交易日（本地库仍保留完整份额历史，供后续回补二级数据时复用）
    dset = set(dates)
    share_dates = [d for d in db.get_share_dates(path) if d in dset]
    shares = {d: db.get_shares_by_date(d, path=path) for d in share_dates}
    # 全量历史汇总（二级资金流 ∪ 一级份额的日期并集）：趋势图用它画完整区间
    history_full = db.get_history_full(path)
    return {
        "generated_at": bj_now().strftime("%Y-%m-%d %H:%M:%S"),
        "latest_date": dates[-1] if dates else None,
        "dates": dates,
        "last_fetch": db.get_meta("last_fetch", "—", path),
        "last_sync": db.get_meta("last_sync", "—", path),
        "news_ok": db.get_meta("news_ok", "1", path) == "1",
        "days": days,
        "news": news,
        "shares": shares,
        "history_full": history_full,
        # 疑似护盘/压盘模块：带最近 10 个有分时的日期（上限控制体积；库里存的是全量）
        "intraday": {d: intraday.build_view(d, path)
                     for d in db.get_intraday_dates(path)[-10:]},
        # 方案C 原始行：带最近 TICK_EXPORT_DAYS 天，供下次运行在干净环境恢复后继续累积
        "tick_rows": {d: intraday.tick_rows_for_export(d, path)
                      for d in db.get_tick_dates(path)[-intraday.TICK_EXPORT_DAYS:]},
        "cats": db.get_cats(path),
        "funds": db.get_funds(path),
        "actions_url": ACTIONS_URL,
    }


def render_index(payload: dict, template: str = TEMPLATE) -> str:
    """把服务端模板转成静态页：注入 window.__STATIC__ 标记，数据源改 data.json。"""
    with open(template, encoding="utf-8") as f:
        html = f.read()
    flag = {
        "actions_url": payload.get("actions_url", ""),
        "generated_at": payload.get("generated_at", ""),
    }
    inject = "<script>window.__STATIC__=%s;</script>\n</head>" % json.dumps(
        flag, ensure_ascii=False)
    return html.replace("</head>", inject, 1)


def export(out_dir: str = DOCS_DIR, template: str = TEMPLATE,
           path: str = db.DB_PATH) -> dict:
    """生成 docs/data.json + docs/index.html（+ .nojekyll）。返回摘要。"""
    os.makedirs(out_dir, exist_ok=True)
    payload = build_static_payload(path)

    with open(os.path.join(out_dir, "data.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(render_index(payload, template))

    # 让 Pages 跳过 Jekyll 处理，避免任何下划线文件被吞
    open(os.path.join(out_dir, ".nojekyll"), "w").close()

    return {
        "dates": payload["dates"],
        "latest": payload["latest_date"],
        "days": len(payload["days"]),
        "share_days": len(payload.get("shares") or {}),
        "generated_at": payload["generated_at"],
    }


def main():
    ap = argparse.ArgumentParser(description="导出静态站数据（GitHub Pages）")
    ap.add_argument("--no-sync", action="store_true", help="只导出，不联网抓取")
    ap.add_argument("--date", help="额外补抓指定交易日 YYYY-MM-DD")
    ap.add_argument("--days", type=int, default=20, help="回补窗口（自然日）")
    ap.add_argument("--out", default=DOCS_DIR, help="输出目录")
    ap.add_argument("--mode", default="full", choices=["full", "tick"],
                    help="full=完整同步导出；tick=盘中轻量采样（只抓分时+逐笔大单，不跑全量回补）")
    args = ap.parse_args()

    db.init_db()
    restored = import_from_days_file()
    if restored:
        print("[import] 从 data.json 恢复历史 %d 天" % restored)

    # 盘中轻量采样：只抓当日分时 + 逐笔大单，不跑全量回补/份额（快，适合盘中多次触发）。
    # 目标日期必须用「今天」——target_date() 在 15:30 前返回的是上一交易日，会采错天。
    if args.mode == "tick":
        td = bj_now().date()
        if not fetcher.is_trading_day(td):
            print("[tick] 今日非交易日，跳过采样（仍导出，保证 data.json 完整）")
        else:
            ds = td.strftime("%Y-%m-%d")
            try:
                n, funds, ds_list = intraday.archive(ds, backup=False)
                print("[intraday] 分时归档 %d 行 / %d 只" % (n, funds))
            except Exception as e:
                print("[intraday] 分时抓取失败（不影响采样）：%s" % e)
            try:
                tn, tf = intraday.archive_tick(ds)
                ev = intraday.detect_tick(ds)
                print("[tick] 采样 %s：大单 %d 行 / %d 只；集中事件 %d 个"
                      % (ds, tn, tf, len(ev or [])))
            except Exception as e:
                print("[tick] 逐笔采样失败：%s" % e)
        res = export(out_dir=args.out)
        print("[export] %s" % json.dumps(res, ensure_ascii=False))
        return

    if args.date:
        r = fetcher.backfill_date(args.date)
        print("[backfill] %s -> %d 只 (主力有值 %d)" %
              (args.date, r["etf_count"], r["mainnet_ok"]))
    if not args.no_sync:
        done = sync_missing(days=args.days)
        print("[sync] 补齐/重抓：%s" % (", ".join(done) if done else "无"))
        # 一级市场申赎（ETF 份额）：东财 datacenter 一次可回补整段历史
        # 重试一次：偶发的写库占用（本地网盘同步/文件锁）不该让当天份额缺失
        err = None
        for attempt in range(2):
            try:
                sdays, hit, n = fetcher.sync_shares()
                print("[shares] 份额回补：%d 个交易日 / %d 只基金 / %d 行" % (sdays, hit, n))
                err = None
                break
            except Exception as e:
                err = e
                time.sleep(2)
        if err:
            print("[shares] 份额抓取失败（不影响主流程）：%s" % err)
        # 疑似护盘/压盘模块：归档分时（上游只给近 5 日，窗口一滑过就再也拿不到）
        # + 重算各日信号（收盘后有大单净额，可把"疑似"升级为"已印证"）
        try:
            tgt = target_date().strftime("%Y-%m-%d")
            # backup=True：顺带试东财 1 分钟 K 备选源（上游恢复可回补更早的分时）
            n, funds, ds_list = intraday.archive(tgt, backup=True)
            sig_n = 0
            for ds in ds_list:
                sig_n += len(intraday.detect(ds))
            print("[intraday] 分时归档 %d 行 / %d 只 / %d 天；信号 %d 条" % (
                n, funds, len(ds_list), sig_n))
            # 方案C（逐笔大额买单）：归档大单 + 联动升级方案A 信号。
            # 数据源是滚动窗口，盘后仅剩尾盘快照；盘中多次采样可累积全天大单分布。
            tk_total, tk_funds = 0, 0
            for ds in ds_list:
                try:
                    tn, tf = intraday.archive_tick(ds)
                    intraday.detect_tick(ds)
                    tk_total += tn
                    tk_funds = max(tk_funds, tf)
                except Exception as e:
                    notes.append("逐笔大单归档失败(%s)" % type(e).__name__)
            print("[tick] 逐笔大单归档 %d 行 / 覆盖 %d 只基金（盘后仅尾盘快照，盘中采样可累积全天）"
                  % (tk_total, tk_funds))
            # 缺口检测：某天没抓到且已滑出上游窗口 → 永久缺失，明确告知
            gap = intraday.gap_dates()
            db.set_meta("intraday_gap", ",".join(gap) if gap else "", path)
            if gap:
                print("[intraday] ⚠ 分时历史有缺口（已无法回补）：%s" % ", ".join(gap))
            # 缺口并入同步状态（页面时间戳条可见）；幂等：先去掉旧提示再追加
            base = db.get_meta("last_sync", "", path)
            if "；⚠ 分时缺口" in base:
                base = base.split("；⚠ 分时缺口")[0]
            if gap:
                base += "；⚠ 分时缺口 %d 天（%s）" % (len(gap), ",".join(gap[:5]))
            db.set_meta("last_sync", base, path)
            intraday.notify_stub(None)
        except Exception as e:
            print("[intraday] 分时抓取失败（不影响主流程）：%s" % e)

    res = export(out_dir=args.out)
    print("[export] %s" % json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
