/* ============================================================
 * 页面 · Agent 编排（流程图 + 工具清单 + 实时日志）
 * ============================================================ */
(function (global) {
  'use strict';
  var h = global.h, UI = global.UI, API = global.API, Narrate = global.Narrate;

  var NODE_POS = {          // 纵向流式布局的序号
    intake: 1, judge: 2, review: 3, execution: 4,
    reincarnation: 5, mengpo: 6, archive: 7
  };

  function nodeCard(n, agents) {
    var ag = (agents || []).filter(function (a) { return a.node === n.key; });
    return h('div', {
      style: {
        display: 'flex', gap: '12px', padding: '12px', background: 'var(--bg-3)',
        borderRadius: '8px', border: '1px solid var(--line)', marginBottom: '8px'
      }
    },
      h('div', {
        style: {
          width: '28px', height: '28px', borderRadius: '50%', flex: 'none',
          background: 'rgba(217,164,65,.15)', color: 'var(--gold-2)',
          display: 'grid', placeItems: 'center', fontSize: '13px',
          border: '1px solid rgba(217,164,65,.4)'
        }
      }, String(NODE_POS[n.key] || '?')),
      h('div', { style: { flex: 1, minWidth: 0 } },
        h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' } },
          h('span', { style: { color: 'var(--text)', fontSize: '13px' } }, n.name),
          ag.length ? UI.tag(ag[0].code, 'purple') : null,
          ag.length ? h('span', { style: { fontSize: '11px', color: 'var(--text-3)' } }, ag[0].name) : null),
        h('div', { style: { fontSize: '12px', color: 'var(--text-3)', marginTop: '3px', lineHeight: 1.6 } }, n.desc),
        ag.length && ag[0].duty
          ? h('div', { style: { fontSize: '11px', color: 'var(--text-2)', marginTop: '4px' } }, '职责：' + ag[0].duty)
          : null));
  }

  function edgeList(edges) {
    return h('div', { style: { marginTop: '10px' } },
      h('div', { class: 'card-title', style: { fontSize: '12px' } }, '节点之间的流转条件'),
      (edges || []).map(function (e) {
        /* 原来写的是 intake ──▶ judge 这种英文 key，现在讲成一句人话 */
        return h('div', { style: { fontSize: '12px', color: 'var(--text-2)', padding: '3px 0', lineHeight: 1.7 } },
          '从「' + Narrate.node(e.from) + '」' +
          (e.label ? '在' + e.label + '时' : '') +
          '，转到「' + Narrate.node(e.to) + '」。');
      }));
  }

  global.PAGES.agents = {
    name: 'Agent 编排',
    sub: '节点编排与流转',
    render: function (ps) {
      if (!ps.loaded && !ps.busy) { ps.busy = true; this.load(ps); }
      var g = ps.graph || {};
      var nodes = g.nodes || [];
      var agents = g.agents || [];
      var toolsList = ps.tools || [];
      var logs = ps.logs || [];

      return [
        h('div', { class: 'grid grid-2' },
          h('div', { class: 'card' },
            h('div', { class: 'card-title' }, '七殿会审状态图',
              h('span', { class: 'sub' }, g.engine ? Narrate.engine(g.engine) : ''),
              h('div', { class: 'spacer' }),
              UI.btn('刷新', 'sm ghost', function () { global.PAGES.agents.load(ps); })),
            h('div', { style: { fontSize: '12px', color: 'var(--text-3)', marginBottom: '10px', lineHeight: 1.7 } },
              '共 ' + nodes.length + ' 个节点、' + agents.length + ' 个 Agent、' +
              toolsList.length + ' 个工具。每个节点由独立 Agent 负责，' +
              '通过检查点（Checkpoint）实现挂起与恢复，异常不炸流程。'),
            nodes.map(function (n) { return nodeCard(n, agents); }),
            edgeList(g.edges)),

          h('div', null,
            h('div', { class: 'card' },
              h('div', { class: 'card-title' }, '🔧 工具清单',
                h('span', { class: 'sub' }, '工具注册表 Schema（代码确定性调用）'),
                h('div', { class: 'spacer' }), UI.tag(String(toolsList.length) + ' 个', 'gold')),
              h('div', { style: { maxHeight: '420px', overflowY: 'auto' } },
                toolsList.map(function (t) {
                  var params = Object.keys(t.parameters || {});
                  return h('div', {
                    style: {
                      padding: '8px 10px', background: 'var(--bg-3)', borderRadius: '6px',
                      marginBottom: '6px', borderLeft: '2px solid var(--line-2)'
                    }
                  },
                    /* 这里是开发者看的 Schema 区，机器名保留，但先给中文名，
                       参数不再裸列 (case_id, missing)，改成「需要 N 项参数」+ 可展开原名 */
                    h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' } },
                      h('span', { style: { color: 'var(--text)', fontSize: '12px' } }, Narrate.toolLabel(t.name)),
                      h('span', { class: 'mono', style: { color: 'var(--gold-2)', fontSize: '11px' } }, t.name),
                      h('span', { style: { fontSize: '11px', color: 'var(--text-3)' } },
                        params.length ? '需要 ' + params.length + ' 项参数' : '无需参数')),
                    h('div', { style: { fontSize: '11px', color: 'var(--text-3)', marginTop: '2px' } },
                      t.description),
                    Narrate.details(t.parameters, t, '__paramOpen'));
                }))),

            h('div', { class: 'card' },
              h('div', { class: 'card-title' }, '🧠 最近推理日志',
                h('div', { class: 'spacer' }), UI.tag(String(ps.logTotal || 0) + ' 条', 'gray')),
              logs.length
                ? h('div', { style: { maxHeight: '300px', overflowY: 'auto' } }, logs.slice(0, 20).map(function (g2) {
                  return h('div', { style: { fontSize: '12px', padding: '6px 0', borderBottom: '1px solid var(--bg-3)' } },
                    h('div', { style: { display: 'flex', gap: '6px', alignItems: 'center' } },
                      UI.tag(g2.agent_name || g2.agent_code, 'purple'),
                      h('span', { style: { color: 'var(--text-2)' } }, Narrate.action(g2.action || '')),
                      h('span', { style: { marginLeft: 'auto', color: 'var(--text-3)', fontSize: '11px' } },
                        Mini.fmtTime(g2.created_at, true))),
                    h('div', { style: { color: 'var(--text-3)', marginTop: '2px' } },
                      Narrate.thought(g2.thought || '').slice(0, 100)));
                }))
                : UI.empty('暂无日志', '⚙'))))
      ];
    },

    load: function (ps) {
      ps.busy = true;
      return Promise.all([API.agentGraph(), API.agentTools(), API.agentLogs({ limit: 40 })])
        .then(function (r) {
          ps.graph = r[0];
          ps.tools = (r[1] || {}).list || [];
          ps.logs = (r[2] || {}).list || [];
          ps.logTotal = (r[2] || {}).total || 0;
          ps.loaded = true;
        })
        .catch(function (e) { UI.toast(e.message, 'err'); })
        .then(function () { ps.busy = false; });
    }
  };
})(window);
