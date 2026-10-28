(function () {
  "use strict";

  var PLUGIN = "astrbot-plugin-media-warden";
  var bridge = window.AstrBotPluginPage || window.AstrBotPluginView;
  var state = { items: [], q: "", group: "", kind: "", selected: null, total: 0,
                detail: null, mode: "struct", groups: [] };

  var KIND_LABELS = {
    all: "全部", image: "图片", gif: "动图", video: "视频", file: "文档",
    voice: "语音", text: "纯文本", forward: "聊天记录"
  };
  var KIND_ORDER = ["all", "image", "gif", "video", "file", "text", "forward"];

  function $(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function pad(n) { return (n < 10 ? "0" : "") + n; }

  function fmtTs(ts) {
    if (!ts) return "—";
    var d = new Date(ts * 1000);
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
      " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  function fmtBytes(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
    return (n / 1048576).toFixed(1) + " MB";
  }

  var toastTimer = null;
  function toast(msg, bad) {
    var el = $("toast");
    el.textContent = msg;
    el.className = "toast show" + (bad ? " bad" : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.className = "toast"; }, 3200);
  }

  // 宿主的 bridge-sdk 是注入在 </body> 前的（排在 app.js 之后），所以这里必须
  // 等它出现，不能假设脚本一执行桥就在。
  function waitForBridge(timeoutMs, cb) {
    var started = Date.now();
    (function tick() {
      bridge = bridge || window.AstrBotPluginPage || window.AstrBotPluginView;
      if (bridge && bridge.apiGet) return cb(null);
      if (Date.now() - started > timeoutMs) return cb(new Error("页面桥未加载"));
      setTimeout(tick, 40);
    })();
  }

  function api(path, params) {
    if (!bridge || !bridge.apiGet) return Promise.reject(new Error("页面桥未就绪"));
    return bridge.apiGet("page/" + path, params || {});
  }

  function post(path, body) {
    if (!bridge || !bridge.apiPost) return Promise.reject(new Error("页面桥未就绪"));
    return bridge.apiPost("page/" + path, body || {});
  }

  function unwrap(resp) {
    if (!resp || typeof resp !== "object") return null;
    if (resp.status === "error") throw new Error(resp.message || "请求失败");
    return resp.data === undefined ? resp : resp.data;
  }

  function itemKey(it) {
    return it.source === "flat" ? ("flat:" + it.rel) : ("entry:" + it.record_id);
  }

  function thumbParams(it, rel) {
    if (it.source === "flat") return { source: "flat", path: it.rel };
    return { id: it.record_id, path: rel };
  }

  function fillThumb(img, params) {
    api("thumb", params).then(function (payload) {
      var d = unwrap(payload);
      if (d && d.data_url) { img.src = d.data_url; }
      else { img.outerHTML = '<div class="thumb broken">图片加载失败</div>'; }
    }).catch(function () {
      img.outerHTML = '<div class="thumb broken">图片加载失败</div>';
    });
  }

  function hydrateThumbs(item, root) {
    var scope = root || document;
    var imgs = scope.querySelectorAll(".thumb[data-asset]");
    Array.prototype.forEach.call(imgs, function (img) {
      var rel = img.getAttribute("data-asset");
      img.removeAttribute("data-asset");
      fillThumb(img, thumbParams(item, rel));
    });
  }

  function renderStats(s) {
    if (!s) { $("stats").textContent = ""; return; }
    state.stats = s;
    var parts = [];
    if (s.records) parts.push(["聊天记录", s.records]);
    if (s.texts) parts.push(["纯文本", s.texts]);
    parts.push(["存档图片", s.assets]);
    parts.push(["存档占用", fmtBytes(s.bytes)]);
    // 扁平文件的行数与体积来自索引库的 COUNT/SUM（以前数的是「取了一页」的行，
    // 于是永远显示 2000；占用也只算存档内的图片字节，和磁盘实际差一个量级）。
    parts.push(["扁平文件", s.flat_total || 0]);
    if (s.flat_bytes) parts.push(["扁平占用", fmtBytes(s.flat_bytes)]);
    if (s.missing) parts.push(["缺失", s.missing]);
    $("stats").innerHTML = parts.map(function (p) {
      return "<span>" + esc(p[0]) + " <b>" + esc(p[1]) + "</b></span>";
    }).join("");
  }

  function renderKinds() {
    var bar = $("kinds");
    bar.innerHTML = KIND_ORDER.map(function (k) {
      return '<button class="tab' + (state.kind === k ? " active" : "") +
        '" data-kind="' + esc(k) + '">' + esc(KIND_LABELS[k] || k) + "</button>";
    }).join("");
    Array.prototype.forEach.call(bar.querySelectorAll(".tab"), function (btn) {
      btn.addEventListener("click", function () {
        state.kind = btn.getAttribute("data-kind") || "";
        if (state.kind === "all") state.kind = "";
        renderKinds();
        loadList();  // 换类型 = 从头取,state.items 会被 renderList 覆盖
      });
    });
  }

  function groupLabel(g) {
    return g.name ? (g.name + " (" + g.group_id + ")") : g.group_id;
  }

  function renderGroups() {
    var sel = $("group");
    var cur = sel.value;
    var html = '<option value="">全部群</option>';
    state.groups.forEach(function (g) {
      html += '<option value="' + esc(g.group_id) + '">' + esc(groupLabel(g)) + "</option>";
    });
    sel.innerHTML = html;
    sel.value = cur || "";
  }

  async function loadGroups() {
    try {
      var d = unwrap(await api("groups"));
      state.groups = (d && d.groups) || [];
    } catch (e) {
      // 机器人没连上时退回 stats 里的群号（没有群名，但至少能过滤）
      state.groups = Object.keys((state.stats && state.stats.groups) || {})
        .map(function (gid) { return { group_id: gid, name: "" }; });
    }
    renderGroups();
  }

  function kindPill(it) {
    var label = KIND_LABELS[it.kind] || it.kind || "未知";
    return '<span class="pill kind-' + esc(it.kind || "unknown") + '">' + esc(label) + "</span>";
  }

  function renderList(data) {
    state.items = (data && data.items) || [];
    state.total = (data && data.total) || 0;
    var list = $("list");
    $("list-head").textContent = state.total + " 条" +
      (state.items.length < state.total ? "（显示前 " + state.items.length + " 条）" : "");
    if (!state.items.length) {
      list.innerHTML = '<p class="hint">没有匹配的保存物。群里发图/文档会自动保存；' +
        '合并转发与纯文本需要先开启对应开关。</p>';
      return;
    }
    list.innerHTML = state.items.map(function (it) {
      var badges = [kindPill(it)];
      if (it.source === "flat") {
        badges.push('<span class="pill">文件</span>');
        badges.push('<span class="pill">' + esc(fmtBytes(it.bytes)) + "</span>");
      } else {
        badges.push('<span class="pill">' + esc(it.node_count) + " 节点</span>");
        if (it.asset_count) badges.push('<span class="pill">' + esc(it.asset_count) + " 图</span>");
        if (it.has_render) badges.push('<span class="pill ok">有渲染图</span>');
        if (it.kind === "forward" && !it.can_native) badges.push('<span class="pill warn">无原消息</span>');
        if (it.missing_count) badges.push('<span class="pill warn">缺 ' + esc(it.missing_count) + " 图</span>");
      }
      return '<div class="item' + (itemKey(it) === state.selected ? " active" : "") +
        '" data-key="' + esc(itemKey(it)) + '">' +
        '<div class="row1"><span class="time">' + esc(fmtTs(it.captured_ts)) + "</span>" +
        '<span class="group">群 ' + esc(it.group_id || "-") + "</span></div>" +
        '<div class="preview">' + esc(it.preview || "（无文本）") + "</div>" +
        '<div class="meta">' + badges.join("") + "</div></div>";
    }).join("");
    // 一页 200 条,总数远不止时给「加载更多」(以前只写「显示前 N 条」,老文件根本
    // 翻不到 —— 分类管理要看的是全部,不是最新一页)。
    if (state.items.length < state.total) {
      var rest = state.total - state.items.length;
      list.innerHTML += '<div class="more-wrap"><button class="btn" id="btn-more">' +
        "加载更多（还有 " + rest + " 条）</button></div>";
      $("btn-more").addEventListener("click", function () {
        this.disabled = true;
        this.textContent = "加载中…";
        loadList(state.items.length);
      });
    }
    Array.prototype.forEach.call(list.querySelectorAll(".item"), function (el) {
      el.addEventListener("click", function () {
        var key = el.getAttribute("data-key");
        var it = state.items.filter(function (x) { return itemKey(x) === key; })[0];
        if (!it) return;
        Array.prototype.forEach.call(list.querySelectorAll(".item"), function (e2) {
          e2.classList.toggle("active", e2 === el);
        });
        state.selected = key;
        if (it.source === "flat") renderFlatDetail(it);
        else openDetail(it.record_id);
      });
    });
  }

  function renderSegment(seg) {
    if (seg.type === "image") {
      if (seg.missing) {
        return '<p class="seg-dim">[图片缺失' + (seg.reason ? ": " + esc(seg.reason) : "") + "]</p>";
      }
      return '<div class="thumbs"><img class="thumb" data-asset="' + esc(seg.asset) +
        '" alt="加载中…" title="点击查看原图"></div>';
    }
    if (seg.type === "text") {
      return seg.text ? '<p class="seg-text">' + esc(seg.text) + "</p>" : "";
    }
    var label = seg.type;
    if (seg.raw && typeof seg.raw === "object") {
      var parts = [];
      Object.keys(seg.raw).forEach(function (k) {
        if (seg.raw[k] !== null && seg.raw[k] !== undefined && seg.raw[k] !== "") {
          parts.push(k + "=" + seg.raw[k]);
        }
      });
      if (parts.length) label = seg.type + " " + parts.join(" ");
    } else if (typeof seg.raw === "string" && seg.raw) {
      label = seg.raw;
    }
    if (seg.type === "face" && seg.id != null) label = "表情 " + seg.id;
    if (seg.type === "at" && seg.qq) label = "@" + seg.qq;
    return '<p class="seg-dim">[' + esc(label) + "]</p>";
  }

  function detailTabs(d) {
    if (!d.has_render && !d.render) return "";
    var struct = state.mode !== "render";
    return '<div class="detail-tabs">' +
      '<button class="tab' + (struct ? " active" : "") + '" data-mode="struct">结构化</button>' +
      '<button class="tab' + (struct ? "" : " active") + '" data-mode="render">渲染图</button>' +
      "</div>";
  }

  function renderDetail(d) {
    state.detail = d;
    var rid = d.record_id;
    var isForward = (d.kind || "forward") === "forward";
    var renderName = d.render || "render.png";
    var head = '<div class="detail-head">' +
      '<span class="id">' + esc(rid) + "</span>" +
      kindPill(d) +
      '<span class="pill">群 ' + esc(d.group_id || "-") + "</span>" +
      (d.sender_name ? '<span class="pill">' + esc(d.sender_name) + "</span>" : "") +
      (isForward ? '<span class="pill">' + esc(d.node_count) + " 节点</span>" : "") +
      (isForward && d.root_msg_id ? '<span class="pill">原消息 ' + esc(d.root_msg_id) + "</span>" : "") +
      "</div>";

    var actions = '<div class="actions">';
    if (isForward) {
      // 目标群下拉:默认这条存档原来的群,可以选机器人所在的其它群(带群名)
      var opts = "";
      var seen = {};
      var list = state.groups.slice();
      if (d.group_id && !list.some(function (g) { return g.group_id === d.group_id; })) {
        list.unshift({ group_id: d.group_id, name: "" });
      }
      list.forEach(function (g) {
        if (seen[g.group_id]) return;
        seen[g.group_id] = true;
        opts += '<option value="' + esc(g.group_id) + '"' +
          (g.group_id === d.group_id ? " selected" : "") + ">" +
          esc(groupLabel(g)) + "</option>";
      });
      actions += '<select id="target-group" class="target">' + opts + "</select>" +
        '<button class="btn primary" id="btn-native"' + (d.can_native ? "" : " disabled") +
        '>原地发回（保装扮）</button>' +
        '<button class="btn" id="btn-rebuild">重建转发（丢装扮）</button>';
    }
    actions += '<button class="btn" id="btn-open">打开目录</button>' +
      '<button class="btn danger" id="btn-delete">删除</button></div>';

    var body;
    if (state.mode === "render" && (d.has_render || d.render)) {
      body = '<div class="render-wrap"><img class="render-img" id="render-img" ' +
        'data-asset="' + esc(renderName) + '" alt="渲染图加载中…"></div>';
    } else {
      body = (d.nodes || []).map(function (n) {
        var segs = (n.segments || []).map(renderSegment).join("");
        // uin 不可信时不显示：NapCat 有时给整条记录同一个 user_id（转发创建者），
        // 照抄会看成「两个不同昵称共用一个 QQ 号」。
        return '<div class="node"><div class="node-head">' +
          '<span class="name">' + esc(n.name || n.uin || "?") + "</span>" +
          (n.uin && d.uin_reliable !== false ? "<span>(" + esc(n.uin) + ")</span>" : "") +
          (n.time ? "<span>" + esc(fmtTs(n.time)) + "</span>" : "") +
          "</div>" + (segs || '<p class="seg-dim">（空节点）</p>') + "</div>";
      }).join("");
      if (isForward && !segsHasImages(d)) {
        body += '<p class="hint">这条聊天记录没有内嵌图片（或图片当时没取到）。</p>';
      }
    }
    $("detail").innerHTML = head + detailTabs(d) +
      '<div class="detail-body">' + actions + body + "</div>";

    Array.prototype.forEach.call(document.querySelectorAll(".detail-tabs .tab"), function (b) {
      b.addEventListener("click", function () {
        state.mode = b.getAttribute("data-mode");
        renderDetail(d);
        hydrateThumbs({ source: "entry", record_id: rid }, $("detail"));
      });
    });
    var bn = $("btn-native"), br = $("btn-rebuild");
    function targetGroup() {
      var sel = $("target-group");
      return sel ? sel.value : "";
    }
    if (bn) bn.addEventListener("click", function () { doResend(rid, "native", targetGroup()); });
    if (br) br.addEventListener("click", function () { doResend(rid, "rebuild", targetGroup()); });
    $("btn-open").addEventListener("click", function () { doReveal({ id: rid }); });
    $("btn-delete").addEventListener("click", function () {
      doDelete({ id: rid }, "删除这个条目？（结构化数据 + 渲染图 + 图片资产一起删，不可恢复）");
    });
    hydrateThumbs({ source: "entry", record_id: rid }, $("detail"));
  }

  function segsHasImages(d) {
    return ((d && d.nodes) || []).some(function (n) {
      return ((n.segments) || []).some(function (s) { return s.type === "image"; });
    });
  }

  function renderFlatDetail(it) {
    state.detail = it;
    var head = '<div class="detail-head">' + kindPill(it) +
      '<span class="pill">群 ' + esc(it.group_id || "-") + "</span>" +
      '<span class="pill">' + esc(fmtTs(it.captured_ts)) + "</span>" +
      '<span class="pill">' + esc(fmtBytes(it.bytes)) + "</span></div>";
    var actions = '<div class="actions">' +
      '<button class="btn" id="btn-open">打开目录</button>' +
      '<button class="btn danger" id="btn-flat-delete">删除文件</button></div>';
    var body = "";
    if (it.kind === "image" || it.kind === "gif") {
      body = '<div class="render-wrap"><img class="thumb render-img" id="flat-img" ' +
        'alt="加载中…" title="点击查看原图"></div>';
    }
    body += '<p class="seg-dim">文件: ' + esc(it.rel || it.path || "") + "</p>" +
      '<p class="seg-dim">落盘时间: ' + esc(fmtTs(it.captured_ts)) +
      " · 消息 id: " + esc(it.root_msg_id || "-") + "</p>";
    $("detail").innerHTML = head + '<div class="detail-body">' + actions + body + "</div>";
    $("btn-open").addEventListener("click", function () {
      doReveal({ path: it.rel || it.path });
    });
    $("btn-flat-delete").addEventListener("click", function () {
      doDelete({ source: "flat", path: it.path },
               "删除这个文件？（磁盘上的文件与索引行一起删，不可恢复）");
    });
    var img = $("flat-img");
    if (img) fillThumb(img, thumbParams(it, ""));
  }

  async function loadStats() {
    try {
      var s = unwrap(await api("stats"));
      state.stats = s;
      renderStats(s);
    } catch (e) { console.warn(e); }
  }

  async function loadList(offset) {
    var params = { limit: 200 };
    if (state.q) params.q = state.q;
    if (state.group) params.group = state.group;
    if (state.kind) params.kind = state.kind;
    if (offset) params.offset = offset;
    try {
      var data = unwrap(await api("records", params));
      if (offset) {
        // 追加下一页:服务端的 total 是真实总数,前端只负责把页拼起来
        state.items = state.items.concat(data.items || []);
        renderList({ total: data.total, items: state.items, kinds: data.kinds, labels: data.labels });
      } else {
        renderList(data);
      }
    } catch (e) {
      $("list").innerHTML = '<p class="hint">加载失败：' + esc(e.message) + "</p>";
    }
  }

  async function openDetail(rid) {
    try {
      var d = unwrap(await api("record", { id: rid }));
      state.mode = "struct";
      renderDetail(d);
    } catch (e) {
      $("detail").innerHTML = '<p class="hint">打开失败：' + esc(e.message) + "</p>";
    }
  }

  async function doResend(rid, mode, groupId) {
    var btns = document.querySelectorAll(".actions .btn");
    Array.prototype.forEach.call(btns, function (b) { b.disabled = true; });
    try {
      var body = { id: rid, mode: mode, fallback: mode === "native" };
      if (groupId) body.group_id = groupId;
      var r = unwrap(await post("resend", body));
      if (r && r.ok) {
        toast((r.mode === "native" ? "已原地转发" : "已重建发送") + (r.note ? "（" + r.note + "）" : ""));
      } else {
        toast("发送失败：" + ((r && r.error) || "未知原因"), true);
      }
    } catch (e) {
      toast("发送失败：" + e.message, true);
    } finally {
      Array.prototype.forEach.call(btns, function (b) { b.disabled = false; });
    }
  }

  async function doReveal(body) {
    try {
      var r = unwrap(await post("reveal", body));
      if (r && r.opened) toast("已在资源管理器打开");
      else toast((r && r.note) || "已定位目录: " + ((r && r.path) || ""));
    } catch (e) {
      toast("打开失败：" + e.message, true);
    }
  }

  async function doDelete(body, question) {
    if (!window.confirm(question)) return;
    try {
      unwrap(await post("delete", body));
      toast("已删除");
      state.selected = null;
      $("detail").innerHTML = '<p class="hint">左侧选择一条查看内容</p>';
      await Promise.all([loadStats(), loadList()]);
    } catch (e) {
      toast("删除失败：" + e.message, true);
    }
  }

  // 预览大图用页面内蒙层，不用 window.open：桌面版（Tauri）里 window.open
  // 是空操作，点了等于没反应。
  function ensureLightbox() {
    var box = document.getElementById("lightbox");
    if (!box) {
      box = document.createElement("div");
      box.id = "lightbox";
      box.className = "lightbox";
      box.innerHTML = '<img alt="预览">';
      box.addEventListener("click", function () { box.classList.remove("show"); });
      document.body.appendChild(box);
    }
    return box;
  }

  function openLightbox(src) {
    var box = ensureLightbox();
    var img = box.querySelector("img");
    if (img) img.src = src;
    box.classList.add("show");
  }

  function bind() {
    var qTimer = null;
    $("q").addEventListener("input", function (e) {
      state.q = e.target.value.trim();
      clearTimeout(qTimer);
      qTimer = setTimeout(loadList, 220);
    });
    $("group").addEventListener("change", function (e) {
      state.group = e.target.value;
      loadList();
    });
    $("refresh").addEventListener("click", function () {
      loadStats();
      loadList();
    });
    document.addEventListener("click", function (e) {
      var img = e.target;
      if (img && img.classList && img.classList.contains("thumb") && img.src) {
        openLightbox(img.src);
      }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" || e.key === "Esc") {
        var box = document.getElementById("lightbox");
        if (box) box.classList.remove("show");
      }
    });
  }

  function boot() {
    bind();
    renderKinds();
    waitForBridge(5000, function (err) {
      if (err) {
        $("list").innerHTML =
          '<p class="hint">页面桥没有加载（宿主没注入 bridge-sdk），刷新一下试试。</p>';
        return;
      }
      if (typeof bridge.ready === "function") {
        try { bridge.ready(); } catch (e) { /* 老版本桥没有 ready */ }
      }
      loadStats();
      loadGroups();
      loadList();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
