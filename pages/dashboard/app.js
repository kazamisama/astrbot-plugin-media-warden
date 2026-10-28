(function () {
  "use strict";

  var PLUGIN = "astrbot-plugin-media-warden";
  var bridge = window.AstrBotPluginPage || window.AstrBotPluginView;
  var state = { items: [], q: "", group: "", selected: null, total: 0 };

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

  function hydrateThumbs(rid) {
    var imgs = document.querySelectorAll(".thumb[data-asset]");
    Array.prototype.forEach.call(imgs, function (img) {
      var rel = img.getAttribute("data-asset");
      img.removeAttribute("data-asset");
      api("thumb", { id: rid, path: rel }).then(function (payload) {
        var d = unwrap(payload);
        if (d && d.data_url) {
          img.src = d.data_url;
        } else {
          img.outerHTML = '<div class="thumb broken">图片加载失败</div>';
        }
      }).catch(function () {
        img.outerHTML = '<div class="thumb broken">图片加载失败</div>';
      });
    });
  }

  function renderStats(s) {
    if (!s) { $("stats").textContent = ""; return; }
    var parts = [
      ["存档", s.records], ["节点", s.nodes], ["图片", s.assets],
      ["占用", fmtBytes(s.bytes)]
    ];
    if (s.missing) parts.push(["缺失", s.missing]);
    $("stats").innerHTML = parts.map(function (p) {
      return "<span>" + esc(p[0]) + " <b>" + esc(p[1]) + "</b></span>";
    }).join("");
  }

  function renderGroups(groups) {
    var sel = $("group");
    var cur = sel.value;
    var html = '<option value="">全部群</option>';
    (groups || []).forEach(function (g) {
      html += '<option value="' + esc(g) + '">' + esc(g) + "</option>";
    });
    sel.innerHTML = html;
    sel.value = cur || "";
  }

  function renderList(data) {
    state.items = (data && data.items) || [];
    state.total = (data && data.total) || 0;
    var list = $("list");
    $("list-head").textContent = state.total + " 条存档" +
      (state.items.length < state.total ? "（显示前 " + state.items.length + " 条）" : "");
    if (!state.items.length) {
      list.innerHTML = '<p class="hint">没有匹配的存档。在群里回复一条合并转发并发送 "warden 存档" 即可保存。</p>';
      return;
    }
    list.innerHTML = state.items.map(function (it) {
      var badges = [];
      badges.push('<span class="pill">' + it.node_count + " 节点</span>");
      if (it.asset_count) badges.push('<span class="pill">' + it.asset_count + " 图</span>");
      if (!it.can_native) badges.push('<span class="pill warn">无原消息</span>');
      if (it.missing_count) badges.push('<span class="pill warn">缺 ' + it.missing_count + " 图</span>");
      return '<div class="item' + (it.record_id === state.selected ? " active" : "") +
        '" data-id="' + esc(it.record_id) + '">' +
        '<div class="row1"><span class="time">' + esc(fmtTs(it.captured_ts)) + "</span>" +
        '<span class="group">群 ' + esc(it.group_id || "-") + "</span></div>" +
        '<div class="preview">' + esc(it.preview || "（无文本）") + "</div>" +
        '<div class="meta">' + badges.join("") + "</div></div>";
    }).join("");
    Array.prototype.forEach.call(list.querySelectorAll(".item"), function (el) {
      el.addEventListener("click", function () { openDetail(el.getAttribute("data-id")); });
    });
  }

  function renderSegment(seg, rid) {
    if (seg.type === "image") {
      if (seg.missing) {
        return '<p class="seg-dim">[图片缺失' + (seg.reason ? ": " + esc(seg.reason) : "") + "]</p>";
      }
      // src 由 hydrateThumbs() 通过带鉴权的桥接口取 base64 后填上
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

  function renderDetail(d) {
    var rid = d.record_id;
    var canNative = d.can_native;
    var head = '<div class="detail-head">' +
      '<span class="id">' + esc(rid) + "</span>" +
      '<span class="pill">群 ' + esc(d.group_id || "-") + "</span>" +
      '<span class="pill">' + d.node_count + " 节点</span>" +
      (d.root_msg_id ? '<span class="pill">原消息 ' + esc(d.root_msg_id) + "</span>" : '<span class="pill warn">无原消息 id</span>') +
      "</div>";
    var actions = '<div class="actions">' +
      '<button class="btn primary" id="btn-native"' + (canNative ? "" : " disabled") +
      '>发回原群（原地·保装扮）</button>' +
      '<button class="btn" id="btn-rebuild">发回群（重建·丢装扮）</button>' +
      '<button class="btn danger" id="btn-delete">删除存档</button>' +
      "</div>";
    var body = (d.nodes || []).map(function (n) {
      var segs = (n.segments || []).map(function (s) { return renderSegment(s, rid); }).join("");
      return '<div class="node"><div class="node-head">' +
        '<span class="name">' + esc(n.name || n.uin || "?") + "</span>" +
        (n.uin ? '<span>(' + esc(n.uin) + ")</span>" : "") +
        (n.time ? '<span>' + esc(fmtTs(n.time)) + "</span>" : "") +
        "</div>" + (segs || '<p class="seg-dim">（空节点）</p>') + "</div>";
    }).join("");
    $("detail").innerHTML = head + '<div class="detail-body">' + actions + body + "</div>";

    $("btn-native").addEventListener("click", function () { doResend(rid, "native"); });
    $("btn-rebuild").addEventListener("click", function () { doResend(rid, "rebuild"); });
    $("btn-delete").addEventListener("click", function () { doDelete(rid); });
  }

  async function loadStats() {
    try {
      var s = unwrap(await api("stats"));
      renderStats(s);
      if (s && s.groups) renderGroups(Object.keys(s.groups));
    } catch (e) { console.warn(e); }
  }

  async function loadList() {
    var params = { limit: 100 };
    if (state.q) params.q = state.q;
    if (state.group) params.group = state.group;
    try {
      var data = unwrap(await api("records", params));
      renderList(data);
    } catch (e) {
      $("list").innerHTML = '<p class="hint">加载失败：' + esc(e.message) + "</p>";
    }
  }

  async function openDetail(rid) {
    state.selected = rid;
    Array.prototype.forEach.call(document.querySelectorAll(".item"), function (el) {
      el.classList.toggle("active", el.getAttribute("data-id") === rid);
    });
    try {
      var d = unwrap(await api("record", { id: rid }));
      renderDetail(d);
      hydrateThumbs(rid);
    } catch (e) {
      $("detail").innerHTML = '<p class="hint">打开失败：' + esc(e.message) + "</p>";
    }
  }

  async function doResend(rid, mode) {
    var btns = document.querySelectorAll(".actions .btn");
    Array.prototype.forEach.call(btns, function (b) { b.disabled = true; });
    try {
      var r = unwrap(await post("resend", { id: rid, mode: mode, fallback: mode === "native" }));
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

  async function doDelete(rid) {
    if (!window.confirm("删除这条存档？图片和 record.json 会一起删掉，不可恢复。")) return;
    try {
      unwrap(await post("delete", { id: rid }));
      toast("已删除");
      state.selected = null;
      $("detail").innerHTML = '<p class="hint">左侧选择一条存档查看内容</p>';
      await Promise.all([loadStats(), loadList()]);
    } catch (e) {
      toast("删除失败：" + e.message, true);
    }
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
      if (img && img.classList && img.classList.contains("thumb")) {
        window.open(img.src, "_blank");
      }
    });
  }

  function boot() {
    bind();
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
      loadList();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
