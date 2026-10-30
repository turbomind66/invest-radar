"""容器调度器 scheduler.py 的单元测试（离线，不联网、不依赖 docker）。

2026-10-30 变更：FileLock 从 scheduler 本地类改成 common.lock.FileLock
（Windows 走 msvcrt、Linux 走 fcntl，跨平台强互斥），旧测试通过给
`scheduler.fcntl` 赋值模拟的行为已不存在，改为 patch `common.lock` 模块
里的 `fcntl` / `msvcrt` 引用。
"""
import datetime
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import scheduler
import common.lock as _lock_mod


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


class _FakeMsvcrt(object):
    """模拟 msvcrt：raise_=True 时 locking(LK_NBLCK) 抛 OSError 表示已被占用。

    msvcrt 只暴露 locking()，没有 unlocking()；解锁靠 mode=msvcrt.LK_UNLCK。
    """

    LK_NBLCK = 1
    LK_UNLCK = 2

    def __init__(self, raise_=False):
        self.raise_ = raise_

    def locking(self, fd, mode, nbytes):
        if mode == self.LK_NBLCK and self.raise_:
            raise OSError("lock violation")


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
    """common.lock.FileLock：三种后端（fcntl / msvcrt / O_EXCL 兜底）都能互斥。"""

    def setUp(self):
        self.tmp = os.path.join(tempfile.gettempdir(), "ntt_t_%d.lock" % os.getpid())

    def tearDown(self):
        try:
            if os.path.exists(self.tmp):
                os.remove(self.tmp)
        except OSError:
            pass

    def test_fcntl_backend_acquire_ok(self):
        old_f, old_m = _lock_mod.fcntl, _lock_mod.msvcrt
        _lock_mod.fcntl, _lock_mod.msvcrt = _FakeFcntl(raise_=False), None
        try:
            lk = _lock_mod.FileLock(self.tmp)
            self.assertTrue(lk.acquire())
            lk.release()
        finally:
            _lock_mod.fcntl, _lock_mod.msvcrt = old_f, old_m

    def test_fcntl_backend_busy_returns_false(self):
        # 锁被别的进程占用时必须返回 False（调度器据此跳过本次，避免并发写 SQLite）
        old_f, old_m = _lock_mod.fcntl, _lock_mod.msvcrt
        _lock_mod.fcntl, _lock_mod.msvcrt = _FakeFcntl(raise_=True), None
        try:
            lk = _lock_mod.FileLock(self.tmp)
            self.assertFalse(lk.acquire())
        finally:
            _lock_mod.fcntl, _lock_mod.msvcrt = old_f, old_m

    def test_msvcrt_backend_acquire_ok(self):
        # Windows 主路径：msvcrt 提供真互斥（旧实现直接不锁 → 会并发抢写）
        old_f, old_m = _lock_mod.fcntl, _lock_mod.msvcrt
        _lock_mod.fcntl, _lock_mod.msvcrt = None, _FakeMsvcrt(raise_=False)
        try:
            lk = _lock_mod.FileLock(self.tmp)
            self.assertTrue(lk.acquire())
            lk.release()
        finally:
            _lock_mod.fcntl, _lock_mod.msvcrt = old_f, old_m

    def test_msvcrt_backend_busy_returns_false(self):
        old_f, old_m = _lock_mod.fcntl, _lock_mod.msvcrt
        _lock_mod.fcntl, _lock_mod.msvcrt = None, _FakeMsvcrt(raise_=True)
        try:
            lk = _lock_mod.FileLock(self.tmp)
            self.assertFalse(lk.acquire())
        finally:
            _lock_mod.fcntl, _lock_mod.msvcrt = old_f, old_m

    def test_fallback_stale_lock_reclaimed(self):
        # 兜底：既没 fcntl 又没 msvcrt，遇到陈旧锁文件必须能自我回收，
        # 而不是永久挡在门外（旧 Windows 分支就是这样卡死过调度）
        old_f, old_m = _lock_mod.fcntl, _lock_mod.msvcrt
        _lock_mod.fcntl, _lock_mod.msvcrt = None, None
        try:
            with open(self.tmp, "w") as f:
                f.write("999999 0")  # 假 PID + 远古时间戳
            lk = _lock_mod.FileLock(self.tmp)
            self.assertTrue(lk.acquire(), "陈旧锁必须能回收")
            lk.release()
        finally:
            _lock_mod.fcntl, _lock_mod.msvcrt = old_f, old_m


class TestRunTask(unittest.TestCase):
    def test_skip_when_locked(self):
        # 占用锁后，run_task 应直接跳过且不启动子进程
        old_acquire = _lock_mod.FileLock.acquire
        _lock_mod.FileLock.acquire = lambda self: False
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
            _lock_mod.FileLock.acquire = old_acquire

    def test_nonzero_returncode_marks_failure(self):
        # 2026-10-30 修：staticsite 崩溃/非零退出必须记成失败，
        # 旧实现丢弃 returncode、日志谎报「完成」→ 定时任务静默坏掉
        class _Proc(object):
            returncode = 1

        old_run = scheduler.subprocess.run
        old_acquire = _lock_mod.FileLock.acquire
        _lock_mod.FileLock.acquire = lambda self: True
        scheduler.subprocess.run = lambda *a, **k: _Proc()
        try:
            ok = scheduler.run_task("full")
        finally:
            scheduler.subprocess.run = old_run
            _lock_mod.FileLock.acquire = old_acquire
        self.assertFalse(ok, "非零退出码必须让 run_task 返回 False")

    def test_tick_args_include_mode(self):
        # tick 必须走轻量模式，否则会跑全量回补（盘中采样会很慢）
        seen = {}

        class _Proc(object):
            returncode = 0

        def _spy(cmd, **k):
            seen["cmd"] = cmd
            return _Proc()

        old_run = scheduler.subprocess.run
        old_acquire = _lock_mod.FileLock.acquire
        _lock_mod.FileLock.acquire = lambda self: True
        scheduler.subprocess.run = _spy
        try:
            scheduler.run_task("tick")
        finally:
            scheduler.subprocess.run = old_run
            _lock_mod.FileLock.acquire = old_acquire
        cmd = seen.get("cmd") or []
        self.assertIn("--mode=tick", cmd)
        self.assertTrue(any(str(x).endswith("staticsite.py") for x in cmd))


if __name__ == "__main__":
    unittest.main(verbosity=2)
