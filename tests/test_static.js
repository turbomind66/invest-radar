/**
 * 静态模式一致性测试（离线，无需浏览器/网络）。
 *
 * 关键点：GitHub Pages 版把筛选逻辑搬到了浏览器（computeStaticPayload），
 * 而服务端版用的是 Python 的 build_payload。两套逻辑必须逐字段等价，
 * 否则同一天数据在两种部署下会显示不同结果。
 *
 * 做法：
 *   1) 调 tests/gen_static_expected.py，用服务端 Python 逻辑算出期望值；
 *   2) 从 templates/index.html 提取真实内联脚本，在 Node 里跑 computeStaticPayload；
 *   3) 逐字段对比。
 *
 * 运行：node tests/test_static.js
 */
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const ROOT = path.join(__dirname, '..');
const HTML = path.join(ROOT, 'templates', 'index.html');

// ---------------- 让 Python 产出期望值 ----------------
function pickPython() {
  for (const bin of [process.env.PYTHON, 'python3', 'python'].filter(Boolean)) {
    const r = spawnSync(bin, ['--version'], { encoding: 'utf8' });
    if (r.status === 0) return bin;
  }
  throw new Error('未找到可用的 python 解释器');
}

const py = pickPython();
const gen = spawnSync(py, [path.join(__dirname, 'gen_static_expected.py')], {
  encoding: 'utf8', cwd: ROOT, maxBuffer: 32 * 1024 * 1024,
});
if (gen.status !== 0) {
  console.error('生成期望值失败：\n' + (gen.stderr || gen.stdout));
  process.exit(1);
}
const { raw, cases } = JSON.parse(gen.stdout);

// ---------------- 桩件 ----------------
function extractScript(html) {
  const m = html.match(/<script>([\s\S]*?)<\/script>\s*<\/body>/);
  if (!m) throw new Error('未能在 index.html 中定位内联脚本');
  return m[1].replace(/\nloadData\(\);\s*$/, '\n');
}
const els = {};
const mkEl = (id) => ({
  id, innerHTML: '', textContent: '', value: '', disabled: false, style: {},
  classList: { add() {}, remove() {} },
});
global.document = {
  getElementById: (id) => els[id] || (els[id] = mkEl(id)),
  querySelector: (s) => els['q_' + s] || (els['q_' + s] = mkEl(s)),
  querySelectorAll: () => [],
};
global.window = { addEventListener() {} };
global.echarts = {
  init: () => ({ setOption() {}, resize() {}, clear() {} }),
  graphic: { LinearGradient: function () {} },
};
// 静态模式不应有任何服务端请求
global.fetch = async () => { throw new Error('静态模式不应发起 fetch'); };

eval(extractScript(fs.readFileSync(HTML, 'utf8')));

// ---------------- 断言工具 ----------------
let pass = 0, fail = 0;
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
function cmp(tag, a, b) {
  if (eq(a, b)) { pass++; return true; }
  fail++;
  console.log(`  \u274c ${tag}\n     服务端: ${JSON.stringify(a)}\n     静态端: ${JSON.stringify(b)}`);
  return false;
}
const r2 = (v) => (v === null || v === undefined) ? null : Math.round(v);

// ---------------- 用例 ----------------
console.log('\n=== 服务端 build_payload  vs  前端 computeStaticPayload ===');
for (const c of cases) {
  const p = c.params || {};
  const js = computeStaticPayload(raw, p.date || null, p.cats || [], p.codes || []);
  const py = c.payload;
  let ok = true;
  ok &= cmp(`[${c.name}] 生效日期`, py.filters.date, js.filters.date);
  ok &= cmp(`[${c.name}] 自动回退来源`, py.filters.fallback_from || null, js.filters.fallback_from || null);
  ok &= cmp(`[${c.name}] 明细顺序`, py.etf.map((e) => e.code), js.etf.map((e) => e.code));
  ok &= cmp(`[${c.name}] 主力净买`, py.etf.map((e) => r2(e.main_net)), js.etf.map((e) => r2(e.main_net)));
  ok &= cmp(`[${c.name}] 汇总净买`, py.summary.total_main_net_yi, js.summary.total_main_net_yi);
  ok &= cmp(`[${c.name}] 当日全量`, py.summary.full_total_main_net_yi, js.summary.full_total_main_net_yi);
  ok &= cmp(`[${c.name}] 只数统计`,
    [py.summary.etf_count, py.summary.main_cnt, py.summary.full_etf_count],
    [js.summary.etf_count, js.summary.main_cnt, js.summary.full_etf_count]);
  ok &= cmp(`[${c.name}] 分类小计`, py.summary.by_cat_yi, js.summary.by_cat_yi);
  ok &= cmp(`[${c.name}] 四档净额`, py.summary.tier_yi, js.summary.tier_yi);
  ok &= cmp(`[${c.name}] 一级净申购合计`, py.summary.share_net_sub_yi, js.summary.share_net_sub_yi);
  ok &= cmp(`[${c.name}] 份额覆盖数`, py.summary.share_cover, js.summary.share_cover);
  ok &= cmp(`[${c.name}] 份额明细`,
    py.etf.map((e) => [e.chg_shares, e.net_sub]),
    js.etf.map((e) => [e.chg_shares, e.net_sub]));
  ok &= cmp(`[${c.name}] 数据完整度标记`, [py.mainnet_ok, py.partial_main], [js.mainnet_ok, js.partial_main]);
  ok &= cmp(`[${c.name}] 信号判定`, py.signal, js.signal);
  ok &= cmp(`[${c.name}] 历史序列`,
    py.history.map((h) => [h.date, r2(h.total_main_net), h.etf_count, h.main_cnt,
                           r2(h.share_net_sub), h.share_cnt]),
    js.history.map((h) => [h.date, r2(h.total_main_net), h.etf_count, h.main_cnt,
                           r2(h.share_net_sub), h.share_cnt]));
  if (ok) { pass++; console.log(`  \u2705 ${c.name}`); }
}

// 信号阈值必须在两端口径一致（防止有人只改了一边）
console.log('\n=== 信号阈值口径 ===');
const TH = [[null, ['主力净流入暂不可用', 'flat']], [6, ['明显净买入（护盘）', 'buy']],
  [1, ['净买入', 'buy']], [0, ['基本中性', 'flat']], [-1, ['净卖出', 'sell']],
  [-6, ['明显净卖出', 'sell']]];
for (const [v, exp] of TH) {
  cmp(`signalLabel(${v})`, exp, signalLabel(v));
}

console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
process.exit(fail === 0 ? 0 : 1);
