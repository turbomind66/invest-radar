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
  classList: {
    _s: new Set(),
    add(...c) { c.forEach((x) => this._s.add(x)); },
    remove(...c) { c.forEach((x) => this._s.delete(x)); },
    contains(x) { return this._s.has(x); },
  },
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
  chk(els['fresh-data'].textContent.includes('2026-10-27'), '时间戳条显示生效日期');
  chk(els['fresh-data'].textContent.includes('未生成'), '时间戳条提示自动回退原因');
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
  chk(els['fresh-data'].textContent.includes('2026-10-26'), '日期已切换');
  chk(els['fresh-data'].textContent.includes('历史'), '标注为历史日期');
  chk(!els['fresh-data'].textContent.includes('未生成'), '不再显示回退提示');

  console.log('\n[5] 重置筛选');
  resetFilters(); await tick(200);
  chk(els['fresh-data'].textContent.includes('2026-10-27'), '回到自动模式（最新有效日）');
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

  console.log('\n[8] 「数据更新于」时间戳');
  // 北京时间 2026-10-28 09:55 = UTC 01:55
  chk(parseBjTime('2026-10-28 09:55:00') === Date.UTC(2026, 9, 28, 1, 55, 0),
      '北京时间字符串按 UTC+8 解析');
  chk(parseBjTime('2026-10-28T09:55') === Date.UTC(2026, 9, 28, 1, 55, 0),
      '兼容 T 分隔且缺省秒');
  chk(parseBjTime('—') === null && parseBjTime('') === null && parseBjTime(null) === null,
      '无效/缺失时间返回 null（不抛异常）');

  // 相对时间（传入固定 now，避免依赖运行时刻）
  const NOW = parseBjTime('2026-10-28 10:55:00');
  chk(relTime(NOW - 20 * 1000, NOW) === '刚刚更新', '20 秒前 → 刚刚更新');
  chk(relTime(NOW - 25 * 60 * 1000, NOW) === '25 分钟前', '25 分钟前 → 25 分钟前');
  chk(relTime(NOW - 6 * 3600 * 1000, NOW) === '6 小时前', '6 小时前 → 6 小时前');
  chk(relTime(NOW - 50 * 3600 * 1000, NOW) === '2 天前', '50 小时前 → 2 天前');
  chk(relTime(NOW + 60 * 1000, NOW) === '刚刚更新', '未来时间（时钟偏差）→ 刚刚更新');
  chk(relTime(null, NOW) === '', '无时间 → 空串');

  render(buildData(''));
  await tick(80);
  chk(els['fresh-time'].textContent === '2026-10-28 09:55', '时间戳显示到分钟（实际 ' + els['fresh-time'].textContent + '）');
  chk(/前|刚刚/.test(els['fresh-rel'].textContent), '相对时间徽标有内容（实际 ' + els['fresh-rel'].textContent + '）');
  chk(els['fresh-data'].textContent.includes('2026-10-27'), '时间戳条同时标明数据日期');
  chk(els['fresh-data'].textContent.includes('未生成'), '自动回退时给出原因');
  chk(!els['fresh'].classList.contains('warn'), '新鲜数据不告警');

  // 数据陈旧 → 琥珀告警
  render(Object.assign(buildData(''), { last_fetch: '2026-10-01 16:20:00' }));
  await tick(80);
  chk(els['fresh'].classList.contains('warn'), '超过 3 天未更新 → 告警态');
  chk(els['fresh-hint'].textContent.includes('未更新'), '告警时提示文案切换');
  chk(els['fresh-time'].textContent === '2026-10-01 16:20', '陈旧数据仍显示真实更新时间');

  // 更新时间未知
  render(Object.assign(buildData(''), { last_fetch: '—' }));
  await tick(80);
  chk(els['fresh'].classList.contains('unknown'), '更新时间缺失 → 中性灰态');
  chk(els['fresh-time'].textContent === '—', '更新时间缺失显示占位符');
  chk(els['fresh-rel'].textContent === '更新时间未知', '更新时间缺失给出明确说明');

  // 历史日期时时间戳条标明「历史」
  onDateChange('2026-10-26'); await tick(150);
  chk(els['fresh-data'].textContent.includes('历史'), '查看历史日期时标注历史');

  console.log('\n[9] 类别净买入/净卖出双饼图（回归：负值类别不得被吞掉）');
  // 真实故障复现：8 类中 7 类为净卖出时，单张饼图只渲染唯一正值类别，
  // 表现为"全是中证A500买入"。现拆成买入/卖出两张饼图：
  // 卖出图用绝对值画扇区角度（否则负值仍被压成 0），标签/tooltip 保留真实负号。
  render(buildData(''));
  await tick(150);
  const buyOpt = optionLog['cat-buy'];
  const sellOpt = optionLog['cat-sell'];
  chk(!!buyOpt && buyOpt.series[0].type === 'pie', '净买入侧使用饼图');
  chk(!!sellOpt && sellOpt.series[0].type === 'pie', '净卖出侧使用饼图');
  const buyData = (buyOpt && buyOpt.series[0].data) || [];
  const sellData = (sellOpt && sellOpt.series[0].data) || [];
  const buyNames = buyData.map(x => x.name);
  const sellNames = sellData.map(x => x.name);
  chk(buyNames.length + sellNames.length === 3,
      `3 个类别全部保留（买入 ${buyNames.length} + 卖出 ${sellNames.length}）`);
  chk(buyNames.includes('上证50'), '净买入类别「上证50」在买入饼图');
  chk(sellNames.includes('科创50'), '净卖出类别「科创50」在卖出饼图');
  chk(sellNames.includes('沪深300'), '净卖出类别「沪深300」在卖出饼图');
  chk(buyData.every(x => x.real > 0), '买入饼图只含正值类别');
  chk(sellData.every(x => x.real < 0), '卖出饼图只含负值类别');
  chk(sellData.every(x => x.value > 0), '卖出饼图扇区角度用绝对值（正值）');
  const sci = sellData.find(x => x.name === '科创50');
  chk(!!sci && sci.value === 14.73, '卖出扇区角度 = |-14.73| = 14.73');
  // 标签策略：占比 ≥20% 的主扇区才带标签（percent 由 ECharts 注入）
  // 扇区标签只放类别名（窄面板下"名称+数值"两行会被画布边缘裁切），数值在下方色点列表
  const sellLbl = sellOpt.series[0].label.formatter({ name: '科创50', data: sci, percent: 35.5 });
  chk(String(sellLbl) === '科创50', `卖出图主扇区标签为类别名（实际 "${sellLbl}"）`);
  const buyLbl = buyOpt.series[0].label.formatter({ name: '上证50', data: buyData[0], percent: 100 });
  chk(String(buyLbl) === '上证50', '买入图主扇区标签为类别名');
  const smallLbl = sellOpt.series[0].label.formatter({ name: '中证500', data: { real: -0.90 }, percent: 2.2 });
  chk(String(smallLbl) === '', '小扇区不画标签（数值在下方色点列表，避免叠字/裁切）');
  chk(els['cat-sell-list'].innerHTML.includes('-14.73亿') && els['cat-sell-list'].innerHTML.includes('-9.71亿'),
      '被裁切风险的数值完整保留在色点列表');
  chk(buyData.concat(sellData).every(x => typeof x.value === 'number' && !isNaN(x.value)),
      '所有扇区值均为有效数值');
  // 饼图下方色点列表：所有类别数值一眼可见（不靠 hover、无图例分页）
  chk(els['cat-buy-list'].innerHTML.includes('1.66亿'), '买入图下方列表显示数值');
  chk(els['cat-sell-list'].innerHTML.includes('-14.73亿'), '卖出图下方列表显示带负号数值');
  chk(els['cat-sell-list'].innerHTML.includes('科创50') && els['cat-sell-list'].innerHTML.includes('沪深300'),
      '卖出图下方列表含全部类别名');

  console.log('\n[10] 类内基金明细（回归：正值基金不得被类别合计吞掉）');
  // 桩件复盘：沪深300 两只（华泰柏瑞 -8.61 + 易方达 -1.10 = -9.71）应展开逐只明细，
  // 单基金类别（上证50 / 科创50）不展开——类别值即基金值，无对冲可能。
  const sellHtml = els['cat-sell-list'].innerHTML;
  const buyHtml = els['cat-buy-list'].innerHTML;
  chk(sellHtml.includes('cat-sub'), '多基金类别（沪深300）展开类内明细行');
  chk(sellHtml.includes('华泰柏瑞') && sellHtml.includes('易方达'), '明细用公司短标签区分同指数基金');
  chk(sellHtml.includes('-8.61') && sellHtml.includes('-1.10'), '明细含类内逐只数值');
  chk(!buyHtml.includes('cat-sub'), '单基金类别（上证50）不展开明细');
  // 真实故障场景重放（2026-10-27 中证1000 类）：类内 易方达 +0.17 被合计 -2.01 吞进卖出图，
  // 改造后卖出图下方明细必须能看到这只逆势净买入基金。
  drawCat({ '中证1000': -2.01, '中证A500': 0.43 }, [
    { code: '560010', name: '易方达中证1000ETF', cat: '中证1000', main_net: 17498160 },
    { code: '159845', name: '华夏中证1000ETF', cat: '中证1000', main_net: -1574400 },
    { code: '512100', name: '南方中证1000ETF', cat: '中证1000', main_net: -216634432 },
    { code: '563300', name: '中证A500ETF', cat: '中证A500', main_net: 42793296 },
  ]);
  const mixSell = els['cat-sell-list'].innerHTML;
  const mixBuy = els['cat-buy-list'].innerHTML;
  chk(mixSell.includes('易方达') && mixSell.includes('+0.17'),
      '卖出类别明细中可见类内正值「易方达 +0.17」');
  chk(mixSell.includes('<b class="r">+0.17</b>'), '类内正值标红（红=净买入全局语言）');
  chk(mixSell.includes('华夏') && mixSell.includes('-0.02') && mixSell.includes('南方') && mixSell.includes('-2.17'),
      '类内其余两只负值基金完整可见');
  chk(mixSell.includes('中证1000') && mixSell.includes('-2.01亿'), '类别行仍保留合计 -2.01亿');
  chk(mixBuy.includes('中证A500') && mixBuy.includes('+0.43亿') && !mixBuy.includes('cat-sub'),
      '单基金买入类别（中证A500）不展开明细');
  const tipTxt = String(optionLog['cat-sell'].tooltip.formatter(
    { name: '中证1000', percent: 4.7, data: { real: -2.01 } }));
  chk(tipTxt.includes('易方达 +0.17') && tipTxt.includes('南方 -2.17'),
      'tooltip 同步展开类内明细');

  console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => {
  console.error('\n\u274c 运行时异常：', e);
  process.exit(1);
});
