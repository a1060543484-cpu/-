/* ============================================================
 * api.js —— 后端接口封装（含 token、统一错误、SSE 流式对话）
 * ============================================================ */
(function (global) {
  'use strict';

  var TOKEN_KEY = 'difu.token';
  var USER_KEY = 'difu.user';

  var state = {
    token: localStorage.getItem(TOKEN_KEY) || '',
    user: null,
    base: ''                       // 与后端同源部署，无需跨域前缀
  };
  try { state.user = JSON.parse(localStorage.getItem(USER_KEY) || 'null'); } catch (e) { state.user = null; }

  function setAuth(token, user) {
    state.token = token || '';
    state.user = user || null;
    if (token) {
      localStorage.setItem(TOKEN_KEY, token);
      localStorage.setItem(USER_KEY, JSON.stringify(user || {}));
    } else {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(USER_KEY);
    }
  }

  function headers(extra) {
    var h = { 'Content-Type': 'application/json' };
    if (state.token) h['Authorization'] = 'Bearer ' + state.token;
    for (var k in (extra || {})) h[k] = extra[k];
    return h;
  }

  function qs(params) {
    if (!params) return '';
    var parts = [];
    for (var k in params) {
      var v = params[k];
      if (v === undefined || v === null || v === '' ) continue;
      parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(v));
    }
    return parts.length ? '?' + parts.join('&') : '';
  }

  function handle(res) {
    return res.text().then(function (txt) {
      var data;
      try { data = txt ? JSON.parse(txt) : {}; }
      catch (e) { throw new Error('服务返回格式异常：' + txt.slice(0, 120)); }
      if (res.status === 401) {
        setAuth('', null);
        if (global.App && App.onUnauthorized) App.onUnauthorized();
      }
      if (data.code !== 0 || res.status >= 400) {
        var err = new Error(data.msg || ('请求失败 ' + res.status));
        err.status = res.status;
        err.payload = data;
        throw err;
      }
      return data.data;
    });
  }

  function request(method, path, body, params) {
    var opt = { method: method, headers: headers() };
    if (body !== undefined) opt.body = JSON.stringify(body);
    return fetch(state.base + path + qs(params), opt).then(handle);
  }

  function upload(path, formData) {
    return fetch(state.base + path, {
      method: 'POST',
      headers: state.token ? { 'Authorization': 'Bearer ' + state.token } : {},
      body: formData
    }).then(handle);
  }

  var api = {
    state: state, setAuth: setAuth, qs: qs,
    get: function (p, params) { return request('GET', p, undefined, params); },
    post: function (p, body) { return request('POST', p, body || {}); },
    put: function (p, body) { return request('PUT', p, body || {}); },
    del: function (p) { return request('DELETE', p); },
    upload: upload,

    /* ---------------- 认证 ---------------- */
    login: function (username, password) {
      return request('POST', '/api/auth/login', { username: username, password: password })
        .then(function (d) { setAuth(d.token, d.user); return d; });
    },
    logout: function () {
      return request('POST', '/api/auth/logout', {}).catch(function () {}).then(function () { setAuth('', null); });
    },
    me: function () { return request('GET', '/api/auth/me'); },

    /* ---------------- 仪表盘 ---------------- */
    overview: function () { return request('GET', '/api/dashboard/overview'); },
    series: function () { return request('GET', '/api/dashboard/series'); },
    system: function () { return request('GET', '/api/dashboard/system'); },

    /* ---------------- 卷宗 ---------------- */
    cases: function (params) { return request('GET', '/api/cases', undefined, params); },
    caseDetail: function (id) { return request('GET', '/api/cases/' + id); },
    createCase: function (body) { return request('POST', '/api/cases', body); },
    runCase: function (id) { return request('POST', '/api/cases/' + id + '/run', {}); },
    expireCase: function (id) { return request('POST', '/api/cases/' + id + '/expire', {}); },
    archiveCase: function (id) { return request('POST', '/api/cases/' + id + '/archive', {}); },
    deleteCase: function (id) { return request('DELETE', '/api/cases/' + id); },
    addNote: function (id, content) { return request('POST', '/api/cases/' + id + '/note', { content: content }); },

    /* ---------------- 亡魂 ---------------- */
    deceased: function (params) { return request('GET', '/api/deceased', undefined, params); },
    deceasedDetail: function (id) { return request('GET', '/api/deceased/' + id); },
    createDeceased: function (body) { return request('POST', '/api/deceased', body); },
    updateDeceased: function (id, body) { return request('PUT', '/api/deceased/' + id, body); },

    /* ---------------- 证据 ---------------- */
    evidence: function (params) { return request('GET', '/api/evidence', undefined, params); },
    deleteEvidence: function (id) { return request('DELETE', '/api/evidence/' + id); },

    /* ---------------- 判决/服刑/轮回 ---------------- */
    judgments: function () { return request('GET', '/api/judgments'); },
    executions: function () { return request('GET', '/api/executions'); },
    reincarnations: function () { return request('GET', '/api/reincarnations'); },

    /* ---------------- 审批 ---------------- */
    approvals: function (params) { return request('GET', '/api/approvals', undefined, params); },
    decide: function (id, approve, opinion) {
      return request('POST', '/api/approvals/' + id + '/decide', { approve: approve, opinion: opinion });
    },

    /* ---------------- 知识库 ---------------- */
    knowledge: function (params) { return request('GET', '/api/knowledge', undefined, params); },
    createKnowledge: function (body) { return request('POST', '/api/knowledge', body); },
    deleteKnowledge: function (id) { return request('DELETE', '/api/knowledge/' + id); },
    searchKnowledge: function (q, category) {
      return request('GET', '/api/knowledge/search', undefined, { q: q, category: category });
    },

    /* ---------------- Agent / 日志 ---------------- */
    agentGraph: function () { return request('GET', '/api/agents/graph'); },
    agentTools: function () { return request('GET', '/api/agents/tools'); },
    agentLogs: function (params) { return request('GET', '/api/agents/logs', undefined, params); },
    auditLogs: function (params) { return request('GET', '/api/audit/logs', undefined, params); },
    toolCalls: function (params) { return request('GET', '/api/tools/calls', undefined, params); },

    /* ---------------- 通知 / 字典 / 模型 / 用户 ---------------- */
    notifications: function () { return request('GET', '/api/notifications'); },
    readNotification: function (id) { return request('POST', '/api/notifications/' + id + '/read', {}); },
    dict: function (params) { return request('GET', '/api/dict', undefined, params); },
    createDict: function (body) { return request('POST', '/api/dict', body); },
    deleteDict: function (id) { return request('DELETE', '/api/dict/' + id); },
    models: function () { return request('GET', '/api/models'); },
    updateModel: function (id, body) { return request('PUT', '/api/models/' + id, body); },
    activateModel: function (id) { return request('POST', '/api/models/' + id + '/activate', {}); },
    testModel: function (id) { return request('POST', '/api/models/' + id + '/test', {}); },
    users: function () { return request('GET', '/api/users'); },

    /* ---------------- 对话 ---------------- */
    chatHistory: function (sid) { return request('GET', '/api/chat/history', undefined, { session_id: sid }); },
    chatCase: function (body) { return request('POST', '/api/chat/case', body); },

    /* ---------------- 演示数据 ---------------- */
    seed: function () { return request('POST', '/api/demo/seed', {}); },
    buildDemo: function () { return request('POST', '/api/demo/build', {}); },
    /* 后端要求二次确认，防止误点 / 被 CSRF 触发一键清库 */
    resetDemo: function () { return request('POST', '/api/demo/reset', { confirm: 'RESET' }); }
  };

  /**
   * SSE 流式对话（POST + ReadableStream 手工解析）
   * onMeta(meta) / onDelta(text) / onDone(info) / onError(msg)
   */
  api.chatStream = function (payload, handlers) {
    var h = handlers || {};
    return fetch(state.base + '/api/chat/stream', {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify(payload)
    }).then(function (res) {
      if (!res.ok || !res.body) throw new Error('对话服务不可用（' + res.status + '）');
      var reader = res.body.getReader();
      var dec = new TextDecoder('utf-8');
      var buf = '';
      var stopped = false;

      function dispatch(block) {
        var ev = 'message', data = '';
        block.split('\n').forEach(function (line) {
          if (line.indexOf('event:') === 0) ev = line.slice(6).trim();
          else if (line.indexOf('data:') === 0) data += line.slice(5).trim();
        });
        if (!data) return;
        var obj;
        try { obj = JSON.parse(data); } catch (e) { obj = { text: data }; }
        if (ev === 'meta' && h.onMeta) h.onMeta(obj);
        else if (ev === 'delta' && h.onDelta) h.onDelta(obj.text || '');
        else if (ev === 'done' && h.onDone) h.onDone(obj);
        else if (ev === 'error' && h.onError) h.onError(obj.msg || '对话出错');
      }

      function pump() {
        return reader.read().then(function (r) {
          if (r.done || stopped) { if (h.onDone) h.onDone({}); return; }
          buf += dec.decode(r.value, { stream: true });
          var idx;
          while ((idx = buf.indexOf('\n\n')) >= 0) {
            var block = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            if (block.trim()) dispatch(block);
          }
          return pump();
        });
      }
      return pump();
    }).catch(function (e) {
      if (h.onError) h.onError(e.message || '网络异常');
    });
  };

  global.API = api;
})(window);
