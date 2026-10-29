/**
 * 静态站产物启动测试（离线）。
 *
 * 直接加载 docs/index.html（staticsite.py 的生成物，非模板），按 GitHub Pages 的
 * 执行顺序跑两个内联脚本，验证：
 *   - 数据源是相对路径 data.json（Pages 下没有后端）
 *   - __STATIC__ 标记生效，界面切到静态模式
 *   - 首屏自动回退到"最近有效日"
 *   - 刷新按钮被换成 Actions 链接
 *   - 点筛选只做本地计算，不再发请求
 *
 * 断言值全部从 data.json 现算，不绑定具体某天的行情数字。
 * 运行：node tests/test_static_boot.js
 */
const fs = require('fs');
const path = require('path');

const DOCS = path.join(__dirname, '..', '..', 'docs');
const HTML = path.join(DOCS, 'index.html');
const RAW = JSON.parse(fs.readFileSync(path.join(DOCS, 'data.json'), 'utf8'));

if (!fs.existsSync(HTML)) {
  console.error('缺少 docs/index.html，请先运行：python staticsite.py --no-sync');
  process.exit(1);
}

// ---------------- 桩件 ----------------
const els = {};
const replaced = [];
const mkEl = (id) => ({
  id, innerHTML: '', textContent: '', value: '', disabled: false, style: {}, tag: 'div',
  classList: { add() {}, remove() {} },
  parentNode: { replaceChild(n, o) { replaced.push([o.id, n.tag, n.href]); } },
});
global.document = {
  getElementById: (id) => els[id] || (els[id] = mkEl(id)),
  querySelector: (s) => els['q_' + s] || (els['q_' + s] = mkEl(s)),
  querySelectorAll: () => [],
  createElement: (t) => Object.assign(mkEl('new_' + t), { tag: t }),
};
global.window = { addEventListener() {}, open(u) { global.__opened = u; } };
global.echarts = {
  init: () => ({ setOption() {}, resize() {}, clear() {} }),
  graphic: { LinearGradient: function () {} },
};
const fetched = [];
global.fetch = async (u) => { fetched.push(u); return { ok: true, json: async () => RAW }; };

// 顺序执行产物里的内联脚本（head 的 __STATIC__ 标记 → body 主脚本）。
// 必须用「直接 eval」在模块作用域执行，否则函数声明不会外泄。
const scripts = [...fs.readFileSync(HTML, 'utf8').matchAll(/<script>([\s\S]*?)<\/script>/g)]
  .map((m) => m[1].replace(/\nloadData\(\);\s*$/, '\n'));
eval(scripts.length > 1 ? scripts[0] : '');
eval(scripts[scripts.length - 1]);

// ---------------- 期望值（从数据现算）----------------
const hasMain = (rows) => (rows || []).some((e) => e.main_net !== null && e.main_net !== undefined);
const validDates = (RAW.dates || []).filter((d) => hasMain(RAW.days[d]));
const EXPECT_DATE = validDates[validDates.length - 1] || (RAW.dates || []).slice(-1)[0];
const rowsOf = (d) => RAW.days[d] || [];
const yi = (v) => (Math.round(v / 1e6) / 100).toFixed(2);
const EXPECT_TOTAL = yi(rowsOf(EXPECT_DATE).reduce((a, e) => a + (e.main_net || 0), 0));

let pass = 0, fail = 0;
const chk = (c, m) => { c ? (pass++, console.log('  \u2705 ' + m)) : (fail++, console.log('  \u274c ' + m)); };
const rowsCount = () => (els['q_#tbl tbody'].innerHTML.match(/<tr>/g) || []).length;

(async () => {
  await loadData();
  await new Promise((r) => setTimeout(r, 200));
  console.log('\n[静态站产物启动自检]');

  chk(!!global.window.__STATIC__, 'head 中 window.__STATIC__ 标记存在');
  chk(fetched[0] === 'data.json', `数据源为相对路径 data.json（实际 ${fetched[0]}）`);
  chk(els['fresh-data'].textContent.includes(EXPECT_DATE), `首屏落在最近有效日 ${EXPECT_DATE}`);
  // 「数据更新于」时间戳：取自 data.json 的 last_fetch（北京时间），精确到分钟
  const EXPECT_FETCH = String(RAW.last_fetch || '').slice(0, 16);
  chk(EXPECT_FETCH && els['fresh-time'].textContent === EXPECT_FETCH,
      `时间戳条显示数据更新于 ${EXPECT_FETCH}（实际 ${els['fresh-time'].textContent}）`);
  chk(!!els['fresh-rel'].textContent && els['fresh-rel'].textContent !== '更新时间未知',
      `时间戳条给出相对时间（实际 ${els['fresh-rel'].textContent}）`);
  chk(els['cards'].innerHTML.includes(EXPECT_TOTAL), `首屏净买入合计 = ${EXPECT_TOTAL} 亿`);
  chk(rowsCount() === rowsOf(EXPECT_DATE).length, `明细行数 = ${rowsOf(EXPECT_DATE).length}`);
  chk(replaced.length === 1 && replaced[0][1] === 'a', '刷新按钮已换成 Actions 链接');
  chk((replaced[0] || [])[2] && String(replaced[0][2]).includes('actions'), '链接指向 Actions 工作流');

  // 本地筛选：不发请求、结果按类别收敛
  const cat = (RAW.cats || [])[0];
  const before = fetched.length;
  toggleCat(cat.cat);
  await new Promise((r) => setTimeout(r, 150));
  const catRows = rowsOf(EXPECT_DATE).filter((e) => e.cat === cat.cat);
  chk(fetched.length === before, '本地筛选不再发起网络请求');
  chk(els['f-summary'].innerHTML.includes(cat.cat), `筛选摘要含类别 ${cat.cat}`);
  chk(rowsCount() === catRows.length, `按类别过滤后 ${catRows.length} 行`);

  // 静态模式下点刷新应跳 Actions，而不是请求后端
  refresh();
  await new Promise((r) => setTimeout(r, 50));
  chk(!!global.__opened, '点击刷新跳转到 Actions 页面');

  console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
  process.exit(fail === 0 ? 0 : 1);
})();
