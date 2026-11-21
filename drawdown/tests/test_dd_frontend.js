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
    querySelector() { return null; },
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
    off() {}, on(ev, fn) { this._h = this._h || {}; this._h[ev] = fn; },
  }),
};
global.fetch = async () => ({
  ok: true, json: async () => ({ series: { series: [], events: [] }, signals: [], table: {} }),
});

const EXPORTER = `;global.__T = { renderChart, renderGroups, showEvt, STATE };`;
eval(extractScript(HTML_TEXT) + EXPORTER);

// ---------- 断言 ----------
let pass = 0, fail = 0;
function ok(cond, name) {
  if (cond) { pass++; console.log('  \u2705 ' + name); }
  else { fail++; console.log('  \u274c ' + name); }
}

// 自包含最小 catalog（不依赖外部临时文件）
const CATALOG = {
  assets: [
    { id: 'sh000300', name: '沪深300', ready: true, actual_from: '2005-04-08', points: 5253 },
    { id: 'sh000001', name: '上证指数', ready: true, actual_from: '1990-12-19', points: 8595 },
  ],
  groups: [{ key: 'index', label: 'A股指数', ids: ['sh000300', 'sh000001'] }],
  default_ids: ['sh000300', 'sh000001'],
};

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
  { id: 'sh000300', name: '沪深300',
    line: [['2017-01-01', 4000], ['2018-01-26', 4381.3], ['2020-01-01', 4100]],
    dd: [['2017-01-01', 0], ['2018-01-26', 0], ['2020-01-01', -10]], points: 3 },
  { id: 'sh000001', name: '上证指数',
    line: [['2017-01-01', 3100], ['2020-01-01', 3000]],
    dd: [['2017-01-01', 0], ['2020-01-01', -5]], points: 2 },
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

// ---- 4) 点位/回撤值格式化：axis tooltip 传的是标量，不是 [date, value] ----
const mainS = opt.series.find(s => s.yAxisIndex === 0 && s.name === '沪深300');
const ddS = opt.series.find(s => s.yAxisIndex === 1 && s.name === '沪深300');
ok(mainS.tooltip.valueFormatter(12.345) === '+12.35%', '主线 tooltip 传标量输出累计涨跌%（不再是 —）');
ok(ddS.tooltip.valueFormatter(-53.1) === '-53.10%', '回撤线 tooltip 传标量能格式化出百分比（不再是 NaN%）');
ok(ddS.tooltip.valueFormatter(null) === '—', '回撤线空值显示 —');

// ---- 5) 事件标注说明：独立 scatter + 说明条 ----
const evtSeries = opt.series.find(s => s.type === 'scatter');
ok(!!evtSeries && evtSeries.name === '大事件', '事件标注为独立 scatter series（markPoint 的 tooltip 会被 axis 模式压制）');
const evtTxt = evtSeries.tooltip.formatter({ data: { name: '测试事件', evDate: '2020-01-01', evDesc: '描述内容' } });
ok(evtTxt.indexOf('测试事件') >= 0 && evtTxt.indexOf('描述内容') >= 0, '事件 item tooltip 含标题与描述');
__T.showEvt({ name: '测试事件', evDate: '2020-01-01', evDesc: '描述内容' });
const note = document.getElementById('evtNote');
ok(note.style.display === 'block' && note.innerHTML.indexOf('描述内容') >= 0,
   'showEvt 在页面说明条渲染事件详情');

// ---- 6) 叠加归一化：不同量纲统一为累计涨跌%，窗口首点 = 0% ----
__T.renderChart({ series: seriesData, events });
opt = LAST_OPT;
const hsMain = opt.series.find(s => s.yAxisIndex === 0 && s.name === '沪深300');
const shMain = opt.series.find(s => s.yAxisIndex === 0 && s.name === '上证指数');
ok(hsMain.data[0][1] === 0, '沪深300 窗口首点归一为 0%');
ok(shMain.data[0][1] === 0, '上证指数 窗口首点归一为 0%（不同量纲同轴可比）');
ok(Math.abs(hsMain.data[1][1] - ((4381.3 / 4000 - 1) * 100)) < 1e-3,
   '沪深300 第二点 = 相对窗口首点涨跌%（+9.53%）');
ok(Math.abs(shMain.data[1][1] - ((3000 / 3100 - 1) * 100)) < 1e-3,
   '上证指数 末点 = 相对窗口首点涨跌%（-3.23%）');
ok(/累计涨跌%/.test(opt.yAxis[0].name) && /%/.test(opt.yAxis[0].axisLabel.formatter),
   '主图 Y 轴改为累计涨跌%（不再是绝对点位，避免价差大时显示不全）');

// 设起始日 → 以后端裁剪后的首点为 0；重置 → 各标的最早点 0（等价于传入各自全量数据）
const clipped = [
  { id: 'sh000300', name: '沪深300',
    line: [['2015-01-05', 3500], ['2016-01-04', 3150]], dd: [['2015-01-05', 0], ['2016-01-04', -10]], points: 2 },
  { id: 'sh000001', name: '上证指数',
    line: [['2015-01-05', 3350], ['2016-01-04', 2680]], dd: [['2015-01-05', 0], ['2016-01-04', -20]], points: 2 },
];
__T.renderChart({ series: clipped, events: [] });
opt = LAST_OPT;
const cHs = opt.series.find(s => s.yAxisIndex === 0 && s.name === '沪深300');
const cSh = opt.series.find(s => s.yAxisIndex === 0 && s.name === '上证指数');
ok(cHs.data[0][1] === 0 && cSh.data[0][1] === 0, '设置起始日期后，两条线都在起始日归 0%');
ok(Math.abs(cHs.data[1][1] - (-10)) < 1e-6 && Math.abs(cSh.data[1][1] - (-20)) < 1e-6,
   '起始日之后的点按同一起点换算涨跌%');

// tooltip 同时回显绝对点位（归一化后不丢失点位信息）
const tipTxt = opt.tooltip.formatter([{
  seriesType: 'line', seriesIndex: 0, seriesName: '沪深300', marker: '',
  value: ['2016-01-04', -10], data: ['2016-01-04', -10],
}]);
ok(tipTxt.indexOf('-10.00%') >= 0 && tipTxt.indexOf('3150.0000') >= 0,
   'tooltip 同时显示累计涨跌%与原始点位');

// 事件 pin 也按归一化坐标绘制（否则会跑到天上）
const evtPt = opt.series.find(s => s.type === 'scatter' && s.name === '大事件');
ok(!evtPt || evtPt.data.every(d => Math.abs(d.value[1]) < 1000),
   '事件标注坐标随主线归一化（不再使用绝对点位）');

// ---- 7) 拖动缩放后以视窗左边界重新归零（输入框日期与曲线基准始终一致） ----
const chartInst = charts.chart;
ok(!!(chartInst._h && typeof chartInst._h.datazoom === 'function'), '注册了 datazoom 监听（缩放联动）');
__T.renderChart({ series: seriesData, events });
LAST_OPT.dataZoom = [{
  startValue: new Date(2018, 0, 26).getTime(),
  endValue: new Date(2020, 0, 1).getTime(),
}];
const realST = global.setTimeout;
global.setTimeout = (fn) => { fn(); return 0; };
try { chartInst._h.datazoom(); } finally { global.setTimeout = realST; }
const zHs = LAST_OPT.series.find(s => s.yAxisIndex === 0 && s.name === '沪深300');
ok(zHs && Math.abs(zHs.data[1][1]) < 1e-9, '缩放后沪深300 以视窗左边界(2018-01-26)归 0%');
ok(__T.STATE.dfrom === '2018-01-26',
   '缩放起点写回「起」条件，与曲线基准一致：' + __T.STATE.dfrom);

console.log('\n结果: ' + pass + ' 通过, ' + fail + ' 失败');
process.exit(fail ? 1 : 0);
