"""单应用形态自动补数（auto_refresh）门控回归测试（v2.8.2，完全离线）。

核心断言只有一条，但很关键：**auto_refresh 被 import 时不得改变进程时区**。

为什么重要：auto_refresh 里有一段「把进程时区固定成 Asia/Shanghai」的逻辑
（沙箱可能是 UTC，而 fetcher.fetch_and_save 用 `datetime.date.today()`，
北京 06:00 时 UTC 还是前一天 → 当天数据被写成前一天并覆盖历史行）。
但 `national_team/app.py` 是**无条件** `import auto_refresh` 的（再判断
enabled()），若时区在 import 期就被改掉，那么直接跑 app.py 的三种原部署
（Windows 本地 / Linux 本地 / Linux VPS）进程时区也会被悄悄改掉——
违反「三种原部署方式不受牵连」的铁律。

所以时区修正必须**只在真正启用（NT_AUTO_REFRESH=1）时才生效**。
本测试把这条钉死。
"""
import importlib
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # national_team/
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import auto_refresh  # noqa: E402


class TestAutoRefreshGating(unittest.TestCase):

    def setUp(self):
        self._saved_tz = os.environ.get("TZ")
        self._saved_flag = os.environ.get("NT_AUTO_REFRESH")

    def tearDown(self):
        if self._saved_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._saved_tz
        if self._saved_flag is None:
            os.environ.pop("NT_AUTO_REFRESH", None)
        else:
            os.environ["NT_AUTO_REFRESH"] = self._saved_flag
        auto_refresh._TZ_FIXED = False

    def test_enabled_default_is_false(self):
        """默认关闭 → 三种原部署方式行为完全不变。"""
        os.environ.pop("NT_AUTO_REFRESH", None)
        self.assertFalse(auto_refresh.enabled())

    def test_enabled_truthy_values(self):
        for v in ("1", "true", "TRUE", "yes", "on", " On "):
            os.environ["NT_AUTO_REFRESH"] = v
            self.assertTrue(auto_refresh.enabled(), "应视为启用：%r" % v)

    def test_enabled_falsy_values(self):
        for v in ("0", "", "false", "no", "off", "maybe"):
            os.environ["NT_AUTO_REFRESH"] = v
            self.assertFalse(auto_refresh.enabled(), "应视为禁用：%r" % v)

    def test_import_does_not_change_process_timezone(self):
        """⚠️ 铁律：import auto_refresh 不得动进程时区。

        做法：往 time 模块注入一个假的 tzset（Windows 原生没有 time.tzset），
        reload 一次 auto_refresh，看有没有人调过它 / 有没有写 TZ 变量。
        """
        calls = []
        orig_tzset = getattr(time, "tzset", None)
        time.tzset = lambda: calls.append("tzset")
        os.environ.pop("TZ", None)
        try:
            importlib.reload(auto_refresh)
            self.assertEqual(calls, [], "import 阶段调用了 tzset")
            self.assertIsNone(os.environ.get("TZ"),
                              "import 阶段不应写入 TZ 环境变量")
        finally:
            if orig_tzset is None:
                try:
                    del time.tzset
                except AttributeError:
                    pass
            else:
                time.tzset = orig_tzset

    def test_tz_fix_only_applies_when_explicitly_called(self):
        """显式调用后：TZ 变量被写成 Asia/Shanghai，且 tzset 被调用一次。

        ⚠️ 不断言「时区名变了」——Windows 没有 time.tzset、TZ 变量不生效，
        断言时区名会变成恒真/恒假的哑弹。这里只钉函数契约。
        """
        calls = []
        orig_tzset = getattr(time, "tzset", None)
        time.tzset = lambda: calls.append("tzset")
        os.environ.pop("TZ", None)
        try:
            auto_refresh._ensure_shanghai_tz()
            self.assertEqual(os.environ.get("TZ"), "Asia/Shanghai")
            self.assertEqual(len(calls), 1, "应调用一次 tzset")
        finally:
            if orig_tzset is None:
                try:
                    del time.tzset
                except AttributeError:
                    pass
            else:
                time.tzset = orig_tzset

    def test_tz_fix_is_idempotent(self):
        calls = []
        orig_tzset = getattr(time, "tzset", None)
        time.tzset = lambda: calls.append("tzset")
        try:
            auto_refresh._ensure_shanghai_tz()
            auto_refresh._ensure_shanghai_tz()
            self.assertEqual(len(calls), 1, "重复调用不应重复 tzset")
        finally:
            if orig_tzset is None:
                try:
                    del time.tzset
                except AttributeError:
                    pass
            else:
                time.tzset = orig_tzset

    def test_recent_trading_days_are_sorted_and_unique(self):
        days = auto_refresh._recent_trading_days(5)
        self.assertEqual(days, sorted(days))
        self.assertEqual(len(days), len(set(days)))
        self.assertTrue(all(len(d) == 10 for d in days))

    def test_seed_restore_failure_does_not_raise(self):
        """种子文件缺失时只记日志，绝不抛错阻断启动。

        ⚠️ 用 mock 隔离真实库——绝不能拿仓库里的 data.db 当试验田。
        """
        import staticsite

        class _Boom(Exception):
            pass

        orig = staticsite.restore_missing_from_days_file
        staticsite.restore_missing_from_days_file = lambda *a, **k: (_ for _ in ()).throw(_Boom("模拟种子损坏"))
        try:
            self.assertIsNone(auto_refresh._restore_seed())   # 吞掉异常、返回 None
        finally:
            staticsite.restore_missing_from_days_file = orig


if __name__ == "__main__":
    unittest.main()
