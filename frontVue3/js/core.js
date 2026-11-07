/* ============================================================
 * core.js —— 极简响应式 + 虚拟 DOM 引擎（零依赖，Vue3 风格 API）
 * 提供：h() / reactive() / mount() / computed 化的 render 调度
 * 设计要点：
 *   1. Proxy 深响应式，任何 set 都会 schedule 一次全局重渲染；
 *   2. 重渲染走 vdom diff，输入框值相同时不写 DOM，保住光标与焦点；
 *   3. 无网络/无构建环境下仍可离线运行，纯浏览器能力。
 * ============================================================ */
(function (global) {
  'use strict';

  /* ---------------- 虚拟节点 ---------------- */
  function flatten(list, out) {
    for (var i = 0; i < list.length; i++) {
      var c = list[i];
      if (c === null || c === undefined || c === false || c === true) continue;
      if (Array.isArray(c)) flatten(c, out);
      else out.push(typeof c === 'object' ? c : String(c));
    }
  }

  function h(tag, props) {
    var children = [];
    flatten(Array.prototype.slice.call(arguments, 2), children);
    props = props || {};
    return {
      tag: tag, props: props, children: children,
      key: props.key === undefined ? null : props.key
    };
  }

  var SVG_TAGS = { svg: 1, path: 1, circle: 1, line: 1, g: 1, rect: 1, text: 1, polyline: 1 };

  /* ---------------- 属性 ---------------- */
  function attrName(k) {
    if (k === 'className') return 'class';
    if (k === 'htmlFor') return 'for';
    return k;
  }

  function styleStr(obj) {
    var s = '';
    for (var k in obj) {
      if (obj[k] === null || obj[k] === undefined || obj[k] === '') continue;
      var name = k.replace(/[A-Z]/g, function (m) { return '-' + m.toLowerCase(); });
      s += name + ':' + obj[k] + ';';
    }
    return s;
  }

  function evName(k) { return k.slice(2).toLowerCase(); }

  function addListener(node, key, fn) {
    if (typeof fn !== 'function') return;
    var name = evName(key);
    node._ls = node._ls || {};
    node._ls[name] = node._ls[name] || [];
    node._ls[name].push(fn);
    node.addEventListener(name, fn);
  }

  function removeListener(node, key, fn) {
    if (typeof fn !== 'function') return;
    var name = evName(key);
    node.removeEventListener(name, fn);
    if (node._ls && node._ls[name]) {
      node._ls[name] = node._ls[name].filter(function (f) { return f !== fn; });
    }
  }

  function setProp(node, k, v, prev) {
    if (k === 'key' || k === 'ref') {
      if (k === 'ref' && typeof v === 'function') v(node);
      return;
    }
    if (k === 'class' || k === 'className') {
      var cv = v === null || v === undefined ? '' : String(v);
      if (node.className !== cv) node.className = cv;
      return;
    }
    if (k === 'style') {
      var sv = typeof v === 'string' ? v : styleStr(v || {});
      if (node._style !== sv) { node.style.cssText = sv; node._style = sv; }
      return;
    }
    if (k === 'html' || k === 'innerHTML') { node.innerHTML = v || ''; return; }
    if (k === 'value') {
      var val = v === null || v === undefined ? '' : String(v);
      if (node.value !== val) node.value = val;
      return;
    }
    if (k === 'checked' || k === 'disabled' || k === 'selected' || k === 'readOnly' || k === 'multiple') {
      node[k] = !!v;
      return;
    }
    if (k.charAt(0) === 'o' && k.charAt(1) === 'n' && /[A-Z]/.test(k.charAt(2))) {
      removeListener(node, k, prev);
      addListener(node, k, v);
      return;
    }
    if (v === false || v === null || v === undefined) node.removeAttribute(attrName(k));
    else node.setAttribute(attrName(k), v === true ? '' : v);
  }

  function applyProps(node, prev, next) {
    var k;
    for (k in prev) {
      if (!(k in next)) {
        if (k === 'class' || k === 'className') node.className = '';
        else if (k === 'style') { node.style.cssText = ''; node._style = ''; }
        else if (k === 'html' || k === 'innerHTML') node.innerHTML = '';
        else if (k === 'value') node.value = '';
        else if (k.charAt(0) === 'o' && /[A-Z]/.test(k.charAt(2))) removeListener(node, k, prev[k]);
        else node.removeAttribute(attrName(k));
      }
    }
    for (k in next) {
      if (k === 'key') continue;
      var v = next[k], p = prev[k];
      if (v === p) continue;                    // 相同值不写 DOM：输入时不丢焦点
      setProp(node, k, v, p);
    }
  }

  /* ---------------- 创建 / 打补丁 ---------------- */
  function createDom(v) {
    if (typeof v === 'string') return document.createTextNode(v);
    var d = SVG_TAGS[v.tag] ? document.createElementNS('http://www.w3.org/2000/svg', v.tag)
                            : document.createElement(v.tag);
    applyProps(d, {}, v.props || {});
    for (var i = 0; i < v.children.length; i++) d.appendChild(createDom(v.children[i]));
    return d;
  }

  function patchNode(node, next, prev) {
    if (typeof next === 'string') {
      if (node.nodeValue !== next) node.nodeValue = next;
      return;
    }
    applyProps(node, prev.props || {}, next.props || {});
    patchChildren(node, next.children, prev.children);
  }

  function patchChildren(parent, next, prev) {
    var common = Math.min(next.length, prev.length);
    for (var i = 0; i < common; i++) {
      var node = parent.childNodes[i];
      var nv = next[i], pv = prev[i];
      var same = (typeof nv === 'string' && typeof pv === 'string') ||
                 (typeof nv === 'object' && typeof pv === 'object' && nv.tag === pv.tag && nv.key === pv.key);
      if (!same) parent.replaceChild(createDom(nv), node);
      else patchNode(node, nv, pv);
    }
    /* 新增：从尾部追加 */
    for (var j = common; j < next.length; j++) parent.appendChild(createDom(next[j]));
    /* 删除：必须从尾部往前删，否则 childNodes 下标会错位、残留旧节点
       （曾导致切页后上一页的卡片残留在新页面上） */
    for (var k = prev.length - 1; k >= next.length; k--) {
      var extra = parent.childNodes[k];
      if (extra) parent.removeChild(extra);
    }
  }

  /* ---------------- 响应式 ---------------- */
  var proxyCache = new WeakMap();
  var proxySet = new WeakSet();          // 已经是 proxy 的直接返回，避免多层代理
  var pending = false;
  var ARRAY_MUTATORS = ['push', 'pop', 'shift', 'unshift', 'splice', 'sort', 'reverse'];

  function reactive(obj) {
    if (obj === null || typeof obj !== 'object') return obj;
    if (proxySet.has(obj)) return obj;
    if (proxyCache.has(obj)) return proxyCache.get(obj);
    var isArr = Array.isArray(obj);
    var proxy = new Proxy(obj, {
      get: function (t, k, r) {
        if (isArr && ARRAY_MUTATORS.indexOf(k) >= 0) {
          return function () {
            var args = Array.prototype.slice.call(arguments);
            // push/unshift/splice 写入的新元素也要包成响应式
            if (k === 'push' || k === 'unshift') {
              args = args.map(function (x) { return (x !== null && typeof x === 'object') ? reactive(x) : x; });
            } else if (k === 'splice' && args.length > 2) {
              args = args.slice(0, 2).concat(
                args.slice(2).map(function (x) { return (x !== null && typeof x === 'object') ? reactive(x) : x; }));
            }
            var res = Array.prototype[k].apply(t, args);
            schedule();
            return res;
          };
        }
        var v = t[k];
        if (v !== null && typeof v === 'object') return reactive(v);
        return v;
      },
      set: function (t, k, v) {
        var old = t[k];
        // 写入的对象也包成响应式：这样 push 进来的新对象后续改字段仍能触发重渲染
        t[k] = (v !== null && typeof v === 'object') ? reactive(v) : v;
        if (old !== v) schedule();
        return true;
      },
      deleteProperty: function (t, k) { delete t[k]; schedule(); return true; }
    });
    proxyCache.set(obj, proxy);
    proxySet.add(proxy);
    return proxy;
  }

  function schedule() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(function () {
      pending = false;
      try { renderNow(); } catch (e) { console.error('[render]', e); }
    });
  }

  /* ---------------- 挂载 ---------------- */
  var container = null, AppFn = null, lastTree = null;

  function mount(fn, target) {
    AppFn = fn;
    container = target || document.getElementById('root');
    renderNow();
  }

  function renderNow() {
    if (!AppFn) return;
    var next = AppFn();
    var child = container.firstChild;
    if (!lastTree || !child) {
      container.innerHTML = '';
      container.appendChild(createDom(next));
    } else if (typeof next === 'string' || typeof lastTree === 'string') {
      container.innerHTML = '';
      container.appendChild(createDom(next));
    } else if (next.tag !== lastTree.tag) {
      container.replaceChild(createDom(next), child);
    } else {
      patchNode(child, next, lastTree);
    }
    lastTree = next;
  }

  /* ---------------- 工具函数 ---------------- */
  function pad(n) { return n < 10 ? '0' + n : String(n); }

  function fmtTime(ts, withSec) {
    if (!ts) return '—';
    var s = String(ts).replace('T', ' ').replace('Z', '');
    if (withSec) return s.slice(0, 19);
    return s.slice(0, 16);
  }

  function rel(ts) {
    if (!ts) return '—';
    var t = Date.parse(String(ts).replace(' ', 'T'));
    if (isNaN(t)) return fmtTime(ts);
    var d = Math.floor((Date.now() - t) / 1000);
    if (d < 60) return d <= 0 ? '刚刚' : d + ' 秒前';
    if (d < 3600) return Math.floor(d / 60) + ' 分钟前';
    if (d < 86400) return Math.floor(d / 3600) + ' 小时前';
    if (d < 2592000) return Math.floor(d / 86400) + ' 天前';
    return fmtTime(ts);
  }

  function today() {
    var d = new Date();
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  }

  function uid(prefix) {
    return (prefix || 'id') + '-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  }

  function fmtNum(n, digits) {
    if (n === null || n === undefined || n === '') return '—';
    var v = Number(n);
    if (isNaN(v)) return String(n);
    if (digits === undefined) return v.toLocaleString('zh-CN');
    return v.toFixed(digits);
  }

  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  /** 把 vnode（或已是真实节点/字符串）转成 DOM，供弹窗等原生容器使用 */
  function toDom(v) {
    if (!v) return document.createTextNode('');
    if (v.nodeType) return v;                       // 已是真实节点
    return createDom(v);
  }

  global.Mini = {
    h: h, reactive: reactive, mount: mount, schedule: schedule,
    fmtTime: fmtTime, rel: rel, today: today, uid: uid, fmtNum: fmtNum,
    clamp: clamp, sleep: sleep, createDom: createDom, toDom: toDom
  };
  global.h = h;
})(window);
