(function () {
  "use strict";

  var bridge = window.AstrBotPluginPage || window.AstrBotPluginView;
  var state = { style: {}, timer: null, dirty: false };

  function $(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  var toastTimer = null;
  function toast(msg, bad) {
    var el = $("toast");
    el.textContent = msg;
    el.className = "toast show" + (bad ? " bad" : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.className = "toast"; }, 3200);
  }

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

  function hexOk(v) { return /^#[0-9a-fA-F]{6}$/.test(String(v || "")); }

  var BIND = [
    ["width", "style_width", "int"], ["font", "style_font_size", "int"],
    ["radius", "style_radius", "int"], ["bg", "style_bg_color", "color"],
    ["bubble", "style_bubble_color", "color"], ["text", "style_text_color", "color"],
    ["dim", "style_dim_color", "color"], ["timefmt", "style_time_format", "text"],
    ["dark", "style_dark", "bool"]
  ];

  function readForm() {
    var out = {};
    BIND.forEach(function (b) {
      var el = $(b[0]);
      if (!el) return;
      if (b[2] === "int") out[b[1]] = parseInt(el.value, 10);
      else if (b[2] === "bool") out[b[1]] = !!el.checked;
      else out[b[1]] = el.value;
    });
    var shape = document.querySelector('input[name="shape"]:checked');
    out.style_avatar_shape = shape ? shape.value : "circle";
    return out;
  }

  function writeForm(style) {
    BIND.forEach(function (b) {
      var el = $(b[0]);
      if (!el) return;
      if (b[2] === "bool") el.checked = !!style[b[1]];
      else el.value = style[b[1]];
    });
    var pair = [["bg", "style_bg_color"], ["bubble", "style_bubble_color"],
                ["text", "style_text_color"], ["dim", "style_dim_color"]];
    pair.forEach(function (p) {
      var t = $(p[0] + "-text");
      if (t) t.value = style[p[1]] || "";
    });
    var shape = style.style_avatar_shape || "circle";
    Array.prototype.forEach.call(document.querySelectorAll('input[name="shape"]'), function (r) {
      r.checked = (r.value === shape);
    });
    syncLabels();
  }

  function syncLabels() {
    $("width-val").textContent = $("width").value;
    $("font-val").textContent = $("font").value;
    $("radius-val").textContent = $("radius").value;
  }

  function schedulePreview() {
    state.dirty = true;
    clearTimeout(state.timer);
    state.timer = setTimeout(refreshPreview, 180);
  }

  async function refreshPreview() {
    var values = readForm();
    $("preview-meta").textContent = "渲染中…";
    try {
      var d = unwrap(await post("style_preview", { values: values }));
      $("preview").src = d.data_url;
      $("preview-meta").textContent = Math.round((d.bytes || 0) / 1024) + " KB";
      $("status").textContent = "";
    } catch (e) {
      $("preview-meta").textContent = "预览失败";
      $("status").textContent = e.message;
    }
  }

  async function loadStyle() {
    try {
      var d = unwrap(await api("style"));
      state.style = (d && d.style) || {};
      writeForm(state.style);
      refreshPreview();
    } catch (e) {
      $("status").textContent = "读取样式失败：" + e.message;
    }
  }

  async function save() {
    var values = readForm();
    try {
      var d = unwrap(await post("style_save", { values: values }));
      state.style = (d && d.style) || values;
      toast("已保存（新消息生效）");
      refreshPreview();
    } catch (e) {
      toast("保存失败：" + e.message, true);
    }
  }

  function bind() {
    BIND.forEach(function (b) {
      var el = $(b[0]);
      if (!el) return;
      el.addEventListener("input", function () {
        syncLabels();
        if (b[2] === "color") {
          var t = $(b[0] + "-text");
          if (t) t.value = el.value;
        }
        schedulePreview();
      });
    });
    [["bg", "bg-text"], ["bubble", "bubble-text"], ["text", "text-text"],
     ["dim", "dim-text"]].forEach(function (p) {
      var picker = $(p[0]), text = $(p[1]);
      if (!picker || !text) return;
      picker.addEventListener("input", function () { text.value = picker.value; });
      text.addEventListener("input", function () {
        if (!hexOk(text.value)) return;
        picker.value = text.value;
        schedulePreview();
      });
    });
    Array.prototype.forEach.call(document.querySelectorAll('input[name="shape"]'), function (r) {
      r.addEventListener("change", schedulePreview);
    });
    $("save").addEventListener("click", save);
    $("reset").addEventListener("click", function () {
      writeForm(state.style);
      refreshPreview();
    });
  }

  function boot() {
    bind();
    waitForBridge(5000, function (err) {
      if (err) {
        $("status").textContent = "页面桥没有加载（宿主没注入 bridge-sdk），刷新一下试试。";
        return;
      }
      if (typeof bridge.ready === "function") {
        try { bridge.ready(); } catch (e) { /* 老桥没有 ready */ }
      }
      loadStyle();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
