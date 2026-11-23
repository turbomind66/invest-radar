/* common/static_refresh.js — 静态站共享刷新逻辑（汪汪队 / QDII / 回撤全景 复用）
 *
 * 由 national_team/app.py 与 qdii / drawdown 的 render_index / staticsite 在
 * 「构建或响应时」内联注入到页面 <head>，因此服务端模式与 GitHub Pages /
 * WorkBuddy 静态模式都能用。
 *
 * 暴露 window.StaticRefresh，各页面在 STATIC 模式下调用：
 *   - refreshToday(opts)  刷新“当日”实时数据（有 Worker 走 Worker，否则用浏览器直连 fetcher）
 *   - fullUpdate()        全量更新（有 Worker 触发 GitHub Action；否则打开 Actions 页，兼容旧行为）
 *   - probe()             探测「本地刷新服务」是否可用（python serve_docs.py），返回 Promise<bool>
 *   - localRefresh(page)  让本地服务真抓一遍并把 docs/ 重新导出（page: nt/dd/qdii/all）
 *   - configure(cfg)      注入 {workerUrl, actionsUrl, module, localBase}
 *
 * 为什么需要 probe/localRefresh：
 *   GitHub Pages 是只读快照，静态页只能引导去 Actions；但在自己电脑上用
 *   `python serve_docs.py` 起的静态站是有后端的——同源多出一个 /api/local/* 接口，
 *   点「更新数据」就能原地抓取，不必再去 Actions 手动 dispatch。
 *   Pages 上该接口 404 → probe 返回 false → 自动回退旧行为，两端共用同一份代码。
 *
 * 设计要点（扩容友好）：
 *   刷新/Worker/跨模块导航等公共逻辑只写一次；新增模块 = 复用本文件 + 提供
 *   todayFetcher（浏览器直连）与 onToday（合并数据）两个回调即可。
 */
(function () {
  var SR = {};
  var cfg = { workerUrl: "", actionsUrl: "", module: "nt", localBase: "" };

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

  /* ---------------- 本地刷新服务（python serve_docs.py） ----------------
   * 同源路径约定：localBase 是「页面目录 → 站点根」的相对前缀
   *   汪汪队（站点根 /）      → ""
   *   回撤全景 / QDII（子目录） → "../"
   * Pages 上没有这个接口，fetch 会 404/HTML → probe 返回 false，保持旧行为。
   */
  function base() {
    var b = cfg.localBase || "";
    return b ? b.replace(/\/+$/, "") + "/" : "";
  }
  function localUrl(path) { return base() + "api/local/" + String(path).replace(/^\/+/, ""); }

  SR.local = false;      // probe 成功后置 true
  SR._probing = null;    // 并发调用只发一次请求
  SR.probeTimeout = 1500;

  // 探测本地刷新服务：返回 Promise<bool>，永不 reject。
  SR.probe = function () {
    if (SR._probing) return SR._probing;
    var ctl = (typeof AbortController !== "undefined") ? new AbortController() : null;
    var timer = null;
    var opts = { cache: "no-store" };
    if (ctl) { opts.signal = ctl.signal; }
    var req = fetch(localUrl("status"), opts)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { SR.local = !!(d && d.ok); return SR.local; })
      .catch(function () { SR.local = false; return false; })
      .then(function (v) { if (timer) clearTimeout(timer); return v; });
    // file:// 打开静态页时 fetch 会立刻 reject，不必等超时；本地服务正常情况下 1ms 就回。
    if (ctl && typeof setTimeout === "function") {
      timer = setTimeout(function () { try { ctl.abort(); } catch (e) {} }, SR.probeTimeout);
    }
    SR._probing = req;
    return req;
  };

  // 让本地服务抓取并重新导出静态站，完成后自动重载页面。
  // opts: { btn, onMsg(text), onDone(ok), pollMs }
  SR.localRefresh = function (page, opts) {
    opts = opts || {};
    var btn = opts.btn;
    var old = btn ? btn.textContent : "";
    var page_ = page || cfg.module || "nt";
    var msg = opts.onMsg || function () {};
    var fail = opts.onFail || function (t) { try { alert(t); } catch (e) {} };
    var pollMs = opts.pollMs || 1500;
    var maxWait = 30 * 60 * 1000;         // 抓取最坏 20 分钟，30 分钟兜底
    var t0 = (typeof Date !== "undefined") ? Date.now() : 0;

    function busy(text) { if (btn) { btn.disabled = true; btn.textContent = text; } }
    function restore() { if (btn) { btn.disabled = false; btn.textContent = old; } }

    busy("更新中…");
    msg("正在抓取最新数据，通常 30–120 秒…");

    function tick() {
      return fetch(localUrl("status"), { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var j = (d && d.jobs && d.jobs[page_]) || {};
          var tail = (j.log && j.log.length) ? String(j.log[j.log.length - 1]) : "";
          if (j.state === "running") {
            if (tail) { busy("更新中…"); msg("抓取中：" + tail.slice(0, 40)); }
            if (t0 && Date.now() - t0 > maxWait) {
              restore(); msg("更新超时，请查看服务端日志");
              fail("更新超时：请查看运行 serve_docs.py 的窗口日志");
              return { ok: false };
            }
            return null;                  // 继续轮询
          }
          var ok = (j.state === "ok");
          if (ok) { msg("更新完成 · 正在重新载入…"); setTimeout(function () { location.reload(); }, 900); }
          else {
            var reason = j.error || tail || "未知错误";
            restore(); msg("更新失败：" + reason);
            fail("更新失败：" + reason);
          }
          if (opts.onDone) opts.onDone(ok, j);
          return { ok: ok, job: j };
        })
        .catch(function (e) { restore(); msg("更新状态查询失败：" + e); fail("更新状态查询失败：" + e); return { ok: false }; });
    }

    return fetch(localUrl("refresh?page=" + encodeURIComponent(page_)), { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || d.ok === false) {
          var why = (d && d.error) || "本地服务未响应";
          restore(); msg("启动更新失败：" + why);
          fail("启动更新失败：" + why);
          return { ok: false };
        }
        return new Promise(function (resolve) {
          (function loop() {
            tick().then(function (res) {
              if (res) resolve(res);
              else setTimeout(loop, pollMs);
            });
          })();
        });
      })
      .catch(function (e) { restore(); msg("启动更新失败：" + e); fail("启动更新失败：" + e); return { ok: false }; });
  };

  window.StaticRefresh = SR;
})();
