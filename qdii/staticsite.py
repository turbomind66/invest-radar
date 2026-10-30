"""qdii/staticsite.py — 导出 QDII 定投面板静态站（GitHub Pages / WorkBuddy 静态托管）。

复用 qdii_app.route_api 直接拿与服务端完全一致的数据口径（决策/信号/溢价/费率/基金池），
避免重写。产物：
    docs/qdii/qdii_data.json   页面所需的全部接口数据（一次性打包）
    docs/qdii/index.html       注入 STATIC 标记 + worker_url + 内联公共刷新逻辑

用法：
    python qdii/staticsite.py                 # 导出到 docs/qdii/
    python qdii/staticsite.py --out /path     # 导出到指定目录
    python qdii/staticsite.py --allow-empty   # 空数据也写（本地调试用，CI 别开）

⚠️ 2026-10-30 修（GitHub Actions 空覆盖 / 隐私 / 原子写）：
1. build_payload 现在会先 `fetcher_pool.init_pool()` 把 FUND_POOL 59 只
   种子灌进 qdii.db —— 否则在 Actions 干净 runner 上（qdii.db 被 .gitignore
   排除），qdii_fund 表是空的，board/decision 全 zero，git push 会把
   docs/qdii/qdii_data.json 覆盖成空白页。真实抓取（limit/fee/signal/premium）
   走 qdii_scheduler --once，见 daily.yml 的抓取步骤。
2. 默认拒绝写入空 payload（board.rows=0 且 target 是非空的旧文件时），
   避免上游偶发全挂就洗掉整站快照。--allow-empty 才强制。
3. status 里的 db 绝对路径不再落进公开 JSON：本地部署排查用 /api/qdii/status
   仍拿得到，Pages 端只保留 basename。
4. qdii_data.json 与 index.html 都改为「先写 .tmp 再 os.replace」的原子写，
   中途异常/半写状态不会污染 Pages；json.dump 也补上 allow_nan=False +
   default=str 兜底，上游偶发 inf/NaN 不会静默写出浏览器 JSON.parse 拒收的
   `NaN` 字面量（会让整站白屏）。
"""
import os
import sys
import json
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
# BASE（本模块目录）必须排在 ROOT（部署根）之前 —— 否则部署根下存在同名
# 模块时（沙箱里确有旧副本），`import constants/db` 会加载到那一份，
# 表现为「部署了新代码、线上却跑着旧行为」。
for p in (ROOT, BASE):
    if p not in sys.path:
        sys.path.insert(0, p)
if sys.path and sys.path[0] != BASE:
    sys.path.remove(BASE)
    sys.path.insert(0, BASE)

import qdii_app  # noqa: E402  route_api 直接给出与服务端一致的 JSON
import db  # noqa: E402
import fetcher_pool  # noqa: E402

TEMPLATE = os.path.join(BASE, "templates", "index.html")
OUT_DIR = os.environ.get("QDII_DOCS_DIR") or os.path.join(ROOT, "docs", "qdii")
COMMON_JS = os.path.join(ROOT, "common", "static_refresh.js")
ACTIONS_URL = ("https://github.com/turbomind66/invest-radar"
               "/actions/workflows/daily.yml")

# Actions runner 是 UTC；容器已 TZ=Asia/Shanghai；Windows 本地按系统时区。
# 时间戳字段一律走北京时间，别把 UTC 时间当"数据生成时间"发给用户。
TZ_BEIJING = datetime.timezone(datetime.timedelta(hours=8))


def bj_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).astimezone(TZ_BEIJING)


def _sanitize_status(payload_status: dict) -> dict:
    """公开 JSON 里只保留 basename，避免把开发者/服务器的绝对路径写进 Pages。

    服务端 /api/qdii/status 仍返回完整路径用于本机排查（README 排错速查依赖它）。
    """
    st = dict(payload_status or {})
    if "db" in st:
        st["db_basename"] = os.path.basename(st.get("db") or "")
        st.pop("db", None)
    return st


def build_payload():
    """从路由层直接取各接口数据（与线上服务端同口径）。

    先在干净库里跑 init_pool，保证 FUND_POOL 59 只种子存在，board 不会因
    qdii.db gitignored 而在 Actions runner 上返回空数组。真实业务数据
    （limit/fee/signal/premium/tracking）由 qdii_scheduler --once 抓取。
    """
    try:
        db.init_db()
    except Exception as e:  # noqa: BLE001
        print("[qdii-static] init_db 失败（继续尝试导出）：", e)
    try:
        fetcher_pool.init_pool()
    except Exception as e:  # noqa: BLE001
        print("[qdii-static] init_pool 失败（继续尝试导出）：", e)
    return {
        "generated_at": bj_now().strftime("%Y-%m-%d %H:%M:%S"),
        "funds": qdii_app.route_api("/api/qdii/funds"),
        "decision": qdii_app.route_api("/api/qdii/decision"),
        "signal": qdii_app.route_api("/api/qdii/signal"),
        "premium": qdii_app.route_api("/api/qdii/premium"),
        "board": qdii_app.route_api("/api/qdii/board"),
        "status": _sanitize_status(qdii_app.route_api("/api/qdii/status")),
    }


def _looks_empty(payload: dict) -> bool:
    """判定「几乎肯定是抓取失败/空库」：board.rows 与 decision.rows 同时为空。

    只用 funds（init_pool 会预置 59 条）判定不够严格；只要总览/决策都没数据，
    Pages 端就是白屏 → 保留旧快照更安全。
    """
    board_rows = (payload.get("board") or {}).get("rows") or []
    dec_rows = (payload.get("decision") or {}).get("rows") or []
    return not board_rows and not dec_rows


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
        "<script>window.__STATIC__=%s;</script>\n"
        % json.dumps(flag, ensure_ascii=False, allow_nan=False)
        + ("<script>%s</script>\n" % common_js if common_js else "")
        + "</head>"
    )
    return html.replace("</head>", inject, 1)


def _atomic_write(path: str, data_bytes: bytes) -> None:
    """先写 .tmp 再 os.replace，中途崩溃不会留下半文件让 Pages 白屏。"""
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data_bytes)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass  # 某些 FS（网络盘、overlayfs）不支持 fsync，非致命
    os.replace(tmp, path)


def export(out_dir: str = OUT_DIR, template: str = TEMPLATE,
           allow_empty: bool = False) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    payload = build_payload()

    json_path = os.path.join(out_dir, "qdii_data.json")
    html_path = os.path.join(out_dir, "index.html")

    if _looks_empty(payload) and not allow_empty:
        old_size = os.path.getsize(json_path) if os.path.exists(json_path) else 0
        print("[qdii-static] ⚠ 本次导出决策/总览均为空，为避免把 Pages 洗成白屏跳过写入；"
              "旧 qdii_data.json=%d 字节。确认要覆盖请加 --allow-empty。" % old_size)
        return {
            "skipped": True,
            "reason": "empty payload",
            "generated_at": payload["generated_at"],
            "funds": len((payload.get("funds") or {}).get("funds") or []),
        }

    # allow_nan=False：上游偶发 inf/NaN（价格字段除以 0）时 json 会写 NaN 字面量，
    # 浏览器 JSON.parse 直接拒绝、整站白屏；default=str 让 Decimal 等非原生
    # 类型也有兜底而不是 TypeError。
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                      default=str, separators=(",", ":")).encode("utf-8")
    _atomic_write(json_path, body)
    _atomic_write(html_path, render_index(payload, template).encode("utf-8"))

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
    ap.add_argument("--allow-empty", action="store_true",
                    help="即使本次决策/总览都是空也强制覆盖（本地调试用）")
    args = ap.parse_args()
    res = export(out_dir=args.out, allow_empty=args.allow_empty)
    print("[qdii-static] %s" % json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
