/* ============================================================
 * 页面 · 阴司卷宗（列表 + 详情抽屉 + 新建 + 流程推进）
 * ============================================================ */
(function (global) {
  'use strict';
  var h = global.h, UI = global.UI, API = global.API;

  var STATUS_CODES = ['pending', 'accepted', 'judging', 'reviewing', 'executing',
    'reincarnating', 'completed', 'suspended', 'rejected'];
  var NODE_SEQ = ['intake', 'judge', 'review', 'execution', 'reincarnation', 'mengpo', 'archive'];
  var NODE_NAME = {
    intake: '阴差受理', judge: '判官审理', review: '阎王复核', execution: '刑罚执行',
    reincarnation: '六道轮回', mengpo: '孟婆登记', archive: '卷宗归档'
  };

  /* ---------------- 流程条 ---------------- */
  function flowBar(c, ck) {
    var done = c.current_node === 'archive' || c.status === '已归档';
    var cur = c.current_node || '';
    var halted = ck && ck.status === 'interrupted';
    var idx = NODE_SEQ.indexOf(cur);
    return h('div', { class: 'flow' }, NODE_SEQ.map(function (k, i) {
      var cls = '';
      if (done || (idx >= 0 && i < idx)) cls = 'done';
      if (k === cur && !done) cls = halted ? 'halt' : 'active';
      return h('div', { class: 'flow-node ' + cls },
        h('div', { class: 'dot' }),
        h('div', { class: 'n' }, NODE_NAME[k]),
        h('div', { class: 'a' }, k === cur ? (halted ? '挂起' : '进行中') : (cls === 'done' ? '完成' : '待办')));
    }));
  }

  /* ---------------- 详情弹窗 ---------------- */
  function openDetail(id) {
    var st = Mini.reactive({ loading: true, data: null, tab: 'flow', note: '', opBusy: false, err: '' });
    /* 弹窗体是原生 DOM 容器：内容由 paint() 手工重绘 */
    var body = document.createElement('div');
    body.appendChild(Mini.toDom(h('div', { class: 'empty' }, UI.spinner(), ' 加载卷宗…')));

    function refresh() {
      st.loading = true;
      st.err = '';
      API.caseDetail(id)
        .then(function (d) { st.data = d; st.loading = false; })
        .catch(function (e) {
          /* 加载失败要留下明确提示，否则弹窗会永远停在「加载中…」 */
          st.err = e.message || '卷宗加载失败';
          st.loading = false;
          UI.toast(st.err, 'err');
        });
    }
    refresh();

    function act(fn, okMsg) {
      if (st.opBusy) return;
      st.opBusy = true;
      fn().then(function () {
        UI.toast(okMsg || '操作成功');
        refresh();
        var ps = pageState['cases'];
        if (ps && ps.load) ps.load(ps);
      }).catch(function (e) { UI.toast(e.message, 'err'); })
        .then(function () { st.opBusy = false; });
    }

    var mid = UI.openModal({ title: '卷宗详情', body: body, footer: false });

    /* 弹窗是原生 DOM，内容变化时手工重绘；关闭后停止重绘 */
    function paint() {
      if (!body.isConnected) { clearInterval(timer); return; }
      body.innerHTML = '';
      if (!st.data) {
        body.appendChild(Mini.toDom(st.err
          ? h('div', { class: 'empty' }, h('div', null, '⚠ ' + st.err),
              h('div', { class: 'btn sm', onClick: refresh, style: { marginTop: '12px' } }, '重试'))
          : h('div', { class: 'empty' }, UI.spinner(), ' 加载中…')));
        return;
      }
      body.appendChild(Mini.toDom(render(st.data, st, act, refresh, mid)));
    }
    var timer = setInterval(paint, 400);
    paint();
  }

  function render(d, st, act, refresh, mid) {
    var c = d.case || d;
    var dec = d.deceased || {};
    var judgment = d.judgment || {};
    var execution = d.execution || {};
    var reinc = d.reincarnation || {};
    var ck = d.checkpoint || {};

    var tabs = [
      { v: 'flow', t: '流转轨迹' },
      { v: 'judgment', t: '判决与执行' },
      { v: 'evidence', t: '证据材料 (' + (d.evidence || []).length + ')' },
      { v: 'notes', t: '批注 (' + (d.notes || []).length + ')' },
      { v: 'logs', t: 'Agent 日志' }
    ];

    var head = h('div', null,
      h('div', { style: { display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '12px', flexWrap: 'wrap' } },
        h('span', { class: 'mono', style: { color: 'var(--gold-2)', fontSize: '14px' } }, c.case_no),
        UI.statusTag(c.status),
        UI.riskTag(c.risk_level),
        UI.priorityTag(c.priority),
        h('div', { style: { marginLeft: 'auto' } }, UI.progress(c.progress, 100))),
      h('div', { style: { fontSize: '16px', marginBottom: '12px' } }, c.title || '未命名卷宗'),
      UI.kv([
        ['亡魂', (dec.name || '—') + (dec.gender ? ' · ' + dec.gender : '')],
        ['功德 / 罪业', (dec.merit_score || 0) + ' / ' + (dec.sin_score || 0) + '　' + (dec.merit_level || '')],
        ['死因', dec.cause_of_death || '—'],
        ['籍贯', dec.hometown || '—'],
        ['来源', c.source === 'chat' ? '对话建卷' : c.source === 'demo' ? '演示数据' : '人工建卷'],
        ['立案时间', Mini.fmtTime(c.created_at, true)]
      ]));

    var flowCard = h('div', { style: { marginTop: '16px' } },
      h('div', { class: 'card-title' }, '七殿流转', h('div', { class: 'spacer' }),
        ck.node ? UI.tag('检查点：' + (NODE_NAME[ck.node] || ck.node), ck.status === 'interrupted' ? 'orange' : 'green') : null),
      flowBar(c, ck),
      h('div', { class: 'btn-row', style: { marginTop: '12px' } },
        UI.btn(c.status === 'pending' ? '▶ 开始办案' : '▶ 推进流程', 'primary sm',
          function () { act(function () { return API.runCase(c.id); }, '流程已推进'); }, st.opBusy),
        c.status === 'executing'
          ? UI.btn('⏩ 模拟刑期届满', 'sm', function () { act(function () { return API.expireCase(c.id); }, '已触发轮回'); }, st.opBusy)
          : null,
        UI.btn('🗄 归档', 'sm ghost', function () { act(function () { return API.archiveCase(c.id); }, '已归档'); }, st.opBusy),
        UI.btn('🗑 删除', 'sm danger', function () {
          UI.confirm('删除卷宗', '将同时删除该卷宗的证据、判决、审批与检查点，不可恢复。', function () {
            API.deleteCase(c.id).then(function () {
              UI.toast('卷宗已删除');
              UI.closeModal(mid);
              var ps = pageState['cases'];
              if (ps && ps.load) ps.load(ps);
            }).catch(function (e) { UI.toast(e.message, 'err'); });
          });
        }, st.opBusy)));

    /* --- 各 tab 内容 --- */
    function flowTab() {
      var items = [];
      (d.agent_logs || []).slice().reverse().forEach(function (g) {
        items.push(h('div', { class: 'tl-item' },
          h('div', { class: 'hd' },
            UI.tag(g.agent_name || g.agent_code, 'gold'),
            h('span', null, g.action || ''),
            h('span', { class: 'tm' }, Mini.fmtTime(g.created_at, true))),
          g.thought ? h('div', { class: 'bd' }, g.thought) : null,
          g.tool_name ? h('div', { class: 'bd mono' },
            '⚙ ' + g.tool_name + ' → ' + String(g.tool_output || '').slice(0, 160)) : null));
      });
      return items.length ? h('div', { class: 'timeline' }, items) : UI.empty('暂无流转记录', '⚖');
    }

    function judgmentTab() {
      return [
        judgment.id ? h('div', { class: 'card', style: { background: 'var(--bg-3)' } },
          h('div', { class: 'card-title' }, '⚖ 判决书 ',
            h('span', { class: 'sub' }, Mini.fmtTime(judgment.created_at, true))),
          UI.kv([
            ['认定罪名', UI.tag(judgment.crime || '—', 'red')],
            ['适用法条', judgment.law_article || '—'],
            ['功德 / 罪业', (judgment.merit_score || 0) + ' / ' + (judgment.sin_score || 0)],
            ['量刑', (judgment.sentence_type || '') + ' · ' + (judgment.sentence_years || 0) + ' 年'],
            ['刑罚', judgment.penalty_desc || '—'],
            ['风险等级', UI.riskTag(judgment.risk_level)],
            ['审批人', judgment.approved_by || '（无需复核）']
          ]),
          h('div', { style: { marginTop: '10px', fontSize: '12px', color: 'var(--text-3)' } }, '判决理由：'),
          h('div', { class: 'mono', style: { marginTop: '4px', color: 'var(--text-2)', lineHeight: 1.8 } }, judgment.reason || '—'))
          : UI.empty('尚未生成判决', '⚖'),

        execution.id ? h('div', { class: 'card', style: { background: 'var(--bg-3)' } },
          h('div', { class: 'card-title' }, '⛓ 服刑记录'),
          UI.kv([
            ['服刑地狱', execution.prison || '—'],
            ['刑期', (execution.sentence_days || 0) + ' 天'],
            ['起止', (execution.start_date || '') + ' → ' + (execution.end_date || '')],
            ['剩余', (execution.remaining_days || 0) + ' 天'],
            ['状态', UI.tag(execution.status || '', execution.status === '服刑中' ? 'red' : 'green')]
          ]),
          h('div', { style: { marginTop: '10px' } }, UI.progress(
            execution.sentence_days ? (1 - (execution.remaining_days || 0) / execution.sentence_days) * 100 : 0, 200)))
          : null,

        reinc.id ? h('div', { class: 'card', style: { background: 'var(--bg-3)' } },
          h('div', { class: 'card-title' }, '☯ 六道轮回判决'),
          UI.kv([
            ['投生六道', UI.tag(reinc.realm || '—', 'purple')],
            ['来世', reinc.next_life || '—'],
            ['孟婆登记', reinc.mengpo_registered ? UI.tag('已饮忘川', 'green') : UI.tag('未登记', 'gray')]
          ]),
          h('div', { style: { marginTop: '10px', fontSize: '12px', color: 'var(--text-3)' } }, '判定依据：'),
          h('div', { class: 'mono', style: { marginTop: '4px', color: 'var(--text-2)' } }, reinc.realm_reason || '—'),
          reinc.document ? h('pre', { class: 'mono', style: { marginTop: '10px' } }, reinc.document) : null)
          : null
      ];
    }

    function evidenceTab() {
      var evis = d.evidence || [];
      if (!evis.length) return UI.empty('暂无证据材料', '🔍');
      return h('div', null, evis.map(function (e) {
        return h('div', { class: 'card', style: { background: 'var(--bg-3)', marginBottom: '10px' } },
          h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '6px' } },
            UI.tag(e.evi_type || 'text', 'blue'),
            h('span', { style: { fontSize: '13px' } }, e.file_name || ''),
            h('span', { style: { marginLeft: 'auto', fontSize: '11px', color: 'var(--text-3)' } },
              Mini.fmtTime(e.created_at))),
          h('div', { style: { fontSize: '12px', color: 'var(--text-2)', lineHeight: 1.7 } },
            (e.summary || e.ocr_text || '').slice(0, 300)));
      }));
    }

    function notesTab() {
      var notes = d.notes || [];
      var ta = h('textarea', {
        placeholder: '写下你的批注…',
        value: st.note,
        onInput: function (e) { st.note = e.target.value; },
        style: { minHeight: '70px' }
      });
      return h('div', null,
        h('div', { class: 'field' }, ta,
          h('div', { style: { marginTop: '8px' } },
            UI.btn('提交批注', 'primary sm', function () {
              if (!st.note.trim()) return UI.toast('批注内容不能为空', 'err');
              act(function () {
                return API.addNote(c.id, st.note.trim()).then(function () { st.note = ''; });
              }, '批注已记录');
            }, st.opBusy))),
        notes.length
          ? h('div', { class: 'timeline' }, notes.map(function (n) {
            return h('div', { class: 'tl-item' },
              h('div', { class: 'hd' },
                UI.tag(n.actor_type === 'agent' ? 'Agent' : '人工', n.actor_type === 'agent' ? 'purple' : 'gold'),
                h('span', null, n.actor_name || ''),
                h('span', { class: 'tm' }, Mini.fmtTime(n.created_at, true))),
              h('div', { class: 'bd' }, n.content));
          }))
          : UI.empty('暂无批注', '✎'));
    }

    function logsTab() {
      var logs = d.agent_logs || [];
      if (!logs.length) return UI.empty('暂无 Agent 推理日志', '⚙');
      return h('div', null, logs.map(function (g) {
        return h('div', { class: 'card', style: { background: 'var(--bg-3)', marginBottom: '10px' } },
          h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '6px', flexWrap: 'wrap' } },
            UI.tag(g.agent_name, 'purple'),
            h('span', { style: { fontSize: '12px', color: 'var(--text-2)' } }, g.action || ''),
            g.model ? UI.tag(g.model, 'gray') : null,
            h('span', { style: { marginLeft: 'auto', fontSize: '11px', color: 'var(--text-3)' } },
              (g.latency_ms || 0) + 'ms · ' + Mini.fmtTime(g.created_at, true))),
          g.thought ? h('div', { style: { fontSize: '12px', color: 'var(--text-2)', lineHeight: 1.7 } }, g.thought) : null,
          g.tool_name ? h('pre', { class: 'mono', style: { marginTop: '6px' } },
            g.tool_name + '\n→ ' + String(g.tool_output || '').slice(0, 220)) : null);
      }));
    }

    var tabBody = st.tab === 'flow' ? flowTab() : st.tab === 'judgment' ? judgmentTab()
      : st.tab === 'evidence' ? evidenceTab() : st.tab === 'notes' ? notesTab() : logsTab();

    return h('div', null,
      head, flowCard,
      UI.tabs(tabs, st.tab, function (v) { st.tab = v; }),
      tabBody,
      (d.approvals && d.approvals.length)
        ? h('div', { class: 'card', style: { marginTop: '14px', background: 'var(--bg-3)' } },
          h('div', { class: 'card-title' }, '✍ 复核记录'),
          d.approvals.map(function (a) {
            return h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center', padding: '6px 0', fontSize: '12px' } },
              UI.tag(a.appr_type || '复核', 'orange'),
              h('span', null, a.title || ''),
              UI.tag(a.status === 'approved' ? '已批准' : a.status === 'rejected' ? '已驳回' : '待批',
                a.status === 'approved' ? 'green' : a.status === 'rejected' ? 'red' : 'orange'),
              h('span', { style: { marginLeft: 'auto', color: 'var(--text-3)' } },
                (a.approver_name || '') + ' ' + Mini.fmtTime(a.decided_at || a.created_at)));
          }))
        : null);
  }

  /* ---------------- 新建卷宗 ---------------- */
  function openCreate() {
    var st = Mini.reactive({ list: [], form: { deceased_id: '', title: '', priority: 'normal' }, busy: false });
    API.deceased({ size: 100 }).then(function (d) { st.list = d.list || []; }).catch(function () {});
    var body = h('div', null,
      UI.field('选择亡魂', UI.select(
        [{ value: '', label: '请选择在册亡魂' }].concat(st.list.map(function (x) {
          return { value: x.id, label: x.name + '（' + x.merit_level + ' · 功德' + x.merit_score + '）' };
        })),
        st.form.deceased_id, function (v) { st.form.deceased_id = v; })),
      UI.field('卷宗标题', h('input', {
        placeholder: '如：赵德柱斗殴误伤案', value: st.form.title,
        onInput: function (e) { st.form.title = e.target.value; }
      }), '留空则自动生成'),
      UI.field('紧急程度', UI.select(
        [{ value: 'normal', label: '普通' }, { value: 'high', label: '紧急' },
         { value: 'urgent', label: '特急' }, { value: 'low', label: '较低' }],
        st.form.priority, function (v) { st.form.priority = v; })));

    var mid = UI.openModal({
      title: '新建卷宗', body: body, okText: '立案',
      onOk: function () {
        if (!st.form.deceased_id) { UI.toast('请选择亡魂', 'err'); return false; }
        API.createCase({
          deceased_id: Number(st.form.deceased_id),
          title: st.form.title,
          priority: st.form.priority
        }).then(function (c) {
          UI.toast('卷宗 ' + c.case_no + ' 已建立');
          var ps = pageState['cases'];
          if (ps && ps.load) ps.load(ps);
        }).catch(function (e) { UI.toast(e.message, 'err'); });
      }
    });
    return mid;
  }

  /* ---------------- 列表页 ---------------- */
  global.PAGES.cases = {
    name: '阴司卷宗',
    sub: 'Case File',
    openDetail: openDetail,
    render: function (ps, st) {
      if (!ps.loaded && !ps.busy) { ps.busy = true; this.load(ps); }
      ps.filter = ps.filter || { status: '', keyword: '', page: 1 };
      if (ps.params && ps.params.keyword !== undefined) {
        ps.filter.keyword = ps.params.keyword;
        ps.params = null;
        this.load(ps);
      }
      var list = ps.list || [];

      return [
        h('div', { class: 'card' },
          h('div', { class: 'btn-row', style: { marginBottom: '14px', alignItems: 'center' } },
            h('div', { class: 'search-box' },
              h('span', { style: { color: 'var(--text-3)' } }, '⌕'),
              h('input', {
                placeholder: '卷宗编号 / 标题',
                value: ps.filter.keyword,
                onInput: function (e) { ps.filter.keyword = e.target.value; },
                onKeyDown: function (e) {
                  if (e.key === 'Enter') { ps.filter.page = 1; global.PAGES.cases.load(ps); }
                }
              })),
            UI.select([{ value: '', label: '全部状态' }].concat(
              STATUS_CODES.map(function (s) { return { value: s, label: UI.statusText(s) }; })),
              ps.filter.status, function (v) { ps.filter.status = v; ps.filter.page = 1; global.PAGES.cases.load(ps); }),
            UI.btn('查询', 'sm', function () { ps.filter.page = 1; global.PAGES.cases.load(ps); }),
            h('div', { style: { marginLeft: 'auto' } }),
            UI.btn('＋ 新建卷宗', 'primary sm', openCreate),
            UI.btn('▶ 生成演示数据', 'sm ghost', function () {
              API.buildDemo().then(function (r) {
                UI.toast('已生成 ' + (r.cases || []).length + ' 个演示卷宗');
                global.PAGES.cases.load(ps);
              }).catch(function (e) { UI.toast(e.message, 'err'); });
            })),

          list.length ? h('div', { class: 'tbl-wrap' },
            h('table', null,
              h('thead', null, h('tr', null,
                ['卷宗编号', '标题', '亡魂', '功德/罪业', '状态', '风险', '进度', '当前节点', '立案时间', '']
                  .map(function (t) { return h('th', null, t); }))),
              h('tbody', null, list.map(function (c) {
                return h('tr', null,
                  h('td', null, h('span', { class: 'mono', style: { color: 'var(--gold-2)' } }, c.case_no)),
                  h('td', { style: { color: 'var(--text)', maxWidth: '260px' } }, c.title),
                  h('td', null, c.deceased_name || '—'),
                  h('td', null, (c.deceased_merit || 0) + ' / ' + (c.deceased_sin || 0)),
                  h('td', null, UI.statusTag(c.status)),
                  h('td', null, UI.riskTag(c.risk_level)),
                  h('td', null, UI.progress(c.progress, 60)),
                  h('td', null, NODE_NAME[c.current_node] || '—'),
                  h('td', { style: { fontSize: '12px' } }, Mini.fmtTime(c.created_at)),
                  h('td', null, UI.btn('详情', 'sm ghost', function () { openDetail(c.id); })));
              }))))
            : UI.empty(ps.busy ? '加载中…' : '暂无卷宗，可点击「生成演示数据」', '⚖'),

          UI.pagination(ps.filter.page, ps.size || 10, ps.total || 0, function (p) {
            ps.filter.page = p; global.PAGES.cases.load(ps);
          }))
      ];
    },

    load: function (ps) {
      ps.busy = true;
      ps.filter = ps.filter || { status: '', keyword: '', page: 1 };
      return API.cases({
        status: ps.filter.status, keyword: ps.filter.keyword,
        page: ps.filter.page, size: 10
      }).then(function (d) {
        ps.list = d.list || [];
        ps.total = d.total || 0;
        ps.size = d.size || 10;
        ps.loaded = true;
      }).catch(function (e) { UI.toast(e.message, 'err'); })
        .then(function () { ps.busy = false; });
    }
  };
})(window);
