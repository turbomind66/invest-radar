"""回撤全景「一键本地更新」离线单测（/api/dd/refresh）。

背景：本地 Win11 直接 `python national_team/app.py` 时既没有容器里的 scheduler
（dd 任务 17:30），也没有 WorkBuddy 的 NT_AUTO_REFRESH=1，dd.db 只能等 GitHub
Actions 导出静态快照才会变 —— 用户看到的「必须去 Actions 点一下」就是这个。
本测试锁定三件事：
  1) 「落后于最近收盘交易日」的判定（盘中不能把当天算成落后）；
  2) 后台任务的状态机（started → running → done/ok/msg，失败也要有 msg）；
  3) 自动增量只在「既非容器、也非 WorkBuddy」的本地直跑形态启用。

全程不联网：drawdown.updater.refresh 被替换为假实现。
"""
import datetime
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402


class TestLastClosedTradingDay(unittest.TestCase):
    def test_intraday_excludes_today(self):
        """交易日 09:30 还没收盘 → 最近收盘交易日是上一个交易日。"""
        got = app.last_closed_trading_day(datetime.datetime(2026, 11, 23, 9, 30))
        self.assertEqual(got, "2026-11-20")   # 11-21/22 是周末

    def test_after_close_includes_today(self):
        got = app.last_closed_trading_day(datetime.datetime(2026, 11, 23, 16, 0))
        self.assertEqual(got, "2026-11-23")

    def test_skip_weekend(self):
        got = app.last_closed_trading_day(datetime.datetime(2026, 11, 22, 20, 0))
        self.assertEqual(got, "2026-11-20")

    def test_stale_semantics(self):
        self.assertTrue(app._dd_stale(None), "空库算滞后")
        self.assertTrue(app._dd_stale("1990-01-01"))
        # 与「最近收盘交易日」同日不算落后（否则每次启动都白抓一轮）
        self.assertFalse(app._dd_stale(app.last_closed_trading_day()))


class TestDDAutoSwitch(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k)
                       for k in ("NT_DD_AUTO", "APP_ROLE", "NT_AUTO_REFRESH")}
        for k in ("NT_DD_AUTO", "APP_ROLE", "NT_AUTO_REFRESH"):
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_local_default_on(self):
        """本地直跑（无 APP_ROLE、无 NT_AUTO_REFRESH）默认自己补。"""
        self.assertTrue(app.dd_auto_enabled())

    def test_container_and_single_app_opt_out(self):
        """容器由 scheduler 负责、WorkBuddy 由 auto_refresh 负责 → 不重复抓。"""
        os.environ["APP_ROLE"] = "web"
        self.assertFalse(app.dd_auto_enabled())
        os.environ.pop("APP_ROLE")
        os.environ["NT_AUTO_REFRESH"] = "1"
        self.assertFalse(app.dd_auto_enabled())

    def test_explicit_override(self):
        os.environ["APP_ROLE"] = "web"           # 显式开关优先于形态推断
        os.environ["NT_DD_AUTO"] = "1"
        self.assertTrue(app.dd_auto_enabled())
        os.environ["NT_DD_AUTO"] = "0"
        self.assertFalse(app.dd_auto_enabled())


class TestDDBackgroundTask(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._lock = os.environ.get("SCHED_LOCK_PATH")
        # 锁文件放临时目录，别在仓库里留 .sync.lock.dd
        os.environ["SCHED_LOCK_PATH"] = os.path.join(self.tmp, "s.lock")
        app._DD_TASK.update(running=False, start=None, done=None,
                            ok=None, msg="", changed=[], nav=0, log=[])
        self._orig = None

    def tearDown(self):
        if self._orig is not None:
            from drawdown import updater
            updater.refresh = self._orig
        if self._lock is None:
            os.environ.pop("SCHED_LOCK_PATH", None)
        else:
            os.environ["SCHED_LOCK_PATH"] = self._lock
        shutil.rmtree(self.tmp, ignore_errors=True)
        app._DD_TASK.update(running=False, ok=None, msg="", changed=[], nav=0, log=[])

    def _patch_refresh(self, fn):
        from drawdown import updater
        self._orig = updater.refresh
        updater.refresh = fn

    def test_success_updates_state(self):
        seen = []

        def fake(nav=True, log=print):
            seen.append(nav)
            log("fake：抓取 sh000001")
            return {"changed": ["sh000001"], "changed_n": 1, "rows": 1,
                    "nav": 2, "cost": 1.5}

        self._patch_refresh(fake)
        app._dd_refresh_bg()
        self.assertEqual(seen, [True], "默认同时补 QDII 净值（还原模式要用）")
        self.assertFalse(app._DD_TASK["running"])
        self.assertTrue(app._DD_TASK["ok"])
        self.assertIn("1 只有新增日线", app._DD_TASK["msg"])
        self.assertEqual(app._DD_TASK["nav"], 2)
        self.assertTrue(any("fake" in x for x in app._DD_TASK["log"]),
                        "运行日志要能被前端轮询看到")

    def test_failure_keeps_msg_and_clears_running(self):
        def boom(nav=True, log=print):
            raise RuntimeError("上游全断")

        self._patch_refresh(boom)
        app._dd_refresh_bg()
        self.assertFalse(app._DD_TASK["running"], "失败也必须退出 running，否则按钮永远转圈")
        self.assertFalse(app._DD_TASK["ok"])
        self.assertIn("上游全断", app._DD_TASK["msg"])

    def test_trigger_conflict_reports_running(self):
        app._DD_TASK["running"] = True
        self.assertEqual(app.trigger_dd_refresh()["status"], "running")
        self.assertTrue(app._DD_TASK["running"], "已在跑时不应再起一个线程")

    def test_status_payload_has_water_line(self):
        orig = app._dd_water
        app._dd_water = lambda: ("2026-11-20", "2026-11-21 08:00:00")
        try:
            st = app.dd_refresh_status()
        finally:
            app._dd_water = orig
        for k in ("running", "ok", "msg", "log", "latest", "last_fetch",
                  "stale", "target", "changed", "nav"):
            self.assertIn(k, st)
        self.assertIsInstance(st["log"], list)      # 必须是拷贝，前端读的是快照


if __name__ == "__main__":
    unittest.main()
