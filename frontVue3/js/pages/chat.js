/* ============================================================
 * 页面 · 问案判官（SSE 流式对话 + RAG 命中溯源）
 * ============================================================ */
(function (global) {
  'use strict';
  var h = global.h, UI = global.UI, API = global.API;

  var SESSION = 'web-' + Date.now().toString(36);

  var QUICK = [
    '盗伐林木者依《酆都法典》当判何罪？',
    '侵吞赈灾粮款如何量刑？',
    '功德分与罪业分如何折算刑期？',
    '什么情况下会判入地狱道？',
    '自首能否减罪？减几等？'
  ];


  /* 大模型的答复是 Markdown（**加粗**、- 列表、`代码`、# 标题），
     直接塞进 textContent 会把星号和连字符原样显示出来。
     这里做一个够用的子集渲染：先转义 HTML，再替换 Markdown 标记，
     最后用 innerHTML 挂到气泡上。转义在前，所以模型输出里的 <script> 之类不会生效。 */
  function esc(t) {
    return String(t == null ? '' : t)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function md(t) {
    var lines = esc(t).replace(/\r\n/g, '\n').split('\n');
    var out = [], list = [], inCode = false, codeBuf = [];

    function flushList() {
      if (!list.length) return;
      out.push('<ul>' + list.join('') + '</ul>');
      list = [];
    }

    for (var i = 0; i < lines.length; i++) {
      var ln = lines[i];
      if (/^\s*```/.test(ln)) {                       /* 代码块整体包一层 <pre> */
        if (inCode) { out.push('<pre class="md-code">' + codeBuf.join('\n') + '</pre>'); codeBuf = []; }
        inCode = !inCode;
        continue;
      }
      if (inCode) { codeBuf.push(ln); continue; }

      var m = ln.match(/^\s*[-*]\s+(.+)$/) || ln.match(/^\s*\d+\.\s+(.+)$/);
      if (m) {                                        /* 列表项先攒着，连续多行合并成一个 <ul> */
        list.push('<li>' + inline(m[1]) + '</li>');
        continue;
      }
      flushList();
      if (!ln.trim()) { if (out.length && out[out.length - 1] !== '<br>') out.push('<br>'); continue; }
      var hm = ln.match(/^\s*(#{1,4})\s+(.+)$/);
      if (hm) {
        out.push('<h' + (hm[1].length <= 2 ? 3 : 4) + '>' + inline(hm[2]) + '</h' + (hm[1].length <= 2 ? 3 : 4) + '>');
        continue;
      }
      out.push(inline(ln));
    }
    if (inCode && codeBuf.length) out.push('<pre class="md-code">' + codeBuf.join('\n') + '</pre>');
    flushList();
    return out.join('');
  }

  /* 行内标记：**粗体**、`代码`。放在最后统一处理，避免和列表/标题的正则打架 */
  function inline(s) {
    return s.replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
            .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<i>$2</i>');
  }

  function scrollBottom() {
    var el = document.querySelector('.chat-list');
    if (el) el.scrollTop = el.scrollHeight;
  }

  global.PAGES.chat = {
    name: '问案判官',
    sub: 'RAG 对话',
    render: function (ps) {
      if (!ps.loaded) { ps.loaded = true; ps.msgs = ps.msgs || []; ps.loadHistory(); }
      ps.input = ps.input || '';
      ps.busy = ps.busy || false;
      ps.caseId = ps.caseId || 0;
      ps.cases = ps.cases || [];

      function send(text) {
        var q = (text !== undefined ? text : ps.input).trim();
        if (!q || ps.busy) return;
        /* 必须持有 reactive 后的代理对象：直接改原始对象不会触发重渲染 */
        var um = global.Mini.reactive({ role: 'user', text: q });
        ps.msgs.push(um);
        var ai = global.Mini.reactive({ role: 'assistant', text: '', rag: [], streaming: true, mode: '' });
        ps.msgs.push(ai);
        ps.input = '';
        ps.busy = true;
        scrollBottom();

        API.chatStream({ session_id: SESSION, question: q, case_id: ps.caseId || 0 }, {
          onMeta: function (m) {
            ai.rag = m.rag_hits || [];
            ai.mode = m.mode || '';
          },
          onDelta: function (t) {
            ai.text += t;
            scrollBottom();
          },
          onDone: function (info) {
            ai.streaming = false;
            ps.busy = false;
            if (info && info.latency_ms) ai.latency = info.latency_ms;
          },
          onError: function (msg) {
            ai.text = ai.text || ('⚠ ' + msg);
            ai.streaming = false;
            ps.busy = false;
            UI.toast(msg, 'err');
          }
        });
      }

      var msgs = ps.msgs.length ? ps.msgs : [{
        role: 'assistant',
        text: '下官乃酆都判官，掌阴阳律令、善恶簿册。\n' +
              '你可问我：罪名认定、法条适用、量刑尺度、功德折算、六道轮回之判。\n' +
              '我的答复基于《酆都法典》《判例库》《善恶簿》检索结果，并会标注引用出处。',
        rag: [], mode: ''      /* 开场白不是任何引擎生成的，别给它挂「本地规则引擎」标签 */
      }];

      return [
        h('div', { class: 'chat-wrap' },
          h('div', { class: 'chat-list' }, msgs.map(function (m, mi) {
            var isUser = m.role === 'user';
            /* 每个气泡的子节点数量必须恒定（rag / 正文 / 脚注 三件套），
               否则 vdom 按下标 diff 会把不同结构的节点错位复用，出现命中行重复 */
            return h('div', { class: 'msg' + (isUser ? ' user' : ''), key: m.uid || ('m' + mi) },
              h('div', { class: 'who' }, isUser ? (App.st.user ? App.st.user.real_name.slice(0, 1) : '我') : '判'),
              /* minWidth:0 让 flex 子项可收缩，否则命中行文字会把气泡撑到超宽 */
              h('div', { style: { maxWidth: '76%', minWidth: 0 } },
                h('div', {
                  class: 'rag-hit',
                  style: { display: (m.rag && m.rag.length) ? 'block' : 'none' }
                }, m.rag && m.rag.length
                  ? '📚 检索命中：' + m.rag.map(function (r) { return r.title + '(' + r.score + ')'; }).join(' · ')
                  : ''),
                /* 用户输入按纯文本显示（它本来就是纯文本）；判官答复走 Markdown 子集渲染。
                   流式过程中也要渲染，否则「思考中…」时的半句话会先露出星号再突然变样。 */
                h('div', { class: 'bubble' + (m.streaming ? ' typing' : '') + (isUser ? '' : ' md'),
                           html: isUser ? esc(m.text) : md(m.text) }),
                h('div', { style: { fontSize: '11px', color: 'var(--text-3)', marginTop: '4px' } },
                  (!isUser && (m.latency || m.mode))
                    ? (m.mode === 'llm' ? '大模型生成' : '本地规则引擎') +
                      (m.latency ? ' · ' + m.latency + 'ms' : '')
                    : '')));
          })),

          h('div', { style: { marginTop: '8px' } },
            h('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '8px' } },
              QUICK.map(function (q) {
                return h('span', {
                  class: 'chip', style: { cursor: 'pointer' },
                  onClick: function () { send(q); }
                }, q);
              })),

            h('div', { class: 'chat-input' },
              h('textarea', {
                placeholder: '向判官提问，如「侵吞赈灾粮款如何量刑？」（Enter 发送，Shift+Enter 换行）',
                value: ps.input,
                onInput: function (e) { ps.input = e.target.value; },
                onKeyDown: function (e) {
                  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
                }
              }),
              h('div', { style: { display: 'flex', flexDirection: 'column', gap: '6px' } },
                UI.btn(ps.busy ? '思考中…' : '发问', 'primary', function () { send(); }, ps.busy),
                UI.btn('清空', 'sm ghost', function () {
                  ps.msgs = [];
                  ps.loadHistory();
                })))
          ))
      ];
    },

    loadHistory: function () {
      var ps = pageState['chat'];
      API.chatHistory(SESSION).then(function (d) {
        if ((d.list || []).length) {
          ps.msgs = (d.list || []).map(function (m) {
            return { role: m.role, text: m.content, rag: [], mode: '' };
          });
        }
      }).catch(function () {});
    }
  };
})(window);
