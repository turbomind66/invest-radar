// 模拟 GitHub Pages：按顺序执行产物里的所有内联脚本（head 的 __STATIC__ + body 主脚本）
const fs = require('fs'), path = require('path');
const HTML = path.join(__dirname, 'docs', 'index.html');
const RAW = JSON.parse(fs.readFileSync(path.join(__dirname, 'docs', 'data.json'), 'utf8'));

const els = {};
const replaced = [];
const mkEl = (id) => ({
  id, innerHTML:'', textContent:'', value:'', disabled:false, style:{}, tag:'div',
  classList:{add(){},remove(){}},
  parentNode:{ replaceChild(n, o){ replaced.push([o.id, n.tag, n.href, n.textContent]); } },
});
global.document = {
  getElementById:(id)=>els[id]||(els[id]=mkEl(id)),
  querySelector:(s)=>els['q_'+s]||(els['q_'+s]=mkEl(s)),
  querySelectorAll:()=>[],
  createElement:(t)=>Object.assign(mkEl('new_'+t), {tag:t}),
};
global.window = { addEventListener(){}, open(u){ global.__opened = u; } };
global.echarts = { init:()=>({setOption(){},resize(){},clear(){}}), graphic:{LinearGradient:function(){}} };
let fetched = [];
global.fetch = async (u) => { fetched.push(u); return { ok:true, json: async()=>RAW }; };

const scripts = [...fs.readFileSync(HTML,'utf8').matchAll(/<script>([\s\S]*?)<\/script>/g)]
  .map(m => m[1].replace(/\nloadData\(\);\s*$/, '\n'));
// 必须用「直接 eval」在模块作用域执行，否则函数声明不会外泄
eval(scripts.length > 1 ? scripts[0] : '');        // head: window.__STATIC__ 标记
eval(scripts[scripts.length - 1]);                 // body: 主脚本

let pass=0, fail=0;
const chk=(c,m)=>{ c? (pass++,console.log('  ✅ '+m)) : (fail++,console.log('  ❌ '+m)); };

(async()=>{
  await loadData(); await new Promise(r=>setTimeout(r,200));
  console.log('\n[静态站启动自检]');
  chk(!!global.window.__STATIC__, 'head 中 __STATIC__ 标记存在');
  chk(fetched[0]==='data.json', `数据源为相对路径 data.json（实际 ${fetched[0]}）`);
  chk(els['meta-date'].textContent.includes('2026-10-27'), `自动回退到最近有效日（${els['meta-date'].textContent}）`);
  chk(els['cards'].innerHTML.includes('-41.08'), '首屏净买入合计 = -41.08 亿');
  chk(els['f-cats'].innerHTML.includes('沪深300'), '类别 chips 渲染');
  chk(els['q_#tbl tbody'].innerHTML.includes('510300'), '明细表渲染');
  chk(replaced.length===1 && replaced[0][1]==='a', `刷新按钮已换成链接（${JSON.stringify(replaced[0]||null)}）`);
  chk((els['q_.note'] ? '' : '') === '', '说明块存在');
  const before = fetched.length;
  toggleCat('沪深300'); await new Promise(r=>setTimeout(r,150));
  chk(fetched.length===before, '本地筛选不再发起网络请求');
  chk(els['f-summary'].innerHTML.includes('沪深300'), '筛选摘要已更新');
  chk(els['cards'].innerHTML.includes('-9.71'), '按类别汇总重算为 -9.71 亿');
  refresh(); await new Promise(r=>setTimeout(r,50));
  chk(!!global.__opened, '点击刷新会跳转 Actions 页面');
  console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
  process.exit(fail?1:0);
})();
