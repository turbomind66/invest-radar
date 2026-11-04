/**
 * 趋势图日期缩放 + 三张图二级口径文案（离线，无需浏览器/网络）。
 *
 * 做法与 test_frontend.js / test_news_range.js 一致：从 templates/index.html
 * 提取真实内联脚本，用轻量 DOM / ECharts 桩件在 Node 中执行；追加导出器
 * 暴露 eval 私有的函数。桩件额外实现 getOption / dispatchAction，用来验证
 * 「notMerge 重绘时保留用户缩放窗口」与「全览」按钮。
 *
 * 运行：node tests/test_trend_zoom.js
 */
const fs = require('fs');
const path = require('path');

const HTML = path.join(__dirname, '..', 'templates', 'index.html');
const HTML_TEXT = fs.readFileSync(HTML, 'utf8');

function extractScript(html) {
  const m = html.match(/<script>([\s\S]*?)<\/script>\s*<\/body>/);
  if (!m) throw new Error('未能在 index.html 中定位内联脚本');
  return m[1].replace(/\nloadData\(\);\s*$/, '\n');
}

// ---------------- 桩件 ----------------
const els = {};
const mkEl = (id) => ({
  id, innerHTML: '', textContent: '', value: '', style: {},
  classList: { add() {}, remove() {} },
});
global.document = {
  getElementById: (id) => els[id] || (els[id] = mkEl(id)),
  querySelector: (s) => els['q_' + s] || (els['q_' + s] = mkEl(s)),
  querySelectorAll: () => [],
  activeElement: null,
};
global.window = { addEventListener() {} };   // __STATIC__ 未设 ⇒ 动态模式
global.fetch = async () => ({ ok: true, status: 200, json: async () => ({}) });

const charts = {};
function mkChart(id) {
  const c = {
    id, _opt: null, actions: [], cleared: 0,
    setOption(o) { c._opt = o; },
    clear() { c._opt = null; c.cleared++; },
    resize() {},
    // 只回填 dataZoom —— 与真实 ECharts getOption() 的读取路径等价
    getOption() { return c._opt ? { dataZoom: c._opt.dataZoom } : null; },
    dispatchAction(a) {
      c.actions.push(a);
      if (a.type === 'dataZoom' && c._opt && c._opt.dataZoom) {
        c._opt.dataZoom.forEach((d) => { d.start = a.start; d.end = a.end; });
      }
    },
  };
  return c;
}
global.echarts = {
  init: (dom) => charts[dom.id] || (charts[dom.id] = mkChart(dom.id)),
  graphic: { LinearGradient: function () {} },
};

const EXPORTER = `
;global.__T = {
  drawTrend, trendZoomRange, resetTrendZoom,
  windowDays: () => TREND_WINDOW_DAYS,
};`;
eval(extractScript(HTML_TEXT) + EXPORTER);

// ---------------- 断言工具 ----------------
let pass = 0, fail = 0;
function ok(cond, name) {
  if (cond) { pass++; console.log('  \u2705 ' + name); }
  else { fail++; console.log('  \u274c ' + name); }
}
const near = (a, b) => Math.abs(a - b) < 0.01;

function mkHistory(n, withShare = true) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const d = new Date(Date.UTC(2025, 0, 1) + i * 86400000).toISOString().slice(0, 10);
    out.push({
      date: d,
      main_cnt: 1, total_main_net: (i - n / 2) * 1e8,
      share_cnt: withShare ? 1 : 0,
      share_net_sub: withShare ? (i % 7 - 3) : null,
    });
  }
  return out;
}

(async () => {
  console.log('\n=== 默认窗口：只显示最近 90 个交易日 ===');
  ok(__T.windowDays() === 90, '窗口常量为 90 天');
  const z366 = __T.trendZoomRange(366);
  ok(near(z366.start, (1 - 90 / 366) * 100) && z366.end === 100,
     '366 天时 start≈75.41%、end=100（尾部 90 天）');
  const z60 = __T.trendZoomRange(60);
  ok(z60.start === 0 && z60.end === 100, '不足 90 天时全览，不出现无意义滑块窗口');
  const z3 = __T.trendZoomRange(3);
  ok(z3.start === 0 && z3.end === 100, '3 天时全览');

  console.log('\n=== drawTrend：dataZoom(inside + slider) 生效 ===');
  const c = charts['trend'] || (charts['trend'] = mkChart('trend'));
  // drawTrend 内部若已 init 过则复用 charts['trend']，这里保证同实例
  __T.drawTrend(mkHistory(366));
  const o = c._opt;
  ok(!!o && Array.isArray(o.dataZoom) && o.dataZoom.length === 2,
     '配置了两个 dataZoom 组件');
  ok(o.dataZoom[0].type === 'inside' && o.dataZoom[0].zoomOnMouseWheel === true,
     'inside 组件开启鼠标滚轮缩放');
  ok(o.dataZoom[0].moveOnMouseMove === true, 'inside 组件支持拖拽平移');
  ok(o.dataZoom[1].type === 'slider' && o.dataZoom[1].bottom === 8,
     'slider 组件固定在底部');
  ok(near(o.dataZoom[0].start, z366.start) && near(o.dataZoom[1].start, z366.start),
     '两个组件共用同一初始窗口（滚轮与滑块同步）');
  ok(o.dataZoom.every((d) => d.xAxisIndex === 0), '都作用于唯一的 category x 轴');
  ok(o.grid.bottom >= 70, 'grid.bottom 为滑块留出空间（>=70）');
  ok(o.series.length === 2, '双轴两条线（二级 + 一级）均保留');
  ok(o.xAxis.data.length === 366, 'x 轴仍是全量 366 天，缩放只裁剪窗口');

  console.log('\n=== 重绘保留用户缩放窗口 ===');
  c._opt.dataZoom.forEach((d) => { d.start = 20; d.end = 60; });  // 模拟用户缩放
  __T.drawTrend(mkHistory(366));
  ok(near(c._opt.dataZoom[0].start, 20) && near(c._opt.dataZoom[0].end, 60),
     'notMerge 全量重绘后 inside 窗口仍为 20~60');
  ok(near(c._opt.dataZoom[1].start, 20) && near(c._opt.dataZoom[1].end, 60),
     'slider 窗口同步保留');

  console.log('\n=== 「全览」按钮 ===');
  __T.resetTrendZoom();
  const act = c.actions[c.actions.length - 1];
  ok(act && act.type === 'dataZoom' && act.start === 0 && act.end === 100,
     '派发 dataZoom 0~100，恢复全量区间');
  __T.drawTrend(mkHistory(366));
  ok(near(c._opt.dataZoom[0].start, 0) && near(c._opt.dataZoom[0].end, 100),
     '全览后重绘保持全量，不被默认窗口覆盖');

  console.log('\n=== 边界：小数据集 / 无一级数据 / 空历史 ===');
  c._opt.dataZoom.forEach((d) => { d.start = 30; d.end = 80; });
  __T.drawTrend(mkHistory(3));
  ok(c._opt.dataZoom[0].start === 0 && c._opt.dataZoom[0].end === 100,
     '切到 3 天筛选时强制全览，避免只剩一条线');
  __T.drawTrend(mkHistory(366, false));
  ok(c._opt.series.length === 1 && c._opt.dataZoom.length === 2,
     '无一级份额时单轴仍带缩放');
  const cleared = c.cleared;
  __T.drawTrend([]);
  ok(c.cleared > cleared && c._opt.title.text.includes('无历史数据'),
     '空历史走 clear + 提示，不抛错');

  console.log('\n=== ②A：三张图的二级口径标注 ===');
  // 只数三张图 <small> 里的标注：页面另有一处既有回退提示也含相同措辞
  ok((HTML_TEXT.match(/<small>二级主力净流入口径｜/g) || []).length === 3,
     '三张图标题各标注一次「二级主力净流入口径」');
  ok(/class="scope-line"[\s\S]*?不可相加或直接比较/.test(HTML_TEXT),
     '口径说明行明确写出「不可相加或直接比较」');
  ok(/class="scope-line"[\s\S]*?一级市场净申购/.test(HTML_TEXT),
     '口径说明行点明头条采用的是一级市场净申购');
  ok(/滚轮缩放 · 拖拽平移 · 底部滑块/.test(HTML_TEXT)
     && /onclick="resetTrendZoom\(\)"/.test(HTML_TEXT),
     '趋势面板提供缩放操作提示与「全览」按钮');

  console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
  process.exit(fail ? 1 : 0);
})();
