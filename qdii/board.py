"""qdii/board.py — 基金总览：把「申购状态 + 限额」与「多平台费率」合并成一张表。

原来页面拆成 ④ 申购状态+限额 与 ⑤ 多平台费率 两张表，同一只基金要在两张表之间
来回对代码；这里在服务端按 code 做一次 join，输出「一只基金一个块」的结构，前端
直接按块渲染（基金级字段 rowspan 合并、平台级字段逐行展开）。

**为什么把固定年费提到基金级**：管理费 / 托管费 / 销售服务费是基金合同条款，
同一只基金在任何平台都一样 —— 逐平台重复三遍既占表格宽度，又容易让人误以为
平台之间有差异。真正"按平台变"的只有「是否在售」与「申购费」。
"""
import constants as C
import db
import fetcher_fee
import fetcher_limit

# 平台展示顺序：天天基金（官方直销口径）→ 支付宝 → 理财通（推算）
PLATFORM_ORDER = {"天天基金": 1, "支付宝": 2, "理财通": 3}

STATUS_TEXT = {"SUSPEND": "暂停", "LIMIT": "限大额", "OPEN": "开放"}


def _fmt_amount(v):
    """金额展示：整数不带小数点（1000 → '1000'），小数保留原样（10.5 → '10.5'）。"""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return str(int(f)) if f == int(f) else ("%g" % f)


def _first(rows, key):
    """取第一个非空值：三个平台的管理费/托管费相同，取谁都一样，但支付宝可能缺值。"""
    for r in rows:
        v = r.get(key)
        if v is not None:
            return v
    return None


def _platform_row(r):
    src = (r.get("source") or "")
    return {
        "platform": r.get("platform") or "—",
        "on_sale": 1 if r.get("on_sale") else 0,
        "sub_fee_rate": r.get("sub_fee_rate"),
        "sub_fee_discount": r.get("sub_fee_discount"),
        # 理财通没有公开接口，值是按平台规则推算的，前端据此打「推算」徽标。
        # 用 source 前缀判断而不是平台名硬编码，以后接新平台只要带上 rule: 即可。
        "approx": src.startswith("rule:"),
        "source": src,
    }


def _block(code, meta, limit_row):
    sub_status = (limit_row.get("sub_status") or "").upper()
    is_limited = 1 if limit_row.get("is_limited") else 0
    amount = limit_row.get("limit_amount")
    if not is_limited:
        limit_text = "不限"
    elif amount is not None and amount >= 1e9:
        limit_text = "不限"
    else:
        limit_text = _fmt_amount(amount) or "—"
    return {
        "code": code,
        "name": meta.get("name") or "",
        "category": meta.get("category") or "",
        "sub_status": sub_status,
        # 文案统一在后端算：前端再拼一次容易和逐基金决策的口径漂移
        "status_text": STATUS_TEXT.get(sub_status, "未知"),
        "is_limited": is_limited,
        "limit_amount": amount,
        "limit_text": limit_text,
        "next_open_date": limit_row.get("next_open_date") or "",
        # 起购金额：优先用限额表的（天天基金官方口径），费率表兜底
        "buy_start": (limit_row.get("buy_start")
                      if limit_row.get("buy_start") is not None
                      else None),
        "mgmt_fee": None,
        "custodian_fee": None,
        "sales_service_fee": None,
        "annual_fee": None,
        "platforms": [],
        "_raw": [],
    }


def merge_board(limit_rows, fee_rows, pool=()):
    """纯函数：把两组原始行合并成「一只基金一个块」的列表。

    - 基金顺序跟随 fee_rows（上游已按 FUND_POOL 大类顺序排好），
      只有申购状态、没有费率行的基金追加在后面（按 FUND_POOL 秩），
      否则"暂停且平台抓不到费率"的基金会在页面上凭空消失。
    - 平台顺序固定 天天基金 → 支付宝 → 理财通，「券商」等未接入平台的遗留行跳过。
    """
    meta = {f.get("code"): f for f in pool}
    limit_map = {r.get("code"): r for r in (limit_rows or [])}
    pool_rank = {f["code"]: i for i, f in enumerate(C.FUND_POOL)}

    blocks, idx = [], {}
    for r in fee_rows or []:
        if r.get("platform") == "券商":
            continue
        code = r.get("code")
        if not code:
            continue
        if code not in idx:
            idx[code] = len(blocks)
            blocks.append(_block(code, meta.get(code) or {}, limit_map.get(code) or {}))
        block = blocks[idx[code]]
        block["platforms"].append(_platform_row(r))
        block["_raw"].append(r)      # 固定年费要从原始行取（_platform_row 已裁剪字段）

    extras = [r for r in (limit_rows or []) if r.get("code") not in idx]
    extras.sort(key=lambda r: pool_rank.get(r.get("code"), 999))
    for r in extras:
        code = r.get("code")
        idx[code] = len(blocks)
        blocks.append(_block(code, meta.get(code) or {}, r))

    for b in blocks:
        plats = b["platforms"]
        plats.sort(key=lambda p: PLATFORM_ORDER.get(p["platform"], 9))
        raw = sorted(b.pop("_raw"), key=lambda r: PLATFORM_ORDER.get(r.get("platform"), 9))
        b["mgmt_fee"] = _first(raw, "mgmt_fee")
        b["custodian_fee"] = _first(raw, "custodian_fee")
        b["sales_service_fee"] = _first(raw, "sales_service_fee")
        fees = [v for v in (b["mgmt_fee"], b["custodian_fee"],
                            b["sales_service_fee"]) if v is not None]
        b["annual_fee"] = round(sum(fees), 4) if fees else None
        # 可买平台数，前端标题栏用（11 只基金逐个手数容易数错）
        b["onsale_platforms"] = sum(1 for p in plats if p["on_sale"])
    return blocks


def get_fund_board(path: str = C.QDII_DB_PATH):
    """从库里取两组行合并（页面唯一数据入口）。"""
    return merge_board(fetcher_limit.get_latest_limit_status(path=path),
                       fetcher_fee.get_platform_fee(path=path),
                       db.get_fund_pool(path))


def board_summary(blocks):
    """表头摘要：与 strategy.category_stats 同口径（暂停 / 限大额 / 正常开放）。

    「限大额但日限 < 100 元」单列（`nominal_only`）：名义上还在卖、实际投不进去，
    策略里写作「等同变相暂停」。阈值必须和 strategy 的 100 元一致——
    两边口径一旦不同，表头写「可买 4 只」而决策理由写「等同暂停」，就是自相矛盾。
    """
    suspend = limited = opened = 0
    min_limit = None
    for b in blocks or []:
        st = b["sub_status"]
        if st == "SUSPEND":
            suspend += 1
        elif st == "LIMIT":
            limited += 1
            amt = b["limit_amount"] or 0
            if amt and amt < 1e9:
                min_limit = amt if min_limit is None else min(min_limit, amt)
        else:
            opened += 1
    nominal_only = sum(1 for b in blocks or []
                       if b["sub_status"] == "LIMIT" and b["is_limited"]
                       and (b["limit_amount"] or 0) < 100)
    return {"total": len(blocks or []), "open": opened, "limited": limited,
            "suspend": suspend, "min_limit": min_limit,
            "nominal_only": nominal_only,
            # 真能正常投的只数：名义开放(OPEN) + 限额够用的
            "usable": opened + limited - nominal_only}


if __name__ == "__main__":
    db.init_db()
    rows = get_fund_board()
    print("funds:", len(rows), board_summary(rows))
    for b in rows:
        print("  %s %s %s 限额=%s 年费=%s 平台=%s" % (
            b["code"], b["name"], b["status_text"], b["limit_text"],
            b["annual_fee"],
            ",".join("%s(%s)" % (p["platform"], "在售" if p["on_sale"] else "不可售")
                     for p in b["platforms"])))
