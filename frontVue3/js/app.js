/* ============================================================
 * app.js —— 应用外壳：登录、路由、侧边栏、顶栏
 * ============================================================ */
(function (global) {
  'use strict';
  var h = global.h, UI = global.UI, API = global.API;

  /* ---------------- 全局状态 ---------------- */
  var st = Mini.reactive({
    route: 'dashboard',
    user: API.state.user,
    token: API.state.token,
    booting: true,
    /* 🔴 原来这里把 `admin / admin123` 直接预填进输入框，登录页还有三个
       「一键填入弱口令」按钮 —— 等于把管理员口令写在公开前端里。
       现在口令一律手输（首次启动的随机口令见后端 .run/credentials.txt）。 */
    login: { username: 'admin', password: '', err: '', busy: false },
    notify: { list: [], unread: 0 },
    toasts: []
  });

  var PAGES = global.PAGES = {};
  var pageState = global.pageState = {};   // 每个页面自己的响应式状态（页面模块内可访问）

  function pageStateOf(key) {
    if (!pageState[key]) pageState[key] = Mini.reactive({ loaded: false, busy: false });
    return pageState[key];
  }

  function go(route, params) {
    if (!PAGES[route]) route = 'dashboard';
    st.route = route;
    if (global.location.hash !== '#' + route) {
      try { global.history.replaceState(null, '', '#' + route); }
      catch (e) { global.location.hash = route; }
    }
    var ps = pageStateOf(route);
    if (params) ps.params = params;
    var page = PAGES[route];
    if (page.load) {
      try { page.load(ps, st); } catch (e) { console.error(e); UI.toast(e.message, 'err'); }
    }
  }

  /* ---------------- 登录 ---------------- */
  function doLogin() {
    if (st.login.busy) return;
    if (!st.login.password) { st.login.err = '请输入口令'; return; }
    st.login.busy = true;
    st.login.err = '';
    API.login(st.login.username.trim(), st.login.password)
      .then(function (d) {
        st.user = d.user;
        st.token = d.token;
        st.booting = false;
        UI.toast('欢迎回来，' + d.user.real_name);
        go('dashboard');
        loadNotify();
      })
      .catch(function (e) {
        st.login.err = e.message || '登录失败';
      })
      .then(function () { st.login.busy = false; });
  }

  function doLogout() {
    UI.confirm('退出登录', '确定要退出当前账号吗？', function () {
      API.logout().then(function () {
        st.user = null; st.token = '';
        UI.toast('已退出登录', 'info');
      });
    });
  }

  function loadNotify() {
    API.notifications().then(function (d) {
      st.notify.list = d.list || [];
      st.notify.unread = d.unread || 0;
    }).catch(function () {});
  }

  /* 通知面板：补证通知 / 待办复核 / 复核结果都从这里看
     （admin 能看到全岗位的通知，其余角色只看到自己的 + 广播） */
  var NTYPE = { supplement: '补证', approval: '复核', system: '系统' };

  function notifyDialog() {
    var body = document.createElement('div');
    function paint() {
      body.innerHTML = '';
      var list = st.notify.list || [];
      if (!list.length) {
        body.appendChild(Mini.toDom(h('div', { class: 'empty' },
          h('div', { class: 'ic' }, '🔔'), h('div', null, '暂无通知'))));
        return;
      }
      body.appendChild(Mini.toDom(h('div', { class: 'timeline' }, list.map(function (n) {
        return h('div', { class: 'tl-item' },
          h('div', { class: 'hd' },
            h('span', { class: 'tag ' + (n.is_read ? 'gray' : (n.ntype === 'supplement' ? 'orange' : 'gold')) },
              NTYPE[n.ntype] || n.ntype),
            h('b', { style: { marginLeft: '6px' } }, n.title),
            n.user_name ? h('span', { class: 'tag gray', style: { marginLeft: '6px' } }, '→ ' + n.user_name) : null,
            h('span', { class: 'tm' }, Mini.fmtTime(n.created_at)),
            n.is_read ? null : h('span', { class: 'tag red', style: { marginLeft: '6px' } }, '未读')),
          h('div', { class: 'bd' },
            h('div', null, n.content || ''),
            h('div', { style: { marginTop: '6px' } },
              n.link ? h('button', {
                class: 'btn sm ghost',
                onClick: function () {
                  var cid = parseInt(String(n.link).replace('case:', ''), 10) || 0;
                  UI.closeModal(mid);
                  if (!cid) return;
                  go('cases');
                  PAGES.cases.openDetail(cid);
                }
              }, '查看卷宗') : null,
              n.is_read ? null : h('button', {
                class: 'btn sm ghost', style: { marginLeft: '6px' },
                onClick: function () {
                  API.readNotification(n.id).then(function () {
                    n.is_read = 1;
                    st.notify.unread = Math.max(0, (st.notify.unread || 0) - 1);
                    paint();
                  }).catch(function (e) { UI.toast(e.message, 'err'); });
                }
              }, '标为已读'))));
      }))));
    }
    paint();
    var mid = UI.openModal({ title: '通知（未读 ' + (st.notify.unread || 0) + '）', body: body, footer: false });
    loadNotify();
    return mid;
  }

  /* ---------------- 侧边栏 ---------------- */
  var NAV = [
    { key: 'dashboard', name: '阎罗大殿', icon: '☯' },
    { key: 'cases', name: '阴司卷宗', icon: '⚖' },
    { key: 'approvals', name: '阎王复核', icon: '✍', badge: 'approval' },
    { key: 'deceased', name: '亡魂名录', icon: '👤' },
    { key: 'evidence', name: '证据勘验', icon: '🔍' },
    { key: 'chat', name: '问案判官', icon: '💬' },
    { key: 'knowledge', name: '幽冥典籍', icon: '📜' },
    { key: 'agents', name: 'Agent 编排', icon: '⚙' },
    { key: 'logs', name: '双日志', icon: '📋' },
    { key: 'settings', name: '阴阳设置', icon: '⚙' }
  ];

  function sidebar() {
    return h('aside', { class: 'sidebar' },
      h('div', { class: 'logo' },
        h('h1', null, '酆都阴司'),
        h('p', null, 'AI 智能地府管理系统')),
      h('nav', { class: 'nav' }, NAV.map(function (n) {
        return h('div', {
          class: 'nav-item' + (st.route === n.key ? ' active' : ''),
          onClick: function () { go(n.key); }
        },
          h('span', { class: 'ico' }, n.icon),
          h('span', null, n.name),
          n.badge === 'approval' && st.notify.pendingApprovals
            ? h('span', { class: 'badge' }, String(st.notify.pendingApprovals)) : null);
      })),
      h('div', { class: 'sidebar-foot' },
        h('div', { class: 'u' },
          h('div', { class: 'avatar' }, (st.user && st.user.real_name || '?').slice(0, 1)),
          h('div', null,
            h('div', { style: { color: 'var(--text)', fontSize: '13px' } }, st.user ? st.user.real_name : '未登录'),
            h('div', null, st.user ? st.user.role_name : ''))),
        h('div', { style: { display: 'flex', gap: '8px' } },
          h('span', { style: { cursor: 'pointer' }, onClick: doLogout }, '退出登录'),
          h('span', { style: { marginLeft: 'auto' } }, 'v1.0'))));
  }

  function topbar() {
    var page = PAGES[st.route];
    return h('div', { class: 'topbar' },
      h('h2', null, page ? page.name : ''),
      h('span', { class: 'tag gold' }, page && page.sub ? page.sub : '阴司办案台'),
      h('div', { class: 'spacer' }),
      h('div', { class: 'search-box', style: { minWidth: '200px' } },
        h('span', { style: { color: 'var(--text-3)' } }, '⌕'),
        h('input', {
          placeholder: '搜索卷宗编号 / 亡魂姓名，回车跳转',
          onKeyDown: function (e) {
            if (e.key === 'Enter') { go('cases', { keyword: e.target.value }); }
          }
        })),
      h('button', { class: 'btn ghost', onClick: notifyDialog },
        '通知',
        st.notify.unread ? h('span', { class: 'badge', style: { marginLeft: '4px' } }, String(st.notify.unread)) : null));
  }

  /* ---------------- 登录页 ---------------- */
  function loginView() {
    return h('div', { id: 'login' },
      h('div', { class: 'login-box' },
        h('h1', null, '酆 都 阴 司'),
        h('p', { class: 'sub' }, 'AI 智能地府管理系统'),
        UI.field('阴差账号', h('input', {
          value: st.login.username,
          placeholder: '用户名',
          onInput: function (e) { st.login.username = e.target.value; },
          onKeyDown: function (e) { if (e.key === 'Enter') doLogin(); }
        })),
        UI.field('阴司令牌', h('input', {
          type: 'password', value: st.login.password, placeholder: '口令',
          onInput: function (e) { st.login.password = e.target.value; },
          onKeyDown: function (e) { if (e.key === 'Enter') doLogin(); }
        })),
        h('div', { class: 'err' }, st.login.err),
        h('button', {
          class: 'btn primary', style: { width: '100%', justifyContent: 'center' },
          onClick: doLogin, disabled: st.login.busy
        }, st.login.busy ? '验印中…' : '入 殿 办 案'),
        h('div', { class: 'demo' },
          '首次启动会生成随机口令，见后端 ',
          h('b', null, '.run/credentials.txt'),
          '（600 权限）；DIFU_DEMO_MODE=1 的固定演示口令只对全新空库有效，已有库仍以本文件为准。')));
  }

  /* ---------------- 根组件 ---------------- */
  function App() {
    if (st.booting) {
      return h('div', { style: { display: 'grid', placeItems: 'center', height: '100vh', color: 'var(--text-3)' } },
        h('div', null, h('div', { style: { fontSize: '26px', textAlign: 'center', marginBottom: '10px' } }, '☯'),
          h('div', null, '正在连接酆都服务器…')));
    }
    if (!st.user) return loginView();
    var page = PAGES[st.route] || PAGES.dashboard;
    return h('div', { id: 'app' },
      sidebar(),
      h('div', { class: 'main' },
        topbar(),
        h('div', { class: 'content' }, page.render(pageStateOf(st.route), st))));
  }

  /* 解析 #route?key=value 形式的深链 */
  function parseHash() {
    var raw = (global.location.hash || '').replace(/^#/, '');
    if (!raw) return { route: '', query: '' };
    var i = raw.indexOf('?');
    if (i < 0) return { route: raw, query: '' };
    return { route: raw.slice(0, i), query: raw.slice(i + 1) };
  }

  /* 深链：?case=<id> 直接打开卷宗详情。
     注意 go() 会把 hash 改写为 #route（丢弃 query），因此必须在 go 之前
     解析并暂存 caseId，go 之后再弹窗。 */
  var pendingCaseId = 0;

  function captureDeepLink() {
    var q = parseHash().query || (global.location.search || '').replace(/^\?/, '');
    var m = /(?:^|[?&])case=(\d+)/.exec(q);
    if (m) pendingCaseId = Number(m[1]);
  }

  function openDeepLink() {
    if (!pendingCaseId) return;
    var cid = pendingCaseId;
    pendingCaseId = 0;
    if (PAGES.cases && PAGES.cases.openDetail) {
      setTimeout(function () { PAGES.cases.openDetail(cid); }, 250);
    }
  }

  /* ---------------- 启动 ---------------- */
  function boot() {
    Mini.mount(App, document.getElementById('root'));
    if (st.token) {
      API.me()
        .then(function (u) {
          st.user = u;
          API.state.user = u;
          st.booting = false;
          captureDeepLink();
          go(parseHash().route || 'dashboard');
          loadNotify();
          openDeepLink();
        })
        .catch(function () {
          st.user = null; st.booting = false;
        });
    } else {
      st.booting = false;
    }
  }

  global.App = {
    st: st, go: go, loadNotify: loadNotify,
    onUnauthorized: function () {
      st.user = null;
      UI.toast('登录已过期，请重新登录', 'err');
    }
  };

  window.addEventListener('hashchange', function () {
    var p = parseHash();
    if (p.route && PAGES[p.route]) { captureDeepLink(); go(p.route); openDeepLink(); }
  });

  window.addEventListener('DOMContentLoaded', boot);
  if (document.readyState !== 'loading') boot();
})(window);
