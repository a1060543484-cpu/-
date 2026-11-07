/* ============================================================
 * 页面 · 证据勘验（多模态上传 + 解析结果）
 * ============================================================ */
(function (global) {
  'use strict';
  var h = global.h, UI = global.UI, API = global.API;

  /** 字节数 → 可读体积 */
  function fmtSize(n) {
    n = Number(n || 0);
    if (!n) return '—';
    if (n < 1024) return n + ' B';
    if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1048576).toFixed(2) + ' MB';
  }

  var TYPE_LABEL = { image: '图像', audio: '音频', pdf: 'PDF', doc: '文档', text: '文本', video: '视频' };
  var TYPE_ICON = { image: '🖼', audio: '🎙', pdf: '📕', doc: '📄', text: '📝', video: '🎬' };

  function uploadDialog(cases, presetCaseId, onDone) {
    var st = Mini.reactive({ case_id: presetCaseId || (cases[0] && cases[0].id) || 0, files: [], busy: false, result: null });
    var body = document.createElement('div');

    function paint() {
      if (!body.isConnected) return;
      body.innerHTML = '';
      body.appendChild(Mini.toDom(h('div', null,
        UI.field('关联卷宗', UI.select(
          cases.map(function (c) { return { value: c.id, label: c.case_no + ' · ' + c.title }; }),
          st.case_id, function (v) { st.case_id = Number(v); })),
        h('div', { class: 'upload-zone', onClick: function () { fileInput.click(); } },
          h('div', { style: { fontSize: '24px', marginBottom: '6px' } }, '⬆'),
          h('div', null, '点击选择文件，或将文件拖入此处'),
          h('div', { style: { fontSize: '11px', marginTop: '6px' } },
            '支持图像 / 音频 / PDF / 文本文档，上传后自动 OCR、ASR 与抽文解析')),
        st.files.length
          ? h('div', { style: { marginTop: '10px' } }, st.files.map(function (f) {
            return h('div', { class: 'chip' }, f.name + ' · ' + fmtSize(f.size));
          }))
          : null,
        st.result
          ? h('div', { style: { marginTop: '12px' } },
            h('div', { class: 'card-title' }, '解析结果'),
            (st.result || []).map(function (r) {
              return h('div', { style: { fontSize: '12px', color: 'var(--text-2)', padding: '4px 0' } },
                UI.tag(TYPE_LABEL[r.type] || r.type, 'blue'), ' ',
                r.file_name, ' · 提取 ' + r.text_len + ' 字');
            }))
          : null,
        h('div', { class: 'btn-row', style: { marginTop: '14px' } },
          UI.btn(st.busy ? '上传解析中…' : '开始上传', 'primary', doUpload, st.busy || !st.files.length)))));
    }

    var fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.multiple = true;
    fileInput.style.display = 'none';
    fileInput.addEventListener('change', function () {
      st.files = Array.prototype.slice.call(fileInput.files);
      paint();
    });
    body.appendChild(fileInput);
    body.addEventListener('dragover', function (e) { e.preventDefault(); });
    body.addEventListener('drop', function (e) {
      e.preventDefault();
      st.files = Array.prototype.slice.call(e.dataTransfer.files);
      paint();
    });

    function doUpload() {
      if (st.busy) return;
      st.busy = true;
      var fd = new FormData();
      fd.append('case_id', String(st.case_id));
      st.files.forEach(function (f) { fd.append('files', f, f.name); });
      API.upload('/api/evidence/upload', fd)
        .then(function (r) {
          st.result = r;
          UI.toast('已上传并解析 ' + r.length + ' 件证据');
          paint();
          onDone && onDone();
        })
        .catch(function (e) { UI.toast(e.message, 'err'); })
        .then(function () { st.busy = false; paint(); });
    }

    /* 先挂载弹窗再绘制：paint() 依赖 body.isConnected */
    var mid = UI.openModal({ title: '上传证据材料', body: body, footer: false });
    paint();
    return mid;
  }

  global.PAGES.evidence = {
    name: '证据勘验',
    sub: 'Multimodal',
    render: function (ps) {
      if (!ps.loaded && !ps.busy) { ps.busy = true; this.load(ps); }
      var list = ps.list || [];
      var self = this;

      return [
        h('div', { class: 'card' },
          h('div', { class: 'card-title' },
            '证据材料库',
            h('span', { class: 'sub' }, '共 ' + list.length + ' 件'),
            h('div', { class: 'spacer' }),
            UI.btn('⬆ 上传证据', 'primary sm', function () {
              API.cases({ size: 100 }).then(function (d) {
                uploadDialog(d.list || [], 0, function () { self.load(ps); });
              }).catch(function (e) { UI.toast(e.message, 'err'); });
            })),

          list.length ? h('div', { class: 'tbl-wrap' },
            h('table', null,
              h('thead', null, h('tr', null,
                ['类型', '文件名', '关联卷宗', '摘要', '大小', '状态', '上传时间', '操作']
                  .map(function (t) { return h('th', null, t); }))),
              h('tbody', null, list.map(function (e) {
                return h('tr', null,
                  h('td', null, UI.tag((TYPE_ICON[e.evi_type] || '') + ' ' + (TYPE_LABEL[e.evi_type] || e.evi_type), 'blue')),
                  h('td', { style: { color: 'var(--text)' } }, e.file_name || '—'),
                  h('td', null, h('span', { class: 'mono', style: { color: 'var(--gold-2)' } }, e.case_no || '#' + e.case_id)),
                  h('td', { style: { maxWidth: '320px', fontSize: '12px' } },
                    (e.summary || e.ocr_text || '—').slice(0, 80)),
                  h('td', { style: { fontSize: '12px' } }, fmtSize(e.size)),
                  h('td', null, UI.tag(e.status || '已解析', 'green')),
                  h('td', { style: { fontSize: '12px' } }, Mini.fmtTime(e.created_at)),
                  h('td', null, UI.btn('删除', 'sm danger', function () {
                    UI.confirm('删除证据', '确定删除「' + (e.file_name || '') + '」？', function () {
                      API.deleteEvidence(e.id).then(function () {
                        UI.toast('已删除'); self.load(ps);
                      }).catch(function (err) { UI.toast(err.message, 'err'); });
                    });
                  })));
              }))))
            : UI.empty('暂无证据材料，点击右上角上传', '🔍')),

        h('div', { class: 'card' },
          h('div', { class: 'card-title' }, '多模态解析链路'),
          h('div', { class: 'flow' },
            [['上传', '文件落盘 uploads/'], ['识别', '按扩展名判定类型'],
             ['解析', 'OCR / ASR / PDF 抽文'], ['入库', 'evidence 表 + 回灌知识库'],
             ['取证', 'Agent 引用并留痕']].map(function (x, i) {
              return h('div', { class: 'flow-node done' },
                h('div', { class: 'dot' }),
                h('div', { class: 'n' }, (i + 1) + ' ' + x[0]),
                h('div', { class: 'a' }, x[1]));
            })),
          h('div', { style: { marginTop: '10px', fontSize: '11px', color: 'var(--text-3)' } },
            '无视觉/语音模型 Key 时自动降级：图像走离线模拟 OCR、音频走离线模拟 ASR、PDF 用纯 zlib 解 FlateDecode 抽文，' +
            '保证全链路在零依赖、零密钥环境下仍可演示。'))
      ];
    },

    load: function (ps) {
      ps.busy = true;
      return API.evidence()
        .then(function (d) {
          var rows = d.list || [];
          return API.cases({ size: 100 }).then(function (cd) {
            var map = {};
            (cd.list || []).forEach(function (c) { map[c.id] = c.case_no; });
            rows.forEach(function (r) { r.case_no = map[r.case_id] || ''; });
            ps.list = rows;
            ps.loaded = true;
          });
        })
        .catch(function (e) { UI.toast(e.message, 'err'); })
        .then(function () { ps.busy = false; });
    }
  };
})(window);
