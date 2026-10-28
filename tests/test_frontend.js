/**
 * 前端渲染逻辑测试（离线，无需浏览器）。
 *
 * 做法：从 templates/index.html 提取内联 <script>，用轻量 DOM / ECharts / fetch 桩件
 * 在 Node 中真实执行页面的渲染与筛选逻辑，断言输出结果。
 *
 * 运行：node tests/test_frontend.js
 */
const fs = require('fs');
const path = require('path');

const HTML = path.join(__dirname, '..', 'templates', 'index.html');

// ---------------- 提取页面脚本 ----------------
function extractScript(html) {
  const m = html.match(/<script>([\s\S]*?)<\/script>\s*<\/body>/);
  if (!m) throw new Error('未能在 index.html 中定位内联脚本');
  // 去掉末尾的自动入口调用，测试中手动控制时序
  return m[1].replace(/\nloadData\(\);\s*$/, '\n');
}

// ---------------- 桩件 ----------------
const els = {};
const mkEl = (id) => ({
  id, innerHTML: '', textContent: '', value: '', disabled: false, style: {},
  classList: { add() {}, remove() {} },
});
global.document = {
  getElementById: (id) => els[id] || (els[id] = mkEl(id)),
  querySelector: (sel) => els['q_' + sel] || (els['q_' + sel] = mkEl(sel)),
  querySelectorAll: () => [],
};
global.window = { addEventListener() {} };

const optionLog = {};
global.echarts = {
  init: (el) => {
    const key = (el && el.id) || 'unknown';
    return { setOption: (o) => { optionLog[key] = o; }, resize() {}, clear() {} };
  },
  graphic: { LinearGradient: function () {} },
};

// ---------------- 桩后端数据（与真实 /api/data 结构一致）----------------
const RAW = [
  { code: '510300', name: '华泰柏瑞沪深300ETF', cat: '沪深300', price: 4.123, chg: -1.34, amount: 5.848e9, main_net: -8.61e8 },
  { code: '510310', name: '易方达沪深300ETF', cat: '沪深300', price: 1.900, chg: -1.20, amount: 1.200e9, main_net: -1.10e8 },
  { code: '510050', name: '华夏上证50ETF', cat: '上证50', price: 3.100, chg: 0.40, amount: 2.100e9, main_net: 1.66e8 },
  { code: '588000', name: '华夏科创50ETF', cat: '科创50', price: 1.100, chg: -3.10, amount: 3.000e9, main_net: -14.73e8 },
];
const ALL_DATES = ['2026-10-26', '2026-10-27', '2026-10-28'];
const FULL_TOTAL_YI = +(RAW.reduce((a, e) => a + e.main_net, 0) / 1e8).toFixed(2);  // -22.78

function buildData(url) {
  const q = new URLSearchParams(url.split('?')[1] || '');
  const qdate = q.get('date');
  const date = qdate || '2026-10-27';                       // 桩件模拟“自动回退到最近有效日”
  const cats = (q.get('cats') || '').split(',').filter(Boolean);
  const codes = (q.get('codes') || '').split(',').filter(Boolean);

  let etf = RAW.filter(e =>
    (!cats.length || cats.includes(e.cat)) &&
    (!codes.length || codes.includes(e.code)));
  // 模拟“盘前当日”：价格已抓取、主力资金尚未生成
  if (date === '2026-10-28') etf = etf.map(e => ({ ...e, main_net: null }));
  const hasMain = etf.filter(e => e.main_net !== null).length;
  const total = etf.reduce((a, e) => a + (e.main_net || 0), 0);

  const byCat = {};
  etf.forEach(e => { byCat[e.cat] = (byCat[e.cat] || 0) + (e.main_net || 0); });

  // 与真实后端一致：筛选后无数据时 history 返回空数组
  const history = etf.length === 0 ? [] : ALL_DATES.map(d => {
    const rows = d === '2026-10-28' ? etf.map(e => ({ ...e, main_net: null })) : etf;
    return {
      date: d,
      total_main_net: rows.reduce((a, e) => a + (e.main_net || 0), 0),
      by_cat: {}, etf_count: rows.length,
      main_cnt: rows.filter(e => e.main_net !== null).length,
    };
  });

  return {
    latest_date: date, dates: ALL_DATES, last_fetch: '2026-10-28 09:55:00',
    news_ok: false, mainnet_ok: hasMain > 0, partial_main: false,
    etf, news: [], history,
    filters: {
      date, cats, codes, filtered: !!(cats.length || codes.length),
      auto: !qdate, fallback_from: qdate ? null : '2026-10-28',
    },
    options: {
      dates: ALL_DATES,
      cats: [{ cat: '上证50', count: 1 }, { cat: '沪深300', count: 2 }, { cat: '科创50', count: 1 }],
      funds: RAW.map(r => ({ code: r.code, name: r.name, cat: r.cat })),
    },
    summary: {
      total_main_net_yi: hasMain ? +(total / 1e8).toFixed(2) : null,
      total_amount_yi: +(etf.reduce((a, e) => a + (e.amount || 0), 0) / 1e8).toFixed(2),
      etf_count: etf.length, main_cnt: hasMain,
      by_cat_yi: Object.fromEntries(Object.entries(byCat).map(([k, v]) => [k, +(v / 1e8).toFixed(2)])),
      full_total_main_net_yi: FULL_TOTAL_YI, full_etf_count: RAW.length,
    },
    signal: total > 0 ? ['净买入', 'buy'] : ['净卖出', 'sell'],
  };
}
global.fetch = async (url) => ({ json: async () => buildData(url) });

// ---------------- 执行页面脚本 ----------------
eval(extractScript(fs.readFileSync(HTML, 'utf8')));

// ---------------- 断言工具 ----------------
let pass = 0, fail = 0;
const tick = (ms = 90) => new Promise(r => setTimeout(r, ms));
function chk(cond, msg) {
  if (cond) { pass++; console.log('  \u2705 ' + msg); }
  else { fail++; console.log('  \u274c ' + msg); }
}
const rowCount = () => (els['q_#tbl tbody'].innerHTML.match(/<tr>/g) || []).length;
const chipCount = (id) => (els[id].innerHTML.match(/class="chip/g) || []).length;

// ---------------- 用例 ----------------
(async () => {
  console.log('\n[1] 首屏（自动模式，后端回退到最近有效日）');
  await loadData(); await tick();
  chk(els['meta-date'].textContent.includes('2026-10-27'), '元信息显示生效日期');
  chk(els['meta-date'].textContent.includes('未生成'), '元信息提示自动回退原因');
  chk(els['cards'].innerHTML.includes(String(FULL_TOTAL_YI)), `汇总卡片为全量合计 ${FULL_TOTAL_YI} 亿`);
  chk(els['f-cats'].innerHTML.includes('沪深300'), '类别 chips 已渲染');
  chk(chipCount('f-codes') === 5, `基金 chips = 全部 + 4 只（实际 ${chipCount('f-codes')}）`);
  chk(els['q_#tbl tbody'].innerHTML.includes('华泰柏瑞'), '明细表已渲染');
  chk(els['q_#tbl tfoot'].innerHTML.includes('合计'), '明细表底部有合计行');
  chk(rowCount() === 4, `明细 4 行（实际 ${rowCount()}）`);

  console.log('\n[2] 按类别筛选（点选“沪深300”）');
  toggleCat('沪深300'); await tick(150);
  chk(els['f-cats'].innerHTML.includes('chip on'), '类别 chip 呈选中态');
  chk(els['f-summary'].innerHTML.includes('沪深300'), '筛选摘要含类别');
  chk(rowCount() === 2, `明细过滤为 2 行（实际 ${rowCount()}）`);
  chk(els['cards'].innerHTML.includes('-9.71'), '汇总按筛选重算为 -9.71 亿');
  chk(optionLog['trend'] && optionLog['trend'].series[0].data.length === 3, '趋势图重绘为 3 个点');
  chk(optionLog['trend'] && optionLog['trend'].series[0].data[2] === null, '当日无数据点在趋势图中断线(null)');
  chk(chipCount('f-codes') === 3, `基金列表联动收敛为 全部+2 只（实际 ${chipCount('f-codes')}）`);

  console.log('\n[3] 类别 + 基金叠加（取交集）');
  toggleCode('510300'); await tick(150);
  chk(rowCount() === 1, `交集后仅 1 只（实际 ${rowCount()}）`);
  chk(els['q_#tbl tbody'].innerHTML.includes('510300'), '保留的是 510300');
  chk(els['q_#tbl tbody'].innerHTML.includes('-8.61'), '该基金净买入 -8.61 亿');
  chk(els['f-summary'].innerHTML.includes('全量'), '筛选摘要给出“当日全量”对比');

  console.log('\n[4] 按日期筛选（切到历史 2026-10-26）');
  onDateChange('2026-10-26'); await tick(150);
  chk(els['meta-date'].textContent.includes('2026-10-26'), '日期已切换');
  chk(els['meta-date'].textContent.includes('历史'), '标注为历史日期');
  chk(!els['meta-date'].textContent.includes('未生成'), '不再显示回退提示');

  console.log('\n[5] 重置筛选');
  resetFilters(); await tick(200);
  chk(els['meta-date'].textContent.includes('2026-10-27'), '回到自动模式（最新有效日）');
  chk(rowCount() === 4, `恢复全量 4 行（实际 ${rowCount()}）`);

  console.log('\n[6] 空结果兜底（类别与基金无交集）');
  render(buildData('?cats=' + encodeURIComponent('上证50') + '&codes=510300'));
  await tick(80);
  chk(els['q_#tbl tbody'].innerHTML.includes('没有数据'), '明细显示友好空提示');
  chk(optionLog['trend'] && optionLog['trend'].title
      && optionLog['trend'].title.text.includes('无历史数据'), '趋势图显示无数据提示');

  console.log('\n[7] 主力数据缺失日（选中当日盘前）');
  render(buildData('?date=2026-10-28'));
  await tick(80);
  chk(els['warn'].style.display === 'block', '显示主力数据缺失警示条');
  chk(els['cards'].innerHTML.includes('—'), '汇总卡片显示占位符而非错误的 0');

  console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => {
  console.error('\n\u274c 运行时异常：', e);
  process.exit(1);
});
