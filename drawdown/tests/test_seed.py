"""drawdown/tests/test_seed.py — gzip 种子恢复单测（零网络，纯临时目录）。

覆盖：空库恢复 / 已有数据幂等跳过 / 无种子降级 / WAL 残留清理 / 原子写。
"""
import gzip
import os
import shutil
import sqlite3
import tempfile
import unittest

from drawdown import db, seed


def _make_seed_db(path, rows=("d1", "d2")):
    """造一个最小可识别的 dd.db（含 dd_daily + dd_meta），供恢复测试用。"""
    c = sqlite3.connect(path)
    c.execute("""CREATE TABLE IF NOT EXISTS dd_daily(
        asset TEXT, date TEXT, close REAL, source TEXT, PRIMARY KEY(asset, date))""")
    c.execute("""CREATE TABLE IF NOT EXISTS dd_meta(k TEXT PRIMARY KEY, v TEXT)""")
    for i, d in enumerate(rows):
        c.execute("INSERT INTO dd_daily VALUES(?,?,?,?)", ("a", d, 100.0 + i, "test"))
    c.commit()
    c.close()


def _gzip_file(src, dst):
    with open(src, "rb") as f, gzip.open(dst, "wb") as g:
        shutil.copyfileobj(f, g)


class TestSeed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ddseed_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_restore_empty_db(self):
        """空目标库 → 从 gzip 恢复，数据与种子一致。"""
        seed_db = os.path.join(self.tmp, "source.db")
        _make_seed_db(seed_db)
        gz = os.path.join(self.tmp, "dd.db.gz")
        _gzip_file(seed_db, gz)
        target = os.path.join(self.tmp, "dd.db")
        logs = []
        ok = seed.ensure(path=target, seed_file=gz, log=logs.append)
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(target))
        info = db.available_assets(target)
        self.assertEqual(info.get("a", {}).get("n"), 2)
        self.assertTrue(any("恢复" in x for x in logs))

    def test_idempotent_skip_existing(self):
        """目标库已有数据 → 跳过，不覆盖。"""
        seed_db = os.path.join(self.tmp, "source.db")
        _make_seed_db(seed_db, ("d1",))
        gz = os.path.join(self.tmp, "dd.db.gz")
        _gzip_file(seed_db, gz)
        target = os.path.join(self.tmp, "dd.db")
        _make_seed_db(target, ("d1", "d2", "d3"))  # 目标数据更多
        logs = []
        ok = seed.ensure(path=target, seed_file=gz, log=logs.append)
        self.assertFalse(ok)
        info = db.available_assets(target)
        self.assertEqual(info.get("a", {}).get("n"), 3)  # 未被覆盖
        self.assertTrue(any("跳过" in x for x in logs))

    def test_no_seed_degrades(self):
        """无种子文件 → 返回 False，不抛异常。"""
        target = os.path.join(self.tmp, "dd.db")
        logs = []
        ok = seed.ensure(path=target, seed_file=os.path.join(self.tmp, "none.gz"),
                         log=logs.append)
        self.assertFalse(ok)
        self.assertTrue(any("无种子" in x for x in logs))

    def test_clears_stale_wal(self):
        """恢复时清理陈旧 -wal/-shm，避免回放覆盖新库。"""
        seed_db = os.path.join(self.tmp, "source.db")
        _make_seed_db(seed_db)
        gz = os.path.join(self.tmp, "dd.db.gz")
        _gzip_file(seed_db, gz)
        target = os.path.join(self.tmp, "dd.db")
        for suffix in ("-wal", "-shm"):
            with open(target + suffix, "wb") as f:
                f.write(b"stale")
        seed.ensure(path=target, seed_file=gz, log=lambda *_: None)
        for suffix in ("-wal", "-shm"):
            self.assertFalse(os.path.exists(target + suffix))

    def test_no_tmp_leftover(self):
        """成功恢复后不残留 .tmp 文件。"""
        seed_db = os.path.join(self.tmp, "source.db")
        _make_seed_db(seed_db)
        gz = os.path.join(self.tmp, "dd.db.gz")
        _gzip_file(seed_db, gz)
        target = os.path.join(self.tmp, "dd.db")
        seed.ensure(path=target, seed_file=gz, log=lambda *_: None)
        self.assertFalse(os.path.exists(target + ".seed.tmp"))


if __name__ == "__main__":
    unittest.main()
