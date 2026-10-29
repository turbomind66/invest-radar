"""qdii/db.py — QDII 定投决策面板 SQLite 存储层（零第三方依赖）。

数据库路径由环境变量 NT_QDII_DB_PATH 控制（默认本模块目录下 qdii.db），
与 national_team 的 data.db 完全分离。所有写入通过 UPSERT（INSERT OR REPLACE /
ON CONFLICT）保证重跑幂等。
"""
import sqlite3
import os

DB_PATH = os.environ.get("NT_QDII_DB_PATH") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "qdii.db")


# ---- 第4节数据模型（与开发文档一致） ----
SCHEMA = [
    """CREATE TABLE IF NOT EXISTS qdii_fund (
        code TEXT PRIMARY KEY, name TEXT, index_name TEXT,
        category TEXT, company TEXT, fund_type TEXT,
        online_code TEXT, is_offline INTEGER, note TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS qdii_limit_status (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT, date TEXT,
        sub_status TEXT, limit_amount REAL, is_limited INTEGER,
        next_open_date TEXT, buy_start REAL, redeem_status TEXT,
        source TEXT, fetched_at TEXT,
        UNIQUE(code, date)
    )""",
    """CREATE TABLE IF NOT EXISTS qdii_platform_fee (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT, platform TEXT,
        on_sale INTEGER, sub_fee_rate REAL, sub_fee_discount REAL,
        redeem_fee_ladder TEXT,
        sales_service_fee REAL, mgmt_fee REAL, custodian_fee REAL,
        redeem_free_days INTEGER, dca_status TEXT,
        large_sub_limit REAL, buy_start REAL,
        source TEXT, fetched_at TEXT,
        UNIQUE(code, platform)
    )""",
    """CREATE TABLE IF NOT EXISTS qdii_us_signal (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT, symbol TEXT, name TEXT,
        last_price REAL, change_pct REAL,
        is_realtime INTEGER, as_of TEXT, fetched_at TEXT,
        change_bp REAL,
        UNIQUE(date, symbol)
    )""",
    """CREATE TABLE IF NOT EXISTS qdii_tracking_error (
        code TEXT PRIMARY KEY,
        te_disclosed REAL, te_computed REAL, tracking_deviation REAL,
        method TEXT, as_of_date TEXT, source TEXT, benchmark TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS qdii_premium (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT, code TEXT, online_code TEXT,
        market_price REAL, nav REAL, nav_date TEXT,
        premium_pct REAL, source TEXT, fetched_at TEXT,
        est_nav REAL, est_premium_pct REAL,
        bench_label TEXT, bench_asof TEXT,
        UNIQUE(date, code)
    )""",
    """CREATE TABLE IF NOT EXISTS qdii_decision (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT, code TEXT, strategy TEXT,
        suggested_multiple REAL, reason_json TEXT,
        UNIQUE(date, code)
    )""",
    # 大类维度决策（2026-10-29 起为页面主口径，逐基金决策仍保留在 qdii_decision 供追溯）
    """CREATE TABLE IF NOT EXISTS qdii_decision_cat (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT, category TEXT, strategy TEXT,
        suggested_multiple REAL, status_text TEXT, reason_json TEXT,
        total INTEGER, open_cnt INTEGER, suspend_cnt INTEGER, limit_cnt INTEGER,
        UNIQUE(date, category)
    )""",
    """CREATE TABLE IF NOT EXISTS qdii_meta (k TEXT PRIMARY KEY, v TEXT)""",
]


def init_db(path: str = DB_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    for stmt in SCHEMA:
        cur.execute(stmt)
    # 兼容旧库：缺列则补（幂等，已存在则忽略）
    for _tbl, _col, _decl in (
        ("qdii_tracking_error", "benchmark", "TEXT"),
        ("qdii_platform_fee", "buy_start", "REAL"),
        ("qdii_premium", "est_nav", "REAL"),
        ("qdii_premium", "est_premium_pct", "REAL"),
        ("qdii_premium", "bench_label", "TEXT"),
        ("qdii_premium", "bench_asof", "TEXT"),
        ("qdii_us_signal", "change_bp", "REAL"),
    ):
        try:
            cur.execute("ALTER TABLE %s ADD COLUMN %s %s" % (_tbl, _col, _decl))
        except sqlite3.OperationalError:
            pass
    conn.commit()
    return conn


def get_conn(path: str = DB_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def set_meta(k, v, path: str = DB_PATH):
    conn = get_conn(path)
    conn.execute(
        "INSERT INTO qdii_meta(k, v) VALUES(?, ?) "
        "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
    conn.commit()
    conn.close()


def get_meta(k, default=None, path: str = DB_PATH):
    conn = get_conn(path)
    row = conn.execute("SELECT v FROM qdii_meta WHERE k=?", (k,)).fetchone()
    conn.close()
    return row["v"] if row else default


def upsert_row(conn, table, cols, vals, conflict):
    """通用 UPSERT。conflict 形如 'code,date'。"""
    placeholders = ",".join("?" for _ in cols)
    sql = (
        "INSERT INTO %s(%s) VALUES(%s) ON CONFLICT(%s) DO UPDATE SET %s"
        % (table, ",".join(cols), placeholders, conflict,
           ",".join("%s=excluded.%s" % (c, c) for c in cols))
    )
    conn.execute(sql, vals)


# ---- 基金池 ----
def seed_fund_pool(rows, path: str = DB_PATH):
    conn = get_conn(path)
    # 清理**不在本次种子清单里**的历史记录：基金池会随新发现增删，
    # 只 upsert 不删除的话，库里会残留早已移出池子的基金，
    # 决策统计与页面表格都会把它们算进去（2026-10-29 实测：残留 1 只导致
    # 标普500 类别被统计成 14 只而不是 13 只）。
    keep = [r["code"] for r in rows]
    if keep:
        marks = ",".join("?" for _ in keep)
        conn.execute("DELETE FROM qdii_fund WHERE code NOT IN (%s)" % marks, keep)
    for r in rows:
        conn.execute(
            "INSERT INTO qdii_fund(code,name,index_name,category,company,"
            "fund_type,online_code,is_offline,note) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(code) DO UPDATE SET name=excluded.name,"
            "index_name=excluded.index_name,category=excluded.category,"
            "company=excluded.company,fund_type=excluded.fund_type,"
            "online_code=excluded.online_code,is_offline=excluded.is_offline,"
            "note=excluded.note",
            (r["code"], r["name"], r["index_name"], r["category"], r["company"],
             r["fund_type"], r["online_code"], r["is_offline"], r["note"]))
    conn.commit()
    conn.close()


def get_fund_pool(path: str = DB_PATH):
    conn = get_conn(path)
    rows = conn.execute("SELECT * FROM qdii_fund ORDER BY category, code").fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    init_db()
    print("QDII db initialized at", DB_PATH)
