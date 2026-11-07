/* ============================================================
 * 页面 · 亡魂名录（善恶簿）
 * ============================================================ */
(function (global) {
  'use strict';
  var h = global.h, UI = global.UI, API = global.API;

  function levelOf(merit) {
    if (merit >= 80) return '甲等';
    if (merit >= 60) return '乙等';
    if (merit >= 40) return '丙等';
    if (merit >= 20) return '丁等';
    return '戊等';
  }

  function editDialog(row, onDone) {
    var st = Mini.reactive({
      f: {
        name: row.name || '', gender: row.gender || '男', birth_year: row.birth_year || 0,
        death_date: row.death_date || '', cause_of_death: row.cause_of_death || '',
        hometown: row.hometown || '', merit_score: row.merit_score || 0,
        sin_score: row.sin_score || 0, life_summary: row.life_summary || ''
      }, busy: false
    });

    function bind(key, type) {
      return {
        value: st.f[key],
        onInput: function (e) {
          st.f[key] = type === 'number' ? Number(e.target.value || 0) : e.target.value;
        }
      };
    }

    var body = h('div', null,
      h('div', { class: 'form-row' },
        UI.field('姓名', h('input', bind('name'))),
        UI.field('性别', UI.select(['男', '女'], st.f.gender, function (v) { st.f.gender = v; }))),
      h('div', { class: 'form-row' },
        UI.field('生年', h('input', Object.assign({ type: 'number' }, bind('birth_year', 'number')))),
        UI.field('卒日', h('input', bind('death_date'), '如 2026-10-20'))),
      h('div', { class: 'form-row' },
        UI.field('功德分', h('input', Object.assign({ type: 'number' }, bind('merit_score', 'number'))),
          '当前分档：' + levelOf(st.f.merit_score)),
        UI.field('罪业分', h('input', Object.assign({ type: 'number' }, bind('sin_score', 'number'))))),
      UI.field('死因', h('input', bind('cause_of_death'))),
      UI.field('籍贯', h('input', bind('hometown'))),
      UI.field('生平概要', h('textarea', bind('life_summary'))));

    return UI.openModal({
      title: '编辑亡魂档案 · ' + (row.name || ''),
      body: body, okText: '保存',
      onOk: function () {
        if (st.busy) return false;
        st.busy = true;
        var payload = Object.assign({}, st.f, { merit_level: levelOf(st.f.merit_score) });
        API.updateDeceased(row.id, payload)
          .then(function () { UI.toast('档案已更新'); onDone && onDone(); })
          .catch(function (e) { UI.toast(e.message, 'err'); })
          .then(function () { st.busy = false; });
      }
    });
  }

  function createDialog(onDone) {
    var st = Mini.reactive({
      f: { name: '', gender: '男', birth_year: 0, death_date: '', cause_of_death: '',
           hometown: '', merit_score: 50, sin_score: 0, life_summary: '' }, busy: false
    });
    function bind(key, type) {
      return {
        value: st.f[key],
        onInput: function (e) { st.f[key] = type === 'number' ? Number(e.target.value || 0) : e.target.value; }
      };
    }
    var body = h('div', null,
      h('div', { class: 'form-row' },
        UI.field('姓名', h('input', Object.assign({ placeholder: '亡魂姓名' }, bind('name'))), '必填'),
        UI.field('性别', UI.select(['男', '女'], st.f.gender, function (v) { st.f.gender = v; }))),
      h('div', { class: 'form-row' },
        UI.field('功德分', h('input', Object.assign({ type: 'number' }, bind('merit_score', 'number'))),
          '自动分档：' + levelOf(st.f.merit_score)),
        UI.field('罪业分', h('input', Object.assign({ type: 'number' }, bind('sin_score', 'number'))))),
      h('div', { class: 'form-row' },
        UI.field('死因', h('input', bind('cause_of_death'))),
        UI.field('籍贯', h('input', bind('hometown')))),

      UI.field('生平概要', h('textarea', Object.assign({ placeholder: '记录其一生善恶事迹，供判官量刑参考' }, bind('life_summary')))));

    return UI.openModal({
      title: '新建亡魂档案', body: body, okText: '登记',
      onOk: function () {
        if (!st.f.name.trim()) { UI.toast('姓名不能为空', 'err'); return false; }
        if (st.busy) return false;
        st.busy = true;
        API.createDeceased(st.f)
          .then(function () { UI.toast('亡魂已入善恶簿'); onDone && onDone(); })
          .catch(function (e) { UI.toast(e.message, 'err'); })
          .then(function () { st.busy = false; });
      }
    });
  }

  function detailDialog(id) {
    var st = Mini.reactive({ d: null, busy: true });
    var body = document.createElement('div');
    body.appendChild(Mini.toDom(h('div', { class: 'empty' }, UI.spinner(), ' 加载中…')));
    var mid = UI.openModal({ title: '亡魂档案', body: body, footer: false });
    API.deceasedDetail(id).then(function (d) { st.d = d; paint(); })
      .catch(function (e) { UI.toast(e.message, 'err'); });

    function paint() {
      if (!body.isConnected) return;
      body.innerHTML = '';
      var d = st.d;
      if (!d) { body.appendChild(Mini.toDom(h('div', { class: 'empty' }, '加载中…'))); return; }
      body.appendChild(Mini.toDom(h('div', null,
        h('div', { style: { display: 'flex', gap: '10px', alignItems: 'center', marginBottom: '12px' } },
          h('span', { style: { fontSize: '18px', color: 'var(--gold-2)' } }, d.name),
          UI.tag(d.gender, 'blue'), UI.tag(d.merit_level || levelOf(d.merit_score), 'gold'),
          UI.tag(d.status || '', 'gray')),
        UI.kv([
          ['生年', d.birth_year || '—'], ['卒日', d.death_date || '—'],
          ['死因', d.cause_of_death || '—'], ['籍贯', d.hometown || '—'],
          ['功德分', d.merit_score], ['罪业分', d.sin_score],
          ['登记时间', Mini.fmtTime(d.created_at, true)]
        ]),
        h('div', { style: { marginTop: '12px', fontSize: '12px', color: 'var(--text-3)' } }, '生平概要'),
        h('div', { class: 'mono', style: { marginTop: '4px', color: 'var(--text-2)', lineHeight: 1.8 } }, d.life_summary || '—'),
        h('div', { class: 'card-title', style: { marginTop: '16px' } }, '涉及卷宗'),
        (d.cases || []).length
          ? (d.cases || []).map(function (c) {
            return h('div', { style: { display: 'flex', gap: '8px', padding: '5px 0', fontSize: '12px', alignItems: 'center' } },
              h('span', { class: 'mono', style: { color: 'var(--gold-2)' } }, c.case_no),
              h('span', null, c.title),
              UI.statusTag(c.status),
              h('span', { style: { marginLeft: 'auto', color: 'var(--text-3)' } }, Mini.fmtTime(c.created_at)));
          })
          : UI.empty('暂无卷宗', '⚖'),
        h('div', { class: 'card-title', style: { marginTop: '16px' } }, '相关证据'),
        (d.evidence || []).length
          ? (d.evidence || []).map(function (e) {
            return h('div', { style: { fontSize: '12px', color: 'var(--text-2)', padding: '4px 0' } },
              UI.tag(e.evi_type, 'blue'), ' ' + (e.file_name || ''));
          })
          : UI.empty('暂无证据', '🔍'))));
    }
  }

  global.PAGES.deceased = {
    name: '亡魂名录',
    sub: '善恶簿',
    render: function (ps) {
      if (!ps.loaded && !ps.busy) { ps.busy = true; this.load(ps); }
      ps.keyword = ps.keyword || '';
      var list = ps.list || [];
      var self = this;

      return [
        h('div', { class: 'card' },
          h('div', { class: 'btn-row', style: { marginBottom: '14px', alignItems: 'center' } },
            h('div', { class: 'search-box' },
              h('span', { style: { color: 'var(--text-3)' } }, '⌕'),
              h('input', {
                placeholder: '姓名 / 籍贯',
                value: ps.keyword,
                onInput: function (e) { ps.keyword = e.target.value; },
                onKeyDown: function (e) { if (e.key === 'Enter') self.load(ps); }
              })),
            UI.btn('查询', 'sm', function () { self.load(ps); }),
            h('div', { style: { marginLeft: 'auto' } }),
            UI.btn('＋ 登记亡魂', 'primary sm', function () {
              createDialog(function () { self.load(ps); });
            })),

          list.length ? h('div', { class: 'tbl-wrap' },
            h('table', null,
              h('thead', null, h('tr', null,
                ['姓名', '性别', '功德分', '罪业分', '分档', '死因', '籍贯', '状态', '登记时间', '操作']
                  .map(function (t) { return h('th', null, t); }))),
              h('tbody', null, list.map(function (r) {
                var color = r.merit_score >= 60 ? 'green' : r.merit_score >= 40 ? 'gold' : 'red';
                return h('tr', null,
                  h('td', null, h('span', { style: { color: 'var(--text)' } }, r.name)),
                  h('td', null, r.gender || '—'),
                  h('td', null, UI.tag(String(r.merit_score), color)),
                  h('td', null, UI.tag(String(r.sin_score), r.sin_score >= 50 ? 'red' : 'gray')),
                  h('td', null, r.merit_level || levelOf(r.merit_score)),
                  h('td', { style: { maxWidth: '180px' } }, r.cause_of_death || '—'),
                  h('td', null, r.hometown || '—'),
                  h('td', null, UI.tag(r.status || '', 'blue')),
                  h('td', { style: { fontSize: '12px' } }, Mini.fmtTime(r.created_at)),
                  h('td', null,
                    h('div', { class: 'btn-row' },
                      UI.btn('详情', 'sm ghost', function () { detailDialog(r.id); }),
                      UI.btn('编辑', 'sm ghost', function () {
                        editDialog(r, function () { self.load(ps); });
                      }))));
              }))))
            : UI.empty('善恶簿中暂无亡魂', '👤'),

          h('div', { style: { marginTop: '10px', fontSize: '11px', color: 'var(--text-3)' } },
            '分档规则：功德 ≥80 甲等 · ≥60 乙等 · ≥40 丙等 · ≥20 丁等 · 其余戊等。' +
            '功德与罪业将作为判官量刑与六道轮回的核心依据。'))
      ];
    },

    load: function (ps) {
      ps.busy = true;
      return API.deceased({ keyword: ps.keyword || '' })
        .then(function (d) { ps.list = d.list || []; ps.loaded = true; })
        .catch(function (e) { UI.toast(e.message, 'err'); })
        .then(function () { ps.busy = false; });
    }
  };
})(window);
