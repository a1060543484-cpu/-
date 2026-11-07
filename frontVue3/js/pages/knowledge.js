/* ============================================================
 * 页面 · 幽冥典籍（知识库 + RAG 检索测试）
 * ============================================================ */
(function (global) {
  'use strict';
  var h = global.h, UI = global.UI, API = global.API;

  var CATS = ['法典库', '判例库', '善恶簿库', '证据库', '冥律注疏'];

  function addDialog(onDone) {
    var st = Mini.reactive({ title: '', category: '法典库', content: '', busy: false });
    var body = h('div', null,
      UI.field('标题', h('input', {
        placeholder: '如《酆都法典》· 贪污罪条',
        value: st.title, onInput: function (e) { st.title = e.target.value; }
      })),
      UI.field('分类', UI.select(CATS, st.category, function (v) { st.category = v; })),
      UI.field('正文', h('textarea', {
        placeholder: '粘贴法条原文、判例记录或注疏内容，系统会自动切片并向量化',
        value: st.content,
        onInput: function (e) { st.content = e.target.value; },
        style: { minHeight: '180px' }
      }), '将按 220 字切片、40 字重叠，自动提取关键词与向量'));

    return UI.openModal({
      title: '新增典籍', body: body, okText: '入库',
      onOk: function () {
        if (!st.title.trim() || !st.content.trim()) { UI.toast('标题与正文不能为空', 'err'); return false; }
        if (st.busy) return false;
        st.busy = true;
        API.createKnowledge({ title: st.title.trim(), category: st.category, content: st.content.trim() })
          .then(function (r) {
            UI.toast('已入库，切片 ' + (r.chunks || 0) + ' 段');
            onDone && onDone();
          })
          .catch(function (e) { UI.toast(e.message, 'err'); })
          .then(function () { st.busy = false; });
      }
    });
  }

  function detailDialog(doc) {
    var body = h('div', null,
      UI.kv([['分类', doc.category || '—'], ['来源', doc.source || '—'],
             ['切片', (doc.chunk_count || 0) + ' 段'], ['入库时间', Mini.fmtTime(doc.created_at, true)]]),
      h('div', { style: { marginTop: '12px' } },
        h('pre', { class: 'mono', style: { maxHeight: '380px', overflowY: 'auto' } }, doc.content || '')));
    return UI.openModal({ title: doc.title || '典籍详情', body: body, footer: false });
  }

  global.PAGES.knowledge = {
    name: '幽冥典籍',
    sub: 'RAG Knowledge',
    render: function (ps) {
      if (!ps.loaded && !ps.busy) { ps.busy = true; this.load(ps); }
      ps.cat = ps.cat || '';
      ps.q = ps.q || '';
      var self = this;
      var list = ps.list || [];
      var stats = ps.stats || {};
      var hits = ps.hits || null;

      return [
        h('div', { class: 'grid grid-4', style: { marginBottom: '14px' } },
          h('div', { class: 'kpi' }, h('div', { class: 'label' }, '典籍篇数'),
            h('div', { class: 'value' }, String(stats.docs || 0)), h('div', { class: 'hint' }, '法典/判例/注疏')),
          h('div', { class: 'kpi b' }, h('div', { class: 'label' }, '知识切片'),
            h('div', { class: 'value' }, String(stats.chunks || 0)), h('div', { class: 'hint' }, '220 字/片 · 40 字重叠')),
          h('div', { class: 'kpi p' }, h('div', { class: 'label' }, '已向量化'),
            h('div', { class: 'value' }, String(stats.vectorized || 0)),
            h('div', { class: 'hint' }, 'Embedding 成功切片')),
          h('div', { class: 'kpi g' }, h('div', { class: 'label' }, '检索模式'),
            h('div', { class: 'value', style: { fontSize: '16px' } }, stats.mode || '—'),
            h('div', { class: 'hint' }, '向量优先 · 关键词兜底'))),

        h('div', { class: 'card' },
          h('div', { class: 'card-title' }, '🔎 RAG 检索测试', h('div', { class: 'spacer' }),
            UI.btn('＋ 新增典籍', 'primary sm', function () { addDialog(function () { self.load(ps); }); })),
          h('div', { class: 'btn-row', style: { marginBottom: '12px' } },
            h('div', { class: 'search-box', style: { flex: 1, maxWidth: '460px' } },
              h('span', { style: { color: 'var(--text-3)' } }, '⌕'),
              h('input', {
                placeholder: '输入问题，测试知识库命中，如「盗伐林木当判何罪」',
                value: ps.q,
                onInput: function (e) { ps.q = e.target.value; },
                onKeyDown: function (e) { if (e.key === 'Enter') doSearch(); }
              })),
            UI.select([{ value: '', label: '全部分类' }].concat(CATS.map(function (c) { return { value: c, label: c }; })),
              ps.cat, function (v) { ps.cat = v; }),
            UI.btn('检索', 'primary sm', doSearch)),

          hits
            ? (hits.length
              ? h('div', null, hits.map(function (r, i) {
                return h('div', {
                  style: {
                    padding: '10px 12px', background: 'var(--bg-3)', borderRadius: '8px',
                    marginBottom: '8px', borderLeft: '3px solid var(--gold)'
                  }
                },
                  h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '4px' } },
                    h('span', { style: { color: 'var(--gold-2)' } }, '#' + (i + 1)),
                    h('span', { style: { color: 'var(--text)', fontSize: '13px' } }, r.title),
                    UI.tag(r.category, 'blue'),
                    UI.tag('score ' + r.score, 'gold'),
                    UI.tag(r.mode, 'gray'),
                    h('span', { style: { marginLeft: 'auto', fontSize: '11px', color: 'var(--text-3)' } },
                      'chunk #' + r.chunk_id)),
                  h('div', { style: { fontSize: '12px', color: 'var(--text-2)', lineHeight: 1.8 } }, r.content));
              }))
              : UI.empty('未命中任何切片，试试其他问法', '📜'))
            : null),

        h('div', { class: 'card' },
          h('div', { class: 'card-title' }, '典籍目录', h('span', { class: 'sub' }, '共 ' + list.length + ' 篇'),
            h('div', { class: 'spacer' }),
            UI.select([{ value: '', label: '全部分类' }].concat(CATS.map(function (c) { return { value: c, label: c }; })),
              ps.cat, function (v) { ps.cat = v; self.load(ps); })),
          list.length ? h('div', { class: 'tbl-wrap' },
            h('table', null,
              h('thead', null, h('tr', null,
                ['标题', '分类', '来源', '切片', '入库时间', '操作'].map(function (t) { return h('th', null, t); }))),
              h('tbody', null, list.map(function (d) {
                return h('tr', null,
                  h('td', { style: { color: 'var(--text)', maxWidth: '340px' } }, d.title),
                  h('td', null, UI.tag(d.category, 'blue')),
                  h('td', { style: { fontSize: '12px' } }, d.source || '—'),
                  h('td', null, String(d.chunk_count || 0)),
                  h('td', { style: { fontSize: '12px' } }, Mini.fmtTime(d.created_at)),
                  h('td', null, h('div', { class: 'btn-row' },
                    UI.btn('查看', 'sm ghost', function () { detailDialog(d); }),
                    UI.btn('删除', 'sm danger', function () {
                      UI.confirm('删除典籍', '将同时删除其全部切片与向量，确定？', function () {
                        API.deleteKnowledge(d.id).then(function () {
                          UI.toast('已删除'); self.load(ps);
                        }).catch(function (e) { UI.toast(e.message, 'err'); });
                      });
                    }))));
              }))))
            : UI.empty('知识库为空', '📜'))
      ];

      function doSearch() {
        if (!ps.q.trim()) return UI.toast('请输入检索词', 'err');
        API.searchKnowledge(ps.q.trim(), ps.cat || undefined)
          .then(function (d) { ps.hits = d.hits || []; })
          .catch(function (e) { UI.toast(e.message, 'err'); });
      }
    },

    load: function (ps) {
      ps.busy = true;
      return API.knowledge({ category: ps.cat || '' })
        .then(function (d) { ps.list = d.list || []; ps.stats = d.stats || {}; ps.loaded = true; })
        .catch(function (e) { UI.toast(e.message, 'err'); })
        .then(function () { ps.busy = false; });
    }
  };
})(window);
