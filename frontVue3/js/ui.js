/* ============================================================
 * ui.js —— 通用组件库（Toast / Modal / 标签 / 表单 / 空态 / 分页）
 * ============================================================ */
(function (global) {
  'use strict';
  var h = global.h;

  /* ---------------- Toast ---------------- */
  var toasts = [];
  function toast(msg, type) {
    var id = Mini.uid('t');
    toasts.push({ id: id, msg: String(msg), type: type || 'ok' });
    renderToasts();
    setTimeout(function () {
      toasts = toasts.filter(function (t) { return t.id !== id; });
      renderToasts();
    }, type === 'err' ? 4200 : 2600);
  }
  function renderToasts() {
    var box = document.getElementById('toast');
    if (!box) return;
    box.innerHTML = '';
    toasts.forEach(function (t) {
      var d = document.createElement('div');
      d.className = 'toast ' + (t.type === 'err' ? 'err' : t.type === 'info' ? '' : 'ok');
      d.textContent = t.msg;
      box.appendChild(d);
    });
  }

  /* ---------------- 弹窗 ---------------- */
  var modalStack = [];
  function openModal(opts) {
    var id = Mini.uid('m');
    modalStack.push({ id: id, opts: opts });
    renderModals();
    return id;
  }
  function closeModal(id) {
    modalStack = modalStack.filter(function (m) { return m.id !== id; });
    renderModals();
  }
  function renderModals() {
    var box = document.getElementById('modal-root');
    if (!box) return;
    box.innerHTML = '';
    modalStack.forEach(function (m) {
      var o = m.opts;
      var mask = document.createElement('div');
      mask.className = 'modal-mask';
      mask.addEventListener('click', function (e) {
        if (e.target === mask && o.closable !== false) closeModal(m.id);
      });
      var dlg = document.createElement('div');
      dlg.className = 'modal';
      var head = document.createElement('div');
      head.className = 'modal-head';
      /* 🔴 原来是 `head.innerHTML = '<span>' + o.title + '</span>'`：
         标题可能来自后端（典籍标题 / 亡魂姓名 / 文件名），拼进 innerHTML
         就是存储型 XSS，攻击者能直读 localStorage['difu.token']。
         用 textContent 后所有内容一律当纯文本，浏览器不会解析标签。 */
      var titleSpan = document.createElement('span');
      titleSpan.textContent = o.title || '提示';
      head.appendChild(titleSpan);
      if (o.closable !== false) {
        var x = document.createElement('span');
        x.className = 'x';
        x.textContent = '✕';
        x.addEventListener('click', function () { closeModal(m.id); });
        head.appendChild(x);
      }
      dlg.appendChild(head);

      var body = document.createElement('div');
      body.className = 'modal-body';
      if (o.body) body.appendChild(Mini.toDom(o.body));   // body 可以是 vnode
      dlg.appendChild(body);

      if (o.footer !== false) {
        var foot = document.createElement('div');
        foot.className = 'modal-foot';
        var cancel = document.createElement('button');
        cancel.className = 'btn ghost';
        cancel.textContent = o.cancelText || '取消';
        cancel.addEventListener('click', function () {
          closeModal(m.id);
          if (o.onCancel) o.onCancel();
        });
        foot.appendChild(cancel);
        if (o.onOk) {
          var okb = document.createElement('button');
          okb.className = 'btn primary';
          okb.textContent = o.okText || '确定';
          okb.addEventListener('click', function () {
            var keep = o.onOk();
            if (keep !== false) closeModal(m.id);
          });
          foot.appendChild(okb);
        }
        dlg.appendChild(foot);
      }
      mask.appendChild(dlg);
      box.appendChild(mask);
    });
  }

  /* 简易确认框 */
  function confirmDialog(title, text, onOk, okText) {
    var p = document.createElement('div');
    p.className = 'kv';
    /* 🔴 同上：`text` 可能来自后端字段，原来直接拼进 innerHTML。 */
    var kEl = document.createElement('div');
    kEl.className = 'k';
    kEl.textContent = '操作';
    var vEl = document.createElement('div');
    vEl.className = 'v';
    vEl.textContent = text || '';
    p.appendChild(kEl);
    p.appendChild(vEl);
    return openModal({
      title: title, body: p, onOk: onOk,
      okText: okText || '确定', cancelText: '取消'
    });
  }

  /* ---------------- 小工具组件 ---------------- */
  function tag(text, color) {
    return h('span', { class: 'tag ' + (color || 'gray') }, text);
  }

  var STATUS_TEXT = {
    'pending': '待受理', 'accepted': '已受理', 'judging': '审理中', 'reviewing': '待复核',
    'executing': '刑罚执行中', 'reincarnating': '轮回判定中', 'completed': '已归档',
    'rejected': '已驳回', 'suspended': '已挂起'
  };
  var STATUS_COLOR = {
    '待受理': 'gray', '已受理': 'blue', '审理中': 'gold', '待复核': 'orange',
    '刑罚执行中': 'red', '轮回判定中': 'purple', '已归档': 'green', '已驳回': 'gray', '已挂起': 'cyan',
    'pending': 'gray', 'accepted': 'blue', 'judging': 'gold', 'reviewing': 'orange',
    'executing': 'red', 'reincarnating': 'purple', 'completed': 'green', 'rejected': 'gray',
    'suspended': 'cyan'
  };
  /** 状态码 → 中文（后端存英文码，展示层统一转中文） */
  function statusText(s) { return STATUS_TEXT[s] || s || '未知'; }
  function statusTag(s) {
    return tag(statusText(s), STATUS_COLOR[s] || 'gray');
  }

  var RISK_COLOR = { '高': 'red', '中': 'orange', '低': 'green', '极高': 'red' };
  function riskTag(r) { return tag(r || '—', RISK_COLOR[r] || 'gray'); }

  var PRIORITY_COLOR = { 'urgent': 'red', 'high': 'orange', 'normal': 'blue', 'low': 'gray' };
  var PRIORITY_TEXT = { 'urgent': '特急', 'high': '紧急', 'normal': '普通', 'low': '较低' };
  function priorityTag(p) { return tag(PRIORITY_TEXT[p] || p || '普通', PRIORITY_COLOR[p] || 'gray'); }

  function empty(text, icon) {
    return h('div', { class: 'empty' },
      h('span', { class: 'ic' }, icon || '☯'),
      h('div', null, text || '暂无数据'));
  }

  function progress(pct, width) {
    return h('div', { class: 'progress', style: { width: (width || 70) + 'px' } },
      h('i', { style: { width: Math.max(0, Math.min(100, pct || 0)) + '%' } }));
  }

  function kv(rows) {
    return h('div', { class: 'kv' },
      rows.map(function (r) {
        return [h('div', { class: 'k' }, r[0]), h('div', { class: 'v' }, r[1] === undefined ? '—' : r[1])];
      }));
  }

  function field(label, input, hint) {
    return h('div', { class: 'field' },
      h('label', null, label), input,
      hint ? h('div', { class: 'hint' }, hint) : null);
  }

  function input(props) { return h('input', props || {}); }
  function textarea(props) { return h('textarea', props || {}); }
  function select(opts, value, onchange, props) {
    var p = { onChange: function (e) { onchange && onchange(e.target.value); } };
    for (var k in (props || {})) p[k] = props[k];
    return h('select', p, opts.map(function (o) {
      var val = typeof o === 'string' ? o : o.value;
      var label = typeof o === 'string' ? o : o.label;
      return h('option', { value: val, selected: String(val) === String(value) }, label);
    }));
  }

  function btn(text, cls, onclick, disabled) {
    return h('button', {
      class: 'btn ' + (cls || ''),
      disabled: !!disabled,
      onClick: onclick
    }, text);
  }

  function tabs(items, active, onPick) {
    return h('div', { class: 'tabs' }, items.map(function (it) {
      var val = typeof it === 'string' ? it : it.value;
      var label = typeof it === 'string' ? it : it.label;
      return h('div', {
        class: 'tab' + (val === active ? ' active' : ''),
        onClick: function () { onPick(val); }
      }, label);
    }));
  }

  function barChart(data, labels, colors) {
    var max = Math.max.apply(null, data.concat([1]));
    return h('div', { class: 'bars' }, data.map(function (v, i) {
      var pct = Math.round(v / max * 100);
      return h('div', { class: 'bar-col' },
        h('div', { class: 'vv' }, String(v)),
        h('div', { class: 'bar-wrap' },
          h('div', {
            class: 'bar',
            style: {
              height: v ? Math.max(3, pct) + '%' : '0',
              background: colors ? colors[i % colors.length] : undefined
            }
          })),
        h('div', { class: 'lb' }, labels[i] || ''));
    }));
  }

  function pagination(page, size, total, onPage) {
    var pages = Math.max(1, Math.ceil(total / size));
    return h('div', { class: 'btn-row', style: { justifyContent: 'flex-end', marginTop: '12px', alignItems: 'center' } },
      h('span', { style: { fontSize: '12px', color: 'var(--text-3)', marginRight: 'auto' } },
        '共 ' + total + ' 条 · 第 ' + page + '/' + pages + ' 页'),
      btn('上一页', 'sm ghost', function () { onPage(page - 1); }, page <= 1),
      btn('下一页', 'sm ghost', function () { onPage(page + 1); }, page >= pages));
  }

  function spinner() {
    return h('span', { class: 'spin' }, '◌');
  }

  global.UI = {
    toast: toast, openModal: openModal, closeModal: closeModal, confirm: confirmDialog,
    tag: tag, statusTag: statusTag, statusText: statusText, riskTag: riskTag, priorityTag: priorityTag,
    empty: empty, progress: progress, kv: kv, field: field, input: input, textarea: textarea,
    select: select, btn: btn, tabs: tabs, barChart: barChart, pagination: pagination, spinner: spinner
  };
})(window);
