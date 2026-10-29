"""容器调度器 scheduler.py 的单元测试（离线，不联网、不依赖 docker）。"""
import datetime
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import scheduler


class _FakeFcntl(object):
    """模拟 fcntl：raise_=True 时表示锁已被别的进程占用。"""

    LOCK_EX = 2
    LOCK_NB = 4
    LOCK_UN = 8

    def __init__(self, raise_=False):
        self.raise_ = raise_
        self.calls = []

    def flock(self, fd, op):
        self.calls.append(op)
        if self.raise_:
            raise OSError("Resource temporarily unavailable")


class TestParseTimes(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(scheduler.parse_times("06:00,17:10"), [(6, 0), (17, 10)])

    def test_dedup_and_sort(self):
        self.assertEqual(scheduler.parse_times("17:10,06:00,17:10"), [(6, 0), (17, 10)])

    def test_single(self):
        self.assertEqual(scheduler.parse_times("10:30"), [(10, 30)])

    def test_invalid_dropped(self):
        # 25:00 越界、abc 非数字、10:5x 非数字、10 缺分钟 —— 全部丢弃
        self.assertEqual(scheduler.parse_times("25:00,abc,10:5x,10,"), [])

    def test_empty(self):
        self.assertEqual(scheduler.parse_times(""), [])
        self.assertEqual(scheduler.parse_times(None), [])

    def test_whitespace_tolerated(self):
        self.assertEqual(scheduler.parse_times(" 10:30 , 14:00 "), [(10, 30), (14, 0)])


class TestDue(unittest.TestCase):
    def test_hit(self):
        now = datetime.datetime(2026, 10, 29, 10, 30, 15)
        self.assertTrue(scheduler.due(now, [(10, 30), (14, 0)]))

    def test_miss_minute(self):
        # 10:31 不应重复触发 10:30 的任务（防重的第一道关口）
        now = datetime.datetime(2026, 10, 29, 10, 31, 0)
        self.assertFalse(scheduler.due(now, [(10, 30)]))

    def test_miss_hour(self):
        now = datetime.datetime(2026, 10, 29, 11, 30, 0)
        self.assertFalse(scheduler.due(now, [(10, 30)]))

    def test_empty_times(self):
        self.assertFalse(scheduler.due(datetime.datetime(2026, 10, 29, 10, 30), []))


class TestFileLock(unittest.TestCase):
    def test_degrade_without_fcntl(self):
        # Windows / 无 fcntl 平台：降级为不加锁，必须仍能正常运行
        old = scheduler.fcntl
        scheduler.fcntl = None
        try:
            lk = scheduler.FileLock(os.path.join(tempfile.gettempdir(), "ntt_t.lock"))
            self.assertTrue(lk.acquire())
            lk.release()
        finally:
            scheduler.fcntl = old

    def test_acquire_ok(self):
        old = scheduler.fcntl
        scheduler.fcntl = _FakeFcntl(raise_=False)
        try:
            lk = scheduler.FileLock(os.path.join(tempfile.gettempdir(), "ntt_ok.lock"))
            self.assertTrue(lk.acquire())
            lk.release()
        finally:
            scheduler.fcntl = old

    def test_acquire_busy_returns_false(self):
        # 锁被占用时必须返回 False（调度器据此跳过本次，避免并发写 SQLite）
        old = scheduler.fcntl
        scheduler.fcntl = _FakeFcntl(raise_=True)
        try:
            lk = scheduler.FileLock(os.path.join(tempfile.gettempdir(), "ntt_busy.lock"))
            self.assertFalse(lk.acquire())
        finally:
            scheduler.fcntl = old


class TestRunTask(unittest.TestCase):
    def test_skip_when_locked(self):
        # 占用锁后，run_task 应直接跳过且不启动子进程
        old_acquire = scheduler.FileLock.acquire
        scheduler.FileLock.acquire = lambda self: False
        try:
            called = {"n": 0}
            old_run = scheduler.subprocess.run

            def _spy(*a, **k):
                called["n"] += 1
                return None

            scheduler.subprocess.run = _spy
            try:
                ok = scheduler.run_task("full")
            finally:
                scheduler.subprocess.run = old_run
            self.assertFalse(ok)
            self.assertEqual(called["n"], 0, "锁占用时不应启动子进程")
        finally:
            scheduler.FileLock.acquire = old_acquire

    def test_tick_args_include_mode(self):
        # tick 必须走轻量模式，否则会跑全量回补（盘中采样会很慢）
        seen = {}

        def _spy(cmd, **k):
            seen["cmd"] = cmd
            return None

        old_run = scheduler.subprocess.run
        old_fcntl = scheduler.fcntl
        scheduler.fcntl = _FakeFcntl(raise_=False)
        scheduler.subprocess.run = _spy
        try:
            scheduler.run_task("tick")
        finally:
            scheduler.subprocess.run = old_run
            scheduler.fcntl = old_fcntl
        cmd = seen.get("cmd") or []
        self.assertIn("--mode=tick", cmd)
        self.assertTrue(any(str(x).endswith("staticsite.py") for x in cmd))


if __name__ == "__main__":
    unittest.main(verbosity=2)
