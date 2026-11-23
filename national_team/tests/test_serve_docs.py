"""serve_docs.py（本地静态站 + 一键刷新服务）的离线测试。

背景：GitHub Pages 上的静态页只能引导用户去 Actions；本地用
`python serve_docs.py` 起的静态站则能原地抓取。这里钉死三件事：

1. 刷新命令**只能写工作目录**（.local_docs），绝不碰 docs/ ——
   docs/ 是 Pages 发布目录兼 Actions 快照，本地库覆盖它会冲掉线上校准结果。
2. 任务状态机 idle→running→ok/error 可被前端轮询（前端据此显示进度/失败原因）。
3. 同页面重复触发被拒（抓取重、东财会限流，连点不能并发）。

全程不联网：Job 的步骤被替换成 `python -c` 假命令。
"""
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import serve_docs  # noqa: E402


class TestPageSteps(unittest.TestCase):
    def test_all_steps_write_into_work_dir(self):
        """导出步骤的 --out 必须指向工作目录；任何一步都不许落到 docs/。

        抓取类步骤（qdii_scheduler --once）只写库不导出，没有 --out 是正常的。
        """
        work = os.path.join(ROOT, ".local_docs")
        for page in ("nt", "dd", "qdii"):
            steps = serve_docs.page_steps(page, work)
            self.assertTrue(steps, "%s 没有步骤" % page)
            for argv in steps:
                exports = any("staticsite" in a or "updater" in a for a in argv)
                if not exports:
                    continue
                outs = [argv[i + 1] for i, a in enumerate(argv) if a == "--out"]
                self.assertEqual(len(outs), 1, "%s 导出步骤缺 --out：%s" % (page, argv))
                self.assertTrue(outs[0].startswith(work),
                                "%s 的 --out 没落在工作目录：%s" % (page, outs[0]))

    def test_qdii_runs_scheduler_before_export(self):
        """QDII 必须先抓（scheduler --once）再导出，否则导出的是旧库。"""
        steps = serve_docs.page_steps("qdii", "/tmp/x")
        self.assertEqual(len(steps), 2)
        self.assertIn("qdii_scheduler.py", steps[0][0])
        self.assertIn("staticsite.py", steps[1][0])

    def test_dd_uses_module_entry(self):
        """drawdown 是包，必须 -m 跑（直接跑文件会 ImportError）。"""
        steps = serve_docs.page_steps("dd", "/tmp/x")
        self.assertEqual(steps[0][:2], ["-m", "drawdown.updater"])
        self.assertIn("--export", steps[0])


class TestJobStateMachine(unittest.TestCase):
    def _job(self, code):
        fake = lambda page, work: [["-c", code]]  # noqa: E731
        return serve_docs.Job("nt", steps_fn=fake)

    def _wait(self, job, timeout=20):
        t0 = time.time()
        while job.running() and time.time() - t0 < timeout:
            time.sleep(0.05)
        return job.snapshot()

    def test_success(self):
        job = self._job("print('hello')")
        self.assertEqual(job.snapshot()["state"], "idle")
        self.assertTrue(job.start())
        snap = self._wait(job)
        self.assertEqual(snap["state"], "ok")
        self.assertTrue(snap["ok"])
        self.assertEqual(snap["error"], "")
        self.assertIn("hello", " ".join(snap["log"]))
        self.assertEqual(snap["state"], "ok")     # 完成后仍可重复读
        self.assertGreaterEqual(snap["elapsed"], 0)

    def test_failure_reports_error_and_tail(self):
        job = self._job("import sys; print('boom'); sys.exit(3)")
        job.start()
        snap = self._wait(job)
        self.assertEqual(snap["state"], "error")
        self.assertFalse(snap["ok"])
        self.assertIn("exit=3", snap["error"])
        self.assertIn("boom", " ".join(snap["log"]))

    def test_duplicate_start_rejected(self):
        job = self._job("import time; time.sleep(0.6)")
        self.assertTrue(job.start())
        self.assertFalse(job.start(), "运行中重复触发应被拒绝")
        self._wait(job)
        # 结束后可以再次启动（重新抓一遍是合法操作）
        self.assertTrue(job.start())
        self._wait(job)

    def test_snapshot_shape_for_frontend(self):
        """前端按这些字段轮询：state/ok/error/elapsed/log。"""
        job = self._job("print('x')")
        snap = job.snapshot()
        for k in ("state", "ok", "error", "started_at", "ended_at", "elapsed", "log"):
            self.assertIn(k, snap)
        self.assertIsInstance(snap["log"], list)


class TestSyncSkeleton(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.src = os.path.join(self.tmp, "docs")
        self.dst = os.path.join(self.tmp, "work")
        os.makedirs(os.path.join(self.src, "dd"))
        self._w(os.path.join(self.src, "index.html"), "<html>v1</html>")
        self._w(os.path.join(self.src, "data.json"), '{"v":1}')
        self._w(os.path.join(self.src, "dd", "index.html"), "<html>dd1</html>")
        self._w(os.path.join(self.src, "dd", "data.json"), '{"dd":1}')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _w(path, text):
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def _r(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_首次整份复制(self):
        self.assertEqual(serve_docs.sync_skeleton(self.src, self.dst), "copytree")
        self.assertEqual(self._r(os.path.join(self.dst, "data.json")), '{"v":1}')
        self.assertEqual(self._r(os.path.join(self.dst, "dd", "data.json")), '{"dd":1}')

    def test_二次只更新骨架不动数据(self):
        serve_docs.sync_skeleton(self.src, self.dst)
        # 本地抓取写入了新数据，同时模板也改了
        self._w(os.path.join(self.dst, "data.json"), '{"v":2}')
        self._w(os.path.join(self.src, "index.html"), "<html>v2</html>")
        n = serve_docs.sync_skeleton(self.src, self.dst)
        self.assertIsInstance(n, int)
        self.assertEqual(self._r(os.path.join(self.dst, "index.html")), "<html>v2</html>",
                         "模板更新要跟着走")
        self.assertEqual(self._r(os.path.join(self.dst, "data.json")), '{"v":2}',
                         "本地抓取的数据不能被快照冲掉")

    def test_docs_目录本身不被改动(self):
        before = self._r(os.path.join(self.src, "data.json"))
        serve_docs.sync_skeleton(self.src, self.dst)
        serve_docs.sync_skeleton(self.src, self.dst)
        self.assertEqual(self._r(os.path.join(self.src, "data.json")), before)


if __name__ == "__main__":
    unittest.main()
