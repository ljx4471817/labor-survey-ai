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
  if (!getToken()) { location.replace('/login'); return false; }
  return true;
}

async function handle401(res) {
  if (res && res.status === 401) {
    clearAuth();
    location.replace('/login');
    return true;
  }
  return false;
}
