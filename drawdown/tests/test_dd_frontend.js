/**
 * 回撤全景前端交互回归测试（离线，无需浏览器/网络）。
 *
 * 从 drawdown/templates/index.html 提取真实内联脚本，用轻量 DOM / ECharts 桩件在 Node 执行，
 * 覆盖「沪深300 筛选不掉」的三个叠加根因：
 *  1) 只剩最后一个标的时 chip 视觉取消但 STATE.ids 不变（状态不同步）
 *  2) 图例只列主线名，回撤虚线(name=主线+" 回撤%")不在 legend.data → 点图例无法隐藏
 *  3) notMerge 重绘清空图例开关 → 刚隐藏的线又冒出来
 *
 * 运行：node tests/test_dd_frontend.js
 */
const fs = require('fs');
const path = require('path');

const HTML = path.join(__dirname, '..', 'templates', 'index.html');
const HTML_TEXT = fs.readFileSync(HTML, 'utf8');

function extractScript(html) {
  const blocks = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
  const m = blocks[blocks.length - 1];
  if (!m) throw new Error('未定位到内联脚本');
  return m[1].replace(/\nboot\(\);\s*$/, '\n');
}

// ---------- 桩件 ----------
function mkClassList() {
  const set = new Set();
  return {
    add: c => set.add(c), remove: c => set.delete(c),
    toggle: c => set.has(c) ? set.delete(c) : set.add(c),
    contains: c => set.has(c), _set: set,
  };
}
const els = {}, created = [];
function mkEl(id) {
  const el = {
    id, innerHTML: '', textContent: '', value: '', style: {}, title: '',
    classList: mkClassList(), onclick: null, children: [], _cls: '',
    appendChild(c) { this.children.push(c); },
  };
  Object.defineProperty(el, 'className', {
    get() { return el._cls; },
    set(v) {
      el._cls = v;
      el.classList._set.clear();
      String(v).split(/\s+/).filter(Boolean).forEach(c => el.classList._set.add(c));
    },
  });
  return el;
}
global.document = {
  getElementById: (id) => els[id] || (els[id] = mkEl(id)),
  querySelector: (s) => els['q_' + s] || (els['q_' + s] = mkEl(s)),
  querySelectorAll: () => [],
  createElement: () => { const e = mkEl('dyn'); created.push(e); return e; },
};
global.window = {};
let LAST_OPT = null;
const charts = {};
global.echarts = {
  init: (dom) => charts[dom.id] || (charts[dom.id] = {
    setOption(o) { LAST_OPT = o; },
    // 真实 ECharts getOption().legend 是数组（每项含 selected），桩件按此形态返回
    getOption() {
      if (!LAST_OPT) return {};
      const o = JSON.parse(JSON.stringify(LAST_OPT));
      if (o.legend && !Array.isArray(o.legend)) o.legend = [o.legend];
      return o;
    },
    off() {}, on() {},
  }),
};
global.fetch = async () => ({
  ok: true, json: async () => ({ series: { series: [], events: [] }, signals: [], table: {} }),
});

const EXPORTER = `;global.__T = { renderChart, renderGroups, STATE };`;
eval(extractScript(HTML_TEXT) + EXPORTER);

// ---------- 断言 ----------
let pass = 0, fail = 0;
function ok(cond, name) {
  if (cond) { pass++; console.log('  \u2705 ' + name); }
  else { fail++; console.log('  \u274c ' + name); }
}

const CATALOG = JSON.parse(fs.readFileSync(
  path.join(__dirname, '..', '..', '..', 'outputs', '_diag_payload.json'), 'utf8')).catalog;

// ---- 1) 只剩最后一个标的：chip 不可被"假取消" ----
created.length = 0;
__T.STATE.ids = ['sh000300'];
__T.renderGroups(CATALOG);
const hsChip = created.find(e => e.textContent === '沪深300');
ok(!!hsChip, 'renderGroups 生成了沪深300 chip');
ok(hsChip.classList.contains('on'), '初始沪深300 chip 处于选中态');
hsChip.onclick();  // 尝试取消最后一个标的
ok(__T.STATE.ids.indexOf('sh000300') >= 0, '点击后 STATE.ids 仍保留最后一个标的');
ok(hsChip.classList.contains('on'), '点击后 chip 保持选中态（不再变灰误导）');

// ---- 2) 主线与回撤线同名：同一图例项统一控制 ----
const seriesData = [
  { id: 'sh000300', name: '沪深300', line: [], dd: [], points: 2 },
  { id: 'sh000001', name: '上证指数', line: [], dd: [], points: 2 },
];
const events = [{ target: 'sh000300', date: '2018-01-26', title: 't', desc: 'd', side: 'crash' }];
__T.renderChart({ series: seriesData, events });
let opt = LAST_OPT;
const mainNames = opt.series.filter(s => s.yAxisIndex === 0 && s.name !== '大事件').map(s => s.name);
const ddNames = opt.series.filter(s => s.yAxisIndex === 1).map(s => s.name);
ok(JSON.stringify(mainNames) === JSON.stringify(ddNames),
   '主线与回撤线同名（图例一项即可同时隐藏两条）：' + JSON.stringify(mainNames));
ok(opt.legend.data.indexOf('沪深300') >= 0, '沪深300 在 legend.data 中');
ok(!ddNames.some(n => /回撤%$/.test(n)), '回撤线不再使用"… 回撤%"独立名（否则图例无法控制）');

// ---- 3) notMerge 重绘保留图例开关状态 ----
LAST_OPT.legend.selected = { '沪深300': false };  // 模拟用户点图例隐藏了沪深300
__T.renderChart({ series: seriesData, events });
opt = LAST_OPT;
ok(opt.legend.selected && opt.legend.selected['沪深300'] === false,
   '重绘后仍保留"沪深300 已隐藏"的图例状态（不被 notMerge 清空）');

console.log('\n结果: ' + pass + ' 通过, ' + fail + ' 失败');
process.exit(fail ? 1 : 0);
