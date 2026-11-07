/* ============================================================
 * 页面 · 双日志（Agent 推理日志 / 审计日志 / 工具调用留痕）
 * ============================================================ */
(function (global) {
  'use strict';
  var h = global.h, UI = global.UI, API = global.API, Narrate = global.Narrate;

  var TABS = [
    { value: 'agent', label: 'Agent 推理日志' },
    { value: 'audit', label: '审计日志' },
    { value: 'tool', label: '工具调用留痕' }
  ];

  function pretty(v) {
    if (v === null || v === undefined) return '—';
    if (typeof v === 'string') return v;
    try { return JSON.stringify(v, null, 2); } catch (e) { return String(v); }
  }

  function pre(text, maxH) {
    return h('pre', {
      class: 'mono',
      style: { margin: 0, maxHeight: (maxH || 180) + 'px', overflow: 'auto', fontSize: '11px' }
    }, text);
  }

  function agentTable(list) {
    if (!list.length) return UI.empty('暂无 Agent 推理日志', '⚙');
    return h('div', { class: 'tbl-wrap' }, h('table', null,
      h('thead', null, h('tr', null, ['时间', 'Agent', '节点', '动作', '思考', '工具', '模型/耗时', '卷宗']
        .map(function (t) { return h('th', null, t); }))),
      h('tbody', null, list.map(function (r) {
        var expanded = r.__open;
        var hasTool = r.tool_name || r.tool_output;
        var think = Narrate.thought(r.thought || '—');
        return h('tr', null,
          h('td', { style: { fontSize: '11px', whiteSpace: 'nowrap' } }, Mini.fmtTime(r.created_at, true)),
          h('td', null, h('div', null, UI.tag(r.agent_code || '—', 'purple')),
            h('div', { style: { fontSize: '10px', color: 'var(--text-3)' } }, r.agent_name || '')),
          h('td', null, r.node ? UI.tag(Narrate.node(r.node), 'cyan') : '—'),
          h('td', { style: { color: 'var(--text)', fontSize: '12px' } }, Narrate.action(r.action || '—')),
          /* 思考：默认一句人话，点开才看完整文本（历史脏日志已被 thought 洗过） */
          h('td', { style: { fontSize: '11px', color: 'var(--text-2)', maxWidth: '300px' } },
            h('div', { style: { cursor: 'pointer', lineHeight: 1.6 },
                onClick: function () { r.__open = !expanded; } },
              think.slice(0, 60) + (think.length > 60 ? ' …' : '')),
            expanded ? pre(think, 160) : null),
          /* 工具：默认中文名 + 结果人话，展开才显示原始入参/出参 JSON */
          h('td', { style: { maxWidth: '260px' } }, hasTool
            ? h('div', null,
                h('div', { style: { fontSize: '11px', color: 'var(--text-2)', lineHeight: 1.6, cursor: 'pointer' },
                    onClick: function () { r.__open = !expanded; } },
                  r.tool_name ? Narrate.tool(r.tool_name, r.tool_output) : '本次流程的内部记录'),
                expanded && r.tool_input && Object.keys(r.tool_input).length
                  ? pre(pretty(r.tool_input), 100) : null,
                expanded && r.tool_output && Object.keys(r.tool_output).length
                  ? pre(pretty(r.tool_output), 120) : null)
            : '—'),
          h('td', { style: { fontSize: '11px', color: 'var(--text-3)' } },
            h('div', null, Narrate.model(r.model)),
            h('div', null, (r.latency_ms || 0) + 'ms · ' + (r.tokens || 0) + ' tok')),
          h('td', null, r.case_id
            ? h('span', { style: { color: 'var(--gold)', cursor: 'pointer', fontSize: '12px' },
                onClick: function () { App.go('cases'); } }, '#' + r.case_id)
            : '—'));
      }))));
  }

  function auditTable(list) {
    if (!list.length) return UI.empty('暂无审计日志', '📋');
    var SRC = { human: '人工', scheduler: '调度器', agent: 'Agent', system: '系统' };
    return h('div', { class: 'tbl-wrap' }, h('table', null,
      h('thead', null, h('tr', null, ['时间', '来源', '操作人', '动作', '对象', '详情', 'IP']
        .map(function (t) { return h('th', null, t); }))),
      h('tbody', null, list.map(function (r) {
        return h('tr', null,
          h('td', { style: { fontSize: '11px', whiteSpace: 'nowrap' } }, Mini.fmtTime(r.created_at, true)),
          h('td', null, UI.tag(SRC[r.source] || r.source || '—',
            r.source === 'scheduler' ? 'cyan' : (r.source === 'agent' ? 'purple' : 'gray'))),
          h('td', { style: { fontSize: '12px' } }, r.user_name || '系统'),
          h('td', { style: { color: 'var(--text)', fontSize: '12px' } }, r.action || '—'),
          h('td', { style: { fontSize: '11px', color: 'var(--text-3)' } },
            (r.target_type || '—') + (r.target_id ? '#' + r.target_id : '')),
          h('td', { style: { fontSize: '11px', color: 'var(--text-2)', maxWidth: '280px' } },
            /* 审计详情常是 {file: ...} 这类字典，默认给中文摘要，
               整份原始记录折叠保留，不用半截 JSON 糊人 */
            (function () {
              var d = Narrate.parse(r.detail);
              if (!d) return '—';
              if (d._text !== undefined) return String(d._text).slice(0, 120);
              return (Narrate.summarize(d) || '含 ' + Object.keys(d).length + ' 项记录').slice(0, 120);
            })(),
            Narrate.details(r.detail, r, '__auditDetail')),
          h('td', { class: 'mono', style: { fontSize: '11px', color: 'var(--text-3)' } }, r.ip || '—'));
      }))));
  }

  function toolTable(list) {
    if (!list.length) return UI.empty('暂无工具调用留痕', '🔧');
    return h('div', { class: 'tbl-wrap' }, h('table', null,
      h('thead', null, h('tr', null, ['时间', 'Agent', '工具', '入参', '结果', '状态', '耗时', '卷宗']
        .map(function (t) { return h('th', null, t); }))),
      h('tbody', null, list.map(function (r) {
        var args = Narrate.parse(r.args);
        return h('tr', null,
          h('td', { style: { fontSize: '11px', whiteSpace: 'nowrap' } }, Mini.fmtTime(r.created_at, true)),
          h('td', null, UI.tag(r.agent_code || '—', 'purple')),
          /* 工具名说中文，原始机器名收在展开的技术详情里 */
          h('td', null, h('span', { style: { fontSize: '11px', color: 'var(--gold-2)' } },
            Narrate.toolLabel(r.tool_name) || '—'),
            Narrate.details(r.tool_name, r, '__toolNameOpen')),
          h('td', { style: { maxWidth: '220px' } },
            h('div', { style: { fontSize: '11px', color: 'var(--text-2)', lineHeight: 1.6 } },
              Narrate.summarize(args) || '本次调用的入参'),
            Narrate.details(r.args, r, '__argsOpen')),
          h('td', { style: { maxWidth: '220px' } },
            h('div', { style: { fontSize: '11px', color: 'var(--text-2)', lineHeight: 1.6 } },
              Narrate.tool(r.tool_name, r.result)),
            Narrate.details(r.result, r, '__resultOpen')),
          h('td', null, r.success ? UI.tag('成功', 'green') : UI.tag('失败', 'red')),
          h('td', { style: { fontSize: '11px', color: 'var(--text-3)' } }, (r.duration_ms || 0) + 'ms'),
          h('td', null, r.case_id ? '#' + r.case_id : '—'));
      }))));
  }

  global.PAGES.logs = {
    name: '双日志',
    sub: 'Agent / Audit / Tool',
    render: function (ps) {
      if (!ps.loaded && !ps.busy) { ps.busy = true; this.load(ps); }
      ps.tab = ps.tab || 'agent';
      ps.limit = ps.limit || 60;
      var self = this;

      return [
        h('div', { class: 'card' },
          h('div', { class: 'card-title' }, '📋 全链路留痕',
            h('span', { class: 'sub' }, 'Agent 思考过程 · 人工操作审计 · 工具真实调用'),
            h('div', { class: 'spacer' }),
            UI.select([{ value: 30, label: '最近 30 条' }, { value: 60, label: '最近 60 条' },
                       { value: 100, label: '最近 100 条' }, { value: 200, label: '最近 200 条' }],
              ps.limit, function (v) { ps.limit = +v; self.load(ps); }),
            UI.btn('刷新', 'sm ghost', function () { self.load(ps); })),

          UI.tabs(TABS, ps.tab, function (v) { ps.tab = v; self.load(ps); }),

          h('div', { style: { marginTop: '12px' } },
            ps.tab === 'agent' ? agentTable(ps.agentList || [])
              : ps.tab === 'audit' ? auditTable(ps.auditList || [])
                : toolTable(ps.toolList || [])),

          h('div', { style: { marginTop: '10px', fontSize: '11px', color: 'var(--text-3)' } },
            'Agent 日志 ' + (ps.agentTotal || 0) + ' 条 · 审计日志 ' + (ps.auditTotal || 0) +
            ' 条 · 工具调用 ' + (ps.toolTotal || 0) + ' 条')),
      ];
    },

    load: function (ps) {
      ps.busy = true;
      var lim = ps.limit || 60;
      return Promise.all([
        API.agentLogs({ limit: lim }),
        API.auditLogs({ limit: lim }),
        API.toolCalls({ limit: lim })
      ]).then(function (r) {
        ps.agentList = (r[0] || {}).list || []; ps.agentTotal = (r[0] || {}).total || 0;
        ps.auditList = (r[1] || {}).list || []; ps.auditTotal = (r[1] || {}).total || 0;
        ps.toolList = (r[2] || {}).list || []; ps.toolTotal = (r[2] || {}).total || 0;
        ps.loaded = true;
      }).catch(function (e) { UI.toast(e.message, 'err'); })
        .then(function () { ps.busy = false; });
    }
  };
})(window);
