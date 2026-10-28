"""补历史数据 CLI：用历史接口还原指定交易日的真实收盘快照。

用法：
  python backfill.py               # 补昨天
  python backfill.py 2026-10-27    # 补指定日期

核心逻辑见 fetcher.backfill_date（已固化进应用，启动时会自动补回缺失的历史交易日）。
"""
import sys
import datetime

import db
import fetcher


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = sys.argv[1]
    else:
        target = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    print("== 补数据目标日期:", target, "==")
    res = fetcher.backfill_date(target)
    print("结果:", res)
    print("当前库内日期:", db.get_dates())
    etfs = db.get_etf_by_date(target)
    total = sum((e["main_net"] or 0) for e in etfs)
    print("该日主力净流入合计(亿):", round(total / 1e8, 2) if etfs else "无数据")
    for e in etfs[:5]:
        print("  ", e["name"], "收", e["price"], "涨%", e["chg"],
              "额亿", round(e["amount"] / 1e8, 2), "主净亿",
              (round(e["main_net"] / 1e8, 2) if e["main_net"] is not None else "—"))
