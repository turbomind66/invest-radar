/**
 * 新闻栏日期段前端测试（离线，无需浏览器/网络）。
 *
 * 做法与 test_frontend.js 一致：从 templates/index.html 提取真实内联脚本，
 * 用轻量 DOM / fetch 桩件在 Node 中执行。追加一小段导出器，用来设置
 * eval 私有的 let 变量（STATIC_RAW / newsRange / DATA / newsCache）。
 *
 * 运行：node tests/test_news_range.js
 */
const fs = require('fs');
const path = require('path');

const HTML = path.join(__dirname, '..', 'templates', 'index.html');

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
global.echarts = {
  init: () => ({ setOption() {}, resize() {}, clear() {} }),
  graphic: { LinearGradient: function () {} },
};

const API_ITEMS = [
  { date: '2026-10-30', time: '2026-10-30 18:41', title: '接口甲', url: 'u1', src: '东方财富' },
  { date: '2026-10-27', time: '2026-10-27 17:28', title: '接口乙', url: 'u2', src: '新浪财经' },
];
const fetchLog = [];
global.fetch = async (url) => {
  fetchLog.push(url);
  return { ok: true, status: 200, json: async () => ({ count: API_ITEMS.length, items: API_ITEMS }) };
};

const EXPORTER = `
;global.__T = {
  setRaw:   r => { STATIC_RAW = r; },
  setRange: r => { newsRange = r; },
  getRange: () => newsRange,
  setData:  d => { DATA = d; },
  setCache: c => { newsCache = c; },
};`;
eval(extractScript(fs.readFileSync(HTML, 'utf8')) + EXPORTER);

// ---------------- 断言工具 ----------------
let pass = 0, fail = 0;
function ok(cond, name) {
  if (cond) { pass++; console.log('  \u2705 ' + name); }
  else { fail++; console.log('  \u274c ' + name); }
}

const D = {
  dates: ['2026-10-27', '2026-10-30'],
  latest_date: '2026-10-30',
  filters: { date: '2026-10-30' },
  news_ok: true,
  news: [{ time: '2026-10-30 18:41', title: '当日新闻甲', url: 'u1', src: '东方财富' }],
  news_fallback: { date: '2026-10-27', items: [
    { time: '2026-10-27 17:28', title: '兜底新闻', url: 'u2', src: '新浪财经' },
  ] },
};

(async () => {
  console.log('\n=== 新闻栏：静态快照区间过滤 ===');
  __T.setRaw({ news: {
    '2026-10-26': [{ time: '2026-10-26 08:00', title: '区间外旧' }],
    '2026-10-27': [{ time: '2026-10-27 09:00', title: '甲' }],
    '2026-10-29': [],
    '2026-10-30': [
      { time: '2026-10-30 18:41', title: '乙' },
      { time: '2026-10-30 00:27', title: '丙' },
    ],
    '2026-11-02': [{ time: '2026-11-02 10:00', title: '区间外新' }],
  } });
  const r = newsStaticRange('2026-10-27', '2026-10-30');
  ok(r.length === 3, '闭区间只取范围内 3 条，排除区间外两天');
  ok(r.map(x => x.title).join(',') === '乙,丙,甲', '跨天按时间倒序');
  ok(r[0].date === '2026-10-30' && r[2].date === '2026-10-27', '每条补上 date 供分组');
  ok(newsStaticRange('2026-11-05', '2026-11-10').length === 0, '空区间返回 []');

  console.log('\n=== 新闻栏：按日期分组渲染 ===');
  renderNewsGrouped(r, '2026-10-27', '2026-10-30');
  const grouped = els['news'].innerHTML;
  ok((grouped.match(/class="nd"/g) || []).length === 2, '生成两个日期分隔标题');
  ok(grouped.includes('2026-10-30') && grouped.includes('2026-10-27'), '分隔标题带日期');
  ok(grouped.includes('乙') && grouped.includes('甲'), '区间内条目都列出');
  renderNewsGrouped([], '2026-11-05', '2026-11-10');
  ok(els['news'].innerHTML.includes('没有已归档的新闻'), '无结果时给出明确提示');

  console.log('\n=== 默认：跟随全局所选日期（原行为不变） ===');
  __T.setData(D);
  __T.setRange(null);
  __T.setCache(null);
  fetchLog.length = 0;
  await renderNews(D, true);
  ok(els['news'].innerHTML.includes('当日新闻甲'), '显示所选日期的新闻');
  ok(els['news-range-info'].textContent.includes('跟随'), '提示当前为跟随模式');
  ok(els['news-from'].value === '2026-10-30' && els['news-to'].value === '2026-10-30',
     '起止输入框自动同步为所选日期');
  ok(fetchLog.length === 0, '跟随模式不发起区间请求');

  const D2 = Object.assign({}, D, { news: [] });
  await renderNews(D2, true);
  ok(els['news'].innerHTML.includes('兜底新闻'), '当日无新闻时仍展示最近有新闻的一天');
  ok(els['news'].innerHTML.includes('2026-10-27'), '兜底结果标注来源日期');

  console.log('\n=== 自定义日期段（动态站走 /api/news） ===');
  __T.setRange({ from: '2026-10-27', to: '2026-10-30' });
  __T.setCache(null);
  fetchLog.length = 0;
  await renderNews(D, true);
  ok(fetchLog.length === 1 && fetchLog[0].includes('api/news?from=2026-10-27&to=2026-10-30'),
     '按起止日期请求 /api/news');
  ok(els['news'].innerHTML.includes('接口甲') && els['news'].innerHTML.includes('接口乙'),
     '区间内两天的新闻都列出');
  ok(els['news-range-info'].textContent.includes('共 2 条'), '显示区间总条数');
  fetchLog.length = 0;
  await renderNews(D, false);
  ok(fetchLog.length === 0, '同一区间复用缓存，切换筛选不重复请求');

  console.log('\n=== 交互校验 ===');
  __T.setRange(null);
  els['news-from'].value = '2026-10-30';
  els['news-to'].value = '2026-10-27';
  onNewsQuery();
  ok(__T.getRange() === null, '起始晚于结束时不进入区间模式');
  ok(els['news-range-info'].textContent.includes('不能晚于'), '给出错误提示');
  els['news-from'].value = '2026-10-27';
  els['news-to'].value = '2026-10-28';
  onNewsQuery();
  ok(__T.getRange() && __T.getRange().from === '2026-10-27', '合法区间生效');
  onNewsFollow();
  ok(__T.getRange() === null, '「跟随日期」按钮回到单日模式');

  console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
  process.exit(fail ? 1 : 0);
})();
