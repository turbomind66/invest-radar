/**
 * QDII 全球市场信号表渲染测试（离线，无需浏览器）。
 *
 * 背景（2026-10-30 线上事故）：模板里调用了 esc() 但从未定义，
 * 渲染第一个数据行就抛 ReferenceError —— 分组头（美股/亚太…）画得出、
 * 数据行全不渲染，页面表现为「信号表格只剩分组标题、下面全空」。
 * 本测试用轻量 DOM 桩真实执行 renderSignal，确保分组头 + 数据行都能出来，
 * 且状态列（market_status）正常渲染。
 *
 * 运行：node qdii/tests/test_signal_render.js
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const HTML = path.join(__dirname, '..', 'templates', 'index.html');

function extractScript(html) {
  // 取最后一个内联 <script> 块（页面主逻辑），并剥掉末尾自动入口调用
  const m = html.match(/<script>([\s\S]*?)<\/script>\s*<\/body>/);
  if (!m) throw new Error('未能在 qdii/templates/index.html 中定位内联脚本');
  return m[1].replace(/if\(STATIC\)\{ loadStatic\(\); \} else \{ loadAll\(\); \}\s*$/, '\n');
}

// ---------------- 轻量 DOM 桩 ----------------
function mkEl(id) {
  const el = {
    id, children: [], textContent: '', value: '', disabled: false,
    style: {}, className: '', href: '',
    classList: {
      _s: new Set(),
      add(...c) { c.forEach((x) => this._s.add(x)); },
      remove(...c) { c.forEach((x) => this._s.delete(x)); },
      contains(x) { return this._s.has(x); },
    },
    appendChild(c) { this.children.push(c); return c; },
    getAttribute() { return null; },
    _qs: {},
    // 同一个元素对同一选择器必须返回同一个子元素（否则渲染写进 A、断言读到 B）
    querySelector(sel) { return this._qs[sel] || (this._qs[sel] = mkEl(id + ' ' + sel)); },
  };
  // innerHTML 清空时同步清空子节点（与真实 DOM 行为一致），
  // 否则多次 renderSignal 之间子节点会残留、污染后续断言。
  let _html = '';
  Object.defineProperty(el, 'innerHTML', {
    get() { return _html; },
    set(v) { _html = v; if (v === '') el.children.length = 0; },
  });
  return el;
}
const els = {};
global.document = {
  getElementById: (id) => els[id] || (els[id] = mkEl(id)),
  createElement: () => mkEl('dyn'),
  querySelector: () => mkEl('q'),
  querySelectorAll: () => [],
  addEventListener() {},
};
global.window = { addEventListener() {} };
global.location = { protocol: 'http:', port: '5001', hostname: '127.0.0.1', pathname: '/' };
global.echarts = { init: () => ({ setOption() {}, resize() {}, clear() {} }) };
global.fetch = async () => { throw new Error('测试不应发起网络请求'); };

// ---------------- 加载页面脚本 ----------------
// 模板脚本顶层是 function 声明与 const，eval 后无法从外部直接访问，
// 因此把「页面脚本 + 调用代码」拼成同一段执行，返回值即断言输入。
const src = extractScript(fs.readFileSync(HTML, 'utf8'));
function runInPage(code) {
  return new Function(src + '\n' + code)();
}

// ---------------- 测试用例 ----------------
let passed = 0;
function t(name, fn) {
  try { fn(); passed++; console.log('  ok -', name); }
  catch (e) { console.error('  FAIL -', name, '\n    ', e && e.message); process.exitCode = 1; }
}

const SAMPLE_ROWS = [
  { symbol: 'IXIC', name: '纳斯达克综合指数', last_price: 22861.03, change_pct: 0.61,
    change_value: 138.72, change_bp: null, is_realtime: 0, market_status: '已收盘 04:00 · 21:30 开盘' },
  { symbol: 'NK', name: '日经225指数', last_price: 51302.37, change_pct: -0.58,
    change_value: -298.32, change_bp: null, is_realtime: 0, market_status: '已收盘 14:30' },
  { symbol: 'DAX', name: '德国DAX指数', last_price: 24123.45, change_pct: 0.32,
    change_value: 77.10, change_bp: null, is_realtime: 1, market_status: '实时' },
  { symbol: 'UST10Y', name: '美债10Y收益率', last_price: 4.086, change_pct: null,
    change_value: 3.0, change_bp: 3.0, is_realtime: 1, market_status: '实时' },
  { symbol: 'VIX', name: 'VIX波动率', last_price: 16.32, change_pct: -1.2,
    change_value: -0.20, change_bp: null, is_realtime: 0, market_status: '已收盘 04:00 · 21:30 开盘' },
];
const SAMPLE_META = {
  IXIC: { group: '美股', kind: '现货 · 指数' },
  NK: { group: '亚太', kind: '现货 · 指数' },
  DAX: { group: '欧洲', kind: '现货 · 指数' },
  UST10Y: { group: '利率', kind: '收益率' },
  VIX: { group: '美股', kind: '波动率' },
};
const SAMPLE_GROUPS = ['美股', '亚太', '欧洲', '利率'];

// 便捷执行：先跑 renderSignal，再把 tbody 序列化为可断言的结果返回
function renderAnd(rows, meta, groups, pick) {
  return runInPage(
    'renderSignal(' + JSON.stringify(rows) + ',' + JSON.stringify(meta || {}) + ',' +
    JSON.stringify(groups || []) + ');\nreturn ' + pick
  );
}
const PICK_COUNT = 'document.getElementById("signalTable").querySelector("tbody").children.length';
const PICK_HTML = 'document.getElementById("signalTable").querySelector("tbody").children.map(c=>c.innerHTML).join("|")';

t('renderSignal 渲染分组头 + 全部数据行（esc 未定义会在这里抛错）', () => {
  const n = renderAnd(SAMPLE_ROWS, SAMPLE_META, SAMPLE_GROUPS, PICK_COUNT);
  // 4 个分组都有数据 → 4 个分组头 + 5 个数据行
  assert.strictEqual(n, 4 + SAMPLE_ROWS.length, 'tbody 行数 = 分组头数 + 数据行数');
});

t('状态列按 market_status 渲染（实时/已收盘文案都出现）', () => {
  const html = renderAnd(SAMPLE_ROWS, SAMPLE_META, SAMPLE_GROUPS, PICK_HTML);
  assert.ok(html.indexOf('已收盘 14:30') >= 0, '包含日经「已收盘 14:30」');
  assert.ok(html.indexOf('已收盘 04:00 · 21:30 开盘') >= 0, '包含美股现货状态');
  assert.ok(html.indexOf('>实时<') >= 0, '包含「实时」标签');
  assert.ok(html.indexOf('grpRow') >= 0, '包含分组头样式');
});

t('esc() 已定义且正确转义', () => {
  const out = runInPage('return esc(\'<b title="x">&\\\'</b>\')');
  assert.strictEqual(out, '&lt;b title=&quot;x&quot;&gt;&amp;&#39;&lt;/b&gt;');
});

t('空数据时表格清空且不抛错', () => {
  const n = renderAnd([], {}, [], PICK_COUNT);
  assert.strictEqual(n, 0);
});

t('market_status 缺失时回退 is_realtime 兜底', () => {
  const rows = [{ symbol: 'NK', name: '日经225指数', last_price: 51302.37, change_pct: -0.58,
                  change_value: -298.32, change_bp: null, is_realtime: 0 }];
  const meta = { NK: { group: '亚太', kind: '现货 · 指数' } };
  const html = renderAnd(rows, meta, ['亚太'], PICK_HTML);
  assert.ok(html.indexOf('已收盘') >= 0, 'is_realtime=0 兜底显示「已收盘」');
});

console.log(passed + ' passed');
if (process.exitCode) process.exit(process.exitCode);
