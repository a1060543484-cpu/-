/* ============================================================
 * 页面 · 阴阳设置（字典 / 模型配置 / 用户 / 演示数据）
 * ============================================================ */
(function (global) {
  'use strict';
  var h = global.h, UI = global.UI, API = global.API, Narrate = global.Narrate;

  var DICT_NAMES = {
    crime: '罪名', punishment: '刑罚', realm: '六道', jurisdiction: '辖区',
    merit_level: '功德等级', risk_level: '风险等级', case_status: '卷宗状态'
  };
  var PURPOSE = { text: '文本推理', chat: '对话推理', judge: '判官裁量', vision: '图像识别',
                  asr: '语音转写', embedding: '向量化' };

  function dictPanel(ps, self) {
    var groups = ps.groups || {};
    var types = Object.keys(groups);
    ps.dtype = ps.dtype || (types[0] || 'crime');

    var rows = groups[ps.dtype] || [];

    return h('div', { class: 'card' },
      h('div', { class: 'card-title' }, '📖 字典管理',
        h('span', { class: 'sub' }, '罪名 / 刑罚 / 六道 / 辖区 / 等级'),
        h('div', { class: 'spacer' }),
        UI.btn('＋ 新增字典项', 'primary sm', function () { dictAddDialog(ps.dtype, function () { self.load(ps); }); })),

      UI.tabs(types.map(function (t) { return { value: t, label: DICT_NAMES[t] || t }; }),
        ps.dtype, function (v) { ps.dtype = v; }),

      rows.length ? h('div', { class: 'tbl-wrap', style: { marginTop: '12px' } },
        h('table', null,
          h('thead', null, h('tr', null, ['Key', 'Value', '排序', '备注', '操作']
            .map(function (t) { return h('th', null, t); }))),
          h('tbody', null, rows.map(function (r) {
            return h('tr', null,
              h('td', { class: 'mono', style: { color: 'var(--gold-2)' } }, r.dict_key),
              h('td', { style: { color: 'var(--text)' } }, r.dict_value),
              h('td', null, String(r.sort || 0)),
              h('td', { style: { fontSize: '12px', color: 'var(--text-3)' } }, r.remark || '—'),
              h('td', null, UI.btn('删除', 'sm danger', function () {
                UI.confirm('删除字典项', '删除「' + r.dict_value + '」？业务数据不受影响。', function () {
                  API.deleteDict(r.id).then(function () { UI.toast('已删除'); self.load(ps); })
                    .catch(function (e) { UI.toast(e.message, 'err'); });
                });
              })));
          }))))
        : UI.empty('该分类暂无字典项', '📖'));
  }

  function dictAddDialog(dtype, onDone) {
    var st = Mini.reactive({ key: '', value: '', sort: 10, remark: '' });
    var body = h('div', null,
      UI.field('字典类型', h('input', { value: DICT_NAMES[dtype] || dtype, disabled: true })),
      UI.field('Key（英文标识）', h('input', {
        placeholder: '如 theft / 盗窃', value: st.key,
        onInput: function (e) { st.key = e.target.value; }
      })),
      UI.field('Value（中文名）', h('input', {
        placeholder: '如 盗窃罪', value: st.value,
        onInput: function (e) { st.value = e.target.value; }
      })),
      UI.field('排序', h('input', {
        type: 'number', value: String(st.sort),
        onInput: function (e) { st.sort = +e.target.value; }
      })),
      UI.field('备注', h('input', {
        placeholder: '选填', value: st.remark,
        onInput: function (e) { st.remark = e.target.value; }
      })));

    return UI.openModal({
      title: '新增字典项', body: body, okText: '保存',
      onOk: function () {
        if (!st.key.trim() || !st.value.trim()) { UI.toast('Key 与 Value 不能为空', 'err'); return false; }
        API.createDict({ dict_type: dtype, dict_key: st.key.trim(), dict_value: st.value.trim(),
                         sort: st.sort, remark: st.remark })
          .then(function () { UI.toast('已新增'); onDone && onDone(); })
          .catch(function (e) { UI.toast(e.message, 'err'); });
      }
    });
  }

  function modelEditDialog(m, onDone) {
    var st = Mini.reactive({
      name: m.name || '', provider: m.provider || '', base_url: m.base_url || '',
      model: m.model || '', api_key: '', temperature: m.temperature, max_tokens: m.max_tokens
    });
    var body = h('div', null,
      UI.field('名称', h('input', {
        value: st.name, onInput: function (e) { st.name = e.target.value; }
      })),
      UI.field('供应商', h('input', {
        value: st.provider, placeholder: 'DeepSeek / 通义 / OpenAI',
        onInput: function (e) { st.provider = e.target.value; }
      })),
      UI.field('Base URL', h('input', {
        value: st.base_url, placeholder: 'https://api.deepseek.com/v1',
        onInput: function (e) { st.base_url = e.target.value; }
      }), '兼容 OpenAI 协议，一般以 /v1 结尾'),
      UI.field('模型名', h('input', {
        value: st.model, placeholder: 'deepseek-chat',
        onInput: function (e) { st.model = e.target.value; }
      }), 'DeepSeek 可填 deepseek-chat（别名），或 deepseek-flash / deepseek-v4-pro'),
      UI.field('API Key', h('input', {
        type: 'password', value: '',
        placeholder: m.has_key ? '已配置（留空则不修改）' : 'sk-...',
        onInput: function (e) { st.api_key = e.target.value; }
      }), '留空表示沿用原有 Key；填写后立即生效，无需重启服务'),
      UI.field('温度', h('input', {
        type: 'number', step: '0.1', min: '0', max: '2', value: String(st.temperature),
        onInput: function (e) { st.temperature = +e.target.value; }
      }), '0 最确定、2 最发散；办案建议 0.2 ~ 0.4'),
      UI.field('最大 Token', h('input', {
        type: 'number', min: '1', value: String(st.max_tokens),
        onInput: function (e) { st.max_tokens = +e.target.value; }
      })));

    return UI.openModal({
      title: '编辑模型配置 · ' + (PURPOSE[m.purpose] || m.purpose), body: body, okText: '保存',
      onOk: function () {
        var payload = {
          name: st.name.trim(), provider: st.provider.trim(), base_url: st.base_url.trim(),
          model: st.model.trim(), temperature: st.temperature, max_tokens: st.max_tokens
        };
        if (st.api_key.trim()) payload.api_key = st.api_key.trim();
        API.updateModel(m.id, payload)
          .then(function (r) { UI.toast(r || '已保存'); onDone && onDone(); })
          .catch(function (e) { UI.toast(e.message, 'err'); });
      }
    });
  }

  function modelPanel(ps, self) {
    var list = ps.models || [];
    return h('div', { class: 'card' },
      h('div', { class: 'card-title' }, '🤖 模型配置',
        h('span', { class: 'sub' }, '未配置 Key 时全链路自动降级为本地规则引擎'),
        h('div', { class: 'spacer' }),
        UI.tag('已启用 ' + list.filter(function (m) { return m.is_active; }).length + ' 个', 'gold')),

      list.length ? h('div', { class: 'tbl-wrap' },
        h('table', null,
          h('thead', null, h('tr', null, ['用途', '名称', '供应商', '模型', 'Base URL', 'API Key', '温度', '状态', '最近测试', '操作']
            .map(function (t) { return h('th', null, t); }))),
          h('tbody', null, list.map(function (m) {
            return h('tr', null,
              h('td', null, UI.tag(PURPOSE[m.purpose] || m.purpose, 'blue'),
                m.is_active ? h('div', null, UI.tag('启用中', 'green')) : null),
              h('td', { style: { color: 'var(--text)' } }, m.name),
              h('td', { style: { fontSize: '12px' } }, m.provider || '—'),
              h('td', { class: 'mono', style: { fontSize: '12px', color: 'var(--gold-2)' } }, m.model),
              h('td', { style: { fontSize: '11px', color: 'var(--text-3)', maxWidth: '180px',
                                 overflow: 'hidden', textOverflow: 'ellipsis' } }, m.base_url || '—'),
              h('td', null, m.has_key
                ? h('span', { class: 'mono', style: { fontSize: '11px', color: 'var(--green)' } }, m.api_key)
                : UI.tag('未配置', 'gray')),
              h('td', null, String(m.temperature)),
              h('td', null, m.status === 'ok' ? UI.tag('可用', 'green')
                : m.status === 'fail' ? UI.tag('失败', 'red') : UI.tag('未测试', 'gray')),
              h('td', { style: { fontSize: '11px', color: 'var(--text-3)' } },
                m.last_test ? Mini.fmtTime(m.last_test, true) : '—'),
              h('td', null, h('div', { class: 'btn-row' },
                UI.btn('编辑', 'sm ghost', function () {
                  modelEditDialog(m, function () { self.load(ps); });
                }),
                UI.btn('启用', 'sm primary', function () {
                  API.activateModel(m.id).then(function (r) { UI.toast(r || '已启用'); self.load(ps); })
                    .catch(function (e) { UI.toast(e.message, 'err'); });
                }, m.is_active),
                UI.btn('测试', 'sm ghost', function () {
                  UI.toast('正在测试连通性…');
                  API.testModel(m.id).then(function (r) {
                    UI.toast(r && r.ok ? '连通正常' : ('失败：' + ((r && r.msg) || '未知')), r && r.ok ? 'ok' : 'err');
                    self.load(ps);
                  }).catch(function (e) { UI.toast(e.message, 'err'); });
                }))));
          }))))
        : UI.empty('暂无模型配置', '🤖'),

      h('div', { style: { marginTop: '12px', fontSize: '11px', color: 'var(--text-3)', lineHeight: 1.8 } },
        '降级策略：配置 API Key 后自动调用大模型做推理与裁量；未配置时由内置规则引擎兜底，' +
        '罪名匹配、量刑、功德折算、六道判定全部可离线运行，仅「自然语言生成」与「图片 OCR」降级。'));
  }

  function userPanel(ps) {
    var list = ps.users || [];
    return h('div', { class: 'card' },
      h('div', { class: 'card-title' }, '👥 阴司人员',
        h('span', { class: 'sub' }, '六类角色 · 行级数据权限'),
        h('div', { class: 'spacer' }), UI.tag(list.length + ' 人', 'gold')),
      list.length ? h('div', { class: 'tbl-wrap' },
        h('table', null,
          h('thead', null, h('tr', null, ['ID', '账号', '姓名', '角色', '辖区', '状态', '创建时间']
            .map(function (t) { return h('th', null, t); }))),
          h('tbody', null, list.map(function (u) {
            return h('tr', null,
              h('td', null, '#' + u.id),
              h('td', { class: 'mono', style: { color: 'var(--gold-2)' } }, u.username),
              h('td', { style: { color: 'var(--text)' } }, u.real_name),
              h('td', null, UI.tag(u.role_name || u.role_code, 'purple')),
              h('td', null, u.jurisdiction_id ? '#' + u.jurisdiction_id : '—'),
              h('td', null, UI.tag(u.status === 'active' ? '在职' : (u.status || '—'),
                u.status === 'active' ? 'green' : 'gray')),
              h('td', { style: { fontSize: '12px', color: 'var(--text-3)' } }, Mini.fmtTime(u.created_at)));
          }))))
        : UI.empty('暂无用户', '👥'));
  }

  function demoPanel(ps, self) {
    var steps = [
      ['seed', '初始化基础数据', '角色、辖区、6 名阴司、字典、模型配置、12 篇典籍、3 名亡魂', API.seed],
      ['build', '生成演示卷宗', '建 3 个卷宗并跑完整流程（受理→判决→执行/复核→轮回→归档）', API.buildDemo],
      ['reset', '重置全部数据', '清库重建 + 重跑演示，用于反复演示', API.resetDemo]
    ];
    return h('div', { class: 'card' },
      h('div', { class: 'card-title' }, '🧪 演示数据'),
      h('div', { style: { fontSize: '12px', color: 'var(--text-3)', marginBottom: '12px', lineHeight: 1.8 } },
        '所有操作幂等，可反复执行。重置会清空全部业务数据（含日志），仅保留表结构。'),
      steps.map(function (s) {
        return h('div', {
          style: {
            display: 'flex', alignItems: 'center', gap: '12px', padding: '12px',
            background: 'var(--bg-3)', borderRadius: '8px', marginBottom: '8px'
          }
        },
          h('div', { style: { flex: 1 } },
            h('div', { style: { color: 'var(--text)', fontSize: '13px' } }, s[1]),
            h('div', { style: { fontSize: '11px', color: 'var(--text-3)', marginTop: '2px' } }, s[2])),
          UI.btn('执行', s[0] === 'reset' ? 'sm danger' : 'sm primary', function () {
            var run = function () {
              ps.busy = true;
              UI.toast('执行中…');
              s[3]().then(function (r) {
                UI.toast('完成：' + Narrate.result(r));
                self.load(ps);
                if (App.loadNotify) App.loadNotify();
              }).catch(function (e) { UI.toast(e.message, 'err'); })
                .then(function () { ps.busy = false; });
            };
            if (s[0] === 'reset') UI.confirm('重置全部数据',
              '将清空所有卷宗、日志与上传文件记录（仅管理员可执行；执行前自动备份到 FastAPI/backups/），确定继续？',
              run, '确认重置');
            else run();
          }, ps.busy));
      }));
  }

  global.PAGES.settings = {
    name: '阴阳设置',
    sub: 'System',
    render: function (ps) {
      if (!ps.loaded && !ps.busy) { ps.busy = true; this.load(ps); }
      ps.tab = ps.tab || 'dict';
      var self = this;
      var T = [
        { value: 'dict', label: '字典管理' },
        { value: 'model', label: '模型配置' },
        { value: 'user', label: '人员权限' },
        { value: 'demo', label: '演示数据' }
      ];

      var sys = ps.system || {};
      var degrade = sys.degrade || {};

      return [
        h('div', { class: 'card', style: { marginBottom: '14px' } },
          h('div', { class: 'card-title' }, '⚙ 运行环境'),
          h('div', { class: 'grid grid-4' },
            h('div', { class: 'kpi' }, h('div', { class: 'label' }, 'Python'),
              h('div', { class: 'value', style: { fontSize: '15px' } }, sys.python || '—'),
              h('div', { class: 'hint' }, '标准库运行')),
            h('div', { class: 'kpi g' }, h('div', { class: 'label' }, '流程引擎'),
              h('div', { class: 'value', style: { fontSize: '15px' } },
                sys.engine ? Narrate.engine(sys.engine) : '—'),
              h('div', { class: 'hint' }, sys.engine_note || '如实报告实际执行引擎')),
            h('div', { class: 'kpi b' }, h('div', { class: 'label' }, '数据库'),
              h('div', { class: 'value', style: { fontSize: '15px' } }, sys.db || '—'),
              h('div', { class: 'hint' }, 'sqlite / mysql')),
            h('div', { class: 'kpi p' }, h('div', { class: 'label' }, '工具数'),
              h('div', { class: 'value' }, String(sys.tools || 0)),
              h('div', { class: 'hint' }, '代码确定性调用')))),
        h('div', { class: 'card', style: { marginBottom: '14px' } },
          h('div', { class: 'card-title' }, '依赖降级矩阵',
            h('span', { class: 'sub' }, '缺失依赖自动降级，功能不中断')),
          h('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap' } },
            /* 只渲染布尔字段；engine / langgraph_used 等字符串字段单独说明，
               否则会被当 truthy 显示成「降级」，把人误导。 */
            Object.keys(degrade).filter(function (k) {
              return typeof degrade[k] === 'boolean';
            }).map(function (k) {
              return UI.tag(Narrate.depend(k) + (degrade[k] ? ' · 降级' : ' · 已安装'),
                degrade[k] ? 'orange' : 'green');
            }).concat([
              UI.tag('实际引擎 · ' + Narrate.engine(degrade.engine || 'builtin-state-machine'), 'gray'),
              UI.tag('LangGraph · ' + (degrade.langgraph_used ? '执行中'
                : (degrade.langgraph_installed ? '已安装但未使用' : '未安装')), 'gray')
            ]))),

        h('div', { class: 'card' },
          UI.tabs(T, ps.tab, function (v) { ps.tab = v; }),
          h('div', { style: { marginTop: '14px' } },
            ps.tab === 'dict' ? dictPanel(ps, self)
              : ps.tab === 'model' ? modelPanel(ps, self)
                : ps.tab === 'user' ? userPanel(ps)
                  : demoPanel(ps, self)))
      ];
    },

    load: function (ps) {
      ps.busy = true;
      return Promise.all([API.dict(), API.models(), API.users(), API.system()])
        .then(function (r) {
          ps.groups = (r[0] || {}).groups || {};
          ps.dictList = (r[0] || {}).list || [];
          ps.models = (r[1] || {}).list || [];
          ps.users = (r[2] || {}).list || [];
          ps.system = r[3] || {};
          ps.loaded = true;
        })
        .catch(function (e) { UI.toast(e.message, 'err'); })
        .then(function () { ps.busy = false; });
    }
  };
})(window);
