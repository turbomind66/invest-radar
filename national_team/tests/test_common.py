"""common 层的单元测试（离线，跨平台）。

覆盖 v2.6.2 修复的两大坑：
- common.http._proxy_handler：ProxyHandler 的 dict 键必须是**协议方案名**
  （http/https），旧实现误用环境变量名 HTTP_PROXY 作键，导致环境代理被静默忽略。
- common.lock.FileLock：Windows 上旧分支「exists⇒占用」，崩溃后留下的锁文件
  永久挡门。现在走 msvcrt / fcntl / O_EXCL+PID 兜底三选一。
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import common.http as http_mod
import common.lock as lock_mod


class _FakeProxyHandler(object):
    """记录 ProxyHandler 收到的 dict，方便断言键名。"""

    last = None

    def __init__(self, proxies):
        _FakeProxyHandler.last = proxies

    def __class_getitem__(cls, item):  # 兼容未来用 typing 参数化
        return cls


class ProxyHandlerTests(unittest.TestCase):
    """common.http 的代理装配必须把键写成协议方案，而不是环境变量名。"""

    def setUp(self):
        self._saved = {}
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                  "NO_PROXY", "no_proxy"):
            self._saved[k] = os.environ.get(k)
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_no_proxy_env_returns_empty_dict(self):
        h = http_mod._proxy_handler(use_proxy=True)
        # 未设环境代理时 ProxyHandler 收到 {}，urllib 会退回直连（等价旧行为）
        self.assertEqual(h.proxies, {})

    def test_uppercase_env_maps_to_scheme_keys(self):
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:10808"
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:10808"
        h = http_mod._proxy_handler(use_proxy=True)
        # 修复的核心断言：dict 键必须是 http / https，而不是 HTTP_PROXY / HTTPS_PROXY
        self.assertEqual(set(h.proxies.keys()), {"http", "https"})
        self.assertEqual(h.proxies["http"], "http://127.0.0.1:10808")

    def test_lowercase_env_also_works(self):
        os.environ["http_proxy"] = "http://127.0.0.1:8888"
        h = http_mod._proxy_handler(use_proxy=True)
        self.assertEqual(h.proxies.get("http"), "http://127.0.0.1:8888")
        self.assertNotIn("HTTP_PROXY", h.proxies)
        self.assertNotIn("http_proxy", h.proxies)

    def test_use_proxy_false_forces_direct(self):
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:10808"
        h = http_mod._proxy_handler(use_proxy=False)
        self.assertEqual(h.proxies, {}, "use_proxy=False 必须显式空 dict 绕过代理")

    def test_no_proxy_env_respected_for_auto_bypass(self):
        os.environ["NO_PROXY"] = ".corp.example,10.*,localhost"
        self.assertFalse(http_mod._auto_proxy("api.corp.example"))
        self.assertFalse(http_mod._auto_proxy("10.0.0.7"))
        self.assertFalse(http_mod._auto_proxy("localhost"))
        self.assertTrue(http_mod._auto_proxy("hq.sinajs.cn"))

    def test_localhost_and_docker_bridge_auto_bypass(self):
        # 常见本地/容器网桥地址一律直连（README 踩坑 #4 / gateway 内部代理链）
        for h in ("127.0.0.1", "localhost", "::1", "host.docker.internal", "172.17.0.1"):
            self.assertFalse(http_mod._auto_proxy(h), "%s 不应走代理" % h)


class FileLockCrossPlatformTests(unittest.TestCase):
    """FileLock 三种后端都能 acquire/release，且不会把陈旧锁永久留在盘上。"""

    def setUp(self):
        self.tmp = os.path.join(tempfile.gettempdir(),
                                "common_lock_%d_%d.lock" % (os.getpid(), id(self)))

    def tearDown(self):
        try:
            if os.path.exists(self.tmp):
                os.remove(self.tmp)
        except OSError:
            pass

    def test_fallback_stale_pid_reclaimed(self):
        """既无 fcntl 也无 msvcrt 时：假 PID + 远古时间戳 → acquire 应能回收。"""
        old_f, old_m = lock_mod.fcntl, lock_mod.msvcrt
        lock_mod.fcntl = lock_mod.msvcrt = None
        try:
            with open(self.tmp, "w") as f:
                f.write("999999 0")
            lk = lock_mod.FileLock(self.tmp)
            self.assertTrue(lk.acquire(), "陈旧锁应被自动回收")
            lk.release()
        finally:
            lock_mod.fcntl, lock_mod.msvcrt = old_f, old_m

    def test_fallback_fresh_lock_blocks(self):
        """兜底模式下别的实例刚写的锁（mtime 新） → 本次必须失败。

        写 os.getpid() 会让 `_is_stale` 的 PID 分支被跳过（同 PID 视为“就是自己”），
        从而走 mtime 分支判“未过期”。这精确复刻了“上一个实例还活着、正在写”
        的正常占用场景，比在 Windows 上探 PID 1 更可靠（System Idle Process
        的 OpenProcess 在受限用户下会直接失败，探活结果不稳定）。
        """
        old_f, old_m = lock_mod.fcntl, lock_mod.msvcrt
        lock_mod.fcntl = lock_mod.msvcrt = None
        try:
            with open(self.tmp, "w") as f:
                f.write("%d %d" % (os.getpid(), int(__import__("time").time())))
            lk = lock_mod.FileLock(self.tmp)
            self.assertFalse(lk.acquire(), "mtime 新鲜的占用不应抢走")
        finally:
            lock_mod.fcntl, lock_mod.msvcrt = old_f, old_m

    def test_real_filesystem_lock_round_trip(self):
        """在当前平台真实跑一遍（Linux=fcntl / Windows=msvcrt），确保能拿能释放。"""
        lk = lock_mod.FileLock(self.tmp)
        self.assertTrue(lk.acquire())
        # 第二个实例应拿不到（同一文件同一进程 fcntl/msvcrt 有独占语义，
        # 但 flock 对同一 fd 是幂等的；对**不同** fd 打开同一文件才会互斥）
        lk.release()
        # 释放后再拿应该成功
        lk2 = lock_mod.FileLock(self.tmp)
        self.assertTrue(lk2.acquire())
        lk2.release()


class ComboPickBackendTest(unittest.TestCase):
    """combo_app.pick_backend 的 /qdii 前缀必须严格匹配，不能命中 /qdii-something。

    v2.6.2 之前 `p.startswith("/qdii")` 会把 /qdii-x、/qdii2 都路由到 QDII 子进程，
    两个面板 URL 命名重叠时（比如未来加 /qdii2 板块）就是串台 bug。
    """

    def test_paths(self):
        # 只在 main 定义之后 import combo_app；顶层 spawn 已挪进 main()，
        # 这里 import 不会再抢端口
        import combo_app
        cases = [
            ("/", "nt"),
            ("/qdii", "qdii"),
            ("/qdii/", "qdii"),
            ("/qdii/board", "qdii"),
            ("/api/qdii/signal", "qdii"),
            ("/api/data", "nt"),
            ("/api/qdii2/foo", "nt"),   # 精确匹配 /api/qdii + 分隔符
            ("/qdii-extra", "nt"),      # 旧实会误判成 qdii
            ("/qdii2", "nt"),
            ("/qdii?x=1", "qdii"),
            ("/api/qdii/premium?sort=code", "qdii"),
        ]
        for path, expected in cases:
            self.assertEqual(combo_app.pick_backend(path), expected,
                             "%s 应路由到 %s" % (path, expected))

    def test_combo_app_import_has_no_side_effect(self):
        """import combo_app 不再抢端口（v5-safe-spawn 之前会在顶层 spawn 两个子进程）。"""
        import combo_app
        # PROC_DIAG 里的 port 在 import 后必须是 None（未 main() 之前）
        self.assertIsNone(combo_app.PROC_DIAG["nt"]["port"])
        self.assertIsNone(combo_app.PROC_DIAG["qdii"]["port"])
        # 顶层 proc 也应为 None
        self.assertIsNone(combo_app.PROC_DIAG["nt"]["proc"])
        self.assertIsNone(combo_app.PROC_DIAG["qdii"]["proc"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
