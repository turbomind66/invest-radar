"""drawdown/backfill.py — 全历史回补入口（离线脚本，跑完即退出）。

用法（在仓库根执行）：
    python -m drawdown.backfill                    # 抓全部缺失标的 + 重算指标
    python -m drawdown.backfill --force            # 强制重抓全部
    python -m drawdown.backfill sh000001 sz399006  # 只抓指定 id
    python -m drawdown.backfill --rebuild-only     # 只重算指标，不抓数据
    python -m drawdown.backfill --export           # 抓完导出 docs/dd/data.json

设计：
- 断点续传：fetcher.fetch_all 默认跳过已有数据的标的；失败留空，重跑自动补。
- 东财限流时（RemoteDisconnected）不硬刚，记录失败，隔几分钟重跑即可。
- 全历史一次请求拿全，单标的 0.2~0.5s，30 标的串行 < 30s，1核2G 无压力。
"""
import argparse
import os
import sys

# 复用项目 import 惯例：ROOT 在 sys.path，包内相对导入
from . import fetcher
from . import engine
from . import api
from . import db
from . import constants as C


def main():
    ap = argparse.ArgumentParser(description="回撤全景全历史回补")
    ap.add_argument("--force", action="store_true", help="强制重抓（默认跳过已有）")
    ap.add_argument("--rebuild-only", action="store_true", help="只重算指标不抓取")
    ap.add_argument("--export", action="store_true", help="导出 docs/dd/data.json")
    ap.add_argument("--nav", action="store_true", help="抓 QDII 场内ETF历史净值（溢价/汇率还原用）")
    ap.add_argument("--sleep", type=float, default=0.4, help="标的间间隔秒")
    ap.add_argument("ids", nargs="*", help="只抓指定标的 id")
    args = ap.parse_args()

    db.init_db()
    if args.nav:
        from . import updater
        nav = updater.fetch_nav(force=args.force)
        print("[backfill] 净值抓完：%s" % nav)
        return
    if not args.rebuild_only:
        res = fetcher.fetch_all(force=args.force, only=args.ids or None, sleep=args.sleep)
        failed = [k for k, v in res.items() if v[0] == 0]
        if failed:
            print("[backfill] ⚠ 失败标的（重跑自动补）：", ", ".join(failed))
    if args.ids:
        for aid in args.ids:
            engine.rebuild_asset(aid)
    else:
        engine.rebuild_all()

    if args.export:
        out = os.path.join(C.ROOT, "docs", "dd")
        info = api.export_static(out)
        print("[backfill] 静态快照：%s/ (data.json %d bytes, index.html %d bytes)"
              % (out, info["bytes"], info["html_bytes"]))
    avail = db.available_assets()
    print("[backfill] 完成，共 %d/%d 标的有数据" % (len(avail), len(C.ALL)))


if __name__ == "__main__":
    main()
