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
    // 真实 DOM 语义：toggle(c, force) 的 force 为布尔时是「设置」而不是「翻转」
    toggle: (c, force) => {
      const want = force === undefined ? !set.has(c) : !!force;
      if (want) set.add(c); else set.delete(c);
      return want;
    },
    contains: c => set.has(c), _set: set,
  };
}
const els = {}, created = [];
function mkEl(id) {
  const el = {
    id, innerHTML: '', textContent: '', value: '', style: {}, title: '',
    classList: mkClassList(), onclick: null, children: [], _cls: '', _attrs: {}, _qs: null,
    appendChild(c) { this.children.push(c); },
    querySelector() { return null; },
    querySelectorAll(sel) { return (this._qs && this._qs[sel]) || []; },
    getAttribute(k) { return this._attrs[k] != null ? this._attrs[k] : null; },
    setAttribute(k, v) { this._attrs[k] = String(v); },
    get dataset() { return this._attrs; },
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
    _setCount: 0,
    setOption(o, notMerge) { LAST_OPT = o; this._setCount++; this._notMerge = !!notMerge; },
    // 真实 ECharts getOption().legend 是数组（每项含 selected），桩件按此形态返回
    getOption() {
      if (!LAST_OPT) return {};
      const o = JSON.parse(JSON.stringify(LAST_OPT));
      if (o.legend && !Array.isArray(o.legend)) o.legend = [o.legend];
      return o;
    },
    // 真实语义：off(ev) 清掉该事件全部监听、off(ev, fn) 只解绑匹配的 —— 桩件照此实现，
    // 否则「还原模式是否残留走势图 datazoom 监听」这类回归无法被测出。
    off(ev, fn) {
      if (!this._h) return;
      if (!ev) { this._h = {}; return; }
      if (!fn) { delete this._h[ev]; return; }
      if (this._h[ev] === fn) delete this._h[ev];
    },
    on(ev, fn) { this._h = this._h || {}; this._h[ev] = fn; },
    dispose() { this._disposed = true; },
    getZr() {
      this._zr = this._zr || {
        _h: {},
        on(ev, fn) { this._h[ev] = fn; },
        off(ev, fn) {
          if (!ev) { this._h = {}; return; }
          if (!fn) { delete this._h[ev]; return; }
          if (this._h[ev] === fn) delete this._h[ev];
        },
      };
      return this._zr;
    },
    convertToPixel() { return [100, 200]; },
  }),
};
global.fetch = async () => ({
  ok: true, json: async () => ({ series: { series: [], events: [] }, signals: [], table: {} }),
});

// ④「周期区间」分段按钮（日/周/月/季/年）：initGran 会在脚本加载时绑定点击并按 STATE.gran 打 active
const GRAN_BTNS = ['day', 'week', 'month', 'quarter', 'year'].map(g => {
  const b = mkEl('gbtn_' + g);
  b._attrs['data-gran'] = g;
  return b;
});
els.granBar = mkEl('granBar');
els.granBar._qs = { '.gbtn': GRAN_BTNS };

const EXPORTER = `;global.__T = { renderChart, renderGroups, showEvt, STATE, reconHits, noHitNote, nearestEvt, applyTable, gotoPage, pageCount, setTable: function(t){TABLE=t;}, getTable: function(){return TABLE;}, getRows: function(){return ROWS;}, renderReconstruct, renderReconMany, loadReconstruct, getRECON: function(){return RECON;}, clipPct, reconSeriesOne, reconSeriesMany, unbindChart, getRAW: function(){return RC_RAW;}, getChart: function(){return CHART;}, renderSignals, winSummary, winMap, aggPeriods, periodKey, staticTable, staticSignals, COLS, setPayload: function(p){PAYLOAD=p;}, setGran: setGran, switchView: switchView, paintGran: paintGran };`;
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


// ---- 8) 事件 pin 像素容差命中（点在尖端/边缘也能弹说明） ----
const pinEvents = [{ target: 'sh000300', date: '2018-01-26', title: '容差命中事件', desc: '说明文字', side: 'crash' }];
__T.renderChart({ series: seriesData, events: pinEvents });
const zrInst = charts.chart.getZr();
ok(typeof zrInst._h.click === 'function', '注册了 zr 层容差点击监听');
document.getElementById('evtNote').style.display = 'none';
zrInst._h.click({ offsetX: 104, offsetY: 192 });   // 距图钉中心 ~4px
const noteEl = document.getElementById('evtNote');
ok(noteEl.style.display === 'block' && noteEl.innerHTML.indexOf('说明文字') >= 0,
   '点偏 4px 仍能命中 pin 并弹出说明（不再只有 1、2 个能点）');
ok(__T.nearestEvt(100, 240) === null, '超出 16px 容差不算命中（不会误弹）');

// ---- 9) 还原模式跟随叠加选择（不再固定第一只纳指ETF） ----
const RC = [
  { etf: '513100', name: '纳指ETF国泰', bench_name: '纳斯达克100' },
  { etf: '513500', name: '标普500ETF', bench_name: '标普500' },
];
__T.renderGroups(Object.assign({}, CATALOG, { reconstruct: RC }));
__T.STATE.ids = ['sh000300', 'sh000001'];
ok(__T.reconHits().length === 0, '叠加全是 A 股宽基时无可还原标的');
ok(__T.noHitNote().indexOf('无海外敞口') >= 0, '无可还原标的时给出原因说明');
__T.STATE.ids = ['sh000300', '513100'];
ok(__T.reconHits().map(x => x.etf).join(',') === '513100', '叠加含 1 只可还原标的时纳入该只');
__T.STATE.ids = ['513100', '513500'];
ok(__T.reconHits().length === 2, '叠加含 2 只可还原标的时两只都纳入（走叠加还原对比）');


// ---- 10) 单标的用绝对点位；叠加才归一化为百分比 ----
const ONE = [{ id: 'sh000300', name: '沪深300',
  line: [['2017-01-01', 4000], ['2018-01-26', 4381.3]],
  dd: [['2017-01-01', 0], ['2018-01-26', -10]], points: 2 }];
__T.renderChart({ series: ONE, events: [] });
opt = LAST_OPT;
const oneMain = opt.series.find(s => s.yAxisIndex === 0 && s.name === '沪深300');
ok(oneMain.data[0][1] === 4000 && oneMain.data[1][1] === 4381.3,
   '只选 1 个标的时走势用原始点位/价格（不再换算成%）');
ok(/点位/.test(opt.yAxis[0].name) && !opt.yAxis[0].axisLabel,
   '单标的 Y 轴显示数值（不是百分比刻度）：' + opt.yAxis[0].name);
ok(oneMain.tooltip.valueFormatter(4381.3) === '4381.3000', '单标的 tooltip 直接给点位');
const tipOne = opt.tooltip.formatter([{
  seriesType: 'line', seriesIndex: 0, seriesName: '沪深300', marker: '',
  value: ['2018-01-26', 4381.3], data: ['2018-01-26', 4381.3],
}]);
ok(tipOne.indexOf('4381.3000') >= 0 && tipOne.indexOf('较起点') >= 0,
   '单标的 tooltip 同时给点位与「较起点」涨跌');
__T.renderChart({ series: ONE, events: [{ target: 'sh000300', date: '2018-01-26', title: 'e', desc: 'd', side: 'crash' }] });
const oneEvt = LAST_OPT.series.find(s => s.type === 'scatter');
ok(oneEvt.data[0].value[1] === 4381.3, '单标的事件 pin 落在原始点位坐标');

// 叠加（2 条以上）仍必须归一化
__T.renderChart({ series: seriesData, events: [] });
ok(/累计涨跌%/.test(LAST_OPT.yAxis[0].name) && /%/.test(LAST_OPT.yAxis[0].axisLabel.formatter),
   '叠加 ≥2 个标的时 Y 轴切回累计涨跌%');

// ---- 11) 同一宏观事件（同侧 10 天内）只画一个图钉 ----
__T.renderChart({ series: seriesData, events: [
  { target: 'sh000300', date: '2018-01-26', title: '同一宏观事件', desc: 'd', side: 'crash' },
  { target: 'sh000001', date: '2018-01-24', title: '同一宏观事件', desc: 'd', side: 'crash' },
  { target: 'sh000001', date: '2019-01-04', title: '另一个事件', desc: 'd', side: 'rally' },
] });
const dedup = LAST_OPT.series.find(s => s.type === 'scatter');
ok(dedup.data.length === 2, '同侧 10 天内的重复事件只画一个 pin（避免多条线叠一堆）');

// ---- 12) 回撤明细分页：首页 / 末页 / 页码跳转 ----
const manyRows = [];
for (let i = 0; i < 130; i++) {
  manyRows.push({ date: '2020-01-01', close: 1, peak: 1, peak_date: '2020-01-01',
    dd_pct: 0, days_since_peak: 0, state: 'x' });
}
__T.setTable({ name: '测试标的', daily: manyRows, periods: [], episodes: [] });
__T.STATE.view = 'daily'; __T.STATE.page = 0;
__T.applyTable();
ok(__T.pageCount() === 3, '130 行按 60 行/页 共 3 页');
document.getElementById('last').onclick();
ok(__T.STATE.page === 2, '「末页」跳到最后 1 页');
document.getElementById('first').onclick();
ok(__T.STATE.page === 0, '「首页」回到第 1 页');
document.getElementById('pageJump').value = '2';
document.getElementById('jumpGo').onclick();
ok(__T.STATE.page === 1, '输入页码 2 → 跳到第 2 页');
__T.gotoPage(99);
ok(__T.STATE.page === 2, '越界页码被夹到末页（不会白屏）');
ok(document.getElementById('pageJump').max === '3', '页码输入框上限随总页数同步');

// ---- 13) 回撤事件视图改用波段识别结果（覆盖嵌套大跌） ----
__T.setTable({ name: '测试标的', daily: [], periods: [], episodes: [
  { peak_date: '2015-06-08', trough_date: '2016-01-28', recover_date: null,
    drop_pct: -43.5, rebound_pct: 41.2, recover_days: null, ongoing: 1 },
] });
__T.STATE.view = 'events'; __T.STATE.page = 0;
__T.applyTable();
const evRows = __T.getRows();
ok(evRows.length === 1 && evRows[0].peak_date === '2015-06-08',
   '回撤事件视图读 episodes（2007 高点未收复期间的 2015 大跌也能单列）');

// ---- 14) 还原模式：起点/止点日期生效 + 不残留走势图监听（图表卡死回归） ----
const RC_ONE = {
  etf: '513100', name: '纳指ETF国泰', bench_name: '纳斯达克100',
  t0: '2013-05-15', points: 3, note: '起点 2013-05-15 = 0%',
  curves: {
    price:    [['2013-05-15', 0], ['2015-01-05', 10], ['2020-01-02', 120]],
    nav:      [['2013-05-15', 0], ['2015-01-05', 8],  ['2020-01-02', 110]],
    restored: [['2013-05-15', 0], ['2015-01-05', 9],  ['2020-01-02', 130]],
    bench:    [['2013-05-15', 0], ['2015-01-05', 9.5], ['2020-01-02', 128]],
  },
};
__T.STATE.dfrom = ''; __T.STATE.dto = '';
__T.renderReconstruct(RC_ONE);
opt = LAST_OPT;
ok(opt.series.length === 4, '还原图只画 4 条曲线（场内价/净值/还原/基准）');
const rcFull = opt.series.find(s => /还原%/.test(s.name));
ok(rcFull.data.length === 3 && rcFull.data[0][1] === 0, '未设起点时以 t0 为首点归 0%');

// 关键回归：还原模式原来看不到 STATE.dfrom，设了起点日期曲线纹丝不动
__T.STATE.dfrom = '2015-01-05';
__T.renderReconstruct(RC_ONE);
opt = LAST_OPT;
const rcA = opt.series.find(s => /还原%/.test(s.name));
ok(rcA.data.length === 2 && rcA.data[0][0] === '2015-01-05' && rcA.data[0][1] === 0,
   '还原模式设起点日期生效：曲线从 2015-01-05 起算并归 0%');
const expectRel = ((1 + 130 / 100) / (1 + 9 / 100) - 1) * 100;
ok(Math.abs(rcA.data[1][1] - expectRel) < 1e-3,
   '起点后的点按新基准重算（不是原来的 +130%）：' + rcA.data[1][1].toFixed(2) + '%');
ok(document.getElementById('chartHint').textContent.indexOf('2015-01-05') >= 0,
   '图表提示标注了实际起算日');

__T.STATE.dto = '2015-01-05';
__T.renderReconstruct(RC_ONE);
ok(LAST_OPT.series.find(s => /还原%/.test(s.name)).data.length === 1,
   '设止点日期后曲线被裁剪到止点（不再画出窗口外的点）');
__T.STATE.dto = '';

// 关键回归：走势图的 datazoom 监听若残留，在还原图里拖动缩放会把主线/回撤线/事件 pin 合并进来 → 卡死
const rcInst = __T.getChart();
ok(rcInst._h && typeof rcInst._h.datazoom === 'function', '还原模式注册了自己的缩放监听');
ok(!rcInst._h.click && !rcInst._h.mouseover, '还原模式下不再保留走势图 click/mouseover 监听');
ok(!rcInst.getZr()._h.click && !rcInst.getZr()._h.mousemove,
   '还原模式下不再保留事件 pin 的 zr 容差监听（convertToPixel 找不到 series 会每帧报错）');

const zoomTo = () => {
  LAST_OPT.dataZoom = [{
    startValue: new Date(2015, 0, 5).getTime(),
    endValue: new Date(2020, 0, 2).getTime(),
  }];
  const realST2 = global.setTimeout;
  global.setTimeout = (fn) => { fn(); return 0; };
  try { rcInst._h.datazoom(); } finally { global.setTimeout = realST2; }
};
const beforeLen = LAST_OPT.series.length;
zoomTo();
ok(LAST_OPT.series.length === beforeLen,
   '缩放后 series 数量不变（走势回撤线/事件 pin 未串入还原图）');
ok(LAST_OPT.series.every(s => s.yAxisIndex === undefined),
   '还原图 series 没有被塞入 yAxisIndex:1 的回撤线');
ok(__T.STATE.dfrom === '2015-01-05', '还原模式缩放起点写回「起」条件：' + __T.STATE.dfrom);
zoomTo();
zoomTo();
ok(LAST_OPT.series.length === 4, '连续缩放多次 series 数不膨胀（卡死的直接原因已消除）');

// 切回走势图：还原监听被替换，走势数据完整
__T.renderChart({ series: seriesData, events });
opt = LAST_OPT;
ok(opt.series.some(s => s.yAxisIndex === 1), '切回走势图后回撤线正常存在');
ok(typeof rcInst._h.datazoom === 'function' && !!rcInst._h.click,
   '走势图的缩放与事件监听重新绑定');
ok(__T.getRAW() === null, '切回走势图时清空还原曲线缓存（不会拿旧还原数据重算）');

// 叠加还原同样支持起点日期
const RC_MANY = [
  { name: '纳指ETF国泰', bench_name: '纳斯达克100', curves: {
    restored: [['2015-01-05', 0], ['2020-01-02', 130]],
    price: [['2015-01-05', 0], ['2020-01-02', 120]],
    bench: [['2015-01-05', 0], ['2020-01-02', 128]] } },
  { name: '标普500ETF', bench_name: '标普500', curves: {
    restored: [['2016-01-04', 0], ['2020-01-02', 60]],
    price: [['2016-01-04', 0], ['2020-01-02', 55]],
    bench: [['2016-01-04', 0], ['2020-01-02', 58]] } },
];
__T.STATE.dfrom = ''; __T.STATE.dto = '';
__T.renderReconMany(RC_MANY);
opt = LAST_OPT;
ok(opt.series.length === 6, '叠加还原：2 只 ×（还原+场内价）+ 2 条基准 = 6 条');
__T.STATE.dfrom = '2016-01-04';
__T.renderReconMany(RC_MANY);
opt = LAST_OPT;
const manyRes = opt.series.filter(s => /还原%/.test(s.name));
ok(manyRes.length === 2 && manyRes.every(s => s.data[0][1] === 0),
   '叠加还原设起点后各线仍从 0% 起（起点早于某只的 t0 时取该只 t0）');
ok(__T.getRAW() && __T.getRAW().mode === 'many', '叠加还原缓存了原始曲线供缩放重算');

// ---- 区间口径（所选起止日）----
// ④「周期汇总」的区间粒度：日 / 周 / 月 / 季 / 年（贴在表头的分段按钮，不再是①里的下拉）
(function testGran(){
  ok(GRAN_BTNS.length === 5, '周期区间提供 日/周/月/季/年 五个粒度');
  ok(GRAN_BTNS.find(b => b.getAttribute('data-gran') === 'month').classList.contains('active'),
     '默认粒度「月」按钮处于选中态');
  __T.setGran('quarter', false);
  ok(__T.STATE.gran === 'quarter', '切到「季」后 STATE.gran = quarter');
  ok(GRAN_BTNS.find(b => b.getAttribute('data-gran') === 'quarter').classList.contains('active')
     && !GRAN_BTNS.find(b => b.getAttribute('data-gran') === 'month').classList.contains('active'),
     '选中态跟着切换（季高亮、月取消）');
  __T.STATE.view = 'daily';
  __T.setGran('year', false);
  ok(__T.STATE.view === 'period', '在「日明细」视图下切粒度会自动跳回周期汇总（否则看不到变化）');
  ok(GRAN_BTNS.find(b => b.getAttribute('data-gran') === 'year').getAttribute('aria-pressed') === 'true',
     'aria-pressed 同步，读屏可感知当前粒度');
  ok(__T.periodKey('2015-06-08', 'day') === '2015-06-08', '日粒度键即当天日期');
  ok(__T.periodKey('2015-06-08', 'quarter') === '2015-Q2', '季粒度键 2015-Q2');
  ok(__T.periodKey('2015-06-08', 'month') === '2015-06', '月粒度键 2015-06');
  ok(__T.periodKey('2015-06-08', 'year') === '2015', '年粒度键 2015');
  ok(/^2015-W\d\d$/.test(__T.periodKey('2015-06-08', 'week')), '周粒度键按 ISO 周：' + __T.periodKey('2015-06-08', 'week'));
  ok(/id="granBar"/.test(HTML_TEXT) && /class="gbtn active"\s+data-gran="month"/.test(HTML_TEXT),
     '模板里「月」为默认高亮（周期汇总与日明细才不重复）');
  ok(!/id="gran"/.test(HTML_TEXT), '① 卡片里不再留旧的下拉（避免两个控件改同一状态）');
})();

// 五种粒度对同一批日线的行数：日 4 行 / 周 3 行 / 月 3 行 / 季 2 行 / 年 1 行
(function testGranAgg(){
  const rows = [
    { date: '2020-01-02', close: 100, dd_pct: 0, peak: 100, peak_date: '2020-01-02', days_since_peak: 0, state: 'new_high' },
    { date: '2020-01-03', close: 98, dd_pct: -2, peak: 100, peak_date: '2020-01-02', days_since_peak: 1, state: 'drawdown' },
    { date: '2020-02-03', close: 90, dd_pct: -10, peak: 100, peak_date: '2020-01-02', days_since_peak: 22, state: 'drawdown' },
    { date: '2020-07-01', close: 105, dd_pct: 0, peak: 105, peak_date: '2020-07-01', days_since_peak: 0, state: 'new_high' },
  ];
  const n = g => __T.aggPeriods(rows, g, null).length;
  ok(n('day') === 4, '日粒度：4 个交易日 → 4 行');
  ok(n('week') === 3, '周粒度：跨 3 个 ISO 周 → 3 行');
  ok(n('month') === 3, '月粒度：01 月 2 行 + 02 月 + 07 月 → 3 行');
  ok(n('quarter') === 2, '季粒度：Q1 / Q3 → 2 行');
  ok(n('year') === 1, '年粒度：全部收进 2020 → 1 行');
  const jan = __T.aggPeriods(rows, 'month', null)[0];
  ok(jan.start === '2020-01-02' && jan.end === '2020-01-03', '月粒度首行区间 01-02 ~ 01-03（起止不同天）');
})();

// 区间高点：峰值从窗口第一根起算，而不是历史最高收盘
const WIN_ROWS = [
  { date: '2015-01-05', close: 100 }, { date: '2015-06-08', close: 150 },
  { date: '2015-08-26', close: 85 }, { date: '2015-12-31', close: 110 },
];
const ws = __T.winSummary(WIN_ROWS);
ok(ws && ws.peak === 150 && ws.peak_date === '2015-06-08', '区间高点取窗口内最高收盘 150（不是历史高点）');
ok(Math.abs(ws.deepest_pct - ((85 / 150 - 1) * 100)) < 1e-3, '区间最深回撤以区间高点为基准（-43.33%）');
ok(ws.trough === 85 && ws.trough_date === '2015-08-26', '区间高点后的谷底 85');
ok(ws.ongoing === 1 && ws.recover_date === null, '窗口末未收复区间高点 → ongoing');
ok(Math.abs(ws.dd_pct - ((110 / 150 - 1) * 100)) < 1e-3, '区间回撤（末值）同样以区间高点为基准');
ok(__T.winSummary([]) === null, '空窗口返回 null（前端提示「区间内无数据」）');
const winMapOne = __T.winMap(WIN_ROWS);
ok(winMapOne['2015-08-26'].wpeak === 150 && winMapOne['2015-08-26'].wdd < -43,
   'winMap 给出逐日区间峰值与区间回撤');

// 周期聚合：月粒度必须真的按月起止（不再是「起=止=当天」）
const WS_DAILY = [];
['2015-01-05', '2015-01-30', '2015-02-02', '2015-02-27'].forEach((d, i) => {
  WS_DAILY.push({ date: d, close: 100 + i, dd_pct: 0, peak: 200, peak_date: '2007-10-16',
                  days_since_peak: 1, state: 'drawdown' });
});
const aggM = __T.aggPeriods(WS_DAILY, 'month', __T.winMap(WS_DAILY));
ok(aggM.length === 2, '月粒度把 4 个交易日聚成 2 行');
ok(aggM[0].start === '2015-01-05' && aggM[0].end === '2015-01-30', '首行区间 2015-01-05 ~ 2015-01-30（起止不同天）');
ok(aggM[0].wpeak != null && aggM[0].wdd_pct != null, '聚合行同时带区间峰值与区间回撤');
ok(aggM[0].peak === 200 && aggM[0].peak_date === '2007-10-16', '历史峰值列仍保留全历史口径');

// 信号卡：给了 window 才追加「所选区间」块
__T.renderSignals([{
  id: 'sh000300', name: '沪深300', close: 3731, peak: 5877.2, peak_date: '2007-10-16',
  dd_pct: -32.71, dd_rank_pct: 30, days_since_peak: 6975, recover_days_median: 244,
  hint: '当前回撤分位 30%', window: {
    from: '2015-01-05', to: '2015-12-31', points: 244, peak: 5353.75, peak_date: '2015-06-08',
    dd_pct: -30.31, deepest_pct: -43.48, deepest_date: '2015-08-26', trough: 3025.69,
    trough_date: '2015-08-26', days_down: 56, rebound_pct: 23.31, recover_date: null,
    recover_days: 140, ongoing: 1, days_since_peak: 206, is_global_peak: 0,
    history_peak: 5877.2, history_peak_date: '2007-10-16',
  },
}]);
const sigBox = document.getElementById('signals');
const sigHtml = sigBox.children[0].innerHTML;
ok(sigHtml.indexOf('所选区间') >= 0 && sigHtml.indexOf('5353.75') >= 0,
   '信号卡展示所选区间的区间高点（5353.75）');
ok(sigHtml.indexOf('历史高点') >= 0 && sigHtml.indexOf('5877.20') >= 0,
   '信号卡同时保留全历史高点（5877.20）');
ok(sigHtml.indexOf('未收复') >= 0, '区间高点未修复时明确写「未收复」');
ok(sigHtml.indexOf('区间高点=历史高点') < 0, '区间高点≠历史高点时不加「=历史高点」标');

__T.STATE.dfrom = ''; __T.STATE.dto = '';
__T.renderSignals([{ id: 'sh000300', name: '沪深300', close: 1, peak: 2, dd_pct: -1, hint: '' }]);
const sigKids = document.getElementById('signals').children;
ok(sigKids[sigKids.length - 1].innerHTML.indexOf('winsig') < 0,
   '未设起止日时不渲染区间块（避免与全历史口径重复）');

// 表格：区间列存在，且按窗口口径渲染
__T.setTable({
  id: 'sh000300', name: '沪深300', gran: 'month',
  window: { from: '2015-01-05', to: '2015-12-31', peak: 5353.75, peak_date: '2015-06-08', is_global_peak: 0 },
  periods: [{ period: '2015-01', start: '2015-01-05', end: '2015-01-30', close: 3434.39,
              wpeak: 3643.79, wpeak_date: '2015-01-26', wdd_pct: -5.75,
              wdeepest_pct: -7.92, wdeepest_date: '2015-01-19',
              peak: 5877.2, dd_pct: -41.56, days_since_peak: 6975 }],
  daily: [], episodes: [], events: [], total: 244,
});
__T.STATE.view = 'period';
__T.applyTable();
const rowObj = __T.getRows()[0];
ok(rowObj.wpeak === 3643.79, '表格行带区间峰值');
const theadHtml = document.getElementById('q_#tbl thead').innerHTML;
ok(theadHtml.indexOf('区间峰值') >= 0 && theadHtml.indexOf('区间回撤%') >= 0,
   '表头出现「区间峰值 / 区间回撤%」列');
ok(theadHtml.indexOf('历史峰值') >= 0 && theadHtml.indexOf('历史回撤%') >= 0,
   '表头同时保留「历史峰值 / 历史回撤%」列');
ok(document.getElementById('tableHint').textContent.indexOf('区间高点') >= 0,
   '表头提示写明本次区间高点与起止日');

// 静态快照：按起止日裁剪 + 区间重算 + 周期聚合（无服务端接口时的兜底）
__T.setPayload({ table: {
  id: 'sh000300', name: '沪深300',
  daily: [
    { date: '2014-12-31', close: 3533, peak: 5877.2, peak_date: '2007-10-16', dd_pct: -39.9, days_since_peak: 1, state: 'drawdown' },
    { date: '2015-01-05', close: 3641, peak: 5877.2, peak_date: '2007-10-16', dd_pct: -38.0, days_since_peak: 2, state: 'drawdown' },
    { date: '2015-06-08', close: 5354, peak: 5877.2, peak_date: '2007-10-16', dd_pct: -8.9, days_since_peak: 3, state: 'drawdown' },
    { date: '2015-08-26', close: 3026, peak: 5877.2, peak_date: '2007-10-16', dd_pct: -48.5, days_since_peak: 4, state: 'drawdown' },
  ],
  events: [],
} });
const st = __T.staticTable('2015-01-01', '2015-12-31', 'month');
ok(st && st.daily.length === 3, '静态版按起止日裁剪（2014 年那根被剔除）');
ok(st.window && st.window.peak === 5354 && st.window.peak_date === '2015-06-08',
   '静态版区间高点 = 窗口内最高收盘 5354');
ok(st.periods.length === 3 && st.periods[0].start === '2015-01-05'
   && st.periods[0].end === '2015-01-05',
   '静态版周期聚合按起止日重新算（3 个自然月 3 行）');
ok(st.episodes.length >= 1 && st.episodes[0].drop_pct <= -10,
   '静态版回撤事件按窗口重算（峰→谷≥10%）');

// 静态快照的还原模式：data.json 的 reconstruct.by_etf 带全部可还原标的，
// 勾选哪只画哪只 —— 不能永远只出首只纳指ETF（GitHub Pages 用户报的问题）。
(function testStaticRecon(){
  const c1 = { etf: '513100', name: '纳指ETF国泰', bench_name: '纳斯达克100', t0: '2013-05-15', points: 2,
    curves: { price: [['2013-05-15', 0], ['2020-01-02', 180]], nav: [['2013-05-15', 0], ['2020-01-02', 150]],
              restored: [['2013-05-15', 0], ['2020-01-02', 120]], bench: [['2013-05-15', 0], ['2020-01-02', 118]] } };
  const c2 = { etf: '513500', name: '标普500ETF', bench_name: '标普500', t0: '2013-12-06', points: 2,
    curves: { price: [['2013-12-06', 0], ['2020-01-02', 90]], nav: [['2013-12-06', 0], ['2020-01-02', 80]],
              restored: [['2013-12-06', 0], ['2020-01-02', 70]], bench: [['2013-12-06', 0], ['2020-01-02', 68]] } };
  window.__STATIC__ = {};
  __T.setPayload({ reconstruct: {
    available: [{ etf: '513100', name: '纳指ETF国泰', bench_name: '纳斯达克100' },
                { etf: '513500', name: '标普500ETF', bench_name: '标普500' }],
    current: c1, by_etf: { '513100': c1, '513500': c2 },
  } });
  __T.STATE.dfrom = ''; __T.STATE.dto = '';
  __T.STATE.ids = ['513500']; __T.STATE.rcEtf = '';
  __T.loadReconstruct();
  ok(__T.getRECON() && __T.getRECON().etf === '513500',
     '静态版叠加标普500ETF → 画它自己（不再固定首只纳指ETF）');
  ok(__T.STATE.rcEtf === '513500', '静态版把下拉同步为当前命中的标的');
  __T.STATE.ids = ['513100', '513500'];
  __T.loadReconstruct();
  const smany = (LAST_OPT.series || []).filter(s => /还原%/.test(s.name));
  ok(smany.length === 2, '静态版叠加两只可还原标的 → 两条还原线（叠加对比）');
  __T.STATE.ids = ['sh000300']; __T.STATE.rcEtf = '513500';
  __T.loadReconstruct();
  ok(__T.getRECON() && __T.getRECON().etf === '513500',
     '静态版叠加无海外敞口时按下拉选择出图');
  ok(document.getElementById('rcNote').style.display === 'block'
     && document.getElementById('rcNote').textContent.indexOf('无海外敞口') >= 0,
     '静态版同样提示「当前叠加的标的无海外敞口」');
  __T.STATE.ids = ['sh000300']; __T.STATE.rcEtf = '513030';
  __T.loadReconstruct();
  ok(__T.getRECON() && __T.getRECON().etf === '513100',
     '静态快照缺该只曲线时回退到第一只有数据的，不白屏（513030 未打包 → 取 513100）');
  delete window.__STATIC__;
})();

console.log('\n结果: ' + pass + ' 通过, ' + fail + ' 失败');
process.exit(fail ? 1 : 0);
