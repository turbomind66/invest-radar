"""qdii/fetcher_fee.py — 模块C：多平台申赎规则 + 费率矩阵。

平台列：基金公司(基准) / 天天基金 / 支付宝。
- 基金公司基准限购：来自模块B的 qdii_limit_status（各代销渠道统一，权威基准）。
- 天天基金：FundRateInfo.ashx 含申购费/赎回费阶梯/管理/托管/销售服务费。
- 支付宝：fund123.cn/matiaria 服务端渲染取 window.context（在售状态）。
- 券商：本期只标记「是否代销」由前端提示「待核对」，不自动抓取。
"""
import os
import sys
import re
import json
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
for p in (BASE, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from common.http import fetch_text, fetch_json
import db
import constants as C


def to_pct(v):
    """'0.80%' / 0.8 / '0.8' -> 0.8 (百分点的数值)"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def fetch_rate_info(code):
    url = C.URL_RATE_INFO.format(code=code)
    obj = fetch_json(url, referer="https://fundmobapi.eastmoney.com/",
                     use_proxy=None, retries=2)
    # 兼容两种返回结构：顶层直接含字段，或包裹在 Datas 内
    if "Datas" in obj and isinstance(obj["Datas"], dict):
        return obj["Datas"]
    return obj


def parse_rate_info(obj):
    d = obj if isinstance(obj, dict) else {}
    # 申购费阶梯：sg[] = [{rate(折后), source(原价), min, max}]
    sg = d.get("sg") or []
    sub_fee_rate = None
    sub_fee_discount = None
    if sg:
        first = sg[0]
        # 不同版本字段名：source 原价 / rate 折后
        sub_fee_rate = to_pct(first.get("source") or first.get("rate"))
        sub_fee_discount = to_pct(first.get("rate") or first.get("source"))
    # 赎回费阶梯：sh[] = [{time(持有期限), rate}]
    sh = d.get("sh") or []
    redeem_ladder = {}
    for item in sh:
        period = item.get("time") or item.get("min") or item.get("name")
        rate = to_pct(item.get("rate"))
        if period is not None and rate is not None:
            redeem_ladder[str(period)] = rate
    # 年费三件套
    mgmt = to_pct(d.get("MGREXP"))
    custodian = to_pct(d.get("TRUSTEXP"))
    sales = to_pct(d.get("SALESEXP"))
    return {
        "sub_fee_rate": sub_fee_rate,
        "sub_fee_discount": sub_fee_discount,
        "redeem_fee_ladder": json.dumps(redeem_ladder, ensure_ascii=False),
        "mgmt_fee": mgmt,
        "custodian_fee": custodian,
        "sales_service_fee": sales,
        "large_sub_limit": to_pct(d.get("MAXSG")),
        "dca_status": d.get("DTZT"),  # 定投状态
    }


def fetch_alipay_info(code):
    """支付宝(fund123.cn)：在售状态 + 管理/托管费 + 申购起点。返回 dict 或 None。

    实测可用字段（materialInfo.fundBrief）：
      saleStatus      在售状态，如 '正常申购' / '暂停销售'
      manageRate      管理费，如 '1%'    -> mgmt_fee
      trusteeRate     托管费，如 '0.2%'  -> custodian_fee
      purchaseMinMount 申购起点金额（元）-> buy_start
      redeemMinMount  最低赎回份额
      purchaseRatio/redeemRatio 实测多为 '--'（该页不提供），故不依赖；
    申购费率以天天基金 FundRateInfo 为准（权威披露值）。
    """
    url = C.URL_ALIPAY.format(code=code)
    txt = fetch_text(url, referer="https://www.fund123.cn/",
                     encoding="utf-8", use_proxy=None, retries=2)
    m = re.search(r"window\.context\s*=\s*(\{.*?\});", txt, re.S)
    if not m:
        return None
    ctx = json.loads(m.group(1))
    brief = (ctx.get("materialInfo") or {}).get("fundBrief") or {}
    return {
        "sale_status": brief.get("saleStatus"),
        "mgmt_fee": to_pct(brief.get("manageRate")),
        "custodian_fee": to_pct(brief.get("trusteeRate")),
        "buy_start": to_pct(brief.get("purchaseMinMount")),
    }


def fetch_platform_fee(date: str = None, path: str = C.QDII_DB_PATH):
    date = date or datetime.date.today().isoformat()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    conn = db.get_conn(path)
    # 天天基金即东方财富自家平台，其在售状态应以模块B的申购状态为准
    # （不能只用「是否取到费率数据」代替，否则暂停申购的基金会误显示成在售）。
    lmap = {}
    try:
        lrows = conn.execute(
            "SELECT code, sub_status FROM qdii_limit_status "
            "WHERE date=(SELECT MAX(date) FROM qdii_limit_status)").fetchall()
        lmap = {r["code"]: r["sub_status"] for r in lrows}
    except Exception:  # noqa: BLE001
        lmap = {}
    cnt = 0
    for f in C.FUND_POOL:
        code = f["code"]
        # ---- 天天基金行 ----
        try:
            info = parse_rate_info(fetch_rate_info(code))
        except Exception as e:  # noqa: BLE001
            info = {}
        tt_on_sale = 0 if lmap.get(code) == "SUSPEND" else (1 if info else 0)
        db.upsert_row(conn, "qdii_platform_fee",
                      ["code", "platform", "on_sale", "sub_fee_rate",
                       "sub_fee_discount", "redeem_fee_ladder",
                       "sales_service_fee", "mgmt_fee", "custodian_fee",
                       "redeem_free_days", "dca_status", "large_sub_limit",
                       "source", "fetched_at"],
                      [code, "天天基金", tt_on_sale,
                       info.get("sub_fee_rate"), info.get("sub_fee_discount"),
                       info.get("redeem_fee_ladder"), info.get("sales_service_fee"),
                       info.get("mgmt_fee"), info.get("custodian_fee"),
                       None, info.get("dca_status"), info.get("large_sub_limit"),
                       "eastmoney_rateinfo", now],
                      "code,platform")
        # ---- 支付宝行 ----
        try:
            ap = fetch_alipay_info(code)
        except Exception:  # noqa: BLE001
            ap = None
        if ap and ap.get("sale_status"):
            sale = ap["sale_status"]
            on_sale = 0 if (("暂停" in sale) or ("停止" in sale)) else 1
            # 解析成功：连同从该平台取到的费率一并落库（用于跨平台差异比对）
            db.upsert_row(conn, "qdii_platform_fee",
                          ["code", "platform", "on_sale", "mgmt_fee",
                           "custodian_fee", "buy_start", "source", "fetched_at"],
                          [code, "支付宝", on_sale, ap.get("mgmt_fee"),
                           ap.get("custodian_fee"), ap.get("buy_start"),
                           "fund123_cn", now],
                          "code,platform")
        else:
            # 解析失败：只更新状态，不覆盖历史费率（避免把已有值写成 NULL）
            db.upsert_row(conn, "qdii_platform_fee",
                          ["code", "platform", "on_sale", "source", "fetched_at"],
                          [code, "支付宝", 0, "fund123_cn", now],
                          "code,platform")
        # ---- 理财通行（按平台通用规则推算，非实时抓取）----
        # 事实（2026-10-29 实测）：理财通网页版 fund.qq.com / licai.qq.com 域名已下线，
        # 只在微信 App 内提供服务，且无公开 API（需登录态），无法逐只抓取。
        # 但平台之间的差异是结构性的，可以按规则推算：
        #   · 管理费/托管费：基金合同约定，全平台一致 → 直接沿用同一基金值
        #   · 申购费：理财通 A 类长期 1 折（促销可 0 元购）→ 按原费率 × 0.1
        #   · 申购状态：跟随基金公司限购公告（与天天基金同源）
        #   · 起购：理财通常见 10 元
        # source 标 rule:licai，前端据此显示「规则推算」徽标，不伪装成实时数据。
        base_rate = info.get("sub_fee_rate")      # 原申购费率（与天天基金同列语义）
        licai_rate = round(base_rate * 0.1, 4) if base_rate else None   # 1 折后
        licai_on_sale = 0 if lmap.get(code) == "SUSPEND" else 1
        db.upsert_row(conn, "qdii_platform_fee",
                      ["code", "platform", "on_sale", "sub_fee_rate",
                       "sub_fee_discount", "mgmt_fee", "custodian_fee",
                       "buy_start", "source", "fetched_at"],
                      [code, "理财通", licai_on_sale, base_rate, licai_rate,
                       info.get("mgmt_fee"), info.get("custodian_fee"),
                       10.0, "rule:licai", now],
                      "code,platform")
        cnt += 1
    conn.commit()
    conn.close()
    db.set_meta("last_fee_fetch", now, path)
    return cnt


def get_platform_fee(code=None, path: str = C.QDII_DB_PATH):
    conn = db.get_conn(path)
    if code:
        rows = conn.execute(
            "SELECT * FROM qdii_platform_fee WHERE code=?", (code,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM qdii_platform_fee").fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    db.init_db()
    n = fetch_platform_fee()
    print("fetched platform fee for %d funds" % n)
    for r in get_platform_fee("161125"):
        print("  ", r["platform"], "on_sale=", r["on_sale"],
              "mgmt=", r["mgmt_fee"], "custodian=", r["custodian_fee"],
              "sales=", r["sales_service_fee"])
