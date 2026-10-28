"""SQLite 存储：国家队宽基ETF每日净买入快照 + 相关新闻。"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.db")

# 四档资金流净额（元）：主力净 = 大单净 + 超大单净（东财口径，已勾稽验证）。
# 解决「分时图大额买入被主力净额口径对冲/拆单吞掉」的问题。
TIER_FIELDS = ("huge_net", "big_net", "mid_net", "small_net")

# 明细导出/展示窗口（交易日）：data.json 只带最近 N 天明细，更早的历史留在库里；
# 趋势图用 get_history_full() 的全量汇总展示完整区间，避免仓库体积随天数膨胀。
EXPORT_WINDOW_DAYS = 90


def init_db(path: str = DB_PATH):
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS etf_daily(
            date TEXT, code TEXT, name TEXT, cat TEXT,
            price REAL, chg REAL, amount REAL, main_net REAL,
            huge_net REAL, big_net REAL, mid_net REAL, small_net REAL,
            PRIMARY KEY(date, code))"""
    )
    # 旧库迁移：补四档列（新库上面已建全，ALTER 会失败则跳过）
    for col in TIER_FIELDS:
        try:
            cur.execute("ALTER TABLE etf_daily ADD COLUMN %s REAL" % col)
        except sqlite3.OperationalError:
            pass
    cur.execute(
        """CREATE TABLE IF NOT EXISTS news(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT, time TEXT, title TEXT, url TEXT, src TEXT)"""
    )
    # ETF 份额（一级市场申赎口径）：份额(亿份) / 净值(元) / 日变动(亿份) / 净申购(亿元)
    cur.execute(
        """CREATE TABLE IF NOT EXISTS etf_share(
            date TEXT, code TEXT, name TEXT, cat TEXT,
            shares REAL, nav REAL, chg_shares REAL, net_sub REAL,
            PRIMARY KEY(date, code))"""
    )
    # 分时归档（分钟级价量）：疑似护盘/压盘识别的原始素材
    cur.execute(
        """CREATE TABLE IF NOT EXISTS intraday_minute(
            date TEXT, code TEXT, name TEXT, cat TEXT,
            minute TEXT, price REAL, vol REAL, amount REAL,
            PRIMARY KEY(date, code, minute))"""
    )
    # 护盘/压盘信号：kind=support(护盘)/pressure(压盘)，level=suspect/confirmed
    cur.execute(
        """CREATE TABLE IF NOT EXISTS intraday_signal(
            date TEXT, code TEXT, name TEXT, cat TEXT,
            minute TEXT, kind TEXT, amount REAL, ratio REAL, price REAL,
            price_chg REAL, cover REAL, level TEXT, note TEXT,
            PRIMARY KEY(date, code, minute, kind))"""
    )
    # 逐笔大额成交（方案C：逐笔大额买单识别）的归档：来源东财 stock/details/get 滚动明细。
    # 只存"大单"（单笔成交额 ≥ 阈值），控制体积；direction: 1=主动买 / 2=主动卖 / 其它=中性。
    cur.execute(
        """CREATE TABLE IF NOT EXISTS intraday_tick(
            date TEXT, code TEXT, name TEXT, cat TEXT,
            ttime TEXT, price REAL, volume REAL, direction INTEGER, amount REAL,
            PRIMARY KEY(date, code, ttime, price, volume, direction))"""
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
            "INSERT OR REPLACE INTO etf_daily(date,code,name,cat,price,chg,amount,main_net,"
            "huge_net,big_net,mid_net,small_net) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (date, r["code"], r["name"], r["cat"], r.get("price"), r.get("chg"),
             r.get("amount"), r.get("main_net"),
             r.get("huge_net"), r.get("big_net"), r.get("mid_net"), r.get("small_net")),
        )
    conn.commit()
    conn.close()


def upsert_etf_rows(date: str, rows: list, path: str = DB_PATH) -> int:
    """按行写入（UPSERT），**不删除**该日已有行，且**空值不覆盖旧值**。

    与 save_etf_daily 的区别：
      - 批量回补时若上游某天抽风，只更新抓到的行，不会把已归档的其它基金数据洗掉；
      - COALESCE 语义：本次没取到的字段（主力净/四档/价格）保留库里已有的值，
        避免"历史接口该日缺资金流"把实时抓到的数据覆盖成 NULL。
    """
    conn = _conn(path)
    cur = conn.cursor()
    n = 0
    for r in rows:
        cur.execute(
            "INSERT INTO etf_daily(date,code,name,cat,price,chg,amount,main_net,"
            "huge_net,big_net,mid_net,small_net) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(date, code) DO UPDATE SET"
            " name=excluded.name, cat=excluded.cat,"
            " price=COALESCE(excluded.price, etf_daily.price),"
            " chg=COALESCE(excluded.chg, etf_daily.chg),"
            " amount=COALESCE(excluded.amount, etf_daily.amount),"
            " main_net=COALESCE(excluded.main_net, etf_daily.main_net),"
            " huge_net=COALESCE(excluded.huge_net, etf_daily.huge_net),"
            " big_net=COALESCE(excluded.big_net, etf_daily.big_net),"
            " mid_net=COALESCE(excluded.mid_net, etf_daily.mid_net),"
            " small_net=COALESCE(excluded.small_net, etf_daily.small_net)",
            (date, r["code"], r["name"], r["cat"], r.get("price"), r.get("chg"),
             r.get("amount"), r.get("main_net"),
             r.get("huge_net"), r.get("big_net"), r.get("mid_net"), r.get("small_net")),
        )
        n += 1
    conn.commit()
    conn.close()
    return n


def update_flow_rows(date: str, rows: list, path: str = DB_PATH) -> int:
    """只更新某日的资金流列（主力净 + 四档），不动价格/成交额/其它列。

    用于每日"强制重刷"：某天可能已抓到但四档不全（或抓取时上游还没出数），
    这里只把该日的资金流精确还原回来。返回实际更新的行数。
    """
    conn = _conn(path)
    cur = conn.cursor()
    n = 0
    for r in rows:
        cur.execute(
            "UPDATE etf_daily SET main_net=?, huge_net=?, big_net=?, mid_net=?, small_net=?"
            " WHERE date=? AND code=?",
            (r.get("main_net"), r.get("huge_net"), r.get("big_net"),
             r.get("mid_net"), r.get("small_net"), date, r["code"]),
        )
        n += cur.rowcount
    conn.commit()
    conn.close()
    return n


# ---------------- 分时（分钟级）归档与护盘/压盘信号 ----------------

def save_intraday_rows(date: str, rows: list, path: str = DB_PATH):
    """写入某日分钟级量额归档（来自腾讯分时接口）。

    rows 每项：{code, name, cat, minute(HHMM), price, vol(手), amount(元)}
    """
    conn = _conn(path)
    cur = conn.cursor()
    for r in rows:
        cur.execute(
            "INSERT OR REPLACE INTO intraday_minute(date,code,name,cat,minute,price,vol,amount)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (date, r["code"], r.get("name"), r.get("cat"), r["minute"],
             r.get("price"), r.get("vol"), r.get("amount")),
        )
    conn.commit()
    conn.close()


def get_intraday_by_date(date: str, cats=None, codes=None, path: str = DB_PATH):
    """取某日全池分钟级数据：{code: [{minute, price, vol, amount}, ...]}。"""
    conn = _conn(path)
    extra, args = _where(cats, codes)
    rows = conn.execute(
        "SELECT code,name,cat,minute,price,vol,amount FROM intraday_minute"
        " WHERE date=?" + extra + " ORDER BY code, minute", [date] + args,
    ).fetchall()
    conn.close()
    out = {}
    for code, name, cat, minute, price, vol, amount in rows:
        out.setdefault(code, []).append({
            "name": name, "cat": cat, "minute": minute,
            "price": price, "vol": vol, "amount": amount,
        })
    return out


def get_intraday_dates(path: str = DB_PATH):
    """有分时归档的日期列表（升序）。"""
    conn = _conn(path)
    rows = conn.execute(
        "SELECT DISTINCT date FROM intraday_minute ORDER BY date"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def save_intraday_signals(date: str, rows: list, path: str = DB_PATH):
    """写入某日的护盘/压盘信号。kind: support(护盘) / pressure(压盘)。"""
    conn = _conn(path)
    cur = conn.cursor()
    for r in rows:
        cur.execute(
            "INSERT OR REPLACE INTO intraday_signal(date,code,name,cat,minute,kind,amount,"
            "ratio,price,price_chg,cover,level,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (date, r["code"], r.get("name"), r.get("cat"), r["minute"], r["kind"],
             r.get("amount"), r.get("ratio"), r.get("price"), r.get("price_chg"),
             r.get("cover"), r.get("level"), r.get("note")),
        )
    conn.commit()
    conn.close()


def get_intraday_signals(date: str, cats=None, codes=None, path: str = DB_PATH):
    """取某日信号列表（按金额倒序）。"""
    conn = _conn(path)
    extra, args = _where(cats, codes)
    rows = conn.execute(
        "SELECT code,name,cat,minute,kind,amount,ratio,price,price_chg,cover,level,note"
        " FROM intraday_signal WHERE date=?" + extra + " ORDER BY amount DESC", [date] + args,
    ).fetchall()
    conn.close()
    keys = ("code", "name", "cat", "minute", "kind", "amount", "ratio", "price",
            "price_chg", "cover", "level", "note")
    return [dict(zip(keys, r)) for r in rows]


def save_tick_rows(date: str, rows: list, path: str = DB_PATH):
    """写入某日逐笔大单归档（来自东财 stock/details/get）。

    rows 每项：{code, name, cat, ttime(HH:MM:SS), price, volume(手), direction, amount(元)}
    主键 (date,code,ttime,price,volume,direction) 去重：盘中多次采样幂等追加，不重复。
    """
    conn = _conn(path)
    cur = conn.cursor()
    for r in rows:
        cur.execute(
            "INSERT OR REPLACE INTO intraday_tick(date,code,name,cat,ttime,price,volume,direction,amount)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (date, r["code"], r.get("name"), r.get("cat"), r["ttime"],
             r.get("price"), r.get("volume"), r.get("direction"), r.get("amount")),
        )
    conn.commit()
    conn.close()


def get_tick_by_date(date: str, cats=None, codes=None, path: str = DB_PATH):
    """取某日全部逐笔大单：[(code,name,cat,ttime,price,volume,direction,amount), ...]。"""
    conn = _conn(path)
    extra, args = _where(cats, codes)
    rows = conn.execute(
        "SELECT code,name,cat,ttime,price,volume,direction,amount FROM intraday_tick"
        " WHERE date=?" + extra + " ORDER BY code, ttime", [date] + args,
    ).fetchall()
    conn.close()
    return [{"code": r[0], "name": r[1], "cat": r[2], "ttime": r[3], "price": r[4],
             "volume": r[5], "direction": r[6], "amount": r[7]} for r in rows]


def get_tick_dates(path: str = DB_PATH):
    """有逐笔大单归档的日期列表（升序）。"""
    conn = _conn(path)
    rows = conn.execute(
        "SELECT DISTINCT date FROM intraday_tick ORDER BY date"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


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
        "SELECT code,name,cat,price,chg,amount,main_net,huge_net,big_net,mid_net,small_net"
        " FROM etf_daily WHERE date=?" + extra
        + " ORDER BY (main_net IS NULL), main_net DESC",
        [date] + args,
    ).fetchall()
    conn.close()
    return [
        {"code": r[0], "name": r[1], "cat": r[2], "price": r[3], "chg": r[4],
         "amount": r[5], "main_net": r[6],
         "huge_net": r[7], "big_net": r[8], "mid_net": r[9], "small_net": r[10]}
        for r in rows
    ]


# ---------------- ETF 份额（一级市场申赎口径） ----------------

def save_share_rows(date: str, rows: list, path: str = DB_PATH):
    """写入某日份额快照。rows 每项含 code/name/cat/shares/nav/chg_shares/net_sub。"""
    conn = _conn(path)
    cur = conn.cursor()
    for r in rows:
        cur.execute(
            "INSERT OR REPLACE INTO etf_share(date,code,name,cat,shares,nav,chg_shares,net_sub)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (date, r["code"], r.get("name"), r.get("cat"), r.get("shares"),
             r.get("nav"), r.get("chg_shares"), r.get("net_sub")),
        )
    conn.commit()
    conn.close()


def get_shares_by_date(date: str, cats=None, codes=None, path: str = DB_PATH):
    """取某日份额明细，可按类别/基金代码筛选。"""
    conn = _conn(path)
    extra, args = _where(cats, codes)
    rows = conn.execute(
        "SELECT code,name,cat,shares,nav,chg_shares,net_sub"
        " FROM etf_share WHERE date=?" + extra + " ORDER BY code",
        [date] + args,
    ).fetchall()
    conn.close()
    return [
        {"code": r[0], "name": r[1], "cat": r[2], "shares": r[3], "nav": r[4],
         "chg_shares": r[5], "net_sub": r[6]}
        for r in rows
    ]


def get_share_dates(path: str = DB_PATH):
    """有份额数据的日期列表（升序）。"""
    conn = _conn(path)
    rows = conn.execute(
        "SELECT DISTINCT date FROM etf_share WHERE shares IS NOT NULL ORDER BY date"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def get_news_by_date(date: str, path: str = DB_PATH):
    conn = _conn(path)
    rows = conn.execute(
        "SELECT time,title,url,src FROM news WHERE date=? ORDER BY id DESC", (date,)
    ).fetchall()
    conn.close()
    return [{"time": r[0], "title": r[1], "url": r[2], "src": r[3]} for r in rows]


def get_history(cats=None, codes=None, path: str = DB_PATH, limit_days: int = 0):
    """返回每个交易日的汇总（可按类别/基金筛选）。

    total_main_net 只累加有值(非 NULL)的主力净流入；has_main 标记该日
    在筛选范围内是否存在有效主力数据，供前端断线显示。
    limit_days > 0 时只保留最近若干个有数据的交易日（与静态导出窗口一致）。
    """
    conn = _conn(path)
    extra, args = _where(cats, codes)
    rows = conn.execute(
        "SELECT date, cat, code, main_net, amount FROM etf_daily WHERE 1=1" + extra
        + " ORDER BY date", args,
    ).fetchall()
    # 一级市场申赎：同一筛选条件下聚合份额净申购（亿元）
    srows = conn.execute(
        "SELECT date, net_sub FROM etf_share WHERE 1=1" + extra + " ORDER BY date", args,
    ).fetchall()
    conn.close()
    agg, order = {}, []
    for d, cat, code, mn, amt in rows:
        if d not in agg:
            agg[d] = {"date": d, "total_main_net": 0.0, "total_amount": 0.0,
                      "by_cat": {}, "etf_count": 0, "main_cnt": 0,
                      "share_net_sub": 0.0, "share_cnt": 0}
            order.append(d)
        a = agg[d]
        a["etf_count"] += 1
        a["total_amount"] += (amt or 0)
        if mn is not None:
            a["main_cnt"] += 1
            a["total_main_net"] += mn
        a["by_cat"].setdefault(cat, 0.0)
        a["by_cat"][cat] += (mn or 0)
    for d, ns in srows:
        if d in agg and ns is not None:
            agg[d]["share_net_sub"] += ns
            agg[d]["share_cnt"] += 1
    if limit_days and len(order) > limit_days:
        keep = set(order[-limit_days:])
        order = [d for d in order if d in keep]
    return [agg[d] for d in order]


def get_history_full(path: str = DB_PATH) -> list:
    """全量历史汇总（日期 = 二级资金流 ∪ 一级份额），供趋势图展示完整区间。

    二级资金流接口只保留最近约 121 个交易日，一级份额可回补 368 个交易日，
    这里取两者日期并集：某日只有一级数据时 total_main_net 为 None（前端断线）。
    """
    conn = _conn(path)
    mrows = conn.execute(
        "SELECT date, main_net, amount FROM etf_daily ORDER BY date"
    ).fetchall()
    srows = conn.execute(
        "SELECT date, net_sub FROM etf_share WHERE net_sub IS NOT NULL ORDER BY date"
    ).fetchall()
    conn.close()
    agg, order = {}, []
    for d, mn, amt in mrows:
        if d not in agg:
            agg[d] = {"date": d, "total_main_net": None, "total_amount": 0.0,
                      "by_cat": {}, "etf_count": 0, "main_cnt": 0,
                      "share_net_sub": 0.0, "share_cnt": 0}
            order.append(d)
        a = agg[d]
        a["etf_count"] += 1
        a["total_amount"] += (amt or 0)
        if mn is not None:
            a["main_cnt"] += 1
            a["total_main_net"] = (a["total_main_net"] or 0.0) + mn
    for d, ns in srows:
        if d not in agg:
            agg[d] = {"date": d, "total_main_net": None, "total_amount": 0.0,
                      "by_cat": {}, "etf_count": 0, "main_cnt": 0,
                      "share_net_sub": 0.0, "share_cnt": 0}
            order.append(d)
        a = agg[d]
        a["share_net_sub"] += ns
        a["share_cnt"] += 1
    order.sort()
    return [agg[d] for d in order]


if __name__ == "__main__":
    init_db()
    print("db ok", DB_PATH)
