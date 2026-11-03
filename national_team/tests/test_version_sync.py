"""版本号同步回归测试（完全离线）。

背景：APP_VERSION 是 `national_team/app.py` 与 `qdii/qdii_app.py` 里的硬编码常量，
发版时极易漏改 —— v2.6.3 就漏了，线上 `/api/qdii/status` 一直显示 v2.6.2，
让人误以为部署没生效（实际代码是新代码）。

这里直接从 CHANGELOG.md 解析最新版本号，断言两个常量都随之更新，
以后发版只漏改就会被测试拦下。

注：v2.8.5 起更新日志已从 README.md 迁到独立的 CHANGELOG.md（README 只保留
当前版本与指向链接），故数据源同步改为 CHANGELOG.md。
"""
import os
import re
import unittest

# .../invest-radar/national_team/tests/test_version_sync.py -> 仓库根
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

APP_PY = os.path.join(BASE, "national_team", "app.py")
QDII_APP_PY = os.path.join(BASE, "qdii", "qdii_app.py")
CHANGELOG = os.path.join(BASE, "CHANGELOG.md")


def changelog_latest_version() -> str:
    """从 CHANGELOG.md 取最新（最靠前）的版本号，如 v2.6.3。"""
    with open(CHANGELOG, encoding="utf-8") as fh:
        txt = fh.read()
    m = re.search(r"^- \*\*(v\d+\.\d+\.\d+)\*\*", txt, re.M)
    if not m:
        raise AssertionError("CHANGELOG.md 里找不到 - **vX.Y.Z** 形式的版本号")
    return m.group(1)


def const_version(path: str) -> str:
    """从源码里取 APP_VERSION 常量的字面量，如 v2.6.3-nt。"""
    with open(path, encoding="utf-8") as fh:
        txt = fh.read()
    m = re.search(r"^APP_VERSION\s*=\s*[\"']([^\"']+)[\"']", txt, re.M)
    if not m:
        raise AssertionError("在 %s 里找不到 APP_VERSION 常量" % path)
    return m.group(1)


class TestVersionSync(unittest.TestCase):
    def test_changelog_has_version(self):
        self.assertRegex(changelog_latest_version(), r"^v\d+\.\d+\.\d+$")

    def test_national_team_version_matches_changelog(self):
        latest = changelog_latest_version()
        got = const_version(APP_PY)
        self.assertTrue(
            got.startswith(latest),
            "national_team/app.py 的 APP_VERSION=%r 与 CHANGELOG 最新版 %r 不一致（发版漏改）"
            % (got, latest),
        )

    def test_qdii_version_matches_changelog(self):
        latest = changelog_latest_version()
        got = const_version(QDII_APP_PY)
        self.assertTrue(
            got.startswith(latest),
            "qdii/qdii_app.py 的 APP_VERSION=%r 与 CHANGELOG 最新版 %r 不一致（发版漏改）"
            % (got, latest),
        )

    def test_two_modules_same_version(self):
        a = const_version(APP_PY).split("-")[0]
        b = const_version(QDII_APP_PY).split("-")[0]
        self.assertEqual(a, b, "两个模块的 APP_VERSION 主版本号不一致：%r vs %r" % (a, b))


if __name__ == "__main__":
    unittest.main()
