/* ============================================================
 * 页面 · 阎王复核（待办审批）
 * ============================================================ */
(function (global) {
  'use strict';
  var h = global.h, UI = global.UI, API = global.API;

  function decideDialog(a, onDone) {
    var st = Mini.reactive({ opinion: a.status === 'pending' ? '' : (a.opinion || ''), busy: false });
    var body = h('div', null,
      UI.kv([
        ['审批单号', a.approval_no || '—'],
        ['类型', a.appr_type || '复核'],
        ['卷宗', a.case_no || '—'],
        ['标题', a.title || '—'],
        ['申请人', a.applicant || '—'],
        ['提请理由', a.reason || '—']
      ]),
      h('div', { style: { marginTop: '14px' } },
        UI.field('复核意见', h('textarea', {
          placeholder: '如：罪证确凿，准予执行；或：证据不足，退回重审。',
          value: st.opinion,
          onInput: function (e) { st.opinion = e.target.value; }
        }))),
      h('div', { class: 'btn-row' },
        UI.btn('✔ 批准执行', 'primary', function () { submit(true); }, st.busy),
        UI.btn('✘ 驳回重审', 'danger', function () { submit(false); }, st.busy)));

    function submit(approve) {
      if (st.busy) return;
      st.busy = true;
      API.decide(a.id, approve, st.opinion.trim())
        .then(function () {
          UI.toast(approve ? '已批准，流程继续' : '已驳回，退回判官重审');
          UI.closeModal(mid);
          onDone && onDone();
          App.loadNotify();
        })
        .catch(function (e) { UI.toast(e.message, 'err'); })
        .then(function () { st.busy = false; });
    }

    var mid = UI.openModal({ title: '阎王复核', body: body, footer: false });
  }

  global.PAGES.approvals = {
    name: '阎王复核',
    sub: 'Approval',
    render: function (ps) {
      if (!ps.loaded && !ps.busy) { ps.busy = true; this.load(ps); }
      ps.status = ps.status || 'pending';
      var list = ps.list || [];
      var pending = ps.pending || 0;

      return [
        h('div', { class: 'grid grid-3', style: { marginBottom: '14px' } },
          h('div', { class: 'kpi r' },
            h('div', { class: 'label' }, '待复核'), h('div', { class: 'value' }, String(pending)),
            h('div', { class: 'hint' }, '高风险卷宗需阎王裁决')),
          h('div', { class: 'kpi g' },
            h('div', { class: 'label' }, '已批准'),
            h('div', { class: 'value' }, String((ps.all || []).filter(function (x) { return x.status === 'approved'; }).length)),
            h('div', { class: 'hint' }, '进入刑罚执行')),
          h('div', { class: 'kpi b' },
            h('div', { class: 'label' }, '已驳回'),
            h('div', { class: 'value' }, String((ps.all || []).filter(function (x) { return x.status === 'rejected'; }).length)),
            h('div', { class: 'hint' }, '退回判官重审'))),

        h('div', { class: 'card' },
          UI.tabs([
            { v: 'pending', t: '待复核 (' + pending + ')' },
            { v: 'approved', t: '已批准' },
            { v: 'rejected', t: '已驳回' },
            { v: 'all', t: '全部' }
          ], ps.status, function (v) { ps.status = v; this.load(ps); }.bind(this)),

          list.length ? list.map(function (a) {
            var canDecide = a.can_decide;
            return h('div', {
              class: 'card',
              style: {
                background: 'var(--bg-3)', marginBottom: '10px',
                borderLeft: '3px solid ' + (a.status === 'pending' ? 'var(--orange)' : a.status === 'approved' ? 'var(--green)' : 'var(--red)')
              }
            },
              h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' } },
                h('span', { class: 'mono', style: { color: 'var(--gold-2)' } }, a.approval_no || ''),
                UI.tag(a.appr_type || '复核', 'orange'),
                UI.tag(a.status === 'pending' ? '待复核' : a.status === 'approved' ? '已批准' : '已驳回',
                  a.status === 'pending' ? 'orange' : a.status === 'approved' ? 'green' : 'red'),
                h('div', { style: { marginLeft: 'auto', fontSize: '11px', color: 'var(--text-3)' } },
                  Mini.fmtTime(a.created_at, true)),
                canDecide
                  ? UI.btn('去裁决', 'primary sm', function () {
                    decideDialog(a, function () { global.PAGES.approvals.load(ps); });
                  })
                  : null),
              h('div', { style: { marginTop: '8px', fontSize: '14px', color: 'var(--text)' } }, a.title || ''),
              h('div', { style: { marginTop: '4px', fontSize: '12px', color: 'var(--text-3)' } },
                '卷宗 ' + (a.case_no || '') + ' · 申请人 ' + (a.applicant || '—')),
              a.reason ? h('div', { style: { marginTop: '8px', fontSize: '12px', color: 'var(--text-2)', lineHeight: 1.7 } },
                '理由：' + a.reason) : null,
              a.opinion ? h('div', {
                style: {
                  marginTop: '8px', padding: '8px 10px', background: 'var(--bg-2)',
                  borderRadius: '6px', fontSize: '12px', borderLeft: '2px solid var(--gold)'
                }
              }, '阎王批复：' + a.opinion + '　— ' + (a.approver_name || '') + ' ' + Mini.fmtTime(a.decided_at)) : null);
          }) : UI.empty(ps.status === 'pending' ? '当前无待复核事项，阴司运转正常' : '暂无记录', '✍'))
      ];
    },

    load: function (ps) {
      ps.busy = true;
      return API.approvals()
        .then(function (d) {
          ps.all = d.list || [];
          ps.pending = d.pending || 0;
          var cur = ps.status || 'pending';
          ps.list = cur === 'all' ? ps.all : ps.all.filter(function (a) { return a.status === cur; });
          ps.loaded = true;
          App.st.notify.pendingApprovals = ps.pending;
        })
        .catch(function (e) { UI.toast(e.message, 'err'); })
        .then(function () { ps.busy = false; });
    }
  };
})(window);
