/* ============================================================
 * 页面 · 阎罗大殿（数据大屏）
 * ============================================================ */
(function (global) {
  'use strict';
  var h = global.h, UI = global.UI, API = global.API;

  var NODE_ICONS = {
    intake: '①', judge: '②', review: '③', execution: '④',
    reincarnation: '⑤', mengpo: '⑥', archive: '⑦'
  };

  function flowStrip(flow, currentNodes) {
    var nodes = (flow && flow.nodes) || [];
    return h('div', { class: 'flow' }, nodes.map(function (n) {
      var done = currentNodes[n.key] === 'done';
      var active = currentNodes[n.key] === 'active';
      return h('div', { class: 'flow-node' + (done ? ' done' : active ? ' active' : '') },
        h('div', { class: 'dot' }),
        h('div', { class: 'n' }, NODE_ICONS[n.key] + ' ' + n.name),
        h('div', { class: 'a' }, n.agent),
        h('div', { class: 'a' }, active ? '进行中' : done ? '完成' : '待办'));
    }));
  }

  global.PAGES.dashboard = {
    name: '阎罗大殿',
    sub: '数据总览',
    render: function (ps) {
      if (!ps.loaded && !ps.busy) { ps.busy = true; this.load(ps); }

      var ov = ps.ov || {};
      var by = ov.by_status || {};
      var degrade = ov.degrade || {};
      var flow = ov.flow || {};
      var series = ps.series || { dates: [], counts: [] };

      /* 后端 by_status 的 key 为英文状态码，这里统一按码取数。
         先按「在办」推高亮，只有全部卷宗都已归档时整条流程才算走完——
         否则只要有一条归档就会把仍在服刑/待复核的节点也刷成「完成」。 */
      var cur = {};
      var inFlight = false;
      if (by.pending) { cur.intake = 'active'; inFlight = true; }
      if (by.accepted || by.judging) { cur.intake = 'done'; cur.judge = 'active'; inFlight = true; }
      if (by.reviewing) { cur.intake = 'done'; cur.judge = 'done'; cur.review = 'active'; inFlight = true; }
      if (by.executing) { cur.intake = cur.judge = cur.review = 'done'; cur.execution = 'active'; inFlight = true; }
      if (by.reincarnating) { cur.intake = cur.judge = cur.review = cur.execution = 'done'; cur.reincarnation = 'active'; inFlight = true; }
      if (!inFlight && by.completed) {
        cur.intake = cur.judge = cur.review = cur.execution = cur.reincarnation = cur.mengpo = cur.archive = 'done';
      } else if (by.completed) {
        /* 有在办卷宗时，已归档的数量只体现在 KPI 上，流程条继续指在办节点 */
        cur.archive = cur.archive || undefined;
      }

      var kpis = [
        { label: '在办卷宗', value: ov.cases || 0, hint: '待受理 ' + (by.pending || 0) + ' · 待复核 ' + (by.reviewing || 0), cls: '' },
        { label: '在册亡魂', value: ov.deceased || 0, hint: '总罪业 ' + (ov.total_sin || 0), cls: 'b' },
        { label: '证据件数', value: ov.evidence || 0, hint: '判决 ' + (ov.judgments || 0) + ' 份', cls: 'p' },
        { label: '待阎王复核', value: ov.pending_approvals || 0, hint: '需人工裁决', cls: 'r' },
        { label: '已入轮回', value: ov.reincarnations || 0, hint: '服刑中 ' + (by.executing || 0) + ' · 判定中 ' + (by.reincarnating || 0), cls: 'g' },
        { label: 'Agent 日志', value: ov.agent_logs || 0, hint: '人工审计 ' + (ov.audit_logs || 0), cls: '' },
        { label: '知识切片', value: ov.chunks || 0, hint: 'RAG 检索单元', cls: 'b' },
        { label: '编排引擎', value: ov.engine === 'langgraph' ? 'LG' : 'SM',
          hint: (ov.degrade && ov.degrade.langgraph_used === false)
            ? '内置状态机（langgraph 未参与执行）' : (ov.engine || 'builtin-state-machine'), cls: 'p' }
      ];

      var realms = ov.by_realm || {};
      var realmNames = Object.keys(realms);
      var realmCounts = realmNames.map(function (k) { return realms[k]; });

      var STATUS_CODES = ['pending', 'accepted', 'judging', 'reviewing', 'executing', 'reincarnating', 'completed'];
      var statusNames = STATUS_CODES.map(UI.statusText);
      var statusCounts = STATUS_CODES.map(function (k) { return by[k] || 0; });

      return [
        /* KPI 区 */
        h('div', { class: 'grid grid-4' }, kpis.map(function (k) {
          return h('div', { class: 'kpi ' + k.cls },
            h('div', { class: 'label' }, k.label),
            h('div', { class: 'value' }, String(k.value)),
            h('div', { class: 'hint' }, k.hint));
        })),

        /* 流程条 */
        h('div', { class: 'card' },
          h('div', { class: 'card-title' },
            '七殿会审流程',
            h('span', { class: 'sub' }, 'StateGraph · ' + (ov.engine || 'builtin-state-machine')),
            h('div', { class: 'spacer' }),
            UI.btn('查看编排', 'sm ghost', function () { App.go('agents'); })),
          flowStrip(flow, cur)),

        h('div', { class: 'grid grid-2' },
          /* 案件状态分布 */
          h('div', { class: 'card' },
            h('div', { class: 'card-title' }, '卷宗状态分布', h('div', { class: 'spacer' }),
              h('span', { class: 'sub' }, '实时')),
            UI.barChart(statusCounts, statusNames, ['#4a9de0', '#d9a441', '#e08a3c', '#e05c5c', '#9a6ce0', '#3fc9c9', '#46b87e'])),

          /* 六道轮回分布 */
          h('div', { class: 'card' },
            h('div', { class: 'card-title' }, '六道轮回分布', h('div', { class: 'spacer' }),
              h('span', { class: 'sub' }, '已判 ' + realmNames.length + ' 道')),
            realmNames.length
              ? UI.barChart(realmCounts, realmNames, ['#f0c469', '#46b87e', '#9a6ce0', '#e05c5c', '#4a9de0', '#e08a3c'])
              : UI.empty('尚无轮回记录', '☯'))),

        /* 近 7 天趋势 + 降级矩阵 */
        h('div', { class: 'grid grid-2' },
          h('div', { class: 'card' },
            h('div', { class: 'card-title' }, '近七日立案趋势', h('div', { class: 'spacer' }),
              h('span', { class: 'sub' }, '按创建日期聚合')),
            series.dates.length
              ? UI.barChart(series.counts, series.dates.map(function (d) { return d.slice(5); }))
              : UI.empty('暂无立案数据', '◷')),

          h('div', { class: 'card' },
            h('div', { class: 'card-title' }, '依赖降级矩阵', h('div', { class: 'spacer' }),
              h('span', { class: 'sub' }, '缺失依赖自动降级')),
            h('div', { style: { display: 'grid', gap: '8px' } },
              /* 只渲染布尔字段：engine / flow_engine / checkpointer 是字符串，
                 混进来会被当 truthy 显示成「降级」，把人误导。 */
              Object.keys(degrade).filter(function (k) {
                return typeof degrade[k] === 'boolean';
              }).map(function (k) {
                var degraded = degrade[k];   /* true = 缺失依赖，已降级 */
                return h('div', { style: { display: 'flex', alignItems: 'center', gap: '10px', fontSize: '13px' } },
                  h('span', { style: { width: '110px', color: 'var(--text-3)' } }, k),
                  UI.tag(degraded ? '降级' : '已安装', degraded ? 'orange' : 'green'),
                  h('span', { style: { color: 'var(--text-3)', fontSize: '11px' } },
                    degraded ? '走内置兜底实现' : '启用原生能力'));
              }),
              h('div', { style: { marginTop: '10px', fontSize: '11px', color: 'var(--text-3)', lineHeight: 1.8 } },
                degrade.langgraph_used
                  ? '说明：LangGraph StateGraph 正在驱动节点与条件边；检查点由 ' +
                    (degrade.checkpointer || 'MemorySaver') + ' 记录，业务状态持久化在项目 checkpoint 表，重启可续跑。'
                  : '说明：未启用 LangGraph，办案流程跑内置状态机（纯标准库）；',
                degrade.langgraph_used ? ''
                  : 'APScheduler / FastAPI 等缺失时也会自动切换到线程调度器与自研 HTTP 服务，业务链路不受影响。'),
              h('div', { style: { marginTop: '6px', fontSize: '11px', color: 'var(--text-3)' } },
                '实际引擎 · ' + (degrade.engine || 'builtin-state-machine') +
                '　|　引擎开关 · ' + (degrade.flow_engine || 'auto')))))
      ];
    },

    load: function (ps) {
      ps.busy = true;
      return Promise.all([API.overview(), API.series()])
        .then(function (r) {
          ps.ov = r[0];
          ps.series = r[1];
          ps.loaded = true;
        })
        .catch(function (e) { UI.toast(e.message, 'err'); })
        .then(function () { ps.busy = false; });
    }
  };
})(window);
