/* common/static_refresh.js — 静态托管站共享刷新逻辑（国家队 / QDII 复用）
 *
 * 由 national_team/app.py 与 qdii 的 render_index / staticsite 在「构建或响应时」
 * 内联注入到页面 <head>，因此服务端模式与 GitHub Pages / WorkBuddy 静态模式都能用。
 *
 * 暴露 window.StaticRefresh，各页面在 STATIC 模式下调用：
 *   - refreshToday(opts)  刷新“当日”实时数据（有 Worker 走 Worker，否则用浏览器直连 fetcher）
 *   - fullUpdate()        全量更新（有 Worker 触发 GitHub Action；否则打开 Actions 页，兼容旧行为）
 *   - configure(cfg)      注入 {workerUrl, actionsUrl, module}
 *
 * 设计要点（扩容友好）：
 *   刷新/Worker/跨模块导航等公共逻辑只写一次；新增模块 = 复用本文件 + 提供
 *   todayFetcher（浏览器直连）与 onToday（合并数据）两个回调即可。
 */
(function () {
  var SR = {};
  var cfg = { workerUrl: "", actionsUrl: "", module: "nt" };

  SR.configure = function (c) {
    if (!c) return;
    for (var k in c) if (c.hasOwnProperty(k)) cfg[k] = c[k];
  };

  function wu() { return (cfg.workerUrl || "").replace(/\/+$/, ""); }

  // 全量更新：有 Worker 走 Worker（安全持有 GitHub Token，前端不暴露）；
  // 否则打开 Actions 页（旧行为，保持静态测试兼容）。
  SR.fullUpdate = function () {
    if (cfg.workerUrl) {
      return fetch(wu() + "/refresh", { method: "POST" })
        .then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (d) {
          alert("已触发全量更新，约 1–2 分钟后数据刷新完成，请稍后手动刷新页面。" +
                (d && d.detail ? d.detail : ""));
        })
        .catch(function (e) { alert("触发全量更新失败：" + e); });
    }
    if (cfg.actionsUrl) { window.open(cfg.actionsUrl, "_blank", "noopener"); return; }
    alert("未配置更新入口（请设置 STATIC_WORKER_URL，或在部署平台手动触发工作流）");
  };

  // 刷新当日实时数据。
  // opts: { todayFetcher: ()=>Promise<data>, onToday: (data)=>void, btn: element }
  SR.refreshToday = function (opts) {
    opts = opts || {};
    var btn = opts.btn;
    var old = btn ? btn.textContent : "";
    if (btn) { btn.disabled = true; btn.textContent = "抓取中…"; }
    function done() { if (btn) { btn.disabled = false; btn.textContent = old; } }
    function apply(data) { if (opts.onToday) opts.onToday(data); done(); }

    if (cfg.workerUrl) {
      return fetch(wu() + "/today?module=" + encodeURIComponent(cfg.module))
        .then(function (r) { return r.json(); })
        .then(apply)
        .catch(function (e) { alert("实时抓取失败：" + e); done(); });
    }
    if (opts.todayFetcher) {
      return opts.todayFetcher()
        .then(apply)
        .catch(function (e) {
          alert("实时抓取失败（上游不可达，可改用「全量更新」）：" + e);
          done();
        });
    }
    alert("未配置实时刷新入口（本模块实时抓取需配合 Worker，请设置 STATIC_WORKER_URL）");
    done();
  };

  window.StaticRefresh = SR;
})();
