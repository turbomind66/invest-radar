"""SQLite 写库瞬时错误重试回归测试（v2.8.2，完全离线）。

背景（2026-11-02 修 WorkBuddy 版「很多内容不显示」时挖出的既有缺陷）：

    单应用形态（WorkBuddy）里，HTTP 读线程与抓取写线程在**同一个进程**内
    并发读写同一个 SQLite 文件时，某条**新开连接**上的第一条 DML 会偶发报：

        sqlite3.OperationalError: attempt to write a readonly database

    本地复现矩阵（修复前）：
        1 写 0 读 → 正常      0 写 2 读 → 正常
        1 写 1 读 → 复现      2 写 0 读 → 复现      2 写 1 读 → 复现
    与日志模式无关（WAL / DELETE / mmap_size=0 三种配置都复现），
    失败后立刻用新连接写又是正常的（文件属性、query_only、journal_mode 均正常）
    → 判定为 SQLite/文件层的**瞬时**状态，而非库损坏或权限问题。

    修复：db.retry_write 装饰器 —— 写库遇「瞬时类」错误整体重试
    （每次重试都新开一条连接），退避 50ms→100ms→…，最多 6 次。

本测试覆盖：
  1. 瞬时错误重试到成功；
  2. 一直失败时按上限抛出（不无限重试）；
  3. **确定性错误立即抛出**（绝不掩盖真实 bug：语法错/约束错不该重试）；
  4. 真实并发场景（2 写 + 1 读同进程）零错误。
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # national_team/
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import db  # noqa: E402


class TestRetryWrite(unittest.TestCase):

    def test_transient_error_is_retried_until_success(self):
        calls = []

        @db.retry_write
        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise sqlite3.OperationalError("attempt to write a readonly database")
            return "ok"

        self.assertEqual(flaky(), "ok")
        self.assertEqual(len(calls), 3)

    def test_transient_error_gives_up_after_max_retries(self):
        calls = []

        @db.retry_write
        def always_fail():
            calls.append(1)
            raise sqlite3.OperationalError("attempt to write a readonly database")

        with self.assertRaises(sqlite3.OperationalError):
            always_fail()
        self.assertEqual(len(calls), db._WRITE_RETRY_MAX)

    def test_deterministic_error_is_raised_immediately(self):
        """语法错/约束错属于真 bug —— 必须立刻抛出，不能被重试掩盖。"""
        calls = []

        @db.retry_write
        def broken():
            calls.append(1)
            raise sqlite3.OperationalError("no such table: xxx")

        with self.assertRaises(sqlite3.OperationalError):
            broken()
        self.assertEqual(len(calls), 1, "确定性错误不应重试")

    def test_busy_error_is_retried(self):
        calls = []

        @db.retry_write
        def busy():
            calls.append(1)
            if len(calls) < 2:
                raise sqlite3.OperationalError("database is locked")
            return 1

        self.assertEqual(busy(), 1)
        self.assertEqual(len(calls), 2)

    def test_non_operational_error_passes_through(self):
        @db.retry_write
        def boom():
            raise ValueError("不是 SQLite 错")

        with self.assertRaises(ValueError):
            boom()


class TestConcurrentWrites(unittest.TestCase):

    """真实并发：同进程内多连接并发读写同一个库，必须零错误。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="db_conc_")
        self.dbp = os.path.join(self.tmp, "data.db")
        db.init_db(self.dbp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_two_writers_one_reader_no_error(self):
        errs = []
        stop = threading.Event()

        def writer(kind):
            for i in range(120):
                if stop.is_set():
                    return
                try:
                    if kind == 0:
                        db.save_etf_daily(
                            "2026-02-%02d" % (i % 28 + 1),
                            [{"code": "c", "name": "x", "cat": "y", "price": 1.0,
                              "chg": 1.0, "amount": 1.0, "main_net": 1.0}],
                            self.dbp)
                    else:
                        db.save_share_rows(
                            "2026-01-%02d" % (i % 28 + 1),
                            [{"code": "c", "name": "x", "cat": "y", "shares": 1.0,
                              "nav": 1.0, "chg_shares": 1.0, "net_sub": 1.0}],
                            self.dbp)
                except Exception as e:  # noqa: BLE001
                    errs.append("%s: %s" % (type(e).__name__, e))
                    stop.set()
                    return

        def reader():
            for _ in range(120):
                if stop.is_set():
                    return
                try:
                    db.get_dates(self.dbp)
                    db.get_share_dates(self.dbp)
                except Exception as e:  # noqa: BLE001
                    errs.append("%s: %s" % (type(e).__name__, e))
                    stop.set()
                    return

        ts = [threading.Thread(target=writer, args=(0,)),
              threading.Thread(target=writer, args=(1,)),
              threading.Thread(target=reader)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(120)
        self.assertEqual(errs, [], "并发读写出现错误：%s" % errs[:3])


if __name__ == "__main__":
    unittest.main()
