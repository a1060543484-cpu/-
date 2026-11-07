/* ============================================================
 * 自然语言展示层 · Narrate
 * ------------------------------------------------------------
 * 为什么要有这一层：
 *   后端 agent_log 里存的是「机器友好」的东西 —— 工具名是 dot/snake_case、
 *   工具结果是 JSON 字典、节点与引擎是英文 key。以前前端直接 String(...)
 *   糊到页面上，人看到的就成了：
 *     ⚙ evidence.check_material → {"complete": false, "missing": [...]}
 *     thread_id=case-120-...，引擎=builtin-state-machine
 *   这不叫「透明」，这叫「把内部实现倒给用户看」。
 *
 * 这一层定死三条规矩（对应「不黑箱」）：
 *   1. 默认展示 = 一句人话，讲清「做了什么、结果如何、接下来会怎样」。
 *   2. 原始数据一个字节都不删，折叠在「查看技术详情」里，随时能核对。
 *      只想办案的人不被字典糊脸，想排查的人也没被没收证据。
 *   3. 翻不动的字段宁可不显示（留在详情里），绝不把 { } / = /
 *      snake_case 直接摆到界面上；历史脏日志也顺手洗干净。
 *
 * 数据形状兼容（重要）：
 *   /api/cases/{id}  返回的 agent_logs 是数据库原始行，tool_output 是 JSON 字符串；
 *   /api/agent/logs  返回前已 jloads，tool_input / tool_output 是对象。
 *   下面 parse() 把两种统一成对象，调用方不用关心来自哪个接口。
 *
 * 依赖：core.js 的全局 h()。本文件必须在 core.js 之后、页面脚本之前引入。
 * ============================================================ */
(function (global) {
  'use strict';
  var h = global.h;

  /* ---------------- 内部标识 → 中文 ---------------- */
  // 与后端 tools.py 的 @tool 注册表一一对应（那里有完整描述，这里取短动宾）
  var TOOL_LABEL = {
    'case.get': '查阅卷宗',
    'case.update': '更新卷宗进度',
    'case.note': '补记流转批注',
    'case.archive': '归档卷宗并回灌判例',
    'evidence.list': '清点证据材料',
    'evidence.check_material': '核对卷宗材料',
    'evidence.supplement_notice': '发出补证通知',
    'deceased.get': '查阅亡魂档案',
    'deceased.update_merit': '核算功德与罪业',
    'knowledge.search': '检索法典与判例',
    'law.match_crime': '比对罪名',
    'law.match_penalty': '匹配刑罚条款',
    'merit.calculate': '核算功德分',
    'judgment.create': '拟写判决书',
    'execution.create': '建立服刑记录',
    'execution.progress': '刷新服刑进度',
    'realm.match': '匹配六道归属',
    'reincarnation.create': '生成转世文书',
    'mengpo.register': '完成忘川登记',
    'approval.create': '提交阎王复核',
    'notification.send': '发送站内通知',
    'parse_file': '解析上传材料'
  };

  var NODE_LABEL = {
    intake: '阴差受理', judge: '判官审理', review: '阎王复核', execution: '刑罚执行',
    reincarnation: '六道轮回', mengpo: '孟婆登记', archive: '卷宗归档',
    chat: '对话建卷', END: '结案', end: '结案'
  };

  var ENGINE_LABEL = {
    'langgraph': 'LangGraph 编排引擎',
    'builtin-state-machine': '内置状态机（离线可用）',
    'tool': '规则工具',
    'error': '异常',
    'apscheduler替代': '定时巡检'
  };

  var MODEL_LABEL = {
    'tool': '规则工具', 'error': '异常', 'apscheduler替代': '定时巡检'
  };

  // 设置页「依赖降级矩阵」的英文 key，也别让人去猜 llm / vector_pg 是什么
  var DEPEND_LABEL = {
    llm: '大模型', vision: '图像识别', asr: '语音转写', embedding: '向量模型',
    langgraph: 'LangGraph 编排', apscheduler: '定时任务', tortoise: '异步 ORM',
    fastapi: '接口框架', vector_pg: '向量数据库', pymysql: 'MySQL 驱动'
  };
  function depend(key) { return DEPEND_LABEL[key] || key; }

  var FLOW_MODE_LABEL = {
    auto: '自动选择（优先 LangGraph，不可用则回退）',
    langgraph: '强制使用 LangGraph',
    builtin: '强制使用内置状态机'
  };
  function flowMode(key) { return FLOW_MODE_LABEL[key] || key; }

  var STATUS_LABEL = {
    pending: '待受理', accepted: '已受理', judging: '审理中', reviewing: '复核中',
    executing: '服刑中', reincarnating: '轮回中', completed: '已办结',
    suspended: '已挂起', rejected: '已驳回', running: '进行中', interrupted: '已中断',
    ok: '正常', fail: '异常'
  };

  var EVI_TYPE_LABEL = {
    text: '文字', image: '图像', audio: '音频', video: '影像',
    pdf: '文书', doc: '文书', ocr: '识别文本', file: '附件'
  };

  // 只有认识的结果字段才进人话；不认识的一律留在「技术详情」，不瞎猜
  var KEY_LABEL = {
    complete: '结论', missing: '缺少', evidence_count: '证据份数', evidence_types: '证据类型',
    crime: '认定罪名', law_article: '适用法条', risk_level: '风险等级',
    sentence_years: '刑期（年）', sentence_type: '量刑', penalty_desc: '刑罚',
    merit_score: '功德分', sin_score: '罪业分', realm: '投生六道', next_life: '来世',
    progress: '办理进度', status: '状态', current_node: '当前环节',
    remaining_days: '剩余天数', sentence_days: '刑期（天）', prison: '服刑地狱',
    note_id: '批注编号', approval_no: '审批单号', title: '标题', content: '内容',
    summary: '摘要', type: '类型', count: '数量', message: '说明', error: '原因',
    ok: '结果', success: '结果', evi_type: '证据类型', file_name: '文件名',
    updated: '已更新字段', archived_at: '归档时间'
  };

  // 纯编号类字段对人没有信息量，不必出现在句子里（详情里仍有）
  var SKIP_KEYS = { case_id: 1, id: 1, deceased_id: 1, judgment_id: 1, user_id: 1 };

  /* ---------------- 基础工具 ---------------- */
  function parse(v) {
    if (v === null || v === undefined || v === '') return null;
    if (typeof v === 'object') return v;
    var s = String(v).trim();
    if (!s) return null;
    if (s.charAt(0) !== '{' && s.charAt(0) !== '[') return { _text: s };
    try { return JSON.parse(s); } catch (e) { return { _text: s }; }
  }

  function pretty(v) {
    if (v === null || v === undefined) return '';
    if (typeof v === 'string') return v;
    try { return JSON.stringify(v, null, 2); } catch (e) { return String(v); }
  }

  function toolLabel(name) { return TOOL_LABEL[name] || (name ? '业务操作' : ''); }
  function node(name) { return NODE_LABEL[name] || name || '—'; }
  function eviType(t) { return EVI_TYPE_LABEL[t] || String(t); }

  function engine(name) {
    var s = String(name || '');
    return ENGINE_LABEL[s.toLowerCase()] || s || '未识别';
  }

  function model(name) {
    var s = String(name || '');
    if (!s) return '规则引擎';
    if (MODEL_LABEL[s]) return MODEL_LABEL[s];
    if (ENGINE_LABEL[s.toLowerCase()]) return ENGINE_LABEL[s.toLowerCase()];
    return s;   // 真模型名（如 deepseek-chat）本来就该给人看，原样保留
  }

  /* ---------------- 文本清洗（含历史脏日志） ---------------- */
  // 老版本把内部标识直接写进 thought，例如：
  //   thread_id=case-120-DF-20261104-0003，引擎=builtin-state-machine
  // 这类整串都是标识符的，直接换成人话；混在中文句子里的做逐项替换。
  function thought(text) {
    var s = String(text || '');
    if (!s) return '';
    var tid = /thread_id\s*=\s*([^\s，,;；]+)/.exec(s);
    var eng = /(?:引擎|engine)\s*=\s*([^\s，,;；]+)/.exec(s);
    if (tid && eng) {
      return '本次办案由' + engine(eng[1]) + '接手，卷宗的流转轨迹已单独存档，' +
        '中途挂起或服务重启后都能从原处继续。';
    }
    return s
      .replace(/thread_id\s*=\s*[^\s，,;；]+/g, '流转轨迹')
      .replace(/continue_flow/g, '续跑流程')
      .replace(/(?:引擎|engine)\s*=\s*([^\s，,;；]+)/g, function (_, n) { return '引擎为' + engine(n); })
      .replace(/builtin-state-machine/g, '内置状态机')
      .replace(/langgraph/gi, 'LangGraph 编排引擎')
      .replace(/apscheduler替代/g, '定时巡检')
      .replace(/checkpoint/gi, '检查点')
      .replace(/[，,]\s*[，,]/g, '，');
  }

  // action 里最常见的代码味：「调用工具 evidence.check_material」
  function action(text) {
    var s = String(text || '');
    var m = /^调用工具\s+([\w.]+)$/.exec(s);
    if (m) return toolLabel(m[1]);
    return s.replace(/调用工具\s+([\w.]+)/g, function (_, n) { return toolLabel(n); })
      .replace(/builtin-state-machine/g, '内置状态机')
      .replace(/langgraph/gi, 'LangGraph 编排引擎');
  }

  /* ---------------- 字典 → 人话 ---------------- */
  function valueText(key, val) {
    if (val === null || val === undefined || val === '') return '无';
    if (key === 'complete') return val ? '材料齐备' : '材料不齐';
    if (key === 'ok' || key === 'success') return val ? '成功' : '未成功';
    if (key === 'evidence_types' || key === 'missing' || key === 'updated') {
      if (!Array.isArray(val)) return String(val);
      if (!val.length) return '无';
      return val.map(function (x) {
        return key === 'evidence_types' ? eviType(x) : (STATUS_LABEL[x] || String(x));
      }).join('、');
    }
    if (Array.isArray(val)) return val.length ? val.length + ' 项' : '无';
    if (typeof val === 'boolean') return val ? '是' : '否';
    if (typeof val === 'object') return '含 ' + Object.keys(val).length + ' 项信息';
    if (key === 'status' || key === 'current_node') {
      return STATUS_LABEL[val] || NODE_LABEL[val] || String(val);
    }
    var t = String(val);
    return t.length > 60 ? t.slice(0, 60) + '…' : t;
  }

  // 通用兜底：把认识的字段拼成中文短句，最多 4 项，避免又变回「一坨」
  function summarize(obj) {
    if (!obj || typeof obj !== 'object') return '';
    var parts = [];
    Object.keys(obj).forEach(function (k) {
      if (SKIP_KEYS[k] || k.charAt(0) === '_' || !KEY_LABEL[k]) return;
      parts.push(KEY_LABEL[k] + '：' + valueText(k, obj[k]));
    });
    if (!parts.length) return '';
    return parts.slice(0, 4).join('；') + '。';
  }

  // 用户吐槽的那条：材料校验结果，要说清差什么、现在有什么、下一步怎么办
  function materialText(o) {
    var types = Array.isArray(o.evidence_types)
      ? o.evidence_types.map(eviType).join('、') : '';
    var count = (o.evidence_count === null || o.evidence_count === undefined)
      ? '若干' : o.evidence_count;
    var base = '卷内现有 ' + count + ' 份证据' + (types ? '（' + types + '）' : '');
    if (o.complete === true) return '材料齐备，' + base + '，可以移交审理。';
    var miss = Array.isArray(o.missing) ? o.missing.filter(Boolean) : [];
    return '材料还差 ' + miss.length + ' 项' +
      (miss.length ? '：' + miss.join('、') : '') +
      '；' + base + '。补齐后重新提交即可继续办理。';
  }

  var FIXED_RESULT = {
    'case.update': '卷宗的状态与办理进度已更新。',
    'case.note': '已在卷宗里补记一条流转批注。',
    'case.archive': '卷宗已归档，本案判例已回灌知识库。',
    'evidence.supplement_notice': '已向阴差发出补证通知，卷宗暂时挂起，补齐材料后可继续办理。',
    'mengpo.register': '忘川登记已完成，亡魂饮汤忘却前尘。',
    'approval.create': '已生成待办审批单，流程挂起等待阎王复核。',
    'notification.send': '站内通知已发出。'
  };

  /**
   * 主入口：把一条工具调用翻译成一句人话。
   * @param {string} name 工具标识，如 evidence.check_material
   * @param {object|string} out tool_output，对象或 JSON 字符串都行
   * @returns {string} 自然语言句子；调用方再自行决定要不要挂「技术详情」
   */
  function tool(name, out) {
    var label = toolLabel(name);
    var obj = parse(out);
    if (!obj) return label ? '已执行「' + label + '」。' : '';
    if (Array.isArray(obj)) {
      return label + '：共查到 ' + obj.length + ' 条记录。';
    }
    if (obj._text !== undefined) {
      var txt = String(obj._text);
      // 万一后端给了没解析开的 JSON 文本，也别把它摆到界面上
      return label + '：' + (txt.charAt(0) === '{' ? '已完成，详见技术详情。' : txt);
    }
    if (obj.error) return label + '未能完成：' + obj.error;
    if (name === 'evidence.check_material') return label + '：' + materialText(obj);
    if (FIXED_RESULT[name]) return label + '：' + FIXED_RESULT[name];
    var s = summarize(obj);
    // 有些日志只有内部记录、没有工具名（如流程启动），别输出一个孤零零的冒号
    if (!label) return s || '已记下一条内部流转记录，详情可展开查看。';
    return label + '：' + (s || '已完成，可在技术详情里查看返回数据。');
  }

  /** 接口返回值 → 人话（设置页执行完弹的 toast 用，别再 JSON.stringify） */
  function result(v) {
    var o = parse(v);
    if (!o) return '操作已完成';
    if (o._text !== undefined) return String(o._text);
    var keys = ['message', 'msg', 'detail', 'note', 'summary'];
    for (var i = 0; i < keys.length; i++) {
      if (typeof o[keys[i]] === 'string' && o[keys[i]]) return o[keys[i]];
    }
    if (o.ok === true || o.success === true || o.status === 'ok') return '操作已完成';
    return summarize(o) || '操作已完成';
  }

  /**
   * 可折叠的原始技术详情：默认收起，点开才显示机器原始数据。
   * state 必须来自 Mini.reactive 树（如某条日志对象），set 才会触发重绘。
   */
  function details(raw, state, key) {
    var p = parse(raw);
    if (!p) return null;
    var text = (p._text !== undefined) ? String(p._text) : pretty(p);
    if (!text || !text.trim() || text === '{}' || text === '[]' || text === '""') return null;
    var k = key || '__narrateOpen';
    var st = state || {};
    return h('div', { style: { marginTop: '6px' } },
      h('span', {
        style: {
          fontSize: '11px', color: 'var(--text-3)', cursor: 'pointer',
          textDecoration: 'underline', userSelect: 'none'
        },
        onClick: function () { st[k] = !st[k]; }
      }, st[k] ? '收起技术详情' : '查看技术详情'),
      st[k] ? h('pre', {
        class: 'mono',
        style: {
          marginTop: '6px', maxHeight: '180px', overflow: 'auto', fontSize: '11px',
          color: 'var(--text-2)', whiteSpace: 'pre-wrap', wordBreak: 'break-all'
        }
      }, text) : null);
  }

  global.Narrate = {
    parse: parse, pretty: pretty, toolLabel: toolLabel, node: node, engine: engine,
    model: model, depend: depend, flowMode: flowMode, action: action, thought: thought, tool: tool,
    result: result, summarize: summarize, details: details
  };
})(window);
