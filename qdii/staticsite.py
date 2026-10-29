"""qdii/staticsite.py — 导出 QDII 定投面板静态站（GitHub Pages / WorkBuddy 静态托管）。

复用 qdii_app.route_api 直接拿与服务端完全一致的数据口径（决策/信号/溢价/费率/基金池），
避免重写。产物：
    docs/qdii/qdii_data.json   页面所需的全部接口数据（一次性打包）
    docs/qdii/index.html       注入 STATIC 标记 + worker_url + 内联公共刷新逻辑

用法：
    python qdii/staticsite.py                 # 导出到 docs/qdii/
    python qdii/staticsite.py --out /path     # 导出到指定目录
"""
import os
import sys
import json
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
for p in (BASE, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

import qdii_app  # noqa: E402  route_api 直接给出与服务端一致的 JSON
import db  # noqa: E402

TEMPLATE = os.path.join(BASE, "templates", "index.html")
OUT_DIR = os.environ.get("QDII_DOCS_DIR") or os.path.join(ROOT, "docs", "qdii")
COMMON_JS = os.path.join(ROOT, "common", "static_refresh.js")
ACTIONS_URL = ("https://github.com/turbomind66/invest-radar"
               "/actions/workflows/daily.yml")


def build_payload():
    """从路由层直接取各接口数据（与线上服务端同口径）。"""
    try:
        db.init_db()
    except Exception:
        pass
    return {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "funds": qdii_app.route_api("/api/qdii/funds"),
        "decision": qdii_app.route_api("/api/qdii/decision"),
        "signal": qdii_app.route_api("/api/qdii/signal"),
        "premium": qdii_app.route_api("/api/qdii/premium"),
        "board": qdii_app.route_api("/api/qdii/board"),
        "status": qdii_app.route_api("/api/qdii/status"),
    }


def _common_refresh_js() -> str:
    try:
        with open(COMMON_JS, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def render_index(payload: dict, template: str = TEMPLATE) -> str:
    """把 QDII 模板转成静态页：注入 window.__STATIC__ 标记 + 内联公共刷新逻辑。"""
    with open(template, encoding="utf-8") as f:
        html = f.read()
    flag = {
        "actions_url": ACTIONS_URL,
        "generated_at": payload.get("generated_at", ""),
        # 与国家队一致：仅当部署时设 STATIC_WORKER_URL 才启用 Worker 桥接。
        "worker_url": (os.environ.get("STATIC_WORKER_URL") or "").strip(),
        # 静态站对端面板相对地址：QDII 页在 qdii/ 子目录，国家队页在上一级(/)。
        # 阶段一之前 Pages 只有国家队一页时前端隐藏切换按钮；两页都发布后放开互跳。
        # 可用 STATIC_QDII_PEER_URL 覆盖（自定义目录结构时用），默认 "../" 在 Pages/http.server 下均正确。
        "peer_url": (os.environ.get("STATIC_QDII_PEER_URL") or "../").strip(),
    }
    common_js = _common_refresh_js()
    inject = (
        "<script>window.__STATIC__=%s;</script>\n" % json.dumps(flag, ensure_ascii=False)
        + ("<script>%s</script>\n" % common_js if common_js else "")
        + "</head>"
    )
    return html.replace("</head>", inject, 1)


def export(out_dir: str = OUT_DIR, template: str = TEMPLATE) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    payload = build_payload()
    with open(os.path.join(out_dir, "qdii_data.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(render_index(payload, template))
    return {
        "funds": len((payload.get("funds") or {}).get("funds") or []),
        "decision": len((payload.get("decision") or {}).get("rows") or []),
        "signal": len((payload.get("signal") or {}).get("rows") or []),
        "premium": len((payload.get("premium") or {}).get("rows") or []),
        "generated_at": payload["generated_at"],
    }


def main():
    import argparse
    ap = argparse.ArgumentParser(description="导出 QDII 静态站")
    ap.add_argument("--out", default=OUT_DIR, help="输出目录")
    args = ap.parse_args()
    res = export(out_dir=args.out)
    print("[qdii-static] %s" % json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
