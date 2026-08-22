// H5 共享工具：DOM 查询 + HTML 转义 + 手机号白名单鉴权。
function $(id) { return document.getElementById(id); }
function escapeHtml(s) {
  return (s || '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

const TOKEN_KEY = 'lsx_token';
const EXP_KEY = 'lsx_token_expires_at';

function getToken() {
  const t = localStorage.getItem(TOKEN_KEY);
  const expRaw = localStorage.getItem(EXP_KEY);
  if (!t || !expRaw) return null;
  const expMs = parseInt(expRaw, 10) * 1000;
  if (!Number.isFinite(expMs) || Date.now() >= expMs) {
    clearAuth();
    return null;
  }
  return t;
}

function authHeader() {
  const t = getToken();
  return t ? { Authorization: 'Bearer ' + t } : {};
}

function clearAuth() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(EXP_KEY);
}

function requireLoginOrRedirect() {
  if (!getToken()) {
    // 记住来源地址，登录后跳回（仅同源路径）
    const next = encodeURIComponent(location.pathname + location.search);
    location.replace('/login?next=' + next);
    return false;
  }
  return true;
}

async function handle401(res) {
  if (res && res.status === 401) {
    clearAuth();
    // 也带 next：登录后回到原页面
    const next = encodeURIComponent(location.pathname + location.search);
    location.replace('/login?next=' + next);
    return true;
  }
  return false;
}

const USER_NAME_KEY = "lsx_user_name";

function setUserName(name) {
  if (name) localStorage.setItem(USER_NAME_KEY, name);
  else localStorage.removeItem(USER_NAME_KEY);
}

function getUserName() {
  return localStorage.getItem(USER_NAME_KEY) || "";
}


function logout() {
  // 退出登录：清 token 与用户名，回登录页重新登录
  clearAuth();
  setUserName('');
  location.replace('/login');
}


// ---- theme (dark mode: follow system + manual toggle) ----
var THEME_KEY = "lsx_theme";

function currentTheme() {
  var saved = localStorage.getItem(THEME_KEY);
  if (saved === "dark" || saved === "light") return saved;
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme() {
  var t = currentTheme();
  document.documentElement.setAttribute("data-theme", t);
  document.querySelectorAll(".theme-toggle").forEach(function (btn) {
    btn.setAttribute("aria-label", t === "dark" ? "Switch to light" : "Switch to dark");
  });
}

function toggleTheme() {
  var next = currentTheme() === "dark" ? "light" : "dark";
  localStorage.setItem(THEME_KEY, next);
  applyTheme();
}

document.addEventListener("DOMContentLoaded", applyTheme);


// ---- admin unified top nav (dashboard / quiz-admin / whitelist / quiz-stats) ----
var ADMIN_NAV = [
  { key: 'dashboard', href: '/dashboard', label: '数据看板', quizOnly: false },
  { key: 'quiz-admin', href: '/quiz-admin', label: '测验管理', quizOnly: true },
  { key: 'whitelist-admin', href: '/whitelist-admin', label: '白名单管理', quizOnly: false },
];

// current: 'dashboard' | 'quiz-admin' | 'whitelist-admin' | 'quiz-stats'
// me: optional whoami object (whitelist version includes city/county); auto-fetch when omitted
async function initAdminNav(current, me) {
  var nav = document.getElementById('adminNav');
  if (!nav) return null;
  if (!me) {
    try {
      var r = await fetch('/api/admin/whitelist/whoami', { headers: authHeader() });
      if (await handle401(r)) return null;
      if (!r.ok) return null;
      me = await r.json();
    } catch (e) {
      return null;
    }
  }
  var roleTag = document.getElementById('roleTag');
  if (roleTag && me) {
    roleTag.textContent = (me.sys_role || '') + ' · ' + (me.admin_level || '') +
      (me.city ? ' · ' + me.city : '') + (me.county ? ' · ' + me.county : '');
  }
  var canQuiz = me.sys_role === '系统管理员' || ['市级', '省级'].indexOf(me.admin_level) !== -1;
  var activeKey = current === 'quiz-stats' ? 'quiz-admin' : current;
  nav.innerHTML = ADMIN_NAV.filter(function (item) {
    return !item.quizOnly || canQuiz;
  }).map(function (item) {
    if (item.key === activeKey) {
      return '<span class="active">' + escapeHtml(item.label) + '</span>';
    }
    return '<a href="' + item.href + '">' + escapeHtml(item.label) + '</a>';
  }).join('');
  return me;
}
