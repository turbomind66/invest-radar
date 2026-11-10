"""版本号同步回归测试（完全离线）。

背景：APP_VERSION 是 `national_team/app.py` 与 `qdii/qdii_app.py` 里的硬编码常量，
发版时极易漏改 —— v2.6.3 就漏了，线上 `/api/qdii/status` 一直显示 v2.6.2，
让人误以为部署没生效（实际代码是新代码）。

这里直接从 CHANGELOG.md 解析最新版本号，断言两个常量都随之更新，
以后发版只漏改就会被测试拦下。

注：v2.8.6 起更新日志已从 README.md 迁到独立的 CHANGELOG.md（README 只保留
当前版本与指向链接），故数据源同步改为 CHANGELOG.md。

v2.8.15 起另附免责提示守卫：静态页由模板重导生成，历史上出现过盘中采样把
静态 HTML 覆盖回旧模板的事故（v2.8.13），这里守住「两个模板 + 两份静态页 +
配置速查页」都必须带「仅作参考」声明。
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


PANEL_PAGES = (
    ("NT 模板", "national_team", "templates", "index.html"),
    ("QDII 模板", "qdii", "templates", "index.html"),
    ("NT 静态页", "docs", "index.html"),
    ("QDII 静态页", "docs", "qdii", "index.html"),
)
SETUP_PAGE = os.path.join(BASE, "cron_trigger_setup.html")


def read_page(rel_parts) -> str:
    with open(os.path.join(BASE, *rel_parts), encoding="utf-8") as fh:
        return fh.read()


def disclaimer_bar(html: str) -> str:
    """取出顶部免责条整段，用于比对模板与静态页是否同版。"""
    m = re.search(r'<div class="disclaimer-bar"[^>]*>.*?</div>', html, re.S)
    return m.group(0) if m else ""


class TestDisclaimer(unittest.TestCase):
    def test_every_panel_has_top_and_footer_disclaimer(self):
        for label, *parts in PANEL_PAGES:
            with self.subTest(page=label):
                html = read_page(tuple(parts))
                self.assertTrue(disclaimer_bar(html), "%s 缺少顶部免责条" % label)
                self.assertIn("仅作参考", html, "%s 免责条缺少「仅作参考」" % label)
                self.assertIn("不构成任何投资建议", html, "%s 免责条缺少投资建议声明" % label)
                self.assertIn('class="disc"', html, "%s 缺少页脚免责声明" % label)
                self.assertIn("据此操作", html, "%s 缺少风险自担声明" % label)
                self.assertIn("风险自担", html, "%s 缺少风险自担声明" % label)

    def test_static_pages_mirror_template_disclaimer(self):
        """静态页必须由含免责条的新模板重导，防止被旧模板覆盖回去。"""
        nt_tpl = disclaimer_bar(read_page(("national_team", "templates", "index.html")))
        qdii_tpl = disclaimer_bar(read_page(("qdii", "templates", "index.html")))
        self.assertEqual(disclaimer_bar(read_page(("docs", "index.html"))), nt_tpl)
        self.assertEqual(disclaimer_bar(read_page(("docs", "qdii", "index.html"))), qdii_tpl)

    def test_setup_page_has_disclaimer(self):
        # cron_trigger_setup.html 被 .gitignore 排除（含内部信息，不进公开仓库），
        # CI checkout 后不存在；只在本地/部署副本存在时校验，避免 CI 误红。
        if not os.path.exists(SETUP_PAGE):
            self.skipTest("cron_trigger_setup.html 未纳入仓库，跳过")
        with open(SETUP_PAGE, encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("仅作参考", html)
        self.assertIn("不构成投资建议", html)


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
