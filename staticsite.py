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
import json
import os

import db
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
    if raw.get("last_fetch"):
        db.set_meta("last_fetch", raw["last_fetch"], path)
    return n


# ---------------- 补齐缺失交易日 ----------------
def sync_missing(days: int = 20, path: str = db.DB_PATH) -> list:
    """把最近 days 个自然日内缺失的交易日补回，并重抓"主力数据未更新"的目标日。"""
    tgt = target_date()
    existed = set(db.get_dates(path))
    start = tgt - datetime.timedelta(days=days)
    todo = []
    d = start
    while d <= tgt:
        if fetcher.is_trading_day(d) and d.strftime("%Y-%m-%d") not in existed:
            todo.append(d.strftime("%Y-%m-%d"))
        d += datetime.timedelta(days=1)

    done = []
    for ds in todo:
        try:
            res = fetcher.backfill_date(ds)
            if res.get("etf_count"):
                done.append(ds)
        except Exception:
            continue

    # 目标日已有数据但主力净流入全空（收盘后接口尚未更新）→ 重抓一次
    ds_tgt = tgt.strftime("%Y-%m-%d")
    rows = db.get_etf_by_date(ds_tgt, path=path)
    if rows and not any(r["main_net"] is not None for r in rows):
        try:
            fetcher.backfill_date(ds_tgt)
            done.append(ds_tgt + "(refetch)")
        except Exception:
            pass

    if done:
        db.set_meta("last_fetch", bj_now().strftime("%Y-%m-%d %H:%M:%S"), path)
    return done


# ---------------- 组装静态数据 ----------------
def build_static_payload(path: str = db.DB_PATH) -> dict:
    """全量快照：所有日期的明细一次性打包，筛选交给前端算。

    结构刻意贴近 /api/data 的字段语义，前端同一套渲染代码可直接复用。
    """
    dates = db.get_dates(path)
    days = {d: db.get_etf_by_date(d, path=path) for d in dates}
    news = {d: db.get_news_by_date(d, path=path) for d in dates}
    # 一级市场申赎（ETF 份额）：按日期打包，前端与明细表合并展示
    # 只打包应用展示的交易日（本地库仍保留完整份额历史，供后续回补二级数据时复用）
    dset = set(dates)
    share_dates = [d for d in db.get_share_dates(path) if d in dset]
    shares = {d: db.get_shares_by_date(d, path=path) for d in share_dates}
    return {
        "generated_at": bj_now().strftime("%Y-%m-%d %H:%M:%S"),
        "latest_date": dates[-1] if dates else None,
        "dates": dates,
        "last_fetch": db.get_meta("last_fetch", "—", path),
        "news_ok": db.get_meta("news_ok", "1", path) == "1",
        "days": days,
        "news": news,
        "shares": shares,
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
    args = ap.parse_args()

    db.init_db()
    restored = import_from_days_file()
    if restored:
        print("[import] 从 data.json 恢复历史 %d 天" % restored)

    if args.date:
        r = fetcher.backfill_date(args.date)
        print("[backfill] %s -> %d 只 (主力有值 %d)" %
              (args.date, r["etf_count"], r["mainnet_ok"]))
    if not args.no_sync:
        done = sync_missing(days=args.days)
        print("[sync] 补齐/重抓：%s" % (", ".join(done) if done else "无"))
        # 一级市场申赎（ETF 份额）：东财 datacenter 一次可回补整段历史
        try:
            sdays, hit, n = fetcher.sync_shares()
            print("[shares] 份额回补：%d 个交易日 / %d 只基金 / %d 行" % (sdays, hit, n))
        except Exception as e:
            print("[shares] 份额抓取失败（不影响主流程）：%s" % e)

    res = export(out_dir=args.out)
    print("[export] %s" % json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
