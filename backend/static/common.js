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
