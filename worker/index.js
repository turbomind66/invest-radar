// worker/index.js — Cloudflare Worker：invest-radar 静态站的云端桥接
//
// 部署：Cloudflare Workers 新建服务，将本文件设为入口，设置环境变量 GH_TOKEN
//      （仅服务端使用，前端永不接触，避免泄露）。
// 作用：
//   GET  /today?module=nt    服务端拉取国家队当天实时价+资金流（绕开浏览器 CORS/编码坑）
//   GET  /today?module=qdii 返回最新已发布的 QDII 快照（QDII 实时源重，阶段一用最新提交近似）
//   POST /refresh            持有 GH_TOKEN 触发 daily.yml 工作流（点“全量更新”时调用）
//
// 静态页把 STATIC_WORKER_URL 指向本 Worker 地址后：
//   · “刷新当日行情”按钮 → GET /today（可靠实时）
//   · “全量更新”按钮     → POST /refresh（一键触发后台重算，不再手动去 Actions）
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const cors = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    };
    if (request.method === "OPTIONS")
      return new Response(null, { headers: cors });

    try {
      if (url.pathname === "/today" && request.method === "GET") {
        const mod = url.searchParams.get("module") || "nt";
        const data = mod === "qdii" ? await qdiiToday() : await ntToday();
        return json(data, cors);
      }
      if (url.pathname === "/refresh" && request.method === "POST") {
        return json(await triggerRefresh(env), cors);
      }
      return new Response("not found", { status: 404, headers: cors });
    } catch (e) {
      return json({ error: String(e) }, cors, 500);
    }
  },
};

function json(o, headers, status = 200) {
  return new Response(JSON.stringify(o), {
    status,
    headers: { ...headers, "Content-Type": "application/json; charset=utf-8" },
  });
}

const SITE = "https://turbomind66.github.io/invest-radar";

// ---------- 国家队：服务端实时抓取 ----------
async function ntToday() {
  const meta = await (await fetch(SITE + "/data.json")).json();
  const funds = (meta.funds || []).map((f) => f.code);
  const [prices, flows] = await Promise.all([sinaPrices(funds), emFlows(funds)]);
  const out = {};
  funds.forEach((c) => (out[c] = Object.assign({}, prices[c] || {}, flows[c] || {})));
  return out;
}

function sinaPrefix(c) {
  return c[0] === "5" || c[0] === "6" ? "sh" + c : "sz" + c;
}
async function sinaPrices(codes) {
  if (!codes.length) return {};
  const list = codes.map(sinaPrefix).join(",");
  const r = await fetch("https://hq.sinajs.cn/list=" + list, {
    headers: { Referer: "https://finance.sina.com.cn/" },
  });
  const buf = await r.arrayBuffer();
  const txt = new TextDecoder("gbk").decode(buf);
  const out = {};
  const re = /var\s+hq_str_(\w+)\s*=\s*"([^"]*)"/g;
  let m;
  while ((m = re.exec(txt))) {
    const code = m[1].replace(/^(sh|sz)/, "");
    const f = m[2].split(",");
    if (f.length > 4) out[code] = { price: parseFloat(f[3]), prevClose: parseFloat(f[2]) };
  }
  return out;
}
function emSecid(c) {
  return (c[0] === "5" || c[0] === "6" ? "1." : "0.") + c;
}
async function emFlows(codes) {
  if (!codes.length) return {};
  const secids = codes.map(emSecid).join(",");
  const url =
    "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get?lmt=1&klt=1" +
    "&fields1=f51,f52&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65,f66,f67,f68,f69,f70,f71,f72,f73,f74,f75,f76,f77,f78,f79,f80,f81,f82,f83,f84" +
    "&secid=" + secids;
  const r = await fetch(url);
  const d = await r.json();
  const out = {};
  (d && d.data && d.data.diff ? d.data.diff : []).forEach((x) => {
    const code = (x.f100 || "").split(".").pop();
    if (!code) return;
    out[code] = {
      main_net: x.f62 || 0,
      huge_net: x.f66 || 0,
      big_net: x.f65 || 0,
      mid_net: x.f64 || 0,
      small_net: x.f63 || 0,
    };
  });
  return out;
}

// ---------- QDII：阶段一返回最新已发布快照 ----------
async function qdiiToday() {
  const r = await fetch(SITE + "/qdii/qdii_data.json", { cache: "no-store" });
  return await r.json();
}

// ---------- 触发 GitHub Actions 全量重算 ----------
async function triggerRefresh(env) {
  const token = env.GH_TOKEN;
  if (!token) return { error: "未配置 GH_TOKEN（Worker 环境变量）" };
  const api =
    "https://api.github.com/repos/turbomind66/invest-radar/actions/workflows/daily.yml/dispatches";
  const r = await fetch(api, {
    method: "POST",
    headers: {
      Authorization: "Bearer " + token,
      Accept: "application/vnd.github+json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: "main" }),
  });
  if (!r.ok) {
    const t = await r.text();
    return { error: "GitHub 触发失败 " + r.status, detail: t.slice(0, 200) };
  }
  return { ok: true, detail: "已触发 daily.yml，约 1–2 分钟后数据刷新完成" };
}
