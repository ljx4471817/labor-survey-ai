#!/usr/bin/env bash
# pg_query.sh — 跑一次性 PG 查询/分析。**绝不改任何 PG 角色密码。**
#
# 为什么存在：2026-09-20 傍晚事故的根因是"为了跑查询去 ALTER USER 密码,
# 然后还原时改错了,把线上后端的密码搞 mismatch"。根本解法不是"改得漂亮一点",
# 而是**根本不要改**:后端用的密码就写在 .env 里,直接读来用即可。
#
# 相比 pg_touch.sh(改密版)的好处:
#   - 没有"改密窗口" → 线上后端不会在查询期间认证失败(100% 无中断)
#   - 不可能误覆盖 PG 的密码 hash
#   - 跑完不需要"还原"这一步(因为压根没改)
#
# 用法:
#   bash scripts/pg_query.sh '<command>'
#
# 例:
#   bash scripts/pg_query.sh 'backend/venv/bin/python /tmp/my_stats.py'
#   bash scripts/pg_query.sh 'psql -d lsx_query_log -c "SELECT count(*) FROM query_log"'
#
# command 内可用环境变量:
#   $PGPASSWORD           psql/libpq 自动识别
#   $PG_TOUCH_PWD_FILE    指向 600 临时文件(内容=密码),给 psycopg 脚本用
#   $LSX_DB_PWD           显式密码值(排版用)
#
# 需要"轮换密码"时不要用本脚本,用 scripts/pg_touch.sh --rotate。

set -Eeuo pipefail

if [ $# -lt 1 ]; then
  echo "用法: $0 '<command>'" >&2
  echo "  例: $0 'backend/venv/bin/python /tmp/my_stats.py'" >&2
  exit 2
fi

RUN_BLOCK="$1"
ENV_FILE="${LSX_ENV_FILE:-/opt/labor-survey-ai/.env}"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
yellow(){ printf '\033[33m%s\033[0m\n' "$*"; }

# 读 .env:优先直接读(快),读不到再 sudo(同机运维常见)
_read_env_pwd() {
  local raw=""
  if [ -r "$ENV_FILE" ]; then
    raw="$(python3 -c "
import re,sys
d=open('$ENV_FILE','rb').read().decode('utf-8')
m=re.search(r'LSX_DB_WHITELIST=postgresql://lsx:([^@]+)@',d)
print(m.group(1) if m else '')
" 2>/dev/null || true)"
  fi
  if [ -z "$raw" ]; then
    raw="$(sudo python3 -c "
import re,sys
d=open('$ENV_FILE','rb').read().decode('utf-8')
m=re.search(r'LSX_DB_WHITELIST=postgresql://lsx:([^@]+)@',d)
print(m.group(1) if m else '')
" 2>/dev/null || true)"
  fi
  printf '%s' "$raw"
}

yellow "[1/3] 从 .env 读取数据库密码（不改 PG）..."
PWD_VAL="$(_read_env_pwd)"
if [ -z "$PWD_VAL" ]; then
  red "!! 读不到 $ENV_FILE 里的 LSX_DB_WHITELIST 密码"
  red "   若 .env 与 PG 密码已不同步 → 走 scripts/pg_touch.sh --rotate 修复"
  exit 3
fi
yellow "    OK（长度 ${#PWD_VAL}）"

# 验证密码真的能登入（防御性:凭据不同步时立刻暴露，而不是让用户脚本莫名报错）
yellow "[2/3] 验证该密码能登入 PG ..."
if sudo -u postgres psql -tAc "SELECT 1" \
     "postgresql://lsx:${PWD_VAL}@127.0.0.1:5432/lsx_whitelist" >/dev/null 2>&1; then
  green "    OK: 凭据有效"
else
  red "!! .env 密码登不进 PG —— 说明两边已不同步（正是 9/20 事故的状态）"
  red "   修复: bash /opt/labor-survey-ai/scripts/pg_touch.sh --rotate"
  exit 4
fi

# 交给用户的命令执行（密码通过环境变量 + 600 临时文件传递）
TMP_FILE="$(mktemp /tmp/.lsx-pwd-XXXXXX)"
chmod 600 "$TMP_FILE"
printf '%s' "$PWD_VAL" > "$TMP_FILE"
trap 'rm -f "$TMP_FILE"' EXIT

yellow "[3/3] 执行: $RUN_BLOCK"
echo "    (密码可用: \$PGPASSWORD / \$PG_TOUCH_PWD_FILE / \$LSX_DB_PWD)"
echo "    (已预设 PGHOST=127.0.0.1 PGUSER=lsx，裸 psql -d <db> 即可)"
echo "---BEGIN OUTPUT---"
set +e
PGHOST=127.0.0.1 PGUSER=lsx \
PGPASSWORD="$PWD_VAL" LSX_DB_PWD="$PWD_VAL" PG_TOUCH_PWD_FILE="$TMP_FILE" \
  bash -c "$RUN_BLOCK"
RC=$?
set -e
echo "---END OUTPUT---  (exit=$RC)"

if [ $RC -eq 0 ]; then
  green "✓ 完成（PG 角色密码全程未改动）"
else
  yellow "⚠️  命令返回 $RC（PG 角色密码仍未被改动）"
fi
exit $RC
