"""drawdown/db.py — 回撤全景页独立 SQLite 存储（与 data.db / qdii.db 分离）。

与 national_team/db.py 同款并发防护惯例（WAL 每库只设一次、进程锁 + 双检、
busy timeout 30s），但库文件独立，绝不碰三个原部署的数据。

表：
- dd_daily     全历史日线（收盘价），PRIMARY KEY(asset, date)
- dd_metrics   预计算回撤指标（peak/dd_pct/days_since_peak/state），PRIMARY KEY(asset, date)
- dd_events    识别出的峰-谷-修复事件段，PRIMARY KEY(asset, peak_date, trough_date)
- dd_nav       QDII 场外基金历史净值（二期溢价还原用）
- dd_meta      抓取状态 / 断点续传水位
"""
import sqlite3
import os
import threading

from . import constants as C

DB_PATH = C.DD_DB_PATH

_INIT_LOCK = threading.Lock()
_INITIALIZED_PATHS = set()
_BUSY_TIMEOUT_MS = int(os.environ.get("NT_SQLITE_BUSY_MS", "30000"))


def init_db(path: str = DB_PATH):
    """建表 + WAL（每库只做一次，双检 + 进程锁，避免并发 DDL 写锁）。"""
    key = os.path.abspath(path)
    if key in _INITIALIZED_PATHS:
        return
    with _INIT_LOCK:
        if key in _INITIALIZED_PATHS:
            return
        os.makedirs(os.path.dirname(key) or ".", exist_ok=True)
        conn = sqlite3.connect(path, timeout=_BUSY_TIMEOUT_MS / 1000.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError:
            pass
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS dd_daily(
            asset TEXT, date TEXT, close REAL, source TEXT,
            PRIMARY KEY(asset, date))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS dd_metrics(
            asset TEXT, date TEXT, close REAL, peak REAL, peak_date TEXT,
            dd_pct REAL, days_since_peak INTEGER, state TEXT,
            PRIMARY KEY(asset, date))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS dd_events(
            asset TEXT, peak_date TEXT, trough_date TEXT, recover_date TEXT,
            peak REAL, trough REAL, drop_pct REAL, recover_days INTEGER,
            ongoing INTEGER, PRIMARY KEY(asset, peak_date, trough_date))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS dd_nav(
            code TEXT, date TEXT, nav REAL,
            PRIMARY KEY(code, date))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS dd_meta(
            key TEXT PRIMARY KEY, value TEXT)""")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_dd_daily_date ON dd_daily(date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_dd_metrics_asset ON dd_metrics(asset, date)")
        conn.commit()
        conn.close()
        _INITIALIZED_PATHS.add(key)


def get_conn(path: str = DB_PATH):
    init_db(path)
    conn = sqlite3.connect(path, timeout=_BUSY_TIMEOUT_MS / 1000.0)
    conn.row_factory = sqlite3.Row
    return conn


def upsert_daily(asset, rows, path=DB_PATH):
    """写入某标的全历史日线 rows=[(date, close), ...]，按 asset+date 幂等。"""
    if not rows:
        return 0
    conn = get_conn(path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO dd_daily(asset,date,close,source) VALUES(?,?,?,?)",
            [(asset, d, c, "em_sina") for d, c in rows])
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def get_daily(asset, path=DB_PATH):
    conn = get_conn(path)
    try:
        rs = conn.execute(
            "SELECT date, close FROM dd_daily WHERE asset=? ORDER BY date", (asset,)).fetchall()
        return [(r["date"], r["close"]) for r in rs]
    finally:
        conn.close()


def get_daily_range(asset, dfrom, dto, path=DB_PATH):
    conn = get_conn(path)
    try:
        rs = conn.execute(
            "SELECT date, close FROM dd_daily WHERE asset=? AND date>=? AND date<=? ORDER BY date",
            (asset, dfrom, dto)).fetchall()
        return [(r["date"], r["close"]) for r in rs]
    finally:
        conn.close()


def save_metrics(asset, rows, path=DB_PATH):
    if not rows:
        return 0
    conn = get_conn(path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO dd_metrics(asset,date,close,peak,peak_date,dd_pct,days_since_peak,state) "
            "VALUES(?,?,?,?,?,?,?,?)", [(asset,) + tuple(r) for r in rows])
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def get_metrics(asset, dfrom=None, dto=None, path=DB_PATH):
    sql = "SELECT date,close,peak,peak_date,dd_pct,days_since_peak,state FROM dd_metrics WHERE asset=?"
    args = [asset]
    if dfrom:
        sql += " AND date>=?"
        args.append(dfrom)
    if dto:
        sql += " AND date<=?"
        args.append(dto)
    sql += " ORDER BY date"
    conn = get_conn(path)
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def save_events(asset, rows, path=DB_PATH):
    conn = get_conn(path)
    try:
        conn.execute("DELETE FROM dd_events WHERE asset=?", (asset,))
        if rows:
            conn.executemany(
                "INSERT OR REPLACE INTO dd_events(asset,peak_date,trough_date,recover_date,"
                "peak,trough,drop_pct,recover_days,ongoing) VALUES(?,?,?,?,?,?,?,?,?)",
                [(asset,) + tuple(r) for r in rows])
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def get_events(asset=None, path=DB_PATH):
    conn = get_conn(path)
    try:
        if asset:
            rs = conn.execute(
                "SELECT * FROM dd_events WHERE asset=? ORDER BY peak_date", (asset,)).fetchall()
        else:
            rs = conn.execute("SELECT * FROM dd_events ORDER BY asset, peak_date").fetchall()
        return [dict(r) for r in rs]
    finally:
        conn.close()


def get_meta(key, default=None, path=DB_PATH):
    conn = get_conn(path)
    try:
        r = conn.execute("SELECT value FROM dd_meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default
    finally:
        conn.close()


def set_meta(key, value, path=DB_PATH):
    conn = get_conn(path)
    try:
        conn.execute("INSERT OR REPLACE INTO dd_meta(key,value) VALUES(?,?)", (key, str(value)))
        conn.commit()
    finally:
        conn.close()


def has_asset(asset, path=DB_PATH):
    conn = get_conn(path)
    try:
        r = conn.execute("SELECT 1 FROM dd_daily WHERE asset=? LIMIT 1", (asset,)).fetchone()
        return bool(r)
    finally:
        conn.close()


def available_assets(path=DB_PATH):
    conn = get_conn(path)
    try:
        rs = conn.execute("SELECT asset, COUNT(*) AS n, MIN(date) AS d0, MAX(date) AS d1 "
                          "FROM dd_daily GROUP BY asset").fetchall()
        return {r["asset"]: {"n": r["n"], "from": r["d0"], "to": r["d1"]} for r in rs}
    finally:
        conn.close()
