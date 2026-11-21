"""三页一致性守卫（完全离线）。

背景（丁总 2026-11-21 反馈）：
  ① WorkBuddy 启动时预热页写死「两个面板正在启动」——站点已经是三个页面
     （汪汪队 / 回撤全景 / QDII），以后还会继续加，写死数量的文案必然变成错误信息；
  ② 三个页面顶部的切换卡顺序不一致（回撤页把 QDII 排在回撤前面），来回切会「跳位置」；
  ③ 回撤页缺页脚免责声明与 GitHub 入口，另两页的 GitHub 标识写的是 turbomind
     （GitHub 账号实名是 turbomind66）。

这里把三件事都钉成断言，防止后续改模板时再次漂移。
"""
import os
import re
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 三个面板页：模板 + 对应静态页（静态页由模板重导，必须同步）
PAGES = (
    ("NT 模板", ("national_team", "templates", "index.html")),
    ("QDII 模板", ("qdii", "templates", "index.html")),
    ("DD 模板", ("drawdown", "templates", "index.html")),
    ("NT 静态页", ("docs", "index.html")),
    ("QDII 静态页", ("docs", "qdii", "index.html")),
    ("DD 静态页", ("docs", "dd", "index.html")),
)

# 切换卡的固定顺序（三页必须一致）：汪汪队 → 回撤全景 → 全球 QDII
TAB_ORDER = ("汪汪队", "回撤全景", "全球 QDII")
GH_HANDLE = "turbomind66"

GH_ANCHOR = re.compile(r'<a class="gh"[^>]*>(.*?)</a>', re.S)
TABBAR = re.compile(r'<nav class="tabbar"[^>]*>(.*?)</nav>', re.S)


def read_page(parts) -> str:
    with open(os.path.join(BASE, *parts), encoding="utf-8") as fh:
        return fh.read()


def tabbar(html: str) -> str:
    m = TABBAR.search(html)
    return m.group(1) if m else ""


class TestTabBar(unittest.TestCase):
    def test_tab_order_identical_on_every_page(self):
        for label, parts in PAGES:
            with self.subTest(page=label):
                bar = tabbar(read_page(parts))
                self.assertTrue(bar, "%s 缺少切换卡" % label)
                pos = [bar.find(t) for t in TAB_ORDER]
                self.assertNotIn(-1, pos, "%s 切换卡缺少某一项：%r" % (label, pos))
                self.assertEqual(pos, sorted(pos),
                                 "%s 切换卡顺序与其它页不一致（应为 %s）"
                                 % (label, " → ".join(TAB_ORDER)))

    def test_exactly_one_active_tab(self):
        for label, parts in PAGES:
            with self.subTest(page=label):
                bar = tabbar(read_page(parts))
                self.assertEqual(bar.count("tab active"), 1,
                                 "%s 应当只有一个高亮选项卡" % label)


class TestFooterGithub(unittest.TestCase):
    def test_every_page_has_github_anchor(self):
        for label, parts in PAGES:
            with self.subTest(page=label):
                html = read_page(parts)
                self.assertIn('class="site-foot"', html, "%s 缺少页脚 .site-foot" % label)
                m = GH_ANCHOR.search(html)
                self.assertTrue(m, "%s 页脚缺少 GitHub 入口" % label)
                self.assertIn("github.com/turbomind66/invest-radar",
                              GH_ANCHOR.search(html).group(0).replace("\n", " "),
                              "%s 的 GitHub 链接不是 turbomind66/invest-radar" % label)

    def test_github_label_is_full_handle(self):
        """标识文字必须与 GitHub 账号一致（turbomind66），不能是缩写 turbomind。"""
        for label, parts in PAGES:
            with self.subTest(page=label):
                m = GH_ANCHOR.search(read_page(parts))
                self.assertTrue(m, "%s 页脚缺少 GitHub 入口" % label)
                text = re.sub(r"<[^>]+>", "", m.group(1)).strip()
                self.assertEqual(text, GH_HANDLE,
                                 "%s 的 GitHub 标识文字应为 %s，实际 %r"
                                 % (label, GH_HANDLE, text))


class TestWarmingPage(unittest.TestCase):
    """预热页文案不得写死页面数量/名称（会随页面增加而过时）。"""

    def setUp(self):
        with open(os.path.join(BASE, "combo_app.py"), encoding="utf-8") as fh:
            self.src = fh.read()

    def test_no_hardcoded_page_count(self):
        m = re.search(r"^WARMING = \((.*?)\)\.encode", self.src, re.S | re.M)
        self.assertTrue(m, "combo_app.py 里找不到 WARMING 定义")
        body = m.group(1)
        for word in ("两个", "三个", "面板", "页面数"):
            self.assertNotIn(word, body,
                             "预热页文案写死了「%s」，页面增减后就成了错误提示" % word)

    def test_warming_page_still_auto_refreshes(self):
        m = re.search(r"^WARMING = \((.*?)\)\.encode", self.src, re.S | re.M)
        self.assertIn("http-equiv='refresh'", m.group(1),
                      "预热页缺少 meta refresh，用户会停在过渡页")


if __name__ == "__main__":
    unittest.main()
