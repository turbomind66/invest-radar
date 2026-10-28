"""SQLite 存储：国家队宽基ETF每日净买入快照 + 相关新闻。"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.db")


def init_db(path: str = DB_PATH):
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS etf_daily(
            date TEXT, code TEXT, name TEXT, cat TEXT,
            price REAL, chg REAL, amount REAL, main_net REAL,
            PRIMARY KEY(date, code))"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS news(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT, time TEXT, title TEXT, url TEXT, src TEXT)"""
    )
    cur.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()


def _conn(path: str = DB_PATH):
    init_db(path)
    return sqlite3.connect(path)


def save_etf_daily(date: str, rows: list, path: str = DB_PATH):
    conn = _conn(path)
    cur = conn.cursor()
    cur.execute("DELETE FROM etf_daily WHERE date=?", (date,))
    for r in rows:
        cur.execute(
            "INSERT OR REPLACE INTO etf_daily(date,code,name,cat,price,chg,amount,main_net) VALUES(?,?,?,?,?,?,?,?)",
            (date, r["code"], r["name"], r["cat"], r.get("price"), r.get("chg"),
             r.get("amount"), r.get("main_net")),
        )
    conn.commit()
    conn.close()


def save_news(date: str, rows: list, path: str = DB_PATH):
    conn = _conn(path)
    cur = conn.cursor()
    cur.execute("DELETE FROM news WHERE date=?", (date,))
    for r in rows:
        cur.execute(
            "INSERT INTO news(date,time,title,url,src) VALUES(?,?,?,?,?)",
            (date, r.get("time", ""), r.get("title", ""), r.get("url", ""), r.get("src", "")),
        )
    conn.commit()
    conn.close()


def set_meta(key: str, value: str, path: str = DB_PATH):
    conn = _conn(path)
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))
    conn.commit()
    conn.close()


def get_meta(key: str, default=None, path: str = DB_PATH):
    conn = _conn(path)
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    conn.close()
    return row[0] if row else default


def get_dates(path: str = DB_PATH):
    conn = _conn(path)
    rows = conn.execute("SELECT DISTINCT date FROM etf_daily ORDER BY date").fetchall()
    conn.close()
    return [r[0] for r in rows]


def get_cats(path: str = DB_PATH):
    """观察池内所有类别及其基金只数（用于筛选下拉）。"""
    conn = _conn(path)
    rows = conn.execute(
        "SELECT cat, COUNT(DISTINCT code) FROM etf_daily GROUP BY cat ORDER BY cat"
    ).fetchall()
    conn.close()
    return [{"cat": r[0], "count": r[1]} for r in rows]


def latest_main_date(path: str = DB_PATH):
    """最近一个存在有效主力净流入数据的日期（用于盘前自动回退）。"""
    conn = _conn(path)
    row = conn.execute("SELECT MAX(date) FROM etf_daily WHERE main_net IS NOT NULL").fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def get_funds(path: str = DB_PATH):
    """观察池内所有基金（代码/名称/类别），按类别归组（用于筛选）。"""
    conn = _conn(path)
    rows = conn.execute(
        "SELECT code, name, cat FROM etf_daily GROUP BY code ORDER BY cat, code"
    ).fetchall()
    conn.close()
    return [{"code": r[0], "name": r[1], "cat": r[2]} for r in rows]


def _where(cats=None, codes=None, prefix=" WHERE "):
    """构造类别/基金过滤的 SQL 片段与参数。"""
    sql, args = "", []
    if cats:
        sql += " AND cat IN (%s)" % ",".join("?" * len(cats))
        args.extend(list(cats))
    if codes:
        sql += " AND code IN (%s)" % ",".join("?" * len(codes))
        args.extend(list(codes))
    return sql, args


def get_etf_by_date(date: str, cats=None, codes=None, path: str = DB_PATH):
    """取某日明细，可按类别(cats)/基金代码(codes)筛选。"""
    conn = _conn(path)
    extra, args = _where(cats, codes)
    rows = conn.execute(
        "SELECT code,name,cat,price,chg,amount,main_net FROM etf_daily WHERE date=?" + extra
        + " ORDER BY (main_net IS NULL), main_net DESC",
        [date] + args,
    ).fetchall()
    conn.close()
    return [
        {"code": r[0], "name": r[1], "cat": r[2], "price": r[3], "chg": r[4],
         "amount": r[5], "main_net": r[6]}
        for r in rows
    ]


def get_news_by_date(date: str, path: str = DB_PATH):
    conn = _conn(path)
    rows = conn.execute(
        "SELECT time,title,url,src FROM news WHERE date=? ORDER BY id DESC", (date,)
    ).fetchall()
    conn.close()
    return [{"time": r[0], "title": r[1], "url": r[2], "src": r[3]} for r in rows]


def get_history(cats=None, codes=None, path: str = DB_PATH):
    """返回每个交易日的汇总（可按类别/基金筛选）。

    total_main_net 只累加有值(非 NULL)的主力净流入；has_main 标记该日
    在筛选范围内是否存在有效主力数据，供前端断线显示。
    """
    conn = _conn(path)
    extra, args = _where(cats, codes)
    rows = conn.execute(
        "SELECT date, cat, code, main_net, amount FROM etf_daily WHERE 1=1" + extra
        + " ORDER BY date", args,
    ).fetchall()
    conn.close()
    agg, order = {}, []
    for d, cat, code, mn, amt in rows:
        if d not in agg:
            agg[d] = {"date": d, "total_main_net": 0.0, "total_amount": 0.0,
                      "by_cat": {}, "etf_count": 0, "main_cnt": 0}
            order.append(d)
        a = agg[d]
        a["etf_count"] += 1
        a["total_amount"] += (amt or 0)
        if mn is not None:
            a["main_cnt"] += 1
            a["total_main_net"] += mn
        a["by_cat"].setdefault(cat, 0.0)
        a["by_cat"][cat] += (mn or 0)
    return [agg[d] for d in order]


if __name__ == "__main__":
    init_db()
    print("db ok", DB_PATH)
