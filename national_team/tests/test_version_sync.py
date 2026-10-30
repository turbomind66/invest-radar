"""版本号同步回归测试（完全离线）。

背景：APP_VERSION 是 `national_team/app.py` 与 `qdii/qdii_app.py` 里的硬编码常量，
发版时极易漏改 —— v2.6.3 就漏了，线上 `/api/qdii/status` 一直显示 v2.6.2，
让人误以为部署没生效（实际代码是新代码）。

这里直接从 README「更新日志」解析最新版本号，断言两个常量都随之更新，
以后发版只漏改就会被测试拦下。
"""
import os
import re
import unittest

# .../invest-radar/national_team/tests/test_version_sync.py -> 仓库根
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

APP_PY = os.path.join(BASE, "national_team", "app.py")
QDII_APP_PY = os.path.join(BASE, "qdii", "qdii_app.py")
README = os.path.join(BASE, "README.md")


def readme_latest_version() -> str:
    """从 README「更新日志」章节取最新（最靠前）的版本号，如 v2.6.3。"""
    with open(README, encoding="utf-8") as fh:
        txt = fh.read()
    idx = txt.index("## 更新日志")
    m = re.search(r"^- \*\*(v\d+\.\d+\.\d+)\*\*", txt[idx:], re.M)
    if not m:
        raise AssertionError("README「更新日志」里找不到 - **vX.Y.Z** 形式的版本号")
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
    def test_readme_has_changelog(self):
        self.assertRegex(readme_latest_version(), r"^v\d+\.\d+\.\d+$")

    def test_national_team_version_matches_readme(self):
        latest = readme_latest_version()
        got = const_version(APP_PY)
        self.assertTrue(
            got.startswith(latest),
            "national_team/app.py 的 APP_VERSION=%r 与 README 最新版 %r 不一致（发版漏改）"
            % (got, latest),
        )

    def test_qdii_version_matches_readme(self):
        latest = readme_latest_version()
        got = const_version(QDII_APP_PY)
        self.assertTrue(
            got.startswith(latest),
            "qdii/qdii_app.py 的 APP_VERSION=%r 与 README 最新版 %r 不一致（发版漏改）"
            % (got, latest),
        )

    def test_two_modules_same_version(self):
        a = const_version(APP_PY).split("-")[0]
        b = const_version(QDII_APP_PY).split("-")[0]
        self.assertEqual(a, b, "两个模块的 APP_VERSION 主版本号不一致：%r vs %r" % (a, b))


if __name__ == "__main__":
    unittest.main()
