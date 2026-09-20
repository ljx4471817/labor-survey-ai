#!/usr/bin/env bash
# pg_touch.sh — **密码轮换专用**（会改 PG 角色密码）。日常只读查询请用 pg_query.sh。
#
# ⚠️ 2026-09-20 傍晚事故复盘：
#    旧版本脚本"临时改密 → 跑命令 → 还原"的设计有 2 个坑:
#      1) 改密窗口期，线上后端仍用 .env 里的旧密码 → 每次新建连接都认证失败
#         （db.py 是线程局部连接，连接被回收时就会踩到）
#      2) "还原"步骤一旦写错(如还原成占位符字符串)，PG 密码 hash 被永久改坏,
#         而 .env 没动 → 后端全面 401/500，且原密码不可恢复
#    结论: 只读查询**根本不需要改密码**(密码就在 .env 里) → 见 pg_query.sh
#          本脚本收窄为"轮换/修复"单一职责,并强制停服消除窗口。
#
# 用法:
#   bash scripts/pg_touch.sh --rotate [--pwd '<新密码>']
#
# 流程（任一失败即中止，不会留下半改状态）:
#   备份 .env(存到仓库外) → 停服务 → ALTER PG → 同步 .env 4 行 → 重启 → 验证 200
#
# 不传 --rotate 直接退出：防止被当成"跑查询"误用。

set -Eeuo pipefail

ROOT_DIR="/opt/labor-survey-ai"
ENV_FILE="${LSX_ENV_FILE:-$ROOT_DIR/.env}"
PG_USER="${LSX_PG_USER:-lsx}"
SERVICE="labor-survey"
BACKUP_DIR="${LSX_BACKUP_DIR:-/root}"          # 故意放仓库外,避免密钥进 git
HEALTH_URL="http://127.0.0.1:8001/health"
ADMIN_URL="http://127.0.0.1:8001/api/admin/whitelist"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
yellow(){ printf '\033[33m%s\033[0m\n' "$*"; }

if [ "${1:-}" != "--rotate" ]; then
  red "本脚本只用于密码轮换,请显式传 --rotate"
  echo
  echo "  只读查询/一次性分析 → bash $ROOT_DIR/scripts/pg_query.sh '<command>'"
  echo "  确实要轮换密码      → bash $0 --rotate"
  exit 2
fi
shift

NEW_PWD=""
if [ "${1:-}" = "--pwd" ]; then
  NEW_PWD="${2:-}"
  [ -n "$NEW_PWD" ] || { red "--pwd 后面要跟密码"; exit 2; }
fi
[ -n "$NEW_PWD" ] || NEW_PWD="Lgx_$(openssl rand -base64 18 | tr -d '/+=' | head -c 16)"

# 1. 备份 .env 到**仓库外**（防止 .env.bak 被 git add -A 收走进公开仓库）
TS="$(date +%Y%m%d-%H%M%S)"
BACKUP_PATH="$BACKUP_DIR/.env.bak-$TS-pgrotate"
yellow "[1/5] 备份 .env → $BACKUP_PATH（仓库外,root 600）"
sudo install -m 600 -o root -g root "$ENV_FILE" "$BACKUP_PATH"

# 2. 停服务（消除"改密窗口"期间的认证失败）
yellow "[2/5] 停 $SERVICE ..."
sudo systemctl stop "$SERVICE"
green "    已停"

# 3. 改 PG 密码
yellow "[3/5] ALTER USER $PG_USER ..."
sudo -u postgres psql -c "ALTER USER ${PG_USER} WITH PASSWORD '${NEW_PWD}';" >/dev/null
green "    PG hash 已更新"

# 4. 同步 .env 4 行（幂等：任何 postgresql://lsx:<pw>@ 都替换）
yellow "[4/5] 同步 .env 4 行 ..."
export NEW_PWD ENV_FILE PG_USER
sudo -E python3 - <<'PY'
import re, os
p = os.environ['ENV_FILE']; u = os.environ['PG_USER']; nw = os.environ['NEW_PWD']
src = open(p, 'rb').read().decode('utf-8')
pat = re.compile(r'(LSX_DB_[A-Z_]+=postgresql://' + re.escape(u) + r':)([^@]+)(@127\.0\.0\.1:5432)')
new, n = pat.subn(r'\g<1>' + nw + r'\g<3>', src)
assert n == 4, f'期望替换 4 行,实际 {n} 行 —— 中止(不改写)'
open(p, 'w').write(new)
print(f'    已替换 {n} 行')
PY

# 5. 重启 + 验证
yellow "[5/5] 重启 $SERVICE 并验证 ..."
sudo systemctl start "$SERVICE"
for i in 1 2 3 4 5 6 7 8 9 10; do
  code=$(curl -sS -o /dev/null -w '%{http_code}' "$HEALTH_URL" 2>/dev/null || echo 000)
  [ "$code" = "200" ] && break
  sleep 1
done
[ "$code" = "200" ] || { red "!! /health 未就绪(code=$code)"; journalctl -u "$SERVICE" -n 20 --no-pager; exit 5; }
green "    /health → 200"

TOKEN=$(curl -sS -X POST http://127.0.0.1:8001/api/auth/login \
  -H 'Content-Type: application/json' -d '{"phone":"18275117145"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin).get('token',''))" 2>/dev/null || echo "")
if [ ${#TOKEN} -eq 96 ]; then
  code=$(curl -sS -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOKEN" "$ADMIN_URL")
  if [ "$code" = "200" ]; then green "    admin 接口 → 200 ✓"; else red "!! admin 接口 → $code"; exit 6; fi
else
  yellow "    (跳过 admin 校验:未取到 token)"
fi

green "✓ 轮换完成。新密码已同步到 PG 与 .env。"
echo "  备份(含旧 .env): $BACKUP_PATH"
echo "  如需留档当前密码: echo '<新密码>' | sudo tee /root/.lsx-db-pwd && sudo chmod 600 /root/.lsx-db-pwd"
